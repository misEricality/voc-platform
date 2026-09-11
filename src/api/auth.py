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
from datetime import datetime, timedelta, timezone

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

# ---------- 公开只读端点限流（2026-09-11 P0-3）----------
# /api/wordcloud（jieba + 跨游戏 TF-IDF 重算）、/compare、/trends 等是 CPU 密集端点，
# 2C2G 单机一个循环脚本即可打满 → 统一限流。阈值高于 Agent（读多写少、成本低）。
_PUBLIC_HITS: dict[str, deque] = defaultdict(deque)
_PUBLIC_WINDOW = 60
_PUBLIC_MAX_PER_MIN_DEFAULT = 120  # 默认值；env 覆盖时每请求读 env（支持热重载）
_PUBLIC_HITS_MAX_KEYS = 50_000

# ---------- Agent 成本熔断（P0-4 · 2026-09-11）----------
# 背景：POST /api/agent/chat 单次请求最多跑 MAX_TOOL_ROUNDS=5 轮，每轮都把**全量 messages**
# （含此前所有 tool 返回）重新发给 LLM，token 消耗近似 O(轮数²)。per-IP 每分钟限流只能压住
# 瞬时峰值，压不住"慢速长跑"——30 req/min × 24h ≈ 4.3 万次对话/IP/天，足以烧干余额。
# 故再加**自然日**维度的两层额度：全局（保余额）+ 单 IP（防单点刷爆）。
#
# ⚠️ 计数在进程内（uvicorn 单 worker，与其它限流器一致）：重启即清零。内测期可接受；
#    持久化随 P1「审计日志」一起做（那时按 DB 统计即可，无需另建表）。
_AGENT_COST_DAY: str = ""  # 当前计数所属自然日（YYYY-MM-DD）
_AGENT_DAILY_TOTAL_USED: int = 0  # 全局当日已消耗的 chat 次数
_AGENT_DAILY_IP_USED: dict[str, int] = {}
_AGENT_DAILY_IP_MAX_KEYS = 50_000
_AGENT_DAILY_TOTAL_DEFAULT = 300  # 全局 300 次/日
_AGENT_DAILY_PER_IP_DEFAULT = 50  # 单 IP 50 次/日
_AGENT_TZ_OFFSET_HOURS = 8  # 按 UTC+8 切自然日（部署在中国大陆）


def _today_key() -> str:
    """当日 key（UTC+8 自然日，YYYY-MM-DD）"""
    tz = timezone(timedelta(hours=_AGENT_TZ_OFFSET_HOURS))
    return datetime.now(tz).strftime("%Y-%m-%d")


def _rollover_agent_daily() -> None:
    """跨自然日则重置计数（惰性，无需定时任务）"""
    global _AGENT_COST_DAY, _AGENT_DAILY_TOTAL_USED
    day = _today_key()
    if day != _AGENT_COST_DAY:
        _AGENT_COST_DAY = day
        _AGENT_DAILY_TOTAL_USED = 0
        _AGENT_DAILY_IP_USED.clear()


def _agent_daily_limits() -> tuple[int, int]:
    """(全局日额度, 单 IP 日额度)，每请求读 env 支持热调；<=0 表示该项不限制"""
    total = int(os.getenv("AGENT_DAILY_CHAT_LIMIT", str(_AGENT_DAILY_TOTAL_DEFAULT)))
    per_ip = int(os.getenv("AGENT_DAILY_CHAT_LIMIT_PER_IP", str(_AGENT_DAILY_PER_IP_DEFAULT)))
    return total, per_ip


def check_agent_daily_quota(ip: str) -> None:
    """Agent 成本熔断：全局日额度 + 单 IP 日额度，超限抛 429

    用法：在 **消耗 LLM 的端点**（当前仅 /api/agent/chat）handler 顶部调用。
    关闭：AGENT_DAILY_CHAT_LIMIT=0 且 AGENT_DAILY_CHAT_LIMIT_PER_IP=0
    """
    global _AGENT_DAILY_TOTAL_USED
    total, per_ip = _agent_daily_limits()
    if total <= 0 and per_ip <= 0:
        return  # 显式关闭（测试 / 本地开发）

    _rollover_agent_daily()

    if total > 0 and _AGENT_DAILY_TOTAL_USED >= total:
        raise HTTPException(
            429, f"今日全站 Agent 对话额度已用尽（{total} 次/日），请明天再试"
        )
    if per_ip > 0 and _AGENT_DAILY_IP_USED.get(ip, 0) >= per_ip:
        raise HTTPException(
            429, f"今日该来源的 Agent 对话额度已用尽（{per_ip} 次/日），请明天再试"
        )

    # 计入（保守：请求被受理即扣，失败不退还）
    _AGENT_DAILY_TOTAL_USED += 1
    _AGENT_DAILY_IP_USED[ip] = _AGENT_DAILY_IP_USED.get(ip, 0) + 1
    if len(_AGENT_DAILY_IP_USED) > _AGENT_DAILY_IP_MAX_KEYS:
        _AGENT_DAILY_IP_USED.clear()


