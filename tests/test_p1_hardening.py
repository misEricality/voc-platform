"""P1 内测加固单测（2026-09-11）：访问审计日志 / 公网模式启动自检 / B站评论对外脱敏

对应 docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md §5.5.3 的四条 P1。
这里覆盖"阈值定不下来"时最需要保证的三件事：
1. 审计**不能拖垮业务** —— 落库失败必须吞掉、返回 0；
2. 审计**不记错东西** —— 静态资源不记、XFF 只信末段、崩溃请求也要留痕；
3. 脱敏**不可逆且在对外那一层** —— 库里原文不变，出网的看不到昵称/mid。
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from src.api import access_log, service


# ---------------------------------------------------------------- 夹具

@pytest.fixture
def audit_db(monkeypatch, tmp_path):
    """把审计库指到临时文件并清空全局缓冲（模块级 deque 会跨测试残留）"""
    monkeypatch.setenv("ACCESS_LOG_ENABLED", "1")
    monkeypatch.setenv("ACCESS_LOG_DB", str(tmp_path / "access_log.db"))
    access_log._BUFFER.clear()
    yield tmp_path / "access_log.db"
    access_log._BUFFER.clear()


def _rows(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT ts, ip, method, path, status, anon, duration_ms FROM access_log ORDER BY id"
        ).fetchall()


def _drive(mw, path, *, headers=(), method="GET"):
    """手动驱动 ASGI 中间件（不引入 pytest-asyncio / TestClient 依赖）"""
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers),
        "client": ("9.9.9.9", 4321),
        "query_string": b"",
    }
    asyncio.run(mw(scope, receive, send))
    return sent


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


# ---------------------------------------------------------------- 1. 缓冲与落库

def test_disabled_records_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_LOG_ENABLED", "0")
    monkeypatch.setenv("ACCESS_LOG_DB", str(tmp_path / "off.db"))
    access_log._BUFFER.clear()
    access_log.record(ip="1.1.1.1", method="GET", path="/api/overview", status=200)
    assert len(access_log._BUFFER) == 0
    assert access_log.flush() == 0
    assert not (tmp_path / "off.db").exists()


def test_flush_writes_all_columns(audit_db):
    access_log.record(ip="1.2.3.4", method="GET", path="/api/overview", status=200, duration_ms=12)
    access_log.record(ip="5.6.7.8", method="POST", path="/api/agent/chat", status=200,
                      anon="a" * 80, duration_ms=2600)
    assert access_log.flush() == 2
    assert access_log.flush() == 0  # 缓冲已清空，幂等
    rows = _rows(audit_db)
    assert len(rows) == 2
    assert rows[0][1] == "1.2.3.4" and rows[0][3] == "/api/overview" and rows[0][4] == 200
    assert rows[1][2] == "POST" and rows[1][3] == "/api/agent/chat"
    assert len(rows[1][5]) == 64  # anon 截断，防超长 header 灌进库里


def test_flush_failure_is_swallowed(monkeypatch, tmp_path):
    """审计库不可写时：不抛异常、缓冲清空（不无限堆积内存）"""
    monkeypatch.setenv("ACCESS_LOG_ENABLED", "1")
    # 指向一个"目录"，sqlite 打不开 → 必然失败
    monkeypatch.setenv("ACCESS_LOG_DB", str(tmp_path))
    access_log._BUFFER.clear()
    access_log.record(ip="1.1.1.1", method="GET", path="/api/overview", status=200)
    assert access_log.flush() == 0
    assert len(access_log._BUFFER) == 0


def test_prune_drops_old_rows(audit_db):
    access_log.ensure_schema()
    with sqlite3.connect(str(audit_db)) as conn:
        conn.executemany(
            "INSERT INTO access_log (ts, ip, method, path, status) VALUES (?, '1.1.1.1', 'GET', '/api/x', 200)",
            [("2000-01-01T00:00:00+00:00",), ("2999-01-01T00:00:00+00:00",)],
        )
    assert access_log.prune(days=30) == 1
    assert len(_rows(audit_db)) == 1
    assert access_log.prune(days=0) == 0  # ≤0 = 不清理


# ---------------------------------------------------------------- 2. 中间件

def test_middleware_records_api_and_skips_noise(audit_db):
    mw = access_log.AccessAuditMiddleware(_ok_app)
    _drive(mw, "/api/overview", headers=[
        (b"x-forwarded-for", b"1.2.3.4, 5.6.7.8"),  # 首段可伪造 → 只信末段
        (b"x-anon-user-id", b"anon-7"),
    ])
    _drive(mw, "/api/health")                 # 探活不记
    _drive(mw, "/vendor/echarts.min.js")      # 静态资源不记
    _drive(mw, "/src/pages/dashboard.js")
    assert access_log.flush() == 1
    rows = _rows(audit_db)
    assert len(rows) == 1
    _ts, ip, method, path, status, anon, duration_ms = rows[0]
    assert ip == "5.6.7.8"
    assert (method, path, status, anon) == ("GET", "/api/overview", 200, "anon-7")
    assert duration_ms is not None and duration_ms >= 0


def test_middleware_still_logs_when_handler_crashes(audit_db):
    async def boom(scope, receive, send):
        raise RuntimeError("handler exploded")

    with pytest.raises(RuntimeError):
        _drive(access_log.AccessAuditMiddleware(boom), "/api/agent/chat")
    assert access_log.flush() == 1
    row = _rows(audit_db)[0]
    assert row[4] == 0  # 响应头都没发出 → status=0，正是"谁在打崩我"的线索


# ---------------------------------------------------------------- 3. 公网模式自检

def test_public_mode_off_is_noop(monkeypatch):
    from src.api.main import _check_public_mode

    monkeypatch.setenv("PUBLIC_MODE", "0")
    _check_public_mode(admin_hash=None, session_secret=None)  # 本地开发不该被拦


def test_public_mode_rejects_missing_essentials(monkeypatch):
    from src.api.main import _check_public_mode

    monkeypatch.setenv("PUBLIC_MODE", "1")
    for name in ("SESSION_SECRET_KEY", "ADMIN_PASSWORD_HASH", "ENTRY_AUTH_ENFORCED",
                 "AGENT_DAILY_CHAT_LIMIT", "AGENT_DAILY_CHAT_LIMIT_PER_IP"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        _check_public_mode(admin_hash=None, session_secret=None)
    msg = str(excinfo.value)
    assert "SESSION_SECRET_KEY" in msg
    assert "ADMIN_PASSWORD_HASH" in msg
    assert "ENTRY_AUTH_ENFORCED" in msg


def test_public_mode_rejects_unlimited_agent_quota(monkeypatch):
    from src.api.main import _check_public_mode

    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setenv("ENTRY_AUTH_ENFORCED", "1")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "0")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "50")
    with pytest.raises(RuntimeError) as excinfo:
        _check_public_mode(admin_hash="h", session_secret="s")
    assert "AGENT_DAILY_CHAT_LIMIT" in str(excinfo.value)


def test_public_mode_rejects_non_numeric_limit(monkeypatch):
    from src.api.main import _check_public_mode

    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setenv("ENTRY_AUTH_ENFORCED", "1")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "很多")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "50")
    with pytest.raises(RuntimeError):
        _check_public_mode(admin_hash="h", session_secret="s")


def test_public_mode_passes_when_complete(monkeypatch):
    from src.api.main import _check_public_mode

    monkeypatch.setenv("PUBLIC_MODE", "1")
    monkeypatch.setenv("ENTRY_AUTH_ENFORCED", "1")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "300")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "50")
    _check_public_mode(admin_hash="h", session_secret="s")  # 不抛即通过


def _set_public_essentials(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("PUBLIC_MODE", mode)
    monkeypatch.setenv("SESSION_SECRET_KEY", "s")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "h")
    monkeypatch.setenv("ENTRY_AUTH_ENFORCED", "1")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT", "300")
    monkeypatch.setenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", "50")


def test_public_mode_disables_api_docs(monkeypatch):
    """P1-2：公网形态必须关掉 /docs /redoc /openapi.json —— 否则持准入口令者能一次拿到
    全部 admin 端点、参数名与字段约束（VPS 实测带 gate 口令即 200）。"""
    from src.api.main import create_app

    _set_public_essentials(monkeypatch, "1")
    app = create_app(db_url="sqlite:///:memory:")
    assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)


def test_local_mode_keeps_api_docs(monkeypatch):
    """本地 / CI 保持文档可用（调试便利，不能为了公网一刀切）"""
    from src.api.main import create_app

    _set_public_essentials(monkeypatch, "0")
    app = create_app(db_url="sqlite:///:memory:")
    assert (app.docs_url, app.openapi_url) == ("/docs", "/openapi.json")


# ---------------------------------------------------------------- 4. B站脱敏

def _bili_comment(author="某UP主", extra=None):
    return {
        "platform": "bilibili",
        "author": author,
        "author_id": "12345678",
        "content": "这游戏还行",
        "extra": extra if extra is not None else {
            "aid": 1,
            "profile": {"uname": author, "level": 6, "vip": True, "sex": "男",
                        "official": {"type": 0, "desc": "up认证"}},
        },
    }


def test_bilibili_author_is_pseudonymised():
    src = _bili_comment()
    out = service._public_comment(src)
    assert out["author"].startswith("B站用户")
    assert "某UP主" not in out["author"]
    assert src["author"] == "某UP主"  # 库里/内存里的原文不动，只脱对外这一层


def test_bilibili_profile_pii_removed_but_buckets_kept():
    out = service._public_comment(_bili_comment())
    profile = out["extra"]["profile"]
    assert "uname" not in profile and "official" not in profile
    assert profile["level"] == 6 and profile["vip"] is True and profile["sex"] == "男"


def test_bilibili_pseudonym_is_stable():
    first = service._public_comment(_bili_comment(author="同名的人"))["author"]
    second = service._public_comment(_bili_comment(author="同名的人"))["author"]
    other = service._public_comment(_bili_comment(author="另一个人"))["author"]
    assert first == second  # 同一昵称 → 同一伪名（跨页可辨认同一账号）
    assert first != other


def test_steam_and_empty_extra_untouched():
    steam = {"platform": "steam", "author": "76561197960287930", "extra": {}}
    assert service._public_comment(steam) is steam  # steamid 本就是匿名 ID，不做处理
    no_extra = {"platform": "bilibili", "author": "某UP主", "extra": None}
    assert service._public_comment(no_extra)["author"].startswith("B站用户")


# ---------------------------------------------------------------- 5. 展示端形态（不收口就会被白采）

def test_display_only_refuses_pipeline(monkeypatch):
    """展示端不得采集/标注：run_pipeline 在进入采集器之前就拒绝

    这条守卫的价值是**双重**的：既不让 VPS 白跑采集（结果次日被整库覆盖），
    也不让它在没有生产标注器 Key 的情况下用错分析器写脏 `analyzer_version`。
    """
    from src.pipeline import run_pipeline

    monkeypatch.setenv("DISPLAY_ONLY", "1")
    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline(platform="steam", target_id="292030", skip_analysis=True)
    assert "展示端" in str(excinfo.value)


def test_display_only_defaults_to_public_mode(monkeypatch):
    """默认跟随 PUBLIC_MODE（公网形态无需额外记开关），显式 DISPLAY_ONLY 可覆盖"""
    from src.runtime_mode import display_only

    monkeypatch.setenv("PUBLIC_MODE", "0")
    monkeypatch.delenv("DISPLAY_ONLY", raising=False)
    assert display_only() is False           # 本地开发照常采集

    monkeypatch.setenv("PUBLIC_MODE", "1")
    assert display_only() is True            # 公网形态 = 展示端（默认收口，不用记）

    monkeypatch.setenv("DISPLAY_ONLY", "0")
    assert display_only() is False           # 显式覆盖：确需一台可写的公网实例


def test_web_app_logging_is_configured(monkeypatch):
    """`LOG_LEVEL` 不能是空转的（2026-09-11 实测线上一条 `voc.api` INFO 都没有）

    uvicorn 只给 `uvicorn.*` 配 handler 且 `propagate=False`，root logger 一直没 handler →
    应用自己的 `log.info` 被 lastResort（只收 WARNING+）静默丢弃。这里锁住「create_app 会
    按 LOG_LEVEL 配置 root 日志」，避免以后又悄悄丢掉全部应用侧诊断（含展示端形态声明）。
    """
    import logging

    from src.api.main import create_app

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    root.handlers.clear()   # 模拟"这个进程还没人配过日志"的启动状态
    try:
        create_app(db_url="sqlite:///:memory:")
        assert root.handlers, "create_app 没有配置 root logger（LOG_LEVEL 会空转）"
        assert root.level == logging.INFO
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
