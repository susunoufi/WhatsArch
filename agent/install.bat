@echo off
chcp 65001 >nul 2>&1
title WhatsArch Agent Installer

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   WhatsArch Agent Installer
    echo   ─────────────────────────
    echo.
    echo   Python is required but not found.
    echo   Please install Python 3.10+ from:
    echo.
    echo   https://python.org/downloads
    echo.
    echo   Make sure to check "Add Python to PATH"
    echo   during installation, then run this file again.
    echo.
    start https://python.org/downloads
    pause
    exit /b 1
)

REM Download installer.py if not present
set INSTALLER_PY=%~dp0installer.py
if not exist "%INSTALLER_PY%" (
    echo Downloading installer...
    curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/installer.py" -o "%INSTALLER_PY%"
)

REM Launch the visual installer
python "%INSTALLER_PY%"