def agent_usage_snapshot() -> dict:
    """当日 Agent 用量快照（只读，不消耗额度；供运维/健康检查观测）"""
    _rollover_agent_daily()
    total, per_ip = _agent_daily_limits()
    return {
        "day": _AGENT_COST_DAY,
        "total_used": _AGENT_DAILY_TOTAL_USED,
        "total_limit": total,
        "per_ip_limit": per_ip,
        "distinct_ips": len(_AGENT_DAILY_IP_USED),
    }


def client_ip(request: Request) -> str:
    """取客户端 IP（Caddy 反代下读 X-Forwarded-For **末段**；否则直连 client.host）

    2026-09-11 修复 P0-2（XFF 伪造绕过限流）：
    原实现取 `xff.split(",")[0]`（首段），但反代（Caddy/nginx）默认把真实客户端 IP
    **追加**到 XFF 末尾、并不覆盖客户端自带的头。攻击者只要每次请求带
    `X-Forwarded-For: <随机IP>`，首段就是伪造值 → 限流 key 每次都变 → 全部限流形同虚设。

    取**末段**是可信的：末段是最后一跳反代写入的真实 IP（我们的链路只有 Caddy 一跳）。
    配套要求（见 docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md §7A）：
    Caddy 侧显式 `header_up X-Forwarded-For {remote_host}` 覆盖，双保险。

    ⚠️ 若未来在 Caddy 前再加一跳（如 Cloudflare），Caddy 见到的 remote_host 会变成
    上游代理 IP，末段就不再是真实客户端 —— 届时需改用 `trusted_proxies` 白名单
    （Caddy 端）+ 应用层按可信跳数从右往左取。
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # 末段 = 最后一跳（Caddy）写入的真实 IP；首段可被客户端伪造
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _agent_limit() -> int:
    """每请求读 env（支持测试 monkeypatch / 运行时调阈值）"""
    return int(os.getenv("AGENT_RATE_LIMIT_PER_MIN", str(_AGENT_MAX_PER_MIN_DEFAULT)))


def _public_limit() -> int:
    """每请求读 env（支持测试 monkeypatch / 运行时调阈值）"""
    return int(os.getenv("PUBLIC_RATE_LIMIT_PER_MIN", str(_PUBLIC_MAX_PER_MIN_DEFAULT)))


def _rate_check(
    bucket: dict[str, deque], ip: str, limit: int, window: int, max_keys: int
) -> None:
    """滑动窗口限流内核（单进程 in-memory；uvicorn 单 worker 够用）"""
    if limit <= 0:
        return  # 显式关闭（测试 / 本地开发）
    now = time.monotonic()
    dq = bucket.get(ip, deque())
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(
            429, f"请求过于频繁（>{limit} req/{window}s/IP），请稍后再试"
        )
    dq.append(now)
    bucket[ip] = dq

    # 容量保护（2026-09-09 对抗审查 P0#2）：伪造 X-Forwarded-For 每个请求换一个 IP，
    # 会让 bucket 无限增长（过期 key 的 deque 虽空但 key 仍驻留 dict）。
    # 超过上限时先淘汰"窗口内已无请求"的 key；仍超则清空（宁可短暂放行也不 OOM）。
    if len(bucket) > max_keys:
        stale = [k for k, v in bucket.items() if not v or now - v[-1] > window]
        for k in stale:
            bucket.pop(k, None)
        if len(bucket) > max_keys:
            bucket.clear()


def check_agent_rate(ip: str) -> None:
    """Agent 端点 IP 频率限制：60 req/min（默认），超阈值则 429

    用法：在 agent 端点 handler 顶部调用 `check_agent_rate(client_ip(request))`
    关闭：设 AGENT_RATE_LIMIT_PER_MIN=0（或负数）
    """
    _rate_check(_AGENT_HITS, ip, _agent_limit(), _AGENT_WINDOW, _AGENT_HITS_MAX_KEYS)


def check_public_rate(ip: str) -> None:
    """公开只读端点 IP 频率限制：120 req/min（默认），超阈值则 429

    用法：`check_public_rate(client_ip(request))`，或直接用依赖 `public_rate_limit`。
    关闭：设 PUBLIC_RATE_LIMIT_PER_MIN=0（或负数）
    """
    _rate_check(_PUBLIC_HITS, ip, _public_limit(), _PUBLIC_WINDOW, _PUBLIC_HITS_MAX_KEYS)


def agent_rate_limit(request: Request) -> None:
    """FastAPI 依赖：Agent 端点限流（等价 check_agent_rate(client_ip(request))）"""
    check_agent_rate(client_ip(request))


def public_rate_limit(request: Request) -> None:
    """FastAPI 依赖：公开只读端点限流（挂到 public_router 的 dependencies）"""
    check_public_rate(client_ip(request))


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
