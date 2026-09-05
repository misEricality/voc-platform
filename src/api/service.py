"""只读数据服务（公开端点的查询逻辑，与 FastAPI 解耦便于测试）

数据来源：复用 src/storage/db.py 仓储聚合（list_targets / opinion_matrix /
negative_pain_points 等），缺的查询（overview / trends / comments 分页）在此补齐，
不改动既有仓储方法。

2026-09-03 单游戏看板（WEB_DASHBOARD.md §4）新增三条统一口径：
1. **时间窗**：所有公开聚合端点支持 `start` / `end`（`YYYY-MM-DD` 闭区间），
   统一作用在 `Comment.posted_at`（评论发布时间，非采集时间）。
2. **颗粒度 grain**：`comment`（原声，一条评论算一次）/ `opinion`（观点，多观点可重复计入）。
   - 情感分布：comment → `comments.sentiment`；opinion → `comment_opinions.sentiment`
   - L1 主题分布：comment → `comments.topic`（主观点的 L1，见下方「主观点落盘链路」）；
     opinion → `comment_opinions.full_path` 的 L1 段
3. **全量零填充 full**：L1 主题按 `config/topics/gaming.yaml` 的 `primary` 顺序返回全部
   10 条（无数据的补 0，含「综合与元表达」），用于看板固定顺序的条形图。

**主观点落盘链路**（2026-09-03 核实）：`comment_opinions` 表**没有** `is_core` 列
（那是 `src/analyzers/base.py::Opinion` 的运行时字段，不落盘）。DB 里唯一持久化的
「主观点 L1」就是 `comments.topic` —— 由 `src/analyzers/sentiment_llm.py` 的 core 观点判定
写入（`topic = core.full_path.split("/")[0]`，越界回落 `fallback`）。因此
`grain=comment` 的主题聚合直接按 `comments.topic` 分组，无需加列迁移。
⚠️ `comments.topic` 可能为 NULL（`sentiment_local.py` 本地模型不打主题）→ 聚合时排除。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.storage.db import (
    Comment,
    CommentOpinion,
    Danmaku,
    GameMeta,
    _topic_segment,
    _utcnow,
)

# config/topics/gaming.yaml：主题体系唯一权威源（primary 顺序即看板条形图顺序）
TOPICS_YAML = Path(__file__).resolve().parents[2] / "config" / "topics" / "gaming.yaml"
# config/monitoring/targets.yaml：监控目标清单（targets 段 = 单游戏看板可见白名单）
MONITORING_YAML = Path(__file__).resolve().parents[2] / "config" / "monitoring" / "targets.yaml"
# data/covers/：游戏封面本地缓存（library_600x900 竖版，随 data/ 目录同步部署）
COVERS_DIR = Path(__file__).resolve().parents[2] / "data" / "covers"
DATE_FMT = "%Y-%m-%d"
SENTIMENTS = ("positive", "negative", "neutral")


def _meta_name(extra_meta: str | None, fallback: str) -> str:
    if extra_meta:
        try:
            return (json.loads(extra_meta) or {}).get("name") or fallback
        except (json.JSONDecodeError, TypeError):
            pass
    return fallback


# ==================== 主题配置 + 时间窗（单游戏看板新增） ====================

@lru_cache(maxsize=1)
def _load_topic_config() -> dict:
    """读 config/topics/gaming.yaml → {primary: [...], fallback: str, hierarchy: {...}}

    刻意不复用 src/analyzers/sentiment_llm.py::_load_topic_config —— 那条 import 链会拖入
    openai / torch 等重依赖，API 层不该为读一个 yaml 付出这个代价。代价是两份 5 行代码，
    换来依赖隔离，值得。

    lru_cache：文件内容变更需重启 API 生效（主题体系是低频变更的静态配置，可接受）。
    """
    if not TOPICS_YAML.exists():
        return {"primary": [], "fallback": "其他", "hierarchy": {}}
    try:
        cfg = yaml.safe_load(TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {"primary": [], "fallback": "其他", "hierarchy": {}}
    return {
        "primary": list(cfg.get("primary") or []),
        "fallback": cfg.get("fallback") or "其他",
        "hierarchy": cfg.get("hierarchy") or {},
    }


def topic_tree_payload() -> dict:
    """L1~L3 主题树（前端树状筛选器数据源）

    返回 primary（有序 L1 列表）/ fallback / hierarchy（{L1: {L2: [L3, ...]}}）/
    levels（['L1','L2','L3']，前端据此决定是否渲染树状层级）。
    """
    cfg = _load_topic_config()
    return {
        "primary": cfg["primary"],
        "fallback": cfg["fallback"],
        "hierarchy": cfg["hierarchy"],
        "levels": ["L1", "L2", "L3"],
    }


def _parse_date(value: str | None, *, field: str) -> datetime | None:
    """'YYYY-MM-DD' → datetime；None/空串返回 None；非法格式抛 ValueError（路由层转 422）"""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), DATE_FMT)
    except ValueError:
        raise ValueError(f"{field} 需为 YYYY-MM-DD 格式，收到：{value!r}")


def _apply_time(conditions: list, start: str | None, end: str | None) -> None:
    """把 start/end 转成 posted_at 区间条件，就地追加到 conditions。

    闭区间语义：[start 00:00:00, end 23:59:59.999999] —— 实现上用
    `posted_at < end + 1 天`，避免 datetime 精度问题漏掉当天 23:59:59 之后的数据。
    """
    dt_start = _parse_date(start, field="start")
    dt_end = _parse_date(end, field="end")
    if dt_start and dt_end and dt_start > dt_end:
        raise ValueError(f"start 不能晚于 end（{start} > {end}）")
    if dt_start:
        conditions.append(Comment.posted_at >= dt_start)
    if dt_end:
        conditions.append(Comment.posted_at < dt_end + timedelta(days=1))


@lru_cache(maxsize=1)
def _monitored_target_ids() -> frozenset[str]:
    """config/monitoring/targets.yaml targets 段 → {"steam:2358720", ...} 白名单

    单游戏看板的可见范围 = 此清单（2026-08-23 起 4 款网游已归档并显式排除）。
    不筛 enabled：enabled=false 仅表示暂停采集，历史数据仍值得展示。
    """
    if not MONITORING_YAML.exists():
        return frozenset()
    try:
        cfg = yaml.safe_load(MONITORING_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return frozenset()
    return frozenset(
        f"{t.get('platform', 'steam')}:{t.get('id')}"
        for t in (cfg.get("targets") or [])
        if t.get("id")
    )


def list_targets_payload(
    session: Session, platform: str | None = None, *, monitored: bool = False
) -> list[dict]:
    """目标列表 + 聚合指标（直接复用 CommentRepository.list_targets 的同源查询）

    monitored=True：仅返回 targets.yaml targets 段内的目标（单游戏看板用，
    确保归档网游即便有残留数据也不会出现在筛选器与图表里）。
    """
    from src.storage.db import CommentRepository

    rows = CommentRepository(session).list_targets(platform=platform)
    if monitored:
        allow = _monitored_target_ids()
        rows = [t for t in rows if t["target_id"] in allow]
    return rows


def overview_payload(
    session: Session,
    target_id: str,
    *,
    start: str | None = None,
    end: str | None = None,
    grain: str = "comment",
) -> dict | None:
    """单目标 KPI：总量/分析覆盖/情感构成/均分/推荐率/时间范围

    - `total` 恒为原声（评论）数，**不受 grain 影响**（指标卡永远讲原声口径）
    - `grain="comment"`（默认）：`sentiment` 按 `comments.sentiment` 聚合
    - `grain="opinion"`：`sentiment` 按 `comment_opinions.sentiment` 聚合（多观点可重复计入），
      并额外返回 `opinion_total`
    - 时间窗作用在 `Comment.posted_at`
    """
    conditions = [Comment.target_id == target_id]
    _apply_time(conditions, start, end)

    cols = [
        func.count(Comment.id).label("total"),
        func.count(Comment.analyzed_at).label("analyzed"),
        func.sum(case((Comment.sentiment == "positive", 1), else_=0)).label("pos"),
        func.sum(case((Comment.sentiment == "negative", 1), else_=0)).label("neg"),
        func.sum(case((Comment.sentiment == "neutral", 1), else_=0)).label("neu"),
        func.avg(Comment.sentiment_score).label("avg_score"),
        func.sum(Comment.rating).label("rating_sum"),
        func.count(Comment.rating).label("rating_cnt"),
        func.sum(case((Comment.rating == 1, 1), else_=0)).label("recommend_cnt"),
        func.min(Comment.posted_at).label("first_posted"),
        func.max(Comment.posted_at).label("last_posted"),
        func.max(Comment.extra_meta).label("extra_meta"),
    ]
    row = session.execute(select(*cols).where(*conditions)).one()
    if (row.total or 0) == 0:
        return None

    pos, neg, neu = int(row.pos or 0), int(row.neg or 0), int(row.neu or 0)
    opinion_total: int | None = None
    if grain == "opinion":
        op_stmt = (
            select(CommentOpinion.sentiment, func.count(CommentOpinion.id))
            .join(Comment, Comment.id == CommentOpinion.comment_id)
            .where(*conditions)
            .group_by(CommentOpinion.sentiment)
        )
        op_counts = {s: 0 for s in SENTIMENTS}
        for senti, cnt in session.execute(op_stmt):
            if senti in op_counts:
                op_counts[senti] += int(cnt or 0)
        pos, neg, neu = op_counts["positive"], op_counts["negative"], op_counts["neutral"]
        opinion_total = pos + neg + neu

    n = pos + neg + neu
    return {
        "target_id": target_id,
        "name": _meta_name(row.extra_meta, target_id),
        "total": int(row.total),
        "analyzed": int(row.analyzed or 0),
        "opinion_total": opinion_total,
        "grain": grain,
        "window": {"start": start, "end": end},
        "sentiment": {
            "positive": pos, "negative": neg, "neutral": neu,
            "positive_pct": round(pos / n * 100, 1) if n else 0,
            "negative_pct": round(neg / n * 100, 1) if n else 0,
            "neutral_pct": round(neu / n * 100, 1) if n else 0,
        },
        "avg_score": round(row.avg_score, 3) if row.avg_score is not None else None,
        "recommend_count": int(row.recommend_cnt or 0),  # 库内好评条数（指标对比表用）
        "recommend_rate": (
            round((row.rating_sum or 0) / row.rating_cnt * 100, 1) if row.rating_cnt else None
        ),
        "first_posted": row.first_posted.isoformat() if row.first_posted else None,
        "last_posted": row.last_posted.isoformat() if row.last_posted else None,
    }


def topics_payload(
    session: Session,
    target_id: str,
    level: str = "L1",
    *,
    grain: str = "opinion",
    sentiment: str | None = None,
    start: str | None = None,
    end: str | None = None,
    full: bool = False,
    exclude_meta: bool = True,
) -> list[dict]:
    """主题分布（支持原声/观点双颗粒度、L1/L2/L3 层级、情感过滤、时间窗、全量零填充）

    - `grain="opinion"`（默认，既有行为）：按 `comment_opinions.full_path` 的指定层级段聚合
    - `grain="comment"`：按 `comments.topic` 聚合（= 主观点的 L1），**level 必须为 L1**
    - `full=True`：按 `config/topics/gaming.yaml` 的 `primary` 顺序返回**全部** L1
      （无数据补 0，含「综合与元表达」），供看板固定顺序条形图使用；该模式下
      强制 `exclude_meta=False`（否则元表达先被过滤再补 0，与「全部显示」自相矛盾）
    - 默认排序为总量 desc（既有行为，`compare.js` 等消费方依赖），`full=True` 时改为固定顺序

    抛出的 ValueError 由路由层转成 422。
    """
    if level not in {"L1", "L2", "L3"}:
        raise ValueError(f"level 仅支持 L1/L2/L3，收到：{level}")
    if grain not in {"comment", "opinion"}:
        raise ValueError(f"grain 仅支持 comment/opinion，收到：{grain}")
    if grain == "comment" and level != "L1":
        raise ValueError("grain=comment 时 level 必须为 L1（comments.topic 只存 L1）")
    if full and level != "L1":
        raise ValueError("full 仅支持 L1（零填充按 primary 列表，只在 L1 有定义）")
    if full:
        exclude_meta = False

    conditions = [Comment.target_id == target_id]
    _apply_time(conditions, start, end)
    agg: dict[str, dict[str, int]] = {}

    if grain == "comment":
        stmt = select(Comment.topic, Comment.sentiment).where(*conditions)
        if sentiment:
            stmt = stmt.where(Comment.sentiment == sentiment)
        for topic, senti in session.execute(stmt):
            if not topic:
                continue  # topic 为 NULL（本地模型不打主题）→ 不计入主题分布
            if exclude_meta and topic.startswith("综合与元表达"):
                continue
            bucket = agg.setdefault(topic, {s: 0 for s in SENTIMENTS})
            bucket[senti if senti in bucket else "neutral"] += 1
    else:
        stmt = (
            select(CommentOpinion.full_path, CommentOpinion.sentiment)
            .join(Comment, Comment.id == CommentOpinion.comment_id)
            .where(*conditions)
        )
        if sentiment:
            stmt = stmt.where(CommentOpinion.sentiment == sentiment)
        for fp, senti in session.execute(stmt):
            if exclude_meta and (fp or "").startswith("综合与元表达"):
                continue
            seg = _topic_segment(fp, level)
            if not seg:
                continue
            bucket = agg.setdefault(seg, {s: 0 for s in SENTIMENTS})
            bucket[senti if senti in bucket else "neutral"] += 1

    def _row(topic: str, c: dict[str, int]) -> dict:
        pos, neg, neu = c.get("positive", 0), c.get("negative", 0), c.get("neutral", 0)
        total = pos + neg + neu
        return {
            "topic": topic, "total": total,
            "positive": pos, "negative": neg, "neutral": neu,
            "negative_pct": round(neg / total * 100, 1) if total else 0,
        }

    if full:
        primary = _load_topic_config()["primary"]
        out = [_row(t, agg.get(t, {})) for t in primary]
        # 防御：primary 之外仍有数据（不该发生，如旧标签未迁移）→ 追加在末尾，不静默丢
        for topic, c in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
            if topic not in primary:
                out.append(_row(topic, c))
        return out

    return [
        _row(topic, c)
        for topic, c in sorted(agg.items(), key=lambda kv: -sum(kv[1].values()))
    ]


def _parse_extra(raw: str | None) -> dict:
    """解析 comments.extra_json（Steam playtime 等）；非法/为空返回 {}"""
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def comments_payload(
    session: Session,
    *,
    target_id: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sentiment: str | None = None,
    topic: str | None = None,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    grain: str = "opinion",
    sort: str = "time",
) -> dict:
    """评论（原声）分页列表，附观点标签 + extra 解析

    topic 过滤随 grain：
    - grain=opinion（默认，既有行为）：任一观点 full_path 以 topic 段开头（L1/L2/L3 前缀）
    - grain=comment（原声粒度）：comments.topic 精确等于 topic（只有 L1）
    start/end：按 posted_at 过滤（YYYY-MM-DD 闭区间）。
    sort：time = posted_at desc（单游戏看板口径，2026-09-03）；
          likes = likes desc → posted_at desc（B站原声列表口径，2026-09-04）。
    """
    conditions = [Comment.analyzed_at.is_not(None)]
    if target_id:
        conditions.append(Comment.target_id == target_id)
    if sentiment:
        conditions.append(Comment.sentiment == sentiment)
    if q:
        conditions.append(Comment.content.like(f"%{q}%"))
    _apply_time(conditions, start, end)
    if topic:
        if grain == "comment":
            conditions.append(Comment.topic == topic)
        else:
            op_ids = select(CommentOpinion.comment_id).where(
                CommentOpinion.full_path.like(f"{topic}%")
            )
            conditions.append(Comment.id.in_(op_ids))

    base = select(Comment).where(*conditions)
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0

    if sort == "likes":
        order = (Comment.likes.desc().nullslast(), Comment.posted_at.desc(), Comment.id.desc())
    else:
        order = (Comment.posted_at.desc().nullslast(), Comment.id.desc())
    rows = list(session.execute(
        base.order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars())

    # 批量取观点标签
    ids = [r.id for r in rows]
    op_map: dict[int, list[dict]] = {}
    if ids:
        op_stmt = select(CommentOpinion).where(CommentOpinion.comment_id.in_(ids))
        for op in session.execute(op_stmt).scalars():
            op_map.setdefault(op.comment_id, []).append({
                "full_path": op.full_path,
                "sentiment": op.sentiment,
                "quote": op.quote,
            })

    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [
            {
                **r.to_dict(),
                "name": _meta_name(r.extra_meta, r.target_id),
                "extra": _parse_extra(r.extra_json),
                "opinions": op_map.get(r.id, []),
            }
            for r in rows
        ],
    }


def opinions_payload(
    session: Session,
    *,
    target_id: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sentiment: str | None = None,
    topic: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """观点分页列表（观点粒度看板用，2026-09-03 新增）

    每条观点附所属原声（comment 字段：原文/情感/主题/推荐/点赞/游玩时长），
    情感过滤作用在**观点级** `CommentOpinion.sentiment`；
    topic 过滤 = full_path 前缀（L1/L2/L3 均可）；
    时间窗与排序都按**所属原声的 posted_at**（列表按评论时间降序）。
    """
    conditions = []
    if target_id:
        conditions.append(Comment.target_id == target_id)
    if sentiment:
        conditions.append(CommentOpinion.sentiment == sentiment)
    if topic:
        conditions.append(CommentOpinion.full_path.like(f"{topic}%"))
    _apply_time(conditions, start, end)

    base = (
        select(CommentOpinion)
        .join(Comment, Comment.id == CommentOpinion.comment_id)
        .where(*conditions)
    )
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0

    rows = list(session.execute(
        base.order_by(Comment.posted_at.desc().nullslast(), CommentOpinion.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars())

    # 批量取父原声
    cids = {r.comment_id for r in rows}
    c_map: dict[int, Comment] = {}
    if cids:
        c_stmt = select(Comment).where(Comment.id.in_(cids))
        c_map = {c.id: c for c in session.execute(c_stmt).scalars()}

    items = []
    for r in rows:
        c = c_map.get(r.comment_id)
        items.append({
            **r.to_dict(),
            "comment": None if c is None else {
                **c.to_dict(),
                "name": _meta_name(c.extra_meta, c.target_id),
                "extra": _parse_extra(c.extra_json),
            },
        })
    return {"total": int(total), "page": page, "page_size": page_size, "items": items}


def danmaku_payload(
    session: Session,
    bvid: str,
    *,
    width_sec: int = 30,
    samples_per_bucket: int = 10,
    sample_max_chars: int = 15,
) -> dict:
    """弹幕时间轴（2026-09-04 B站视频看板重做）

    - 30s **固定桶**（bucket_danmaku_rows，与采集时高光总结同一切分）
    - 每桶附 `samples`：随机 samples_per_bucket 条、长度 ≤ sample_max_chars 的弹幕
      （前端悬停浮层直接展示，随机性每次请求刷新）
    bvid 参数实际为 danmaku.video_id（形态 `bilibili:video:{aid}`）—— 前端始终传
    comments.target_id，与 danmaku.video_id 同源，直接等值查询即可。
    """
    import random

    from src.storage.db import bucket_danmaku_rows

    rows = list(session.execute(
        select(Danmaku).where(Danmaku.video_id == bvid).order_by(Danmaku.progress)
    ).scalars())
    if not rows:
        return {"video_id": bvid, "total": 0, "width_sec": width_sec, "buckets": []}

    bucket_list = []
    for b in bucket_danmaku_rows(rows, width=width_sec):
        pool = [r.content for r in b["rows"] if r.content and len(r.content) <= sample_max_chars]
        k = min(samples_per_bucket, len(pool))
        bucket_list.append({
            "start_sec": b["start_sec"],
            "end_sec": b["end_sec"],
            "count": b["count"],
            "samples": random.sample(pool, k) if k else [],
        })
    return {
        "video_id": bvid,
        "total": len(rows),
        "width_sec": width_sec,
        "buckets": bucket_list,
    }


def bilibili_videos_payload(session: Session) -> list[dict]:
    """B 站视频看板数据源（2026-09-04）：fetched 视频的快照 + 采集量 + 性别分布 + 高光

    - 只返回 status=fetched 且已有 aid 的视频（aid 是评论/弹幕 target_id 的映射键）
    - 性别分布：comments.extra_json.profile.sex（男/女/secret），SQLite json_extract 聚合
    - 高光：highlights_json 解析（采集时 LLM 总结落库，页面零成本）
    """
    from src.storage.db import BilibiliQueue

    rows = list(session.execute(
        select(BilibiliQueue)
        .where(BilibiliQueue.status == "fetched", BilibiliQueue.aid.is_not(None))
        .order_by(BilibiliQueue.pubdate.desc().nullslast())
    ).scalars())

    out = []
    for row in rows:
        target_id = f"bilibili:video:{row.aid}"
        m = session.execute(
            select(
                func.count(Comment.id).label("total"),
                func.sum(case((func.json_extract(Comment.extra_json, "$.profile.sex") == "男", 1), else_=0)).label("male"),
                func.sum(case((func.json_extract(Comment.extra_json, "$.profile.sex") == "女", 1), else_=0)).label("female"),
            ).where(Comment.target_id == target_id, Comment.platform == "bilibili")
        ).one()
        total = int(m.total or 0)
        male, female = int(m.male or 0), int(m.female or 0)
        try:
            highlights = json.loads(row.highlights_json) if row.highlights_json else None
        except (json.JSONDecodeError, TypeError):
            highlights = None
        try:
            tags = json.loads(row.tags_json) if row.tags_json else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        out.append({
            **row.to_dict(),
            "target_id": target_id,
            "tags": tags,
            "highlights": highlights,
            "collected": {"comments": int(row.comment_count or 0), "danmaku": int(row.danmaku_count or 0)},
            "sex": {"male": male, "female": female, "unknown": max(0, total - male - female)},
        })
    return out


def compare_payload(session: Session, targets: list[str], level: str = "L1") -> dict:
    """多目标对比聚合包（对齐 Streamlit 多目标对比视图的数据口径）"""
    from src.storage.db import CommentRepository

    if not targets:
        return {"targets": [], "sentiment_ratio": [], "matrix": {}, "pain_points": {}}
    repo = CommentRepository(session)

    ratio_df = repo.sentiment_ratio_by_targets(targets)
    matrix_df = repo.opinion_matrix(targets, level=level)
    pain = repo.negative_pain_points(targets, level="L2", top=5)

    matrix: dict[str, dict[str, int]] = {}
    if not matrix_df.empty:
        for topic, row in matrix_df.iterrows():
            matrix[str(topic)] = {str(k): int(v) for k, v in row.items()}

    all_targets = repo.list_targets(platform=None)
    return {
        "targets": [t for t in all_targets if t["target_id"] in targets],
        "sentiment_ratio": ratio_df.to_dict(orient="records"),
        "matrix": matrix,
        "pain_points": {k: [{"topic": t, "count": c} for t, c in v] for k, v in pain.items()},
    }


def trends_payload(
    session: Session,
    target_id: str | None = None,
    days: int = 30,
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """P8 时间序列：按日聚合评论量 + 情感构成 + 推荐率（posted_at 日历日，naive UTC 语义）

    时间窗二选一：`start`/`end`（YYYY-MM-DD 闭区间）优先；未提供则回落 `days`
    （向后兼容 pages/trends.js 的 `?days=`）。
    每日追加 `rating_sum` / `rating_cnt` / `recommend_rate`；
    `recommend_rate` 在当天无 rating 数据时为 **null**（前端折线断裂，不用 0 造成误导）。
    """
    dt_start = _parse_date(start, field="start")
    dt_end = _parse_date(end, field="end")
    if dt_start and dt_end and dt_start > dt_end:
        raise ValueError(f"start 不能晚于 end（{start} > {end}）")

    conditions = [Comment.posted_at.is_not(None)]
    if dt_start or dt_end:
        # start/end 优先；只给了一端时另一端用 days 补齐语义（start 缺省 = end 前推 days）
        if not dt_start and dt_end:
            dt_start = dt_end - timedelta(days=max(1, days) - 1)
        if not dt_end and dt_start:
            dt_end = dt_start + timedelta(days=max(1, days) - 1)
        _apply_time(conditions, dt_start.strftime(DATE_FMT), dt_end.strftime(DATE_FMT))
    else:
        conditions.append(Comment.posted_at >= _utcnow() - timedelta(days=days))
    if target_id:
        conditions.append(Comment.target_id == target_id)

    stmt = (
        select(
            func.date(Comment.posted_at).label("day"),
            func.count(Comment.id).label("total"),
            func.sum(case((Comment.sentiment == "positive", 1), else_=0)).label("pos"),
            func.sum(case((Comment.sentiment == "negative", 1), else_=0)).label("neg"),
            func.sum(case((Comment.sentiment == "neutral", 1), else_=0)).label("neu"),
            func.sum(Comment.rating).label("rating_sum"),
            func.count(Comment.rating).label("rating_cnt"),
        )
        .where(*conditions)
        .group_by(func.date(Comment.posted_at))
        .order_by(func.date(Comment.posted_at))
    )
    items = []
    for day, total, pos, neg, neu, r_sum, r_cnt in session.execute(stmt):
        r_cnt = int(r_cnt or 0)
        items.append({
            "day": str(day),
            "total": int(total or 0),
            "positive": int(pos or 0),
            "negative": int(neg or 0),
            "neutral": int(neu or 0),
            "rating_sum": int(r_sum or 0),
            "rating_cnt": r_cnt,
            "recommend_rate": round((r_sum or 0) / r_cnt * 100, 1) if r_cnt else None,
        })
    return {
        "target_id": target_id,
        "days": days,
        "start": start,
        "end": end,
        "items": items,
    }


# ==================== 游戏元数据（游戏对比看板 · 2026-09-04） ====================

# Steam review_score → 中文评测描述（appreviews 的 review_score_desc 恒为英文，不随 language 翻译）
_SCORE_CN = {
    9: "好评如潮", 8: "特别好评", 7: "好评", 6: "多半好评", 5: "褒贬不一",
    4: "多半差评", 3: "差评", 2: "差评如潮", 1: "差评如潮",
}
# 存量行的英文描述 → 中文（兜底，review_score 缺失时用）
_DESC_EN2CN = {
    "Overwhelmingly Positive": "好评如潮", "Very Positive": "特别好评",
    "Positive": "好评", "Mostly Positive": "多半好评", "Mixed": "褒贬不一",
    "Mostly Negative": "多半差评", "Negative": "差评", "Very Negative": "差评如潮",
    "Overwhelmingly Negative": "差评如潮",
}


def _parse_release_date(raw) -> date | None:
    """解析 Steam release_date（schinese "2024年8月20日" / english "20 Aug, 2024"）"""
    if isinstance(raw, dict):
        raw = raw.get("date")
    if not raw or not isinstance(raw, str):
        return None
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", raw)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    for fmt in ("%d %b, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _download_cover(appid: str) -> str | None:
    """下载竖版封面到 data/covers/{appid}.jpg（library_600x900，404 回退 header.jpg）

    本地化原因（2026-09-04 决策）：不依赖 Steam CDN 连通性、无防盗链问题、
    随 data/ 目录同步即可完成公网部署。全部失败返回 None（前端走 CDN 兜底）。
    """
    import requests

    try:
        COVERS_DIR.mkdir(parents=True, exist_ok=True)
        dest = COVERS_DIR / f"{appid}.jpg"
        if dest.exists() and dest.stat().st_size > 0:
            return f"{appid}.jpg"  # 已下载过，不重复拉
        for url in (
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
        ):
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200 and r.content:
                    dest.write_bytes(r.content)
                    return f"{appid}.jpg"
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        pass
    return None


def _refresh_game_meta(session: Session, target_id: str) -> None:
    """拉取并落库单个游戏的元数据（appdetails + appreviews + 封面下载）

    完整度驱动的重试策略：release_date / review_score 任一缺失视为不完整 →
    fetched_at 回拨为「1 小时前」，下个小时内的下次请求会再试；
    齐全才标记为完整新鲜（24h TTL）。防 Steam 限流导致的字段长期缺失。
    """
    from src.collectors.steam import SteamCollector

    appid = target_id.split(":", 1)[1] if ":" in target_id else target_id
    collector = SteamCollector()
    info = collector.fetch_app_info(appid) or {}
    summary = collector.fetch_review_summary(appid) or {}

    row = session.get(GameMeta, target_id)
    if row is None:
        row = GameMeta(target_id=target_id)
        session.add(row)
    # 部分成功也留盘：新值非空才覆盖（保留旧值防抖动）
    row.release_date = _parse_release_date(info.get("release_date")) or row.release_date
    row.review_score = summary.get("review_score") or row.review_score
    # 评级描述：优先按 review_score 映射中文（appreviews 描述恒为英文）
    row.rating_desc = (
        _SCORE_CN.get(summary.get("review_score") or 0)
        or row.rating_desc
        or summary.get("rating_desc")
    )
    row.total_reviews = summary.get("total_reviews") or row.total_reviews
    row.total_positive = summary.get("total_positive") or row.total_positive
    row.cover_file = _download_cover(appid) or row.cover_file

    incomplete = row.release_date is None or row.review_score is None
    if incomplete:
        # 不完整 → 1 小时后允许重试（回拨视为新鲜但 TTL 将尽）
        row.fetched_at = _utcnow() - timedelta(hours=GameMeta.REFRESH_TTL_HOURS - 1)
    else:
        row.fetched_at = _utcnow()
    session.commit()


def _refresh_game_meta_batch(target_ids: list[str]) -> None:
    """后台线程批量刷新（stale-while-revalidate）：独立 session + 0.5s 间隔防限流"""
    import time as _time

    from src.storage.db import init_db

    try:
        _, S = init_db()
        for i, t in enumerate(target_ids):
            try:
                with S() as s:
                    _refresh_game_meta(s, t)
            except Exception as e:  # noqa: BLE001
                logging.getLogger("voc.api").warning("game_meta 后台刷新失败 %s: %s", t, e)
            if i < len(target_ids) - 1:
                _time.sleep(0.5)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("voc.api").warning("game_meta 后台刷新线程异常: %s", e)


def _meta_row_dict(row: GameMeta | None, target_id: str) -> dict:
    """行 → 响应 dict；评级描述兜底做英文→中文映射（存量行 review_score 可能缺失）"""
    if row is None:
        return {
            "target_id": target_id, "release_date": None, "rating_desc": None,
            "review_score": None, "total_reviews": None, "total_positive": None,
            "cover_file": None, "fetched_at": None,
        }
    d = row.to_dict()
    if row.review_score:
        d["rating_desc"] = _SCORE_CN.get(row.review_score) or d["rating_desc"]
    else:
        d["rating_desc"] = _DESC_EN2CN.get(d["rating_desc"] or "", d["rating_desc"])
    return d


def games_meta_payload(session: Session, targets: list[str]) -> dict:
    """游戏元数据（stale-while-revalidate，2026-09-04 加载策略）

    **立即返回**现有行（可能字段为 NULL），缺行/超 TTL/字段缺失（review_score 为空）
    的目标交给后台线程刷新（0.5s 间隔），响应体 `refreshing` 非空时前端 3s 轮询。
    修复：原先同步刷新 6 款游戏串行打 Steam 接口，首屏阻塞 30s+。
    """
    import threading

    items: list[dict] = []
    refreshing: list[str] = []
    now = _utcnow()
    for t in targets:
        row = session.get(GameMeta, t)
        stale = (
            row is None
            or row.fetched_at is None
            or now - row.fetched_at > timedelta(hours=GameMeta.REFRESH_TTL_HOURS)
            or row.review_score is None  # 不完整行（限流导致字段缺失）→ 尽快补
        )
        if stale:
            refreshing.append(t)
        items.append(_meta_row_dict(row, t))

    if refreshing:
        threading.Thread(
            target=_refresh_game_meta_batch, args=(refreshing,),
            name="game-meta-refresh", daemon=True,
        ).start()
    return {"items": items, "refreshing": refreshing}
