# scripts/ops/register_local_collect_task.ps1
# Register Windows Task Scheduler task: local daily collect (02:00 BJT = machine local time)
#
# Background (2026-09-02): after the Web dashboard launch, the data pipeline switched from
# "GH Actions collect -> GH Release -> local sync" to "local collect -> frontend reads directly",
# removing sync lag and the GH Actions schedule jitter (up to 8h).
# The GH workflow `collect` job was disabled the same day (`test` job kept as CI regression gate).
#
# Command: daily_incremental_collect.py --no-download --no-upload --lookback-days 7 --push-db
#   --no-download: skip pulling remote GH Release (prevents an older remote DB overwriting the
#                  newer local DB; local data/voc.db is the single source of truth now)
#   --no-upload  : skip uploading to GH Release (gh CLI not installed on this machine;
#                  after installing gh + `gh auth login`, remove this flag to restore cloud backup)
#   --push-db    : after the whole collect chain finishes, push the local DB to the self-hosted
#                  VPS (data channel variant "1b" - docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md
#                  section 0.5). Non-blocking: a push failure only logs a warning and never
#                  changes the collect exit code. Register with -NoPushDb to opt out.
#
# Notes:
# - Registered as current user (no admin required); if registration fails on permissions,
#   re-run from an elevated PowerShell
# - Missed trigger (machine off/asleep) -> StartWhenAvailable runs it ASAP after boot
# - If the machine stays off for more than 2 days, a data gap appears (smart_window only
#   covers yesterday + the day before). Recovery:
#   python scripts/ops/daily_incremental_collect.py --no-download --no-upload --full-replay
#
# Last updated: 2026-09-11 (02:00 collect now also ships the DB to the VPS: added --push-db to
#   the 02:00 action plus a -NoPushDb opt-out switch. The push runs at the very END of the
#   02:00 chain (after bilibili run-due and the GH Release upload step) and is non-blocking.
#   Why reuse the 02:00 chain instead of adding a 5th task: variant "1b" only needs "the freshest
#   DB once per day", and the push MUST happen after collection. A separate task would need an
#   ad-hoc ordering guard, while the flag is inherently ordered and keeps the ops surface small.
#   See docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md section 0.5 / section 11.)
# Last updated: 2026-09-10 (added snapshot-publish task VOC-Local-Publish-Snapshot at 04:30:
#   runs ops/publish_static_snapshot.ps1 -> exports the 3 read-only dashboards to
#   data/exports/snapshot and publishes them to EdgeOne Pages (public static site).
#   Runs AFTER collect 02:00 / sentinel 03:00 / agent-prune 03:30 so the export sees the
#   fresh data. Kept as a SEPARATE task on purpose: publish failure must not change the
#   collect exit code or the 03:00 sentinel verdict. NOTE: daily_incremental_collect.py has
#   no --publish-snapshot flag (earlier docs claimed it existed; corrected 2026-09-10).)
# Last updated: 2026-09-09 (added agent-prune task VOC-Local-Agent-Prune at 03:30:
#   runs scripts/ops/prune_agent_history.py to delete agent_sessions older than
#   AGENT_RETENTION_DAYS (default 30, 0 = keep forever). FK CASCADE cleans messages.
#   Runs after the 03:00 sentinel so any session created today from a fresh install
#   has time to be written before the 03:30 sweep sees it.)
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
    [string]$At = "02:00",           # 02:00 BJT (machine local timezone)
    [string]$CheckAt = "03:00",      # sentinel check time
    [string]$PublishAt = "04:30",    # static snapshot publish time
    [string]$SnapshotProject = "voc-platform",  # EdgeOne Pages project name
    [switch]$NoPushDb                # register the 02:00 task WITHOUT the local DB -> VPS push
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
    foreach ($name in @($TaskName, "$TaskName-Check", "VOC-Local-Agent-Prune", "VOC-Local-Publish-Snapshot")) {
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
# --push-db: ship the finished DB to the self-hosted VPS (variant 1b); skipped with -NoPushDb.
#            Must stay LAST on the command line - it runs after the collect chain, not before.
$pushFlag = if ($NoPushDb) { "" } else { " --push-db" }
$inner = "`"$Python`" `"$Script`" --no-download --no-upload --lookback-days 7$pushFlag >> `"$LogFile`" 2>&1"
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
    -Description "VoC local daily collect (02:00 BJT) -> data/voc.db; GH Release cloud backup paused (no gh CLI); pushes DB to the self-hosted VPS (variant 1b, non-blocking)" `
    -Force | Out-Null

$pushNote = if ($NoPushDb) { "push to VPS DISABLED" } else { "pushes DB to VPS (non-blocking)" }
Write-Host "  -> $TaskName daily at $At (local collect, lookback 7d, no download/upload, $pushNote)"

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

# ---- agent-prune task: 03:30 delete agent_sessions older than AGENT_RETENTION_DAYS ----
$PruneTaskName = "VOC-Local-Agent-Prune"
$PruneScript = Join-Path $ProjectRoot "scripts\ops\prune_agent_history.py"
if (-not (Test-Path $PruneScript)) { Write-Error "agent-prune script not found: $PruneScript"; exit 1 }
$PruneLogFile = Join-Path $LogDir "agent-prune.log"

$pruneTask = Get-ScheduledTask -TaskName $PruneTaskName -ErrorAction SilentlyContinue
if ($pruneTask) { Write-Host "updating: $PruneTaskName" } else { Write-Host "creating: $PruneTaskName" }

$innerPrune = "`"$Python`" `"$PruneScript`" >> `"$PruneLogFile`" 2>&1"
$cmdArgsPrune = "/c cd /d `"$ProjectRoot`" && $innerPrune"
$actionPrune = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgsPrune -WorkingDirectory $ProjectRoot
$triggerPrune = New-ScheduledTaskTrigger -Daily -At "03:30"
$settingsPrune = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $PruneTaskName -Action $actionPrune -Trigger $triggerPrune `
    -Settings $settingsPrune -Principal $principal `
    -Description "VoC agent history prune (03:30 BJT): delete agent_sessions older than AGENT_RETENTION_DAYS (default 30, 0=forever); FK CASCADE drops agent_messages" `
    -Force | Out-Null

Write-Host "  -> $PruneTaskName daily at 03:30 (prune agent_sessions older than retention)"

# ---- snapshot-publish task: 04:30 export static snapshot + publish to EdgeOne Pages ----
$PublishTaskName = "VOC-Local-Publish-Snapshot"
$PublishScript = Join-Path $ProjectRoot "scripts\ops\publish_static_snapshot.ps1"
if (-not (Test-Path $PublishScript)) { Write-Error "publish script not found: $PublishScript"; exit 1 }
$PublishLogFile = Join-Path $LogDir "publish-snapshot.log"

$pubTask = Get-ScheduledTask -TaskName $PublishTaskName -ErrorAction SilentlyContinue
if ($pubTask) { Write-Host "updating: $PublishTaskName" } else { Write-Host "creating: $PublishTaskName" }

# publish_static_snapshot.ps1 is PowerShell; run it through powershell.exe so the
# ExecutionPolicy holds in the non-interactive scheduled-task context
$innerPub = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PublishScript`" -Name $SnapshotProject >> `"$PublishLogFile`" 2>&1"
$cmdArgsPub = "/c cd /d `"$ProjectRoot`" && $innerPub"
$actionPub = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArgsPub -WorkingDirectory $ProjectRoot
$triggerPub = New-ScheduledTaskTrigger -Daily -At $PublishAt
$settingsPub = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $PublishTaskName -Action $actionPub -Trigger $triggerPub `
    -Settings $settingsPub -Principal $principal `
    -Description "VoC static snapshot publish (04:30 BJT): export pre-aggregated JSON + deploy to EdgeOne Pages (public read-only dashboards); intentionally separate from collect so publish failures never affect the collect exit code" `
    -Force | Out-Null

Write-Host "  -> $PublishTaskName daily at $PublishAt (export + publish static snapshot to EdgeOne Pages)"

Write-Host ""
Write-Host "Test run manually:"
Write-Host "  & `"$Python`" `"$Script`" --no-download --no-upload"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\ops\push_db_to_vps.ps1`" -DryRun"
Write-Host "  & `"$Python`" `"$CheckScript`" --dry-run"
Write-Host "  & `"$Python`" `"$PruneScript`" --dry-run"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PublishScript`" -Name $SnapshotProject"
Write-Host "Uninstall:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/ops/register_local_collect_task.ps1 -Uninstall"
