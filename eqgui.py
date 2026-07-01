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
# '토큰 받기' = 무료(public) 멤버십 토큰 자동 발급 경로. 채널 입장/타자 불필요.
#   Telegram: 봇 딥링크(start=token) → 봇이 토큰을 바로 DM.
#   Discord : 웹에서 디스코드로 로그인 → 'EQ Autopilot' 페이지의 '내 멤버십 토큰'에서 복사.
URL_FREE_DC = "https://discord.com/oauth2/authorize?client_id=1516790855208538152&response_type=code&redirect_uri=https%3A%2F%2Fapp.edgequant.app%2F&scope=identify+guilds.join&state=aptoken"               # 웹 로그인(디스코드) → 토큰 표시
URL_FREE_TG = "https://t.me/EdgeQuantSignalBot?start=token"  # 봇이 토큰 DM
# EdgeQuant membership gating — the app holds a per-member TOKEN and polls two PER-MEMBER static
# files (Streamlit static serving): the heartbeat (permissions snapshot) and the signal feed.
#   hb-<token>.json   → {ok, tier, autopilot:{enabled, force_dry_run, caps, brokers}, exp}
#   sig-<token>.json  → the signal (direction/stop/contracts), gated by tier on the server side
# No shared secret: each member's files are written for their own token; revoke = server stops/locks.
APP_BASE = "https://app.edgequant.app/app/static"
SIG_POLL_SECS = 3                                   # feed poll cadence while the loop runs
HB_REFRESH_MS = 5 * 60 * 1000                       # heartbeat re-check every 5 min
STOP_RETRIES = 2                                    # protective stop: retries on a transient miss
STOP_RETRY_WAIT = 1.5                               # seconds between stop retries
MAX_SIGNAL_AGE_SEC = 60                             # 자동진입: 발행 1분 이내 신호만 진입(오래된 건 대기)


def _hb_url(token):
    import autopilot_crypto
    return f"{APP_BASE}/hb-{autopilot_crypto.path_id(token)}.json"


def _feed_url(token):
    import autopilot_crypto
    return f"{APP_BASE}/sig-{autopilot_crypto.path_id(token)}.json"


# 브로커별 연결 필드 스펙. f1/f2(secret)/f3 라벨(None=숨김), acct=계좌목록, futures=진입/신호 지원.
_BROKERS = ["projectx", "ibkr", "bybit", "bitget", "ninjatrader"]
_BROKER_SPEC = {
    "projectx":    {"label": "Topstep (ProjectX)", "f1": "TopstepX user email", "f2": "ProjectX API Key",
                    "f3": None, "acct": True, "futures": True},
    "ibkr":        {"label": "IBKR (TWS/Gateway)", "f1": "Host (예: 127.0.0.1)", "f2": None,
                    "f3": "Port (7497/7496)", "acct": True, "futures": True, "preview": True},
    "bybit":       {"label": "Bybit (USDT perp)", "f1": "API Key", "f2": "API Secret",
                    "f3": "Testnet (1=on)", "acct": False, "futures": False, "preview": True},
    "bitget":      {"label": "Bitget (USDT-F)", "f1": "API Key", "f2": "API Secret",
                    "f3": "Passphrase", "acct": False, "futures": False, "preview": True},
    "ninjatrader": {"label": "NinjaTrader (ATI)", "f1": "NT 계좌 (비우면 전체)", "f2": None,
                    "f3": None, "acct": False, "futures": False, "preview": True},
}


def _broker_label(b):
    return _BROKER_SPEC.get(b, {}).get("label", b)


def _build_broker(broker, f1, f2, f3, accounts):
    """선택된 브로커의 어댑터를 만든다(위젯 비의존 — 스레드에서도 안전).
    f1/f2/f3 = 연결 필드(브로커별 의미). accounts = 계좌/스코프 리스트."""
    acc = [a for a in (accounts or []) if a]
    if broker == "projectx":
        return ProjectXBroker(ProjectXCfg(base_url="https://api.topstepx.com",
                                          user_name=f1, api_key=f2, accounts=acc))
    if broker == "ibkr":
        from eqexec.broker.ibkr import IBKRBroker
        from eqexec.config import IBKRCfg
        return IBKRBroker(IBKRCfg(host=(f1 or "127.0.0.1"), port=int(f3 or 7497), accounts=acc))
    if broker == "bybit":
        from eqexec.broker.bybit import BybitBroker
        from eqexec.config import BybitCfg
        return BybitBroker(BybitCfg(api_key=f1, api_secret=f2, testnet=(str(f3).strip() == "1")))
    if broker == "bitget":
        from eqexec.broker.bitget import BitgetBroker
        from eqexec.config import BitgetCfg
        return BitgetBroker(BitgetCfg(api_key=f1, api_secret=f2, passphrase=f3))
    if broker == "ninjatrader":
        from eqexec.broker.ninjatrader import NinjaTraderBroker
        from eqexec.config import NinjaTraderCfg
        return NinjaTraderBroker(NinjaTraderCfg(accounts=([f1] if f1 else [])))
    raise ValueError(f"unknown broker {broker!r}")


def _resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

