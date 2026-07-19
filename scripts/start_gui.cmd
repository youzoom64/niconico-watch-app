@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
call "%~dp0ensure_venv.cmd"
if errorlevel 1 exit /b 1
for /f %%P in ('powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"ProcessId=$PID\").ParentProcessId"') do set "NICONICO_WATCH_APP_CMD_PID=%%P"
echo [niconico-watch-app] PyQt6 GUI starting
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
call "%~dp0start_intervention_api.cmd"
"%PYTHON_EXE%" main.py gui
set "EXIT_CODE=%ERRORLEVEL%"
call "%~dp0stop_intervention_api.cmd"
exit /b %EXIT_CODE%
