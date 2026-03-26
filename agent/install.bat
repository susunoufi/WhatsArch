@echo off
chcp 65001 >nul 2>&1
title WhatsArch Agent Installer

echo.
echo   ============================================
echo    WhatsArch Local Agent - Installer
echo   ============================================
echo.

REM Check if Python is available
echo   Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] Python is required but not found.
    echo   Please install Python 3.10+ from:
    echo.
    echo   https://python.org/downloads
    echo.
    echo   IMPORTANT: Check "Add Python to PATH" during installation!
    echo   Then run this file again.
    echo.
    start https://python.org/downloads
    pause
    exit /b 1
)
echo   [OK] Python found
echo.

REM Download installer.py
set INSTALLER_PY=%~dp0installer.py
echo   Downloading installer...

REM Try Railway server first, then GitHub
curl -sL "https://whatsarch-production.up.railway.app/download/installer.py" -o "%INSTALLER_PY%" 2>nul
if not exist "%INSTALLER_PY%" (
    curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/installer.py" -o "%INSTALLER_PY%" 2>nul
)

REM Check if download succeeded
if not exist "%INSTALLER_PY%" (
    echo   [ERROR] Failed to download installer.
    echo   Check your internet connection and try again.
    echo.
    pause
    exit /b 1
)

REM Check file is not empty (curl might create empty file on error)
for %%A in ("%INSTALLER_PY%") do (
    if %%~zA LSS 100 (
        echo   [ERROR] Downloaded file is empty or corrupted.
        echo   Check your internet connection and try again.
        del "%INSTALLER_PY%" >nul 2>&1
        echo.
        pause
        exit /b 1
    )
)

echo   [OK] Installer downloaded
echo.
echo   Opening visual installer in your browser...
echo   (Keep this window open until installation is done)
echo.

REM Launch the visual installer
python "%INSTALLER_PY%"

REM If we get here, the installer exited
echo.
echo   Installer finished. You can close this window.
echo.
pause
