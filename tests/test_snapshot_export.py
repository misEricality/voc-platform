"""方案③ 静态快照导出回归（scripts/ops/export_static_snapshot.py）

锁住的回归：
1. 纯函数：compute_windows（「近 N 天不含当天」口径）/ canonical_query 确定性 /
   file_name_for（路径参数端点取段，Windows 文件名无冒号）/ parse_query（解码+删空）/
   match_route（精确匹配 + `*` 通配符 + 键集不一致拒绝）
2. build_static_index：裁剪「系统管理」导航与 data/admin 脚本、缓存串替换为快照 stamp、
   api.js 前注入 STATIC_SNAPSHOT 开关与 SNAPSHOT_META
3. 集成：种子测试 DB → export_snapshot 全链路（manifest 路由覆盖三页数据源 /
   文件真实落盘 / 零数据目标跳过 / covers 拷贝）

独立测试 DB（data/voc_test_*.db）与独立输出目录（data/exports/snapshot_test_*），
绝不碰 data/voc.db 与正式快照目录 data/exports/snapshot/。
最后更新：2026-09-07
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from scripts.ops.export_static_snapshot import (  # noqa: E402
    build_static_index,
    canonical_query,
    clean_params,
    compute_windows,
    export_snapshot,
    file_name_for,
    match_route,
    parse_query,
)


# ==================== fixtures ====================

@pytest.fixture
def test_db_path():
    db = ROOT / "data" / f"voc_test_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    yield db
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


@pytest.fixture
def seeded_db(test_db_path):
    """预置：1 个 Steam 目标（3 条已标注评论 + 2 条观点）+ 1 个采集任务
    + 1 个已采集 B 站视频（带 aid，2 条已标注评论）"""
    from src.storage.db import (
        BilibiliQueue,
        CollectTaskRepository,
        CommentRepository,
        init_db,
    )

    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    now = _now_naive()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        for i in range(3):
            c = repo.upsert(_fake_raw(
                "steam", f"r{i}", "2358720",
                ["战斗手感超爽", "优化太差劝退", "剧情一般般"][i],
                rating=1, likes=10 - i, posted_at=now - timedelta(days=i),
                extra={"appid": "2358720"},
            ), target_meta={"name": "黑神话：悟空"})
            c.sentiment = ["positive", "negative", "neutral"][i]
            c.sentiment_score = [0.8, -0.7, 0.0][i]
            c.analyzed_at = now
            s.flush()
            if i == 0:
                _add_opinion(s, c, "玩法与内容/战斗手感/动作系统", "positive")
            elif i == 1:
                _add_opinion(s, c, "技术与性能/优化问题/掉帧", "negative")
        s.commit()

        CollectTaskRepository(s).create("steam", "2358720", name="黑神话：悟空")

        # B 站视频：aid=11712345 → target_id = bilibili:video:11712345
        s.add(BilibiliQueue(
            bv_id="BV1TESTQ", status="fetched", aid=11712345,
            pubdate=now - timedelta(days=30), due_date=now - timedelta(days=23),
            comment_count=2, danmaku_count=0,
        ))
        s.commit()
        for i in range(2):
            c = repo.upsert(_fake_raw(
                "bilibili", f"bc{i}", "",
                ["视频讲得不错", "节奏太拖了"][i],
                rating=None, likes=5 - i, posted_at=now - timedelta(days=i),
                extra={"aid": "11712345"},
            ))
            c.sentiment = ["positive", "negative"][i]
            c.sentiment_score = [0.7, -0.6][i]
            c.analyzed_at = now
        s.commit()
    return test_db_path


@pytest.fixture
def snapshot_out(tmp_path):
    return tmp_path / "snapshot_test"


# ==================== 造数小工具 ====================

def _now_naive():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fake_raw(platform, source_id, target_id, content, *, rating, likes,
              posted_at, extra):
    """构造 RawComment 鸭子类型（与 test_api.py 同手法，避免 import src.collectors）"""
    return type("R", (), {
        "platform": platform, "source_id": source_id, "target_id": target_id,
        "content": content, "author": "u", "author_id": "a",
        "rating": rating, "language": "schinese", "likes": likes,
        "replies": None, "posted_at": posted_at, "extra": extra,
    })()


def _add_opinion(s, comment, full_path, sentiment):
    from src.storage.db import CommentOpinion
    s.add(CommentOpinion(
        comment_id=comment.id, full_path=full_path,
        sentiment=sentiment, quote=comment.content,
    ))


# ==================== 纯函数 ====================

def test_compute_windows_recent_n_days_exclude_today():
    """「近 N 天不含当天」：start = 今天-N，end = 昨天；all 档无窗口"""
    w = compute_windows(date(2026, 9, 7))
    assert w["all"] == {}
    assert w["30d"] == {"start": "2026-08-08", "end": "2026-09-06"}
    assert w["7d"] == {"start": "2026-08-31", "end": "2026-09-06"}
    assert w["1d"] == {"start": "2026-09-06", "end": "2026-09-06"}


def test_canonical_query_deterministic_and_drops_empty():
    assert canonical_query({"b": "2", "a": "1"}) == "a=1&b=2"
    assert canonical_query({"a": "", "b": None, "c": "x"}) == "c=x"
    assert canonical_query({}) == ""


def test_clean_params_stringifies():
    assert clean_params({"page": 2, "x": "", "y": None}) == {"page": "2"}


def test_file_name_for_path_param_endpoint():
    """danmaku 路径参数含冒号 → 文件名只取端点段，不能含 ':'（Windows 非法）"""
    name = file_name_for("/api/danmaku/bilibili:video:11712345", {})
    assert name.startswith("api/danmaku__")
    assert ":" not in name
    assert file_name_for("/api/overview", {"target": "steam:1"}) == (
        file_name_for("/api/overview", {"target": "steam:1"}))  # 确定性
    assert file_name_for("/api/overview", {"target": "steam:1"}) != (
        file_name_for("/api/overview", {"target": "steam:2"}))  # 参数不同 → 文件不同


def test_parse_query_decodes_and_drops_empty():
    assert parse_query("target=steam%3A1&grain=comment&page=") == {
        "target": "steam:1", "grain": "comment",
    }


def test_match_route_exact_and_wildcard():
    routes = [
        {"path": "/api/overview", "params": {"target": "steam:1"}, "file": "a.json"},
        {"path": "/api/games/meta", "params": {"targets": "*"}, "file": "m.json"},
    ]
    # 精确命中
    assert match_route(routes, "/api/overview", {"target": "steam:1"})["file"] == "a.json"
    # 多传/少传/值不同 → 未收录
    assert match_route(routes, "/api/overview", {"target": "steam:2"}) is None
    assert match_route(routes, "/api/overview", {"target": "steam:1", "page": "2"}) is None
    assert match_route(routes, "/api/overview", {}) is None
    # 通配符：任意非空 targets 命中（dashboard/compare 两种排序共用）
    assert match_route(routes, "/api/games/meta", {"targets": "steam:9,steam:8"})
    # 空值不算命中通配符
    assert match_route(routes, "/api/games/meta", {"targets": ""}) is None


# ==================== build_static_index ====================

FAKE_INDEX = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head><meta charset="UTF-8"><title>Lynx — 实时口碑看板</title>
<link rel="stylesheet" href="src/web.css?v=20260906c"></head>
<body>
<header class="topbar">
  <nav class="nav" id="nav">
    <div class="nav-drop">
      <a href="#/compare" data-page="compare">Steam游戏看板 ▾</a>
      <div class="nav-menu">
        <a href="#/compare" data-page="compare">游戏对比</a>
        <a href="#/dashboard" data-page="dashboard">单游戏</a>
      </div>
    </div>
    <a href="#/bilibili" data-page="bilibili">B站视频看板</a>
    <div class="nav-drop">
      <a href="#/admin" data-page="admin">系统管理 ▾</a>
      <div class="nav-menu">
        <a href="#/admin" data-page="admin">采集任务</a>
        <a href="#/data" data-page="data">数据管理</a>
      </div>
    </div>
  </nav>
  <div class="topbar-meta" id="dbMeta"></div>
</header>
<script src="vendor/echarts.min.js"></script>
<script src="src/api.js?v=20260905c"></script>
<script src="src/pages/dashboard.js?v=20260905c"></script>
<script src="src/pages/bilibili.js?v=20260905c"></script>
<script src="src/pages/data.js?v=20260906c"></script>
<script src="src/pages/admin.js?v=20260907"></script>
<script src="src/main.js?v=20260905c"></script>
</body></html>
"""


