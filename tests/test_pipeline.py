"""基础单元测试

运行：pytest tests/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collectors.base import RawComment
from src.collectors.steam import SteamCollector, POPULAR_GAMES
from src.storage.db import init_db, CommentRepository


# ===== RawComment 测试 =====

def test_raw_comment_basic():
    rc = RawComment(platform="steam", source_id="123", content="test")
    assert rc.platform == "steam"
    assert rc.source_id == "123"
    assert rc.content == "test"
    assert isinstance(rc.fetched_at, datetime)


def test_raw_comment_to_dict():
    rc = RawComment(platform="steam", source_id="123", content="test", posted_at=datetime(2025, 1, 1))
    d = rc.to_dict()
    assert isinstance(d["posted_at"], str)
    assert d["platform"] == "steam"


# ===== Steam Collector 测试 =====

def test_steam_collector_creation():
    """采集器可创建（不要求 API Key 也能实例化）"""
    collector = SteamCollector(api_key="test")
    assert collector.platform == "steam"


def test_popular_games_known():
    """热门游戏字典应包含 CS2"""
    assert "730" in POPULAR_GAMES
    assert POPULAR_GAMES["730"] == "CS2"


def test_steam_to_raw_conversion():
    """测试 Steam API 返回值到 RawComment 的转换"""
    collector = SteamCollector(api_key="test")
    sample_review = {
        "recommendationid": "12345",
        "author": {"steamid": "123", "playtime_forever": 1000},
        "review": "Great game!",
        "voted_up": True,
        "language": "schinese",
        "votes_up": 10,
        "comment_count": 2,
        "timestamp_created": 1700000000,
        "steam_purchase": True,
    }
    rc = collector._to_raw("730", sample_review)
    assert rc.source_id == "12345"
    assert rc.rating == 1
    assert rc.language == "schinese"
    assert rc.likes == 10
    assert rc.extra["appid"] == "730"


# ===== Storage 测试 =====

@pytest.fixture
def repo():
    """临时数据库 fixture（Windows 兼容性修复：先 dispose 再 unlink）

    为什么不用 :memory:：SQLAlchemy + SQLite 的 :memory: 在多连接/多线程下
    每个连接会得到独立的内存数据库，导致仓库层与测试 fixture 间数据不可见。
    使用 NamedTemporaryFile + 本地文件更稳，但 Windows 上 SQLite 持锁，必须
    在 unlink 前 dispose 连接池，否则会出现 WinError 32。
    """
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine, SessionLocal = init_db(f"sqlite:///{tmp.name}")
    session = SessionLocal()
    r = CommentRepository(session)
    try:
        yield r
    finally:
        # 顺序很关键：先 close session，再 dispose 引擎，最后才删文件
        session.close()
        engine.dispose()
        try:
            os.unlink(tmp.name)
        except (PermissionError, OSError):
            # Windows 上偶发延迟释放；临时目录会在重启时清理
            pass


def test_db_init_and_upsert(repo):
    """测试初始化和单条插入"""
    rc = RawComment(
        platform="steam",
        source_id="abc123",
        content="非常好玩",
        rating=1,
        language="schinese",
    )
    rc.extra = {"appid": "730"}
    repo.upsert(rc)
    repo.commit()
    assert repo.count() == 1


def test_db_upsert_idempotent(repo):
    """同一 source_id 重复 upsert 不会产生多条"""
    rc = RawComment(platform="steam", source_id="abc123", content="first")
    rc.extra = {"appid": "730"}
    repo.upsert(rc)
    repo.commit()

    rc2 = RawComment(platform="steam", source_id="abc123", content="second")
    rc2.extra = {"appid": "730"}
    repo.upsert(rc2)
    repo.commit()

    assert repo.count() == 1
    comment = repo.find_unanalyzed()[0]
    assert comment.content == "second"


def test_db_analysis_update(repo):
    """测试分析结果写入"""
    rc = RawComment(platform="steam", source_id="abc123", content="test")
    rc.extra = {"appid": "730"}
    repo.upsert(rc)
    repo.commit()

    comments = repo.find_unanalyzed()
    assert len(comments) == 1

    repo.update_analysis(
        comments[0].id,
        sentiment="positive",
        sentiment_score=0.8,
        sentiment_confidence=0.95,
        topic="玩法",
        sub_topics=["有趣", "耐玩"],
    )
    repo.commit()

    analyzed = repo.all_analyzed()
    assert len(analyzed) == 1
    assert analyzed[0].sentiment == "positive"
    assert analyzed[0].topic == "玩法"


def test_db_count_and_filter(repo):
    """测试按平台筛选"""
    for i in range(3):
        rc = RawComment(platform="steam", source_id=f"s{i}", content=f"c{i}")
        rc.extra = {"appid": "730"}
        repo.upsert(rc)
    for i in range(2):
        rc = RawComment(platform="other", source_id=f"o{i}", content=f"c{i}")
        repo.upsert(rc)
    repo.commit()

    assert repo.count() == 5
    assert repo.count(platform="steam") == 3
    assert repo.count(platform="other") == 2