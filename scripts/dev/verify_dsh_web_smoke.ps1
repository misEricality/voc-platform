#!/usr/bin/env pwsh
# ============================================================
# verify_dsh_web_smoke.ps1 -- DSH web profile smoke test
# ------------------------------------------------------------
# Purpose: spin up dsh web once on this machine and verify:
#   1. DSH process boots cleanly (no pollution of ~/.dsh; uses temp DSH_HOME)
#   2. Web shell is reachable (parse URL + curl + check title)
#   3. Port + token link works (200 = UI rendered, 401 = server up but needs token,
#      both count as "web shell alive")
# ------------------------------------------------------------
# Use:  powershell -ExecutionPolicy Bypass -File scripts/dev/verify_dsh_web_smoke.ps1
#       powershell -ExecutionPolicy Bypass -File scripts/dev/verify_dsh_web_smoke.ps1 -KeepHome
# Exit: 0 = pass; non-zero = fail (with stderr message)
# ------------------------------------------------------------
# Last update: 2026-09-07
# ============================================================
# IMPORTANT: This file is ASCII-only on purpose. PowerShell 5.x on
# Windows parses ps1 as ANSI by default; non-ASCII chars in comments
# or strings cause "Unexpected token" parse errors on some hosts.
# ============================================================

[CmdletBinding()]
param(
    [int]$Port = 3081,
    [int]$WaitSeconds = 30,
    [switch]$KeepHome
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ---------- Path resolution (run from project root) ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

$LogsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogPath = Join-Path $LogsDir "verify_dsh_web_$Timestamp.log"

$TempHome = Join-Path $ProjectRoot ".dsh-home-smoke"
if (-not (Test-Path $TempHome)) { New-Item -ItemType Directory -Path $TempHome -Force | Out-Null }

# ---------- Cleanup on Ctrl+C / abnormal exit ----------
$script:DshProc = $null
$Cleanup = {
    if ($null -ne $script:DshProc -and -not $script:DshProc.HasExited) {
        try { Stop-Process -Id $script:DshProc.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    if (-not $KeepHome -and (Test-Path $TempHome)) {
        Remove-Item -Path $TempHome -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $RunCwd) {
        Remove-Item -Path $RunCwd -Recurse -Force -ErrorAction SilentlyContinue
    }
}
trap { & $Cleanup; break }
Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action $Cleanup | Out-Null

# ---------- Banner ----------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "DSH web profile smoke test" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  port        : $Port"
Write-Host "  DSH_HOME    : $TempHome (temp; does not touch ~/.dsh)"
Write-Host "  log file    : $LogPath"
Write-Host "  wait seconds: $WaitSeconds"
Write-Host ""

# ---------- 1. Find dsh executable ----------
# The npm-installed `dsh` is a PowerShell wrapper that re-invokes node on the
# real JS entry. Two ways to spawn it: (1) via `powershell -File dsh.ps1 ...`
# (recurses through the wrapper) or (2) directly call node on bin.js. We use
# (2) to keep one process and avoid the wrapper's extra redirect.
$dshCmd = Get-Command dsh -ErrorAction SilentlyContinue
if ($null -eq $dshCmd) {
    Write-Host "[X] dsh command not found (npm-cache path not on PATH)" -ForegroundColor Red
    Write-Host "    Verify: Get-Command dsh should return a .ps1 path" -ForegroundColor Yellow
    exit 1
}
$dshScriptPath = $dshCmd.Source
# Resolve: <cache>/_npx/.../node_modules/.bin/dsh.ps1 -> <cache>/_npx/.../node_modules/@deepseek-ai/dsh/lib/bin.js
$binWrapperDir = Split-Path -Parent $dshScriptPath
$pkgRoot = Split-Path -Parent $binWrapperDir
$dshEntry = Join-Path $pkgRoot "@deepseek-ai\dsh\lib\bin.js"
if (-not (Test-Path $dshEntry)) {
    Write-Host "[X] dsh JS entry not found: $dshEntry" -ForegroundColor Red
    exit 1
}
Write-Host "[i] dsh entry: $dshEntry" -ForegroundColor Gray

# ---------- 2. Start DSH web ----------
Write-Host "[1/4] starting dsh web (--no-open)..." -ForegroundColor Yellow
$env:DSH_HOME = $TempHome

# WARNING: DSH env loader walks the working directory for .env and refuses to
# boot if it finds "launcher-only" vars like DEEPSEEK_BASE_URL/GLM_BASE_URL.
# Our project's .env legitimately exports those for the analyzer pipeline, so
# DSH MUST be spawned from a directory that has no .env. Use %TEMP% as cwd.
# (2026-09-07: this was a recurring trap; an inline `node dsh...` invoked from
# the project root silently exits 1 with the same error.)
$RunCwd = Join-Path $env:TEMP "dsh-web-smoke-cwd-$Timestamp"
if (-not (Test-Path $RunCwd)) { New-Item -ItemType Directory -Path $RunCwd -Force | Out-Null }

$NodeArgs = @(
    $dshEntry,
    "--profile", "web",
    "--port", "$Port",
    "--host", "127.0.0.1",
    "--no-open"
)
$StdoutPath = "$LogPath.stdout"
$StderrPath = "$LogPath.stderr"
$script:DshProc = Start-Process `
    -FilePath "node" `
    -ArgumentList $NodeArgs `
    -WorkingDirectory $RunCwd `
    -RedirectStandardOutput $StdoutPath `
    -RedirectStandardError  $StderrPath `
    -NoNewWindow `
    -PassThru
Write-Host "    PID = $($script:DshProc.Id)" -ForegroundColor Gray

# ---------- 3. Wait for URL line in stdout ----------
Write-Host "[2/4] waiting for dsh web URL (up to $WaitSeconds s)..." -ForegroundColor Yellow
$Deadline = (Get-Date).AddSeconds($WaitSeconds)
$WebUrl = $null
while ((Get-Date) -lt $Deadline) {
    if ($script:DshProc.HasExited) {
        Write-Host "[X] DSH process exited early (exit=$($script:DshProc.ExitCode))" -ForegroundColor Red
        Write-Host "    full stdout : $StdoutPath" -ForegroundColor Yellow
        Write-Host "    full stderr : $StderrPath" -ForegroundColor Yellow
        exit 2
    }
    if (Test-Path $StdoutPath) {
        $content = Get-Content $StdoutPath -Raw -ErrorAction SilentlyContinue
        if ($content -match "dsh web:\s+(\S+)") {
            $WebUrl = $Matches[1]
            break
        }
    }
    Start-Sleep -Milliseconds 500
}
if ($null -eq $WebUrl) {
    Write-Host "[X] no 'dsh web: ...' URL line in stdout within $WaitSeconds s" -ForegroundColor Red
    Write-Host "    Likely causes: plugin load hung / sandbox blocked write to ~/.dsh" -ForegroundColor Yellow
    Write-Host "    Debug: Get-Content '$StderrPath' -Tail 30" -ForegroundColor Yellow
    exit 3
}
Write-Host "    URL = $WebUrl" -ForegroundColor Green

# ---------- 4. curl the index ----------
Write-Host "[3/4] GET $WebUrl ..." -ForegroundColor Yellow
$status = 0
$body = ""
try {
    $resp = Invoke-WebRequest -Uri $WebUrl -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    $status = [int]$resp.StatusCode
    $body = $resp.Content
} catch {
    if ($null -ne $_.Exception.Response) {
        try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = 0 }
    }
}
$statusColor = if ($status -in 200, 401, 403) { "Green" } else { "Red" }
Write-Host "    HTTP $status" -ForegroundColor $statusColor

# ---------- 5. Extract title (only meaningful at 200) ----------
$TitleFound = $false
if ($status -eq 200 -and $body) {
    if ($body -match "<title>([^<]+)</title>") {
        $Title = $Matches[1]
        Write-Host "    title = $Title" -ForegroundColor Gray
        if ($Title -match "Harness|DeepSeek") { $TitleFound = $true }
    }
}

# ---------- 6. Stop process ----------
Write-Host "[4/4] stopping dsh process..." -ForegroundColor Yellow
& $Cleanup

# ---------- 7. Verdict ----------
Write-Host ""
if ($status -in 200, 401, 403) {
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "[OK] smoke test passed" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Web shell reachable (HTTP $status)"
    if ($TitleFound) {
        Write-Host "  Page title matches 'Harness' -> UI rendering OK"
    } elseif ($status -eq 200) {
        Write-Host "  Page title did not match (SPA hash routing; initial HTML may be empty)" -ForegroundColor Yellow
    } else {
        Write-Host "  Got 401/403 (server up, token needed; expected)"
    }
    Write-Host ""
    Write-Host "Next steps (pick one):" -ForegroundColor Cyan
    Write-Host "  A) Open URL in browser to inspect UI -> continue Plan A"
    Write-Host "  B) UI too heavy -> switch to Plan B (custom UI + SDK) or C (headless endpoint)"
    Write-Host "  C) UI acceptable -> proceed to Plan A phase 1 (write voc-bridge tool)"
    exit 0
} else {
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host "[FAIL] smoke test failed" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Red
    Write-Host "  HTTP $status is not in expected range (200/401/403)"
    Write-Host "  Full log: $LogPath"
    exit 4
}
