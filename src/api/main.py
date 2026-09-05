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

from src.api.routers import admin_router, auth_router, public_router
from src.storage.db import init_db

log = logging.getLogger("voc.api")

ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = ROOT / "product" / "web"


def create_app(*, db_url: str | None = None) -> FastAPI:
    # .env 加载（与 pipeline 行为一致；不覆盖已有环境变量）
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass

    # DB 初始化延迟到 lifespan（startup）—— 避免模块导入（含测试 collect）时触碰主库
    # 见对抗审查 P1#2：模块级 app=create_app() 曾在导入即 init_db，污染 CI 的 data/voc.db
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _, SessionLocal = init_db(app.state.db_url)
        app.state.SessionLocal = SessionLocal
        try:
            yield
        finally:
            SessionLocal.remove() if hasattr(SessionLocal, "remove") else None

    app = FastAPI(title="灵听 · Lynx VoC API", version="1.0.0", lifespan=lifespan)
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

    app.include_router(public_router)
    app.include_router(auth_router)
    app.include_router(admin_router)

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

