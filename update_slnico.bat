@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update_slnico.ps1" %*
if errorlevel 1 (
  echo [ERROR] SlNicoLiveRec download failed.
  pause
  exit /b 1
)
echo [OK] SlNicoLiveRec download completed.
pause
