"""原声分析 Agent API 端点回归测试（2026-09-09）

锁住的回归（详见 docs/architecture/ORIGINAL_VOICE_ANALYSIS_AGENT.md §4）：
1. POST /api/agent/sessions —— 创建会话，写入 anon_user_id
2. GET /api/agent/sessions —— 按 anon_user_id 隔离；分页
3. GET /api/agent/sessions/{id} —— 详情含消息；越权 404
4. DELETE /api/agent/sessions/{id} —— 删除 + FK CASCADE
5. GET /api/agent/export —— Markdown 导出，anon_user_id 隔离
6. POST /api/agent/chat —— SSE 框架（Round 3：单轮 token 流，无 LLM 实调）
7. 速率限制 —— 60 req/min/IP 超阈值 429

每个用例用独立测试 DB + FastAPI TestClient。
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def test_db_path():
    db = ROOT / "data" / f"voc_test_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    yield db
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


@pytest.fixture
def client(test_db_path, monkeypatch):
    """独立测试 DB + FastAPI TestClient"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    # 测试环境关掉 agent 的全部限流/熔断（避免影响其他测试；均有专门用例覆盖）
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MIN", "0")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "0")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "0")
    monkeypatch.setenv("AGENT_MAX_CONCURRENCY", "0")

    from src.api.main import create_app

    app = create_app(db_url=f"sqlite:///{test_db_path}")
    with TestClient(app) as c:
        yield c


# ==================== 1. Sessions CRUD ====================


