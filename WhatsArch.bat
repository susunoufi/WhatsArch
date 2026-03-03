@echo off
chcp 65001 >nul 2>&1
title WhatsArch
cd /d "%~dp0"

:: Check if setup has been run
if not exist "venv\Scripts\python.exe" (
    echo WhatsArch is not set up yet.
    echo Running setup first...
    echo.
    call setup.bat
    if %errorlevel% neq 0 exit /b 1
)

:: Activate venv and run
call venv\Scripts\activate.bat
python run.py %*
