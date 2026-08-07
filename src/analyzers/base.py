"""分析器抽象基类"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Opinion:
    """观点：短语 + 情感 + 程序匹配的标签

    设计要点（2026-08-06 v4 · 方案4 观点短语→程序匹配）：
    - phrase: LLM 自由提取的观点短句（从原声提炼，尽量保留原词）
    - sentiment: 观点情感（positive/negative/neutral）
    - sentiment_score: 观点情感分数（-1.0 ~ +1.0）
    - sentiment_confidence: 观点级置信度（0.0 ~ 1.0，方案B 由 LLM 输出）
    - is_core: 是否最核心观点（整条评论至多 1 个）
    - l3: 程序匹配的 L3 标签（匹配不到为 None）
    - full_path: 由 l3 映射得到的完整路径（L1/L2/L3），落盘前填充
    """

    phrase: str
    sentiment: str  # positive / negative / neutral
    sentiment_score: float  # -1.0 ~ +1.0
    sentiment_confidence: float = 0.5  # 0.0 ~ 1.0（方案B 新增）
    is_core: bool = False
    l3: str | None = None  # 程序匹配的 L3（None=未匹配）
    full_path: str | None = None  # 映射后填充
    quote_start: int | None = None
    quote_end: int | None = None

    def to_dict(self) -> dict:
        return {
            "phrase": self.phrase,
            "sentiment": self.sentiment,
            "sentiment_score": self.sentiment_score,
            "sentiment_confidence": self.sentiment_confidence,
            "is_core": self.is_core,
            "l3": self.l3,
            "full_path": self.full_path,
            "quote_start": self.quote_start,
            "quote_end": self.quote_end,
        }


@dataclass
class AnalysisResult:
    """统一格式的分析结果（v3）"""

    sentiment: str  # 整体情感 = 核心观点情感
    sentiment_score: float  # 整体分数 = 核心观点分数
    sentiment_confidence: float  # 0.0 ~ 1.0
    topic: str | None = None  # 由程序从核心观点映射 L1（落盘前填充）
    opinions: list[Opinion] = field(default_factory=list)
    reasoning: str | None = None  # 推理依据（可选，主要用于调试）
    raw: dict = field(default_factory=dict)  # 原始API返回


class BaseAnalyzer(abc.ABC):
    """分析器基类"""

    name: ClassVar[str] = "base"

    @abc.abstractmethod
    def analyze(self, text: str, *, context: dict | None = None) -> AnalysisResult:
        """分析单条文本"""
        ...

    def analyze_batch(
        self, texts: list[str], *, context: dict | None = None
    ) -> list[AnalysisResult]:
        """批量分析，默认串行（子类可重写为并发）"""
        return [self.analyze(t, context=context) for t in texts]


def get_analyzer(provider: str | None = None) -> BaseAnalyzer:
    """根据配置获取分析器实例

    Args:
        provider: deepseek / qwen / glm / local
    """
    provider = (provider or os.getenv("ANALYZER_PROVIDER", "deepseek")).lower()

    if provider == "local":
        try:
            from .sentiment_local import LocalSentimentAnalyzer
            return LocalSentimentAnalyzer()
        except ImportError:
            raise ImportError(
                "本地分析器需要安装 transformers + torch："
                "pip install transformers torch"
            )

    # 默认 LLM 分析器
    from .sentiment_llm import LLMSentimentAnalyzer
    return LLMSentimentAnalyzer(provider=provider)