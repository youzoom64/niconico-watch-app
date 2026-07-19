@echo off
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" exit /b 0
echo [niconico-watch-app] .venv not found. Automatic setup starts now.
call "%~dp0setup_venv.cmd"
exit /b %ERRORLEVEL%
