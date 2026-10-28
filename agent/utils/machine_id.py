"""
PC 고유 식별값 (에이전트 ID 재료)

에이전트 ID는 `컴퓨터이름-MAC뒤6자리` 인데, 전에는 파이썬 `uuid.getnode()` 로 MAC 을 얻었다.
이 함수는 Windows 에서 "아무 어댑터 하나"(블루투스·Hyper-V 가상 어댑터 등)를 고르고 어댑터를 못 고르면
무작위 값을 돌려주기 때문에, 재부팅·설정 초기화 때마다 ID 가 바뀌어 같은 PC 가 여러 개로 등록됐다
(운영 DB: PC 485대에 ID 1,005개).

여기서는 **물리 랜카드의 고유(permanent) MAC** 을 고정 규칙으로 고른다:
  1. WMI MSFT_NetAdapter 중 하드웨어 인터페이스이고 가상이 아닌 어댑터 (블루투스·Hyper-V·WAN 미니포트 제외)
  2. 유선(802.3) → 무선(802.11) → 그 밖의 순서, 같은 종류면 장치 ID(PnPDeviceID) 순
  3. 그래도 없으면 Win32_NetworkAdapter 의 물리 어댑터(PCI/USB) 로 한 번 더 시도
설정 파일이 지워져도 같은 PC 면 같은 값이 나오므로 ID 가 유지된다.
"""

import logging
import os
import re
import socket
import uuid
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# NdisPhysicalMedium 값 → 선택 우선순위 (작을수록 우선)
_MEDIUM_802_3 = 14
_MEDIUM_802_11 = 9
_MEDIUM_BLUETOOTH = 10
_MEDIUM_PRIORITY = {_MEDIUM_802_3: 0, _MEDIUM_802_11: 1}

# Win32_NetworkAdapter 예비 경로에서 물리 장치로 인정하는 장치 ID 접두사
_PHYSICAL_PNP_PREFIXES = ("PCI\\", "USB\\")

_MAC_RE = re.compile(r"^[0-9A-F]{12}$")


class Adapter:
    """선택에 필요한 어댑터 정보만 담은 값 객체 (테스트에서 직접 만들 수 있게 단순한 형태)"""

    def __init__(self, mac: str, hardware: bool, virtual: bool, medium: int, pnp_id: str, name: str = ""):
        self.mac = _normalize_mac(mac)
        self.hardware = bool(hardware)
        self.virtual = bool(virtual)
        self.medium = int(medium or 0)
        self.pnp_id = pnp_id or ""
        self.name = name

    def __repr__(self) -> str:
        return f"Adapter({self.name!r}, {self.mac}, hw={self.hardware}, virtual={self.virtual}, medium={self.medium})"


def _normalize_mac(raw) -> str:
    """'04:7C:16:EC:0E:33' / '04-7C-…' / '047C16EC0E33' → '047C16EC0E33' (아니면 빈 문자열)"""
    if not raw:
        return ""
    text = re.sub(r"[^0-9A-Fa-f]", "", str(raw)).upper()
    return text if _MAC_RE.match(text) and text != "0" * 12 else ""


def format_mac(mac12: str) -> str:
    """'047C16EC0E33' → '04:7C:16:EC:0E:33'"""
    return ":".join(mac12[i:i + 2] for i in range(0, 12, 2))


# ─── WMI 조회 ────────────────────────────────────────────────────

def _wmi_query(namespace: str, wql: str, fields: tuple) -> List[dict]:
    """WMI 질의 결과를 평범한 dict 목록으로. 실패하면 빈 목록 (pywin32 가 없거나 WMI 가 막힌 PC).

    COM 객체는 이 함수 안에서 값만 꺼내고 모두 해제한다 (CoUninitialize 뒤에 해제되면 프로세스가 죽는다).
    """
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        return []
    pythoncom.CoInitialize()
    rows: List[dict] = []
    try:
        service = win32com.client.GetObject(f"winmgmts:{namespace}")
        for item in service.ExecQuery(wql):
            rows.append({name: getattr(item, name, None) for name in fields})
            del item
        del service
    except Exception as e:  # WMI 저장소 손상·권한 문제 등
        logger.debug(f"WMI 조회 실패 ({namespace}): {e}")
    finally:
        pythoncom.CoUninitialize()
    return rows


