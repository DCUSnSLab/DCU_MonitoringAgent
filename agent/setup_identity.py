"""관리자용 선택적 초기 설정 창. 자동 시작/OTA 경로에서는 호출하지 않는다."""
import argparse
import socket
from pathlib import Path
from agent.config import ConfigManager
from agent.identity import Identity, SEAT_PATTERN


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seat", help="건물-실습실-자리")
    parser.add_argument("--config", default=ConfigManager.get_default_config_path())
    args = parser.parse_args(argv)
    path = Path(args.config).with_name("installation.json")
    if path.exists():
        raise SystemExit("이미 설치 식별정보가 있습니다. 자리 변경은 서버의 기존 설치 폐기 후 별도로 처리하세요.")
    seat = args.seat
    if not seat:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.title("에이전트 초기 설정")
        values = socket.gethostname().split("-") if SEAT_PATTERN.fullmatch(socket.gethostname()) else ["", "", ""]
        entries = []
        for row, (label, value) in enumerate(zip(("건물번호", "실습실번호", "자리번호"), values)):
            tk.Label(root, text=label).grid(row=row, column=0, padx=12, pady=6)
            entry = tk.Entry(root)
            entry.insert(0, value)
            entry.grid(row=row, column=1, padx=12, pady=6)
            entries.append(entry)
        def confirm():
            nonlocal seat
            candidate = "-".join(e.get().strip() for e in entries)
            if not SEAT_PATTERN.fullmatch(candidate):
                messagebox.showerror("입력 확인", "건물-실습실번호-자리번호 형식을 확인하세요.")
                return
            seat = candidate
            root.destroy()
        tk.Button(root, text="확인", command=confirm).grid(row=3, columnspan=2, pady=10)
        root.mainloop()
    if not seat or not SEAT_PATTERN.fullmatch(seat):
        raise SystemExit("올바른 자리 코드가 필요합니다.")
    Identity(path).initialize("", lambda: seat, hostname=seat)
    print("자리 설정 완료:", seat)


if __name__ == "__main__":
    main()
