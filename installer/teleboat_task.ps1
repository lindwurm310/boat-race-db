# 毎朝 拡張機能を最新版にして、Edge でテレボートのログイン画面を開くタスクを登録する
# (拡張機能の自動ログイン・全自動購入が動く)。デスクトップに「ボートレース拡張機能を更新」も置く。
#   登録:  powershell -ExecutionPolicy Bypass -File teleboat_task.ps1 [-Time 09:30] [-Branch main]
#   削除:  powershell -ExecutionPolicy Bypass -File teleboat_task.ps1 -Remove
param([string]$Time = "09:30", [string]$Branch = "", [switch]$Remove)
$Dest = Join-Path $env:LOCALAPPDATA "BoatRaceYosou"
$Name = "BoatRaceTeleboat"
$Upd = "BoatRaceExtensionUpdate"
$Bat = Join-Path ([Environment]::GetFolderPath("Desktop")) "ボートレース拡張機能を更新.bat"
if ($Remove) {
    schtasks /Delete /TN $Name /F | Out-Null
    schtasks /Delete /TN $Upd /F | Out-Null
    Remove-Item $Bat -ErrorAction SilentlyContinue
    Write-Host "タスクとデスクトップの更新用ファイルを削除しました"
    exit
}
New-Item -ItemType Directory -Force $Dest | Out-Null
$self = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($f in "update_extension.ps1", "teleboat_task.ps1") {
    $s = Join-Path $self $f
    if ((Test-Path $s) -and ((Resolve-Path $s).Path -ne (Join-Path $Dest $f))) { Copy-Item $s (Join-Path $Dest $f) -Force }
}
if ($Branch) { Set-Content (Join-Path $Dest "update_branch.txt") $Branch -Encoding ASCII }
$ps = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$Dest\update_extension.ps1`""
$t = [datetime]::ParseExact($Time, "HH:mm", $null).AddMinutes(-5).ToString("HH:mm")
schtasks /Create /TN $Upd /SC DAILY /ST $t /TR $ps /F | Out-Null
schtasks /Create /TN $Name /SC DAILY /ST $Time /TR "cmd /c start msedge https://ib.mbrace.or.jp/" /F | Out-Null
Set-Content $Bat "@echo off`r`n$ps`r`npause" -Encoding Default
Write-Host "毎日 $t に拡張機能を最新版にし、$Time に Edge でテレボートを開くタスクを登録しました (パソコンが起動している必要があります)"
Write-Host "デスクトップの「ボートレース拡張機能を更新」をダブルクリックすると、いつでも最新版にできます"
