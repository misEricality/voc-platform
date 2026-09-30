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

from concurrent.futures import ThreadPoolExecutor

from src.collectors.steam import SteamCollector
from src.collectors.bilibili import BilibiliCollector
from src.storage.db import Danmaku, init_db, CommentRepository
from src.analyzers import get_analyzer
from src.runtime_mode import display_only
from src.analyzers.embedder import get_embedder, MODEL_NAME

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.pipeline")

# 标注批量大小：与 src/analyzers/sentiment_llm.DEFAULT_BATCH_SIZE 对齐（10 条/批）。
# 不在此处 import 该模块：local 分析器路径无需 openai 依赖，保持惰性。
# 2026-10-01 实测（GLM-5.3-Flash，同一样本 30 条）：5 条/批 3.29s/条、**10 条/批 2.15s/条**、
# 20 条/批 2.57s/条且单请求 p50 从 21s 涨到 37.9s（尾部更差）→ 10 是最优点，故不改。
ANALYSIS_BATCH_SIZE = 10

# 标注并发（2026-10-01）：GLM-5.3-Flash 单请求延迟方差大（实测 p50 ~21s、见过 170.9s 尖峰），
# 串行跑会让 02:00 链路更可能压进 03:00 哨兵窗口 —— 哨兵在「02:00 仍在运行」时会整个跳过，
# 那晚的失败就不会被补采。实测同一份 30 条样本：并发 1 → 64.5s，并发 2 → 48.4s，
# **并发 3 → 23.5s（2.74×，0 失败）**。默认 3，可用 ANALYZER_CONCURRENCY 覆盖。
ANALYSIS_CONCURRENCY_DEFAULT = 3
ANALYSIS_CONCURRENCY_MAX = 8


def _analysis_concurrency() -> int:
    """标注并发度（env ``ANALYZER_CONCURRENCY``）。

    非法 / 越界一律回退默认并告警（不因一个手滑的 env 把跑批打挂）。
    上限 :data:`ANALYSIS_CONCURRENCY_MAX`：再高收益递减，且会放大 provider 侧限流风险。
    """
    raw = os.getenv("ANALYZER_CONCURRENCY")
    if raw is None or not str(raw).strip():
        return ANALYSIS_CONCURRENCY_DEFAULT
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        log.warning("ANALYZER_CONCURRENCY=%r 不是整数，回退默认 %d", raw, ANALYSIS_CONCURRENCY_DEFAULT)
        return ANALYSIS_CONCURRENCY_DEFAULT
    if n < 1:
        log.warning("ANALYZER_CONCURRENCY=%d < 1，回退默认 %d", n, ANALYSIS_CONCURRENCY_DEFAULT)
        return ANALYSIS_CONCURRENCY_DEFAULT
    if n > ANALYSIS_CONCURRENCY_MAX:
        log.warning("ANALYZER_CONCURRENCY=%d 超上限 %d，按上限执行", n, ANALYSIS_CONCURRENCY_MAX)
        return ANALYSIS_CONCURRENCY_MAX
    return n


def _collect_valid_l2(hierarchy: dict | None) -> set[str] | None:
    """从 topic hierarchy（L1 → {L2: [L3...]}）收集全部合法 L2 标签。

    P3#2（2026-09-21）：供 update_analysis 的越界过滤使用；无词表时返回 None（不过滤）。
    """
    if not isinstance(hierarchy, dict) or not hierarchy:
        return None
    out: set[str] = set()
    for l2_map in hierarchy.values():
        if isinstance(l2_map, dict):
            out.update(l2_map.keys())
    return out or None


def _analyzer_supports_raise_on_error(analyzer) -> bool:
    """探测 analyze_batch 是否接受 raise_on_error（LLM 分析器专有参数）。

    local 分析器走 BaseAnalyzer.analyze_batch(texts, *, context=None)，传该参数会
    TypeError；测试 Fake 分析器多为 **(texts, **kwargs)**。用签名探测而非
    try/except TypeError —— 后者会吞掉实现内部的真实 TypeError。
    """
    import inspect

    try:
        params = inspect.signature(analyzer.analyze_batch).parameters
    except (TypeError, ValueError):
        return False
    if "raise_on_error" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _is_analysis_failure(result, text: str) -> bool:
    """失败占位判定（P1#1 双保险 · 2026-09-21）：非空文本却拿到「零置信度且无观点」的结果。

    两类来源都表现为该形态：① analyze_batch 异常被吞后的 neutral 占位；
    ② LLM 返回缺失 index → _empty_result()。成功路径置信度恒 > 0（无观点兜底为 0.5），
    且此处额外要求 opinions 为空，故不会误伤真实成功结果。
    """
    if not text or not text.strip():
        return False
    return (
        float(getattr(result, "sentiment_confidence", 0.0) or 0.0) == 0.0
        and not getattr(result, "opinions", None)
    )


