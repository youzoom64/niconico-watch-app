@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

set "PY_EXE=%CD%\tools\python311\python.exe"
set "WX_VENV=%CD%\.venv-whisperx"

if not exist "%PY_EXE%" (
  echo [niconico-watch-app] Project-local Python is missing. Run setup_venv.cmd first.
  exit /b 1
)

if not exist "%WX_VENV%\Scripts\python.exe" (
  echo [niconico-watch-app] Creating WhisperX .venv-whisperx...
  "%PY_EXE%" -m virtualenv "%WX_VENV%"
  if errorlevel 1 goto :error
)

echo [niconico-watch-app] Installing CUDA PyTorch for WhisperX...
"%WX_VENV%\Scripts\python.exe" -m pip install --upgrade torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto :error

echo [niconico-watch-app] Installing WhisperX requirements...
"%WX_VENV%\Scripts\python.exe" -m pip install -r requirements-whisperx.txt
if errorlevel 1 goto :error

"%WX_VENV%\Scripts\python.exe" -c "import torch, whisperx; raise SystemExit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 (
  echo [niconico-watch-app] WhisperX CUDA check failed.
  goto :error
)

echo [niconico-watch-app] WhisperX .venv setup completed
exit /b 0

:error
echo [niconico-watch-app] WhisperX .venv setup failed
exit /b 1
