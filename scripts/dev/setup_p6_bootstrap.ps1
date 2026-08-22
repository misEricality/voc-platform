# setup_p6_bootstrap.ps1
#
# 用途：P6 自动化流水线一次性收口（2026-08-22 洁癖）
#
# 一次性脚本，做两件事（可分开执行）：
#   1. A2 · 建 voc-daily-bootstrap release + 上传本地 data/voc.db 为 asset
#      （plan §6.2 步骤 0 第 2 条「首次跑前手动建基线 release」——从 2026-08-19 开工起就一直挂着）
#   2. A3 · git push origin main（推 7 个 commit，含 workflow 改动 → 需 workflow scope）
#
# 依赖：
#   - PowerShell 7+（含 pwsh）
#   - gh CLI 装好并 `gh auth login` 过一次（或把 fine-grained PAT 写到 $env:GH_TOKEN）
#   - git 已配 user.name / user.email
#
# 使用：
#   # 只做 A2（建 bootstrap release）
#   pwsh scripts/dev/setup_p6_bootstrap.ps1 -Step Bootstrap
#
#   # 只做 A3（推 commit）
#   pwsh scripts/dev/setup_p6_bootstrap.ps1 -Step Push
#
#   # 两件都做
#   pwsh scripts/dev/setup_p6_bootstrap.ps1 -Step All
#
#   # Dry-run：打印要执行的命令但不真跑
#   pwsh scripts/dev/setup_p6_bootstrap.ps1 -Step All -DryRun
#
# 最后更新：2026-08-22

[CmdletBinding()]
param(
    [ValidateSet('Bootstrap', 'Push', 'All')]
    [string]$Step = 'All',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot | Split-Path -Parent
Set-Location $repoRoot

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "⚠ $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "✗ $msg" -ForegroundColor Red; exit 1 }

# ---------- 0. 前置检查 ----------

Write-Step "0. 前置检查"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Err "gh CLI 没装。请先 `winget install --id GitHub.cli` 或参考 https://cli.github.com/"
}

if (-not $env:GH_TOKEN) {
    Write-Warn "GH_TOKEN 未设，依赖 `gh auth status`。如未登录会失败。"
    $null = gh auth status 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "gh 未登录。请 `gh auth login` 或设 `$env:GH_TOKEN=<fine-grained PAT>`"
    }
} else {
    Write-Ok "GH_TOKEN 已设（长度=$($env:GH_TOKEN.Length)）"
}

$dbPath = Join-Path $repoRoot 'data\voc.db'
if (-not (Test-Path $dbPath)) {
    Write-Err "本地 DB 不存在：$dbPath"
}
$dbSize = (Get-Item $dbPath).Length
Write-Ok "本地 DB：$dbPath ($dbSize bytes ≈ $([Math]::Round($dbSize/1MB, 1)) MB)"

# ---------- A2 · 建 bootstrap release ----------

function Invoke-Bootstrap {
    param([bool]$DryRun)

    Write-Step "A2 · 建 voc-daily-bootstrap release"

    $tag = 'voc-daily-bootstrap'
    $assetName = 'voc.db'
    $notes = "P6 baseline（plan §6.2 步骤 0 第 2 条；2026-08-22 洁癖收口）。

- 来源：本地 data/voc.db（$dbSize bytes）
- 用途：workflow 每日 cron 从此 release 拉取基线库做增量采集
- 验证：`gh release view $tag --json assets,target_commitish`
"

    if ($DryRun) {
        Write-Host "[DRY] gh release create $tag --title $tag --notes `"...`""
        Write-Host "[DRY] gh release upload $tag $dbPath --name $assetName --clobber"
        return
    }

    # 1. 建 release（已存在则跳过）
    $existing = gh release view $tag 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Warn "Release $tag 已存在（$existing 中已含 asset？）。跳过 create。"
    } else {
        gh release create $tag --title $tag --notes $notes
        if ($LASTEXITCODE -ne 0) { Write-Err "gh release create 失败" }
        Write-Ok "Release $tag 创建成功"
    }

    # 2. 上传 asset
    Write-Host "上传 $([Math]::Round($dbSize/1MB,1)) MB asset（大文件可能需要 1-2 分钟）..."
    gh release upload $tag $dbPath --name $assetName --clobber
    if ($LASTEXITCODE -ne 0) { Write-Err "gh release upload 失败" }
    Write-Ok "Asset 上传成功"

    # 3. 验证
    Write-Host "验证：API 查 release assets..."
    $assets = gh release view $tag --json assets --jq '.assets[] | "\(.name) \(.size)"'
    Write-Host $assets
    $vocAsset = gh release view $tag --json assets --jq '.assets[] | select(.name=="voc.db") | .size'
    if ($vocAsset -and [int]$vocAsset -gt 1000000) {
        Write-Ok "voc.db asset 大小 = $vocAsset bytes（$([Math]::Round([int]$vocAsset/1MB,1)) MB），符合预期"
    } else {
        Write-Warn "voc.db asset 大小异常：$vocAsset"
    }
}

# ---------- A3 · git push ----------

function Invoke-Push {
    param([bool]$DryRun)

    Write-Step "A3 · git push origin main"

    $unpushed = git log --oneline origin/main..HEAD
    if (-not $unpushed) {
        Write-Ok "无未推送 commit"
        return
    }
    Write-Host "未推送 commit:"
    $unpushed | ForEach-Object { Write-Host "  $_" }

    if ($DryRun) {
        Write-Host "[DRY] git push origin main"
        return
    }

    # 检测是否含 workflow 文件改动 → 必须 workflow scope
    $wfChanged = git diff --name-only origin/main..HEAD | Select-String -Path '.github/workflows/' -SimpleMatch
    if ($wfChanged) {
        Write-Warn "本次 push 含 workflow 文件改动（$($wfChanged -join ', ')），需要 PAT 含 workflow scope"
    }

    git push origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Err "git push 失败。若提示 403 且提到 workflow scope，需网页端编辑 workflow 或换含 workflow 的 PAT"
    }
    Write-Ok "git push 成功"

    # 验证远端
    Write-Host "验证：远端 head + workflow 含 test job"
    $remoteHead = git log --oneline -1 origin/main
    Write-Host "  origin/main HEAD: $remoteHead"

    $wfContent = gh api repos/misEricality/voc-platform/contents/.github/workflows/daily-collect.yml --jq '.content' 2>&1 | ForEach-Object { [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($_ -replace '\s','')) }
    if ($wfContent -match '^\s*test:\s*$') {
        Write-Ok "远端 workflow 含 test: job"
    } else {
        Write-Warn "远端 workflow 未检测到 test: job（可能 push 未生效或 push 走的是 PAT 但 GitHub 阻了 workflow 文件改动）"
    }
}

# ---------- 主流程 ----------

switch ($Step) {
    'Bootstrap' { Invoke-Bootstrap -DryRun $DryRun }
    'Push'      { Invoke-Push      -DryRun $DryRun }
    'All'       {
        Invoke-Bootstrap -DryRun $DryRun
        Invoke-Push      -DryRun $DryRun
    }
}

Write-Step "完成"
Write-Host "  - Bootstrap release：gh release view voc-daily-bootstrap --json assets"
Write-Host "  - 远端 head：git log --oneline -1 origin/main"
Write-Host "  - 明天 09:15 北京时间 cron 跑完后查：gh api repos/misEricality/voc-platform/actions/runs?per_page=1 --jq '.workflow_runs[].jobs[] | .name'"