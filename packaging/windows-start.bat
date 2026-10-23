@echo off
setlocal
set "STATIONNET_DATA=%~dp0data"
"%~dp0app\StationNet.exe"
if errorlevel 1 pause
