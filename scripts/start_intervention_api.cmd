@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
call "%~dp0ensure_venv.cmd"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8794 -State Listen -ErrorAction SilentlyContinue) { exit 0 } exit 1"
if "%ERRORLEVEL%"=="0" (
  echo [niconico-watch-app] Intervention API already running on 127.0.0.1:8794
  endlocal & exit /b 0
)
set "PYTHON=%CD%\.venv\Scripts\python.exe"
set "LOG_DIR=%CD%\data\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG=%LOG_DIR%\intervention_api.log"
set "ERR=%LOG_DIR%\intervention_api.err.log"
start "Niconico Watch Intervention API" /b "%PYTHON%" main.py api --host 127.0.0.1 --port 8794 > "%LOG%" 2> "%ERR%"
endlocal
