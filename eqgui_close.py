# EQ Auto-Close — standalone GUI (Topstep / ProjectX). AUTO-CLOSE ONLY build (no entry/signal):
# connect → pick account → flatten (dry/LIVE) → daily auto-close at a set time. Derived from eqgui.py.
# KO/EN, single-account scope, key in OS secret store. Runs on the user's machine with their own key.
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
    "user": {"ko": "TopstepX user email", "en": "TopstepX user email"},
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
    "consent": {"ko": "동의: 본인 키·본인 기기·본인 책임. EdgeQuant는 거래하지 않음 (실행 동작에 필요)",
                "en": "I agree: my key, my device, my responsibility. EdgeQuant does not trade. (required to act)"},
    "ready": {"ko": "준비됨. 키 입력 → '연결 테스트' → 통과하면 나머지 기능이 켜집니다.",
              "en": "Ready. Enter key → 'Test connection' → the rest unlocks once it passes."},
    "conn_first": {"ko": "※ 먼저 '연결 테스트'를 통과해야 청산·자동 청산 기능이 활성화됩니다.",
                   "en": "※ Pass 'Test connection' first to unlock close / auto-close."},
    "conn_ok": {"ko": "기능이 활성화되었습니다.", "en": "Features unlocked."},
    "need_creds": {"ko": "이메일과 API Key를 모두 입력하세요.", "en": "Enter both email and API Key."},
    "need_consent": {"ko": "실행 동작은 먼저 동의 체크박스를 켜야 합니다.", "en": "Tick the consent box before acting."},
    "live_confirm": {"ko": "실거래 확인", "en": "Confirm LIVE"},
    "input_needed": {"ko": "입력 필요", "en": "Input needed"},
    "sec_auto": {"ko": "자동 청산 (매일 지정 시각)", "en": "Auto-close (daily at the set time)"},
    "cutoff": {"ko": "청산 시각(ET)", "en": "Close time (ET)"},
    "auto_live": {"ko": "실제 청산으로 실행 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "auto_start": {"ko": "자동 청산 시작", "en": "Start auto-close"},
    "auto_stop": {"ko": "자동 청산 중지", "en": "Stop auto-close"},
    "auto_on_ind": {"ko": "  ● 자동 청산 ON  ", "en": "  ● Auto-close ON  "},
    "auto_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
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
        root.title("EQ Auto-Close")
        root.geometry("700x640")
        self.q = queue.Queue()
        self.lang = _load()["lang"]
        self.frm = None
        self._auto_on = False
        self._unlocked = False
        self._connected = False          # 연결 테스트 통과 전엔 실행 버튼 비활성
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
        ttk.Label(top, text="EQ 자동 청산 — Topstep (ProjectX)", font=("Helvetica", 16, "bold")).pack(side="left")
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
        # 동의 — 연결 테스트 '바로 밑'(실행 동작 전 필요)
        self.consent = tk.IntVar()
        ttk.Checkbutton(frm, variable=self.consent, text=self.t("consent")).pack(anchor="w", pady=(6, 0))
        ttk.Label(frm, text=self.t("conn_first"), foreground="#888").pack(anchor="w")

        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_flat"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        cf = ttk.Frame(frm); cf.pack(fill="x", pady=3)
        self.b_flat_dry = ttk.Button(cf, text=self.t("dry_close"), command=lambda: self.flatten(False))
        self.b_flat_dry.pack(side="left")
        self.b_flat_live = ttk.Button(cf, text=self.t("live_close"), command=lambda: self.flatten(True))
        self.b_flat_live.pack(side="left", padx=8)

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
        self.out = scrolledtext.ScrolledText(frm, height=10, font=("Menlo", 11), wrap="word")
        self.out.pack(fill="both", expand=True, pady=(6, 0))

        # 연결 테스트 통과 전엔 비활성화할 '실행' 버튼들. b_hc(연결 테스트)는 항상 활성.
        self._action_btns = [self.b_acc, self.b_flat_dry, self.b_flat_live, self.b_auto]
        self._set_actions_enabled(self._connected)
        self.log(self.t("ready"))
        self._async_load_key(d.get("user", ""))

    def _set_actions_enabled(self, on):
        """연결 테스트 통과 시에만 청산·자동 청산 버튼을 활성화한다."""
        st = "normal" if on else "disabled"
        for b in getattr(self, "_action_btns", []):
            try:
                b.config(state=st)
            except Exception:
                pass

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
        # 작업 중엔 연결 테스트도 잠그고, 끝나면 연결 여부에 맞춰 실행 버튼 복원.
        self.b_hc.config(state="disabled" if on else "normal")
        self._set_actions_enabled(False if on else self._connected)

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
            self._connected = True                # 통과 → 나머지 기능 활성화(_busy 복원이 반영)
            self.log("🔓 " + self.t("conn_ok"))
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


def _bind_clipboard(root):
    """Cut/Copy/Paste/Select-All을 위젯 클래스의 가상이벤트에 한 번씩 바인딩한다. 클래스에 걸면
    Tk 기본 핸들러를 대체하므로 OS 키(Cmd/Ctrl+V → <<Paste>>)가 정확히 한 번만 붙여넣는다.
    (raw 키 bind_all 방식은 기본 붙여넣기 위에 덧붙여 '두 번' 붙는 문제가 있었다.)"""
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

    # 위젯 '클래스'의 가상이벤트(<<Paste>> 등)에 바인딩 → Tk 기본 핸들러를 대체. OS가 Cmd/Ctrl+V를
    # <<Paste>>로 매핑하므로 _paste가 딱 한 번만 실행된다(이전 bind_all 방식은 두 번 붙던 버그).
    for cls in ("Entry", "TEntry", "Text"):
        for ev, fn in (("<<Paste>>", _paste), ("<<Copy>>", _copy),
                       ("<<Cut>>", _cut), ("<<SelectAll>>", _all)):
            try:
                root.bind_class(cls, ev, fn)
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
