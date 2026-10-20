@echo off
setlocal EnableExtensions

rem IMPORTANT: This uninstaller must preserve the extracted StationNet folder.
rem It removes only the generated paths explicitly listed below. Never replace
rem these calls with deletion of INSTALL_DIR itself.
for %%I in ("%~dp0.") do set "INSTALL_DIR=%%~fI"
for %%I in ("%INSTALL_DIR%\..") do set "PARENT_DIR=%%~fI"
set "REMOVE_FAILED=0"

echo.
echo ============================================================
echo  StationNet uninstaller
echo ============================================================
echo  This resets StationNet to a clean extracted copy.
echo.
echo  PRESERVED:
echo    %INSTALL_DIR%
echo    Application source, scripts, and documentation
echo.
echo  REMOVED:
echo    Python environment, station data, and generated caches
echo.

rem Refuse to operate unless this looks like the StationNet source folder.
if not exist "%INSTALL_DIR%\run.py" goto :invalid_copy
if not exist "%INSTALL_DIR%\requirements.txt" goto :invalid_copy
if not exist "%INSTALL_DIR%\stationnet\__init__.py" goto :invalid_copy
if not exist "%INSTALL_DIR%\uninstall.bat" goto :invalid_copy

if not exist "%INSTALL_DIR%\data" goto :no_data

choice /C YN /N /M "Back up the station database and data first? [Y/N]: "
if errorlevel 2 goto :confirm_reset

for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"`) do set "STAMP=%%I"
if not defined STAMP set "STAMP=backup"
set "BACKUP_DIR=%PARENT_DIR%\StationNet-data-backup-%STAMP%-%RANDOM%"

echo.
echo Backing up station data to:
echo   %BACKUP_DIR%
robocopy "%INSTALL_DIR%\data" "%BACKUP_DIR%" /E /COPY:DAT /DCOPY:DAT /R:2 /W:1 >nul
if errorlevel 8 (
    echo ERROR: The backup failed. Nothing has been removed.
    echo Any partial backup remains at the path above for inspection.
    pause
    exit /b 1
)
echo Backup completed successfully.
goto :confirm_reset

:no_data
echo No station data directory was found; no backup is needed.

:confirm_reset
echo.
choice /C YN /N /M "Remove installed/runtime files and reset this copy? [Y/N]: "
if errorlevel 2 (
    echo Uninstall cancelled. Nothing was removed.
    pause
    exit /b 0
)

echo.
echo Removing generated StationNet files...

call :remove_directory "%INSTALL_DIR%\.venv"
call :remove_directory "%INSTALL_DIR%\data"
call :remove_directory "%INSTALL_DIR%\instance"
call :remove_directory "%INSTALL_DIR%\.pytest_cache"
call :remove_directory "%INSTALL_DIR%\.pytest-tmp"
call :remove_directory "%INSTALL_DIR%\.test-data"
call :remove_directory "%INSTALL_DIR%\__pycache__"
call :remove_file "%INSTALL_DIR%\.coverage"

rem Python may create cache folders inside source and test packages. Limit the
rem recursive search to those two known trees; never recurse over INSTALL_DIR.
if exist "%INSTALL_DIR%\stationnet" (
    for /D /R "%INSTALL_DIR%\stationnet" %%D in (__pycache__) do call :remove_directory "%%~fD"
)
if exist "%INSTALL_DIR%\tests" (
    for /D /R "%INSTALL_DIR%\tests" %%D in (__pycache__) do call :remove_directory "%%~fD"
)

rem Safety assertion: a successful reset must leave the source tree in place.
if not exist "%INSTALL_DIR%\run.py" set "REMOVE_FAILED=1"
if not exist "%INSTALL_DIR%\stationnet\__init__.py" set "REMOVE_FAILED=1"
if not exist "%INSTALL_DIR%\install.bat" set "REMOVE_FAILED=1"
if not exist "%INSTALL_DIR%\uninstall.bat" set "REMOVE_FAILED=1"

if "%REMOVE_FAILED%"=="1" (
    echo.
    echo ERROR: The reset was incomplete. Close StationNet and retry.
    echo The working directory and application source were preserved.
    pause
    exit /b 1
)

echo.
echo StationNet was uninstalled successfully.
echo The working directory and application source remain in place.
echo Run install.bat whenever you want to install this copy again.
echo.
pause
exit /b 0

:remove_directory
if exist "%~1" rmdir /S /Q "%~1"
if exist "%~1" set "REMOVE_FAILED=1"
exit /b 0

:remove_file
if exist "%~1" del /F /Q "%~1"
if exist "%~1" set "REMOVE_FAILED=1"
exit /b 0

:invalid_copy
echo ERROR: This does not appear to be a complete StationNet source folder.
echo No files were removed.
pause
exit /b 1
