@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo === Optional WhisperX CUDA setup ===
echo This component requires a supported NVIDIA GPU and driver.
echo The main application and Faster-Whisper do not require this installation.

if not exist ".venv\Scripts\python.exe" (
  echo Preparing the basic environment first...
  call "%~dp0setup.bat" --no-pause
  if errorlevel 1 goto :error
)

call "%~dp0scripts\setup_whisperx_venv.cmd"
if errorlevel 1 goto :error

echo WhisperX setup complete.
if /I "%~1"=="--no-pause" exit /b 0
pause
exit /b 0

:error
echo [ERROR] WhisperX setup failed. The main application can still run without it.
if /I "%~1"=="--no-pause" exit /b 1
pause
exit /b 1
