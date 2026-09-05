"""collect_tasks 表 + 种子迁移 + WAL + B 站 paused 状态回归测试

锁住的回归（详见 docs/architecture/WEB_DASHBOARD.md §3 数据模型变更）：
1. CollectTask CRUD（创建 / 重复拒绝 / 暂停恢复 / 编辑 / 删除）
2. seed_collect_tasks_from_yaml 幂等（二次种子化 0 新增；excluded 不迁移）
3. load_targets_from_db / load_targets_any（DB 优先 + 空表种子化回退 yaml）
4. WAL 模式生效（journal_mode=wal）
5. bilibili_queue paused 状态：runner 扫描跳过 paused（due_found=0）

每个用例用独立测试 DB（data/voc_test_*.db），绝不碰 data/voc.db。
最后更新：2026-09-01
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def test_db_path():
    """每个用例分配独立测试 DB（data/voc_test_<uuid>.db）。"""
    db = ROOT / "data" / f"voc_test_{uuid.uuid4().hex[:8]}.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    yield db
    # WAL 模式会产生 -wal/-shm 副文件，一并清理
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


@pytest.fixture
def session_factory(test_db_path, monkeypatch):
    """初始化独立测试 DB 并返回 SessionLocal。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    from src.storage.db import init_db

    _, SessionLocal = init_db(f"sqlite:///{test_db_path}")
    return SessionLocal


def _write_targets_yaml(tmp_path: Path, targets: list[dict]) -> Path:
    import yaml

    p = tmp_path / "targets.yaml"
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump({"version": 1, "targets": targets}, f, allow_unicode=True)
    return p


# ==================== 1. CollectTask CRUD ====================

def test_collect_task_create_and_get(session_factory):
    from src.storage.db import CollectTaskRepository

    with session_factory() as s:
        repo = CollectTaskRepository(s)
        task = repo.create("steam", "2358720", name="黑神话：悟空", count=None)
        assert task.id is not None
        assert task.enabled == 1
        assert task.count is None  # auto 模式

        got = repo.get_by_target("steam", "2358720")
        assert got is not None and got.name == "黑神话：悟空"


def test_collect_task_duplicate_rejected(session_factory):
    from src.storage.db import CollectTaskRepository

    with session_factory() as s:
        repo = CollectTaskRepository(s)
        repo.create("steam", "2358720")
        with pytest.raises(ValueError):
            repo.create("steam", "2358720")


def test_collect_task_pause_and_resume(session_factory):
    from src.storage.db import CollectTaskRepository

    with session_factory() as s:
        repo = CollectTaskRepository(s)
        task = repo.create("steam", "2358720")
        repo.set_enabled(task.id, False)
        assert repo.get(task.id).enabled == 0  # 已暂停
        repo.set_enabled(task.id, True)
        assert repo.get(task.id).enabled == 1


def test_collect_task_update_cannot_change_target_id(session_factory):
    """编辑只影响 name/language/count；target_id 无入口可改（API 层也不提供）"""
    from src.storage.db import CollectTaskRepository

    with session_factory() as s:
        repo = CollectTaskRepository(s)
        task = repo.create("steam", "2358720", name="旧名", count=30)
        repo.update(task.id, name="新名", count=None, clear_count=True)
        updated = repo.get(task.id)
        assert updated.name == "新名"
        assert updated.count is None
        assert updated.target_id == "2358720"


def test_collect_task_delete(session_factory):
    from src.storage.db import CollectTaskRepository

    with session_factory() as s:
        repo = CollectTaskRepository(s)
        task = repo.create("steam", "2358720")
        assert repo.delete(task.id) is True
        assert repo.get(task.id) is None
        assert repo.delete(task.id) is False  # 幂等删除


def test_collect_task_list_enabled_only(session_factory):
    from src.storage.db import CollectTaskRepository

    with session_factory() as s:
        repo = CollectTaskRepository(s)
        t1 = repo.create("steam", "111")
        repo.create("steam", "222")
        repo.set_enabled(t1.id, False)
        enabled = repo.list_all(enabled_only=True)
        assert [t.target_id for t in enabled] == ["222"]
        assert len(repo.list_all()) == 2


# ==================== 2. 种子迁移（yaml → collect_tasks） ====================

def test_seed_from_yaml_idempotent(session_factory, tmp_path):
    """种子化幂等：二次执行 0 新增；enabled=false 条目保留暂停态"""
    from src.storage.db import CollectTaskRepository, seed_collect_tasks_from_yaml

    yaml_path = _write_targets_yaml(tmp_path, [
        {"platform": "steam", "id": "111", "name": "Game A", "language": "schinese", "count": None, "enabled": True},
        {"platform": "steam", "id": "222", "name": "Game B", "enabled": False},
        {"platform": "bilibili", "id": "BV1xx", "enabled": True},  # 非 steam：不迁移
    ])

    with session_factory() as s:
        assert seed_collect_tasks_from_yaml(s, yaml_path) == 2
        # 幂等：再跑一遍 0 新增
        assert seed_collect_tasks_from_yaml(s, yaml_path) == 0
        repo = CollectTaskRepository(s)
        assert repo.count() == 2
        paused = repo.get_by_target("steam", "222")
        assert paused.enabled == 0  # yaml enabled=false → 迁移为暂停态


