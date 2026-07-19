@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo [niconico-watch-app] Process Supervisor starting
"..\..\.venv\Scripts\python.exe" process_supervisor_gui.py
pause
