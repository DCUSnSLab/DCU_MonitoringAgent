"""GEN-1773: 구형 읽기 경로를 유지하면서 이력에 고정 PK 참조를 추가한다."""
from sqlalchemy import text, inspect

TABLES = ("status_reports", "alert_logs", "agent_status_minutes", "blocked_site_daily_stats")


def statements():
    yield """CREATE OR REPLACE FUNCTION dcu_set_agent_pk() RETURNS trigger AS $$
    BEGIN
      SELECT id INTO NEW.agent_pk FROM agents WHERE agent_id = NEW.agent_id;
      RETURN NEW;
    END; $$ LANGUAGE plpgsql"""
    for table in TABLES:
        yield f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS agent_pk INTEGER REFERENCES agents(id) ON DELETE CASCADE"
        yield f"CREATE INDEX IF NOT EXISTS ix_{table}_agent_pk ON {table}(agent_pk)"
        yield f"DROP TRIGGER IF EXISTS trg_agent_pk ON {table}"
        yield f"CREATE TRIGGER trg_agent_pk BEFORE INSERT OR UPDATE OF agent_id ON {table} FOR EACH ROW EXECUTE FUNCTION dcu_set_agent_pk()"
        yield f"UPDATE {table} t SET agent_pk=a.id FROM agents a WHERE t.agent_id=a.agent_id AND t.agent_pk IS NULL"


async def apply(conn):
    if conn.dialect.name == "postgresql":
        for sql in statements():
            await conn.execute(text(sql))
    elif conn.dialect.name == "sqlite":
        columns = await conn.run_sync(lambda c: {v["name"] for v in inspect(c).get_columns("agents")})
        if "seat_confirmed" not in columns:
            await conn.execute(text("ALTER TABLE agents ADD COLUMN seat_confirmed BOOLEAN NOT NULL DEFAULT 0"))
        for table in TABLES:
            columns = await conn.run_sync(lambda c, name=table: {v["name"] for v in inspect(c).get_columns(name)})
            if "agent_pk" not in columns:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN agent_pk INTEGER REFERENCES agents(id) ON DELETE CASCADE"))
            await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_agent_pk ON {table}(agent_pk)"))
            for suffix, event in (("insert", "INSERT"), ("update", "UPDATE OF agent_id")):
                await conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS trg_{table}_{suffix}_pk AFTER {event} ON {table} "
                    f"BEGIN UPDATE {table} SET agent_pk=(SELECT id FROM agents WHERE agent_id=NEW.agent_id) WHERE id=NEW.id; END"))
            await conn.execute(text(f"UPDATE {table} SET agent_pk=(SELECT id FROM agents WHERE agents.agent_id={table}.agent_id) WHERE agent_pk IS NULL"))
