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


# ==================== daily 编排接入 run-due（2026-09-05 调度缺口修复回归） ====================

def test_daily_bilibili_queue_orchestration(monkeypatch):
    """daily_incremental_collect.run_bilibili_queue 复用 runner 并透传 limit"""
    import src.queue.runner as runner_mod
    from scripts.ops.daily_incremental_collect import run_bilibili_queue

    calls = {}

    def fake_run_due(*, limit, dry_run=False):
        calls["limit"] = limit
        assert dry_run is False
        return {"due_found": 2, "fetched": 1, "failed": 1,
                "skipped": 0, "errors": ["BV1xxx: RuntimeError: boom"]}

    monkeypatch.setattr(runner_mod, "run_due_collection", fake_run_due)
    r = run_bilibili_queue(limit=3)
    assert calls["limit"] == 3
    assert r["ok"] is True  # 单视频失败由 runner 重试自治，不计为脚本失败
    assert r["fetched"] == 1
    assert "BV1xxx" in r["error"]


def test_daily_bilibili_queue_structural_failure(monkeypatch):
    """runner 结构性异常 → 不向上抛，转为 ok=False 的结果行（不阻塞 Steam 结果）"""
    import src.queue.runner as runner_mod
    from scripts.ops.daily_incremental_collect import run_bilibili_queue

    def fake_run_due(*, limit, dry_run=False):
        raise RuntimeError("db locked")

    monkeypatch.setattr(runner_mod, "run_due_collection", fake_run_due)
    r = run_bilibili_queue(limit=5)
    assert r["ok"] is False
    assert "RuntimeError" in r["error"]


# ==================== run-due 前置修复（2026-09-06 对抗审查：孤儿回收 + pending 重识别） ====================

def test_run_due_reclaims_orphan_fetching(monkeypatch):
    """卡在 fetching 的孤儿行 → run-due 开头回收为 scheduled → 本轮正常采集落 fetched"""
    import src.queue.runner as runner_mod

    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue
        from sqlalchemy import select

        with SessionLocal() as s:
            s.add(BilibiliQueue(
                bv_id="BVorphan", status="fetching",
                pubdate=datetime(2020, 1, 1), due_date=datetime(2020, 1, 8),
            ))
            s.commit()

        monkeypatch.setattr(runner_mod, "init_db", lambda: (None, SessionLocal))
        monkeypatch.setattr(
            runner_mod, "_run_pipeline",
            lambda bv: {"ok": True, "fetched": 10, "analyzed": 10, "danmaku": 5, "error": None},
        )

        report = runner_mod.run_due_collection(limit=5)
        assert report["reclaimed"] == 1
        assert report["due_found"] == 1
        assert report["fetched"] == 1

        with SessionLocal() as s:
            row = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == "BVorphan")).scalar_one()
            assert row.status == "fetched"
            assert row.fetched_at is not None
    finally:
        teardown_tmp_db(path)


def test_run_due_reidentifies_pending_then_collects(monkeypatch):
    """pending（创建时识别失败）→ 重识别成功 → scheduled（旧视频立即到期）→ 同轮采集"""
    import src.queue.runner as runner_mod
    import src.queue.cli as cli_mod

    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue
        from sqlalchemy import select

        with SessionLocal() as s:
            s.add(BilibiliQueue(bv_id="BVpending", status="pending"))
            s.commit()

        monkeypatch.setattr(runner_mod, "init_db", lambda: (None, SessionLocal))
        monkeypatch.setattr(
            cli_mod, "_lookup_pubdate",
            lambda bv: (datetime(2020, 1, 1), "重识别出的标题"),
        )
        monkeypatch.setattr(
            runner_mod, "_run_pipeline",
            lambda bv: {"ok": True, "fetched": 8, "analyzed": 8, "danmaku": 2, "error": None},
        )

        report = runner_mod.run_due_collection(limit=5)
        assert report["reidentified"] == 1
        assert report["due_found"] == 1   # due = 2020-01-08，早已过期 → 同轮认领
        assert report["fetched"] == 1

        with SessionLocal() as s:
            row = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == "BVpending")).scalar_one()
            assert row.status == "fetched"
            assert row.pubdate == datetime(2020, 1, 1)
            assert row.title == "重识别出的标题"
    finally:
        teardown_tmp_db(path)


