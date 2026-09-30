"""LLM 批量输出健壮性回归（2026-09-30）：截断 / JSON 残缺不再放大成整批异常

**背景（实测定位）**：DeepSeek 在含 2-3 条百字评论的批次上 `finish_reason=length`
（completion 正好撞死 `MAX_OUTPUT_TOKENS=2500`），JSON 被拦腰截断。而 `_parse_batch`
的贪婪正则兜底分支里**第二次 `json.loads` 没有任何保护** → `JSONDecodeError` 穿透
`analyze_batch`，配合主链路 `raise_on_error=True`（src/pipeline.py）把「一次输出太长」
放大成「整个 target 当天失败」，只能靠次日哨兵补采兜。

本文件锁住修复后的四条行为：

1. `extract_json_object` **永不抛异常**（截断 → None，不再是 JSONDecodeError）；
2. 输出类异常（截断 / 不可解析）→ **自动降批重试**，正常批次仍一次请求打满；
3. 降到单条仍失败才走「抛出 / 失败占位」语义，且失败占位**不经过 `_finalize`**
   —— 否则整条评论会被程序兜底匹配成「1 个观点 + 置信度 0」，形状上不像失败，会被落库；
4. API / 网络类异常**不降批**（拆开照样失败，只会把 1 次失败放大成 N 次请求）。
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------- 夹具 / 工具

@pytest.fixture
def analyzer(monkeypatch):
    """真实 LLMSentimentAnalyzer（不联网：client 会被 _install 换成 Fake）"""
    monkeypatch.setenv("GLM_API_KEY", "sk-fake-for-tests-only")
    from src.analyzers.sentiment_llm import LLMSentimentAnalyzer

    return LLMSentimentAnalyzer(provider="glm-5.3-flash")


def _chunk_size(kwargs: dict) -> int:
    """从**真实 prompt** 里数出本批条数（`[0] ...` `[1] ...` 行），不靠 mock 侧约定"""
    user = kwargs["messages"][-1]["content"]
    return len(re.findall(r"^\[\d+\] ", user, re.M))


def _valid_content(n: int) -> str:
    """产出 n 条合法批量结果（phrase 含"打击感" → 程序匹配层能命中 L3）"""
    return json.dumps(
        {
            "results": [
                {
                    "index": i,
                    "sentiment": "positive",
                    "sentiment_score": 0.8,
                    "opinions": [
                        {
                            "phrase": f"打击感超爽{i}",
                            "sentiment": "positive",
                            "sentiment_score": 0.8,
                            "sentiment_confidence": 0.9,
                            "is_core": True,
                        }
                    ],
                }
                for i in range(n)
            ]
        },
        ensure_ascii=False,
    )


class _FakeClient:
    """把 `chat.completions.create` 换成可编程 handler，并记录每次请求的 kwargs"""

    def __init__(self, handler):
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                content, finish_reason = handler(kwargs)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=content), finish_reason=finish_reason
                        )
                    ],
                    usage=SimpleNamespace(completion_tokens=1234),
                )

        self.chat = SimpleNamespace(completions=_Completions())


def _install(analyzer, handler) -> _FakeClient:
    client = _FakeClient(handler)
    analyzer.client = client  # type: ignore[assignment]
    return client


def _sizes(client: _FakeClient) -> list[int]:
    return [_chunk_size(c) for c in client.calls]


# ---------------------------------------------------------------- 1. JSON 提取

def test_extract_json_object_accepts_plain_fenced_and_wrapped():
    from src.analyzers.sentiment_llm import extract_json_object

    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('好的，结果如下：\n{"a": 1}\n以上') == {"a": 1}


def test_extract_json_object_never_raises():
    """原缺陷：截断内容会走到贪婪正则兜底分支的第二次 json.loads 并抛 JSONDecodeError"""
    from src.analyzers.sentiment_llm import extract_json_object

    assert extract_json_object('{"results": [{"index": 0, "sentiment": "pos') is None
    assert extract_json_object("") is None
    assert extract_json_object(None) is None
    assert extract_json_object("完全不是 JSON") is None
    assert extract_json_object("[1, 2]") is None  # 顶层必须是对象


def test_parse_batch_raises_typed_error_instead_of_jsondecodeerror(analyzer):
    from src.analyzers.sentiment_llm import BatchOutputError, OutputUnparsable

    with pytest.raises(OutputUnparsable) as excinfo:
        analyzer._parse_batch('{"results": [{"index": 0', batch_size=2)
    assert isinstance(excinfo.value, BatchOutputError)
    assert not isinstance(excinfo.value, json.JSONDecodeError)


def test_parse_batch_aligns_results_by_index(analyzer):
    content = json.dumps(
        {"results": [{"index": 0, "sentiment": "positive", "sentiment_score": 0.5, "opinions": []},
                     {"index": 1, "sentiment": "negative", "sentiment_score": -0.5, "opinions": []}]}
    )
    out = analyzer._parse_batch(content, batch_size=2)
    assert [r.sentiment for r in out] == ["positive", "negative"]


def test_missing_index_raises_incomplete_instead_of_empty_result(analyzer):
    """缺 index **不得**静默返回空结果：空结果经兜底匹配会变成「1 观点 + conf=0」的假标注"""
    from src.analyzers.sentiment_llm import OutputIncomplete

    content = json.dumps(
        {"results": [{"index": 1, "sentiment": "negative", "sentiment_score": -0.5, "opinions": []}]}
    )
    with pytest.raises(OutputIncomplete) as excinfo:
        analyzer._parse_batch(content, batch_size=2)
    assert "缺 index" in str(excinfo.value) and "[0]" in str(excinfo.value)


def test_empty_results_array_raises_incomplete(analyzer):
    from src.analyzers.sentiment_llm import OutputIncomplete

    for payload in ('{"results": []}', "{}", '{"results": null}'):
        with pytest.raises(OutputIncomplete):
            analyzer._parse_batch(payload, batch_size=2)



# ---------------------------------------------------------------- 2. 降批重试

def test_truncated_batch_splits_and_annotates_everything(analyzer):
    """主修复：10 条批次被截断 → 自动降批 5+5，10 条全部拿到标注，**不抛异常**"""

    def handler(kwargs):
        n = _chunk_size(kwargs)
        return (_valid_content(n), "length") if n > 5 else (_valid_content(n), "stop")

    client = _install(analyzer, handler)
    texts = [f"打击感不错{i}" for i in range(10)]
    results = analyzer.analyze_batch(texts, batch_size=10, raise_on_error=True)

    assert len(results) == 10
    assert all(r.opinions for r in results), "降批后每条都该拿到观点"
    assert _sizes(client) == [10, 5, 5]


def test_repeated_split_drives_down_to_single_comment(analyzer):
    """连续截断时对半拆到 1 条（MIN_SPLIT_BATCH_SIZE）为止，顺序可预测"""

    def handler(kwargs):
        n = _chunk_size(kwargs)
        return (_valid_content(n), "stop") if n == 1 else ("{", "length")

    client = _install(analyzer, handler)
    results = analyzer.analyze_batch([f"打击感不错{i}" for i in range(8)], batch_size=8,
                                     raise_on_error=True)

    assert all(r.opinions for r in results)
    assert _sizes(client) == [8, 4, 2, 1, 1, 2, 1, 1, 4, 2, 1, 1, 2, 1, 1]


def test_healthy_batch_uses_single_request_per_chunk(analyzer):
    """降批只发生在异常路径：正常批次仍是一次请求打满 batch_size"""
    client = _install(analyzer, lambda kw: (_valid_content(_chunk_size(kw)), "stop"))

    results = analyzer.analyze_batch([f"打击感不错{i}" for i in range(25)], batch_size=10)

    assert len(results) == 25
    assert _sizes(client) == [10, 10, 5]


def test_unparsable_output_also_triggers_split(analyzer):
    """不是只有 finish_reason=length 才降批：JSON 坏（说明文字混入/被污染）同样降批"""

    def handler(kwargs):
        n = _chunk_size(kwargs)
        return ("我觉得无法分析", "stop") if n > 2 else (_valid_content(n), "stop")

    client = _install(analyzer, handler)
    results = analyzer.analyze_batch([f"打击感不错{i}" for i in range(4)], batch_size=4,
                                     raise_on_error=True)

    assert all(r.opinions for r in results)
    assert _sizes(client) == [4, 2, 2]


# ---------------------------------------------------------------- 3. 单条仍失败

def test_single_comment_failure_does_not_become_fabricated_neutral(analyzer):
    """降到 1 条仍截断 → 失败占位（conf=0 且无观点），**不经过 `_finalize`**

    若经过 `_finalize`，「挂壁游戏外挂满天飞」会被整条评论兜底匹配成
    「1 个观点 + 置信度 0」，形状上不像失败 → 会被落库固化成 neutral 假标注。
    """
    from src.pipeline import _is_analysis_failure

    client = _install(analyzer, lambda kw: ('{"results": [', "length"))
    text = "挂壁游戏外挂满天飞，官方根本不管"
    results = analyzer.analyze_batch([text], batch_size=10, raise_on_error=False)

    assert len(client.calls) == 1, "已是单条，不应再往下拆"
    assert len(results) == 1
    assert results[0].opinions == []
    assert results[0].sentiment_confidence == 0.0
    assert _is_analysis_failure(results[0], text) is True, "主链路守卫必须认得出这条是失败"


def test_only_the_bad_comment_is_skipped_when_others_succeed(analyzer):
    """个别条答不动 ≠ 系统性故障：只跳过该条 + 告警，**不得**抛

    若为单条抛错，`run_pipeline` 会让整个 target 每天失败、哨兵每天补采，
    而那条评论模型就是答不动 → 永久卡死（这正是 2026-09-30 收紧 `raise_on_error` 口径的原因）。
    """
    from src.pipeline import _is_analysis_failure

    bad = "无法分析的评论需要跳过"

    def handler(kwargs):
        n = _chunk_size(kwargs)
        user = kwargs["messages"][-1]["content"]
        return ('{"results": []}', "stop") if bad in user else (_valid_content(n), "stop")

    _install(analyzer, handler)
    texts = [f"打击感不错{i}" for i in range(10)]
    texts[3] = bad
    results = analyzer.analyze_batch(texts, batch_size=10, raise_on_error=True)

    assert len(results) == 10
    assert _is_analysis_failure(results[3], texts[3]) is True, "坏条应是失败占位（不落库、下轮重试）"
    assert all(r.opinions for i, r in enumerate(results) if i != 3), "其余 9 条照常产出观点"


def test_single_comment_failure_raises_when_raise_on_error(analyzer):
    """单条调用输出不可用 → 抛（len(texts)==1 时「个别条」与「整批」重合）"""
    from src.analyzers.sentiment_llm import OutputTruncated

    _install(analyzer, lambda kw: ('{"results": [', "length"))
    with pytest.raises(OutputTruncated) as excinfo:
        analyzer.analyze_batch(["外挂满天飞"], batch_size=10, raise_on_error=True)
    assert "max_tokens" in str(excinfo.value), "错误信息要指向真实原因（截断）而非 JSON 坏了"


def test_all_batches_unusable_raises_systemic_error(analyzer):
    """整批一条都拿不到（真·故障夜）→ 仍抛，保住 P1#1 的失败可见性（避免静默零产出）"""

    def handler(kwargs):
        raise RuntimeError("provider 全量故障")

    _install(analyzer, handler)
    with pytest.raises(RuntimeError):
        analyzer.analyze_batch([f"评论{i}" for i in range(20)], batch_size=10, raise_on_error=True)


