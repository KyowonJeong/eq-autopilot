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


# ── 타임존: tzdata 없이도 도는 현재시각 (대표 2026-07-20 실사고: Windows tzdata 누락 →
#    자동청산 ET ZoneInfo 로드 실패 → 로컬시각(KST)으로 잘못 폴백 → 청산 미발화).
#    UTC는 파이썬 내장이라 무조건 성공. ET는 ZoneInfo가 되면 쓰고, 안 되면 US-Eastern DST 규칙으로
#    UTC에서 산술 계산 — 청산은 tz DB에 절대 의존하지 않는다(대표: "청산은 최대한 간단하게"). ──
def _us_eastern_offset(u):
    """US Eastern의 UTC 오프셋(시간). EDT=-4(3월 둘째 일요일~11월 첫째 일요일), 그 외 EST=-5.
    DST 판정은 근사 ET 날짜(UTC-5)로 — 청산시각(06·14 ET)은 전환 경계(2am)와 멀어 안전."""
    et = u - _dt.timedelta(hours=5)                       # 근사 ET 날짜
    y = et.year
    mar = 8 + (6 - _dt.date(y, 3, 8).weekday()) % 7       # 3월 둘째 일요일
    nov = 1 + (6 - _dt.date(y, 11, 1).weekday()) % 7      # 11월 첫째 일요일
    return -4 if _dt.date(y, 3, mar) <= et.date() < _dt.date(y, 11, nov) else -5


def _now_in(tzname):
    """tzname의 현재 시각(tz-aware). UTC 내장 · ET는 ZoneInfo→실패 시 산술 폴백.
    tzdata가 없어도 절대 예외를 던지지 않는다 — 청산이 조용히 죽지 않게 한다."""
    u = _dt.datetime.now(_dt.timezone.utc)
    if tzname == "UTC":
        return u
    if ZoneInfo is not None:
        try:
            return u.astimezone(ZoneInfo(tzname))
        except Exception:
            pass
    if "New_York" in tzname:                              # America/New_York 산술 폴백
        off = _us_eastern_offset(u)
        return (u + _dt.timedelta(hours=off)).replace(tzinfo=_dt.timezone(_dt.timedelta(hours=off)))
    return u.astimezone()                                 # 미지의 zone → 로컬(최후)

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

# ── 앱 로그 파일 영속화(대표 2026-07-15): 화면 로그를 일자별 파일에도 기록(타임스탬프 부여).
#    앱을 닫아도 수신·체결 지연 기록이 남아 사후 감사 가능. 30일 지난 파일은 시작 시 정리. ──
LOG_DIR = os.path.join(APP_DIR, "logs")
_LOG_LOCK = threading.Lock()