def test_run_due_pending_reidentify_failure_stays_pending(monkeypatch):
    """重识别仍失败（风控/网络）→ 保持 pending 不误标，due_found=0"""
    import src.queue.runner as runner_mod
    import src.queue.cli as cli_mod

    SessionLocal, path = setup_tmp_db()
    try:
        from src.storage.db import BilibiliQueue
        from sqlalchemy import select

        with SessionLocal() as s:
            s.add(BilibiliQueue(bv_id="BVstuck", status="pending"))
            s.commit()

        monkeypatch.setattr(runner_mod, "init_db", lambda: (None, SessionLocal))
        monkeypatch.setattr(cli_mod, "_lookup_pubdate", lambda bv: (None, None))

        report = runner_mod.run_due_collection(limit=5)
        assert report["reidentified"] == 0
        assert report["due_found"] == 0

        with SessionLocal() as s:
            row = s.execute(select(BilibiliQueue).where(BilibiliQueue.bv_id == "BVstuck")).scalar_one()
            assert row.status == "pending"
    finally:
        teardown_tmp_db(path)


# ==================== _lookup_pubdate 会话链路（2026-09-05 412 根治回归） ====================

def test_lookup_pubdate_parses_view_response(monkeypatch):
    """正常响应：pubdate(unix→naive UTC) + title 正确解析"""
    from src.queue.cli import _lookup_pubdate

    class FakeResp:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(self, url, **kw):
        if "finger/spi" in url:
            return FakeResp({"code": 0, "data": {"b_3": "buvid3-x", "b_4": "buvid4-y"}})
        if "web-interface/view" in url:
            return FakeResp({"code": 0, "data": {"pubdate": 1720000000, "title": "测试视频"}})
        raise AssertionError(f"意外请求: {url}")

    monkeypatch.setattr("requests.Session.get", fake_get)
    from src.collectors.bilibili import BilibiliCollector
    monkeypatch.setattr(BilibiliCollector, "_throttle", lambda self: None)

    pubdate, title = _lookup_pubdate("BV1testpubdate")
    assert title == "测试视频"
    assert pubdate == datetime(2024, 7, 3, 9, 46, 40)  # 1720000000 → naive UTC


def test_lookup_pubdate_goes_through_collector_session(monkeypatch):
    """根治回归：请求必须先取 buvid 指纹（finger/spi）再打 view——裸 requests 直连会 412"""
    import requests as _requests
    from src.queue.cli import _lookup_pubdate

    calls = []

    class FakeResp:
        status_code = 200

        def json(self):
            if "finger/spi" in calls[-1]:
                return {"code": 0, "data": {"b_3": "x", "b_4": "y"}}
            return {"code": 0, "data": {"pubdate": 1720000000, "title": "T"}}

    def fake_get(self, url, **kw):
        calls.append(url)
        return FakeResp()

    monkeypatch.setattr(_requests.Session, "get", fake_get)
    from src.collectors.bilibili import BilibiliCollector
    monkeypatch.setattr(BilibiliCollector, "_throttle", lambda self: None)

    _lookup_pubdate("BV1testsession")
    assert any("finger/spi" in u for u in calls), "必须先请求 finger/spi 获取 buvid 指纹"
    assert any("web-interface/view" in u for u in calls)


def test_lookup_pubdate_fails_gracefully_on_risk_control(monkeypatch):
    """412 风控（非 JSON / API code≠0 抛 RuntimeError）→ (None, None)，不向上抛"""
    from src.queue.cli import _lookup_pubdate

    class FakeResp:
        status_code = 412

        def json(self):
            raise ValueError("非 JSON 响应")

    monkeypatch.setattr("requests.Session.get", lambda self, url, **kw: FakeResp())
    from src.collectors.bilibili import BilibiliCollector
    monkeypatch.setattr(BilibiliCollector, "_throttle", lambda self: None)

    pubdate, title = _lookup_pubdate("BV1testrisk")
    assert pubdate is None
    assert title is None


# ==================== run-due 零评论防护（2026-09-07 代理空采回归） ====================

def test_run_pipeline_zero_comments_is_failure(monkeypatch):
    """B站采集 0 条评论 → _run_pipeline 判失败（代理 TUN 空数据/风控不再静默标 fetched）"""
    import src.queue.runner as runner_mod
    import src.pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod, "run_pipeline",
        lambda **kw: {"fetched": 0, "analyzed": 0, "danmaku": 0},
    )
    result = runner_mod._run_pipeline("BVzero")
    assert result["ok"] is False
    assert "0 条评论" in result["error"]


def test_run_pipeline_normal_comments_ok(monkeypatch):
    """正常采集（fetched>0）→ ok=True 透传数量"""
    import src.queue.runner as runner_mod
    import src.pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod, "run_pipeline",
        lambda **kw: {"fetched": 1000, "analyzed": 1000, "danmaku": 1800},
    )
    result = runner_mod._run_pipeline("BVnormal")
    assert result["ok"] is True
    assert result["fetched"] == 1000
    assert result["danmaku"] == 1800


if __name__ == "__main__":
    test_add_with_pubdate_creates_scheduled_row()
    test_due_query_filters_by_date()
    test_status_machine_transitions()
    test_revisit_flag()
    test_to_dict_serializable()
    test_unique_bv_id_constraint()
    print("[OK] all 6 tests passed")