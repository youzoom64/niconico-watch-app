@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHON_BASE=J:\system_tools\python_bases\3.11.1\python.exe"

if not exist "%PYTHON_BASE%" (
  echo ERROR: Python 3.11 runtime not found: %PYTHON_BASE%
  exit /b 1
)

echo [niconico-watch-app] creating .venv
"%PYTHON_BASE%" -m venv .venv
if errorlevel 1 goto :error

echo [niconico-watch-app] upgrading pip
".venv\Scripts\python.exe" -m pip install -U pip
if errorlevel 1 goto :error

echo [niconico-watch-app] installing requirements
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [niconico-watch-app] .venv setup completed
exit /b 0

:error
echo [niconico-watch-app] .venv setup failed
exit /b 1
