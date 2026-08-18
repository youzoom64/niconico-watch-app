@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo ERROR: venv Python not found: %PYTHON_EXE%
  exit /b 1
)

"%PYTHON_EXE%" main.py tracker --once
endlocal
