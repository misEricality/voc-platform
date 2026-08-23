"""一次性脚本：导出 CS2 的 50 条评论明细

按字段来源分级标注，方便核对设计师原型所需字段。

> ⚠️ 2026-08-23 注：CS2 数据已从主库归档到 `data/archive/online_games_2026-08-23.db`。
> 本脚本保留作为历史记录，运行时会指向主库发现 0 条。
> 如要从归档 DB 导出 CS2 数据，改脚本里的 db_path 指向 `data/archive/online_games_2026-08-23.db`。
"""

A. 【采样原始】- 来自 Steam API，未做任何处理
B. 【程序派生】- 我们的采集/存储层加工的字段
C. 【LLM 标注】- DeepSeek 给出的标签与推理
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from src.storage.db import Comment, CommentRepository, init_db

EXPORT_DIR = Path("data/exports")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def export_one(c: Comment) -> dict:
    """将一条 Comment 拆解为三类字段"""
    return {
        # ============ A. 采样原始字段（来自 Steam API） ============
        "A_source_id": c.source_id,  # Steam recommendationid
        "A_content": c.content,  # 评论文本
        "A_author_id": c.author_id,  # Steam steamid（匿名）
        "A_rating": c.rating,  # 1=推荐/0=不推荐（来自 voted_up）
        "A_language": c.language,  # 'schinese'
        "A_likes": c.likes,  # 点赞数（votes_up）
        "A_replies": c.replies,  # 回复数（comment_count）
        "A_posted_at": c.posted_at.isoformat() if c.posted_at else None,
        # ============ B. 程序派生字段（我们加工的） ============
        "B_id": c.id,  # SQLite 自增主键
        "B_platform": c.platform,  # 'steam'
        "B_target_id": c.target_id,  # 'steam:730'
        "B_target_meta": c.extra_meta,  # 游戏名称/类型，JSON
        "B_extra_json": c.extra_json,  # appid/playtime/购买途径等，JSON
        "B_fetched_at": c.fetched_at.isoformat() if c.fetched_at else None,
        # ============ C. LLM 标注字段（DeepSeek 输出） ============
        "C_sentiment": c.sentiment,
        "C_sentiment_score": c.sentiment_score,
        "C_sentiment_confidence": c.sentiment_confidence,
        "C_topic": c.topic,
        "C_sub_topics": c.sub_topics,  # JSON
        "C_analyzed_at": c.analyzed_at.isoformat() if c.analyzed_at else None,
    }


def main() -> None:
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        repo = CommentRepository(s)
        comments = list(
            s.execute(
                select(Comment)
                .where(Comment.platform == "steam", Comment.target_id == "steam:730")
                .order_by(Comment.id)
            ).scalars()
        )

    rows = [export_one(c) for c in comments]

    # JSON 全量
    json_path = EXPORT_DIR / "cs2_50_full.json"
    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # CSV（按字段顺序，前缀分类）
    csv_path = EXPORT_DIR / "cs2_50_full.csv"
    headers = list(rows[0].keys())
    csv_path.write_text(
        "\n".join(
            [",".join(headers)]
            + [
                ",".join(
                    f'"{str(r[h]).replace(chr(34), chr(34)*2).replace(chr(10), " ")}"'
                    for h in headers
                )
                for r in rows
            ]
        ),
        encoding="utf-8-sig",  # BOM 让 Excel 直接识别 UTF-8
    )

    # 概览
    samples_path = EXPORT_DIR / "cs2_50_sample_5.json"
    samples_path.write_text(
        json.dumps(rows[:5], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"导出 {len(rows)} 条 → {json_path.relative_to('.')}")
    print(f"        CSV  → {csv_path.relative_to('.')}")
    print(f"        Sample x5 → {samples_path.relative_to('.')}")


if __name__ == "__main__":
    main()
