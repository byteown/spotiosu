# spotiosu launcher (Windows PowerShell)
# Creates a local virtualenv on first run, installs deps, then starts the bot.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    py -3 -m venv .venv
    & ".venv\Scripts\python.exe" -m pip install --upgrade pip
    & ".venv\Scripts\python.exe" -m pip install -r requirements.txt
}

if (-not (Test-Path "config.json")) {
    Write-Host "config.json not found - copying from config.example.json." -ForegroundColor Yellow
    Copy-Item "config.example.json" "config.json"
    Write-Host "Edit config.json with your osu! IRC + API credentials, then run again." -ForegroundColor Yellow
    exit 1
}

& ".venv\Scripts\python.exe" -m bot @args
