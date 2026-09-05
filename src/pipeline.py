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


def _download_bili_cover(bvid: str, pic_url: str) -> bool:
    """B 站封面本地化（2026-09-04 视频看板）：下载 pic 到 data/covers/{bvid}.jpg

    与 Steam 封面同策略（预览环境 B 站 CDN 图加载失败）。失败返回 False（前端回退 CDN）。
    """
    import requests

    covers = Path(__file__).resolve().parent.parent / "data" / "covers"
    try:
        covers.mkdir(parents=True, exist_ok=True)
        dest = covers / f"{bvid}.jpg"
        if dest.exists() and dest.stat().st_size > 0:
            return True
        r = requests.get(
            pic_url, timeout=15,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"},
        )
        if r.status_code == 200 and r.content:
            dest.write_bytes(r.content)
            return True
    except Exception as e:  # noqa: BLE001
        log.warning(f"  封面下载失败（不阻塞）: {e}")
    return False


def _snapshot_bili_queue(bv_id: str, info: dict) -> None:
    """B 站视频快照落库（2026-09-04 视频看板）：view API 元数据 → bilibili_queue 快照列

    队列无行时（存量数据未入队，如直接跑 pipeline / sync 来的库）**按 fetched 自动建行**，
    并补采集量统计 —— 回填脚本依赖此行为自愈。
    失败由调用方兜底（不阻塞采集主流程）。
    """
    import json as _json
    from datetime import datetime, timezone as _tz

    from sqlalchemy import func as _func
    from sqlalchemy import select as _select

    from src.storage.db import BilibiliQueue, Comment, Danmaku, _utcnow, init_db

    _, S = init_db()
    stat = info.get("stat") or {}
    owner = info.get("owner") or {}
    with S() as s:
        row = s.execute(
            _select(BilibiliQueue).where(BilibiliQueue.bv_id == bv_id)
        ).scalar_one_or_none()
        if row is None and info.get("aid"):
            # 队列无行 → 按 fetched 创建（bv_id 用 view 返回的真实 bvid）
            tid = f"bilibili:video:{info['aid']}"
            n_c = s.execute(_select(_func.count(Comment.id)).where(
                Comment.target_id == tid, Comment.platform == "bilibili")).scalar() or 0
            n_d = s.execute(_select(_func.count(Danmaku.id)).where(
                Danmaku.video_id == tid)).scalar() or 0
            pubdate = None
            if info.get("pubdate"):
                pubdate = datetime.fromtimestamp(info["pubdate"], tz=_tz.utc).replace(tzinfo=None)
            row = BilibiliQueue(
                bv_id=info.get("bvid") or bv_id, status="fetched", pubdate=pubdate,
                comment_count=int(n_c), danmaku_count=int(n_d), fetched_at=_utcnow(),
            )
            s.add(row)
        if row is None:
            return
        row.aid = info.get("aid") or row.aid
        row.title = info.get("title") or row.title
        row.pic = info.get("pic") or row.pic
        row.owner_name = owner.get("name") or row.owner_name
        row.owner_mid = str(owner.get("mid")) if owner.get("mid") else row.owner_mid
        row.view = stat.get("view") or row.view
        row.like_count = stat.get("like") or row.like_count
        row.coin = stat.get("coin") or row.coin
        row.favorite = stat.get("favorite") or row.favorite
        row.reply_total = stat.get("reply") or row.reply_total
        row.danmaku_total = stat.get("danmaku") or row.danmaku_total
        row.duration = info.get("duration") or row.duration
        if info.get("tags"):
            row.tags_json = _json.dumps(info["tags"], ensure_ascii=False)
        row.stats_fetched_at = _utcnow()
        saved_bv = row.bv_id
        s.commit()
    # 封面本地化（在 DB 会话外执行；失败不阻塞）
    if info.get("pic") and saved_bv:
        _download_bili_cover(saved_bv, info["pic"])
    log.info(f"  视频快照已写入 bilibili_queue（bv={saved_bv}）")


