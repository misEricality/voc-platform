"""认证与依赖注入（管理员鉴权 + DB session + 登录限流）

鉴权设计（WEB_DASHBOARD.md §4.2）：
- 单管理员：ADMIN_PASSWORD_HASH 环境变量，格式 pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>
  生成工具：scripts/ops/hash_admin_password.py
- Session：starlette SessionMiddleware 签名 cookie（SECRET_KEY = SESSION_SECRET_KEY）
- 保护范围：/api/admin/* 全部；/api/auth/* 与公开端点豁免
- 登录限流（对抗审查 P2#9）：按客户端 IP 计失败次数，5 次/5 分钟窗口，超出 429
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

SESSION_ADMIN_KEY = "admin"

# ---------- 登录限流（in-memory，单进程；uvicorn 单 worker 够用） ----------

_LOGIN_FAILURES: dict[str, deque] = defaultdict(deque)
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW = 300  # 5 分钟窗口

# ---------- 通用 IP 限流（2026-09-09 原声分析 Agent · 决策 #15）----------

_AGENT_HITS: dict[str, deque] = defaultdict(deque)
_AGENT_WINDOW = 60  # 60 秒窗口
_AGENT_MAX_PER_MIN_DEFAULT = 60  # 默认值；env 覆盖时每请求读 env（支持热重载）
_AGENT_HITS_MAX_KEYS = 50_000  # 最多追踪的 IP 数（防伪造 XFF 撑爆内存，见对抗审查 P0#2）


def client_ip(request: Request) -> str:
    """取客户端 IP（Caddy 反代下读 X-Forwarded-For 首段；否则直连 client.host）"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _agent_limit() -> int:
    """每请求读 env（支持测试 monkeypatch / 运行时调阈值）"""
    return int(os.getenv("AGENT_RATE_LIMIT_PER_MIN", str(_AGENT_MAX_PER_MIN_DEFAULT)))


def check_agent_rate(ip: str) -> None:
    """通用 IP 频率限制：60 req/min（默认），超阈值则 429

    用法：在 agent 端点 handler 顶部调用 `check_agent_rate(client_ip(request))`
    关闭：设 AGENT_RATE_LIMIT_PER_MIN=0（或负数）
    """
    limit = _agent_limit()
    if limit <= 0:
        return  # 显式关闭（测试 / 本地开发）

    now = time.monotonic()
    dq = _AGENT_HITS.get(ip, deque())
    while dq and now - dq[0] > _AGENT_WINDOW:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(
            429, f"请求过于频繁（>{limit} req/{_AGENT_WINDOW}s/IP），请稍后再试"
        )
    dq.append(now)
    _AGENT_HITS[ip] = dq

    # 容量保护（2026-09-09 对抗审查 P0#2）：伪造 X-Forwarded-For 每个请求换一个 IP，
    # 会让 _AGENT_HITS 无限增长（过期 key 的 deque 虽空但 key 仍驻留 dict）。
    # 超过上限时先淘汰"窗口内已无请求"的 key；仍超则清空（宁可短暂放行也不 OOM）。
    if len(_AGENT_HITS) > _AGENT_HITS_MAX_KEYS:
        stale = [
            k for k, v in _AGENT_HITS.items()
            if not v or now - v[-1] > _AGENT_WINDOW
        ]
        for k in stale:
            _AGENT_HITS.pop(k, None)
        if len(_AGENT_HITS) > _AGENT_HITS_MAX_KEYS:
            _AGENT_HITS.clear()


def check_login_rate(ip: str) -> None:
    """登录前检查：窗口内失败次数超阈值则 429"""
    now = time.monotonic()
    dq = _LOGIN_FAILURES.get(ip, deque())
    while dq and now - dq[0] > _LOGIN_WINDOW:
        dq.popleft()
    if len(dq) >= _LOGIN_MAX_FAILS:
        raise HTTPException(
            429, f"登录失败次数过多，请 {_LOGIN_WINDOW // 60} 分钟后再试"
        )
    _LOGIN_FAILURES[ip] = dq  # 确保 deque 已注册


def record_login_failure(ip: str) -> None:
    """记录一次失败登录（成功后由 clear_login_failures 清零）"""
    now = time.monotonic()
    dq = _LOGIN_FAILURES.get(ip, deque())
    while dq and now - dq[0] > _LOGIN_WINDOW:
        dq.popleft()
    dq.append(now)
    _LOGIN_FAILURES[ip] = dq


def clear_login_failures(ip: str) -> None:
    """登录成功后清空该 IP 的失败计数"""
    _LOGIN_FAILURES.pop(ip, None)


# ---------- 密码哈希（stdlib，无额外依赖） ----------

def hash_password(password: str, *, iterations: int = 240_000) -> str:
    """生成 ADMIN_PASSWORD_HASH 格式的哈希（pbkdf2_sha256$iter$salt_hex$hash_hex）"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """校验密码 vs ADMIN_PASSWORD_HASH；未配置时一律拒绝（fail-closed）"""
    if not stored:
        return False
    try:
        algo, iters, salt_hex, hash_hex = stored.strip().split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def admin_configured() -> bool:
    """管理员是否已配置（未配置时登录端点提示部署步骤）"""
    return bool(os.getenv("ADMIN_PASSWORD_HASH"))


# ---------- 依赖 ----------

def require_admin(request: Request) -> None:
    """保护 /api/admin/*：未登录抛 401"""
    if not request.session.get(SESSION_ADMIN_KEY):
        raise HTTPException(status_code=401, detail="未登录或会话过期")


def get_session(request: Request) -> Session:
    """从 app.state 取 DB session（请求级，结束自动关闭）"""
    SessionLocal = request.app.state.SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
