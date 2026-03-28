@echo off
REM Register WhatsArch Tools in Windows Add/Remove Programs
REM Called automatically after installation

set INSTALL_DIR=%USERPROFILE%\Documents\WhatsArch
set UNINSTALL_CMD=%INSTALL_DIR%\agent\WhatsArch\agent\uninstall.bat
set REG_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\WhatsArchTools

REM Calculate approximate size (MB)
set SIZE_KB=120000

reg add "%REG_KEY%" /v "DisplayName" /t REG_SZ /d "WhatsArch Tools" /f >nul 2>&1
reg add "%REG_KEY%" /v "DisplayVersion" /t REG_SZ /d "2.0.0" /f >nul 2>&1
reg add "%REG_KEY%" /v "Publisher" /t REG_SZ /d "WhatsArch" /f >nul 2>&1
reg add "%REG_KEY%" /v "InstallLocation" /t REG_SZ /d "%INSTALL_DIR%" /f >nul 2>&1
reg add "%REG_KEY%" /v "UninstallString" /t REG_SZ /d "\"%UNINSTALL_CMD%\"" /f >nul 2>&1
reg add "%REG_KEY%" /v "DisplayIcon" /t REG_SZ /d "%INSTALL_DIR%\agent\WhatsArch\chat_search\static\icon-48.png" /f >nul 2>&1
reg add "%REG_KEY%" /v "EstimatedSize" /t REG_DWORD /d %SIZE_KB% /f >nul 2>&1
reg add "%REG_KEY%" /v "NoModify" /t REG_DWORD /d 1 /f >nul 2>&1
reg add "%REG_KEY%" /v "NoRepair" /t REG_DWORD /d 1 /f >nul 2>&1

echo WhatsArch Tools registered in Add/Remove Programs
