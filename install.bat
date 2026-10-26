@echo off
rem Boat race yosou installer. Double-click to install.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1" %*
pause
