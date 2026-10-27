"""
에이전트 ID 고정 (GEN-1692) 단위 테스트

ID 재료인 MAC 을 물리 랜카드에서 고정 규칙으로 고르는지, 설정 파일의 ID 가 틀려도 고정 ID 로 바뀌는지 확인한다.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from agent.config import ConfigManager
from agent.utils import machine_id as mid
from agent.utils.machine_id import Adapter, build_agent_id, choose_physical_adapter, get_physical_mac

# 개발 PC 에서 실제로 조회된 어댑터 구성 (블루투스·Hyper-V 가상 어댑터 포함)
BLUETOOTH = Adapter("8C882B0087C5", hardware=False, virtual=True, medium=10, pnp_id="BTH\\MS_BTHPAN\\7", name="Bluetooth PAN")
WIFI = Adapter("BC035876B150", hardware=True, virtual=False, medium=9, pnp_id="PCI\\VEN_8086&DEV_7A70", name="Wi-Fi")
ETHERNET = Adapter("04:7C:16:EC:0E:33", hardware=True, virtual=False, medium=14, pnp_id="PCI\\VEN_10EC&DEV_8125", name="Realtek")
HYPERV = Adapter("00155DDCDD2C", hardware=False, virtual=True, medium=0, pnp_id="", name="Hyper-V")


def test_wired_preferred_over_wifi_bluetooth_virtual():
    chosen = choose_physical_adapter([BLUETOOTH, WIFI, HYPERV, ETHERNET])
    assert chosen is ETHERNET
    assert chosen.mac == "047C16EC0E33"  # 구분자 있는 형식도 정규화


def test_order_of_input_does_not_matter():
    assert choose_physical_adapter([ETHERNET, WIFI]) is choose_physical_adapter([WIFI, ETHERNET])


def test_wifi_when_no_wired():
    assert choose_physical_adapter([BLUETOOTH, WIFI, HYPERV]) is WIFI


def test_two_wired_adapters_sorted_by_device_id():
    a = Adapter("AAAAAAAAAAAA", True, False, 14, "PCI\\VEN_8086&DEV_15F3\\3&11583659&0&FE")
    b = Adapter("BBBBBBBBBBBB", True, False, 14, "PCI\\VEN_10EC&DEV_8125\\4&2A6C6B6&0&00E0")
    assert choose_physical_adapter([a, b]).mac == "BBBBBBBBBBBB"  # 'PCI\VEN_10EC' < 'PCI\VEN_8086'
    assert choose_physical_adapter([b, a]).mac == "BBBBBBBBBBBB"


def test_virtual_bluetooth_and_empty_mac_never_chosen():
    assert choose_physical_adapter([BLUETOOTH, HYPERV]) is None
    assert choose_physical_adapter([Adapter("", True, False, 14, "PCI\\X")]) is None
    assert choose_physical_adapter([Adapter("000000000000", True, False, 14, "PCI\\X")]) is None
    assert choose_physical_adapter([]) is None


def test_fallback_source_used_when_first_has_no_physical():
    assert get_physical_mac(sources=[lambda: [BLUETOOTH], lambda: [WIFI]]) == "BC035876B150"
    assert get_physical_mac(sources=[lambda: [], lambda: []]) == ""


def test_agent_id_format_keeps_hostname_and_last_6_hex():
    assert build_agent_id("D2-521-01", "047C16EC0E33") == "D2-521-01-EC0E33"


def test_config_replaces_stored_id_with_derived(monkeypatch, tmp_path):
    """설정 파일에 (블루투스 MAC 등으로 만든) 다른 ID 가 있어도 고정 ID 로 바꾸고 저장"""
    monkeypatch.setattr(mid, "get_physical_mac", lambda sources=None: "047C16EC0E33")
    monkeypatch.setenv("COMPUTERNAME", "D2-521-01")
    path = tmp_path / "agent_config.yaml"
    path.write_text('agent:\n  id: "D2-521-01-0087C5"\n  version: "1.0.30"\n', encoding="utf-8")

    cfg = ConfigManager(str(path)).config
    assert cfg.agent.id == "D2-521-01-EC0E33"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["agent"]["id"] == "D2-521-01-EC0E33"


def test_config_keeps_stored_id_when_no_physical_adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(mid, "get_physical_mac", lambda sources=None: "")
    path = tmp_path / "agent_config.yaml"
    path.write_text('agent:\n  id: "D2-521-01-0087C5"\n', encoding="utf-8")
    assert ConfigManager(str(path)).config.agent.id == "D2-521-01-0087C5"


def test_config_generates_fallback_id_when_nothing_available(monkeypatch, tmp_path):
    monkeypatch.setattr(mid, "get_physical_mac", lambda sources=None: "")
    monkeypatch.setenv("COMPUTERNAME", "D2-521-01")
    path = tmp_path / "agent_config.yaml"
    path.write_text('agent:\n  id: ""\n', encoding="utf-8")
    agent_id = ConfigManager(str(path)).config.agent.id
    assert agent_id.startswith("D2-521-01-") and len(agent_id) == len("D2-521-01-") + 6
