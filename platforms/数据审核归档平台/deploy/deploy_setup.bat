@echo off
rem UTF-8 console so cmd's own localized prompts render correctly
chcp 65001 >nul
rem ============================================================
rem  Audit and Archive Platform - LAN deploy setup
rem  Run ONCE as Administrator.
rem  Step 1: portproxy  Windows:8010 -^> WSL:8010
rem  Step 2: firewall   allow TCP 8010 from LAN subnet only
rem  Step 3: enable firewall logging (for view_access.bat)
rem  HOW TO RUN: right-click this file -^> "Run as administrator"
rem  -^> click "Yes" on the UAC prompt.
rem ------------------------------------------------------------
rem  THIS FILE MUST STAY PURE ASCII WITH CRLF LINE ENDINGS.
rem  cmd reads batch files using codepage 936 and seeks by byte
rem  offset: UTF-8 Chinese or LF-only endings desync the parser -
rem  "rem" prefixes get eaten and comment text runs as commands,
rem  and even "exit /b" stops working so the script runs on.
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
echo [1/3] Getting WSL IP...
rem 2^>nul: wsl.exe may print a localhost-proxy warning; keep it out of the capture
for /f %%i in ('wsl -e hostname -I 2^>nul') do set WSLIP=%%i
if "%WSLIP%"=="" (
  echo       FAILED to get WSL IP. Is WSL running? Try: wsl -e hostname -I
  pause
  exit /b
)
echo       WSL IP: %WSLIP%
echo [2/3] Applying portproxy and firewall rule...
netsh interface portproxy delete v4tov4 listenport=8010 listenaddress=0.0.0.0 >nul 2>&1
netsh interface portproxy add v4tov4 listenport=8010 listenaddress=0.0.0.0 connectport=8010 connectaddress=%WSLIP%
netsh advfirewall firewall delete rule name="AuditArchive-8010" >nul 2>&1
netsh advfirewall firewall add rule name="AuditArchive-8010" dir=in action=allow protocol=TCP localport=8010 remoteip=192.168.2.0/24
echo [3/3] Enabling firewall log so view_access.bat can show client IPs...
netsh advfirewall set currentprofile logging filename "%SystemRoot%\System32\LogFiles\Firewall\pfirewall.log" >nul 2>&1
netsh advfirewall set currentprofile logging allowedconnections enable >nul 2>&1
echo.
rem Print the ACTUAL current LAN IP - a hardcoded one goes stale when DHCP
rem moves it, and then this message points everyone at the wrong machine.
for /f %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp)[0].IPAddress"') do set LANIP=%%i
if "%LANIP%"=="" set LANIP=192.168.2.106
echo Done. Employees open:  http://%LANIP%:8010
echo Login: admin / password - see the deployment doc, section 5.
echo.
echo IMPORTANT: the WSL IP above changes when WSL restarts, which breaks
echo the portproxy rule. Run install_autostart.bat once - it refreshes
echo this rule automatically at every logon.
echo.
echo If the office has more subnets, add them manually, e.g.:
echo   netsh advfirewall firewall add rule name="AuditArchive-8010-LAN2" dir=in action=allow protocol=TCP localport=8010 remoteip=10.0.0.0/16
pause
