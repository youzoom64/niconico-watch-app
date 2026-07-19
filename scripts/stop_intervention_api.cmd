@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "$listeners = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8794 -State Listen -ErrorAction SilentlyContinue; $listeners | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }"
endlocal
