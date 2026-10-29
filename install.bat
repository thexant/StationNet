@echo off
setlocal EnableExtensions

rem StationNet Windows installer. All paths are anchored to this script so
rem installation works when the extracted folder is moved or contains spaces.
for %%I in ("%~dp0.") do set "INSTALL_DIR=%%~fI"
set "VENV_DIR=%INSTALL_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

echo.
echo ============================================================
echo  StationNet installer
echo ============================================================
echo  Install directory: %INSTALL_DIR%
echo.

if not exist "%INSTALL_DIR%\run.py" goto :invalid_copy
if not exist "%INSTALL_DIR%\requirements.txt" goto :invalid_copy

where py.exe >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_COMMAND=py -3"
) else (
    where python.exe >nul 2>nul
    if errorlevel 1 goto :python_missing
    set "PYTHON_COMMAND=python"
)

%PYTHON_COMMAND% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto :python_old

if not exist "%VENV_PYTHON%" (
    echo [1/5] Creating the Python environment...
    %PYTHON_COMMAND% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :failed
) else (
    echo [1/5] Existing Python environment found.
)

echo [2/5] Installing StationNet dependencies...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r "%INSTALL_DIR%\requirements.txt"
if errorlevel 1 goto :failed

echo [3/5] Installing the bundled WIKI reader...
"%VENV_PYTHON%" "%INSTALL_DIR%\packaging\kiwix_tools.py" "%INSTALL_DIR%\kiwix_tools"
if errorlevel 1 goto :failed

echo [4/5] Preparing the stations directory...
if not exist "%INSTALL_DIR%\stations" mkdir "%INSTALL_DIR%\stations"
if errorlevel 1 goto :failed

echo [5/5] Creating run.bat...
(
    echo @echo off
    echo setlocal EnableExtensions
    echo cd /d "%%~dp0"
    echo if not exist ".venv\Scripts\python.exe" ^(
    echo     echo ERROR: StationNet is not installed in this folder.
    echo     echo Run install.bat first.
    echo     pause
    echo     exit /b 1
    echo ^)
    echo echo Starting StationNet at http://127.0.0.1:8080
    echo echo Press Ctrl+C in this window to stop the server.
    echo echo.
    echo ".venv\Scripts\python.exe" "run.py"
    echo if errorlevel 1 pause
) > "%INSTALL_DIR%\run.bat"
if errorlevel 1 goto :failed

echo.
echo StationNet is installed successfully.
echo Station data will be stored in "%INSTALL_DIR%\stations\default".
if exist "%INSTALL_DIR%\data\station.db" echo The existing station in "%INSTALL_DIR%\data" will be migrated there on first start.
echo Use run.bat to start StationNet later.
echo.
choice /C YN /N /M "Start StationNet now? [Y/N]: "
if errorlevel 2 goto :done

echo.
echo Starting StationNet at http://127.0.0.1:8080
echo Press Ctrl+C in this window to stop the server.
echo.
cd /d "%INSTALL_DIR%"
"%VENV_PYTHON%" "%INSTALL_DIR%\run.py"
goto :done

:invalid_copy
echo ERROR: run.py or requirements.txt is missing.
echo Extract a complete StationNet copy, then run install.bat from that folder.
goto :failed_pause

:python_missing
echo ERROR: Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/ and try again.
goto :failed_pause

:python_old
echo ERROR: StationNet requires Python 3.11 or newer.
echo Install a current Python release and try again.
goto :failed_pause

:failed
echo.
echo ERROR: StationNet installation did not complete.
echo Review the message above, then run install.bat again.

:failed_pause
echo.
pause
exit /b 1

:done
echo.
echo To start StationNet later, run:
echo   "%INSTALL_DIR%\run.bat"
echo.
pause
exit /b 0
