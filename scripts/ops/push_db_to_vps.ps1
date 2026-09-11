# scripts/ops/push_db_to_vps.ps1
# Push the local SQLite DB to the self-hosted VPS (data channel variant "1b").
#
# Why this exists
# ---------------
# The public VPS site serves /api/* from a long-lived uvicorn service that reads
# <RemoteDb> on every request. The local machine stays the single source of truth:
# Steam anti-bot handling and the local proxy must remain local, so collection is
# local and only the resulting DB is shipped. See
# docs/architecture/SELF_HOSTED_VPS_DEPLOYMENT.md (section 0.5 + section 11).
#
# Pipeline (fail-fast; the CALLER downgrades any non-zero exit to a warning)
# ------------------------------------------------------------------------
#   1. local : VACUUM INTO a temp snapshot  (consistent + WAL-safe + defragmented)
#   2. local : sha256 of that snapshot
#   3. scp   : snapshot -> <Remote>:/tmp/voc.db.new
#   4. remote: sha256 check + PRAGMA integrity_check + comments-count sanity
#   5. remote: sqlite3 .backup() INTO the live DB file  (in-place, NOT a rename)
#
# Design deviation from the original plan (`mv` atomic replace)
# ------------------------------------------------------------
# The plan said "atomic rename on the remote side". That is silently wrong for this
# service: uvicorn keeps a SQLAlchemy connection pool, and `mv` swaps the inode, so
# pooled connections keep reading the OLD file forever (no error, just stale data).
# sqlite3's .backup() rewrites pages inside the existing file instead, so already-open
# readers observe the new rows. It is still atomic per transaction and safe to run
# while the service is serving reads (WAL + busy_timeout).
#
# Usage
# -----
#   powershell -ExecutionPolicy Bypass -File scripts/ops/push_db_to_vps.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/ops/push_db_to_vps.ps1 -DryRun
#
# Exit code: 0 = remote DB replaced, non-zero = nothing changed on the remote side.
#
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 parses BOM-less files as
#       ANSI; non-ASCII comments corrupt parsing) - same reason register_*.ps1 are English.

param(
    [string]$DbPath = "",                                        # local DB (default <root>/data/voc.db)
    [string]$Remote = "ubuntu@134.175.115.248",                  # ssh target
    [string]$RemoteDb = "/home/voc/voc-platform/data/voc.db",    # live DB on the VPS
    [string]$Identity = "$HOME\.ssh\k_lynx_web.pem",             # ssh private key ("" = default)
    [string]$Python = "",                                        # local python (default .venv-ml)
    [string]$RemotePython = "python3",
    [switch]$DryRun,                                             # local snapshot only, no ssh
    [switch]$RestartService,                                     # defensive uvicorn restart after swap
    [int]$TimeoutSec = 1800
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path "$PSScriptRoot/../..").Path
if (-not $DbPath) { $DbPath = Join-Path $ProjectRoot "data\voc.db" }
if (-not $Python) {
    $venvPy = Join-Path $ProjectRoot ".venv-ml\Scripts\python.exe"
    if (Test-Path $venvPy) { $Python = $venvPy } else { $Python = "python" }
}

