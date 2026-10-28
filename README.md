# DCU 실습실 모니터링 에이전트

실습실 PC에서 실행되는 프로세스와 크롬 브라우저 활동을 모니터링하여 시험 중 부정행위를 방지하는 에이전트입니다.

## 주요 기능

- **프로세스 모니터링**: 실행 중인 모든 프로그램 목록, CPU/메모리 사용량 수집
- **크롬 탭/URL 모니터링**: Chrome DevTools Protocol(CDP)로 모든 탭의 URL 실시간 수집
- **차단 감지**: 허용되지 않은 프로그램 또는 차단 URL 접근 시 경고 알림
- **시스템 트레이 UI**: 백그라운드 실행 + 우클릭 메뉴
- **종료 방지**: 관리자 비밀번호 없이 에이전트를 종료할 수 없음
- **자동 시작**: Windows 레지스트리에 자동 시작 등록/해제
- **서버 전송**: REST API로 서버에 주기적 상태 보고 (선택적)
- **오프라인 버퍼**: 서버 미연결 시 로컬 JSON 파일에 데이터 저장 후 재연결 시 전송

## 요구사항

- **OS**: Windows 10/11
- **Python**: 3.7 이상 (권장 3.9+)
- **크롬 CDP 활성화**: 별도 설정 필요 (아래 참고)

## 설치

```powershell
# 1. 레포지토리 클론
git clone https://github.com/your-repo/DCU_MonitoringAgent.git
cd DCU_MonitoringAgent

# 2. 가상환경 생성 및 활성화
python -m venv venv
.\venv\Scripts\activate

# 3. 의존성 설치
pip install -r requirements.txt
```

## 크롬 CDP 설정 (필수)

크롬 탭/URL 모니터링을 위해 크롬을 원격 디버깅 모드로 실행해야 합니다.

### 방법 1: PowerShell로 직접 실행 (테스트용)
```powershell
Start-Process "chrome.exe" -ArgumentList "--remote-debugging-port=9222", "--user-data-dir=C:\chrome-debug"
```

### 방법 2: 크롬 바로가기 수정 (실습실 배포용)
1. 크롬 바로가기 우클릭 → 속성
2. **대상(Target)** 끝에 추가:
   ```
   --remote-debugging-port=9222 --user-data-dir=C:\chrome-debug
   ```
3. 크롬 재시작

> **주의**: `--user-data-dir` 옵션이 없으면 기존에 실행 중인 크롬 세션과 충돌하여 CDP가 동작하지 않습니다.

## 실행 방법

### 트레이 모드 (기본, 실습실 배포용)
```powershell
.\venv\Scripts\python.exe -m agent.main
```
시스템 트레이에 녹색 원 아이콘이 표시됩니다.

### 콘솔 모드 (디버깅용)
```powershell
.\venv\Scripts\python.exe -m agent.main --console --debug
```

### 테스트 모드 (10초 실행 후 자동 종료)
```powershell
.\venv\Scripts\python.exe -m agent.main --test --debug
```

## 설정 파일

`config/agent_config.yaml`에서 모든 설정을 변경할 수 있습니다.

```yaml
agent:
  id: ""              # 비워두면 MAC 주소 기반 자동 생성
  lab_name: "실습실1"

monitoring:
  interval_seconds: 5   # 모니터링 주기

  process:
    mode: "all"          # all | whitelist | blacklist
    whitelist:           # whitelist 모드: 허용 목록
      - "chrome.exe"
      - "code.exe"
    blacklist:           # blacklist 모드: 차단 목록
      - "kakaotalk.exe"

  chrome:
    enabled: true
    debug_port: 9222
    user_data_dir: "C:\\chrome-debug"
    blocked_urls:        # 부분 일치 차단 URL
      - "chat.openai.com"
      - "chatgpt.com"

security:
  prevent_exit: true
  exit_password: "admin1234"   # 반드시 변경하세요!

server:
  enabled: false           # true로 변경 시 서버 전송 활성화
  base_url: "http://서버IP:8080"
```

## 프로젝트 구조

```
DCU_MonitoringAgent/
├── agent/
│   ├── main.py                    # 진입점 (트레이/콘솔/테스트 모드)
│   ├── config.py                  # 설정 관리 (Pydantic)
│   ├── models.py                  # 데이터 모델
│   ├── monitors/
│   │   ├── process_monitor.py     # 프로세스 모니터링 (psutil + WMI)
│   │   ├── window_monitor.py      # 윈도우 창 제목 모니터링 (win32gui)
│   │   └── chrome_monitor.py      # 크롬 탭/URL 모니터링 (CDP)
│   ├── collectors/
│   │   └── data_collector.py      # 통합 데이터 수집기
│   ├── network/
│   │   └── api_client.py          # 서버 REST API 클라이언트
│   ├── ui/
│   │   └── tray_app.py            # 시스템 트레이 UI (pystray)
│   └── utils/
│       └── logger.py              # 로깅 설정
├── config/
│   └── agent_config.yaml          # 에이전트 설정 파일
├── tests/
│   ├── test_process_monitor.py    # Phase 2 테스트
│   └── test_chrome_monitor.py     # Phase 3 테스트
├── logs/                          # 로그 파일 (자동 생성)
├── data/offline_buffer/           # 오프라인 버퍼 (자동 생성)
└── requirements.txt
```

