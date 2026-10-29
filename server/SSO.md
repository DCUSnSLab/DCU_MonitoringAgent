# 모니터링 대시보드 SSO

SSO는 신원 확인만 담당한다. DCUCODE의 교수자 역할과 관계없이 이 시스템의
`dashboard_users`에 기록한 권한으로 접근을 결정한다. SSO/DCUCODE 저장소 변경은 없다.

## 로컬 실행 (PowerShell)

### Docker Compose (현재 테스트 구성)

`server/.env`에 SSO 설정을 넣고 실행한다. 이 파일은 Git에서 제외된다.

```powershell
cd C:\Dev\DCU_MonitoringAgent\server
docker compose up -d --build
docker compose ps
```

프론트는 **http://localhost:3000**, 백엔드는 **http://localhost:8000**이다.
SSO 콘솔의 콜백은 **http://localhost:8000/api/auth/callback**을 등록한다.
DB는 Compose의 PostgreSQL과 `postgres_data` 볼륨을 사용한다. 기존 로컬 SQLite 파일은 보존하며,
SQLite 사용자/통계 데이터를 자동 복사하지 않는다. 최초 관리자는 아래 DB 등록 절차를 따른다.

```powershell
docker compose exec backend python -m app.manage_dashboard list
docker compose stop
```

프론트의 공개 API/WS 주소는 이미지 빌드 시 결정된다. `.dockerignore`가 로컬 `.env*`,
`node_modules`, `.next` 등을 제외하므로 직접 실행용 설정이나 비밀값이 이미지에 포함되지 않는다.

### Python / Next.js 직접 실행

프로젝트 루트에서 가상환경을 만들고 의존성을 설치한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r server\backend\requirements-dev.txt
cd server\backend
Copy-Item .env.example .env
```

`.env`에 발급된 `SSO_CLIENT_SECRET`과 고정 Fernet 키를 넣는다. 키는 다음 명령으로 생성한다.
키를 바꾸면 기존 refresh token은 복호화할 수 없어 다시 로그인해야 한다.

```powershell
..\..\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
$env:OTA_FILES_DIR = "$PWD\..\..\data\ota_files"
..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

별도 터미널에서 `server/dcu-dashboard`로 이동해 `npm ci`, `npm run dev`를 실행한다.
브라우저에서는 **http://localhost:3000**으로 접속한다. `127.0.0.1`과 `localhost`를
섞으면 쿠키/Origin이 달라진다. OAuth 클라이언트 `agent-monitoring`의 Redirect URI는
**http://localhost:8000/api/auth/callback**, grants는 `authorization_code refresh_token`,
scopes는 `openid profile email offline_access`로 등록한다.

처음 직접 실행할 때는 3001/8001을 사용했지만, 현재 설정은 3000/8000으로 복원했다.

SSO 로그인 버튼 → 운영 SSO → 로컬 콜백 순으로 이동한다. 아직 권한이 없는 계정은
승인 대기로 기록되고 접근이 거부된다. 등록 전 실제 비밀번호 로그인은 필요하지 않다.

## 최초 관리자 DB 등록

같은 SSO issuer의 동일한 계정이면 `sub` UUID는 로컬과 운영에서 동일하다.
계정을 삭제 후 다시 만들면 UUID가 바뀐다. 사용자명만으로 관리자 권한을 자동 부여하지 않는다.

관리자로 사용할 계정으로 SSO 로그인을 한 번 진행한다. 승인 대기로 저장되어 접근은 거부된다.
운영 DB에서 해당 계정의 UUID를 확인한 뒤 직접 관리자 권한으로 변경한다.

```sql
SELECT sub, username, role, enabled FROM dashboard_users;

UPDATE dashboard_users
SET role = 'admin', enabled = TRUE
WHERE sub = '확인된-SSO-UUID'
RETURNING sub, username, role, enabled;
```

대상 계정과 UPDATE 결과 1행을 확인하고 다시 로그인한다. 이후에는 웹 관리 페이지에서
권한을 부여·회수한다. 서버 시작 시 사용자를 자동 생성하거나 관리자 권한을 자동 부여하지 않는다.

## 웹에서 사용자 권한 관리

관리자로 로그인하면 우측 상단에 **관리** 버튼이 표시된다. `/admin`에서 로그인한 사용자의
이름/아이디/이메일/UUID를 검색하고, 관리자·조회 사용자·접근 없음 역할과 계정 활성 여부를 변경한다.
첫 로그인한 일반 계정은 승인 대기 목록에 표시된다. 여러 관리자와 여러 조회 사용자를 둘 수 있다.
조회 사용자는 관리 버튼이 없고, URL/API를 직접 호출해도 사용자 목록과 권한 변경이 거부된다.

마지막 활성 관리자의 강등/비활성화는 거부된다. 다른 관리자가 있으면 자기 권한을 내릴 수 있지만
이후 관리 페이지에 접근할 수 없다. 접근 회수는 세션을 폐기하고, viewer/admin 전환은 세션을 유지한 채
다음 요청부터 반영한다. 열린 WebSocket은 최대 30초 내 회수된다.
권한 변경 이력은 `dashboard_access_logs`에 변경자·대상·역할·시각을 기록한다.

## 서버 관리 명령 (초기 등록·복구)

`server/backend`에서 동일한 DB 설정으로 실행한다. 운영에서는 백엔드 컨테이너 안에서 실행한다.
이 명령은 서버/DB 운영자 전용이다. 최초 로그인한 사용자에게 자동으로 관리자 권한을 주지 않는다.

