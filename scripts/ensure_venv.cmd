@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo [niconico-watch-app] .venv not found. Automatic setup starts now.
  call "%~dp0setup_venv.cmd"
  if errorlevel 1 exit /b 1
)
if not exist ".venv-whisperx\Scripts\python.exe" (
  echo [niconico-watch-app] .venv-whisperx not found. Automatic setup starts now.
  call "%~dp0setup_whisperx_venv.cmd"
  if errorlevel 1 exit /b 1
)
exit /b 0
