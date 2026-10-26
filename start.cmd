@echo off
rem ============================================================
rem  Zhenyu - one-click launcher (Windows)
rem  1) start locally only        2) share with colleagues on the same Wi-Fi
rem  Keep this window open while the site is running.
rem ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0backend"

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY set "PY=python"

if not exist "..\data\zhenyu.db" (
  echo [init] first run: creating database and loading sample data...
  "%PY%" run.py init
)

set "MODE=%~1"
if not defined MODE set "MODE=2"
if "%MODE%"=="1" (
  set "HOST=127.0.0.1"
  echo [mode] local only: http://127.0.0.1:8130
) else (
  set "HOST=0.0.0.0"
  echo [mode] LAN share: colleagues open http://YOUR-LAN-IP:8130
)

start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8130/"

"%PY%" run.py serve --host %HOST% --port 8130

echo.
echo Service stopped. Press any key to close.
pause >nul
