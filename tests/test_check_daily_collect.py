"""每日采集哨兵判定逻辑测试（2026-09-07）

纯逻辑测试：should_backfill 的四种分支；daily_collect_running 通过 monkeypatch 隔离。
不读真实计划任务、不启动任何进程。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.ops.check_daily_collect as sentinel  # noqa: E402

TODAY = datetime(2026, 9, 7)


def test_backfill_when_last_result_failed(monkeypatch):
    """02:00 跑了但退出码非 0（任一目标失败）→ 补采（核心场景：凌晨网络全灭）"""
    monkeypatch.setattr(sentinel, "daily_collect_running", lambda: False)
    status = {
        "last_run": datetime(2026, 9, 7, 2, 0, 1),
        "last_result": 1,
        "state": "Ready",
    }
    do_run, reason = sentinel.should_backfill(status, TODAY)
    assert do_run is True
    assert "退出码 1" in reason


def test_skip_when_last_result_success(monkeypatch):
    """02:00 成功 → 跳过（不浪费 LLM 成本与时间）"""
    monkeypatch.setattr(sentinel, "daily_collect_running", lambda: False)
    status = {
        "last_run": datetime(2026, 9, 7, 2, 0, 1),
        "last_result": 0,
        "state": "Ready",
    }
    do_run, reason = sentinel.should_backfill(status, TODAY)
    assert do_run is False
    assert "成功" in reason


def test_backfill_when_missed_today(monkeypatch):
    """今日未跑（机器关机错过且 StartWhenAvailable 未触发）→ 补采"""
    monkeypatch.setattr(sentinel, "daily_collect_running", lambda: False)
    status = {
        "last_run": datetime(2026, 9, 5, 2, 0, 1),
        "last_result": 0,
        "state": "Ready",
    }
    do_run, reason = sentinel.should_backfill(status, TODAY)
    assert do_run is True
    assert "未跑" in reason


def test_skip_when_task_still_running(monkeypatch):
    """02:00 长任务仍在运行（LLM 分析可跑到 04:00+）→ 绝不并发第二个"""
    monkeypatch.setattr(sentinel, "daily_collect_running", lambda: False)
    status = {
        "last_run": datetime(2026, 9, 7, 2, 0, 1),
        "last_result": 267009,  # 0x41301 currently running
        "state": "Running",
    }
    do_run, _ = sentinel.should_backfill(status, TODAY)
    assert do_run is False


def test_skip_when_orphan_process_alive(monkeypatch):
    """任务状态已结束但残留的 daily_incremental_collect 进程在跑 → 跳过"""
    monkeypatch.setattr(sentinel, "daily_collect_running", lambda: True)
    status = {
        "last_run": datetime(2026, 9, 7, 2, 0, 1),
        "last_result": 1,
        "state": "Ready",
    }
    do_run, reason = sentinel.should_backfill(status, TODAY)
    assert do_run is False
    assert "进程" in reason


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
