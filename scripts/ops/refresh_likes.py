"""一次性回采脚本：对"发布满 N 天"的评论回采互动数据

业务背景：
  首次采集时下列字段没有业务价值（冷启动 0/空容易误读），所以首次入库为 NULL：
    - votes_up / comment_count（点赞数 / 评论数）
    - developer_response（开发者回复，开发者可能晚回）
  评论发布满 7 天后，点赞与开发者回复基本稳定，这时回采一次性价比最高。

用法：
  # 默认回采所有 ≥7 天的评论（基于 posted_at 字段）
  python -m scripts.refresh_likes --platform steam --target 730

  # 自定义阈值（调试用）
  python -m scripts.refresh_likes --platform steam --target 730 --min-age-days 0

设计要点：
  - 只对 posted_at < now - min_age_days 的评论生效
  - 通过 Steam appreviews 接口全量重拉取（按 recommendationid 对账）
  - 不调用 LLM、不修改 LLM 标注结果
  - upsert 时 likes/replies/developer_response 有值，会自动写入对应 refreshed_at
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import or_, select

# 项目根目录注入：scripts/ops/refresh_likes.py → ../../../ （即 voc_platform/）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
load_dotenv()

from src.collectors.steam import SteamCollector
from src.storage.db import Comment, CommentRepository, init_db

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.refresh_likes")


def refresh_likes(
    platform: str,
    target_id: str,
    min_age_days: int = 7,
    language: str = "schinese",
    max_count: int = 10000,
) -> dict:
    """回采指定目标 ≥min_age_days 天的评论的互动数据

    Args:
        platform: 平台名
        target_id: 目标 ID（Steam appid）
        min_age_days: 评论发布至少多少天才回采（默认 7）
        language: Steam 语言过滤
        max_count: 翻页安全上限（默认 10000），实际命中目标后即停止

    Returns:
        报告字典
    """
    log.info(
        f"===== 开始回采：platform={platform} target={target_id} "
        f"min_age_days={min_age_days} ====="
    )

    engine, SessionLocal = init_db()
    collector = SteamCollector()

    # 1. 从 DB 拉出需要回采的 source_ids
    #    条件：发布时间足够老 且 likes 或 developer_response 任意一个尚未回采
    #    这样脚本可幂等运行：重复调用不会重复处理完全回采过的评论
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=min_age_days)
    with SessionLocal() as s:
        rows = s.execute(
            select(Comment.source_id, Comment.posted_at)
            .where(
                Comment.platform == platform,
                Comment.target_id == f"{platform}:{target_id}",
                Comment.posted_at < cutoff,
                or_(
                    Comment.likes_refreshed_at.is_(None),
                    Comment.developer_response_refreshed_at.is_(None),
                ),
            )
        ).all()

    if not rows:
        log.info("没有需要回采的评论（要么太少，要么都还不够 N 天）")
        return {"refreshed": 0, "skipped": 0, "total_target": 0}

    log.info(f"找到 {len(rows)} 条 ≥{min_age_days} 天前的评论，准备回采")
    target_ids = {src for (src, _) in rows}

    # 2. 单次游标续翻回采（fetch_metadata=True 时会写入 likes/replies/developer_response）
    #    必须用 filter="recent" 按时间排序拉取。
    #    原因：Steam 默认 ranking（"all" 按 helpfulness）会随 votes_up 变化，跨时间拉取
    #    同一游戏的 Top 100 评论 ID 集合不稳定；按时间排序则 source_id 稳定，便于对账。
    #
    #    重要：fetch_comments 内部已按 cursor 前进翻页，这里必须一次性遍历生成器，
    #    不能循环调用 collect() —— collect() 每次都会从 cursor="*" 重新开始，
    #    导致永远只看最新一页（历史 bug：likes 从未真正回采成功）。
    #    max_count 是"翻页安全上限"（防止 Steam 返回过多数据时失控）。
    matched = 0
    skipped = 0
    fetched_total = 0
    with SessionLocal() as s:
        repo = CommentRepository(s)
        for raw in collector.fetch_comments(
            target_id,
            max_count=max_count,
            language=language,
            fetch_metadata=True,
            filter="recent",
        ):
            fetched_total += 1
            if raw.source_id in target_ids:
                repo.upsert(raw)
                matched += 1
                if matched >= len(target_ids):
                    break
            else:
                skipped += 1
        repo.commit()

    if matched < len(target_ids):
        log.warning(
            f"回采未覆盖全部目标：匹配 {matched}/{len(target_ids)}。"
            "可能原因：Steam recent 流只返回较新评论，老评论已超出可翻页范围；"
            "可尝试增大 --max-count 或按 posted_at 分批处理老评论。"
        )

    log.info(
        f"回采完成：matched={matched}, skipped={skipped}, total_fetched={fetched_total} "
        f"（skipped 是 Steam 返回但 DB 没有的评论）"
    )
    return {
        "platform": platform,
        "target_id": target_id,
        "min_age_days": min_age_days,
        "matched": matched,
        "skipped": skipped,
        "fetched_total": fetched_total,
    }


def main():
    p = argparse.ArgumentParser(description="回采发布满 N 天的评论点赞数 / 回复数")
    p.add_argument("--platform", default="steam")
    p.add_argument("--target", required=True, help="目标 ID（Steam appid）")
    p.add_argument("--language", default="schinese")
    p.add_argument(
        "--min-age-days",
        type=int,
        default=7,
        help="评论发布至少多少天才回采（默认 7）",
    )
    p.add_argument(
        "--max-count",
        type=int,
        default=10000,
        help="单次最大拉取条数（默认 10000）",
    )
    args = p.parse_args()

    report = refresh_likes(
        platform=args.platform,
        target_id=args.target,
        min_age_days=args.min_age_days,
        language=args.language,
        max_count=args.max_count,
    )
    log.info(f"===== 报告：{report} =====")


if __name__ == "__main__":
    main()
