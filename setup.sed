[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSize=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt="DCU 모니터링 에이전트를 설치하시겠습니까? (백그라운드 트레이 형태로 실행됩니다)"
DisplayLicense=
FinishMessage="설치가 완료되었습니다."
TargetName=dist\DCU_MonitoringAgent_Setup.exe
FriendlyName=DCU 모니터링 에이전트 설치
AppLaunched=cmd.exe /c install_agent.bat
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
[SourceFiles]
SourceFiles0=.\
SourceFiles1=.\dist\
[SourceFiles0]
%FILE0%=
[SourceFiles1]
%FILE1%=
[Strings]
FILE0="install_agent.bat"
FILE1="DCU_MonitoringAgent.exe"