```powershell
# 로그인한 계정의 UUID·아이디·이름·역할·활성 여부 확인
..\..\.venv\Scripts\python.exe -m app.manage_dashboard list
# 신원 확인 후 권한 부여 (처음에는 관리자 본인)
..\..\.venv\Scripts\python.exe -m app.manage_dashboard grant <SSO-UUID> --role admin
# 조회 사용자 승인
..\..\.venv\Scripts\python.exe -m app.manage_dashboard grant <SSO-UUID> --role viewer
# 권한 회수 + 기존 세션 모두 폐기
..\..\.venv\Scripts\python.exe -m app.manage_dashboard revoke <SSO-UUID>
```

승인 후 SSO 로그인을 다시 진행한다. `viewer`, `admin` 모두 대시보드·통계를 조회할 수 있고,
`admin`은 웹에서 사용자 접근 권한도 관리할 수 있다. CLI에도 마지막 관리자 보호를 동일하게 적용한다.
계정 연결 키는 SSO `sub`이고 이름·학번·이메일로 합치지 않는다.

## 세션과 보안

- Authorization Code + PKCE S256, 서버에 저장한 일회용 state/nonce/verifier와 브라우저 쿠키로 콜백을 검증한다.
- ID token의 RS256 서명·issuer·audience·exp·sub·nonce를 검증한다. SSO 토큰은 브라우저에 내리지 않는다.
- 브라우저에는 HttpOnly / SameSite=Lax 쿠키, DB에는 세션 난수의 SHA-256 해시와 암호화한 refresh token만 저장한다.
- 세션 절대 수명은 기본 12시간. SSO 토큰 만료 전에 refresh하고 새 refresh token으로 교체한다.
  같은 세션은 프로세스 내 락과 PostgreSQL 행 잠금으로 갱신을 직렬화한다.
- SSO `invalid_grant`는 세션 폐기. 네트워크/5xx는 기존 절대 만료 내에서 30초 뒤 재시도한다.
- API는 매 요청에서 로컬 접근 권한을 확인한다. WebSocket은 Origin과 세션을 확인하고 30초마다 재검사한다.
- 로그아웃은 Origin을 검사하는 POST로 서버 세션을 지운 뒤 SSO `/logout`으로 이동한다.
  SSO의 다른 서비스 세션까지 자동으로 종료되는 것은 아니다.
- PC 에이전트의 API Key 인증과 OTA 통신은 기존 방식을 유지한다.

## 운영 전환과 복구

1. 같은 SSO 클라이언트에 운영 콜백을 **추가**한다. 로그인 검증 후 로컬 콜백 유지/제거를 결정한다.
2. 프론트·API·WebSocket을 같은 HTTPS origin에서 서비스하는 구성을 권장한다.
   서로 다른 사이트이면 SameSite=Lax 쿠키가 전달되지 않는다. 같은 호스트의 다른 포트는 같은 사이트다.
3. `DASHBOARD_AUTH_MODE=sso`, 운영 `DASHBOARD_FRONTEND_URL`, `SSO_REDIRECT_URI`,
   `DASHBOARD_COOKIE_SECURE=true`, 정확한 `CORS_ORIGINS`를 설정한다. `*`를 사용하지 않는다.
4. `SSO_CLIENT_SECRET`, `SSO_TOKEN_ENCRYPTION_KEY`는 Kubernetes Secret으로 전달한다.
   SSO의 token/jwks만 내부 주소를 쓸 때 `SSO_INTERNAL_BASE=http://dcu-sso.dcucode.svc.cluster.local:8000`.
   `SSO_ISSUER`는 공개 주소 `https://code.cu.ac.kr/sso`를 유지한다.
5. 테이블은 기존 서버 방식대로 시작 시 `create_all`로 추가한다. 기존 데이터 테이블을 변경하지 않는다.
   운영 PostgreSQL DB를 백업하고 운영자 계정에 권한을 부여한다.
6. Jenkins가 적용하는 manifest는 서버의 `/services/dcuclassagent/k8s-manifest.yaml`이므로
   이 저장소의 manifest 수정만으로 운영 설정이 바뀌지 않는다. 실제 배포 파일에도 설정을 반영해야 한다.
7. 운영 로그인·미승인 거부·통계·WebSocket·로그아웃을 확인한다.
8. 문제 발생 시 `DASHBOARD_AUTH_MODE=local`로 명시적으로 되돌리고 재배포한다.
   기존 `DASHBOARD_ADMIN_PASSWORD`/`DASHBOARD_TOKEN_SECRET`을 준비해야 한다.
   SSO 모드에서는 기존 비밀번호 로그인과 Bearer 토큰으로 우회할 수 없다.

이번 작업에서는 원격 push, main 병합, 운영 설정 변경, 배포를 수행하지 않는다.

## 검증

```powershell
# 로컬 .env가 SSO 모드여도 기존 인증 회귀 테스트는 local 모드로 실행
$env:DASHBOARD_AUTH_MODE = 'local'
$env:OTA_FILES_DIR = "$PWD\..\..\data\test-ota"
..\..\.venv\Scripts\python.exe -m pytest -q
```

OIDC 테스트는 로컬 RSA 키와 가짜 SSO HTTP 응답을 사용한다. 실제 운영 계정의 비밀번호나
운영 데이터를 사용하지 않는다. 실제 브라우저 로그인·승인은 별도로 확인해야 한다.
