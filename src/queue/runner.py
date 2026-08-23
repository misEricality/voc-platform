"""B 站采集队列执行器

被 CLI `run-due` 和 workflow 每日 cron 共用。

逻辑：
1. 查 status=scheduled 且 due_date <= today 的视频，按 due_date 排序，limit 上限
2. 逐个调 `src.pipeline.run_pipeline(platform='bilibili', target_id=bv_id)`
3. 成功 → 标 fetched，记录 comment_count / danmaku_count
4. 失败 → fail_count += 1；3 次失败标 failed（dead-letter）
5. 返回 report dict

未来扩展：
- 串行 vs 并发（默认串行，限速友好）
- 单批耗时分块（避免 workflow 30min 超时）
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from src.storage.db import (  # noqa: E402
    BilibiliQueue,
    _utcnow,
    init_db,
)


log = logging.getLogger("voc.queue.runner")

# 失败重试阈值：单视频失败超过此次数 → 入 dead-letter（status=failed）
MAX_FAIL_COUNT = 3


def _select_due(session, *, limit: int, today_naive_utc: datetime) -> list[BilibiliQueue]:
    """查今天到期的待采条目"""
    stmt = (
        select(BilibiliQueue)
        .where(BilibiliQueue.status == "scheduled")
        .where(BilibiliQueue.due_date <= today_naive_utc)
        .order_by(BilibiliQueue.due_date)
        .limit(limit)
    )
    return list(session.execute(stmt).scalars())


def _run_pipeline(bv_id: str) -> dict:
    """调一次 pipeline，返回 {ok, fetched, analyzed, error}

    注意：pipeline 是同步阻塞调用，单视频可能要 1-3 分钟（按 BILIBILI_COLLECTION §五
    实测）。如需并发可改用 asyncio + httpx，但目前阶段 0 保持简单串行。
    """
    from src.pipeline import run_pipeline  # lazy import

    try:
        report = run_pipeline(
            platform="bilibili",
            target_id=bv_id,
            max_count=None,  # 让 collector 内部决定全量/抽样
            language=None,
            posted_after=None,
            posted_before=None,
            skip_analysis=False,
        )
        return {
            "ok": True,
            "fetched": report.get("fetched", 0),
            "analyzed": report.get("analyzed", 0),
            "danmaku": report.get("danmaku", 0),  # 若 pipeline 已包含
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "fetched": 0,
            "analyzed": 0,
            "danmaku": 0,
            "error": f"{type(e).__name__}: {e}",
        }


def run_due_collection(*, limit: int = 50, dry_run: bool = False) -> dict[str, Any]:
    """扫描今天到期的待采条目并触发采集。

    Args:
        limit: 单次最多处理多少个视频（防风控）
        dry_run: True 则只扫描、不实际采集

    Returns:
        {
            "due_found": int,
            "fetched": int,
            "failed": int,
            "skipped": int,
            "errors": list[str],
        }
    """
    today_naive = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    _, SessionLocal = init_db()

    report = {
        "due_found": 0,
        "fetched": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    with SessionLocal() as s:
        rows = _select_due(s, limit=limit, today_naive_utc=today_naive)
        report["due_found"] = len(rows)

        if not rows:
            log.info("今天无到期任务（due_date <= %s）", today_naive.date())
            return report

        log.info("找到 %d 个到期任务，开始处理", len(rows))

        for row in rows:
            bv = row.bv_id
            log.info("── %s ──", bv)

            if dry_run:
                log.info("  [DRY-RUN] 跳过")
                report["skipped"] += 1
                continue

            # 先标 fetching（防止 cron 重复执行时多进程同采）
            row.status = "fetching"
            s.commit()

            result = _run_pipeline(bv)

            if result["ok"]:
                row.status = "fetched"
                row.fetched_at = _utcnow()
                row.comment_count = result.get("fetched")
                row.danmaku_count = result.get("danmaku")
                row.fail_count = 0
                row.fail_reason = None
                report["fetched"] += 1
                log.info(
                    "  [OK] fetched=%d analyzed=%d danmaku=%d",
                    result["fetched"], result["analyzed"], result["danmaku"],
                )
            else:
                row.fail_count = (row.fail_count or 0) + 1
                row.fail_reason = result["error"]
                if row.fail_count >= MAX_FAIL_COUNT:
                    row.status = "failed"
                    report["failed"] += 1
                    log.warning(
                        "  [DEAD-LETTER] 失败 %d 次，入 dead-letter：%s",
                        row.fail_count, result["error"],
                    )
                else:
                    # 仍放回 scheduled，下次 cron 重试
                    row.status = "scheduled"
                    log.warning(
                        "  [FAIL #%d] %s（下次 cron 再试）",
                        row.fail_count, result["error"],
                    )
                report["errors"].append(f"{bv}: {result['error']}")

            s.commit()

    return report