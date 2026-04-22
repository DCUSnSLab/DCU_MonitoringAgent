@echo off
echo Installing DCU Monitoring Agent...

set "TARGET_DIR=%LOCALAPPDATA%\DCU_MonitoringAgent"
if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

echo 1. Copying files...
copy /Y "DCU_MonitoringAgent.exe" "%TARGET_DIR%\DCU_MonitoringAgent.exe"

echo 2. Registering Startup program...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "DCU_MonitoringAgent" /t REG_SZ /d "\"%TARGET_DIR%\DCU_MonitoringAgent.exe\"" /f

echo 3. Creating Desktop Shortcut...
set SCRIPT="%TEMP%\CreateShortcut.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > %SCRIPT%
echo sLinkFile = "%USERPROFILE%\Desktop\DCU Monitoring Agent.lnk" >> %SCRIPT%
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> %SCRIPT%
echo oLink.TargetPath = "%TARGET_DIR%\DCU_MonitoringAgent.exe" >> %SCRIPT%
echo oLink.WorkingDirectory = "%TARGET_DIR%" >> %SCRIPT%
echo oLink.Save >> %SCRIPT%
cscript /nologo %SCRIPT%
del %SCRIPT%

echo 4. Starting Agent...
start "" "%TARGET_DIR%\DCU_MonitoringAgent.exe"

echo Installation Complete! Check your system tray.
timeout /t 3 >nul
