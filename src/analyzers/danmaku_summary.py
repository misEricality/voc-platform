"""弹幕高光时刻 LLM 总结（2026-09-04 · B站视频看板）

设计：
- **采集时一次性完成**（pipeline 弹幕入库后调 top3 高光桶），结果落
  `bilibili_queue.highlights_json`；页面零延迟、零重复成本，API 进程无需 LLM Key。
- 输入：该桶内弹幕文本（截断防爆）；输出：一段中文总结 —— 概括主要内容 +
  观众倾向与态度（行文规避「情绪」「立场」二词，2026-09-04 工程师要求）。
- Provider 复用 LLMSentimentAnalyzer.PROVIDER_CONFIG（同一套 env：DEEPSEEK_API_KEY 等），
  默认 deepseek；失败抛异常由调用方决定是否阻塞（pipeline 中不阻塞主流程）。
"""
from __future__ import annotations

import os
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"
DANMAKU_PROMPT = PROMPTS_DIR / "danmaku_summary.txt"

# 单次总结的输入上限（字符）：超长截断，防 token 爆炸
MAX_INPUT_CHARS = 3000


def _load_prompt() -> str:
    if not DANMAKU_PROMPT.exists():
        raise FileNotFoundError(f"弹幕总结提示词缺失：{DANMAKU_PROMPT}")
    return DANMAKU_PROMPT.read_text(encoding="utf-8")


def summarize_bucket(texts: list[str], *, provider: str = "deepseek") -> str:
    """总结一个高光桶的弹幕内容

    Args:
        texts: 桶内弹幕文本列表（调用方已按需筛选）
        provider: LLM provider 键（见 LLMSentimentAnalyzer.PROVIDER_CONFIG）

    Returns:
        总结文本（一段中文）
    """
    from openai import OpenAI

    from .sentiment_llm import LLMSentimentAnalyzer

    cfg = LLMSentimentAnalyzer.PROVIDER_CONFIG[provider]
    api_key = os.getenv(cfg["api_key_env"])
    if not api_key:
        raise ValueError(f"未找到 API Key，请在 .env 中配置 {cfg['api_key_env']}")
    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv(cfg["base_url_env"], cfg["default_base_url"]),
    )
    model = os.getenv(cfg["model_env"], cfg["default_model"])

    body = "\n".join(f"- {t}" for t in texts)
    if len(body) > MAX_INPUT_CHARS:
        body = body[:MAX_INPUT_CHARS] + "\n…（已截断）"

    kwargs = {}
    if cfg.get("extra_body"):
        kwargs["extra_body"] = cfg["extra_body"]  # deepseek V4 需显式禁用 thinking
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _load_prompt()},
            {"role": "user", "content": body},
        ],
        temperature=0.5,
        max_tokens=400,
        **kwargs,
    )
    return (resp.choices[0].message.content or "").strip()
