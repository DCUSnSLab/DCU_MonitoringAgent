"""설치 확인이 끝나기 전에는 식별정보를 생성하거나 에이전트를 시작하지 않는다."""
import subprocess
import sys
from pathlib import Path

import pytest

from agent.identity import Identity
from agent import setup_identity as setup


@pytest.mark.parametrize("hostname,expected", [
    ("D02-521-19", ["D02", "521", "19"]),
    ("D2-535-01", ["D2", "535", "01"]),
    ("DESKTOP-ABC123", ["", "", ""]),
    ("D2-535-01-AABBCC", ["", "", ""]),
])
def test_hostname_autofill(hostname, expected):
    assert setup.initial_values(hostname) == expected


def test_cancel_creates_no_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "prompt_seat", lambda values: None)
    assert setup.main(["--config", str(tmp_path / "agent_config.yaml")]) == 1
    assert not (tmp_path / "installation.json").exists()


def test_confirmation_persists_explicit_seat_over_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.socket, "gethostname", lambda: "D02-521-19")
    observed = []
    def confirm(values):
        observed.append(values)
        return "D2-535-01"
    monkeypatch.setattr(setup, "prompt_seat", confirm)
    assert setup.main(["--config", str(tmp_path / "agent_config.yaml")]) == 0
    assert observed == [["D02", "521", "19"]]
    assert Identity(tmp_path / "installation.json").state["agent_id"] == "D2-535-01"


def test_reinstall_same_seat_preserves_credentials_and_blocks_silent_reassignment(tmp_path, monkeypatch):
    path = tmp_path / "installation.json"
    original = Identity(path)
    original.initialize("", lambda: "unused", "D2-535-01")
    original.state["registered"] = True
    original.save()
    before = original.state.copy()
    monkeypatch.setattr(setup.socket, "gethostname", lambda: "D2-535-99")
    observed = []
    monkeypatch.setattr(setup, "prompt_seat", lambda values: observed.append(values) or "D2-535-01")
    args = ["--config", str(tmp_path / "agent_config.yaml")]
    assert setup.main(args) == 0
    assert observed == [["D2", "535", "01"]]
    assert Identity(path).state == before
    assert setup.main(args + ["--seat", "D2-535-99"]) == 1
    assert Identity(path).state == before


def test_main_process_exit_status_and_no_monitoring_side_effects(tmp_path):
    args = [sys.executable, "-m", "agent.main", "--setup", "--config", str(tmp_path / "agent_config.yaml")]
    root = Path(__file__).resolve().parents[1]
    for seat, expected in (("D9-999-20", 0), ("D9-999-20", 0), ("D9-999-21", 1)):
        proc = subprocess.run(args + ["--seat", seat], cwd=root, capture_output=True, timeout=30)
        assert proc.returncode == expected, proc.stderr
    assert not (tmp_path / "agent_config.yaml").exists()  # 설정 창 단계에서는 수집/서버 통신을 시작하지 않는다


@pytest.mark.parametrize("fields,expected", [
    (("c7", "521", "7"), "C7-521-07"),
    (("C07", "504", "30"), "C7-504-30"),
    (("D02", "521", "04"), "D2-521-04"),
    (("D99", "521", "04"), "D99-521-04"),
    ((" d11 ", " 504 ", " 0 "), "D11-504-00"),
])
def test_seat_normalization(fields, expected):
    assert setup.normalize_seat(*fields) == expected


@pytest.mark.parametrize("fields", [
    ("7C", "521", "7"), ("CC7", "521", "7"), ("C", "521", "7"),
    ("C100", "521", "7"), ("C007", "521", "7"),
    ("C7", "52", "7"), ("C7", "0521", "7"), ("C7", "５２１", "7"),
    ("C7", "521", ""), ("C7", "521", "007"), ("C7", "521", "a"),
])
def test_invalid_seat_fields_rejected(fields):
    with pytest.raises(ValueError):
        setup.normalize_seat(*fields)


@pytest.mark.parametrize("value", ["", "abc123", "Lab PC", "테스트", "Lab-PC", "A" * 129])
def test_custom_id_rejects_non_english_letters(value):
    with pytest.raises(ValueError):
        setup.normalize_custom(value)


def test_custom_id_saved_and_reinstall_prefills_custom_mode(tmp_path, monkeypatch):
    args = ["--config", str(tmp_path / "agent_config.yaml")]
    monkeypatch.setattr(setup, "prompt_seat", lambda values: " LabComputer ")
    assert setup.main(args) == 0
    original = Identity(tmp_path / "installation.json").state
    assert original["agent_id"] == "LabComputer"
    observed = []
    monkeypatch.setattr(setup, "prompt_seat", lambda values, custom_id: observed.append(custom_id) or custom_id)
    assert setup.main(args) == 0
    assert observed == ["LabComputer"]
    assert Identity(tmp_path / "installation.json").state == original


@pytest.mark.parametrize("custom", [False, True])
def test_dialog_radio_selection_and_invalid_confirmation(monkeypatch, custom):
    import tkinter as tk
    from tkinter import messagebox
    original_tk = tk.Tk
    def hidden_root():
        root = original_tk()
        root.withdraw()
        return root
    monkeypatch.setattr(tk, "Tk", hidden_root)
    warnings = []
    monkeypatch.setattr(messagebox, "showwarning", lambda title, message, **kwargs: warnings.append(message))
    def exercise(root, *args):
        radios = [w for w in root.winfo_children() if isinstance(w, tk.Radiobutton)]
        frames = [w for w in root.winfo_children() if isinstance(w, tk.Frame)]
        fields = [w for w in frames[0].winfo_children() if isinstance(w, tk.Entry)]
        custom_field = next(w for w in frames[1].winfo_children() if isinstance(w, tk.Entry))
        confirm = next(w for w in root.winfo_children() if isinstance(w, tk.Button) and w['text'] == '확인')
        if custom:
            radios[1].invoke()
            assert all(str(w['state']) == 'disabled' for w in fields)
            custom_field.insert(0, 'Wrong123')
            confirm.invoke()
            assert len(warnings) == 1
            custom_field.delete(0, tk.END)
            custom_field.insert(0, 'LabComputer')
        else:
            assert str(custom_field['state']) == 'disabled'
            for field, value in zip(fields, ['Wrong', '521', '7']):
                field.insert(0, value)
            confirm.invoke()
            assert len(warnings) == 1
            fields[0].delete(0, tk.END)
            fields[0].insert(0, 'c07')
        confirm.invoke()
    monkeypatch.setattr(tk.Misc, 'mainloop', exercise)
    assert setup.prompt_seat(['', '', '']) == ('LabComputer' if custom else 'C7-521-07')
