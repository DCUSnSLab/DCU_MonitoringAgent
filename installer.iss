[Setup]
AppName=DCU 모니터링 에이전트
AppVersion=1.0.0
DefaultDirName={autopf}\DCU_MonitoringAgent
DefaultGroupName=DCU 모니터링 에이전트
OutputDir=dist\Setup
OutputBaseFilename=DCU_MonitoringAgent_Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 아이콘 생성"; GroupDescription: "추가 작업:"
Name: "autostart"; Description: "윈도우 시작 시 자동 실행 (레지스트리 등록)"; GroupDescription: "추가 작업:"

[Files]
; PyInstaller로 빌드된 1-file EXE를 포함
Source: "dist\DCU_MonitoringAgent.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DCU 모니터링 에이전트"; Filename: "{app}\DCU_MonitoringAgent.exe"
Name: "{autodesktop}\DCU 모니터링 에이전트"; Filename: "{app}\DCU_MonitoringAgent.exe"; Tasks: desktopicon

[Registry]
; 자동 실행 등록
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DCU_MonitoringAgent"; ValueData: """{app}\DCU_MonitoringAgent.exe"""; Tasks: autostart

[Run]
Filename: "{app}\DCU_MonitoringAgent.exe"; Description: "설치 완료 후 실행"; Flags: nowait postinstall skipifsilent
