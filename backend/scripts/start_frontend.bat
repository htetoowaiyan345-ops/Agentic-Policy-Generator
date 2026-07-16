@echo off
setlocal
set PROJECT_DIR=%~dp0..\..
cd /d "%PROJECT_DIR%\frontend\web"
python -u -m http.server 5173 --bind 127.0.0.1
endlocal
