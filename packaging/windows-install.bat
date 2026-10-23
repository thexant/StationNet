@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
    echo Installation failed. Review the error above.
    pause
    exit /b 1
)
echo Start StationNet with %%LOCALAPPDATA%%\StationNet\start.bat
pause
