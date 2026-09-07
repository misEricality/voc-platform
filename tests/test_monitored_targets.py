"""monitored 白名单并集 + 零数据任务可见（2026-09-06 对抗审查回归）

目标 4：admin 新增任务（collect_tasks）必须免改 yaml 即出现在前端目标下拉，
即使 backfill 尚未跑完（total=0）也要可见可选；归档网游（不在 yaml、不在
collect_tasks）依旧不可见。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import src.storage.db as db_module  # noqa: E402


def setup_tmp_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _, SessionLocal = db_module.init_db(db_url=f"sqlite:///{tmp.name}")
    return SessionLocal, tmp.name


def teardown_tmp_db(path):
    import gc
    gc.collect()
    try:
        os.unlink(path)
    except (FileNotFoundError, PermissionError):
        pass


def test_monitored_includes_collect_tasks_without_yaml():
    """admin 新增任务（不在 targets.yaml）→ monitored=True 也可见（total=0）"""
    from src.storage.db import CollectTask
    from src.api import service

    SessionLocal, path = setup_tmp_db()
    try:
        with SessionLocal() as s:
            s.add(CollectTask(platform="steam", target_id="999999", name="新游戏", enabled=1))
            s.commit()

        with SessionLocal() as s:
            rows = service.list_targets_payload(s, "steam", monitored=True)
        ids = {r["target_id"]: r for r in rows}
        assert "steam:999999" in ids, "admin 新增任务必须免改 yaml 即可见"
        assert ids["steam:999999"]["total"] == 0
        assert ids["steam:999999"]["name"] == "新游戏"
    finally:
        teardown_tmp_db(path)


def test_unmonitored_keeps_comments_only_semantics():
    """monitored=False 保持原语义：只返回有评论聚合的目标"""
    from src.storage.db import CollectTask
    from src.api import service

    SessionLocal, path = setup_tmp_db()
    try:
        with SessionLocal() as s:
            s.add(CollectTask(platform="steam", target_id="999999", name="新游戏", enabled=1))
            s.commit()

        with SessionLocal() as s:
            rows = service.list_targets_payload(s, "steam", monitored=False)
        assert "steam:999999" not in {r["target_id"] for r in rows}
    finally:
        teardown_tmp_db(path)


if __name__ == "__main__":
    test_monitored_includes_collect_tasks_without_yaml()
    test_unmonitored_keeps_comments_only_semantics()
    print("[OK] all 2 tests passed")
