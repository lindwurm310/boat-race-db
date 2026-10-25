@echo off
rem usage: yosou predict --date 2026-10-26   (see analysis\README.md)
py -3 "%~dp0analysis\yosou.py" %*
