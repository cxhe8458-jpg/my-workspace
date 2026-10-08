@echo off
rem ============================================================
rem  View access records (who visited the platform)
rem  Run as Administrator (right-click -> Run as administrator)
rem  [1] Recent requests from uvicorn log (time / path / status)
rem  [2] Real client IPs from Windows Firewall log (last 25 hits)
rem  [3] Summary: how many visits per client IP
rem  NOTE: [2]/[3] need the firewall log to be enabled - run
rem  deploy_setup.bat once first (it enables the firewall log).
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
echo [1] Recent requests (uvicorn log, last 15):
wsl -e bash -lc "grep -E 'HTTP/1' /tmp/platform.log | tail -n 15"
echo.
echo [2] Real client IPs on port 8688 (Windows Firewall log, last 25):
powershell -NoProfile -Command "Get-Content \"$env:SystemRoot\System32\LogFiles\Firewall\pfirewall.log\" | Where-Object { $_ -match '\s8688(\s|$)' -and $_ -notmatch '^#' } | Select-Object -Last 25"
echo.
echo [3] Visits per client IP (all records):
powershell -NoProfile -Command "Get-Content \"$env:SystemRoot\System32\LogFiles\Firewall\pfirewall.log\" | Where-Object { $_ -match '\s8688(\s|$)' -and $_ -notmatch '^#' } | ForEach-Object { ($_ -split ' ')[4] } | Group-Object | Sort-Object Count -Descending | Select-Object Count,Name | Format-Table -AutoSize"
echo.
echo Firewall log file: %SystemRoot%\System32\LogFiles\Firewall\pfirewall.log
pause
