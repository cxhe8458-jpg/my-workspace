@echo off
rem UTF-8 console so Chinese output from WSL is readable (file itself stays ASCII)
chcp 65001 >nul
rem wsl.exe prints its warnings as UTF-16 by default, which shows up as
rem garbage even on a UTF-8 console; WSL_UTF8=1 makes it emit UTF-8.
set WSL_UTF8=1
rem ============================================================
rem  View access records (who visited the platform)
rem  Run as Administrator (right-click -^> "Run as administrator")
rem  [1] Requests and login attempts from the service log
rem  [2] Real client IPs from the Windows Firewall log (last 25)
rem  [3] Summary: visits per client IP
rem  NOTE: [2]/[3] need the firewall log enabled - run
rem  deploy_setup.bat once first (it enables the firewall log).
rem  NOTE: everyone shares the admin account, so the platform's own
rem  audit history cannot tell WHO did what. These IPs are the only
rem  hint available. See the deployment doc, section 1.
rem ------------------------------------------------------------
rem  THIS FILE MUST STAY PURE ASCII WITH CRLF LINE ENDINGS.
rem  cmd reads batch files using codepage 936 and seeks by byte
rem  offset: UTF-8 Chinese or LF-only endings desync the parser -
rem  "rem" prefixes get eaten and comment text runs as commands.
rem  Anything that needs Chinese goes into a .sh file instead.
rem ============================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo.
  echo  NOT running as administrator.
  echo  Please right-click this file and choose "Run as administrator",
  echo  then confirm the UAC popup.
  echo.
  pause
  exit /b
)
echo ============ Platform access records ============
echo.
echo [1] Service log - requests and login attempts:
cd /d %~dp0
cd ..
wsl -e bash -lc "bash deploy/show_access.sh"
echo.
echo [2] Real client IPs on port 8010 (Windows Firewall log, last 25):
powershell -NoProfile -Command "Get-Content \"$env:SystemRoot\System32\LogFiles\Firewall\pfirewall.log\" | Where-Object { $_ -match '\s8010(\s|$)' -and $_ -notmatch '^#' } | Select-Object -Last 25"
echo.
echo [3] Visits per client IP (all records):
powershell -NoProfile -Command "Get-Content \"$env:SystemRoot\System32\LogFiles\Firewall\pfirewall.log\" | Where-Object { $_ -match '\s8010(\s|$)' -and $_ -notmatch '^#' } | ForEach-Object { ($_ -split ' ')[4] } | Group-Object | Sort-Object Count -Descending | Select-Object Count,Name | Format-Table -AutoSize"
echo.
echo Firewall log file: %SystemRoot%\System32\LogFiles\Firewall\pfirewall.log
pause
