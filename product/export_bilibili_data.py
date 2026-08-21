"""B 站单视频原型数据导出（2026-08-21 · 简单原型 v0.1）

导出指定 bvid 的视频原型所需数据为单个 JSON：
- 视频元数据（bvid / title / owner / pubdate / tags / stat）
- 评论：总量、已分析率、情感分布、L1 主题分布、TOP 负面观点
- 弹幕：progress 分桶、模式分布、TOP 高亮时刻、散点云采样
- 评论者画像：性别比（仅性别）

读 DB、不动 DB；供 build_bilibili_video.py 组装 HTML 用。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, func

from src.storage.db import init_db, Comment, CommentOpinion, Danmaku


# 情绪粗分类词典（弹幕专用；不进 LLM 链路）
EMOTION_KEYWORDS = {
    "warm": ["好", "棒", "爽", "牛", "帅", "赞", "爱", "强", "神", "顶", "绝", "嗨", "炸", "过瘾", "燃", "惊喜"],
    "cool": ["烂", "差", "弱", "菜", "失望", "垃圾", "无聊", "尴尬", "拉", "崩", "坑", "糊", "亏", "劝退"],
}


def emotion_for(text: str, color_int: int | None) -> str:
    """粗分情绪：词典匹配 + 暖色 int 提示

    Args:
        text: 弹幕文本
        color_int: B 站弹幕颜色（高 8 位为 R/G/B；>=0xAAAAAA 视为暖色）
    """
    if not text:
        return "neutral"
    t = text.lower()
    for emo, kws in EMOTION_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return emo
    if color_int and color_int >= 0xAAAAAA:
        return "warm"
    return "neutral"


def export_video(bvid: str, out_path: Path, *, bucket_size_s: int = 60,
                 scatter_sample: int = 300) -> dict:
    """导出指定 bvid 的原型数据 JSON

    Args:
        bvid: B 站视频 BV 号
        out_path: 输出 JSON 路径
        bucket_size_s: 弹幕 progress 分桶大小（秒），默认 60
        scatter_sample: 弹幕散点云采样条数（默认 300，浏览器友好）
    """
    _, SessionLocal = init_db()
    payload: dict = {}

    with SessionLocal() as s:
        # ===== 1. 找 target_id（按 bvid 查 comments.extra_meta）=====
        # 任取一条该 bvid 的评论（其 extra_meta 含有视频元数据快照）
        # 全表扫：B 站评论按入库顺序，bvid 不连续，必须扫全
        row = s.execute(
            select(Comment.extra_meta)
            .where(Comment.platform == "bilibili")
        ).scalars().all()
        target_meta = None
        target_id = None
        for meta_str in row:
            if not meta_str:
                continue
            try:
                d = json.loads(meta_str)
            except json.JSONDecodeError:
                continue
            if d.get("bvid") == bvid:
                target_meta = d
                break
        if not target_meta:
            raise RuntimeError(f"未找到 bvid={bvid} 的视频元数据（先采？）")

        target_id = f"bilibili:video:{target_meta['aid']}"
        pubdate = target_meta.get("pubdate")
        pubdate_iso = (
            datetime.fromtimestamp(pubdate, tz=timezone.utc).replace(tzinfo=None).isoformat()
            if pubdate else None
        )

        payload["video"] = {
            "bvid": bvid,
            "aid": target_meta["aid"],
            "cid": target_meta.get("cid"),
            "title": target_meta.get("name"),
            "owner_name": (target_meta.get("owner") or {}).get("name"),
            "owner_face": (target_meta.get("owner") or {}).get("face"),
            "pubdate_iso": pubdate_iso,
            "tags": target_meta.get("tags") or [],
            "stat": target_meta.get("stat") or {},
        }

        # ===== 2. 评论：总量 / 已分析 / 情感分布 =====
        total = s.execute(
            select(func.count(Comment.id)).where(Comment.target_id == target_id)
        ).scalar()
        analyzed = s.execute(
            select(func.count(Comment.id)).where(
                Comment.target_id == target_id, Comment.analyzed_at.is_not(None)
            )
        ).scalar()

        sent_rows = s.execute(
            select(Comment.sentiment, func.count(Comment.id))
            .where(Comment.target_id == target_id, Comment.analyzed_at.is_not(None))
            .group_by(Comment.sentiment)
        ).all()
        sent_dist = {s_name: 0 for s_name in ("positive", "neutral", "negative")}
        for sent, n in sent_rows:
            sent_dist[sent or "neutral"] = n

        avg_score = s.execute(
            select(func.avg(Comment.sentiment_score)).where(
                Comment.target_id == target_id, Comment.analyzed_at.is_not(None)
            )
        ).scalar()
        avg_conf = s.execute(
            select(func.avg(Comment.sentiment_confidence)).where(
                Comment.target_id == target_id, Comment.analyzed_at.is_not(None)
            )
        ).scalar()

        payload["comments"] = {
            "total": total,
            "analyzed": analyzed,
            "analyzed_rate": round(analyzed / total * 100, 1) if total else 0,
            "sentiment_dist": sent_dist,
            "sentiment_dist_pct": {
                k: round(v / analyzed * 100, 1) if analyzed else 0
                for k, v in sent_dist.items()
            },
            "avg_score": round(avg_score, 3) if avg_score is not None else None,
            "avg_confidence": round(avg_conf, 3) if avg_conf is not None else None,
        }

        # ===== 3. L1 主题分布（含情感）=====
        topic_rows = s.execute(
            select(Comment.topic, Comment.sentiment, func.count(Comment.id))
            .where(Comment.target_id == target_id, Comment.analyzed_at.is_not(None))
            .group_by(Comment.topic, Comment.sentiment)
        ).all()
        topic_agg: dict[str, dict[str, int]] = defaultdict(lambda: {"positive": 0, "neutral": 0, "negative": 0})
        for topic, sent, n in topic_rows:
            topic_agg[topic or "其他"][sent or "neutral"] += n
        topic_list = []
        for name, dist in sorted(topic_agg.items(), key=lambda kv: -sum(kv[1].values())):
            t_total = sum(dist.values())
            topic_list.append({
                "name": name,
                "total": t_total,
                "positive": dist["positive"],
                "neutral": dist["neutral"],
                "negative": dist["negative"],
                "pct": round(t_total / analyzed * 100, 1) if analyzed else 0,
            })
        payload["comments"]["topics_l1"] = topic_list

        # ===== 4. TOP 负面观点（L2 + phrase + 次数）=====
        # _topic_segment 是 db.py 里的私有工具；从 src.storage.db 导入
        from src.storage.db import _topic_segment as _seg
        neg_rows = s.execute(
            select(CommentOpinion.full_path, CommentOpinion.quote, func.count(CommentOpinion.id))
            .join(Comment, Comment.id == CommentOpinion.comment_id)
            .where(
                Comment.target_id == target_id,
                CommentOpinion.sentiment == "negative",
                ~CommentOpinion.full_path.like("综合与元表达%"),
            )
            .group_by(CommentOpinion.full_path, CommentOpinion.quote)
            .order_by(func.count(CommentOpinion.id).desc())
            .limit(10)
        ).all()
        payload["comments"]["top_negative_opinions"] = [
            {"full_path": fp, "l1": _seg(fp, "L1"),
             "l2": _seg(fp, "L2"), "phrase": q, "count": c}
            for fp, q, c in neg_rows
        ]

        # ===== 5. 弹幕：分桶 + 模式 + TOP 时刻 + 散点云采样 =====
        dm_rows = s.execute(
            select(Danmaku.progress, Danmaku.mode, Danmaku.color, Danmaku.content, Danmaku.posted_at)
            .where(Danmaku.video_id == target_id)
            .order_by(Danmaku.progress.asc())
        ).all()

        duration_s = max((p for p, _, _, _, _ in dm_rows), default=0)
        n_buckets = (duration_s // bucket_size_s) + (1 if duration_s % bucket_size_s else 0)

        # 5.1 progress 分桶 + 情绪色聚类
        buckets = []
        for bi in range(int(n_buckets)):
            start = bi * bucket_size_s
            end = start + bucket_size_s
            seg = [(p, m, c, t) for p, m, c, t, _ in dm_rows
                   if start <= p < end]
            count = len(seg)
            emo_counter = Counter(emotion_for(t, c) for _, _, c, t in seg)
            mode_counter = Counter(m for _, m, _, _ in seg)
            buckets.append({
                "start_s": start,
                "end_s": end,
                "count": count,
                "top_emotion": emo_counter.most_common(1)[0][0] if count else "neutral",
                "warm": emo_counter.get("warm", 0),
                "neutral_emo": emo_counter.get("neutral", 0),
                "cool": emo_counter.get("cool", 0),
                "mode_dist": dict(mode_counter),
            })

        # 5.2 模式总分布（全部弹幕）
        mode_total = Counter(m for _, m, _, _, _ in dm_rows)
        mode_dist_pct = {f"mode_{k}": round(v / len(dm_rows) * 100, 1)
                         for k, v in mode_total.items()}

        # 5.3 TOP10 高亮时刻（按桶内 count 倒序）+ 代表样本
        top_buckets = sorted(
            [b for b in buckets if b["count"] > 0],
            key=lambda b: -b["count"]
        )[:10]
        top_moments = []
        for b in top_buckets:
            seg = [(p, t) for p, m, c, t, _ in dm_rows
                   if b["start_s"] <= p < b["end_s"]]
            # 取点赞高的 3 条作为代表样本（无点赞字段，按文本长度 + 内容多样性选 3）
            samples_seen = []
            for _, t in seg:
                tt = (t or "").strip()
                if tt and tt not in samples_seen and 2 <= len(tt) <= 30:
                    samples_seen.append(tt)
                if len(samples_seen) >= 3:
                    break
            top_moments.append({
                "start_s": b["start_s"],
                "end_s": b["end_s"],
                "count": b["count"],
                "samples": samples_seen,
            })

        # 5.4 散点云采样（按 progress 均匀抽取 N 条）
        dm_sorted = sorted(dm_rows, key=lambda r: r[0])
        scatter = []
        if dm_sorted:
            step = max(1, len(dm_sorted) // scatter_sample)
            for i in range(0, len(dm_sorted), step):
                p, m, c, t, _ = dm_sorted[i]
                if t and 0 < len(t) <= 30:
                    scatter.append({
                        "progress_s": p,
                        "mode": m,
                        "color_int": c,
                        "content": t,
                        "emotion": emotion_for(t, c),
                    })
                if len(scatter) >= scatter_sample:
                    break

        payload["danmaku"] = {
            "total": len(dm_rows),
            "duration_s": duration_s,
            "bucket_size_s": bucket_size_s,
            "buckets": buckets,
            "mode_dist": mode_dist_pct,
            "top_moments": top_moments,
            "scatter_sample": scatter,
        }

        # ===== 6. 评论者性别比（仅性别）=====
        prof_rows = s.execute(
            select(Comment.extra_json)
            .where(Comment.target_id == target_id)
            .limit(2000)
        ).scalars().all()
        sex_counter: Counter = Counter()
        for ej in prof_rows:
            if not ej:
                continue
            try:
                d = json.loads(ej)
            except json.JSONDecodeError:
                continue
            sex = ((d.get("profile") or {}).get("sex") or "保密")
            sex_counter[sex] += 1
        sex_total = sum(sex_counter.values())
        payload["profile"] = {
            "sex_dist_pct": {
                k: round(v / sex_total * 100, 1) if sex_total else 0
                for k, v in sex_counter.items()
            },
            "sex_sample_size": sex_total,
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已导出 {out_path} ({out_path.stat().st_size:,} bytes)")
    print(f"  video: {payload['video']['title']}")
    print(f"  comments: {payload['comments']['total']} (analyzed={payload['comments']['analyzed']})")
    print(f"  danmaku: {payload['danmaku']['total']} (duration={payload['danmaku']['duration_s']}s)")
    print(f"  scatter samples: {len(payload['danmaku']['scatter_sample'])}")
    return payload


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--bvid", default="BV1kS8H6VERt",
                        help="目标 bvid（默认黑神话·钟馗）")
    parser.add_argument("--out", default=str(ROOT / "data" / "_bili_video.json"),
                        help="输出 JSON 路径")
    parser.add_argument("--bucket", type=int, default=60,
                        help="弹幕分桶秒数（默认 60）")
    parser.add_argument("--scatter-sample", type=int, default=300,
                        help="散点云采样条数（默认 300）")
    args = parser.parse_args()

    export_video(
        args.bvid,
        Path(args.out),
        bucket_size_s=args.bucket,
        scatter_sample=args.scatter_sample,
    )