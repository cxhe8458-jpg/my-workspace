@echo off
rem ============================================================
rem  Data Platform - LAN deploy setup (run once, as Administrator)
rem  Step 1: portproxy  Windows:8688 -> WSL:8688
rem  Step 2: firewall   allow TCP 8688 from LAN subnet only
rem  HOW TO RUN: right-click this file -> "Run as administrator"
rem  -> click "Yes" on the UAC prompt.
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
echo [1/2] Getting WSL IP...
for /f %%i in ('wsl -e hostname -I') do set WSLIP=%%i
echo       WSL IP: %WSLIP%
echo [2/2] Applying portproxy and firewall rule...
netsh interface portproxy delete v4tov4 listenport=8688 listenaddress=0.0.0.0 >nul 2>&1
netsh interface portproxy add v4tov4 listenport=8688 listenaddress=0.0.0.0 connectport=8688 connectaddress=%WSLIP%
netsh advfirewall firewall delete rule name="DataPlatform-8688" >nul 2>&1
netsh advfirewall firewall add rule name="DataPlatform-8688" dir=in action=allow protocol=TCP localport=8688 remoteip=192.168.2.0/24
rem Enable firewall log so view_access.bat can show real client IPs
netsh advfirewall set currentprofile logging filename "%SystemRoot%\System32\LogFiles\Firewall\pfirewall.log" >nul 2>&1
netsh advfirewall set currentprofile logging allowedconnections enable >nul 2>&1
echo.
rem Print the ACTUAL current LAN IP - a hardcoded one goes stale when DHCP
rem moves it, and then this message points everyone at the wrong machine.
for /f %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp)[0].IPAddress"') do set LANIP=%%i
if "%LANIP%"=="" set LANIP=192.168.2.106
echo Done. Employees open:  http://%LANIP%:8688
echo Access records: run deploy\view_access.bat as administrator
echo If the office has more subnets, add them manually, e.g.:
echo   netsh advfirewall firewall add rule name="DataPlatform-8688-LAN2" dir=in action=allow protocol=TCP localport=8688 remoteip=10.0.0.0/16
pause
