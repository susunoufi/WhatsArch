@echo off
chcp 65001 >nul 2>&1
title WhatsArch Tools - Uninstaller

echo.
echo   ============================================
echo    WhatsArch Tools - Uninstaller
echo   ============================================
echo.

set INSTALL_DIR=%USERPROFILE%\Documents\WhatsArch
set AGENT_DIR=%INSTALL_DIR%\agent

echo   This will remove:
echo     - WhatsArch Agent service
echo     - Python virtual environment
echo     - AI models cache
echo     - Startup entry
echo.
echo   This will NOT remove:
echo     - Your chat data (in %INSTALL_DIR%\...)
echo     - Python itself
echo.

set /p CONFIRM="   Are you sure? (y/n): "
if /i not "%CONFIRM%"=="y" (
    echo   Cancelled.
    pause
    exit /b 0
)

echo.
echo   [1/5] Stopping agent...
taskkill /IM python.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

echo   [2/5] Removing startup entry...
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\WhatsArch Agent.lnk" >nul 2>&1
del "%INSTALL_DIR%\agent\start-agent-hidden.vbs" >nul 2>&1
del "%INSTALL_DIR%\agent\start-agent.bat" >nul 2>&1

echo   [3/5] Removing virtual environment...
if exist "%AGENT_DIR%\venv" rmdir /s /q "%AGENT_DIR%\venv" >nul 2>&1

echo   [4/5] Removing agent code...
if exist "%AGENT_DIR%\WhatsArch" rmdir /s /q "%AGENT_DIR%\WhatsArch" >nul 2>&1
if exist "%AGENT_DIR%" rmdir /s /q "%AGENT_DIR%" >nul 2>&1

echo   [5/5] Removing from Windows registry...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\WhatsArchTools" /f >nul 2>&1

echo.
echo   ============================================
echo    WhatsArch Tools removed successfully!
echo   ============================================
echo.
echo   Your chat data is still in:
echo   %INSTALL_DIR%
echo.
echo   To remove chat data too, delete that folder manually.
echo.
pause
