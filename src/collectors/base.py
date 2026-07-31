"""采集器抽象基类

所有平台的采集器必须继承 BaseCollector，实现 fetch_comments 方法。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterator


def _utcnow():
    """替代弃用的 ``datetime.utcnow()``，返回 naive UTC datetime"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class RawComment:
    """统一格式的原始评论数据"""

    platform: str  # 来源平台：steam / bilibili / weibo / ...
    source_id: str  # 该平台内的唯一标识（如 Steam 推荐ID）
    content: str  # 评论文本
    author: str | None = None  # 作者昵称（已脱敏或匿名）
    author_id: str | None = None  # 作者平台ID
    rating: int | None = None  # 评分（如 Steam 的 1=推荐 0=不推荐）
    language: str | None = None  # 语言代码（如 schinese / english）
    likes: int = 0  # 点赞数
    replies: int = 0  # 回复数
    posted_at: datetime | None = None  # 发布时间
    extra: dict = field(default_factory=dict)  # 平台特有字段
    fetched_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        d = asdict(self)
        # datetime 序列化
        for k, v in list(d.items()):
            if isinstance(v, datetime):
                d[k] = v.isoformat() if v else None
        return d


class BaseCollector(abc.ABC):
    """采集器抽象基类

    每个平台的采集器应继承此类，实现 fetch_comments。
    """

    platform: str = "base"

    def __init__(self, **kwargs):
        self.config = kwargs

    @abc.abstractmethod
    def fetch_comments(
        self,
        target_id: str,
        *,
        max_count: int = 100,
        language: str | None = None,
        **kwargs,
    ) -> Iterator[RawComment]:
        """拉取指定目标的评论

        Args:
            target_id: 目标对象ID（如 Steam appid）
            max_count: 最大采集数量
            language: 语言过滤（可选）

        Yields:
            RawComment 对象
        """
        ...

    def collect(
        self,
        target_id: str,
        *,
        max_count: int = 100,
        language: str | None = None,
        **kwargs,
    ) -> list[RawComment]:
        """便捷方法：一次性收集所有评论"""
        return list(self.fetch_comments(target_id, max_count=max_count, language=language, **kwargs))