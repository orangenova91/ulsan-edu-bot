"""
울산교육연수원 연수 이어보기 프로그램 (GUI)

- 아이디/비밀번호 입력 후 [시작] → 로그인 → 나의학습방 > 수강과정 → 이어보기 → 동영상 재생
- 저장: 아이디는 설정 파일에, 비밀번호는 Windows 자격 증명 관리자에 저장 (평문 파일에 저장하지 않음)
- 자동 실행: 프로그램을 열면 몇 초 뒤 자동으로 시작 / 윈도우 로그인 시 프로그램 자동 실행
"""

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from pathlib import Path

import keyring

import login_bot as bot

APP_NAME = "UetiBot"
SETTINGS_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME / "settings.json"
LOG_FILE = SETTINGS_FILE.with_name("last_run.log")  # 마지막 실행 기록
STARTUP_FILE = (
    Path(os.environ.get("APPDATA", Path.home()))
    / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{APP_NAME}.cmd"
)
AUTO_START_DELAY = 5  # 자동 시작 전 대기 시간(초) — 이 사이에 취소할 수 있음


# ---------------------------------------------------------------------------
# 설정 저장/불러오기
# ---------------------------------------------------------------------------
def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_password(user_id: str) -> str:
    try:
        return keyring.get_password(APP_NAME, user_id) or ""
    except Exception:
        return ""


def save_password(user_id: str, password: str) -> None:
    keyring.set_password(APP_NAME, user_id, password)


