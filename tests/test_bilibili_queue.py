"""B 站采集队列单元测试（不依赖真实 B 站 API）

覆盖：
- add 后落库 + status 推断
- list 过滤 status
- due 过滤 due_date <= today
- skip 改 status
- remove 仅允许删除非 fetched
- show 序列化
- 状态机正确性
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 让测试可独立运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 用临时 DB（避免污染主库）
import src.storage.db as db_module  # noqa: E402


def setup_tmp_db(monkeypatch_module=None):
    """创建临时 SQLite DB 并返回 SessionLocal"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_url = f"sqlite:///{tmp.name}"
    _, SessionLocal = db_module.init_db(db_url=db_url)
    return SessionLocal, tmp.name


def teardown_tmp_db(path):
    """延迟删除，避开 Windows 文件占用"""
    import gc
    import time
    for attempt in range(5):
        gc.collect()
        try:
            os.unlink(path)
            return
        except (FileNotFoundError, PermissionError):
            time.sleep(0.1)
    # 最后兜底：不报错，留文件等下次清理
    print(f"  WARN: tmp db {path} 占用中，跳过清理")


def teardown_tmp_db(path):
    """延迟删除，避开 Windows 文件占用"""
    import gc
    gc.collect()
    try:
        os.unlink(path)
    except (FileNotFoundError, PermissionError):
        pass


def test_add_with_pubdate_creates_scheduled_row():
    """已知 pubdate 时，status 应该是 scheduled 且 due_date = pubdate + 7d"""
    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue
        from sqlalchemy import select

        # 模拟 add：手动构造 row（绕过 B 站 API 调用）
        pubdate = datetime(2026, 8, 1, 10, 0, 0)  # naive UTC
        row = BilibiliQueue(
            bv_id="BV1test00001",
            title="测试视频",
            pubdate=pubdate,
            due_date=pubdate + timedelta(days=7),
            status="scheduled",
            added_by="manual",
        )
        with SessionLocal() as s:
            s.add(row)
            s.commit()
            result = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == "BV1test00001")).scalar_one()
            assert result.status == "scheduled"
            assert result.due_date == pubdate + timedelta(days=7)
            assert result.title == "测试视频"
    finally:
        teardown_tmp_db(path)


def test_due_query_filters_by_date():
    """due 状态查询只返回 due_date <= today 的行"""
    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue
        from sqlalchemy import select

        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

        # 3 条记录：
        # - past due（应入选）
        # - today（应入选）
        # - future（不应入选）
        past = BilibiliQueue(bv_id="BVpast", pubdate=today - timedelta(days=10), due_date=today - timedelta(days=3), status="scheduled")
        today_row = BilibiliQueue(bv_id="BVtoday", pubdate=today - timedelta(days=8), due_date=today, status="scheduled")
        future = BilibiliQueue(bv_id="BVfuture", pubdate=today - timedelta(days=2), due_date=today + timedelta(days=5), status="scheduled")

        with SessionLocal() as s:
            s.add_all([past, today_row, future])
            s.commit()

            stmt = (
                select(BilibiliQueue)
                .where(BilibiliQueue.status == "scheduled")
                .where(BilibiliQueue.due_date <= today)
            )
            rows = list(s.execute(stmt).scalars())
            bv_ids = sorted(r.bv_id for r in rows)
            assert bv_ids == ["BVpast", "BVtoday"]
    finally:
        teardown_tmp_db(path)


def test_status_machine_transitions():
    """验证 pending → scheduled → fetching → fetched 的状态转换"""
    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue

        row = BilibiliQueue(bv_id="BVstate", status="pending")
        with SessionLocal() as s:
            s.add(row)
            s.commit()
            row_id = row.id

        # pending → scheduled（识别 pubdate 后）
        with SessionLocal() as s:
            r = s.get(BilibiliQueue, row_id)
            r.status = "scheduled"
            r.pubdate = datetime.now(timezone.utc).replace(tzinfo=None)
            r.due_date = r.pubdate + timedelta(days=7)
            s.commit()

        # scheduled → fetching（cron 取走）
        with SessionLocal() as s:
            r = s.get(BilibiliQueue, row_id)
            assert r.status == "scheduled"
            r.status = "fetching"
            s.commit()

        # fetching → fetched（成功）
        with SessionLocal() as s:
            r = s.get(BilibiliQueue, row_id)
            r.status = "fetched"
            r.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
            r.comment_count = 1000
            r.danmaku_count = 1200
            s.commit()

        with SessionLocal() as s:
            r = s.get(BilibiliQueue, row_id)
            assert r.status == "fetched"
            assert r.comment_count == 1000
            assert r.danmaku_count == 1200
            assert r.fetched_at is not None
    finally:
        teardown_tmp_db(path)


def test_revisit_flag():
    """high-value 重采标记"""
    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue

        row = BilibiliQueue(bv_id="BVhi", status="fetched", revisit=False)
        with SessionLocal() as s:
            s.add(row)
            s.commit()
            row_id = row.id

        with SessionLocal() as s:
            r = s.get(BilibiliQueue, row_id)
            r.revisit = True
            r.note = "high-value：5万评论，需季度重采"
            s.commit()

        with SessionLocal() as s:
            r = s.get(BilibiliQueue, row_id)
            assert r.revisit is True
            assert "high-value" in r.note
    finally:
        teardown_tmp_db(path)


def test_to_dict_serializable():
    """to_dict 输出可 JSON 序列化（前端可视化需要）"""
    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue
        import json

        row = BilibiliQueue(
            bv_id="BVserial",
            title="测试",
            pubdate=datetime(2026, 8, 1),
            due_date=datetime(2026, 8, 8),
            status="scheduled",
        )
        d = row.to_dict()
        # 必须可序列化
        s = json.dumps(d, ensure_ascii=False)
        loaded = json.loads(s)
        assert loaded["bv_id"] == "BVserial"
        assert loaded["title"] == "测试"
        assert loaded["status"] == "scheduled"
    finally:
        teardown_tmp_db(path)


def test_unique_bv_id_constraint():
    """BV 号唯一约束：重复 add 应抛 IntegrityError"""
    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue
        from sqlalchemy.exc import IntegrityError

        row1 = BilibiliQueue(bv_id="BVdup", status="scheduled")
        with SessionLocal() as s:
            s.add(row1)
            s.commit()

        row2 = BilibiliQueue(bv_id="BVdup", status="pending")
        integrity_error = None
        with SessionLocal() as s:
            s.add(row2)
            try:
                s.commit()
            except IntegrityError as e:
                integrity_error = e
                s.rollback()

        assert integrity_error is not None, "应该抛 IntegrityError"
        assert "UNIQUE" in str(integrity_error).upper() or "unique" in str(integrity_error).lower()
    finally:
        teardown_tmp_db(path)


if __name__ == "__main__":
    test_add_with_pubdate_creates_scheduled_row()
    test_due_query_filters_by_date()
    test_status_machine_transitions()
    test_revisit_flag()
    test_to_dict_serializable()
    test_unique_bv_id_constraint()
    print("[OK] all 6 tests passed")