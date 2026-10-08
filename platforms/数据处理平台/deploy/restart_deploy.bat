@echo off
rem Restart the platform service in WSL (stop old, start new with auth)
rem Double click. No administrator needed.
cd /d %~dp0
cd ..
echo Restarting platform service in WSL...
wsl -e bash -lc "bash deploy/start.sh && sleep 2 && echo --- startup log --- && tail -n 6 /tmp/platform.log"
echo.
pause
