# ボートレース予想ツールのアンインストール (uninstall.bat から呼ばれる)
param([string]$Dest = "$env:LOCALAPPDATA\BoatRaceYosou")
$ErrorActionPreference = "Continue"
schtasks /Delete /F /TN "BoatRaceYosou\daily" 2>$null | Out-Null
$desk = [Environment]::GetFolderPath("Desktop")
Get-ChildItem $desk -Filter "ボートレース *.lnk" | Remove-Item -Force
$path = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", (($path -split ";" | Where-Object { $_ -and $_ -ne $Dest }) -join ";"), "User")
Write-Host "自動実行・ショートカット・PATH を削除しました"
$a = Read-Host "データ (DB・モデル・予想の記録) も含めて $Dest を削除しますか? [y/N]"
if ($a -match '^[yY]') { Remove-Item -Recurse -Force $Dest; Write-Host "削除しました" }
else { Write-Host "$Dest は残しました" }
