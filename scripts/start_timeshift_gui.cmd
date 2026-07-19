@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set NICONICO_WATCH_APP_ROLE=timeshift
set NICONICO_WATCH_APP_CMD_PID=
cd /d "%~dp0.."

set "PYTHONW_EXE=%CD%\.venv\Scripts\pythonw.exe"
if not exist "%PYTHONW_EXE%" (
  echo ERROR: venv Python not found: %PYTHONW_EXE%
  echo Run: scripts\setup_venv.cmd
  pause
  exit /b 1
)

echo [niconico-watch-app] starting standalone timeshift GUI
start "Niconico Timeshift" /D "%CD%" "%PYTHONW_EXE%" "%CD%\main.py" timeshift
if errorlevel 1 (
  echo ERROR: failed to start standalone timeshift GUI
  pause
  exit /b 1
)
echo [niconico-watch-app] standalone timeshift GUI started
exit /b 0
