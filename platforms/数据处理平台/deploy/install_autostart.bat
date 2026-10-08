@echo off
rem ============================================================
rem  Install auto-start: platform starts automatically at logon
rem  Run as Administrator (right-click -> Run as administrator)
rem  Uninstall later:  schtasks /delete /tn DataPlatform /f
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
schtasks /create /tn "DataPlatform" /tr "\"%~dp0autostart_wsl.bat\"" /sc onlogon /ru "%USERNAME%" /it /f
if %errorlevel% equ 0 (
  echo.
  echo  Auto-start installed. The platform will start automatically
  echo  every time you log in to Windows.
  echo  Uninstall:  schtasks /delete /tn DataPlatform /f
) else (
  echo.
  echo  Install failed. Screenshot this window and send it for help.
)
pause
