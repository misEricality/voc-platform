"""SQLite 存储层

使用 SQLAlchemy 2.x 同步 API。
个人项目起步用 SQLite，数据量上来后可平滑迁移到 PostgreSQL。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    LargeBinary,
    Index,
    create_engine,
    select,
    delete,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from ..collectors.base import RawComment

Base = declarative_base()


def _utcnow():
    """返回 naive UTC datetime，替代弃用的 ``datetime.utcnow()``

    与 ``datetime.utcnow()`` 行为等价（返回不含 tzinfo 的 UTC 时间），
    但不会触发 Python 3.12+ 的 DeprecationWarning。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    # likes / replies 默认为 NULL（表示"尚未回采"）。
    # 数据采集策略：首次入库时不记录点赞数与回复数（避免冷启动 0 误导），
    # 评论发布满 7 天后由 scripts/refresh_likes.py 一次性回采填回。
    likes = Column(Integer, nullable=True)
    replies = Column(Integer, nullable=True)
    posted_at = Column(DateTime)
    extra_json = Column(Text)  # 平台特有字段 JSON

    # 分析结果（情感）
    # sentiment = 核心 L1（topic）所表达的情感（整体情感），非整条评论所有话题综合
    sentiment = Column(String(16))  # positive / negative / neutral
    sentiment_score = Column(Float)  # -1 ~ +1
    sentiment_confidence = Column(Float)  # 0 ~ 1

    # 分析结果（主题）
    topic = Column(String(64))  # 主标签
    sub_topics = Column(Text)  # 子标签 JSON list

    # 元数据
    fetched_at = Column(DateTime, default=_utcnow)
    # likes_refreshed_at: 最近一次回采点赞/回复数的时间（NULL = 尚未回采）
    likes_refreshed_at = Column(DateTime)
    # developer_response_refreshed_at: 最近一次回采开发者回复的时间（NULL = 尚未回采）
    # 机制同 likes：评论发布满 7 天后由回采脚本拉取真实回复，避免 0 误导。
    developer_response_refreshed_at = Column(DateTime)
    analyzed_at = Column(DateTime)
    # 分析溯源（2026-08-21 P10 · 分析结果无版本）：格式 "{provider}:{model}@{prompt_hash8}"
    # provider: llm/local；model: deepseek-v4-flash/qwen3.7-plus/... 或本地模型标识；
    # prompt_hash8: 三个 prompt 文件内容拼接的 SHA256 前 8 位（任一文件改动 → hash 变 → version 变）。
    # 换模型/换 prompt 后存量数据可按此字段分组重打或比对。
    analyzer_version = Column(String(64), nullable=True, index=True)
    extra_meta = Column(Text)  # 目标元数据（如游戏名称）

    __table_args__ = (
        # 同一平台同一评论唯一
        Index("ux_platform_source", "platform", "source_id", unique=True),
        Index("ix_target", "platform", "target_id"),
        Index("ix_sentiment", "platform", "sentiment"),
        # 回采扫描常用：找"已发布 ≥7 天、但还没回采点赞/回复"的评论
        Index("ix_posted_refresh", "posted_at", "likes_refreshed_at"),
        # 回采扫描：找"已发布 ≥7 天、但还没回采开发者回复"的评论
        Index("ix_posted_dev_response", "posted_at", "developer_response_refreshed_at"),
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
            "likes_refreshed_at": self.likes_refreshed_at.isoformat() if self.likes_refreshed_at else None,
            "developer_response_refreshed_at": self.developer_response_refreshed_at.isoformat() if self.developer_response_refreshed_at else None,
            "analyzer_version": self.analyzer_version,
        }


