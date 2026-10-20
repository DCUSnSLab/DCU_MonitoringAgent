-- GEN-1683: 상태 보고 이력을 1초 단위(status_reports)에서 1분 집계(agent_status_minutes)로 전환
-- ---------------------------------------------------------------------------
-- 배경: 보고(PC당 약 1초 간격)마다 status_reports 에 1행씩 INSERT 했지만 읽는 코드가 없었다.
--       새 백엔드는 status_reports 에 쓰지 않고, 상태 이력을 에이전트 × 1분 집계로 저장한다.
--       새 테이블 agent_status_minutes 는 백엔드 시작 시 create_all 이 자동으로 만든다.
--
-- 이 스크립트는 두 부분으로 나눠 실행한다.
--   1부: 새 백엔드 배포 "전" — 알림이 status_reports 없이도 저장되도록 연결을 끊는다.
--        (이전 백엔드도 1부 적용 후 정상 동작하므로 롤백해도 안전)
--   2부: 새 백엔드 배포 "후" — 더 이상 쓰지 않는 status_reports 를 비운다.
--        (배포 전에 비우면 이전 백엔드가 다시 채운다)
--
-- 주의: status_reports 를 TRUNCATE ... CASCADE 하거나 1부 없이 DELETE 하면
--       alert_logs.report_id 의 ON DELETE CASCADE 때문에 알림 이력(통계의 원본)이 함께 지워진다.
--       2부의 TRUNCATE 는 CASCADE 를 붙이지 않았으므로, 1부를 빠뜨렸다면 지우지 않고 오류로 멈춘다.
--
-- 실행: psql -U dcu -d dcu_monitoring -f 2026-10-20_report_minutely.sql  (부분별로 나눠 실행)
--   또는 k8s: kubectl -n dcu-agent-monitor exec -i <postgres-pod> -- psql -U dcu -d dcu_monitoring
-- ---------------------------------------------------------------------------


-- ═══ 1부: 새 백엔드 배포 전 ═══════════════════════════════════════════
BEGIN;

-- 새 알림은 report_id 를 비워 둔다
ALTER TABLE alert_logs ALTER COLUMN report_id DROP NOT NULL;

-- alert_logs → status_reports 외래키 제거 (이름이 환경마다 다를 수 있어 카탈로그에서 찾아 지운다)
DO $$
DECLARE
    fk record;
BEGIN
    FOR fk IN
        SELECT conname FROM pg_constraint
        WHERE contype = 'f'
          AND conrelid = 'alert_logs'::regclass
          AND confrelid = 'status_reports'::regclass
    LOOP
        EXECUTE format('ALTER TABLE alert_logs DROP CONSTRAINT %I', fk.conname);
        RAISE NOTICE 'dropped %', fk.conname;
    END LOOP;
END $$;

COMMIT;

-- 확인: 아래 결과가 0 이어야 2부를 실행할 수 있다
SELECT count(*) AS remaining_fk
FROM pg_constraint
WHERE contype = 'f' AND confrelid = 'status_reports'::regclass AND conrelid = 'alert_logs'::regclass;


-- ═══ 2부: 새 백엔드 배포 후 ═══════════════════════════════════════════
-- 새 백엔드가 떠 있는지(1분 요약 로그 "최근 1분 보고 …" 확인) 본 뒤 실행한다.
-- TRUNCATE 는 DELETE 와 달리 즉시 디스크 공간을 돌려주고 dead tuple 을 남기지 않는다.
-- 테이블 정의는 남겨 둔다 (예전 데이터를 다시 넣을 일이 생기면 쓸 수 있도록).
--
-- alert_logs.report_id 의 예전 번호는 참조 대상이 없어지지만 그대로 둔다
-- (조회에 쓰지 않고, 수백만 행 UPDATE 로 테이블만 부풀리지 않도록).

-- BEGIN;
-- TRUNCATE TABLE status_reports;
-- COMMIT;
