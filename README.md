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

| 브랜치 | 용도 | Jenkins 동작 |
|---|---|---|
| `develop` | 기본 작업 브랜치. 기능 브랜치(`GEN-N-설명`)는 여기로 PR | 서버 테스트 + 이미지 빌드 + Harbor `develop` 태그 푸시. **운영에 배포하지 않음** |
| `main` | 릴리즈 브랜치. `develop` → `main` PR 로만 반영 | 테스트 + 빌드 + `latest` 푸시 + 운영 K8s 배포(rollout) |

- Jenkins 잡(backend, frontend)의 브랜치 지정자에 `*/main` 과 `*/develop` 을 모두 넣어야 develop 푸시에도 검증이 돕니다.
- 이 프로젝트는 개발 서버가 없으므로 develop 은 "깨지지 않는지 검증"과 develop 용 에이전트 배포까지만 담당합니다.

### 에이전트 배포 채널 (stable / develop)

에이전트 빌드는 Jenkins 가 아니라 Windows PC 에서 `build_agent.bat` 로 만들고 서버에 올립니다(OTA).
릴리즈에는 **채널**이 있어, 각 PC 는 자기 채널의 최신 버전만 받습니다.

| 채널 | 받는 PC | 쓰임 |
|---|---|---|
| `stable` | 실습실 전체 PC (기본, 구버전 에이전트 포함) | 검증이 끝난 정식 배포 |
| `develop` | develop 빌드로 설치한 PC 만 | 실습실에 영향 없이 새 버전을 먼저 시험 |

```bat
REM develop 채널로 빌드·업로드 (버전은 1.0.31-dev1 처럼 stable 과 겹치지 않게)
build_agent.bat develop

REM stable 채널로 빌드·업로드 (인자 없이 실행하면 프롬프트에서 선택, Enter = stable)
build_agent.bat stable
```

- 채널은 exe 에 구워지며(`config/agent_config.yaml` 의 `agent.channel`), 빌드 스크립트가 선택한 채널로 기록합니다.
  빌드 뒤 `agent.channel` 이 `develop` 으로 남은 설정 파일은 커밋하지 마세요.
- 시험용 PC 를 develop 채널에 넣으려면 그 PC 에 develop 빌드의 `DCU_MonitoringAgent_Setup.zip` 을 한 번 수동 설치합니다. 이후 develop 업로드를 OTA 로 받습니다.
- develop PC 를 다시 stable 로 돌리려면 stable 빌드를 수동 설치합니다.
- 서버에서 채널별 최신 버전 확인: `GET /api/ota/latest?channel=develop` (기본은 stable).

## 라이선스

MIT License
