@echo off
echo ============================================
echo  WhatsArch Local Agent - Installer
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found! Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Creating startup shortcut...
REM Create a VBS script to run agent hidden
echo Set WshShell = WScript.CreateObject("WScript.Shell") > "%APPDATA%\WhatsArch-Agent.vbs"
echo WshShell.Run "pythonw ""%~dp0agent.py""", 0, False >> "%APPDATA%\WhatsArch-Agent.vbs"

REM Add to startup
copy "%APPDATA%\WhatsArch-Agent.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\" >nul 2>&1

echo.
echo ============================================
echo  Installation complete!
echo  The agent will start automatically on login.
echo  Starting now...
echo ============================================
echo.
start pythonw agent.py
pause
