@echo off
echo =========================================================
echo    AI People Counter & Surveillance Analytics System
echo =========================================================
echo.

cd /d "%~dp0"

echo [1/2] Checking Python environment...
py --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python was not found. Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

echo [2/2] Starting FastAPI Server on http://localhost:8000 ...
echo.
echo Open your web browser and navigate to: http://localhost:8000
echo Press Ctrl+C to stop the server.
echo.

py -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
pause
