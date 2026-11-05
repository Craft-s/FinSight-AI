@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0desktop\setup-windows.ps1"
set "setupExit=%ERRORLEVEL%"
if not "%setupExit%"=="0" (
    echo FinSight AI setup did not complete. Review the message above and retry.
    pause
    exit /b %setupExit%
)
echo Setup complete. Open FinSight.Desktop.exe from this folder.
pause