def test_create_session(client):
    """POST /api/agent/sessions 应返回 uuid + 200"""
    r = client.post(
        "/api/agent/sessions",
        json={"page": "agent", "title": "首问"},
        headers={"X-Anon-User-Id": "anon-1"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["id"]
    assert data["page"] == "agent"
    assert data["title"] == "首问"
    assert data["anon_user_id"] == "anon-1"
    assert data["model"] == "deepseek-v4-flash"


def test_list_sessions_isolated_by_anon(client):
    """anon_user_id A 看不到 anon_user_id B 的会话；不传 header → 空列表"""
    # 创建 3 个会话，2 个属于 anon-A，1 个属于 anon-B
    for _ in range(2):
        client.post("/api/agent/sessions", json={"page": "dashboard"},
                    headers={"X-Anon-User-Id": "anon-A"})
    client.post("/api/agent/sessions", json={"page": "compare"},
                headers={"X-Anon-User-Id": "anon-B"})

    # anon-A 列出
    r = client.get("/api/agent/sessions", headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 2
    assert all(s["anon_user_id"] == "anon-A" for s in data["sessions"])

    # anon-B 列出
    r = client.get("/api/agent/sessions", headers={"X-Anon-User-Id": "anon-B"})
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["sessions"][0]["anon_user_id"] == "anon-B"

    # 无 header → 空
    r = client.get("/api/agent/sessions")
    data = r.json()["data"]
    assert data["total"] == 0
    assert data["sessions"] == []


def test_list_sessions_pagination(client):
    """分页正确：limit=1 翻页，total 一致"""
    for _ in range(3):
        client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-A"})

    r = client.get("/api/agent/sessions?limit=1&offset=0",
                   headers={"X-Anon-User-Id": "anon-A"})
    data = r.json()["data"]
    assert data["total"] == 3
    assert len(data["sessions"]) == 1

    r = client.get("/api/agent/sessions?limit=1&offset=2",
                   headers={"X-Anon-User-Id": "anon-A"})
    data = r.json()["data"]
    assert data["total"] == 3
    assert len(data["sessions"]) == 1


def test_get_session_detail_includes_messages(client):
    """会话详情应含 messages 列表（user/assistant/tool）"""
    # 创建会话 + 手动插入 3 条消息
    r = client.post("/api/agent/sessions", json={"page": "dashboard"},
                    headers={"X-Anon-User-Id": "anon-A"})
    sid = r.json()["data"]["id"]

    from src.storage.db import AgentMessage, _utcnow, init_db
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        s.add(AgentMessage(session_id=sid, role="user", content="hi"))
        s.add(AgentMessage(session_id=sid, role="assistant", content="hello"))
        s.add(AgentMessage(session_id=sid, role="tool", content="{}",
                            tool_call_id="c1", tool_name="query_overview"))
        s.commit()

    r = client.get(f"/api/agent/sessions/{sid}",
                   headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 200
    detail = r.json()["data"]
    assert detail["session"]["id"] == sid
    assert len(detail["messages"]) == 3
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "tool"]


def test_get_session_cross_anon_returns_404(client):
    """anon-A 不能读 anon-B 的会话（404，不暴露存在性）"""
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-B"})
    sid = r.json()["data"]["id"]

    r = client.get(f"/api/agent/sessions/{sid}",
                   headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 404


def test_delete_session_cascades_messages(client):
    """DELETE 应级联删 messages"""
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-A"})
    sid = r.json()["data"]["id"]

    from src.storage.db import AgentMessage, init_db
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        s.add(AgentMessage(session_id=sid, role="user", content="hi"))
        s.add(AgentMessage(session_id=sid, role="assistant", content="hello"))
        s.commit()

    r = client.delete(f"/api/agent/sessions/{sid}",
                      headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] == sid

    # 再次 GET 应 404
    r = client.get(f"/api/agent/sessions/{sid}",
                   headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 404

    # messages 表也应清空
    with SessionLocal() as s:
        from sqlalchemy import select, func
        from src.storage.db import AgentMessage as M
        n = s.execute(select(func.count()).select_from(M).where(M.session_id == sid)).scalar()
        assert n == 0


def test_delete_cross_anon_returns_404(client):
    """anon-A 不能删 anon-B 的会话"""
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-B"})
    sid = r.json()["data"]["id"]

    r = client.delete(f"/api/agent/sessions/{sid}",
                      headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 404


# ==================== 2. Export ====================


def test_export_markdown_isolated(client):
    """导出严格按 anon_user_id 隔离"""
    # anon-A: 1 个会话 + 2 条消息
    r = client.post("/api/agent/sessions", json={"page": "dashboard", "title": "A 的会话"},
                    headers={"X-Anon-User-Id": "anon-A"})
    sid_a = r.json()["data"]["id"]
    from src.storage.db import AgentMessage, init_db
    _, SessionLocal = init_db()
    with SessionLocal() as s:
        s.add(AgentMessage(session_id=sid_a, role="user", content="A 的问题"))
        s.add(AgentMessage(session_id=sid_a, role="assistant", content="A 的回答"))
        s.commit()

    # anon-B: 1 个会话 + 1 条消息
    r = client.post("/api/agent/sessions", json={"page": "compare", "title": "B 的会话"},
                    headers={"X-Anon-User-Id": "anon-B"})
    sid_b = r.json()["data"]["id"]
    with SessionLocal() as s:
        s.add(AgentMessage(session_id=sid_b, role="user", content="B 的问题"))
        s.commit()

    # anon-A 导出
    r = client.get("/api/agent/export", headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert 'filename="lynx_agent_history_' in r.headers.get("content-disposition", "")
    body = r.text
    assert "A 的会话" in body
    assert "A 的问题" in body
    assert "A 的回答" in body
    assert "B 的会话" not in body
    assert "B 的问题" not in body
    assert "anon-A" in body
    assert "anon-B" not in body

    # 无 header → 友好提示
    r = client.get("/api/agent/export")
    assert r.status_code == 200
    assert "无内容可导出" in r.text


# ==================== 3. Chat SSE ====================


def test_chat_returns_sse_stream(client):
    """POST /api/agent/chat 应返回 text/event-stream"""
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-A"})
    sid = r.json()["data"]["id"]

    # 没 LLM key 时 chat 应优雅返回 error 事件（不抛 500）
    r = client.post(
        "/api/agent/chat",
        json={"session_id": sid, "user_msg": "测试"},
        headers={"X-Anon-User-Id": "anon-A"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    # 要么有 token 流，要么有 error 事件（取决于是否配 DEEPSEEK_API_KEY）
    assert "event:" in body


def test_chat_requires_valid_session(client):
    """不存在的 session_id 应 404"""
    r = client.post(
        "/api/agent/chat",
        json={"session_id": "non-existent", "user_msg": "hi"},
        headers={"X-Anon-User-Id": "anon-A"},
    )
    assert r.status_code == 404


def test_chat_persists_user_message(client):
    """发送后 user 消息应落库（前端刷新后能看到）"""
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-A"})
    sid = r.json()["data"]["id"]

    client.post("/api/agent/chat",
                json={"session_id": sid, "user_msg": "我的问题"},
                headers={"X-Anon-User-Id": "anon-A"})

    # 验证：user 消息一定在；assistant/tool 可能存在（取决于 chat 是否成功调用 LLM）
    r = client.get(f"/api/agent/sessions/{sid}",
                   headers={"X-Anon-User-Id": "anon-A"})
    msgs = r.json()["data"]["messages"]
    assert len(msgs) >= 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "我的问题"


# ==================== 4. 速率限制 ====================


def test_agent_rate_limit(client, monkeypatch):
    """60 req/min 超阈值 429（用 monkeypatch 调到 3 便于测试）"""
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MIN", "3")

    # 重置模块内的限流状态（避免受其他用例污染）
    from src.api import auth
    auth._AGENT_HITS.clear()

    # 第 1-3 次成功，第 4 次 429
    for i in range(3):
        r = client.get("/api/agent/sessions", headers={"X-Anon-User-Id": "anon-A"})
        assert r.status_code == 200, f"req {i+1} 失败: {r.status_code}"

    r = client.get("/api/agent/sessions", headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 429
    assert "请求过于频繁" in r.text


# ==================== 5. 工具函数 ====================


def test_export_markdown_friendly_empty():
    """无 anon_user_id 时导出友好提示"""
    from src.agent.sessions import export_markdown
    md = export_markdown(s=None, anon_user_id=None)  # type: ignore
    assert "无内容可导出" in md


# ==================== 6. 文档搜索（§4.1 公开端点）====================


def test_search_docs_endpoint(client):
    """POST /api/agent/search：与 search_docs tool 共享同一 md_corpus 索引

    锁住：
    - 参数通过 query string 传入（与 §4.1 表 q + top_k 写法一致）
    - top_k 限制生效
    - 空 query 被 422（min_length=1 校验）
    - top_k > 20 被 422（ge=1, le=20）
    """
    # 正常路径：返回 items + query/top_k 回显
    r = client.post("/api/agent/search?q=agent&top_k=3")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["query"] == "agent"
    assert data["top_k"] == 3
    assert isinstance(data["items"], list)
    assert len(data["items"]) <= 3
    # 命中项字段齐全（来自 md_corpus.search）
    if data["items"]:
        item = data["items"][0]
        assert {"path", "snippet", "score"}.issubset(item.keys())

    # 空 query → 422
    r = client.post("/api/agent/search?q=")
    assert r.status_code == 422

    # top_k 超界 → 422
    r = client.post("/api/agent/search?q=agent&top_k=999")
    assert r.status_code == 422


def test_search_docs_endpoint_rate_limit(client, monkeypatch):
    """search 端点也走 IP 速率限制（与 chat 同框架）"""
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MIN", "2")
    from src.api import auth
    auth._AGENT_HITS.clear()

    # 前 2 次成功
    for i in range(2):
        r = client.post("/api/agent/search?q=test")
        assert r.status_code == 200, f"req {i+1}: {r.status_code}"

    # 第 3 次 429
    r = client.post("/api/agent/search?q=test")
    assert r.status_code == 429


# ==================== 对抗审查修复回归（2026-09-09 P0/P1）====================


def test_chat_no_header_returns_404(client):
    """无 X-Anon-User-Id 时 chat 应 404（fail-closed，2026-09-09 对抗审查 P0#1）

    锁住：chat 端点与 get/delete 一致，无 anon header 一律拒绝，
    不允许向任意 session_id 写消息（原逻辑短路放行 = 越权 + 消耗 LLM）。
    """
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-A"})
    sid = r.json()["data"]["id"]

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "越权测试"},
                    headers={})  # 无 header
    assert r.status_code == 404


def test_chat_cross_anon_returns_404(client):
    """anon-A 不能给 anon-B 的 session 发 chat（2026-09-09 对抗审查 P0#1）"""
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-B"})
    sid = r.json()["data"]["id"]

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "越权测试"},
                    headers={"X-Anon-User-Id": "anon-A"})
    assert r.status_code == 404


def test_list_sessions_page_total_consistency(client):
    """带 page 过滤时 total 应只统计该 page 的会话（2026-09-09 对抗审查 P1#4）

    锁住：total 与列表用同一套过滤条件，否则带 page 时 total 失真。
    """
    h = {"X-Anon-User-Id": "anon-A"}
    client.post("/api/agent/sessions", json={"page": "dashboard"}, headers=h)
    client.post("/api/agent/sessions", json={"page": "dashboard"}, headers=h)
    client.post("/api/agent/sessions", json={"page": "compare"}, headers=h)

    r = client.get("/api/agent/sessions?page=dashboard", headers=h).json()["data"]
    assert r["total"] == 2, f"total 应只统计 dashboard 会话，实际 {r['total']}"
    assert len(r["sessions"]) == 2
    assert all(s["page"] == "dashboard" for s in r["sessions"])


def test_agent_rate_hits_bounded(monkeypatch):
    """伪造海量 IP 时 _AGENT_HITS 不应无限增长（2026-09-09 对抗审查 P0#2）

    锁住：超过 _AGENT_HITS_MAX_KEYS 后触发淘汰/清空，dict 大小受控，
    防止伪造 X-Forwarded-For 撑爆内存。
    """
    from src.api import auth

    auth._AGENT_HITS.clear()
    monkeypatch.setattr(auth, "_AGENT_HITS_MAX_KEYS", 5)
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MIN", "60")  # 高阈值，避免 429 干扰

    for i in range(20):
        auth.check_agent_rate(f"10.9.9.{i}")

    assert len(auth._AGENT_HITS) <= auth._AGENT_HITS_MAX_KEYS, (
        f"_AGENT_HITS 应被限幅到 <= {auth._AGENT_HITS_MAX_KEYS}，"
        f"实际 {len(auth._AGENT_HITS)}"
    )
    auth._AGENT_HITS.clear()


# ==================== 5. XFF 取信（P0-2 · 2026-09-11） ====================


def _make_request(headers: dict | None = None, client=("127.0.0.1", 1234)):
    """构造最小 http scope 的 starlette Request（单测 client_ip 用）"""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": client,
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_client_ip_takes_last_xff_segment():
    """P0-2：应取 X-Forwarded-For **末段**（反代追加的真实 IP），不取可伪造的首段"""
    from src.api.auth import client_ip

    # 客户端伪造首段 + Caddy 追加真实 IP（末段）→ 必须返回末段
    assert client_ip(_make_request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})) == "203.0.113.9"
    # 单值（Caddy header_up 覆盖模式）也正确
    assert client_ip(_make_request({"X-Forwarded-For": "203.0.113.9"})) == "203.0.113.9"
    # 无 XFF → 回退直连 client.host
    assert client_ip(_make_request({}, client=("10.0.0.5", 5555))) == "10.0.0.5"


def test_spoofed_xff_first_segment_cannot_bypass_rate_limit(client, monkeypatch):
    """P0-2 端到端：每次换伪造首段也无法绕过限流（末段相同 → key 不变 → 仍 429）"""
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MIN", "2")
    from src.api import auth

    auth._AGENT_HITS.clear()

    # 前 2 次成功：首段每次换（伪造），末段固定 = 真实身份
    for i in range(2):
        r = client.get(
            "/api/agent/sessions",
            headers={"X-Anon-User-Id": "anon-X", "X-Forwarded-For": f"1.2.3.{i}, 203.0.113.9"},
        )
        assert r.status_code == 200, f"req {i + 1}: {r.status_code}"

    # 第 3 次换一个伪造首段：末段未变，应仍被限流拦下
    r = client.get(
        "/api/agent/sessions",
        headers={"X-Anon-User-Id": "anon-X", "X-Forwarded-For": "9.9.9.9, 203.0.113.9"},
    )
    assert r.status_code == 429, "换伪造首段不应绕过限流"

    auth._AGENT_HITS.clear()


# ==================== 6. 成本熔断（P0-4 · 2026-09-11）====================


def _reset_daily_quota():
    """把 auth 的日额度计数复位（隔离用例间状态）"""
    from src.api import auth

    auth._AGENT_COST_DAY = ""
    auth._AGENT_DAILY_TOTAL_USED = 0
    auth._AGENT_DAILY_IP_USED.clear()


def test_agent_daily_quota_unit_and_rollover(monkeypatch):
    """P0-4 单测：日额度用尽 → 429；跨自然日自动重置；snapshot 只读"""
    from fastapi import HTTPException

    from src.api import auth

    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "1")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "0")
    _reset_daily_quota()
    try:
        auth.check_agent_daily_quota("1.1.1.1")  # 用掉唯一额度
        with pytest.raises(HTTPException) as ei:
            auth.check_agent_daily_quota("1.1.1.1")
        assert ei.value.status_code == 429
        assert "额度已用尽" in ei.value.detail

        # 模拟跨日：把计数日期改成过去 → 下一次调用应重置
        auth._AGENT_COST_DAY = "2000-01-01"
        auth.check_agent_daily_quota("1.1.1.1")
        snap = auth.agent_usage_snapshot()
        assert snap["total_used"] == 1
        assert snap["total_limit"] == 1
    finally:
        _reset_daily_quota()


def test_agent_daily_quota_per_ip_429(client, monkeypatch):
    """P0-4 端到端：单 IP 日额度用尽后 /chat 直接 429（连流都不开）"""
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "0")  # 关全局，只验单 IP
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "1")
    _reset_daily_quota()

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-q"})
    sid = r.json()["data"]["id"]
    hdr = {"X-Anon-User-Id": "anon-q"}

    r1 = client.post("/api/agent/chat", json={"session_id": sid, "user_msg": "第一问"}, headers=hdr)
    assert r1.status_code == 200, r1.text

    r2 = client.post("/api/agent/chat", json={"session_id": sid, "user_msg": "第二问"}, headers=hdr)
    assert r2.status_code == 429
    assert "额度已用尽" in r2.text
    _reset_daily_quota()


