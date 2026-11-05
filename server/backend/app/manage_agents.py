"""GEN-1773 운영 도구. 기본 읽기 전용; 변경 명령은 --apply가 있어야 커밋한다."""
import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select, update, delete, func, text, and_
from app.database import AsyncSessionLocal
from app.models import (Agent, AgentInstallation, AgentIDAlias, AgentHardware, AgentCPU,
                        AgentGPU, AgentDisk, AgentNetworkAdapter, AgentStatusMinute,
                        AlertLogDB, StatusReportDB, BlockedSiteDailyStat)
from app.routers.hardware import SEAT_PATTERN


def seat_code(value):
    """자리 코드 또는 자리 코드-MAC뒤6자리만 인정한다. 임의 접미사는 제거하지 않는다."""
    if not value:
        return None
    if SEAT_PATTERN.fullmatch(value):
        return value
    prefix, separator, suffix = value.rpartition("-")
    if separator and re.fullmatch(r"[0-9A-Fa-f]{6}", suffix) and SEAT_PATTERN.fullmatch(prefix):
        return prefix
    return None


def candidate_seat(hostname, agent_id):
    host_seat, id_seat = seat_code(hostname), seat_code(agent_id)
    # 서로 다른 자리를 가리키는 정보는 자동으로 어느 쪽을 우선하지 않는다.
    if host_seat and id_seat and host_seat != id_seat:
        return None
    return host_seat or id_seat


async def run(args):
    async with AsyncSessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            await db.execute(text("SELECT pg_advisory_xact_lock(1773)"))
            # 이관은 점검 시간에만 실행. 쓰기와 교차해 누락되는 것을 방지한다.
            if args.command == "merge":
                await db.execute(text("LOCK TABLE agents, agent_installations, agent_id_aliases, "
                                      "alert_logs, status_reports, agent_status_minutes, blocked_site_daily_stats "
                                      "IN SHARE ROW EXCLUSIVE MODE"))
        if args.command == "candidates":
            groups = {}
            for a in (await db.scalars(select(Agent))).all():
                candidate = candidate_seat(a.hostname, a.agent_id)
                groups.setdefault(candidate or "UNASSIGNED", []).append({"id": a.id, "agent_id": a.agent_id,
                    "hostname": a.hostname, "mac": a.mac_address, "last_seen": str(a.last_seen_at)})
            print(json.dumps(groups, ensure_ascii=False, indent=2))
        elif args.command == "create-seat":
            if not args.agent_id or not SEAT_PATTERN.fullmatch(args.agent_id):
                raise ValueError("Valid seat code required")
            db.add(Agent(agent_id=args.agent_id, hostname=args.agent_id, seat_confirmed=True))
            await db.flush()
            print("create-seat", args.agent_id)
        elif args.command == "installations":
            for i, a in (await db.execute(select(AgentInstallation, Agent).join(Agent, Agent.id == AgentInstallation.agent_pk))).all():
                print(i.installation_id, a.agent_id, i.status)
        elif args.command in ("approve", "revoke"):
            item = await db.get(AgentInstallation, str(UUID(args.installation_id)))
            if not item:
                raise ValueError("Unknown installation")
            await db.execute(select(Agent.id).where(Agent.id == item.agent_pk).with_for_update())
            now = datetime.now(timezone.utc)
            if args.command == "approve":
                if not item.token_hash:
                    raise ValueError("Registration token required")
                await db.execute(update(AgentInstallation).where(AgentInstallation.agent_pk == item.agent_pk,
                    AgentInstallation.status == "active").values(status="revoked", revoked_at=now))
                await db.flush()
                item.status, item.approved_at, item.revoked_at = "active", now, None
            else:
                item.status, item.revoked_at = "revoked", now
            print(args.command, item.installation_id)
        elif args.command == "merge":
            source = await db.get(Agent, args.source)
            target = await db.get(Agent, args.target)
            if not source or not target or source.id == target.id:
                raise ValueError("Specify distinct existing source and target internal IDs")
            if await db.scalar(select(func.count()).select_from(AgentInstallation).where(
                    AgentInstallation.agent_pk == source.id, AgentInstallation.status == "active")):
                raise ValueError("Revoke source installation before merging")
            # 집계 충돌은 자동 합산하지 않고 원본을 보존한 채 검토 대상으로 남긴다.
            for model, keys in ((AgentStatusMinute, ("minute",)),
                                (BlockedSiteDailyStat, ("url_pattern", "stat_date"))):
                left, right = model.__table__.alias("s"), model.__table__.alias("t")
                conditions = [left.c[k] == right.c[k] for k in keys]
                conflict = await db.scalar(select(func.count()).select_from(left.join(right, and_(*conditions)))
                    .where(left.c.agent_id == source.agent_id, right.c.agent_id == target.agent_id))
                if conflict:
                    raise ValueError(f"{model.__tablename__}: {conflict} overlapping aggregates; manual review required")
            if await db.get(AgentHardware, source.id) or any([
                await db.scalar(select(func.count()).select_from(m).where(m.agent_pk == source.id))
                for m in (AgentCPU, AgentGPU, AgentDisk, AgentNetworkAdapter)]):
                raise ValueError("Source has hardware snapshot; review before merging")
            for model in (AlertLogDB, StatusReportDB, AgentStatusMinute, BlockedSiteDailyStat):
                await db.execute(update(model).where(model.agent_id == source.agent_id).values(agent_id=target.agent_id))
            for model in (AgentInstallation, AgentIDAlias):
                await db.execute(update(model).where(model.agent_pk == source.id).values(agent_pk=target.id))
            alias = await db.get(AgentIDAlias, source.agent_id)
            if alias and alias.agent_pk != target.id:
                raise ValueError("Alias collision")
            if not alias:
                db.add(AgentIDAlias(legacy_agent_id=source.agent_id, agent_pk=target.id))
            await db.flush()
            await db.execute(delete(Agent).where(Agent.id == source.id))
            print(f"merge {source.agent_id} -> {target.agent_id}")
        if args.apply:
            await db.commit()
            print("APPLIED")
        else:
            await db.rollback()
            print("DRY RUN: no changes committed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["candidates", "create-seat", "installations", "approve", "revoke", "merge"])
    parser.add_argument("--agent-id")
    parser.add_argument("--installation-id")
    parser.add_argument("--source", type=int)
    parser.add_argument("--target", type=int)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
