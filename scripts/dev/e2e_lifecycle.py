"""端到端验证：首次采集 likes=None + 回采后 likes 被填上

使用独立测试 DB，不污染主 voc.db。
跑法：python -m scripts.dev.e2e_lifecycle
"""
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select, func
from src.storage.db import Comment, CommentRepository, init_db

TEST_DB_PATH = "data/voc_e2e.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH}"

# 清理旧的测试库
if os.path.exists(TEST_DB_PATH):
    os.remove(TEST_DB_PATH)

engine, SessionLocal = init_db(TEST_DB_URL)
print(f"创建测试库: {TEST_DB_URL}")

with SessionLocal() as s:
    repo = CommentRepository(s)

    # === Step 1: 首次入库（likes=None）===
    print("\n=== Step 1: 首次入库（likes 应为 NULL）===")
    from src.collectors.base import RawComment
    from datetime import datetime, timezone

    def _utcnow():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    # 模拟 8 天前发布的评论 + 1 天前发布的评论
    old_posted = _utcnow().replace()  # 取当前时刻
    # 构造两条：1 条 8 天前，1 条 1 天前
    posted_old = datetime(2026, 7, 26, 12, 0, 0)
    posted_new = datetime(2026, 8, 2, 12, 0, 0)

    rc_old = RawComment(
        platform="steam",
        source_id="old_001",
        content="8 天前发布的评论",
        rating=1,
        language="schinese",
        likes=None,
        replies=None,
        posted_at=posted_old,
    )
    rc_old.extra = {"appid": "730", "weighted_vote_score": "0.6"}
    repo.upsert(rc_old)

    rc_new = RawComment(
        platform="steam",
        source_id="new_001",
        content="1 天前发布的评论",
        rating=0,
        language="schinese",
        likes=None,
        replies=None,
        posted_at=posted_new,
    )
    rc_new.extra = {"appid": "730"}
    repo.upsert(rc_new)
    repo.commit()
    print(f"  入库 2 条：1 条发布于 {posted_old.date()}（8 天前），1 条发布于 {posted_new.date()}（1 天前）")
    print(f"  ✓ 验证：两条的 likes 都应为 None")

    all_rows = s.execute(select(Comment.source_id, Comment.likes, Comment.likes_refreshed_at)).all()
    for src, likes, refreshed in all_rows:
        print(f"    {src}: likes={likes}, refreshed={refreshed}")

    # === Step 2: 模拟回采（只对 8 天前的评论）===
    print("\n=== Step 2: 回采 8 天前的评论（likes 应被填上）===")
    rc_old_refreshed = RawComment(
        platform="steam",
        source_id="old_001",
        content="8 天前发布的评论",
        rating=1,
        language="schinese",
        likes=42,
        replies=3,
        posted_at=posted_old,
    )
    rc_old_refreshed.extra = {"appid": "730"}
    repo.upsert(rc_old_refreshed)
    repo.commit()

    print(f"  ✓ 验证：8 天前的评论 likes=42，likes_refreshed_at 应被设置")
    print(f"  ✓ 验证：1 天前的评论 likes 仍为 None（未到 7 天）")

    all_rows = s.execute(
        select(
            Comment.source_id,
            Comment.likes,
            Comment.replies,
            Comment.likes_refreshed_at,
        )
    ).all()
    for src, likes, replies, refreshed in all_rows:
        refreshed_str = refreshed.isoformat() if refreshed else "NULL"
        print(f"    {src}: likes={likes}, replies={replies}, refreshed={refreshed_str}")

    # === 断言 ===
    print("\n=== 断言 ===")
    old_row = s.execute(select(Comment).where(Comment.source_id == "old_001")).scalar_one()
    new_row = s.execute(select(Comment).where(Comment.source_id == "new_001")).scalar_one()

    assert old_row.likes == 42, f"old.likes 应为 42，实际为 {old_row.likes}"
    assert old_row.replies == 3, f"old.replies 应为 3，实际为 {old_row.replies}"
    assert old_row.likes_refreshed_at is not None, "old.likes_refreshed_at 应被设置"

    assert new_row.likes is None, f"new.likes 应为 None，实际为 {new_row.likes}"
    assert new_row.replies is None, f"new.replies 应为 None，实际为 {new_row.replies}"
    assert new_row.likes_refreshed_at is None, "new.likes_refreshed_at 应为 None"

    print("  ✅ 所有断言通过")

# 清理测试库
os.remove(TEST_DB_PATH)
print(f"\n清理测试库: {TEST_DB_PATH}")