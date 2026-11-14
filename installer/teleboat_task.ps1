# 毎朝 Edge でテレボートのログイン画面を開くタスクを登録する (拡張機能の自動ログイン・全自動購入が動く)。
#   登録:  powershell -ExecutionPolicy Bypass -File installer\teleboat_task.ps1 [-Time 09:30]
#   削除:  powershell -ExecutionPolicy Bypass -File installer\teleboat_task.ps1 -Remove
param([string]$Time = "09:30", [switch]$Remove)
$Name = "BoatRaceTeleboat"
if ($Remove) {
    schtasks /Delete /TN $Name /F | Out-Null
    Write-Host "タスク $Name を削除しました"
    exit
}
schtasks /Create /TN $Name /SC DAILY /ST $Time /TR "cmd /c start msedge https://ib.mbrace.or.jp/" /F | Out-Null
Write-Host "毎日 $Time に Edge でテレボートを開くタスク $Name を登録しました (パソコンがスリープ・電源オフだと動きません)"
