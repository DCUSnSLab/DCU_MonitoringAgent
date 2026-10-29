"""
OTA(Over-The-Air) 업데이트 라우터

관리자가 새 빌드 exe를 업로드하고,
에이전트가 최신 버전 정보 조회 및 다운로드할 수 있는 API를 제공합니다.
"""

import hashlib
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import OTARelease

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OTA"])

# OTA 파일 저장 디렉토리
OTA_DIR = os.environ.get("OTA_FILES_DIR", "/app/ota_files")
os.makedirs(OTA_DIR, exist_ok=True)

# 배포 채널: stable = 실습실 전체 PC, develop = 개발·검증용 PC (develop 빌드로 설치한 PC만 받는다)
CHANNELS = ("stable", "develop")
DEFAULT_CHANNEL = "stable"


def normalize_channel(channel: Optional[str]) -> str:
    """채널 이름 정리. 모르는 값(구버전 에이전트 등)은 stable 로 본다."""
    channel = (channel or "").strip().lower()
    return channel if channel in CHANNELS else DEFAULT_CHANNEL


async def latest_release_in_channel(db: AsyncSession, channel: Optional[str]) -> Optional[OTARelease]:
    """해당 채널의 최신 릴리즈 (없으면 None)"""
    result = await db.execute(
        select(OTARelease)
        .where(OTARelease.channel == normalize_channel(channel))
        .order_by(desc(OTARelease.uploaded_at), desc(OTARelease.id))
        .limit(1)
    )
    return result.scalar_one_or_none()


def verify_api_key(api_key: str = None):
    """X-API-Key 헤더 검증 (agents.py와 동일 로직)"""
    from fastapi import Header
    async def _verify(x_api_key: str = Header(...)):
        if x_api_key != settings.api_key:
            raise HTTPException(status_code=403, detail="Invalid API key")
    return _verify


async def _verify_api_key(x_api_key: str = Depends()):
    """Dependency 형태의 API Key 검증"""
    pass


# ─── 업로드: 관리자가 새 빌드를 서버에 올림 ──────────────────────

@router.post("/api/ota/upload")
async def upload_release(
    version: str = Form(...),
    release_notes: str = Form(""),
    channel: str = Form(DEFAULT_CHANNEL),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    새 에이전트 빌드(.exe)를 업로드합니다.

    - version: 버전 문자열 (예: "1.1.0"). 채널과 관계없이 전체에서 유일해야 한다 (develop 은 "1.0.31-dev1" 처럼 구분)
    - release_notes: 릴리즈 노트 (선택)
    - channel: 배포 채널 stable(기본) / develop — 그 채널의 에이전트만 이 버전을 받는다
    - file: 에이전트 exe 파일
    """
    if channel.strip().lower() not in CHANNELS:
        raise HTTPException(status_code=422, detail="channel 은 stable 또는 develop 이어야 합니다")
    channel = normalize_channel(channel)
    # 중복 버전 체크
    existing = await db.execute(
        select(OTARelease).where(OTARelease.version == version)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Version {version} already exists")

    # 파일 저장
    filename = f"DCU_MonitoringAgent_v{version}.exe"
    filepath = os.path.join(OTA_DIR, filename)

    hasher = hashlib.sha256()
    file_size = 0

    with open(filepath, "wb") as f:
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            f.write(chunk)
            hasher.update(chunk)
            file_size += len(chunk)

    checksum = hasher.hexdigest()

    # DB 저장
    release = OTARelease(
        version=version,
        channel=channel,
        filename=filename,
        checksum=checksum,
        file_size=file_size,
        release_notes=release_notes or None,
    )
    db.add(release)

    logger.info(f"OTA 릴리즈 업로드 완료: v{version} [{channel}] ({file_size} bytes, sha256={checksum[:16]}...)")

    # 오래된 릴리즈 정리 (채널마다 최근 30개만 보관)
    await _cleanup_old_releases(db, channel)

    return {
        "status": "ok",
        "version": version,
        "channel": channel,
        "checksum": checksum,
        "file_size": file_size,
    }


MAX_RELEASES = 30


async def _cleanup_old_releases(db: AsyncSession, channel: str):
    """해당 채널에서 최근 MAX_RELEASES개를 제외한 오래된 릴리즈를 DB + 파일 모두 삭제합니다."""
    result = await db.execute(
        select(OTARelease).where(OTARelease.channel == channel).order_by(desc(OTARelease.uploaded_at), desc(OTARelease.id))
    )
    all_releases = result.scalars().all()

    if len(all_releases) <= MAX_RELEASES:
        return

    old_releases = all_releases[MAX_RELEASES:]
    deleted = 0
    for release in old_releases:
        # 파일 삭제
        filepath = os.path.join(OTA_DIR, release.filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                logger.warning(f"OTA 파일 삭제 실패 ({release.filename}): {e}")

        # DB 레코드 삭제
        await db.delete(release)
        deleted += 1

    if deleted:
        logger.info(f"OTA 정리 [{channel}]: {deleted}개 이전 릴리즈 삭제 (보관: {MAX_RELEASES}개)")


# ─── 최신 버전 정보 조회 ─────────────────────────────────────────

@router.get("/api/ota/latest")
async def get_latest_release(
    channel: str = DEFAULT_CHANNEL,
    db: AsyncSession = Depends(get_db),
):
    """채널(stable/develop)의 최신 OTA 릴리즈 정보를 반환합니다."""
    release = await latest_release_in_channel(db, channel)

    if not release:
        raise HTTPException(status_code=404, detail="No releases available")

    return {
        "version": release.version,
        "channel": release.channel,
        "checksum": release.checksum,
        "file_size": release.file_size,
        "release_notes": release.release_notes,
        "uploaded_at": release.uploaded_at.isoformat() if release.uploaded_at else None,
    }


# ─── 파일 다운로드 ───────────────────────────────────────────────

@router.get("/api/ota/download/{version}")
async def download_release(
    version: str,
    db: AsyncSession = Depends(get_db),
):
    """특정 버전의 에이전트 exe 파일을 다운로드합니다."""
    result = await db.execute(
        select(OTARelease).where(OTARelease.version == version)
    )
    release = result.scalar_one_or_none()

    if not release:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")

    filepath = os.path.join(OTA_DIR, release.filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Release file not found on server")

    return FileResponse(
        path=filepath,
        filename=release.filename,
        media_type="application/octet-stream",
        headers={"X-Checksum-SHA256": release.checksum},
    )
