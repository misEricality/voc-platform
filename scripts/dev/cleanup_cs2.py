"""一次性脚本：清理 CS2 数据
- 删除 7/31 及之前的数据
- 删除非中文（language != 'schinese'）的数据
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, func, select

from src.storage.db import Comment, init_db

engine, SessionLocal = init_db()

with SessionLocal() as s:
    # 1. 删除 7/31 及之前的 CS2 评论
    cutoff = "2026-07-31 23:59:59"
    deleted_old = s.execute(
        delete(Comment).where(
            Comment.platform == "steam",
            Comment.target_id == "steam:730",
            Comment.posted_at <= cutoff,
        )
    )
    print(f"删除 7/31 及之前的 CS2 评论: {deleted_old.rowcount} 条")

    # 2. 删除非中文（language != 'schinese'）的 CS2 评论
    deleted_non_zh = s.execute(
        delete(Comment).where(
            Comment.platform == "steam",
            Comment.target_id == "steam:730",
            Comment.language != "schinese",
        )
    )
    print(f"删除非中文 CS2 评论: {deleted_non_zh.rowcount} 条")

    s.commit()

    # 3. 统计 CS2 剩余数据
    remaining = s.execute(
        select(func.count(Comment.id)).where(
            Comment.platform == "steam",
            Comment.target_id == "steam:730",
        )
    ).scalar() or 0
    print(f"\n清理后 CS2 剩余: {remaining} 条")

    # 4. 按日期分布
    print("\n=== 按日期分布 ===")
    rows = s.execute(
        select(
            func.date(Comment.posted_at).label("d"),
            func.count(Comment.id),
        )
        .where(
            Comment.platform == "steam",
            Comment.target_id == "steam:730",
        )
        .group_by(func.date(Comment.posted_at))
        .order_by("d")
    ).all()
    for d, c in rows:
        print(f"  {d}: {c} 条")