T = {
    "subtitle": {"ko": "본인 기기에서 본인 키로 실행. EdgeQuant는 키를 받지도, 대신 거래하지도 않습니다.",
                 "en": "Runs on your machine with your key. EdgeQuant never receives your key or trades for you."},
    "broker": {"ko": "브로커", "en": "Broker"},
    "broker_preview": {"ko": "(미검증 — 테스트넷/Sim 먼저)", "en": "(unverified — testnet/Sim first)"},
    "acct_topstep_only": {"ko": "계좌 목록은 Topstep 전용입니다.", "en": "Account list is Topstep-only."},
    "token": {"ko": "멤버십 토큰", "en": "Membership token"},
    "token_get": {"ko": "토큰 받기", "en": "Get token"},
    "gate_none": {"ko": "멤버십 토큰을 입력하세요 (무료 사용 → 채널에서 발급).",
                  "en": "Enter a membership token (Use free → issued in the channel)."},
    "gate_locked": {"ko": "잠김 — 토큰이 유효하지 않거나 만료/철회됨 (fail-closed).",
                    "en": "Locked — token invalid or expired/revoked (fail-closed)."},
    "gate_master_off": {"ko": "잠김 — 관리자가 Autopilot을 꺼둠 (마스터 OFF).",
                        "en": "Locked — Autopilot disabled by admin (master OFF)."},
    "gate_ok": {"ko": "멤버십: {tier} · 자동청산 {u} · 자동진입 {a}{dry}",
                "en": "Membership: {tier} · auto-close {u} · auto-entry {a}{dry}"},
    "gate_dry": {"ko": " · 강제 모의(LIVE 잠금)", "en": " · forced dry-run (LIVE locked)"},
    "warn_mix": {"ko": "⚠ 청산 시 사용 계좌의 모든 포지션이 일괄 청산됩니다. "
                       "그 계좌에 다른 거래를 섞지 말고 전용 계좌를 사용하세요.",
                 "en": "⚠ Closing flattens EVERY position on the chosen account. "
                       "Don't mix other trades on it — use a dedicated account."},
    "lang": {"ko": "언어", "en": "Language"},
    "btn_home": {"ko": "홈페이지", "en": "Website"},
    "btn_join": {"ko": "멤버십 가입", "en": "Join membership"},
    "btn_free": {"ko": "무료 사용", "en": "Use free"},
    "free_msg": {"ko": "이 버튼을 누르면 ① 무료 멤버십 토큰 발급 + ② 무료 시그널 방 가입이 됩니다.\n"
                       "받은 토큰을 위 '멤버십 토큰' 칸에 붙여넣으면 자동 청산이 열립니다.\n"
                       "• Telegram: 봇이 토큰 + 방 초대 링크를 DM 합니다.\n"
                       "• Discord: 로그인하면 토큰 발급 + 무료 시그널 서버에 자동 가입됩니다.",
                 "en": "Clicking gets you ① a free membership token + ② joins the free signal room.\n"
                       "Paste the token into the 'Membership token' box above to unlock auto-close.\n"
                       "• Telegram: the bot DMs you the token + a room invite.\n"
                       "• Discord: log in → token issued + auto-joined to the free signal server."},
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
    "sec_flat": {"ko": "즉시 청산 (지금 사용 계좌의 모든 포지션 닫기)",
                 "en": "Immediate close (flatten all positions on the chosen account now)"},
    "dry_close": {"ko": "모의 청산 (Dry-run)", "en": "Dry-run close"},
    "live_close": {"ko": "⚠ 실제 청산 (LIVE)", "en": "⚠ LIVE close"},
    "consent": {"ko": "동의: 본인 키·본인 기기·본인 책임. EdgeQuant는 거래하지 않음 (실행 동작에 필요)",
                "en": "I agree: my key, my device, my responsibility. EdgeQuant does not trade. (required to act)"},
    "ready": {"ko": "준비됨. 키 입력 → '연결 테스트' → 통과하면 나머지 기능이 켜집니다.",
              "en": "Ready. Enter key → 'Test connection' → the rest unlocks once it passes."},
    "conn_first": {"ko": "※ 먼저 '연결 테스트'를 통과해야 청산·자동 진입 기능이 활성화됩니다.",
                   "en": "※ Pass 'Test connection' first to unlock close / auto-entry."},
    "conn_ok": {"ko": "기능이 활성화되었습니다.", "en": "Features unlocked."},
    "need_creds": {"ko": "이메일과 API Key를 모두 입력하세요.", "en": "Enter both email and API Key."},
    "need_consent": {"ko": "실행 동작은 먼저 동의 체크박스를 켜야 합니다.", "en": "Tick the consent box before acting."},
    "live_confirm": {"ko": "실거래 확인", "en": "Confirm LIVE"},
    "input_needed": {"ko": "입력 필요", "en": "Input needed"},
    "pick_acct": {"ko": "진입은 '사용 계좌'에서 단일 계좌를 지정해야 합니다 (전체 불가).",
                  "en": "Entry requires a single account in 'Account' (not all)."},
    "sec_auto": {"ko": "자동 청산 (매일 지정 시각)", "en": "Auto-close (daily at the set time)"},
    "cutoff": {"ko": "청산 시각(ET)", "en": "Close time (ET)"},
    "auto_live": {"ko": "실제 청산으로 실행 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "auto_start": {"ko": "자동 청산 시작", "en": "Start auto-close"},
    "auto_stop": {"ko": "자동 청산 중지", "en": "Stop auto-close"},
    "auto_on_ind": {"ko": "  ● 자동 청산 ON  ", "en": "  ● Auto-close ON  "},
    "auto_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sec_sig": {"ko": "자동 진입 (실시간 신호 · MNQ)", "en": "Auto-entry (live signal · MNQ)"},
    "sig_live": {"ko": "실제 진입 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "sig_start": {"ko": "신호 대기 시작", "en": "Start signal watch"},
    "sig_stop": {"ko": "신호 대기 중지", "en": "Stop signal watch"},
    "sig_on_ind": {"ko": "  ● 신호 대기 ON  ", "en": "  ● Watching ON  "},
    "sig_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sig_note": {"ko": "※ 신호의 방향·손절가·수량(MNQ)으로 자동 진입합니다. '사용 계좌'만 고르면 됩니다. "
                       "이미 포지션이 있으면 중복 진입하지 않습니다(두 군데서 켜도 2배 진입 방지).",
                 "en": "※ Enters automatically using the signal's direction, stop, and size (MNQ); just pick the "
                       "account. It won't enter if a position is already open (no doubling even if run in two "
                       "places)."},
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
                "lang": d.get("lang", "ko"), "token": d.get("token", ""),
                "broker": d.get("broker", "projectx"), "f1": d.get("f1", user),
                "f3": d.get("f3", "")}
    except Exception:
        return {"user": "", "key": "", "acct": "", "lang": "ko", "token": "",
                "broker": "projectx", "f1": "", "f3": ""}


def _save(user, key, acct, lang, token=None, broker=None, f1=None, f3=None):
    _kc_save(user, key)                                  # secret(f2) → Keychain only
    cur = _load()
    if token is None:
        token = cur.get("token", "")
    if broker is None:
        broker = cur.get("broker", "projectx")
    if f1 is None:
        f1 = cur.get("f1", user)
    if f3 is None:
        f3 = cur.get("f3", "")
    try:
        import yaml
        with open(CFG_PATH, "w") as f:
            yaml.safe_dump({"live": False, "broker": broker, "lang": lang, "token": token,
                            "f1": f1, "f3": f3,
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
        self._connected = False          # 연결 테스트 통과 전엔 실행 버튼 비활성
        self._broker_name = _load().get("broker", "projectx")
        self._token = _load().get("token", "")
        # 멤버십 게이트(하트비트). 기본 = fail-closed(권한 전부 막힘).
        self._gate = {"ok": False, "tier": "—", "enabled": False, "force_dry_run": True,
                      "caps": {"use": False, "manualentry": False, "autoentry": False},
                      "brokers": {}, "reason": "no token"}
        self._build()
        root.after(120, self._drain)
        root.after(800, lambda: self._heartbeat(periodic=True))   # 시작 직후 + 주기 권한 갱신

    def _async_load_key(self, user):
        """Read the key from Keychain off the main thread, then fill the field — never blocks the GUI."""
        def w():
            k = _kc_load(user)
            if k:
                self.root.after(0, lambda: self._set_key(k))
        threading.Thread(target=w, daemon=True).start()

    def _set_key(self, text):
        """Set the (readonly) secret field's value programmatically (브로커에 비밀 필드 없으면 무시)."""
        if not hasattr(self, "key"):
            return
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
        # 브로커별로 만들어지는 위젯 속성 정리(파괴된 위젯의 stale 참조 방지).
        for _a in ("key", "f3", "scope", "b_lock"):
            if hasattr(self, _a):
                delattr(self, _a)
        frm = ttk.Frame(self.root, padding=14); frm.pack(fill="both", expand=True); self.frm = frm

        top = ttk.Frame(frm); top.pack(fill="x")
        try:
            self._logo = tk.PhotoImage(file=_resource("eqlogo.png"))
            ttk.Label(top, image=self._logo).pack(side="left", padx=(0, 8))
        except Exception:
            self._logo = None
        ttk.Label(top, text=f"EQ Autopilot — {_broker_label(self._broker_name)}",
                  font=("Helvetica", 16, "bold")).pack(side="left")
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

        # 멤버십 토큰(게이팅) + 권한 상태
        rt = ttk.Frame(frm); rt.pack(fill="x", pady=3)
        ttk.Label(rt, text=self.t("token"), width=18).pack(side="left")
        self.token_e = ttk.Entry(rt); self.token_e.pack(side="left", fill="x", expand=True)
        self.token_e.insert(0, d.get("token", "")); self.token_e.bind("<FocusOut>", self._save_token)
        ttk.Button(rt, text=self.t("paste"), width=8, command=self._paste_token).pack(side="left", padx=(4, 0))
        ttk.Button(rt, text=self.t("token_get"), width=9, command=self._open_free).pack(side="left", padx=(4, 0))
        self.gate_lbl = tk.Label(frm, text="", foreground="#888", anchor="w", justify="left", wraplength=660)
        self.gate_lbl.pack(anchor="w", pady=(0, 4))

        spec = _BROKER_SPEC.get(self._broker_name, _BROKER_SPEC["projectx"])
        # 브로커 선택
        rb = ttk.Frame(frm); rb.pack(fill="x", pady=3)
        ttk.Label(rb, text=self.t("broker"), width=18).pack(side="left")
        self.brokerbox = ttk.Combobox(rb, values=[_broker_label(b) for b in _BROKERS],
                                      state="readonly", width=22)
        self.brokerbox.set(_broker_label(self._broker_name)); self.brokerbox.pack(side="left")
        self.brokerbox.bind("<<ComboboxSelected>>", self._on_broker)
        if spec.get("preview"):
            ttk.Label(rb, text=self.t("broker_preview"), foreground="#b06f00").pack(side="left", padx=(8, 0))
        # f1 (브로커별 1번 필드)
        r1 = ttk.Frame(frm); r1.pack(fill="x", pady=3)
        ttk.Label(r1, text=spec["f1"], width=18).pack(side="left")
        self.user = ttk.Entry(r1); self.user.pack(side="left", fill="x", expand=True)
        self.user.insert(0, d.get("f1") or d.get("user") or "")
        # f2 (비밀 — PIN 잠금) — 있는 브로커만
        if spec.get("f2"):
            r2 = ttk.Frame(frm); r2.pack(fill="x", pady=3)
            ttk.Label(r2, text=spec["f2"], width=18).pack(side="left")
            self.key = ttk.Entry(r2, show="•"); self.key.pack(side="left", fill="x", expand=True)
            self.key.insert(0, d["key"]); self.key.config(state="readonly")
            self.b_lock = ttk.Button(r2, text=self.t("unlock"), width=11, command=self._unlock)
            self.b_lock.pack(side="left", padx=(4, 0))
            ttk.Button(r2, text=self.t("paste"), width=8, command=self._paste_key).pack(side="left", padx=(4, 0))
            self.show = tk.IntVar()
            ttk.Checkbutton(r2, text=self.t("show"), variable=self.show, command=self._toggle).pack(side="left", padx=5)
        # f3 (추가 필드) — 있는 브로커만
        if spec.get("f3"):
            r3e = ttk.Frame(frm); r3e.pack(fill="x", pady=3)
            ttk.Label(r3e, text=spec["f3"], width=18).pack(side="left")
            _mask = "•" if ("Secret" in spec["f3"] or "Passphrase" in spec["f3"]) else ""
            self.f3 = ttk.Entry(r3e, show=_mask); self.f3.pack(side="left", fill="x", expand=True)
            self.f3.insert(0, d.get("f3", ""))
        # 계좌 스코프 — 계좌 개념 있는 브로커만(Topstep/IBKR)
        if spec.get("acct"):
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
        self.cb_auto_live = ttk.Checkbutton(af, text=self.t("auto_live"), variable=self.auto_live)
        self.cb_auto_live.pack(side="left", padx=(0, 10))
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
        self.cb_sig_live = ttk.Checkbutton(sg, text=self.t("sig_live"), variable=self.sig_live)
        self.cb_sig_live.pack(side="left", padx=(0, 12))
        self.b_sig = ttk.Button(sg, text=self.t("sig_stop") if self._sig_on else self.t("sig_start"),
                                command=self.toggle_sig); self.b_sig.pack(side="left")
        self.sig_ind = tk.Label(sg, font=("Helvetica", 11, "bold"))
        self.sig_ind.pack(side="left", padx=(10, 0))
        self._set_sig_ind(self._sig_on)
        ttk.Label(frm, text=self.t("sig_note"), foreground="#888", wraplength=660,
                  justify="left").pack(anchor="w")

        ttk.Separator(frm).pack(fill="x", pady=8)
        self.out = scrolledtext.ScrolledText(frm, height=10, font=("Menlo", 11), wrap="word")
        self.out.pack(fill="both", expand=True, pady=(6, 0))

        # 연결 테스트 통과 전엔 비활성화할 '실행' 버튼들. b_hc(연결 테스트)는 항상 활성.
        self._action_btns = [self.b_acc, self.b_flat_dry, self.b_flat_live, self.b_auto, self.b_sig]
        self._apply_gating()
        self.log(self.t("ready"))
        self._async_load_key(d.get("user", ""))

    def _set_actions_enabled(self, on):
        """_busy()용. 작업 중(on=False)엔 전부 잠그고, 끝나면 게이팅 상태로 복원."""
        if not on:
            for b in getattr(self, "_action_btns", []):
                try:
                    b.config(state="disabled")
                except Exception:
                    pass
        else:
            self._apply_gating()

    def _apply_gating(self):
        """연결(_connected) + 멤버십 하트비트(_gate)로 모든 실행 버튼·LIVE를 결정한다.
        reads(계좌목록·계약조회)=연결만 필요 / 청산·자동청산=use / 수동진입=manualentry /
        자동진입=autoentry / force_dry_run=LIVE 잠금. 토큰 무효·만료·마스터OFF=fail-closed."""
        g, conn = self._gate, self._connected
        master = conn and g.get("ok") and g.get("enabled")
        caps = g.get("caps", {})
        use = bool(master and caps.get("use"))
        auto = bool(master and caps.get("autoentry"))
        live_ok = not g.get("force_dry_run", True)

        fut = bool(_BROKER_SPEC.get(self._broker_name, {}).get("futures"))  # 자동 진입=선물(Topstep)만
        topstep = self._broker_name == "projectx"

        def en(b, ok):
            try:
                b.config(state="normal" if ok else "disabled")
            except Exception:
                pass
        en(self.b_acc, conn and topstep)
        en(self.b_flat_dry, use); en(self.b_flat_live, use and live_ok); en(self.b_auto, use)
        en(self.b_sig, auto and fut)
        for cb, var in ((self.cb_auto_live, self.auto_live), (self.cb_sig_live, self.sig_live)):
            try:
                if not live_ok:
                    var.set(0); cb.config(state="disabled")
                else:
                    cb.config(state="normal")
            except Exception:
                pass
        # fail-closed: 돌던 루프가 권한을 잃으면(토큰 변경·강등·만료·마스터 OFF) 자동 중지한다.
        # 버튼만 끄면 이미 도는 스레드가 계속 진입/청산하는 구멍이 생긴다.
        if getattr(self, "_sig_on", False) and not (auto and fut):
            self._sig_on = False
            self.b_sig.config(text=self.t("sig_start")); self._set_sig_ind(False)
            self.log("⏹ 자동 진입 권한 상실 → 신호 대기 자동 중지 (fail-closed).")
        if getattr(self, "_auto_on", False) and not use:
            self._auto_on = False
            self.b_auto.config(text=self.t("auto_start")); self._set_auto_ind(False)
            self.log("⏹ 자동 청산 권한 상실 → 자동 청산 자동 중지 (fail-closed).")
        self._update_gate_label()

    def _update_gate_label(self):
        g = self._gate
        if not (self._token or "").strip():
            txt, col = self.t("gate_none"), "#888"
        elif not g.get("ok"):
            r = g.get("reason")
            txt, col = self.t("gate_locked") + (f" ({r})" if r else ""), "#b00020"
        elif not g.get("enabled"):
            txt, col = self.t("gate_master_off"), "#b00020"
        else:
            caps = g.get("caps", {})
            txt = self.t("gate_ok").format(
                tier=g.get("tier", "—"), u="✓" if caps.get("use") else "✗",
                a="✓" if caps.get("autoentry") else "✗",
                dry=self.t("gate_dry") if g.get("force_dry_run") else "")
            col = "#1a7f37"
        try:
            self.gate_lbl.config(text=txt, foreground=col)
        except Exception:
            pass

    def _save_token(self, *_):
        self._token = self.token_e.get().strip()
        self._persist()
        self._heartbeat()

    def _paste_token(self):
        try:
            self.token_e.delete(0, "end")
            self.token_e.insert(0, self.root.clipboard_get().strip())
            self._save_token()
        except Exception:
            pass

    def _heartbeat(self, periodic=False):
        """멤버십 토큰으로 hb-<token>.json을 읽어 권한(_gate) 갱신. 실패/만료/철회 = fail-closed."""
        tok = (self.token_e.get().strip() if hasattr(self, "token_e") else self._token)
        self._token = tok

        def w():
            gate = {"ok": False, "tier": "—", "enabled": False, "force_dry_run": True,
                    "caps": {"use": False, "manualentry": False, "autoentry": False},
                    "brokers": {}, "reason": "no token"}
            if tok:
                try:
                    import requests
                    import time as _t
                    import autopilot_crypto
                    r = requests.get(_hb_url(tok), params={"t": int(_t.time())}, timeout=8)
                    try:
                        hb = autopilot_crypto.decrypt(tok, r.text) if r.ok else {}
                    except Exception:
                        hb = {"ok": False, "reason": "decrypt failed"}
                    if not hb.get("ok"):
                        gate["reason"] = hb.get("reason") or "locked"
                    elif hb.get("exp") and _t.time() > hb["exp"]:
                        gate["reason"] = "expired"
                    else:
                        ap = hb.get("autopilot", {})
                        gate = {"ok": True, "tier": hb.get("tier", "—"),
                                "enabled": bool(ap.get("enabled")),
                                "force_dry_run": bool(ap.get("force_dry_run", True)),
                                "caps": ap.get("caps", {}) or {}, "brokers": ap.get("brokers", {}) or {},
                                "reason": ""}
                except Exception as e:
                    gate["reason"] = f"heartbeat error: {e}"
            self._gate = gate
            self.root.after(0, self._apply_gating)
        threading.Thread(target=w, daemon=True).start()
        if periodic:
            try:
                self.root.after(HB_REFRESH_MS, lambda: self._heartbeat(periodic=True))
            except Exception:
                pass

    def _open_free(self):
        """Let the user pick Discord or Telegram for the free public signal channel."""
        win = tk.Toplevel(self.root)
        win.withdraw()                       # 위치 잡기 전엔 숨김(왼쪽→점프 방지)
        win.title(self.t("btn_free"))
        win.transient(self.root); win.resizable(False, False)
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
        win.deiconify()                      # 최종 위치에서 바로 보임(점프 없음)
        win.grab_set()

    def _secret(self):
        return self.key.get().strip() if hasattr(self, "key") else ""

    def _f3(self):
        return self.f3.get().strip() if hasattr(self, "f3") else ""

    def _persist(self):
        _save(self.user.get().strip(), self._secret(), self._scope(), self.lang,
              token=self._token, broker=self._broker_name, f1=self.user.get().strip(), f3=self._f3())

    def _on_broker(self, *_):
        sel = self.brokerbox.get()
        for b in _BROKERS:
            if _broker_label(b) == sel:
                self._broker_name = b
                break
        self._connected = False
        self._unlocked = False
        self._persist()
        self._build()

    def _set_lang(self, *_):
        self.lang = "en" if self.langbox.get() == "English" else "ko"
        self._persist()
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
        if not hasattr(self, "scope"):
            return ""
        s = self.scope.get().strip()
        return "" if s in ("", self.t("all"), T["all"]["ko"], T["all"]["en"]) else s

    def _broker(self):
        sc = self._scope()
        return _build_broker(self._broker_name, self.user.get().strip(), self._secret(),
                             self._f3(), [sc] if sc else [])

    def _busy(self, on):
        # 작업 중엔 연결 테스트도 잠그고, 끝나면 연결 여부에 맞춰 실행 버튼 복원.
        self.b_hc.config(state="disabled" if on else "normal")
        self._set_actions_enabled(False if on else self._connected)

    def _creds_ok(self):
        spec = _BROKER_SPEC.get(self._broker_name, {})
        if not self.user.get().strip() or (spec.get("f2") and not self._secret()):
            messagebox.showwarning(self.t("input_needed"), self.t("need_creds")); return False
        self._persist(); return True

    def _consent_ok(self):
        if not self.consent.get():
            messagebox.showwarning(self.t("need_consent"), self.t("need_consent")); return False
        return True

    def _fill_scope(self, names):
        if not hasattr(self, "scope"):
            return
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
            b = self._broker(); b.healthcheck()
            if self._broker_name == "projectx":      # Topstep만 계좌목록 채움
                try:
                    names = [str(a.get("name")) for a in b._accounts()]
                    self.root.after(0, lambda: self._fill_scope(names))
                except Exception:
                    pass
            pos = b.list_open_positions(); sc = self._scope()
            self.log(f"✅ connected ({sc or 'all'}) — open positions: {len(pos)}")
            for p in pos: self.log(f"   • {p.account_name} / {p.symbol}  net={p.net_qty}")
            if not pos: self.log("   (flat)")
            self._connected = True                # 통과 → 나머지 기능 활성화(_busy 복원이 반영)
            self.log("🔓 " + self.t("conn_ok"))
        self._run(w)

    def accounts(self):
        if self._broker_name != "projectx":
            messagebox.showinfo(self.t("broker"), self.t("acct_topstep_only")); return
        if not self._creds_ok(): return
        self.log("\n── accounts ──")
        def w():
            b = self._broker()
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
        broker, f1, f2, f3, sc = (self._broker_name, self.user.get().strip(), self._secret(),
                                  self._f3(), self._scope())
        live = bool(self.auto_live.get())
        self._auto_on = True
        self.b_auto.config(text=self.t("auto_stop"))
        self._set_auto_ind(True)
        self.log(f"\n▶ autopilot ON — daily {cutoff} ET · {_broker_label(broker)} [{sc or 'all'}] · "
                 f"{'LIVE' if live else 'dry-run'}. (keep the app open & the computer awake)")
        self.log(f"   {self.t('warn_mix')}")
        threading.Thread(target=self._auto_loop, args=(cutoff, broker, f1, f2, f3, sc, live),
                         daemon=True).start()

    def _handle_stop_failure(self, b, aid, contract, direction, size, stop, res):
        """Protective stop didn't land after a market entry. Broker rejection = permanent (e.g. price
        already through the stop) → flatten the position now so we're never unprotected. Transient
        (network) → retry the stop a few times; if it still won't land, KEEP the position and warn
        loudly (the 14:00 daily flatten is the backstop)."""
        import time as _t
        if res.get("stop_rejected"):
            self._flatten_unprotected(b, aid, contract, res.get("stop_error"))
            return
        # Transient miss — retry just the stop (position stays).
        self.log(f"   ⚠ 손절 거치 일시 실패({res.get('stop_error')}) — 재시도…")
        for i in range(STOP_RETRIES):
            _t.sleep(STOP_RETRY_WAIT)
            sr = b.place_protective_stop(aid, contract, direction, size, stop,
                                         custom_tag=f"EQ-AP-{int(_t.time() * 1000)}")   # 매 재시도 유니크
            if sr.get("stop"):
                self.log(f"   🛡 손절 거치 완료(재시도 {i + 1}회차).")
                return
            if sr.get("stop_rejected"):                 # transient hardened into a rejection
                self._flatten_unprotected(b, aid, contract, sr.get("stop_error"))
                return
            self.log(f"   … 재시도 {i + 1} 실패: {sr.get('stop_error')}")
        self.log("   🔴 손절 미거치(네트워크) — 포지션 유지 중. 14:00 일일청산이 백스톱이나, "
                 "지금 수동으로 손절/확인 권장!")

    def _flatten_unprotected(self, b, aid, contract, why):
        """Market-close a just-entered position whose protective stop was rejected."""
        self.log(f"   🛑 손절 거부됨({why}) → 무방비 포지션 즉시 청산")
        try:
            b.close_contract(aid, contract)
            self.log("   ↩ 포지션 청산 완료(손절 불가로 진입 취소).")
        except Exception as ce:
            self.log(f"   ❌ 긴급 청산 실패: {ce} — 즉시 수동 확인 필요!")

    def _auto_loop(self, cutoff, broker, f1, f2, f3, sc, live):
        import time as _t
        try:
            tz = ZoneInfo("America/New_York") if ZoneInfo else None
        except Exception as e:
            # Windows 등 시스템 tz DB 없고 tzdata 미동봉이면 여기서 죽어 스레드가 조용히 사라진다 →
            # 청산이 영영 안 됨. 로그로 드러내고 로컬 시각으로라도 동작(시각 확인 필요). (대표 2026-06-30)
            tz = None
            self.log(f"⚠ ET 타임존 로드 실패({e!r}) — tzdata 누락 의심. 로컬 시각 기준으로 동작하니 청산 시각 확인!")
        fired = None
        while self._auto_on:
            now = _dt.datetime.now(tz)
            today = now.strftime("%Y-%m-%d")
            if now.strftime("%H:%M") >= cutoff and fired != today:
                fired = today
                self.log(f"\n⏰ {cutoff} ET → auto-close ([{sc or 'all'}], {'LIVE' if live else 'dry-run'})")
                try:
                    b = _build_broker(broker, f1, f2, f3, [sc] if sc else [])
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
        if not _BROKER_SPEC.get(self._broker_name, {}).get("futures"):
            messagebox.showinfo(self.t("broker"), self.t("acct_topstep_only")); return
        if not self._creds_ok() or not self._consent_ok():
            return
        sc = self._scope()
        if not sc:
            messagebox.showwarning(self.t("scope"), self.t("pick_acct")); return
        if not self._token:
            messagebox.showwarning(self.t("token"), self.t("gate_none")); return
        # 자동 진입은 '진입(수동)' 섹션과 완전 무관 — 계약(MNQ)·수량·손절가 모두 신호에서 받는다.
        symbol = "MNQ"                           # auto-trading is MNQ only (not NQ)
        user, key = self.user.get().strip(), self.key.get().strip()
        live = bool(self.sig_live.get()) and not self._gate.get("force_dry_run")  # 강제 모의 존중
        url = _feed_url(self._token)             # 멤버별 신호 피드
        self._sig_on = True
        self.b_sig.config(text=self.t("sig_stop"))
        self._set_sig_ind(True)
        self.log(f"\n▶ signal watch ON — {symbol} · account [{sc}] · "
                 f"size from signal · {'LIVE' if live else 'dry-run'}. polling member feed")
        threading.Thread(target=self._sig_loop,
                         args=(url, user, key, sc, symbol, live),
                         daemon=True).start()

    def _resolve_contract(self, b, symbol):
        """현재(활성) 계약ID를 종목으로 자동 조회. 활성 우선, 없으면 첫 결과."""
        cs = b.search_contracts(symbol)
        if not cs:
            return None
        active = next((c.get("id") for c in cs if c.get("activeContract")), None)
        return active or cs[0].get("id")

    def _sig_loop(self, url, user, key, sc, symbol, live):
        import time as _t
        import requests
        import autopilot_crypto
        last_id = None
        while self._sig_on:
            try:
                r = requests.get(url, params={"t": int(_t.time())}, timeout=8)
                sig = autopilot_crypto.decrypt(self._token, r.text) if r.ok else {}
            except Exception as e:
                self.log(f"   signal feed error: {e}"); _t.sleep(SIG_POLL_SECS); continue
            sid = sig.get("id")
            # no-trade(거래 없음) 신호도 새로 오면 '받았다'만 표시(포지션은 안 잡음).
            if sid and sid != last_id and not (sig.get("tradeable") and sig.get("direction")):
                last_id = sid
                import datetime as _dtl0
                _recv0 = _dtl0.datetime.now()
                _pub0 = sig.get("published_at")
                try:
                    _sent0 = _dtl0.datetime.fromtimestamp(float(_pub0)) if _pub0 else None
                except (TypeError, ValueError):
                    _sent0 = None
                self.log(f"\n📭 신호 수신 [{sid}] — {sig.get('instrument') or ''} · NO-TRADE (거래 없음, 포지션 안 잡음)")
                self.log(f"   ⏱ 보낸 시각 {_sent0.strftime('%H:%M:%S') if _sent0 else '?'}  ·  "
                         f"받은 시각 {_recv0.strftime('%H:%M:%S')}")
            if sid and sid != last_id and sig.get("tradeable") and sig.get("direction"):
                last_id = sid    # 이 신호 id는 처리/스킵 완료로 표시(매 폴 재판단 방지)
                # 🛑 오래된 신호로 실수 진입 방지: 앱을 켜면 피드에 '어제 신호'가 남아 있는데, 그게
                # 새 신호로 판정돼 즉시 실진입하던 버그. published_at이 최근(MAX_SIGNAL_AGE_SEC 이내)이
                # 아니면 진입하지 않고 새 신호를 기다린다. 발행시각 불명이면 안전상 진입 안 함.
                _pub_ts = sig.get("published_at")
                try:
                    _age = _t.time() - float(_pub_ts) if _pub_ts is not None else None
                except (TypeError, ValueError):
                    _age = None
                if _age is None or _age > MAX_SIGNAL_AGE_SEC:
                    if _age is None:
                        _why = "발행시각 불명(안전상 스킵)"
                    elif _age < 300:
                        _why = f"발행 {_age:.0f}초 전(1분 초과, 오래됨)"
                    else:
                        _why = f"발행 {_age / 60:.0f}분 전(오래됨)"
                    self.log(f"\n⏸ 신호 [{sid}] 진입 안 함 — {_why}. 새 신호를 기다립니다.")
                    _t.sleep(SIG_POLL_SECS)
                    continue
                direction, stop = sig.get("direction"), sig.get("stop_price")
                try:
                    size = int(sig.get("contracts") or 0)    # 수량도 신호에서 받는다(MNQ)
                except (TypeError, ValueError):
                    size = 0
                sym = symbol or (sig.get("instrument") or "")
                # 캡처 순간 로그 — 보낸 시각(피드 published_at) · 받은 시각(now) · 지연 · 포지션 정보.
                import datetime as _dtl
                _recv = _dtl.datetime.now()
                _pub = sig.get("published_at")
                try:
                    _sent = _dtl.datetime.fromtimestamp(float(_pub)) if _pub else None
                    _lat = f"{_recv.timestamp() - float(_pub):.1f}s"
                except (TypeError, ValueError):
                    _sent, _lat = None, "?"
                self.log(f"\n📶 신호 캡처 [{sid}] — {sig.get('instrument') or sym} {direction} x{size}")
                self.log(f"   포지션: {direction} · 수량 {size} (MNQ) · 손절 {stop} · 진입참조 {sig.get('entry_ref')}")
                self.log(f"   ⏱ 보낸 시각 {_sent.strftime('%H:%M:%S') if _sent else '?'}  ·  "
                         f"받은 시각 {_recv.strftime('%H:%M:%S')}  ·  지연 {_lat}")
                self.log(f"   → {sym} 계약 조회 → {'LIVE' if live else 'dry-run'} 진입 [{sc}]")
                if size <= 0:
                    self.log("   ⏭ signal has no contract count (no entry_ref?) — skipping."); continue
                try:
                    b = ProjectXBroker(ProjectXCfg(base_url="https://api.topstepx.com", user_name=user,
                                                   api_key=key, accounts=[sc]))
                    contract = self._resolve_contract(b, sym)
                    if not contract:
                        self.log(f"   ❌ no active contract found for '{sym}'."); continue
                    self.log(f"   contract: {contract}")
                    match = [a for a in b._accounts()
                             if str(a.get("name")) == sc or str(a.get("id")) == sc]
                    if not match:
                        self.log(f"   ❌ account '{sc}' not found."); continue
                    aid = match[0]["id"]
                    # 중복 진입 방지: 그 계좌에 이미 포지션이 있으면 건너뛴다(다른 인스턴스/다른 기기가
                    # 먼저 진입했거나 미청산). 두 군데서 켜놔도 2배로 안 들어가게.
                    existing = b.list_open_positions()
                    if existing:
                        if live:
                            self.log(f"   ⏭ already in a position ({len(existing)}) — "
                                     f"skipping to avoid doubling.")
                            continue
                        self.log(f"   (note) already in a position ({len(existing)}) — LIVE would skip.")
                    # customTag은 ProjectX에서 '계좌당 유일'해야 함 → 고정값 쓰면 두 번째 진입부터
                    # errorCode=2("custom tag already in use"). 매 진입마다 ms 타임스탬프로 유니크하게.
                    res = b.place_entry(account_id=aid, contract_id=contract, side=direction,
                                        size=size, order_type=2, stop_loss_price=stop,
                                        custom_tag=f"EQ-AP-{int(_recv.timestamp() * 1000)}",
                                        dry_run=not live)
                    if not live:
                        self.log(f"   DRY-RUN entry: {res.get('would_place')}")
                        if res.get("would_place_stop"):
                            self.log(f"   DRY-RUN stop:  {res.get('would_place_stop')}")
                    else:
                        self.log(f"   ✅ 진입 완료: {res.get('entry', res)}")
                        if res.get("stop"):
                            self.log("   🛡 보호 손절 거치 완료.")
                        elif res.get("stop_error"):
                            self._handle_stop_failure(b, aid, contract, direction, size,
                                                      stop, res)
                except Exception as e:
                    self.log(f"   ❌ signal entry failed: {e}")
            _t.sleep(SIG_POLL_SECS)


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

    # 위젯 '클래스'의 가상이벤트(<<Paste>> 등)에 바인딩 → Tk 기본 핸들러를 **대체**한다.
    # OS가 Cmd/Ctrl+V를 <<Paste>>로 매핑하므로 우리 _paste가 딱 한 번만 실행된다.
    # (이전엔 raw 키를 bind_all('all' 태그)로 잡아, 클래스 기본 붙여넣기 '다음에' 또 삽입돼
    #  두 번 붙던 버그가 있었다.)
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
