# scripts/ops/register_local_collect_task.ps1
# Register Windows Task Scheduler task: local daily collect (02:00 BJT = machine local time)
#
# Background (2026-09-02): after the Web dashboard launch, the data pipeline switched from
# "GH Actions collect -> GH Release -> local sync" to "local collect -> frontend reads directly",
# removing sync lag and the GH Actions schedule jitter (up to 8h).
# The GH workflow `collect` job was disabled the same day (`test` job kept as CI regression gate).
#
# Command: daily_incremental_collect.py --no-download --no-upload
#   --no-download: skip pulling remote GH Release (prevents an older remote DB overwriting the
#                  newer local DB; local data/voc.db is the single source of truth now)
#   --no-upload  : skip uploading to GH Release (gh CLI not installed on this machine;
#                  after installing gh + `gh auth login`, remove this flag to restore cloud backup)
#
# Notes:
# - Registered as current user (no admin required); if registration fails on permissions,
#   re-run from an elevated PowerShell
# - Missed trigger (machine off/asleep) -> StartWhenAvailable runs it ASAP after boot
# - If the machine stays off for more than 2 days, a data gap appears (smart_window only
#   covers yesterday + the day before). Recovery:
#   python scripts/ops/daily_incremental_collect.py --no-download --no-upload --full-replay
#
# Last updated: 2026-09-07 (added sentinel task VOC-Local-Daily-Collect-Check at 03:00:
#   checks whether the 02:00 run succeeded (LastTaskResult != 0 or missed) and re-runs
#   daily_incremental_collect.py if needed. Covers "ran but failed" (3 nights in a row of
#   dead Steam network at 02:00) which StartWhenAvailable cannot handle. The sentinel is
#   concurrency-safe: skips when the 02:00 task is still Running or a collect process exists.
#   Idempotent by design: upsert dedupe + analyzed_at skip = no duplicate LLM cost.)
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 parses BOM-less files as ANSI;
#       non-ASCII comments corrupt parsing - same reason register_sync_tasks.ps1 is English)

param(
    [switch]$Uninstall,
    [string]$At = "02:00",      # 02:00 BJT (machine local timezone)
    [string]$CheckAt = "03:00"  # sentinel check time
)

$ErrorActionPreference = "Stop"
$TaskName = "VOC-Local-Daily-Collect"
$ProjectRoot = (Resolve-Path "$PSScriptRoot/../..").Path
$Python = Join-Path $ProjectRoot ".venv-ml\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "scripts\ops\daily_incremental_collect.py"
$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "collect.log"

if (-not (Test-Path $Python)) { Write-Error "python not found: $Python (check .venv-ml exists)"; exit 1 }
if (-not (Test-Path $Script)) { Write-Error "script not found: $Script"; exit 1 }

if ($Uninstall) {
    foreach ($name in @($TaskName, "$TaskName-Check")) {
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "removed: $name"
        } else {
            Write-Host "skip: $name (not found)"
        }
    }
    exit 0
}

# log dir for output redirection
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) { Write-Host "updating: $TaskName" } else { Write-Host "creating: $TaskName" }

# redirect output via cmd /c (ScheduledTaskAction does not support redirection itself)
# --lookback-days 7: 7-day overlapping re-crawl against Steam recent-feed non-determinism
# (single-pass coverage ~80-95%; upsert idempotent + analyzed-skip keep the cost to pagination)
$inner = "`"$Python`" `"$Script`" --no-download --no-upload --lookback-days 7 >> `"$LogFile`" 2>&1"
$cmdArgs = "/c cd /d `"$ProjectRoot`" && $inner"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgs -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 150)
# current user + Interactive token: no admin required; runs while the user session exists (locked OK)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "VoC local daily collect (02:00 BJT) -> data/voc.db; GH Release cloud backup paused (no gh CLI)" `
    -Force | Out-Null

Write-Host "  -> $TaskName daily at $At (local collect, lookback 7d, no download/upload)"

# ---- sentinel task: 03:00 re-run if the 02:00 collect failed or was missed ----
$CheckTaskName = "$TaskName-Check"
$CheckScript = Join-Path $ProjectRoot "scripts\ops\check_daily_collect.py"
if (-not (Test-Path $CheckScript)) { Write-Error "sentinel script not found: $CheckScript"; exit 1 }
$CheckLogFile = Join-Path $LogDir "collect-check.log"

$checkTask = Get-ScheduledTask -TaskName $CheckTaskName -ErrorAction SilentlyContinue
if ($checkTask) { Write-Host "updating: $CheckTaskName" } else { Write-Host "creating: $CheckTaskName" }

$innerCheck = "`"$Python`" `"$CheckScript`" >> `"$CheckLogFile`" 2>&1"
$cmdArgsCheck = "/c cd /d `"$ProjectRoot`" && $innerCheck"
$actionCheck = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgsCheck -WorkingDirectory $ProjectRoot
$triggerCheck = New-ScheduledTaskTrigger -Daily -At $CheckAt
$settingsCheck = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 150)

Register-ScheduledTask -TaskName $CheckTaskName -Action $actionCheck -Trigger $triggerCheck `
    -Settings $settingsCheck -Principal $principal `
    -Description "VoC daily collect sentinel (03:00 BJT): re-run collect if the 02:00 run failed or was missed; concurrency-safe, idempotent" `
    -Force | Out-Null

Write-Host "  -> $CheckTaskName daily at $CheckAt (sentinel: backfill if 02:00 failed/missed)"
Write-Host ""
Write-Host "Test run manually:"
Write-Host "  & `"$Python`" `"$Script`" --no-download --no-upload"
Write-Host "  & `"$Python`" `"$CheckScript`" --dry-run"
Write-Host "Uninstall:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/ops/register_local_collect_task.ps1 -Uninstall"
