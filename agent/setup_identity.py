"""설치 BAT의 자리 확인 단계. 자동 시작/OTA 경로에서는 호출하지 않는다."""
import argparse
import socket
import re
from pathlib import Path
from agent.config import ConfigManager
from agent.identity import Identity, SEAT_PATTERN


def normalize_seat(building, classroom, seat):
    building, classroom, seat = (v.strip() for v in (building, classroom, seat))
    match = re.fullmatch(r"([A-Za-z])([0-9]{1,2})", building)
    if not match:
        raise ValueError("건물번호는 영문자 1개와 숫자 1~2자리로 입력하세요. 예: D2, D11 (100 이상 불가)")
    if not re.fullmatch(r"[0-9]{3}", classroom):
        raise ValueError("실습실번호는 숫자 3자리로 입력하세요. 예: 521, 504")
    if not re.fullmatch(r"[0-9]{1,2}", seat):
        raise ValueError("자리번호는 숫자 1~2자리로 입력하세요. 예: 04, 30")
    result = f"{match[1].upper()}{match[2].lstrip('0') or '0'}-{classroom}-{seat.zfill(2)}"
    if len(result) > 128:
        raise ValueError("에이전트 ID는 128자를 초과할 수 없습니다.")
    return result


def normalize_custom(value):
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z]{1,128}", value):
        raise ValueError("임의 ID는 영문 대소문자만 1~128자 입력하세요. 숫자·공백·특수문자는 사용할 수 없습니다.")
    return value


def normalize_id(value):
    if "-" not in value:
        return normalize_custom(value)
    parts = value.split("-")
    if len(parts) != 3:
        raise ValueError("건물-실습실-자리 형식을 확인하세요.")
    return normalize_seat(*parts)


def initial_values(hostname, saved_seat=None):
    value = saved_seat if saved_seat and SEAT_PATTERN.fullmatch(saved_seat) else hostname
    return value.split("-") if SEAT_PATTERN.fullmatch(value) else ["", "", ""]


def prompt_seat(values, custom_id=""):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.title("에이전트 자리 설정")
    root.resizable(False, False)
    seat = None  # 창 닫기/취소는 성공이 아니다.
    mode = tk.StringVar(value="custom" if custom_id else "seat")
    tk.Radiobutton(root, text="자리 입력", variable=mode, value="seat").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
    seat_frame = tk.Frame(root)
    seat_frame.grid(row=1, column=0, columnspan=2, padx=12)
    entries = []
    for row, (label, value, example) in enumerate(zip(
            ("건물번호", "실습실번호", "자리번호"), values,
            ("ex. D2, D11", "ex. 521, 504", "ex. 04, 30"))):
        tk.Label(seat_frame, text=label).grid(row=row, column=0, padx=8, pady=6)
        entry = tk.Entry(seat_frame)
        entry.insert(0, value)
        entry.grid(row=row, column=1, padx=8, pady=6)
        tk.Label(seat_frame, text=example, fg="gray").grid(row=row, column=2, sticky="w", padx=8)
        entries.append(entry)
    tk.Radiobutton(root, text="임의로 입력", variable=mode, value="custom").grid(row=2, column=0, sticky="w", padx=12, pady=(10, 0))
    custom_frame = tk.Frame(root)
    custom_frame.grid(row=3, column=0, columnspan=2, padx=20, pady=8, sticky="w")
    custom_entry = tk.Entry(custom_frame, width=28)
    custom_entry.insert(0, custom_id)
    custom_entry.grid(row=0, column=0, padx=8)
    tk.Label(custom_frame, text="영문만 입력 (ex. LabComputer)", fg="gray").grid(row=0, column=1, padx=8)

    def select_mode(*_):
        for entry in entries:
            entry.configure(state="normal" if mode.get() == "seat" else "disabled")
        custom_entry.configure(state="normal" if mode.get() == "custom" else "disabled")
        (entries[0] if mode.get() == "seat" else custom_entry).focus_set()
    mode.trace_add("write", select_mode)
    select_mode()

    def confirm():
        nonlocal seat
        try:
            candidate = (normalize_seat(*(e.get() for e in entries)) if mode.get() == "seat"
                         else normalize_custom(custom_entry.get()))
        except ValueError as exc:
            messagebox.showwarning("입력 확인", str(exc), parent=root)
            return
        seat = candidate
        root.destroy()

    tk.Button(root, text="취소", command=root.destroy).grid(row=4, column=0, pady=12)
    tk.Button(root, text="확인", command=confirm).grid(row=4, column=1, pady=12)
    root.bind("<Return>", lambda _: confirm())
    root.bind("<Escape>", lambda _: root.destroy())
    root.mainloop()
    return seat


def show_error(message):
    from tkinter import messagebox
    messagebox.showerror("에이전트 자리 설정", message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seat", help="건물-강의실-자리")
    parser.add_argument("--config", default=ConfigManager.get_default_config_path())
    args = parser.parse_args(argv)
    path = Path(args.config).with_name("installation.json")
    try:
        identity = Identity(path)
        saved = identity.state["agent_id"] if identity.state else None
        custom = {"custom_id": saved} if saved and re.fullmatch(r"[A-Za-z]{1,128}", saved) else {}
        seat = args.seat or prompt_seat(initial_values(socket.gethostname(), saved), **custom)
        if seat is None:
            return 1
        seat = normalize_id(seat)
        if saved and saved != seat:
            raise ValueError("이미 다른 자리로 등록된 설치입니다. 기존 설치를 폐기하고 자리 변경 절차를 진행하세요.")
        # 재설치에서 같은 자리를 확인하면 UUID/토큰/등록 상태를 그대로 재사용한다.
        identity.initialize("", lambda: seat, hostname=seat)
        return 0
    except Exception as exc:
        if not args.seat:
            show_error(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