def _analyze_chunk_once(
    analyzer, chunk: list, *, platform: str, target_id: str, supports_raise: bool
) -> list:
    """只做一次批次 LLM 调用（**不碰 DB、不写日志**）—— 并发路径要求它无副作用。"""
    kwargs: dict = {"context": {"platform": platform, "target_id": target_id}}
    if supports_raise:
        kwargs["raise_on_error"] = True
    return analyzer.analyze_batch([c.content for c in chunk], **kwargs)


def _iter_chunk_results(
    analyzer,
    chunks: list[list],
    *,
    concurrency: int,
    platform: str,
    target_id: str,
    supports_raise: bool,
):
    """按**窗口**并发跑批次 LLM，并**按批顺序**产出 ``(chunk, results)``。

    并发只覆盖「LLM 调用」这一段，落库仍由调用方在主线程串行做（SQLite 写不可并发）。
    窗口内任一批抛异常 → 直接向上抛（该 target 判失败，与串行语义一致）；同窗口内
    已发出的其他批次请求结果会被丢弃 —— 它们保持未分析，下轮重试（幂等，只多花 token）。
    """
    if concurrency <= 1 or len(chunks) <= 1:
        for chunk in chunks:
            yield chunk, _analyze_chunk_once(
                analyzer, chunk, platform=platform, target_id=target_id, supports_raise=supports_raise
            )
        return

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for w in range(0, len(chunks), concurrency):
            window = chunks[w : w + concurrency]
            futures = [
                pool.submit(
                    _analyze_chunk_once, analyzer, ch,
                    platform=platform, target_id=target_id, supports_raise=supports_raise,
                )
                for ch in window
            ]
            for ch, fut in zip(window, futures):
                yield ch, fut.result()


