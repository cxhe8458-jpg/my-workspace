@echo off
rem UTF-8 console so Chinese output from WSL is readable (file itself stays ASCII)
chcp 65001 >nul
rem wsl.exe prints its warnings as UTF-16 by default, which shows up as
rem garbage even on a UTF-8 console; WSL_UTF8=1 makes it emit UTF-8.
set WSL_UTF8=1
rem ============================================================
rem  Runs at Windows logon (installed by install_autostart.bat).
rem  1) Refresh portproxy with the CURRENT WSL IP.
rem     WSL2 is NAT'd and gets a new 172.28.x.x on most restarts,
rem     which silently breaks the rule written by deploy_setup.bat:
rem     the service looks healthy inside WSL but nobody on the LAN
rem     can reach it. Re-applying it here makes that self-healing.
rem     Needs admin - the scheduled task runs with highest privileges.
rem  2) Start the platform service inside WSL.
rem ============================================================
rem  enabledelayedexpansion is REQUIRED: %WSLIP% inside an if-block
rem  is expanded when the block is parsed - i.e. before the for-loop
rem  assigns it - so it would always be empty. Use !WSLIP! instead.
setlocal enabledelayedexpansion
cd /d %~dp0
cd ..

net session >nul 2>&1
if %errorlevel% equ 0 (
  for /f %%i in ('wsl -e hostname -I 2^>nul') do set WSLIP=%%i
  netsh interface portproxy delete v4tov4 listenport=8010 listenaddress=0.0.0.0 >nul 2>&1
  netsh interface portproxy add v4tov4 listenport=8010 listenaddress=0.0.0.0 connectport=8010 connectaddress=!WSLIP!
  echo Portproxy refreshed -^> !WSLIP!:8010
) else (
  echo Not elevated - skipping portproxy refresh.
  echo If LAN users cannot connect, run deploy_setup.bat as administrator.
)

wsl -e bash -lc "bash deploy/start.sh"
endlocal