class CommentOpinion(Base):
    """观点表：每条完整标签路径对应一段从原声提炼的观点（颗粒度比原声细）

    设计要点（2026-08-05 v2 重构，对齐工程师 A-E 决策）：
    - full_path: 完整标签路径（如"玩法与内容/玩法机制/动作系统"或"其他/整体评价/整体评价"）
    - sentiment: 观点级情感（positive/negative/neutral），每条观点独立
    - quote: 必须从 comment.content 中可定位（quote_start/quote_end）
    - 允许同路径多观点（同 full_path 不同 quote）
    - 已删除 label / label_level（路径由 full_path 承载）
    """

    __tablename__ = "comment_opinions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(
        Integer,
        nullable=False,
        index=True,
    )
    full_path = Column(String(255), nullable=False, index=True)  # 完整路径 L1/L2[/L3]
    sentiment = Column(String(16), nullable=False)  # 观点级情感
    sentiment_confidence = Column(Float)  # 观点级置信度（0~1，方案B）
    quote = Column(Text, nullable=False)  # 对应原声片段
    quote_start = Column(Integer)  # 在 content 中的起始字符位置（可选）
    quote_end = Column(Integer)  # 在 content 中的结束字符位置（可选）
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_opinions_path", "full_path"),
        Index("ix_opinions_comment", "comment_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "comment_id": self.comment_id,
            "full_path": self.full_path,
            "sentiment": self.sentiment,
            "sentiment_confidence": self.sentiment_confidence,
            "quote": self.quote,
            "quote_start": self.quote_start,
            "quote_end": self.quote_end,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CommentEmbedding(Base):
    """评论向量表：每条评论一个 embedding 向量（衍生数据，可随时全量重建）

    设计要点（2026-08-11）：
    - 独立表 + model 字段：换 embedding 模型 = 清表全量重算（衍生数据，重建即修复），
      因此 model 字段强制记录，禁止新旧模型向量混存。
    - vector 存 L2 归一化后的 float32 数组（tobytes()），查询用内积 = 余弦。
    - 单空间约束：任意时刻表内只允许一种 model（由迁移脚本 --force 与读取侧断言保证）。
    - 规模：512 维 × 4B ≈ 2KB/条，1 万条约 20MB，SQLite BLOB 无压力。
    """

    __tablename__ = "comment_embeddings"

    comment_id = Column(Integer, primary_key=True)  # 1:1 → comments.id
    model = Column(String(64), nullable=False, index=True)  # 如 BAAI/bge-small-zh-v1.5
    dim = Column(Integer, nullable=False)  # 512
    vector = Column(LargeBinary, nullable=False)  # float32 tobytes()
    created_at = Column(DateTime, default=_utcnow)

    def __repr__(self) -> str:
        return f"<CommentEmbedding comment_id={self.comment_id} model={self.model} dim={self.dim}>"


class Danmaku(Base):
    """B 站弹幕表（2026-08-13 · BILIBILI_COLLECTION.md 规格）

    设计要点：
    - video_id 对齐 comments.target_id 格式（'bilibili:video:{aid}'）
    - 双时间戳：progress（视频内秒点，对齐内容段落）+ posted_at（发送时间，区分早期/后期）
    - user_hash 为接口匿名 hash，不落真实身份（合规）
    - 弹幕不进 LLM 打标链路（成本红线），仅做词典匹配 + 时间窗聚合
    """

    __tablename__ = "danmaku"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(64), nullable=False, index=True)  # bilibili:video:{aid}
    cid = Column(String(32))  # 分 P 弹幕池 id
    content = Column(Text, nullable=False)
    progress = Column(Integer)  # 视频内时间点（秒）
    mode = Column(Integer)  # 弹幕类型（1=滚动 4=底部 5=顶部 7=高级）
    color = Column(Integer)  # 弹幕颜色（可作情绪粗信号）
    user_hash = Column(String(32))  # 用户匿名 hash
    posted_at = Column(DateTime)  # 弹幕发送时间
    fetched_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_danmaku_video", "video_id", "progress"),
        Index("ux_danmaku_dedup", "video_id", "progress", "content", "user_hash"),
    )


# 弹幕分桶宽度（秒）：30s 固定桶（2026-09-04 B站视频看板，工程师确认）
DANMAKU_BUCKET_WIDTH_SEC = 30


def bucket_danmaku_rows(rows: list, *, width: int = DANMAKU_BUCKET_WIDTH_SEC) -> list[dict]:
    """弹幕 ORM 行 → 30s 固定桶列表（pipeline 高光总结与 API 时间轴共用）

    Returns: [{start_sec, end_sec, count, rows}]，按 start_sec 升序
    """
    grouped: dict[int, list] = {}
    for r in rows:
        idx = (r.progress or 0) // width
        grouped.setdefault(idx, []).append(r)
    return [
        {"start_sec": idx * width, "end_sec": (idx + 1) * width, "count": len(rs), "rows": rs}
        for idx, rs in sorted(grouped.items())
    ]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "video_id": self.video_id,
            "cid": self.cid,
            "content": self.content,
            "progress": self.progress,
            "mode": self.mode,
            "color": self.color,
            "user_hash": self.user_hash,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
        }