def _persist_chunk_results(
    repo,
    chunk: list,
    results: list,
    *,
    target_id: str,
    analyzer_version: str | None,
    valid_l1: set[str] | None,
    valid_l2: set[str] | None,
) -> tuple[int, int]:
    """把一批标注结果落库，返回 ``(已分析条数, 跳过条数)``。

    失败占位（零置信度且无观点）**不落库**：``analyzed_at`` 留空 → 下轮自动重试
    （P1#1 双保险；2026-09-21 起「批次异常向上抛」主链路的兜底）。
    """
    analyzed = skipped = 0
    for i, c in enumerate(chunk):
        result = results[i] if i < len(results) else None
        # 双保险（P1#1）：批次异常被吞 / LLM 缺失 index 都会产出「零置信度
        # 且无观点」的占位结果 —— 这类结果**不落库**（analyzed_at 留空 →
        # 下轮自动重试），避免把脏标注固化进主链路。
        if result is None or _is_analysis_failure(result, c.content):
            skipped += 1
            log.warning(
                "  [分析] 跳过无效结果 comment_id=%s target=%s（保持未分析，下轮重试）",
                c.id, target_id,
            )
            continue
        # 方案4：topic 已由 analyzer 从核心观点映射；观点（opinions）随主流程落库
        repo.update_analysis(
            c.id,
            sentiment=result.sentiment,
            sentiment_score=result.sentiment_score,
            sentiment_confidence=result.sentiment_confidence,
            topic=result.topic,
            opinions=[op.to_dict() for op in result.opinions],
            valid_l1_labels=valid_l1,
            valid_l2_labels=valid_l2,
            analyzer_version=analyzer_version,
        )
        analyzed += 1
    return analyzed, skipped


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
    # 展示端（VPS）拒绝采集与标注（2026-09-11「看着能采」陷阱收口）：
    # 放在最前面 —— 连采集器初始化都不进，日志里只留一句人话给线上排查。
    # 判定见 src/runtime_mode.py；展示端的形态由 PUBLIC_MODE 默认继承。
    if display_only():
        raise RuntimeError(
            "本实例是展示端（DISPLAY_ONLY=1 / PUBLIC_MODE=1），不执行采集与标注。"
            "采集任务请在本地看板增删改，再运行 scripts/ops/push_db_to_vps.ps1 把 DB 推过来。"
        )

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
            from sqlalchemy import func as _func
            from sqlalchemy import select as _select

            items = collector.fetch_danmaku(target_meta["cid"])
            inserted_d = repo.save_danmaku(
                db_full_target, str(target_meta["cid"]), items
            )
            # 报告库内累计而非本次新增：重采时 upsert 全部去重 → 新增 0，
            # runner/回填会把队列行 danmaku_count 覆盖成 0（BV1x54y1e7zf 事故，2026-09-06）
            danmaku_count = repo.session.execute(
                _select(_func.count(Danmaku.id)).where(Danmaku.video_id == db_full_target)
            ).scalar() or 0
            log.info(f"  [2.2] 弹幕新增 {inserted_d} 条（库内累计 {danmaku_count}，分片后 {len(items)} 条）")
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
    skipped_count = 0
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
            # 查询刚入库的评论（按目标），只挑未分析者
            comments = repo.list_by_target(platform, db_lookup_target, limit=max_count)
            pending = [c for c in comments if c.analyzed_at is None]
            # analyzer_version：取自 analyzer（LLM/本地都有 analyzer_version 属性）
            # 缺省时为 None（旧 caller 也能跑；新数据 analyzer_version 留空，可后续回填）
            analyzer_version = getattr(analyzer, "analyzer_version", None)
            # 越界标签过滤接线（P3#2 · 2026-09-21）：把词表边界下推到落库层，唤醒
            # db.update_analysis 的 valid_l1/l2 过滤（此前主链路从未传入 → 过滤休眠）。
            # 属性缺失（local / 测试 Fake 分析器）→ None = 不过滤。
            valid_l1 = set(getattr(analyzer, "topic_primary", None) or []) or None
            valid_l2 = _collect_valid_l2(getattr(analyzer, "topic_hierarchy", None))
            # P1#1 主修复（2026-09-21）：改**按批**打标（原先逐条 analyze() 约 10 倍请求），
            # 且显式 raise_on_error=True。原逐条 analyze() 默认吞异常 → 返回
            # neutral/conf=0 占位仍被 update_analysis 固化 analyzed_at，一个 LLM 故障夜
            # 会把当晚评论永久标成 neutral、永不重试且无告警。现在批级异常向上抛 →
            # 该 target 判失败 → 哨兵次日补采（此前已提交的批保留，剩余未分析下轮重试）。
            supports_raise = _analyzer_supports_raise_on_error(analyzer)
            chunks = [
                pending[start : start + ANALYSIS_BATCH_SIZE]
                for start in range(0, len(pending), ANALYSIS_BATCH_SIZE)
            ]
            concurrency = _analysis_concurrency()
            if len(chunks) > 1 and concurrency > 1:
                log.info(
                    f"  待分析 {len(pending)} 条 / {len(chunks)} 批（并发 {concurrency}）"
                )
            for chunk, results in _iter_chunk_results(
                analyzer, chunks, concurrency=concurrency,
                platform=platform, target_id=target_id, supports_raise=supports_raise,
            ):
                a_, s_ = _persist_chunk_results(
                    repo, chunk, results,
                    target_id=target_id, analyzer_version=analyzer_version,
                    valid_l1=valid_l1, valid_l2=valid_l2,
                )
                analyzed_count += a_
                skipped_count += s_
                # 逐批提交（2026-09-06 修复）：原「循环后一次 commit」会把 SQLite 写锁
                # 横跨整个 LLM 分析阶段（单条 30-60s × N 条 = 锁握数小时），其他写者
                # （每日 cron / admin backfill / run-due）在 busy_timeout 内抢不到锁 →
                # database is locked 连锁失败（9/6 凌晨 6 游戏 daily 全挂 + B站 run-due
                # 两次卡死 fetching 的根因）。WAL 模式下逐批 commit 开销可忽略。
                repo.commit()
            if skipped_count:
                log.warning(
                    f"  完成 {analyzed_count} 条分析；{skipped_count} 条无有效结果已跳过落库"
                    f"（保持未分析，下轮自动重试）"
                )
            else:
                log.info(f"  完成 {analyzed_count} 条分析")

    session.close()

    report = {
        "platform": platform,
        "target_id": target_id,
        "target_meta": target_meta,
        "fetched": len(raws),
        "analyzed": analyzed_count,
        "analysis_skipped": skipped_count,
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