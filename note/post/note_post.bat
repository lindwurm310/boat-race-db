@echo off
rem Daily note.com post of tomorrow's boat race article (run by Task Scheduler 17:00-19:00 every 30 min).
rem This file is ASCII only; the Japanese instructions are in ask_post.txt / ask_test.txt (UTF-8).
rem Test run (stops before publishing): note_post.bat test
chcp 65001 >nul
cd /d "%~dp0"
set PATH=%USERPROFILE%\.local\bin;%PATH%
if not exist "log" mkdir "log"
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).AddDays(1).ToString('yyyy-MM-dd')"') do set TOMORROW=%%d
set LOG=log\%TOMORROW%.txt
if /i not "%1"=="test" if exist "log\posted_%TOMORROW%.txt" exit /b 0
powershell -NoProfile -Command "try { $j = Invoke-RestMethod 'https://lindwurm310.github.io/boat-race-db/note/latest.json'; if ($j.date -eq '%TOMORROW%') { exit 0 } else { exit 1 } } catch { exit 1 }"
if errorlevel 1 (
  echo %date% %time% SKIP %TOMORROW% article not ready >> "%LOG%"
  exit /b 0
)
rem Token saving: skip without calling Claude if the article is already published (note public API).
if /i not "%1"=="test" (
  powershell -NoProfile -Command "try { $j = Invoke-RestMethod 'https://lindwurm310.github.io/boat-race-db/note/latest.json'; $n = Invoke-RestMethod 'https://note.com/api/v2/creators/lindwurm310/contents?kind=note&page=1'; $hit = $n.data.contents | Where-Object { $_.name -eq $j.title } | Select-Object -First 1; if ($hit) { Write-Output ('OK %TOMORROW% ' + $hit.noteUrl); exit 0 } else { exit 1 } } catch { exit 1 }" > "log\check.txt"
  if not errorlevel 1 (
    echo %date% %time% SKIP %TOMORROW% already published >> "%LOG%"
    type "log\check.txt" >> "%LOG%"
    echo posted> "log\posted_%TOMORROW%.txt"
    exit /b 0
  )
)
rem Token saving: at most 2 Claude runs per day (a failed run is retried once, not every 30 min).
set RUNS=0
if exist "%LOG%" for /f %%n in ('findstr /c:"ask_post.txt" "%LOG%" ^| find /c /v ""') do set RUNS=%%n
if /i not "%1"=="test" if %RUNS% GEQ 2 (
  echo %date% %time% SKIP %TOMORROW% already tried %RUNS% times >> "%LOG%"
  exit /b 0
)
set ASK=ask_post.txt
if /i "%1"=="test" set ASK=ask_test.txt
echo ==== %date% %time% %ASK% >> "%LOG%"
type "%ASK%" | claude -p --model sonnet --chrome --permission-mode auto --permission-prompts none --allowedTools "mcp__claude-in-chrome__*" "WebFetch" "Read" >> "%LOG%" 2>&1
echo ==== exit %errorlevel% >> "%LOG%"
if /i not "%1"=="test" findstr /r /c:"^OK %TOMORROW% https://note.com/lindwurm310/n/" "%LOG%" >nul && echo posted> "log\posted_%TOMORROW%.txt"
