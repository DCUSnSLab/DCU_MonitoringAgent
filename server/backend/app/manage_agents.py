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


def timestamp(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def group_conflict(db, members, target):
    """자리 전체를 먼저 검사한다. 일부만 병합한 뒤 충돌을 발견하지 않도록 한다."""
    source_ids = [a.id for a in members if not target or a.id != target.id]
    if not source_ids:
        return None
    if await db.scalar(select(func.count()).select_from(AgentInstallation).where(
            AgentInstallation.agent_pk.in_(source_ids), AgentInstallation.status == "active")):
        return "활성 설치가 있는 구형 행: 설치 연결 검토 필요"
    for model in (AgentHardware, AgentCPU, AgentGPU, AgentDisk, AgentNetworkAdapter):
        if await db.scalar(select(func.count()).select_from(model).where(model.agent_pk.in_(source_ids))):
            return "구형 행에 하드웨어 정보가 있음: 사양 선택 검토 필요"
    ids = [a.agent_id for a in members]
    for model, keys in ((AgentStatusMinute, ("minute",)),
                        (BlockedSiteDailyStat, ("url_pattern", "stat_date"))):
        columns = [getattr(model, k) for k in keys]
        collision = select(*columns).where(model.agent_id.in_(ids)).group_by(*columns).having(func.count() > 1).limit(1)
        if (await db.execute(collision)).first():
            return f"{model.__tablename__}: 집계 구간 중복"
    aliases = (await db.scalars(select(AgentIDAlias).where(AgentIDAlias.legacy_agent_id.in_(ids)))).all()
    allowed = {a.id for a in members}
    if any(alias.agent_pk not in allowed for alias in aliases):
        return "기존 별칭이 다른 에이전트를 참조함"
    return None


async def merge_all(db):
    """대표 행 자동 생성 및 비충돌 그룹 일괄 병합. 호출자가 commit/rollback한다."""
    agents = (await db.scalars(select(Agent).order_by(Agent.id))).all()
    by_id = {a.agent_id: a for a in agents}
    groups, unassigned = {}, []
    for agent in agents:
        seat = candidate_seat(agent.hostname, agent.agent_id)
        if seat:
            groups.setdefault(seat, []).append(agent)
        else:
            unassigned.append({"id": agent.id, "agent_id": agent.agent_id, "hostname": agent.hostname})
    result = {"agents_before": len(agents), "created_seats": 0, "merged_rows": 0,
              "merged_groups": [], "skipped_groups": [], "unassigned": unassigned}
    for seat, members in sorted(groups.items()):
        target = by_id.get(seat)
        canonical_alias = await db.get(AgentIDAlias, seat)
        if target and target not in members:
            reason = "대표 ID의 hostname이 다른 자리를 가리킴"
        elif canonical_alias and canonical_alias.agent_pk not in {a.id for a in members}:
            reason = "대표 ID의 기존 별칭이 다른 에이전트를 참조함"
        else:
            reason = await group_conflict(db, members, target)
        if reason:
            result["skipped_groups"].append({"seat": seat, "reason": reason,
                                             "ids": [a.id for a in members]})
            continue
        if len(members) == 1 and target is members[0]:
            continue  # 재실행해도 기존 정상 행/시각/설치 정보는 바뀌지 않는다.
        newest = max(members, key=lambda a: (timestamp(a.last_seen_at or a.registered_at), a.id))
        earliest = min(timestamp(a.registered_at) for a in members)
        old_ids = [a.agent_id for a in members if a is not target]
        if target is None:
            values = {c.name: getattr(newest, c.name) for c in Agent.__table__.columns
                      if c.name not in ("id", "agent_id", "registered_at", "seat_confirmed")}
            target = Agent(agent_id=seat, registered_at=earliest, seat_confirmed=True, **values)
            db.add(target)
            await db.flush()
            by_id[seat] = target
            result["created_seats"] += 1
        elif newest is not target:
            # 이미 승인된 대표 설치가 있으면 그 설치의 현재 상태를 구형 캐시로 덮어쓰지 않는다.
            active = await db.scalar(select(AgentInstallation.installation_id).where(
                AgentInstallation.agent_pk == target.id, AgentInstallation.status == "active"))
            if not active:
                for c in Agent.__table__.columns:
                    if c.name not in ("id", "agent_id", "registered_at", "seat_confirmed"):
                        setattr(target, c.name, getattr(newest, c.name))
        target.registered_at = earliest
        target.seat_confirmed = True
        target.lab_name = seat.split("-")[1]
        await db.flush()
        for source in members:
            if source.id == target.id:
                continue
            for model in (AlertLogDB, StatusReportDB, AgentStatusMinute, BlockedSiteDailyStat):
                await db.execute(update(model).where(model.agent_id == source.agent_id)
                                 .values(agent_id=seat, agent_pk=target.id))
            for model in (AgentInstallation, AgentIDAlias):
                await db.execute(update(model).where(model.agent_pk == source.id).values(agent_pk=target.id))
            if not await db.get(AgentIDAlias, source.agent_id):
                db.add(AgentIDAlias(legacy_agent_id=source.agent_id, agent_pk=target.id))
            await db.flush()
            await db.execute(delete(Agent).where(Agent.id == source.id))
            result["merged_rows"] += 1
        result["merged_groups"].append({"seat": seat, "target_id": target.id, "legacy_ids": old_ids})
    result["agents_after"] = await db.scalar(select(func.count()).select_from(Agent))
    return result


async def run(args):
    async with AsyncSessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            await db.execute(text("SELECT pg_advisory_xact_lock(1773)"))
            # 이관은 점검 시간에만 실행. 쓰기와 교차해 누락되는 것을 방지한다.
            if args.command in ("merge", "merge-all"):
                await db.execute(text("LOCK TABLE agents, agent_installations, agent_id_aliases, "
                                      "alert_logs, status_reports, agent_status_minutes, blocked_site_daily_stats, "
                                      "agent_hardware, agent_cpus, agent_gpus, agent_disks, agent_network_adapters "
                                      "IN SHARE ROW EXCLUSIVE MODE"))
        if args.command == "merge-all":
            result = await merge_all(db)
            result["mode"] = "apply" if args.apply else "dry-run"
        elif args.command == "candidates":
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
        if args.command == "merge-all":
            print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["candidates", "create-seat", "installations", "approve", "revoke", "merge", "merge-all"])
    parser.add_argument("--agent-id")
    parser.add_argument("--installation-id")
    parser.add_argument("--source", type=int)
    parser.add_argument("--target", type=int)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
