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
PUSH_BASE = "https://app.edgequant.app/"           # 공개 트랙레코드 푸시(?profile_push=) + 페이지(?u=)
TR_LOOKBACK_DAYS = 90                              # 푸시당 체결 조회 범위(서버가 tid로 멱등 병합)
TR_CHUNK = 40                                      # 청크당 trade 수(URL 길이 안전)
SIG_POLL_SECS = 3                                   # feed poll cadence while the loop runs
HB_REFRESH_MS = 5 * 60 * 1000                       # heartbeat re-check every 5 min
# 자산별 세션 청산 시각(거래봉 마감, 서버 archive_resolver._WINDOW와 동일 상수) — 자동청산은
# 사용자가 시각을 고르는 게 아니라 시스템 세션 마감에 자동으로 맞춘다(3자산·BTC 2세션).
#   NQ 10-14 ET → 14:00 ET · GC 02-06 ET → 06:00 ET · BTC 22-02/02-06 UTC → 02:00·06:00 UTC
_ASSET_EXITS = {"NQ": [("America/New_York", 14)], "GC": [("America/New_York", 6)],
                "BTC": [("UTC", 2), ("UTC", 6)]}
AUTO_FIRE_WINDOW_MIN = 30                           # 마감 후 이 분 안에서만 발화(놓친 tick 대비)
STOP_RETRIES = 2                                    # protective stop: retries on a transient miss
STOP_RETRY_WAIT = 1.5                               # seconds between stop retries
MAX_SIGNAL_AGE_SEC = 60                             # 자동진입: 발행 1분 이내 신호만 진입(오래된 건 대기)


def _cross_basis_bitget() -> float:
    """빗겟 캘리브레이션(대표 2026-07-12): 신호 손절은 Bybit BTCUSDT.P 좌표계 —
    Bitget 체결용으로 순수 거래소 베이시스(빗겟가-바이빗가, 같은 순간 공개 티커)만 가산한다.
    (진입 후 드리프트는 양쪽 공통이라 미반영 — Bybit 회원의 원본 손절/공식 채점과 일관 유지.)
    실패 시 0.0(원본 손절 그대로) — 베이시스는 보통 리스크의 2~4%라 미조정도 치명적이지 않다."""
    import requests
    try:
        by = requests.get("https://api.bybit.com/v5/market/tickers",
                          params={"category": "linear", "symbol": "BTCUSDT"}, timeout=5).json()
        bg = requests.get("https://api.bitget.com/api/v2/mix/market/ticker",
                          params={"productType": "USDT-FUTURES", "symbol": "BTCUSDT"}, timeout=5).json()
        p_by = float(by["result"]["list"][0]["lastPrice"])
        p_bg = float(bg["data"][0]["lastPr"])
        return p_bg - p_by
    except Exception:
        return 0.0


def _hb_url(token):
    import autopilot_crypto
    return f"{APP_BASE}/hb-{autopilot_crypto.path_id(token)}.json"


def _feed_url(token):
    import autopilot_crypto
    return f"{APP_BASE}/sig-{autopilot_crypto.path_id(token)}.json"


# 브로커별 연결 필드 스펙. f1/f2(secret)/f3 라벨(None=숨김), acct=계좌목록, futures=진입/신호 지원.
_BROKERS = ["projectx", "ibkr", "bybit", "bitget", "ninjatrader"]

# 자산 탭 + 자산별 브로커 매트릭스(대표 2026-07-10):
#   Topstep(projectx)·IBKR = MNQ·MGC (선물) · Bybit·Bitget = BTC만(BTCUSDT.P, 크립토)
#   ⚠️ BTC를 CME MBTC 선물로 안 함 — MBTC는 주말 휴장인데 BTC 엣지가 주말(일요일)에 몰려 있어
#      MBTC로 돌리면 실행 성과가 크게 훼손됨. BTC는 크립토(주말 거래) 전용.
_ASSETS = ["NQ", "GC", "BTC"]
_ASSET_BROKERS = {"NQ": ["projectx", "ibkr"],
                  "GC": ["projectx", "ibkr"],
                  "BTC": ["bybit", "bitget"]}
_ASSET_LABEL = {"NQ": {"ko": "나스닥 (NQ)", "en": "Nasdaq (NQ)"},
                "GC": {"ko": "금 (GC)", "en": "Gold (GC)"},
                "BTC": {"ko": "비트코인 (BTC)", "en": "Bitcoin (BTC)"}}
