@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo [niconico-watch-app] creating .venv
where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -m venv .venv
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python 3.11 is not installed.
    echo Install Python 3.11 and enable "Add Python to PATH".
    pause
    exit /b 1
  )
  python -m venv .venv
)
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
