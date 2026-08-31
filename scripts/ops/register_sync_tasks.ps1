# scripts/ops/register_sync_tasks.ps1
# 注册 Windows Task Scheduler 任务：自动 sync GH Release → 本地 voc.db
# 4 个 task 错开 10:00 / 13:00 / 18:00 / 22:00 每天

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskPrefix = "VOC-Sync-Release"
$ProjectRoot = (Resolve-Path "$PSScriptRoot/../..").Path
$Python = (Get-Command python).Source
$Script = Join-Path $ProjectRoot "scripts/ops/smart_sync_release.py"

if (-not (Test-Path $Python)) { Write-Error "python not in PATH"; exit 1 }
if (-not (Test-Path $Script)) { Write-Error "smart_sync_release.py not found"; exit 1 }

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "需要以管理员身份运行 PowerShell"
    exit 2
}

$Times = @("10:00", "13:00", "18:00", "22:00")

if ($Uninstall) {
    Write-Host "Uninstalling VOC sync tasks..."
    foreach ($t in $Times) {
        $name = "$TaskPrefix-$t"
        $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "  removed: $name"
        } else {
            Write-Host "  skip: $name (not found)"
        }
    }
    Write-Host "Done."
    exit 0
}

Write-Host "Registering VOC sync tasks"
Write-Host "  project: $ProjectRoot"
Write-Host "  python:  $Python"
Write-Host "  script:  $Script"
Write-Host ""

foreach ($t in $Times) {
    $name = "$TaskPrefix-$t"
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Write-Host "  updating: $name"
    } else {
        Write-Host "  creating: $name"
    }

    $action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $t
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest -LogonType S4U

    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Auto-sync GitHub Release to local voc.db at $t" -Force | Out-Null
    Write-Host "    -> $name daily at $t"
}

Write-Host ""
Write-Host "Done. 4 daily tasks registered."
Write-Host "Test run manually: & '$Python' '$Script'"
Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File scripts/ops/register_sync_tasks.ps1 -Uninstall"