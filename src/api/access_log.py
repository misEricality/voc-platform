"""访问审计日志（P1 内测加固 · 2026-09-11）

**为什么单独一个 DB 文件**（§5.5.3 原计划写进主库 `access_log` 表）：
主库 `data/voc.db` 每天 02:00 被本机快照**整库覆盖**到 VPS（§0.5 变体 ①b，
`scripts/ops/push_db_to_vps.ps1` 走 `sqlite3.Connection.backup()` 逐页重写）。
审计表若放主库 → 每天被清空，等于没有审计；且审计行还会把每晚 scp 的库撑大。
故单开 `data/access_log.db`（env `ACCESS_LOG_DB` 可覆盖），只存在于运行时节点本地。

**写放大保护**：请求路径上只往内存 deque 追加（无 IO、无锁竞争）；后台任务每
`ACCESS_LOG_FLUSH_SEC`（默认 5s）批量落库一次，进程退出前强制 flush。
**落库失败即丢弃**（不重排缓冲）：审计是观测设施，任何情况下都不该拖垮业务请求，
也不该在 DB 故障时把内存吃光。

表 `access_log`：ts / ip / method / path / status / anon / duration_ms
- `ip` 取 XFF **末段**（与 `auth.client_ip` 同口径，复用同一实现，避免两处逻辑漂移）；
- 原计划 5 列之外多了 `method` / `duration_ms`：method 用于把 SSE 对话
  （`POST /api/agent/chat`）与同路径其它动词分开；duration_ms 是"阈值定不下来"时
  唯一能回答"谁在被刷"的输入。两者都不含个人信息；
- **不记 query string / body**：`/api/agent/chat` 的 body 是用户提问原文，
  属体验数据而非审计必需；静态资源（`/src` `/vendor` `/covers`）不入库，信噪比太低；
- Caddy 层 basic_auth 拒绝的 401 **不经过应用** → 由 fail2ban 读 Caddy JSON 日志覆盖（§7A-2）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("voc.api.access_log")

ROOT = Path(__file__).resolve().parent.parent.parent

_DDL = """
CREATE TABLE IF NOT EXISTS access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    ip          TEXT,
    method      TEXT,
    path        TEXT,
    status      INTEGER,
    anon        TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS ix_access_ts ON access_log (ts);
