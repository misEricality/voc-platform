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


def client_ip(request: Request) -> str:
    """取客户端 IP（Caddy 反代下读 X-Forwarded-For 首段；否则直连 client.host）"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
