"""每日采集哨兵（03:00 计划任务 VOC-Local-Daily-Collect-Check）

职责：检查 02:00 的 VOC-Local-Daily-Collect 是否成功，失败/未跑则补采。

背景（2026-09-07）：02:00 采集连续三晚因本机到 store.steampowered.com 网络不通
全灭（与用户代理程序启停状态相关）。StartWhenAvailable 只补「错过的运行」，
不处理「跑了但失败」——本哨兵补上这个洞：失败后 1 小时内自动重试一次
（03:00-05:30 时段网络恢复即可追平，7 天回看窗口保证幂等补齐）。

判定规则（should_backfill）：
- 上次运行时间不是今天 → 未跑（机器关机等）→ 补采
- LastTaskResult != 0 → 上次失败（任一目标失败即退出码 1）→ 补采
- 任务当前 State=Running，或系统里已有 daily_incremental_collect 进程 → 跳过
  （02:00 长任务可能跑到 04:00+，绝不能并发第二个）
- LastTaskResult == 0 → 成功 → 跳过

幂等性：daily_incremental_collect 重跑安全——upsert 去重 + analyzed_at 跳过
（不重复 LLM 成本）+ 7 天回看窗口补齐缺口。

用法：
    python scripts/ops/check_daily_collect.py            # 检查 + 按需补采（计划任务用）
    python scripts/ops/check_daily_collect.py --dry-run  # 只判定不执行
    python scripts/ops/check_daily_collect.py --force    # 无条件补采（手动应急）

最后更新：2026-09-07
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

TASK_NAME = "VOC-Local-Daily-Collect"
DAILY_SCRIPT = ROOT / "scripts" / "ops" / "daily_incremental_collect.py"

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("voc.collect_sentinel")


def get_task_status() -> dict:
    """读取计划任务的 LastRunTime / LastTaskResult / State（PowerShell）。"""
    ps = (
        "$ErrorActionPreference='Stop';"
        f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}';"
        "$i = $t | Get-ScheduledTaskInfo;"
        "Write-Output $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss');"
        "Write-Output $i.LastTaskResult;"
        "Write-Output $t.State"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"读取计划任务失败: {r.stderr.strip()[:200]}")
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return {
        "last_run": datetime.strptime(lines[0], "%Y-%m-%d %H:%M:%S"),
        "last_result": int(lines[1]),
        "state": lines[2],
    }


def daily_collect_running() -> bool:
    """系统里是否已有 daily_incremental_collect 进程（防孤儿 + 防并发）。"""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
        "Where-Object { $_.CommandLine -match 'daily_incremental_collect' } | "
        "Measure-Object | Select-Object -ExpandProperty Count"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return int(r.stdout.strip() or 0) > 0
    except ValueError:
        return False


def should_backfill(status: dict, today: datetime) -> tuple[bool, str]:
    """纯判定逻辑（便于测试）。返回 (是否补采, 原因)。"""
    if status["state"] == "Running":
        return False, "02:00 任务仍在运行中，跳过（防并发）"
    if daily_collect_running():
        return False, "检测到 daily_incremental_collect 进程仍在运行（任务已结束但进程残留/手动在跑），跳过"
    if status["last_run"].date() != today.date():
        return True, f"上次运行时间为 {status['last_run']:%Y-%m-%d %H:%M}，今日未跑 → 补采"
    if status["last_result"] == 0:
        return False, "02:00 任务上次成功，跳过"
    return True, f"上次退出码 {status['last_result']}（部分/全部目标失败）→ 补采"


def run_backfill(lookback_days: int) -> int:
    """同步执行补采（继承 stdout，由计划任务重定向到日志）。返回退出码。"""
    cmd = [
        sys.executable, str(DAILY_SCRIPT),
        "--no-download", "--no-upload",
        "--lookback-days", str(lookback_days),
    ]
    log.info("启动补采：%s", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main():
    parser = argparse.ArgumentParser(description="每日采集哨兵：失败/未跑则补采")
    parser.add_argument("--dry-run", action="store_true", help="只判定不执行")
    parser.add_argument("--force", action="store_true", help="无条件补采（手动应急）")
    parser.add_argument("--lookback-days", type=int, default=7,
                        help="补采回看天数（与计划任务一致，默认 7）")
    args = parser.parse_args()

    log.info("===== 哨兵检查开始（%s）=====", TASK_NAME)
    try:
        status = get_task_status()
    except Exception as e:  # noqa: BLE001
        # 读取失败（如计划任务被删）不静默：按未跑处理走补采，让日志留下痕迹
        log.warning("读取计划任务状态失败：%s → 按「今日未跑」处理", e)
        status = {"last_run": datetime(1970, 1, 1), "last_result": -1, "state": "Unknown"}

    log.info(
        "任务状态：LastRun=%s LastResult=%s State=%s",
        status["last_run"], status["last_result"], status["state"],
    )

    if args.force:
        do_run, reason = True, "--force 手动强制补采"
    else:
        do_run, reason = should_backfill(status, datetime.now())

    log.info("判定：%s（%s）", "需要补采" if do_run else "跳过补采", reason)
    if not do_run or args.dry_run:
        if args.dry_run and do_run:
            log.info("--dry-run：仅判定，不执行")
        return 0

    code = run_backfill(lookback_days=args.lookback_days)
    log.info("补采结束，退出码 %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