if (-not (Test-Path $DbPath)) { Write-Error "local DB not found: $DbPath"; exit 1 }
$srcSize = (Get-Item $DbPath).Length
if ($srcSize -lt 1MB) { Write-Error "local DB suspiciously small ($srcSize bytes): $DbPath"; exit 1 }
if (-not (Test-Path $Python)) { Write-Error "python not found: $Python"; exit 1 }
if (-not $DryRun -and $Identity -and -not (Test-Path $Identity)) {
    Write-Error "ssh key not found: $Identity"; exit 1
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Tmp = Join-Path ([IO.Path]::GetTempPath()) "voc_push_$stamp.db"
$sshOpts = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new")
if ($Identity) { $sshOpts = @("-i", $Identity) + $sshOpts }

try {
    # ---- 1. consistent local snapshot -------------------------------------
    # The helper is written to a temp .py file on purpose: passing multi-line code
    # through `python -c` from PowerShell mangles embedded double quotes (the exact
    # failure this script hit on 2026-09-11: `con.execute(PRAGMA ...` -> SyntaxError).
    Write-Host "[1/5] local snapshot via VACUUM INTO -> $Tmp"
    $TmpPy = Join-Path ([IO.Path]::GetTempPath()) "voc_push_snapshot_$stamp.py"
    $snapPy = @'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
con = sqlite3.connect(src)
con.execute("PRAGMA busy_timeout=60000")
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
con.execute("VACUUM INTO '%s'" % dst.replace("'", "''"))
con.close()
v = sqlite3.connect(dst)
print("snapshot_integrity=%s" % v.execute("PRAGMA integrity_check").fetchone()[0])
print("snapshot_comments=%s" % v.execute("SELECT COUNT(*) FROM comments").fetchone()[0])
v.close()
'@
    [IO.File]::WriteAllText($TmpPy, $snapPy, [Text.Encoding]::ASCII)
    & $Python $TmpPy $DbPath $Tmp
    if ($LASTEXITCODE -ne 0) { throw "local snapshot failed (rc=$LASTEXITCODE)" }
    $tmpSize = (Get-Item $Tmp).Length
    Write-Host ("      snapshot size = {0:N1} MB (source {1:N1} MB)" -f ($tmpSize / 1MB), ($srcSize / 1MB))

    # ---- 2. local hash ----------------------------------------------------
    $localHash = (Get-FileHash -Algorithm SHA256 -Path $Tmp).Hash.ToLower()
    Write-Host "[2/5] local sha256 = $localHash"

    if ($DryRun) {
        Write-Host "[3/5] -DryRun: stop before scp (remote untouched)"
        exit 0
    }

    # ---- 3. upload --------------------------------------------------------
    Write-Host "[3/5] scp -> ${Remote}:/tmp/voc.db.new"
    & scp @sshOpts $Tmp "${Remote}:/tmp/voc.db.new"
    if ($LASTEXITCODE -ne 0) { throw "scp failed (rc=$LASTEXITCODE)" }

    # ---- 4+5. remote verify + in-place restore ----------------------------
    Write-Host "[4/5] remote verify + in-place sqlite3 .backup() -> $RemoteDb"
    $restorePy = @'
import hashlib, sqlite3, sys
new, live = "/tmp/voc.db.new", sys.argv[1]
print("remote_sha256=" + hashlib.sha256(open(new, "rb").read()).hexdigest())
src = sqlite3.connect("file:%s?mode=ro" % new, uri=True)
print("incoming_integrity=" + src.execute("PRAGMA integrity_check").fetchone()[0])
incoming = src.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
print("incoming_comments=%d" % incoming)
if incoming <= 0:
    sys.exit("refuse: incoming comments == 0")
dst = sqlite3.connect(live)
dst.execute("PRAGMA busy_timeout=60000")
print("live_comments_before=%d" % dst.execute("SELECT COUNT(*) FROM comments").fetchone()[0])
src.backup(dst)
print("live_comments_after=%d" % dst.execute("SELECT COUNT(*) FROM comments").fetchone()[0])
print("live_integrity=" + dst.execute("PRAGMA integrity_check").fetchone()[0])
dst.close(); src.close()
print("RESTORE_OK")
'@
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($restorePy))
    $remoteCmd = "echo $b64 | base64 -d > /tmp/voc_restore.py && sudo $RemotePython /tmp/voc_restore.py '$RemoteDb'; " +
                 "rc=`$?; rm -f /tmp/voc_restore.py; exit `$rc"
    $out = & ssh @sshOpts $Remote $remoteCmd
    $rc = $LASTEXITCODE
    $out | ForEach-Object { Write-Host "      $_" }
    if ($rc -ne 0) { throw "remote restore failed (rc=$rc)" }

    $remoteHash = ($out | Select-String -Pattern '^remote_sha256=([0-9a-f]{64})$' |
                   Select-Object -First 1).Matches.Groups[1].Value
    if (-not $remoteHash) { throw "remote sha256 missing in output" }
    if ($remoteHash -ne $localHash) {
        throw "sha256 mismatch: local=$localHash remote=$remoteHash (remote NOT replaced)"
    }
    if (-not ($out -match 'RESTORE_OK')) { throw "RESTORE_OK marker missing" }
    Write-Host "      sha256 verified, remote DB replaced in place"

    & ssh @sshOpts $Remote "rm -f /tmp/voc.db.new"
    if ($LASTEXITCODE -ne 0) { Write-Warning "remote temp cleanup failed (harmless)" }

    if ($RestartService) {
        Write-Host "[5/5] restart voc-web (defensive: drop pooled handles)"
        & ssh @sshOpts $Remote "sudo systemctl restart voc-web && sleep 2 && systemctl is-active voc-web"
        if ($LASTEXITCODE -ne 0) { Write-Warning "voc-web restart reported non-zero" }
    } else {
        Write-Host "[5/5] no service restart (in-place backup keeps live readers correct)"
    }

    Write-Host ""
    Write-Host "DONE: $DbPath -> ${Remote}:$RemoteDb"
    exit 0
}
catch {
    # Write-Host (not Write-Error): with $ErrorActionPreference='Stop' a Write-Error raised
    # inside a catch block becomes another terminating error and hides the real message.
    Write-Host "PUSH FAILED: $_" -ForegroundColor Red
    exit 1
}
finally {
    # exit codes must survive: the Python caller only reads the exit code + stderr/stdout.
    if (Test-Path $Tmp) { Remove-Item -Force $Tmp -ErrorAction SilentlyContinue }
    if ($TmpPy -and (Test-Path $TmpPy)) { Remove-Item -Force $TmpPy -ErrorAction SilentlyContinue }
}
