@echo off
setlocal
rem The shared core lives in core\; each station's data lives in stations\<id>\.
rem A pre-stations data\ folder beside this script is migrated on first start.
set "STATIONNET_HOME=%~dp0."
"%~dp0core\StationNet.exe" %*
if errorlevel 1 pause
