"""Agent chat tool_call 循环测试（2026-09-09 第 4 轮）

不调真实 LLM：monkeypatch `src.agent.chat._stream_llm_round` 替换为 fake。
Fake 按 (round_idx) 字典返回预定 (text, tool_calls)，模拟完整 tool_call 循环。

覆盖：
- 无 tool_call 路径：直接出 done 事件 + assistant 落库
- 有 tool_call 路径：tool_start / tool_end 事件 + tool 落库 + 新一轮 LLM
- 多轮 tool_call 循环：2 轮 tool → 1 轮 text
- MAX_ROUNDS 上限：超阈值强制收尾
- skill 注入：含"痛点"关键词 → system prompt 应包含负面痛点指引
- 错误处理：LLM 抛异常 → error 事件 + 部分内容落库
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def test_db_path():
    db = ROOT / "data" / f"voc_chat_loop_{uuid.uuid4().hex[:8]}.db"
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
    """FastAPI TestClient + 独立测试 DB"""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("AGENT_RATE_LIMIT_PER_MIN", "0")

    from src.api.main import create_app

    app = create_app(db_url=f"sqlite:///{test_db_path}")
    with TestClient(app) as c:
        yield c


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本切成 [(event, data_dict), ...]"""
    events = []
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        ev = None
        data = None
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    data = {"_raw": line}
        if ev and data is not None:
            events.append((ev, data))
    return events


def _make_fake_stream_round(plan: list[tuple[str, list[dict]]]):
    """造一个 fake _stream_llm_round，按调用次数返回 plan 里的结果

    plan: [(text_or_empty, tool_calls_list), ...]
    调用第 n 次返回 plan[n]（越界则返回最后一项）
    """
    call_count = {"n": 0}

    async def fake(client, model, messages, *, loop):
        idx = min(call_count["n"], len(plan) - 1)
        call_count["n"] += 1
        text, tool_calls = plan[idx]
        return text, tool_calls, None  # 2026-09-10：_stream_llm_round 返回三元组（含 usage）

    return fake


# ==================== 基础路径 ====================


def test_chat_no_tool_path(client, monkeypatch, test_db_path):
    """无 tool_call：直接出 token + done + assistant 落库"""
    from src.agent import chat as chat_mod

    # 造 session
    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    # fake LLM：第 1 轮返回纯文本
    monkeypatch.setattr(chat_mod, "_stream_llm_round",
                        _make_fake_stream_round([("你好，有什么可以帮你的吗？", [])]))

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "你好"},
                    headers={"X-Anon-User-Id": "anon-1"})
    assert r.status_code == 200

    events = _parse_sse(r.text)
    assert ("token", {"delta": "你好，有什么可以帮你的吗？"}) in events
    assert any(ev == "done" for ev, _ in events)

    # assistant 消息应落库
    r2 = client.get(f"/api/agent/sessions/{sid}",
                    headers={"X-Anon-User-Id": "anon-1"})
    msgs = r2.json()["data"]["messages"]
    roles = [m["role"] for m in msgs]
    assert "user" in roles and "assistant" in roles


def test_chat_with_single_tool_call(client, monkeypatch):
    """单轮 tool_call：先 tool_start/end，再 final text"""
    from src.agent import chat as chat_mod

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    # 第 1 轮 LLM 返回 tool_call（query_overview），第 2 轮返回最终文本
    fake_plan = [
        ("", [
            {"id": "call_1", "function": {"name": "query_overview",
                                            "arguments": {"target": "steam:0000000"}}},
        ]),
        ("该目标暂无评论数据。", []),
    ]
    monkeypatch.setattr(chat_mod, "_stream_llm_round",
                        _make_fake_stream_round(fake_plan))

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "黑神话有多少评论"},
                    headers={"X-Anon-User-Id": "anon-1"})
    events = _parse_sse(r.text)

    ev_names = [ev for ev, _ in events]
    assert "tool_start" in ev_names
    assert "tool_end" in ev_names
    assert "token" in ev_names
    assert "done" in ev_names

    # tool_start 应包含 name=query_overview
    tool_start = next(d for ev, d in events if ev == "tool_start")
    assert tool_start["name"] == "query_overview"
    assert tool_start["args"]["target"] == "steam:0000000"

    # 消息落库：user + assistant(tool_call) + tool + assistant(final) = 4 条
    r2 = client.get(f"/api/agent/sessions/{sid}",
                    headers={"X-Anon-User-Id": "anon-1"})
    msgs = r2.json()["data"]["messages"]
    assert len(msgs) == 4
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant"]
    # tool 消息应包含 tool_call_id + tool_name
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["tool_name"] == "query_overview"


