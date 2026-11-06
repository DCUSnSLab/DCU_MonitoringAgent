"""GEN-1773: 시작 시 Windows 물리 장치 수집. 조회 실패=None, 조회 성공/없음=[] 구분."""
import json
import platform
import socket
import subprocess
from datetime import datetime, timezone


def query(class_name, namespace="root/cimv2"):
    # 고정된 클래스명만 호출한다. 사용자 입력을 PowerShell에 삽입하지 않는다.
    script = f"$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; @(Get-CimInstance -Namespace '{namespace}' -ClassName {class_name}) | ConvertTo-Json -Depth 3 -Compress"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, timeout=30, creationflags=0x08000000,
                                encoding="utf-8", errors="replace")
        if result.returncode:
            return None
        rows = json.loads(result.stdout) if result.stdout.strip() else []
        return rows if isinstance(rows, list) else [rows]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def number(value):
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def gpu_memory(pnp_id):
    """WMI AdapterRAM의 4GiB 잘림을 피하고 디스플레이 드라이버의 64bit 값을 읽는다."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SYSTEM\\CurrentControlSet\\Enum\\" + pnp_id) as device:
            driver = winreg.QueryValueEx(device, "Driver")[0]
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SYSTEM\\CurrentControlSet\\Control\\Class\\" + driver) as key:
            value = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
            return int.from_bytes(value, "little") if isinstance(value, bytes) else int(value)
    except (OSError, TypeError, ValueError, ImportError):
        return None


def collect():
    result = {"hostname": socket.gethostname(), "os_version": platform.platform(),
              "collected_at": datetime.now(timezone.utc).isoformat(), "memory_total_bytes": None,
              "cpus": None, "gpus": None, "disks": None, "adapters": None}
    memory = query("Win32_PhysicalMemory")
    if memory and all(number(c.get("Capacity")) is not None for c in memory):
        result["memory_total_bytes"] = sum(number(c["Capacity"]) for c in memory)
    cpus = query("Win32_Processor")
    if cpus is not None:
        result["cpus"] = [{"device_key": c["DeviceID"], "model": c.get("Name"),
                            "physical_cores": number(c.get("NumberOfCores")),
                            "logical_processors": number(c.get("NumberOfLogicalProcessors"))} for c in cpus]
    gpus = query("Win32_VideoController")
    if gpus is not None:
        # AdapterRAM은 일부 드라이버에서 32bit 잘림. 신뢰할 수 없는 용량/내장 구분은 NULL.
        result["gpus"] = [{"device_key": c.get("PNPDeviceID") or c["DeviceID"], "model": c.get("Name"),
                            "vendor": c.get("AdapterCompatibility"), "dedicated_memory_bytes": gpu_memory(c.get("PNPDeviceID", "")),
                            "gpu_type": None} for c in gpus]
    disks = query("Win32_DiskDrive")
    media = query("MSFT_PhysicalDisk", "root/Microsoft/Windows/Storage")
    media_by_serial = {str(c.get("SerialNumber", "")).strip(): c.get("MediaType") for c in media or []}
    if disks is not None:
        result["disks"] = [{"device_key": c.get("PNPDeviceID") or c["DeviceID"], "model": c.get("Model"),
                             "capacity_bytes": number(c.get("Size")),
                             "media_type": {3: "hdd", 4: "ssd"}.get(media_by_serial.get(str(c.get("SerialNumber", "")).strip()))} for c in disks]
    adapters = query("MSFT_NetAdapter", "root/StandardCimv2")
    if adapters is not None:
        result["adapters"] = [{"device_key": c.get("InterfaceGuid") or c.get("PnPDeviceID") or c["Name"],
                                "name": c.get("InterfaceDescription"),
                                "mac_address": (c.get("PermanentAddress") or c.get("MacAddress") or "").replace("-", ":").upper() or None,
                                "adapter_type": {14: "ethernet", 9: "wifi"}.get(c.get("NdisPhysicalMedium"), "other"),
                                "is_physical": bool(c.get("HardwareInterface"))} for c in adapters]
    for key in ("cpus", "gpus", "disks", "adapters"):
        if result[key] is not None:
            result[key].sort(key=lambda c: c["device_key"])
    return result


def configuration(snapshot):
    return {k: v for k, v in snapshot.items() if k != "collected_at"}
