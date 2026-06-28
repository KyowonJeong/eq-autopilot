# EQ Autopilot — standalone GUI (Topstep / ProjectX). v4: KO/EN language toggle + single-account
# scope + full persistence. Runs on the user's own machine with their own key.
import os
import sys
import webbrowser
import threading
import queue
import subprocess
import hashlib
import datetime as _dt
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from eqexec.config import ProjectXCfg
from eqexec.broker.projectx import ProjectXBroker

def _app_dir():
    """Per-OS config dir: Windows %APPDATA%, macOS Application Support, else ~/.config."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "EQAutopilot")


APP_DIR = _app_dir()
os.makedirs(APP_DIR, exist_ok=True)
CFG_PATH = os.path.join(APP_DIR, "config.yaml")

URL_HOME = "https://edgequant.app"
URL_JOIN = "https://app.edgequant.app/?nav=registration"
# Free path = join a public signal channel → bot gives a free token. Two channels to choose from.
URL_FREE_DC = "https://discord.gg/jwU4fkfvU"        # public Discord invite (discord_gate._PUBLIC_INVITE)
URL_FREE_TG = "https://t.me/+EpF27gYYhIRjNTJi"      # public Telegram invite (telegram_gate._PUBLIC_INVITE)
# EdgeQuant signal feed the auto-entry loop polls (Streamlit static serving).
FEED_URL = "https://app.edgequant.app/app/static/autopilot_signal.json"
SIG_POLL_SECS = 3                                   # feed poll cadence while the loop runs


def _resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

T = {
    "subtitle": {"ko": "본인 기기에서 본인 키로 실행. EdgeQuant는 키를 받지도, 대신 거래하지도 않습니다.",
                 "en": "Runs on your machine with your key. EdgeQuant never receives your key or trades for you."},
    "warn_mix": {"ko": "⚠ 자동 청산은 사용 계좌의 모든 포지션을 일괄 청산합니다. "
                       "그 계좌에 다른 거래를 섞지 말고 전용 계좌를 사용하세요.",
                 "en": "⚠ Auto-close flattens EVERY position on the chosen account. "
                       "Don't mix other trades on it — use a dedicated account."},
    "lang": {"ko": "언어", "en": "Language"},
    "btn_home": {"ko": "홈페이지", "en": "Website"},
    "btn_join": {"ko": "멤버십 가입", "en": "Join membership"},
    "btn_free": {"ko": "무료 사용", "en": "Use free"},
    "free_msg": {"ko": "공개 시그널 채널에 입장하면 봇이 무료 토큰을 발급합니다.\n채널을 선택하세요:",
                 "en": "Join a public signal channel and the bot issues a free token.\nPick a channel:"},
    "user": {"ko": "TopstepX Username", "en": "TopstepX Username"},
    "key": {"ko": "ProjectX API Key", "en": "ProjectX API Key"},
    "show": {"ko": "보기", "en": "Show"},
    "paste": {"ko": "붙여넣기", "en": "Paste"},
    "unlock": {"ko": "🔓 잠금해제", "en": "🔓 Unlock"},
    "lock": {"ko": "🔒 잠금", "en": "🔒 Lock"},
    "pin_new": {"ko": "키 변경용 PIN을 새로 설정하세요 (숫자/문자):",
                "en": "Set a PIN (needed to change the key):"},
    "pin_enter": {"ko": "PIN 입력:", "en": "Enter PIN:"},
    "pin_wrong": {"ko": "PIN이 틀립니다.", "en": "Wrong PIN."},
    "locked_msg": {"ko": "키를 보거나 바꾸려면 먼저 '잠금해제'(PIN)를 하세요.",
                   "en": "Unlock (PIN) first to view or change the key."},
    "scope": {"ko": "사용 계좌", "en": "Account"},
    "all": {"ko": "(전체 계좌)", "en": "(all accounts)"},
    "scope_note": {"ko": "※ '사용 계좌'에 지정한 계좌에만 청산/진입이 적용됩니다. (전체 = 모든 활성 계좌)",
                   "en": "※ Only the chosen account is flattened/entered. (all = every active account)"},
    "btn_conn": {"ko": "연결 테스트", "en": "Test connection"},
    "btn_accts": {"ko": "계좌 목록 불러오기", "en": "Load accounts"},
    "sec_flat": {"ko": "청산 (Flatten — 사용 계좌의 열린 포지션 닫기)",
                 "en": "Flatten (close open positions on the chosen account)"},
    "dry_close": {"ko": "모의 청산 (Dry-run)", "en": "Dry-run close"},
    "live_close": {"ko": "⚠ 실제 청산 (LIVE)", "en": "⚠ LIVE close"},
    "sec_entry": {"ko": "진입 (Entry — 수동 테스트, 사용 계좌)", "en": "Entry (manual test, chosen account)"},
    "contract": {"ko": "계약ID/심볼", "en": "Contract ID/symbol"},
    "find_contract": {"ko": "계약 조회", "en": "Find contract"},
    "side": {"ko": "방향", "en": "Side"},
    "size": {"ko": "수량", "en": "Size"},
    "sl": {"ko": "손절가", "en": "Stop price"},
    "dry_entry": {"ko": "모의 진입 (Dry-run)", "en": "Dry-run entry"},
    "live_entry": {"ko": "⚠ 실제 진입 (LIVE)", "en": "⚠ LIVE entry"},
    "consent": {"ko": "동의: 본인 키·본인 기기·본인 책임. EdgeQuant는 거래하지 않음 (실행 동작에 필요)",
                "en": "I agree: my key, my device, my responsibility. EdgeQuant does not trade. (required to act)"},
    "ready": {"ko": "준비됨. 키 입력 → '계좌 목록 불러오기'로 사용 계좌 선택 → 연결 테스트/청산/진입.",
              "en": "Ready. Enter key → 'Load accounts' → pick an account → test/close/enter."},
    "need_creds": {"ko": "Username과 API Key를 모두 입력하세요.", "en": "Enter both Username and API Key."},
    "need_consent": {"ko": "실행 동작은 먼저 동의 체크박스를 켜야 합니다.", "en": "Tick the consent box before acting."},
    "live_confirm": {"ko": "실거래 확인", "en": "Confirm LIVE"},
    "input_needed": {"ko": "입력 필요", "en": "Input needed"},
    "pick_acct": {"ko": "진입은 '사용 계좌'에서 단일 계좌를 지정해야 합니다 (전체 불가).",
                  "en": "Entry requires a single account in 'Account' (not all)."},
    "sec_auto": {"ko": "자동 운영 (매일 자동 청산)", "en": "Autopilot (daily auto-close)"},
    "cutoff": {"ko": "청산 시각(ET)", "en": "Close time (ET)"},
    "auto_live": {"ko": "실제 청산으로 실행 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "auto_start": {"ko": "자동 운영 시작", "en": "Start autopilot"},
    "auto_stop": {"ko": "자동 운영 중지", "en": "Stop autopilot"},
    "auto_on_ind": {"ko": "  ● 자동 운영 ON  ", "en": "  ● Autopilot ON  "},
    "auto_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sec_sig": {"ko": "자동 진입 (실시간 신호)", "en": "Auto-entry (live signal)"},
    "sig_live": {"ko": "실제 진입 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "sig_start": {"ko": "신호 대기 시작", "en": "Start signal watch"},
    "sig_stop": {"ko": "신호 대기 중지", "en": "Stop signal watch"},
    "sig_on_ind": {"ko": "  ● 신호 대기 ON  ", "en": "  ● Watching ON  "},
    "sig_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sig_note": {"ko": "※ 위 '진입' 섹션의 계약ID·수량·'사용 계좌'로 진입합니다. 신호가 오면 그 방향+손절가로 "
                       "자동 진입합니다.",
                 "en": "※ Enters on the 'Entry' section's contract ID + size + chosen account, in the "
                       "signal's direction with the signal's stop."},
    "sig_need_contract": {"ko": "먼저 '진입' 섹션에 계약ID를 넣으세요(계약 조회).",
                          "en": "Set a contract ID in the 'Entry' section first (Find contract)."},
    "auto_note": {"ko": "※ 앱이 떠 있고 컴퓨터가 켜져(절전 해제) 있어야 작동. 매일 그 시각에 '사용 계좌'를 청산합니다.",
                  "en": "※ App must stay open and the computer awake. Closes the chosen account daily at that time."},
}


KC_SERVICE = "EQAutopilot"   # Keychain / Credential-Manager service name
_IS_MAC = sys.platform == "darwin"


def _kc_save(account, secret):
    """Store the API key in the OS secret store (never on disk): macOS Keychain via `security`,
    Windows/Linux via the `keyring` lib (Windows Credential Manager / Secret Service)."""
    if not secret:
        return
    if _IS_MAC:
        try:
            subprocess.run(["/usr/bin/security", "add-generic-password", "-a", account or "default",
                            "-s", KC_SERVICE, "-w", secret, "-U"], capture_output=True, timeout=8)
        except Exception:
            pass
        return
    try:
        import keyring
        keyring.set_password(KC_SERVICE, account or "default", secret)
    except Exception:
        pass


def _kc_load(account):
    if _IS_MAC:
        try:
            r = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", account or "default",
                                "-s", KC_SERVICE, "-w"], capture_output=True, text=True, timeout=8)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""
    try:
        import keyring
        return keyring.get_password(KC_SERVICE, account or "default") or ""
    except Exception:
        return ""


def _pin_hash():
    return _kc_load("__eqpin__")


def _pin_set(pin):
    _kc_save("__eqpin__", hashlib.sha256(pin.encode()).hexdigest())


def _pin_ok(pin):
    h = _pin_hash()
    return bool(h) and hashlib.sha256(pin.encode()).hexdigest() == h


def _load():
    try:
        import yaml
        with open(CFG_PATH) as f:
            d = yaml.safe_load(f) or {}
        px = d.get("projectx", {}); a = px.get("accounts") or []
        user = px.get("user_name", "")
        # Key is loaded from Keychain ASYNC (after the window is up) so the GUI never blocks on
        # the `security` subprocess at startup. _load() stays fast (yaml only).
        return {"user": user, "key": px.get("api_key", ""), "acct": (a[0] if a else ""),
                "lang": d.get("lang", "ko")}
    except Exception:
        return {"user": "", "key": "", "acct": "", "lang": "ko"}


def _save(user, key, acct, lang):
    _kc_save(user, key)                                  # key → Keychain only
    try:
        import yaml
        with open(CFG_PATH, "w") as f:
            yaml.safe_dump({"live": False, "broker": "projectx", "lang": lang,
                            "projectx": {"base_url": "https://api.topstepx.com", "user_name": user,
                                         "api_key": "", "accounts": ([acct] if acct else [])}}, f)
    except Exception:
        pass


class App:
    def __init__(self, root):
        self.root = root
        root.title("EQ Autopilot")
        root.geometry("720x860")
        self.q = queue.Queue()
        self.lang = _load()["lang"]
        self.frm = None
        self._auto_on = False
        self._sig_on = False
        self._unlocked = False
        self._build()
        root.after(120, self._drain)

    def _async_load_key(self, user):
        """Read the key from Keychain off the main thread, then fill the field — never blocks the GUI."""
        def w():
            k = _kc_load(user)
            if k:
                self.root.after(0, lambda: self._set_key(k))
        threading.Thread(target=w, daemon=True).start()

    def _set_key(self, text):
        """Set the (readonly) key field's value programmatically."""
        self.key.config(state="normal")
        self.key.delete(0, "end"); self.key.insert(0, text)
        if not self._unlocked:
            self.key.config(state="readonly")

    def t(self, k):
        return T[k][self.lang]

    def _set_auto_ind(self, on):
        """Green lit pill while autopilot runs; gray when off."""
        try:
            self.auto_ind.config(text=self.t("auto_on_ind") if on else self.t("auto_off_ind"),
                                 fg="white" if on else "#666",
                                 bg="#22a722" if on else "#dddddd")
        except Exception:
            pass

    def _set_sig_ind(self, on):
        """Green lit pill while the signal-watch loop runs; gray when off."""
        try:
            self.sig_ind.config(text=self.t("sig_on_ind") if on else self.t("sig_off_ind"),
                                fg="white" if on else "#666",
                                bg="#22a722" if on else "#dddddd")
        except Exception:
            pass

    def _build(self):
        d = _load()
        if self.frm is not None:
            self.frm.destroy()
        frm = ttk.Frame(self.root, padding=14); frm.pack(fill="both", expand=True); self.frm = frm

        top = ttk.Frame(frm); top.pack(fill="x")
        try:
            self._logo = tk.PhotoImage(file=_resource("eqlogo.png"))
            ttk.Label(top, image=self._logo).pack(side="left", padx=(0, 8))
        except Exception:
            self._logo = None
        ttk.Label(top, text="EQ Autopilot — Topstep (ProjectX)", font=("Helvetica", 16, "bold")).pack(side="left")
        ttk.Label(top, text=self.t("lang")).pack(side="right", padx=(0, 4))
        self.langbox = ttk.Combobox(top, values=["한국어", "English"], width=9, state="readonly")
        self.langbox.set("English" if self.lang == "en" else "한국어")
        self.langbox.pack(side="right"); self.langbox.bind("<<ComboboxSelected>>", self._set_lang)
        links = ttk.Frame(frm); links.pack(fill="x", pady=(4, 0))
        ttk.Button(links, text=self.t("btn_home"), command=lambda: webbrowser.open(URL_HOME)).pack(side="left")
        ttk.Button(links, text=self.t("btn_join"), command=lambda: webbrowser.open(URL_JOIN)).pack(side="left", padx=6)
        ttk.Button(links, text=self.t("btn_free"), command=self._open_free).pack(side="left")
        ttk.Label(frm, text=self.t("subtitle"), foreground="#666").pack(anchor="w", pady=(6, 4))
        tk.Label(frm, text=self.t("warn_mix"), foreground="#b00020", wraplength=660,
                 justify="left", font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 8))

        r1 = ttk.Frame(frm); r1.pack(fill="x", pady=3)
        ttk.Label(r1, text=self.t("user"), width=18).pack(side="left")
        self.user = ttk.Entry(r1); self.user.pack(side="left", fill="x", expand=True); self.user.insert(0, d["user"])
        r2 = ttk.Frame(frm); r2.pack(fill="x", pady=3)
        ttk.Label(r2, text=self.t("key"), width=18).pack(side="left")
        self.key = ttk.Entry(r2, show="•"); self.key.pack(side="left", fill="x", expand=True)
        self.key.insert(0, d["key"]); self.key.config(state="readonly")
        self.b_lock = ttk.Button(r2, text=self.t("unlock"), width=11, command=self._unlock)
        self.b_lock.pack(side="left", padx=(4, 0))
        ttk.Button(r2, text=self.t("paste"), width=8, command=self._paste_key).pack(side="left", padx=(4, 0))
        self.show = tk.IntVar()
        ttk.Checkbutton(r2, text=self.t("show"), variable=self.show, command=self._toggle).pack(side="left", padx=5)

        r3 = ttk.Frame(frm); r3.pack(fill="x", pady=3)
        ttk.Label(r3, text=self.t("scope"), width=18).pack(side="left")
        self.scope = ttk.Combobox(r3, values=[self.t("all")], state="normal")
        self.scope.set(d["acct"] or self.t("all")); self.scope.pack(side="left", fill="x", expand=True)
        ttk.Label(frm, text=self.t("scope_note"), foreground="#888").pack(anchor="w")

        row = ttk.Frame(frm); row.pack(fill="x", pady=(8, 2))
        self.b_hc = ttk.Button(row, text=self.t("btn_conn"), command=self.healthcheck); self.b_hc.pack(side="left")
        self.b_acc = ttk.Button(row, text=self.t("btn_accts"), command=self.accounts); self.b_acc.pack(side="left", padx=6)

        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_flat"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        cf = ttk.Frame(frm); cf.pack(fill="x", pady=3)
        ttk.Button(cf, text=self.t("dry_close"), command=lambda: self.flatten(False)).pack(side="left")
        ttk.Button(cf, text=self.t("live_close"), command=lambda: self.flatten(True)).pack(side="left", padx=8)

        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_entry"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        ef = ttk.Frame(frm); ef.pack(fill="x", pady=3)
        ttk.Label(ef, text=self.t("contract")).pack(side="left")
        self.contract = ttk.Entry(ef, width=20); self.contract.pack(side="left", padx=(2, 4))
        ttk.Button(ef, text=self.t("find_contract"), width=8, command=self.find_contract).pack(side="left", padx=(0, 8))
        ttk.Label(ef, text=self.t("side")).pack(side="left")
        self.side = ttk.Combobox(ef, values=["LONG", "SHORT"], width=7, state="readonly"); self.side.set("LONG")
        self.side.pack(side="left", padx=(2, 8))
        ttk.Label(ef, text=self.t("size")).pack(side="left")
        self.size = ttk.Spinbox(ef, from_=1, to=50, width=5); self.size.set("1"); self.size.pack(side="left", padx=(2, 8))
        ttk.Label(ef, text=self.t("sl")).pack(side="left")
        self.sl = ttk.Entry(ef, width=10); self.sl.pack(side="left", padx=2)
        ef3 = ttk.Frame(frm); ef3.pack(fill="x", pady=3)
        ttk.Button(ef3, text=self.t("dry_entry"), command=lambda: self.entry(False)).pack(side="left")
        ttk.Button(ef3, text=self.t("live_entry"), command=lambda: self.entry(True)).pack(side="left", padx=8)

        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_auto"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        af = ttk.Frame(frm); af.pack(fill="x", pady=3)
        ttk.Label(af, text=self.t("cutoff")).pack(side="left")
        self.cutoff = ttk.Entry(af, width=7); self.cutoff.insert(0, "14:00"); self.cutoff.pack(side="left", padx=(2, 10))
        self.auto_live = tk.IntVar()
        ttk.Checkbutton(af, text=self.t("auto_live"), variable=self.auto_live).pack(side="left", padx=(0, 10))
        self.b_auto = ttk.Button(af, text=self.t("auto_stop") if self._auto_on else self.t("auto_start"),
                                 command=self.toggle_auto); self.b_auto.pack(side="left")
        self.auto_ind = tk.Label(af, font=("Helvetica", 11, "bold"))
        self.auto_ind.pack(side="left", padx=(10, 0))
        self._set_auto_ind(self._auto_on)
        ttk.Label(frm, text=self.t("auto_note"), foreground="#888").pack(anchor="w")

        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_sig"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        sg = ttk.Frame(frm); sg.pack(fill="x", pady=3)
        self.sig_live = tk.IntVar()
        ttk.Checkbutton(sg, text=self.t("sig_live"), variable=self.sig_live).pack(side="left", padx=(0, 12))
        self.b_sig = ttk.Button(sg, text=self.t("sig_stop") if self._sig_on else self.t("sig_start"),
                                command=self.toggle_sig); self.b_sig.pack(side="left")
        self.sig_ind = tk.Label(sg, font=("Helvetica", 11, "bold"))
        self.sig_ind.pack(side="left", padx=(10, 0))
        self._set_sig_ind(self._sig_on)
        ttk.Label(frm, text=self.t("sig_note"), foreground="#888", wraplength=660,
                  justify="left").pack(anchor="w")

        ttk.Separator(frm).pack(fill="x", pady=8)
        self.consent = tk.IntVar()
        ttk.Checkbutton(frm, variable=self.consent, text=self.t("consent")).pack(anchor="w")
        self.out = scrolledtext.ScrolledText(frm, height=10, font=("Menlo", 11), wrap="word")
        self.out.pack(fill="both", expand=True, pady=(6, 0))
        self.log(self.t("ready"))
        self._async_load_key(d.get("user", ""))

    def _open_free(self):
        """Let the user pick Discord or Telegram for the free public signal channel."""
        win = tk.Toplevel(self.root)
        win.title(self.t("btn_free"))
        win.transient(self.root); win.resizable(False, False); win.grab_set()
        ttk.Label(win, text=self.t("free_msg"), wraplength=320, justify="left",
                  padding=16).pack(fill="x")
        bf = ttk.Frame(win, padding=(16, 0, 16, 16)); bf.pack(fill="x")

        def go(url):
            webbrowser.open(url); win.destroy()
        ttk.Button(bf, text="Discord", command=lambda: go(URL_FREE_DC)).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(bf, text="Telegram", command=lambda: go(URL_FREE_TG)).pack(
            side="left", expand=True, fill="x", padx=(4, 0))
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_rootx() + 60}+{self.root.winfo_rooty() + 90}")

    def _set_lang(self, *_):
        self.lang = "en" if self.langbox.get() == "English" else "ko"
        _save(self.user.get().strip(), self.key.get().strip(), self._scope(), self.lang)
        self._unlocked = False
        self._build()

    def _unlock(self):
        if self._unlocked:                                   # → re-lock
            self._unlocked = False
            self.show.set(0)
            self.key.config(state="readonly", show="•")
            self.b_lock.config(text=self.t("unlock"))
            return
        if not _pin_hash():                                  # first time → set a PIN
            p = simpledialog.askstring("PIN", self.t("pin_new"), show="*", parent=self.root)
            if not p:
                return
            _pin_set(p)
        else:
            p = simpledialog.askstring("PIN", self.t("pin_enter"), show="*", parent=self.root)
            if not p or not _pin_ok(p):
                messagebox.showwarning("PIN", self.t("pin_wrong")); return
        self._unlocked = True
        self.key.config(state="normal")
        self.b_lock.config(text=self.t("lock"))

    def _paste_key(self):
        if not self._unlocked:
            messagebox.showinfo("PIN", self.t("locked_msg")); return
        try:
            self.key.delete(0, "end")
            self.key.insert(0, self.root.clipboard_get().strip())
        except Exception:
            pass

    def _toggle(self):
        if not self._unlocked:
            self.show.set(0)
            messagebox.showinfo("PIN", self.t("locked_msg")); return
        self.key.config(show="" if self.show.get() else "•")

    def log(self, m): self.q.put(m)

    def _drain(self):
        while not self.q.empty():
            try:
                self.out.insert("end", self.q.get() + "\n"); self.out.see("end")
            except Exception:
                pass
        self.root.after(120, self._drain)

    def _scope(self):
        s = self.scope.get().strip()
        return "" if s in ("", self.t("all"), T["all"]["ko"], T["all"]["en"]) else s

    def _broker(self):
        sc = self._scope()
        return ProjectXBroker(ProjectXCfg(base_url="https://api.topstepx.com",
                                          user_name=self.user.get().strip(), api_key=self.key.get().strip(),
                                          accounts=([sc] if sc else [])))

    def _busy(self, on):
        for b in (self.b_hc, self.b_acc):
            b.config(state="disabled" if on else "normal")

    def _creds_ok(self):
        if not self.user.get().strip() or not self.key.get().strip():
            messagebox.showwarning(self.t("input_needed"), self.t("need_creds")); return False
        _save(self.user.get().strip(), self.key.get().strip(), self._scope(), self.lang); return True

    def _consent_ok(self):
        if not self.consent.get():
            messagebox.showwarning(self.t("need_consent"), self.t("need_consent")); return False
        return True

    def _fill_scope(self, names):
        cur = self.scope.get()
        self.scope["values"] = [self.t("all")] + names
        if cur not in ([self.t("all")] + names):
            self.scope.set(self.t("all"))

    def _run(self, fn):
        self._busy(True)
        def wrap():
            try:
                fn()
            except Exception as e:
                self.log(f"❌ {e}")
            finally:
                self.root.after(0, lambda: self._busy(False))
        threading.Thread(target=wrap, daemon=True).start()

    def healthcheck(self):
        if not self._creds_ok(): return
        self.log("\n── connection test ──")
        def w():
            b = self._broker(); b.authenticate()
            names = [str(a.get("name")) for a in b._accounts()]
            self.root.after(0, lambda: self._fill_scope(names))
            pos = b.list_open_positions(); sc = self._scope()
            self.log(f"✅ connected ({sc or 'all'}) — open positions: {len(pos)}")
            for p in pos: self.log(f"   • {p.account_name} / {p.symbol}  net={p.net_qty}")
            if not pos: self.log("   (flat)")
        self._run(w)

    def accounts(self):
        if not self._creds_ok(): return
        self.log("\n── accounts ──")
        def w():
            b = ProjectXBroker(ProjectXCfg(base_url="https://api.topstepx.com",
                                           user_name=self.user.get().strip(), api_key=self.key.get().strip()))
            accts = b._accounts()
            names = [str(a.get("name")) for a in accts]
            self.root.after(0, lambda: self._fill_scope(names))
            for a in accts:
                self.log(f"   • name={a.get('name')}  id={a.get('id')}  bal={a.get('balance')}  canTrade={a.get('canTrade')}")
        self._run(w)

    def flatten(self, live):
        if not self._creds_ok() or not self._consent_ok(): return
        sc = self._scope()
        if live and not messagebox.askyesno(
                self.t("live_confirm"),
                f"LIVE flatten.\nAccount: {sc or '⚠ ALL'}\n\n{self.t('warn_mix')}\n\nProceed?"):
            return
        self.log(f"\n── {'⚠ LIVE' if live else 'dry-run'} flatten ({sc or 'all'}) ──")
        def w():
            b = self._broker(); res = b.flatten_all(dry_run=not live)
            for p in res.planned: self.log(f"   • {p.account_name} / {p.symbol}  net={p.net_qty}")
            if not live:
                self.log("DRY-RUN — no orders sent." if res.planned else "no open positions (flat).")
                return
            self.log("closed: " + (", ".join(f"{p.account_name}/{p.symbol}" for p in res.closed) or "—"))
            if res.cancelled:
                self.log(f"cancelled orders: {len(res.cancelled)}")
            for e in res.errors: self.log(f"   ⚠ {e}")
            self.log("✅ flat" if not res.errors else "⚠ INCOMPLETE — check broker now!")
        self._run(w)

    def entry(self, live):
        if not self._creds_ok() or not self._consent_ok(): return
        sc = self._scope()
        if not sc:
            messagebox.showwarning(self.t("scope"), self.t("pick_acct")); return
        contract = self.contract.get().strip()
        if not contract:
            messagebox.showwarning(self.t("input_needed"), self.t("contract")); return
        side, size = self.side.get(), int(self.size.get())
        slv = self.sl.get().strip()
        try:
            sl_price = float(slv) if slv else None
        except ValueError:
            messagebox.showwarning(self.t("input_needed"), self.t("sl") + " = 21450.0"); return
        sl_txt = f"\nStop: {sl_price}" if sl_price is not None else ""
        if live and not messagebox.askyesno(self.t("live_confirm"),
                f"LIVE entry.\n{side} {size} @ {contract}{sl_txt}\nAccount: {sc}\nProceed?"):
            return
        self.log(f"\n── {'⚠ LIVE' if live else 'dry-run'} entry: {side} {size} {contract} "
                 f"(SL@{sl_price if sl_price is not None else '—'}) ({sc}) ──")
        def w():
            b = self._broker()
            match = [a for a in b._accounts() if str(a.get("name")) == sc or str(a.get("id")) == sc]
            if not match:
                self.log(f"❌ account '{sc}' not found — use 'Load accounts'."); return
            aid = match[0]["id"]
            r = b.place_entry(account_id=aid, contract_id=contract, side=side, size=size, order_type=2,
                              stop_loss_price=sl_price, custom_tag="EQ-Autopilot-test", dry_run=not live)
            if not live:
                self.log(f"DRY-RUN — entry: {r.get('would_place')}")
                if r.get("would_place_stop"):
                    self.log(f"DRY-RUN — stop:  {r.get('would_place_stop')}")
            else:
                self.log(f"✅ order sent: {r}")
        self._run(w)

    def find_contract(self):
        if not self._creds_ok(): return
        term = self.contract.get().strip()
        if not term:
            messagebox.showwarning(self.t("input_needed"), "MNQ / NQ / GC …"); return
        self.log(f"\n── contract search: {term} ──")
        def w():
            b = ProjectXBroker(ProjectXCfg(base_url="https://api.topstepx.com",
                                           user_name=self.user.get().strip(), api_key=self.key.get().strip()))
            cs = b.search_contracts(term)
            if not cs:
                self.log("   no contracts found."); return
            active = None
            for c in cs:
                act = c.get("activeContract")
                self.log(f"   • {c.get('id')}   {c.get('name')}   active={act}")
                if act and active is None:
                    active = c.get("id")
            pick = active or cs[0].get("id")
            if pick:
                self.root.after(0, lambda: (self.contract.delete(0, "end"), self.contract.insert(0, pick)))
                self.log(f"→ filled: {pick}")
        self._run(w)

    def toggle_auto(self):
        if self._auto_on:
            self._auto_on = False
            self.b_auto.config(text=self.t("auto_start"))
            self._set_auto_ind(False)
            self.log("⏹ autopilot stopped.")
            return
        if not self._creds_ok() or not self._consent_ok():
            return
        cutoff = self.cutoff.get().strip()
        if len(cutoff) != 5 or cutoff[2] != ":" or not (cutoff[:2] + cutoff[3:]).isdigit():
            messagebox.showwarning(self.t("input_needed"), "HH:MM"); return
        user, key, sc = self.user.get().strip(), self.key.get().strip(), self._scope()
        live = bool(self.auto_live.get())
        self._auto_on = True
        self.b_auto.config(text=self.t("auto_stop"))
        self._set_auto_ind(True)
        self.log(f"\n▶ autopilot ON — daily {cutoff} ET · account [{sc or 'all'}] · "
                 f"{'LIVE' if live else 'dry-run'}. (keep the app open & the computer awake)")
        self.log(f"   {self.t('warn_mix')}")
        threading.Thread(target=self._auto_loop, args=(cutoff, user, key, sc, live), daemon=True).start()

    def _auto_loop(self, cutoff, user, key, sc, live):
        import time as _t
        tz = ZoneInfo("America/New_York") if ZoneInfo else None
        fired = None
        while self._auto_on:
            now = _dt.datetime.now(tz)
            today = now.strftime("%Y-%m-%d")
            if now.strftime("%H:%M") >= cutoff and fired != today:
                fired = today
                self.log(f"\n⏰ {cutoff} ET → auto-close ([{sc or 'all'}], {'LIVE' if live else 'dry-run'})")
                try:
                    b = ProjectXBroker(ProjectXCfg(base_url="https://api.topstepx.com", user_name=user,
                                                   api_key=key, accounts=([sc] if sc else [])))
                    res = b.flatten_all(dry_run=not live)
                    if not res.planned:
                        self.log("   no open positions (flat).")
                    elif not live:
                        self.log("   DRY-RUN plan: " + ", ".join(f"{p.account_name}/{p.symbol}" for p in res.planned))
                    else:
                        self.log("   closed: " + (", ".join(f"{p.account_name}/{p.symbol}" for p in res.closed) or "—"))
                        for e in res.errors:
                            self.log(f"   ⚠ {e}")
                        if not res.errors:
                            self.log("   ✅ flat")
                except Exception as e:
                    self.log(f"   ❌ auto-close failed — {e}")
            _t.sleep(15)

    # ── auto-ENTRY on EdgeQuant signal ──────────────────────────────────────
    def toggle_sig(self):
        if self._sig_on:
            self._sig_on = False
            self.b_sig.config(text=self.t("sig_start"))
            self._set_sig_ind(False)
            self.log("⏹ signal watch stopped.")
            return
        if not self._creds_ok() or not self._consent_ok():
            return
        sc = self._scope()
        if not sc:
            messagebox.showwarning(self.t("scope"), self.t("pick_acct")); return
        contract = self.contract.get().strip()
        if not contract:
            messagebox.showwarning(self.t("input_needed"), self.t("sig_need_contract")); return
        user, key = self.user.get().strip(), self.key.get().strip()
        try:
            size = int(self.size.get())          # reuse the Entry section's quantity
        except ValueError:
            size = 1
        live = bool(self.sig_live.get())
        self._sig_on = True
        self.b_sig.config(text=self.t("sig_stop"))
        self._set_sig_ind(True)
        self.log(f"\n▶ signal watch ON — {contract} · account [{sc}] · size {size} · "
                 f"{'LIVE' if live else 'dry-run'}. polling {FEED_URL}")
        threading.Thread(target=self._sig_loop,
                         args=(FEED_URL, user, key, sc, contract, size, live),
                         daemon=True).start()

    def _sig_loop(self, url, user, key, sc, contract, size, live):
        import time as _t
        import requests
        last_id = None
        while self._sig_on:
            try:
                r = requests.get(url, params={"t": int(_t.time())}, timeout=8)
                sig = r.json() if r.ok else {}
            except Exception as e:
                self.log(f"   signal feed error: {e}"); _t.sleep(SIG_POLL_SECS); continue
            sid = sig.get("id")
            if sid and sid != last_id and sig.get("tradeable") and sig.get("direction"):
                last_id = sid
                direction, stop = sig.get("direction"), sig.get("stop_price")
                self.log(f"\n📶 signal {sid}: {direction} {sig.get('instrument')} stop@{stop} "
                         f"→ {'LIVE' if live else 'dry-run'} entry on {contract} [{sc}]")
                try:
                    b = ProjectXBroker(ProjectXCfg(base_url="https://api.topstepx.com", user_name=user,
                                                   api_key=key, accounts=[sc]))
                    match = [a for a in b._accounts()
                             if str(a.get("name")) == sc or str(a.get("id")) == sc]
                    if not match:
                        self.log(f"   ❌ account '{sc}' not found."); continue
                    aid = match[0]["id"]
                    res = b.place_entry(account_id=aid, contract_id=contract, side=direction,
                                        size=size, order_type=2, stop_loss_price=stop,
                                        custom_tag="EQ-Autopilot-signal", dry_run=not live)
                    if not live:
                        self.log(f"   DRY-RUN entry: {res.get('would_place')}")
                        if res.get("would_place_stop"):
                            self.log(f"   DRY-RUN stop:  {res.get('would_place_stop')}")
                    else:
                        self.log(f"   ✅ entered: {res}")
                except Exception as e:
                    self.log(f"   ❌ signal entry failed: {e}")
            _t.sleep(SIG_POLL_SECS)


