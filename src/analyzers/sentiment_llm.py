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

批次输出健壮性（2026-09-30）：
- 输出上限 MAX_OUTPUT_TOKENS 2500 → 8000（原值对「10 条/批 + 长评论」偏紧，实测截断）；
- 「截断 / JSON 不可解析 / 缺 index」统一抛 BatchOutputError 子类，
  analyze_batch 据此**自动降批重试**（对半拆到 1 条），不再把一次输出问题放大成整个 target 失败；
- JSON 提取收敛到 extract_json_object()，**不会再从解析层抛出**（原贪婪正则兜底
  分支的第二次 json.loads 无保护，是 2026-09-30 定位到的实际故障点）；
- `raise_on_error` 语义收敛为「**系统性**故障才抛」：API/网络/鉴权照旧抛；
  单条输出不可用 → 失败占位 + 告警（跳过该条、下轮重试）；**整批一条都拿不到**
  （真·故障夜）→ 仍抛，保住 P1#1 的失败可见性。

注意：评论级 sentiment_confidence 固定填 0.5 占位；观点级 sentiment_confidence
取自 LLM 输出（缺失时用 |sentiment_score| 兜底）——见 _parse_batch。
"""

from __future__ import annotations

import hashlib
import json
import logging
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

log = logging.getLogger("voc.analyzer.llm")

DEFAULT_BATCH_SIZE = 10
# 输出上限（2026-09-30 上调 2500 → 8000；原 2026-09-08 成本优化值 2500）：
# 2500 对「10 条/批」偏紧 —— 2026-09-30 实测 DeepSeek 在含 2-3 条百字评论的批次上
# finish_reason=length（completion 正好 2500），JSON 被拦腰截断 → 整批解析失败
# → 该 target 当天判失败（靠次日哨兵补采兜）。生产库长度分布下 p90 批次总长 1215 字，
# 已落在观测到的截断区间内，属**会真实发生**的失败。
# 抬高上限本身几乎不增加成本：输出 token 按实际产出计费，正常批次仍只产出 ~1-2k；
# 只有原先「截断 + 次日重试」的批次会一次跑完（反而更省）。
# 两家 provider 实测均接受 ≤32768；8000 ≈ 观测峰值（2500）的 3 倍余量。
MAX_OUTPUT_TOKENS = 8000
# 输出异常（截断 / JSON 不可解析）时的自动降批下限：拆到 1 条仍失败 → 判该条失败。
# 不再往下拆（1 条就是最小可重试单位：单条评论的输出不可能超不过 max_tokens）。
MIN_SPLIT_BATCH_SIZE = 1

# 用于 analyzer_version 溯源的 prompt 集合（任一文件内容改动 → 集合 hash 变 → version 变）。
PROMPT_FILES_FOR_VERSION: tuple[str, ...] = (
    "sentiment.txt",
    "sentiment_user.txt",
    "sentiment_user_strict.txt",
)


def _load_prompt(filename: str) -> str:
    """从 config/prompts/ 加载纯文本 prompt"""
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8").strip()


def compute_prompt_set_hash() -> str:
    """计算「当前 prompt 集合」的内容哈希（前 8 位 hex）。

    用途：analyzer_version 的 @ 后缀。任一 prompt 文件内容改动 → hash 变 →
    下一次打标的 analyzer_version 自动变化 → 存量数据可按此字段识别"用旧 prompt 打标"的样本。

    文件来源：`PROMPT_FILES_FOR_VERSION`（不含话题词表；词表变更由 analyzer 内部重读处理）。
    """
    h = hashlib.sha256()
    for name in PROMPT_FILES_FOR_VERSION:
        path = PROMPTS_DIR / name
        if path.exists():
            h.update(name.encode("utf-8"))
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()[:8]


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


# ============================================================================
# 批次输出异常与 JSON 提取（2026-09-30 修复：截断导致整批异常）
# ============================================================================

class BatchOutputError(RuntimeError):
    """批次级「输出不可用」异常（截断 / JSON 不可解析）。

    与 API / 网络 / 鉴权错误的区别在于**降批即可缓解**（批越小输出越短），
    所以 ``analyze_batch`` 遇到它先自动降批重试，而不是直接把整个 target 判失败。
    """


class OutputTruncated(BatchOutputError):
    """``finish_reason == 'length'``：输出被 ``max_tokens`` 截断，JSON 必然残缺。"""


class OutputUnparsable(BatchOutputError):
    """返回内容里取不出可用 JSON 对象。"""


class OutputIncomplete(BatchOutputError):
    """JSON 合法，但 results 数组缺 index（或为空）—— 模型没按约定回答每一条。

    归到「可降批」一类：批次变小后模型更不容易漏；降批仍漏则按失败占位跳过该条。
    """


_FENCE_RE = re.compile(r"```(?:json)?\s*")


def extract_json_object(content: str | None) -> dict | None:
    """从模型返回文本里尽力取出 JSON 对象；**任何情况下都不抛异常**（取不到返回 None）。

    这是 2026-09-30 修复的核心：原实现对「整段解析失败」会退到贪婪正则 ``\\{.*\\}``
    再 ``json.loads`` 一次，而**第二次解析没有任何保护** —— 输出被 max_tokens 截断时
    这里会抛 ``JSONDecodeError`` 穿透到 ``analyze_batch`` 之外，把一次「输出太长」放大成
    「整个 target 当天失败」。现在统一收敛为返回 None，由调用方转成可降批重试的
    :class:`OutputUnparsable`。

    两级尝试：① 去掉 ```json 围栏后的整段；② 首个 ``{`` 到末个 ``}`` 的切片
    （模型在 JSON 前后加了说明文字时用）。
    """
    if not content:
        return None
    text = _FENCE_RE.sub("", content).strip().rstrip("`").strip()
    candidates = [text]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m and m.group(0) != text:
        candidates.append(m.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


class LLMSentimentAnalyzer(BaseAnalyzer):
    """基于大模型的批量情感分析器（v3）"""

    name = "llm"

    @property
    def analyzer_version(self) -> str:
        """分析溯源标识：'{name}:{model}@{prompt_hash8}'

        落库到 ``comments.analyzer_version``；换 provider / 换模型 / 改 prompt 任何一个
        都会自动产生新 version，旧数据可通过此字段分组识别。
        """
        return f"{self.name}:{self.model}@{self.prompt_hash}"

    PROVIDER_CONFIG = {
        "deepseek": {
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url_env": "DEEPSEEK_BASE_URL",
            "default_base_url": "https://api.deepseek.com/v1",
            "model_env": "DEEPSEEK_MODEL",
            "default_model": "deepseek-flash",
            # V4-Flash 默认 thinking 开启（此时 temperature 无效）→ 标注任务显式禁用，保快+稳
            "extra_body": {"thinking": {"type": "disabled"}},
        },
        "qwen": {
            "api_key_env": "QWEN_API_KEY",
            "base_url_env": "QWEN_BASE_URL",
            # Token Plan 个人版（千问AI平台）：OpenAI 兼容端点
            "default_base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "model_env": "QWEN_MODEL",
            "default_model": "qwen3.7-plus",
        },
        "glm": {
            "api_key_env": "GLM_API_KEY",
            "base_url_env": "GLM_BASE_URL",
            "default_base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "model_env": "GLM_MODEL",
            "default_model": "glm-4-flash",
        },
        # 主标注器：智谱 BigModel GLM-5.3-Flash（VLM 范畴）。2026-09-30 起为默认。
        # 与「glm」provider 共享 Key，但端点/模型独立（GLM_5_3_FLASH_*）。
        # 凭据来源：用户变量「glm_plan」（GLM Coding Plan 套餐 key）→ .env 的 GLM_API_KEY。
        # 文档：https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash
        #      https://docs.bigmodel.cn/cn/api/introduction#python-sdk
        # 注：api_key_env 统一为 GLM_API_KEY（2026-08-31 决策：与 DEEPSEEK/QWEN/STEAM
        #     命名一致；保留独立 GLM_5_3_FLASH_BASE_URL/MODEL 因为端点/模型独立）。
        "glm-5.3-flash": {
            "api_key_env": "GLM_API_KEY",  # 用户变量 glm_plan（Windows 大小写不敏感）
            "base_url_env": "GLM_5_3_FLASH_BASE_URL",
            "default_base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "model_env": "GLM_5_3_FLASH_MODEL",
            "default_model": "glm-5.3-flash",
            # 2026-09-08 成本优化：GLM-5.3-Flash 思考模式不可关闭（thinking.type 仅支持
            # enabled），但支持 reasoning_effort=low —— 实测 reasoning_tokens=0，
            # 输出从「思考+JSON」收敛为纯 JSON，单条耗时 15-20s → ~1.5s
            "extra_body": {"reasoning_effort": "low"},
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
        # provider 级额外请求体（如 deepseek 的 thinking 禁用）
        self.extra_body = cfg.get("extra_body")

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

        # analyzer_version 溯源：缓存 prompt 集合 hash（实例化一次）
        self.prompt_hash: str = compute_prompt_set_hash()

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
        raise_on_error: bool = False,
    ) -> list[AnalysisResult]:
        """批量分析（10 条/批）

        Args:
            texts: 评论列表
            context: 上下文（忽略，批量模式下每条 context 由调用方管理）
            batch_size: 批大小
            strict: True 时用 strict prompt（收敛第 2/3 轮：强制至少 1 条观点）
            raise_on_error: True 时**系统性故障**抛出（调用方决定重试策略）；
                False（默认，兼容）→ 返回失败标记结果（neutral）。
                ⚠️ 失败标记结果若被 update_analysis 落库会固化 analyzed_at，
                下轮跳过不重试。**主链路（src/pipeline.py）自 2026-09-21 起
                已显式传 True**，并对「零置信度且无观点」的占位结果跳过落库，
                双保险防固化（2026-09-08：GLM 400 等错误曾被静默吞掉并固化 neutral）。
                2026-09-30 起「系统性」的口径收紧为三类：
                ① API / 网络 / 鉴权异常（原语义，不降批）；
                ② 整批一条都拿不到结果（`ok_total == 0`，真·故障夜）；
                ③ 单条调用（len(texts)==1）输出不可用。
                **个别条答不动**（其余条正常）→ 仅该条失败占位 + 告警，不抛 ——
                否则一条模型答不动的评论会让 target 每天失败、哨兵每天补采而永久卡死。

        Returns:
            list[AnalysisResult]，长度 == len(texts)。
            单条输出不可用时该位置是失败占位（conf=0 且无观点），由调用方决定跳过。
        """
        results: list[AnalysisResult] = [self._empty_result() for _ in texts]

        ok_total = 0
        last_error: Exception | None = None
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            ok, err = self._analyze_chunk(
                chunk, results, start, strict=strict, raise_on_error=raise_on_error
            )
            ok_total += ok
            last_error = err or last_error

        # 系统性输出故障才抛（2026-09-30 决策）：**整批一条都拿不到**（例如 provider 开始
        # 对所有请求返回不可用内容）→ 与 API 故障同级，抛出去让 target 判失败、次日补采，
        # 保住 P1#1 的「故障夜必须可见」。而**个别条**答不动只是跳过该条 + 告警计数 ——
        # 若为单条抛错，`run_pipeline` 会让整个 target 每天失败、哨兵每天补采，
        # 而那条评论模型就是答不动 → 永久卡死。
        if raise_on_error and texts and ok_total == 0:
            raise last_error or BatchOutputError(f"整批输出不可用（{len(texts)} 条全部未取到结果）")

        return results

    def _analyze_chunk(
        self,
        chunk: list[str],
        results: list[AnalysisResult],
        start: int,
        *,
        strict: bool,
        raise_on_error: bool,
    ) -> tuple[int, Exception | None]:
        """处理单个批次并写入 ``results[start:start+len(chunk)]``。

        **降批重试（2026-09-30）**：输出异常（截断 / JSON 不可解析 / 缺 index）时把批次
        对半拆开各自重试，直到 :data:`MIN_SPLIT_BATCH_SIZE`。「输出太长写坏了 JSON」这个问题
        本身就随批次变小而消失 —— 截断是不可重试的（重跑同样撞上限），但**降批是可解的**，
        所以这里不去猜「该调多大 max_tokens」，而是让失败的那一批自己变小。

        降批只发生在异常路径：正常批次仍是一次请求打满 ``batch_size``。
        API / 网络 / 鉴权等异常**不降批**（拆开照样失败，只会把一次失败放大成 N 次请求）。

        Returns:
            ``(成功解析的条数, 最后一个输出类异常)`` —— 供 :meth:`analyze_batch`
            判断是否属于「整批都拿不到结果」的系统性故障。
        """
        try:
            parsed = self._request_batch(chunk, strict=strict)
        except BatchOutputError as e:
            if len(chunk) > MIN_SPLIT_BATCH_SIZE:
                mid = len(chunk) // 2
                log.warning(
                    "批次输出异常（%s），降批重试 %d → %d + %d", e, len(chunk), mid, len(chunk) - mid
                )
                ok_l, err_l = self._analyze_chunk(
                    chunk[:mid], results, start, strict=strict, raise_on_error=raise_on_error
                )
                ok_r, err_r = self._analyze_chunk(
                    chunk[mid:], results, start + mid, strict=strict, raise_on_error=raise_on_error
                )
                return ok_l + ok_r, err_l or err_r
            # 单条仍答不动 → 失败占位（不抛：见 analyze_batch 末尾说明）。占位形状与
            # pipeline._is_analysis_failure 判据一致 → 跳过落库 + 告警计数 + 下轮重试。
            log.warning("单条输出不可用（%s），跳过该条（保持未分析）", e)
            results[start] = self._failure_result(e)
            return 0, e
        except Exception as e:  # noqa: BLE001 — API / 网络 / 鉴权：保持既有语义，不降批
            if raise_on_error:
                raise
            for i in range(len(chunk)):
                results[start + i] = self._failure_result(e)
            return 0, e

        # 合并批次结果（含映射 + core 判定 + 空观点程序兜底）
        for local_idx, r in enumerate(parsed):
            results[start + local_idx] = self._finalize(r, text=chunk[local_idx])
        return len(parsed), None

    def _request_batch(self, chunk: list[str], *, strict: bool) -> list[AnalysisResult]:
        """发一次请求 + 解析；输出不可用时抛 :class:`BatchOutputError`（可降批缓解）。"""
        prompt = build_batch_user_prompt(chunk, strict=strict)
        kwargs: dict = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            max_tokens=MAX_OUTPUT_TOKENS,
            timeout=60,
        )
        # provider 级额外参数（如 deepseek 禁用 thinking，使 temperature 生效）
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        # 截断必须显式判掉：此时 JSON 一定残缺，直接解析只会得到「JSON 坏了」这种
        # 误导性错误（真实原因是输出不够长），分不清就无从降批。
        if getattr(choice, "finish_reason", None) == "length":
            raise OutputTruncated(
                f"输出被 max_tokens={MAX_OUTPUT_TOKENS} 截断（finish_reason=length），"
                f"completion_tokens={getattr(getattr(resp, 'usage', None), 'completion_tokens', '?')}"
            )
        return self._parse_batch(choice.message.content, batch_size=len(chunk))

    @staticmethod
    def _failure_result(error: Exception) -> AnalysisResult:
        """失败占位：零置信度 + 无观点。

        形状与 ``pipeline._is_analysis_failure`` 的判据一致 →
        ``analyzed_at`` 留空、下轮重试，不会被固化成 neutral 假标注。
        """
        return AnalysisResult(
            sentiment="neutral",
            sentiment_score=0.0,
            sentiment_confidence=0.0,
            opinions=[],
            reasoning=f"批量分析失败: {str(error)[:100]}",
            raw={"error": str(error)},
        )

    def _empty_result(self) -> AnalysisResult:
        return AnalysisResult(
            sentiment="neutral",
            sentiment_score=0.0,
            sentiment_confidence=0.0,
            opinions=[],
            reasoning="未返回",
        )

    def _parse_batch(self, content: str | None, *, batch_size: int) -> list[AnalysisResult]:
        """解析批量 JSON（results 数组，按 index 对齐）

        Returns:
            list[AnalysisResult]，长度 == batch_size

        Raises:
            OutputUnparsable: 取不出 JSON 对象（可降批重试缓解）。
            OutputIncomplete: JSON 合法但 results 缺 index / 为空数组（可降批重试缓解）。
                **两条都刻意不再「返回整批空结果」** —— 静默空结果经 `_finalize` 的
                整条评论兜底匹配后，会变成「1 个观点 + 置信度 0」的**假标注**：形状上
                不像失败，`pipeline._is_analysis_failure` 放行 → 固化落库（2026-09-30 收口）。
        """
        data = extract_json_object(content)
        if data is None:
            raise OutputUnparsable(f"返回内容无法解析为 JSON（前 200 字符: {(content or '')[:200]!r}）")

        raw_results = data.get("results")
        if not isinstance(raw_results, list) or not raw_results:
            raise OutputIncomplete(
                f"results 数组缺失或为空（batch_size={batch_size}，"
                f"实际 type={type(raw_results).__name__}）"
            )

        bucket: dict[int, dict] = {}
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < batch_size:
                bucket[idx] = item

        missing = [i for i in range(batch_size) if i not in bucket]
        if missing:
            raise OutputIncomplete(f"results 缺 index {missing}（batch_size={batch_size}）")

        out: list[AnalysisResult] = []
        for i in range(batch_size):
            item = bucket[i]  # 上面保证 index 齐全（缺任何一个都已在 OutputIncomplete 处抛出）

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
            FALLBACK_L3,
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

        # 3. topic = 具体维度的 L1（兜底治理：topic 应回答"谈什么"）
        #    若 core 是兜底/元表达（总体体验评价/网络梗/推荐度等），但评论里另有具体维度观点，
        #    则改用第一个具体观点作为 topic 锚点；整体情感仍取 core（整体褒贬）不变。
        topic_op = core
        if core.l3 in FALLBACK_L3:
            specific = [op for op in valid_opinions if op.l3 not in FALLBACK_L3]
            if specific:
                topic_op = specific[0]
        if topic_op.full_path:
            topic = topic_op.full_path.split("/")[0]
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