"""GEN-1773: 설치 인증과 구형 ID 호환. 기존 모니터링 판정과 분리한다."""
import hashlib
import hmac
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select
from app.config import settings
from app.models import Agent, AgentIDAlias, AgentInstallation


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def authorize(db, agent_id, installation_id, token):
    agent = await db.scalar(select(Agent).where(Agent.agent_id == agent_id))
    if agent is None:
        alias = await db.get(AgentIDAlias, agent_id)
        agent = await db.get(Agent, alias.agent_pk) if alias else None
    if installation_id:
        try:
            installation_id = str(UUID(installation_id))
        except ValueError:
            raise HTTPException(403, "Invalid installation ID")
        installation = await db.get(AgentInstallation, installation_id)
        if (not agent or not installation or installation.agent_pk != agent.id
                or installation.status != "active" or not installation.token_hash
                or not hmac.compare_digest(installation.token_hash, digest(token))):
            raise HTTPException(403, "Installation is not authorized for this seat")
    else:
        if not settings.allow_legacy_agents:
            raise HTTPException(403, "Legacy reports disabled")
        # 활성 설치가 있는 자리는 API key만으로 우회할 수 없다.
        if agent and await db.scalar(select(AgentInstallation.installation_id).where(
                AgentInstallation.agent_pk == agent.id, AgentInstallation.status == "active")):
            raise HTTPException(403, "Installation credentials required")
    return agent
