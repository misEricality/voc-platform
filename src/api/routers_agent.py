"""原声分析 Agent API 路由（2026-09-09 · 见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §4）

端点清单：
- POST   /api/agent/sessions             新建会话
- GET    /api/agent/sessions             列出会话（anon_user_id 隔离 + 分页 + page 过滤）
- GET    /api/agent/sessions/{id}        详情（含消息）
- DELETE /api/agent/sessions/{id}        删除（FK CASCADE 删消息）
- POST   /api/agent/chat                 SSE 流式对话（Round 3 单轮；Round 4 加 tool 循环）
- GET    /api/agent/export               Markdown 导出（按 anon_user_id 隔离）

所有端点位于 /api/agent/*，公开访问 + IP 速率限制（60 req/min）。
anon_user_id 从 X-Anon-User-Id header 读；缺失时按"无身份"处理（隔离生效）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.agent import sessions as sess_mod
from src.api.auth import (
    check_agent_daily_quota,
    check_agent_rate,
    client_ip,
    get_session,
)

log = logging.getLogger("voc.api.agent")

agent_router = APIRouter(prefix="/api/agent", tags=["agent"])


def _ok(data: Any) -> dict:
    return {"ok": True, "data": data}


# ==================== Sessions CRUD ====================


# P0-5（2026-09-11）：请求体字段一律加长度上限——否则可塞超大 JSON 直灌 LLM（按 token 计费）
# 或写入 DB。上限按「前端正常用量 × 充裕余量」取值，正常使用不会触顶。
_MAX_USER_MSG = 4000  # 单条用户输入
_MAX_HISTORY_ITEMS = 50  # 前端回传的历史条数（tool 由服务端管理，不回传）
_MAX_HISTORY_CONTENT = 20_000  # 单条历史内容（assistant 结论可能较长）
_MAX_PAGE_CONTEXT = 8_000  # 页面上下文 JSON 串
_MAX_TITLE = 100


class CreateSessionBody(BaseModel):
    page: str = Field(..., min_length=1, max_length=32)
    page_context: str | None = Field(None, max_length=_MAX_PAGE_CONTEXT)
    title: str | None = Field(None, max_length=_MAX_TITLE)
    model: str | None = Field(None, max_length=64)


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|tool)$")
    content: str | None = Field(None, max_length=_MAX_HISTORY_CONTENT)
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = Field(None, max_length=128)
    tool_name: str | None = Field(None, max_length=64)


class ChatBody(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    user_msg: str = Field(..., min_length=1, max_length=_MAX_USER_MSG)
    history: list[ChatMessage] | None = Field(None, max_length=_MAX_HISTORY_ITEMS)
    # 2026-09-10「引用当前查询」：前端聚合摘要（≤2000 字符，chat.py 再截断），不落库
    context: str | None = Field(None, max_length=4000)


@agent_router.post("/sessions")
def api_create_session(
    body: CreateSessionBody,
    request: Request,
    s: Session = Depends(get_session),
):
    """新建会话（id 服务端生成 uuid4，前端立即持有）"""
    check_agent_rate(client_ip(request))
    anon = sess_mod.get_anon_user_id(request)
    sess = sess_mod.create_session(
        s,
        page=body.page,
        page_context=body.page_context,
        title=body.title,
        model=body.model or "deepseek-v4-flash",
        anon_user_id=anon,
    )
    return _ok(sess.to_dict())


@agent_router.get("/sessions")
def api_list_sessions(
    request: Request,
    page: str | None = None,
    limit: int = 30,
    offset: int = 0,
    s: Session = Depends(get_session),
):
    """列出会话（强制 anon_user_id 隔离；不传 anon header → 返回空列表）"""
    check_agent_rate(client_ip(request))
    anon = sess_mod.get_anon_user_id(request)
    if limit < 1 or limit > 100:
        raise HTTPException(422, "limit 必须在 1-100")
    if offset < 0:
        raise HTTPException(422, "offset 必须 >= 0")
    rows, total = sess_mod.list_sessions(s, anon_user_id=anon, page=page, limit=limit, offset=offset)
    return _ok({
        "sessions": [r.to_dict() for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@agent_router.get("/sessions/{session_id}")
def api_get_session(
    session_id: str,
    request: Request,
    s: Session = Depends(get_session),
):
    """会话详情（含全部消息）；越权返回 404（不泄露存在性）"""
    check_agent_rate(client_ip(request))
    anon = sess_mod.get_anon_user_id(request)
    detail = sess_mod.get_session_detail(s, session_id, anon)
    if not detail:
        raise HTTPException(404, "会话不存在或无权访问")
    return _ok(detail)


@agent_router.delete("/sessions/{session_id}")
def api_delete_session(
    session_id: str,
    request: Request,
    s: Session = Depends(get_session),
):
    """删除会话（FK CASCADE 删消息）；越权返回 404"""
    check_agent_rate(client_ip(request))
    anon = sess_mod.get_anon_user_id(request)
    if not sess_mod.delete_session(s, session_id, anon):
        raise HTTPException(404, "会话不存在或无权访问")
    return _ok({"deleted": session_id})


# ==================== Chat SSE ====================


@agent_router.post("/chat")
async def api_chat(
    body: ChatBody,
    request: Request,
    s: Session = Depends(get_session),
):
    """流式对话（SSE）。

    前端用 EventSource 不便带 body，改用 fetch + ReadableStream；本端点返回
    `text/event-stream`，按 §0 SSE 协议推送 event: token / done / error。
    """
    ip = client_ip(request)
    check_agent_rate(ip)
    anon = sess_mod.get_anon_user_id(request)

    # 校验 session 归属（fail-closed，与 get/delete 一致：无 anon 或归属不符一律 404）
    # 2026-09-09 对抗审查 P0#1：原逻辑 `if anon and sess.anon_user_id and ...` 在
    #   无 X-Anon-User-Id 时短路放行，允许向任意 session_id 写消息（越权 + 消耗 LLM）
    from src.storage.db import AgentSession
    sess = s.get(AgentSession, body.session_id)
    if not anon or not sess or sess.anon_user_id != anon:
        raise HTTPException(404, "会话不存在或无权访问")

    # P0-4 成本熔断（刻意放在归属校验**之后**）：chat 是唯一真实消耗 LLM 的端点
    # （最多 5 轮 tool 循环，每轮重发全量 messages）。全局日额度保余额、单 IP 日额度防单点刷爆；
    # 超限直接 429，连流都不开。放在校验之后 → 越权/不存在的会话不扣额度，防被拿 404 刷爆额度。
    check_agent_daily_quota(ip)

    # 解析 page_context → dict（若为 JSON 字符串）
    page_ctx: dict | None = None
    if sess.page_context:
        try:
            import json
            page_ctx = json.loads(sess.page_context)
        except (TypeError, ValueError):
            page_ctx = None

    # 持久化 user 消息（落库，让用户刷新后能看到）
    sess_mod.append_messages(s, body.session_id, [{
        "role": "user", "content": body.user_msg,
    }])

    history = []
    if body.history:
        for h in body.history:
            history.append({
                "role": h.role,
                "content": h.content or "",
            })

    from src.agent.chat import stream_chat_guarded

    async def event_gen():
        # chat.py 负责 assistant / tool 消息的落库（与 tool_call 循环共享状态机）
        # user_msg 已在路由层落库（让用户立即能看到自己的输入，避免 SSE 等待）
        # P0-4：stream_chat_guarded 额外做全局并发闸（满则发 error 事件，不静默挂起）
        async for chunk in stream_chat_guarded(
            user_msg=body.user_msg,
            session=s,
            session_id=body.session_id,
            history=history,
            page_context=page_ctx,
            context=body.context,
        ):
            yield chunk

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",  # 禁用 nginx buffering（若 VPS 用 nginx 反代）
        },
    )


# ==================== Search Docs (P2 FAQ 公开端点 · §4.1) ====================


@agent_router.post("/search")
def api_search_docs(
    request: Request,
    q: str = Query(..., min_length=1, description="查询关键词或短语"),
    top_k: int = Query(5, ge=1, le=20, description="返回最相关的 K 个章节"),
):
    """项目文档 FAQ 检索（前端调试用 · §4.1）

    与 search_docs tool 共享同一索引（md_corpus.search）；
    只暴露为公开端点，便于前端在 chat 流外快速验证召回效果。

    公开原因：搜索结果无隐私（命中的是公共文档章节）；
    速率限制仍生效（60/min/IP），与 chat 同框架防滥用。

    Query 参数而非 body：与 §4.1 表 `?q=&top_k=5` 写法对齐；
    也方便 curl 测试 `curl -X POST '.../api/agent/search?q=agent&top_k=3'`。
    """
    check_agent_rate(client_ip(request))
    from src.agent.md_corpus import search as md_search
    items = md_search(q, top_k=top_k)
    return _ok({"items": items, "query": q, "top_k": top_k})


# ==================== Export ====================


@agent_router.get("/export")
def api_export(
    request: Request,
    s: Session = Depends(get_session),
):
    """Markdown 导出（严格按 anon_user_id 隔离，无 admin 通配）

    返回 text/markdown，浏览器触发下载（前端用 Content-Disposition 接收）。
    """
    check_agent_rate(client_ip(request))
    anon = sess_mod.get_anon_user_id(request)
    md = sess_mod.export_markdown(s, anon)
    filename = f"lynx_agent_history_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.md"
    return PlainTextResponse(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
