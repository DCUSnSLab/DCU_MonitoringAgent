"""OTA 배포 채널 테스트 (GEN-1692).

릴리즈는 stable(실습실 전체) / develop(개발용 PC) 채널로 나뉘고, 에이전트는 자기 채널의 최신 버전만 안내받는다.
"""
import hashlib

import httpx
import pytest

from app import main
from app.config import settings
from app.database import get_db
from app.models import OTARelease
from app.routers import agents, ota
from app.schemas import StatusReportRequest


@pytest.fixture
async def client(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ota, "OTA_DIR", str(tmp_path))

    async def test_db():
        yield db

    main.app.dependency_overrides[get_db] = test_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.pop(get_db, None)


async def upload(client, version, channel=None, content=b"exe-bytes"):
    data = {"version": version, "release_notes": f"v{version}"}
    if channel is not None:
        data["channel"] = channel
    return await client.post("/api/ota/upload", data=data, files={"file": ("agent.exe", content)})


def report(version, channel=None, agent_id="A"):
    payload = {"agent_id": agent_id, "hostname": "PC-1", "agent_version": version, "user_state": "safe",
               "alerts": [], "active_detections": []}
    if channel is not None:
        payload["channel"] = channel
    return StatusReportRequest(**payload)


async def test_upload_defaults_to_stable_and_rejects_unknown_channel(client):
    r = await upload(client, "1.0.31")
    assert r.status_code == 200 and r.json()["channel"] == "stable"
    assert r.json()["checksum"] == hashlib.sha256(b"exe-bytes").hexdigest()

    r = await upload(client, "1.0.31-dev1", channel="develop")
    assert r.status_code == 200 and r.json()["channel"] == "develop"

    assert (await upload(client, "1.0.32", channel="beta")).status_code == 422
    assert (await upload(client, "1.0.31", channel="develop")).status_code == 409  # 버전은 채널과 무관하게 유일


async def test_latest_is_per_channel(client):
    await upload(client, "1.0.31")
    await upload(client, "1.0.31-dev1", channel="develop")
    await upload(client, "1.0.31-dev2", channel="develop")

    assert (await client.get("/api/ota/latest")).json()["version"] == "1.0.31"
    assert (await client.get("/api/ota/latest?channel=stable")).json()["version"] == "1.0.31"
    assert (await client.get("/api/ota/latest?channel=develop")).json()["version"] == "1.0.31-dev2"
    assert (await client.get("/api/ota/latest?channel=nope")).json()["version"] == "1.0.31"  # 모르는 채널 → stable


async def test_latest_404_when_channel_empty(client):
    await upload(client, "1.0.31-dev1", channel="develop")
    assert (await client.get("/api/ota/latest")).status_code == 404  # stable 에는 아직 없음


async def test_report_offers_update_only_from_own_channel(client, db):
    await upload(client, "1.0.31")
    await upload(client, "1.0.31-dev1", channel="develop")

    # 실습실 PC(stable, 구버전 에이전트는 channel 을 보내지 않음) → stable 최신만
    body = await agents.receive_report(report("1.0.30"), db)
    assert (body["update_available"], body["latest_version"]) == (True, "1.0.31")
    body = await agents.receive_report(report("1.0.31", agent_id="B"), db)
    assert body["update_available"] is False  # develop 1.0.31-dev1 이 더 늦게 올라와도 stable 은 영향 없음

    # develop PC → develop 최신만
    body = await agents.receive_report(report("1.0.30", channel="develop", agent_id="C"), db)
    assert (body["update_available"], body["latest_version"]) == (True, "1.0.31-dev1")
    body = await agents.receive_report(report("1.0.31-dev1", channel="develop", agent_id="D"), db)
    assert body["update_available"] is False


async def test_cleanup_keeps_recent_per_channel(client, db, monkeypatch):
    monkeypatch.setattr(ota, "MAX_RELEASES", 2)
    for v in ("1.0.1", "1.0.2", "1.0.3"):
        await upload(client, v)
    await upload(client, "1.0.3-dev1", channel="develop")
    await db.commit()

    from sqlalchemy import select
    rows = (await db.execute(select(OTARelease.version, OTARelease.channel).order_by(OTARelease.version))).all()
    assert rows == [("1.0.2", "stable"), ("1.0.3", "stable"), ("1.0.3-dev1", "develop")]  # stable 만 2개로 정리


async def test_download_unaffected_by_channel(client):
    await upload(client, "1.0.31-dev1", channel="develop", content=b"dev-exe")
    r = await client.get("/api/ota/download/1.0.31-dev1")
    assert r.status_code == 200 and r.content == b"dev-exe"
