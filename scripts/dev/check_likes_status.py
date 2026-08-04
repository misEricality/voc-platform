"""一次性脚本：查看当前 DB 中 CS2 评论的 likes 状态 + posted_at 分布"""
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# 项目根目录注入
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, func

from src.storage.db import Comment, CommentRepository, init_db

engine, SessionLocal = init_db()
with SessionLocal() as s:
    total = s.scalar(select(func.count(Comment.id)).where(Comment.target_id == "steam:730")) or 0
    likes_null = s.scalar(
        select(func.count(Comment.id))
        .where(Comment.target_id == "steam:730", Comment.likes.is_(None))
    ) or 0
    likes_filled = total - likes_null
    refreshed = s.scalar(
        select(func.count(Comment.id))
        .where(Comment.target_id == "steam:730", Comment.likes_refreshed_at.is_not(None))
    ) or 0
    oldest = s.scalar(select(func.min(Comment.posted_at)).where(Comment.target_id == "steam:730"))
    newest = s.scalar(select(func.max(Comment.posted_at)).where(Comment.target_id == "steam:730"))

now = datetime.now(timezone.utc).replace(tzinfo=None)
oldest_age_days = (now - oldest).days if oldest else None
newest_age_days = (now - newest).days if newest else None

print(f"Total CS2 comments: {total}")
print(f"likes IS NULL: {likes_null}（首次入库后尚未回采）")
print(f"likes 已填: {likes_filled}（已回采）")
print(f"likes_refreshed_at IS NOT NULL: {refreshed}")
print()
print(f"Oldest posted_at: {oldest} 距今 {oldest_age_days} 天")
print(f"Newest posted_at: {newest} 距今 {newest_age_days} 天")