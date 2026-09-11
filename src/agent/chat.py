"""原声分析 Agent 的 chat 编排（2026-09-09 · 见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §4）

实现：
- tool_call 循环（最多 MAX_ROUNDS 轮防死循环）
- skill 自动注入（关键词 / tool_name 命中即追加到 system prompt）
- assistant + tool 消息落库（让用户刷新后能看到完整对话）
- 所有 DB 操作复用调用方传入的 session（请求级）

SSE 事件协议（前端按 event 字段处理）：
  - event: token       data: {"delta": "..."}                  # 流式 token
  - event: tool_start  data: {"id": "...", "name": "...", "args": {...}}  # 开始调用
  - event: tool_end    data: {"id": "...", "result_preview": "..."}      # 工具返回
  - event: done        data: {"rounds": N, "full_text": "..."}  # 完成
  - event: error       data: {"message": "..."}                # 错误
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

from sqlalchemy.orm import Session

from src.agent.tools import TOOL_SCHEMAS, run_tool

log = logging.getLogger("voc.agent.chat")

MAX_TOOL_ROUNDS = 5  # tool_call 循环上限（含中间有 tool 的轮次），防死循环/费用爆炸

# ---------- 并发闸 + 单轮输出上限（P0-4 · 2026-09-11 成本熔断）----------
# 2C2G 单机 + 同步 OpenAI SDK 跑在线程池里：并发 chat 流太多会打满 CPU/内存，
# 且每路都是一次真实计费调用。这里全局限制同时在跑的流数量（超出短时排队、再超时明确报错）。
_MAX_CONCURRENCY_DEFAULT = 2  # 同时进行的 chat 流上限
_QUEUE_WAIT_DEFAULT = 20  # 排队等待上限（秒）
_MAX_TOKENS_DEFAULT = 2048  # 单轮 LLM 输出 token 上限（tool 调用 + 正文共用）

_active_streams = 0


def _max_concurrency() -> int:
    """并发上限；<=0 表示不限制（测试 / 本地）"""
    return int(os.getenv("AGENT_MAX_CONCURRENCY", str(_MAX_CONCURRENCY_DEFAULT)))


def _queue_wait_sec() -> float:
    return float(os.getenv("AGENT_QUEUE_WAIT_SEC", str(_QUEUE_WAIT_DEFAULT)))


def _try_acquire_slot() -> bool:
    """占一个并发槽（返回 True 时调用方必须在 finally 里 _release_slot）"""
    global _active_streams
    limit = _max_concurrency()
    if limit > 0 and _active_streams >= limit:
        return False
    _active_streams += 1
    return True


def _release_slot() -> None:
    global _active_streams
    if _active_streams > 0:
        _active_streams -= 1


async def _acquire_slot() -> bool:
    """短时轮询等待并发槽；等不到返回 False（不做无限排队，避免 SSE 静默挂起）"""
    if _try_acquire_slot():
        return True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _queue_wait_sec()
    while loop.time() < deadline:
        await asyncio.sleep(0.25)
        if _try_acquire_slot():
            return True
    return False

SYSTEM_PROMPT = """你是"原声分析助手"，集成在 Lynx VoC 平台内的自然语言数据查询助手。

你的能力：
- 调用 4 个工具查 voc.db（query_overview / query_topics / query_comments / search_docs）
- 综合工具返回的数据给出结论，附"玩家原声引用"
- 引用具体评论时用「」包裹并标注情感/主题

风格要求：
- 简洁、结构化（优先用列表/表格）
- 不编造数据——所有数字必须来自工具返回
- 中文回答，玩家原声保留原文（不翻译）
- 若用户问项目文档/设计意图类，优先调 search_docs 检索项目文档

工作流硬性约束（2026-09-09 教训）：
1. **禁止"思考外泄"**：不要在 content 里写"让我先查 X、然后查 Y"等步骤描述。
   你每轮的 content 要么是面向用户的最终结论/补充，要么留空。规划步骤在"内心"
   完成即可——通过连续调用多个 tool 实现步骤推进，不要把步骤念给用户听。