## 데이터 프로토콜

에이전트는 5~10초마다 아래 JSON 구조의 상태 보고서를 서버에 전송합니다:

```json
{
  "message_type": "status_report",
  "agent_id": "LAB01-PC01-AABBCC",
  "timestamp": "2026-04-21T17:30:00+09:00",
  "system_info": { "cpu_percent": 45.2, "memory_percent": 62.8 },
  "processes": [
    { "pid": 1234, "name": "chrome.exe", "cpu_percent": 12.5, "memory_mb": 256.3,
      "window_title": "Google Chrome", "is_foreground": true }
  ],
  "browser_details": {
    "chrome": {
      "is_running": true, "debug_mode": true, "tab_count": 3,
      "tabs": [
        { "tab_id": "ABC123", "title": "ChatGPT", "url": "https://chatgpt.com/", "is_active": true }
      ]
    }
  },
  "alerts": [
    { "level": "warning", "code": "BLOCKED_URL", "message": "차단된 URL 접근: https://chatgpt.com/" }
  ]
}
```

## 테스트

```powershell
# 전체 테스트 실행 (17개)
.\venv\Scripts\pytest.exe tests\ -v
```

## 브랜치 · 배포 전략

aibootcamp 와 같은 방식으로 운영합니다.

| 브랜치 | 용도 | Jenkins 동작 (루트 `Jenkinsfile`, 멀티브랜치 파이프라인) |
|---|---|---|
| `develop` | 기본 작업 브랜치. 기능 브랜치(`GEN-N-설명`)는 여기로 PR | 서버 테스트 → 이미지 빌드·Harbor `develop` 태그 푸시 → **develop 환경** 배포 |
| `main` | 릴리즈 브랜치. `develop` → `main` PR 로만 반영 | 서버 테스트 → 이미지 빌드·`latest` 푸시 → **운영** 배포 |
| 그 외 (`GEN-*`, PR) | 기능 작업 | 서버 테스트 + 대시보드 빌드 확인만 (푸시·배포 안 함) |

| 환경 | 네임스페이스 | 백엔드 | 대시보드 | 매니페스트 |
|---|---|---|---|---|
| 운영 | `dcu-agent-monitor` | http://203.250.35.27:30085 | http://203.250.33.67:30030 | Jenkins 호스트의 `/services/dcuclassagent/k8s-manifest.yaml` (저장소 `server/k8s-manifest.yaml` 과 맞춰 둘 것) |
| develop | `dcu-agent-monitor-dev` | http://203.250.35.27:30086 | http://203.250.33.67:30031 | 저장소 `server/k8s-manifest-dev.yaml` (Jenkins 가 바로 apply) |

- 두 환경은 DB·OTA 저장소까지 분리돼 있어 develop 배포는 운영에 영향을 주지 않습니다.
- 배포 때 **바뀐 서비스만 재시작**합니다. 비교 기준은 이미지에 들어가는 소스(백엔드: `app/`·`Dockerfile`·`requirements.txt`·`constraints.txt`,
  대시보드: `server/dcu-dashboard` 전체)와 매니페스트 파일입니다. 대시보드만 고친 배포에서는 백엔드가 재시작되지 않아
  수업 중에도 메모리 상태(DANGER 유지 등)가 초기화되거나 보고가 끊기지 않고, 테스트만 고친 커밋은 아무것도 재시작하지 않습니다.
  매니페스트(ConfigMap 등)가 바뀌면 두 서비스 모두 재시작해 새 설정을 읽습니다.
  (지난 배포의 기준값은 Deployment 주석 `dcu-monitoring/source-tree` 에 기록)
- **저장소 밖에서 바꾼 설정**(Secret 생성·변경 등)은 위 기준에 잡히지 않으므로, Jenkins 에서 `FORCE_RESTART` 를 켜고 빌드해야 반영됩니다.
- 이미지 태그: 배포마다 불변 태그 `<브랜치>-<빌드번호>-<커밋>` 을 푸시하고, 실제로 재시작하는 서비스만 환경 태그(`latest` / `develop`)를 그 이미지로 옮깁니다.

#### Jenkins 잡 설정 (멀티브랜치 파이프라인, 한 번만)

1. Jenkins → 새 Item → **Multibranch Pipeline** (예: `dcu-monitoring`)
2. Branch Sources → GitHub, 저장소 `DCUSnSLab/DCU_MonitoringAgent`, 자격 증명은 aibootcamp 잡과 동일. Behaviours 는 기본값에서 두 가지를 바꿉니다.
   - **Discover branches → Strategy: `All branches`**
     기본값(`Exclude branches that are also filed as PRs`)이면 develop → main 릴리즈 PR 이 열려 있는 동안 develop 잡이 빠져 develop 환경 배포가 멈춥니다.
   - **Discover pull requests from forks 삭제**
     저장소가 공개라서, 남아 있으면 누구든 fork PR 을 열어 배포용 에이전트에서 자기 Dockerfile·스크립트를 실행할 수 있습니다.
     (Discover pull requests from origin 은 그대로 두어도 됩니다 — PR 은 테스트만 함)
