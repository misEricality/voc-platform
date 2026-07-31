"""基于大模型的情感与主题分析器

兼容 OpenAI 协议的 API（DeepSeek / Qwen / GLM 全部支持）。
通过环境变量配置 provider 与 key，可热切换。

业务配置（prompt 模板、主题词表）从 config/ 目录加载：
- config/prompts/sentiment.txt        — 系统提示词
- config/prompts/sentiment_user.txt   — 用户提示词模板（含 {text} {context} 占位符）
- config/topics/gaming.yaml           — 主题词表（key: primary/fallback）
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from openai import OpenAI

from .base import BaseAnalyzer, AnalysisResult


# 项目根目录（src/analyzers/sentiment_llm.py → ../../../）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"
TOPICS_DIR = PROJECT_ROOT / "config" / "topics"


def _load_prompt(filename: str) -> str:
    """从 config/prompts/ 加载纯文本 prompt"""
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8").strip()


def _load_topic_config(category: str = "gaming") -> dict:
    """从 config/topics/{category}.yaml 加载主题词表

    Returns:
        {"primary": [...], "fallback": "其他"}
    """
    path = TOPICS_DIR / f"{category}.yaml"
    if not path.exists():
        return {"primary": [], "fallback": "其他"}
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_user_prompt(text: str, context: dict | None, topic_primary: list[str]) -> str:
    """构造用户提示词，主题词表动态注入"""
    primary_str = "/".join(topic_primary) if topic_primary else "其他"
    return _load_prompt("sentiment_user.txt").format(
        text=text[:2000],
        context=json.dumps(context, ensure_ascii=False) if context else "无",
    ).replace(
        # 提示词中"游戏性/性能/价格/..."的占位列表会被主题词表覆盖
        "游戏性/性能/价格/客服/画面/剧情/音效/操作/服务/其他",
        primary_str,
    )


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

    def __init__(self, provider: str = "deepseek", topic_category: str = "gaming", **kwargs):
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

        # 加载业务配置
        topic_cfg = _load_topic_config(topic_category)
        self.topic_primary: list[str] = topic_cfg.get("primary", [])
        self.topic_fallback: str = topic_cfg.get("fallback", "其他")
        self.system_prompt: str = _load_prompt("sentiment.txt")

    def analyze(self, text: str, *, context: dict | None = None) -> AnalysisResult:
        if not text or not text.strip():
            return AnalysisResult(
                sentiment="neutral",
                sentiment_score=0.0,
                sentiment_confidence=0.0,
                topic=self.topic_fallback,
                sub_topics=[],
                reasoning="空文本",
            )

        user_prompt = build_user_prompt(text, context, self.topic_primary)

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=30,
            )
            content = resp.choices[0].message.content
            return self._parse(content)
        except Exception as e:
            return AnalysisResult(
                sentiment="neutral",
                sentiment_score=0.0,
                sentiment_confidence=0.0,
                topic=self.topic_fallback,
                sub_topics=[],
                reasoning=f"分析失败: {str(e)[:100]}",
                raw={"error": str(e)},
            )

    def _parse(self, content: str) -> AnalysisResult:
        """解析模型返回的 JSON"""
        content = re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("`")

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if not m:
                raise
            data = json.loads(m.group(0))

        sentiment = str(data.get("sentiment", "neutral")).lower()
        if sentiment not in {"positive", "negative", "neutral"}:
            sentiment = "neutral"

        return AnalysisResult(
            sentiment=sentiment,
            sentiment_score=float(data.get("sentiment_score", 0.0)),
            sentiment_confidence=float(data.get("sentiment_confidence", 0.5)),
            topic=data.get("topic") or self.topic_fallback,
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