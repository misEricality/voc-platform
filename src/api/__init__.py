"""Web API 服务层（2026-09-01 · Web 实时看板，见 docs/architecture/WEB_DASHBOARD.md）

职责：
- FastAPI 应用工厂 create_app()
- 公开只读端点（访客，无鉴权）+ 管理端点（session 鉴权）
- SQLite WAL 并发读 × cron 每日写

用法：
    uvicorn src.api.main:app --host 127.0.0.1 --port 8000
"""
