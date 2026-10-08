@echo off
rem UTF-8 console so Chinese output from WSL is readable (file itself stays ASCII)
chcp 65001 >nul
rem wsl.exe prints its warnings as UTF-16 by default, which shows up as
rem garbage even on a UTF-8 console; WSL_UTF8=1 makes it emit UTF-8.
set WSL_UTF8=1
rem Restart the platform service in WSL (systemd does the stop/start).
rem Double click. No administrator needed.
rem Use this after changing backend code - routes load at process start.
cd /d %~dp0
cd ..
echo Restarting Audit ^& Archive Platform service in WSL...
echo.
wsl -e bash -lc "bash deploy/start.sh"
echo.
pause
