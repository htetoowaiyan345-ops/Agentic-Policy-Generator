@echo off
setlocal
set PROJECT_DIR=%~dp0

echo ============================================
echo   Agentic Policy Platform
echo ============================================
echo.

REM ---- Locate npm/node (in order) ----
REM   1. Globally-installed Node 20+ on PATH
REM   2. Portable Node at D:\node-portable\node-v20.18.0-win-x64\
REM   3. Bundled portable Node at <PROJECT_DIR>\node-portable\
set NODE_DIR=
set NODE_BIN=

where npm >nul 2>&1
if "%errorlevel%"=="0" goto node_global
if exist "D:\node-portable\node-v20.18.0-win-x64\npm.cmd" goto node_portable_d
if exist "%PROJECT_DIR%node-portable\node-v20.18.0-win-x64\npm.cmd" goto node_bundled
goto node_missing

:node_global
set NODE_BIN=npm.cmd
echo [run.bat] Found npm on global PATH
goto node_ready

:node_portable_d
set NODE_DIR=D:\node-portable\node-v20.18.0-win-x64
set NODE_BIN=%NODE_DIR%\npm.cmd
echo [run.bat] Using portable Node at %NODE_DIR%
goto node_ready

:node_bundled
set NODE_DIR=%PROJECT_DIR%node-portable\node-v20.18.0-win-x64
set NODE_BIN=%NODE_DIR%\npm.cmd
echo [run.bat] Using bundled portable Node at %NODE_DIR%
goto node_ready

:node_missing
echo [run.bat] ERROR: Node.js / npm not found.
echo [run.bat] Install Node 20+ from https://nodejs.org/ OR
echo [run.bat] extract a portable Node to D:\node-portable\
echo [run.bat] e.g. unzip node-v20.18.0-win-x64.zip to D:\node-portable\
pause
exit /b 1

:node_ready

REM ---- Ensure node_modules is populated (dir may exist but be empty/broken) ----
REM   A non-empty dir is no guarantee vite is installed; check the vite.cmd shim.
if not exist "%PROJECT_DIR%frontend\web\node_modules\.bin\vite.cmd" goto do_install
if not exist "%PROJECT_DIR%frontend\web\node_modules\.bin\svelte-kit.cmd" goto do_install
goto npm_done

:do_install
echo [run.bat] node_modules missing - running npm install first...
if defined NODE_DIR set "PATH=%NODE_DIR%;%PATH%"
pushd "%PROJECT_DIR%frontend\web"
call %NODE_BIN% install
set RC=%errorlevel%
popd
if not "%RC%"=="0" (
  echo [run.bat] npm install failed (code %RC%).
  pause
  exit /b %RC%
)
:npm_done

REM ---- Prepend portable Node dir to OUR PATH so every subsequent
REM      `start "..."` cmd /k child inherits it through win32 environment
REM      propagation. ----
if defined NODE_DIR set "PATH=%NODE_DIR%;%PATH%"

REM ---- Kill stale servers on 8000/5173 ----
echo [run.bat] Stopping any previous servers on ports 8000/5173...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do (
  taskkill /PID %%P /F >nul 2>&1
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5173" ^| findstr LISTENING') do (
  taskkill /PID %%P /F >nul 2>&1
)
timeout /t 1 /nobreak > nul

REM ---- Start the backend API in a new persistent window ----
echo [run.bat] Starting API server (port 8000)...
start "API Server :8000" cmd /k "cd /d ""%PROJECT_DIR%backend"" && set PYTHONPATH=%PROJECT_DIR%\backend && python -m api.server"

REM ---- Wait for API ----
echo [run.bat] Waiting for API server...
timeout /t 3 /nobreak > nul

REM ---- Start the SvelteKit dev server in a new persistent window ----
REM   The portable Node dir is now already on OUR PATH, so children
REM   inherit it automatically. We also `where node` once to verify
REM   before launching -- if `node` still resolves to nothing, fall
REM   back to the absolute path.
echo [run.bat] Starting SvelteKit dev server (port 5173)...
where node >nul 2>&1
if errorlevel 1 (
  if defined NODE_BIN (
    start "Frontend :5173" cmd /k "cd /d ""%PROJECT_DIR%frontend\web"" && ""%NODE_BIN%"" run dev"
  ) else (
    start "Frontend :5173" cmd /k "cd /d ""%PROJECT_DIR%frontend\web"" && npm run dev"
  )
) else (
  start "Frontend :5173" cmd /k "cd /d ""%PROJECT_DIR%frontend\web"" && npm run dev"
)

REM ---- Poll for frontend bind so the user doesn't see ERR_CONNECTION_REFUSED. ----
echo [run.bat] Waiting for web UI on port 5173...
set /a TRIES=0
:wait_web
if %TRIES% GEQ 30 goto web_timeout
netstat -ano | findstr ":5173" | findstr LISTENING >nul 2>&1
if not errorlevel 1 goto web_ready
set /a TRIES+=1
timeout /t 1 /nobreak > nul
goto wait_web
:web_ready
echo [run.bat] Web UI is up on http://localhost:5173/
:web_timeout

REM ---- Open browser ----
echo [run.bat] Opening browser...
start http://localhost:5173/

echo.
echo ============================================
echo   Both servers started.
echo   API:     http://localhost:8000
echo   Web UI:  http://localhost:5173
echo.
echo   If the browser shows "can't reach this page", check the
echo   "Frontend :5173" cmd window for npm errors.
echo.
echo   Close the two server windows to stop.
echo ============================================
echo.
pause
endlocal