def _generate_danmaku_highlights(aid: int, *, top_n: int = 3, provider: str = "deepseek") -> None:
    """弹幕高光 LLM 总结（2026-09-04 视频看板；采集时一次性完成，结果落 highlights_json）

    取 30s 固定桶中弹幕量最多的 top_n 个，按时长升序逐桶调 LLM 总结。
    """
    import json as _json

    from sqlalchemy import select as _select

    from src.analyzers.danmaku_summary import summarize_bucket
    from src.storage.db import (
        BilibiliQueue,
        Danmaku,
        _utcnow,
        bucket_danmaku_rows,
        init_db,
    )

    video_id = f"bilibili:video:{aid}"
    _, S = init_db()
    with S() as s:
        rows = list(s.execute(
            _select(Danmaku).where(Danmaku.video_id == video_id)
        ).scalars())
        if not rows:
            log.info("  高光总结跳过：该视频无弹幕")
            return
        buckets = bucket_danmaku_rows(rows)
        top = sorted(buckets, key=lambda b: -b["count"])[:top_n]
        top = sorted(top, key=lambda b: b["start_sec"])  # 左起按时长排列

        import random as _random

        out = []
        for b in top:
            texts = [r.content for r in b["rows"] if r.content]
            # 120 条随机抽样（2026-09-04 工程师确认）：桶内弹幕多时避免只取前段
            if len(texts) > 120:
                texts = _random.sample(texts, 120)
            if not texts:
                continue
            summary = summarize_bucket(texts, provider=provider)
            out.append({
                "start_sec": b["start_sec"], "end_sec": b["end_sec"],
                "count": b["count"], "summary": summary,
            })
            log.info(f"  高光 {b['start_sec']}~{b['end_sec']}s（{b['count']} 条）：{summary[:36]}…")

        row = s.execute(
            _select(BilibiliQueue).where(BilibiliQueue.aid == aid)
        ).scalar_one_or_none()
        if row is None:
            log.warning("  高光总结落库跳过：bilibili_queue 无 aid=%s 行", aid)
            return
        row.highlights_json = _json.dumps(
            {"generated_at": _utcnow().isoformat(), "buckets": out},
            ensure_ascii=False,
        )
        s.commit()
    log.info(f"  高光总结已写入（{len(out)} 桶）")


def run_pipeline(
    platform: str,
    target_id: str,
    max_count: int | None = 50,
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
        max_count: 采集数量上限；``None`` = 自动模式（配时间窗时各游戏量自适应，
            靠采集器自然耗尽窗口；Steam 自动模式必须配 posted_after/posted_before）。
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

            # 视频快照落库（2026-09-04 B站视频看板；失败不阻塞主流程）
            try:
                _snapshot_bili_queue(target_id, info)
            except Exception as e:
                log.warning(f"  视频快照写入失败（不阻塞主流程）: {e}")

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

    # 3.3 B 站弹幕高光总结（2026-09-04 视频看板；采集时一次性调 LLM，失败不阻塞主流程）
    if platform == "bilibili" and target_meta.get("aid"):
        try:
            _generate_danmaku_highlights(target_meta["aid"])
        except Exception as e:
            log.warning(f"  [2.3] 弹幕高光总结失败（不阻塞主流程）: {e}")

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
                    limit=max_count,  # None=自动模式：向量化该目标全部缺失
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
            # analyzer_version：取自 analyzer（LLM/本地都有 analyzer_version 属性）
            # 缺省时为 None（旧 caller 也能跑；新数据 analyzer_version 留空，可后续回填）
            analyzer_version = getattr(analyzer, "analyzer_version", None)
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
                    analyzer_version=analyzer_version,
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
        choices=["deepseek", "qwen", "glm", "glm-5.3-flash", "local"],
        help="分析器后端（glm-5.3-flash 为智谱 BigModel 备选 LLM，独立凭据）",
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