def test_build_static_index_trims_and_injects():
    meta = {"generated_at": "2026-09-07 21:00", "comments": 21917}
    html = build_static_index(FAKE_INDEX, meta, "20260907s1")

    assert "Lynx — 口碑看板（静态快照）" in html
    assert "Lynx — 实时口碑看板" not in html
    assert "#/admin" not in html and "#/data" not in html      # 系统管理导航整块移除
    assert "#/compare" in html and "#/bilibili" in html        # 三页导航保留
    assert "pages/data.js" not in html and "pages/admin.js" not in html
    assert "v=20260907s1" in html and "v=20260905c" not in html  # 缓存串统一 stamp
    assert "window.STATIC_SNAPSHOT = true;" in html
    assert "SNAPSHOT_META" in html and "21917" in html
    # 开关注入在 api.js 之前（shim 初始化依赖）
    assert html.index("STATIC_SNAPSHOT") < html.index("src/api.js")


# ==================== 集成：export_snapshot 全链路 ====================

def test_export_snapshot_end_to_end(seeded_db, snapshot_out):
    stats = export_snapshot(seeded_db, snapshot_out, list_pages=2)

    # 统计口径：1 Steam 目标 + 1 B 站视频
    assert stats["steam_targets"] == 1 and stats["bili_videos"] == 1
    assert stats["routes"] == stats["files"]

    # manifest 存在且可解析，路由覆盖三页数据源
    manifest = json.loads(
        (snapshot_out / "snapshot" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1 and manifest["routes"]
    paths = {r["path"] for r in manifest["routes"]}
    for ep in ("/api/targets", "/api/games/meta", "/api/topics/tree",
               "/api/overview", "/api/topics", "/api/trends",
               "/api/comments", "/api/opinions",
               "/api/bilibili/videos", "/api/danmaku/bilibili%3Avideo%3A11712345"):
        assert ep in paths, f"manifest 缺少 {ep}"

    # Steam 游戏路由：4 窗口 × 2 颗粒度 overview + 累计口径 L2 极性对比
    overviews = [r for r in manifest["routes"] if r["path"] == "/api/overview"]
    assert len(overviews) == 4 * 2 + 1  # steam 4窗口×2grain + bili 1（无 grain 参数）
    l2 = [r for r in manifest["routes"] if r["path"] == "/api/topics"
          and r["params"].get("level") == "L2"]
    assert {r["params"]["sentiment"] for r in l2} == {"negative", "positive"}

    # 每条路由指向的文件真实存在且可解析
    for r in manifest["routes"]:
        f = snapshot_out / "snapshot" / r["file"]
        assert f.exists(), f"快照文件缺失：{r['file']}"
        json.loads(f.read_text(encoding="utf-8"))

    # 静态版入口：裁剪 + 注入（复用同一转换函数，端到端再锁一次）
    index_html = (snapshot_out / "index.html").read_text(encoding="utf-8")
    assert "STATIC_SNAPSHOT" in index_html and "#/admin" not in index_html

    # 前端资源与封面拷贝
    assert (snapshot_out / "src" / "api.js").exists()
    assert (snapshot_out / "vendor" / "echarts.min.js").exists()
    assert (snapshot_out / "covers").is_dir()

    # 幂等：重跑不报错且路由数一致（输出目录整体重建）
    stats2 = export_snapshot(seeded_db, snapshot_out, list_pages=2)
    assert stats2["routes"] == stats["routes"]


def test_export_snapshot_skips_zero_data_targets(test_db_path, snapshot_out):
    """零数据 Steam 目标不导出逐游戏文件（避免静态页拿到 null overview 崩溃）"""
    from src.storage.db import CollectTaskRepository, init_db

    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    with SessionLocal() as s:
        CollectTaskRepository(s).create("steam", "9999999", name="空数据游戏")
        s.commit()

    export_snapshot(test_db_path, snapshot_out, list_pages=1)
    manifest = json.loads(
        (snapshot_out / "snapshot" / "manifest.json").read_text(encoding="utf-8"))
    per_game = [r for r in manifest["routes"]
                if r["path"] == "/api/overview" and r["params"].get("grain")]
    assert per_game == []  # 无任何逐游戏 overview 文件
    # 共享端点仍导出（targets 白名单含占位行，属「添加即见」语义）
    assert any(r["path"] == "/api/targets" for r in manifest["routes"])
