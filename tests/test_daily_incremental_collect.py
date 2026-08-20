"""P6 自动化采集编排测试

锁住的回归（详见 docs/plan/P6_AUTOMATION_PIPELINE.md §4.2）：
1. 空库起步 → 采集后 DB 写入正确
2. 有库起步 → 只新增（不覆盖已有 likes_refreshed_at）
3. 时间窗计算正确（max(posted_at) - 1 天）
4. 单 target 失败不阻塞其他 target

每个用例用 data/test_*.db（已被 .gitignore 排除 *.db），绝不碰 data/voc.db；
参照 scripts/dev/e2e_lifecycle.py 的「独立测试 DB」模式。
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------- fixtures ----------

@pytest.fixture
def test_db_path():
    """每个用例分配独立测试 DB（data/test_<uuid>.db，不污染 data/voc.db）。"""
    db = ROOT / "data" / f"voc_test_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    return db


@pytest.fixture(autouse=True)
def _isolated_env(test_db_path, monkeypatch):
    """设置 DATABASE_URL 指向独立测试 DB；抑制真实 embedder/analyzer 加载。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setattr("src.pipeline.get_embedder", lambda: None)
    yield
    # 清理：测试结束删 DB
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except OSError:
            pass


def _write_targets_yaml(targets: list[dict]) -> Path:
    """写一个临时的 targets.yaml（用 tempfile，避免依赖 sandbox 写权限）。"""
    import tempfile

    fd, raw_path = tempfile.mkstemp(suffix=".yaml", prefix="voc_targets_")
    os.close(fd)
    p = Path(raw_path)
    try:
        import yaml
        with p.open("w", encoding="utf-8") as f:
            yaml.safe_dump({"version": 1, "targets": targets}, f, allow_unicode=True)
        return p
    except Exception:
        p.unlink(missing_ok=True)
        raise


# ---------- fake 数据层 ----------

def _make_fake_collector(comments: list):
    """替代 SteamCollector：每次 collect 都返回同一份 raws"""
    class _FakeCollector:
        def fetch_app_info(self, target_id):
            return {"name": f"Game {target_id}", "type": "game"}

        def collect(self, target_id, max_count=50, language="schinese",
                    posted_after=None, posted_before=None):
            return list(comments)

    return _FakeCollector


def _fake_analyzer_factory():
    """返回固定结果的分析器"""
    from src.analyzers.base import AnalysisResult

    class _FakeAnalyzer:
        name = "fake"

        def analyze(self, text, *, context=None):
            return AnalysisResult(
                sentiment="positive",
                sentiment_score=0.5,
                sentiment_confidence=0.9,
                topic="玩法与内容",
                opinions=[],
            )

    return _FakeAnalyzer()


def _make_raw_comment(source_id: str, content: str, appid: str, posted_at):
    from src.collectors.base import RawComment
    return RawComment(
        platform="steam",
        source_id=source_id,
        content=content,
        author_id=f"u-{source_id}",
        rating=1,
        language="schinese",
        posted_at=posted_at,
        extra={"appid": appid},
    )


# ---------- 用例 1：空库起步 → 采集后 DB 写入正确 ----------

def test_load_targets_and_first_run_writes_to_empty_db(monkeypatch):
    """空库起步 → 加载 targets.yaml → 跑 run_one_target → DB 中应写入目标评论"""
    from sqlalchemy import select
    from src.storage.db import init_db, Comment

    targets_cfg = _write_targets_yaml([{
        "platform": "steam", "id": "999", "name": "Test Game",
        "language": "schinese", "count": 3, "enabled": True,
    }])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    raws = [_make_raw_comment(f"r{i}", f"content-{i}", "999", now) for i in range(3)]

    fake_collector = _make_fake_collector(raws)
    from src.pipeline import COLLECTORS
    monkeypatch.setitem(COLLECTORS, "steam", fake_collector)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: _fake_analyzer_factory())

    from scripts.ops.daily_incremental_collect import load_targets, run_one_target
    targets = load_targets(targets_cfg)
    assert len(targets) == 1
    assert targets[0]["id"] == "999"

    result = run_one_target(targets[0])
    assert result["ok"] is True
    assert result["fetched"] == 3
    assert result["analyzed"] == 3

    engine, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment)).scalars())
        assert len(rows) == 3
        for r in rows:
            assert r.target_id == "steam:999"
            assert r.platform == "steam"


# ---------- 用例 2a：时间窗计算（max - 1 天） ----------

