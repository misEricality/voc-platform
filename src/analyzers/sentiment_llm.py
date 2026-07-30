"""基于大模型的情感与主题分析器

兼容 OpenAI 协议的 API（DeepSeek / Qwen / GLM 全部支持）。
通过环境变量配置 provider 与 key，可热切换。
"""

from __future__ import annotations

import json
import os
import re

from openai import OpenAI

from .base import BaseAnalyzer, AnalysisResult


# 单条文本的情感+主题分析 prompt
SYSTEM_PROMPT = """你是一名资深消费者洞察分析师，擅长从游戏评测、产品评论、社交媒体反馈中提取情感与主题。
请严格按照 JSON 格式输出，不要输出任何 JSON 之外的内容。"""

USER_PROMPT_TEMPLATE = """请分析以下消费者评论的情感与主题：

【评论文本】
{text}

【可选上下文】
{context}

请按以下 JSON Schema 输出：
{{
  "sentiment": "positive" | "negative" | "neutral",
  "sentiment_score": -1.0 到 1.0 之间的浮点数（-1=极度负面，+1=极度正面）,
  "sentiment_confidence": 0.0 到 1.0 之间的浮点数（对判断的确信度）,
  "topic": "主标签（一级分类，如：游戏性/性能/价格/客服/画面/剧情/音效/操作/服务/其他）",
  "sub_topics": ["子标签1", "子标签2", ...] (0~3个),
  "reasoning": "简要分析依据（1-2句话）"
}}

注意事项：
1. 情感判断需结合语气词、情绪词、上下文
2. 主题聚焦该评论的核心讨论对象，而非表面词
3. 中文评论请用中文标签
4. 严格返回 JSON，禁止 Markdown 代码块标记
"""


class LLMSentimentAnalyzer(BaseAnalyzer):
    """基于大模型的情感分析器"""

    name = "llm"

    PROVIDER_CONFIG = {
        "deepseek": {
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url_env": "DEEPSEEK_BASE_URL",
            "default_base_url": "https://api.deepseek.com/v1",
            "model_env": "DEEPSEEK_MODEL",
            "default_model": "deepseek-chat",
        },
        "qwen": {
            "api_key_env": "QWEN_API_KEY",
            "base_url_env": "QWEN_BASE_URL",
            "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model_env": "QWEN_MODEL",
            "default_model": "qwen-turbo",
        },
        "glm": {
            "api_key_env": "GLM_API_KEY",
            "base_url_env": "GLM_BASE_URL",
            "default_base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "model_env": "GLM_MODEL",
            "default_model": "glm-4-flash",
        },
    }

    def __init__(self, provider: str = "deepseek", **kwargs):
        super().__init__(**kwargs)
        if provider not in self.PROVIDER_CONFIG:
            raise ValueError(
                f"不支持的 provider: {provider}，可选：{list(self.PROVIDER_CONFIG.keys())}"
            )

        cfg = self.PROVIDER_CONFIG[provider]
        self.provider = provider
        self.api_key = os.getenv(cfg["api_key_env"])
        self.base_url = os.getenv(cfg["base_url_env"], cfg["default_base_url"])
        self.model = os.getenv(cfg["model_env"], cfg["default_model"])

        if not self.api_key:
            raise ValueError(
                f"未找到 API Key，请在 .env 中配置 {cfg['api_key_env']}"
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def analyze(self, text: str, *, context: dict | None = None) -> AnalysisResult:
        if not text or not text.strip():
            return AnalysisResult(
                sentiment="neutral",
                sentiment_score=0.0,
                sentiment_confidence=0.0,
                topic="其他",
                sub_topics=[],
                reasoning="空文本",
            )

        ctx_str = json.dumps(context, ensure_ascii=False) if context else "无"
        prompt = USER_PROMPT_TEMPLATE.format(text=text[:2000], context=ctx_str)

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,  # 低温度保证结果稳定
                response_format={"type": "json_object"},  # 强制 JSON 输出
                timeout=30,
            )
            content = resp.choices[0].message.content
            return self._parse(content)
        except Exception as e:
            # 失败时返回兜底结果（保证流水线不中断）
            return AnalysisResult(
                sentiment="neutral",
                sentiment_score=0.0,
                sentiment_confidence=0.0,
                topic="其他",
                sub_topics=[],
                reasoning=f"分析失败: {str(e)[:100]}",
                raw={"error": str(e)},
            )

    def _parse(self, content: str) -> AnalysisResult:
        """解析模型返回的 JSON"""
        # 某些模型偶发返回带 Markdown 代码块
        content = re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("`")

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 尝试截取第一个 JSON 对象
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if not m:
                raise
            data = json.loads(m.group(0))

        # 字段归一化
        sentiment = str(data.get("sentiment", "neutral")).lower()
        if sentiment not in {"positive", "negative", "neutral"}:
            sentiment = "neutral"

        return AnalysisResult(
            sentiment=sentiment,
            sentiment_score=float(data.get("sentiment_score", 0.0)),
            sentiment_confidence=float(data.get("sentiment_confidence", 0.5)),
            topic=data.get("topic") or "其他",
            sub_topics=data.get("sub_topics") or [],
            reasoning=data.get("reasoning"),
            raw=data,
        )


if __name__ == "__main__":
    # 冒烟测试（需要配置 API Key）
    import sys
    try:
        analyzer = LLMSentimentAnalyzer(provider="deepseek")
    except ValueError as e:
        print(f"[SKIP] {e}")
        sys.exit(0)

    samples = [
        "这游戏太好玩了，强烈推荐，根本停不下来！",
        "服务器太烂了，天天掉线，体验极差",
        "买了300小时，整体还行，就是后期内容有点单调",
    ]
    for s in samples:
        r = analyzer.analyze(s)
        print(f"\n评论：{s}")
        print(f"  → 情感={r.sentiment} 分数={r.sentiment_score:.2f} 置信度={r.sentiment_confidence:.2f}")
        print(f"  → 主题={r.topic} 子标签={r.sub_topics}")