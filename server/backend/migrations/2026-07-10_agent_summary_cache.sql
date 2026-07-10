-- 대시보드 목록 조회 N+1 제거: agents 테이블에 최신 보고서 요약 비정규화
-- ---------------------------------------------------------------------------
-- 배경: GET /api/dashboard/agents 가 에이전트마다 status_reports에서 최신 보고서를
--       찾던 N+1 쿼리라, 대량 이력 + 다수 오프라인 환경에서 초기 로딩이 수 분 걸렸다.
--       최신 요약값을 agents 행에 캐시하도록 바꾸면서, 기존 배포 DB에는 컬럼을 수동 추가해야 한다.
--
-- 중요: app/main.py 의 Base.metadata.create_all 은 "없는 테이블"만 생성하고
--       "기존 테이블의 컬럼 추가(ALTER)"는 하지 않는다. 따라서 신규 백엔드 배포 전에
--       이 스크립트를 반드시 먼저 실행해야 한다(안 그러면 없는 컬럼 참조로 500 에러).
--
-- 실행: psql -U dcu -d dcu_monitoring -f 2026-07-10_agent_summary_cache.sql
--   또는 k8s: kubectl -n dcu-agent-monitor exec -i <postgres-pod> -- \
--             psql -U dcu -d dcu_monitoring < 2026-07-10_agent_summary_cache.sql
-- ---------------------------------------------------------------------------

-- 1) 요약 컬럼 추가 (idempotent)
BEGIN;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_report_at    timestamptz;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS process_count     integer          NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS chrome_tab_count  integer          NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS alert_count       integer          NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS cpu_percent       double precision NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS memory_percent    double precision NOT NULL DEFAULT 0;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS foreground_window varchar(512);
COMMIT;

-- 2) 백필/상세조회 가속용 복합 인덱스.
--    대용량 테이블에서는 쓰기 잠금을 피하려고 CONCURRENTLY 권장 → 트랜잭션 밖에서 개별 실행.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_status_reports_agent_reported
    ON status_reports (agent_id, reported_at DESC);

-- 3) 기존 에이전트의 최신값 1회 백필 (다음 보고 전까지 카드가 0/빈값으로 뜨지 않도록)
UPDATE agents a SET
    process_count     = s.process_count,
    chrome_tab_count  = s.chrome_tab_count,
    alert_count       = s.alert_count,
    cpu_percent       = s.cpu_percent,
    memory_percent    = s.memory_percent,
    foreground_window = s.foreground_window,
    last_report_at    = s.reported_at
FROM (
    SELECT DISTINCT ON (agent_id)
        agent_id, reported_at, process_count, chrome_tab_count, alert_count,
        cpu_percent, memory_percent, foreground_window
    FROM status_reports
    ORDER BY agent_id, reported_at DESC
) s
WHERE s.agent_id = a.agent_id;