def _log_to_file(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        now = _dt.datetime.now()
        ts = now.strftime("%H:%M:%S")
        path = os.path.join(LOG_DIR, f"eq-{now:%Y%m%d}.log")
        with _LOG_LOCK, open(path, "a", encoding="utf-8") as f:
            for ln in str(msg).split("\n"):
                f.write(f"[{ts}] {ln}\n")
    except Exception:
        pass                     # 로그 실패가 매매를 막으면 안 됨


def _prune_logs(days: int = 30):
    try:
        import time as _tm
        cut = _tm.time() - days * 86400
        for fn in os.listdir(LOG_DIR):
            p = os.path.join(LOG_DIR, fn)
            if fn.startswith("eq-") and fn.endswith(".log") and os.path.getmtime(p) < cut:
                os.remove(p)
    except Exception:
        pass


_prune_logs()

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
HB_GRACE_MIN = 60      # 네트워크 순단 유예(분) - 서버(홈피) 재시작·과부하가 자동매매 권한을
#                        끊지 않게(대표 2026-08-07: 8/5 홈피 반복 재시작이 15분 유예 소진→
#                        전원 fail-closed 사고). 명시 거부(locked/expired)는 유예 없이 즉시 잠금.
# 자산별 세션 청산 시각(거래봉 마감, 서버 archive_resolver._WINDOW와 동일 상수) — 자동청산은
# 사용자가 시각을 고르는 게 아니라 시스템 세션 마감에 자동으로 맞춘다(3자산·BTC 2세션).
#   NQ 10-14 ET → 14:00 ET · GC 02-06 ET → 06:00 ET · BTC 22-02/02-06 UTC → 02:00·06:00 UTC
_ASSET_EXITS = {"NQ": [("America/New_York", 14)], "GC": [("America/New_York", 6)],
                # BTC = X2+BE 조건부 출구(2026-07-15 챔피언): 일 22:00 진입 후 4H 블록마다
                # 판정(반대봉→청산 / 첫봉 순항→본절 / 24h 만기→청산). 판정 시각 6개.
                # 옛 E0(02시 무조건 청산)는 마지막 블록(22시)이 대신한다 — 폴백 아님, 규칙이 다름.
                "BTC": [("UTC", h) for h in (2, 6, 10, 14, 18, 22)]}
# BTC 조건부 출구가 판정할 블록 인덱스: 마감시각 → k (22:00 진입 기준 0..5)
_BTC_BLOCK_K = {2: 0, 6: 1, 10: 2, 14: 3, 18: 4, 22: 5}
AUTO_FIRE_WINDOW_MIN = 30                           # 마감 후 이 분 안에서만 발화(놓친 tick 대비)
# 세션 진입(신호 도착) 시각 — 진입 전 API 사전 점검용(대표 2026-07-13). NQ·GC 주말 스킵.
_ASSET_ENTRIES = {"NQ": [("America/New_York", 10)], "GC": [("America/New_York", 2)],
                  "BTC": [("UTC", 22)]}       # H22 단독 — 02:00 진입 은퇴(2026-07-14)
PRECHECK_WINDOW_MIN = 70                            # 진입까지 이 분 이내면 사전 점검 발동
STOP_RETRIES = 2                                    # protective stop: retries on a transient miss
STOP_RETRY_WAIT = 1.5                               # seconds between stop retries
MAX_SIGNAL_AGE_SEC = 60                             # 자동진입: 발행 1분 이내 신호만 진입(오래된 건 대기)


def _next_entry_dt(asset: str):
    """이 자산의 다음 진입(신호 도착) 시각 — tz-aware datetime. NQ·GC는 주말 건너뜀."""
    from datetime import timedelta
    best = None
    for tzname, hour in _ASSET_ENTRIES.get(asset, []):
        _n = _now_in(tzname)                             # tzdata 없이도 정확(산술 폴백)
        d = _n.replace(hour=hour, minute=0, second=0, microsecond=0)
        if d <= _n:
            d += timedelta(days=1)
        if asset in ("NQ", "GC"):
            while d.weekday() >= 5:                  # 토(5)·일(6) 스킵
                d += timedelta(days=1)
        if best is None or d < best:
            best = d
    return best


def _entry_fail_hint(msg: str, ko: bool) -> str:
    """주문 거절 메시지 → 원인별 안내 문구. 10005(권한)를 잔고 문제로 오도하지 않게(대표 2026-07-12)."""
    m = (msg or "").lower()
    if "10005" in m or "permission" in m or "40014" in m:
        return ("API 키에 주문 권한이 없습니다. 거래소 API 관리에서 이 키에 파생상품(계약) "
                "주문 권한을 켜고, IP 제한이 있다면 이 컴퓨터 IP를 허용하세요."
                if ko else
                "The API key lacks trade permission. In the exchange's API settings, enable "
                "derivatives (contract) order permission for this key, and allow this computer's "
                "IP if the key is IP-restricted.")
    if "10004" in m or "sign" in m or "40012" in m or "40013" in m:
        return ("API 키/시크릿(패스프레이즈)이 올바른지 다시 확인하세요."
                if ko else "Double-check the API key/secret (and passphrase).")
    if "110007" in m or "insufficient" in m or "margin" in m or "balance" in m or "40754" in m:
        return ("가용 잔고(마진)가 부족합니다 — 잔고를 늘리거나 해당 심볼의 레버리지를 높이세요."
                if ko else
                "Insufficient available margin — add funds or raise the symbol's leverage.")
    return ("잔고(마진)·레버리지·API 키 권한을 확인하세요."
            if ko else "Check margin balance, leverage and API key permissions.")


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
_BROKERS = ["projectx", "tradovate", "ibkr", "bybit", "bitget"]

# 자산 탭 + 자산별 브로커 매트릭스(대표 2026-07-10):
#   Topstep(projectx)·IBKR = MNQ·MGC (선물) · Bybit·Bitget = BTC만(BTCUSDT.P, 크립토)
#   ⚠️ BTC를 CME MBTC 선물로 안 함 — MBTC는 주말 휴장인데 BTC 엣지가 주말(일요일)에 몰려 있어
#      MBTC로 돌리면 실행 성과가 크게 훼손됨. BTC는 크립토(주말 거래) 전용.
_ASSETS = ["NQ", "GC", "BTC"]
_ASSET_BROKERS = {"NQ": ["projectx", "tradovate", "ibkr"],
                  "GC": ["projectx", "tradovate", "ibkr"],
                  "BTC": ["bybit", "bitget"]}
_ASSET_LABEL = {"NQ": {"ko": "나스닥 (NQ)", "en": "Nasdaq (NQ)"},
                "GC": {"ko": "금 (GC)", "en": "Gold (GC)"},
                "BTC": {"ko": "비트코인 (BTC)", "en": "Bitcoin (BTC)"}}

# 선물 시간마감 '지정가 청산' 대상 — 대표 2026-07-27 "청산은 비트 빼고 시장가": 선물(NQ·GC)은
#   지정가 도전 없이 즉시 시장가 청산. 빈 dict = 전 선물 시장가(백테스트 비용 가정과도 일치).
#   BTC(크립토)만 지정가 도전 → 시장가 폴백 유지(_exec_close_limit).
#   (구 GC 지정가 배선은 _exec_close_limit_fut에 보존 — 재개하려면 {"GC": "MGC"}로 복원.)
_LIMIT_EXIT_FUT = {}
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
    # Tradovate = 자기자본 주력 브로커(대표 2026-07-27). f3 = "cid:sec[:demo]"
    #   (API 키 페어 콜론 연결 — 셋째 토막 'demo'면 데모 서버). preview=데모 실검증 전.
    "tradovate":   {"label": "Tradovate", "f1": "Username", "f2": "Password",
                    "f3": "API cid:sec[:demo]", "acct": True, "futures": True,
                    "preview": True},
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
    if broker == "tradovate":
        from eqexec.broker.tradovate import TradovateBroker
        from eqexec.config import TradovateCfg
        import hashlib as _hl
        import uuid as _uu
        parts = [p.strip() for p in str(f3 or "").split(":")]
        _env = "demo" if "demo" in [p.lower() for p in parts[2:]] else "live"
        # deviceId = 머신 고정 해시 — 기기 인증 반복(캡차) 방지, 개인정보 아님
        _dev = _hl.sha256(str(_uu.getnode()).encode()).hexdigest()[:24]
        return TradovateBroker(TradovateCfg(env=_env, name=(f1 or ""), password=(f2 or ""),
                                            cid=(parts[0] if parts and parts[0] else ""),
                                            sec=(parts[1] if len(parts) > 1 else ""),
                                            app_id="EdgeQuant-Autopilot",
                                            device_id=_dev, accounts=acc))
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
                  "en": "Enter a membership token — press 'Use free' to get one."},
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
                       "Don't mix in other trades — use a dedicated account."},
    "lang": {"ko": "Language", "en": "Language"},   # 언어 선택 라벨은 언어 무관 고정(대표 2026-07-12)
    "btn_home": {"ko": "홈페이지", "en": "Website"},
    "btn_join": {"ko": "멤버십 가입", "en": "Join membership"},
    "btn_free": {"ko": "무료 사용", "en": "Use free"},
    "free_msg": {"ko": "이 버튼을 누르면 ① 무료 멤버십 토큰이 발급되고 ② 무료 시그널 방에 가입됩니다.\n"
                       "받은 토큰을 위 '멤버십 토큰' 칸에 붙여넣으면 자동 청산이 열립니다.\n"
                       "• Telegram: 봇이 토큰 + 방 초대 링크를 DM 합니다.\n"
                       "• Discord: 로그인하면 토큰이 발급되고 무료 시그널 서버에 자동 가입됩니다.",
                 "en": "This button ① issues you a free membership token and ② joins you to the free signal room.\n"
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
    "pin_enter": {"ko": "PIN 입력 (이 앱에서 키 보호용으로 설정한 PIN — 거래소 비밀번호 아님):",
                  "en": "Enter PIN (the key-protection PIN you set in this app — not your exchange password):"},
    "pin_wrong": {"ko": "PIN이 틀립니다 — 이 앱에서 키 보호용으로 설정했던 PIN입니다 (거래소 비밀번호 아님).",
                  "en": "Wrong PIN — this is the key-protection PIN you set in this app (not your exchange password)."},
    "locked_msg": {"ko": "키를 보거나 바꾸려면 먼저 '잠금해제'(PIN)를 하세요.",
                   "en": "Unlock (PIN) first to view or change the key."},
    "scope": {"ko": "사용 계좌", "en": "Account"},
    "all": {"ko": "(전체 계좌)", "en": "(all accounts)"},
    "scope_note": {"ko": "※ '사용 계좌'에 지정한 계좌에만 청산/진입이 적용됩니다. (전체 = 모든 활성 계좌)",
                   "en": "※ Closes and entries apply only to the chosen account. (all = every active account)"},
    "btn_conn": {"ko": "연결 테스트", "en": "Test connection"},
    "btn_accts": {"ko": "계좌 목록 불러오기", "en": "Load accounts"},
    "sec_flat": {"ko": "즉시 청산 (지금 사용 계좌의 모든 포지션 닫기)",
                 "en": "Immediate close (flatten all positions on the chosen account now)"},
    "dry_close": {"ko": "모의 청산 (Dry-run)", "en": "Dry-run close"},
    "live_close": {"ko": "⚠ 실제 청산 (LIVE)", "en": "⚠ LIVE close"},
    "consent": {"ko": "동의: 본인 키·본인 기기·본인 책임. EdgeQuant는 거래하지 않음 (실행 동작에 필요)",
                "en": "I agree: my key, my device, my responsibility. EdgeQuant does not trade. (required to act)"},
    "ready": {"ko": "준비됨. 키 입력 → '연결 테스트' → 통과하면 나머지 기능이 켜집니다.",
              "en": "Ready. Enter your key → 'Test connection' → the rest unlocks once it passes."},
    "conn_first": {"ko": "※ 먼저 '연결 테스트'를 통과해야 청산·자동 진입 기능이 활성화됩니다.",
                   "en": "※ Pass 'Test connection' first to unlock close / auto-entry."},
    "conn_ok": {"ko": "기능이 활성화되었습니다.", "en": "Features unlocked."},
    "need_creds": {"ko": "이메일과 API Key를 모두 입력하세요.", "en": "Enter both email and API Key."},
    "need_consent": {"ko": "실행하려면 먼저 동의 체크박스를 켜세요.", "en": "Tick the consent box before acting."},
    "live_confirm": {"ko": "실거래 확인", "en": "Confirm LIVE"},
    "input_needed": {"ko": "입력 필요", "en": "Input needed"},
    "pick_acct": {"ko": "진입하려면 '사용 계좌'에서 단일 계좌를 지정하세요 (전체 불가).",
                  "en": "Entry requires a single account in 'Account' (not all)."},
    "sec_tr": {"ko": "트랙레코드 기록 (Autopilot)", "en": "Track record (Autopilot)"},
    # 앱은 '기록'에만 동의받는다(대표 2026-08-08 "공개동의하지 말구 기록 동의로 바꾸고").
    # 공개 여부는 홈피 대시보드에서 회원이 직접 켜고 끈다 - 앱에서 결정할 일이 아니다.
    "tr_public": {"ko": "기록 동의", "en": "Record my results"},
    "tr_on_ind": {"ko": "  ● 자동 동기화 ON  ", "en": "  ● Auto-sync ON  "},
    "tr_off_ind": {"ko": "  ○ 자동 동기화 꺼짐  ", "en": "  ○ Auto-sync off  "},
    "tr_page": {"ko": "내 페이지", "en": "My page"},
    "tr_push": {"ko": "동기화(푸시)", "en": "Sync (push)"},
    "tr_note": {"ko": "※ 실거래 트랙레코드를 자동 생성합니다. EdgeQuant가 실행한 거래만 집계하며, "
                      "같은 계좌에서 직접 하신 거래는 제외됩니다. 앱은 해당 체결만 로컬에서 R 단위로 "
                      "변환한 후 요약만 서버에 전송합니다. API 키, 계좌번호, 잔고 등 민감 정보는 "
                      "전송되지 않습니다. 여기서 동의하는 것은 '기록'까지이며, 이 결과를 멤버십 "
                      "페이지에 공개할지는 홈페이지 대시보드에서 언제든 켜고 끌 수 있습니다.",
                "en": "※ Builds your real-trading track record automatically. Only trades executed by "
                      "EdgeQuant are counted — trades you place yourself on the same account are "
                      "excluded. The app converts those fills to R units locally and sends only the "
                      "summary to the server — sensitive data such as API keys, account numbers "
                      "and balances are never transmitted. This consent covers recording only; "
                      "whether to publish it on the membership page is a toggle you control "
                      "anytime in your dashboard."},
    "sec_live": {"ko": "라이브 실행", "en": "Go Live"},
    "demo_start": {"ko": "▶ 모의 시작", "en": "▶ Start Demo"},
    "live_start": {"ko": "▶ 라이브 시작", "en": "▶ Go Live"},
    "live_stopall": {"ko": "⏹ 전체 정지", "en": "⏹ Stop all"},
    "live_1r_note": {"ko": "1R = 거래당 기본 리스크(typical risk) · 신호 확신도에 따라 최대 3R"
                           "(maximum risk)까지 — 계좌 여유는 1R의 3배로 잡으세요.",
                     "en": "1R = typical risk per trade · scales up to 3R (maximum risk) with signal "
                           "confidence — budget 3× your 1R."},
    "live_note": {"ko": "체크된 자산을 연결 테스트 후 한 번에 시작합니다(하나라도 실패하면 시작 안 함). "
                        "신호의 방향·손절로 자동 진입, 세션 마감엔 자동 청산. 포지션은 종목별 독립 관리.",
                  "en": "Starts every checked asset at once after connection tests (one failure = nothing "
                        "starts). Auto-enters with the signal's direction & stop, auto-closes at session "
                        "end. Positions are managed independently per symbol."},
    "sec_auto": {"ko": "자산별 자동 청산 (세션 마감 자동)", "en": "Per-asset auto-close (at session close)"},
    "auto_sched": {"ko": "청산 시각: NQ 14:00 ET · GC 06:00 ET · BTC 02:00 UTC (자동)",
                   "en": "Close times: NQ 14:00 ET · GC 06:00 ET · BTC 02:00 UTC (auto)"},
    "auto_live": {"ko": "실제 청산 실행 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "auto_start": {"ko": "자동 청산 시작", "en": "Start auto-close"},
    "auto_stop": {"ko": "자동 청산 중지", "en": "Stop auto-close"},
    "auto_on_ind": {"ko": "  ● 자동 청산 ON  ", "en": "  ● Auto-close ON  "},
    "auto_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sec_sig": {"ko": "자산별 자동 진입 (실시간 신호)", "en": "Per-asset auto-entry (live signal)"},
    "sig_1r": {"ko": "1R ($)", "en": "1R ($)"},
    "sig_live": {"ko": "실제 진입 (체크 안 하면 모의)", "en": "Run LIVE (unchecked = dry-run)"},
    "sig_start": {"ko": "신호 대기 시작", "en": "Start signal watch"},
    "sig_stop": {"ko": "신호 대기 중지", "en": "Stop signal watch"},
    "sig_on_ind": {"ko": "  ● 신호 대기 ON  ", "en": "  ● Watching ON  "},
    "sig_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sig_note": {"ko": "※ 신호의 방향·손절가로 자동 진입하고, 계약 수는 위 1R($ 리스크)로 앱이 자동 계산합니다 "
                       "(신호에 계약 수 없음). ⚠️ EdgeQuant는 신호 확신도에 따라 포지션을 키워 "
                       "거래당 최대 3R까지 리스크를 감수합니다 — 계좌 여유는 1R의 3배 기준으로 잡으세요. "
                       "자산은 신호의 종목으로 자동 판별(NQ→MNQ·GC→MGC·BTC→BTCUSDT.P). "
                       "'사용 계좌'만 고르면 됩니다. 포지션은 종목별로 독립 관리됩니다 — 같은 종목은 기존 "
                       "포지션이 완전히 청산된 것이 확인된 후에만 새로 진입하여 중복 포지션을 방지하고, 다른 "
                       "종목은 서로 영향을 주지 않으므로 NQ와 GC도 같은 계좌에서 동시에 독립 운용할 수 있습니다.",
                 "en": "※ Enters automatically using the signal's direction and stop; the contract count is computed "
                       "by the app from your 1R above (the signal carries no contract count). ⚠️ EdgeQuant scales "
                       "position size with signal confidence — up to 3R risk per trade; budget your account for "
                       "3× your 1R. The instrument is detected from the signal (NQ→MNQ · GC→MGC · BTC→BTCUSDT.P). "
                       "Just pick the account. Positions are managed independently per symbol — the same "
                       "symbol re-enters only after the previous position is confirmed fully closed (no doubling), "
                       "and different symbols never affect each other, so NQ and GC can run side by side on one "
                       "account."},
    "auto_note": {"ko": "※ 앱이 떠 있고 컴퓨터가 켜져(절전 해제) 있어야 작동. 설정된 각 자산의 세션 마감 시각에 그 자산 브로커를 청산합니다.",
                  "en": "※ App must stay open and the computer awake. Each configured asset's positions are flattened at its session close."},
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


def _kc_del(account):
    """비밀 삭제(PIN 재설정용). 없는 항목이어도 조용히 통과."""
    if _IS_MAC:
        try:
            subprocess.run(["/usr/bin/security", "delete-generic-password", "-a", account or "default",
                            "-s", KC_SERVICE], capture_output=True, timeout=8)
        except Exception:
            pass
        return
    try:
        import keyring
        keyring.delete_password(KC_SERVICE, account or "default")
    except Exception:
        pass


def _pin_hash():
    return _kc_load("__eqpin__")


def _pin_set(pin):
    _kc_save("__eqpin__", hashlib.sha256(pin.encode()).hexdigest())


def _pin_ok(pin):
    h = _pin_hash()
    return bool(h) and hashlib.sha256(pin.encode()).hexdigest() == h


# Topstep funded는 잔고가 $0에서 시작(명목 150K는 트레일링 드로다운 기준일 뿐, balance는
# 이익만 0부터 적립) → '방패'는 잔고 그 자체다(start_bal 빼기 없음, 대표 2026-07-26 실계좌 확인).
# payouts = 이 계좌의 지금까지 출금 횟수(0~5). 빅실드 정본(대표 2026-08-02 채택):
#   펀디드 1R = 전 구간 $300 고정. 출금은 두 단계(계정 합산 기준, 전제="합산 3발 전엔 재량
#   Live 전환 없음"):
#   ① 방패기(합산 1~3발): 잔고에 방패 $6,000 상시 유지 — $12,000 도달 시마다 $6,000 출금
#   ② Fast-Payout기(합산 4~5발, 라이브 초대 전): 방패 해제 — 자격($150+ 익절 5일·직전 출금 후
#      순익+)이 차는 순간 잔고의 절반을 즉시 출금(회당 $6,000 한도)
#   회전(합산 5발): 라이브 전환 — 새 계정으로 재시작(잔여는 Live 이월이나 보수적으로 0 산입)
#   앱은 계정 합산을 직접 모르므로 계좌별 출금 횟수(권장 2계좌 기준 <2=방패기)로 근사 안내.
_PROP_DEFAULTS = {"on": False, "type": "test", "r_test": 1200.0, "r_buffer": 300.0,
                  "r_steady": 300.0, "buffer": 6000.0, "payouts": 0,
                  "last_bal": None}   # 직전 관측 잔고 - 출금 자동 감지용(2026-08-02)
_PCT_DEFAULTS = {"on": False, "pct": 0.4, "floor": 200.0}   # 자본 비례 모드(대표 2026-07-26 #18)
_PAYOUT_CHUNK = 6000.0   # Topstep 회당 출금 단위(DLL 계좌 $6,000) — 방패+이 값 도달 시 출금 권장 팝업


def _new_acct(one_r=600.0, acct_id="", on=True, label="", prop=None, pct=None, manual=False,
              broker=""):
    p = dict(_PROP_DEFAULTS)
    if isinstance(prop, dict):
        p.update({k: prop[k] for k in _PROP_DEFAULTS if k in prop})
        p["on"] = bool(p["on"]); p["type"] = "funded" if p["type"] == "funded" else "test"
        for k in ("r_test", "r_buffer", "r_steady", "buffer"):
            p[k] = _as_float(p[k], _PROP_DEFAULTS[k])
        p["payouts"] = max(0, min(5, int(_as_float(p.get("payouts"), 0))))
        # 챔피언 마이그레이션(2026-07-27): 구 기본값 그대로인 계좌만 신챔피언으로 자동 이행
        # (900/300/600·9000 및 1200/300/450·3000 → 1200/300/300·방패 6000). 커스텀 값은 불변.
        if (p["r_test"], p["r_buffer"], p["r_steady"], p["buffer"]) in (
                (900.0, 300.0, 600.0, 9000.0), (1200.0, 300.0, 450.0, 3000.0)):
            p["r_test"], p["r_steady"], p["buffer"] = 1200.0, 300.0, 6000.0
    pc = dict(_PCT_DEFAULTS)
    if isinstance(pct, dict):
        pc["on"] = bool(pct.get("on"))
        pc["pct"] = _as_float(pct.get("pct"), _PCT_DEFAULTS["pct"])
        pc["floor"] = _as_float(pct.get("floor"), _PCT_DEFAULTS["floor"])
    # broker: 계좌별 브로커(대표 2026-08-09 "계좌 추가에서 브로커 선택" — BTC Bybit+Bitget
    # 동시 발주). ""=자산 기본 브로커 사용(_acct_broker가 해석) — 구 설정 무변경 호환.
    return {"id": str(acct_id or ""), "one_r": float(one_r or 600), "on": bool(on),
            "label": str(label or ""), "prop": p, "pct": pc, "manual": bool(manual),
            "broker": str(broker or "")}


def _as_float(v, default=0.0):
    """관대한 float 파싱(콤마 허용). 실패 시 default — 위젯/설정값 방어."""
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _load():
    """자산별 설정 로드(대표 2026-07-24). 스키마 assets{자산:{broker, creds{broker:{f1,f3}},
    include, accounts:[{id,one_r,on,label}]}}. **계좌는 자산별**(자산마다 브로커·계좌 독립),
    계좌당 1R 절대$. 마이그레이션 3종: ①옛 assets(단일 acct→계좌 1개) ②2026-07-24 초기 브로커중심
    (asset_broker/accounts{broker}) ③flat 키. 비밀(f2)은 브로커 f1을 키로 Keychain async 로드."""
    try:
        import yaml
        with open(CFG_PATH) as f:
            d = yaml.safe_load(f) or {}
    except Exception:
        d = {}
    out = {"lang": d.get("lang", "ko"), "token": d.get("token", "")}
    assets_raw = d.get("assets") or {}
    _has_accounts = any(isinstance(v, dict) and "accounts" in v for v in assets_raw.values())
    _broker_centric = (not _has_accounts) and (d.get("asset_broker") is not None
                                               or isinstance(d.get("accounts"), dict))
    acfg = {}
    for a in _ASSETS:
        brs = _ASSET_BROKERS[a]
        if _broker_centric:
            # ② 초기 브로커중심 → 자산별로 재조립
            ab = d.get("asset_broker") or {}
            bk = ab.get(a) if ab.get(a) in brs else brs[0]
            creds_all = d.get("creds") or {}
            creds = {b: {"f1": (creds_all.get(b) or {}).get("f1", ""),
                         "f3": (creds_all.get(b) or {}).get("f3", "")}
                     for b in brs if b in creds_all}
            accounts = [_new_acct(x.get("one_r"), x.get("id"), x.get("on", True), x.get("label"),
                                  x.get("prop"), x.get("pct"), x.get("manual", False), bk)
                        for x in ((d.get("accounts") or {}).get(bk) or [])]
            include = bool((d.get("asset_on") or {}).get(a, True))
        else:
            s = assets_raw.get(a) or {}
            bk = s.get("broker") if s.get("broker") in brs else brs[0]
            creds = {b: {"f1": cr.get("f1", ""), "f3": cr.get("f3", "")}
                     for b, cr in (s.get("creds") or {}).items() if b in brs}
            include = bool(s.get("include", True))
            if s.get("accounts"):                      # ③ 새 자산중심(현행)
                accounts = [_new_acct(x.get("one_r"), x.get("id"), x.get("on", True), x.get("label"),
                                      x.get("prop"), x.get("pct"), x.get("manual", False),
                                      x.get("broker", ""))
                            for x in s["accounts"]]
            else:                                      # ① 옛 assets: 단일 acct → 계좌 1개
                one_r = float(s.get("one_r", 600) or 600)
                _crbk = (s.get("creds") or {}).get(bk) or {}
                acct_id = (_crbk.get("acct") or s.get("acct") or "").strip()
                accounts = [_new_acct(one_r, acct_id, True,
                                      acct_id[-4:] if acct_id else _broker_label(bk))]
        if not accounts:
            accounts = [_new_acct(600.0, "", True, _broker_label(bk))]
        creds.setdefault(bk, {"f1": "", "f3": ""})
        acfg[a] = {"broker": bk, "creds": creds, "include": include, "accounts": accounts}
    # flat(단일) 키 — 자산 설정 전무할 때만
    if not assets_raw and not _broker_centric and (d.get("f1") or "").strip():
        fb = d.get("broker", "projectx")
        for a in _ASSETS:
            if fb in _ASSET_BROKERS[a]:
                acfg[a]["broker"] = fb
                acfg[a]["creds"][fb] = {"f1": d.get("f1", ""), "f3": d.get("f3", "")}
                facct = ((d.get("projectx", {}) or {}).get("accounts") or [""])[0]
                acfg[a]["accounts"] = [_new_acct(d.get("one_r", 600), facct, True, _broker_label(fb))]
                break
    out["assets"] = acfg
    out["dry_run"] = bool(d.get("dry_run", True))
    if "cfg_open" in d:
        out["cfg_open"] = bool(d.get("cfg_open"))
    _p = d.get("profile") or {}
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


# ── 실제 1R 원장 (대표 2026-07-26 A안) — 트랙레코드 R 환산 정확화 ─────────────
# 계좌·시점마다 1R이 달라지는 사이징(프롭 페이즈·자본 비례)에서 고정 1R 나눗셈은 R을
# 왜곡한다. 발주 순간의 '실제 1R'을 (자산|계좌)별 시계열로 남겨 두고, 트랙레코드 수집 때
# 체결 시각 직전 진입의 1R로 나눈다(시각 매칭 — BTC 세션 날짜 경계 문제도 우회).
_RLEDGER_PATH = os.path.join(APP_DIR, ".r_ledger.json")


def _record_real_r(asset: str, acct: str, one_r: float) -> None:
    """실제 진입 1R 기록: {asset|acct: [[entry_epoch, one_r], ...]} (계좌당 최근 500건 유지)."""
    import time as _t
    import json as _json
    try:
        with open(_RLEDGER_PATH, encoding="utf-8") as f:
            d = _json.load(f)
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    k = f"{asset}|{acct or ''}"
    d.setdefault(k, []).append([_t.time(), float(one_r)])
    d[k] = d[k][-500:]
    try:
        with open(_RLEDGER_PATH, "w", encoding="utf-8") as f:
            _json.dump(d, f)
    except Exception:
        pass


def _lookup_real_r(ledger: dict, asset: str, acct: str, fill_ts: float, fallback: float) -> float:
    """체결 시각(fill_ts, epoch) 직전(≤)의 그 (자산|계좌) 진입 1R. 없으면 fallback."""
    rows = (ledger or {}).get(f"{asset}|{acct or ''}") or []
    best_ts, best_r = -1.0, None
    for ts, r in rows:
        if ts <= fill_ts + 60 and ts > best_ts:      # +60s: 진입-첫체결 미세 지연 허용
            best_ts, best_r = ts, r
    return float(best_r) if best_r is not None else float(fallback)


def _lookup_baseline_r(ledger: dict, asset: str, acct: str, fallback: float) -> float:
    """그 (자산|계좌)의 '첫 진입 1R' = 개인 기준선. 상대 현금(cash_rel)의 분모.
    개인마다 달라 서로 역산·비교 불가하고, 자본이 커지면 현재 1R/기준선 배수가 커져
    상대 규모가 성장한다(대표 2026-07-26 — $600 고정 기준 금지)."""
    rows = (ledger or {}).get(f"{asset}|{acct or ''}") or []
    return float(rows[0][1]) if rows else float(fallback)


# ── EQ 거래 원장 (대표 2026-07-17) ────────────────────────────────────────────
# 브로커는 '계좌 전체' 체결을 준다 — 회원이 같은 계좌에서 손수 친 MNQ/MGC/BTC 거래가
# 트랙레코드에 섞인다. 브로커 조회 응답에는 주문 태그(customTag/orderLinkId)가 없어서
# 태그만으론 못 거른다 → 앱이 자기가 낸 진입을 여기 적고, 체결을 이 원장과 대조한다.
_LEDGER_PATH = os.path.join(APP_DIR, ".eqtrades.json")
_LEDGER_SINCE_PATH = os.path.join(APP_DIR, ".eqtrades_since")
_LEDGER_KEEP_DAYS = 400                 # 조회창(90일)보다 넉넉히 — 원장이 먼저 마르면 안 됨
# 자산별 최대 보유시간(h): 진입 1건이 커버하는 체결 창. NQ/GC=당일 세션, BTC=4h 홀드 + 여유.
_LEDGER_HOLD_H = {"NQ": 12.0, "GC": 12.0, "BTC": 6.0}
_SYM_MATCH_DAYS = 7      # EQ가 연 '그 계약'의 청산 매칭창(일) — 시간창 넘긴 수동 청산 포착(대표 2026-07-29)


def _ledger_load() -> list:
    try:
        import json as _json
        with open(_LEDGER_PATH, encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _ledger_add(asset: str, symbol: str = "", direction: str = "", tag: str = "") -> None:
    """EQ가 낸 진입 1건 기록(라이브 진입에서만 호출). 실패해도 조용히 — 진입을 막지 않는다."""
    try:
        import json as _json
        import time as _t
        d = _ledger_load()
        d.append({"ts_ms": int(_t.time() * 1000), "asset": asset, "symbol": str(symbol or ""),
                  "direction": str(direction or ""), "tag": str(tag or "")})
        cut = (_t.time() - _LEDGER_KEEP_DAYS * 86400) * 1000
        d = [r for r in d if (r.get("ts_ms") or 0) >= cut]
        with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
            _json.dump(d, f)
    except Exception:
        pass


def _ledger_since() -> float:
    """원장 필터 적용 시작 시각(ms). 이 이전 체결은 원장이 없으니 옛 방식(전부 포함) —
    필터는 '앞으로'만(대표 2026-07-17). 기존 회원의 과거 트랙레코드를 지우지 않기 위함.
    파일이 없으면 지금을 시작점으로 박제(최초 1회)."""
    try:
        with open(_LEDGER_SINCE_PATH, encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        pass
    import time as _t
    now = _t.time() * 1000
    try:
        with open(_LEDGER_SINCE_PATH, "w", encoding="utf-8") as f:
            f.write(str(now))
    except Exception:
        return 0.0                      # 기록 실패 = 필터 못 씀 → 안전하게 옛 방식
    return now


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


def _save_full(lang, token, acfg, profile=None, dry_run=None, cfg_open=None):
    """자산별 설정(acfg={자산:{broker,creds{broker:{f1,f3}},include,accounts[]}}) + lang/token/전역
    dry_run + 공개프로필을 yaml에 저장. 비밀(f2)은 여기서 안 씀 — 크레덴셜 저장 시 Keychain에 이미 넣음."""
    try:
        import yaml
        payload = {"live": False, "lang": lang, "token": token,
                   "assets": {a: {"broker": c.get("broker"),
                                  "creds": {b: dict(v) for b, v in (c.get("creds") or {}).items()},
                                  "include": bool(c.get("include", True)),
                                  "accounts": [dict(x) for x in (c.get("accounts") or [])]}
                              for a, c in (acfg or {}).items()}}
        if dry_run is not None:
            payload["dry_run"] = bool(dry_run)
        if cfg_open is not None:
            payload["cfg_open"] = bool(cfg_open)
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
        # ── 예외 자동 리포트(대표 2026-07-27 "필수") — 회원 머신의 미처리 예외를 서버(/eqerr)로.
        #    키·계좌번호·잔고 무전송(마스킹), EQ_ERR_REPORT=0 으로 끔. 실패해도 앱 무사.
        self._err_sent = {}
        try:
            import threading as _th0
            root.report_callback_exception = (
                lambda et, ev, tb: self._report_error("tk", ev))
            _th0.excepthook = (
                lambda a: self._report_error(f"thread:{getattr(a.thread, 'name', '?')}", a.exc_value))
        except Exception:
            pass
        # 기본 창 크기 — 섹션이 늘어(트랙레코드·자산탭·진입정보) 잘리지 않게 확대(대표 2026-07-12).
        # 화면이 그보다 작으면 화면 높이에 맞춤.
        try:
            _h = min(1010, root.winfo_screenheight() - 60)
        except Exception:
            _h = 1010
        root.geometry(f"860x{_h}")
        root.minsize(760, 700)
        self.q = queue.Queue()
        self.lang = _load()["lang"]
        self.frm = None
        self._auto_on = False
        self._sig_on = False
        # 계좌별 무장 상태(대표 2026-07-24 자산별 계좌): 자동청산/자동진입은 (자산,계좌idx)마다
        # 독립. 탭 전환은 보기 전환일 뿐 무장을 안 바꾼다. _*_on은 '루프 살아있음' 플래그.
        self._auto_accts = {}   # {(asset,idx): [job,...]}  자동청산
        self._sig_accts = {}    # {(asset,idx): cfg}        신호대기 진입
        self._unlocked = False
        self._connected = False          # 연결 테스트 통과 전엔 실행 버튼 비활성 (현재 탭 기준)
        self._conn_by_broker = {}        # 브로커 연결테스트 통과 기억(크레덴셜 단위) — 탭 전환 무영향
        _d0 = _load()
        # 계좌 중심 설정(대표 2026-07-24 멀티계좌 · 계좌당 1R · 자산 등가중)
        # 자산별 설정(대표 2026-07-24): 계좌는 자산 탭 안에서 관리(자산마다 브로커·계좌 독립).
        self._acfg = _d0["assets"]       # {자산:{broker,creds{broker:{f1,f3}},include,accounts[]}}
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
        threading.Thread(target=self._build_watch, daemon=True).start()   # 빌드 교체 감지(#31)
        self._precheck_done = {}                                    # {(asset, entry_iso): True}
        root.after(60 * 1000, self._precheck_tick)                  # 진입 1시간 전 API 사전 점검

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
        if getattr(self, "_scroll_host", None) is not None:
            self._scroll_host.destroy()
        elif self.frm is not None:
            self.frm.destroy()
        # 브로커별로 만들어지는 위젯 속성 정리(파괴된 위젯의 stale 참조 방지).
        for _a in ("key", "f3", "scope", "b_lock"):
            if hasattr(self, _a):
                delattr(self, _a)
        # 본문을 세로 스크롤 캔버스로 감싼다 — 노트북/저해상도에서 아래 섹션이 짤리던 문제
        # (테스터 리포트 2026-07-12). 창을 줄여도 스크롤로 전부 접근 가능.
        host = ttk.Frame(self.root); host.pack(fill="both", expand=True)
        self._scroll_host = host
        _cv = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        _sb = ttk.Scrollbar(host, orient="vertical", command=_cv.yview)
        _cv.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        _cv.pack(side="left", fill="both", expand=True)
        frm = ttk.Frame(_cv, padding=14)
        _win = _cv.create_window((0, 0), window=frm, anchor="nw")
        # 내용이 뷰포트보다 짧으면(설정 접힘 등) 안쪽 프레임을 뷰포트 높이로 늘려
        # 로그 창(expand=True)이 남는 공간을 전부 먹게 한다(대표 2026-07-13).
        def _fit_canvas(_e=None):
            try:
                _need = max(frm.winfo_reqheight(), _cv.winfo_height())
                if _cv.itemcget(_win, "height") != str(_need):
                    _cv.itemconfigure(_win, height=_need)
                _cv.configure(scrollregion=_cv.bbox("all"))
            except Exception:
                pass
        frm.bind("<Configure>", _fit_canvas)
        _cv.bind("<Configure>", lambda e: (_cv.itemconfigure(_win, width=e.width),
                                           _fit_canvas()))

        def _wheel(e):
            _cv.yview_scroll(-1 * (e.delta if sys.platform == "darwin"
                                   else int(e.delta / 120)), "units")
        _cv.bind_all("<MouseWheel>", _wheel)
        _cv.bind_all("<Button-4>", lambda e: _cv.yview_scroll(-1, "units"))   # 리눅스
        _cv.bind_all("<Button-5>", lambda e: _cv.yview_scroll(1, "units"))
        self.frm = frm

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

        # ── 라이브 패널 — 자산별 세팅 후 한방 실행(대표 2026-07-24 자산별 계좌) ──────────
        #   계좌·계좌별 1R은 각 **자산 탭 브로커 설정**에서 관리(자산마다 브로커·계좌 독립).
        #   여기선 자산별 실행 여부·상태·연결테스트·정지만. 시그널 1건 → 그 자산 켜진 계좌 전부 진입.
        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_live"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        # 동의(전 자산 공통) — 실행 동작 전 필요, 탭 재빌드에도 상태 유지
        if not hasattr(self, "consent"):
            self.consent = tk.IntVar()
        ttk.Checkbutton(frm, variable=self.consent, text=self.t("consent"),
                        command=self._update_tr_status).pack(anchor="w", pady=(2, 2))
        # 자산별 실행 행 — [자산] 상태 [연결테스트] [정지] ●
        self._live_include = {}
        self._live_rows = {}       # {asset: (status_lbl, dot)}
        lv = ttk.Frame(frm); lv.pack(fill="x", pady=(3, 0))
        for _a in _ASSETS:
            row = ttk.Frame(lv); row.pack(fill="x", pady=1)
            var = tk.IntVar(value=1 if self._acfg[_a].get("include", True) else 0)
            self._live_include[_a] = var
            ttk.Checkbutton(row, text=_a, width=5, variable=var,
                            command=self._on_asset_toggle).pack(side="left")
            dot = tk.Label(row, text="●", foreground="#9ca3af", font=("Helvetica", 12, "bold"))
            dot.pack(side="right", padx=(6, 0))
            ttk.Button(row, text=("정지" if self.lang == "ko" else "Stop"), width=5,
                       command=lambda a=_a: self._panel_stop(a)).pack(side="right", padx=(4, 0))
            ttk.Button(row, text=self.t("btn_conn"), width=11,
                       command=lambda a=_a: self._panel_conn_test(a)).pack(side="right", padx=(6, 0))
            lbl = tk.Label(row, text="", anchor="w", justify="left", foreground="#888")
            lbl.pack(side="left", fill="x", expand=True, padx=(6, 0))
            self._live_rows[_a] = (lbl, dot)
        ttk.Label(frm, text=self.t("live_1r_note"), foreground="#888",
                  wraplength=760, justify="left").pack(anchor="w", pady=(2, 0))
        lc = ttk.Frame(frm); lc.pack(fill="x", pady=(4, 0))
        # 모의/라이브 = '시작 버튼 2종'으로 선택(체크박스 폐지 — 켜둔 채 잊는 함정 제거,
        # 대표 2026-07-22 실사고: 모의 상태로 신호 캡처 → 실진입 놓침). live_dry는 내부 상태.
        self.live_dry = tk.IntVar(value=1)
        self.b_live_start = ttk.Button(lc, text=self.t("live_start"),
                                       command=lambda: self._master_start(dry=False))
        self.b_live_start.pack(side="left")
        self.b_demo_start = ttk.Button(lc, text=self.t("demo_start"),
                                       command=lambda: self._master_start(dry=True))
        self.b_demo_start.pack(side="left", padx=(8, 0))
        self.b_live_stop = ttk.Button(lc, text=self.t("live_stopall"), command=self._master_stop)
        self.b_live_stop.pack(side="left", padx=(6, 0))
        # 모든 자산·계좌 포지션 즉시 시장가 청산 — 패닉 버튼(대표 2026-07-27). 무장 해제(전체
        # 정지)와 별개로, 지금 열려 있는 포지션 자체를 정리한다. 확인 대화 후 실행.
        ttk.Button(lc, text=("모든 포지션 청산" if self.lang == "ko" else "Close all positions"),
                   command=self._close_all_positions).pack(side="left", padx=(6, 0))
        # 서버 신호 없이 전 자산 전 계좌 잔고·1R을 한 번에 확인(대표 2026-07-26).
        ttk.Button(lc, text=("전 자산 1R 조회" if self.lang == "ko" else "Preview 1R (all)"),
                   command=self._preview_one_r_all).pack(side="left", padx=(6, 0))
        ttk.Label(frm, text=self.t("live_note"), foreground="#888", wraplength=760,
                  justify="left").pack(anchor="w", pady=(2, 0))
        self._refresh_live_panel()

        # ── 공개 트랙레코드 (Autopilot 전용) — 로컬 계산 요약만 서버로 푸시(키·잔고 무접촉) ──
        ttk.Separator(frm).pack(fill="x", pady=8)
        ttk.Label(frm, text=self.t("sec_tr"), font=("Helvetica", 12, "bold")).pack(anchor="w")
        # 핸들·이름 입력 제거(대표 2026-07-11) — 서버가 회원 계정(텔레그램/디스코드)에서 자동 설정.
        tr = ttk.Frame(frm); tr.pack(fill="x", pady=3)
        _prof = self._profile
        self.tr_public = tk.IntVar(value=1 if _prof.get("public") else 0)
        # 토글 즉시 _profile 반영+영속 — 저장 없이 탭 바꾸면 옛 값으로 되살아나던 버그(대표 2026-07-12)
        self.cb_tr_public = ttk.Checkbutton(tr, text=self.t("tr_public"), variable=self.tr_public,
                                            command=self._on_tr_public)
        self.cb_tr_public.pack(side="left", padx=(0, 10))
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
        # ── 자산별 브로커 설정 — 접이식(대표 2026-07-13: 한 번 셋업하면 열 일이 드묾).
        #    기본: 미설정 자산이 있으면 펼침, 전부 설정돼 있으면 접힘. 상태는 세션 유지+영속.
        if not hasattr(self, "_cfg_open"):
            _unset = any(not (self._creds_of(_a).get("f1") or "").strip()
                         for _a in _ASSETS if self._acfg[_a].get("include", True))
            self._cfg_open = bool(d.get("cfg_open", _unset))
        self._cfg_hdr = tk.Button(
            frm, text=("▾ " if self._cfg_open else "▸ ")
            + ("자산별 브로커 설정 (NQ·GC·BTC)" if self.lang == "ko"
               else "Per-asset broker setup (NQ·GC·BTC)"),
            command=self._toggle_cfg, relief="flat", anchor="w",
            font=("Helvetica", 12, "bold"), padx=0)
        self._cfg_hdr.pack(fill="x", anchor="w")
        self._cfg_body = ttk.Frame(frm)
        if self._cfg_open:
            self._cfg_body.pack(fill="x")
        _outer_frm = frm
        frm = self._cfg_body                      # 아래 설정 위젯들은 접이식 본문으로

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
            # '보기' 체크박스 폐지(대표 2026-08-09) — 키는 열람 불가·교체만 가능(write-only).
            # 덕분에 PIN 재설정 시 키를 지킬 필요가 없어졌다(_pin_forgot 코드 인증 참조).
        # f3 (추가 필드) — 있는 브로커만
        if spec.get("f3"):
            r3e = ttk.Frame(frm); r3e.pack(fill="x", pady=3)
            ttk.Label(r3e, text=spec["f3"], width=18).pack(side="left")
            _mask = "•" if ("Secret" in spec["f3"] or "Passphrase" in spec["f3"]) else ""
            self.f3 = ttk.Entry(r3e, show=_mask); self.f3.pack(side="left", fill="x", expand=True)
            self.f3.insert(0, c.get("f3", ""))
            ttk.Button(r3e, text=self.t("paste"), width=8,
                       command=lambda: self._paste_into(self.f3)).pack(side="left", padx=(4, 0))
        # ── 계좌 관리(대표 2026-07-24 자산별 계좌) — 이 자산(self._asset)의 등록 계좌 리스트.
        #   계좌마다 [실행 on][라벨][1R $][삭제] — 값은 FocusOut/토글 시 이 자산 accounts에 즉시
        #   저장(_save_acct_widgets). 여러 펀디드/챌린지 계좌를 각자 1R로 동시에 굴린다.
        #   b_acc(계좌 목록 불러오기)는 항상 생성 — 게이팅(_action_btns)이 참조. 크립토는 계좌
        #   개념이 없어(API키=계좌) 리스트/추가/불러오기 UI를 숨기고 계좌 1개(id="")만 두되
        #   1R 입력은 보여준다(사이징).
        self.b_acc = ttk.Button(frm, text=self.t("btn_accts"), command=self.healthcheck)
        self._acct_widgets = {}                     # {idx: {on,label,one_r}} — 현재 자산 계좌 위젯
        _accts = self._accts_of(self._asset)
        if spec.get("acct"):
            ttk.Label(frm, text=("등록 계좌 — 계좌별 1R·실행 여부 (라이브 패널에서 자산 단위로 시작)"
                                 if self.lang == "ko"
                                 else "Registered accounts — per-account 1R & on/off"),
                      foreground="#555", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=(6, 1))
            for _i, _ac in enumerate(_accts):
                self._acct_edit_row(frm, _i, _ac, spec, deletable=True, show_on=True)
            addr = ttk.Frame(frm); addr.pack(fill="x", pady=(3, 2))
            self.acct_pick = ttk.Combobox(addr, values=[], state="normal")
            self.acct_pick.pack(side="left", fill="x", expand=True)
            ttk.Button(addr, text=("계좌 추가" if self.lang == "ko" else "Add"), width=8,
                       command=self._add_acct).pack(side="left", padx=(4, 0))
            self.b_acc.pack(in_=addr, side="left", padx=(4, 0))
            # Topstep 계좌 목록을 백그라운드로 자동 로드 → '계좌 추가' 콤보를 미리 채운다
            # (대표 2026-07-26: 연결테스트를 따로 안 눌러도 목록이 바로 떠서 계좌를 한 번에 추가).
            if self._broker_of(self._asset) == "projectx":
                self._autoload_topstep_scope(self._asset)
            ttk.Label(frm, text=self.t("scope_note"), foreground="#888").pack(anchor="w")
            # 선물 자산 간 계좌 설정 복사(대표 2026-07-26 #20) — NQ·GC는 같은 프롭 계좌로 운용
            _srcs = [a for a in _ASSETS if a != self._asset
                     and _BROKER_SPEC.get(self._broker_of(a), {}).get("acct")]
            if _srcs:
                cprow = ttk.Frame(frm); cprow.pack(fill="x", pady=(2, 2))
                ttk.Label(cprow, text=("계좌 설정 복사 ←" if self.lang == "ko"
                                       else "Copy account setup ←"),
                          foreground="#888").pack(side="left")
                for _src in _srcs:
                    ttk.Button(cprow, text=_src, width=5,
                               command=lambda a=_src: self._copy_acct_setup(a)
                               ).pack(side="left", padx=(4, 0))
        else:
            # 크립토(Bybit/Bitget) — 계좌ID 개념 없음(API키=계좌). 계좌 리스트 개방(대표
            # 2026-08-09 "계좌 추가 이런 식으로 모든 자산"): 행마다 브로커 선택 + 1R + on/off
            # → 같은 자산을 Bybit·Bitget 동시 발주(각자 1R). 키는 위 브로커 콤보로 전환해
            # 브로커별로 등록해 두면 계좌 행이 자기 브로커 키를 쓴다.
            ttk.Label(frm, text=("등록 계좌 — 거래소별 1R·실행 여부 (동시 발주 가능)"
                                 if self.lang == "ko"
                                 else "Registered accounts — per-exchange 1R & on/off"),
                      foreground="#555", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=(6, 1))
            for _i, _ac in enumerate(_accts):
                self._acct_edit_row(frm, _i, _ac, spec,
                                    deletable=len(_accts) > 1, show_on=len(_accts) > 1)
            _addr = ttk.Frame(frm); _addr.pack(fill="x", pady=(3, 2))
            ttk.Button(_addr, text=("계좌 추가" if self.lang == "ko" else "Add"), width=8,
                       command=self._add_acct).pack(side="left")
        # 계좌 정보 저장 버튼(대표 2026-08-09 "저장하기 버튼 있음 좋겠어") — 키·설정을 즉시
        # 영속(+Keychain)하고 현재 브로커 연결 테스트까지. 탭 전환 저장에만 의존하지 않게.
        _svrow = ttk.Frame(frm); _svrow.pack(fill="x", pady=(6, 2))
        ttk.Button(_svrow, text=("계좌 정보 저장 + 연결 테스트 + 1R 확인" if self.lang == "ko"
                                 else "Save + test + check 1R"),
                   command=self._save_creds_and_test).pack(side="left")
        # 서버 신호 없이 '지금 이 자산 전 계좌의 1R($)'만 잔고 조회로 미리 보여준다(대표 2026-07-26).
        ttk.Button(frm, text=("이 자산 전 계좌 1R 확인" if self.lang == "ko"
                              else "Preview 1R — all accounts"),
                   command=lambda a=self._asset: self._preview_one_r(a)
                   ).pack(anchor="w", pady=(4, 1))
        ttk.Label(frm, text=self.t("conn_first"), foreground="#888").pack(anchor="w")
        frm = _outer_frm                          # 접이식 본문 끝 — 이후(로그)는 바깥에

        # 자동 청산·자동 진입 섹션은 라이브 패널로 통합(대표 2026-07-13) — 개별 정지도 패널 줄에서.
        ttk.Separator(frm).pack(fill="x", pady=8)
        self.out = scrolledtext.ScrolledText(frm, height=10, font=("Menlo", 11), wrap="word")
        self.out.pack(fill="both", expand=True, pady=(6, 0))

        # 연결 테스트 통과 전엔 비활성화할 '실행' 버튼들. 계좌 버튼(연결 테스트 겸)은 항상 활성.
        self._action_btns = [self.b_acc, self.b_tr]
        self._apply_gating()
        # 안내 로그: 첫 실행만 '준비됨…', 이후(탭·브로커 전환 재빌드)는 그 자산의 실제 연결
        # 상태를 찍는다 — '준비됨'이 매번 떠서 리셋된 걸로 오해되던 것 수정(대표 2026-07-12).
        if not getattr(self, "_built_once", False):
            self._built_once = True
            self.log(self.t("ready"))
        else:
            _ok = self._conn_by_broker.get(self._broker_name)
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
        # 계좌 버튼은 연결 테스트 겸용이라 항상 활성(Topstep) — 'conn and'를 걸면 연결해야
        # 계좌 버튼이 켜지는데 연결이 곧 이 버튼이라 신규 설정이 영영 못 나감(테스터 실사고
        # 2026-07-14 GC 데드락). 자격 미입력은 healthcheck 초입 _creds_ok()가 안내.
        en(self.b_acc, topstep)
        # 루프 '정지'는 어느 탭에서든 항상 가능해야 함(미연결 탭에서 버튼이 죽으면 돌던 루프를
        # 못 세움 — 대표 2026-07-12). 시작 조건은 기존대로(연결+권한), 도는 중엔 무조건 활성.


        # 자동 진입 = 전 브로커(선물 place_entry + 크립토 place_entry, 2026-07-11 크립토 제한 해제).
        # 신호 대기 무장 = '연결 테스트 통과 자산만'(2026-07-11) — 현재 탭 브로커 종류와는 무관.

        # 공개 트랙레코드 푸시 = Autopilot 등급 자격(서버 entitled와 동일 기준: 마스터 스위치 무관,
        # 주문 실행이 아니라 본인 성과 공개라서). 토큰 유효 + royal/admin이면 활성.
        _tr_ok = bool(g.get("ok")) and g.get("tier") in ("royal", "admin")
        if hasattr(self, "b_tr"):
            en(self.b_tr, _tr_ok)
        # 공개 동의도 Autopilot 전용(대표 2026-07-13) — Operator 이하는 해제+비활성.
        # 표시만 끄고 저장값(_profile.public)은 안 건드림 → 권한 확인·복귀 시 저장된 동의로
        # 복원 = "한 번 동의하면 앱 켤 때마다 동의 유지"(대표).
        if hasattr(self, "cb_tr_public"):
            try:
                if not _tr_ok:
                    self.tr_public.set(0)
                    self.cb_tr_public.config(state="disabled")
                else:
                    self.tr_public.set(1 if (self._profile or {}).get("public") else 0)
                    self.cb_tr_public.config(state="normal")
            except Exception:
                pass
        if hasattr(self, "tr_ind"):
            self._update_tr_status()
        # 시작 버튼 2종(체크박스 폐지): 라이브 = 권한 + 서버 모의강제 아님 · 모의 = 권한만
        if hasattr(self, "b_live_start"):
            _pu = bool(g.get("ok") and g.get("enabled") and caps.get("use"))
            _pa = bool(g.get("ok") and g.get("enabled") and caps.get("autoentry"))
            en(self.b_live_start, (_pu or _pa) and live_ok)
            if hasattr(self, "b_demo_start"):
                en(self.b_demo_start, _pu or _pa)
        self._refresh_live_panel()
        # fail-closed: 돌던 루프가 '권한'을 잃으면(토큰 변경·강등·만료·마스터 OFF) 자동 중지한다.
        # 버튼만 끄면 이미 도는 스레드가 계속 진입/청산하는 구멍이 생긴다.
        # ⚠️ 판정은 하트비트 권한만 — 연결(_connected)은 '현재 탭' 상태라 여기 섞으면
        # 탭 전환이 돌던 루프를 죽인다(대표 2026-07-11 "자산 옮기면 다 리셋" 버그).
        perm_use = bool(g.get("ok") and g.get("enabled") and caps.get("use"))
        perm_auto = bool(g.get("ok") and g.get("enabled") and caps.get("autoentry"))
        _tripped = []
        if getattr(self, "_sig_on", False) and not perm_auto:
            self._sig_on = False
            self._sig_accts.clear()
            self._set_sig_ind(False)
            self.log("⏹ 자동 진입 권한 상실 → 신호 대기 자동 중지 (fail-closed).")
            _tripped.append("자동 진입")
        if getattr(self, "_auto_on", False) and not perm_use:
            self._auto_on = False
            self._auto_accts.clear()
            self._set_auto_ind(False)
            self.log("⏹ 자동 청산 권한 상실 → 자동 청산 자동 중지 (fail-closed).")
            _tripped.append("자동 청산")
        # fail-closed로 무장 해제되면 조용히 방치되지 않게 큰 알림 + 재무장까지 반복 리마인드
        # (대표 2026-08-07: 8/5 사고가 2일간 조용했던 근본 원인). 명시 거부가 아닌 순단
        # 유예 소진도 여기로 들어온다 - 사용자는 '왜 진입 안 됐지'를 놓치면 안 된다.
        if _tripped:
            self._disarmed_reason = " · ".join(_tripped)
            self._notify_disarmed(first=True)
        self._update_gate_label()

    def _notify_disarmed(self, first=False):
        """자동매매 무장 해제 경보 - 데스크톱 알림 + 재무장까지 15분마다 반복(대표 2026-08-07).
        권한이 회복돼(_sig_on/_auto_on 재개 or 게이트 정상) 있으면 반복 종료."""
        g = getattr(self, "_gate", {}) or {}
        _still = not (g.get("ok") and g.get("enabled")
                      and (g.get("caps", {}) or {}).get("autoentry"))
        if not _still:
            self._disarmed_reason = None
            return                                  # 권한 회복 → 리마인드 종료
        reason = getattr(self, "_disarmed_reason", None) or "자동매매"
        msg = f"EdgeQuant: {reason}이(가) 중지됨 (fail-closed). 앱에서 다시 켜주세요(Go Live)."
        try:                                        # macOS 데스크톱 알림(로그·배너와 별개로 튐)
            import subprocess, sys as _sys
            if _sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e",
                                  f'display notification "{msg}" with title "EdgeQuant 경보" sound name "Basso"'])
        except Exception:
            pass
        if first:
            try:
                from tkinter import messagebox as _mb
                self.root.after(100, lambda: _mb.showwarning("EdgeQuant 경보", msg))
            except Exception:
                pass
        try:                                        # 15분마다 재알림(재무장 전까지)
            self.root.after(15 * 60 * 1000, lambda: self._notify_disarmed(first=False))
        except Exception:
            pass

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
            import time as _t
            gate = {"ok": False, "tier": "—", "enabled": False, "force_dry_run": True,
                    "caps": {"use": False, "manualentry": False, "autoentry": False},
                    "brokers": {}, "reason": "no token"}
            _net_fail = False                               # 네트워크성 실패(서버 무응답/5xx/타임아웃)
            if tok:
                import requests
                import autopilot_crypto
                for attempt in (1, 2):                      # 1회 재시도(총 2회)
                    try:
                        r = requests.get(_hb_url(tok), params={"t": int(_t.time())}, timeout=15)
                        try:
                            hb = autopilot_crypto.decrypt(tok, r.text) if r.ok else {}
                        except Exception:
                            hb = {"ok": False, "reason": "decrypt failed"}
                        if not r.ok:                        # 5xx/일시 응답불량 → 재시도 후 네트워크성
                            if attempt == 1:
                                _t.sleep(2); continue
                            _net_fail = True
                            gate["reason"] = f"server {r.status_code}"
                        elif not hb.get("ok"):
                            gate["reason"] = hb.get("reason") or "locked"     # 명시 거부 = 즉시 잠금
                        elif hb.get("exp") and _t.time() > hb["exp"]:
                            gate["reason"] = "expired"                        # 명시 만료 = 즉시 잠금
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
                        else:
                            _net_fail = True
            # ── 네트워크 유예(대표 2026-07-14: 서버 순단·과부하로 가동이 죽던 것): 서버에
            # '닿지 못한' 실패는 최근 15분 내 정상 권한을 유지한 채 경고만. 서버가 명시적으로
            # 거부(locked/expired/decrypt)한 경우는 유예 없이 즉시 fail-closed(위에서 처리). ──
            if gate.get("ok"):
                self._gate_good, self._gate_good_ts = dict(gate), _t.time()
            elif _net_fail and getattr(self, "_gate_good", None) and                     (_t.time() - getattr(self, "_gate_good_ts", 0)) < HB_GRACE_MIN * 60:
                _age = int((_t.time() - self._gate_good_ts) // 60)
                gate = {**self._gate_good,
                        "reason": f"네트워크 순단 — 최근 권한 유지(유예 {_age}/{HB_GRACE_MIN}분)"}
                self.log(f"⚠ 하트비트 네트워크 순단 — 마지막 정상 권한으로 {HB_GRACE_MIN - _age}분 "
                         f"유예 중 (서버가 명시 거부하면 즉시 잠금)")
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

    # ── 계좌 중심 접근 헬퍼(대표 2026-07-24 멀티계좌) ──────────────────────────
    def _broker_of(self, asset):
        return self._acfg[asset]["broker"] or _ASSET_BROKERS[asset][0]

    def _creds_of(self, asset, broker=None):
        """자산 탭의 (현재 또는 지정) 브로커 크레덴셜 {f1,f3}. 계좌는 자산별이라 크레덴셜도 자산 내."""
        bk = broker or self._acfg[asset]["broker"]
        return self._acfg[asset].setdefault("creds", {}).setdefault(bk, {"f1": "", "f3": ""})

    def _acct_broker(self, asset, acct):
        """계좌의 실행 브로커(대표 2026-08-09 계좌별 브로커 — BTC Bybit+Bitget 동시 발주).
        계좌에 broker가 없거나 이 자산에서 못 쓰는 값이면 자산 기본 브로커(구 동작)."""
        bk = (acct.get("broker") or "").strip()
        return bk if bk in _ASSET_BROKERS.get(asset, []) else self._broker_of(asset)

    def _accts_of(self, asset):
        """자산의 계좌 리스트(없으면 빈 슬롯 1개 보장)."""
        accts = self._acfg[asset].setdefault("accounts", [])
        if not accts:
            accts.append(_new_acct(600.0, "", True, _broker_label(self._acfg[asset]["broker"])))
        return accts

    def _active_accts(self, asset):
        """on=True인 계좌만(실제 진입/조회 대상)."""
        return [a for a in self._accts_of(asset) if a.get("on")]

    def _save_cfg(self, **kw):
        """_save_full 래퍼 — 현재 앱 상태를 yaml로 영속화."""
        _save_full(self.lang, self._token, self._acfg, self._profile, **kw)

    def _acur(self):
        """현재 자산 탭의 브로커 + 그 자산 브로커 크레덴셜 {broker,f1,f3}."""
        cr = self._creds_of(self._asset)
        return {"broker": self._broker_name, "f1": cr.get("f1", ""), "f3": cr.get("f3", "")}

    def _collect_acct_widgets(self):
        """현재 자산 탭 계좌 위젯값(on·라벨·1R) → self._acfg[자산]['accounts'] 반영(저장은 호출부가)."""
        accts = self._accts_of(self._asset)
        for idx, w in getattr(self, "_acct_widgets", {}).items():
            if idx >= len(accts):
                continue
            try:
                accts[idx]["on"] = bool(w["on"].get())
            except Exception:
                pass
            try:
                accts[idx]["label"] = str(w["label"].get()).strip()
            except Exception:
                pass
            _v = _as_float(w["one_r"].get(), 0.0)
            if _v > 0:
                accts[idx]["one_r"] = _v
            try:                                       # 계좌별 브로커(대표 2026-08-09)
                if w.get("broker") is not None:
                    _lbl2bk = {_broker_label(b): b for b in _ASSET_BROKERS.get(self._asset, [])}
                    _bv = _lbl2bk.get(str(w["broker"].get()).strip())
                    if _bv:
                        accts[idx]["broker"] = _bv
            except Exception:
                pass
            try:
                accts[idx]["manual"] = bool(w["manual"].get())
            except Exception:
                pass

    def _save_current_asset(self):
        """현재 탭 크레덴셜(f1,f3) → 이 자산 브로커 창고, 비밀(f2) → Keychain, 실행자산·계좌위젯
        반영·영속화. 계좌ID·1R·on·라벨은 자산 탭 계좌 위젯이 관리(자산별 accounts)."""
        bk = self._broker_name
        self._acfg[self._asset]["broker"] = bk
        cr = self._creds_of(self._asset, bk)
        if hasattr(self, "user"):
            cr["f1"] = self.user.get().strip()
        if hasattr(self, "f3"):
            cr["f3"] = self._f3()
        if hasattr(self, "key"):                 # 비밀(f2) → Keychain (f1 키로)
            _kc_save(cr.get("f1", ""), self.key.get())
        # 실행 자산(라이브 패널 체크) → 자산별 include 반영
        for _a, _v in getattr(self, "_live_include", {}).items():
            try:
                self._acfg[_a]["include"] = bool(_v.get())
            except Exception:
                pass
        self._collect_acct_widgets()             # 계좌 위젯(라벨·1R·on)
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True)
        self._refresh_live_panel()

    def _persist(self):
        self._save_current_asset()

    def _on_asset(self, asset):
        """자산 탭 전환 — 현재 탭 저장 → 자산 바꿈 → 그 자산 브로커로 → 재빌드."""
        if asset == self._asset or asset not in _ASSETS:
            return
        self._save_current_asset()
        self._asset = asset
        self._broker_name = self._broker_of(asset)
        # 탭 전환 = 보기 전환일 뿐 — 연결은 브로커별 기억 복원, 돌던 루프는 안 건드림.
        self._connected = bool(self._conn_by_broker.get(self._broker_name, False))
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
        self._acfg[self._asset]["broker"] = self._broker_name
        # 새 브로커는 연결 테스트를 다시 통과해야 함(브로커별 크레덴셜 분리).
        self._connected = bool(self._conn_by_broker.get(self._broker_name, False))
        self._unlocked = False
        self._save_cfg()
        self._build()

    def _set_lang(self, *_):
        self.lang = "en" if self.langbox.get() == "English" else "ko"
        self._persist()
        self._unlocked = False
        self._build()

    def _unlock(self):
        if self._unlocked:                                   # → re-lock
            self._unlocked = False
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
            if p is None:
                return
            if not p or not _pin_ok(p):
                self._pin_forgot(); return
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
            if pin is None:
                return
            if not pin or not _pin_ok(pin):
                self._pin_forgot(); return
        self._f1_unlocked = True
        # show는 "•" 유지 — 잠금해제 = 교체 가능일 뿐, 열람은 불가(write-only, 대표 2026-08-09)
        self.user.config(state="normal")
        self.b_lock_f1.config(text=self.t("lock"))

    def _pin_forgot(self):
        """PIN 재설정 = 가입 채널 코드 인증 (대표 2026-08-09 '멤버 텔레그램이나 디스코드로
        네자리 숫자'). 구 설계(키 전부 삭제)는 폐지 — 브로커 비밀키는 발급 시 한 번만
        보여주는 곳이 많아 재발급 강제가 가혹하다. 키 열람 기능이 없어졌으므로(교체만 가능)
        PIN만 리셋해도 키는 새지 않는다. 서버(/eqpin)가 멤버십 토큰 주인의 텔레그램/디스코드
        DM으로 4자리 코드를 보내고, 매칭되면 PIN 해시만 삭제·재설정. 키는 전부 유지.
        서버 불통·토큰 미입력 시 최후수단 = 구 삭제 방식(_pin_wipe_reset)."""
        import requests
        _ko = self.lang == "ko"
        tok = (self._token or "").strip()
        if not messagebox.askyesno(
                "PIN",
                (self.t("pin_wrong") + "\n\nPIN을 잊으셨나요? 본인 확인 후 재설정할 수 있습니다.\n\n"
                 "가입하신 텔레그램/디스코드 DM으로 4자리 확인 코드를 보내드립니다. "
                 "재설정해도 저장된 API 키는 그대로 유지됩니다. 계속할까요?") if _ko else
                (self.t("pin_wrong") + "\n\nForgot your PIN? You can reset it after verification.\n\n"
                 "We will send a 4-digit code to the Telegram/Discord account you joined with. "
                 "Your stored API keys are kept. Continue?")):
            return
        via = None
        if tok:
            try:
                r = requests.post(PUSH_BASE + "eqpin", timeout=10,
                                  json={"action": "req", "token": tok})
                t = (r.text or "").strip()
                if t.startswith("pr:sent:"):
                    via = t.split(":", 2)[2]
                elif t == "pr:err:cooldown":
                    via = "cooldown"                 # 직전 코드가 아직 유효 — 입력 단계로
            except Exception:
                pass
        if via is None:
            if messagebox.askyesno("PIN", (
                    "확인 코드를 보낼 수 없습니다 (서버 연결 실패 또는 멤버십 토큰 미입력).\n\n"
                    "최후수단으로 저장된 API 키를 모두 삭제하는 방식의 재설정을 진행할까요?") if _ko else (
                    "Could not send a verification code (server unreachable or no membership "
                    "token).\n\nProceed with the last-resort reset that deletes all stored API "
                    "keys?")):
                self._pin_wipe_reset()
            return
        if via == "admin":
            messagebox.showinfo("PIN", ("가입 채널 정보가 없는 토큰이라 코드를 설계자에게 "
                                        "보냈습니다. 전달받은 4자리 코드를 입력하세요.") if _ko else
                                       ("This token has no joined channel on file, so the code "
                                        "was sent to the architect. Enter the 4-digit code you "
                                        "receive."))
        for _ in range(3):
            code = simpledialog.askstring(
                "PIN", ("DM으로 받은 4자리 확인 코드를 입력하세요:" if _ko else
                        "Enter the 4-digit code from your DM:"), parent=self.root)
            if not code:
                return
            try:
                r = requests.post(PUSH_BASE + "eqpin", timeout=10,
                                  json={"action": "verify", "token": tok, "code": code.strip()})
                t = (r.text or "").strip()
            except Exception:
                t = "pr:err:network"
            if t == "pr:ok":
                _kc_del("__eqpin__")
                self._unlocked = False
                self._f1_unlocked = False
                _np = simpledialog.askstring("PIN", self.t("pin_new"), show="*", parent=self.root)
                if _np:
                    _pin_set(_np)
                self.log("🔐 " + ("PIN 재설정 완료 — 저장된 API 키는 그대로 유지됩니다." if _ko
                                  else "PIN reset — your stored API keys are kept."))
                return
            if t == "pr:err:bad_code":
                messagebox.showwarning("PIN", "코드가 틀립니다." if _ko else "Wrong code.")
                continue
            messagebox.showwarning("PIN", ("코드가 만료됐거나 시도 횟수를 넘겼습니다. "
                                           "처음부터 다시 시도하세요.") if _ko else
                                          ("Code expired or too many attempts. "
                                           "Start over."))
            return

    def _pin_wipe_reset(self):
        """최후수단 PIN 재설정(구 방식, 2026-07-27) — 서버 코드 인증이 불가능할 때만.
        보호 대상(키체인의 브로커 비밀키)을 함께 삭제해 잊은 PIN이 우회로가 되지 않게 한다."""
        _ko = self.lang == "ko"
        if not messagebox.askyesno(
                "PIN",
                ("⚠ 재설정하면 이 앱에 저장된 모든 API 비밀키가 삭제되며, 각 브로커 설정에서 "
                 "다시 붙여넣어야 합니다(계좌 목록·1R 등 다른 설정은 유지). 계속할까요?") if _ko else
                ("⚠ Resetting deletes every API secret stored by this app — you will need to "
                 "paste each broker key again (accounts, 1R and other settings are kept). "
                 "Continue?")):
            return
        for _a, _s in (self._acfg or {}).items():            # 등록된 브로커 비밀 전부 삭제
            for _b, _cr in (_s.get("creds") or {}).items():
                _f1 = (_cr.get("f1") or "").strip()
                if _f1:
                    _kc_del(_f1)
        _kc_del("__eqpin__")
        self._unlocked = False
        self._f1_unlocked = False
        _np = simpledialog.askstring("PIN", self.t("pin_new"), show="*", parent=self.root)
        if _np:
            _pin_set(_np)
        self.log("🔐 " + ("PIN 재설정 완료 — 저장된 API 비밀키가 삭제되었습니다. 각 브로커 "
                          "설정에서 키를 다시 입력하세요." if _ko else
                          "PIN reset — stored API secrets were deleted. Re-enter each broker key "
                          "in its settings."))

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

    def log(self, m):
        _log_to_file(m)          # 파일에도 영속(타임스탬프 부여) — 대표 2026-07-15
        self.q.put(m)

    def _drain(self):
        while not self.q.empty():
            try:
                self.out.insert("end", self.q.get() + "\n"); self.out.see("end")
            except Exception:
                pass
        self.root.after(120, self._drain)

    def _scope(self):
        # 단일 계좌 스코프 UI 폐지(멀티계좌로 이관, 대표 2026-07-24) — 연결테스트/계좌목록은
        # 계좌 없이 크레덴셜로 조회한다. 항상 빈 스코프.
        return ""

    def _broker(self):
        sc = self._scope()
        return _build_broker(self._broker_name, self.user.get().strip(), self._secret(),
                             self._f3(), [sc] if sc else [])

    def _busy(self, on):
        # 작업 중엔 계좌(연결 테스트 겸) 버튼도 잠그고, 끝나면 _apply_gating이 버튼별 조건으로
        # 복원한다. (구버전 `self._connected`로 일괄 복원 → 현재 탭 미연결이면 연결과 무관한
        # 동기화(푸시) 버튼까지 잠긴 채 다음 하트비트(5분)까지 방치 — 대표 2026-07-16 리포트.)
        try:
            self.b_acc.config(state="disabled" if on else "normal")
        except Exception:
            pass
        self._set_actions_enabled(not on)

    def _creds_ok(self, silent=False):
        # 빠진 필드를 브로커 실제 라벨로 안내 — 옛 고정문구 "이메일과 API Key"는 Bybit 등
        # 크립토(이메일 칸 없음)에서 대혼란(테스터 리포트 2026-07-12).
        # silent=True: 팝업 없이 True/False만(계좌 추가 후 자동 연결 테스트 게이트, #19).
        spec = _BROKER_SPEC.get(self._broker_name, {})
        _missing = []
        if not self.user.get().strip():
            _missing.append(spec.get("f1", "ID"))
        if spec.get("f2") and not self._secret():
            _missing.append(spec.get("f2"))
        if _missing:
            if silent:
                return False
            _f = " · ".join(_missing)
            messagebox.showwarning(
                self.t("input_needed"),
                (f"다음 항목을 입력하세요: {_f}\n"
                 "(비밀 필드는 '잠금해제' 후 '붙여넣기'로 입력합니다)") if self.lang == "ko" else
                (f"Please fill in: {_f}\n"
                 "(secret fields: press 'Unlock' first, then 'Paste')"))
            return False
        self._persist(); return True

    def _consent_ok(self):
        if not self.consent.get():
            messagebox.showwarning(self.t("need_consent"), self.t("need_consent")); return False
        return True

    def _fill_scope(self, names):
        # 불러온 계좌 목록 → '계좌 추가' 콤보 채움(대표 2026-07-24 멀티계좌)
        if not hasattr(self, "acct_pick"):
            return
        try:
            self.acct_pick["values"] = names
            if names and not self.acct_pick.get().strip():
                self.acct_pick.set(names[0])
        except Exception:
            pass

    def _autoload_topstep_scope(self, asset):
        """Topstep(projectx) 자산 탭 진입 시 브로커 계좌 목록을 백그라운드로 받아 '계좌 추가'
        콤보를 미리 채운다(대표 2026-07-26 버그: 예전엔 '연결 테스트'를 눌러야만 _fill_scope가
        호출돼 그 전엔 콤보가 비어 계좌를 못 골랐다). (브로커,사용자)별 캐시로 탭 전환마다
        재조회하지 않는다. 실패 시 조용히 생략 — '연결 테스트'를 누르면 원인이 로그에 나온다."""
        cr = self._creds_of(asset)
        f1 = (cr.get("f1") or "").strip()
        if not f1:
            return
        ck = ("projectx", f1)
        cache = getattr(self, "_scope_cache", None)
        if cache is None:
            cache = self._scope_cache = {}
        if ck in cache:                       # 이미 받은 목록이면 즉시 채우고 끝(재조회 없음)
            self._fill_scope(cache[ck])
            return
        import threading as _th

        def w():
            try:
                b = _build_broker("projectx", f1, _kc_load(f1) or "", cr.get("f3", ""), [])
                names = [str(a.get("name")) for a in b._accounts()]
            except Exception:
                names = None
            if names:
                cache[ck] = names

                def _apply():
                    if self._asset == asset:  # 아직 그 탭이면 콤보 채움(탭 전환 후 오채움 방지)
                        self._fill_scope(names)
                self.root.after(0, _apply)
        _th.Thread(target=w, daemon=True).start()

    def _copy_acct_setup(self, src_asset):
        """다른 선물 자산(src)의 계좌 구성(계좌 목록·1R·프롭/자본% 모드)을 현재 자산으로 복사.
        NQ·GC는 같은 프롭 계좌로 운용하므로 설정을 한 번에 맞춘다(대표 2026-07-26 #20).
        브로커·크레덴셜은 자산별 독립이라 건드리지 않는다 — 계좌 구성만 복사."""
        import copy as _copy
        dst = self._asset
        src_accts = self._accts_of(src_asset)
        if not src_accts or not any((x.get("id") or "").strip() for x in src_accts):
            messagebox.showinfo(self.t("btn_accts"),
                                (f"{src_asset}에 복사할 계좌가 없습니다." if self.lang == "ko"
                                 else f"No accounts to copy from {src_asset}.")); return
        if not messagebox.askyesno(
                ("계좌 설정 복사" if self.lang == "ko" else "Copy account setup"),
                (f"{src_asset}의 계좌 구성을 {dst}(으)로 덮어쓸까요? (브로커·키는 유지)"
                 if self.lang == "ko" else
                 f"Overwrite {dst}'s account setup with {src_asset}'s? (broker/keys kept)")):
            return
        self._collect_acct_widgets()
        self._acfg[dst]["accounts"] = [_copy.deepcopy(a) for a in src_accts]
        self._save_cfg()
        self._build()
        self.log(f"📋 {src_asset} → {dst} 계좌 설정 복사 완료 "
                 f"({len(src_accts)}개 계좌)")

    def _add_acct(self):
        """자산 탭 설정에서 계좌 추가 — acct_pick의 선택/입력값을 이 자산의 계좌ID로 등록(중복 방지)."""
        asset = self._asset
        # 현재 화면의 계좌 위젯값(라벨·1R·on) 먼저 보존 — 재빌드로 유실 방지
        self._collect_acct_widgets()
        # 크립토(계좌ID 없음): 새 행을 바로 추가 — 브로커는 현재 탭 브로커로 시작, 행의
        # 콤보에서 바꾼다(대표 2026-08-09 Bybit+Bitget 동시 발주).
        if not _BROKER_SPEC.get(self._broker_of(asset), {}).get("acct"):
            _bk0 = self._broker_name
            self._accts_of(asset).append(
                _new_acct(600.0, "", True, _broker_label(_bk0), broker=_bk0))
            self._save_cfg()
            self._build()
            return
        try:
            val = str(self.acct_pick.get()).strip()
        except Exception:
            val = ""
        aid = val.split("·")[-1].strip() if "·" in val else val
        if not aid:
            # Topstep인데 콤보가 아직 비어있으면(계좌 목록 로드 전) 로드를 띄우고 안내
            # (대표 2026-07-26: 연결테스트 안 눌러도 목록이 뜨게 — 자동 로드 재시도).
            _empty_combo = not list(self.acct_pick["values"] or [])
            if self._broker_of(asset) == "projectx" and _empty_combo:
                self._autoload_topstep_scope(asset)
                messagebox.showinfo(self.t("btn_accts"),
                                    "계좌 목록을 불러오는 중입니다 — 잠시 후 목록에서 계좌를 골라 "
                                    "다시 '계좌 추가'를 누르세요." if self.lang == "ko" else
                                    "Loading your account list — pick an account and press Add again.")
                return
            messagebox.showwarning(self.t("btn_accts"),
                                   "추가할 계좌를 선택하거나 입력하세요." if self.lang == "ko"
                                   else "Pick or type an account first."); return
        accts = self._accts_of(asset)
        if any((x.get("id") or "").strip() == aid for x in accts):
            messagebox.showinfo(self.t("btn_accts"),
                                "이미 등록된 계좌입니다." if self.lang == "ko"
                                else "Already registered."); return
        # 첫 계좌가 빈 슬롯(id 미지정)이면 그걸 채우고, 아니면 새 계좌로 추가
        if len(accts) == 1 and not (accts[0].get("id") or "").strip():
            accts[0]["id"] = aid
            accts[0]["label"] = accts[0].get("label") or aid[-4:]
        else:
            accts.append(_new_acct(600.0, aid, True, aid[-4:]))
        self._save_cfg()
        # 자동 연결 테스트(#19): creds 판정은 재빌드 '전'(위젯 값 살아있을 때), 실행은 재빌드
        # '후' 지연(위젯 재생성 완료 뒤). 순서가 뒤집히면 연결 테스트가 빈 크레덴셜로 실패하거나
        # 재빌드 도중 위젯 접근으로 계좌 목록이 깜빡였음(대표 2026-07-26 리포트).
        _auto_test = self._creds_ok(silent=True)
        self._build()
        if _auto_test:
            self.root.after(350, self.healthcheck)

    def _del_acct(self, asset, idx):
        """이 자산의 등록 계좌 삭제 — 확인 후. 최소 1개(빈 슬롯) 유지. 인덱스가 밀리므로
        이 자산의 무장은 전부 해제(잘못된 계좌 타겟 방지 — 실거래 안전)."""
        if asset == self._asset:
            self._collect_acct_widgets()   # 다른 행의 미저장 편집(라벨·1R) 보존 후 삭제
        accts = self._accts_of(asset)
        if idx >= len(accts):
            return
        lbl = accts[idx].get("label") or (accts[idx].get("id") or "")[-4:] or "?"
        if not messagebox.askyesno(("계좌 삭제" if self.lang == "ko" else "Remove account"),
                                   (f"'{lbl}' 계좌를 삭제할까요?" if self.lang == "ko"
                                    else f"Remove account '{lbl}'?")):
            return
        # 삭제로 idx가 밀리면 (asset,idx) 무장 키가 엉뚱한 계좌를 가리키므로 이 자산 무장 전면 해제
        for d in (self._sig_accts, self._auto_accts):
            for k in [k for k in d if k[0] == asset]:
                d.pop(k, None)
        if not self._auto_accts:
            self._auto_on = False
        if not self._sig_accts:
            self._sig_on = False
        del accts[idx]
        if not accts:
            accts.append(_new_acct(600.0, "", True, _broker_label(self._broker_of(asset))))
        self._set_auto_ind(bool(self._auto_accts), sorted({k[0] for k in self._auto_accts}))
        self._set_sig_ind(bool(self._sig_accts), sorted({k[0] for k in self._sig_accts}))
        self._save_cfg()
        self._build()

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
            b = self._broker()
            try:
                b.healthcheck()
            except Exception as _e:
                self.log(f"❌ {_e}")
                if self._broker_name in ("bybit", "bitget"):
                    self.log(("   힌트: ① Testnet 칸 확인 — 실계좌 키면 빈칸/0, 테스트넷 키면 1"
                              "  ② 키에 IP 제한이 있으면 이 기기 IP를 허용"
                              "  ③ 키 권한(읽기·주문) 확인") if self.lang == "ko" else
                             ("   Hints: ① Check the Testnet field — live key: empty/0, testnet key: 1"
                              "  ② If the key has an IP whitelist, allow this machine's IP"
                              "  ③ Check key permissions (read/trade)"))
                return
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
            self._conn_by_broker[self._broker_name] = True   # 탭 전환 후 복귀해도 재연결 불필요
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
            self.root.after(0, self._refresh_live_panel)
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
        self.log("   🔴 손절 미거치(네트워크) — 포지션 유지 중. 세션 마감 자동청산이 백스톱이지만, "
                 "지금 수동으로 손절/확인을 권장!")

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
        # tz는 _now_in()이 tzdata 없이도 정확히 계산(ET 산술 폴백) — ZoneInfo 로드 실패로
        # 청산이 죽던 사고 근절(대표 2026-07-20). ZoneInfo 없을 때 1회만 알린다.
        if ZoneInfo is None and not getattr(self, "_tz_fallback_warned", False):
            self._tz_fallback_warned = True
            self.log("ℹ 타임존 DB 없음 — 청산·진입 시각을 UTC 기준 산술 계산으로 처리합니다(정상).")

        while self._auto_on:
            jobs = [j for jl in list(self._auto_accts.values()) for j in jl]   # 무장 계좌 동적 스냅샷
            for j in jobs:
                now = _now_in(j["tz"])
                today = now.strftime("%Y-%m-%d")
                # 🐞 2026-07-27 실사고: 키가 (자산,시각)뿐이라 같은 자산 다계좌 무장 시 첫
                # 계좌 발화가 나머지 계좌를 '오늘 이미 청산'으로 건너뛰게 했다(GC 2계좌 중
                # EXPRESS 실계좌 미청산). 계좌를 키에 포함해 계좌마다 하루 1회 독립 발화.
                key = (j["asset"], j["hour"], j.get("acct") or j["broker"])
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
                try:
                    b = _build_broker(j["broker"], j["f1"], j["f2"], j["f3"],
                                      [j["acct"]] if j["acct"] else [])
                    # ── BTC(일요일 전용): 포지션이 없으면 이 블록 판정은 무의미하다 — 평일엔 BTC
                    # 포지션 자체가 없다(일 22시 진입~월 22시 청산). '세션 마감' 로그 없이 조용히
                    # 건너뛴다(대표 2026-07-16: 평일마다 뜨던 무의미한 BTC 마감 로그 제거).
                    # 단 포지션 조회가 실패하면 스킵하지 않고 기존 경로로(안전 — 진짜 홀드 놓침 방지).
                    if j["asset"] == "BTC" and j["hour"] in _BTC_BLOCK_K:
                        _bq = "ERR"
                        try:
                            _bq = b.position_qty("BTCUSDT") if hasattr(b, "position_qty") else None
                        except Exception:
                            _bq = "ERR"
                        if _bq != "ERR" and not _bq:
                            continue          # 포지션 확실히 없음 → 무음 스킵
                    self.log(f"\n⏰ {_lab} 세션 마감 → auto-close [{j['broker']}"
                             f"{('/' + j['acct']) if j['acct'] else ''}] ({'LIVE' if live else 'dry-run'})")
                    # ── BTC 조건부 출구(X2+TR) — 무조건 청산이 아니라 봉을 보고 판정 ──
                    if j["asset"] == "BTC" and j["hour"] in _BTC_BLOCK_K:
                        if not self._btc_x2be_step(b, j, live, _dt.timezone.utc):
                            continue          # hold/breakeven → 이번 시각엔 청산 안 함
                    # 크립토(BTC) 시간마감 청산 = 지정가 도전 → 시장가 폴백(대표 2026-07-15).
                    # 손절은 거래소 첨부 스탑(시장가 트리거)이라 여기 안 옴.
                    # 2026-08-03 전면 시장가(대표): BTC 청산 지정가 도전도 폐지 — flatten_all이
                    # 곧장 시장가 청산. (_exec_close_limit는 롤백용 보존, 호출만 끔)
                    if live and j["broker"] == "projectx" and j["asset"] in _LIMIT_EXIT_FUT:
                        self._exec_close_limit_fut(b, j["asset"])
                    res = b.flatten_all(dry_run=not live)   # 잔여 확인 겸 최종 청산(플랫이면 no-op)
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
                            # 청산 통보(#53) - 수량 0. 서버는 수량을 지우지 않고 open=False로만
                            # 바꿔 실현 손익을 다음 진입 전까지 금액으로 보여준다(대표 2026-08-08
                            # "청산시 피엔엘도 담 거래 전까지 보여줘").
                            self._send_fill(j["asset"], closed=True)
                        else:
                            # 🚨 청산 후에도 포지션이 남았거나 실패 — 로그만으론 못 본다(2026-07-27
                            # Follower 계좌 청산 거부 실사고). 팝업으로 즉시 수동 개입 요청.
                            _errs = "\n".join(str(e)[:120] for e in res.errors[:4])
                            _pt = ("자동청산 실패 — 수동 확인 필요" if self.lang == "ko"
                                   else "Auto-close failed — manual action needed")
                            _pm = ((f"{_lab} 자동청산이 완전히 끝나지 않았습니다.\n\n{_errs}\n\n"
                                    "브로커 화면에서 포지션을 직접 확인·청산하세요.") if self.lang == "ko"
                                   else (f"Auto-close for {_lab} did not fully complete.\n\n{_errs}\n\n"
                                         "Check and close the position directly on your broker."))
                            self.root.after(0, lambda t=_pt, m=_pm: messagebox.showwarning(t, m))
                except Exception as e:
                    self.log(f"   ❌ auto-close failed — {e}")
                    self._report_error("auto_close", e)   # 예외 리포트(대표 2026-07-27)
            _t.sleep(15)

    # ── auto-ENTRY on EdgeQuant signal ──────────────────────────────────────
    # ── 라이브 패널 상태 표시(대표 2026-07-24 자산별 계좌) ─────────────
    def _asset_row_state(self, asset):
        """(요약문, 점 색) — 자산 실행 설정 완결성·연결·무장 상태를 한 줄로.
        완결성 = 크레덴셜 f1 · 켜진 계좌 최소 1개 · (acct 브로커면)계좌ID · 1R>0.
        계좌별 브로커(대표 2026-08-09): 라벨·키·연결 판정을 켜진 계좌들의 브로커 전체로."""
        active = self._active_accts(asset)
        # 켜진 계좌들의 브로커(중복 제거, 순서 유지) — 없으면 자산 기본 브로커
        bks = []
        for ac in active:
            _b = self._acct_broker(asset, ac)
            if _b not in bks:
                bks.append(_b)
        if not bks:
            bks = [self._broker_of(asset)]
        missing = []
        for _b in bks:
            if not (self._creds_of(asset, _b).get("f1") or "").strip():
                missing.append((f"{_broker_label(_b)} 키" if self.lang == "ko"
                                else f"{_broker_label(_b)} key"))
        if not active:
            missing.append("켜진 계좌" if self.lang == "ko" else "account on")
        else:
            if any(_as_float(ac.get("one_r")) <= 0 and not (ac.get("prop") or {}).get("on")
                   and not (ac.get("pct") or {}).get("on") for ac in active):
                missing.append("1R")
            if any(_BROKER_SPEC.get(self._acct_broker(asset, ac), {}).get("acct")
                   and not (ac.get("id") or "").strip() for ac in active):
                missing.append("계좌ID" if self.lang == "ko" else "acct id")
        if missing:
            return (("✗ 미설정: " if self.lang == "ko" else "✗ missing: ")
                    + " · ".join(missing), "#9ca3af")
        # 무장 여부·실거래 여부 — 이 자산의 (asset,idx) 키가 sig/auto에 있으면 가동 중
        sig_keys = [k for k in getattr(self, "_sig_accts", {}) if k[0] == asset]
        auto_keys = [k for k in getattr(self, "_auto_accts", {}) if k[0] == asset]
        armed = bool(sig_keys or auto_keys)
        live = False
        if sig_keys:
            live = bool(self._sig_accts[sig_keys[0]].get("live"))
        elif auto_keys:
            _jobs = self._auto_accts[auto_keys[0]]
            live = bool(_jobs[0].get("live")) if _jobs else False
        _n = len(active)
        base = ("+".join(_broker_label(_b) for _b in bks) + " · "
                + (f"{_n}계좌" if self.lang == "ko" else f"{_n} acct"))
        _untested = [_b for _b in bks if not self._conn_by_broker.get(_b)]
        if _untested:
            return base + (" · 연결 테스트 필요" if self.lang == "ko" else " · test connection"), "#9ca3af"
        if armed:
            base += " · " + (("가동 중(실거래)" if live else "가동 중(모의)") if self.lang == "ko"
                             else ("RUNNING live" if live else "RUNNING dry"))
            return base, ("#21c55e" if live else "#eab308")
        return base + (" · 준비됨" if self.lang == "ko" else " · ready"), "#9ca3af"

    def _pct_btn_text(self, pc):
        if (pc or {}).get("on"):
            return (f"자본 {_as_float(pc.get('pct'), 0.4):g}%" if self.lang == "ko"
                    else f"{_as_float(pc.get('pct'), 0.4):g}% eq")
        return "자본%: 끔" if self.lang == "ko" else "% sizing: off"

    def _pct_dialog(self, idx):
        """계좌별 자본 비례 사이징(대표 2026-07-26 #18): 1R = 잔고 × pct%, 최소 floor.
        발주 순간 잔고 조회(선물=account_balance / 크립토=available_usdt). 프롭 모드와 배타."""
        ko = self.lang == "ko"
        try:
            acct = self._accts_of(self._asset)[idx]
        except Exception:
            return
        if (acct.get("prop") or {}).get("on"):
            messagebox.showinfo("EQ", ("프롭 자동 사이징이 켜져 있어 자본 비례를 함께 쓸 수 없습니다. "
                                       "프롭을 끄고 다시 시도하세요." if ko else
                                       "Prop auto-sizing is on; disable it first to use % sizing."))
            return
        pc = dict(_PCT_DEFAULTS); pc.update(acct.get("pct") or {})
        win = tk.Toplevel(self.root)
        win.title(("자본 비례 사이징 — " + (acct.get("label") or "")) if ko
                  else ("Capital-proportional sizing — " + (acct.get("label") or "")))
        win.resizable(False, False); win.grab_set()
        frm = ttk.Frame(win, padding=12); frm.pack(fill="both", expand=True)
        on_v = tk.IntVar(value=1 if pc.get("on") else 0)
        ttk.Checkbutton(frm, variable=on_v,
                        text=("자본 비례 사이징 사용(잔고의 %로 1R 자동)" if ko else
                              "Enable capital-proportional sizing (1R = % of balance)")
                        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(frm, text=("잔고 대비 비율 %" if ko else "Percent of balance %")).grid(row=1, column=0, sticky="w")
        pe = ttk.Entry(frm, width=8); pe.insert(0, f"{_as_float(pc.get('pct'), 0.4):g}")
        pe.grid(row=1, column=1, sticky="w")
        ttk.Label(frm, text=("최소 1R $ (수수료 방어)" if ko else "Min 1R $ (fee floor)")).grid(row=2, column=0, sticky="w")
        fe = ttk.Entry(frm, width=8); fe.insert(0, f"{_as_float(pc.get('floor'), 200.0):g}")
        fe.grid(row=2, column=1, sticky="w")
        ttk.Label(frm, foreground="#888", wraplength=360, justify="left",
                  text=(("발주 순간 계좌 잔고를 조회해 1R = 잔고 × 비율로 계산합니다(선물·크립토 모두). "
                         "권장 0.4%는 13년 최악 낙폭(약 42R)에서도 자본의 ~17%에 그치는 안전 비율입니다. "
                         "잔고 조회가 안 되면 24시간 내 마지막 성공값을 쓰고, 그것도 없으면 그 계좌는 "
                         "이번 진입을 건너뜁니다.") if ko else
                        ("At order time the account balance is read and 1R = balance × percent "
                         "(futures and crypto alike). The suggested 0.4% keeps even the 13-year "
                         "worst drawdown (~42R) to ~17% of capital. If the balance can't be read, "
                         "the last value within 24h is used; if none, that account skips the entry."))
                  ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 8))

        def _ok():
            newc = {"on": bool(on_v.get()), "pct": _as_float(pe.get(), 0.4),
                    "floor": _as_float(fe.get(), 200.0)}
            if newc["on"] and (newc["pct"] <= 0 or newc["pct"] > 100):
                messagebox.showwarning("EQ", "비율은 0~100 사이여야 합니다." if ko
                                       else "Percent must be between 0 and 100."); return
            acct["pct"] = newc
            try:
                w = self._acct_widgets.get(idx) or {}
                if w.get("pct_btn"):
                    w["pct_btn"].config(text=self._pct_btn_text(newc))
                if w.get("one_r"):
                    w["one_r"].config(state=("disabled" if newc["on"] else "normal"))
            except Exception:
                pass
            self._save_acct_widgets(); win.destroy()

        ttk.Button(frm, text=("저장" if ko else "Save"), command=_ok).grid(row=4, column=1)
        ttk.Button(frm, text=("취소" if ko else "Cancel"), command=win.destroy).grid(row=4, column=2)

    def _prop_btn_text(self, pr):
        if not (pr or {}).get("on"):
            return "프롭: 끔" if self.lang == "ko" else "Prop: off"
        if pr.get("type") == "funded":
            _pc = max(0, min(5, int(_as_float((pr or {}).get("payouts"), 0))))
            return (f"프롭: 펀디드 {_pc}/5발" if self.lang == "ko" else f"Prop: funded {_pc}/5")
        return "프롭: 테스트" if self.lang == "ko" else "Prop: test"

    def _prop_dialog(self, idx):
        """계좌별 프롭 자동 사이징 설정(대표 2026-07-26 태스크 #16).
        유형=정적 태그(통과 시 펀디드는 새 계좌라 전환 감지 불필요), 버퍼기↔안정기는
        발주 순간 잔고로 매일 재평가(히스테리시스 없음)."""
        ko = self.lang == "ko"
        try:
            acct = self._accts_of(self._asset)[idx]
        except Exception:
            return
        pr = dict(_PROP_DEFAULTS); pr.update(acct.get("prop") or {})
        win = tk.Toplevel(self.root)
        win.title(("프롭 자동 사이징 — " + (acct.get("label") or "")) if ko
                  else ("Prop auto-sizing — " + (acct.get("label") or "")))
        win.resizable(False, False); win.grab_set()
        frm = ttk.Frame(win, padding=12); frm.pack(fill="both", expand=True)
        on_v = tk.IntVar(value=1 if pr.get("on") else 0)
        ttk.Checkbutton(frm, variable=on_v,
                        text=("프롭 자동 사이징 사용(수동 1R 대신 페이즈별 1R)" if ko else
                              "Enable prop auto-sizing (phase 1R replaces manual 1R)")
                        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ty_v = tk.StringVar(value=pr.get("type") or "test")
        ttk.Label(frm, text=("계좌 유형" if ko else "Account type")).grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(frm, text=("테스트기(챌린지)" if ko else "Test (challenge)"),
                        variable=ty_v, value="test").grid(row=1, column=1, sticky="w")
        ttk.Radiobutton(frm, text=("펀디드" if ko else "Funded"),
                        variable=ty_v, value="funded").grid(row=1, column=2, sticky="w")
        ents = {}
        _rows = [("r_test", "챌린지 1R $" if ko else "Challenge 1R $"),
                 ("r_steady", "펀디드 1R $" if ko else "Funded 1R $"),
                 ("payouts", "출금 횟수 (0~5)" if ko else "Payouts so far (0~5)")]
        # r_buffer·buffer 필드는 Fast-Payout 채택으로 미사용 — 저장값은 유지(마이그레이션 호환)
        for i, (k, lab) in enumerate(_rows, start=2):
            ttk.Label(frm, text=lab).grid(row=i, column=0, sticky="w", pady=1)
            e = ttk.Entry(frm, width=10)
            e.insert(0, f"{_as_float(pr.get(k), _PROP_DEFAULTS[k]):g}")
            e.grid(row=i, column=1, sticky="w", pady=1)
            ents[k] = e
        ttk.Label(frm, foreground="#888", wraplength=380, justify="left",
                  text=(("펀디드 1R은 전 구간 $300 고정, 출금은 두 단계입니다(빅실드). "
                         "방패기(계정 합산 1~3발): 방패 $6,000을 계좌에 남기고 잔고 $12,000 도달 "
                         "시마다 $6,000 출금. Fast-Payout기(합산 4~5발, 라이브 초대 전): 출금 "
                         "자격($150+ 익절일 5일·직전 출금 후 순익 플러스)이 차는 순간 잔고의 "
                         "절반을 즉시 출금(회당 $6,000 한도). 합산 5발이면 이 계정은 라이브 전환 "
                         "대상 — 새 계정으로 교체합니다. "
                         "출금 횟수는 출금 팝업에서 '예'로 자동 +1 되며 여기서 수동 조정도 됩니다.") if ko else
                        ("Funded 1R is a flat $300 throughout, and payouts run in two phases "
                         "(big shield). Shield phase (login payouts 1-3): keep a $6,000 shield in "
                         "the account and withdraw $6,000 each time the balance reaches $12,000. "
                         "Fast-Payout phase (payouts 4-5, until the Live invite): the moment you "
                         "qualify (five $150+ winning days, net positive since the last payout), "
                         "withdraw half the balance immediately ($6,000/payout cap). At five "
                         "payouts the login becomes a Live-transition candidate — rotate to a "
                         "fresh login. The count auto-increments via the payout popup and can be "
                         "adjusted here."))
                  ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(8, 8))

        def _ok():
            newp = {"on": bool(on_v.get()), "type": ty_v.get()}
            for k in ents:
                newp[k] = _as_float(ents[k].get(), _PROP_DEFAULTS[k])
            newp["payouts"] = max(0, min(5, int(_as_float(ents["payouts"].get(), 0))))
            # 다이얼로그에서 뺀 레거시 필드(r_buffer·buffer)는 기존 저장값 유지 — 여기서
            # 참조하다 KeyError로 저장이 조용히 죽던 사고 수리(대표 2026-07-28 "저장 안 됨").
            for k in ("r_buffer", "buffer"):
                newp[k] = _as_float(pr.get(k), _PROP_DEFAULTS[k])
            if newp["on"] and any(newp[k] <= 0 for k in ("r_test", "r_steady")):
                messagebox.showwarning("EQ", "1R 값은 0보다 커야 합니다." if ko
                                       else "1R values must be > 0.")
                return
            acct["prop"] = newp
            try:
                w = self._acct_widgets.get(idx) or {}
                if w.get("prop_btn"):
                    w["prop_btn"].config(text=self._prop_btn_text(newp))
                if w.get("one_r"):
                    w["one_r"].config(state=("disabled" if newp["on"] else "normal"))
            except Exception:
                pass
            self._save_acct_widgets()
            win.destroy()

        ttk.Button(frm, text=("저장" if ko else "Save"), command=_ok).grid(row=9, column=1)
        ttk.Button(frm, text=("취소" if ko else "Cancel"), command=win.destroy).grid(row=9, column=2)

    def _refresh_live_panel(self):
        for asset, (lbl, dot) in getattr(self, "_live_rows", {}).items():
            try:
                txt, col = self._asset_row_state(asset)
                lbl.config(text=txt)
                dot.config(foreground=col)
            except Exception:
                pass

    def _acct_edit_row(self, parent, idx, acct, spec, deletable=True, show_on=True):
        """자산 탭 계좌 한 줄(편집): [on] 라벨 · 1R$ · (계좌ID) · [삭제].
        값은 FocusOut/토글 시 _save_acct_widgets → 이 자산 accounts에 저장. idx로 키잉."""
        row = ttk.Frame(parent); row.pack(fill="x", pady=1)
        if show_on:
            on = tk.IntVar(value=1 if acct.get("on", True) else 0)
            ttk.Checkbutton(row, variable=on, width=2,
                            command=self._save_acct_widgets).pack(side="left")
        else:
            on = tk.IntVar(value=1)          # 크립토 단일 계좌 = 항상 on(토글 없음)
        # 계좌별 브로커 선택(대표 2026-08-09 "계좌 추가에서 브로커 선택하게") — 이 자산에
        # 브로커가 2개 이상일 때만 노출. 발주·청산·연결테스트 전부 이 값을 따른다.
        _brs = _ASSET_BROKERS.get(self._asset, [])
        if len(_brs) > 1:
            bk_cb = ttk.Combobox(row, values=[_broker_label(b) for b in _brs],
                                 state="readonly", width=8)
            bk_cb.set(_broker_label(self._acct_broker(self._asset, acct)))
            bk_cb.pack(side="left", padx=(0, 4))
            bk_cb.bind("<<ComboboxSelected>>", lambda e: self._save_acct_widgets())
        else:
            bk_cb = None
        aid = (acct.get("id") or "").strip()
        lbl_e = ttk.Entry(row, width=12)
        lbl_e.insert(0, acct.get("label")
                     or (aid[-4:] if aid
                         else _broker_label(self._acct_broker(self._asset, acct))))
        lbl_e.pack(side="left", padx=(0, 4))
        lbl_e.bind("<FocusOut>", lambda e: self._save_acct_widgets())
        ttk.Label(row, text="1R $").pack(side="left")
        r_e = ttk.Entry(row, width=8)
        r_e.insert(0, f"{_as_float(acct.get('one_r'), 600.0):g}")
        r_e.pack(side="left", padx=(0, 4))
        r_e.bind("<FocusOut>", lambda e: self._save_acct_widgets())
        self._acct_widgets[idx] = {"on": on, "label": lbl_e, "one_r": r_e, "broker": bk_cb}
        pr = acct.get("prop") or {}
        _pb_txt = self._prop_btn_text(pr)
        pb = ttk.Button(row, text=_pb_txt, width=11,
                        command=lambda i=idx: self._prop_dialog(i))
        pb.pack(side="left", padx=(4, 0))
        self._acct_widgets[idx]["prop_btn"] = pb
        pc = acct.get("pct") or {}
        cb = ttk.Button(row, text=self._pct_btn_text(pc), width=10,
                        command=lambda i=idx: self._pct_dialog(i))
        cb.pack(side="left", padx=(4, 0))
        self._acct_widgets[idx]["pct_btn"] = cb
        # 수동 모드(대표 2026-07-27): 신호 티켓만 받고 자동 진입 안 함(진입은 사용자가 직접).
        # API 없는 프롭(예: 평가·Sim Funded 단계)에서 신호를 손으로 실행할 때. 자동 청산은 별개.
        man = tk.IntVar(value=1 if acct.get("manual") else 0)
        ttk.Checkbutton(row, text=("수동" if self.lang == "ko" else "Manual"), variable=man,
                        command=self._save_acct_widgets).pack(side="left", padx=(6, 0))
        self._acct_widgets[idx]["manual"] = man
        if pr.get("on") or pc.get("on"):
            r_e.config(state="disabled")               # 자동 사이징 중엔 수동 1R 비활성
        if aid:
            ttk.Label(row, text=f"…{aid[-6:]}", foreground="#888").pack(side="left", padx=(4, 0))
        elif spec.get("acct"):
            ttk.Label(row, text=("계좌 미지정" if self.lang == "ko" else "no id"),
                      foreground="#b06f00").pack(side="left", padx=(4, 0))
        if deletable:
            ttk.Button(row, text=("삭제" if self.lang == "ko" else "Remove"), width=6,
                       command=lambda i=idx: self._del_acct(self._asset, i)).pack(side="right")

    def _save_acct_widgets(self):
        """자산 탭 계좌 위젯값(on·라벨·1R) → 이 자산 accounts 반영 + 영속 + 라이브 패널 새로고침."""
        self._collect_acct_widgets()
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True)
        self._refresh_live_panel()

    def _on_asset_toggle(self):
        """실행 자산 체크 변경 → 자산별 include 반영 + 영속(계좌 재구성 불필요 — 계좌는 자산탭 관리)."""
        for a, var in getattr(self, "_live_include", {}).items():
            try:
                self._acfg[a]["include"] = bool(var.get())
            except Exception:
                pass
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True)
        self._refresh_live_panel()

    def _toggle_cfg(self):
        """자산별 브로커 설정 접기/펼치기(대표 2026-07-13)."""
        self._cfg_open = not self._cfg_open
        if self._cfg_open:
            self._cfg_body.pack(fill="x", after=self._cfg_hdr)
        else:
            self._cfg_body.pack_forget()
        self._cfg_hdr.config(text=("▾ " if self._cfg_open else "▸ ")
                             + ("자산별 브로커 설정 (NQ·GC·BTC)" if self.lang == "ko"
                                else "Per-asset broker setup (NQ·GC·BTC)"))
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True,
                       cfg_open=self._cfg_open)

    def _panel_stop(self, asset):
        """라이브 패널 자산 줄 정지 — 이 자산의 모든 계좌 가동 해제(자동청산+신호대기 둘 다)."""
        removed = 0
        for d in (self._auto_accts, self._sig_accts):
            for k in [k for k in d if k[0] == asset]:
                d.pop(k, None); removed += 1
        if not self._auto_accts:
            self._auto_on = False
        if not self._sig_accts:
            self._sig_on = False
        if removed:
            self.log(f"⏹ {asset} 가동 해제."
                     + ("" if (self._auto_accts or self._sig_accts) else " (가동 계좌 없음 — 루프 종료)"))
            self._set_auto_ind(bool(self._auto_accts), sorted({k[0] for k in self._auto_accts}))
            self._set_sig_ind(bool(self._sig_accts), sorted({k[0] for k in self._sig_accts}))
        self._refresh_live_panel()

    def _save_creds_and_test(self):
        """[계좌 정보 저장 + 연결 테스트 + 1R 확인] (대표 2026-08-09 '한 번에') — 현재 탭
        키(f1/f2/f3)·계좌 설정 즉시 영속 → 현재 브로커 연결 테스트 → 통과 시 이 자산 전
        계좌 잔고·1R 미리보기까지 이어서. 실패는 에러 팝업, 성공 확인 = 잔고·1R 팝업."""
        self._save_current_asset()
        asset, bk = self._asset, self._broker_name
        self.log(f"\n💾 {asset} · {_broker_label(bk)} " + ("설정 저장 완료 — 연결 테스트 중…"
                 if self.lang == "ko" else "saved — testing connection…"))
        if not (self._creds_of(asset, bk).get("f1") or "").strip():
            messagebox.showinfo(self.t("btn_conn"),
                                (f"{_broker_label(bk)} 키가 비어 있습니다 — 저장만 했습니다."
                                 if self.lang == "ko" else
                                 f"{_broker_label(bk)} key is empty — saved settings only."))
            return

        def w():
            err = self._conn_check(asset, bk)

            def done():
                if err:
                    self.log(f"❌ {asset} · {_broker_label(bk)}: {err}")
                    messagebox.showerror(self.t("btn_conn"), f"{_broker_label(bk)}: {err}")
                else:
                    self._connected = True
                    self.log(f"✅ {asset} · {_broker_label(bk)} 연결 OK — 저장·검증 완료")
                    # 성공 확인 = 잔고·1R 팝업(별도 확인 팝업 대신) — 이 자산 전 계좌 조회
                    self._run_1r_preview([asset], (f"{asset} · 저장·연결 OK — 잔고 & 1R"
                                                   if self.lang == "ko"
                                                   else f"{asset} · saved & connected — balance & 1R"))
                self._apply_gating()
                self._refresh_live_panel()
            self.root.after(0, done)
        threading.Thread(target=w, daemon=True).start()

    def _panel_conn_test(self, asset):
        """라이브 패널 자산 줄의 연결 테스트 — 켜진 계좌들의 브로커 전부 무음 점검
        (계좌별 브로커, 대표 2026-08-09: Bitget만 테스트되고 Bybit는 빠지던 것 수리)."""
        self._save_current_asset()
        bks = []
        for ac in self._active_accts(asset):
            _b = self._acct_broker(asset, ac)
            if _b not in bks:
                bks.append(_b)
        if not bks:
            bks = [self._broker_of(asset)]
        _missing = [b for b in bks if not (self._creds_of(asset, b).get("f1") or "").strip()]
        if _missing:
            messagebox.showwarning(self.t("btn_conn"), f"{asset}: "
                                   + ", ".join(_broker_label(b) for b in _missing) + " "
                                   + ("키 미설정" if self.lang == "ko" else "key not set"))
            return

        def w():
            for bk in bks:
                self.log(f"\n── {asset} · {_broker_label(bk)} 연결 테스트 ──")
                err = self._conn_check(asset, bk)
                self.log(f"❌ {asset} · {_broker_label(bk)}: {err}" if err
                         else f"✅ {asset} · {_broker_label(bk)} 연결 OK")
            self.root.after(0, self._refresh_live_panel)
        threading.Thread(target=w, daemon=True).start()

    def _conn_check(self, asset, broker=None):
        """자산의 (지정 또는 기본) 브로커 크레덴셜로 무음 연결 테스트 + (projectx) 등록 계좌ID
        유효성 검사(스레드 컨텍스트). 성공 시 _conn_by_broker[bk]=True(연결=크레덴셜=브로커
        단위). broker 지정 = 계좌별 브로커 테스트(대표 2026-08-09). 반환: None(성공) | 오류."""
        bk = broker or self._broker_of(asset)
        cr = self._creds_of(asset, bk)
        f1 = (cr.get("f1") or "").strip()
        try:
            b = _build_broker(bk, f1, _kc_load(f1) or "", cr.get("f3", ""), [])
            b.healthcheck()
            if bk == "projectx":
                names = [str(x.get("name")) for x in b._accounts()]
                if asset == self._asset:      # 현재 탭 자산이면 '계좌 추가' 콤보도 채움
                    self.root.after(0, lambda n=names: self._fill_scope(n))
                for ac in self._active_accts(asset):
                    if self._acct_broker(asset, ac) != bk:
                        continue              # 다른 브로커 계좌 행은 이 테스트 대상 아님
                    aid = (ac.get("id") or "").strip()
                    if not aid:
                        return ("등록 계좌 중 계좌ID 미지정 — 자산 설정에서 계좌를 추가하세요"
                                if self.lang == "ko" else "a registered account has no id — add one")
                    if aid not in names:
                        return (f"등록 계좌 '{aid}'가 계좌 목록에 없음" if self.lang == "ko"
                                else f"registered account '{aid}' not in list")
            self._conn_by_broker[bk] = True
            try:
                for _ln in (b.entry_info() or []):
                    self.log(f"   ℹ {_broker_label(bk)} {_ln}")
            except Exception:
                pass
            # 키 사전 경고(만료 D-day·권한)를 연결 테스트에서도 즉시 노출(대표 2026-08-09) —
            # 진입 직전 사전점검(_precheck_run)만 기다리지 않고 설정 시점에 미리 안다.
            try:
                if hasattr(b, "key_info"):
                    for _w in (b.key_info() or []):
                        self.log(f"   ⚠ {_broker_label(bk)} {_w}")
            except Exception:
                pass
            return None
        except Exception as e:
            return str(e)[:200]

    def _master_arm(self, asset, idx, live, arm_sig):
        """자산 asset의 계좌 idx 하나 무장 — 자동청산(그 자산의 _ASSET_EXITS) + (권한 시) 신호대기.
        cred = 자산 브로커 크레덴셜 + 그 계좌 acct_id/one_r (대표 2026-07-24 자산별 계좌)."""
        acct = self._accts_of(asset)[idx]
        bk = self._acct_broker(asset, acct)          # 계좌별 브로커(대표 2026-08-09)
        cr = self._creds_of(asset, bk)
        f1 = (cr.get("f1") or "").strip()
        aid = (acct.get("id") or "").strip()
        cred = {"broker": bk, "f1": f1, "f2": (_kc_load(f1) or ""),
                "f3": cr.get("f3", ""), "acct": aid}
        # 수동 모드 계좌 = 티켓만, 자동 청산 안 함(진입도 청산도 사용자가 직접 — API 없는 프롭 대응).
        jobs = [] if acct.get("manual") else \
               [{"asset": asset, "tz": tzname, "hour": hour, "live": live, **cred}
                for tzname, hour in _ASSET_EXITS.get(asset, [])]
        key = (asset, idx)
        if jobs:
            self._auto_accts[key] = jobs
            if not self._auto_on:
                self._auto_on = True
                threading.Thread(target=self._auto_loop, daemon=True).start()
        if arm_sig:
            self._sig_accts[key] = {**cred, "one_r": float(acct.get("one_r", 0) or 0),
                                    "prop": dict(acct.get("prop") or {}),
                                    "pct": dict(acct.get("pct") or {}),
                                    "manual": bool(acct.get("manual")),   # 수동 모드=티켓만·자동진입 안 함
                                    "live": live, "label": acct.get("label", "")}
            if not self._sig_on:
                self._sig_on = True
                threading.Thread(target=self._sig_loop, args=(_feed_url(self._token),),
                                 daemon=True).start()
        self._set_auto_ind(bool(self._auto_accts), sorted({k[0] for k in self._auto_accts}))
        self._set_sig_ind(bool(self._sig_accts), sorted({k[0] for k in self._sig_accts}))

    def _master_start(self, dry: bool = True):
        """[라이브 시작]/[모의 시작] — 실행 체크된 자산을 연결 테스트(자산 브로커, 같은 브로커는
        중복 테스트 방지), 전부 통과 시에만 각 자산의 켜진 계좌를 일괄 무장(하나라도 실패 시
        전체 중단). 계좌마다 자기 1R로. 모의/라이브는 누른 버튼이 결정(체크박스 폐지, 대표 2026-07-22)."""
        if not dry and (self._gate or {}).get("force_dry_run"):
            messagebox.showwarning(self.t("sec_live"),
                                   "서버가 모의 모드를 강제 중입니다 — 지금은 모의 시작만 가능합니다."
                                   if self.lang == "ko" else
                                   "Server is forcing dry-run; only demo start is available."); return
        self.live_dry.set(1 if dry else 0)
        if self._sig_accts or self._auto_accts:
            messagebox.showinfo(self.t("sec_live"),
                                "이미 가동 중입니다 — 먼저 '전체 정지' 후 다시 시작하세요."
                                if self.lang == "ko" else
                                "Already armed — press 'Stop all' first."); return
        if not self._consent_ok():
            return
        if not self._token:
            messagebox.showwarning(self.t("token"), self.t("gate_none")); return
        self._save_current_asset()
        incl = [a for a in _ASSETS if self._live_include[a].get()]
        if not incl:
            messagebox.showwarning(self.t("sec_live"),
                                   "참여 자산이 없습니다 — 자산을 체크하세요."
                                   if self.lang == "ko" else "No assets checked."); return
        # 프리플라이트: 각 자산의 브로커 크레덴셜 + 켜진 계좌(계좌ID·1R) 완결성 (전체 중단 원칙)
        bad = []
        for a in incl:
            _act = self._active_accts(a)
            if not _act:
                bad.append(f"{a}: " + ("켜진 계좌 없음" if self.lang == "ko" else "no account on"))
                continue
            # 계좌별 브로커(대표 2026-08-09) — 키·허용·계좌ID를 계좌 단위로 검사
            for ac in _act:
                bk = self._acct_broker(a, ac)
                _nm = ac.get("label") or _broker_label(bk)
                if not (self._creds_of(a, bk).get("f1") or "").strip():
                    bad.append(f"{a} {_nm} ({_broker_label(bk)}): "
                               + ("키 미설정" if self.lang == "ko" else "key missing"))
                    continue
                if not self._broker_allowed(bk):
                    bad.append(f"{a} {_nm} ({_broker_label(bk)}): "
                               + ("브로커 지원 꺼짐(서버)" if self.lang == "ko"
                                  else "broker disabled (server)"))
                    continue
                if (_as_float(ac.get("one_r")) <= 0 and not (ac.get("prop") or {}).get("on")
                        and not (ac.get("pct") or {}).get("on")):
                    bad.append(f"{a} {_nm}: 1R")
                if (_BROKER_SPEC.get(bk, {}).get("acct")
                        and not (ac.get("id") or "").strip()):
                    bad.append(f"{a} {_nm}: " + ("계좌ID" if self.lang == "ko" else "account id"))
        if bad:
            messagebox.showwarning(self.t("sec_live"), "\n".join(bad)); return
        g = self._gate or {}
        caps = g.get("caps", {})
        perm_use = bool(g.get("ok") and g.get("enabled") and caps.get("use"))
        perm_auto = bool(g.get("ok") and g.get("enabled") and caps.get("autoentry"))
        if not (perm_use or perm_auto):
            messagebox.showwarning(self.t("token"),
                                   "멤버십 권한이 없습니다 — 토큰을 확인하세요."
                                   if self.lang == "ko" else "No membership permission — check your token."); return
        live = not dry
        self.b_live_start.config(state="disabled")
        if hasattr(self, "b_demo_start"):
            self.b_demo_start.config(state="disabled")
        _alabels = "·".join(incl)
        self.log(f"\n══ 라이브 시작 — 연결 테스트 {_alabels} ({'LIVE' if live else 'dry-run'}) ══")

        def w():
            fails = []
            tested = {}                # (브로커,f1)별 연결 테스트 1회(같은 크레덴셜 중복 방지)
            for a in incl:
                # 수동 전용 자산(활성 계좌가 전부 수동 모드)은 발주를 안 하므로 브로커 연결 불필요.
                # 연결 테스트를 건너뛰어 '연결 실패로 전체 중단'되지 않게 한다(대표 2026-07-27).
                _act = [ac for ac in self._accts_of(a) if ac.get("on")]
                if _act and all(ac.get("manual") for ac in _act):
                    self.log(f"── {a} · " + ("수동 모드(신호 티켓만) — 연결 테스트 건너뜀"
                                             if self.lang == "ko" else
                                             "manual mode (signal tickets only) — skipping connection test"))
                    continue
                # 계좌별 브로커 전부 테스트(대표 2026-08-09 BTC Bybit+Bitget 동시 발주)
                _bks = []
                for ac in _act:
                    if ac.get("manual"):
                        continue
                    _b = self._acct_broker(a, ac)
                    if _b not in _bks:
                        _bks.append(_b)
                for bk in _bks:
                    _f1 = (self._creds_of(a, bk).get("f1") or "").strip()
                    _tk = (bk, _f1)
                    if _tk in tested:
                        err = tested[_tk]
                        self.log(f"── {a} · {_broker_label(bk)} — "
                                 + ("연결 확인됨(공유)" if not err else f"연결 실패(공유): {err}"))
                    else:
                        self.log(f"── {a} · {_broker_label(bk)} 연결 테스트 ──")
                        err = self._conn_check(a, bk)
                        tested[_tk] = err
                        self.log(f"✅ {a} · {_broker_label(bk)} 연결 OK" if not err
                                 else f"❌ {a} · {_broker_label(bk)}: {err}")
                    if err:
                        fails.append(f"{a} ({_broker_label(bk)}): {err}")

            def done():
                self.b_live_start.config(state="normal")
                if hasattr(self, "b_demo_start"):
                    self.b_demo_start.config(state="normal")
                if fails:
                    self.log("⛔ 전체 중단 — 아무 계좌도 가동하지 않았습니다.")
                    messagebox.showerror(self.t("sec_live"),
                                         ("연결 실패 — 전체 중단:\n" if self.lang == "ko"
                                          else "Connection failed — aborted:\n") + "\n".join(fails))
                    self._refresh_live_panel(); return
                narmed = 0
                for a in incl:
                    for idx, ac in enumerate(self._accts_of(a)):
                        if ac.get("on"):
                            self._master_arm(a, idx, live, arm_sig=perm_auto)
                            narmed += 1
                _what = ("자동 청산" + (" + 신호 대기" if perm_auto else " (신호 대기는 Autopilot 등급)")
                         if self.lang == "ko" else
                         "auto-close" + (" + signal watch" if perm_auto else ""))
                self.log(f"🚀 라이브 가동 시작 — {narmed}개 계좌 [{_alabels}] · {_what} · "
                         f"{'LIVE' if live else 'dry-run(모의)'}")
                self.log(f"   {self.t('warn_mix')}")
                self._refresh_live_panel()
                self._apply_gating()
            self.root.after(0, done)
        self._run(w)

    def _master_stop(self):
        """[⏹ 전체 정지] — 모든 계좌 무장 해제(루프는 무장 0이면 자연 종료)."""
        n = len(set(list(self._sig_accts) + list(self._auto_accts)))
        self._sig_accts.clear()
        self._auto_accts.clear()
        self._sig_on = False
        self._auto_on = False
        self._set_auto_ind(False, [])
        self._set_sig_ind(False, [])
        self.log(f"\n⏹ 전체 정지 — {n}개 계좌 가동 해제." if self.lang == "ko"
                 else f"\n⏹ Stopped all — {n} account(s) disarmed.")
        self._refresh_live_panel()
        self._apply_gating()

    def _auto_leverage(self, b, sym, size, ref_px=None):
        """잔고-맞춤 레버리지 자동 조정 (대표 2026-07-20 — 110007 잔고부족 진입실패 재발 방지).
        진입 직전 가용잔고를 앱이 직접 조회해, 이 수량이 들어가도록 레버리지를 상향한다
        (하향 안 함 · 실위험은 손절=1R 고정이라 위험 증가 아님). 실패해도 진입은 계속 시도.
        브로커가 ensure_leverage를 지원할 때만(현재 Bybit) 동작 — Bitget은 추후."""
        if not hasattr(b, "ensure_leverage"):
            return
        try:
            px = b.current_market_price(sym)
        except Exception:
            px = None
        px = px or ref_px
        if not px:
            return
        lv = b.ensure_leverage(sym, size, px)
        if lv.get("new"):
            self.log(f"   ⚙ 레버리지 자동 조정 {lv['cur']:g}x → {lv['new']}x "
                     f"(명목 ${lv['notional']:,.0f} · 가용 ${lv['ab']:,.0f})")
        elif lv.get("error"):
            self.log(f"   ⚠ 레버리지 자동 조정 불가: {lv['error']} — 그대로 진행")

    def _confirm_pos_qty(self, get_qty, tries=3):
        """position_qty를 재시도해 '확정' 수량(>=0)을 얻는다. 조회 실패(크립토 −1 / 선물 None)는
        재시도, 끝까지 실패면 −1.0(불확실) 반환. 지정가 체결 확인을 조회 실패와 확실히 구분해
        시장가 이중진입을 막는 tonight-critical 가드(대표 2026-07-26)."""
        import time as _t
        q = None
        for _i in range(max(1, tries)):
            try:
                q = get_qty()
            except Exception:
                q = None
            if q is not None and q >= 0:
                return float(q)
            if _i < tries - 1:
                _t.sleep(0.6)
        return -1.0

    # ── 지정가 체결 정책 미러 (대표 2026-07-15, order_exec.execute_entry 레퍼런스와 동일) ──
    def _exec_entry_limit(self, b, sym, direction, size, stop, pol, live, broker, tag=None):
        """크립토 진입: 지정가(메이커) N회 재시도 → 불리 이동 임계 초과 시 스킵 → 시장가 폴백.
        체결 확인 = 포지션 수량(진입은 항상 flat에서 시작 — 잔여 청산 확인 후라서).
        반환은 place_entry와 동일 계약({entry|error|would_place...}) — 기존 후처리 재사용.
        tag = EQ 주문 표식(orderLinkId/clientOid). 재시도마다 -N을 붙여 유니크하게 —
        거래소가 중복 링크ID를 거절한다."""
        import time as _t
        lp = float(pol["limit_price"])
        if broker == "bitget":                        # 지정가·임계도 빗겟 좌표계로 보정
            _basis = _cross_basis_bitget()
            if _basis:
                lp = round(lp + _basis, 2)
        retries = int(pol.get("retries") or 3)
        interval = float(pol.get("retry_interval_s") or 3)
        thr = pol.get("skip_if_adverse_price")
        if not live:
            self.log(f"   DRY-RUN 체결정책: 지정가 {lp:g} ×{retries}회(간격 {interval:g}s) → "
                     f"불리 {thr}+ 스킵 → 시장가 폴백 · 손절 {stop}")
            return b.place_entry(symbol=sym, side=direction, size=size,
                                 stop_loss_price=stop, custom_tag=tag, dry_run=True)
        self._auto_leverage(b, sym, size, lp)
        self.log(f"   ⏳ 지정가 진입 도전 {lp:g} (메이커, ×{retries})")
        for i in range(retries):
            r = b.place_limit_entry(symbol=sym, side=direction, size=size, price=lp,
                                    stop_loss_price=stop,
                                    custom_tag=(f"{tag}-{i}" if tag else None), dry_run=False)
            oid = r.get("order_id")
            if r.get("error"):
                self.log(f"   ⚠ 지정가 주문 거절({r['error'][:80]}) → 시장가 폴백")
                break
            _t.sleep(max(1.0, interval))
            qty = self._confirm_pos_qty(lambda: b.position_qty(sym))
            if qty > 0:                               # 체결 확정 → 나머지 취소 후 인정
                if oid:
                    b.cancel_order(sym, oid)
                self.log(f"   ✅ 지정가 체결 (시도 {i + 1}, 수량 {qty:g}) — 메이커 수수료")
                return {"entry": {"orderId": oid, "mode": "limit", "price": lp},
                        "stop": bool(stop), "stop_error": None}
            if oid:
                b.cancel_order(sym, oid)              # 미체결/불확실 잔여 주문 정리
            if qty < 0:                               # 조회 불확실 → 재지정가 금지(이중진입 위험)
                self.log("   ⚠ 체결 확인 불가(조회 실패) — 재시도 중단, 폴백 전 포지션 재확인")
                break
        # 미체결 → 시장가 폴백 전, 지정가가 실은 체결됐는데 조회를 놓쳤는지 최종 확인
        # (놓친 채 시장가를 내면 2배 포지션 — tonight-critical, 대표 2026-07-26).
        _final = self._confirm_pos_qty(lambda: b.position_qty(sym))
        if _final > 0:
            self.log(f"   ✅ 최종 확인: 이미 체결됨(수량 {_final:g}) — 시장가 폴백 취소")
            return {"entry": {"orderId": None, "mode": "limit", "price": lp},
                    "stop": bool(stop), "stop_error": None}
        if _final < 0:
            self.log("   🛑 포지션 조회 불가로 체결 여부 불확실 — 이중진입 방지 위해 시장가 폴백 "
                     "보류. 앱에서 수동 확인 필요.")
            self.root.after(0, lambda a=sym: messagebox.showwarning(
                "체결 확인 불가" if self.lang == "ko" else "Fill unconfirmed",
                (f"{a}: 지정가 체결 여부를 확인하지 못했습니다(거래소 조회 실패). 이중진입을 막기 "
                 f"위해 시장가 진입을 보류했습니다 — 거래소에서 포지션을 직접 확인해 주세요." if self.lang == "ko"
                 else f"{a}: could not confirm the limit fill (exchange query failed). Market entry "
                 f"was held to avoid a double position — please check the position on the exchange.")))
            return {"skipped": True, "error": None, "entry": None, "would_place": None,
                    "stop": False, "stop_error": None, "note": "fill_unconfirmed"}
        # 확실히 미체결 → 불리 이동 3구간 판정(대표 2026-08-03, BTC 첫 스킵 사건 후 개정):
        #   ≤임계(0.30R) 풀사이즈 시장가 · 임계~캡(0.75R) 비중축소 시장가(리스크 1R 고정) · 캡 초과 스킵
        cur = b.current_market_price(sym)
        if cur is not None and thr is not None:
            adv = (cur - lp) if str(direction).upper() == "LONG" else (lp - cur)
            if adv > float(thr):
                _cap = pol.get("resize_cap_price")
                _r0 = pol.get("R")
                if _r0 is None and stop is not None:
                    _r0 = abs(lp - float(stop))
                if _cap is not None and _r0 and adv <= float(_cap):
                    # 수량 축소비 = 원거리/(원거리+불리이동) → 실거리 × 축소수량 = 원래 1R$
                    import math as _m
                    _shrink = float(_r0) / (float(_r0) + adv)
                    _sz2 = _m.floor(size * _shrink * 1000) / 1000.0   # 거래소 최소단위(0.001) 내림
                    if _sz2 > 0:
                        self.log(f"   ⚖ 비중 축소 진입 — 불리 {adv:+.2f}({adv / float(_r0):.2f}R) → "
                                 f"수량 ×{_shrink:.3f} ({size:g}→{_sz2:g}) · 리스크 1R 유지, 시장가")
                        return b.place_entry(symbol=sym, side=direction, size=_sz2,
                                             stop_loss_price=stop,
                                             custom_tag=(f"{tag}-R" if tag else None),
                                             dry_run=False)
                self.log(f"   ⛔ 진입 스킵 — 지정가 대비 불리 이동 {adv:+.2f} > "
                         f"{'캡 ' + str(_cap) if _cap is not None else '임계 ' + str(thr)} "
                         f"(손익비 보호, 이 신호는 버림)")
                self.root.after(0, lambda a=sym, v=adv: messagebox.showwarning(
                    "진입 스킵" if self.lang == "ko" else "Entry skipped",
                    (f"{a}: 지정가 미체결 + 가격이 {v:+.2f} 불리하게 이동 — 정책에 따라 이번 "
                     f"진입을 건너뜁니다." if self.lang == "ko" else
                     f"{a}: limit unfilled and price moved {v:+.2f} adversely — entry skipped "
                     f"per policy.")))
                return {"skipped": True, "error": None, "entry": None,
                        "would_place": None, "stop": False, "stop_error": None,
                        "note": "skipped_adverse"}
        self.log("   → 시장가 폴백")
        return b.place_entry(symbol=sym, side=direction, size=size,
                             stop_loss_price=stop, custom_tag=(f"{tag}-M" if tag else None),
                             dry_run=False)

    def _btc_x2be_step(self, b, j, live, utc_tz):
        """BTC 판정 시각(02/06/10/14/18/22 UTC) 1회 처리. 반환 True=청산 진행 / False=보유 유지.
        포지션 없으면 True(아래 flatten_all이 no-op으로 잔여 주문만 정리)."""
        import datetime as _d
        k = _BTC_BLOCK_K[j["hour"]]
        try:
            qty = b.position_qty("BTCUSDT") if hasattr(b, "position_qty") else None
        except Exception:
            qty = None
        if not qty:
            return True                       # 플랫 → 평소 경로(잔여 정리)
        pos = next((p for p in b.list_open_positions() if "BTC" in str(p.symbol).upper()), None)
        if pos is None:
            return True
        is_long = pos.net_qty > 0
        now = _d.datetime.now(utc_tz)
        end = now.replace(minute=0, second=0, microsecond=0)   # 방금 닫힌 블록의 마감시각
        blk = b.block_4h("BTCUSDT", end) if hasattr(b, "block_4h") else None
        o, c = (blk if blk else (None, None))
        d = self.x2be_decide(k, o, c, is_long)
        _dir = "LONG" if is_long else "SHORT"
        _bar = f"{o:g}→{c:g}" if blk else "봉 조회 실패"
        if d == "close":
            self.log(f"   📉 X2+TR 블록{k} [{_bar}] {_dir} → **청산**"
                     + (" (24h 만기)" if k >= 5 else " (반대봉 마감)"))
            return True
        if d == "trail":
            _lbl = "본절(=블록0 시가)" if k == 0 else f"{o:g}(블록{k} 시가)"
            self.log(f"   🛡 X2+TR 블록{k} [{_bar}] {_dir} 순항 → 손절을 **{_lbl}**로 이동, 홀드 유지")
            if live:
                self._btc_move_stop_be(b, pos, is_long, o)
            return False
        self.log(f"   ⏸ X2+TR 블록{k} [{_bar}] {_dir} → 홀드"
                 + ("  ⚠판정 불가라 보수적 홀드(24h 만기가 백스톱)" if not blk else ""))
        return False

    def _btc_move_stop_be(self, b, pos, is_long, target=None):
        """보호 손절을 target(순항한 블록의 시가)으로 이동. target=None이면 진입가(=평균단가).
        블록0의 시가는 진입가와 같으므로 k=0에서는 결과적으로 본절 이동이 된다.

        ⚠️손절은 **유리한 방향으로만** 옮긴다. 규칙상 순항 블록이 이어지면 시가는 단조 개선이라
        (o_{k+1}=c_k, c_k는 순항분) 역행할 일이 없지만, 봉 데이터가 튀는 경우에 손절이 느슨해지면
        리스크가 커지므로 여기서 한 번 더 막는다. 실패해도 홀드 유지 — 기존 손절이 살아 있어
        무방비가 아니다(로그만 경고)."""
        try:
            entry = float(pos.raw.get("avgPrice") or pos.raw.get("entryPrice") or 0)
            tgt = float(target) if target is not None else entry
            if not tgt:
                self.log("   ⚠ 손절 이동 실패: 목표가 조회 불가 — 기존 손절 유지"); return
            cur = None
            try:
                cur = float(pos.raw.get("stopLoss") or 0) or None
            except Exception:
                cur = None
            if cur is not None and ((tgt < cur) if is_long else (tgt > cur)):
                self.log(f"   ⏸ 손절 이동 생략 — 목표 {tgt:g}가 현재 손절 {cur:g}보다 불리(느슨해짐 방지)")
                return
            r = b.set_stop("BTCUSDT", tgt) if hasattr(b, "set_stop") else None
            if r is None:
                self.log(f"   ⚠ 손절 이동 미지원(어댑터) — 기존 손절 유지 (목표 {tgt:g})"); return
            if r.get("error"):
                self.log(f"   ⚠ 손절 이동 실패({str(r['error'])[:60]}) — 기존 손절 유지"); return
            self.log(f"   ✅ 손절 → {tgt:g} 이동 완료" + (" (본절)" if entry and abs(tgt - entry) < 1e-9 else ""))
        except Exception as e:
            self.log(f"   ⚠ 손절 이동 예외({e}) — 기존 손절 유지")

    # ── BTC 조건부 출구 X2+TR (2026-07-15 챔피언, 대표 아이디어) ────────────────────
    # 규칙: 일 22:00 UTC 진입 → 4H 블록(22-02, 02-06, 06-10, 10-14, 14-18, 18-22)마다 마감 시
    #   ① 방금 닫힌 블록이 **포지션 반대 방향**으로 마감 → 즉시 청산
    #   ② 블록이 **순항 마감** → 보호 손절을 **그 블록의 시가**로 이동, 계속 홀드
    #      ⭐블록0의 시가 == 진입가라 '첫 봉 순항 → 본절'이 이 규칙에서 **자동으로** 나온다
    #        (X2+BE의 k==0 특수분기가 소멸 = 규칙이 하나로 통일됨. 실측 차이 0.0)
    #      k≥1은 직전 블록 종가(=순항분)를 손절로 삼아 이익을 조금씩 확정 = 트레일링
    #   ③ 마지막(18-22, 24h) → 무조건 청산
    #   손절은 브로커 스탑이 상시 감시(앱 개입 없음).
    # 검증: 대안 출구 대비 우위 · MDD 5셀 전부 개선 · 부트스트랩 개선
    #       · 평일 음성대조군으로 엣지 실재 확인 · 블록0 항등 실측.
    # ⚠판정 불가(봉 누락·API 실패)면 **홀드** — 잘못 닫느니 두고, 24h 만기가 최종 백스톱.
    # ⚠️서버 archive_resolver.btc_x2be_resolve와 **같은 규칙**이어야 트랙레코드가 진실이다
    #   (scripts/x2be_parity_check.py로 3자 대조).
    @staticmethod
    def x2be_decide(k, blk_open, blk_close, is_long):
        """블록 k(0~5) 마감 시 결정. 반환: 'close' | 'trail' | 'hold'.
        'trail'이면 손절을 blk_open으로 옮긴다(k=0이면 그게 곧 진입가 = 본절).
        순수 함수 — 백테스트 규칙과 1:1 대조 가능하게 IO 분리."""
        if blk_open is None or blk_close is None:
            return "hold"                       # 판정 불가 → 보수적 홀드
        if k >= 5:
            return "close"                      # 24h 만기
        against = (blk_close < blk_open) if is_long else (blk_close > blk_open)
        if against:
            return "close"
        return "trail"                          # 순항 → 그 봉 시가로 손절 이동

    def _exec_entry_limit_fut(self, b, aid, contract, direction, size, stop, pol, live, tag):
        """선물 진입 지정가 정책 — 크립토 _exec_entry_limit의 선물판(ProjectX).
        지정가(type 1) N회 재시도 → 미체결+불리 이동 임계 초과 시 스킵 → 시장가 폴백.
        ⚠크립토와 다른 점: 보호 손절이 주문에 첨부되지 않으므로 **체결 확인 후** 별도로 건다
        (미체결 상태에서 스탑부터 걸면 무포지션 스탑이 남는다).
        반환은 place_entry와 동일 계약({entry|error|stop|stop_error|skipped}) — 후처리 재사용."""
        import time as _t
        lp = float(pol["limit_price"])
        retries = int(pol.get("retries") or 3)
        interval = float(pol.get("retry_interval_s") or 3)
        thr = pol.get("skip_if_adverse_price")
        if not live:
            self.log(f"   DRY-RUN 체결정책(선물): 지정가 {lp:g} ×{retries}회(간격 {interval:g}s) → "
                     f"불리 {thr}+ 스킵 → 시장가 폴백 · 손절 {stop}")
            return b.place_entry(account_id=aid, contract_id=contract, side=direction, size=size,
                                 order_type=2, stop_loss_price=stop, custom_tag=tag, dry_run=True)
        self.log(f"   ⏳ 지정가 진입 도전 {lp:g} (×{retries}) — 슬리피지 회피")
        for i in range(retries):
            r = b.place_limit_entry(aid, contract, direction, size, lp, custom_tag=f"{tag}-L{i}")
            oid = r.get("order_id")
            if r.get("error"):
                self.log(f"   ⚠ 지정가 주문 거절({str(r['error'])[:80]}) → 시장가 폴백")
                break
            _t.sleep(max(1.0, interval))
            qty = self._confirm_pos_qty(lambda: b.position_qty(aid, contract))
            if qty > 0:                               # 체결 확정
                if oid:
                    try:
                        b.cancel_order(aid, oid)      # 잔여 미체결 취소
                    except Exception:
                        pass
                self.log(f"   ✅ 지정가 체결 (시도 {i + 1}, 수량 {qty:g}) — 슬리피지 0")
                sr = b.place_protective_stop(aid, contract, direction, qty, stop,
                                             custom_tag=f"{tag}-{i}") if stop is not None else {}
                return {"entry": {"orderId": oid, "mode": "limit", "price": lp}, **sr}
            if oid:
                try:
                    b.cancel_order(aid, oid)
                except Exception:
                    pass
            if qty < 0:                               # 조회 불확실 → 재지정가 금지(이중진입 위험)
                self.log("   ⚠ 체결 확인 불가(조회 실패) — 재시도 중단, 폴백 전 포지션 재확인")
                break
        # 시장가 폴백 전 최종 확인 — 지정가가 실은 체결됐는데 조회를 놓쳤다면 이중진입 방지.
        _final = self._confirm_pos_qty(lambda: b.position_qty(aid, contract))
        if _final > 0:
            self.log(f"   ✅ 최종 확인: 이미 체결됨(수량 {_final:g}) — 시장가 폴백 취소")
            sr = b.place_protective_stop(aid, contract, direction, _final, stop,
                                         custom_tag=f"{tag}-F") if stop is not None else {}
            return {"entry": {"orderId": None, "mode": "limit", "price": lp}, **sr}
        if _final < 0:
            self.log("   🛑 포지션 조회 불가로 체결 여부 불확실 — 이중진입 방지 위해 시장가 폴백 보류.")
            return {"skipped": True, "error": None, "entry": None, "stop": False,
                    "stop_error": None, "note": "fill_unconfirmed"}
        cur = b.current_market_price(contract)
        if cur is not None and thr is not None:
            adv = (cur - lp) if str(direction).upper() == "LONG" else (lp - cur)
            if adv > float(thr):
                self.log(f"   ⛔ 진입 스킵 — 지정가 대비 불리 이동 {adv:+.2f} > 임계 {thr} "
                         f"(손익비 보호, 이 신호는 버림)")
                return {"skipped": True, "error": None, "entry": None, "stop": False,
                        "stop_error": None, "note": "skipped_adverse"}
        self.log("   → 시장가 폴백")
        return b.place_entry(account_id=aid, contract_id=contract, side=direction, size=size,
                             order_type=2, stop_loss_price=stop, custom_tag=tag, dry_run=False)

    def _exec_close_limit(self, b, sym, retries=3, interval=3.0):
        """크립토 시간마감 청산: 현재가 지정가(메이커) N회 도전 — 남으면 호출측 flatten이
        시장가로 마무리(스킵 없음, order_exec.execute_exit 미러)."""
        import time as _t
        try:
            if not (b.position_qty(sym) > 0):
                return                                  # 이미 플랫
            self.log(f"   ⏳ 청산 지정가 도전 (메이커, ×{retries})")
            for i in range(retries):
                px = b.current_market_price(sym)
                if px is None:
                    return                              # 가격 조회 불가 → 곧장 시장가(flatten)
                r = b.place_limit_close(symbol=sym, price=px, dry_run=False)
                if r.get("flat"):
                    self.log("   ✅ 청산 완료 (지정가)")
                    return
                oid = r.get("order_id")
                if r.get("error"):
                    self.log(f"   ⚠ 청산 지정가 거절({str(r['error'])[:80]}) → 시장가")
                    return
                _t.sleep(max(1.0, interval))
                if not (b.position_qty(sym) > 0):
                    self.log(f"   ✅ 청산 체결 (지정가, 시도 {i + 1}) — 메이커 수수료")
                    if oid:
                        b.cancel_order(sym, oid)
                    return
                if oid:
                    b.cancel_order(sym, oid)
            self.log("   → 지정가 미체결 — 시장가로 마무리")
        except Exception as e:
            self.log(f"   ⚠ 청산 지정가 단계 오류({str(e)[:80]}) → 시장가로 마무리")

    def _exec_close_limit_fut(self, b, asset, retries=3, interval=3.0):
        """선물(GC) 시간마감 청산: 현재가 지정가(반대편 type1) N회 도전 — 남으면 호출측
        flatten_all이 시장가+잔여주문 취소로 마무리(스킵 없음, order_exec.execute_exit 미러).
        _exec_close_limit(크립토)의 선물판: 계좌·계약은 열린 포지션에서 발견, 부분체결은
        다음 시도에서 잔량 기준 재주문. 보호 스탑은 건드리지 않는다 — 청산 미체결 동안
        살아 있어야 하고, 체결 후 고아 스탑은 직후 flatten_all이 취소한다(projectx 설계).
        어떤 실패든 조용히 반환 → 시장가 백스톱이 반드시 마무리(청산 실패 불가)."""
        import time as _t
        sym = _LIMIT_EXIT_FUT.get(asset)
        if not sym:
            return
        try:
            poss = [p for p in b.list_open_positions() if sym in str(p.symbol)]
            if not poss:
                return                                # 이미 플랫
            self.log(f"   ⏳ {asset} 청산 지정가 도전 (×{retries}) — 슬리피지 회피")
            for p in poss:
                aid = p.raw.get("_accountId") or p.account_id
                con = p.raw.get("contractId") or p.symbol
                orig_long = p.net_qty > 0                  # 청산 대상 포지션의 원래 방향
                close_side = "SHORT" if orig_long else "LONG"
                for i in range(retries):
                    # 부호 있는 순포지션 재확인 — projectx는 reduceOnly가 없어, 보호 스탑이 먼저
                    # 터진 뒤 청산 지정가가 '새 반대 포지션'을 여는 레이스가 있다. 매 시도 부호를
                    # 확인해 반전을 감지하면 즉시 중단하고 시장가 flatten에 위임한다(대표 2026-07-26).
                    net = None
                    try:
                        for _q in b.list_open_positions():
                            if str(con) == str(_q.symbol) or str(con) in str(_q.symbol):
                                net = _q.net_qty
                                break
                        else:
                            net = 0.0
                    except Exception:
                        net = None
                    if net is None:
                        return                        # 포지션 조회 실패 → 곧장 시장가(flatten)
                    if net == 0:
                        self.log("   ✅ 청산 체결 (지정가) — 슬리피지 0")
                        break
                    if (net > 0) != orig_long:         # 부호 반전 = 청산이 반대 포지션을 열었음
                        self.log("   🛑 청산 중 반대 포지션 감지 — 지정가 청산 중단, 시장가 flatten 위임")
                        return
                    qty = abs(int(net))
                    px = b.current_market_price(con)
                    if px is None:
                        return                        # 가격 조회 불가 → 시장가(flatten)
                    r = b.place_limit_entry(aid, con, close_side, qty, px,
                                            custom_tag=f"EQ-XC-{int(_t.time() * 1000)}")
                    if r.get("error"):
                        self.log(f"   ⚠ 청산 지정가 거절({str(r['error'])[:80]}) → 시장가")
                        return
                    oid = r.get("order_id")
                    _t.sleep(max(1.0, interval))
                    if oid:
                        try:
                            b.cancel_order(aid, oid)  # 미체결 잔여 취소(재주문/flatten과 충돌 방지)
                        except Exception:
                            pass
                    left = b.position_qty(aid, con)
                    if left == 0:
                        self.log(f"   ✅ 청산 체결 (지정가, 시도 {i + 1}) — 슬리피지 0")
                        break
                else:
                    self.log("   → 지정가 미체결 — 시장가로 마무리")
        except Exception as e:
            self.log(f"   ⚠ 청산 지정가 단계 오류({str(e)[:80]}) → 시장가로 마무리")

    # ── 진입 전 API 사전 점검 (대표 2026-07-13) ──────────────────────────────
    # 무장된 자산마다 다음 진입 70분 전~진입 사이에 1회, 그 자산 브로커 API를 실제 인증해 본다.
    # 실패=팝업+로그(키·IP·네트워크 문제를 진입 전에 발견), Bybit는 키 만료 임박·권한도 경고.
    def _precheck_tick(self):
        try:
            from datetime import datetime
            # 무장 계좌 → (브로커, 자산) 조합 수집(중복 제거) — 점검은 브로커 크레덴셜 단위
            # (대표 2026-07-24 멀티계좌: 같은 브로커 여러 계좌는 크레덴셜이 같아 1회면 충분).
            seen = {}
            for (_a, _idx), _cfg in list(self._sig_accts.items()):
                seen.setdefault((_cfg["broker"], _a), _cfg)
            for (_a, _idx), _jobs in list(self._auto_accts.items()):
                for _j in _jobs:
                    seen.setdefault((_j["broker"], _j["asset"]), _j)
            for (bk, a), cfg in seen.items():
                ent = _next_entry_dt(a)
                if ent is None:
                    continue
                mins = (ent - datetime.now(ent.tzinfo)).total_seconds() / 60.0
                key = (bk, a, ent.isoformat())
                if not (0 < mins <= PRECHECK_WINDOW_MIN) or self._precheck_done.get(key):
                    continue
                self._precheck_done[key] = True
                threading.Thread(target=self._precheck_run,
                                 args=(a, dict(cfg), ent.strftime("%H:%M %Z")),
                                 daemon=True).start()
        except Exception:
            pass
        finally:
            self.root.after(5 * 60 * 1000, self._precheck_tick)

    def _precheck_run(self, asset, cfg, entry_label):
        ko = self.lang == "ko"
        try:
            b = _build_broker(cfg.get("broker"), cfg.get("f1", ""), cfg.get("f2", ""),
                              cfg.get("f3", ""), [cfg.get("acct")] if cfg.get("acct") else [])
            b.healthcheck()
            warns = []
            if hasattr(b, "key_info"):
                try:
                    warns = b.key_info() or []
                except Exception:
                    warns = []
            if warns:
                _w = "\n".join(f"· {w}" for w in warns)
                self.log(f"⚠ {asset} 사전 점검 경고 (진입 {entry_label}):")
                for w in warns:
                    self.log(f"   · {w}")
                self.root.after(0, lambda: messagebox.showwarning(
                    "API 사전 점검" if ko else "API pre-check",
                    (f"{asset} 진입({entry_label}) 전 점검에서 경고가 있습니다:\n\n{_w}"
                     if ko else
                     f"Pre-entry check for {asset} ({entry_label}) has warnings:\n\n{_w}")))
            else:
                self.log(f"🩺 {asset} API 사전 점검 통과 — 진입 {entry_label} 준비 완료"
                         f" ({_broker_label(cfg.get('broker'))})")
        except Exception as e:
            _em = str(e)[:300]
            _hint = _entry_fail_hint(_em, ko)
            self.log(f"❌ {asset} API 사전 점검 실패 (진입 {entry_label}): {_em}")
            self.root.after(0, lambda: messagebox.showerror(
                "API 사전 점검 실패" if ko else "API pre-check failed",
                (f"{asset} 진입({entry_label}) 1시간 전 점검에서 API 호출이 실패했습니다:\n\n{_em}\n\n{_hint}"
                 if ko else
                 f"The pre-entry API check for {asset} ({entry_label}) failed:\n\n{_em}\n\n{_hint}")))

    # ── 공개 트랙레코드 푸시 (Phase B 2단계) ─────────────────────────────────
    @staticmethod
    def _fill_asset(symbol: str):
        """체결 심볼 → 자산. 마이크로(MGC·MNQ)와 풀사이즈(GCE·ENQ·GC·NQ)를 **모두** 인식한다
        (대표 2026-07-24 실사고: 미니/마이크로 분할 진입 시 풀 GC='GCE'가 None으로 드롭돼
        $3578 체결이 트랙레코드에서 통째로 누락). ProjectX 계약ID='CON.F.US.<ROOT>.<만기>'라
        ROOT로 판별하고, 형식이 달라도 티커 토큰으로 폴백. 모르면 None(제외)."""
        s = str(symbol or "").upper()
        if "BTC" in s:
            return "BTC"
        parts = s.split(".")
        root = parts[3] if len(parts) >= 5 and parts[0] == "CON" else s
        if root in ("MGC", "GCE", "GC"):
            return "GC"
        if root in ("MNQ", "ENQ", "NQ"):
            return "NQ"
        # 폴백: CON 형식이 아니어도 명확한 티커 토큰이 있으면 매핑
        for _tok, _a in (("MGC", "GC"), ("GCE", "GC"), ("MNQ", "NQ"), ("ENQ", "NQ")):
            if _tok in s:
                return _a
        return None

    @staticmethod
    def _fill_is_eq(f, asset: str, ledger: list, since_ms: float) -> bool:
        """이 체결이 EQ 진입에서 나온 것인가 — 회원의 수동 거래를 트랙레코드에서 배제(대표 2026-07-17).
        원장 시작(since_ms) 이전 체결은 근거가 없으니 포함(옛 방식 유지 — 과거 기록 보존).
        인정 조건(둘 중 하나):
          (a) 같은 **계약 심볼**의 EQ 진입이 있고 그 진입 이후(−5분)~계약 매칭창(기본 7일) 안 —
              풀계약(GCE)을 12시간 넘겨 수동 보유 후 청산해도 그 익절이 잡힌다(대표 2026-07-29
              실사고: GCE 분할진입을 늦게 청산→시간창 밖 드롭→트랙레코드 6.6R이 0.59R로 반토막).
          (b) (폴백) 같은 자산의 EQ 진입이 있고 보유창(자산별 12h 등) 안 — 심볼이 안 실린 옛 진입 대비.
        ⚠ 한계: 같은 계약을 EQ 청산 후 회원이 손수 재거래하면 매칭창 안에선 구분 못 한다(희소)."""
        ts = int(f.get("ts_ms") or 0)
        if ts < since_ms:
            return True
        fsym = str(f.get("symbol") or "")
        sym_win = _SYM_MATCH_DAYS * 86400 * 1000       # (a) 계약심볼 매칭창
        hold_ms = _LEDGER_HOLD_H.get(asset, 12.0) * 3600 * 1000   # (b) 자산 보유창
        for r in ledger:
            if r.get("asset") != asset:
                continue
            e = int(r.get("ts_ms") or 0)
            rsym = str(r.get("symbol") or "")
            # (a) 계약 심볼 일치 → 늦은 청산도 인정(진입−5분 ~ +7일)
            if fsym and rsym and fsym == rsym and (e - 300_000) <= ts <= (e + sym_win):
                return True
            # (b) 폴백: 자산 보유창
            if e - 300_000 <= ts <= e + hold_ms:
                return True
        return False

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
        self._save_cfg()
        self.log("🔓 공개 트랙레코드: 공개 동의 " + ("ON — 다음 동기화 때 페이지 공개"
                 if self.tr_public.get() else "OFF — 다음 동기화 때 페이지 비공개"))

    def _open_my_page(self):
        h = (self._profile or {}).get("handle")
        if h:
            webbrowser.open(f"{PUSH_BASE}?u={h}")

    AUTOPUSH_EVERY_S = 24 * 3600      # 일일 자동 동기화 주기

    def _build_watch(self):
        """빌드 교체 감지 (2026-07-27 GC 미진입 실사고 — 앱을 켜둔 채 실행 파일이 새 빌드로
        교체되면, 지연 임포트가 새 파일을 옛 오프셋으로 읽다 진입 스레드가 죽는다).
        5분마다 실행 파일 지문(mtime·size)을 확인, 바뀌면 로그 + 경고 팝업 1회.
        자동 재시작은 하지 않는다(포지션 보유 중일 수 있음). EQ_BUILD_WATCH=0 으로 끔."""
        import os as _os
        import sys as _sys
        import time as _t
        if _os.environ.get("EQ_BUILD_WATCH", "1") == "0":
            return
        exe = _sys.executable
        try:
            _st = _os.stat(exe)
            fp = (_st.st_mtime, _st.st_size)
        except OSError:
            return
        while True:
            _t.sleep(300)
            try:
                _st = _os.stat(exe)
                changed = (_st.st_mtime, _st.st_size) != fp
            except OSError:
                continue                       # 교체 순간 일시 부재 — 다음 사이클 재확인
            if changed:
                _ko = self.lang == "ko"
                self.log("\n🔁 " + ("새 버전이 배포되었습니다 — 실행 파일이 바뀌었습니다. "
                                    "포지션이 없는 시점에 앱을 재시작하세요. 재시작 전까지 "
                                    "새 기능 로딩이 실패할 수 있습니다." if _ko else
                                    "A new build was deployed — the executable changed. Restart "
                                    "the app when no position is open; until then, loading new "
                                    "modules may fail."))
                _pt = "새 버전 배포됨 — 재시작 필요" if _ko else "New build deployed — restart needed"
                _pm = (("EQ Autopilot의 새 버전이 배포되었습니다.\n\n켜 둔 앱은 예전 버전인 채로 "
                        "돌다가, 새 기능을 처음 부르는 순간 오류가 날 수 있습니다.\n\n포지션이 "
                        "없는 시점에 앱을 종료했다가 다시 실행해 주세요.") if _ko else
                       ("A new build of EQ Autopilot was deployed.\n\nThis running app is still the "
                        "old version and may fail the first time it loads a new module.\n\nPlease "
                        "quit and relaunch when no position is open."))
                self.root.after(0, lambda t=_pt, m=_pm: messagebox.showwarning(t, m))
                return                          # 1회 경고 후 종료(스팸 방지)

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
        # creds 스냅샷(메인 스레드) — 자산별 계좌(대표 2026-07-24): 자산마다 브로커 크레덴셜 +
        # 등록 계좌 전부. 계좌마다 자기 1R을 함께 실어, 아래서 $손익합÷참여계좌 1R합으로 배수를
        # 보존한다(전 계좌 합산). 같은 (브로커,f1,계좌ID)는 조회 시 중복 제거(seen)된다.
        credlist = []
        for a in _ASSETS:
            bk = self._broker_of(a)
            cr = self._creds_of(a)
            f1 = (cr.get("f1") or "").strip()
            if not f1:
                continue
            _sp = _BROKER_SPEC.get(bk, {})
            for ac in self._accts_of(a):
                aid = (ac.get("id") or "").strip()
                if _sp.get("acct") and not aid:
                    continue
                _prc = ac.get("prop") or {}
                _tr_r = (_as_float(_prc.get("r_steady"), 600.0) if _prc.get("on")
                         else _as_float(ac.get("one_r"), 600.0))
                credlist.append({"broker": bk, "f1": f1, "f2": (_kc_load(f1) or ""),
                                 "f3": cr.get("f3", ""), "acct": aid,
                                 "one_r": _tr_r,
                                 "label": ac.get("label", "")})
        if not credlist:
            if not auto:
                messagebox.showwarning(self.t("input_needed"),
                                       "브로커·키가 설정된 계좌가 없습니다." if self.lang == "ko"
                                       else "No account has broker credentials configured.")
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
            # 계좌 id → 1R 맵(설정 계좌). 로테이션으로 빠진 계좌는 여기 없어 폴백(대표 2026-07-29).
            _r_by_acct = {c["acct"]: c["one_r"] for c in credlist if c.get("acct")}
            _R_FALLBACK = 600.0
            # 로그인(브로커,f1) 단위로 1회 조회 — closed_fills가 그 로그인의 '전 계좌(비활성 포함)'
            # 체결을 계좌 id 부착해 돌려준다. 계좌별 1R은 위 맵으로, 없으면 폴백(닫힌 계좌).
            seen = set()
            for c in credlist:
                key = (c["broker"], c["f1"])
                if key in seen:
                    continue
                seen.add(key)
                try:
                    b = _build_broker(c["broker"], c["f1"], c["f2"], c["f3"], [])   # 계좌 미지정=전부
                    got = b.closed_fills(start_ms)
                    for f in got:
                        _fa = str(f.get("acct") or "")
                        f["_one_r"] = _r_by_acct.get(_fa, _R_FALLBACK)   # 닫힌 계좌=폴백 1R
                        f["_acct_id"] = _fa or c["broker"]
                    fills.extend(got)
                    _n_acct = len({f.get("acct") for f in got})
                    self.log(f"   {_broker_label(c['broker'])}: 체결 {len(got)}건 "
                             f"(계좌 {_n_acct}개 — 로테이션으로 닫힌 계좌 포함)")
                except AttributeError:
                    self.log(f"   {_broker_label(c['broker'])}: 체결 이력 미지원(지원 예정) — 건너뜀")
                except Exception as e:
                    self.log(f"   ⚠ {_broker_label(c['broker'])} 체결 조회 실패: {e}")
            # (date, asset[, BTC세션])별 합산 → trade 1건. NQ/GC=하루 1거래, BTC=하루 2세션(22·02).
            import datetime as _dtd

            def _btc_sess(dt):
                m = dt.hour * 60 + dt.minute
                return "22" if (m < 125 or m >= 840) else "02"

            # EQ 원장 대조 — 회원의 수동 거래 배제(대표 2026-07-17). admin 등급은 수동도 포함.
            _admin_all = str((self._gate or {}).get("tier") or "") == "admin"
            _ledger, _since = _ledger_load(), _ledger_since()
            try:                                     # 실제 1R 원장 로드(A안 트랙레코드 R 정확화)
                import json as _json2
                with open(_RLEDGER_PATH, encoding="utf-8") as _rf:
                    _rledger = _json2.load(_rf)
                if not isinstance(_rledger, dict):
                    _rledger = {}
            except Exception:
                _rledger = {}
            _mine = 0
            agg = {}
            for f in fills:
                a = self._fill_asset(f.get("symbol"))
                if not a:
                    continue
                if not _admin_all and not self._fill_is_eq(f, a, _ledger, _since):
                    _mine += 1
                    continue
                dt = _dtd.datetime.fromtimestamp((f.get("ts_ms") or 0) / 1000, _dtd.timezone.utc)
                d = dt.date().isoformat()
                k = (d, a, _btc_sess(dt) if a == "BTC" else "")
                e = agg.setdefault(k, {"pnl": 0.0, "direction": f.get("direction", "LONG"),
                                       "accts": {}, "base": {}})
                e["pnl"] += float(f.get("pnl") or 0)
                # 참여 계좌 1R = 그 체결 시각 직전 진입의 '실제 1R'(R 원장). 없으면 계좌 명목값
                # 폴백. 프롭 페이즈·자본 비례로 시점마다 1R이 달라도 정확히 정규화(대표 A안).
                _aid = f.get("_acct_id")
                _fb = float(f.get("_one_r") or 600.0)
                e["accts"][_aid] = _lookup_real_r(
                    _rledger, a, _aid, (f.get("ts_ms") or 0) / 1000.0, _fb)
                e.setdefault("base", {})[_aid] = _lookup_baseline_r(_rledger, a, _aid, _fb)
            # R 정규화(대표 2026-07-24 멀티계좌) = **$손익 합 ÷ 참여 계좌 1R 합** — 시그널의 진짜
            # 배수를 보존한다(계좌 A +$1200@1R600 + 계좌 B +$600@1R300 = $1800÷$900 = +2R,
            # 계좌 수만큼 뻥튀기 안 됨). 계좌당 단일 1R·자산 등가중.
            trades = []
            for (d, a, s), v in sorted(agg.items()):
                _rsum = sum(v["accts"].values()) or 600.0
                # 규모 배수 = 현재 1R 합 ÷ 그 회원 '첫 진입 1R 합'(개인 기준선). 공개 상수 600으로
                # 나누면 서버가 rsum=scale×600으로 절대 1R을 역산할 수 있어 개인정보 유출 →
                # 회원마다 다른(서버 미지) baseline으로 나눠 역산·상호비교를 막는다(대표 2026-07-26).
                # 크기 안 바꾼 회원은 scale=1.0 유지, 자본 키운 회원만 배수가 커진다.
                _bsum = sum(v.get("base", {}).values()) or _rsum
                trades.append({"tid": f"agg-{d}-{a}" + (f"-{s}" if s else ""), "date": d,
                               "instrument": a, "direction": v["direction"],
                               # r = 손익비 = 그날 손익 ÷ 그날 실제 1R 합(사이징 무관, 절대 비교 가능).
                               # cash_rel = r과 동일 계산이나 공개 페이지는 이를 '누적한 뒤' 곡선의
                               # 매 점을 그 시점 규모로 재정규화한다 → 자본 투입·복리가 다이나믹하게
                               # 반영(대표 2026-07-26 "곡선이 날마다 업뎃돼도 됨. 어차피 상대금액").
                               # 절대 달러는 전송하지 않는다(개인정보). 규모 정보는 서버가 별도 산출.
                               "r": round(v["pnl"] / _rsum, 3),
                               "scale": round(_rsum / _bsum, 4)})   # 개인 기준선 대비 규모 배수
            if _mine:
                self.log(f"   ⊘ EQ 원장에 없는 체결 {_mine}건 제외(직접 하신 거래 — 트랙레코드 미포함)")
            if not trades:
                self.log("   체결 없음 — 푸시할 내용이 없습니다.")
                return
            self.log(f"   일별 합산 {len(trades)}건 → 푸시 (키·잔고 무전송)")
            # 계좌당 단일 1R·자산 등가중(대표 2026-07-24) — 자산 간 포트폴리오 비중은 균등이라 미동봉.
            risk_weights = None
            pid = autopilot_crypto.path_id(tok)
            ok_total, srv_handle = None, None
            for i in range(0, len(trades), TR_CHUNK):
                payload = {"public": public, "trades": trades[i:i + TR_CHUNK]}
                if risk_weights:
                    payload["risk_weights"] = risk_weights
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

    _APP_VER = "2026.08.09"

    # ── 체결 수량 보고 (#53, 대표 2026-08-08 "앱은 몇 거래 체결했는지만 보내면 대") ────
    # 왜 수량만 보내는가: 나머지는 서버가 이미 안다 - 진입가·손절은 발송 카드에, 현재가는
    # 실시간으로. 수량 하나면 금액이 나온다:
    #     미실현$ = (현재가 − 진입가) × 수량 × 틱가치
    # 보내는 것: {자산, 수량, 계좌 수, 무장 여부}. 계좌번호·잔고·사이징·체결가는 안 보낸다.
    # 앱은 상시 떠들지 않는다(대표 "그 담 한 일분간만, 오분 최대") - 진입이 끝난 시점에 1회,
    # 실패하면 5분 창 안에서만 재시도. 그 뒤 미실현은 서버가 현재가로 계속 계산한다.
    # ⚠️ 발주 경로에는 손대지 않는다 - 이 호출은 전부 별도 스레드이고, 실패해도 조용하다.
    _FILL_RETRY_WINDOW_S = 300      # 5분 창 - 그 뒤로는 포기(서버는 현재가로 계속 계산)

    def _note_fill(self, asset, micro=0.0, coin=0.0):
        """진입 leg 하나가 체결될 때마다 자산별로 누적. 멀티 계좌·멀티 leg를 합산해서
        보낸다(대표 "그건 합산해서 보여주기") - 계좌별로 보내면 서버가 계좌 단위 정보를
        들고 있게 되고 카드도 길어진다."""
        try:
            import threading as _th
            if not hasattr(self, "_fill_lock"):
                self._fill_lock = _th.Lock()
                self._fill_acc = {}
            with self._fill_lock:
                _d = self._fill_acc.setdefault(str(asset), {"micro": 0.0, "coin": 0.0, "n": 0})
                _d["micro"] += float(micro or 0)
                _d["coin"] += float(coin or 0)
                _d["n"] += 1
        except Exception:
            pass

    def _send_fill(self, asset, closed=False):
        """자산 하나의 진입이 끝난 뒤 1회 전송. closed=True면 수량 0(청산 알림).
        Autopilot(autoentry) 전용(대표 2026-08-09 '오토파일럿한테만 앱에서 서버로') —
        대시보드 앱 상태를 보는 등급도 Autopilot뿐이라 그 외 등급은 아예 안 보낸다.
        서버(/eqfill)도 같은 게이트로 이중 방어(fl:ignored)."""
        _caps = (self._gate or {}).get("caps", {})
        if not ((self._gate or {}).get("ok") and _caps.get("autoentry")):
            return
        import threading as _th
        import time as _t

        def w():
            try:
                import requests
                try:
                    import autopilot_crypto
                    tid = autopilot_crypto.path_id(self._token or "")
                except Exception:
                    return                      # 토큰 없으면 보낼 곳이 없다
                if not tid:
                    return
                if closed:
                    body = {"tok_id": tid, "inst": str(asset), "qty_micro": 0, "qty_btc": 0}
                else:
                    if not hasattr(self, "_fill_lock"):
                        return              # 이 자산에서 체결된 게 없다
                    with self._fill_lock:
                        _d = (self._fill_acc or {}).pop(str(asset), None)
                    if not _d:
                        return
                    body = {"tok_id": tid, "inst": str(asset),
                            "accounts": int(_d.get("n") or 0)}
                    if str(asset) == "BTC":
                        body["qty_btc"] = round(float(_d.get("coin") or 0), 6)
                    else:
                        body["qty_micro"] = round(float(_d.get("micro") or 0), 2)
                    if not (body.get("qty_btc") or body.get("qty_micro")):
                        return                  # 체결 0 - 보낼 게 없다
                # 무장 = 자동청산 루프가 실제로 돌고 있는가(_auto_on). 2026-08-05 이틀간 조용히
                # 무장해제됐던 사고를 대시보드가 빨갛게 잡아주도록 같이 보낸다.
                body["armed"] = bool(getattr(self, "_auto_on", False))
                _t0 = _t.time()
                while _t.time() - _t0 < self._FILL_RETRY_WINDOW_S:
                    try:
                        r = requests.post(PUSH_BASE + "eqfill", timeout=8, json=body)
                        # fl:ignored = 서버가 등급상 안 받음(Autopilot 전용) — 재시도 무의미
                        if r.ok and str(r.text).startswith(("fl:ok", "fl:ignored")):
                            return
                    except Exception:
                        pass
                    _t.sleep(20)                # 5분 창 안에서만 - 그 뒤엔 조용히 포기
            except Exception:
                pass
        _th.Thread(target=w, daemon=True).start()

    def _report_error(self, ctx, err):
        """예외 자동 리포트(대표 2026-07-27 "필수") — 서버 /eqerr로 익명 전송해 회원 머신의
        버그를 운영자가 본다("대표의 발견력을 회원 수만큼 스케일"). 전송 내용: 앱버전·OS·
        컨텍스트 태그·마스킹된 에러 문자열·토큰 해시 12자(익명 그룹핑)뿐 — 키·계좌번호·잔고
        무전송(5자리+ 숫자열 → # 마스킹). 중복 1시간 억제·시간당 10건 캡. 실패해도 조용히."""
        import os as _os
        import re as _re
        import time as _t
        import threading as _th
        try:
            if _os.environ.get("EQ_ERR_REPORT", "1") == "0":
                return
            msg = _re.sub(r"\d{5,}", "#", str(err))[:400]
            key = (str(ctx)[:40], msg[:80])
            now = _t.time()
            self._err_sent = {k: v for k, v in getattr(self, "_err_sent", {}).items()
                              if now - v < 3600}
            if key in self._err_sent or len(self._err_sent) >= 10:
                return
            self._err_sent[key] = now

            def w():
                try:
                    import hashlib
                    import platform
                    import requests
                    tid = hashlib.sha256((self._token or "").encode()).hexdigest()[:12]
                    requests.post(PUSH_BASE + "eqerr", timeout=6, json={
                        "ver": self._APP_VER, "os": platform.system(),
                        "tid": tid, "ctx": str(ctx)[:60], "err": msg})
                except Exception:
                    pass
            _th.Thread(target=w, daemon=True).start()
        except Exception:
            pass

    _JITTER_TICK = {"NQ": 0.25, "GC": 0.1}   # 자산별 틱 크기(마이크로·미니 동일 그리드)

    def _jitter_stop(self, asset, entry_ref, stop, lbl):
        """보호 손절가 지터(대표 2026-07-27): 발주 순간 0~수 틱 무작위 오프셋을 **넓히는 방향
        (진입에서 멀어지는 쪽)으로만** 얹는다 — 전 회원 손절가가 틱까지 동일해지는 클러스터
        식별을 깨는 저비용 보험. ⚠절대 조이지 않는다(대표: "절대 줄이는 방향 안 돼") —
        조이면 정본 손절은 사는데 지터 손절만 먼저 털리는 회원이 생김. 넓히면 정본 손절이
        터질 때만 같이 터져 신호 대비 조기 이탈이 없고, 추가 리스크는 손절거리의 ~0.5% 이내.
        신호 카드·사이징은 정본 손절 그대로(계약수 불변). 선물(NQ·GC)만 — BTC는 X2+BE
        본절 이동 로직이 있어 제외. EQ_STOP_JITTER=0 으로 끔."""
        import os as _os
        import random as _rnd
        if _os.environ.get("EQ_STOP_JITTER", "1") == "0":
            return stop
        tick = self._JITTER_TICK.get(asset)
        try:
            e, s = float(entry_ref), float(stop)
        except (TypeError, ValueError):
            return stop
        dist = abs(e - s)
        if not tick or dist <= 0:
            return stop
        jmax = max(1, min(6, int(dist * 0.005 / tick)))   # 거리의 ~0.5% 이내, 1~6틱
        j = _rnd.randint(0, jmax)                          # 0=지터 없음도 허용(값 분산)
        if not j:
            return stop
        widen = -1.0 if e > s else 1.0                     # 롱=손절이 아래→더 내림 / 숏=더 올림
        new = round(s + widen * j * tick, 4)
        self.log(f"   🎲 [{lbl}] 손절 지터 +{j}틱 넓힘 → {new:g} (주문 프라이버시 · 조기이탈 없음 · 추가리스크 ≤0.5%)")
        return new

    def _run_futures_entry(self, b, sc, legs, direction, stop, sig, live, recv, asset, pub):
        """선물 진입 — 계약수 10개 이상이면 미니/마이크로 legs로 분할 체결(대표 2026-07-16).
        legs = [(심볼, 수량), ...]. 예: NQ 23계약 → [('NQ',2),('MNQ',3)] · 5계약 → [('MNQ',5)].
        미니가 커미션이 3배 싸서 백테스트(costs._blended_commission)도 같은 규칙으로 계산한다.

        leg마다 **독립 진입 + 독립 손절**(손절가는 NQ·MNQ 동일 = 같은 지수·같은 포인트).
        잔여정리·손절실패 처리는 leg별로 그대로 재사용한다. 자동청산은 flatten_all이라
        심볼 수와 무관하게 계좌 전체를 비운다(legs 신경 안 씀).
        ⚠️LIVE 경로 — 어느 leg가 실패해도 나머지는 계속(부분 체결 로그로 드러냄)."""
        import time as _t
        import datetime as _dtl
        legs = [(s, int(q)) for s, q in legs if int(q) > 0]
        if not legs:
            self.log("   ⏭ 수량 0 — 건너뜀."); return
        # 계좌 id (1회)
        match = [a for a in b._accounts() if str(a.get("name")) == sc or str(a.get("id")) == sc]
        if not match:
            self.log(f"   ❌ 계좌 '{sc}' 없음."); return
        _aid = match[0]["id"]
        # leg 심볼별 활성 계약 조회
        resolved = []
        for _sym, _qty in legs:
            _con = self._resolve_contract(b, _sym)
            if not _con:
                self.log(f"   ❌ '{_sym}' 활성 계약 없음 — 이 leg 건너뜀."); continue
            resolved.append((_sym, _qty, _con))
        if not resolved:
            self.log("   ❌ 유효 계약 없음 — 진입 중단."); return
        if len(resolved) > 1:
            self.log("   🧩 분할 진입: " + " + ".join(f"{q} {s}" for s, q, _ in resolved)
                     + " (미니 묶음 — 커미션 절감)")
        # ── 잔여 포지션 정리(leg 계약별) — 연속 세션 순서 보장 ──
        existing = b.list_open_positions()
        _mycons = {c for _, _, c in resolved}
        others = [p for p in existing if p.symbol not in _mycons]
        if others:
            self.log(f"   ⚠ 다른 심볼 포지션 {len(others)}개 감지 — 건드리지 않음: "
                     + ", ".join(f"{p.symbol}" for p in others[:3]))
        mine = [p for p in existing if p.symbol in _mycons]
        if mine:
            if not live:
                self.log(f"   (DRY-RUN) 이전 세션 잔여 {len(mine)}개 — LIVE면 청산 확인 후 진입.")
            else:
                self.log(f"   ♻ 이전 세션 잔여 {len(mine)}개 → 청산 후 진입 (연속 세션)")
                try:
                    for p in mine:
                        b.close_contract(p.raw.get("_accountId") or _aid, p.symbol)
                    _dead = False
                    for _chk in range(6):
                        _t.sleep(1)
                        if not any(q.symbol in _mycons for q in b.list_open_positions()):
                            _dead = True; break
                    if not _dead:
                        self.log("   🛑 청산 확인 실패 — 진입 중단(순서 보장). 수동 확인 필요!"); return
                    self.log("   ✅ 잔여 청산 확인 — 진입 진행.")
                except Exception as _ce:
                    self.log(f"   ❌ 잔여 청산 실패: {_ce} — 진입 중단."); return
        # ── 체결 정책(GC 등 지정가) — leg 전부 동일 정책 적용 ──
        _polf = ((sig.get("exec_policy") or {}).get("entry")
                 if isinstance(sig.get("exec_policy"), dict) else None)
        _use_limit = bool(_polf and _polf.get("mode") == "limit_then_market"
                          and _polf.get("limit_price") is not None)
        _ok = 0
        for _sym, _qty, _con in resolved:
            _tag = f"EQ-AP-{int(recv.timestamp() * 1000)}-{_sym}"
            try:
                if _use_limit:
                    res = self._exec_entry_limit_fut(b, _aid, _con, direction, _qty,
                                                     stop, dict(_polf), live, _tag)
                else:
                    res = b.place_entry(account_id=_aid, contract_id=_con, side=direction,
                                        size=_qty, order_type=2, stop_loss_price=stop,
                                        custom_tag=_tag, dry_run=not live)
            except Exception as _ee:
                self.log(f"   ❌ {_sym} 진입 예외: {_ee}"); continue
            if res.get("skipped"):
                self.log(f"   ⏭ {_sym} 정책 스킵(불리 이동)."); continue
            if res.get("error"):
                self.log(f"   ❌ {_sym} 진입 실패: {str(res.get('error'))[:200]}"); continue
            if not live:
                self.log(f"   DRY-RUN {_sym} entry: {res.get('would_place')}")
                if res.get("would_place_stop"):
                    self.log(f"   DRY-RUN {_sym} stop:  {res.get('would_place_stop')}")
                _ok += 1; continue
            self.log(f"   ✅ {_sym} 진입 완료 ×{_qty}")
            # 대시보드 보고용 누적(#53) - 미니/마이크로가 섞이므로 마이크로 환산 계약으로
            # 통일한다(미니 1 = 마이크로 10). 심볼 앞 M이 마이크로.
            self._note_fill(asset, micro=float(_qty) * (1 if str(_sym).upper().startswith("M") else 10))
            _ledger_add(asset, _con, direction, _tag)      # EQ 원장 — 트랙레코드 필터 근거
            if res.get("stop"):
                _sp = res.get("stop_price")
                _adj = (f" (틱 정렬 {stop} → {_sp:g})"
                        if _sp is not None and float(_sp) != float(stop) else "")
                self.log(f"   🛡 {_sym} 보호 손절 거치.{_adj}")
            elif res.get("stop_error"):
                self._handle_stop_failure(b, _aid, _con, direction, _qty, stop, res)
            _ok += 1
        if live and _ok:
            self._entered_at = _mark_entered(asset)
            try:
                _tot = f"{_dtl.datetime.now().timestamp() - float(pub):.1f}s" if pub else "?"
            except (TypeError, ValueError):
                _tot = "?"
            self.log(f"   ⏱ 진입 완료({_ok}/{len(resolved)} leg) · 발송 후 {_tot}")

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

    def _acct_balance(self, cfg):
        """계좌 잔고($) 통합 조회 — 선물(account_balance) / 크립토(available_usdt).
        성공 시 (self._bal_cache에 캐시). 24h 내 마지막 성공값 폴백. 실패·무값 시 None."""
        import time as _t
        b = _build_broker(cfg["broker"], cfg["f1"], cfg["f2"], cfg["f3"],
                          [cfg["acct"]] if cfg.get("acct") else [])
        bal = None
        try:
            if hasattr(b, "account_balance"):
                bal = b.account_balance(cfg.get("acct"))
            elif hasattr(b, "available_usdt"):
                bal = b.available_usdt()
        except Exception:
            bal = None
        ck = (cfg["broker"], cfg.get("acct") or cfg.get("f1"))
        cache = getattr(self, "_bal_cache", None)
        if cache is None:
            cache = self._bal_cache = {}
        if bal is not None and bal > 0:
            cache[ck] = (float(bal), _t.time())
            return float(bal)
        hit = cache.get(ck)
        if hit and (_t.time() - hit[1]) <= 86400:
            return hit[0]                               # 24h 내 마지막 성공값 폴백
        return None

    def _fetch_balance_diag(self, bk, f1, f2, f3, aid, is_fut):
        """잔고 직접 조회 + 실패 원인 표면화. 반환 (bal|None, err|None).
        _acct_balance의 bal>0 게이트·캐시를 우회 — 0/저잔고도 그대로. 크립토 None이면
        authenticate로 인증 실패인지 필드 없음인지 구분해 로그로 드러낸다(대표 2026-07-26)."""
        try:
            b = _build_broker(bk, f1, f2, f3, [aid] if aid else [])
        except Exception as e:
            return None, f"브로커 생성 실패: {str(e)[:100]}"
        try:
            if is_fut and hasattr(b, "account_balance"):
                v = b.account_balance(aid)
            elif hasattr(b, "available_usdt"):
                v = b.available_usdt()
            elif hasattr(b, "account_balance"):
                v = b.account_balance(aid)
            else:
                return None, "잔고 조회 메서드 없음"
            if v is not None:
                return float(v), None
            # None → 왜인지 표면화: 인증부터 확인
            try:
                b.authenticate()
                return None, "인증은 됐으나 잔고 필드가 비어있음(계좌 유형/통화 확인)"
            except Exception as ae:
                return None, f"인증 실패: {str(ae)[:120]}"
        except Exception as e:
            return None, str(e)[:120]

    def _run_1r_preview(self, assets, title):
        """서버 신호 없이 지정 자산들의 전 계좌 잔고·1R을 조회해 팝업+로그로 보여준다.
        1R은 그 잔고로 인라인 계산(프롭 페이즈/자본 비례/고정) → 잔고와 일관. 백그라운드 스레드."""
        import threading as _th
        ko = self.lang == "ko"
        self.log(f"\n💵 {title} — {'서버 신호 없이 잔고만 조회' if ko else 'balance-only, no server signal'}")

        def w():
            blocks = []
            for asset in assets:
                accts = [a for a in self._accts_of(asset) if a.get("on", True)]
                if not accts:
                    blocks.append(f"● {asset}: " + ("켜진 계좌 없음" if ko else "no active accounts"))
                    continue
                rows = [f"● {asset}"]
                for ac in accts:
                    # 계좌별 브로커(대표 2026-08-09) — 잔고도 그 계좌의 브로커·키로 조회
                    bk = self._acct_broker(asset, ac)
                    cr = self._creds_of(asset, bk)
                    f1 = (cr.get("f1") or "").strip()
                    aid = (ac.get("id") or "").strip()
                    lbl = ac.get("label") or (aid[-4:] if aid else _broker_label(bk))
                    if not f1:
                        rows.append("   " + (f"{lbl}: 키 미설정({_broker_label(bk)})" if ko
                                             else f"{lbl}: no credentials ({_broker_label(bk)})"))
                        continue
                    f2 = _kc_load(f1) or ""
                    f3 = cr.get("f3", "")
                    is_fut = bool(_BROKER_SPEC.get(bk, {}).get("acct"))
                    bal, err = self._fetch_balance_diag(bk, f1, f2, f3, aid, is_fut)
                    if err:
                        self.log(f"   ⚠ [{asset}·{lbl}] 잔고 조회 실패 — {err}")
                    _pr = ac.get("prop") or {}
                    _pc = ac.get("pct") or {}
                    r = None
                    if _pr.get("on"):                              # 프롭(Fast-Payout 체제)
                        mode = ("프롭" if ko else "Prop")
                        if _pr.get("type") == "funded":
                            rs = _as_float(_pr.get("r_steady"), 300.0)
                            _pcv = max(0, min(5, int(_as_float(_pr.get("payouts"), 0))))
                            if _pcv >= 5:
                                note = ("5발 완료 — 라이브 전환 대상·진입 안 함" if ko
                                        else "5/5 — Live-transition candidate, no entry")
                            else:
                                r = rs
                                _sh = _pcv < 2
                                note = ((f"펀디드 {_pcv}/5발 · " + ("방패기($12k→$6k 출금)" if _sh
                                                                    else "Fast-Payout(자격 즉시 절반)")) if ko
                                        else (f"funded {_pcv}/5 · " + ("shield ($12k→$6k)" if _sh
                                                                       else "fast-payout (half on qualify)")))
                        else:
                            r = _as_float(_pr.get("r_test"), 1200.0); note = ("테스트기" if ko else "test")
                    elif _pc.get("on"):                            # 자본 비례(잔고×%)
                        mode = ("자본비례" if ko else "% equity")
                        if bal is None:
                            note = ("잔고 조회 실패" if ko else "no balance")
                        else:
                            pct = _as_float(_pc.get("pct"), 0.4)
                            floor = _as_float(_pc.get("floor"), 200.0)
                            r = max(bal * pct / 100.0, floor)
                            note = (f"${bal:,.0f}×{pct:g}%" if r > floor else f"최소 ${floor:g}")
                    else:                                          # 고정
                        mode = ("고정" if ko else "Fixed")
                        r = _as_float(ac.get("one_r"), 600.0); note = ("수동" if ko else "manual")
                    balstr = (f"${bal:,.0f}" if bal is not None else ("조회 실패" if ko else "n/a"))
                    rstr = (f"${r:,.0f}" if r is not None else ("진입 안 함" if ko else "no entry"))
                    self.log(f"   ⚙ [{asset}·{lbl}] 잔고 {balstr} · {mode} 1R {rstr} ({note})")
                    rows.append(f"   [{lbl}] {mode} · {'잔고' if ko else 'Bal'} {balstr}  |  1R {rstr}")
                blocks.append("\n".join(rows))
            msg = "\n\n".join(blocks) if blocks else ("계좌 없음" if ko else "no accounts")
            self.root.after(0, lambda m=msg, t=title: messagebox.showinfo(t, m))
        _th.Thread(target=w, daemon=True).start()

    def _preview_one_r(self, asset):
        """그 자산 전 계좌 잔고·1R 미리보기(자산 탭 버튼)."""
        ko = self.lang == "ko"
        self._run_1r_preview([asset], (f"{asset} · 잔고 & 1R" if ko else f"{asset} · Balance & 1R"))

    def _preview_one_r_all(self):
        """전 자산 전 계좌 잔고·1R 미리보기(라이브 패널 버튼, 대표 2026-07-26)."""
        ko = self.lang == "ko"
        self._run_1r_preview(list(_ASSETS), ("전 자산 · 잔고 & 1R" if ko else "All assets · Balance & 1R"))

    def _close_all_positions(self):
        """패닉 버튼(대표 2026-07-27): 크레덴셜이 설정된 모든 자산·계좌의 포지션을 시장가로
        전부 청산(flatten_all = 포지션 청산 + 잔여 주문 취소). 무장 상태와 무관하게 동작 —
        비상시 무조건 눌러서 정리하는 용도. (브로커,계좌) 단위 중복 제거(NQ·GC 공유 계좌 1회만)."""
        ko = self.lang == "ko"
        if not messagebox.askyesno(
                "전체 청산" if ko else "Close all",
                ("모든 자산·모든 계좌의 열린 포지션을 지금 시장가로 전부 청산하고 잔여 주문을 "
                 "취소합니다.\n\n진행할까요?" if ko else
                 "Close every open position on every configured account at market and cancel "
                 "remaining orders.\n\nProceed?")):
            return
        self.log("\n🧹 " + ("전체 청산 — 모든 자산·계좌 flatten" if ko else "Close all — flatten every account"))

        def w():
            import threading  # noqa: F401
            seen = set()
            n_closed = 0
            for a in _ASSETS:
                # 계좌별 브로커(대표 2026-08-09) — 계좌 행마다 (브로커, 계좌ID) 수집.
                # 패닉 버튼이므로 on/off 무관 전 계좌 대상(구 동작 유지).
                _pairs = []
                for ac in self._accts_of(a):
                    _b = self._acct_broker(a, ac)
                    if _BROKER_SPEC.get(_b, {}).get("acct"):
                        _aid = (ac.get("id") or "").strip()
                        if _aid:
                            _pairs.append((_b, _aid))
                    else:
                        _pairs.append((_b, ""))
                # 크레덴셜만 있고 계좌 행이 없는 브로커도 쓸어담기(안전 — 옛 동작 포함)
                for _b in _ASSET_BROKERS.get(a, []):
                    if not _BROKER_SPEC.get(_b, {}).get("acct") and (_b, "") not in _pairs:
                        _pairs.append((_b, ""))
                for bk, aid in _pairs:
                    cr = self._creds_of(a, bk)
                    f1 = (cr.get("f1") or "").strip()
                    if not f1:
                        continue
                    f2 = _kc_load(f1) or ""
                    f3 = cr.get("f3", "")
                    key = (bk, f1, aid)
                    if key in seen:
                        continue
                    seen.add(key)
                    lbl = f"{a}·{aid[-4:] if aid else _broker_label(bk)}"
                    try:
                        b = _build_broker(bk, f1, f2, f3, [aid] if aid else [])
                        res = b.flatten_all(dry_run=False)
                        closed = ", ".join(f"{p.symbol}" for p in (res.closed or [])) or "—"
                        errs = getattr(res, "errors", None) or []
                        n_closed += len(res.closed or [])
                        self.log(f"   [{lbl}] closed: {closed}"
                                 + (f" · ⚠ {'; '.join(str(e)[:60] for e in errs)}" if errs else ""))
                    except AttributeError:
                        self.log(f"   ⏭ [{lbl}] 이 브로커는 일괄 청산 미지원 — 건너뜀")
                    except Exception as e:
                        self.log(f"   ❌ [{lbl}] 청산 실패: {str(e)[:100]}")
                        self._report_error("close_all", e)
            self.log("   ✅ " + (f"전체 청산 완료 — 포지션 {n_closed}개 정리" if ko
                                else f"Close-all done — {n_closed} positions flattened"))
        import threading as _th
        _th.Thread(target=w, daemon=True).start()

    def _pct_one_r(self, pc, cfg, lbl):
        """자본 비례 1R = 잔고 × pct%, 최소 floor(수수료 방어). 잔고 조회 실패 시 None(호출부가 스킵).
        ※ 선물(나스닥·금) '계약수 0.75 미만 진입금지'는 sizing.compute_size에서 처리(자본 크기 자체는
        본인 자유라 여기서 막지 않음, 대표 2026-07-26)."""
        bal = self._acct_balance(cfg)
        if bal is None:
            return None
        pct = _as_float(pc.get("pct"), 0.4)
        floor = _as_float(pc.get("floor"), 200.0)
        r = max(bal * pct / 100.0, floor)
        self.log(f"   ⚙ [{lbl}] 자본 비례 1R=${r:,.0f} (잔고 ${bal:,.0f} × {pct:g}%, 최소 ${floor:g})")
        return r

    def _bump_payout(self, broker, aid, lbl):
        """출금 완료 기록(+1) — 같은 (브로커, 계좌ID)를 쓰는 전 자산의 계좌(NQ·GC 공유) +
        무장 스냅샷(_sig_accts의 prop 복사본)까지 동기화. 메인 스레드에서 호출."""
        aid = (aid or "").strip()
        new_cnt = None
        for a in _ASSETS:
            if self._broker_of(a) != broker:
                continue
            for ac in self._accts_of(a):
                if (ac.get("id") or "").strip() == aid:
                    p = ac.setdefault("prop", dict(_PROP_DEFAULTS))
                    p["payouts"] = min(5, int(_as_float(p.get("payouts"), 0)) + 1)
                    new_cnt = p["payouts"]
        for _c in getattr(self, "_sig_accts", {}).values():
            if _c.get("broker") == broker and (_c.get("acct") or "").strip() == aid:
                _p = _c.get("prop") or {}
                _p["payouts"] = min(5, int(_as_float(_p.get("payouts"), 0)) + 1)
        if new_cnt is None:
            return
        self._save_cfg()
        try:                                             # 현재 자산 탭의 프롭 버튼 라벨 갱신
            for idx, w in (self._acct_widgets or {}).items():
                ac = self._accts_of(self._asset)[idx]
                if (ac.get("id") or "").strip() == aid and w.get("prop_btn"):
                    w["prop_btn"].config(text=self._prop_btn_text(ac.get("prop")))
        except Exception:
            pass
        self.log(f"   💰 [{lbl}] 출금 기록 +1 → {new_cnt}/5발"
                 + (" — 이 계좌는 졸업(종료)입니다. 새 챌린지 계좌로 교체하세요." if new_cnt >= 5 else ""))

    def _set_prop_lastbal(self, broker, aid, bal):
        """출금 감지용 직전 잔고 기록 — 같은 (브로커, 계좌ID) 전 자산 + 무장 스냅샷 동기.
        메인 스레드에서 호출(root.after 경유)."""
        aid = (aid or "").strip()
        hit = False
        for a in _ASSETS:
            if self._broker_of(a) != broker:
                continue
            for ac in self._accts_of(a):
                if (ac.get("id") or "").strip() == aid:
                    ac.setdefault("prop", dict(_PROP_DEFAULTS))["last_bal"] = float(bal)
                    hit = True
        for _c in getattr(self, "_sig_accts", {}).values():
            if _c.get("broker") == broker and (_c.get("acct") or "").strip() == aid:
                (_c.get("prop") or {})["last_bal"] = float(bal)
        if hit:
            self._save_cfg()

    def _payout_popup(self, cfg, lbl, title, msg):
        """출금 권장 팝업(권장 톤) + '예'면 출금 횟수 +1 기록. 워커 스레드에서 호출."""
        _ko = self.lang == "ko"
        _q = ("\n\n이번 출금을 완료하셨으면 '예'를 눌러 출금 횟수를 기록하세요(+1). "
              "아직이면 '아니요'." if _ko else
              "\n\nIf you have completed this payout, press Yes to record it (+1). "
              "Otherwise press No.")

        def _ask():
            if messagebox.askyesno(title, msg + _q):
                self._bump_payout(cfg.get("broker"), cfg.get("acct"), lbl)
        self.root.after(0, _ask)

    def _prop_one_r(self, pr, cfg, lbl):
        """프롭 1R 해석 — 빅실드 체제(대표 2026-08-02 채택 "세번 출금은 안전 가정").
        테스트기=r_test($1,200) 고정. 펀디드=전 구간 $300 고정 + 출금 두 단계:
          방패기(계좌 출금 <2 ≈ 계정 합산 1~3발): 방패 $6,000 유지 — 잔고 $12,000+ 도달 시
          $6,000 출금 권장 팝업(방패는 계좌에 남김).
          Fast-Payout기(그 후 ~ 라이브 초대 전): 자격($150+ 익절일 5일·직전 출금 후 순익+)이
          차는 순간 잔고의 절반 즉시 출금(회당 $6,000 한도) — 잔고 $1,500+에서 리마인드.
          5발 완료: 진입 안 함(None — 계정 합산 5발=라이브 전환 대상, 새 계정으로 재시작)
        ⚠ Topstep funded는 balance가 $0에서 이익만 적립(명목 150K는 드로다운 기준) → 잔고=쿠션.
        잔고 조회 실패 시 펀디드 1R($300) 그대로(사이징에 잔고 불필요)."""
        if pr.get("type") != "funded":
            r = _as_float(pr.get("r_test"), 1200.0)
            self.log(f"   ⚙ [{lbl}] 프롭 테스트기 1R=${r:g}")
            return r
        rb = _as_float(pr.get("r_buffer"), 300.0)
        rs = _as_float(pr.get("r_steady"), 300.0)
        buf = _as_float(pr.get("buffer"), 6000.0)
        pcnt = max(0, min(5, int(_as_float(pr.get("payouts"), 0))))
        _ko = self.lang == "ko"
        if pcnt >= 5:
            self.log(f"   ⛔ [{lbl}] 프롭 5발 완료 계좌 — 진입 안 함(라이브 전환 대상 — "
                     f"새 계정·새 챌린지로 교체하세요)")
            return None
        try:
            b = _build_broker(cfg["broker"], cfg["f1"], cfg["f2"], cfg["f3"],
                              [cfg["acct"]] if cfg.get("acct") else [])
            bal = b.account_balance(cfg.get("acct"))
            if bal is None:
                raise RuntimeError("잔고 없음(계좌 미발견)")
            bal = float(bal)                             # Topstep funded 0-based: 잔고=쿠션
            _pk = (cfg.get("broker"), (cfg.get("acct") or lbl), pcnt)   # 출금 횟수당 1회 알림
            _alerted = getattr(self, "_payout_alerted", None)
            if _alerted is None:
                _alerted = self._payout_alerted = set()
            # ── 출금 자동 감지(대표 2026-08-02): API엔 payout 이벤트가 없어 잔고 휴리스틱 —
            # 직전 관측 대비 DLL 최대 일손실($3,000)을 넘는 감소는 거래로 설명 불가 ≈ 출금.
            # (연속 발주일 기준. 앱을 며칠 껐다 켰으면 오탐 가능 — 팝업이 확인을 묻는 톤인 이유)
            _lb = pr.get("last_bal")
            try:
                _lb = float(_lb) if _lb is not None else None
            except (TypeError, ValueError):
                _lb = None
            if _lb is not None and _lb - bal >= 3_500.0:
                _dm = ((f"[{lbl}] 출금이 감지된 것 같습니다 — 잔고 ${_lb:,.0f} → ${bal:,.0f} "
                        f"(−${_lb - bal:,.0f}, 하루 거래 손실로는 설명되지 않는 폭입니다).") if _ko else
                       (f"[{lbl}] A payout seems to have occurred — balance ${_lb:,.0f} → "
                        f"${bal:,.0f} (−${_lb - bal:,.0f}, larger than any single-day trading loss)."))
                self._payout_popup(cfg, lbl, "출금 감지" if _ko else "Payout detected", _dm)
            self.root.after(0, lambda _b=bal: self._set_prop_lastbal(
                cfg.get("broker"), cfg.get("acct"), _b))
            _shield = pcnt < 2         # 계좌별 근사: 권장 2계좌 기준 계좌 2발째까지 ≈ 계정 합산 3발
            _stage = ((f"펀디드 {pcnt}/5발 · " + ("방패기" if _shield else "Fast-Payout기"))
                      if _ko else (f"funded {pcnt}/5 · " + ("shield" if _shield else "fast-payout")))
            self.log(f"   ⚙ [{lbl}] 프롭 {_stage} 1R=${rs:g} (잔고 ${bal:,.0f})")
            _thr = (buf + _PAYOUT_CHUNK) if _shield else 1_500.0
            if bal >= _thr:
                if _pk not in _alerted:
                    _alerted.add(_pk)
                    if _shield:
                        _msg = ((f"[{lbl}] 방패기 출금 ({pcnt + 1}번째).\n\n잔고가 ${bal:,.0f}로 "
                                 f"문턱(방패 ${buf:,.0f} + ${_PAYOUT_CHUNK:,.0f})에 도달했습니다. 출금 "
                                 f"자격($150+ 익절일 5일 · 직전 출금 후 순익 플러스)이 차 있다면 "
                                 f"${_PAYOUT_CHUNK:,.0f}을 출금하고 방패 ${buf:,.0f}은 계좌에 남기세요. "
                                 f"계정 합산 3발까지는 이 방패가 최악의 연속 손실을 흡수합니다.") if _ko else
                                (f"[{lbl}] Shield-phase payout (#{pcnt + 1}).\n\nBalance ${bal:,.0f} "
                                 f"reached the threshold (shield ${buf:,.0f} + ${_PAYOUT_CHUNK:,.0f}). "
                                 f"If you qualify (five $150+ winning days, net positive since the last "
                                 f"payout), withdraw ${_PAYOUT_CHUNK:,.0f} and keep the ${buf:,.0f} "
                                 f"shield in the account. Through the login's first three payouts this "
                                 f"shield absorbs the worst losing streaks."))
                    else:
                        _half = min(bal * 0.5, _PAYOUT_CHUNK)
                        _tail = ((" 이번이 5번째 출금이면 이 계정은 라이브 전환 대상이 됩니다 — 남는 "
                                  "잔고는 Live로 이월되지만 회수가 느립니다.") if pcnt == 4 else "") if _ko \
                                else ((" This would be the 5th payout — the login becomes a "
                                       "Live-transition candidate; remaining balance carries into Live "
                                       "but recovers slowly.") if pcnt == 4 else "")
                        _msg = ((f"[{lbl}] Fast-Payout ({pcnt + 1}번째 출금 · 라이브 초대 전까지).\n\n"
                                 f"계정 합산 4발째부터는 방패 없이, 출금 자격($150+ 익절일 5일 · 직전 "
                                 f"출금 후 순익 플러스)이 차는 순간 잔고 ${bal:,.0f}의 절반"
                                 f"(≈${_half:,.0f}, 회당 최대 ${_PAYOUT_CHUNK:,.0f})을 바로 출금하세요. "
                                 f"모으지 않습니다.{_tail}") if _ko else
                                (f"[{lbl}] Fast-Payout (payout #{pcnt + 1} · until the Live invite).\n\n"
                                 f"From the login's fourth payout the shield comes off: if you qualify "
                                 f"(five $150+ winning days, net positive since the last payout), withdraw "
                                 f"half of the ${bal:,.0f} balance now (≈${_half:,.0f}, cap "
                                 f"${_PAYOUT_CHUNK:,.0f}). No hoarding.{_tail}"))
                    self._payout_popup(cfg, lbl, "출금 리마인드" if _ko else "Payout reminder", _msg)
            else:
                _alerted.discard(_pk)                    # 문턱 아래(출금 직후 등) → 다음 도달 때 재알림
            return rs
        except AttributeError:
            self.log(f"   ⚠ [{lbl}] 이 브로커는 잔고 조회 미지원 — 펀디드 1R=${rs:g} 그대로")
            return rs
        except Exception as e:
            self.log(f"   ⚠ [{lbl}] 잔고 조회 실패({str(e)[:80]}) — 펀디드 1R=${rs:g} 그대로")
            return rs

    def _manual_ticket(self, cfg, sig, asset, direction, stop, mult):
        """수동 모드 계좌 — 발주 없이 '실행 티켓'만 표시(대표 2026-07-27). 사용자가 이 숫자를
        보고 본인 브로커 화면에 직접 진입한다(예: 프롭 평가·Sim Funded 단계처럼 API 없는 계좌).
        브로커 연결 불필요 — 사이징은 순수 계산(1R = 계좌 설정값, 자동 사이징 API 조회 안 함)."""
        from eqexec import sizing
        lbl = cfg.get("label") or _broker_label(cfg.get("broker"))
        one_r = float(cfg.get("one_r") or 0) * float(mult or 1.0)
        entry_ref = sig.get("entry_ref")
        _sz = sizing.compute_size(asset, cfg.get("broker"), one_r, entry_ref, stop, direction)
        _ko = self.lang == "ko"
        if not _sz:
            _qty = "?"
            _unit = ""
        else:
            _qty = _sz["size"]
            _unit = "계약" if (_ko and _sz.get("unit") == "contracts") else \
                    ("contracts" if _sz.get("unit") == "contracts" else _sz.get("unit", ""))
            if _sz["size"] <= 0:
                _qty = "0 (1R 대비 손절 큼 — 진입 보류 권장)" if _ko else "0 (stop too wide for 1R)"
        _bar = "─" * 34
        self.log(f"\n📝 {_bar}")
        self.log(f"📝 [{lbl}] 수동 실행 티켓 — {asset} {direction}" if _ko else
                 f"📝 [{lbl}] MANUAL ticket — {asset} {direction}")
        self.log(f"     {'방향' if _ko else 'Side'} : {direction}")
        self.log(f"     {'진입 참조' if _ko else 'Entry'} : {entry_ref}")
        self.log(f"     {'손절가' if _ko else 'Stop'}  : {stop}")
        self.log(f"     {'수량' if _ko else 'Qty'}  : {_qty} {_unit}   (1R ${one_r:g})")
        self.log(("     → 브로커 화면에 직접 입력하세요. 자동 진입 안 함(수동 모드)." if _ko else
                  "     → Enter this on your broker manually. No auto-entry (manual mode)."))
        self.log(f"📝 {_bar}")
        # 팝업으로도 띄워 놓쳐도 보이게(수동은 사용자가 즉시 봐야 하므로).
        _title = f"수동 티켓 — {asset} {direction}" if _ko else f"Manual ticket — {asset} {direction}"
        _msg = ((f"[{lbl}]  {asset} {direction}\n\n진입 참조: {entry_ref}\n손절가: {stop}\n"
                 f"수량: {_qty} {_unit}  (1R ${one_r:g})\n\n브로커 화면에 직접 입력하세요.") if _ko else
                (f"[{lbl}]  {asset} {direction}\n\nEntry: {entry_ref}\nStop: {stop}\n"
                 f"Qty: {_qty} {_unit}  (1R ${one_r:g})\n\nEnter this on your broker manually."))
        self.root.after(0, lambda t=_title, m=_msg: messagebox.showinfo(t, m))

    def _enter_account(self, cfg, sig, asset, direction, stop, mult):
        """단일 계좌 진입 — cfg(broker,f1,f2,f3,acct,one_r,label,live)로 사이징+발주.
        로그는 계좌 라벨을 앞에 붙여 멀티계좌를 구분한다(대표 2026-07-24). 계좌 실패는
        그 계좌만 스킵 — 다른 계좌 진입은 계속 진행한다."""
        import datetime as _dtl
        from eqexec import sizing
        _broker, user, key = cfg["broker"], cfg["f1"], cfg["f2"]
        f3, sc, one_r = cfg["f3"], cfg["acct"], cfg["one_r"]
        lbl = cfg.get("label") or (sc[-4:] if sc else _broker_label(_broker))
        live = self._live_now(cfg.get("live"))
        _pr = cfg.get("prop") or {}
        _pc = cfg.get("pct") or {}
        if _pr.get("on"):
            one_r = self._prop_one_r(_pr, cfg, lbl)    # 단계별 1R — G정책(대표 2026-07-26)
            if one_r is None:
                return                                  # 5발 졸업 완료 계좌 — 진입 안 함(위에서 로그)
        elif _pc.get("on"):
            _pr1 = self._pct_one_r(_pc, cfg, lbl)      # 자본 비례 1R(대표 2026-07-26 #18)
            if _pr1 is None:
                self.log(f"   ⏭ [{lbl}] 잔고 조회 불가(24h 내 값 없음) — 자본 비례 진입 건너뜀."); return
            one_r = _pr1
        _eff_r = one_r * mult
        _sz = sizing.compute_size(asset, _broker, _eff_r, sig.get("entry_ref"), stop, direction)
        if not _sz:
            self.log(f"   ⏭ [{lbl}] 사이징 불가(진입/손절 확인) — 건너뜀."); return
        size = _sz["size"]; sym = _sz["symbol"]
        _legs = _sz.get("legs") or [(sym, size)]
        if size <= 0:
            self.log(f"   ⏭ [{lbl}] 1R=${one_r:g}가 손절거리({_sz['risk_pts']}) 대비 작아 수량 0 — 건너뜀.")
            return
        self.log(f"   [{lbl}] {asset} {direction} x{size} ({sym}) · 손절 {stop} · "
                 f"1R=${one_r:g}×{mult:.2f}=${_eff_r:g}(거리 {_sz['risk_pts']}) · "
                 f"{'LIVE' if live else 'dry-run'}{(' [' + sc + ']') if sc else ''}")
        if live:                                    # 실제 진입 1R 원장 기록(A안, 트랙레코드 R 정확화)
            # 키는 (자산|계좌ID) — 크립토는 계좌ID가 비어(sc="") 브로커명으로 대체해야 조회측
            # (_acct_id = acct or broker, 2914)과 키가 맞는다. 예전엔 기록=sc("")/조회="bybit"로
            # 영구 불일치 → %-크립토 트랙레코드가 실측 1R 대신 명목값으로 왜곡됐음(대표 2026-07-26).
            _record_real_r(asset, sc or _broker, one_r)
        _pub = sig.get("published_at")
        _recv = _dtl.datetime.now()
        _is_fut = bool(_BROKER_SPEC.get(_broker, {}).get("futures"))
        try:
            b = _build_broker(_broker, user, key, f3, [sc] if sc else [])
            if _is_fut:
                # 선물(Topstep/IBKR): 미니/마이크로 legs 분할 진입 — 계좌·계약조회·잔여정리·
                # 진입·손절 전부 leg-aware 헬퍼가 그 계좌(sc)에서 처리(대표 2026-07-16).
                if stop is not None:                      # 손절 지터(넓힘만) — 계좌·진입마다 무작위
                    stop = self._jitter_stop(asset, sig.get("entry_ref"), stop, lbl)
                self._run_futures_entry(b, sc, _legs, direction, stop, sig, live, _recv, asset, _pub)
                return
            # ── 크립토(Bybit/Bitget): symbol·qty만, stopLoss는 주문에 첨부 ──
            _mysym = b._symbol(sym)
            _existing = b.list_open_positions()
            _mine = [p for p in _existing if p.symbol == _mysym]
            _others = [p for p in _existing if p.symbol != _mysym]
            if _others:
                self.log(f"   ⚠ [{lbl}] 다른 심볼 포지션 {len(_others)}개 감지 — 건드리지 않음: "
                         + ", ".join(f"{p.symbol}" for p in _others[:3]))
            if _mine:
                if not live:
                    self.log(f"   (DRY-RUN) [{lbl}] 이전 세션 잔여 {len(_mine)}개 — LIVE면 청산 후 진입.")
                else:
                    self.log(f"   ♻ [{lbl}] 이전 세션 잔여 {len(_mine)}개 → 청산 후 진입 (연속 세션)")
                    _r0 = b.close_symbol(sym, dry_run=False)
                    if not bool(_r0.get("closed")):
                        self.log(f"   🛑 [{lbl}] 잔여 청산 미확인: {_r0.get('error')} — 진입 중단."); return
                    self.log(f"   ✅ [{lbl}] 잔여 청산 확인 — 진입 진행.")
            if _broker == "bitget" and stop is not None:
                _basis = _cross_basis_bitget()
                if _basis:
                    stop = round(stop + _basis, 2)
                    self.log(f"   ⚖ [{lbl}] 빗겟 캘리브레이션: 바이빗 대비 {_basis:+.2f} → 손절 {stop}")
            _pol = ((sig.get("exec_policy") or {}).get("entry")
                    if isinstance(sig.get("exec_policy"), dict) else None)
            _ctag = f"EQ-AP-{int(_dtl.datetime.now().timestamp() * 1000)}"
            if _pol and _pol.get("mode") == "limit_then_market" and _pol.get("limit_price") is not None:
                # 구 지정가 경로 - 2026-08-03 전면 시장가 전환 후 구 신호 호환용으로만 보존
                res = self._exec_entry_limit(b, sym, direction, size, stop, dict(_pol), live, _broker, _ctag)
            else:
                # ── 시장가 진입 (대표 2026-08-03 전면 전환) ──────────────────────────
                # 실거리 사이징: 현재가-손절 거리로 수량을 '축소만' 재계산 → 리스크 항상 ≤1R.
                # (유리하게 내려온 경우 확대는 안 함 - 손절 부근 과대 수량·청산 위험 방지)
                # 극단 캡: 참조가 대비 0.75R 넘게 불리하면 스킵(8/2급 광펌핑 방어).
                _ref = sig.get("entry_ref")
                if live and stop is not None and _ref is not None:
                    try:
                        _cur = b.current_market_price(sym)
                    except Exception:
                        _cur = None
                    if _cur is not None:
                        _d_ref = abs(float(_ref) - float(stop))
                        _d_act = abs(float(_cur) - float(stop))
                        _adv = ((_cur - float(_ref)) if str(direction).upper() == "LONG"
                                else (float(_ref) - _cur))
                        if _d_ref > 0 and _adv > 0.75 * _d_ref:
                            self.log(f"   ⛔ [{lbl}] 진입 스킵 — 참조가 대비 불리 {_adv:+.2f}"
                                     f"({_adv / _d_ref:.2f}R) > 캡 0.75R (광펌핑 방어)")
                            self.root.after(0, lambda a=sym, v=_adv: messagebox.showwarning(
                                "진입 스킵" if self.lang == "ko" else "Entry skipped",
                                (f"{a}: 가격이 참조가 대비 {v:+.2f} 불리하게 이동 — 0.75R 캡 "
                                 f"초과로 이번 진입을 건너뜁니다." if self.lang == "ko" else
                                 f"{a}: price moved {v:+.2f} adversely vs reference — beyond "
                                 f"the 0.75R cap, entry skipped.")))
                            return
                        if _d_act > _d_ref > 0:
                            import math as _m
                            _sz2 = _m.floor(size * (_d_ref / _d_act) * 1000) / 1000.0
                            if 0 < _sz2 < size:
                                self.log(f"   ⚖ [{lbl}] 실거리 사이징 — 거리 {_d_ref:.2f}→{_d_act:.2f} "
                                         f"→ 수량 {size:g}→{_sz2:g} (리스크 1R 유지)")
                                size = _sz2
                if live:
                    self._auto_leverage(b, sym, size)
                res = b.place_entry(symbol=sym, side=direction, size=size,
                                    stop_loss_price=stop, custom_tag=_ctag, dry_run=not live)
            if res.get("skipped"):
                return
            if res.get("error"):
                self.log(f"   ❌ [{lbl}] 진입 실패: {res.get('error')}")
                _em = str(res.get("error"))[:300]; _hint = _entry_fail_hint(_em, self.lang == "ko")
                self.root.after(0, lambda m=_em, a=asset, h=_hint, L=lbl: messagebox.showerror(
                    "진입 실패" if self.lang == "ko" else "Entry failed",
                    (f"[{L}] {a} 진입 주문이 거절되었습니다:\n\n{m}\n\n{h}" if self.lang == "ko"
                     else f"[{L}] {a} entry order was rejected:\n\n{m}\n\n{h}")))
                return
            if not live:
                self.log(f"   DRY-RUN [{lbl}] entry: {res.get('would_place')}")
                if res.get("would_place_stop"):
                    self.log(f"   DRY-RUN [{lbl}] stop:  {res.get('would_place_stop')}")
            else:
                self.log(f"   ✅ [{lbl}] 진입 완료: {res.get('entry', res)}")
                _fill = _dtl.datetime.now()
                try:
                    _tot = f"{_fill.timestamp() - float(_pub):.1f}s" if _pub else "?"
                except (TypeError, ValueError):
                    _tot = "?"
                self.log(f"   ⏱ [{lbl}] 체결 확인 {_fill.strftime('%H:%M:%S')} · 발송 후 {_tot}")
                self._entered_at = _mark_entered(asset)
                self._note_fill(asset, coin=float(size or 0))   # 대시보드 보고용(#53)
                _ledger_add(asset, sym, direction, _ctag)   # EQ 원장 — 트랙레코드 필터 근거
        except Exception as e:
            self.log(f"   ❌ [{lbl}] signal entry failed: {e}")
            self._report_error(f"entry:{asset}", e)      # 예외 리포트(대표 2026-07-27)
            _em = str(e)[:300]
            self.root.after(0, lambda m=_em, a=asset, L=lbl: messagebox.showerror(
                "진입 실패" if self.lang == "ko" else "Entry failed",
                (f"[{L}] {a} 진입 중 오류:\n\n{m}" if self.lang == "ko"
                 else f"[{L}] {a} entry error:\n\n{m}")))

    def _sig_loop(self, url):
        # 무장 계좌 설정은 self._sig_accts에서 동적으로 읽는다(계좌별 무장/해제 즉시 반영).
        # {(asset,idx): {broker,f1,f2,f3,acct,one_r,live,label}} — 신호 자산(key[0]==자산)을 굴리는
        # 계좌 전부에 각자 1R로 진입한다(대표 2026-07-24 자산별 계좌).
        import time as _t
        import requests
        import autopilot_crypto
        last_id = None
        entered_day = {}       # {(key, dedup_key): 발행날짜}. 계좌·세션별 하루 1회 재진입 금지
        while self._sig_on:
            try:
                r = requests.get(url, params={"t": int(_t.time())}, timeout=8)
                sig = autopilot_crypto.decrypt(self._token, r.text) if r.ok else {}
            except Exception as e:
                self._feed_errs = getattr(self, "_feed_errs", 0) + 1
                # 일시적 blip(서버 재배포·순간 지연)은 조용히 재시도한다 — 폴 3초라 1~2회 실패는
                # 흔하고 무해(다음 폴에서 바로 복구). **지속 장애(20회≈1분 연속 실패)만** 한 번 로그.
                # (대표 2026-07-16: '쓸데없는 경고' 폭주 제거 — 로그는 진짜 문제일 때만.)
                if self._feed_errs == 20:
                    self.log(f"   ⚠ 신호 피드 응답 지연이 1분 이상 지속됩니다: {e}")
                _t.sleep(SIG_POLL_SECS); continue
            if getattr(self, "_feed_errs", 0) >= 20:
                self.log("   ✓ 신호 피드 정상 복구")
            self._feed_errs = 0
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
                # 이 자산을 굴리는 무장 계좌 전부(동적) — 키가 (자산,idx)인 계좌
                targets = [(key, cfg) for key, cfg in list(self._sig_accts.items())
                           if key[0] == _asset]
                if not targets:
                    self.log(f"\n➖ 신호 [{sid}] {_asset} — 이 자산 자동진입 미설정(켜진 계좌 없음), 건너뜀.")
                    _t.sleep(SIG_POLL_SECS); continue
                # 신선도/발행일 게이트 (시그널 단위 1회, 계좌 무관)
                import datetime as _dtd
                _pub_ts = sig.get("published_at")
                try:
                    _age = _t.time() - float(_pub_ts) if _pub_ts is not None else None
                    _sig_day = (_dtd.datetime.fromtimestamp(float(_pub_ts)).date()
                                if _pub_ts is not None else None)
                except (TypeError, ValueError):
                    _age, _sig_day = None, None
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
                    _t.sleep(SIG_POLL_SECS); continue
                direction, stop = sig.get("direction"), sig.get("stop_price")
                # size 배수(신뢰도 사이징) — 0~3 클램프(랜딩 '거래당 최대 3R' 캡과 일치).
                try:
                    _mult = float(sig.get("size_mult") or 1.0)
                except (TypeError, ValueError):
                    _mult = 1.0
                _mult = max(0.0, min(_mult, 3.0))
                import datetime as _dtl
                _recv = _dtl.datetime.now(); _pub = sig.get("published_at")
                try:
                    _sent = _dtl.datetime.fromtimestamp(float(_pub)) if _pub else None
                    _lat = f"{_recv.timestamp() - float(_pub):.1f}s"
                except (TypeError, ValueError):
                    _sent, _lat = None, "?"
                # 🚫 재진입 금지(계좌·세션별 하루 1회) → 발주 대상 계좌 선별
                # 수동 계좌(manual)는 자동 발주 대상이 아니라 '티켓만' 표시(진입은 사용자가 직접).
                _fire = []
                _manual = []
                for _key, _cfg in targets:
                    if _sig_day is not None and entered_day.get((_key, _dedup_key)) == _sig_day:
                        self.log(f"   ⏹ [{_cfg.get('label')}] {_asset} 오늘({_sig_day}) 이미 진입 — 재진입 금지.")
                        continue
                    entered_day[(_key, _dedup_key)] = _sig_day   # 성공/실패 무관 — 같은 날 재진입 금지
                    (_manual if _cfg.get("manual") else _fire).append(_cfg)
                if _manual:                                       # 수동 티켓(발주 없음)
                    for _cfg in _manual:
                        self._manual_ticket(_cfg, sig, _asset, direction, stop, _mult)
                if not _fire:
                    _t.sleep(SIG_POLL_SECS); continue
                self.log(f"\n📶 신호 캡처 [{sid}] — {_asset} {direction} · 손절 {stop} · "
                         f"진입참조 {sig.get('entry_ref')} · size×{_mult:.2f} · {len(_fire)}개 계좌 자동 진입")
                self.log(f"   ⏱ 보낸 시각 {_sent.strftime('%H:%M:%S') if _sent else '?'}  ·  "
                         f"받은 시각 {_recv.strftime('%H:%M:%S')}  ·  지연 {_lat}")
                # 계좌별 병렬 발주 — 순차 지연으로 계좌 간 진입가가 벌어지는 것 방지(대표 2026-07-24).
                # 각 계좌는 독립 스레드로 동시에 쏘고(스레드마다 독립 브로커 객체), 로그는 계좌
                # 라벨 [펀디드]/[챌린지]로 구분된다. 한 계좌 실패는 그 계좌만 스킵(다른 계좌 진행).
                _ths = []
                for _cfg in _fire:
                    _th = threading.Thread(target=self._enter_account,
                                           args=(_cfg, sig, _asset, direction, stop, _mult), daemon=True)
                    _th.start(); _ths.append(_th)
                for _th in _ths:
                    _th.join(timeout=45)
                # 계좌 전부 끝난 뒤 합산해 1회 보고(#53) - 별도 스레드라 루프를 잡지 않는다.
                self._send_fill(_asset)
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