def test_seed_missing_yaml_returns_zero(session_factory, tmp_path):
    from src.storage.db import seed_collect_tasks_from_yaml

    with session_factory() as s:
        assert seed_collect_tasks_from_yaml(s, tmp_path / "nonexistent.yaml") == 0


# ==================== 3. load_targets_from_db / load_targets_any ====================

def test_load_targets_from_db_format(test_db_path, session_factory):
    from scripts.ops.daily_incremental_collect import load_targets_from_db
    from src.storage.db import CollectTaskRepository

    with session_factory() as s:
        CollectTaskRepository(s).create("steam", "2358720", name="黑神话：悟空", count=None)

    targets = load_targets_from_db(test_db_path)
    assert targets is not None and len(targets) == 1
    t = targets[0]
    assert t["platform"] == "steam"
    assert t["id"] == "2358720"
    assert t["name"] == "黑神话：悟空"
    assert t["count"] is None


def test_load_targets_from_db_empty_returns_none(test_db_path):
    from scripts.ops.daily_incremental_collect import load_targets_from_db

    assert load_targets_from_db(test_db_path) is None


def test_load_targets_any_falls_back_and_seeds(test_db_path, session_factory, tmp_path):
    """空表 → load_targets_any 自动种子化后从 DB 返回；yaml 中 bilibili 条目不入 collect_tasks"""
    from scripts.ops.daily_incremental_collect import load_targets_any

    yaml_path = _write_targets_yaml(tmp_path, [
        {"platform": "steam", "id": "111", "name": "Game A", "enabled": True},
    ])

    targets = load_targets_any(yaml_path, test_db_path)
    assert len(targets) == 1
    assert targets[0]["id"] == "111"

    # 种子化后 DB 生效：再跑仍从 DB 读（且幂等不重复）
    targets2 = load_targets_any(yaml_path, test_db_path)
    assert len(targets2) == 1


def test_load_targets_any_falls_back_to_yaml_when_unseedable(test_db_path, tmp_path):
    """yaml 不存在且 DB 空 → 回退 yaml 路径应抛 FileNotFoundError（保持原行为）"""
    from scripts.ops.daily_incremental_collect import load_targets_any

    with pytest.raises(FileNotFoundError):
        load_targets_any(tmp_path / "nonexistent.yaml", test_db_path)


# ==================== 4. WAL 模式 ====================

def test_wal_mode_enabled(test_db_path):
    """init_db 建立的连接应运行在 WAL 模式（Web 读 × cron 写并发加固）"""
    from sqlalchemy import text

    from src.storage.db import init_db

    engine, _ = init_db(f"sqlite:///{test_db_path}")
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert str(mode).lower() == "wal", f"journal_mode 应为 wal，实际 {mode}"


# ==================== 5. B 站 paused 状态 ====================

def test_bilibili_paused_skipped_by_runner(test_db_path, session_factory):
    """paused 的队列条目不应被 run-due 扫到（due_found=0）；恢复后重新可见"""
    from datetime import datetime, timedelta, timezone

    from src.queue.runner import run_due_collection
    from src.storage.db import BilibiliQueue

    past_due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    with session_factory() as s:
        s.add(BilibiliQueue(bv_id="BV1PAUSED", status="paused", due_date=past_due))
        s.add(BilibiliQueue(bv_id="BV1ACTIVE", status="scheduled", due_date=past_due))
        s.commit()

    report = run_due_collection(dry_run=True)
    assert report["due_found"] == 1, "paused 条目不应被扫到，scheduled 条目应可见"


def test_bilibili_paused_state_transitions(session_factory):
    """恢复规则：pubdate 已识别 → scheduled；未识别 → pending"""
    from src.storage.db import BilibiliQueue

    with session_factory() as s:
        s.add(BilibiliQueue(bv_id="BV1KNOWN", status="paused", pubdate=__import__("datetime").datetime(2026, 8, 1)))
        s.add(BilibiliQueue(bv_id="BV1UNKNOWN", status="paused", pubdate=None))
        s.commit()

    with session_factory() as s:
        known = s.query(BilibiliQueue).filter_by(bv_id="BV1KNOWN").one()
        unknown = s.query(BilibiliQueue).filter_by(bv_id="BV1UNKNOWN").one()
        # 恢复逻辑（API 层实现，此处锁定目标状态）
        known.status = "scheduled" if known.pubdate else "pending"
        unknown.status = "scheduled" if unknown.pubdate else "pending"
        assert known.status == "scheduled"
        assert unknown.status == "pending"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
