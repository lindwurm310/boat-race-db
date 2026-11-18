@echo off
rem Daily boat race posting (run by Task Scheduler 17:00-19:00 every 30 min).
rem 1) note: note_post.py (Python + Playwright + Edge) publishes tomorrow's article. No Claude tokens.
rem 2) X is not done here (Python tasks BoatRace-X-Poster / BoatRace-X-Follow). No Claude is called.
rem This file is ASCII only; the Japanese instructions are in ask_x.txt / x_prompt.md (UTF-8).
rem Test run (note only, stops before publishing, no X): note_post.bat test
chcp 65001 >nul
cd /d "%~dp0"
set PATH=%USERPROFILE%\.local\bin;%PATH%
set PYTHONIOENCODING=utf-8
if not exist "log" mkdir "log"
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).AddDays(1).ToString('yyyy-MM-dd')"') do set TOMORROW=%%d
set LOG=log\%TOMORROW%.txt
set FIND=%SystemRoot%\System32\find.exe
set FINDSTR=%SystemRoot%\System32\findstr.exe

if /i "%1"=="test" (
  echo ==== %date% %time% note_post.py post --dry >> "%LOG%"
  set NOTE_FORCE=1
  py note_post.py post --dry >> "%LOG%" 2>&1
  echo ==== test done >> "%LOG%"
  exit /b 0
)

rem ---- 1) note (Python). At most 3 tries a day so failed runs do not pile up drafts.
if exist "log\posted_%TOMORROW%.txt" goto xstep
set NRUNS=0
if exist "%LOG%" for /f %%n in ('%FINDSTR% /c:"note_post.py post" "%LOG%" ^| %FIND% /c /v ""') do set NRUNS=%%n
if %NRUNS% GEQ 3 (
  echo %date% %time% SKIP %TOMORROW% note already tried %NRUNS% times >> "%LOG%"
  exit /b 0
)
echo ==== %date% %time% note_post.py post >> "%LOG%"
py note_post.py post > "log\note_out.txt" 2>&1
type "log\note_out.txt" >> "%LOG%"
%FINDSTR% /r /c:"^OK %TOMORROW% https://note.com/lindwurm310/n/" "log\note_out.txt" > "log\posted_%TOMORROW%.txt"
if errorlevel 1 (
  del "log\posted_%TOMORROW%.txt"
  exit /b 0
)

:xstep
rem X posts (results + note announcement) and follows are done by the Python tasks BoatRace-X-Poster / BoatRace-X-Follow.
exit /b 0
