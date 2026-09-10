# 方案③ 静态快照发布脚本（EdgeOne Pages）
#
# 职责：导出快照（export_static_snapshot.py）→ 用 EdgeOne CLI 发布到 EdgeOne Pages。
# 手动 / 计划任务两用。自动发布走**独立计划任务** VOC-Local-Publish-Snapshot（04:30），
# 由 register_local_collect_task.ps1 一并注册（不并入 daily_incremental_collect.py：
# 采集结果判定与发布链路解耦，发布失败不影响采集退出码）。
#
# 用法：
#   # 首次手动：导出 + 生产环境发布
#   powershell -File scripts\ops\publish_static_snapshot.ps1 -Name voc-platform
#
#   # 预览环境（不影响线上）
#   powershell -File scripts\ops\publish_static_snapshot.ps1 -Name voc-platform -Preview
#
#   # 只重新发布（跳过导出）
#   powershell -File scripts\ops\publish_static_snapshot.ps1 -Name voc-platform -SkipExport
#
# Token 配置（三选一，优先级从高到低）：
#   1. -Token <api-token> 参数
#   2. 环境变量 EDGEONE_API_TOKEN
#   3. 项目根 .env 文件的 EDGEONE_API_TOKEN=... 行
#   未提供且 CLI 未登录时，edgeone 会交互式要求登录（计划任务场景请务必配 token）。
#
# EdgeOne CLI 安装（一次性）：npm install -g edgeone
# 退出码：0 成功；2 配置缺失；3 导出失败；4 发布失败
param(
    [Parameter(Mandatory = $true)]
    [string]$Name,                      # EdgeOne Pages 项目名（不存在则自动创建）
    [string]$Token,                     # EdgeOne API Token（可选，见头注）
    [switch]$Preview,                   # 发布到 preview 环境（默认 production）
    [switch]$SkipExport,                # 跳过导出，直接发布现有 dist
    [string]$Dist,                      # 快照目录（默认 <root>/data/exports/snapshot）
    [string]$Python                     # Python 解释器（默认 .venv-ml）
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
# 默认值不放 param 块：$PSScriptRoot 在部分调用方式（包装器/管道）下为空，Join-Path 会炸
if (-not $Dist) { $Dist = Join-Path $Root "data\exports\snapshot" }
if (-not $Python) { $Python = Join-Path $Root ".venv-ml\Scripts\python.exe" }
# 保留未 Resolve 的绝对路径：导出用 --out 传它（冷启动时 Resolve-Path 会返回空）
$DistRaw = $Dist
if (-not [System.IO.Path]::IsPathRooted($DistRaw)) { $DistRaw = Join-Path $Root $DistRaw }
$Dist = Resolve-Path $DistRaw -ErrorAction SilentlyContinue

function Write-Log([string]$msg) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
}

# ---------- 1. 前置检查 ----------
# 顺序要求：非 -SkipExport 时 dist 由本次导出生成，**不能提前校验**（冷启动会误判 exit 2）。
# 2026-09-10 修复：此前先查 dist 再导出，导致产物被清理后无法靠自身重新导出。
if ($SkipExport) {
    if (-not $Dist -or -not (Test-Path (Join-Path $Dist "index.html"))) {
        Write-Log "[FAIL] 快照目录不存在或缺少 index.html：$Dist（-SkipExport 需要已有产物）"
        exit 2
    }
} elseif (-not (Test-Path $Python)) {
    Write-Log "[FAIL] 未找到 Python 解释器：$Python"
    exit 2
}

# ---------- 2. 导出快照 ----------
if (-not $SkipExport) {
    Write-Log "开始导出静态快照…"
    # 显式传 --out，保证自定义 -Dist 时导出目录 == 发布目录（默认同为 data\exports\snapshot）
    & $Python (Join-Path $Root "scripts\ops\export_static_snapshot.py") --out $DistRaw
    if ($LASTEXITCODE -ne 0) {
        Write-Log "[FAIL] 快照导出失败（exit $LASTEXITCODE）"
        exit 3
    }
    # 导出后重新解析（冷启动时 $Dist 之前为空）
    $Dist = Resolve-Path $DistRaw -ErrorAction SilentlyContinue
    if (-not $Dist -or -not (Test-Path (Join-Path $Dist "index.html"))) {
        Write-Log "[FAIL] 导出结束但未找到 index.html：$DistRaw"
        exit 3
    }
    Write-Log "快照导出完成：$Dist"
}

# ---------- 3. 组装 edgeone 命令 ----------
$envName = if ($Preview) { "preview" } else { "production" }

if (-not $Token) { $Token = $env:EDGEONE_API_TOKEN }
if (-not $Token -and (Test-Path (Join-Path $Root ".env"))) {
    foreach ($line in Get-Content (Join-Path $Root ".env")) {
        if ($line -match '^\s*EDGEONE_API_TOKEN\s*=\s*(.+?)\s*$') {
            $Token = $Matches[1].Trim('"', "'")
            break
        }
    }
}

$edgeone = Get-Command edgeone -ErrorAction SilentlyContinue
$args = @()
if ($edgeone) {
    $args = @("pages", "deploy", $Dist, "-n", $Name, "-e", $envName)
    if ($Token) { $args += @("-t", $Token) }
} else {
    # 未安装全局 CLI → 走 npx（要求 node 在 PATH）
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) {
        Write-Log "[FAIL] 未找到 edgeone CLI 且无 node/npx。安装：npm install -g edgeone"
        exit 2
    }
    Write-Log "未找到全局 edgeone，改用 npx -y edgeone…"
    $args = @("-y", "edgeone", "pages", "deploy", $Dist, "-n", $Name, "-e", $envName)
    if ($Token) { $args += @("-t", $Token) }
    $edgeone = Get-Command npx
}

Write-Log "发布到 EdgeOne Pages（env=$envName, project=$Name）…"
& $edgeone.Path @args
if ($LASTEXITCODE -ne 0) {
    Write-Log "[FAIL] EdgeOne Pages 发布失败（exit $LASTEXITCODE）"
    exit 4
}

Write-Log "===== 发布完成：项目 $Name（$envName）====="
exit 0