3. Build Configuration → by Jenkinsfile, Script Path `Jenkinsfile` (저장소 루트)
4. Scan Multibranch Pipeline Triggers 또는 GitHub 웹훅으로 푸시 시 자동 빌드
5. 기존 단일 파이프라인 잡 2개(backend, frontend)에서 옮겨 오는 순서
   예전 `server/backend/Jenkinsfile`, `server/dcu-dashboard/Jenkinsfile` 은 루트 Jenkinsfile 로 합쳐져 develop 에서 지워졌습니다.
   1. 위 1~4 로 멀티브랜치 잡을 만든다 (develop 이 빌드되며 develop 환경이 생김)
   2. 옛 잡의 브랜치 지정자는 `*/main` 만 둔다 (`*/develop` 을 넣어 두었다면 develop 푸시마다 "Jenkinsfile 없음"으로 실패)
   3. develop → main 머지 직후 옛 잡 2개를 비활성화·삭제한다 (그 뒤 main 은 멀티브랜치 잡이 배포)
   옛 잡을 계속 두면 main 에 머지된 뒤 "Jenkinsfile 없음"으로 실패합니다 (운영에는 영향 없이 배포만 안 됨).
- develop 환경 대시보드 로그인은 `dcu-agent-monitor-dev` 네임스페이스에 Secret 을 한 번 만들어야 합니다 (명령은 `server/k8s-manifest-dev.yaml` 머리 주석 참고).
  만든 뒤에는 `FORCE_RESTART` 로 develop 을 한 번 빌드해야 백엔드가 Secret 을 읽습니다.

#### 롤백

- 기본: 문제가 된 커밋을 `git revert` 해서 해당 브랜치에 푸시합니다. 소스가 바뀌므로 파이프라인이 다시 빌드하고 재시작합니다.
- 급할 때(빌드를 기다릴 수 없을 때): 이전 불변 태그를 환경 태그로 되돌리고 kubectl 로 직접 재시작합니다.
  ```bash
  docker pull harbor.cu.ac.kr/dcuclassagent/backend:main-41-1a2b3c4
  docker tag harbor.cu.ac.kr/dcuclassagent/backend:main-41-1a2b3c4 harbor.cu.ac.kr/dcuclassagent/backend:latest
  docker push harbor.cu.ac.kr/dcuclassagent/backend:latest
  kubectl -n dcu-agent-monitor rollout restart deploy/backend
  ```
  이후 원인 수정을 머지하면 파이프라인이 다시 최신으로 맞춥니다. (Jenkins 로 옛 빌드를 재실행하는 방식은 `latest` 를 다시 덮어쓰므로 쓰지 않습니다)

### 에이전트 배포 채널 (stable / develop)

에이전트 빌드는 Jenkins 가 아니라 Windows PC 에서 `build_agent.bat` 로 만들고 서버에 올립니다(OTA).
릴리즈에는 **채널**이 있어, 각 PC 는 자기 채널의 최신 버전만 받습니다.

| 채널 | 보고·업데이트 서버 | 받는 PC | 쓰임 |
|---|---|---|---|
| `stable` | 운영 (30085) | 실습실 전체 PC (기본, 구버전 에이전트 포함) | 검증이 끝난 정식 배포 |
| `develop` | develop 환경 (30086) | develop 빌드로 설치한 PC 만 | 실습실에 영향 없이 새 버전을 먼저 시험 |

develop 빌드는 **develop 서버를 바라보도록** 서버 주소까지 exe 에 구워집니다. 그래서 시험 PC 의 보고는 develop 대시보드(30031)에만 나타나고, 업데이트도 develop 서버에서만 받습니다.

```bat
REM develop 채널로 빌드·업로드 (버전은 1.0.31-dev1 처럼 stable 과 겹치지 않게)
build_agent.bat develop

REM stable 채널로 빌드·업로드 (인자 없이 실행하면 프롬프트에서 선택, Enter = stable)
build_agent.bat stable
```

- 채널과 서버 주소는 exe 에 구워지며(`config/agent_config.yaml` 의 `agent.channel`, `server.base_url`/`ws_url`), 빌드 스크립트가 선택한 채널에 맞춰 기록합니다.
  develop 빌드 뒤 `channel: "develop"`·30086 주소가 남은 설정 파일은 커밋하지 마세요 (stable 빌드를 한 번 돌리면 원래대로 돌아옵니다).
- 시험용 PC 를 develop 채널에 넣으려면 그 PC 에 develop 빌드의 `DCU_MonitoringAgent_Setup.zip` 을 한 번 수동 설치합니다. 이후 develop 업로드를 OTA 로 받습니다.
- develop PC 를 다시 stable 로 돌리려면 stable 빌드를 수동 설치합니다.
- 서버에서 채널별 최신 버전 확인: `GET /api/ota/latest?channel=develop` (기본은 stable).

## 라이선스

MIT License
