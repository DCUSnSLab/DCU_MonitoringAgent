from agent.identity import Identity


def test_seat_transition_and_identity_survives_hostname_change(tmp_path):
    path = tmp_path / "identity.json"
    identity = Identity(path)
    assert identity.initialize("old-mac", lambda: "fallback", "D2-535-21") == "D2-535-21"
    state = identity.state.copy()
    loaded = Identity(path)
    assert loaded.initialize("other", lambda: "other", "DESKTOP-NEW") == "D2-535-21"
    assert loaded.state == state


def test_unknown_hostname_keeps_legacy_or_persists_fallback(tmp_path):
    identity = Identity(tmp_path / "one")
    assert identity.initialize("legacy", lambda: "fallback", "DESKTOP-ABC") == "legacy"
    identity = Identity(tmp_path / "two")
    assert identity.initialize("", lambda: "fallback", "DESKTOP-ABC") == "fallback"
    assert Identity(tmp_path / "two").state["installation_id"] == identity.state["installation_id"]
