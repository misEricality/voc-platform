"""会话/消息 CRUD + Markdown 导出（2026-09-09 · 见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §3/§5.6）

职责：
- create_session：POST /api/agent/sessions 时插入新会话
- list_sessions：按 anon_user_id 过滤，分页
- get_session_detail：取会话 + 全部消息
- delete_session：删会话（FK CASCADE 自动删消息）
- export_markdown：按 anon_user_id 全量导出 Markdown
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.storage.db import AgentMessage, AgentSession, _utcnow

ANON_USER_HEADER = "x-anon-user-id"


def get_anon_user_id(request) -> str | None:
    """从请求头取 anon_user_id；空字符串/None 一律当 None（避免误匹配）"""
    val = request.headers.get(ANON_USER_HEADER, "").strip()
    return val or None


def create_session(
    s: Session,
    *,
    page: str,
    page_context: str | None = None,
    title: str | None = None,
    model: str = "deepseek-v4-flash",
    anon_user_id: str | None = None,
) -> AgentSession:
    """新建一个 session（id 用 uuid4，前端立即可用）"""
    sess = AgentSession(
        id=str(uuid.uuid4()),
        page=page,
        page_context=page_context,
        title=title,
        model=model,
        anon_user_id=anon_user_id,
    )
    s.add(sess)
    s.commit()
    s.refresh(sess)
    return sess


def list_sessions(
    s: Session,
    *,
    anon_user_id: str | None,
    page: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[AgentSession], int]:
    """按 anon_user_id 列出会话（必须传 anon_user_id；不传返回空列表防越权）

    Returns:
        (sessions, total_count)
    """
    if not anon_user_id:
        return [], 0

    # 条件统一（2026-09-09 对抗审查 P1#4）：total 与列表必须用同一套过滤，
    # 否则带 page 时 total 是"该 anon 全量"而列表是"该 page"，分页 total 失真
    conditions = [AgentSession.anon_user_id == anon_user_id]
    if page:
        conditions.append(AgentSession.page == page)

    total_count = s.execute(
        select(func.count()).select_from(AgentSession).where(*conditions)
    ).scalar() or 0

    rows = s.execute(
        select(AgentSession).where(*conditions)
        .order_by(AgentSession.updated_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return list(rows), total_count


def get_session_detail(s: Session, session_id: str,
                        anon_user_id: str | None) -> dict | None:
    """取会话 + 全部消息（按 created_at 升序）；越权访问返回 None"""
    if not anon_user_id:
        return None
    sess = s.execute(
        select(AgentSession).where(AgentSession.id == session_id)
    ).scalar_one_or_none()
    if not sess or sess.anon_user_id != anon_user_id:
        return None
    msgs = s.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
    ).scalars().all()
    return {
        "session": sess.to_dict(),
        "messages": [m.to_dict() for m in msgs],
    }


def delete_session(s: Session, session_id: str, anon_user_id: str | None) -> bool:
    """删会话（FK CASCADE 删 messages）；越权返回 False"""
    if not anon_user_id:
        return False
    sess = s.get(AgentSession, session_id)
    if not sess or sess.anon_user_id != anon_user_id:
        return False
    s.execute(delete(AgentSession).where(AgentSession.id == session_id))
    s.commit()
    return True


def append_messages(s: Session, session_id: str,
                     messages: list[dict[str, Any]]) -> list[AgentMessage]:
    """批量插入消息（每条 dict 至少含 role/content；tool_calls/tool_call_id/tool_name 可选）

    Args:
        messages: [{"role": "user"|"assistant"|"tool", "content": ..., "tool_calls": ..., ...}, ...]
    """
    objs = []
    for m in messages:
        objs.append(AgentMessage(
            session_id=session_id,
            role=m["role"],
            content=m.get("content"),
            tool_calls=json.dumps(m["tool_calls"], ensure_ascii=False) if m.get("tool_calls") else None,
            tool_call_id=m.get("tool_call_id"),
            tool_name=m.get("tool_name"),
        ))
    s.add_all(objs)
    # 同步会话 updated_at（onupdate 自动；此处显式 refresh 保证返回最新值）
    sess = s.get(AgentSession, session_id)
    if sess:
        sess.updated_at = _utcnow()
    s.commit()
    return objs


def touch_session(s: Session, session_id: str) -> None:
    """更新会话的 updated_at（让列表按活跃度排序）"""
    sess = s.get(AgentSession, session_id)
    if sess:
        sess.updated_at = _utcnow()
        s.commit()


# ---------- Markdown 导出 ----------

_ROLE_EMOJI = {"user": "👤", "assistant": "🤖", "tool": "🔧"}


def export_markdown(s: Session, anon_user_id: str | None) -> str:
    """按 anon_user_id 全量导出 Markdown（严格隔离，无 admin 通配）

    格式：
    ```
    # Lynx AI 对话记录

    > 导出时间：2026-09-09 15:23
    > 匿名标识：anon_4f3e2a1b...
    > 共 N 个会话，M 条消息

    ---

    ## 1. <title>
    **页面**：<page>　**创建**：<time>　**消息数**：<n>

    **[15:23:01] 👤 用户**
    ...

    **[15:23:08] 🔧 工具调用**
    - `query_topics` (...)
    ```
    """
    if not anon_user_id:
        return "# Lynx AI 对话记录\n\n> 未提供 anon_user_id，无内容可导出。\n"

    sess_rows = s.execute(
        select(AgentSession)
        .where(AgentSession.anon_user_id == anon_user_id)
        .order_by(AgentSession.created_at.asc())
    ).scalars().all()

    msg_count = 0
    body_sections: list[str] = []
    for idx, sess in enumerate(sess_rows, 1):
        msgs = s.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == sess.id)
            .order_by(AgentMessage.created_at.asc(), AgentMessage.id.asc())
        ).scalars().all()
        msg_count += len(msgs)

        sec_lines = [
            f"## {idx}. {sess.title or '(无标题)'}",
            f"**页面**：`{sess.page}`　**创建**：{_fmt_time(sess.created_at)}　"
            f"**消息数**：{len(msgs)}",
            "",
        ]
        for m in msgs:
            ts = _fmt_time(m.created_at)
            role = m.role
            emoji = _ROLE_EMOJI.get(role, "·")

            if role == "tool":
                sec_lines.append(f"**[{ts}] {emoji} 工具响应** (`{m.tool_name}`)")
                if m.content:
                    sec_lines.append("```")
                    sec_lines.append(m.content[:2000])  # 截断防巨大
                    sec_lines.append("```")
                sec_lines.append("")
                continue

            sec_lines.append(f"**[{ts}] {emoji} {'用户' if role == 'user' else '助手'}**")
            if m.content:
                sec_lines.append(m.content)
            if m.tool_calls:
                try:
                    calls = json.loads(m.tool_calls)
                    for c in calls:
                        sec_lines.append(f"_调用 `{c.get('name')}`: {json.dumps(c.get('arguments', {}), ensure_ascii=False)}_")
                except (TypeError, ValueError):
                    sec_lines.append(f"_调用工具: {m.tool_calls}_")
            sec_lines.append("")
        body_sections.append("\n".join(sec_lines))

    header = [
        "# Lynx AI 对话记录",
        "",
        f"> 导出时间：{_fmt_time(datetime.now(timezone.utc).replace(tzinfo=None))}",
        f"> 匿名标识：`{anon_user_id}`",
        f"> 共 {len(sess_rows)} 个会话，{msg_count} 条消息",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + "\n\n".join(body_sections) + "\n"


def _fmt_time(dt: datetime | None) -> str:
    if not dt:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")
