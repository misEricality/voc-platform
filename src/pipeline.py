"""VoC 主流程编排

串联「数据采集 → 持久化 → AI 分析」三个阶段。

使用：
    python -m src.pipeline --platform steam --target 730 --count 50
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# 让脚本可直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()  # 自动加载 .env

from src.collectors.steam import SteamCollector
from src.collectors.bilibili import BilibiliCollector
from src.storage.db import init_db, CommentRepository
from src.analyzers import get_analyzer
from src.analyzers.embedder import get_embedder, MODEL_NAME

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.pipeline")


# 注册可用的采集器
COLLECTORS = {
    "steam": SteamCollector,
    "bilibili": BilibiliCollector,
}


def run_pipeline(
    platform: str,
    target_id: str,
    max_count: int = 50,
    language: str = "schinese",
    analyzer_provider: str | None = None,
    skip_analysis: bool = False,
    posted_after: datetime | None = None,
    posted_before: datetime | None = None,
) -> dict:
    """运行单平台采集+分析流程

    Args:
        platform: 平台名（steam）
        target_id: 目标ID（Steam appid）
        max_count: 采集数量
        language: 语言过滤（项目默认 schinese；Steam 顶层原则只采中文）
        analyzer_provider: 分析器后端
        skip_analysis: 仅采集不分析
        posted_after: 起始时间过滤（datetime 对象）
        posted_before: 截止时间过滤（datetime 对象，应用层）

    Returns:
        执行报告字典
    """
    if platform not in COLLECTORS:
        raise ValueError(f"暂不支持的平台: {platform}，可选: {list(COLLECTORS.keys())}")

    log.info(f"===== 开始 VoC 流程：{platform} target={target_id} =====")

    # 1. 初始化数据库
    engine, SessionLocal = init_db()
    session = SessionLocal()
    repo = CommentRepository(session)

    # 2. 采集
    log.info(f"[1/3] 采集数据：platform={platform}, target={target_id}, count={max_count}")
    collector = COLLECTORS[platform]()
    target_meta = {}
    db_lookup_target = target_id  # list_by_target 用（内部拼 {platform}:）
    db_full_target = f"{platform}:{target_id}"  # 向量化查询用

    # Steam 特殊处理：补充游戏元数据
    if platform == "steam":
        info = collector.fetch_app_info(target_id)
        if info:
            target_meta = {"name": info.get("name"), "type": info.get("type")}
            log.info(f"  游戏名称：{target_meta.get('name')}")

    # B 站特殊处理：视频元数据（view）→ target_meta；评论后补弹幕
    if platform == "bilibili":
        info = collector.fetch_video_info(target_id)
        if info:
            target_meta = {
                "name": info.get("title"),
                "type": "video",
                "bvid": info.get("bvid"),
                "aid": info.get("aid"),
                "cid": info.get("cid"),
                "tid": info.get("tid"),
                "tname": info.get("tname"),
                "pubdate": info.get("pubdate"),
                "owner": info.get("owner"),
                "desc": info.get("desc"),
                "stat": info.get("stat"),
                "tags": info.get("tags"),
            }
            log.info(f"  视频名称：{target_meta.get('name')}（评论 {info['stat'].get('reply')} | 弹幕 {info['stat'].get('danmaku')}）")
            if info.get("aid"):
                db_lookup_target = f"video:{info['aid']}"
                db_full_target = f"bilibili:video:{info['aid']}"

    raws = collector.collect(
        target_id,
        max_count=max_count,
        language=language,
        posted_after=posted_after,
        posted_before=posted_before,
    )
    log.info(f"  采集到 {len(raws)} 条原始评论")

    if not raws:
        log.warning("未采集到任何数据，退出")
        session.close()
        return {"fetched": 0, "analyzed": 0}

    # 3. 持久化
    log.info(f"[2/3] 写入数据库...")
    inserted = repo.bulk_upsert(raws, target_meta=target_meta)
    log.info(f"  处理 {inserted} 条")

    # 3.2 B 站弹幕（弹幕不进打标链路，仅入库；失败不阻塞主流程）
    danmaku_count = 0
    if platform == "bilibili" and target_meta.get("cid"):
        try:
            items = collector.fetch_danmaku(target_meta["cid"])
            danmaku_count = repo.save_danmaku(
                db_full_target, str(target_meta["cid"]), items
            )
            log.info(f"  [2.2] 弹幕入库 {danmaku_count} 条（分片后 {len(items)} 条）")
        except Exception as e:
            log.warning(f"  [2.2] 弹幕采集失败（不阻塞主流程）: {e}")

    # 3.5 向量化（新增评论 → 语义向量，失败不阻塞；与打标解耦，skip_analysis 时也执行）
    embed_count = 0
    try:
        embedder = get_embedder()
        if embedder is None:
            log.warning("  [2.5] embedder 不可用，跳过向量化（可安装 sentence-transformers 后重跑）")
        else:
            # 防线 1（写入侧软降级）：表内已有其他模型 → 跳过并提示迁移，不混写
            existing_models = repo.embedding_models_in_use()
            if existing_models and set(existing_models) != {MODEL_NAME}:
                log.warning(
                    f"  [2.5] 表内向量模型 {existing_models} ≠ 当前 {MODEL_NAME}，"
                    f"跳过向量化；请先跑 backfill_embeddings.py --force 全量重算"
                )
            else:
                missing_ids = repo.find_missing_embedding_ids(
                    platform=platform,
                    target_id=db_full_target,
                    limit=max_count,
                )
                if missing_ids:
                    comments = repo.get_comments_by_ids(missing_ids)
                    vecs = embedder.encode_batch([c.content for c in comments])
                    embed_count = repo.save_embeddings(
                        [c.id for c in comments], vecs, embedder.model_name, embedder.dim
                    )
                    log.info(f"  [2.5] 向量化 {embed_count} 条（模型 {embedder.model_name}，dim={embedder.dim}）")
                else:
                    log.info("  [2.5] 无新增评论需要向量化")
    except Exception as e:
        log.warning(f"  [2.5] 向量化失败（不阻塞主流程）: {e}")

    # 4. 分析
    analyzed_count = 0
    if not skip_analysis:
        log.info(f"[3/3] AI 分析...")
        try:
            analyzer = get_analyzer(analyzer_provider)
            log.info(f"  使用分析器：{analyzer.name}")
        except (ValueError, ImportError) as e:
            log.warning(f"  分析器初始化失败：{e}")
            log.warning(f"  已跳过分析阶段。可在 .env 中配置 API Key 后重试。")
            analyzer = None

        if analyzer:
            # 查询刚入库的评论（按目标）
            comments = repo.list_by_target(platform, db_lookup_target, limit=max_count)
            for c in comments:
                if c.analyzed_at is not None:
                    continue
                result = analyzer.analyze(c.content, context={"platform": platform, "target_id": target_id})
                # 方案4：topic 已由 analyzer 从核心观点映射；观点（opinions）随主流程落库
                repo.update_analysis(
                    c.id,
                    sentiment=result.sentiment,
                    sentiment_score=result.sentiment_score,
                    sentiment_confidence=result.sentiment_confidence,
                    topic=result.topic,
                    opinions=[op.to_dict() for op in result.opinions],
                )
                analyzed_count += 1
            repo.commit()
            log.info(f"  完成 {analyzed_count} 条分析")

    session.close()

    report = {
        "platform": platform,
        "target_id": target_id,
        "target_meta": target_meta,
        "fetched": len(raws),
        "analyzed": analyzed_count,
        "embedded": embed_count,
        "danmaku": danmaku_count,
    }
    log.info(f"===== 流程完成：{report} =====")
    return report


def main():
    parser = argparse.ArgumentParser(description="VoC 数据采集与分析流水线")
    parser.add_argument("--platform", default="steam", choices=list(COLLECTORS.keys()))
    parser.add_argument("--target", required=True, help="目标ID（Steam appid）")
    parser.add_argument("--count", type=int, default=50, help="采集数量")
    parser.add_argument("--language", default="schinese", help="语言过滤")
    parser.add_argument(
        "--analyzer",
        default=None,
        choices=["deepseek", "qwen", "glm", "local"],
        help="分析器后端",
    )
    parser.add_argument("--skip-analysis", action="store_true", help="仅采集不分析")
    parser.add_argument(
        "--posted-after",
        type=str,
        default=None,
        help="起始时间过滤，格式 YYYY-MM-DD。例：2026-08-03",
    )
    parser.add_argument(
        "--posted-before",
        type=str,
        default=None,
        help="截止时间过滤，格式 YYYY-MM-DD。例：2026-08-04",
    )
    args = parser.parse_args()

    # 解析日期字符串
    posted_after = None
    posted_before = None
    if args.posted_after:
        posted_after = datetime.strptime(args.posted_after, "%Y-%m-%d")
    if args.posted_before:
        posted_before = datetime.strptime(args.posted_before, "%Y-%m-%d")

    run_pipeline(
        platform=args.platform,
        target_id=args.target,
        max_count=args.count,
        language=args.language,
        analyzer_provider=args.analyzer,
        skip_analysis=args.skip_analysis,
        posted_after=posted_after,
        posted_before=posted_before,
    )


if __name__ == "__main__":
    main()