def test_all_batches_unusable_raises_for_output_errors_too(analyzer):
    """输出类（非 API 类）整批不可用同样要抛：否则那天会「成功但 0 条落库」而无告警"""
    from src.analyzers.sentiment_llm import OutputUnparsable

    _install(analyzer, lambda kw: ("不是 JSON", "stop"))
    with pytest.raises(OutputUnparsable):
        analyzer.analyze_batch([f"评论{i}" for i in range(20)], batch_size=10, raise_on_error=True)



# ---------------------------------------------------------------- 4. API 异常不降批

def test_api_error_is_not_split_when_raising(analyzer):
    from src.analyzers.sentiment_llm import BatchOutputError

    def handler(kwargs):
        raise RuntimeError("401 身份验证失败")

    client = _install(analyzer, handler)
    with pytest.raises(RuntimeError) as excinfo:
        analyzer.analyze_batch([f"评论{i}" for i in range(10)], batch_size=10, raise_on_error=True)

    assert not isinstance(excinfo.value, BatchOutputError)
    assert len(client.calls) == 1, "API 错误拆开照样失败，不该放大成多次请求"


def test_api_error_yields_placeholders_without_split(analyzer):
    """非主链路调用（raise_on_error=False）→ 全批失败占位，且仍只发一次请求"""

    def handler(kwargs):
        raise RuntimeError("429 rate limited")

    client = _install(analyzer, handler)
    results = analyzer.analyze_batch([f"评论{i}" for i in range(10)], batch_size=10)

    assert len(results) == 10
    assert all(r.opinions == [] and r.sentiment_confidence == 0.0 for r in results)
    assert len(client.calls) == 1


# ---------------------------------------------------------------- 5. 输出上限

def test_max_output_tokens_raised_and_passed_through(analyzer):
    """2500 实测被长评批次撞穿（finish_reason=length）；抬到 8000 留 ~3 倍余量"""
    from src.analyzers.sentiment_llm import MAX_OUTPUT_TOKENS

    assert MAX_OUTPUT_TOKENS >= 8000, "不得回退到 2500（实测会截断）"
    client = _install(analyzer, lambda kw: (_valid_content(_chunk_size(kw)), "stop"))
    analyzer.analyze_batch(["打击感不错"] * 10, batch_size=10)
    assert client.calls[0]["max_tokens"] == MAX_OUTPUT_TOKENS
