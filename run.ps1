Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "   AI People Counter & Surveillance Analytics System" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "[1/2] Checking Python environment..." -ForegroundColor Yellow
try {
    $pyVersion = py --version
    Write-Host "Found $pyVersion" -ForegroundColor Green
} catch {
    Write-Error "Python not found. Please install Python 3.10+."
    exit 1
}

Write-Host "[2/2] Starting server at http://localhost:8000 ..." -ForegroundColor Yellow
Write-Host "Open your browser to: http://localhost:8000" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to exit.`n"

py -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
