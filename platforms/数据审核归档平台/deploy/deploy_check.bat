@echo off
rem UTF-8 console so Chinese output from WSL is readable (file itself stays ASCII)
chcp 65001 >nul
rem wsl.exe prints its warnings as UTF-16 by default, which shows up as
rem garbage even on a UTF-8 console; WSL_UTF8=1 makes it emit UTF-8.
set WSL_UTF8=1
rem ============================================================
rem  Audit and Archive Platform - deploy check (no admin needed)
rem  THIS FILE MUST STAY PURE ASCII WITH CRLF LINE ENDINGS - see
rem  deploy_setup.bat header for why.
rem  [1] service alive?   401 = up WITH auth (expected)
rem                       200 = up but NO auth  <-- do not expose!
rem                       000 = down
rem  [2] login test with your real credentials
rem  [3] portproxy target vs current WSL IP - the #1 cause of
rem      "works on the server, LAN users cannot connect"
rem ============================================================
echo ============ Audit ^& Archive Platform deploy check ============
echo.
echo [1] Service status:
curl -s --noproxy "*" -o nul -w "     HTTP %{http_code}   (401 = up with auth, 200 = NO AUTH, 000 = down)\n" http://127.0.0.1:8010/api/version
echo.
echo [2] Login test:
set /p U=  username (default admin):
if "%U%"=="" set U=admin
set /p P=  password:
curl -s --noproxy "*" -c "%TEMP%\aa_cookie.txt" -o nul -w "     login  HTTP %{http_code}   (200 = OK / 401 = wrong user or password)\n" -X POST http://127.0.0.1:8010/api/login -H "Content-Type: application/json" -d "{\"user\":\"%U%\",\"password\":\"%P%\"}"
curl -s --noproxy "*" -b "%TEMP%\aa_cookie.txt" -o nul -w "     authed HTTP %{http_code}   (200 = session works)\n" http://127.0.0.1:8010/api/companies
del "%TEMP%\aa_cookie.txt" >nul 2>&1
echo.
echo [3] Portproxy target vs current WSL IP - these MUST match:
for /f %%i in ('wsl -e hostname -I 2^>nul') do set WSLIP=%%i
echo      current WSL IP : %WSLIP%
echo      portproxy rule :
netsh interface portproxy show v4tov4 | findstr /C:"8010"
echo      If they differ, LAN users cannot connect. Fix by running
rem      deploy_setup.bat as administrator (or just log out and back
rem      in, if install_autostart.bat is installed).
echo.
echo [4] LAN test - open this URL from another PC:
rem Detect the CURRENT LAN IP - never hardcode it. DHCP moves it (2026-10-08:
rem 192.168.2.102 -> 192.168.2.106), and a stale URL here sends whoever reads
rem it to whatever machine now holds the old address.
for /f %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp)[0].IPAddress"') do set LANIP=%%i
if "%LANIP%"=="" set LANIP=192.168.2.106
echo      http://%LANIP%:8010
echo.
pause
