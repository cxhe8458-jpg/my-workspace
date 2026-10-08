@echo off
rem Auto-start the platform when the Windows user logs in.
rem Installed into Task Scheduler by install_autostart.bat.
cd /d %~dp0
cd ..
wsl -e bash -lc "bash deploy/start.sh"