def delete_password(user_id: str) -> None:
    try:
        keyring.delete_password(APP_NAME, user_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 윈도우 시작 시 자동 실행 (시작프로그램 폴더에 실행 파일 등록)
# ---------------------------------------------------------------------------
def launch_command() -> str:
    if getattr(sys, "frozen", False):  # exe 로 실행 중
        return f'start "" "{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'start "" "{pythonw}" "{Path(__file__).resolve()}"'


def set_run_at_login(enabled: bool) -> None:
    if enabled:
        # 윈도우에서는 텍스트 모드가 \n 을 \r\n 으로 바꿔 저장함
        STARTUP_FILE.write_text(f"@echo off\n{launch_command()}\n", encoding="utf-8")
    elif STARTUP_FILE.exists():
        STARTUP_FILE.unlink()


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("울산교육연수원 연수 이어보기")
        self.geometry("460x480")
        self.minsize(420, 420)

        self.settings = load_settings()
        self.log_queue: queue.Queue = queue.Queue()
        self.running = False
        self.countdown_job = None

        self.var_id = tk.StringVar(value=self.settings.get("user_id", ""))
        self.var_pw = tk.StringVar()
        self.var_course = tk.StringVar(value=self.settings.get("course_name", ""))
        self.var_save = tk.BooleanVar(value=self.settings.get("save_credentials", False))
        self.var_auto = tk.BooleanVar(value=self.settings.get("auto_start", False))
        self.var_login_run = tk.BooleanVar(value=STARTUP_FILE.exists())

        if self.var_save.get() and self.var_id.get():
            self.var_pw.set(load_password(self.var_id.get()))

        self._build()
        # 봇의 출력(log)을 화면 로그 창으로 보냄 (작업 스레드 → 큐 → 화면), 파일에도 기록
        bot.log = self._bot_log
        self.after(100, self._drain_log)

        if self.var_auto.get() and self.var_id.get() and self.var_pw.get():
            self._start_countdown(AUTO_START_DELAY)

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 4}
        form = ttk.Frame(self)
        form.pack(fill="x", **pad)

        ttk.Label(form, text="아이디").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.var_id).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="비밀번호").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.var_pw, show="●").grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="과정명(선택)").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=self.var_course).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(form, text="비우면 수강 목록의 첫 번째 과정", foreground="gray").grid(
            row=3, column=1, sticky="w"
        )
        form.columnconfigure(1, weight=1)

        opts = ttk.Frame(self)
        opts.pack(fill="x", **pad)
        ttk.Checkbutton(opts, text="아이디/비밀번호 저장", variable=self.var_save).pack(anchor="w")
        ttk.Checkbutton(
            opts, text=f"프로그램을 열면 {AUTO_START_DELAY}초 뒤 자동 시작 (저장 필요)", variable=self.var_auto
        ).pack(anchor="w")
        ttk.Checkbutton(
            opts, text="윈도우 로그인 시 이 프로그램 자동 실행", variable=self.var_login_run,
            command=self._toggle_login_run,
        ).pack(anchor="w")

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", **pad)
        self.btn_start = ttk.Button(buttons, text="시작", command=self.on_start)
        self.btn_start.pack(side="left", fill="x", expand=True)
        self.btn_cancel = ttk.Button(buttons, text="자동 시작 취소", command=self._cancel_countdown)

        self.status = ttk.Label(self, text="대기 중", foreground="gray")
        self.status.pack(anchor="w", **pad)

        self.log_box = scrolledtext.ScrolledText(self, height=12, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # ---- 로그 -------------------------------------------------------------
    def _bot_log(self, *args) -> None:
        text = " ".join(str(x) for x in args)
        self.log_queue.put(text)
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            pass

    def _write_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _drain_log(self) -> None:
        # tkinter 화면은 메인 스레드에서만 고칠 수 있으므로, 작업 스레드는 큐에 넣기만 하고 여기서 처리
        while not self.log_queue.empty():
            item = self.log_queue.get()
            if isinstance(item, tuple) and item[0] == "__done__":
                self._finished(item[1])
            else:
                self._write_log(item)
        self.after(100, self._drain_log)

    # ---- 자동 시작 카운트다운 ---------------------------------------------
    def _start_countdown(self, seconds: int) -> None:
        self.btn_cancel.pack(side="left", padx=(8, 0))
        self._tick(seconds)

    def _tick(self, seconds: int) -> None:
        if seconds <= 0:
            self._cancel_countdown(silent=True)
            self.on_start()
            return
        self.status.configure(text=f"{seconds}초 후 자동 시작합니다...", foreground="#b36b00")
        self.countdown_job = self.after(1000, self._tick, seconds - 1)

    def _cancel_countdown(self, silent: bool = False) -> None:
        if self.countdown_job:
            self.after_cancel(self.countdown_job)
            self.countdown_job = None
        self.btn_cancel.pack_forget()
        if not silent:
            self.status.configure(text="자동 시작을 취소했습니다", foreground="gray")

    def _toggle_login_run(self) -> None:
        try:
            set_run_at_login(self.var_login_run.get())
        except OSError as e:
            messagebox.showerror("오류", f"시작프로그램 등록 실패: {e}")
            self.var_login_run.set(STARTUP_FILE.exists())

    # ---- 실행 -------------------------------------------------------------
    def _persist(self) -> None:
        user_id = self.var_id.get().strip()
        old_id = self.settings.get("user_id", "")
        if old_id and old_id != user_id:
            delete_password(old_id)
        if self.var_save.get():
            save_password(user_id, self.var_pw.get())
        else:
            delete_password(user_id)
        self.settings = {
            "user_id": user_id if self.var_save.get() else "",
            "course_name": self.var_course.get().strip(),
            "save_credentials": self.var_save.get(),
            "auto_start": self.var_auto.get() and self.var_save.get(),
        }
        save_settings(self.settings)

    def on_start(self) -> None:
        if self.running:
            return
        self._cancel_countdown(silent=True)
        user_id, user_pw = self.var_id.get().strip(), self.var_pw.get()
        if not user_id or not user_pw:
            messagebox.showwarning("입력 필요", "아이디와 비밀번호를 입력하세요.")
            return
        try:
            self._persist()
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 실패: {e}")

        self.running = True
        self.btn_start.configure(state="disabled", text="실행 중...")
        self.status.configure(text="실행 중 — 크롬 창이 열립니다", foreground="#0b62c4")
        course = self.var_course.get().strip()
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 실행 ===\n", encoding="utf-8")
        threading.Thread(target=self._worker, args=(user_id, user_pw, course), daemon=True).start()

    def _worker(self, user_id: str, user_pw: str, course: str) -> None:
        code = bot.run(user_id, user_pw, course)
        self.log_queue.put(("__done__", code))

    def _finished(self, code: int) -> None:
        self.running = False
        self.btn_start.configure(state="normal", text="다시 시작")
        if code == 0:
            self.status.configure(text="연수 재생을 시작했습니다. 크롬 강의실 창을 확인하세요.", foreground="#1a7f37")
        else:
            self.status.configure(text="중간에 멈췄습니다. 아래 로그를 확인하세요.", foreground="#c62828")


def selftest(out_path: str) -> None:
    """개발용: exe 안에서 저장소·셀레니움이 제대로 로드되는지 확인해 파일로 기록"""
    lines = [f"keyring backend: {type(keyring.get_keyring()).__name__}"]
    keyring.set_password(APP_NAME + "-selftest", "u", "ok")
    lines.append(f"keyring roundtrip: {keyring.get_password(APP_NAME + '-selftest', 'u')}")
    keyring.delete_password(APP_NAME + "-selftest", "u")
    lines.append(f"selenium: {bot.webdriver.__name__}")
    lines.append(f"profile dir: {bot.PROFILE_DIR}")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--selftest":
        try:
            selftest(sys.argv[2])
        except Exception:
            import traceback
            Path(sys.argv[2]).write_text(traceback.format_exc(), encoding="utf-8")
    else:
        App().mainloop()
