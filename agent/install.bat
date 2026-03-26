@echo off
chcp 65001 >nul 2>&1
echo.
echo  ============================================
echo   WhatsArch Local Agent - Installer
echo  ============================================
echo.
echo  This installs the local agent so you can
echo  process large chats from your computer
echo  through the WhatsArch web interface.
echo.
echo  ============================================
echo.

REM ---- Check Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found!
    echo  Please install Python 3.10+ from https://python.org
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo  [OK] Python %%v found

REM ---- Check Git ----
git --version >nul 2>&1
if errorlevel 1 (
    echo  [WARNING] Git not found. Will try to download files directly.
    set NO_GIT=1
) else (
    for /f "tokens=3" %%v in ('git --version 2^>^&1') do echo  [OK] Git %%v found
)

REM ---- Create install directory ----
set AGENT_DIR=%USERPROFILE%\Documents\WhatsArch\agent
echo.
echo  Installing to: %AGENT_DIR%
echo.

if not exist "%AGENT_DIR%" mkdir "%AGENT_DIR%"

REM ---- Download/update agent code ----
set REPO_DIR=%AGENT_DIR%\WhatsArch
if defined NO_GIT (
    echo  Downloading agent files...
    if not exist "%REPO_DIR%" mkdir "%REPO_DIR%"
    if not exist "%REPO_DIR%\agent" mkdir "%REPO_DIR%\agent"
    if not exist "%REPO_DIR%\chat_search" mkdir "%REPO_DIR%\chat_search"

    REM Download key files via curl
    curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/agent.py" -o "%REPO_DIR%\agent\agent.py"
    curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/agent/requirements.txt" -o "%REPO_DIR%\agent\requirements.txt"

    REM Download chat_search modules
    for %%f in (__init__.py config.py parser.py transcribe.py vision.py indexer.py chunker.py ai_chat.py process_manager.py) do (
        curl -sL "https://raw.githubusercontent.com/susunoufi/WhatsArch/main/chat_search/%%f" -o "%REPO_DIR%\chat_search\%%f"
    )
    echo  [OK] Agent files downloaded
) else (
    if exist "%REPO_DIR%\.git" (
        echo  Updating existing installation...
        cd /d "%REPO_DIR%"
        git pull --quiet
        echo  [OK] Updated to latest version
    ) else (
        echo  Cloning WhatsArch repository...
        git clone --quiet https://github.com/susunoufi/WhatsArch.git "%REPO_DIR%"
        echo  [OK] Repository cloned
    )
)

REM ---- Create virtual environment ----
echo.
echo  Setting up Python environment...
set VENV_DIR=%AGENT_DIR%\venv
if not exist "%VENV_DIR%" (
    python -m venv "%VENV_DIR%"
    echo  [OK] Virtual environment created
) else (
    echo  [OK] Virtual environment exists
)

REM ---- Activate venv and install dependencies ----
call "%VENV_DIR%\Scripts\activate.bat"

echo  Installing Python packages (this may take a few minutes)...
echo.
pip install --quiet --upgrade pip
pip install --quiet -r "%REPO_DIR%\agent\requirements.txt"
echo.
echo  [OK] Python packages installed

REM ---- Check ffmpeg ----
echo.
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo  [NOTE] ffmpeg not found - needed for video processing.
    echo  Install with: winget install ffmpeg
    echo  Or download from: https://ffmpeg.org/download.html
    echo  (You can skip this if you don't need video processing)
) else (
    echo  [OK] ffmpeg found
)

REM ---- Create startup script ----
echo.
echo  Creating startup script...

REM Create a .bat launcher that uses the venv
set LAUNCHER=%AGENT_DIR%\start-agent.bat
echo @echo off > "%LAUNCHER%"
echo cd /d "%REPO_DIR%" >> "%LAUNCHER%"
echo call "%VENV_DIR%\Scripts\activate.bat" >> "%LAUNCHER%"
echo python agent\agent.py >> "%LAUNCHER%"

REM Create a VBS wrapper to run it hidden (no console window)
set VBS_FILE=%AGENT_DIR%\start-agent-hidden.vbs
echo Set WshShell = WScript.CreateObject("WScript.Shell") > "%VBS_FILE%"
echo WshShell.CurrentDirectory = "%REPO_DIR%" >> "%VBS_FILE%"
echo WshShell.Run """" ^& "%VENV_DIR%\Scripts\pythonw.exe" ^& """ """ ^& "%REPO_DIR%\agent\agent.py" ^& """", 0, False >> "%VBS_FILE%"

echo  [OK] Startup scripts created

REM ---- Add to Windows Startup ----
echo.
set /p AUTO_START="  Start agent automatically on login? (Y/n): "
if /i "%AUTO_START%" neq "n" (
    copy "%VBS_FILE%" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\WhatsArch-Agent.vbs" >nul 2>&1
    echo  [OK] Added to Windows Startup
) else (
    echo  [SKIP] Not added to startup. Run manually with:
    echo         %LAUNCHER%
)

REM ---- Start the agent now ----
echo.
echo  ============================================
echo   Installation complete!
echo  ============================================
echo.
echo  Starting agent on http://localhost:11470 ...
echo.
echo  Now open WhatsArch in your browser:
echo  https://whatsarch-production.up.railway.app
echo.
echo  The website will automatically detect
echo  the local agent and show "Local Processing"
echo  in the Management tab.
echo.
echo  ============================================
echo.

start "" "%VBS_FILE%"

REM Wait a moment, then verify
timeout /t 3 /nobreak >nul
curl -s http://localhost:11470/status >nul 2>&1
if errorlevel 1 (
    echo  [WARNING] Agent may not have started. Try running manually:
    echo  %LAUNCHER%
) else (
    echo  [OK] Agent is running!
)

echo.
pause
