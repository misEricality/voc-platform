"""全局测试夹具（2026-09-11）

**为什么需要这个文件**：P1 给应用加了访问审计中间件（`src/api/access_log.py`），
默认开启（`ACCESS_LOG_ENABLED=1`）。而 pytest 里每个 `TestClient(app)` 上下文都会
跑一次 lifespan（`access_log.start()` 建表 + `stop()` flush）—— 不拦的话，整套测试会在
**真实的 `data/access_log.db`** 上建表写行，违反 AGENTS.md §3「测试禁止污染生产数据」。

故此处统一默认关闭审计；`tests/test_p1_hardening.py::audit_db` 夹具再按需打开，
并把它指向 `tmp_path`。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_access_audit(monkeypatch):
    """默认关闭访问审计（需要验证审计的用例自行 monkeypatch 打开）"""
    monkeypatch.setenv("ACCESS_LOG_ENABLED", "0")