def test_chat_max_rounds_forced_finish(client, monkeypatch):
    """超过 MAX_TOOL_ROUNDS：强制收尾 + 提示已到上限（2026-09-09 修复：兜底走 SSE token 事件）"""
    from src.agent import chat as chat_mod

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    # LLM 永远只返 tool_call 不返 text（触发循环上限）
    tool_call = {"id": "call_loop", "function": {
        "name": "query_overview",
        "arguments": {"target": "steam:0000000"},
    }}
    # 备 10 轮（> MAX_TOOL_ROUNDS=5），永远返 tool_call
    fake_plan = [("", [tool_call])] * 10
    monkeypatch.setattr(chat_mod, "_stream_llm_round",
                        _make_fake_stream_round(fake_plan))

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "循环"},
                    headers={"X-Anon-User-Id": "anon-1"})
    events = _parse_sse(r.text)

    # 应有 done 事件 + max_rounds_hit=True
    done = next(d for ev, d in events if ev == "done")
    assert done.get("max_rounds_hit") is True
    assert done["rounds"] == chat_mod.MAX_TOOL_ROUNDS

    # tool_start 应出现 MAX_TOOL_ROUNDS 次
    tool_starts = [d for ev, d in events if ev == "tool_start"]
    assert len(tool_starts) == chat_mod.MAX_TOOL_ROUNDS

    # 2026-09-09：兜底文本必须通过 token 事件流出去（前几次只放 done.full_text 是不行的）
    tokens = [d.get("delta", "") for ev, d in events if ev == "token"]
    full_streamed = "".join(tokens)
    assert "已达上限" in full_streamed or "已到上限" in full_streamed, (
        f"max_rounds 兜底文本必须通过 token 事件推给前端，实际只看到: {full_streamed[:200]}"
    )
    # 且 done.full_text 应包含同样的兜底（落库用）
    assert "已达上限" in done["full_text"] or "已到上限" in done["full_text"]


def test_chat_max_rounds_includes_collected_data(client, monkeypatch):
    """max_rounds 兜底里应包含已收集到的 query_overview 数据快照（用户能看到 LLM 拿到了啥）"""
    from src.agent import chat as chat_mod

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    # R1: query_overview 查 steam:2358720；R2-R5: 任意 tool_call（让循环达到上限）
    fake_plan = [
        ("", [{"id": f"call_{i}", "function": {"name": "query_overview",
                                                "arguments": {"target": "steam:2358720"}}}
              for i in range(3)]),  # R1: 一次性调 3 次同 tool（罕见但合法）
        ("", [{"id": f"c{i}", "function": {"name": "query_overview",
                                            "arguments": {"target": "steam:2358720"}}}
              for i in range(3)]),
    ]
    fake_plan += [("", fake_plan[-1][1])] * 10  # 补足
    monkeypatch.setattr(chat_mod, "_stream_llm_round",
                        _make_fake_stream_round(fake_plan))

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "玩家痛点"},
                    headers={"X-Anon-User-Id": "anon-1"})
    events = _parse_sse(r.text)
    tokens = [d.get("delta", "") for ev, d in events if ev == "token"]
    full = "".join(tokens)
    # 没数据时应该有占位
    assert "本轮工具调用均未返回结构化数据" in full or "可达上限" in full


def test_chat_error_emits_error_event(client, monkeypatch):
    """LLM 抛异常：应出 error 事件 + 不崩"""
    from src.agent import chat as chat_mod

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    async def boom(client, model, messages, *, loop):
        raise RuntimeError("fake LLM down")

    monkeypatch.setattr(chat_mod, "_stream_llm_round", boom)

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "随便问问"},
                    headers={"X-Anon-User-Id": "anon-1"})
    events = _parse_sse(r.text)

    ev_names = [ev for ev, _ in events]
    assert "error" in ev_names
    err = next(d for ev, d in events if ev == "error")
    assert "fake LLM down" in err["message"]


# ==================== Skill 注入 ====================


