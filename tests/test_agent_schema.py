"""原声分析 Agent 数据表 schema 回归测试（2026-09-09）

锁住的回归（详见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §3 数据模型变更）：
1. init_db 自动创建 agent_sessions + agent_messages 两张表
2. anon_user_id 列存在（用于匿名用户隔离）
3. FK ON DELETE CASCADE 生效（删 session 自动级联删 messages；依赖 PRAGMA foreign_keys=ON）
4. 关键索引齐全（anon_user_id / anon_user_id+updated_at / session_id+created_at）
5. to_dict 序列化字段完整

每个用例用独立测试 DB（data/voc_test_*.db），绝不碰 data/voc.db。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def test_db_path():
    """每个用例分配独立测试 DB（data/voc_test_<uuid>.db）。"""
    db = ROOT / "data" / f"voc_test_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    yield db
    # WAL 模式会产生 -wal/-shm 副文件，一并清理
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


@pytest.fixture
def session_factory(test_db_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    from src.storage.db import init_db

    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    return SessionLocal


# ==================== 1. 表与列 ====================


def test_tables_created(test_db_path):
    """init_db 应同时创建 agent_sessions 和 agent_messages 两张表"""
    from sqlalchemy import inspect

    from src.storage.db import init_db

    engine, _ = init_db(f"sqlite:///{test_db_path}")
    insp = inspect(engine)
    tables = insp.get_table_names()
    assert "agent_sessions" in tables, "agent_sessions 表应被创建"
    assert "agent_messages" in tables, "agent_messages 表应被创建"


def test_agent_sessions_columns(test_db_path):
    """agent_sessions 应包含全部预期列（含 anon_user_id）"""
    from sqlalchemy import inspect, text

    from src.storage.db import init_db

    engine, _ = init_db(f"sqlite:///{test_db_path}")
    cols = {row[1]: row[2] for row in engine.connect().execute(
        text("PRAGMA table_info(agent_sessions)")
    ).fetchall()}

    # SQLite 编译：String(N) → VARCHAR(N)；Text → TEXT；DATETIME 不带参数也写作 DATETIME
    # 这里只校验"列存在 + 类型属于可接受的字符串/时间族"，不严格区分 VARCHAR/TEXT
    expected = {
        "id": ("TEXT", "VARCHAR"),
        "page": ("TEXT", "VARCHAR"),
        "page_context": ("TEXT", "VARCHAR"),
        "title": ("TEXT", "VARCHAR"),
        "model": ("TEXT", "VARCHAR"),
        "anon_user_id": ("TEXT", "VARCHAR"),  # 2026-09-09 新增
        "created_at": ("DATETIME",),
        "updated_at": ("DATETIME",),
    }
    for col, allowed_types in expected.items():
        assert col in cols, f"agent_sessions 缺列：{col}"
        actual = cols[col].upper()
        assert any(t in actual for t in allowed_types), \
            f"agent_sessions.{col} 类型应匹配 {allowed_types}，实际 {cols[col]}"


def test_agent_messages_columns(test_db_path):
    """agent_messages 应包含全部预期列"""
    from sqlalchemy import text

    from src.storage.db import init_db

    engine, _ = init_db(f"sqlite:///{test_db_path}")
    cols = {row[1]: row[2] for row in engine.connect().execute(
        text("PRAGMA table_info(agent_messages)")
    ).fetchall()}

    expected = {"id", "session_id", "role", "content", "tool_calls",
                "tool_call_id", "tool_name", "created_at"}
    assert expected.issubset(cols.keys()), f"agent_messages 缺列：{expected - set(cols.keys())}"


# ==================== 2. 索引 ====================


def test_critical_indexes_created(test_db_path):
    """关键索引必须齐全（性能与查询模式相关）"""
    from sqlalchemy import text

    from src.storage.db import init_db

    engine, _ = init_db(f"sqlite:///{test_db_path}")
    with engine.connect() as conn:
        sess_idx = {row[1] for row in conn.execute(
            text("PRAGMA index_list(agent_sessions)")
        ).fetchall()}
        msg_idx = {row[1] for row in conn.execute(
            text("PRAGMA index_list(agent_messages)")
        ).fetchall()}

    expected_sess = {"ix_agent_session_page", "ix_agent_session_updated",
                     "ix_agent_session_anon", "ix_agent_session_anon_updated"}
    expected_msg = {"ix_agent_msg_session"}
    missing = expected_sess - sess_idx
    assert not missing, f"agent_sessions 缺索引：{missing}"
    missing = expected_msg - msg_idx
    assert not missing, f"agent_messages 缺索引：{missing}"


# ==================== 3. FK CASCADE（核心：30 天裁剪依赖）====================


def test_foreign_keys_pragma_enabled(test_db_path):
    """PRAGMA foreign_keys 必须 ON（CASCADE 才生效）"""
    from sqlalchemy import text

    from src.storage.db import init_db

    engine, _ = init_db(f"sqlite:///{test_db_path}")
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert int(result) == 1, f"PRAGMA foreign_keys 应为 1（ON），实际 {result}"


def test_fk_cascade_deletes_messages(session_factory):
    """删除 session 应级联删除其 messages（30 天裁剪的核心依赖）"""
    from src.storage.db import AgentSession, AgentMessage

    SessionLocal = session_factory
    sid = str(uuid.uuid4())
    anon = str(uuid.uuid4())

    with SessionLocal() as s:
        sess = AgentSession(id=sid, page="dashboard", model="deepseek-v4-flash", anon_user_id=anon)
        # 用 relationship 直接挂载 messages（SQLAlchemy 会按依赖顺序 flush）
        sess.messages = [
            AgentMessage(role="user", content="hi"),
            AgentMessage(role="assistant", content="hello"),
            AgentMessage(role="tool", content="{}", tool_call_id="c1", tool_name="query_overview"),
        ]
        s.add(sess)
        s.commit()

        # 删 session → 关系 cascade + DB FK CASCADE 双保险
        s.delete(sess)
        s.commit()

        msg_count = s.query(AgentMessage).filter(AgentMessage.session_id == sid).count()
    assert msg_count == 0, f"FK CASCADE 应删除全部 messages，实际剩余 {msg_count} 条"


# ==================== 4. 序列化 ====================


def test_to_dict_serialization(session_factory):
    """to_dict 应输出完整字段（含 anon_user_id）"""
    from src.storage.db import AgentSession

    SessionLocal = session_factory
    sid = str(uuid.uuid4())
    anon = str(uuid.uuid4())

    with SessionLocal() as s:
        sess = AgentSession(
            id=sid,
            page="compare",
            page_context='{"target_id": "steam:2358720"}',
            title="对比测试",
            model="deepseek-v4-flash",
            anon_user_id=anon,
        )
        s.add(sess)
        s.commit()
        d = sess.to_dict()

    expected_keys = {"id", "page", "page_context", "title", "model",
                     "anon_user_id", "created_at", "updated_at"}
    assert set(d.keys()) == expected_keys
    assert d["id"] == sid
    assert d["anon_user_id"] == anon
    assert d["page"] == "compare"
    assert d["created_at"] is not None


# ==================== 5. init_db 幂等（重启不重建）====================


def test_init_db_idempotent(test_db_path):
    """二次 init_db 不应报错（表已存在 → 跳过 create_all）"""
    from src.storage.db import init_db

    _, _ = init_db(f"sqlite:///{test_db_path}")
    # 二次调用：表已存在，create_all 跳过；自动演进也无新列
    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    assert SessionLocal is not None
