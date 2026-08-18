@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0.venv\Scripts\python.exe" "%~dp0app\ai_reaction_tester.py"
endlocal
