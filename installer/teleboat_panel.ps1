# テレボート自動投票の操作画面 (iPad から使う) を入れる・最新版にする。何度実行しても大丈夫。
#   1. GitHub から最新のプログラムを取る  2. Python と Playwright を入れる (ブラウザは入っている Edge を使う)
#   3. 操作画面をパソコンの起動時に自動で動かす  4. 今すぐ起動して、デスクトップに iPad で開く URL を置く
#   実行:  powershell -ExecutionPolicy Bypass -File teleboat_panel.ps1 [-Branch main]
#   削除:  powershell -ExecutionPolicy Bypass -File teleboat_panel.ps1 -Remove
param([string]$Branch = "", [switch]$Remove)
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Dest = Join-Path $env:LOCALAPPDATA "BoatRaceYosou"
$App = Join-Path $Dest "teleboat-app"
$Task = "BoatRaceTeleboatPanel"
$desk = [Environment]::GetFolderPath("Desktop")
$UpdBat = Join-Path $desk "自動投票パネルを更新.bat"

function Stop-Panel {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*teleboat-app*server.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    # Playwright が動かしていた Edge も閉じる (普段使いの Edge は閉じない)
    Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*playwright*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Remove) {
    Stop-Panel
    schtasks /Delete /TN $Task /F 2>$null | Out-Null
    Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "テレボート自動投票パネル.lnk") -ErrorAction SilentlyContinue
    Remove-Item $UpdBat, (Join-Path $desk "iPad用の自動投票.url") -ErrorAction SilentlyContinue
    Write-Host "自動投票パネルを止めて、自動起動とデスクトップのファイルを削除しました (設定は $Dest\teleboat に残っています)"
    exit
}

Write-Host "テレボート自動投票パネルを入れます" -ForegroundColor Green
New-Item -ItemType Directory -Force $Dest | Out-Null
$BranchFile = Join-Path $Dest "update_branch.txt"
if ($Branch) { Set-Content $BranchFile $Branch -Encoding ASCII }
else {
    if (Test-Path $BranchFile) { $Branch = (Get-Content $BranchFile -Raw -Encoding UTF8).Trim() }
    if (-not $Branch) { $Branch = "main" }
}

# ---- 1. プログラム ----
Write-Host "1/4 最新のプログラムを取っています ($Branch)"
$tmp = Join-Path $env:TEMP "boatrace_panel_update"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp "src.zip"
try {
    Invoke-WebRequest "https://github.com/lindwurm310/boat-race-db/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing -ErrorAction Stop
} catch { Write-Host "ダウンロードできませんでした: $_" -ForegroundColor Red; exit 1 }
Expand-Archive $zip -DestinationPath $tmp -Force
$root = Get-ChildItem $tmp -Directory | Select-Object -First 1
$src = Join-Path $root.FullName "teleboat"
if (-not (Test-Path (Join-Path $src "server.py"))) { Write-Host "取得したファイルに teleboat がありません" -ForegroundColor Red; exit 1 }
Stop-Panel
New-Item -ItemType Directory -Force $App | Out-Null
Copy-Item (Join-Path $src "*") $App -Recurse -Force
Copy-Item (Join-Path $root.FullName "installer\teleboat_panel.ps1") (Join-Path $Dest "teleboat_panel.ps1") -Force
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

# ---- 2. Python と Playwright ----
Write-Host "2/4 Python と Playwright の確認"
$venv = Join-Path $App ".venv"
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    $py = $null
    $yv = Join-Path $Dest ".venv\Scripts\python.exe"
    if (Test-Path $yv) { $py = $yv }
    elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py" }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
    if (-not $py -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "Python を入れます"
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        $cand = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
        if (Test-Path $cand) { $py = $cand }
    }
    if (-not $py) { Write-Host "Python が見つかりません。https://www.python.org/ から Python 3.12 を入れて、もう一度実行してください。" -ForegroundColor Red; exit 1 }
    if ($py -eq "py") { & py -3 -m venv $venv } else { & $py -m venv $venv }
}
if (-not (Test-Path $vpy)) { Write-Host "Python の仮想環境を作れませんでした" -ForegroundColor Red; exit 1 }
& $vpy -m pip install --upgrade pip --quiet 2>$null
& $vpy -m pip install --upgrade playwright --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "Playwright を入れられませんでした" -ForegroundColor Red; exit 1 }
$vpyw = Join-Path $venv "Scripts\pythonw.exe"

