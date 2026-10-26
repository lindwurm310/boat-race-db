@echo off
rem usage: yosou predict / yosou live / yosou daily  (see analysis\README.md)
setlocal
set PYTHONUTF8=1
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0analysis\yosou.py" %*
) else (
  py -3 "%~dp0analysis\yosou.py" %*
)
exit /b %errorlevel%
