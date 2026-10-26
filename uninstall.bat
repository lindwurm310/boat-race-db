@echo off
rem Boat race yosou uninstaller.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\uninstall.ps1" %*
pause
