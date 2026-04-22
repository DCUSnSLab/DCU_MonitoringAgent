@echo off
echo ========================================================
echo DCU Monitoring Agent Build Script (PyInstaller)
echo ========================================================

echo.
echo 1. Checking Packaging Tools...
call .\venv\Scripts\activate
pip install pyinstaller pyyaml pydantic pystray pillow requests pypiwin32

echo.
echo 2. Building Agent Executable...
pyinstaller --noconfirm --name "DCU_MonitoringAgent" --noconsole --onefile --hidden-import="yaml" --hidden-import="pydantic" --hidden-import="pystray" --hidden-import="PIL" --hidden-import="requests" --hidden-import="win32timezone" --add-data "config\agent_config.yaml;config" --clean agent\main.py

echo.
echo 3. Creating Installation ZIP Package...
if not exist "dist\DCU_MonitoringAgent.exe" goto build_failed

powershell -NoProfile -Command "Compress-Archive -Path 'dist\DCU_MonitoringAgent.exe', 'install_agent.bat' -DestinationPath 'dist\DCU_MonitoringAgent_Setup.zip' -Force"

echo ========================================================
echo Build and Packaging Successful!
echo [Distribution File]: dist\DCU_MonitoringAgent_Setup.zip
echo Copy this ZIP file to deploy!
echo ========================================================
goto end_script

:build_failed
echo ========================================================
echo [ERROR] Agent build failed. Please check the logs.
echo ========================================================

:end_script
pause
