-- GEN-1773: fixed internal references; apply after hardware migration.
BEGIN;
CREATE OR REPLACE FUNCTION dcu_set_agent_pk() RETURNS trigger AS $$
    BEGIN
      SELECT id INTO NEW.agent_pk FROM agents WHERE agent_id = NEW.agent_id;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql;
ALTER TABLE status_reports ADD COLUMN IF NOT EXISTS agent_pk INTEGER REFERENCES agents(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_status_reports_agent_pk ON status_reports(agent_pk);
DROP TRIGGER IF EXISTS trg_agent_pk ON status_reports;
CREATE TRIGGER trg_agent_pk BEFORE INSERT OR UPDATE OF agent_id ON status_reports FOR EACH ROW EXECUTE FUNCTION dcu_set_agent_pk();
UPDATE status_reports t SET agent_pk=a.id FROM agents a WHERE t.agent_id=a.agent_id AND t.agent_pk IS NULL;
ALTER TABLE alert_logs ADD COLUMN IF NOT EXISTS agent_pk INTEGER REFERENCES agents(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_alert_logs_agent_pk ON alert_logs(agent_pk);
DROP TRIGGER IF EXISTS trg_agent_pk ON alert_logs;
CREATE TRIGGER trg_agent_pk BEFORE INSERT OR UPDATE OF agent_id ON alert_logs FOR EACH ROW EXECUTE FUNCTION dcu_set_agent_pk();
UPDATE alert_logs t SET agent_pk=a.id FROM agents a WHERE t.agent_id=a.agent_id AND t.agent_pk IS NULL;
ALTER TABLE agent_status_minutes ADD COLUMN IF NOT EXISTS agent_pk INTEGER REFERENCES agents(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_agent_status_minutes_agent_pk ON agent_status_minutes(agent_pk);
DROP TRIGGER IF EXISTS trg_agent_pk ON agent_status_minutes;
CREATE TRIGGER trg_agent_pk BEFORE INSERT OR UPDATE OF agent_id ON agent_status_minutes FOR EACH ROW EXECUTE FUNCTION dcu_set_agent_pk();
UPDATE agent_status_minutes t SET agent_pk=a.id FROM agents a WHERE t.agent_id=a.agent_id AND t.agent_pk IS NULL;
ALTER TABLE blocked_site_daily_stats ADD COLUMN IF NOT EXISTS agent_pk INTEGER REFERENCES agents(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_blocked_site_daily_stats_agent_pk ON blocked_site_daily_stats(agent_pk);
DROP TRIGGER IF EXISTS trg_agent_pk ON blocked_site_daily_stats;
CREATE TRIGGER trg_agent_pk BEFORE INSERT OR UPDATE OF agent_id ON blocked_site_daily_stats FOR EACH ROW EXECUTE FUNCTION dcu_set_agent_pk();
UPDATE blocked_site_daily_stats t SET agent_pk=a.id FROM agents a WHERE t.agent_id=a.agent_id AND t.agent_pk IS NULL;
COMMIT;