CREATE INDEX IF NOT EXISTS ix_access_ip_ts ON access_log (ip, ts);
"""

# 静态资源与探活不入库：一个 SPA 首屏会打 15+ 条 /src/*，噪音远大于信息量
SKIP_PREFIXES = ("/vendor/", "/src/", "/covers/")
SKIP_EXACT = {"/api/health", "/favicon.ico"}

_BUFFER: deque[tuple] = deque(maxlen=20000)
_FLUSH_LOCK = threading.Lock()
_TASK: "asyncio.Task | None" = None


# ---------------------------------------------------------------- 配置

def enabled() -> bool:
    """审计开关（`ACCESS_LOG_ENABLED=0` 关闭；测试里默认关掉）"""
    return os.getenv("ACCESS_LOG_ENABLED", "1") == "1"


def db_file() -> str:
    return os.getenv("ACCESS_LOG_DB") or str(ROOT / "data" / "access_log.db")


def retention_days() -> int:
    try:
        return int(os.getenv("ACCESS_LOG_RETENTION_DAYS", "30"))
    except ValueError:
        return 30


def _flush_sec() -> float:
    try:
        return max(1.0, float(os.getenv("ACCESS_LOG_FLUSH_SEC", "5")))
    except ValueError:
        return 5.0


def _connect() -> sqlite3.Connection:
    path = db_file()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(_DDL)
    return conn


# ---------------------------------------------------------------- 写入

def ensure_schema() -> None:
    """建表（缓冲为空时 `flush()` 会提前返回，故单独提供）"""
    try:
        _connect().close()
    except Exception as exc:  # noqa: BLE001 —— 审计不可影响启动
        log.warning("审计日志建表失败：%s", exc)


def record(
    *,
    ip: str | None,
    method: str,
    path: str,
    status: int,
    anon: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """请求路径调用：只入内存缓冲，绝不阻塞事件循环"""
    if not enabled():
        return
    _BUFFER.append((
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ip,
        method,
        path,
        status,
        anon[:64] if anon else None,
        duration_ms,
    ))


def flush() -> int:
    """同步批量落库（后台任务 / 退出钩子 / 测试调用）；返回写入条数"""
    with _FLUSH_LOCK:
        if not _BUFFER:
            return 0
        rows = list(_BUFFER)
        _BUFFER.clear()
    try:
        conn = _connect()
        try:
            conn.executemany(
                "INSERT INTO access_log (ts, ip, method, path, status, anon, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("审计日志写入失败，丢弃 %d 条：%s", len(rows), exc)
        return 0
    return len(rows)


def prune(days: int | None = None) -> int:
    """删除早于保留期的行（默认 30 天，`<=0` 表示不清理）；返回删除条数"""
    keep = retention_days() if days is None else days
    if keep <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep)).isoformat(timespec="seconds")
    try:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM access_log WHERE ts < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("审计日志清理失败：%s", exc)
        return 0


# ---------------------------------------------------------------- 生命周期

async def _flusher() -> None:
    """后台批次落库 + 每 6h 清理过期行"""
    interval = _flush_sec()
    ticks_per_prune = max(1, int(6 * 3600 / interval))
    ticks = 0
    while True:
        await asyncio.sleep(interval)
        if _BUFFER:
            await asyncio.to_thread(flush)
        ticks += 1
        if ticks % ticks_per_prune == 0:
            await asyncio.to_thread(prune)


async def start() -> None:
    """lifespan 启动：建表 + 首次清理 + 起后台任务"""
    global _TASK
    if not enabled():
        log.info("访问审计未启用（ACCESS_LOG_ENABLED=0）")
        return
    await asyncio.to_thread(ensure_schema)
    removed = await asyncio.to_thread(prune)
    log.info("访问审计已启用：%s（保留 %d 天，本次清理 %d 行）",
             db_file(), retention_days(), removed)
    _TASK = asyncio.create_task(_flusher())


async def stop() -> None:
    """lifespan 退出：停后台任务 + 强制 flush 剩余缓冲"""
    global _TASK
    if _TASK is not None:
        _TASK.cancel()
        try:
            await _TASK
        except asyncio.CancelledError:
            pass
        _TASK = None
    if enabled():
        await asyncio.to_thread(flush)


# ---------------------------------------------------------------- 中间件

def _header(scope: dict, name: bytes) -> str | None:
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            return v.decode("latin-1", errors="replace")[:64]
    return None


class AccessAuditMiddleware:
    """纯 ASGI 中间件：记录非静态请求的 ip / method / path / status / anon / 耗时

    刻意**不用** `@app.middleware("http")`（BaseHTTPMiddleware）：它会给响应再套一层
    task + 队流转发，而 `/api/agent/chat` 是 SSE 流式端点（2026-09-11 实测
    ttfb≈1.2s / total≈2.6s）——少一层转发就少一处"流被缓冲/断流"的风险。
    纯 ASGI 只是包一层 `send`，不改任何消息语义。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if not enabled() or path in SKIP_EXACT or path.startswith(SKIP_PREFIXES):
            return await self.app(scope, receive, send)

        # 延迟导入：避免 access_log ←→ auth 的导入环（auth 未来可能引用本模块记账）
        from starlette.requests import Request

        from src.api.auth import client_ip

        started = time.perf_counter()
        status = 0

        async def _send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            # 客户端中途断开（SSE abort）也会走到这里；status=0 = 响应头都没发出
            record(
                ip=client_ip(Request(scope)),
                method=scope.get("method", ""),
                path=path,
                status=status,
                anon=_header(scope, b"x-anon-user-id"),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
