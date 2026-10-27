@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
echo ========================================================
echo DCU Monitoring Agent Build ^& OTA Upload Script
echo ========================================================

REM --- Config ---
set "CONFIG_FILE=config\agent_config.yaml"
REM stable = production server, develop = develop server (namespace dcu-agent-monitor-dev)
set "SERVER_URL_STABLE=http://203.250.35.27:30085"
set "SERVER_URL_DEVELOP=http://203.250.35.27:30086"
set "API_KEY=dcu-secret-key-change-in-production"
set "SKIP_UPLOAD=0"
REM --- Release channel: stable (all lab PCs) / develop (dev PCs only). Pass as 1st argument or answer the prompt. ---
set "CHANNEL=%~1"
if "!CHANNEL!"=="" set /p "CHANNEL=0. Release channel [stable/develop] (press Enter for stable): "
if "!CHANNEL!"=="" set "CHANNEL=stable"
if not "!CHANNEL!"=="stable" if not "!CHANNEL!"=="develop" (
    echo [ERROR] Unknown channel "!CHANNEL!". Use stable or develop.
    goto end_script
)
if "!CHANNEL!"=="develop" (set "SERVER_URL=%SERVER_URL_DEVELOP%") else (set "SERVER_URL=%SERVER_URL_STABLE%")
set "WS_URL=!SERVER_URL:http=ws!/ws"
echo    Channel: !CHANNEL!  (server: !SERVER_URL!)

REM --- 1. Read current version from config ---
echo.
echo 1. Reading version from %CONFIG_FILE%...
for /f "tokens=2 delims=: " %%a in ('findstr /C:"version:" %CONFIG_FILE%') do set "CURRENT_VERSION=%%~a"
echo    Current version: !CURRENT_VERSION!

REM --- 2. Input new version ---
echo.
set /p "NEW_VERSION=2. Enter new version (current: !CURRENT_VERSION!, press Enter to keep): "
if "!NEW_VERSION!"=="" set "NEW_VERSION=!CURRENT_VERSION!"
echo    Using version: !NEW_VERSION!

REM --- 2a. Write version into config (single source of truth for the build) ---
REM Tolerant regex: matches the version line regardless of indentation / quotes.
powershell -NoProfile -Command "$v='!NEW_VERSION!'; $p='%CONFIG_FILE%'; $q=[char]34; (Get-Content $p -Encoding UTF8) -replace '^(\s*)version:\s*.*$', ('${1}version: ' + $q + $v + $q) | Set-Content $p -Encoding UTF8"
echo    Updated %CONFIG_FILE%

REM --- 2a-1. Write server address into config (baked into the exe; agents follow the bundled address after OTA) ---
REM develop builds talk to the develop server, so they never report to / update from production.
powershell -NoProfile -Command "$u='!SERVER_URL!'; $w='!WS_URL!'; $p='%CONFIG_FILE%'; $q=[char]34; (Get-Content $p -Encoding UTF8) -replace '^(\s*)base_url:\s*.*$', ('${1}base_url: ' + $q + $u + $q) -replace '^(\s*)ws_url:\s*.*$', ('${1}ws_url: ' + $q + $w + $q) | Set-Content $p -Encoding UTF8"
findstr /C:"base_url:" %CONFIG_FILE% | findstr /C:"!SERVER_URL!" >nul || (
    echo [ERROR] Failed to write server address into %CONFIG_FILE%. Aborting.
    goto end_script
)
echo    Server written to config: !SERVER_URL!

REM --- 2a-2. Write channel into config (baked into the exe; agents follow the bundled channel after OTA) ---
powershell -NoProfile -Command "$c='!CHANNEL!'; $p='%CONFIG_FILE%'; $q=[char]34; $t=Get-Content $p -Encoding UTF8; if ($t -match '^\s*channel:') { $t = $t -replace '^(\s*)channel:\s*.*$', ('${1}channel: ' + $q + $c + $q + '             # OTA channel: stable(all lab PCs) / develop(dev PCs) - set by build_agent.bat') } else { $t = $t -replace '^(\s*)version:(.*)$', ('${1}version:$2' + [Environment]::NewLine + '${1}channel: ' + $q + $c + $q) }; Set-Content $p -Value $t -Encoding UTF8"
for /f "tokens=2 delims=: " %%a in ('findstr /C:"channel:" %CONFIG_FILE%') do set "EXE_CHANNEL=%%~a"
echo    Channel written to config: !EXE_CHANNEL!
if not "!EXE_CHANNEL!"=="!CHANNEL!" (
    echo [ERROR] Failed to write channel into %CONFIG_FILE%. Aborting.
    goto end_script
)

REM --- 2b. Read it back and verify the edit actually took effect ---
REM This guarantees the version compiled into the exe matches the version we upload.
set "EXE_VERSION="
for /f "tokens=2 delims=: " %%a in ('findstr /C:"version:" %CONFIG_FILE%') do set "EXE_VERSION=%%~a"
echo    Version written to config: !EXE_VERSION!
if not "!EXE_VERSION!"=="!NEW_VERSION!" (
    echo ========================================================
    echo [ERROR] Failed to write version into %CONFIG_FILE%.
    echo         Expected "!NEW_VERSION!" but file has "!EXE_VERSION!".
    echo         Aborting to avoid an exe/server version mismatch ^(OTA loop^).
    echo ========================================================
    goto end_script
)

