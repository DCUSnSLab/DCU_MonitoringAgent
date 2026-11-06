import json
from types import SimpleNamespace
from unittest.mock import Mock
from agent.identity import Identity
from agent.network.installation import InstallationSync


def test_lost_registration_response_reuses_credentials_and_cache_after_ack(tmp_path, monkeypatch):
    identity = Identity(tmp_path / "installation.json")
    identity.initialize("", lambda: "unused", "D2-535-21")
    snapshot = {"hostname": "D2-535-21", "os_version": "Windows", "collected_at": "2026-11-05T00:00:00+00:00",
                "memory_total_bytes": 32, "cpus": [], "gpus": [], "disks": [], "adapters": []}
    monkeypatch.setattr("agent.network.installation.collect", lambda: snapshot)
    client = SimpleNamespace(_session=SimpleNamespace(headers={}), _post_with_response=Mock())
    sync = InstallationSync(identity)
    client._post_with_response.return_value = (False, None)
    assert not sync.prepare(client)
    first = client._post_with_response.call_args.args[1].copy()
    assert not sync.ready.is_set()
    client._post_with_response.side_effect = [(True, {"status": "active"}), (False, None)]
    assert not sync.prepare(client)
    assert sync.ready.is_set()  # 사양 저장 실패는 일반 보고를 막지 않음
    assert not sync.cache.exists()
    assert client._post_with_response.call_args_list[-2].args[1] == first
    client._post_with_response.side_effect = None
    client._post_with_response.return_value = (True, {"status": "synced"})
    assert sync.prepare(client)
    assert sync.cache.exists()
    before = client._post_with_response.call_count
    assert sync.prepare(client)
    assert client._post_with_response.call_count == before


def test_missing_device_query_does_not_become_success_cache(tmp_path, monkeypatch):
    identity = Identity(tmp_path / "installation.json")
    identity.initialize("", lambda: "unused", "D2-535-21")
    identity.state["registered"] = True
    monkeypatch.setattr("agent.network.installation.collect", lambda: {
        "collected_at": "time", "memory_total_bytes": 32, "cpus": [], "gpus": None, "disks": [], "adapters": []})
    sync = InstallationSync(identity)
    client = SimpleNamespace(_session=SimpleNamespace(headers={}),
        _post_with_response=Mock(return_value=(True, {"status": "synced"})))
    assert not sync.prepare(client)
    assert not sync.cache.exists()
    assert sync.snapshot is None