_BROKER_SPEC = {
    "projectx":    {"label": "Topstep (ProjectX)", "f1": "TopstepX user email", "f2": "ProjectX API Key",
                    "f3": None, "acct": True, "futures": True},
    "ibkr":        {"label": "IBKR (TWS/Gateway)", "f1": "Host (예: 127.0.0.1)", "f2": None,
                    "f3": "Port (7497/7496)", "acct": True, "futures": True, "preview": True},
    "bybit":       {"label": "Bybit (USDT perp)", "f1": "API Key", "f2": "API Secret",
                    "f3": "Testnet (1=on)", "acct": False, "futures": False, "preview": True,
                    "f1_secret": True},
    "bitget":      {"label": "Bitget (USDT-F)", "f1": "API Key", "f2": "API Secret",
                    "f3": "Passphrase", "acct": False, "futures": False, "preview": True,
                    "f1_secret": True},
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
    "token_show": {"ko": "표시", "en": "Show"},
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
    "lang": {"ko": "Language", "en": "Language"},   # 언어 선택 라벨은 언어 무관 고정(대표 2026-07-12)
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
    "sec_tr": {"ko": "공개 트랙레코드 (Autopilot)", "en": "Public track record (Autopilot)"},
    "tr_public": {"ko": "공개 동의", "en": "Make public"},
    "tr_on_ind": {"ko": "  ● 자동 동기화 ON  ", "en": "  ● Auto-sync ON  "},
    "tr_off_ind": {"ko": "  ○ 자동 동기화 꺼짐  ", "en": "  ○ Auto-sync off  "},
    "tr_page": {"ko": "공개 페이지", "en": "My page"},
    "tr_push": {"ko": "동기화(푸시)", "en": "Sync (push)"},
    "tr_note": {"ko": "※ 앱이 브로커 체결 기록을 이 컴퓨터에서 R로 변환해 요약만 서버로 보냅니다 — "
                      "API 키·잔고·계좌금액은 절대 전송 안 됨. 핸들·표시 이름은 회원 계정"
                      "(텔레그램/디스코드)에서 자동 설정되며, 공개 페이지 주소는 동기화 후 아래에 표시됩니다 "
                      "(공개 동의 체크 시에만 노출, 언제든 해제 가능).",
                "en": "※ The app converts your broker fills to R locally and pushes only the summary — "
                      "API keys, balances and account size are never sent. Your handle & display name are "
                      "set automatically from your member account (Telegram/Discord); the public page URL "
                      "appears below after syncing (visible only while 'Make public' is on)."},
    "sec_auto": {"ko": "자동 청산 (세션 마감 자동)", "en": "Auto-close (at session close)"},
    "auto_sched": {"ko": "청산 시각: NQ 14:00 ET · GC 06:00 ET · BTC 02:00/06:00 UTC (자동)",
                   "en": "Close times: NQ 14:00 ET · GC 06:00 ET · BTC 02:00/06:00 UTC (auto)"},
    "auto_live": {"ko": "실제 청산으로 실행 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "auto_start": {"ko": "자동 청산 시작", "en": "Start auto-close"},
    "auto_stop": {"ko": "자동 청산 중지", "en": "Stop auto-close"},
    "auto_on_ind": {"ko": "  ● 자동 청산 ON  ", "en": "  ● Auto-close ON  "},
    "auto_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sec_sig": {"ko": "자동 진입 (실시간 신호)", "en": "Auto-entry (live signal)"},
    "sig_1r": {"ko": "1R ($)", "en": "1R ($)"},
    "sig_live": {"ko": "실제 진입 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "sig_start": {"ko": "신호 대기 시작", "en": "Start signal watch"},
    "sig_stop": {"ko": "신호 대기 중지", "en": "Stop signal watch"},
    "sig_on_ind": {"ko": "  ● 신호 대기 ON  ", "en": "  ● Watching ON  "},
    "sig_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sig_note": {"ko": "※ 신호의 방향·손절가로 자동 진입하고, 계약 수는 위 1R($ 리스크)로 앱이 자동 계산합니다 "
                       "(신호에 계약 수 없음). 자산은 신호의 종목으로 자동 판별(NQ→MNQ·GC→MGC·BTC→MBTC). "
                       "'사용 계좌'만 고르면 됩니다. 이미 포지션이 있으면 중복 진입하지 않습니다.",
                 "en": "※ Enters automatically using the signal's direction and stop; the contract count is computed "
                       "by the app from your 1R above (the signal carries no contract count). The instrument is "
                       "detected from the signal (NQ→MNQ · GC→MGC · BTC→MBTC). Just pick the account. It won't "
                       "enter if a position is already open."},
    "auto_note": {"ko": "※ 앱이 떠 있고 컴퓨터가 켜져(절전 해제) 있어야 작동. 설정된 각 자산의 세션 마감 시각에 그 자산 브로커를 청산합니다.",
                  "en": "※ App must stay open and the computer awake. Each configured asset's broker is flattened at its session close."},
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
    """단일 설정 + 자산별 설정(assets={NQ,GC,BTC}) 로드. 하위호환: 옛 flat 키(broker/f1/f3/one_r)는
    그 브로커를 쓰는 첫 자산 슬롯으로 1회 마이그레이션. 키(f2)는 Keychain에서 async 로드."""
    try:
        import yaml
        with open(CFG_PATH) as f:
            d = yaml.safe_load(f) or {}
    except Exception:
        d = {}
    px = d.get("projectx", {}); a = px.get("accounts") or []
    user = px.get("user_name", "")
    out = {"user": user, "key": px.get("api_key", ""), "acct": (a[0] if a else ""),
           "lang": d.get("lang", "ko"), "token": d.get("token", ""),
           "broker": d.get("broker", "projectx"), "f1": d.get("f1", user),
           "f3": d.get("f3", ""), "one_r": d.get("one_r", 600)}
    assets = d.get("assets") or {}
    acfg = {}
    for _a in _ASSETS:
        s = assets.get(_a) or {}
        brs = _ASSET_BROKERS[_a]
        _bk = s.get("broker") if s.get("broker") in brs else brs[0]
        # 브로커별 자격증명 분리(대표 2026-07-12: 바이빗 키가 빗겟에 공유되던 것 차단) —
        # creds[broker] = {f1,f3,acct}. 옛 스키마(자산 상위 f1/f3/acct)는 당시 브로커 창고로 이관.
        creds = {b: dict(v) for b, v in (s.get("creds") or {}).items() if b in brs}
        if s.get("f1") and _bk not in creds:
            creds[_bk] = {"f1": s.get("f1", ""), "f3": s.get("f3", ""), "acct": s.get("acct", "")}
        cur = creds.get(_bk) or {"f1": "", "f3": "", "acct": ""}
        acfg[_a] = {"broker": _bk, "one_r": s.get("one_r", 600), "creds": creds,
                    # 상위 f1/f3/acct = '현재 브로커' 미러(다운스트림 호환) — 저장·전환 때 갱신
                    "f1": cur.get("f1", ""), "f3": cur.get("f3", ""), "acct": cur.get("acct", "")}
    if not assets and out["f1"]:            # 마이그레이션: 옛 flat → 브로커 맞는 첫 자산
        for _a in _ASSETS:
            if out["broker"] in _ASSET_BROKERS[_a]:
                acfg[_a].update({"broker": out["broker"], "f1": out["f1"], "f3": out["f3"],
                                 "acct": out["acct"], "one_r": out["one_r"]})
                acfg[_a]["creds"][out["broker"]] = {"f1": out["f1"], "f3": out["f3"],
                                                    "acct": out["acct"]}
                break
    out["assets"] = acfg
    _p = d.get("profile") or {}
    # 핸들·이름은 서버 자동(2026-07-11) — 입력은 없지만, 서버가 배정한 핸들은 '공개 페이지'
    # 버튼용으로 로컬 캐시(푸시 응답 pp:ok:N:handle에서 회신받아 저장, 대표 2026-07-12).
    out["profile"] = {"public": bool(_p.get("public")), "handle": _p.get("handle", "")}
    return out


_ENTERED_PATH = os.path.join(APP_DIR, ".entered.json")


def _load_entered() -> dict:
    """자산별 마지막 LIVE 진입 시각 {asset: epoch}. 앱 재시작을 넘겨 영속 — 자동청산 잡이
    '방금 새 세션 진입'을 알아보고 새 포지션을 오살하지 않게 한다(연속 세션 순서 보장)."""
    try:
        import json as _json
        with open(_ENTERED_PATH, encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _mark_entered(asset: str) -> dict:
    d = _load_entered()
    import time as _t
    import json as _json
    d[asset] = _t.time()
    try:
        with open(_ENTERED_PATH, "w", encoding="utf-8") as f:
            _json.dump(d, f)
    except Exception:
        pass
    return d


_PUSHED_PATH = os.path.join(APP_DIR, ".lastpush")


def _last_pushed() -> float:
    try:
        with open(_PUSHED_PATH, encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return 0.0


def _mark_pushed() -> None:
    try:
        import time as _t
        with open(_PUSHED_PATH, "w", encoding="utf-8") as f:
            f.write(str(_t.time()))
    except Exception:
        pass


def _save_full(lang, token, acfg, profile=None):
    """자산별 설정(acfg={asset:{broker,f1,f3,acct,one_r}}) + lang/token + 공개프로필 설정을
    yaml에 저장. 비밀(f2)은 여기서 안 씀 — 각 자산 저장 시 _kc_save로 Keychain에 이미 넣는다."""
    try:
        import yaml
        payload = {"live": False, "lang": lang, "token": token,
                   "assets": {a: dict(c) for a, c in (acfg or {}).items()}}
        if profile is not None:
            payload["profile"] = dict(profile)
        with open(CFG_PATH, "w") as f:
            yaml.safe_dump(payload, f)
    except Exception:
        pass


def _save(user, key, acct, lang, token=None, broker=None, f1=None, f3=None, one_r=None):
    """(레거시 단일 저장 — 호환용) 비밀만 Keychain에, 나머지는 무시(자산별은 _save_full 사용)."""
    _kc_save(user or f1 or "", key)


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
        # 자산별 무장 상태(대표 2026-07-11): 자동청산/자동진입은 자산마다 독립 토글,
        # 탭 전환은 보기 전환일 뿐 무장 상태를 절대 안 바꾼다. _*_on은 '루프 살아있음' 플래그.
        self._auto_jobs = {}    # {asset: [job,...]}
        self._sig_assets = {}   # {asset: cfg}
        self._unlocked = False
        self._connected = False          # 연결 테스트 통과 전엔 실행 버튼 비활성 (현재 탭 기준)
        self._conn_by_asset = {}         # 자산별 연결테스트 통과 기억 — 탭 전환이 리셋 안 시킴
        _d0 = _load()
        self._acfg = _d0["assets"]       # 자산별 설정 {NQ,GC,BTC:{broker,f1,f3,acct,one_r}}
        self._asset = "NQ"               # 현재 편집 중인 자산 탭
        self._broker_name = self._acfg[self._asset]["broker"]
        self._profile = _d0["profile"]   # 공개 트랙레코드 {handle,name,public}
        self._entered_at = _load_entered()   # 자산별 마지막 LIVE 진입 시각(자동청산 오살 방지)
        self._token = _d0.get("token", "")
        # 멤버십 게이트(하트비트). 기본 = fail-closed(권한 전부 막힘).
        self._gate = {"ok": False, "tier": "—", "enabled": False, "force_dry_run": True,
                      "caps": {"use": False, "manualentry": False, "autoentry": False},
                      "brokers": {}, "reason": "no token"}
        self._build()
        root.after(120, self._drain)
        root.after(800, lambda: self._heartbeat(periodic=True))   # 시작 직후 + 주기 권한 갱신
        root.after(5 * 60 * 1000, self._autopush_tick)             # 일일 자동 트랙레코드 동기화

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

    def _set_auto_ind(self, on, assets=None):
        """Green lit pill while autopilot runs; gray when off. assets=무장된 자산 목록 병기
        (전 자산 커버가 아니라 '연결 테스트 통과 자산만'임을 어느 탭에서든 보이게 — 대표 2026-07-11)."""
        try:
            txt = self.t("auto_on_ind") if on else self.t("auto_off_ind")
            if on and assets:
                txt = txt.rstrip() + f" [{'·'.join(assets)}]  "
            self.auto_ind.config(text=txt, fg="white" if on else "#666",
                                 bg="#22a722" if on else "#dddddd")
        except Exception:
            pass

    def _set_sig_ind(self, on, assets=None):
        """Green lit pill while the signal-watch loop runs; gray when off. assets=무장 자산 병기."""
        try:
            txt = self.t("sig_on_ind") if on else self.t("sig_off_ind")
            if on and assets:
                txt = txt.rstrip() + f" [{'·'.join(assets)}]  "
            self.sig_ind.config(text=txt, fg="white" if on else "#666",
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
        self.token_e = ttk.Entry(rt, show="•")  # 토큰 마스킹(잠금) — 기본 •••, 아래 토글로 표시
        self.token_e.pack(side="left", fill="x", expand=True)
        self.token_e.insert(0, d.get("token", "")); self.token_e.bind("<FocusOut>", self._save_token)
        ttk.Button(rt, text=self.t("token_show"), width=6, command=self._toggle_token_show).pack(side="left", padx=(4, 0))
        ttk.Button(rt, text=self.t("paste"), width=8, command=self._paste_token).pack(side="left", padx=(4, 0))
        ttk.Button(rt, text=self.t("token_get"), width=9, command=self._open_free).pack(side="left", padx=(4, 0))
        self.gate_lbl = tk.Label(frm, text="", foreground="#888", anchor="w", justify="left", wraplength=660)
        self.gate_lbl.pack(anchor="w", pady=(0, 4))

        # ── 공개 트랙레코드 (Autopilot 전용) — 로컬 계산 요약만 서버로 푸시(키·잔고 무접촉) ──
        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_tr"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        # 핸들·이름 입력 제거(대표 2026-07-11) — 서버가 회원 계정(텔레그램/디스코드)에서 자동 설정.
        tr = ttk.Frame(frm); tr.pack(fill="x", pady=3)
        _prof = self._profile
        self.tr_public = tk.IntVar(value=1 if _prof.get("public") else 0)
        # 토글 즉시 _profile 반영+영속 — 저장 없이 탭 바꾸면 옛 값으로 되살아나던 버그(대표 2026-07-12)
        ttk.Checkbutton(tr, text=self.t("tr_public"), variable=self.tr_public,
                        command=self._on_tr_public).pack(side="left", padx=(0, 10))
        self.b_tr = ttk.Button(tr, text=self.t("tr_push"), command=self.push_profile)
        self.b_tr.pack(side="left")
        self.tr_ind = tk.Label(tr, font=("Helvetica", 11, "bold"))   # 신호대기와 같은 ● 표시등
        self.tr_ind.pack(side="left", padx=(10, 0))
        self.b_tr_page = ttk.Button(tr, text=self.t("tr_page"), command=self._open_my_page)
        self.b_tr_page.pack(side="left", padx=(10, 0))
        self.tr_status = tk.Label(tr, font=("Helvetica", 11))
        self.tr_status.pack(side="left", padx=(12, 0))
        if not hasattr(self, "consent"):      # 동의 var가 아직 없을 수 있음(아래서 생성) — 선생성
            self.consent = tk.IntVar()
        self._update_tr_status()
        ttk.Label(frm, text=self.t("tr_note"), foreground="#888", wraplength=660,
                  justify="left").pack(anchor="w")
        ttk.Separator(frm).pack(fill="x", pady=8)

        c = self._acur()
        # ── 자산 탭 (NQ/GC/BTC) — 클릭 시 그 자산의 브로커·키·1R로 스왑 ──
        atab = ttk.Frame(frm); atab.pack(fill="x", pady=(2, 6))
        for _a in _ASSETS:
            _lbl = _ASSET_LABEL[_a]["ko" if self.lang == "ko" else "en"]
            tk.Button(atab, text=_lbl, command=lambda a=_a: self._on_asset(a),
                      relief=("sunken" if _a == self._asset else "raised"),
                      font=("Helvetica", 11, "bold" if _a == self._asset else "normal"),
                      bg=("#eaf7ee" if _a == self._asset else "#e2e8f0"),
                      fg=("#178a3a" if _a == self._asset else "#333"),   # 선택=초록(흰색 안 보임, 대표 2026-07-11)
                      padx=14, pady=4).pack(side="left", padx=(0, 4))
        spec = _BROKER_SPEC.get(self._broker_name, _BROKER_SPEC["projectx"])
        # 브로커 선택 — 이 자산이 지원하는 브로커만 (NQ·GC=Topstep/IBKR · BTC=Bybit/Bitget)
        rb = ttk.Frame(frm); rb.pack(fill="x", pady=3)
        ttk.Label(rb, text=self.t("broker"), width=18).pack(side="left")
        self.brokerbox = ttk.Combobox(rb, values=[_broker_label(b) for b in _ASSET_BROKERS[self._asset]],
                                      state="readonly", width=22)
        self.brokerbox.set(_broker_label(self._broker_name)); self.brokerbox.pack(side="left")
        self.brokerbox.bind("<<ComboboxSelected>>", self._on_broker)
        if spec.get("preview"):
            ttk.Label(rb, text=self.t("broker_preview"), foreground="#b06f00").pack(side="left", padx=(8, 0))
        # f1 (브로커별 1번 필드)
        r1 = ttk.Frame(frm); r1.pack(fill="x", pady=3)
        ttk.Label(r1, text=spec["f1"], width=18).pack(side="left")
        _f1sec = bool(spec.get("f1_secret"))          # 크립토 API Key = 비밀 취급(대표 2026-07-12)
        self._f1_unlocked = False
        self.user = ttk.Entry(r1, show=("•" if _f1sec else ""))
        self.user.pack(side="left", fill="x", expand=True)
        self.user.insert(0, c.get("f1", ""))
        if _f1sec:
            self.user.config(state="readonly")        # 잠금해제 후에만 편집·붙여넣기
            self.b_lock_f1 = ttk.Button(r1, text=self.t("unlock"), width=11, command=self._unlock_f1)
            self.b_lock_f1.pack(side="left", padx=(4, 0))
        # Bybit/Bitget은 f1이 긴 API Key라 수동 타이핑이 고역 — 붙여넣기 버튼(대표 2026-07-12)
        ttk.Button(r1, text=self.t("paste"), width=8,
                   command=self._paste_f1).pack(side="left", padx=(4, 0))
        # f2 (비밀 — PIN 잠금) — 있는 브로커만
        if spec.get("f2"):
            r2 = ttk.Frame(frm); r2.pack(fill="x", pady=3)
            ttk.Label(r2, text=spec["f2"], width=18).pack(side="left")
            self.key = ttk.Entry(r2, show="•"); self.key.pack(side="left", fill="x", expand=True)
            self.key.config(state="readonly")   # 값은 _async_load_key(c["f1"])가 Keychain서 채움
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
            self.f3.insert(0, c.get("f3", ""))
            ttk.Button(r3e, text=self.t("paste"), width=8,
                       command=lambda: self._paste_into(self.f3)).pack(side="left", padx=(4, 0))
        # 계좌 스코프 — 계좌 개념 있는 브로커만(Topstep/IBKR)
        if spec.get("acct"):
            r3 = ttk.Frame(frm); r3.pack(fill="x", pady=3)
            ttk.Label(r3, text=self.t("scope"), width=18).pack(side="left")
            self.scope = ttk.Combobox(r3, values=[self.t("all")], state="normal")
            self.scope.set(c.get("acct") or self.t("all")); self.scope.pack(side="left", fill="x", expand=True)
            ttk.Label(frm, text=self.t("scope_note"), foreground="#888").pack(anchor="w")

        row = ttk.Frame(frm); row.pack(fill="x", pady=(8, 2))
        self.b_hc = ttk.Button(row, text=self.t("btn_conn"), command=self.healthcheck); self.b_hc.pack(side="left")
        self.b_acc = ttk.Button(row, text=self.t("btn_accts"), command=self.accounts); self.b_acc.pack(side="left", padx=6)
        # 동의 — 연결 테스트 '바로 밑'(실행 동작 전 필요)
        if not hasattr(self, "consent"):
            self.consent = tk.IntVar()               # 탭 전환(재빌드)에도 동의 상태 유지
        ttk.Checkbutton(frm, variable=self.consent, text=self.t("consent"),
                        command=self._update_tr_status).pack(anchor="w", pady=(6, 0))
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
        # 청산 시각 = 시스템 세션 마감(자산별 자동, _ASSET_EXITS) — 사용자 입력 제거(2026-07-11).
        ttk.Label(af, text=self.t("auto_sched"), foreground="#888").pack(side="left", padx=(0, 10))
        # LIVE 체크 = 자산별 저장값(대표 2026-07-12: 자산 상태 완전 분리)
        self.auto_live = tk.IntVar(value=1 if self._acur().get("auto_live") else 0)
        self.cb_auto_live = ttk.Checkbutton(af, text=self.t("auto_live"), variable=self.auto_live)
        self.cb_auto_live.pack(side="left", padx=(0, 10))
        self.b_auto = ttk.Button(af, text=self.t("auto_stop") if self._asset in self._auto_jobs else self.t("auto_start"),
                                 command=self.toggle_auto); self.b_auto.pack(side="left")
        self.auto_ind = tk.Label(af, font=("Helvetica", 11, "bold"))
        self.auto_ind.pack(side="left", padx=(10, 0))
        self._set_auto_ind(self._asset in self._auto_jobs, [self._asset])
        ttk.Label(frm, text=self.t("auto_note"), foreground="#888").pack(anchor="w")

        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_sig"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        sg = ttk.Frame(frm); sg.pack(fill="x", pady=3)
        ttk.Label(sg, text=self.t("sig_1r")).pack(side="left", padx=(0, 4))
        self.one_r = ttk.Entry(sg, width=8)
        self.one_r.insert(0, str(self._acur().get("one_r", 600)))
        self.one_r.pack(side="left", padx=(0, 14))
        self.sig_live = tk.IntVar(value=1 if self._acur().get("sig_live") else 0)
        self.cb_sig_live = ttk.Checkbutton(sg, text=self.t("sig_live"), variable=self.sig_live)
        self.cb_sig_live.pack(side="left", padx=(0, 12))
        self.b_sig = ttk.Button(sg, text=self.t("sig_stop") if self._asset in self._sig_assets else self.t("sig_start"),
                                command=self.toggle_sig); self.b_sig.pack(side="left")
        self.sig_ind = tk.Label(sg, font=("Helvetica", 11, "bold"))
        self.sig_ind.pack(side="left", padx=(10, 0))
        self._set_sig_ind(self._asset in self._sig_assets, [self._asset])
        ttk.Label(frm, text=self.t("sig_note"), foreground="#888", wraplength=660,
                  justify="left").pack(anchor="w")


        ttk.Separator(frm).pack(fill="x", pady=8)
        self.out = scrolledtext.ScrolledText(frm, height=10, font=("Menlo", 11), wrap="word")
        self.out.pack(fill="both", expand=True, pady=(6, 0))

        # 연결 테스트 통과 전엔 비활성화할 '실행' 버튼들. b_hc(연결 테스트)는 항상 활성.
        self._action_btns = [self.b_acc, self.b_flat_dry, self.b_flat_live, self.b_auto, self.b_sig,
                             self.b_tr]
        self._apply_gating()
        # 안내 로그: 첫 실행만 '준비됨…', 이후(탭·브로커 전환 재빌드)는 그 자산의 실제 연결
        # 상태를 찍는다 — '준비됨'이 매번 떠서 리셋된 걸로 오해되던 것 수정(대표 2026-07-12).
        if not getattr(self, "_built_once", False):
            self._built_once = True
            self.log(self.t("ready"))
        else:
            _ok = self._conn_by_asset.get(self._asset)
            self.log(f"▸ {self._asset} · {_broker_label(self._broker_name)} — "
                     + (("연결됨 ✓ (테스트 통과, 재연결 불필요)" if self.lang == "ko"
                         else "connected ✓ (test passed, no retest needed)") if _ok else
                        ("연결 테스트 필요" if self.lang == "ko" else "connection test required")))
        self._async_load_key(self._acur().get("f1", ""))

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

        topstep = self._broker_name == "projectx"

        def en(b, ok):
            try:
                b.config(state="normal" if ok else "disabled")
            except Exception:
                pass
        en(self.b_acc, conn and topstep)
        # 루프 '정지'는 어느 탭에서든 항상 가능해야 함(미연결 탭에서 버튼이 죽으면 돌던 루프를
        # 못 세움 — 대표 2026-07-12). 시작 조건은 기존대로(연결+권한), 도는 중엔 무조건 활성.
        en(self.b_flat_dry, use); en(self.b_flat_live, use and live_ok)
        en(self.b_auto, use or getattr(self, "_auto_on", False))
        # 자동 진입 = 전 브로커(선물 place_entry + 크립토 place_entry, 2026-07-11 크립토 제한 해제).
        # 신호 대기 무장 = '연결 테스트 통과 자산만'(2026-07-11) — 현재 탭 브로커 종류와는 무관.
        en(self.b_sig, auto or getattr(self, "_sig_on", False))
        # 공개 트랙레코드 푸시 = Autopilot 등급 자격(서버 entitled와 동일 기준: 마스터 스위치 무관,
        # 주문 실행이 아니라 본인 성과 공개라서). 토큰 유효 + royal/admin이면 활성.
        if hasattr(self, "b_tr"):
            en(self.b_tr, bool(g.get("ok")) and g.get("tier") in ("royal", "admin"))
        if hasattr(self, "tr_ind"):
            self._update_tr_status()
        for cb, var in ((self.cb_auto_live, self.auto_live), (self.cb_sig_live, self.sig_live)):
            try:
                if not live_ok:
                    var.set(0); cb.config(state="disabled")
                else:
                    cb.config(state="normal")
            except Exception:
                pass
        # fail-closed: 돌던 루프가 '권한'을 잃으면(토큰 변경·강등·만료·마스터 OFF) 자동 중지한다.
        # 버튼만 끄면 이미 도는 스레드가 계속 진입/청산하는 구멍이 생긴다.
        # ⚠️ 판정은 하트비트 권한만 — 연결(_connected)은 '현재 탭' 상태라 여기 섞으면
        # 탭 전환이 돌던 루프를 죽인다(대표 2026-07-11 "자산 옮기면 다 리셋" 버그).
        perm_use = bool(g.get("ok") and g.get("enabled") and caps.get("use"))
        perm_auto = bool(g.get("ok") and g.get("enabled") and caps.get("autoentry"))
        if getattr(self, "_sig_on", False) and not perm_auto:
            self._sig_on = False
            self._sig_assets.clear()
            self.b_sig.config(text=self.t("sig_start")); self._set_sig_ind(False)
            self.log("⏹ 자동 진입 권한 상실 → 신호 대기 자동 중지 (fail-closed).")
        if getattr(self, "_auto_on", False) and not perm_use:
            self._auto_on = False
            self._auto_jobs.clear()
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

    def _toggle_token_show(self):
        """토큰 표시/잠금 토글 — 어깨너머·스트리밍 노출 방지(기본 잠금)."""
        self.token_e.config(show="" if self.token_e.cget("show") else "•")

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
        """멤버십 토큰으로 hb-<token>.json을 읽어 권한(_gate) 갱신. 실패/만료/철회 = fail-closed.
        하드닝(2026-07-11): timeout 8→15 + 네트워크 일시 오류 1회 재시도 — 경로 순단으로
        회원이 억울하게 잠기는(깜빡 잠김) 오발 감소. 서버가 명시적으로 거부(locked/expired)한
        경우는 재시도 없이 즉시 잠금(진짜 철회는 그대로 fail-closed)."""
        tok = (self.token_e.get().strip() if hasattr(self, "token_e") else self._token)
        tok = (tok or "").strip()
        self._token = tok

        def w():
            gate = {"ok": False, "tier": "—", "enabled": False, "force_dry_run": True,
                    "caps": {"use": False, "manualentry": False, "autoentry": False},
                    "brokers": {}, "reason": "no token"}
            if tok:
                import requests
                import time as _t
                import autopilot_crypto
                for attempt in (1, 2):                      # 1회 재시도(총 2회)
                    try:
                        r = requests.get(_hb_url(tok), params={"t": int(_t.time())}, timeout=15)
                        try:
                            hb = autopilot_crypto.decrypt(tok, r.text) if r.ok else {}
                        except Exception:
                            hb = {"ok": False, "reason": "decrypt failed"}
                        if not r.ok and attempt == 1:       # 5xx/일시 응답불량 → 재시도
                            _t.sleep(2); continue
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
                        break
                    except Exception as e:                  # 네트워크 예외(순단) → 1회 재시도
                        gate["reason"] = f"heartbeat error: {e}"
                        if attempt == 1:
                            _t.sleep(2)
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

    def _acur(self):
        """현재 자산 탭의 설정 dict {broker,f1,f3,acct,one_r}."""
        return self._acfg[self._asset]

    def _save_current_asset(self):
        """현재 탭의 위젯 값을 self._acfg[현재자산] 슬롯에 저장 + 비밀은 Keychain + yaml 영속화."""
        c = self._acfg[self._asset]
        c["broker"] = self._broker_name
        cr = c.setdefault("creds", {}).setdefault(self._broker_name, {})
        if hasattr(self, "user"):
            cr["f1"] = self.user.get().strip()
        cr["f3"] = self._f3()
        cr["acct"] = self._scope()
        # 상위 미러 = 현재 브로커 창고 (다운스트림 c.get("f1") 호환)
        c["f1"], c["f3"], c["acct"] = cr.get("f1", ""), cr.get("f3", ""), cr.get("acct", "")
        if hasattr(self, "one_r"):
            try:
                c["one_r"] = float(str(self.one_r.get()).replace(",", "").strip())
            except (TypeError, ValueError):
                pass
        if hasattr(self, "key"):                 # 비밀(f2) → Keychain (f1 키로)
            _kc_save(c["f1"], self.key.get())
        # LIVE 선택도 자산별(대표 2026-07-12 — 전역이라 탭 넘어가면 '셋된 것처럼' 보이던 혼동 제거)
        if hasattr(self, "auto_live"):
            c["auto_live"] = bool(self.auto_live.get())
        if hasattr(self, "sig_live"):
            c["sig_live"] = bool(self.sig_live.get())
        _save_full(self.lang, self._token, self._acfg, self._profile)

    def _persist(self):
        self._save_current_asset()

    def _on_asset(self, asset):
        """자산 탭 전환 — 현재 탭 저장 → 자산 바꿈 → 그 자산 브로커로 → 재빌드."""
        if asset == self._asset or asset not in self._acfg:
            return
        self._save_current_asset()
        self._asset = asset
        self._broker_name = self._acfg[asset]["broker"]
        # 탭 전환 = 보기 전환일 뿐 — 연결은 자산별 기억 복원, 돌던 루프·체크박스는 안 건드림.
        self._connected = bool(self._conn_by_asset.get(asset, False))
        self._unlocked = False
        self._build()

    def _on_broker(self, *_):
        # 전환 전, 지금 화면 값을 '이전 브로커' 창고에 먼저 저장(안 하면 유실)
        self._save_current_asset()
        sel = self.brokerbox.get()
        for b in _ASSET_BROKERS[self._asset]:    # 이 자산의 브로커 중에서
            if _broker_label(b) == sel:
                self._broker_name = b
                break
        # 미러를 새 브로커 창고로 스위치 — 바이빗↔빗겟 자격증명 완전 분리(대표 2026-07-12)
        c = self._acfg[self._asset]
        c["broker"] = self._broker_name
        cr = c.setdefault("creds", {}).setdefault(self._broker_name, {"f1": "", "f3": "", "acct": ""})
        c["f1"], c["f3"], c["acct"] = cr.get("f1", ""), cr.get("f3", ""), cr.get("acct", "")
        # 무장 상태도 브로커별(대표 2026-07-12): 이 자산이 '이전 브로커'로 무장돼 있었다면 해제 —
        # 새 브로커는 연결 테스트 → 다시 시작해야 무장된다(신호대기·자동청산 동일).
        a = self._asset
        if a in self._sig_assets and self._sig_assets[a].get("broker") != self._broker_name:
            del self._sig_assets[a]
            if not self._sig_assets:
                self._sig_on = False
            self.log(f"⏹ {a} 신호 대기 해제 — 브로커 변경({self._broker_name}). 연결 테스트 후 다시 시작하세요.")
        if a in self._auto_jobs and (self._auto_jobs[a] or [{}])[0].get("broker") != self._broker_name:
            del self._auto_jobs[a]
            if not self._auto_jobs:
                self._auto_on = False
            self.log(f"⏹ {a} 자동청산 해제 — 브로커 변경({self._broker_name}). 연결 테스트 후 다시 시작하세요.")
        self._connected = False
        self._conn_by_asset.pop(self._asset, None)   # 브로커가 바뀌면 연결테스트 다시
        self._unlocked = False
        # ⚠️ _persist() 금지 — 위젯엔 아직 '이전 브로커' 값이 있어 새 창고를 오염시킨다.
        # 위에서 창고 저장·미러 스위치를 끝냈으니 yaml만 직접 영속화하고 재빌드.
        _save_full(self.lang, self._token, self._acfg, self._profile)
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

    def _unlock_f1(self):
        """크립토 API Key(f1) 잠금 토글 — f2와 같은 PIN 사용(대표 2026-07-12)."""
        if self._f1_unlocked:                                # → re-lock
            self._f1_unlocked = False
            self.user.config(state="readonly", show="•")
            self.b_lock_f1.config(text=self.t("unlock"))
            return
        if not _pin_hash():
            pin = simpledialog.askstring("PIN", self.t("pin_new"), show="*", parent=self.root)
            if not pin:
                return
            _pin_set(pin)
        else:
            pin = simpledialog.askstring("PIN", self.t("pin_enter"), show="*", parent=self.root)
            if not pin or not _pin_ok(pin):
                messagebox.showwarning("PIN", self.t("pin_wrong")); return
        self._f1_unlocked = True
        self.user.config(state="normal", show="")
        self.b_lock_f1.config(text=self.t("lock"))

    def _paste_f1(self):
        """f1 붙여넣기 — 비밀 취급 브로커(크립토)는 잠금해제 후에만."""
        _sp = _BROKER_SPEC.get(self._broker_name, {})
        if _sp.get("f1_secret") and not self._f1_unlocked:
            messagebox.showinfo("PIN", self.t("locked_msg")); return
        self._paste_into(self.user)

    def _paste_into(self, entry):
        """클립보드 → 일반 Entry 교체 붙여넣기(strip). f1(API Key)·f3(Passphrase)용."""
        try:
            entry.delete(0, "end")
            entry.insert(0, self.root.clipboard_get().strip())
        except Exception:
            pass

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
            self._conn_by_asset[self._asset] = True   # 탭 전환 후 복귀해도 재연결 불필요
            # 진입 관련 계정 정보(읽기전용): 레버리지·마진모드·가용잔고 — 진입 전에 설정 상태를
            # 앱 로그에서 바로 확인(대표 2026-07-12). 변경은 안 함, 실패 시 조용히 생략.
            try:
                for _ln in (b.entry_info() or []):
                    self.log(f"   ℹ {_ln}")
            except AttributeError:
                pass
            except Exception:
                pass
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
        """자동청산 토글 — **현재 자산만** 무장/해제(대표 2026-07-11). 탭 전환은 무장에 무영향.
        루프는 무장 자산이 하나라도 있으면 돌고, self._auto_jobs를 매 사이클 동적으로 읽는다."""
        a = self._asset
        if a in self._auto_jobs:
            del self._auto_jobs[a]
            if not self._auto_jobs:
                self._auto_on = False
            self.b_auto.config(text=self.t("auto_start"))
            self._set_auto_ind(False, [a])
            self.log(f"⏹ {a} 자동청산 해제." + ("" if self._auto_jobs else " (무장 자산 없음 — 루프 종료)"))
            return
        if not self._consent_ok():
            return
        self._save_current_asset()
        c = self._acfg[a]
        f1 = (c.get("f1") or "").strip()
        if not f1:
            messagebox.showwarning(self.t("input_needed"),
                                   f"{a}: 브로커 키를 입력하세요." if self.lang == "ko"
                                   else f"{a}: enter the broker key."); return
        # 자산별 연결 테스트 통과 필수(대표 2026-07-11)
        if not self._conn_by_asset.get(a):
            messagebox.showwarning(self.t("btn_conn"),
                                   f"{a}: 먼저 '연결 테스트'를 통과하세요." if self.lang == "ko"
                                   else f"{a}: pass 'Test connection' first."); return
        if not self._broker_allowed(c["broker"]):
            self.log(f"➖ {a}: 브로커 [{c['broker']}] 지원 꺼짐(서버) — 자동청산 불가.")
            return
        _sp = _BROKER_SPEC.get(c["broker"], {})
        acct = (c.get("acct") or "").strip()
        if _sp.get("acct") and not acct:
            messagebox.showwarning(self.t("input_needed"),
                                   f"{a}: 사용 계좌를 지정하세요." if self.lang == "ko"
                                   else f"{a}: pick an account."); return
        cred = {"broker": c["broker"], "f1": f1, "f2": (_kc_load(f1) or ""),
                "f3": c.get("f3", ""), "acct": acct}
        live = bool(self.auto_live.get())             # 이 자산의 LIVE 선택(발화 순간 게이트 재판정)
        jobs = [{"asset": a, "tz": tzname, "hour": hour, "live": live, **cred}
                for tzname, hour in _ASSET_EXITS.get(a, [])]
        if not jobs:
            self.log(f"➖ {a}: 세션 마감 스케줄 없음 — 자동청산 미지원.")
            return
        self._auto_jobs[a] = jobs
        self.b_auto.config(text=self.t("auto_stop"))
        self._set_auto_ind(True, [a])
        _sumry = " · ".join(f"{j['asset']} {j['hour']:02d}:00 {'ET' if 'New_York' in j['tz'] else 'UTC'}"
                            for j in jobs)
        self.log(f"\n▶ {a} 자동청산 무장 [{_sumry}] · 현재 무장: {'·'.join(sorted(self._auto_jobs))} · "
                 f"{'LIVE' if live else 'dry-run'}. (keep the app open & the computer awake)")
        self.log(f"   {self.t('warn_mix')}")
        if not self._auto_on:
            self._auto_on = True
            threading.Thread(target=self._auto_loop, daemon=True).start()

    def _handle_stop_failure(self, b, aid, contract, direction, size, stop, res):
        """Protective stop didn't land after a market entry. Broker rejection = permanent (e.g. price
        already through the stop) → flatten the position now so we're never unprotected. Transient
        (network) → retry the stop a few times; if it still won't land, KEEP the position and warn
        loudly (the session-close auto-flatten is the backstop)."""
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
        self.log("   🔴 손절 미거치(네트워크) — 포지션 유지 중. 세션 마감 자동청산이 백스톱이나, "
                 "지금 수동으로 손절/확인 권장!")

    def _flatten_unprotected(self, b, aid, contract, why):
        """Market-close a just-entered position whose protective stop was rejected."""
        self.log(f"   🛑 손절 거부됨({why}) → 무방비 포지션 즉시 청산")
        try:
            b.close_contract(aid, contract)
            self.log("   ↩ 포지션 청산 완료(손절 불가로 진입 취소).")
        except Exception as ce:
            self.log(f"   ❌ 긴급 청산 실패: {ce} — 즉시 수동 확인 필요!")

    def _auto_loop(self):
        """자산별 세션 마감 자동청산. 잡은 self._auto_jobs에서 매 사이클 동적으로 읽는다
        (자산별 무장/해제 즉시 반영 — 대표 2026-07-11). 각 잡은
        자기 tz의 마감시각(+창 AUTO_FIRE_WINDOW_MIN분)에 하루 1회 그 자산 브로커를 flatten.
        같은 선물계좌의 NQ(14 ET)·GC(06 ET)는 세션이 안 겹쳐 flatten_all이 서로를 안 건드리고,
        BTC 02:00 UTC flatten은 다음(02-06) 세션 신호 발행(예측 계산 수 분)보다 항상 먼저 끝난다.
        실거래 여부는 발화 순간 _live_now로 재판정(강제 dry run 즉시 반영)."""
        import time as _t
        fired = {}                                     # {(asset,hour): 그 tz의 날짜}
        _tzs = {}

        def _tz(name):
            if name not in _tzs:
                try:
                    _tzs[name] = ZoneInfo(name) if ZoneInfo else None
                except Exception as e:
                    # Windows 등 tzdata 미동봉이면 청산이 조용히 죽는다 → 드러내고 로컬시각 폴백. (2026-06-30)
                    _tzs[name] = None
                    self.log(f"⚠ 타임존 로드 실패({name}: {e!r}) — tzdata 누락 의심. 로컬 시각 폴백, 청산 시각 확인!")
            return _tzs[name]

        while self._auto_on:
            jobs = [j for jl in list(self._auto_jobs.values()) for j in jl]   # 무장 자산 동적 스냅샷
            for j in jobs:
                now = _dt.datetime.now(_tz(j["tz"]))
                today = now.strftime("%Y-%m-%d")
                key = (j["asset"], j["hour"])
                cur = now.hour * 60 + now.minute
                due = j["hour"] * 60
                if not (due <= cur < due + AUTO_FIRE_WINDOW_MIN) or fired.get(key) == today:
                    continue
                fired[key] = today
                # 🛡 오살 방지(연속 세션): 이 자산에 '방금'(발화창 이내) LIVE 새 진입이 있었으면
                # 이번 마감 청산 스킵 — 신호 루프가 이미 "잔여 청산→확인→진입"을 끝냈다는 뜻이고,
                # 여기서 flatten하면 방금 들어간 새 세션 포지션을 죽인다. (대표 2026-07-11)
                _ea = (self._entered_at or {}).get(j["asset"], 0)
                if _t.time() - _ea < AUTO_FIRE_WINDOW_MIN * 60:
                    self.log(f"\n⏭ {j['asset']} 마감 청산 스킵 — {int((_t.time() - _ea) / 60)}분 전 "
                             f"새 세션 진입(신호 루프가 이전 세션 이미 정리).")
                    continue
                live = self._live_now(j.get("live"))   # 이 자산 LIVE 선택 × 발화 순간 게이트 재판정
                _lab = f"{j['asset']} {j['hour']:02d}:00 {'ET' if 'New_York' in j['tz'] else 'UTC'}"
                self.log(f"\n⏰ {_lab} 세션 마감 → auto-close [{j['broker']}"
                         f"{('/' + j['acct']) if j['acct'] else ''}] ({'LIVE' if live else 'dry-run'})")
                try:
                    b = _build_broker(j["broker"], j["f1"], j["f2"], j["f3"],
                                      [j["acct"]] if j["acct"] else [])
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
        """신호 대기(자동진입) 토글 — **현재 자산만** 무장/해제(대표 2026-07-11). 탭 전환 무영향.
        루프는 무장 자산이 하나라도 있으면 돌고, self._sig_assets를 신호 처리 시 동적으로 읽는다."""
        a = self._asset
        if a in self._sig_assets:
            del self._sig_assets[a]
            if not self._sig_assets:
                self._sig_on = False
            self.b_sig.config(text=self.t("sig_start"))
            self._set_sig_ind(False, [a])
            self.log(f"⏹ {a} 신호 대기 해제." + ("" if self._sig_assets else " (무장 자산 없음 — 루프 종료)"))
            return
        if not self._consent_ok():
            return
        if not self._token:
            messagebox.showwarning(self.t("token"), self.t("gate_none")); return
        self._save_current_asset()               # 현재 탭 값 반영
        c = self._acfg[a]
        f1 = (c.get("f1") or "").strip()
        try:
            one_r = float(c.get("one_r", 0))
        except (TypeError, ValueError):
            one_r = 0.0
        if not f1 or one_r <= 0:
            messagebox.showwarning(self.t("sig_1r"),
                                   f"{a}: 브로커 키와 1R 금액을 설정하세요." if self.lang == "ko"
                                   else f"{a}: set the broker key and 1R amount."); return
        # 자산별 연결 테스트 통과 필수(대표 2026-07-11 — NQ만 테스트했는데 GC까지 붙던 버그)
        if not self._conn_by_asset.get(a):
            messagebox.showwarning(self.t("btn_conn"),
                                   f"{a}: 먼저 '연결 테스트'를 통과하세요." if self.lang == "ko"
                                   else f"{a}: pass 'Test connection' first."); return
        if not self._broker_allowed(c["broker"]):
            self.log(f"➖ {a}: 브로커 [{c['broker']}] 지원 꺼짐(서버) — 자동진입 불가.")
            return
        _sp = _BROKER_SPEC.get(c["broker"], {})
        acct = (c.get("acct") or "").strip()
        if _sp.get("acct") and not acct:
            messagebox.showwarning(self.t("input_needed"),
                                   f"{a}: 사용 계좌를 지정하세요." if self.lang == "ko"
                                   else f"{a}: pick an account."); return
        live = bool(self.sig_live.get())              # 이 자산의 LIVE 선택(발주 순간 게이트 재판정)
        self._sig_assets[a] = {"broker": c["broker"], "f1": f1, "f2": (_kc_load(f1) or ""),
                               "f3": c.get("f3", ""), "acct": acct, "one_r": one_r, "live": live}
        self.b_sig.config(text=self.t("sig_stop"))
        self._set_sig_ind(True, [a])
        _sumry = " · ".join(f"{k}:{v['broker']}(1R${v['one_r']:g})" for k, v in sorted(self._sig_assets.items()))
        self.log(f"\n▶ {a} 신호 대기 무장 — 현재 무장 [{_sumry}] · {'LIVE' if live else 'dry-run'}.")
        if not self._sig_on:
            self._sig_on = True
            url = _feed_url(self._token)             # 멤버별 신호 피드
            self.log("   polling feed")
            threading.Thread(target=self._sig_loop, args=(url,), daemon=True).start()

    # ── 공개 트랙레코드 푸시 (Phase B 2단계) ─────────────────────────────────
    @staticmethod
    def _fill_asset(symbol: str):
        """체결 심볼 → 자산. BTCUSDT→BTC · MNQ 계약→NQ · MGC 계약→GC. 모르면 None(제외)."""
        s = str(symbol or "").upper()
        if "BTCUSDT" in s:
            return "BTC"
        if ".MNQ." in s or s.startswith("MNQ"):
            return "NQ"
        if ".MGC." in s or s.startswith("MGC"):
            return "GC"
        return None

    def _update_tr_status(self):
        """트랙레코드 동기화 상태 라벨 — 마지막 동기화 며칠 전 + 며칠 내 하면 기록이 안 끊기는지.
        브로커 체결 이력 조회창(TR_LOOKBACK_DAYS=90일)이 한계라, 마지막 동기화 + 90일 안에
        다시 동기화해야 공백 없이 이어진다(대표 2026-07-12). 앱 실행 중엔 매일 자동이라 여유 만땅."""
        try:
            import time as _t
            last = _last_pushed()
            ko = self.lang == "ko"
            # ● 자동 동기화 무장 여부(동의+Autopilot 등급) — 먼저 판정해 문구 분기에 사용
            g = self._gate or {}
            _armed = bool(self.consent.get() and g.get("ok") and g.get("tier") in ("royal", "admin"))
            if not last:
                txt = ("아직 동기화 전 — 첫 동기화로 기록 추적 시작" if ko
                       else "not synced yet — run the first sync")
                col = "#888888"
            else:
                n = int((_t.time() - last) // 86400)
                d = max(0, TR_LOOKBACK_DAYS - n)
                _ago = ("오늘" if ko else "today") if n == 0 else (f"{n}일 전" if ko else f"{n}d ago")
                if _armed:
                    # 자동 동기화 ON — 매일 알아서 도니 마감 경고는 소음(대표 2026-07-12). 시각만.
                    txt = (f"마지막 동기화 {_ago}" if ko else f"last sync {_ago}")
                    col = "#1a7f37"
                elif d == 0:
                    txt = (f"마지막 동기화 {_ago} — 지금 동기화해야 기록이 이어집니다!" if ko
                           else f"last sync {_ago} — sync NOW to keep the record intact!")
                    col = "#b00020"
                else:
                    txt = (f"마지막 동기화 {_ago} · {d}일 내 동기화하면 기록이 끊김 없이 이어짐" if ko
                           else f"last sync {_ago} · sync within {d}d for a gapless record")
                    col = "#b00020" if d <= 3 else ("#b8860b" if d <= 14 else "#1a7f37")
            self.tr_status.config(text=txt, foreground=col)
            self.tr_ind.config(text=self.t("tr_on_ind") if _armed else self.t("tr_off_ind"),
                               fg="white" if _armed else "#666",
                               bg="#1a7f37" if _armed else self.root.cget("bg"))
            # 공개 페이지 버튼 — 서버가 핸들 배정한 뒤부터 활성
            self.b_tr_page.config(state="normal" if (self._profile or {}).get("handle") else "disabled")
        except Exception:
            pass

    def _on_tr_public(self):
        """공개 동의 토글 = 즉시 영속(전역·회원 단위 — 탭 전환과 무관). 다음 동기화(수동/자동)가
        새 공개 상태를 서버에 반영한다."""
        self._profile = {**(self._profile or {}), "public": bool(self.tr_public.get())}
        _save_full(self.lang, self._token, self._acfg, self._profile)
        self.log("🔓 공개 트랙레코드: 공개 동의 " + ("ON — 다음 동기화 때 페이지 공개"
                 if self.tr_public.get() else "OFF — 다음 동기화 때 페이지 비공개"))

    def _open_my_page(self):
        h = (self._profile or {}).get("handle")
        if h:
            webbrowser.open(f"{PUSH_BASE}?u={h}")

    AUTOPUSH_EVERY_S = 24 * 3600      # 일일 자동 동기화 주기

    def _autopush_tick(self):
        """1시간마다 깨어나 24h 경과 시 트랙레코드 자동 동기화(무소음). 조건: 동의 체크 +
        Autopilot 등급(royal/admin) 토큰. 주기 푸시 = 기록이 끊김 없이 쌓임(90일 조회창 공백 방지)."""
        try:
            import time as _t
            g = self._gate or {}
            if (self.consent.get() and g.get("ok") and g.get("tier") in ("royal", "admin")
                    and _t.time() - _last_pushed() > self.AUTOPUSH_EVERY_S):
                self.push_profile(auto=True)
        except Exception:
            pass
        self._update_tr_status()                       # 상태 라벨 시간 경과 반영
        try:
            self.root.after(60 * 60 * 1000, self._autopush_tick)   # 다음 체크 1시간 뒤
        except Exception:
            pass

    def push_profile(self, auto: bool = False):
        """브로커 체결(closed PnL)을 로컬에서 R로 변환·일별 합산해 요약만 서버로 푸시.
        키·잔고 무전송. 서버는 tid로 멱등 병합 → 페이지(?u=핸들) 즉시 갱신.
        auto=True: 일일 자동 동기화(경고창 없이 조용히 스킵) — 주기 푸시로 기록이 계속 쌓여
        90일 조회창(브로커 이력 한계)에 공백이 안 생긴다(대표 2026-07-12)."""
        if auto:
            if not self.consent.get():
                return                                   # 동의 전엔 자동 동기화도 안 함(무소음)
        elif not self._consent_ok():
            return
        # 핸들·이름은 서버가 회원 계정(텔레그램/디스코드)에서 자동 설정(대표 2026-07-11) — 앱은 안 보냄.
        public = bool(self.tr_public.get())
        self._profile = {**(self._profile or {}), "public": public}   # handle 캐시 보존
        self._save_current_asset()                          # 프로필 포함 영속화
        # creds 스냅샷(메인 스레드) — 자산별 '모든 브로커 창고' 순회(대표 2026-07-12):
        # 브로커를 갈아탔어도(바이빗→빗겟 등) 예전 브로커 체결까지 전부 트랙레코드에 수집.
        credlist, one_r_by_asset = [], {}
        for a, c in self._acfg.items():
            try:
                one_r = float(c.get("one_r", 0))
            except (TypeError, ValueError):
                one_r = 0.0
            one_r_by_asset[a] = one_r or 600.0
            for bk, cr in (c.get("creds") or {}).items():
                f1 = (cr.get("f1") or "").strip()
                if not f1:
                    continue
                _sp = _BROKER_SPEC.get(bk, {})
                acct = (cr.get("acct") or "").strip()
                if _sp.get("acct") and not acct:
                    continue
                credlist.append({"asset": a, "broker": bk, "f1": f1, "f2": (_kc_load(f1) or ""),
                                 "f3": cr.get("f3", ""), "acct": acct})
        assets_with_creds = {e["asset"] for e in credlist}
        if not credlist:
            if not auto:
                messagebox.showwarning(self.t("input_needed"),
                                       "브로커·키가 설정된 자산이 없습니다." if self.lang == "ko"
                                       else "No asset has broker credentials configured.")
            return
        tok = self._token
        self.log(f"\n📤 트랙레코드 동기화{'(일일 자동)' if auto else ''} — {'공개' if public else '비공개'} · "
                 f"최근 {TR_LOOKBACK_DAYS}일 체결 수집… (핸들·이름은 계정에서 자동)")

        def w():
            import time as _t
            import requests
            import autopilot_crypto
            start_ms = int((_t.time() - TR_LOOKBACK_DAYS * 86400) * 1000)
            fills = []
            seen_brokers = set()                            # 같은 브로커·키·계좌 중복 조회 방지
            for c in credlist:
                bk = (c["broker"], c["f1"], c["acct"])
                if bk in seen_brokers:
                    continue
                seen_brokers.add(bk)
                try:
                    b = _build_broker(c["broker"], c["f1"], c["f2"], c["f3"],
                                      [c["acct"]] if c["acct"] else [])
                    got = b.closed_fills(start_ms)
                    fills.extend(got)
                    self.log(f"   {c['broker']}: 체결 {len(got)}건")
                except AttributeError:
                    self.log(f"   {c['broker']}: 체결 이력 미지원(지원 예정) — 건너뜀")
                except Exception as e:
                    self.log(f"   ⚠ {c['broker']} 체결 조회 실패: {e}")
            # (date, asset[, BTC세션])별 합산 → trade 1건 · R = 실현손익/그 자산 1R.
            # NQ/GC = 하루 1거래. BTC = 하루 2세션(22·02 UTC 진입)이라 세션별로 쪼갠다 —
            # 날짜로만 합치면 두 거래가 1건으로 뭉개지고 방향도 첫 체결 것만 남는다(대표 2026-07-11).
            import datetime as _dtd

            def _btc_sess(dt):
                # 청산시각(UTC)으로 세션 판정: 22세션은 익일 02:00 예정청산(=02:05 전) 또는
                # 진입 직후 조기청산(14:00 이후), 그 사이는 02세션(02:00 진입, 06:00 예정청산·손절 포함).
                m = dt.hour * 60 + dt.minute
                return "22" if (m < 125 or m >= 840) else "02"

            agg = {}
            for f in fills:
                a = self._fill_asset(f.get("symbol"))
                if not a or a not in assets_with_creds:
                    continue
                dt = _dtd.datetime.fromtimestamp((f.get("ts_ms") or 0) / 1000, _dtd.timezone.utc)
                d = dt.date().isoformat()
                k = (d, a, _btc_sess(dt) if a == "BTC" else "")
                e = agg.setdefault(k, {"pnl": 0.0, "direction": f.get("direction", "LONG")})
                e["pnl"] += float(f.get("pnl") or 0)
            trades = [{"tid": f"agg-{d}-{a}" + (f"-{s}" if s else ""), "date": d, "instrument": a,
                       "direction": v["direction"],
                       "r": round(v["pnl"] / one_r_by_asset[a], 3)}
                      for (d, a, s), v in sorted(agg.items())]
            if not trades:
                self.log("   체결 없음 — 푸시할 내용이 없습니다.")
                return
            self.log(f"   일별 합산 {len(trades)}건 → 푸시 (키·잔고 무전송)")
            pid = autopilot_crypto.path_id(tok)
            ok_total, srv_handle = None, None
            for i in range(0, len(trades), TR_CHUNK):
                payload = {"public": public, "trades": trades[i:i + TR_CHUNK]}
                blob = autopilot_crypto.encrypt(tok, payload)
                try:
                    r = requests.get(PUSH_BASE + "eqpush",
                                     params={"profile_push": blob, "pid": pid}, timeout=30)
                    txt = r.text or ""
                    if "pp:ok" in txt:
                        import re as _re
                        m = _re.search(r"pp:ok:(\d+)(?::([a-z0-9_-]+))?", txt)
                        if m:
                            ok_total = m.group(1)
                            srv_handle = m.group(2) or srv_handle
                    else:
                        import re as _re
                        m = _re.search(r"pp:err:[^<\"]+", txt)
                        self.log(f"   ❌ 서버 거절: {m.group(0) if m else txt[:120]}")
                        return
                except Exception as e:
                    self.log(f"   ❌ 푸시 실패: {e}")
                    return
            self.log(f"   ✅ 동기화 완료 — 서버 누적 {ok_total}건.")
            _mark_pushed()                              # 일일 자동 동기화 기준점
            try:
                self.root.after(0, self._update_tr_status)
            except Exception:
                pass
            if srv_handle:                              # 핸들 영속화 → '공개 페이지' 버튼 활성
                self._profile["handle"] = srv_handle
                try:
                    self.root.after(0, self._save_current_asset)
                except Exception:
                    pass
            if public and srv_handle:
                self.log(f"   🔗 공개 페이지: {PUSH_BASE}?u={srv_handle}")
            elif public:
                self.log("   🔗 공개 페이지 주소는 서버가 핸들 배정 후 다음 동기화에 표시됩니다.")
            else:
                self.log("   (비공개 상태 — '공개 동의' 체크 후 다시 푸시하면 페이지가 열립니다)")
        threading.Thread(target=w, daemon=True).start()

    def _resolve_contract(self, b, symbol):
        """현재(활성) 계약ID를 종목으로 자동 조회. 활성 우선, 없으면 첫 결과."""
        cs = b.search_contracts(symbol)
        if not cs:
            return None
        active = next((c.get("id") for c in cs if c.get("activeContract")), None)
        return active or cs[0].get("id")

    _HB_BROKER_KEY = {"projectx": "topstep", "ibkr": "ibkr", "bybit": "bybit", "bitget": "bitget"}

    def _broker_allowed(self, broker: str) -> bool:
        """서버(admin) '브로커별 지원' 토글 반영 — 하트비트 brokers에서 꺼진 브로커는 자동화 제외.
        brokers 정보가 없으면(구버전 hb 등) 허용(하위호환)."""
        gb = (self._gate or {}).get("brokers") or {}
        if not gb:
            return True
        return bool(gb.get(self._HB_BROKER_KEY.get(broker, broker), False))

    def _live_now(self, live_flag: bool) -> bool:
        """발주 '순간'의 실거래 여부 = 시작 시 선택 AND 현재 게이트의 강제 모의 아님.
        어드민이 강제 dry run을 켜면(하트비트 ≤5분 반영) 이미 돌던 루프도 다음 발주부터
        모의로 강등된다 — 시작 때 캡처한 값만 믿으면 킬스위치가 기존 루프에 안 먹는 구멍."""
        return bool(live_flag) and not (self._gate or {}).get("force_dry_run", True)

    def _sig_loop(self, url):
        # 무장 자산 설정은 self._sig_assets에서 동적으로 읽는다(자산별 무장/해제 즉시 반영).
        # {asset: {broker,f1,f2,f3,acct,one_r}} — 신호의 instrument로 해당 자산 설정을 찾아 진입.
        import time as _t
        import requests
        import autopilot_crypto
        from eqexec import sizing
        last_id = None
        entered_day = {}       # 자산별 {asset: 발행날짜}. 같은 자산 같은 날 재진입 금지, 새 날이면 자동 리셋
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
                _asset = str(sig.get("instrument") or "").upper()   # NQ/GC/BTC
                _sess = sig.get("session")                          # BTC 2세션(22/2), 나머지 None
                _dedup_key = f"{_asset}:{_sess}" if _sess is not None else _asset  # 세션별 재진입 판정
                cfg = self._sig_assets.get(_asset)   # 동적 — 미무장 자산 신호는 스킵
                # 이 자산 미설정(탭에 브로커·키·1R 없음) → 진입 안 함. 다른 자산 신호만 처리.
                if not cfg:
                    self.log(f"\n➖ 신호 [{sid}] {_asset} — 이 자산은 자동진입 미설정, 건너뜀.")
                    _t.sleep(SIG_POLL_SECS); continue
                _broker, user, key = cfg["broker"], cfg["f1"], cfg["f2"]
                f3, sc, one_r = cfg["f3"], cfg["acct"], cfg["one_r"]
                live = self._live_now(cfg.get("live"))   # 이 자산 LIVE 선택 × 발주 순간 게이트 재판정
                # 신호 발행 시각 → 나이(신선도) + 발행 '날짜'(하루 1회 재진입 판정)
                import datetime as _dtd
                _pub_ts = sig.get("published_at")
                try:
                    _age = _t.time() - float(_pub_ts) if _pub_ts is not None else None
                    _sig_day = (_dtd.datetime.fromtimestamp(float(_pub_ts)).date()
                                if _pub_ts is not None else None)
                except (TypeError, ValueError):
                    _age, _sig_day = None, None
                # 🚫 재진입 금지(자산별 하루 1회): 이 자산이 이 '발행 날짜'에 이미 진입했으면 스킵
                # (놓쳤든 청산됐든 같은 날 같은 자산 재진입 X). 다른 자산·다음 날 새 신호엔 자동 재진입.
                if _sig_day is not None and entered_day.get(_dedup_key) == _sig_day:
                    self.log(f"\n⏹ 신호 [{sid}] {_asset} 무시 — 오늘({_sig_day}) 이미 진입함(재진입 금지). "
                             f"다음 날 새 신호에 다시 진입합니다.")
                    _t.sleep(SIG_POLL_SECS)
                    continue
                # 🛑 오래된 신호로 실수 진입 방지: published_at이 최근(MAX_SIGNAL_AGE_SEC 이내)이 아니면
                # 진입하지 않고 새 신호를 기다린다. 발행시각 불명이면 안전상 진입 안 함.
                if _age is None or _age > MAX_SIGNAL_AGE_SEC:
                    if _age is None:
                        _why = "발행시각 불명(안전상 스킵)"
                    elif _age < 300:
                        _why = f"발행 {_age:.0f}초 전(1분 초과, 오래됨)"
                    else:
                        _why = f"발행 {_age / 60:.0f}분 전(오래됨)"
                    self.log(f"\n⏸ 신호 [{sid}] {_asset} 진입 안 함 — {_why}. 새 신호를 기다립니다.")
                    _t.sleep(SIG_POLL_SECS)
                    continue
                entered_day[_dedup_key] = _sig_day   # 이 자산·세션 이 날 진입 처리 완료 → 같은 날 재진입 금지(성공/실패 무관)
                direction, stop = sig.get("direction"), sig.get("stop_price")
                # 계약 수 = 사용자 1R($) × 신호 size 배수(신뢰도 사이징, 시스템 공식과 동일 —
                # 백테스트/트랙레코드 R 계산이 이 배수를 전제. 대표 2026-07-12 감사에서 누락 발견).
                # 배수는 0~3으로 클램프(랜딩 '거래당 최대 3R' 캡과 일치).
                try:
                    _mult = float(sig.get("size_mult") or 1.0)
                except (TypeError, ValueError):
                    _mult = 1.0
                _mult = max(0.0, min(_mult, 3.0))
                _eff_r = one_r * _mult
                _sz = sizing.compute_size(_asset, _broker, _eff_r,
                                          sig.get("entry_ref"), stop, direction)
                if not _sz:
                    self.log(f"\n⏭ 신호 [{sid}] — 사이징 불가(자산 {_asset}·브로커 {_broker}·"
                             f"진입/손절 확인). 건너뜀."); continue
                size = _sz["size"]            # 선물=정수 계약 · 크립토=분수 BTC (int() 하면 0.3→0 됨!)
                sym = _sz["symbol"]
                # 캡처 순간 로그 — 보낸 시각(피드 published_at) · 받은 시각(now) · 지연 · 포지션 정보.
                import datetime as _dtl
                _recv = _dtl.datetime.now()
                _pub = sig.get("published_at")
                try:
                    _sent = _dtl.datetime.fromtimestamp(float(_pub)) if _pub else None
                    _lat = f"{_recv.timestamp() - float(_pub):.1f}s"
                except (TypeError, ValueError):
                    _sent, _lat = None, "?"
                self.log(f"\n📶 신호 캡처 [{sid}] — {_asset or sym} {direction} x{size} {sym}")
                self.log(f"   포지션: {direction} · 수량 {size} ({sym}) · 손절 {stop} · 진입참조 "
                         f"{sig.get('entry_ref')} · 1R=${one_r:g}×{_mult:.2f}x=${_eff_r:g}"
                         f"(손절거리 {_sz['risk_pts']})")
                self.log(f"   ⏱ 보낸 시각 {_sent.strftime('%H:%M:%S') if _sent else '?'}  ·  "
                         f"받은 시각 {_recv.strftime('%H:%M:%S')}  ·  지연 {_lat}")
                _is_fut = bool(_BROKER_SPEC.get(_broker, {}).get("futures"))
                self.log(f"   → {sym} @ {_broker} → {'LIVE' if live else 'dry-run'} 진입"
                         f"{(' [' + sc + ']') if sc else ''}")
                if size <= 0:
                    self.log(f"   ⏭ 1R=${one_r:g}가 손절거리({_sz['risk_pts']}) 대비 작아 수량 0 — 건너뜀."); continue
                try:
                    b = _build_broker(_broker, user, key, f3, [sc] if sc else [])
                    _aid, _contract = None, None
                    if _is_fut:
                        # ── 선물(Topstep/IBKR): 계약 조회 + 계좌 id + place_entry(account_id, contract_id) ──
                        _contract = self._resolve_contract(b, sym)
                        if not _contract:
                            self.log(f"   ❌ '{sym}' 활성 계약 없음."); continue
                        self.log(f"   contract: {_contract}")
                        match = [a for a in b._accounts()
                                 if str(a.get("name")) == sc or str(a.get("id")) == sc]
                        if not match:
                            self.log(f"   ❌ 계좌 '{sc}' 없음."); continue
                        _aid = match[0]["id"]
                    # ── 잔여 포지션 정리: "청산 → 죽은 것 확인 → 진입" (대표 2026-07-11) ──
                    # BTC 연속 세션(22-02 청산 = 02-06 진입 시각)에서 순서가 뒤집히면:
                    # 스킵하면 새 세션을 영영 놓치고, 확인 없이 들어가면 2배 포지션. 그래서
                    # '이 자산 심볼' 잔여만 청산·확인 후 진입한다(다른 심볼=수동거래 무접촉).
                    existing = b.list_open_positions()
                    _mysym = _contract if _is_fut else b._symbol(sym)
                    mine = [p for p in existing if p.symbol == _mysym]
                    others = [p for p in existing if p.symbol != _mysym]
                    if others:
                        self.log(f"   ⚠ 다른 심볼 포지션 {len(others)}개 감지 — 건드리지 않음: "
                                 + ", ".join(f"{p.symbol}" for p in others[:3]))
                    if mine:
                        if not live:
                            self.log(f"   (DRY-RUN) 이전 세션 잔여 {len(mine)}개 — LIVE면 청산 확인 후 진입.")
                        else:
                            self.log(f"   ♻ 이전 세션 잔여 포지션 {len(mine)}개 → 청산 후 진입 (연속 세션)")
                            _dead = False
                            if _is_fut:
                                try:
                                    for p in mine:
                                        b.close_contract(p.raw.get("_accountId") or _aid, _mysym)
                                    for _chk in range(6):    # 죽은 것 '확인' 후에만 진입
                                        _t.sleep(1)
                                        if not any(q.symbol == _mysym for q in b.list_open_positions()):
                                            _dead = True; break
                                except Exception as _ce:
                                    self.log(f"   ❌ 잔여 청산 실패: {_ce}")
                            else:
                                _r = b.close_symbol(sym, dry_run=False)
                                _dead = bool(_r.get("closed"))
                                if not _dead:
                                    self.log(f"   ❌ 잔여 청산 미확인: {_r.get('error')}")
                            if not _dead:
                                self.log("   🛑 청산 확인 실패 — 진입 중단(순서 보장). 수동 확인 필요!")
                                continue
                            self.log("   ✅ 잔여 청산 확인 — 진입 진행.")
                    if _is_fut:
                        # customTag은 ProjectX '계좌당 유일' 필요 → ms 타임스탬프로 유니크.
                        res = b.place_entry(account_id=_aid, contract_id=_contract, side=direction,
                                            size=size, order_type=2, stop_loss_price=stop,
                                            custom_tag=f"EQ-AP-{int(_recv.timestamp() * 1000)}",
                                            dry_run=not live)
                    else:
                        # ── 크립토(Bybit/Bitget): symbol·qty만, stopLoss는 주문에 첨부 ──
                        # Bitget = Bybit 좌표계 손절에 거래소 베이시스 가산(캘리브레이션, 대표 2026-07-12)
                        if _broker == "bitget" and stop is not None:
                            _basis = _cross_basis_bitget()
                            if _basis:
                                stop = round(stop + _basis, 2)
                                self.log(f"   ⚖ 빗겟 캘리브레이션: 바이빗 대비 {_basis:+.2f} → 손절 {stop}")
                        res = b.place_entry(symbol=sym, side=direction, size=size,
                                            stop_loss_price=stop, dry_run=not live)
                    if res.get("error"):
                        self.log(f"   ❌ 진입 실패: {res.get('error')}")
                        # 눈에 띄는 알림(대표 2026-07-12) — 마진 부족·API 거절 등을 놓치지 않게.
                        _em = str(res.get("error"))[:300]
                        self.root.after(0, lambda m=_em, a=_asset: messagebox.showerror(
                            "진입 실패" if self.lang == "ko" else "Entry failed",
                            (f"{a} 진입 주문이 거절되었습니다:\n\n{m}\n\n잔고(마진)·레버리지 설정을 "
                             f"확인하세요." if self.lang == "ko" else
                             f"{a} entry order was rejected:\n\n{m}\n\nCheck margin balance and "
                             f"leverage settings.")))
                        continue
                    if not live:
                        self.log(f"   DRY-RUN entry: {res.get('would_place')}")
                        if res.get("would_place_stop"):
                            self.log(f"   DRY-RUN stop:  {res.get('would_place_stop')}")
                    else:
                        self.log(f"   ✅ 진입 완료: {res.get('entry', res)}")
                        # LIVE 진입 시각 기록(영속) — 자동청산 잡이 발화창 내 '방금 진입'을
                        # 알아보고 새 포지션을 죽이지 않게(연속 세션 순서 보장).
                        self._entered_at = _mark_entered(_asset)
                        if res.get("stop"):
                            self.log("   🛡 보호 손절 거치 완료.")
                        elif res.get("stop_error") and _is_fut:  # 선물만 별도 손절 재시도(크립토는 첨부라 불필요)
                            self._handle_stop_failure(b, _aid, _contract, direction, size, stop, res)
                except Exception as e:
                    self.log(f"   ❌ signal entry failed: {e}")
                    _em = str(e)[:300]
                    self.root.after(0, lambda m=_em, a=_asset: messagebox.showerror(
                        "진입 실패" if self.lang == "ko" else "Entry failed",
                        (f"{a} 진입 중 오류:\n\n{m}" if self.lang == "ko"
                         else f"{a} entry error:\n\n{m}")))
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
