"""一次性数据库统计脚本：仅用于 v0.1 验证"""
from sqlalchemy import func, select

from src.storage.db import Comment, CommentRepository, init_db

engine, SessionLocal = init_db()
with SessionLocal() as s:
    repo = CommentRepository(s)
    total = s.scalar(select(func.count(Comment.id))) or 0
    pos = s.scalar(select(func.count(Comment.id)).where(Comment.sentiment == "positive")) or 0
    neg = s.scalar(select(func.count(Comment.id)).where(Comment.sentiment == "negative")) or 0
    neu = s.scalar(select(func.count(Comment.id)).where(Comment.sentiment == "neutral")) or 0
    analyzed = pos + neg + neu
    rate = analyzed * 100 // total if total else 0
    by_topic = s.execute(
        select(Comment.topic, func.count(Comment.id))
        .where(Comment.topic.is_not(None))
        .group_by(Comment.topic)
        .order_by(func.count(Comment.id).desc())
    ).all()
    by_target = s.execute(
        select(Comment.target_id, Comment.extra_meta, func.count(Comment.id))
        .group_by(Comment.target_id, Comment.extra_meta)
        .order_by(func.count(Comment.id).desc())
    ).all()

print(f"Total comments:  {total}")
print(f"Analyzed:        {analyzed} ({rate}%)")
print(f"Sentiment:  pos={pos}  neg={neg}  neu={neu}")
print("\nTop topics:")
for t, c in by_topic[:10]:
    print(f"  {t or '(None)'}: {c}")
print("\nBy target:")
for tid, name, c in by_target:
    print(f"  {tid}  name={name!r}  -> {c}")
