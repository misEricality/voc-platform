"""分析器模块

统一接口，支持多种情感分析后端（DeepSeek / Qwen / GLM / 本地BERT）。
"""

from .base import BaseAnalyzer, AnalysisResult, get_analyzer
from .sentiment_llm import LLMSentimentAnalyzer

__all__ = ["BaseAnalyzer", "AnalysisResult", "get_analyzer", "LLMSentimentAnalyzer"]