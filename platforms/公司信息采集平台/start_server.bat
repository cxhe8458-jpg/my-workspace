@echo off
chcp 65001 >nul
pushd "%~dp0"
"C:\Users\13587\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m server.app
popd
pause
