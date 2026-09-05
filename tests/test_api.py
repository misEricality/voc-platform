"""Web API 回归测试（src/api/，2026-09-01 · WEB_DASHBOARD.md §4/§6 阶段 3 验收）

锁住的回归：
1. 公开只读端点：health / targets / overview / topics / comments / trends / compare
2. 鉴权：/api/admin/* 未登录 401；登录失败 401 / 成功 200
3. 任务管理：Steam CRUD（重复 409 / 暂停 / 删除）；B 站 CRUD（fetched 禁删 409）
4. 字段约束：appid / BV 号解析；无效输入 422

独立测试 DB（data/voc_test_*.db），绝不碰 data/voc.db；
外部接口（Steam appdetails / B 站 view / backfill 线程）全部 mock，不出网。
最后更新：2026-09-01
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


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
def client(test_db_path, monkeypatch):
    """FastAPI TestClient + 独立测试 DB + 已配置的管理员"""
    from fastapi.testclient import TestClient

    from src.api.auth import hash_password
    from src.api.main import create_app

    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("test-pass-123"))
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")

    app = create_app(db_url=f"sqlite:///{test_db_path}")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded_db(test_db_path):
    """预置：1 个 Steam 目标（3 条评论 + 2 条观点）+ 1 个采集任务 + 1 个 B 站队列行"""
    from src.storage.db import (
        BilibiliQueue,
        CollectTaskRepository,
        Comment,
        CommentOpinion,
        CommentRepository,
        init_db,
    )

    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as s:
        repo = CommentRepository(s)
        for i in range(3):
            c = repo.upsert(type("R", (), {
                "platform": "steam", "source_id": f"r{i}",
                "target_id": "2358720",
                "content": ["战斗手感超爽", "优化太差劝退", "剧情一般般"][i],
                "author": f"u{i}", "author_id": f"a{i}", "rating": 1,
                "language": "schinese", "likes": 10 - i, "replies": None,
                "posted_at": now - timedelta(days=i), "extra": {"appid": "2358720"},
            })(), target_meta={"name": "黑神话：悟空"})
            c.sentiment = ["positive", "negative", "neutral"][i]
            c.sentiment_score = [0.8, -0.7, 0.0][i]
            c.sentiment_confidence = 0.9
            c.analyzed_at = now
            s.flush()
            if i == 0:  # 正面评论 → 战斗手感观点
                s.add(CommentOpinion(
                    comment_id=c.id, full_path="玩法与内容/战斗手感/动作系统",
                    sentiment="positive", quote="战斗手感超爽",
                ))
            elif i == 1:  # 负面评论 → 优化观点
                s.add(CommentOpinion(
                    comment_id=c.id, full_path="技术与性能/优化问题/掉帧",
                    sentiment="negative", quote="优化太差劝退",
                ))
            # i == 2（中性）：无观点
        s.commit()

        CollectTaskRepository(s).create("steam", "2358720", name="黑神话：悟空")

        s.add(BilibiliQueue(
            bv_id="BV1TESTQ", status="fetched", pubdate=now - timedelta(days=30),
            due_date=now - timedelta(days=23), comment_count=100, danmaku_count=50,
        ))
        s.commit()
    return test_db_path


# ==================== 公开只读端点 ====================

def test_health(seeded_db, client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["comments"] == 3


def test_targets_list(seeded_db, client):
    r = client.get("/api/targets")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    t = data[0]
    assert t["target_id"] == "steam:2358720"
    assert t["name"] == "黑神话：悟空"
    assert t["total"] == 3 and t["pos"] == 1 and t["neg"] == 1 and t["neu"] == 1


def test_overview_known_and_unknown(seeded_db, client):
    r = client.get("/api/overview", params={"target": "steam:2358720"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["total"] == 3
    assert d["sentiment"]["positive"] == 1
    assert d["sentiment"]["positive_pct"] == 33.3
    assert d["recommend_rate"] == 100.0  # 3 条全 rating=1

    r = client.get("/api/overview", params={"target": "steam:nonexistent"})
    assert r.status_code == 404


def test_topics_level_and_sentiment_filter(seeded_db, client):
    r = client.get("/api/topics", params={"target": "steam:2358720", "level": "L1"})
    assert r.status_code == 200
    topics = {t["topic"]: t for t in r.json()["data"]}
    assert set(topics) == {"玩法与内容", "技术与性能"}
    assert topics["技术与性能"]["negative"] == 1

    r = client.get("/api/topics", params={"target": "steam:2358720", "level": "L2", "sentiment": "negative"})
    topics = {t["topic"]: t for t in r.json()["data"]}
    assert set(topics) == {"优化问题"}


def test_topics_invalid_level_422(seeded_db, client):
    r = client.get("/api/topics", params={"target": "steam:2358720", "level": "L9"})
    assert r.status_code == 422


def test_comments_pagination_and_filters(seeded_db, client):
    r = client.get("/api/comments", params={"target": "steam:2358720", "page_size": 2})
    d = r.json()["data"]
    assert d["total"] == 3 and len(d["items"]) == 2
    assert all("opinions" in item for item in d["items"])
    assert d["items"][0]["opinions"][0]["full_path"].startswith(("玩法与内容", "技术与性能"))

    r = client.get("/api/comments", params={"target": "steam:2358720", "sentiment": "negative"})
    d = r.json()["data"]
    assert d["total"] == 1
    assert d["items"][0]["content"] == "优化太差劝退"

    r = client.get("/api/comments", params={"target": "steam:2358720", "topic": "玩法与内容"})
    assert r.json()["data"]["total"] == 1


def test_trends_daily_grouping(seeded_db, client):
    r = client.get("/api/trends", params={"target": "steam:2358720", "days": 30})
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    assert len(items) == 3  # 3 条评论分布在 3 个不同日期
    assert sum(i["total"] for i in items) == 3


def test_compare_payload(seeded_db, client):
    r = client.get("/api/compare", params={"targets": "steam:2358720"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert len(d["targets"]) == 1
    assert len(d["sentiment_ratio"]) == 1
    assert "matrix" in d and "pain_points" in d


def test_compare_empty_422(seeded_db, client):
    r = client.get("/api/compare", params={"targets": ""})
    assert r.status_code == 422


# ==================== 鉴权 ====================

def test_admin_requires_login(seeded_db, client):
    r = client.get("/api/admin/tasks")
    assert r.status_code == 401


def test_login_flow(seeded_db, client):
    # 未配置 status 探测
    r = client.get("/api/auth/status")
    assert r.json()["data"]["admin_configured"] is True
    assert r.json()["data"]["logged_in"] is False

    # 错误密码
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 401

    # 正确密码 → admin 可访问
    r = client.post("/api/auth/login", json={"password": "test-pass-123"})
    assert r.status_code == 200
    r = client.get("/api/admin/tasks")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["steam"]) == 1
    assert data["steam"][0]["status_display"] == "采集中"

    # 登出后再次 401
    client.post("/api/auth/logout")
    r = client.get("/api/admin/tasks")
    assert r.status_code == 401


# ==================== Steam 任务管理 ====================

def test_steam_task_create_duplicate_and_pause(seeded_db, client, monkeypatch):
    client.post("/api/auth/login", json={"password": "test-pass-123"})

    monkeypatch.setattr("src.api.routers._fetch_steam_game_name", lambda appid: f"Game {appid}")
    monkeypatch.setattr("src.api.routers._spawn_backfill", lambda *a, **k: None)

    # URL 形式新增
    r = client.post("/api/admin/tasks/steam", json={
        "url_or_id": "https://store.steampowered.com/app/292030/The_Witcher_3/", "backfill_days": 7,
    })
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["target_id"] == "292030"
    assert d["name"] == "Game 292030"
    assert d["backfill_started"] is True

    # 重复 → 409
    r = client.post("/api/admin/tasks/steam", json={"url_or_id": "292030"})
    assert r.status_code == 409

    # 无效输入 → 422
    r = client.post("/api/admin/tasks/steam", json={"url_or_id": "not-a-number"})
    assert r.status_code == 422

    # 暂停 → status_display 变化
    r = client.patch(f"/api/admin/tasks/steam/{d['id']}", json={"enabled": False})
    assert r.json()["data"]["status_display"] == "已暂停"

    # 恢复
    r = client.patch(f"/api/admin/tasks/steam/{d['id']}", json={"enabled": True})
    assert r.json()["data"]["status_display"] == "采集中"

    # 编辑 name
    r = client.patch(f"/api/admin/tasks/steam/{d['id']}", json={"name": "巫师 3"})
    assert r.json()["data"]["name"] == "巫师 3"

    # 删除
    r = client.delete(f"/api/admin/tasks/steam/{d['id']}")
    assert r.status_code == 200
    r = client.delete(f"/api/admin/tasks/steam/{d['id']}")
    assert r.status_code == 404


# ==================== B 站任务管理 ====================

def test_bili_task_create_pause_delete_rules(seeded_db, client, monkeypatch):
    client.post("/api/auth/login", json={"password": "test-pass-123"})

    monkeypatch.setattr(
        "src.api.routers._bili_lookup",
        lambda bv: (datetime(2026, 8, 25, 12, 0), "测试视频标题"),
    )

    # 新增（URL 形式）→ pubdate 识别成功 → scheduled，due = +7d
    r = client.post("/api/admin/tasks/bilibili", json={
        "url_or_id": "https://www.bilibili.com/video/BV1NEWVIDEO/", "note": "测试",
    })
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["bv_id"] == "BV1NEWVIDEO"
    assert d["status"] == "scheduled"
    assert d["status_display"] == "待采集"
    assert d["pubdate"].startswith("2026-08-25T12:00")
    assert d["due_date"].startswith("2026-09-01T12:00")

    # 重复 → 409
    r = client.post("/api/admin/tasks/bilibili", json={"url_or_id": "BV1NEWVIDEO"})
    assert r.status_code == 409

    # fetched 禁删 → 409
    fetched = client.get("/api/admin/tasks").json()["data"]["bilibili"]
    fetched_row = next(q for q in fetched if q["bv_id"] == "BV1TESTQ")
    r = client.delete(f"/api/admin/tasks/bilibili/{fetched_row['id']}")
    assert r.status_code == 409

    # fetched 可以暂停吗？规则：已采集无需暂停 → 409
    r = client.patch(f"/api/admin/tasks/bilibili/{fetched_row['id']}", json={"action": "pause"})
    assert r.status_code == 409

    # 新增的 scheduled → pause → resume
    r = client.patch(f"/api/admin/tasks/bilibili/{d['id']}", json={"action": "pause"})
    assert r.json()["data"]["status"] == "paused"
    r = client.patch(f"/api/admin/tasks/bilibili/{d['id']}", json={"action": "resume"})
    assert r.json()["data"]["status"] == "scheduled"  # pubdate 已识别

    # 重新识别（mock 返回新 pubdate）
    monkeypatch.setattr(
        "src.api.routers._bili_lookup",
        lambda bv: (datetime(2026, 8, 26, 9, 0), "新标题"),
    )
    r = client.patch(f"/api/admin/tasks/bilibili/{d['id']}", json={"action": "reidentify"})
    assert r.json()["data"]["pubdate"].startswith("2026-08-26T09:00")

    # 非 fetched 可删除
    r = client.delete(f"/api/admin/tasks/bilibili/{d['id']}")
    assert r.status_code == 200


def test_bili_task_invalid_bv_422(seeded_db, client):
    client.post("/api/auth/login", json={"password": "test-pass-123"})
    r = client.post("/api/admin/tasks/bilibili", json={"url_or_id": "av12345"})
    assert r.status_code == 422


# ---------- P1#1 回归：resume 守卫（仅 paused 可恢复，防 fetched 意外重采） ----------

def test_bili_resume_guard_refuses_non_paused(seeded_db, client, monkeypatch):
    """对抗审查 P1#1：对 scheduled/fetched 直接 resume 应 409，防止 due_date 在过去时被 cron 立即重采"""
    client.post("/api/auth/login", json={"password": "test-pass-123"})
    monkeypatch.setattr("src.api.routers._bili_lookup",
                        lambda bv: (datetime(2026, 8, 25, 12, 0), "测试"))

    r = client.post("/api/admin/tasks/bilibili", json={"url_or_id": "BV1GUARD1"})
    row_id = r.json()["data"]["id"]
    assert r.json()["data"]["status"] == "scheduled"  # pubdate 已识别

    # 直接 resume（未经 pause）→ 409
    r = client.patch(f"/api/admin/tasks/bilibili/{row_id}", json={"action": "resume"})
    assert r.status_code == 409

    # pause → resume 才允许
    client.patch(f"/api/admin/tasks/bilibili/{row_id}", json={"action": "pause"})
    r = client.patch(f"/api/admin/tasks/bilibili/{row_id}", json={"action": "resume"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "scheduled"

    # fetched 也不可 resume（模拟已采集行）
    from src.storage.db import BilibiliQueue, init_db
    _, S = init_db(f"sqlite:///{seeded_db}")
    with S() as s:
        s.get(BilibiliQueue, row_id).status = "fetched"
        s.commit()
    r = client.patch(f"/api/admin/tasks/bilibili/{row_id}", json={"action": "resume"})
    assert r.status_code == 409


# ---------- P1#4 回归：admin 已配置却缺 SESSION_SECRET_KEY → fail-closed ----------

def test_fail_closed_when_admin_without_session_secret(test_db_path, monkeypatch):
    """对抗审查 P1#4：ADMIN_PASSWORD_HASH 配了但 SESSION_SECRET_KEY 没配 → create_app 应抛 RuntimeError（公网下可伪造 cookie）"""
    from src.api.auth import hash_password
    from src.api.main import create_app

    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("any"))
    # 设为空串而非 delenv：create_app 内 load_dotenv(override=False) 会把被 delenv 的
    # 变量从真实 .env 装回来（2026-09-03 起 .env 已含真实 SESSION_SECRET_KEY）
    monkeypatch.setenv("SESSION_SECRET_KEY", "")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")

    with pytest.raises(RuntimeError, match="SESSION_SECRET_KEY 缺失"):
        create_app(db_url=f"sqlite:///{test_db_path}")


# ---------- P2#9 回归：登录限流（5 次/5 分钟，超出 429） ----------

def test_login_rate_limit(seeded_db, client):
    """对抗审查 P2#9：连续 5 次错误密码后第 6 次 429"""
    from src.api import auth

    auth._LOGIN_FAILURES.clear()  # 隔离：清掉前序测试累积的失败计数
    try:
        for _ in range(5):
            assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        r = client.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 429
        # 正确密码在限流期也不可登录（防爆破期间绕过）
        r = client.post("/api/auth/login", json={"password": "test-pass-123"})
        assert r.status_code == 429
    finally:
        auth._LOGIN_FAILURES.clear()  # 清理：避免污染后续测试


# ---------- P2#6 回归：backfill 状态可观测 ----------

def test_backfill_status_endpoint(seeded_db, client, monkeypatch):
    """对抗审查 P2#6：新增带 backfill 的任务后，/api/admin/backfill-status 可查到 job 状态"""
    from src.api import routers

    client.post("/api/auth/login", json={"password": "test-pass-123"})
    monkeypatch.setattr(routers, "_fetch_steam_game_name", lambda appid: f"Game {appid}")
    # stub _spawn_backfill：同步记 running 态，不起线程（避免测试竞态）
    def fake_spawn(platform, target_id, days=7):
        routers._backfill_status(platform, target_id, status="running",
                                 started_at="2026-09-02T00:00:00", fetched=None, error=None)
    monkeypatch.setattr(routers, "_spawn_backfill", fake_spawn)

    r = client.post("/api/admin/tasks/steam", json={"url_or_id": "292030", "backfill_days": 7})
    assert r.status_code == 201 and r.json()["data"]["backfill_started"] is True

    r = client.get("/api/admin/backfill-status")
    assert r.status_code == 200
    jobs = r.json()["data"]
    assert any(j["platform"] == "steam" and j["target_id"] == "292030"
               and j["status"] == "running" for j in jobs)


# ==================== 单游戏看板（2026-09-03）：时间窗 / 双颗粒度 / 全量零填充 ====================

@pytest.fixture
def ranged_db(test_db_path):
    """时间窗/颗粒度测试种子：3 条评论跨 40 天

    - c1（40 天前）：rating=1、sentiment=positive、topic=机制与内容、2 条 positive 观点
      （→ 观点粒度 pos=2 ≠ 原声粒度 pos=1，双口径可区分）
    - c2（3 天前）：rating=0、sentiment=negative、topic=技术与性能、1 条 negative 观点
    - c3（1 天前）：rating=None、sentiment=neutral、topic=None（本地模型无主题）、无观点
      （→ 趋势图该日 recommend_rate=None；原声主题聚合排除 NULL）
    """
    from src.storage.db import Comment, CommentOpinion, init_db

    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as s:
        def add(days_ago, sentiment, rating, topic, extra_json=None):
            c = Comment(
                platform="steam", source_id=f"rr{days_ago}", target_id="steam:2358720",
                content=f"评论{days_ago}", author="u", rating=rating,
                posted_at=now - timedelta(days=days_ago),
                sentiment=sentiment, sentiment_score=0.0, sentiment_confidence=0.9,
                topic=topic, analyzed_at=now,
                likes=10 + days_ago, replies=days_ago,
                extra_json=extra_json,
            )
            s.add(c)
            s.flush()
            return c

        c1 = add(40, "positive", 1, "机制与内容",
                 extra_json='{"appid": "2358720", "playtime_at_review": 600, "playtime_forever": 1200}')
        c2 = add(3, "negative", 0, "技术与性能")
        add(1, "neutral", None, None)  # c3
        s.add(CommentOpinion(comment_id=c1.id, full_path="机制与内容/核心机制与循环/战斗系统",
                             sentiment="positive", quote="q1"))
        s.add(CommentOpinion(comment_id=c1.id, full_path="叙事与世界观/剧情与叙事/主线剧情",
                             sentiment="positive", quote="q2"))
        s.add(CommentOpinion(comment_id=c2.id, full_path="技术与性能/稳定性与缺陷/程序崩溃·报错",
                             sentiment="negative", quote="q3"))
        s.commit()
    return test_db_path


def _win(now, ago_start, ago_end=None):
    """相对 now 的日期窗（YYYY-MM-DD 闭区间，ago_end 缺省 = 今天）"""
    end = datetime.now(timezone.utc).replace(tzinfo=None) if ago_end is None else now - timedelta(days=ago_end)
    return {"start": (now - timedelta(days=ago_start)).strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d")}


def test_overview_time_window(ranged_db, client):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # 全跨度 → 3 条
    r = client.get("/api/overview", params={"target": "steam:2358720", **_win(now, 45)})
    assert r.status_code == 200 and r.json()["data"]["total"] == 3
    # 近 2 天（排除 3 天前的 c2）→ 仅 c3
    r = client.get("/api/overview", params={"target": "steam:2358720", **_win(now, 2)})
    d = r.json()["data"]
    assert d["total"] == 1 and d["sentiment"]["neutral"] == 1


def test_overview_grain_dual_caliber(ranged_db, client):
    """原声 vs 观点双口径：c1 有 2 条 positive 观点 → 观点粒度 pos=2 ≠ 原声粒度 pos=1"""
    r = client.get("/api/overview", params={"target": "steam:2358720", "grain": "comment"})
    d = r.json()["data"]
    assert (d["sentiment"]["positive"], d["sentiment"]["negative"], d["sentiment"]["neutral"]) == (1, 1, 1)

    r = client.get("/api/overview", params={"target": "steam:2358720", "grain": "opinion"})
    d = r.json()["data"]
    assert (d["sentiment"]["positive"], d["sentiment"]["negative"], d["sentiment"]["neutral"]) == (2, 1, 0)
    assert d["opinion_total"] == 3
    assert d["total"] == 3  # total 恒为原声数，不受 grain 影响


def test_topics_full_zero_fill_primary_order(ranged_db, client):
    """full=true → 按 yaml primary 顺序返回全部 L1（零计数回填，含「综合与元表达」）"""
    r = client.get("/api/topics", params={
        "target": "steam:2358720", "level": "L1", "grain": "comment", "full": "true"})
    assert r.status_code == 200
    items = r.json()["data"]
    names = [t["topic"] for t in items]
    assert names[:3] == ["机制与内容", "操控与交互", "视觉与艺术"]  # yaml primary 固定顺序
    assert names[-1] == "综合与元表达"  # 末位元表达也显示
    by_name = {t["topic"]: t for t in items}
    assert by_name["机制与内容"]["total"] == 1 and by_name["机制与内容"]["positive"] == 1
    assert by_name["操控与交互"]["total"] == 0  # 无数据补 0
    # 默认（full 缺省）只返回有数据的、按总量 desc —— compare.js 既有行为不回归
    r = client.get("/api/topics", params={"target": "steam:2358720", "level": "L1"})
    assert {t["topic"] for t in r.json()["data"]} == {"机制与内容", "叙事与世界观", "技术与性能"}


def test_topics_grain_comment_topic_null_excluded(ranged_db, client):
    """原声主题按 comments.topic 聚合；topic=NULL（c3）排除"""
    r = client.get("/api/topics", params={"target": "steam:2358720", "level": "L1", "grain": "comment"})
    topics = {t["topic"]: t for t in r.json()["data"]}
    assert set(topics) == {"机制与内容", "技术与性能"}  # c3 无 topic 不出现
    assert topics["技术与性能"]["negative"] == 1


def test_topics_time_window(ranged_db, client):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    r = client.get("/api/topics", params={
        "target": "steam:2358720", "level": "L1", "grain": "opinion", **_win(now, 2)})
    assert {t["topic"] for t in r.json()["data"]} == set()  # 近 2 天无观点（c3 无观点）


def test_topics_grain_comment_level_l2_422(ranged_db, client):
    """grain=comment 时 level 必须 L1（comments.topic 只存 L1），非 L1 → 422"""
    r = client.get("/api/topics", params={
        "target": "steam:2358720", "level": "L2", "grain": "comment"})
    assert r.status_code == 422


def test_topics_tree_endpoint(ranged_db, client):
    r = client.get("/api/topics/tree")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["primary"][0] == "机制与内容" and d["fallback"] == "综合与元表达"
    assert "核心机制与循环" in d["hierarchy"]["机制与内容"]
    assert d["levels"] == ["L1", "L2", "L3"]


def test_trends_recommend_rate_and_null(ranged_db, client):
    """趋势每日补 recommend_rate；无 rating 日返回 null（前端折线断裂）"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    r = client.get("/api/trends", params={"target": "steam:2358720", **_win(now, 45)})
    assert r.status_code == 200
    by_day = {i["day"]: i for i in r.json()["data"]["items"]}
    assert len(by_day) == 3
    c1_day = (now - timedelta(days=40)).strftime("%Y-%m-%d")
    c2_day = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    c3_day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    assert by_day[c1_day]["recommend_rate"] == 100.0
    assert by_day[c2_day]["recommend_rate"] == 0.0
    assert by_day[c3_day]["recommend_rate"] is None  # rating_cnt=0 → null 而非 0


def test_trends_days_fallback_compatible(ranged_db, client):
    """start/end 缺省回落 days —— pages/trends.js 既有依赖不回归"""
    r = client.get("/api/trends", params={"target": "steam:2358720", "days": 2})
    items = r.json()["data"]["items"]
    assert len(items) == 1  # 仅 c3（1 天前）；days 参数继续可用


def test_invalid_date_range_422(ranged_db, client):
    r = client.get("/api/overview", params={"target": "steam:2358720", "start": "2026/01/01"})
    assert r.status_code == 422
    r = client.get("/api/trends", params={"start": "2026-05-01", "end": "2026-01-01"})
    assert r.status_code == 422


# ==================== 原声/观点列表（2026-09-03 需求变更） ====================

def test_comments_extra_and_grain_topic(ranged_db, client):
    """comments 列表：extra_json 解析游玩时长；grain=comment 时 topic 精确过滤；posted_at 降序"""
    r = client.get("/api/comments", params={
        "target": "steam:2358720", "grain": "comment", "topic": "机制与内容"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["total"] == 1
    item = d["items"][0]
    assert item["topic"] == "机制与内容"
    assert item["extra"]["playtime_at_review"] == 600
    assert item["extra"]["playtime_forever"] == 1200
    # posted_at 降序（不按 likes）
    r = client.get("/api/comments", params={"target": "steam:2358720", "grain": "comment"})
    days = [i["posted_at"] for i in r.json()["data"]["items"]]
    assert days == sorted(days, reverse=True)
    # grain=comment + topic=L2 → 无匹配（精确等于 L1）
    r = client.get("/api/comments", params={
        "target": "steam:2358720", "grain": "comment", "topic": "核心机制与循环"})
    assert r.json()["data"]["total"] == 0


def test_opinions_endpoint(ranged_db, client):
    """/api/opinions：观点分页列表，附所属原声；情感过滤在观点级"""
    r = client.get("/api/opinions", params={"target": "steam:2358720"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["total"] == 3
    item = d["items"][0]
    assert set(item) >= {"id", "comment_id", "full_path", "sentiment", "quote", "comment"}
    c = item["comment"]
    assert c["content"].startswith("评论")
    # c1（40 天前）的观点带游玩时长；首条可能是 c2 的观点（无 extra）
    with_extra = [i["comment"]["extra"] for i in d["items"] if i["comment"]["extra"]]
    assert any(e.get("playtime_forever") == 1200 for e in with_extra)
    # 观点级情感过滤（positive = c1 的 2 条观点）
    r = client.get("/api/opinions", params={"target": "steam:2358720", "sentiment": "positive"})
    d = r.json()["data"]
    assert d["total"] == 2 and all(i["sentiment"] == "positive" for i in d["items"])
    # topic 前缀过滤（L1）
    r = client.get("/api/opinions", params={"target": "steam:2358720", "topic": "叙事与世界观"})
    assert r.json()["data"]["total"] == 1
    # 分页
    r = client.get("/api/opinions", params={"target": "steam:2358720", "page_size": 2})
    assert len(r.json()["data"]["items"]) == 2


def test_opinions_invalid_grain_on_comments_422(ranged_db, client):
    r = client.get("/api/comments", params={"target": "steam:2358720", "grain": "bad"})
    assert r.status_code == 422


def test_targets_monitored_whitelist(seeded_db, client):
    """monitored=true → 仅返回 targets.yaml targets 段内的目标（6 款单机白名单）"""
    # 种子目标 2358720（黑神话：悟空）在白名单内 → 可见
    r = client.get("/api/targets", params={"platform": "steam", "monitored": "true"})
    ids = [t["target_id"] for t in r.json()["data"]]
    assert "steam:2358720" in ids
    # 默认（monitored 缺省）行为不变：不做白名单过滤
    r = client.get("/api/targets", params={"platform": "steam"})
    assert len(r.json()["data"]) >= len(ids)


# ==================== 游戏对比看板：game_meta / games/meta / recommend_count（2026-09-04） ====================

def test_games_meta_endpoint(ranged_db, client, monkeypatch):
    """/api/games/meta：stale-while-revalidate —— 立即返回 + 后台刷新标记；中文评级映射"""
    from src.api import service

    def fake_refresh(session, target_id):
        from datetime import date as _date

        from src.storage.db import GameMeta
        row = session.get(GameMeta, target_id)
        if row is None:
            session.add(GameMeta(
                target_id=target_id, release_date=_date(2024, 8, 20),
                rating_desc="好评如潮", review_score=9,
                total_reviews=100, total_positive=95, cover_file=None,
            ))
        session.commit()

    monkeypatch.setattr(service, "_refresh_game_meta", fake_refresh)
    r = client.get("/api/games/meta", params={"targets": "steam:2358720"})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["refreshing"] == ["steam:2358720"]  # 缺行 → 标记后台刷新（不阻塞响应）
    item = body["items"][0]
    assert item["target_id"] == "steam:2358720" and item["release_date"] is None

    # 模拟后台线程完成刷新（同步调用 fake）
    with client.app.state.SessionLocal() as s:
        service._refresh_game_meta(s, "steam:2358720")

    # 刷新落库后（review_score=9 齐全）：不再刷新，评级为中文
    r = client.get("/api/games/meta", params={"targets": "steam:2358720"})
    body = r.json()["data"]
    assert body["refreshing"] == []
    item = body["items"][0]
    assert item["release_date"] == "2024-08-20"
    assert item["rating_desc"] == "好评如潮" and item["review_score"] == 9

    r = client.get("/api/games/meta", params={"targets": ""})
    assert r.status_code == 422


def test_rating_desc_en2cn_fallback(ranged_db, client, monkeypatch):
    """存量行 review_score 缺失但 rating_desc 为英文 → 响应层映射为中文"""
    from datetime import datetime

    from src.api import service
    from src.storage.db import GameMeta

    def no_refresh(session, target_id):
        raise AssertionError("complete row should not refresh")

    # 直接写一行英文描述的"新鲜"行
    session_factory = client.app.state.SessionLocal
    with session_factory() as s:
        s.add(GameMeta(
            target_id="steam:2358720", rating_desc="Overwhelmingly Positive",
            review_score=None, total_reviews=1, total_positive=1,
            fetched_at=datetime.utcnow(),
        ))
        s.commit()

    monkeypatch.setattr(service, "_refresh_game_meta", no_refresh)
    r = client.get("/api/games/meta", params={"targets": "steam:2358720"})
    item = r.json()["data"]["items"][0]
    # review_score 缺失 → 仍标记刷新（会触发后台线程，但 monkeypatch 后 no-op 保护）
    assert r.json()["data"]["refreshing"] == ["steam:2358720"]
    assert item["rating_desc"] == "好评如潮"  # 英文兜底映射生效


def test_overview_recommend_count(ranged_db, client):
    """overview 补 recommend_count（库内 rating=1 计数，指标对比表用）"""
    r = client.get("/api/overview", params={"target": "steam:2358720"})
    d = r.json()["data"]
    assert d["recommend_count"] == 1  # c1 rating=1，c2 rating=0，c3 None
    # 时间窗内同样生效：近 2 天只有 c3（rating None）→ 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    r = client.get("/api/overview", params={
        "target": "steam:2358720",
        "start": (now - timedelta(days=2)).strftime("%Y-%m-%d"),
        "end": now.strftime("%Y-%m-%d"),
    })
    assert r.json()["data"]["recommend_count"] == 0


def test_parse_release_date():
    """Steam 发行日期解析：中文/英文格式 + 非法输入"""
    from src.api.service import _parse_release_date
    assert str(_parse_release_date({"date": "2024年8月20日"})) == "2024-08-20"
    assert str(_parse_release_date("20 Aug, 2024")) == "2024-08-20"
    assert _parse_release_date("即将推出") is None
    assert _parse_release_date(None) is None


# ==================== B站视频看板（2026-09-04）：视频快照 / 性别分布 / 30s 弹幕桶 / likes 排序 ====================

@pytest.fixture
def bili_db(test_db_path):
    """B站看板种子：1 个 fetched 视频（aid=42，含快照/高光）+ 4 评论（性别/点赞可区分）+ 5 弹幕"""
    import json as _json

    from src.storage.db import BilibiliQueue, Comment, CommentOpinion, Danmaku, init_db

    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as s:
        s.add(BilibiliQueue(
            bv_id="BV1TESTQ", status="fetched", aid=42, title="测试视频",
            pubdate=now - timedelta(days=30), pic="http://example/pic.jpg",
            owner_name="测试UP", owner_mid="100", view=1000,
            like_count=100, coin=10, favorite=20,
            reply_total=500, danmaku_total=900, duration=300,
            tags_json=_json.dumps(["游戏", "单机"], ensure_ascii=False),
            comment_count=4, danmaku_count=5,
            highlights_json=_json.dumps({
                "generated_at": "2026-09-04T00:00:00",
                "buckets": [{"start_sec": 60, "end_sec": 90, "count": 5, "summary": "高光总结内容"}],
            }, ensure_ascii=False),
        ))
        # likes 排序预期：b1(50) > b0(20) > b3(5) > b2(NULL 最后)
        for i, (sex, likes, days) in enumerate([("男", 20, 3), ("男", 50, 2), ("女", None, 1), ("男", 5, 1)]):
            c = Comment(
                platform="bilibili", source_id=f"b{i}", target_id="bilibili:video:42",
                content=f"弹幕评论{i}", author=f"u{i}", rating=None, likes=likes,
                posted_at=now - timedelta(days=days), sentiment="positive",
                sentiment_score=0.5, sentiment_confidence=0.9, topic="机制与内容",
                analyzed_at=now,
                extra_json=_json.dumps({"aid": 42, "profile": {"sex": sex}}, ensure_ascii=False),
            )
            s.add(c)
            s.flush()
            s.add(CommentOpinion(
                comment_id=c.id, full_path="机制与内容/核心机制与循环/战斗系统",
                sentiment="positive", quote=f"观点{i}",
            ))
        for i in range(5):
            s.add(Danmaku(video_id="bilibili:video:42", cid="123", content=f"弹幕{i}", progress=i * 40, mode=1))
        s.commit()
    return test_db_path


def test_bilibili_videos_endpoint(bili_db, client):
    """/api/bilibili/videos：快照 + 采集量 + 性别分布 + 高光解析"""
    r = client.get("/api/bilibili/videos")
    assert r.status_code == 200
    items = r.json()["data"]
    assert len(items) == 1
    v = items[0]
    assert v["target_id"] == "bilibili:video:42" and v["aid"] == 42
    assert v["view"] == 1000 and v["owner_name"] == "测试UP"
    assert v["tags"] == ["游戏", "单机"]
    assert v["collected"]["comments"] == 4
    assert v["sex"] == {"male": 3, "female": 1, "unknown": 0}
    assert v["highlights"]["buckets"][0]["summary"] == "高光总结内容"


def test_danmaku_30s_fixed_buckets(bili_db, client):
    """/api/danmaku：30s 固定桶（0/40/80/120/160s → 5 桶各 1 条）+ 每桶随机样本"""
    r = client.get("/api/danmaku/bilibili:video:42")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["width_sec"] == 30 and d["total"] == 5
    assert len(d["buckets"]) == 5  # 40s 间隔分布在 5 个 30s 桶
    assert all(b["count"] == 1 for b in d["buckets"])
    assert all(0 < len(b["samples"]) <= 10 for b in d["buckets"])
    assert d["buckets"][0]["start_sec"] == 0 and d["buckets"][0]["end_sec"] == 30


def test_comments_sort_likes(bili_db, client):
    """sort=likes：点赞降序，NULL 最后；非法 sort → 422"""
    r = client.get("/api/comments", params={
        "target": "bilibili:video:42", "grain": "comment", "sort": "likes"})
    likes = [i["likes"] for i in r.json()["data"]["items"]]
    assert likes == [50, 20, 5, None]

    r = client.get("/api/comments", params={
        "target": "bilibili:video:42", "grain": "comment", "sort": "bad"})
    assert r.status_code == 422


# ==================== 系统管理：查找 + 子模块架构（2026-09-05） ====================

def test_admin_lookup(seeded_db, client, monkeypatch):
    """/api/admin/tasks/lookup：Steam 返回名称+发行日期；B站返回标题+投稿；未登录 401"""
    # 未登录 → 401
    assert client.get("/api/admin/tasks/lookup",
                      params={"platform": "steam", "url_or_id": "2358720"}).status_code == 401
    client.post("/api/auth/login", json={"password": "test-pass-123"})

    from src.collectors.steam import SteamCollector
    monkeypatch.setattr(SteamCollector, "fetch_app_info",
                        lambda self, appid: {"name": "测试游戏",
                                             "release_date": {"date": "2024年8月20日"}})
    r = client.get("/api/admin/tasks/lookup",
                   params={"platform": "steam", "url_or_id":
                           "https://store.steampowered.com/app/2358720/"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["name"] == "测试游戏" and d["release_date"] == "2024-08-20"

    import src.queue.cli as _cli
    from datetime import datetime as _dt
    monkeypatch.setattr(_cli, "_lookup_pubdate",
                        lambda bv: (_dt(2026, 8, 20, 2, 0), "测试视频标题"))
    r = client.get("/api/admin/tasks/lookup",
                   params={"platform": "bilibili", "url_or_id": "BV1TESTQ"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["title"] == "测试视频标题" and d["pubdate"] == "2026-08-20T02:00:00"

    # 查询不到 → 404；平台非法 → 422
    monkeypatch.setattr(SteamCollector, "fetch_app_info", lambda self, appid: {})
    r = client.get("/api/admin/tasks/lookup",
                   params={"platform": "steam", "url_or_id": "9999999"})
    assert r.status_code == 404
    r = client.get("/api/admin/tasks/lookup",
                   params={"platform": "wii", "url_or_id": "x"})
    assert r.status_code == 422


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
