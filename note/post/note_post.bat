@echo off
rem Daily boat race posting (run by Task Scheduler 17:00-19:00 every 30 min).
rem 1) note: note_post.py (Python + Playwright + Edge) publishes tomorrow's article. No Claude tokens.
rem 2) X: Claude (claude -p) posts to @BoatRace__B and follows, following x_prompt.md.
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
if exist "log\x_done_%TOMORROW%.txt" exit /b 0

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
rem ---- 2) X (Claude). At most 2 runs a day; done once a post or follow line shows up.
set NOTE_URL=
for /f "tokens=3" %%u in ('type "log\posted_%TOMORROW%.txt"') do set NOTE_URL=%%u
if "%NOTE_URL%"=="" exit /b 0
set RUNS=0
if exist "%LOG%" for /f %%n in ('%FINDSTR% /c:"ask_x.txt" "%LOG%" ^| %FIND% /c /v ""') do set RUNS=%%n
if %RUNS% GEQ 2 (
  echo %date% %time% SKIP %TOMORROW% X already tried %RUNS% times >> "%LOG%"
  echo done> "log\x_done_%TOMORROW%.txt"
  exit /b 0
)
echo ==== %date% %time% ask_x.txt %NOTE_URL% >> "%LOG%"
(type ask_x.txt & echo NOTE_URL %NOTE_URL%) | claude -p --model sonnet --chrome --permission-mode auto --permission-prompts none --allowedTools "mcp__claude-in-chrome__*" "WebFetch" "Read" > "log\x_out.txt" 2>&1
echo ==== exit %errorlevel% >> "%LOG%"
type "log\x_out.txt" >> "%LOG%"
%FINDSTR% /r /c:"^XOK %TOMORROW%" /c:"^FOLLOW %TOMORROW%" "log\x_out.txt" >nul && echo done> "log\x_done_%TOMORROW%.txt"
exit /b 0