2. **工具返回空时的处理**：如果某次 query_comments(topic=...) 返回 total=0，
   立刻放弃该 topic 路径，转向不带 topic 过滤的 query_comments(sentiment=...) 拿玩家原声，
   不要反复换 topic 名重试（同一过滤已经查过，再换也只是同样的数据）。
3. **每轮尽量并行**：一次响应里把能并行的 tool 全调出去（query_overview + search_docs、
   query_topics L1+L2、query_topics L3 + query_comments by topic）——不要串行等结果。
4. **5 轮就够**：本对话最多 5 轮 tool 调用，超过会强制收尾。请在前 3-4 轮拿到结论，
   第 4-5 轮拿玩家原声引用，第 5 轮总结输出完整答案。
5. **模糊指代的处理（2026-09-10）**：
   - 若[当前页面上下文]含 target_id，用户说「这游戏」「这个视频」「它」时**默认指该 target**，
     直接用它查数据，无需反问（可在开头一句点明"你正在看的是 XX"）。
   - 若没有页面上下文且用户问题指意不明（如"这游戏怎么样"却没说哪个游戏），
     **先反问确认目标**（"你想问哪款游戏/哪个视频？"），禁止猜测某个具体目标开查。
"""


# ---------- LLM 调用 ----------

def _get_llm_config() -> dict:
    """读 env 拿 LLM 凭据（复用现有 sentiment_llm 的变量名）"""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，Agent 无法调用 LLM")
    return {
        "api_key": api_key,
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    }


def _max_tokens() -> int:
    """P0-4：单轮输出上限。tool 循环每轮都会重新生成，不限长时模型可输出数千 token，
    5 轮叠加即为可观成本；2048 足够容纳 tool_calls + 一段结构化结论。"""
    return int(os.getenv("AGENT_MAX_TOKENS", str(_MAX_TOKENS_DEFAULT)))


def _format_sse(event: str, data: Any) -> str:
    """拼 SSE 一条消息：event: <name>\\ndata: <json>\\n\\n"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_args(raw: str | dict | None) -> dict:
    """OpenAI 流式 tool_call 的 arguments 是 str；非流式是 dict。统一成 dict"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {"_parse_error": True, "_raw": raw[:200]}
    return {}


def _summarize_collected_data(messages: list[dict]) -> list[str]:
    """从 messages 里的 tool 响应里抽取已收集的关键数据快照（max_rounds 兜底用）

    目标：用户看到"已到上限"提示时，能立刻看到 LLM 已经拿到了哪些数据，
    知道不是白问。返回 markdown 行列表（每行一条）。

    提取：
    - query_overview 的 target_name / total / sentiment 构成 / recommend_rate
    - query_topics 的 L1/L2/L3 头部（按 sentiment=negative 优先）
    - query_comments 的 total（玩家原声计数）
    """
    lines: list[str] = []
    overview_seen: set[str] = set()
    topics_seen: dict[str, str] = {}  # key="level:sentiment" → "topic1(123), topic2(45), ..."

    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            data = json.loads(m.get("content") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue

        if "name" in data and "total" in data and "sentiment" in data:
            # query_overview 形状
            name = data.get("name", data.get("target_id", "?"))
            if name not in overview_seen:
                overview_seen.add(name)
                senti = data.get("sentiment", {}) or {}
                lines.append(
                    f"**{name}**：共 {data.get('total', '?')} 条评论 · "
                    f"负向 {senti.get('negative', '?')} ({senti.get('negative_pct', '?')}%) · "
                    f"正向 {senti.get('positive', '?')} · 推荐率 {data.get('recommend_rate', '?')}%"
                )

        if "topics" in data and "level" in data:
            # query_topics 形状
            level = data["level"]
            sentiment = data.get("sentiment_filter") or "all"
            key = f"{level}:{sentiment}"
            if key not in topics_seen:
                tops = data.get("topics", [])[:5]
                if tops:
                    parts = [f"{t.get('topic', '?')}({t.get('total', '?')})" for t in tops]
                    topics_seen[key] = ", ".join(parts)
                    lines.append(f"**{level} {sentiment} 主题**：{topics_seen[key]}")

    return lines or ["（本轮工具调用均未返回结构化数据）"]


def _merge_tool_call_deltas(deltas: list[dict]) -> list[dict]:
    """合并 OpenAI 流式返回的 tool_call delta 列表 → 完整 tool_calls

    OpenAI 流式返回结构（按 index 分组）：
      delta = {"id": "call_abc", "type": "function",
               "function": {"name": "query_xxx", "arguments": '{"ta'}}   # 第一片
      delta = {"function": {"arguments": 'rget": "..."}'}}                # 后续片
    """
    out: dict[int, dict] = {}
    for d in deltas:
        idx = d.get("index", 0)
        slot = out.setdefault(idx, {"id": "", "type": "function",
                                      "function": {"name": "", "arguments": ""}})
        if d.get("id"):
            slot["id"] = d["id"]
        func = d.get("function") or {}
        if func.get("name"):
            slot["function"]["name"] = func["name"]
        if func.get("arguments"):
            slot["function"]["arguments"] += func["arguments"]
    result = []
    for idx in sorted(out.keys()):
        tc = out[idx]
        tc["function"]["arguments"] = _parse_args(tc["function"]["arguments"])
        result.append(tc)
    return result


# ---------- Skill 注入 ----------

def _build_system_prompt(*, user_msg: str, page_context: dict | None,
                         context: str | None = None) -> str:
    """拼装 system prompt（主 prompt + skill 注入 + 页面上下文 + 用户引用数据）"""
    parts = [SYSTEM_PROMPT]
    # skill 注入
    try:
        from src.agent.skills import compose_skill_prompts, find_relevant_skills
        skills = find_relevant_skills(question=user_msg)
        if skills:
            parts.append(compose_skill_prompts(skills))
    except Exception as e:  # skill 加载失败不影响主流程
        log.warning(f"skill match failed: {e}")
    # 页面上下文
    if page_context:
        parts.append(
            f"\n[当前页面上下文]\n{json.dumps(page_context, ensure_ascii=False, indent=2)}"
        )
    # 用户手动引用的查询数据（2026-09-10「引用当前查询」）：前端聚合摘要，随请求传入、不落库
    if context and context.strip():
        parts.append(
            "\n[用户引用的当前查询数据]（来自看板页面，仅供参照——细节与最新数据请用工具核实）：\n"
            + context.strip()[:2000]  # 双保险：前端已裁剪，后端再截断
        )
    return "\n".join(parts)


# ---------- 主流程：tool_call 循环 ----------

async def _stream_llm_round(
    client: Any,
    model: str,
    messages: list[dict],
    *,
    loop: asyncio.AbstractEventLoop,
) -> tuple[str, list[dict]]:
    """单轮 LLM 调用（同步 OpenAI SDK 包到后台线程）

    Returns:
        (final_text, tool_calls, usage)
        - final_text: 本轮 assistant 流式输出的全部 content
        - tool_calls: [{id, name, args}, ...]；空 list 表示无 tool call
        - usage: 本轮 token 消耗 dict（include_usage 末 chunk 携带）；无则 None
    """
    def _call_sync():
        return client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            stream=True,
            temperature=0.3,
            max_tokens=_max_tokens(),  # P0-4：单轮输出上限（不新增签名参数，避免破坏测试替身）
            # 2026-09-10：末 chunk（choices 为空）携带 usage；stream_chat 会逐轮累加
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}},
        )

    stream = await loop.run_in_executor(None, _call_sync)

    text_parts: list[str] = []
    tool_call_deltas: list[dict] = []
    usage: dict | None = None
    for chunk in stream:
        if not chunk.choices:
            # usage chunk：choices 为空 + usage 字段（此前被 continue 跳过，2026-09-10 修复）
            if getattr(chunk, "usage", None):
                u = chunk.usage
                usage = {
                    "prompt_tokens": u.prompt_tokens or 0,
                    "completion_tokens": u.completion_tokens or 0,
                    "total_tokens": u.total_tokens or 0,
                    "cached_tokens": getattr(
                        getattr(u, "prompt_tokens_details", None), "cached_tokens", None
                    ) or 0,
                }
            continue
        delta = chunk.choices[0].delta
        if getattr(delta, "content", None):
            text_parts.append(delta.content)
        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                # Pydantic 模型 → dict
                tool_call_deltas.append({
                    "index": tc.index,
                    "id": tc.id or "",
                    "type": tc.type or "function",
                    "function": {
                        "name": (tc.function.name if tc.function else None) or "",
                        "arguments": (tc.function.arguments if tc.function else None) or "",
                    },
                })

    return "".join(text_parts), _merge_tool_call_deltas(tool_call_deltas), usage


async def stream_chat(
    *,
    user_msg: str,
    session: Session,
    session_id: str,
    history: list[dict] | None = None,
    page_context: dict | None = None,
    context: str | None = None,
) -> AsyncIterator[str]:
    """生成 SSE 事件流（tool_call 循环 + skill 注入 + 消息落库）

    Args:
        session: 调用方的 SQLAlchemy session（请求级，由 routers_agent 注入）
        session_id: AgentSession.id（落库用）
        history: 历史的 user/assistant 消息（不含 tool——tool 由本函数管）
        page_context: 当前页面的 page_context JSON
        context: 用户手动引用的查询数据摘要（「引用当前查询」，随请求传入不落库）
    """
    from src.agent.sessions import append_messages

    full_text_chunks: list[str] = []
    rounds_used = 0
    final_assistant_text = ""
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}

    try:
        # LLM 初始化放在 try 内（2026-09-09 对抗审查 P1#3）：
        # 之前 _get_llm_config() / OpenAI(...) / _build_system_prompt() 在 try 外，
        # 无 DEEPSEEK_API_KEY 时抛 RuntimeError 直接冒泡，前端只见断流、拿不到 error 事件
        cfg = _get_llm_config()
        # 延迟 import openai
        from openai import OpenAI

        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        loop = asyncio.get_running_loop()

        system_prompt = _build_system_prompt(
            user_msg=user_msg, page_context=page_context, context=context,
        )
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_msg})

        for round_idx in range(MAX_TOOL_ROUNDS):
            rounds_used = round_idx + 1

            text, tool_calls, round_usage = await _stream_llm_round(
                client, cfg["model"], messages, loop=loop,
            )
            if round_usage:
                for k in usage_total:
                    usage_total[k] += int(round_usage.get(k) or 0)

            # 1) 流式推 token（无论是否有 tool_call，assistant 的 content 都推）
            if text:
                full_text_chunks.append(text)
                yield _format_sse("token", {"delta": text})

            # 2) 无 tool_call → 本轮就是最终回复
            if not tool_calls:
                final_assistant_text = "".join(full_text_chunks)
                # 落库 assistant（final）
                try:
                    append_messages(session, session_id, [{
                        "role": "assistant", "content": final_assistant_text,
                    }])
                except Exception as e:
                    log.warning(f"persist assistant final failed: {e}")
                yield _format_sse("done", {
                    "rounds": rounds_used,
                    "full_text": final_assistant_text,
                    "usage": usage_total if usage_total["total_tokens"] else None,
                })
                return

            # 3) 有 tool_call：先落 assistant 消息（含 tool_calls），再逐个执行
            assistant_record = {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": tc["id"], "name": tc["function"]["name"],
                     "arguments": tc["function"]["arguments"]}
                    for tc in tool_calls
                ],
            }
            # 推入下一轮 messages（OpenAI 格式：tool_calls 字段 + content 可为空）
            messages.append({
                "role": "assistant",
                "content": text or "",
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["function"]["name"],
                                  "arguments": json.dumps(tc["function"]["arguments"], ensure_ascii=False)}}
                    for tc in tool_calls
                ],
            })
            try:
                append_messages(session, session_id, [assistant_record])
            except Exception as e:
                log.warning(f"persist assistant tool_calls failed: {e}")

            # 4) 逐个执行 tool（落库 + SSE 事件 + 推入 messages）
            for tc in tool_calls:
                tc_id = tc["id"]
                tc_name = tc["function"]["name"]
                tc_args = tc["function"]["arguments"]
                yield _format_sse("tool_start", {"id": tc_id, "name": tc_name, "args": tc_args})

                tool_result = run_tool(tc_name, tc_args, session=session)

                # 截断 SSE 预览（避免 SSE 消息过大）
                preview = tool_result[:300] + ("...[truncated]" if len(tool_result) > 300 else "")
                yield _format_sse("tool_end", {"id": tc_id, "result_preview": preview})

                # 落库 tool 响应
                try:
                    append_messages(session, session_id, [{
                        "role": "tool",
                        "content": tool_result,
                        "tool_call_id": tc_id,
                        "tool_name": tc_name,
                    }])
                except Exception as e:
                    log.warning(f"persist tool result failed: {e}")

                # 推入下一轮 messages（OpenAI 要求 tool_call_id 对齐）
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": tool_result,
                })

            # 5) loop → 下一轮 LLM 会拿到 tool 结果继续生成

        # 超过 MAX_TOOL_ROUNDS：强制收尾
        log.warning(f"chat hit MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS} for session={session_id}")
        # 把"已到上限"提示作为 token 流式推给前端（不能再只放 done.full_text——前端不会显示）
        # 已经流出的 full_text_chunks 是 LLM 全部"让我…"的自述，意义不大；
        # 这里把"已到上限"的明确文案作为最终输出推出去，让前端能给用户一个交代。
        forced_tail = (
            "\n\n---\n"
            "⚠️ 工具调用已达上限（最多 {} 轮）。"
            "已收集到的数据：\n{}\n\n"
            "建议：把问题拆小再问（比如「《黑神话》最近 30 天战斗系统痛点的原声」"
            "而不是「最近 30 天主要痛点是什么」），或直接到看板看指标/筛选评论。"
        ).format(MAX_TOOL_ROUNDS, "\n".join(_summarize_collected_data(messages)))
        yield _format_sse("token", {"delta": forced_tail})
        final_assistant_text = "".join(full_text_chunks) + forced_tail
        try:
            append_messages(session, session_id, [{
                "role": "assistant", "content": final_assistant_text,
            }])
        except Exception as e:
            log.warning(f"persist final after max rounds failed: {e}")
        yield _format_sse("done", {
            "rounds": rounds_used,
            "full_text": final_assistant_text,
            "max_rounds_hit": True,
            "usage": usage_total if usage_total["total_tokens"] else None,
        })

    except Exception as e:
        log.exception("chat stream failed")
        # 把已经流出的内容也尝试落库（避免内容丢失）
        partial = "".join(full_text_chunks)
        if partial:
            try:
                append_messages(session, session_id, [
                    {"role": "assistant", "content": partial + f"\n\n[错误: {e}]"},
                ])
            except Exception:
                pass
        yield _format_sse("error", {"message": str(e)})


async def stream_chat_guarded(**kwargs: Any) -> AsyncIterator[str]:
    """`stream_chat` 的并发保护包装（P0-4）

    路由层统一改用本函数：并发已满时先短时排队，超时则直接发 error 事件
    （SSE 已开始，无法再返回 HTTP 429——用 error 事件让前端拿到明确提示）。
    槽位在 finally 释放，异常/客户端断开都不会泄漏。
    """
    if not await _acquire_slot():
        yield _format_sse("error", {
            "message": "当前对话人数较多（服务并发已满），请稍后重试。",
            "busy": True,
        })
        return
    try:
        async for chunk in stream_chat(**kwargs):
            yield chunk
    finally:
        _release_slot()
