@echo off
echo ============ Data Platform deploy check ============
echo [1] Service status  (401 = running with auth / 200 = no auth / 000 = down):
curl -s -o nul -w "     HTTP %{http_code}\n" http://127.0.0.1:8688/api/queue
echo.
echo [2] Login test - enter the credentials from deploy/start.sh:
set /p U=  username (default admin): 
if "%U%"=="" set U=admin
set /p P=  password: 
curl -s -u %U%:%P% -o nul -w "     HTTP %{http_code}\n" http://127.0.0.1:8688/api/queue
echo     (200 = login OK / 401 = wrong username or password)
echo.
echo [3] LAN test - open this URL in another PC's browser:
rem Detect the CURRENT LAN IP - never hardcode it. The router hands out DHCP
rem leases, so the address moves (2026-10-08: 192.168.2.102 -> 192.168.2.106),
rem and a stale URL here sends whoever reads it to a different machine.
for /f %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp)[0].IPAddress"') do set LANIP=%%i
if "%LANIP%"=="" set LANIP=192.168.2.106
echo     http://%LANIP%:8688
echo.
pause
