"""
배포(frozen) 환경 OTA 설정 동기화 단위 테스트 (GEN-1149)

배포 PC는 OTA 갱신 후에도 기존 APPDATA 설정 파일을 쓰므로,
번들 설정의 탐지 정책 목록이 반영되는지 확인한다.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from agent.config import ConfigManager

OLD_APPDATA = """
agent:
  id: "PC-521-01"
  version: "1.0.28"
monitoring:
  process:
    mode: "all"
    always_blocked: ["claude.exe"]
  chrome:
    blocked_urls: ["chatgpt.com"]
server:
  base_url: "http://old:30085"
"""

BUNDLED = """
agent:
  version: "1.0.30"
monitoring:
  process:
    always_blocked: ["claude.exe", "chatgpt.exe"]
  chrome:
    blocked_urls: ["chatgpt.com", "claude.ai"]
  window_title:
    blocked_keywords: ["ChatGPT", "Claude"]
server:
  base_url: "http://new:30085"
"""


def _frozen(monkeypatch, tmp_path, appdata_text):
    meipass = tmp_path / "meipass"
    (meipass / "config").mkdir(parents=True)
    (meipass / "config" / "agent_config.yaml").write_text(BUNDLED, encoding="utf-8")
    appdata = tmp_path / "agent_config.yaml"
    appdata.write_text(appdata_text, encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    return appdata


def test_policy_lists_synced_from_bundle(monkeypatch, tmp_path):
    appdata = _frozen(monkeypatch, tmp_path, OLD_APPDATA)
    cfg = ConfigManager(str(appdata)).config

    assert cfg.monitoring.chrome.blocked_urls == ["chatgpt.com", "claude.ai"]
    assert cfg.monitoring.process.always_blocked == ["claude.exe", "chatgpt.exe"]
    assert cfg.monitoring.window_title.blocked_keywords == ["ChatGPT", "Claude"]
    # 기존 동기화(버전·서버 주소)도 그대로 동작
    assert cfg.agent.version == "1.0.30"
    assert cfg.server.base_url == "http://new:30085"


def test_synced_values_saved_to_appdata(monkeypatch, tmp_path):
    """동기화된 목록이 APPDATA 파일에 저장돼 다음 실행에도 유지"""
    appdata = _frozen(monkeypatch, tmp_path, OLD_APPDATA)
    ConfigManager(str(appdata))
    saved = yaml.safe_load(appdata.read_text(encoding="utf-8"))
    assert "claude.ai" in saved["monitoring"]["chrome"]["blocked_urls"]
    assert saved["agent"]["id"] == "PC-521-01"


def test_lists_missing_in_bundle_kept(monkeypatch, tmp_path):
    """번들에 없는 목록(예: messenger)은 건드리지 않고 기존 값·기본값 유지"""
    appdata = _frozen(monkeypatch, tmp_path, OLD_APPDATA)
    cfg = ConfigManager(str(appdata)).config
    assert "kakaotalk.exe" in cfg.monitoring.messenger.process_names


def test_not_frozen_no_sync(tmp_path):
    """개발 환경(비 frozen)에서는 동기화하지 않음"""
    appdata = tmp_path / "agent_config.yaml"
    appdata.write_text(OLD_APPDATA, encoding="utf-8")
    cfg = ConfigManager(str(appdata)).config
    assert cfg.monitoring.chrome.blocked_urls == ["chatgpt.com"]
