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

REM Check if installer.py already exists next to this bat file
set INSTALLER_PY=%~dp0installer.py
if exist "%INSTALLER_PY%" (
    echo   [OK] installer.py found locally
    goto :run_installer
)

REM Download installer.py
echo   Downloading installer...

REM Try Railway server first, then GitHub
curl -sL "https://whatsarch-production.up.railway.app/download/installer.py" -o "%INSTALLER_PY%" 2>nul

REM Check if file exists and is not empty
if exist "%INSTALLER_PY%" (
    for %%A in ("%INSTALLER_PY%") do (
        if %%~zA GEQ 100 goto :download_ok
    )
    del "%INSTALLER_PY%" >nul 2>&1
)

REM Try GitHub as fallback
curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/installer.py" -o "%INSTALLER_PY%" 2>nul

if exist "%INSTALLER_PY%" (
    for %%A in ("%INSTALLER_PY%") do (
        if %%~zA GEQ 100 goto :download_ok
    )
    del "%INSTALLER_PY%" >nul 2>&1
)

echo   [ERROR] Failed to download installer.
echo   Check your internet connection and try again.
echo.
pause
exit /b 1

:download_ok
echo   [OK] Installer downloaded
echo.

:run_installer
echo   Opening visual installer in your browser...
echo   (Keep this window open until installation is done)
echo.

REM Launch the visual installer
python "%INSTALLER_PY%"

REM Register in Windows Add/Remove Programs
set REGISTER_BAT=%~dp0register.bat
if exist "%REGISTER_BAT%" (
    call "%REGISTER_BAT%"
)

REM If we get here, the installer exited
echo.
echo   Installer finished. You can close this window.
echo.
pause