def test_calc_posted_after_uses_max_minus_one_day(monkeypatch):
    """时间窗起点 = 该目标在 DB 中 max(posted_at) 减 1 天"""
    from src.storage.db import init_db, CommentRepository

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old_ts = now - timedelta(days=5)
    recent_ts = now - timedelta(days=1)
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        repo.bulk_upsert([
            _make_raw_comment("r-old", "old content", "999", old_ts),
            _make_raw_comment("r-new", "new content", "999", recent_ts),
        ])

    from scripts.ops.daily_incremental_collect import calc_posted_after
    posted_after = calc_posted_after("steam:999", lookback_days=1)

    expected = recent_ts - timedelta(days=1)
    assert posted_after == expected, f"应为 {expected}，实际 {posted_after}"


# ---------- 用例 2b：目标无数据 → 返回 None ----------

def test_calc_posted_after_returns_none_when_no_data(monkeypatch):
    """目标在 DB 中无数据 → 返回 None（全量起步）"""
    from src.storage.db import init_db

    init_db()  # 建空表
    from scripts.ops.daily_incremental_collect import calc_posted_after
    posted_after = calc_posted_after("steam:nonexistent")
    assert posted_after is None


# ---------- 用例 3：有库起步 → 不覆盖已有 likes_refreshed_at ----------

def test_incremental_run_preserves_existing_data(monkeypatch):
    """二次采集不应擦掉已有评论的 likes / likes_refreshed_at（冷启动 NULL 语义）"""
    from sqlalchemy import select
    from src.storage.db import init_db, Comment, CommentRepository

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        repo.bulk_upsert([_make_raw_comment("r1", "existing content", "999", now - timedelta(days=3))])
        # 模拟已回采（写入 likes + 时间戳）
        existing = list(s.execute(select(Comment)).scalars())[0]
        existing.likes = 42
        existing.likes_refreshed_at = now - timedelta(days=1)
        s.commit()

    # 再跑一次 run_one_target：raws 是空（增量语义：模拟没有新评论）
    fake_collector = _make_fake_collector([])
    from src.pipeline import COLLECTORS
    monkeypatch.setitem(COLLECTORS, "steam", fake_collector)
    monkeypatch.setattr("src.pipeline.get_analyzer", lambda provider=None: _fake_analyzer_factory())

    from scripts.ops.daily_incremental_collect import load_targets, run_one_target
    targets_cfg = _write_targets_yaml([{
        "platform": "steam", "id": "999", "name": "Test",
        "language": "schinese", "count": 30, "enabled": True,
    }])
    targets = load_targets(targets_cfg)
    result = run_one_target(targets[0])

    # 验证：likes=42 likes_refreshed_at 没被擦掉
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        rows = list(s.execute(select(Comment)).scalars())
        assert len(rows) == 1
        assert rows[0].likes == 42, f"已有 likes 应保留，实际为 {rows[0].likes}"
        assert rows[0].likes_refreshed_at == now - timedelta(days=1), "已有 likes_refreshed_at 应保留"


# ---------- 用例 4：单 target 失败不阻塞其他 target ----------

def test_single_target_failure_does_not_block_others(monkeypatch):
    """run_one_target 内部异常被捕获 → 后续 target 仍应正常执行"""
    from scripts.ops.daily_incremental_collect import load_targets, run_one_target

    targets_cfg = _write_targets_yaml([
        {"platform": "steam", "id": "fail-id", "name": "Bad", "language": "schinese", "count": 30, "enabled": True},
        {"platform": "steam", "id": "ok-id", "name": "Good", "language": "schinese", "count": 30, "enabled": True},
    ])

    call_count = {"n": 0}

    def fake_run_pipeline(**kwargs):
        call_count["n"] += 1
        if kwargs.get("target_id") == "fail-id":
            raise RuntimeError("simulated network error")
        return {"fetched": 1, "analyzed": 1, "embedded": 0}

    monkeypatch.setattr("scripts.ops.daily_incremental_collect.run_pipeline", fake_run_pipeline)

    targets = load_targets(targets_cfg)
    results = [run_one_target(t) for t in targets]

    assert call_count["n"] == 2, "应尝试两个 target"
    assert results[0]["ok"] is False
    assert "simulated network error" in results[0]["error"]
    assert results[1]["ok"] is True
    assert results[1]["fetched"] == 1


# ---------- 用例 5（额外）：gh_release_exists 在 gh 不可用时不应崩 ----------

def test_gh_release_exists_handles_missing_gh_cli(monkeypatch):
    """本地无 gh CLI 时不应抛异常（仅返回 False）"""
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=["gh"], returncode=127, stderr="gh: not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from scripts.ops.daily_incremental_collect import gh_release_exists
    assert gh_release_exists("nonexistent") is False


# ---------- main（直接跑时） ----------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])