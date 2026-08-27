"""verify_release_upload.py 单元测试（mock gh CLI 输出）"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


# ---------- helpers ----------

class _FakeResult:
    """subprocess.run 的最小替身"""
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _gh_view_ok(release_dict: dict) -> _FakeResult:
    return _FakeResult(returncode=0, stdout=json.dumps(release_dict))


def _gh_view_fail(stderr: str = "gh: not found") -> _FakeResult:
    return _FakeResult(returncode=1, stderr=stderr)


# ---------- 用例 1：release 存在 + voc.db asset uploaded + 大小足够 → PASS ----------

def test_verify_passes_when_asset_uploaded_and_big_enough(monkeypatch):
    """标准 happy path：asset uploaded + size > min → ok=True"""
    from scripts.ops.verify_release_upload import verify

    fake_release = {
        "tagName": "voc-daily-2026-08-27",
        "name": "voc-daily-2026-08-27",
        "isDraft": False,
        "assets": [
            {"name": "voc.db", "size": 92_913_664, "state": "uploaded"},
        ],
    }
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _gh_view_ok(fake_release))

    ok, msg = verify("voc-daily-2026-08-27", min_size=1024)
    assert ok is True, msg
    assert "OK" in msg or "ok" in msg.lower()


# ---------- 用例 2：release 存在但 voc.db asset 缺失（assets=[]） → FAIL ----------

def test_verify_fails_when_release_has_no_db_asset(monkeypatch):
    """P6 A1 历史 bug：release 存在但 assets=[] → 校验必须 fail"""
    from scripts.ops.verify_release_upload import verify

    fake_release = {
        "tagName": "voc-daily-2026-08-27",
        "name": "voc-daily-2026-08-27",
        "isDraft": False,
        "assets": [],  # ← P6 静默失败正是这种状态
    }
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _gh_view_ok(fake_release))

    ok, msg = verify("voc-daily-2026-08-27", min_size=1024)
    assert ok is False
    assert "voc.db" in msg or "asset" in msg.lower()


# ---------- 用例 3：asset 存在但 size 过小（thumbnail/损坏上传） → FAIL ----------

def test_verify_fails_when_asset_size_too_small(monkeypatch):
    """上传到一半或返回 thumbnail/损坏文件 → 校验必须 fail"""
    from scripts.ops.verify_release_upload import verify

    fake_release = {
        "tagName": "voc-daily-2026-08-27",
        "name": "voc-daily-2026-08-27",
        "isDraft": False,
        "assets": [
            {"name": "voc.db", "size": 100, "state": "uploaded"},  # ← 100 bytes
        ],
    }
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _gh_view_ok(fake_release))

    ok, msg = verify("voc-daily-2026-08-27", min_size=1024)
    assert ok is False
    assert "100" in msg or "size" in msg.lower()


# ---------- 用例 4：asset 存在但 state != uploaded（uploading 暂态） → FAIL ----------

def test_verify_fails_when_asset_not_uploaded_state(monkeypatch):
    """asset 还在 uploading / pending / failed → 校验必须 fail"""
    from scripts.ops.verify_release_upload import verify

    fake_release = {
        "tagName": "voc-daily-2026-08-27",
        "name": "voc-daily-2026-08-27",
        "isDraft": False,
        "assets": [
            {"name": "voc.db", "size": 92_913_664, "state": "uploading"},
        ],
    }
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _gh_view_ok(fake_release))

    ok, msg = verify("voc-daily-2026-08-27", min_size=1024)
    assert ok is False
    assert "uploading" in msg or "state" in msg.lower()


# ---------- 用例 5：release 不存在 → FAIL ----------

def test_verify_fails_when_release_not_found(monkeypatch):
    """gh release view returncode != 0 → 校验必须 fail"""
    from scripts.ops.verify_release_upload import verify

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: _gh_view_fail("release not found"),
    )

    ok, msg = verify("voc-daily-2099-12-31", min_size=1024)
    assert ok is False
    assert "不存在" in msg or "失败" in msg


# ---------- 用例 6：gh CLI 完全不在 PATH → 友好失败（不抛 FileNotFoundError）----------

def test_verify_handles_missing_gh_cli_gracefully(monkeypatch, caplog):
    """本地无 gh CLI 时不应抛 FileNotFoundError；返回 (False, 友好 message)"""
    import logging

    from scripts.ops.verify_release_upload import verify

    def fake_run(*a, **k):
        raise FileNotFoundError("gh not in PATH")

    # patch 到 verify_release_upload 模块捕到的 subprocess 引用上，
    # 否则 monkeypatch 全局 subprocess.run 不一定影响模块内「import subprocess」后取的 run
    monkeypatch.setattr("scripts.ops.verify_release_upload.subprocess.run", fake_run)

    with caplog.at_level(logging.INFO, logger="voc.verify_release"):
        ok, msg = verify("voc-daily-2026-08-27", min_size=1024)

    assert ok is False
    assert "gh" in msg and "PATH" in msg


# ---------- 用例 7：兼容网页端上传的 voc.db-1 / voc.db-2 后缀 ----------

def test_verify_passes_with_voc_db_suffix(monkeypatch):
    """网页端上传同名文件会被自动加后缀（voc.db-1 / voc.db-2）→ 校验也应通过"""
    from scripts.ops.verify_release_upload import verify

    fake_release = {
        "tagName": "voc-daily-2026-08-27",
        "name": "voc-daily-2026-08-27",
        "isDraft": False,
        "assets": [
            {"name": "voc.db-1", "size": 92_913_664, "state": "uploaded"},
        ],
    }
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _gh_view_ok(fake_release))

    ok, msg = verify("voc-daily-2026-08-27", min_size=1024)
    assert ok is True, msg


# ---------- 用例 8：精确实匹配优先于前缀匹配 ----------

def test_find_db_asset_prefers_exact_match(monkeypatch):
    """voc.db 应优先于 voc.db-1 返回"""
    from scripts.ops.verify_release_upload import find_db_asset

    release = {"assets": [
        {"name": "voc.db-1", "size": 1, "state": "uploaded"},
        {"name": "voc.db", "size": 92_913_664, "state": "uploaded"},
    ]}
    asset = find_db_asset(release)
    assert asset["name"] == "voc.db", "应优先返回精确匹配的 voc.db"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