class BilibiliQueue(Base):
    """B 站采集队列（2026-08-23 · B 站自动化「待采清单」）

    设计：
    - 工程师手输 BV 号到「待采清单」（status=pending，pubdate 未识别）
    - 系统识别投稿时间后填入 pubdate 与 due_date（pubdate + 7 天）
    - 每天 cron 扫 status='scheduled' AND due_date <= today 的视频，调 pipeline
    - 采成功标 fetched；失败重试 N 次仍败入 dead-letter

    状态机：
        pending    → 待识别 pubdate（刚加入）
        scheduled  → pubdate 已识别，due_date 已计算（等待 cron）
        fetching   → 今日 cron 已取，正在采
        fetched    → 成功
        failed     → 失败重试耗尽（dead-letter）
        paused     → 人工暂停（2026-09-01 Web 看板新增；任意非终态可暂停）
                     runner 只扫 scheduled，paused 天然被跳过；
                     恢复：pubdate 已识别 → scheduled / 未识别 → pending

    注意：status='due' 不存（=scheduled AND due_date <= today，查询时计算）
    """

    __tablename__ = "bilibili_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    bv_id = Column(String(20), nullable=False, unique=True, index=True)
    title = Column(String(512))  # 视频标题（识别后填入）
    pubdate = Column(DateTime)  # 投稿时间（识别后填入）
    due_date = Column(DateTime, index=True)  # = pubdate + 7d，cron 用此判断
    status = Column(String(16), nullable=False, default="pending")
    # status in: pending / scheduled / fetching / fetched / failed
    added_at = Column(DateTime, default=_utcnow, nullable=False)
    added_by = Column(String(32), default="manual")  # 留扩展：CLI / keyword:xxx / up:xxx
    fetched_at = Column(DateTime)  # 采集完成时间
    comment_count = Column(Integer)  # 采到的评论数（fetched 后填）
    danmaku_count = Column(Integer)  # 采到的弹幕数
    fail_count = Column(Integer, default=0, nullable=False)
    fail_reason = Column(Text)  # 最后一次失败原因
    revisit = Column(Boolean, default=False, nullable=False)  # high-value 重采标记
    note = Column(Text)  # 工程师备注

    # ---- 视频快照（2026-09-04 · B站视频看板；采集/识别时从 view API 一次性快照） ----
    aid = Column(Integer)  # AV 号（评论/弹幕 target_id = "bilibili:video:{aid}" 的映射键）
    pic = Column(String(512))  # 封面 URL（B 站 CDN，前端直连）
    owner_name = Column(String(128))  # UP 主昵称
    owner_mid = Column(String(64))  # UP 主 uid（主页链接）
    view = Column(Integer)  # 播放量
    like_count = Column(Integer)  # 点赞（三连之一）
    coin = Column(Integer)  # 投币（三连之一）
    favorite = Column(Integer)  # 收藏（三连之一）
    reply_total = Column(Integer)  # 原视频评论总量（stat.reply）
    danmaku_total = Column(Integer)  # 原视频弹幕总量（stat.danmaku）
    duration = Column(Integer)  # 视频时长（秒）
    tags_json = Column(Text)  # 视频标签 JSON list
    highlights_json = Column(Text)  # 高光时刻 JSON：{generated_at, buckets:[{start_sec,end_sec,count,summary}]}
    stats_fetched_at = Column(DateTime)  # 快照时间

    __table_args__ = (
        Index("ix_biliq_status_due", "status", "due_date"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bv_id": self.bv_id,
            "title": self.title,
            "pubdate": self.pubdate.isoformat() if self.pubdate else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "status": self.status,
            "added_at": self.added_at.isoformat() if self.added_at else None,
            "added_by": self.added_by,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "comment_count": self.comment_count,
            "danmaku_count": self.danmaku_count,
            "fail_count": self.fail_count,
            "fail_reason": self.fail_reason,
            "revisit": self.revisit,
            "note": self.note,
            "aid": self.aid,
            "pic": self.pic,
            "owner_name": self.owner_name,
            "owner_mid": self.owner_mid,
            "view": self.view,
            "like_count": self.like_count,
            "coin": self.coin,
            "favorite": self.favorite,
            "reply_total": self.reply_total,
            "danmaku_total": self.danmaku_total,
            "duration": self.duration,
            "stats_fetched_at": self.stats_fetched_at.isoformat() if self.stats_fetched_at else None,
        }


class CollectTask(Base):
    """采集任务表（2026-09-01 · Web 看板「系统管理」立项，见 docs/architecture/WEB_DASHBOARD.md）

    设计：
    - 目前仅承载 Steam 每日增量目标（从 config/monitoring/targets.yaml 迁入 DB）；
      B 站任务复用 bilibili_queue（队列语义更丰富），API 层做统一视图
    - daily_incremental_collect.py 优先读本表 enabled=1 的任务；表为空时
      回退读 targets.yaml（并自动种子化，幂等）
    - enabled=False = 「已暂停」（表格显示态），与 targets.yaml 的 enabled 同语义
    - count=None = auto 模式（按时间窗耗尽翻页，2026-08-25 语义）
    """

    __tablename__ = "collect_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(16), nullable=False, default="steam")  # 预留多平台
    target_id = Column(String(64), nullable=False)  # appid（steam 为纯数字字符串）
    name = Column(String(255))  # 游戏名（Steam appdetails 回填，可改）
    language = Column(String(16), default="schinese")
    count = Column(Integer, nullable=True)  # None = auto
    source_url = Column(String(255))  # store 页 URL（表格显示用）
    enabled = Column(Integer, nullable=False, default=1)  # 1=采集中 0=已暂停（SQLite bool）
    created_at = Column(DateTime, default=_utcnow)
    last_collected_at = Column(DateTime)  # 采集侧回写（预留）

    __table_args__ = (
        Index("ux_collect_task", "platform", "target_id", unique=True),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "target_id": self.target_id,
            "name": self.name,
            "language": self.language,
            "count": self.count,
            "source_url": self.source_url,
            "enabled": bool(self.enabled),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_collected_at": self.last_collected_at.isoformat() if self.last_collected_at else None,
        }


def init_db(db_url: str | None = None) -> tuple:
    """初始化数据库

    包含轻量 schema 演进（2026-08-21）：表已存在但缺列时，自动 ADD COLUMN；
    仅支持 nullable 列（含默认 NULL 的 server_default）。NOT NULL 带非空默认值
    的演进仍需专用迁移脚本。

    Returns:
        (engine, SessionLocal)
    """
    from sqlalchemy import text

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

    # SQLite 并发加固（2026-09-01 · Web 看板：前端服务随时读 × cron 每日写）：
    # - journal_mode=WAL：读写不互斥（读者不再阻塞写者，根治 Streamlit 时代文件锁坑）
    # - busy_timeout=5000：写锁冲突时等 5s 而非立刻抛 database is locked
    if db_url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - 简单 pragma
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    Base.metadata.create_all(engine)

    # 轻量 schema 演进：检测已存在但缺列的表 → 自动 ADD COLUMN
    # 启动成本：每张表 1 次 PRAGMA，可忽略；只在缺列时 ALTER
    if db_url.startswith("sqlite"):
        with engine.begin() as conn:
            for table_name, table in Base.metadata.tables.items():
                cols_info = conn.execute(
                    text(f"PRAGMA table_info({table_name})")
                ).fetchall()
                if not cols_info:
                    continue  # 表不存在（已由 create_all 创建）
                existing = {row[1] for row in cols_info}  # row[1] = column name
                for col in table.columns:
                    if col.name in existing:
                        continue
                    # 仅自动演进 nullable 列；NOT NULL + 非空默认值的情况
                    # 复杂度高，留给专用迁移脚本
                    if not col.nullable:
                        raise RuntimeError(
                            f"init_db 自动演进不支持 NOT NULL 列 {table_name}.{col.name}；"
                            "请写专用迁移脚本"
                        )
                    col_type = col.type.compile(engine.dialect)
                    sql = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}"
                    conn.execute(text(sql))

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return engine, SessionLocal


