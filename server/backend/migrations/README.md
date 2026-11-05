# GEN-1773: 하드웨어·설치 저장 구조

`2026-11-05_agent_hardware.sql`은 PostgreSQL 기존 DB용 추가형 마이그레이션이다.
기존 `agents`/보고/알림/통계 행과 문자열 ID 참조는 변경하지 않는다.
운영 반영 전 백업 및 별도 검증이 필요하며, 이 단계에서 운영 DB에는 적용하지 않는다.

## 적용

```sh
psql -v ON_ERROR_STOP=1 -U dcu -d dcu_monitoring -f 2026-11-05_agent_hardware.sql
```

신규 DB는 SQLAlchemy `create_all()`로 생성된다. 기존 PostgreSQL도 앱 시작 시 새 테이블을
생성하고 `agents.seat_confirmed` 컬럼을 추가한다. SQL은 같은 구조에 재실행할 수 있지만,
이미 존재하는 테이블의 임의 스키마 차이를 자동 보정하지는 않는다.
이후 `2026-11-05_agent_references.sql`로 기존 이력에 내부 FK를 추가한다.
서버 시작 시 PostgreSQL/SQLite 모두 추가 컬럼과 내부 FK 참조를 초기화한다.

## 저장 구조

- `agents.seat_confirmed`: 기본 `false`. 구형 ID를 추측해 자리 확정하지 않으며 후속 등록/이관에서 변경한다.
- `agent_hardware`: `agent_pk`가 PK/FK. 총 메모리 바이트, 에이전트 수집 시각, 서버 저장 시각.
- `agent_cpus`: 장치 키, 모델, 물리 코어/논리 프로세서 수.
- `agent_gpus`: 장치 키, 모델, 제조사, 전용 메모리 바이트, `integrated`/`discrete` 구분.
- `agent_disks`: 물리 디스크 키, 모델, 용량 바이트, `ssd`/`hdd` 구분. 파티션은 저장하지 않는다.
- `agent_network_adapters`: 장치 키, 이름, MAC, `ethernet`/`wifi`/`other` 구분, 물리 여부.
- `agent_installations`: 설치 UUID, 상태(`pending`/`active`/`revoked`), 등록/승인/폐기 시각, 토큰 해시.
  토큰 원문은 저장하지 않는다. 활성 설치에는 승인 시각과 토큰 해시가 필요하며,
  부분 UNIQUE 인덱스로 자리당 활성 설치를 하나로 제한한다. 대기/폐기 이력은 여러 개 가능하다.
- `agent_id_aliases`: 구형 문자열 ID(PK), 연결된 내부 에이전트 PK, 매핑 생성 시각.
  별칭 조회는 설치 인증 모듈에서, 충돌 검사 및 병합은 manage_agents 도구에서 처리한다.

모든 신규 FK는 `agents.id`를 참조하며 부모 삭제 시 CASCADE한다. 장치별 테이블에는
`(agent_pk, device_key)` UNIQUE를 적용한다. 동일 모델 장치 여러 개와 다른 자리의 동일
장치 키는 허용한다. 모르는 사양은 NULL, 알려진 용량은 음수가 아닌 BIGINT 바이트로 저장한다.
사용량 집계 방식은 유지한다.

## 검증

기존 `Dockerfile.test` 빌드로 SQLite 회귀/제약 테스트를 실행한다.
PostgreSQL 검증은 테스트 DB 연결을 지정해 실행한다.

```sh
TEST_HARDWARE_DATABASE_URL=postgresql+asyncpg://user:password@db/database \
  python -m pytest -q -p no:cacheprovider tests/test_hardware_models.py
```

PostgreSQL 테스트는 무작위 이름의 전용 스키마를 만들고 종료 시 해당 스키마만 삭제한다.
기존 데이터 보존 및 SQL 재실행도 검증한다. 스키마 생성 권한이 있는 개발 DB 계정을 사용한다.
