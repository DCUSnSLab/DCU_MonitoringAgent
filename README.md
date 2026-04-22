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

## 라이선스

MIT License