class GameMeta(Base):
    """游戏元数据表（2026-09-04 · 游戏对比看板，见 docs/architecture/WEB_DASHBOARD.md）

    来源：Steam appdetails（发行日期）+ appreviews（全量评测数/评级描述），
    由 /api/games/meta 懒加载刷新（缺行或 fetched_at 超 TTL 时拉取，失败不阻塞）。
    封面图下载到 data/covers/{appid}.jpg（library_600x900 竖版，随 data/ 目录同步部署）。
    """

    __tablename__ = "game_meta"

    target_id = Column(String(64), primary_key=True)  # 形如 "steam:2358720"
    release_date = Column(Date)  # Steam 发售日（解析失败为 NULL）
    rating_desc = Column(String(32))  # 评测描述（中文）：好评如潮/特别好评/褒贬不一/…
    review_score = Column(Integer)  # Steam review_score 1~9（appreviews 不随 language 翻译描述，2026-09-04 补）
    total_reviews = Column(Integer)  # Steam 全量评测数
    total_positive = Column(Integer)  # Steam 全量好评数
    cover_file = Column(String(255))  # 封面文件名（data/covers/ 下相对路径），NULL=未下载
    fetched_at = Column(DateTime, default=_utcnow)

    # 刷新 TTL：/api/games/meta 超过该时长重新拉取（Steam 评级/评测数每日量级变化小）
    REFRESH_TTL_HOURS = 24

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "rating_desc": self.rating_desc,
            "review_score": self.review_score,
            "total_reviews": self.total_reviews,
            "total_positive": self.total_positive,
            "cover_file": self.cover_file,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


def _topic_segment(full_path: str, level: str) -> str:
    """从 full_path 提取指定层级段。

    L1/L2/L3；注意 L3 词可能自带斜杠（如"整活/梗"），故 L3 取第 3 段起拼接。
    """
    parts = (full_path or "").split("/")
    if level == "L1":
        return parts[0] if parts else (full_path or "")
    if level == "L2":
        return parts[1] if len(parts) > 1 else (parts[0] if parts else "")
    return "/".join(parts[2:]) if len(parts) > 2 else (parts[-1] if parts else "")