REM --- 3. Check duplicate version on server ---
echo.
echo 3. Checking if version !NEW_VERSION! already exists on server...
set "OTA_STATUS=UNKNOWN"
for /f %%s in ('powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%SERVER_URL%/api/ota/download/!NEW_VERSION!' -Method GET -Headers @{'X-API-Key'='%API_KEY%'} -ErrorAction Stop; 'EXISTS' } catch { if ($_.Exception.Response.StatusCode.value__ -eq 404) { 'NOT_FOUND' } else { 'CONN_ERROR' } }"') do set "OTA_STATUS=%%s"

if "!OTA_STATUS!"=="EXISTS" (
    echo ========================================================
    echo [WARNING] Version !NEW_VERSION! already exists on server!
    echo Upload aborted. Please use a different version number.
    echo ========================================================
    goto end_script
)

if "!OTA_STATUS!"=="CONN_ERROR" (
    echo    [WARNING] Could not connect to server. Build will continue without upload.
    set "SKIP_UPLOAD=1"
)
if "!OTA_STATUS!"=="UNKNOWN" (
    echo    [WARNING] Could not connect to server. Build will continue without upload.
    set "SKIP_UPLOAD=1"
)
if "!OTA_STATUS!"=="NOT_FOUND" (
    echo    Version !NEW_VERSION! is available. Proceeding...
)

REM --- 4. Install packaging tools ---
echo.
echo 4. Checking Packaging Tools...
call .\venv\Scripts\activate
pip install pyinstaller pyyaml pydantic pystray pillow requests pypiwin32 comtypes

REM Generate the UI Automation wrapper (comtypes.gen.UIAutomationClient) so it can be bundled.
REM The agent reads browser address bars through it; without it the exe would generate it at startup.
python -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll')"

REM --- 5. Build agent ---
echo.
echo 5. Building Agent Executable (v!NEW_VERSION!)...
pyinstaller --noconfirm --name "DCU_MonitoringAgent" --noconsole --onefile --paths=. --collect-submodules=agent --hidden-import="yaml" --hidden-import="pydantic" --hidden-import="pystray" --hidden-import="PIL" --hidden-import="requests" --hidden-import="win32timezone" --hidden-import="win32com.client" --hidden-import="comtypes.gen.UIAutomationClient" --add-data "config\agent_config.yaml;config" --clean agent\main.py

REM --- 6. Create ZIP package ---
echo.
echo 6. Creating Installation ZIP Package...
if not exist "dist\DCU_MonitoringAgent.exe" goto build_failed

powershell -NoProfile -Command "Compress-Archive -Path 'dist\DCU_MonitoringAgent.exe', 'install_agent.bat' -DestinationPath 'dist\DCU_MonitoringAgent_Setup.zip' -Force"

REM --- 7. Upload to OTA server ---
if "!SKIP_UPLOAD!"=="1" (
    echo.
    echo [SKIP] Server upload skipped - server unreachable.
    goto build_success
)

echo.
echo 7. Uploading to OTA server...
powershell -NoProfile -Command ^
  "$ver='!NEW_VERSION!'; $chan='!CHANNEL!'; $url='%SERVER_URL%/api/ota/upload'; $key='%API_KEY%'; $exe='dist\DCU_MonitoringAgent.exe'; " ^
  "try { " ^
  "  Add-Type -AssemblyName System.Net.Http; " ^
  "  $client = New-Object System.Net.Http.HttpClient; " ^
  "  $client.DefaultRequestHeaders.Add('X-API-Key', $key); " ^
  "  $form = New-Object System.Net.Http.MultipartFormDataContent; " ^
  "  $form.Add((New-Object System.Net.Http.StringContent($ver)), 'version'); " ^
  "  $form.Add((New-Object System.Net.Http.StringContent('Build v'+$ver+' ['+$chan+']')), 'release_notes'); " ^
  "  $form.Add((New-Object System.Net.Http.StringContent($chan)), 'channel'); " ^
  "  $fs = [System.IO.File]::OpenRead((Resolve-Path $exe)); " ^
  "  $sc = New-Object System.Net.Http.StreamContent($fs); " ^
  "  $sc.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/octet-stream'); " ^
  "  $form.Add($sc, 'file', 'DCU_MonitoringAgent.exe'); " ^
  "  $resp = $client.PostAsync($url, $form).Result; " ^
  "  $fs.Close(); " ^
  "  $body = $resp.Content.ReadAsStringAsync().Result; " ^
  "  if ($resp.IsSuccessStatusCode) { Write-Host '   Upload OK!' $body } " ^
  "  else { Write-Host '   [WARNING] Upload failed: HTTP' $resp.StatusCode $body } " ^
  "} catch { Write-Host '   [WARNING] Upload error:' $_.Exception.Message }"

:build_success
echo ========================================================
echo Build Successful! (v%NEW_VERSION% / channel %CHANNEL% / server %SERVER_URL%)
echo [Distribution File]: dist\DCU_MonitoringAgent_Setup.zip
if "%CHANNEL%"=="develop" echo [NOTE] develop build: reports to the develop server and only develop-channel PCs receive it via OTA.
echo ========================================================
goto end_script

:build_failed
echo ========================================================
echo [ERROR] Agent build failed. Please check the logs.
echo ========================================================

:end_script
endlocal
pause

