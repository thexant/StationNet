@echo off
setlocal
rem Desktop launcher for every station under stations\; stations it starts
rem keep running after it closes.
set "STATIONNET_HOME=%~dp0."
start "" "%~dp0core\StationNetManager.exe"
