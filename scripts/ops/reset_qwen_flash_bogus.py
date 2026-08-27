"""P11 · 清理 8/24-25 QWEN-flash 模型名 404 留下的假数据标

背景（详见 .workbuddy/memory/2026-08-25.md）：
- 8/24-25 跑过 3 个 QWEN-flash 模型名（`qwen3.7-flash` / `qwen3-flash` / `qwen3.6-flash`）
  全部 API 404
- `src/analyzers/sentiment_llm.py` 的 catch 块把整个 batch 静默标 `sentiment="neutral"，
  analyzer_version="llm:qwen3-flash@55c003a3"`
- 8/27 sync 后查 DB：261 条评论被污染，集中在 posted_at 8/17~8/25
- 不影响后续 cron 抓新数据（uniqueness + analyzed_at 过滤），但 DEEPSEEK pipeline 看
  `analyzed_at NOT NULL` 会跳过，必须主动 reset 才能重打

设计：
- 一次性脚本，按 AGENTS.md §1 §2 归 `scripts/ops/` + 动词命名
- 默认 dry-run（打印预演改写统计），`--commit` 才真正执行
- 命令式 UPDATE 直接定位 bogus 标记，不依赖文件 / config
- 幂等：bogus 已经是 NULL 的行不影响（UPDATE 是 SET，无论原值都行）
- 跑完保留分析器版本号（如果同 analyzer_version 还得重打，可手动跑后续）

使用：
    # 默认 dry-run 预览
    python scripts/ops/reset_qwen_flash_bogus.py

    # 真正执行
    python scripts/ops/reset_qwen_flash_bogus.py --commit
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.reset_qwen_bogus")


BOGUS_VERSION = "llm:qwen3-flash@55c003a3"  # 见 .workbuddy/memory/2026-08-25.md
BOGUS_VERSION_LIKE = "llm:qwen%"            # 兜底：所有 qwen 家族的 bogus 都清


def find_bogus_rows(session) -> list[dict]:
    """查 bogus 假数据行"""
    from sqlalchemy import select, func

    from src.storage.db import Comment

    stmt = (
        select(
            Comment.platform,
            Comment.target_id,
            func.count(Comment.id).label("cnt"),
        )
        .where(Comment.analyzer_version == BOGUS_VERSION)
        .group_by(Comment.platform, Comment.target_id)
        .order_by(Comment.platform, Comment.target_id)
    )
    rows = session.execute(stmt).all()
    return [{"platform": p, "target_id": t, "count": c} for p, t, c in rows]


def do_reset(session, *, like_pattern: bool = False) -> int:
    """把 bogus 标记的分析结果清掉，让后续 cron 重新打标

    Returns:
        int: 更新的行数
    """
    from sqlalchemy import update

    from src.storage.db import Comment

    pattern = BOGUS_VERSION_LIKE if like_pattern else BOGUS_VERSION

    stmt = (
        update(Comment)
        .where(Comment.analyzer_version.like(pattern))
        .values(
            analyzed_at=None,
            sentiment=None,
            sentiment_score=None,
            sentiment_confidence=None,
            topic=None,
            sub_topics=None,
            analyzer_version=None,
        )
    )
    result = session.execute(stmt)
    session.commit()
    return result.rowcount or 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="P11 · 清理 8/24-25 QWEN-flash 模型 404 留下的假数据标"
    )
    p.add_argument(
        "--commit",
        action="store_true",
        help="默认 dry-run；--commit 才真正 UPDATE 改库",
    )
    p.add_argument(
        "--like",
        action="store_true",
        help="宽松匹配（同时清掉所有 llm:qwen% 假数据，不仅是 flash）",
    )
    args = p.parse_args()

    from src.storage.db import init_db

    _, SessionLocal = init_db()

    with SessionLocal() as s:
        rows = find_bogus_rows(s)
        if not rows:
            log.info("✅ DB 中无 bogus 行（analyzer_version=%s），无需清理", BOGUS_VERSION)
            return 0

        log.info(f"发现 {len(rows)} 个 target 有 bogus 数据：")
        total = 0
        for r in rows:
            log.info(f"  · {r['platform']:9s} {r['target_id']:34s} {r['count']:>5}")
            total += r["count"]
        log.info(f"合计 {total} 条评论将被清理（重置 analyzed_at + 清分析字段）")

        if not args.commit:
            log.warning("[dry-run] 加 --commit 真正执行")
            return 0

        log.warning(">>> 即将执行 UPDATE，请确认")
        n = do_reset(s, like_pattern=args.like)
        log.info(f"✅ 已清理 {n} 行 bogus 数据")
        log.info(
            "下一步：等明早 daily cron（或手动 dispatch），会让 DEEPSEEK 重新打这些评论"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
