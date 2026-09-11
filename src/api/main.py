"""FastAPI 应用工厂

用法：
    # 开发
    uvicorn src.api.main:app --reload --port 8000
    # 生产（VPS）
    uvicorn src.api.main:app --host 127.0.0.1 --port 8000   # Caddy 反代到 443
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from src.api import access_log
from src.api.routers import admin_router, auth_router, public_router
from src.api.routers_agent import agent_router
from src.runtime_mode import display_only
from src.storage.db import init_db

log = logging.getLogger("voc.api")

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = ROOT / "product" / "web"


def _is_public_mode() -> bool:
    """公网形态判定（`PUBLIC_MODE=1`）。`.env` 已由 create_app 开头加载。"""
    return os.getenv("PUBLIC_MODE", "0") == "1"


def _check_public_mode(*, admin_hash: str | None, session_secret: str | None) -> None:
    """`PUBLIC_MODE=1` 启动自检（P1 · 2026-09-11）：任何一项缺失都**拒绝启动**

    设计原则是 **fail-closed**：公网形态下宁可起不来（systemd 反复重启 + 日志里
    一句人话），也不要"看着在跑、其实全裸"。逐项对应 §5.5.3 的 P1 清单：

    - `SESSION_SECRET_KEY`：缺失 → 存在本地兜底常量 → 任何人可伪造 admin cookie；
    - `ADMIN_PASSWORD_HASH`：缺失 → admin 面（采集任务 CRUD）无口令开放；
    - `AGENT_DAILY_CHAT_LIMIT` / `AGENT_DAILY_CHAT_LIMIT_PER_IP`：为 0（不限额）
      → 一个脚本就能烧干 DeepSeek 余额（本站唯一按量付费的外部依赖）；
    - `ENTRY_AUTH_ENFORCED=1`：**全站准入口令**（Caddy `basic_auth`）在应用进程之外，
      应用无法自证已开启 —— 故要求操作者显式声明，避免"以为锁了其实没锁"。

    只告警不拦截的两项：`COOKIE_SECURE`（备案前是明文 `:8443`，强设会让 admin
    登不上）、`ACCESS_LOG_ENABLED`（关掉只少观测，不构成安全漏洞）。
    """
    if not _is_public_mode():
        return

    problems: list[str] = []
    if not session_secret:
        problems.append("SESSION_SECRET_KEY 未配置（admin cookie 可被伪造）")
    if not admin_hash:
        problems.append("ADMIN_PASSWORD_HASH 未配置（admin 面无口令）")

    def _limit(name: str, default: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            return int(raw)
        except ValueError:
            problems.append(f"{name} 不是合法整数：{raw!r}")
            return default

    if _limit("AGENT_DAILY_CHAT_LIMIT", 300) <= 0:
        problems.append("AGENT_DAILY_CHAT_LIMIT ≤ 0（Agent 日总量不设上限 = 余额可被刷干）")
    if _limit("AGENT_DAILY_CHAT_LIMIT_PER_IP", 50) <= 0:
        problems.append("AGENT_DAILY_CHAT_LIMIT_PER_IP ≤ 0（单 IP 不设上限）")
    if os.getenv("ENTRY_AUTH_ENFORCED", "0") != "1":
        problems.append("ENTRY_AUTH_ENFORCED≠1（未声明上游已启用全站准入口令）")

    if problems:
        raise RuntimeError(
            "PUBLIC_MODE=1 启动自检未通过，拒绝启动：\n  - "
            + "\n  - ".join(problems)
            + "\n（逐项理由见 docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md §5.5.3）"
        )

    if os.getenv("COOKIE_SECURE", "0") != "1":
        log.warning("PUBLIC_MODE=1 但 COOKIE_SECURE≠1：admin session cookie 会在明文 HTTP 上传输")
    if os.getenv("ACCESS_LOG_ENABLED", "1") != "1":
        log.warning("PUBLIC_MODE=1 但 ACCESS_LOG_ENABLED=0：无访问审计，阈值与溯源都无从谈起")


def _setup_logging() -> None:
    """让应用自身的日志（`voc.*`）真正落地（2026-09-11）

    **背景**：uvicorn 只给 `uvicorn.*` 配了 handler（且 `propagate=False`），root logger
    始终**没有 handler** —— 于是 `src/api/*` 的 `log.info` 全被 Python 的 lastResort
    （只处理 WARNING 及以上）丢掉，只有 warning/error 会经 stderr 落到 `logs/web.log`。
    表现就是「`.env` 里明明写着 `LOG_LEVEL=INFO`，线上却一条应用日志都看不到」，
    排查时只能看到 uvicorn 的访问行。

    用 `basicConfig`（root 已有 handler 时自动 no-op）—— 不与 uvicorn / pytest 抢配置。
    嫌第三方库（如 jieba 的加载日志）吵就把 `LOG_LEVEL` 调成 `WARNING`。
    """
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def create_app(*, db_url: str | None = None) -> FastAPI:
    # .env 加载（与 pipeline 行为一致；不覆盖已有环境变量）
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass

    # 放在 load_dotenv 之后：LOG_LEVEL 可能来自 .env
    _setup_logging()

    # DB 初始化延迟到 lifespan（startup）—— 避免模块导入（含测试 collect）时触碰主库
    # 见对抗审查 P1#2：模块级 app=create_app() 曾在导入即 init_db，污染 CI 的 data/voc.db
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _, SessionLocal = init_db(app.state.db_url)
        app.state.SessionLocal = SessionLocal
        await access_log.start()  # 审计：建表 + 过期清理 + 后台批量落库
        try:
            yield
        finally:
            await access_log.stop()  # 退出前把缓冲里剩下的行落库
            SessionLocal.remove() if hasattr(SessionLocal, "remove") else None

    # P1-2（2026-09-11）：公网形态关掉交互文档 —— /docs、/redoc、/openapi.json 会把全部
    # admin 端点、参数名、字段约束摊给任何持准入口令的人（实测带 gate 口令即 200）。
    # 本地 / CI（PUBLIC_MODE≠1）保持默认，便于调试。
    public = _is_public_mode()
    app = FastAPI(
        title="灵听 · Lynx VoC API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if public else "/docs",
        redoc_url=None if public else "/redoc",
        openapi_url=None if public else "/openapi.json",
    )
    app.state.db_url = db_url  # None 时 lifespan 内 init_db 读 DATABASE_URL env
    app.state.admin_password_hash = os.getenv("ADMIN_PASSWORD_HASH")

    # session cookie（管理员鉴权）—— fail-closed：admin 已配置却缺 secret 视为部署错误
    secret = os.getenv("SESSION_SECRET_KEY")
    if not secret:
        if app.state.admin_password_hash:
            # admin 已启用但 session 密钥缺失 → 公网下可伪造 cookie，拒绝启动
            raise RuntimeError(
                "ADMIN_PASSWORD_HASH 已配置但 SESSION_SECRET_KEY 缺失："
                "生成 python -c \"import secrets;print(secrets.token_hex(32))\" 后写入 .env"
            )
        log.warning("SESSION_SECRET_KEY 未配置（且 admin 未启用）→ 仅本地只读开发兜底；"
                    "启用 admin 前必须配置")
        secret = "insecure-dev-only"  # 仅当 admin 未配置时的本地兜底
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        max_age=7 * 24 * 3600,
        same_site="lax",
        https_only=os.getenv("COOKIE_SECURE", "0") == "1",
    )

    # 访问审计（P1 · 2026-09-11）：纯 ASGI 中间件，不额外加 BaseHTTPMiddleware 层
    app.add_middleware(access_log.AccessAuditMiddleware)

    # 公网模式启动自检（P1）：挂路由之前就判定，缺件直接抛，systemd 日志第一行可见
    _check_public_mode(
        admin_hash=app.state.admin_password_hash,
        session_secret=os.getenv("SESSION_SECRET_KEY"),
    )

    # 展示端形态（2026-09-11「看着能采」陷阱收口）：公网形态默认继承此形态。
    # 刻意在启动日志里留一句 —— 上线后 `journalctl -u voc-web | grep 展示模式` 即可确认
    # 这台到底能不能写/能不能采，不靠人记。
    if display_only():
        log.info(
            "展示模式（DISPLAY_ONLY/PUBLIC_MODE=1）：admin 写操作一律 403、"
            "pipeline 拒绝采集与标注；采集任务请在本地改后推 DB"
        )

    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(agent_router)

    # 缓存策略：HTML/业务 JS 禁缓存；API GET no-store（看板要实时，防浏览器启发式缓存）；
    # echarts 大文件允许缓存 1 天
    @app.middleware("http")
    async def cache_control(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/vendor/"):
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        elif request.method == "GET" and path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.method == "GET":
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get("/api/health")
    def health():
        with app.state.SessionLocal() as s:
            from sqlalchemy import select, func
            from src.storage.db import Comment

            n = s.execute(select(func.count(Comment.id))).scalar() or 0
        return {"ok": True, "comments": n}

    # 封面静态托管（data/covers/，2026-09-04 游戏对比看板；目录即时创建保证可挂载）
    covers_dir = ROOT / "data" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/covers", StaticFiles(directory=covers_dir), name="covers")

    # 前端静态托管（product/web/；目录未建时不挂载，API 仍可用）
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    else:
        log.info(f"前端目录不存在（{WEB_DIR}），仅提供 API")

    return app


app = create_app()