def _bind_clipboard(root):
    """macOS/PyInstaller Tk doesn't wire Cmd+C/V/X/A → paste was dead. Implement them directly
    against the system clipboard (clipboard_get/append) and bind on several modifier names."""
    def _paste(e):
        w = e.widget
        try:
            try:
                w.delete("sel.first", "sel.last")
            except Exception:
                pass
            w.insert("insert", root.clipboard_get())
        except Exception:
            pass
        return "break"

    def _copy(e):
        try:
            s = e.widget.selection_get()
            root.clipboard_clear(); root.clipboard_append(s)
        except Exception:
            pass
        return "break"

    def _cut(e):
        _copy(e)
        try:
            e.widget.delete("sel.first", "sel.last")
        except Exception:
            pass
        return "break"

    def _all(e):
        try:
            e.widget.select_range(0, "end"); e.widget.icursor("end")
        except Exception:
            pass
        return "break"

    for keych, fn in (("v", _paste), ("c", _copy), ("x", _cut), ("a", _all)):
        for mod in ("Command", "Mod1", "Control"):
            for kc in (keych, keych.upper()):
                try:
                    root.bind_all(f"<{mod}-{kc}>", fn)
                except Exception:
                    pass
    # Standard Edit menu (gives macOS menu access + native routing as a fallback).
    try:
        mb = tk.Menu(root)
        em = tk.Menu(mb, tearoff=0)

        def _ev(name):
            return lambda: (root.focus_get().event_generate(name) if root.focus_get() else None)
        for label, acc, ev in (("Cut", "Cmd+X", "<<Cut>>"), ("Copy", "Cmd+C", "<<Copy>>"),
                               ("Paste", "Cmd+V", "<<Paste>>"), ("Select All", "Cmd+A", "<<SelectAll>>")):
            em.add_command(label=label, accelerator=acc, command=_ev(ev))
        mb.add_cascade(label="Edit", menu=em)
        root.config(menu=mb)
    except Exception:
        pass


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("aqua")
    except Exception:
        pass
    _bind_clipboard(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
