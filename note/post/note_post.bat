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
if exist header.png del header.png
powershell -NoProfile -Command "$j = Invoke-RestMethod 'https://lindwurm310.github.io/boat-race-db/note/latest.json'; Invoke-WebRequest ('https://lindwurm310.github.io/boat-race-db/note/' + $j.image) -OutFile header.png"
set ASK=ask_post.txt
if /i "%1"=="test" set ASK=ask_test.txt
echo ==== %date% %time% %ASK% >> "%LOG%"
type "%ASK%" | claude -p --chrome --permission-mode auto --permission-prompts none --allowedTools "mcp__claude-in-chrome__*" "WebFetch" "Read" >> "%LOG%" 2>&1
echo ==== exit %errorlevel% >> "%LOG%"
if /i not "%1"=="test" findstr /r /c:"^OK %TOMORROW% https://note.com/lindwurm310/n/" "%LOG%" >nul && echo posted> "log\posted_%TOMORROW%.txt"
