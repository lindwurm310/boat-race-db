# ボートレース予想ツール (yosou) のインストーラー
#   install.bat から呼ばれる。管理者権限は不要 (ユーザーのフォルダに入れる)。
#   1. Python が無ければ入れる  2. インストール先にファイルをコピー  3. ライブラリを入れる
#   4. DB の取得・更新・モデルの学習  5. ショートカット・PATH・毎朝の自動実行の登録
param([string]$Dest = "$env:LOCALAPPDATA\BoatRaceYosou", [switch]$Quiet)

# Windows PowerShell 5.1 は、外部コマンド (py, pip など) が標準エラーに書いただけで止まってしまうので
# 全体は Continue にして、止めたい所は -ErrorAction Stop / 終了コードの確認 / throw で止める
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest を速くする
$Src = Split-Path -Parent $PSScriptRoot
$PyVersion = "3.12.10"

function Step($msg) { Write-Host ""; Write-Host "==== $msg" -ForegroundColor Cyan }
function Ask($msg, $default = $true) {
    if ($Quiet) { return $default }
    $d = if ($default) { "Y/n" } else { "y/N" }
    $a = Read-Host "$msg [$d]"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a -match '^[yYはい]'
}

function Find-Python {
    # 3.10 以上の 64bit Python を探す
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.13", "3.12", "3.11", "3.10") {
            try { $p = (& py "-$v" -c "import sys; print(sys.executable)" 2>$null) } catch { $p = $null }
            if ($LASTEXITCODE -eq 0 -and $p) { $cands += ([string]$p).Trim() }
        }
    }
    $cands += @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe")
    foreach ($p in $cands) {
        if (-not (Test-Path $p)) { continue }
        try { $ok = (& $p -c "import sys, struct; print(sys.version_info >= (3, 10) and struct.calcsize('P') == 8)" 2>$null) } catch { $ok = $null }
        if ($ok -and ([string]$ok).Trim() -eq "True") { return $p }
    }
    return $null
}

Write-Host "ボートレース予想ツールをインストールします" -ForegroundColor Green
Write-Host "インストール先: $Dest"
Write-Host "(過去の成績データが約4GBあります。空き容量が10GB以上あるか確認してください)"

# ---- 1. Python ----
Step "1/6 Python の確認"
$py = Find-Python
if (-not $py) {
    Write-Host "Python が見つからないので入れます (Python $PyVersion)"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    }
    $py = Find-Python
    if (-not $py) {
        $exe = Join-Path $env:TEMP "python-$PyVersion-amd64.exe"
        Write-Host "python.org からダウンロードしています..."
        Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-amd64.exe" -OutFile $exe -ErrorAction Stop
        Start-Process $exe -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0" -Wait
        $py = Find-Python
    }
    if (-not $py) { throw "Python を入れられませんでした。https://www.python.org/ から Python 3.12 を入れて、もう一度実行してください。" }
}
Write-Host "Python: $py"

# ---- 2. ファイルのコピー ----
Step "2/6 ファイルのコピー"
New-Item -ItemType Directory -Force $Dest -ErrorAction Stop | Out-Null
if ((Resolve-Path $Src).Path -ne (Resolve-Path $Dest).Path) {
    foreach ($item in "analysis", "installer", "chrome-extension", "fetch.py", "build_db.py", "requirements.txt", "yosou.bat", "README.md") {
        $s = Join-Path $Src $item
        if (Test-Path $s) { Copy-Item $s -Destination $Dest -Recurse -Force -ErrorAction Stop }
    }
}
New-Item -ItemType Directory -Force (Join-Path $Dest "predictions") | Out-Null
# 毎朝の自動実行用: 成績の取り込み・答え合わせ・(7日ごとに)学習し直し → 今日の予想
$daily = @"
@echo off
cd /d "%~dp0"
echo ===== %date% %time% >> "%~dp0daily.log"
call "%~dp0yosou.bat" daily >> "%~dp0daily.log" 2>&1
call "%~dp0yosou.bat" predict >> "%~dp0daily.log" 2>&1
"@
Set-Content -Path (Join-Path $Dest "daily.bat") -Value $daily -Encoding ASCII -ErrorAction Stop

# ---- 3. ライブラリ ----
Step "3/6 ライブラリのインストール (数分かかります)"
$venv = Join-Path $Dest ".venv"
if (-not (Test-Path "$venv\Scripts\python.exe")) { & $py -m venv $venv }
if (-not (Test-Path "$venv\Scripts\python.exe")) { throw "Python の仮想環境 (.venv) を作れませんでした" }
$vpy = "$venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r (Join-Path $Dest "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { throw "ライブラリのインストールに失敗しました" }

$yosou = Join-Path $Dest "yosou.bat"
function Yosou { & $yosou @args; if ($LASTEXITCODE -ne 0) { Write-Host "(終了コード $LASTEXITCODE)" -ForegroundColor Yellow } }