# ---- 3. 自動起動 ----
Write-Host "3/4 パソコンを起動したときに自動で動くようにします"
$start = Join-Path $App "start.bat"
Set-Content $start "@echo off`r`nset PYTHONUTF8=1`r`ncd /d `"$App`"`r`nstart `"`" `"$vpyw`" `"$App\server.py`"" -Encoding Default
# ログオン時のタスクは管理者が要るので、スタートアップ フォルダにショートカットを置く
$startup = [Environment]::GetFolderPath("Startup")
$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut((Join-Path $startup "テレボート自動投票パネル.lnk"))
$lnk.TargetPath = $start
$lnk.WorkingDirectory = $App
$lnk.WindowStyle = 7
$lnk.Save()
# 前の仕組み (毎朝 Edge でテレボートを開いて拡張機能が買う) は二重に買わないように止める
schtasks /Query /TN "BoatRaceTeleboat" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { schtasks /Delete /TN "BoatRaceTeleboat" /F 2>$null | Out-Null }
Set-Content $UpdBat "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"$Dest\teleboat_panel.ps1`"`r`npause" -Encoding Default

# ---- 外出先から: Tailscale Funnel (iPad にアプリは要らない。https で合言葉付きの URL を開く) ----
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
if (-not $ts -and (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) { $ts = "C:\Program Files\Tailscale\tailscale.exe" }
if ($ts) {
    Write-Host "外出先から使えるように Tailscale Funnel を設定します。ブラウザで有効にする画面が出たら「Enable」を押してください"
    & $ts funnel --bg 8766
}

# ---- 4. 起動 ----
Write-Host "4/4 操作画面を起動します"
$urlFile = Join-Path $Dest "teleboat\panel_url.txt"
Remove-Item $urlFile, (Join-Path $Dest "teleboat\panel_url_outside.txt") -ErrorAction SilentlyContinue
# iPad から繋がるように、Windows のファイアウォールで 8766 番を家のネットワーク (プライベート) にだけ開ける (初回だけ管理者の確認が出る)
# 家の Wi-Fi (同じネットワーク) と Tailscale (100.64.0.0/10、外出先から VPN で繋ぐとき) からだけ許す
if (-not (Get-NetFirewallRule -DisplayName "BoatRaceTeleboatPanel2" -ErrorAction SilentlyContinue)) {
    Write-Host "ファイアウォールの設定のため、管理者の確認が出たら「はい」を押してください"
    try {
        Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -Command Remove-NetFirewallRule -DisplayName BoatRaceTeleboatPanel -ErrorAction SilentlyContinue; New-NetFirewallRule -DisplayName BoatRaceTeleboatPanel2 -Direction Inbound -Protocol TCP -LocalPort 8766 -Profile Any -RemoteAddress LocalSubnet,100.64.0.0/10 -Action Allow" -ErrorAction Stop
    } catch { Write-Host "ファイアウォールの設定を飛ばしました (iPad から繋がらないときは、もう一度実行して「はい」を押してください)" -ForegroundColor Yellow }
}
Start-Process -FilePath $vpyw -ArgumentList "`"$App\server.py`"" -WorkingDirectory $App
for ($i = 0; $i -lt 20 -and -not (Test-Path $urlFile); $i++) { Start-Sleep 1 }
Write-Host ""
if (Test-Path $urlFile) {
    $url = (Get-Content $urlFile -Raw -Encoding UTF8).Trim()
    Write-Host "できました。iPad の Safari で次の URL を開いてください (同じ Wi-Fi につないでいるとき):" -ForegroundColor Green
    Write-Host "  $url" -ForegroundColor Yellow
    Write-Host "  (デスクトップの「iPad用の自動投票.url」にも書いてあります。Windows のファイアウォールの確認が出たら「許可」を押してください)"
} else {
    Write-Host "操作画面が起動しませんでした。$App\start.bat をダブルクリックして、もう一度試してください。" -ForegroundColor Red
}
$outFile = Join-Path $Dest "teleboat\panel_url_outside.txt"
Start-Sleep 2
if (Test-Path $outFile) {
    Write-Host "外出先から (iPad の Safari でそのまま開けます):" -ForegroundColor Green
    Write-Host "  $((Get-Content $outFile -Raw -Encoding UTF8).Trim())" -ForegroundColor Yellow
} else {
    Write-Host "外出先から使うには、このパソコンに Tailscale (https://tailscale.com/download) を入れてログインし、もう一度「自動投票パネルを更新」を実行してください。"
}
Write-Host "最新版にするときは、デスクトップの「自動投票パネルを更新」をダブルクリックしてください。"
