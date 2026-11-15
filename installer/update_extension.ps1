# 予想の拡張機能 (chrome-extension) を GitHub の最新版に入れ替える。
# 入れ替えると、ブラウザの拡張機能が1分以内に自分で読み込み直す (edge://extensions の ↻ は要らない)。
#   実行:  powershell -ExecutionPolicy Bypass -File update_extension.ps1 [-Branch main]
param([string]$Branch = "")
$ErrorActionPreference = "Stop"
$Dest = Join-Path $env:LOCALAPPDATA "BoatRaceYosou"
$BranchFile = Join-Path $Dest "update_branch.txt"
if (-not $Branch) {
    if (Test-Path $BranchFile) { $Branch = (Get-Content $BranchFile -Raw -Encoding UTF8).Trim() }
    if (-not $Branch) { $Branch = "main" }
}
$tmp = Join-Path $env:TEMP "boatrace_ext_update"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp "src.zip"
$url = "https://github.com/lindwurm310/boat-race-db/archive/refs/heads/$Branch.zip"
Write-Host "最新版を取得しています: $Branch"
Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
Expand-Archive $zip -DestinationPath $tmp -Force
$root = Get-ChildItem $tmp -Directory | Select-Object -First 1
$src = Join-Path $root.FullName "chrome-extension"
if (-not (Test-Path (Join-Path $src "manifest.json"))) { throw "取得したファイルに chrome-extension がありません" }
$ext = Join-Path $Dest "chrome-extension"
New-Item -ItemType Directory -Force $ext | Out-Null
Copy-Item (Join-Path $src "*") $ext -Recurse -Force
foreach ($f in "update_extension.ps1", "teleboat_task.ps1") {
    $s = Join-Path $root.FullName "installer\$f"
    if (Test-Path $s) { Copy-Item $s (Join-Path $Dest $f) -Force }
}
$ver = (Get-Content (Join-Path $ext "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version
Write-Host "拡張機能を v$ver にしました ($ext)。ブラウザが1分以内に読み込み直します。"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
