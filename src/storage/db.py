"""SQLite 存储层

使用 SQLAlchemy 2.x 同步 API。
个人项目起步用 SQLite，数据量上来后可平滑迁移到 PostgreSQL。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    Index,
    create_engine,
    select,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from ..collectors.base import RawComment

Base = declarative_base()


class Comment(Base):
    """评论主表（含原始数据与分析结果）"""

    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 来源标识
    platform = Column(String(32), nullable=False, index=True)
    source_id = Column(String(128), nullable=False)
    target_id = Column(String(64), nullable=False, index=True)  # 关联游戏/视频ID

    # 原始内容
    content = Column(Text, nullable=False)
    author = Column(String(128))
    author_id = Column(String(128))
    rating = Column(Integer)  # 1=好评 / 0=差评
    language = Column(String(16))
    likes = Column(Integer, default=0)
    replies = Column(Integer, default=0)
    posted_at = Column(DateTime)
    extra_json = Column(Text)  # 平台特有字段 JSON

    # 分析结果（情感）
    sentiment = Column(String(16))  # positive / negative / neutral
    sentiment_score = Column(Float)  # -1 ~ +1
    sentiment_confidence = Column(Float)  # 0 ~ 1

    # 分析结果（主题）
    topic = Column(String(64))  # 主标签
    sub_topics = Column(Text)  # 子标签 JSON list

    # 元数据
    fetched_at = Column(DateTime, default=datetime.utcnow)
    analyzed_at = Column(DateTime)
    extra_meta = Column(Text)  # 目标元数据（如游戏名称）

    __table_args__ = (
        # 同一平台同一评论唯一
        Index("ux_platform_source", "platform", "source_id", unique=True),
        Index("ix_target", "platform", "target_id"),
        Index("ix_sentiment", "platform", "sentiment"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "content": self.content,
            "author": self.author,
            "rating": self.rating,
            "language": self.language,
            "likes": self.likes,
            "replies": self.replies,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "sentiment": self.sentiment,
            "sentiment_score": self.sentiment_score,
            "sentiment_confidence": self.sentiment_confidence,
            "topic": self.topic,
            "sub_topics": json.loads(self.sub_topics) if self.sub_topics else [],
            "analyzed_at": self.analyzed_at.isoformat() if self.analyzed_at else None,
        }


def init_db(db_url: str | None = None) -> tuple:
    """初始化数据库

    Returns:
        (engine, SessionLocal)
    """
    db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///data/voc.db")

    # SQLite 需要单独处理路径
    if db_url.startswith("sqlite:///"):
        db_path = Path(db_url.replace("sqlite:///", ""))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{db_path}"

    engine = create_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, SessionLocal


class CommentRepository:
    """评论仓储 - 提供 upsert / 查询等操作"""

    def __init__(self, session: Session):
        self.session = session

    def upsert(self, raw: RawComment, target_meta: dict | None = None) -> Comment:
        """插入或更新一条评论（基于 platform+source_id 唯一性）"""
        stmt = select(Comment).where(
            Comment.platform == raw.platform,
            Comment.source_id == raw.source_id,
        )
        existing = self.session.execute(stmt).scalar_one_or_none()

        extra_json = json.dumps(raw.extra, ensure_ascii=False) if raw.extra else None
        extra_meta = json.dumps(target_meta, ensure_ascii=False) if target_meta else None

        if existing:
            # 仅更新原始字段（避免覆盖已分析结果）
            existing.content = raw.content
            existing.author = raw.author
            existing.author_id = raw.author_id
            existing.rating = raw.rating
            existing.language = raw.language
            existing.likes = raw.likes
            existing.replies = raw.replies
            existing.posted_at = raw.posted_at
            existing.extra_json = extra_json
            existing.extra_meta = extra_meta
            existing.fetched_at = datetime.utcnow()
            return existing

        comment = Comment(
            platform=raw.platform,
            source_id=raw.source_id,
            target_id=raw.platform + ":" + str(getattr(raw, "target_id", "") or ""),
            content=raw.content,
            author=raw.author,
            author_id=raw.author_id,
            rating=raw.rating,
            language=raw.language,
            likes=raw.likes,
            replies=raw.replies,
            posted_at=raw.posted_at,
            extra_json=extra_json,
            extra_meta=extra_meta,
            fetched_at=datetime.utcnow(),
        )
        # target_id 兜底：使用 source_id 中的 appid（steam场景）
        if raw.platform == "steam" and raw.extra.get("appid"):
            comment.target_id = f"steam:{raw.extra['appid']}"
        self.session.add(comment)
        return comment

    def bulk_upsert(
        self, raws: list[RawComment], target_meta: dict | None = None
    ) -> int:
        """批量插入，返回新插入数（近似）"""
        count = 0
        for r in raws:
            self.upsert(r, target_meta=target_meta)
            count += 1
        self.session.commit()
        return count

    def update_analysis(
        self,
        comment_id: int,
        sentiment: str,
        sentiment_score: float,
        sentiment_confidence: float,
        topic: str | None = None,
        sub_topics: list[str] | None = None,
    ) -> None:
        """更新情感与主题分析结果"""
        stmt = select(Comment).where(Comment.id == comment_id)
        obj = self.session.execute(stmt).scalar_one_or_none()
        if obj:
            obj.sentiment = sentiment
            obj.sentiment_score = sentiment_score
            obj.sentiment_confidence = sentiment_confidence
            obj.topic = topic
            obj.sub_topics = json.dumps(sub_topics, ensure_ascii=False) if sub_topics else None
            obj.analyzed_at = datetime.utcnow()

    def commit(self) -> None:
        self.session.commit()

    def find_unanalyzed(self, limit: int = 100, platform: str | None = None) -> list[Comment]:
        """查找尚未分析（analyzed_at 为空）的评论"""
        stmt = select(Comment).where(Comment.analyzed_at.is_(None))
        if platform:
            stmt = stmt.where(Comment.platform == platform)
        stmt = stmt.limit(limit)
        return list(self.session.execute(stmt).scalars())

    def count(self, platform: str | None = None) -> int:
        from sqlalchemy import func
        stmt = select(func.count(Comment.id))
        if platform:
            stmt = stmt.where(Comment.platform == platform)
        result = self.session.execute(stmt).scalar()
        return result or 0

    def list_by_target(
        self, platform: str, target_id: str, limit: int = 500
    ) -> list[Comment]:
        stmt = (
            select(Comment)
            .where(Comment.platform == platform, Comment.target_id == f"{platform}:{target_id}")
            .order_by(Comment.posted_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars())

    def all_analyzed(self, platform: str | None = None, limit: int = 1000) -> list[Comment]:
        stmt = select(Comment).where(Comment.analyzed_at.is_not(None))
        if platform:
            stmt = stmt.where(Comment.platform == platform)
        stmt = stmt.order_by(Comment.analyzed_at.desc()).limit(limit)
        return list(self.session.execute(stmt).scalars())