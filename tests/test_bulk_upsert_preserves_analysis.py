"""重采不得覆盖已分析结果（2026-09-08 对抗审查 P3 回归）

目标 1 的成本前提：analyzed_at 跳过机制保证「采过的评论不重复标注」。
若 upsert 对已存在评论重置 analyzed_at / 情感字段，每次重采都会整批重标
→ LLM 成本翻倍且数据被覆盖。本测试锁死该行为。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.storage.db as db_module  # noqa: E402
from src.collectors.base import RawComment  # noqa: E402


def setup_tmp_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _, SessionLocal = db_module.init_db(db_url=f"sqlite:///{tmp.name}")
    return SessionLocal, tmp.name


def teardown_tmp_db(path):
    import gc
    gc.collect()
    try:
        os.unlink(path)
    except (FileNotFoundError, PermissionError):
        pass


def _raw(source_id: str, content: str, likes: int | None = None) -> RawComment:
    return RawComment(
        platform="steam",
        source_id=source_id,
        content=content,
        author="tester",
        rating=1,
        language="schinese",
        likes=likes,
        posted_at=datetime(2026, 9, 1, tzinfo=timezone.utc).replace(tzinfo=None),
        extra={"appid": "999"},
    )


def test_upsert_preserves_analyzed_result():
    """重采同一条评论（likes 刷新）→ analyzed_at / sentiment / topic 必须原样保留"""
    from src.storage.db import CommentRepository, init_db
    from datetime import datetime as dt

    SessionLocal, path = setup_tmp_db()
    try:
        with SessionLocal() as s:
            repo = CommentRepository(s)
            repo.upsert(_raw("r-1", "打击感很棒，剧情一般"))
            repo.update_analysis(
                1,
                sentiment="positive",
                sentiment_score=0.6,
                sentiment_confidence=0.9,
                topic="战斗与动作",
                opinions=[{"phrase": "打击感很棒", "sentiment": "positive"}],
                analyzer_version="llm:glm-5.3-flash@abcd1234",
            )
            s.commit()
            marked_at = s.get(db_module.Comment, 1).analyzed_at

        # 回采：likes 刷新（模拟每日 7 天窗口重抓同一条）
        with SessionLocal() as s:
            CommentRepository(s).upsert(_raw("r-1", "打击感很棒，剧情一般", likes=999))
            s.commit()

        with SessionLocal() as s:
            row = s.get(db_module.Comment, 1)
            assert row.analyzed_at == marked_at, "重采不得重置 analyzed_at（否则重复标注）"
            assert row.sentiment == "positive", "重采不得擦除情感结果"
            assert row.topic == "战斗与动作", "重采不得擦除主题"
            assert row.analyzer_version == "llm:glm-5.3-flash@abcd1234"
            assert row.likes == 999, "likes 应正常刷新"
    finally:
        teardown_tmp_db(path)


def test_new_comment_still_unanalyzed():
    """新评论入库 → analyzed_at 为空（进待分析队列），不误标"""
    from src.storage.db import CommentRepository

    SessionLocal, path = setup_tmp_db()
    try:
        with SessionLocal() as s:
            CommentRepository(s).upsert(_raw("r-new", "新评论"))
            s.commit()
            row = s.get(db_module.Comment, 1)
            assert row.analyzed_at is None
            assert row.sentiment is None
    finally:
        teardown_tmp_db(path)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
