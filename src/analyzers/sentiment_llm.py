"""基于大模型的情感与主题分析器（v4 · 批量 · 方案 4：观点短语 → 程序匹配）

兼容 OpenAI 协议的 API（DeepSeek / Qwen / GLM 全部支持）。
通过环境变量配置 provider 与 key，可热切换。

核心设计（2026-08-06 v4）：
- 批量打标：每批 10 条评论，1 次请求（无词表注入，prompt 精简）
- LLM 自由提取观点短语（phrase），不直接选标签（提升召回率）
- 程序用「标签定义词典」匹配 phrase → L3 → 映射 full_path
- 每条观点带 sentiment（观点情感）+ sentiment_score + is_core
- topic = 程序从核心观点（is_core）映射 L1；整体情感 = 核心观点的情感

业务配置（prompt 模板、主题词表、标签定义）从 config/ 目录加载：
- config/prompts/sentiment.txt              — 系统提示词
- config/prompts/sentiment_user.txt         — 用户提示词模板（批量版，含 {batch_size} {batch_texts}）
- config/prompts/sentiment_user_strict.txt  — 收敛第 2/3 轮用（强制至少 1 条观点）
- config/topics/gaming.yaml                 — 三级标签体系
- config/topics/l3_definitions.yaml         — L3 标签定义词典（程序匹配层用）

注意：当前 LLM 输出不含 sentiment_confidence，解析层硬编码 0.5 占位（见 _parse_batch）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from openai import OpenAI

from .base import BaseAnalyzer, AnalysisResult, Opinion

# 项目根目录（src/analyzers/sentiment_llm.py → ../../../）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"
TOPICS_DIR = PROJECT_ROOT / "config" / "topics"

DEFAULT_BATCH_SIZE = 10


def _load_prompt(filename: str) -> str:
    """从 config/prompts/ 加载纯文本 prompt"""
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8").strip()


def _load_topic_config(category: str = "gaming") -> dict:
    """从 config/topics/{category}.yaml 加载主题词表"""
    path = TOPICS_DIR / f"{category}.yaml"
    if not path.exists():
        return {"primary": [], "fallback": "其他", "hierarchy": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_batch_user_prompt(
    texts: list[str],
    topic_l3_full: str | None = None,
    strict: bool = False,
) -> str:
    """构造批量用户提示词（方案4：无词表注入，LLM 自由提取观点短语）

    Args:
        texts: 批内评论（index 从 0 开始）
        topic_l3_full: 已弃用（方案4 不需要词表注入）
        strict: True 时用 strict prompt（第二轮起：强制至少 1 条观点）
    """
    batch_texts = "\n".join(
        f"[{i}] {t[:500]}" for i, t in enumerate(texts)
    )
    prompt_file = "sentiment_user_strict.txt" if strict else "sentiment_user.txt"
    return _load_prompt(prompt_file).format(
        batch_size=len(texts),
        batch_texts=batch_texts,
        topic_l3_full="",  # 占位（prompt 中已无此占位符则忽略）
    )


class LLMSentimentAnalyzer(BaseAnalyzer):
    """基于大模型的批量情感分析器（v3）"""

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
        self.topic_hierarchy: dict = topic_cfg.get("hierarchy", {})
        self.system_prompt: str = _load_prompt("sentiment.txt")

        # L3 映射表（方案4：程序匹配 + 路径映射）
        from .normalize import build_l3_mapping
        self.l3_mapping: dict[str, tuple[str, str]] = build_l3_mapping(self.topic_hierarchy)

    # === 单条（兼容旧接口，内部转批量） ===

    def analyze(self, text: str, *, context: dict | None = None) -> AnalysisResult:
        results = self.analyze_batch([text])
        return results[0] if results else self._empty_result()

    # === 批量打标（核心） ===

    def analyze_batch(
        self,
        texts: list[str],
        *,
        context: dict | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        strict: bool = False,
    ) -> list[AnalysisResult]:
        """批量分析（10 条/批）

        Args:
            texts: 评论列表
            context: 上下文（忽略，批量模式下每条 context 由调用方管理）
            batch_size: 批大小
            strict: True 时用 strict prompt（收敛第 2/3 轮：强制至少 1 条观点）

        Returns:
            list[AnalysisResult]（长度 == len(texts)，缺失的评论返回空结果）
        """
        results: list[AnalysisResult] = [self._empty_result() for _ in texts]

        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            prompt = build_batch_user_prompt(chunk, strict=strict)
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    timeout=60,
                )
                content = resp.choices[0].message.content
                parsed = self._parse_batch(content, batch_size=len(chunk))
            except Exception as e:
                # 整个批次失败 → 该批所有评论标记失败（进下一轮）
                for i in range(len(chunk)):
                    results[start + i] = AnalysisResult(
                        sentiment="neutral",
                        sentiment_score=0.0,
                        sentiment_confidence=0.0,
                        opinions=[],
                        reasoning=f"批量分析失败: {str(e)[:100]}",
                        raw={"error": str(e)},
                    )
                continue

            # 合并批次结果（含映射 + core 判定 + 空观点程序兜底）
            for local_idx, r in enumerate(parsed):
                results[start + local_idx] = self._finalize(r, text=chunk[local_idx])

        return results

    def _empty_result(self) -> AnalysisResult:
        return AnalysisResult(
            sentiment="neutral",
            sentiment_score=0.0,
            sentiment_confidence=0.0,
            opinions=[],
            reasoning="未返回",
        )

    def _parse_batch(self, content: str, *, batch_size: int) -> list[AnalysisResult]:
        """解析批量 JSON（results 数组，按 index 对齐）

        Returns:
            list[AnalysisResult]，长度 == batch_size
            缺失 index 的评论返回空结果
        """
        content = re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("`")
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if not m:
                return [self._empty_result() for _ in range(batch_size)]
            data = json.loads(m.group(0))

        raw_results = data.get("results") or []
        bucket: dict[int, dict] = {}
        if isinstance(raw_results, list):
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index")
                if isinstance(idx, int) and 0 <= idx < batch_size:
                    bucket[idx] = item

        out: list[AnalysisResult] = []
        for i in range(batch_size):
            item = bucket.get(i)
            if not item:
                out.append(self._empty_result())
                continue

            # 方案4：LLM 输出 opinions（phrase + sentiment + score + is_core）+ 评论级 sentiment
            comments_sentiment = str(item.get("sentiment", "neutral")).lower()
            if comments_sentiment not in {"positive", "negative", "neutral"}:
                comments_sentiment = "neutral"
            try:
                comments_score = float(item.get("sentiment_score", 0.0))
            except (TypeError, ValueError):
                comments_score = 0.0
            comments_score = max(-1.0, min(1.0, comments_score))

            opinions: list[Opinion] = []
            raw_opinions = item.get("opinions") or []
            if isinstance(raw_opinions, list):
                for op in raw_opinions:
                    if not isinstance(op, dict):
                        continue
                    phrase = str(op.get("phrase", "")).strip()
                    op_sent = str(op.get("sentiment", "neutral")).strip().lower()
                    if op_sent not in {"positive", "negative", "neutral"}:
                        op_sent = "neutral"
                    if not phrase:
                        continue
                    try:
                        op_score = float(op.get("sentiment_score", 0.0))
                    except (TypeError, ValueError):
                        op_score = 0.0
                    op_score = max(-1.0, min(1.0, op_score))
                    # 方案B：opinion 级置信度（LLM 未输出时用 |score| 兜底——情感越强越确信）
                    try:
                        op_conf = float(op.get("sentiment_confidence", 0.0))
                    except (TypeError, ValueError):
                        op_conf = 0.0
                    if not 0.0 <= op_conf <= 1.0 or op_conf == 0.0:
                        op_conf = min(1.0, abs(op_score))
                    is_core = bool(op.get("is_core", False))
                    opinions.append(Opinion(
                        phrase=phrase,
                        sentiment=op_sent,
                        sentiment_score=op_score,
                        sentiment_confidence=op_conf,
                        is_core=is_core,
                    ))

            out.append(AnalysisResult(
                sentiment=comments_sentiment,  # 评论级情感（供程序兜底用）
                sentiment_score=comments_score,
                sentiment_confidence=0.5,
                opinions=opinions,
                reasoning=item.get("reasoning"),
                raw=item,
            ))
        return out

    def _finalize(self, r: AnalysisResult, *, text: str | None = None) -> AnalysisResult:
        """落盘前加工（方案4）：程序匹配 l3 + 映射 full_path + core 判定 topic

        - phrase → 程序匹配 l3（定义词典）；匹配不到 → 该观点丢弃
        - LLM 未返回可匹配观点 → 用整条评论兜底匹配（短评场景）
        - is_core 无 true → 默认第 1 个合法 opinion 为 core；多 true → 取第 1 个
        - topic = core opinion 映射的 L1（Q1-B 方案）
        - 整体情感/score = core opinion 的观点情感/分数
        - 若 opinions 全未匹配/为空 → topic 用 fallback（Q3-B：sentiment 保留）
        """
        from .normalize import (
            build_keyword_index,
            load_definitions,
            match_l3,
            map_l3_to_path,
            normalize_opinions_v4,
        )

        defs = load_definitions()
        kw_idx = build_keyword_index(defs)

        # 1. 程序匹配 phrase → l3 → full_path（匹配不到的 l3=None）
        op_dicts = [op.to_dict() for op in r.opinions]
        matched = normalize_opinions_v4(op_dicts, kw_idx, defs, self.l3_mapping)

        valid_opinions: list[Opinion] = []
        for d in matched:
            if not d.get("l3"):
                continue  # 未匹配 → 丢弃（观点留空）
            valid_opinions.append(Opinion(
                phrase=d["phrase"],
                sentiment=d.get("sentiment", "neutral"),
                sentiment_score=d.get("sentiment_score", 0.0),
                sentiment_confidence=d.get("sentiment_confidence", 0.5),
                is_core=d.get("is_core", False),
                l3=d["l3"],
                full_path=d.get("full_path"),
            ))

        if not valid_opinions:
            # LLM 未返回可匹配观点 → 程序兜底：用整条评论文本匹配 l3
            # 短评（如"挂壁游戏"）LLM 可能漏提，但程序能匹配到"外挂"
            from .normalize import match_l3
            whole_l3 = match_l3(text, kw_idx, defs)
            if whole_l3:
                path = map_l3_to_path(whole_l3, self.l3_mapping)
                if path:
                    valid_opinions.append(Opinion(
                        phrase=text[:60],
                        sentiment=r.sentiment,  # 用评论级情感
                        sentiment_score=r.sentiment_score,
                        sentiment_confidence=min(1.0, abs(r.sentiment_score)),  # 程序兜底：|score| 代理
                        is_core=True,
                        l3=whole_l3,
                        full_path=path,
                    ))

        if not valid_opinions:
            # 仍无合法观点 → 留空（真无内容：乱码/时间纪念等）
            return AnalysisResult(
                sentiment=r.sentiment,
                sentiment_score=r.sentiment_score,
                sentiment_confidence=0.5,
                topic=self.topic_fallback,
                opinions=[],
                reasoning=r.reasoning,
                raw=r.raw,
            )

        # 2. core 判定（无 true 取第 1 个；多 true 取第 1 个）
        core_ops = [op for op in valid_opinions if op.is_core]
        core = core_ops[0] if core_ops else valid_opinions[0]
        for op in valid_opinions:
            if op is not core:
                op.is_core = False

        # 3. topic = core 的 L1
        if core.full_path:
            topic = core.full_path.split("/")[0]
        else:
            topic = self.topic_fallback
        if topic not in self.topic_primary:
            topic = self.topic_fallback

        # 4. 整体情感 = core 观点情感（方案4）；整体置信度 = core 观点置信度（方案B）
        return AnalysisResult(
            sentiment=core.sentiment,
            sentiment_score=core.sentiment_score,
            sentiment_confidence=core.sentiment_confidence,
            topic=topic,
            opinions=valid_opinions,
            reasoning=r.reasoning,
            raw=r.raw,
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
    results = analyzer.analyze_batch(samples, batch_size=10)
    for s, r in zip(samples, results):
        print(f"\n评论：{s}")
        print(f"  → 整体情感={r.sentiment} 分数={r.sentiment_score:.2f} topic={r.topic}")
        for op in r.opinions:
            print(f"  → [{op.sentiment}] {op.full_path} (core={op.is_core}) \"{op.phrase}\"")