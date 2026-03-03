@echo off
chcp 65001 >nul 2>&1
title WhatsArch - Setup
color 0A

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║        WhatsArch - Installation          ║
echo  ║   WhatsApp Chat Archive Search Engine    ║
echo  ╚══════════════════════════════════════════╝
echo.

:: ─── Step 1: Check Python ───────────────────────────────────────────
echo [1/4] Checking Python...

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo   Python not found!
    echo   Downloading Python installer...
    echo.

    :: Try winget first
    where winget >nul 2>&1
    if %errorlevel% equ 0 (
        echo   Installing Python via winget...
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        if %errorlevel% neq 0 (
            echo   [ERROR] Failed to install Python via winget.
            echo   Please install Python 3.10+ manually from https://www.python.org/downloads/
            echo   Make sure to check "Add Python to PATH" during installation!
            pause
            exit /b 1
        )
        echo   Python installed. You may need to restart this script.
        echo   Press any key to continue...
        pause >nul
    ) else (
        echo   [ERROR] winget not available and Python not found.
        echo   Please install Python 3.10+ manually from https://www.python.org/downloads/
        echo   Make sure to check "Add Python to PATH" during installation!
        pause
        exit /b 1
    )
)

:: Verify Python version
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Found Python %PYVER%

:: ─── Step 2: Check ffmpeg ───────────────────────────────────────────
echo.
echo [2/4] Checking ffmpeg...

where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo   ffmpeg not found (needed for video processing).

    where winget >nul 2>&1
    if %errorlevel% equ 0 (
        echo   Installing ffmpeg via winget...
        winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
        if %errorlevel% neq 0 (
            echo   [WARNING] Could not install ffmpeg automatically.
            echo   Video processing will be disabled. You can install it later:
            echo   winget install ffmpeg
        ) else (
            echo   ffmpeg installed successfully.
        )
    ) else (
        echo   [WARNING] Cannot auto-install ffmpeg (winget not available).
        echo   Video processing will be disabled until you install ffmpeg manually.
    )
) else (
    echo   ffmpeg found.
)

:: ─── Step 3: Create virtual environment & install packages ──────────
echo.
echo [3/4] Setting up Python environment...

cd /d "%~dp0"

if not exist "venv" (
    echo   Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo   [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo   Installing packages (this may take a few minutes)...
call venv\Scripts\activate.bat

pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo   [ERROR] Failed to install packages.
    pause
    exit /b 1
)

echo   All packages installed.

:: ─── Step 4: Create chats folder & .env ─────────────────────────────
echo.
echo [4/4] Setting up folders...

if not exist "chats" (
    mkdir chats
    echo   Created 'chats' folder.
)

if not exist ".env" (
    echo   Creating .env file...
    echo # WhatsArch Configuration> .env
    echo # Get your API key from https://console.anthropic.com/>> .env
    echo ANTHROPIC_API_KEY=>> .env
    echo.
    echo   =====================================================
    echo   IMPORTANT: For AI features (image descriptions,
    echo   smart chat), add your Anthropic API key to .env
    echo   Get one at: https://console.anthropic.com/
    echo   =====================================================
) else (
    echo   .env file already exists.
)

:: ─── Done ───────────────────────────────────────────────────────────
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║         Setup Complete!                  ║
echo  ╚══════════════════════════════════════════╝
echo.
echo  Next steps:
echo    1. Put your WhatsApp export folders in the 'chats' folder
echo    2. (Optional) Add your ANTHROPIC_API_KEY to .env
echo    3. Double-click WhatsArch.bat to start!
echo.
echo  Note: AI models (~2.5GB) will download on first use.
echo  This is a one-time download.
echo.
pause
