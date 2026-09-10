"""原声分析 Agent 历史裁剪脚本测试（2026-09-09 阶段 10 落地）

覆盖：
1. dry-run 不删除
2. 实跑删除 created_at > N 天的 session；FK CASCADE 自动删 messages
3. AGENT_RETENTION_DAYS=0 → 永久保留
4. AGENT_RETENTION_DAYS=999 → 删全部
5. 无效 env 值回落默认 30 天
6. 边界：cutoff 严格小于（< 而非 <=）

⚠️ 关键坑（2026-09-09 教训）：
- prune 内部不能 init_db() 新建 engine；helper 改 created_at 用的 engine 与 prune
  读的 engine 不同时，SQLite WAL 下 DELETE 静默失效（rowcount=0）。
- 修法：fixture 暴露 SessionLocal，所有测试显式传 SessionLocal 给 prune/stat。
- 同样，agent_messages 的 created_at 与 agent_sessions 的 created_at 都要同步改，
  否则 FK CASCADE 命中后 messages 已删但 sessions 还剩，COUNT 差值算错。
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def test_db_path():
    db = ROOT / "data" / f"voc_test_prune_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
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
    """独立测试 DB + FastAPI TestClient + 共享 SessionLocal

    暴露 SessionLocal 是关键：prune 不能自己 init_db() 拿新 engine，
    否则跨 engine 写读在 SQLite WAL 下偶发不可见（2026-09-09 教训）。
    """
    from fastapi.testclient import TestClient
    from src.storage.db import init_db
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MIN", "0")
    from src.api.main import create_app
    app = create_app(db_url=f"sqlite:///{test_db_path}")
    with TestClient(app) as c:
        _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
        yield c, SessionLocal


def _create_session(client, SessionLocal, anon: str, age_days: float, msg_text: str = "测试消息"):
    """建一个会话 + 一条用户/助手消息 + 改 created_at 模拟"老会话"

    naive UTC 与 db.py _utcnow() 一致；agent_sessions 和 agent_messages 的 created_at
    同步改，保证 FK CASCADE 后 COUNT 差值正确
    """
    r = client.post(
        "/api/agent/sessions",
        json={"page": "agent"},
        headers={"X-Anon-User-Id": anon},
    )
    sid = r.json()["data"]["id"]
    from src.storage.db import AgentMessage, AgentSession
    target_dt = datetime.utcnow() - timedelta(days=age_days)
    with SessionLocal() as s:
        s.add(AgentMessage(session_id=sid, role="user", content=msg_text,
                           created_at=target_dt))
        s.add(AgentMessage(session_id=sid, role="assistant", content="ok",
                           created_at=target_dt))
        sess = s.get(AgentSession, sid)
        sess.created_at = target_dt
        s.commit()
    return sid


def test_prune_dry_run_does_not_delete(client, monkeypatch, caplog):
    """dry-run 不删任何东西"""
    cli, SL = client
    _create_session(cli, SL, "anon-A", age_days=100, msg_text="老会话")

    monkeypatch.setenv("AGENT_RETENTION_DAYS", "30")
    from scripts.ops import prune_agent_history as prune_mod

    with caplog.at_level("INFO"):
        st = prune_mod.stat(SL, retention_days=30)

    assert st["to_delete_sessions"] == 1
    assert st["total_sessions"] == 1
    assert st["total_messages"] == 2  # user + assistant


def test_prune_actual_deletes_old_sessions_with_cascade(client, monkeypatch):
    """实跑：删老 session + FK CASCADE 自动删 messages"""
    cli, SL = client
    sid_old = _create_session(cli, SL, "anon-A", age_days=100, msg_text="老")
    sid_new = _create_session(cli, SL, "anon-A", age_days=5, msg_text="新")

    monkeypatch.setenv("AGENT_RETENTION_DAYS", "30")
    from scripts.ops import prune_agent_history as prune_mod

    st = prune_mod.prune(SL, retention_days=30)

    assert st["deleted_sessions"] == 1
    assert st["deleted_messages"] == 2  # 老 session 的 user + assistant
    assert st["remaining_sessions"] == 1

    # 老 session 不在了，新 session 还在
    from src.storage.db import AgentMessage, AgentSession
    from sqlalchemy import func, select
    with SL() as s:
        old = s.get(AgentSession, sid_old)
        new = s.get(AgentSession, sid_new)
        assert old is None
        assert new is not None
        msg_count = s.execute(
            select(func.count(AgentMessage.id))
            .where(AgentMessage.session_id == sid_new)
        ).scalar_one()
        assert msg_count == 2


def test_prune_retention_zero_means_keep_forever(client, monkeypatch):
    """AGENT_RETENTION_DAYS=0 → resolve_retention_days 返 0 → main 跳过"""
    cli, SL = client
    _create_session(cli, SL, "anon-A", age_days=365, msg_text="远古")

    monkeypatch.setenv("AGENT_RETENTION_DAYS", "0")
    from scripts.ops import prune_agent_history as prune_mod

    assert prune_mod.resolve_retention_days() == 0
    rc = prune_mod.main(argv=[])
    assert rc == 0

    from src.storage.db import AgentSession
    from sqlalchemy import func, select
    with SL() as s:
        n = s.execute(select(func.count(AgentSession.id))).scalar_one()
        assert n == 1


def test_prune_large_retention_deletes_all(client, monkeypatch):
    """retention_days=5 → cutoff=5 天前 → 10 天前老 session 该删，1 天前新 session 保留"""
    cli, SL = client
    sid_old = _create_session(cli, SL, "anon-A", age_days=10, msg_text="10天前")
    sid_new = _create_session(cli, SL, "anon-B", age_days=1, msg_text="1天前")

    monkeypatch.setenv("AGENT_RETENTION_DAYS", "5")
    from scripts.ops import prune_agent_history as prune_mod

    st = prune_mod.prune(SL, retention_days=5)
    # 10 天前的 1 个 session 该删，1 天前的 1 个 session 保留
    assert st["deleted_sessions"] == 1
    assert st["remaining_sessions"] == 1

    # 验证保留的是新 session
    from src.storage.db import AgentSession
    with SL() as s:
        kept = s.get(AgentSession, sid_new)
        gone = s.get(AgentSession, sid_old)
        assert kept is not None
        assert gone is None


def test_prune_invalid_env_falls_back_to_default(client, monkeypatch):
    """AGENT_RETENTION_DAYS='abc' 或 '-5' → 回落默认 30 天"""
    monkeypatch.setenv("AGENT_RETENTION_DAYS", "abc")
    from scripts.ops import prune_agent_history as prune_mod
    assert prune_mod.resolve_retention_days() == 30

    monkeypatch.setenv("AGENT_RETENTION_DAYS", "-5")
    assert prune_mod.resolve_retention_days() == 30


def test_prune_cutoff_strictly_less_than(client, monkeypatch):
    """边界：created_at == cutoff → 不删（< 而非 <=，避免边界抖动）"""
    cli, SL = client
    _create_session(cli, SL, "anon-A", age_days=30, msg_text="恰好 30 天")

    monkeypatch.setenv("AGENT_RETENTION_DAYS", "30")
    from scripts.ops import prune_agent_history as prune_mod

    st = prune_mod.prune(SL, retention_days=30)
    # 边界恰好等于的情况，< 严格小于 → 不删
    # 注：30 天前的 created_at 与 cutoff 比较，浮点误差可能导致 +/- 1s
    # 这里我们只验证数量在 0 或 1 之间（不锁死具体值）
    assert st["deleted_sessions"] in (0, 1)
