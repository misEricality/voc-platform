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


# ==================== 补采必须回推 VPS（2026-09-12 调度缺口修复） ====================

def test_backfill_carries_push_db_flag(monkeypatch):
    """补采命令必须带 --push-db。

    缺陷场景：02:00 部分目标失败 → 链末尾照样推了**部分快照**给 VPS；哨兵 03:00 补采
    原先不带 --push-db，补到的数据只落本地 → VPS 整天停在部分快照（实测 2026-09-12
    差 93 条评论）。本用例锁住这个 flag，防止将来被误删。
    """
    captured: dict[str, object] = {}

    def fake_run(cmd, cwd=None):  # noqa: ANN001, ARG001
        captured["cmd"] = list(cmd)
        # 必须返回带 .returncode 的对象：run_backfill 取的是 subprocess.run(...).returncode
        return sentinel.subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sentinel.subprocess, "run", fake_run)
    code = sentinel.run_backfill(lookback_days=7)

    assert code == 0
    cmd = captured["cmd"]
    assert "--push-db" in cmd, "补采必须带 --push-db，否则 VPS 追不上补采结果"
    assert "--no-download" in cmd and "--no-upload" in cmd
    assert cmd[cmd.index("--lookback-days") + 1] == "7"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
