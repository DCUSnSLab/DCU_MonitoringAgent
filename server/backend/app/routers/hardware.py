"""GEN-1773: 등록/사양 API. 구형 등록 및 상태 보고 API와 별도 경로."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
import re

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (Agent, AgentHardware, AgentCPU, AgentGPU, AgentDisk,
                        AgentNetworkAdapter, AgentInstallation)
from app.routers.agents import verify_api_key
from app.services.installation_service import authorize, digest

router = APIRouter(prefix="/api/agents", dependencies=[Depends(verify_api_key)])
SEAT_PATTERN = re.compile(r"^[A-Za-z0-9]+-\d+-\d+$")


class Registration(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    installation_id: UUID
    hostname: str = Field(min_length=1, max_length=256)
    # 클라이언트가 먼저 생성/보관하므로 성공 응답 유실 시에도 같은 자격으로 재시도 가능.
    token: str = Field(min_length=43, max_length=256)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def approve_offline_replacement(db, agent, installation):
    """종료 후 새로 등록된 가장 최근 설치만 교체 승인한다. 자리 행 잠금 안에서 호출한다."""
    if (installation.status != "pending" or agent.is_online or agent.last_seen_at is None
            or utc(installation.registered_at) <= utc(agent.last_seen_at)):
        return
    active = await db.scalar(select(AgentInstallation.installation_id).where(
        AgentInstallation.agent_pk == agent.id, AgentInstallation.status == "active"))
    if not active:
        return  # 최초 설치 승인은 기존 AUTO_APPROVE_INSTALLATIONS 정책을 따른다.
    latest = await db.scalar(select(AgentInstallation.installation_id).where(
        AgentInstallation.agent_pk == agent.id, AgentInstallation.status == "pending"
    ).order_by(AgentInstallation.registered_at.desc(), AgentInstallation.installation_id.desc()).limit(1))
    if latest != installation.installation_id:
        return
    now = datetime.now(timezone.utc)
    # 부분 UNIQUE 인덱스를 위반하지 않도록 기존 승인을 먼저 해제한 뒤 새 설치 활성화.
    await db.execute(update(AgentInstallation).where(
        AgentInstallation.agent_pk == agent.id,
        AgentInstallation.installation_id != installation.installation_id,
        AgentInstallation.status.in_(("active", "pending")),
    ).values(status="revoked", revoked_at=now))
    await db.flush()
    installation.status, installation.approved_at, installation.revoked_at = "active", now, None


@router.post("/installations")
async def register(req: Registration, db: AsyncSession = Depends(get_db)):
    installation_id = str(req.installation_id)
    agent = await db.scalar(select(Agent).where(Agent.agent_id == req.agent_id).with_for_update())
    existing = await db.get(AgentInstallation, installation_id, populate_existing=True)
    if existing:
        if not agent or existing.agent_pk != agent.id or existing.token_hash != digest(req.token):
            raise HTTPException(409, "Installation identity mismatch")
        await approve_offline_replacement(db, agent, existing)
        await db.commit()
        return {"status": existing.status}
    if agent is None:
        agent = Agent(agent_id=req.agent_id, hostname=req.hostname,
                      seat_confirmed=bool(SEAT_PATTERN.fullmatch(req.agent_id)))
        db.add(agent)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(409, "Concurrent registration; retry")
    active = await db.scalar(select(AgentInstallation).where(
        AgentInstallation.agent_pk == agent.id, AgentInstallation.status == "active"))
    status = "active" if settings.auto_approve_installations and not active else "pending"
    now = datetime.now(timezone.utc)
    installation = AgentInstallation(installation_id=installation_id, agent_pk=agent.id,
        status=status, token_hash=digest(req.token), registered_at=now,
        approved_at=now if status == "active" else None)
    db.add(installation)
    try:
        await db.flush()
        if active:
            await approve_offline_replacement(db, agent, installation)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Concurrent registration; retry")
    return {"status": installation.status}


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_key: str = Field(min_length=1, max_length=512)
    model: str | None = Field(default=None, max_length=256)


class CPU(Device):
    physical_cores: int | None = Field(default=None, gt=0)
    logical_processors: int | None = Field(default=None, gt=0)


class GPU(Device):
    vendor: str | None = Field(default=None, max_length=128)
    dedicated_memory_bytes: int | None = Field(default=None, ge=0, le=2**63-1)
    gpu_type: Literal["integrated", "discrete"] | None = None


class Disk(Device):
    capacity_bytes: int | None = Field(default=None, ge=0, le=2**63-1)
    media_type: Literal["ssd", "hdd"] | None = None


class Adapter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_key: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=256)
    mac_address: str | None = Field(default=None, max_length=64)
    adapter_type: Literal["ethernet", "wifi", "other"] | None = None
    is_physical: bool | None = None


class Hardware(BaseModel):
    agent_id: str
    installation_id: UUID
    hostname: str = Field(min_length=1, max_length=256)
    os_version: str = Field(default="", max_length=256)
    collected_at: datetime
    memory_total_bytes: int | None = Field(default=None, ge=0, le=2**63-1)
    # NULL=조회 실패(기존 보존), []=조회 성공했으나 장치 없음(기존 제거)
    cpus: list[CPU] | None = Field(default=None, max_length=256)
    gpus: list[GPU] | None = Field(default=None, max_length=256)
    disks: list[Disk] | None = Field(default=None, max_length=256)
    adapters: list[Adapter] | None = Field(default=None, max_length=256)

    @field_validator("collected_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value

    @field_validator("cpus", "gpus", "disks", "adapters")
    @classmethod
    def unique_devices(cls, value):
        if value is not None and len({d.device_key for d in value}) != len(value):
            raise ValueError("duplicate device_key")
        return value


@router.post("/hardware")
async def sync(req: Hardware, x_installation_token: str = Header(default=""),
               db: AsyncSession = Depends(get_db)):
    agent = await authorize(db, req.agent_id, str(req.installation_id), x_installation_token)
    # 자리 잠금으로 전체 구성 교체를 직렬화한다.
    await db.execute(select(Agent.id).where(Agent.id == agent.id).with_for_update())
    summary = await db.get(AgentHardware, agent.id)
    if summary:
        previous = summary.collected_at
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        if req.collected_at < previous:
            return {"status": "stale"}
    else:
        summary = AgentHardware(agent_pk=agent.id, collected_at=req.collected_at)
        db.add(summary)
    agent.hostname = req.hostname
    agent.os_version = req.os_version or agent.os_version
    if req.memory_total_bytes is not None:
        summary.memory_total_bytes = req.memory_total_bytes
    summary.collected_at, summary.synced_at = req.collected_at, datetime.now(timezone.utc)
    for key, model in (("cpus", AgentCPU), ("gpus", AgentGPU), ("disks", AgentDisk), ("adapters", AgentNetworkAdapter)):
        devices = getattr(req, key)
        if devices is not None:
            await db.execute(delete(model).where(model.agent_pk == agent.id))
            db.add_all([model(agent_pk=agent.id, **d.model_dump()) for d in devices])
    await db.commit()
    return {"status": "synced"}
