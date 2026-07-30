"""分析器抽象基类"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class AnalysisResult:
    """统一格式的分析结果"""

    sentiment: str  # positive / negative / neutral
    sentiment_score: float  # -1.0 ~ +1.0
    sentiment_confidence: float  # 0.0 ~ 1.0
    topic: str | None = None  # 主标签（如：性能/玩法/价格/客服...）
    sub_topics: list[str] = field(default_factory=list)
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