def _adapters_from_msft() -> List[Adapter]:
    """root\StandardCimv2 MSFT_NetAdapter — 고유 MAC(PermanentAddress)·매체 종류·가상 여부를 모두 제공"""
    rows = _wmi_query("root\StandardCimv2", "SELECT * FROM MSFT_NetAdapter",
                      ("PermanentAddress", "HardwareInterface", "Virtual", "NdisPhysicalMedium",
                       "PnPDeviceID", "InterfaceDescription"))
    return [
        Adapter(mac=r["PermanentAddress"], hardware=r["HardwareInterface"], virtual=r["Virtual"],
                medium=r["NdisPhysicalMedium"], pnp_id=str(r["PnPDeviceID"] or ""), name=str(r["InterfaceDescription"] or ""))
        for r in rows
    ]


def _adapters_from_win32() -> List[Adapter]:
    """예비: root\cimv2 Win32_NetworkAdapter (매체 종류가 없어 PCI/USB 장치만 물리로 인정)"""
    rows = _wmi_query("root\cimv2", "SELECT * FROM Win32_NetworkAdapter WHERE PhysicalAdapter=TRUE",
                      ("MACAddress", "PNPDeviceID", "Name"))
    adapters = []
    for r in rows:
        pnp_id = str(r["PNPDeviceID"] or "")
        adapters.append(Adapter(mac=r["MACAddress"], hardware=pnp_id.upper().startswith(_PHYSICAL_PNP_PREFIXES),
                                virtual=False, medium=0, pnp_id=pnp_id, name=str(r["Name"] or "")))
    return adapters


# ─── 선택 규칙 ───────────────────────────────────────────────────

def choose_physical_adapter(adapters: List[Adapter]) -> Optional[Adapter]:
    """물리 어댑터 중 하나를 고정 규칙으로 고른다 (유선 → 무선 → 기타, 같은 종류면 장치 ID 순)."""
    candidates = [
        a for a in adapters
        if a.mac and a.hardware and not a.virtual and a.medium != _MEDIUM_BLUETOOTH
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda a: (_MEDIUM_PRIORITY.get(a.medium, 2), a.pnp_id.upper(), a.mac))
    return candidates[0]


def get_physical_mac(sources: Optional[List[Callable[[], List[Adapter]]]] = None) -> str:
    """물리 랜카드의 고유 MAC ('047C16EC0E33' 형식). 못 찾으면 빈 문자열."""
    for source in sources or (_adapters_from_msft, _adapters_from_win32):
        chosen = choose_physical_adapter(source())
        if chosen is not None:
            return chosen.mac
    return ""


def get_mac_address() -> str:
    """등록 정보용 MAC ('04:7C:16:EC:0E:33'). 물리 랜카드를 못 찾으면 예전 방식(uuid.getnode)으로 대체."""
    mac = get_physical_mac()
    if not mac:
        mac = f"{uuid.getnode():012X}"
    return format_mac(mac)


def build_agent_id(hostname: str, mac12: str) -> str:
    """에이전트 ID = 컴퓨터이름-MAC뒤6자리 (기존 ID 형식 유지)"""
    return f"{hostname}-{mac12[-6:]}"


def derive_agent_id() -> str:
    """이 PC 의 고정 에이전트 ID. 물리 랜카드를 못 찾으면 빈 문자열 (호출 쪽에서 저장된 ID 나 예비 ID 사용)."""
    mac = get_physical_mac()
    if not mac:
        return ""
    hostname = os.environ.get("COMPUTERNAME") or socket.gethostname() or "UNKNOWN"
    return build_agent_id(hostname, mac)
