@echo off
cd /d "%~dp0"
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

echo 4. Applying Chrome Remote Debugging Settings...
set PS_SCRIPT="%TEMP%\PatchChrome.ps1"
echo $portArgs = " --remote-debugging-port=9222 --user-data-dir=C:\chrome-debug" > %PS_SCRIPT%
echo $shell = New-Object -COM WScript.Shell >> %PS_SCRIPT%
echo $desktopPath = [Environment]::GetFolderPath('Desktop') >> %PS_SCRIPT%
echo $publicDesktop = [Environment]::GetFolderPath('CommonDesktopDirectory') >> %PS_SCRIPT%
echo $taskbarPath = "$env:APPDATA\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar" >> %PS_SCRIPT%
echo $startMenuPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs" >> %PS_SCRIPT%
echo $publicStartMenu = "$env:ALLUSERSPROFILE\Microsoft\Windows\Start Menu\Programs" >> %PS_SCRIPT%
echo $shortcuts = Get-ChildItem -Path $desktopPath, $publicDesktop, $taskbarPath, $startMenuPath, $publicStartMenu -Filter "*Chrome*.lnk" -Recurse -ErrorAction SilentlyContinue >> %PS_SCRIPT%
echo foreach ($lnk in $shortcuts) { >> %PS_SCRIPT%
echo     $s = $shell.CreateShortcut($lnk.FullName) >> %PS_SCRIPT%
echo     if ($s.Arguments -notmatch "remote-debugging-port") { >> %PS_SCRIPT%
echo         $s.Arguments = $s.Arguments + $portArgs >> %PS_SCRIPT%
echo         $s.Save() >> %PS_SCRIPT%
echo     } >> %PS_SCRIPT%
echo } >> %PS_SCRIPT%
echo $regPath = 'HKCU:\Software\Classes\ChromeHTML\shell\open\command' >> %PS_SCRIPT%
echo if (Test-Path $regPath) { >> %PS_SCRIPT%
echo     $val = (Get-ItemProperty -Path $regPath).'(default)' >> %PS_SCRIPT%
echo     if ($val -notmatch "remote-debugging-port") { >> %PS_SCRIPT%
echo         $newVal = $val.Replace('"%%1"', '--remote-debugging-port=9222 --user-data-dir=C:\chrome-debug "%%1"') >> %PS_SCRIPT%
echo         Set-ItemProperty -Path $regPath -Name '(default)' -Value $newVal >> %PS_SCRIPT%
echo     } >> %PS_SCRIPT%
echo } >> %PS_SCRIPT%
powershell -NoProfile -ExecutionPolicy Bypass -File %PS_SCRIPT%
del %PS_SCRIPT%

echo 5. Starting Agent...
start "" "%TARGET_DIR%\DCU_MonitoringAgent.exe"

echo Installation Complete! Check your system tray.
timeout /t 3 >nul
