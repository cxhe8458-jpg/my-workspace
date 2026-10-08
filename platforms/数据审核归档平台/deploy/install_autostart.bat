@echo off
rem UTF-8 console so cmd's own localized prompts render correctly
chcp 65001 >nul
rem ============================================================
rem  Install auto-start: the platform starts automatically at logon,
rem  and the portproxy is refreshed with the current WSL IP.
rem  Run as Administrator (right-click -> Run as administrator)
rem  Uninstall later:  schtasks /delete /tn AuditArchive /f
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
rem /rl highest is what lets the logon task rewrite the portproxy rule
rem without a UAC prompt every time. Without it the refresh is skipped
rem and LAN access breaks whenever the WSL IP changes.
schtasks /create /tn "AuditArchive" /tr "\"%~dp0autostart_wsl.bat\"" /sc onlogon /ru "%USERNAME%" /rl highest /it /f
if %errorlevel% equ 0 (
  echo.
  echo  Auto-start installed. At every Windows logon it will:
  echo    1. refresh the portproxy with the current WSL IP
  echo    2. start the platform service inside WSL
  echo  Uninstall:  schtasks /delete /tn AuditArchive /f
  echo.
  echo  Test it now without logging out:  schtasks /run /tn AuditArchive
) else (
  echo.
  echo  Install failed. Screenshot this window and send it for help.
)
pause
