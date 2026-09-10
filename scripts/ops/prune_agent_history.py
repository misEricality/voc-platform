"""原声分析 Agent 对话历史裁剪（计划任务 VOC-Local-Agent-Prune · 03:30）

职责：删除超过保留期（默认 30 天）的 agent_sessions，agent_messages 走 FK CASCADE 自动清理。

设计依据（docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §3.3）：
- 默认保留 30 天（env `AGENT_RETENTION_DAYS` 覆盖；0 = 永久保留）
- 裁剪只看 `created_at`（不用 `updated_at`）：每天活跃的会话仍可保 30 天
- 跑前后输出 rowcount 日志，便于审计
- FK CASCADE 依赖 `PRAGMA foreign_keys=ON`（init_db 已开，db.py:545）
- 用 SQLAlchemy ORM + raw SQL DELETE 命中 CASCADE 级联
- SessionLocal 由调用方注入（CLI 走 _get_session_local()；测试走 fixture）——避免
  SQLite WAL 下跨 SQLAlchemy engine 偶发看不到对方写入的隔离陷阱（2026-09-09 教训）

用法：
    python scripts/ops/prune_agent_history.py            # 实际裁剪（计划任务用）
    python scripts/ops/prune_agent_history.py --dry-run  # 只统计不删除

最后更新：2026-09-09
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.storage.db import (  # noqa: E402  -- sys.path 注入后 import
    AgentMessage,
    AgentSession,
    init_db,
)
from sqlalchemy import func, select, text  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.ops.prune_agent_history")

DEFAULT_RETENTION_DAYS = 30


def resolve_retention_days() -> int:
    """从 env 读 AGENT_RETENTION_DAYS（每请求读，不缓存——支持测试 / 运行时调阈值）"""
    raw = os.getenv("AGENT_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        days = int(raw)
    except ValueError:
        log.warning(f"AGENT_RETENTION_DAYS={raw!r} 非整数，回落默认 {DEFAULT_RETENTION_DAYS} 天")
        return DEFAULT_RETENTION_DAYS
    if days < 0:
        log.warning(f"AGENT_RETENTION_DAYS={days} 为负数，回落默认 {DEFAULT_RETENTION_DAYS} 天")
        return DEFAULT_RETENTION_DAYS
    return days


def cutoff_dt(retention_days: int) -> datetime:
    """cutoff = now - retention_days（naive UTC datetime 对象）

    返 datetime 而非 ISO 字符串：SQLAlchemy bind datetime 到 DateTime 列做类型一致比较，
    避免 SQLite 把 str 字符串当 datetime 解析时格式不一致（"T" vs " " 分隔）导致
    cutoff < created_at 字符串序 True 但实际 SQL 比较 False 的陷阱（2026-09-09 教训）
    """
    return datetime.utcnow() - timedelta(days=retention_days)


def _get_session_local():
    """CLI 路径用：从 init_db() 拿 SessionLocal（生产场景，跨进程无共享）

    ⚠️ 测试不要用！测试必须由 fixture 注入 SessionLocal，避免跨 engine WAL 隔离
    （2026-09-09 教训：SQLite WAL 下不同 SQLAlchemy engine 的连接偶发看不到对方写入）
    """
    _, SessionLocal = init_db()
    return SessionLocal


def stat(SessionLocal, retention_days: int) -> dict:
    """统计会删多少条（不实际删除）

    SessionLocal 由调用方注入（CLI 走 _get_session_local()；测试走 fixture），
    保证 stat/prune 与前置写入用同一 engine。
    """
    cutoff = cutoff_dt(retention_days)
    with SessionLocal() as s:
        sess_count = s.execute(
            select(func.count(AgentSession.id)).where(AgentSession.created_at < cutoff)
        ).scalar_one()
        # FK CASCADE 影响的 messages 数：从所有将被删 session 派生
        msg_count = s.execute(
            select(func.count(AgentMessage.id))
            .join(AgentSession, AgentMessage.session_id == AgentSession.id)
            .where(AgentSession.created_at < cutoff)
        ).scalar_one()
        total_sess = s.execute(select(func.count(AgentSession.id))).scalar_one()
        total_msg = s.execute(select(func.count(AgentMessage.id))).scalar_one()
    return {
        "cutoff": cutoff.isoformat(),
        "retention_days": retention_days,
        "to_delete_sessions": sess_count,
        "to_delete_messages": msg_count,
        "total_sessions": total_sess,
        "total_messages": total_msg,
    }


def prune(SessionLocal, retention_days: int) -> dict:
    """实际裁剪；返回删除统计

    SessionLocal 由调用方注入（同 stat）。

    2026-09-09 教训：
    1. SQLAlchemy ORM Core DELETE 的 result.rowcount 在 SQLite 偶发返 0
       （SELECT 显示有命中但 DELETE rowcount=0），用 before/after COUNT 差值兜底
    2. 用 raw SQL DELETE 保证 FK CASCADE 生效（init_db 已 PRAGMA foreign_keys=ON）
    """
    cutoff = cutoff_dt(retention_days)
    with SessionLocal() as s:
        before_sess = s.execute(select(func.count(AgentSession.id))).scalar_one()
        before_msg = s.execute(select(func.count(AgentMessage.id))).scalar_one()

        s.execute(
            text("DELETE FROM agent_sessions WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        s.commit()

        after_sess = s.execute(select(func.count(AgentSession.id))).scalar_one()
        after_msg = s.execute(select(func.count(AgentMessage.id))).scalar_one()

    return {
        "cutoff": cutoff.isoformat(),
        "retention_days": retention_days,
        "deleted_sessions": before_sess - after_sess,
        "deleted_messages": before_msg - after_msg,
        "remaining_sessions": after_sess,
        "remaining_messages": after_msg,
        "before_sessions": before_sess,
        "before_messages": before_msg,
    }


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="原声分析 Agent 对话历史裁剪（AGENT_RETENTION_DAYS 控制保留天数）",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计不删除（计划任务默认实跑；调试可用 dry-run）")
    args = parser.parse_args(argv)

    retention = resolve_retention_days()
    if retention == 0:
        log.info("AGENT_RETENTION_DAYS=0 → 永久保留，跳过裁剪")
        return 0

    # CLI 路径独立 init_db 拿 SessionLocal（生产场景，无 fixture 注入）
    SessionLocal = _get_session_local()

    if args.dry_run:
        st = stat(SessionLocal, retention)
        log.info(
            f"[dry-run] cutoff={st['cutoff']} retention={st['retention_days']}d "
            f"将删 sessions={st['to_delete_sessions']} messages={st['to_delete_messages']} "
            f"（库内现存 sessions={st['total_sessions']} messages={st['total_messages']}）"
        )
        return 0

    st = prune(SessionLocal, retention)
    log.info(
        f"prune done: cutoff={st['cutoff']} retention={st['retention_days']}d "
        f"deleted sessions={st['deleted_sessions']} messages={st['deleted_messages']} "
        f"remaining sessions={st['remaining_sessions']} messages={st['remaining_messages']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