class CommentRepository:
    """评论仓储 - 提供 upsert / 查询等操作"""

    def __init__(self, session: Session):
        self.session = session

    def upsert(self, raw: RawComment, target_meta: dict | None = None) -> Comment:
        """插入或更新一条评论（基于 platform+source_id 唯一性）

        写入规则：
        - likes / replies 为 None（首次采集） → 不覆盖已有评论上的 likes/replies
        - likes / replies 为整数（回采）→ 覆盖 + 写入 likes_refreshed_at
        """
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
            # likes / replies 只有在传入值（非 None）时才覆盖 —— 首次采集时不擦掉已有值
            if raw.likes is not None:
                existing.likes = raw.likes
                existing.likes_refreshed_at = _utcnow()
            if raw.replies is not None:
                existing.replies = raw.replies
                existing.likes_refreshed_at = _utcnow()
            existing.posted_at = raw.posted_at
            existing.extra_json = extra_json
            if target_meta:
                existing.extra_meta = extra_meta
            existing.fetched_at = _utcnow()
            # developer_response 回采信号：fetch_metadata=True 时 raw.extra 里有该 key
            if raw.extra and "developer_response" in raw.extra:
                existing.developer_response_refreshed_at = _utcnow()
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
            likes_refreshed_at=_utcnow() if raw.likes is not None else None,
            # 新插入时如果 fetch_metadata=True 表示已经是回采模式，记录时间
            developer_response_refreshed_at=(
                _utcnow()
                if raw.extra and "developer_response" in raw.extra
                else None
            ),
            fetched_at=_utcnow(),
        )
        # target_id 兜底：使用 source_id 中的 appid（steam场景）
        if raw.platform == "steam" and raw.extra.get("appid"):
            comment.target_id = f"steam:{raw.extra['appid']}"
        # bilibili：target_id = bilibili:video:{aid}
        if raw.platform == "bilibili" and raw.extra.get("aid"):
            comment.target_id = f"bilibili:video:{raw.extra['aid']}"
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
        opinions: list[dict] | None = None,
        valid_l2_labels: set[str] | None = None,
        valid_l1_labels: set[str] | None = None,
        analyzer_version: str | None = None,
    ) -> None:
        """更新情感与主题分析结果

        Args:
            opinions: 观点列表，每项 {label, level, quote, quote_start?, quote_end?}
            valid_l2_labels: 合法 L2 标签集合；用于过滤越界 sub_topics（不留盘）
            valid_l1_labels: 合法 L1 标签集合；用于过滤越界 topic
            analyzer_version: 分析溯源标识（"{provider}:{model}@{prompt_hash8}"）；
                来自 analyzer.analyzer_version 属性；不传/为 None 则不写入（兼容旧调用）。
        """
        stmt = select(Comment).where(Comment.id == comment_id)
        obj = self.session.execute(stmt).scalar_one_or_none()
        if not obj:
            return

        # 1. 过滤越界标签（业务规则：越界不留盘）
        clean_topic = topic
        if valid_l1_labels is not None and topic not in valid_l1_labels:
            clean_topic = None  # topic 越界 → 置空

        clean_subs = list(sub_topics) if sub_topics else []
        if valid_l2_labels is not None:
            clean_subs = [s for s in clean_subs if s in valid_l2_labels]

        obj.sentiment = sentiment
        obj.sentiment_score = sentiment_score
        obj.sentiment_confidence = sentiment_confidence
        obj.topic = clean_topic
        obj.sub_topics = json.dumps(clean_subs, ensure_ascii=False) if clean_subs else None
        obj.analyzed_at = _utcnow()
        # analyzer_version：缺省时不擦旧值（让旧 caller 也能安全复用），
        # 传非 None 才覆盖。
        if analyzer_version is not None:
            obj.analyzer_version = analyzer_version

        # 2. 同步 opinion 表（先删旧再插新）
        if opinions is not None:
            # 删旧
            del_stmt = select(CommentOpinion).where(
                CommentOpinion.comment_id == comment_id
            )
            old_opinions = list(self.session.execute(del_stmt).scalars())
            for op in old_opinions:
                self.session.delete(op)
            self.session.flush()

            # 插新（每条 opinion = full_path + sentiment + quote；方案4 下 quote 存 phrase）
            content_text = obj.content or ""
            for op_data in opinions:
                full_path = (op_data.get("full_path") or "").strip()
                op_sentiment = (op_data.get("sentiment") or "neutral").strip().lower()
                if op_sentiment not in {"positive", "negative", "neutral"}:
                    op_sentiment = "neutral"
                # 方案4：观点文本是 phrase；兼容旧 quote 字段名
                quote = (op_data.get("phrase") or op_data.get("quote") or "").strip()
                quote_start = op_data.get("quote_start")
                quote_end = op_data.get("quote_end")
                # 方案B：opinion 级置信度（可能缺失 → NULL）
                op_conf = op_data.get("sentiment_confidence")
                if op_conf is not None:
                    try:
                        op_conf = float(op_conf)
                    except (TypeError, ValueError):
                        op_conf = None
                    if op_conf is not None and not 0.0 <= op_conf <= 1.0:
                        op_conf = None

                if not full_path or not quote:
                    continue

                # 自动定位 quote 在 content 中的位置（若 LLM 未提供）
                if quote_start is None and quote in content_text:
                    quote_start = content_text.index(quote)
                    quote_end = quote_start + len(quote)

                self.session.add(CommentOpinion(
                    comment_id=comment_id,
                    full_path=full_path,
                    sentiment=op_sentiment,
                    sentiment_confidence=op_conf,
                    quote=quote,
                    quote_start=quote_start,
                    quote_end=quote_end,
                ))

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

    # ==================== P3 多目标对比 ====================

    def list_targets(self, platform: str | None = "steam") -> list[dict]:
        """目标清单 + 聚合指标（SQL GROUP BY，不整表拉取）。

        返回每项：target_id / name / appid / total / analyzed /
        recommend_rate(Steam 推荐率%) / avg_score / pos / neg / neu
        """
        from sqlalchemy import case, func

        cols = [
            Comment.target_id,
            func.count(Comment.id).label("total"),
            func.count(Comment.analyzed_at).label("analyzed"),
            func.sum(Comment.rating).label("rating_sum"),
            func.count(Comment.rating).label("rating_cnt"),
            func.avg(Comment.sentiment_score).label("avg_score"),
            func.sum(case((Comment.sentiment == "positive", 1), else_=0)).label("pos"),
            func.sum(case((Comment.sentiment == "negative", 1), else_=0)).label("neg"),
            func.sum(case((Comment.sentiment == "neutral", 1), else_=0)).label("neu"),
            func.max(Comment.extra_meta).label("extra_meta"),
        ]
        stmt = (
            select(*cols)
            .group_by(Comment.target_id)
            .order_by(func.count(Comment.id).desc())
        )
        if platform:
            stmt = stmt.where(Comment.platform == platform)

        out: list[dict] = []
        for row in self.session.execute(stmt):
            tid, total, analyzed, rating_sum, rating_cnt, avg_score, pos, neg, neu, extra_meta = row
            name = tid
            if extra_meta:
                try:
                    name = (json.loads(extra_meta) or {}).get("name") or tid
                except (json.JSONDecodeError, TypeError):
                    pass
            recommend = None
            if rating_cnt:
                recommend = round((rating_sum or 0) / rating_cnt * 100, 1)
            out.append({
                "target_id": tid,
                "name": name,
                "appid": tid.split(":", 1)[1] if ":" in tid else tid,
                "total": int(total or 0),
                "analyzed": int(analyzed or 0),
                "recommend_rate": recommend,
                "avg_score": round(avg_score, 3) if avg_score is not None else None,
                "pos": int(pos or 0), "neg": int(neg or 0), "neu": int(neu or 0),
            })
        return out

    def sentiment_ratio_by_targets(self, targets: list[str]) -> "pd.DataFrame":
        """评论级情感构成占比（positive/neutral/negative + n），按目标。"""
        import pandas as pd
        from sqlalchemy import case, func

        if not targets:
            return pd.DataFrame(columns=["target_id", "positive", "neutral", "negative", "n"])
        stmt = (
            select(
                Comment.target_id,
                func.sum(case((Comment.sentiment == "positive", 1), else_=0)).label("pos"),
                func.sum(case((Comment.sentiment == "negative", 1), else_=0)).label("neg"),
                func.sum(case((Comment.sentiment == "neutral", 1), else_=0)).label("neu"),
            )
            .where(Comment.target_id.in_(targets))
            .group_by(Comment.target_id)
        )
        data = []
        for tid, pos, neg, neu in self.session.execute(stmt):
            pos, neg, neu = pos or 0, neg or 0, neu or 0
            n = pos + neg + neu
            data.append({
                "target_id": tid,
                "positive": round(pos / n * 100, 1) if n else 0,
                "neutral": round(neu / n * 100, 1) if n else 0,
                "negative": round(neg / n * 100, 1) if n else 0,
                "n": n,
            })
        return pd.DataFrame(data)

    def opinion_matrix(
        self,
        targets: list[str],
        level: str = "L1",
        sentiment: str | None = None,
        exclude_meta: bool = True,
    ) -> "pd.DataFrame":
        """主题 × 目标 观点计数矩阵（观点级，热力图用）。

        level: L1/L2/L3；sentiment: positive/negative/None=全部；
        exclude_meta: 排除「综合与元表达」兜底桶（避免对比被整体评价淹没）。
        """
        import pandas as pd

        if not targets:
            return pd.DataFrame()
        stmt = (
            select(Comment.target_id, CommentOpinion.full_path, CommentOpinion.sentiment)
            .join(Comment, Comment.id == CommentOpinion.comment_id)
            .where(Comment.target_id.in_(targets))
        )
        records = []
        for tid, fp, senti in self.session.execute(stmt):
            if exclude_meta and (fp or "").startswith("综合与元表达"):
                continue
            if sentiment and senti != sentiment:
                continue
            records.append({"target_id": tid, "topic": _topic_segment(fp, level)})
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        return (
            df.pivot_table(index="topic", columns="target_id", aggfunc="size", fill_value=0)
        )

    def negative_pain_points(
        self, targets: list[str], level: str = "L2", top: int = 5
    ) -> dict[str, list[tuple[str, int]]]:
        """每目标负面观点 TOP 路径（痛点表用）。"""
        from collections import defaultdict
        from sqlalchemy import func

        if not targets:
            return {}
        stmt = (
            select(Comment.target_id, CommentOpinion.full_path, func.count(CommentOpinion.id))
            .join(Comment, Comment.id == CommentOpinion.comment_id)
            .where(
                Comment.target_id.in_(targets),
                CommentOpinion.sentiment == "negative",
                ~CommentOpinion.full_path.like("综合与元表达%"),
            )
            .group_by(Comment.target_id, CommentOpinion.full_path)
        )
        agg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for tid, fp, cnt in self.session.execute(stmt):
            agg[tid][_topic_segment(fp, level)] += cnt or 0
        out: dict[str, list[tuple[str, int]]] = {}
        for tid, segs in agg.items():
            out[tid] = sorted(segs.items(), key=lambda x: -x[1])[:top]
        return out

    # ==================== 向量（embedding）相关 ====================

    def save_embeddings(self, ids: list[int], vectors, model: str, dim: int) -> int:
        """批量保存/覆盖评论向量（comment_id 为键，重复执行 = 幂等覆盖）

        Args:
            ids: 评论 id 列表（与 vectors 行一一对应）
            vectors: (N, dim) float32 数组（已 L2 归一化）
            model: embedding 模型标识（如 BAAI/bge-small-zh-v1.5）
            dim: 向量维度

        Returns:
            写入行数
        """
        import numpy as np

        rows = 0
        for cid, vec in zip(ids, vectors):
            blob = np.asarray(vec, dtype=np.float32).tobytes()
            stmt = select(CommentEmbedding).where(CommentEmbedding.comment_id == cid)
            exist = self.session.execute(stmt).scalar_one_or_none()
            if exist:
                exist.model, exist.dim, exist.vector = model, dim, blob
                exist.created_at = _utcnow()
            else:
                self.session.add(
                    CommentEmbedding(comment_id=cid, model=model, dim=dim, vector=blob)
                )
            rows += 1
        self.session.commit()
        return rows

    def replace_all_embeddings(self, ids: list[int], vectors, model: str, dim: int) -> int:
        """全量替换向量（--force 迁移用）：单事务 DELETE 全部 + INSERT 全部

        原子性：全部向量先编码到内存（调用方负责），本方法内一个事务完成替换；
        SQLite 事务中途失败自动回滚，旧向量保留 → 任意时刻表内只有完整的一种模型。
        """
        self.session.execute(delete(CommentEmbedding))
        self.session.flush()
        return self.save_embeddings(ids, vectors, model, dim)

    def find_missing_embedding_ids(
        self,
        platform: str | None = None,
        target_id: str | None = None,
        limit: int | None = 100,
    ) -> list[int]:
        """找还没有向量的评论 id（增量向量化 / 断点续跑用）

        limit: 返回条数上限；``None`` = 不设上限（该目标全部缺失）。
        """
        stmt = (
            select(Comment.id)
            .outerjoin(CommentEmbedding, CommentEmbedding.comment_id == Comment.id)
            .where(CommentEmbedding.comment_id.is_(None))
        )
        if platform:
            stmt = stmt.where(Comment.platform == platform)
        if target_id:
            stmt = stmt.where(Comment.target_id == target_id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [r for (r,) in self.session.execute(stmt)]

    def embedding_models_in_use(self) -> list[str]:
        """表内已有的模型标识（单空间断言用；正常应 ≤1 种）"""
        return [r for (r,) in self.session.execute(select(CommentEmbedding.model).distinct())]

    def count_embeddings(self) -> int:
        from sqlalchemy import func

        return (
            self.session.execute(select(func.count(CommentEmbedding.comment_id))).scalar()
            or 0
        )

    # ==================== B 站弹幕（danmaku）相关 ====================

    def save_danmaku(self, video_id: str, cid: str | None, items: list[dict]) -> int:
        """批量写入弹幕（幂等去重：video_id+progress+content+user_hash 唯一）

        Args:
            video_id: 视频 target_id（'bilibili:video:{aid}'）
            cid: 分 P 弹幕池 id
            items: [{content, progress, mode, color, user_hash, posted_at}, ...]
                posted_at 可为 datetime 或 unix 秒或 None

        Returns:
            新增条数
        """
        from datetime import datetime as _dt

        inserted = 0
        for it in items:
            content = (it.get("content") or "").strip()
            progress = it.get("progress")
            if not content:
                continue
            user_hash = it.get("user_hash") or ""
            # 去重查询（唯一键：video_id+progress+content+user_hash）
            stmt = select(Danmaku).where(
                Danmaku.video_id == video_id,
                Danmaku.progress == progress,
                Danmaku.content == content,
                Danmaku.user_hash == user_hash,
            )
            if self.session.execute(stmt).scalar_one_or_none():
                continue
            # posted_at 归一化：unix 秒 → naive UTC datetime；datetime 原样；None → None
            posted = it.get("posted_at")
            if isinstance(posted, (int, float)):
                posted = _dt.fromtimestamp(posted, tz=timezone.utc).replace(tzinfo=None)
            elif isinstance(posted, str):
                try:
                    posted = _dt.fromtimestamp(float(posted), tz=timezone.utc).replace(tzinfo=None)
                except ValueError:
                    posted = None
            self.session.add(
                Danmaku(
                    video_id=video_id,
                    cid=cid,
                    content=content,
                    progress=progress,
                    mode=it.get("mode"),
                    color=it.get("color"),
                    user_hash=user_hash,
                    posted_at=posted,
                )
            )
            inserted += 1
        self.session.commit()
        return inserted

    def count_danmaku(self, video_id: str | None = None) -> int:
        from sqlalchemy import func

        stmt = select(func.count(Danmaku.id))
        if video_id:
            stmt = stmt.where(Danmaku.video_id == video_id)
        return self.session.execute(stmt).scalar() or 0

    def load_embedding_matrix(self, model: str | None = None) -> tuple:
        """全量加载向量矩阵（L2 归一化存储，内积 = 余弦）

        Returns:
            (matrix: np.ndarray (N, dim) float32, ids: list[int])
            空表返回 (zeros((0,0)), [])
        """
        import numpy as np

        stmt = select(CommentEmbedding)
        if model:
            stmt = stmt.where(CommentEmbedding.model == model)
        rows = list(self.session.execute(stmt).scalars())
        if not rows:
            return np.zeros((0, 0), dtype=np.float32), []
        matrix = np.frombuffer(
            b"".join(r.vector for r in rows), dtype=np.float32
        ).reshape(len(rows), rows[0].dim)
        return matrix, [r.comment_id for r in rows]

    def get_comments_by_ids(self, ids: list[int]) -> list[Comment]:
        if not ids:
            return []
        stmt = select(Comment).where(Comment.id.in_(ids))
        return list(self.session.execute(stmt).scalars())


class CollectTaskRepository:
    """采集任务仓储（collect_tasks 表 CRUD；2026-09-01 Web 看板）"""

    def __init__(self, session: Session):
        self.session = session

    def list_all(self, enabled_only: bool = False, platform: str | None = None) -> list[CollectTask]:
        stmt = select(CollectTask).order_by(CollectTask.id)
        if enabled_only:
            stmt = stmt.where(CollectTask.enabled == 1)
        if platform:
            stmt = stmt.where(CollectTask.platform == platform)
        return list(self.session.execute(stmt).scalars())

    def get(self, task_id: int) -> CollectTask | None:
        stmt = select(CollectTask).where(CollectTask.id == task_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_target(self, platform: str, target_id: str) -> CollectTask | None:
        stmt = select(CollectTask).where(
            CollectTask.platform == platform,
            CollectTask.target_id == str(target_id),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def create(
        self,
        platform: str,
        target_id: str,
        *,
        name: str | None = None,
        language: str | None = "schinese",
        count: int | None = None,
        source_url: str | None = None,
    ) -> CollectTask:
        """新增任务；同 platform+target_id 已存在则抛 ValueError"""
        if self.get_by_target(platform, target_id) is not None:
            raise ValueError(f"任务已存在：{platform}:{target_id}")
        task = CollectTask(
            platform=platform,
            target_id=str(target_id),
            name=name,
            language=language,
            count=count,
            source_url=source_url,
            enabled=1,
        )
        self.session.add(task)
        self.session.commit()
        return task

    def set_enabled(self, task_id: int, enabled: bool) -> CollectTask | None:
        task = self.get(task_id)
        if task is not None:
            task.enabled = 1 if enabled else 0
            self.session.commit()
        return task

    def update(
        self,
        task_id: int,
        *,
        name: str | None = None,
        language: str | None = None,
        count: int | None = None,
        clear_count: bool = False,
    ) -> CollectTask | None:
        """编辑任务（target_id/platform 不可改）；clear_count=True 把 count 置回 auto(None)"""
        task = self.get(task_id)
        if task is not None:
            if name is not None:
                task.name = name
            if language is not None:
                task.language = language
            if clear_count:
                task.count = None
            elif count is not None:
                task.count = count
            self.session.commit()
        return task

    def delete(self, task_id: int) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        self.session.delete(task)
        self.session.commit()
        return True

    def count(self) -> int:
        from sqlalchemy import func

        return (
            self.session.execute(select(func.count(CollectTask.id))).scalar() or 0
        )


def seed_collect_tasks_from_yaml(session: Session, yaml_path) -> int:
    """把 config/monitoring/targets.yaml 的 targets 段种子化进 collect_tasks（幂等）

    - 已存在（platform+target_id 相同）的跳过
    - excluded_targets 段不迁移（已归档目标保持排除）
    - 仅迁移 Steam 条目（B 站走 bilibili_queue）

    Returns:
        新增条数
    """
    import yaml as _yaml

    path = Path(yaml_path)
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        cfg = _yaml.safe_load(f) or {}

    repo = CollectTaskRepository(session)
    added = 0
    for t in cfg.get("targets", []) or []:
        if t.get("platform") != "steam":
            continue
        tid = str(t.get("id", "")).strip()
        if not tid or repo.get_by_target("steam", tid) is not None:
            continue
        session.add(CollectTask(
            platform="steam",
            target_id=tid,
            name=t.get("name"),
            language=t.get("language", "schinese"),
            count=t.get("count"),
            enabled=1 if t.get("enabled", True) else 0,
        ))
        added += 1
    if added:
        session.commit()
    return added
