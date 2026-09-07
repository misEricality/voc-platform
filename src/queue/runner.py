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

from sqlalchemy import select, update  # noqa: E402

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
        fetched = report.get("fetched", 0)
        # 2026-09-07 对抗审查：B站采集 0 条评论 = 异常（队列视频均为有评论的实机
        # 演示；代理 TUN 模式下 reply 接口会从海外出口返回「code=0 但空数据」，
        # 风控也可能空响应）——此前会静默标 fetched + comment_count=0。改判失败
        # 进重试/dead-letter，让问题可见。
        if fetched == 0:
            return {
                "ok": False,
                "fetched": 0,
                "analyzed": 0,
                "danmaku": 0,
                "error": "采集 0 条评论（疑似风控/地区限制/代理分流，或视频无评论）",
            }
        return {
            "ok": True,
            "fetched": fetched,
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


def _reclaim_orphan_fetching(session) -> int:
    """回收孤儿 fetching 行（2026-09-06 对抗审查）。

    run_due_collection 在**认领任何行之前**调用：此刻库里的 status=fetching 必然来自
    上一次崩溃/被冻结/断电的 run（本 run 串行认领，不存在并发认领窗口）。不回收则
    孤儿行永远无人认领——9/5~9/6 两次卡死 fetching 事故的直接成因。

    并发例外：若手动 run-due 与 02:00 daily 极小概率同时跑，回收会导致其中一方
    重复采集——upsert 幂等 + analyzed_at 跳过保证无重复数据与重复 LLM 成本，可接受。
    """
    n = (
        session.execute(
            update(BilibiliQueue)
            .where(BilibiliQueue.status == "fetching")
            .values(status="scheduled")
        ).rowcount
    )
    session.commit()
    if n:
        log.warning("回收 %d 个孤儿 fetching 行 → scheduled", n)
    return n


def _reidentify_pending(session, *, limit: int = 5) -> int:
    """重试 pending 行的投稿日期识别（2026-09-06 对抗审查）。

    创建任务时 B站 view 识别失败（风控 412 / 网络抖动）→ status=pending、due_date=null，
    此前**永远**不会被 run-due 认领（_select_due 只查 scheduled）。本函数每日给 pending
    行一次重新识别机会：成功 → scheduled（due=pubdate+7，旧视频立即到期）；
    失败 → 保持 pending 明天再试。
    """
    from src.queue.cli import _lookup_pubdate  # lazy：cli 懒加载 runner，避免循环导入

    rows = list(
        session.execute(
            select(BilibiliQueue)
            .where(BilibiliQueue.status == "pending")
            .order_by(BilibiliQueue.id)
            .limit(limit)
        ).scalars()
    )
    ok = 0
    for row in rows:
        pubdate, title = _lookup_pubdate(row.bv_id)
        if pubdate:
            row.pubdate = pubdate
            row.due_date = pubdate + timedelta(days=7)
            if title:
                row.title = title
            row.status = "scheduled"
            ok += 1
            log.info("pending 重识别成功：%s → scheduled（due=%s）", row.bv_id, row.due_date)
        else:
            log.warning("pending 重识别仍失败：%s（明天再试）", row.bv_id)
    session.commit()
    return ok


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
        # 前置修复（2026-09-06）：孤儿 fetching 回收 + pending 重识别（dry-run 不动库）
        if not dry_run:
            report["reclaimed"] = _reclaim_orphan_fetching(s)
            report["reidentified"] = _reidentify_pending(s, limit=5)

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