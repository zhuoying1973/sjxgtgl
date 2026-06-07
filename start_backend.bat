@echo off
setlocal

REM Always run from this script's directory (project root)
pushd "%~dp0"

set "HOST=0.0.0.0"
set "PORT=8000"

echo [Start Backend] Checking if port %PORT% is already in use...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [Start Backend] Backend already running on port %PORT% (PID=%%a). Nothing to do.
  goto :done
)

echo [Start Backend] Starting backend: http://%HOST%:%PORT%/

if exist ".\.venv\Scripts\python.exe" (
  echo Using venv python: .\.venv\Scripts\python.exe
  ".\.venv\Scripts\python.exe" -m uvicorn backend.main:app --reload --host %HOST% --port %PORT%
) else (
  echo Using system python
  python -m uvicorn backend.main:app --reload --host %HOST% --port %PORT%
)

if errorlevel 1 (
    echo [Start Backend] Error: Backend failed to start.
    pause
)

:done
popd
endlocal
