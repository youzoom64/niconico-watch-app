@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
call "%~dp0ensure_venv.cmd"
if errorlevel 1 exit /b 1
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

echo [niconico-watch-app] tracker starting
echo [niconico-watch-app] config: %CD%\config.json
echo [niconico-watch-app] Press Ctrl+C to stop.
"%PYTHON_EXE%" main.py tracker
endlocal