def test_agent_daily_quota_global_429(client, monkeypatch):
    """P0-4 端到端：全局日额度用尽 → 429（保护余额的最后一道闸）"""
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "1")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "0")
    _reset_daily_quota()

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-g"})
    sid = r.json()["data"]["id"]

    assert client.post("/api/agent/chat", json={"session_id": sid, "user_msg": "问"},
                       headers={"X-Anon-User-Id": "anon-g"}).status_code == 200
    r2 = client.post("/api/agent/chat", json={"session_id": sid, "user_msg": "再问"},
                     headers={"X-Anon-User-Id": "anon-g"})
    assert r2.status_code == 429
    assert "全站" in r2.text
    _reset_daily_quota()


def test_agent_concurrency_guard_emits_busy(monkeypatch):
    """P0-4：并发槽占满 + 排队超时 → 发 error(busy) 事件，而不是静默挂起"""
    import asyncio

    from src.agent import chat

    monkeypatch.setenv("AGENT_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("AGENT_QUEUE_WAIT_SEC", "0")
    chat._active_streams = 0
    assert chat._try_acquire_slot() is True  # 占满唯一槽位

    async def _collect():
        return [c async for c in chat.stream_chat_guarded(
            user_msg="x", session=None, session_id="s",
        )]

    try:
        chunks = asyncio.run(_collect())
    finally:
        chat._release_slot()
        chat._active_streams = 0

    assert len(chunks) == 1, "并发满时应只发一个事件就结束"
    assert "event: error" in chunks[0]
    assert "busy" in chunks[0]


def test_llm_config_respects_max_tokens(monkeypatch):
    """P0-4：真正传给 LLM 的 max_tokens 走 AGENT_MAX_TOKENS，默认 2048"""
    import asyncio

    from src.agent import chat

    captured: dict = {}

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return iter([])  # 空流：只验参数，不验内容

    class _FakeClient:
        class chat:  # noqa: N801 - 模拟 openai 客户端结构
            completions = _FakeCompletions()

    monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
    monkeypatch.setenv("AGENT_MAX_TOKENS", "1234")

    async def _call():
        return await chat._stream_llm_round(
            _FakeClient(), "test-model", [], loop=asyncio.get_running_loop(),
        )

    asyncio.run(_call())
    assert captured["max_tokens"] == 1234
    assert captured["stream"] is True

    # 默认值：2048
    monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
    asyncio.run(_call())
    assert captured["max_tokens"] == 2048


# ==================== 7. 请求体上限（P0-5 · 2026-09-11）====================


def test_chat_body_length_limits(client):
    """P0-5：超长 user_msg / 超量 history / 超长历史内容一律 422；边界内放行"""
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-l"})
    sid = r.json()["data"]["id"]
    hdr = {"X-Anon-User-Id": "anon-l"}

    # user_msg 超过 4000 → 422
    assert client.post("/api/agent/chat", headers=hdr,
                       json={"session_id": sid, "user_msg": "x" * 4001}).status_code == 422

    # history 超过 50 条 → 422
    hist = [{"role": "user", "content": "hi"} for _ in range(51)]
    assert client.post("/api/agent/chat", headers=hdr,
                       json={"session_id": sid, "user_msg": "hi",
                             "history": hist}).status_code == 422

    # 单条历史内容超过 20000 → 422
    assert client.post("/api/agent/chat", headers=hdr,
                       json={"session_id": sid, "user_msg": "hi",
                             "history": [{"role": "assistant", "content": "y" * 20001}]}
                       ).status_code == 422

    # 边界内（正好 4000）应放行
    assert client.post("/api/agent/chat", headers=hdr,
                       json={"session_id": sid, "user_msg": "x" * 4000}).status_code == 200
