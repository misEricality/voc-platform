"""4 个 Agent tool 的真实实现（2026-09-09 · 见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §4）

每个 tool 接受 LLM 通过 function calling 传参的 dict + 显式 session 参数，
返回紧凑 JSON 字符串（不返回巨大数据）。

- query_overview(target, start?, end?, grain?)：单目标 KPI → service.overview_payload
- query_topics(target, level, sentiment?, start?, end?, top_n?)：主题分布 → service.topics_payload + top_n
- query_comments(target, sentiment?, topic?, start?, end?, limit?)：评论明细 → service.comments_payload
- search_docs(query, top_k?)：项目文档 FAQ → md_corpus.search

`run_tool(name, args, session)` 是统一入口（chat.py 用），内部按 name 分发到具体函数。
具体函数（query_overview 等）保持纯函数签名，便于单测。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

log = logging.getLogger("voc.agent.tools")


# ---------- 序列化辅助 ----------

def _safe_json(obj: Any, *, max_len: int = 8000) -> str:
    """对象 → JSON 字符串（截断到 max_len 防爆 LLM token 窗）"""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = json.dumps({"_unserializable": str(obj)}, ensure_ascii=False)
    if len(s) > max_len:
        s = s[:max_len] + "...[truncated]"
    return s


# ---------- 4 个 tool 的真实实现 ----------


def query_overview(
    session: Session,
    *,
    target: str,
    start: str | None = None,
    end: str | None = None,
    grain: str = "comment",
) -> str:
    """单目标 KPI（接 service.overview_payload）

    返回字段：total / analyzed / sentiment{...} / avg_score / recommend_rate /
             first_posted / last_posted / window。
    """
    from src.api.service import overview_payload
    if grain not in {"comment", "opinion"}:
        return _safe_json({"error": f"grain 仅支持 comment/opinion，收到：{grain}"})

    data = overview_payload(session, target, start=start, end=end, grain=grain)
    if data is None:
        return _safe_json({"target_id": target, "error": "该目标无评论数据"})
    return _safe_json(data)


def query_topics(
    session: Session,
    *,
    target: str,
    level: str = "L1",
    sentiment: str | None = None,
    start: str | None = None,
    end: str | None = None,
    top_n: int = 10,
) -> str:
    """主题分布（接 service.topics_payload，按 top_n 截断）

    返回：[{topic, total, positive, negative, neutral, negative_pct}, ...]
    """
    from src.api.service import topics_payload
    if level not in {"L1", "L2", "L3"}:
        return _safe_json({"error": f"level 仅支持 L1/L2/L3，收到：{level}"})
    if sentiment and sentiment not in {"positive", "negative", "neutral"}:
        return _safe_json({"error": f"sentiment 仅支持 positive/negative/neutral，收到：{sentiment}"})
    top_n = max(1, min(int(top_n or 10), 50))  # 封顶 50

    try:
        rows = topics_payload(
            session, target, level,
            sentiment=sentiment, start=start, end=end, full=False,
        )
    except ValueError as e:
        return _safe_json({"error": str(e)})

    return _safe_json({
        "target_id": target,
        "level": level,
        "sentiment_filter": sentiment,
        "topics": rows[:top_n],
        "total_topics": len(rows),
    })


def query_comments(
    session: Session,
    *,
    target: str,
    sentiment: str | None = None,
    topic: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
    topic_match_mode: str = "auto",
) -> str:
    """评论明细（接 service.comments_payload，分页取首页 + 截断）

    topic 过滤走 opinion grain：
    - topic_match_mode="auto"（默认）：topic 可传 L1/L2/L3 任一段名字，自动按 full_path
      任意段匹配——例如 query_topics L3 拿到的"战斗系统"也能查到对应评论
    - topic_match_mode="prefix"：仅按 full_path 前缀匹配（只对 L1 名字有效，向后兼容）

    返回：{total, items: [{id, posted_at, sentiment, rating, content, opinions}, ...]}
    items 内 content 截到 280 字（防 tool_result 爆 token）
    """
    from src.api.service import comments_payload
    if sentiment and sentiment not in {"positive", "negative", "neutral"}:
        return _safe_json({"error": f"sentiment 仅支持 positive/negative/neutral，收到：{sentiment}"})
    if topic_match_mode not in {"prefix", "auto"}:
        return _safe_json({"error": f"topic_match_mode 仅支持 prefix/auto，收到：{topic_match_mode}"})
    limit = max(1, min(int(limit or 20), 50))  # 封顶 50

    data = comments_payload(
        session,
        target_id=target,
        page=1, page_size=limit,
        sentiment=sentiment, topic=topic,
        start=start, end=end,
        grain="opinion",
        topic_match_mode=topic_match_mode,
    )
    # 截断 content（每条 ≤ 280 字）
    items = []
    for it in data["items"]:
        it2 = dict(it)
        c = it2.get("content") or ""
        if len(c) > 280:
            it2["content"] = c[:280] + "…"
        items.append(it2)
    return _safe_json({
        "target_id": target,
        "sentiment_filter": sentiment,
        "topic_filter": topic,
        "total": data["total"],
        "returned": len(items),
        "items": items,
    })


def search_docs(*, query: str, top_k: int = 3) -> str:
    """项目文档检索（接 md_corpus.search）

    返回：[{path, heading, snippet, matched_terms, score}, ...]
    """
    from src.agent.md_corpus import search as corpus_search
    top_k = max(1, min(int(top_k or 3), 10))
    hits = corpus_search(query, top_k=top_k)
    if not hits:
        return _safe_json({
            "query": query,
            "hits": [],
            "note": "未找到相关章节；尝试换个关键词",
        })
    return _safe_json({
        "query": query,
        "hits": hits,
    })


# ---------- 统一分发（chat.py 用） ----------

TOOL_DISPATCH = {
    "query_overview": query_overview,
    "query_topics": query_topics,
    "query_comments": query_comments,
    "search_docs": search_docs,
}


def run_tool(name: str, args: dict, *, session: Session) -> str:
    """按 name 执行 tool，返回 JSON 字符串（统一错误处理）

    LLM 通过 function calling 传的 args 是 dict；这里按各 tool 的形参解包。
    未知名 name / 缺 target 等必填参数 → 返回 error JSON（不让 LLM 跑飞）。
    """
    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return _safe_json({"error": f"未知 tool: {name}"})

    # 校验必填字段
    if name in {"query_overview", "query_topics", "query_comments"} and not args.get("target"):
        return _safe_json({"error": "缺少必填参数 target（格式: steam:2358720 或 bilibili:BVxxx）"})
    if name == "query_topics" and not args.get("level"):
        return _safe_json({"error": "缺少必填参数 level（L1/L2/L3）"})
    if name == "search_docs" and not args.get("query"):
        return _safe_json({"error": "缺少必填参数 query"})

    try:
        # search_docs 不需要 session，其他 tool 都需要
        if name == "search_docs":
            return fn(**args)
        return fn(session=session, **args)
    except Exception as e:
        log.exception(f"tool {name} failed")
        return _safe_json({"error": f"tool {name} 执行失败: {e}"})


# ---------- OpenAI function calling schema（前端展示 + LLM 接收）----------
#
# 2026-09-09 §3 红线修复：4 个 tool 的 schema 抽到 config/agent/tools.yaml，
# 此处只负责加载（lru_cache + fail-fast）。工程师迭代 description 时改 yaml
# + 重启服务即可，不再改 Python 代码。

_TOOLS_YAML = Path(__file__).resolve().parents[2] / "config" / "agent" / "tools.yaml"


@lru_cache(maxsize=1)
def _load_tool_schemas() -> list[dict]:
    """加载 config/agent/tools.yaml → list of OpenAI tool schema（lru_cache 缓存）

    行为契约：
    - 文件不存在或解析失败 → 启动期 RuntimeError（fail-fast；不要静默缺 tool）
    - schema 字段 type/function/name/parameters 必须齐全；缺任一 → 拒绝
    - 顺序：保留 yaml 中定义顺序（chat.py 注入 system prompt 时按序展示）
    """
    if not _TOOLS_YAML.exists():
        raise RuntimeError(
            f"agent tools.yaml missing: {_TOOLS_YAML}"
            " —— 4 个 tool schema 应在 config/agent/tools.yaml，"
            "从 src/agent/tools.py 硬编码抽离（2026-09-09 §3 红线修复）"
        )
    try:
        items = yaml.safe_load(_TOOLS_YAML.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        raise RuntimeError(f"agent tools.yaml 解析失败: {e}") from e
    if not isinstance(items, list):
        raise RuntimeError(f"agent tools.yaml 顶层必须是 list，实际：{type(items).__name__}")
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise RuntimeError(f"tools.yaml[{i}] 不是 dict: {item!r}")
        if item.get("type") != "function":
            raise RuntimeError(f"tools.yaml[{i}].type 必须为 'function'，实际：{item.get('type')!r}")
        fn = item.get("function") or {}
        for k in ("name", "description", "parameters"):
            if k not in fn:
                raise RuntimeError(f"tools.yaml[{i}].function 缺字段 '{k}'")
    log.info(f"tool schemas loaded: {len(items)} from {_TOOLS_YAML}")
    return items


# 兼容旧名（chat.py / tests 直接 import TOOL_SCHEMAS）
TOOL_SCHEMAS: list[dict] = _load_tool_schemas()


def reload_tool_schemas() -> None:
    """手动清掉 lru_cache；修改 tools.yaml 后 + 重启进程生效（脚本里调）"""
    _load_tool_schemas.cache_clear()
    global TOOL_SCHEMAS
    TOOL_SCHEMAS = _load_tool_schemas()