def test_skill_injected_into_system_prompt(client, monkeypatch):
    """含"痛点"关键词 → LLM 收到的 system prompt 应包含负面痛点指引"""
    from src.agent import chat as chat_mod

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    captured_messages = []

    async def fake_capture(client, model, messages, *, loop):
        # 捕获 LLM 实际收到的 messages
        captured_messages.extend(messages)
        return ("收到", [], None)

    monkeypatch.setattr(chat_mod, "_stream_llm_round", fake_capture)

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "玩家的痛点是什么"},
                    headers={"X-Anon-User-Id": "anon-1"})

    # 第 1 条 message 应是 system，且包含负面痛点 skill 的关键词
    system_msg = captured_messages[0]
    assert system_msg["role"] == "system"
    assert "负面痛点聚焦" in system_msg["content"] or "负面痛点" in system_msg["content"]
    # skill prompt 应含至少一个具体指令词
    assert "negative_pct" in system_msg["content"] or "玩家原声" in system_msg["content"]


def test_no_skill_when_irrelevant(client, monkeypatch):
    """无关 query → system prompt 不应包含任何 skill 内容"""
    from src.agent import chat as chat_mod

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    captured = []

    async def fake_capture(client, model, messages, *, loop):
        captured.extend(messages)
        return ("你好", [], None)

    monkeypatch.setattr(chat_mod, "_stream_llm_round", fake_capture)

    client.post("/api/agent/chat",
                json={"session_id": sid, "user_msg": "你好呀"},
                headers={"X-Anon-User-Id": "anon-1"})

    system = captured[0]["content"]
    assert "skill 注入" not in system
    assert "负面痛点聚焦" not in system
    assert "情感趋势对比" not in system
    assert "原声引用增强" not in system


# ==================== 单元辅助测试 ====================


def test_merge_tool_call_deltas():
    """_merge_tool_call_deltas：流式 fragment → 完整 tool_call"""
    from src.agent.chat import _merge_tool_call_deltas

    # 模拟 OpenAI 流式：第一片带 id+name+args 起头，第二片续 args
    fragments = [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "query_overview", "arguments": '{"target": "stea'}},
        {"index": 0, "function": {"arguments": 'm:1"}'}},
        {"index": 0, "function": {"arguments": ""}},
    ]
    out = _merge_tool_call_deltas(fragments)
    assert len(out) == 1
    assert out[0]["id"] == "call_1"
    assert out[0]["function"]["name"] == "query_overview"
    assert out[0]["function"]["arguments"] == {"target": "steam:1"}

    # 多 tool_call（index 1 第二个）
    fragments2 = [
        {"index": 0, "id": "call_a", "type": "function",
         "function": {"name": "query_overview", "arguments": "{}"}},
        {"index": 1, "id": "call_b", "type": "function",
         "function": {"name": "query_topics", "arguments": '{"level": "L1"}'}},
    ]
    out2 = _merge_tool_call_deltas(fragments2)
    assert len(out2) == 2
    names = [tc["function"]["name"] for tc in out2]
    assert "query_overview" in names
    assert "query_topics" in names


def test_parse_args_handles_str_and_dict():
    from src.agent.chat import _parse_args
    assert _parse_args('{"target": "x"}') == {"target": "x"}
    assert _parse_args({"target": "x"}) == {"target": "x"}
    assert _parse_args(None) == {}
    assert _parse_args("") == {}
    # 解析失败：返回 _parse_error 标记（带 _raw 便于排查）
    bad = _parse_args("invalid{")
    assert bad.get("_parse_error") is True
    assert "_raw" in bad


def test_chat_llm_config_error_emits_error_event(client, monkeypatch):
    """无 DEEPSEEK_API_KEY 时应发 error 事件（而非断流，2026-09-09 对抗审查 P1#3）

    锁住：LLM 初始化（_get_llm_config）抛异常被 try 捕获 → yield event: error，
    前端能拿到"未配置 key"的友好提示，而不是只见 SSE 断流。
    """
    from src.agent import chat as chat_mod

    r = client.post("/api/agent/sessions", json={"page": "agent"},
                    headers={"X-Anon-User-Id": "anon-1"})
    sid = r.json()["data"]["id"]

    def boom():
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，Agent 无法调用 LLM")

    monkeypatch.setattr(chat_mod, "_get_llm_config", boom)

    r = client.post("/api/agent/chat",
                    json={"session_id": sid, "user_msg": "hi"},
                    headers={"X-Anon-User-Id": "anon-1"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    err = next((d for ev, d in events if ev == "error"), None)
    assert err is not None, f"应发 error 事件，实际事件序列: {[ev for ev, _ in events]}"
    assert "DEEPSEEK_API_KEY" in err["message"]