# ---- 4. データとモデル ----
Step "4/6 過去の成績データ (DB) の準備"
if (Test-Path (Join-Path $Dest "boatrace.sqlite")) {
    Write-Host "DB はもうあります。最新の日まで取り込みます。"
} else {
    Write-Host "GitHub から DB をダウンロードします (約1.1GB、展開後 約4GB)"
    Yosou setup
    if (-not (Test-Path (Join-Path $Dest "boatrace.sqlite"))) { throw "DB をダウンロードできませんでした。ネットにつながっているか確認して、もう一度 install.bat を実行してください。" }
}
Yosou update

Step "5/6 予想モデルの学習"
if (Test-Path (Join-Path $Dest "model\win.txt")) {
    Write-Host "モデルはもうあります (毎朝の自動実行で 7 日ごとに学習し直します)"
} elseif (Ask "モデルを今から学習しますか? (40分〜1時間半ほど。あとで yosou train でも可)") {
    Yosou train
}

# ---- 5. ショートカット・PATH・自動実行 ----
Step "6/6 ショートカットと自動実行の登録"
$sh = New-Object -ComObject WScript.Shell
$desk = [Environment]::GetFolderPath("Desktop")
$links = @(
    @("ボートレース 今日の予想", "/k `"`"$yosou`" predict`""),
    @("ボートレース 直前のおすすめ", "/k `"`"$yosou`" live`""),
    @("ボートレース 答え合わせ (昨日)", "/k `"`"$yosou`" check --date yesterday`""),
    @("ボートレース 拡張機能用サーバー", "/k `"`"$yosou`" serve`""),
    @("ボートレース 選手の成績", "/k `"`"$yosou`" racer && start `"`" `"$Dest\predictions`"`"")
)
foreach ($l in $links) {
    $lnk = $sh.CreateShortcut((Join-Path $desk "$($l[0]).lnk"))
    $lnk.TargetPath = "$env:ComSpec"
    $lnk.Arguments = $l[1]
    $lnk.WorkingDirectory = $Dest
    $lnk.Save()
}
$lnk = $sh.CreateShortcut((Join-Path $desk "ボートレース 予想フォルダ.lnk"))
$lnk.TargetPath = Join-Path $Dest "predictions"
$lnk.Save()
Write-Host "デスクトップにショートカットを作りました"

$parts = @(([string][Environment]::GetEnvironmentVariable("Path", "User")) -split ";" | Where-Object { $_ })
if ($parts -notcontains $Dest) {
    [Environment]::SetEnvironmentVariable("Path", (($parts + $Dest) -join ";"), "User")
    Write-Host "コマンドプロンプトで yosou と打てば使えるようにしました (新しく開いた画面から)"
}

if (Ask "毎朝 7:00 に、成績の取り込み・答え合わせ・今日の予想を自動で実行しますか?") {
    schtasks /Create /F /SC DAILY /ST 07:00 /TN "BoatRaceYosou\daily" /TR "`"$Dest\daily.bat`"" | Out-Null
    Write-Host "タスクスケジューラに登録しました (BoatRaceYosou\daily、結果は $Dest\daily.log)"
}

# ---- Chrome 拡張機能 ----
$startup = [Environment]::GetFolderPath("Startup")
if (Ask "パソコンを起動したとき、Chrome 拡張機能用のサーバーを自動で起動しますか? (画面は最小化されます)") {
    $lnk = $sh.CreateShortcut((Join-Path $startup "ボートレース 拡張機能用サーバー.lnk"))
    $lnk.TargetPath = "$env:ComSpec"
    $lnk.Arguments = "/k `"`"$yosou`" serve`""
    $lnk.WorkingDirectory = $Dest
    $lnk.WindowStyle = 7
    $lnk.Save()
    Write-Host "スタートアップに登録しました"
}
$ext = Join-Path $Dest "chrome-extension"
Write-Host ""
Write-Host "Chrome 拡張機能の入れ方 (最初の1回だけ):" -ForegroundColor Cyan
Write-Host "  1. Chrome のアドレス欄に chrome://extensions と入れて開く"
Write-Host "  2. 右上の「デベロッパー モード」をオンにする"
Write-Host "  3. 「パッケージ化されていない拡張機能を読み込む」を押して、次のフォルダを選ぶ"
Write-Host "       $ext"
Write-Host "  4. デスクトップの「ボートレース 拡張機能用サーバー」を起動したまま、"
Write-Host "     boatrace.jp のレースのページ (出走表・直前情報・オッズ) を開くと右下におすすめが出ます"
if (Ask "拡張機能のフォルダを開きますか? (手順3で選ぶフォルダ)") {
    Start-Process explorer.exe $ext
    try { Start-Process chrome.exe "chrome://extensions" } catch { }
}

Write-Host ""
Write-Host "インストールが終わりました。" -ForegroundColor Green
Write-Host "  朝: デスクトップの「ボートレース 今日の予想」"
Write-Host "  レース前: 「ボートレース 直前のおすすめ」(締切30分以内のレース)"
Write-Host "  Chrome で見るなら: 「ボートレース 拡張機能用サーバー」を起動して boatrace.jp を開く"
Write-Host "  使い方: $Dest\analysis\README.md"
