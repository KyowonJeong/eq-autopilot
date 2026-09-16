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
    DST 판정은 근사 ET 날짜(UTC-5)로 — 청산시각(06, 14 ET)은 전환 경계(2am)와 멀어 안전."""
    et = u - _dt.timedelta(hours=5)                       # 근사 ET 날짜
    y = et.year
    mar = 8 + (6 - _dt.date(y, 3, 8).weekday()) % 7       # 3월 둘째 일요일
    nov = 1 + (6 - _dt.date(y, 11, 1).weekday()) % 7      # 11월 첫째 일요일
    return -4 if _dt.date(y, 3, mar) <= et.date() < _dt.date(y, 11, nov) else -5


def _now_in(tzname):
    """tzname의 현재 시각(tz-aware). UTC 내장, ET는 ZoneInfo→실패 시 산술 폴백.
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


def _machine_id() -> str:
    """이 설치본의 기기 식별자(설치별 난수 12자). 없으면 만들어 파일에 남긴다.

    왜 필요한가(대표 2026-08-14): 한 회원이 여러 머신에서 돌리면(맥=Topstep·크립토 /
    윈도우=NT8·Lucid) 서버가 자산당 칸을 하나만 두고 있어 **나중 보고가 앞 보고를
    덮었다** — GC 실제 7계약(TS 5 + Lucid 2)이 5로 기록된 실사고. 보고에 이 값을 실어
    서버가 기기별로 나눠 담고 합산하게 한다.

    하드웨어 지문(MAC·시리얼)이 아니라 **난수**다. 기기를 특정할 수 없고 설정 폴더를
    지우면 새로 생긴다 — 합산에 필요한 최소한만 담는다.
    """
    _p = os.path.join(APP_DIR, ".machine_id")
    try:
        with open(_p) as f:
            v = "".join(ch for ch in f.read().strip() if ch.isalnum())[:16]
        if v:
            return v
    except Exception:
        pass
    import uuid
    v = uuid.uuid4().hex[:12]
    try:
        with open(_p, "w") as f:
            f.write(v)
    except Exception:
        pass
    return v


MACHINE_ID = _machine_id()

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
# 표시 전용 피드는 느리게 돈다(2026-08-28): 매매 루프의 3초는 진입 속도 때문이고,
# 화면에 적기만 하는 쪽은 그럴 이유가 없다 - 회원 PC와 서버 양쪽 부담을 낮춘다.
_WATCH_POLL_SECS = 20
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
# 사전 점검 창(2026-09-13 대표 "진입 시간 십분 전에 1r 조회 한 번 자동으로 함 어때").
# 종전에는 70분 창 하나였고 _precheck_done 표식이 영구라 점검이 **딱 한 번**(실질 65~70분
# 전) 돌았다 - 남은 60분 동안 NT8이 죽어도 재검증이 0이다. NT8은 브리지 하트비트가 5초만
# 낡아도 healthcheck()가 예외를 던지므로, 진입 직전에 한 번 더 밟는 것이 정확히 그 구멍을
# 메운다. 틱이 5분 주기라 10분 창은 반드시 한 번 걸린다.
PRECHECK_WINDOWS_MIN = (70, 10)                     # 넓은 창(여유 있게 고치라고) + 직전 창
PRECHECK_WINDOW_MIN = PRECHECK_WINDOWS_MIN[0]       # 하위호환(기존 참조)
STOP_RETRIES = 2                                    # protective stop: retries on a transient miss
STOP_RETRY_WAIT = 1.5                               # seconds between stop retries
MAX_SIGNAL_AGE_SEC = 60                             # 자동진입: 발행 1분 이내 신호만 진입(오래된 건 대기)


def _next_entry_dt(asset: str):
    """이 자산의 다음 진입(신호 도착) 시각 — tz-aware datetime. NQ, GC는 주말 건너뜀."""
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
    return ("잔고(마진), 레버리지, API 키 권한을 확인하세요."
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
# Tradovate REST 항목은 선택지에서 뺀다(대표 2026-09-15 "tradovate 브로커는 지워야지 - 둘 다 NinjaTrader"):
# 새 NinjaTrader 계좌엔 API Access 메뉴가 없고, 자기자본도 Lucid처럼 NT8 브리지로 간다. 어댑터 코드와
# 저장된 옛 설정은 그대로 두되(라벨 dict 유지) 새로 고를 수는 없다.
_BROKERS = ["projectx", "ibkr", "bybit", "bitget"]

# 자산 탭 + 자산별 브로커 매트릭스(대표 2026-07-10):
#   Topstep(projectx)·IBKR = MNQ·MGC (선물) · Bybit·Bitget = BTC만(BTCUSDT.P, 크립토)
#   ⚠️ BTC를 CME MBTC 선물로 안 함 — MBTC는 주말 휴장인데 BTC 엣지가 주말(일요일)에 몰려 있어
#      MBTC로 돌리면 실행 성과가 크게 훼손됨. BTC는 크립토(주말 거래) 전용.
_CONSENT_VER = "golive-4item-2026-08"
_ASSETS = ["NQ", "GC", "BTC"]
_ASSET_BROKERS = {"NQ": ["projectx", "nt8", "ibkr"],
                  "GC": ["projectx", "nt8", "ibkr"],
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
                    "f3": "Port (7497/7496)", "acct": True, "futures": True, "status": "unverified"},
    "bybit":       {"label": "Bybit (USDT perp)", "f1": "API Key", "f2": "API Secret",
                    "f3": "Testnet (1=on)", "acct": False, "futures": False,
                    "f1_secret": True},
    "bitget":      {"label": "Bitget (USDT-F)", "f1": "API Key", "f2": "API Secret",
                    "f3": "Passphrase", "f3_secret": True, "acct": False, "futures": False, "status": "beta",
                    "f1_secret": True},
    # Lucid = NT8 브리지(대표 2026-08-10, #38). API가 없어 NinjaTrader 애드온 경유 -
    #   f1 = 앱-애드온 공유 토큰(비밀), f3 = 브리지 포트. 계좌 = NT8 계정 이름 그대로.
    #   Windows 전용(앱과 NT8 같은 머신). preview = 데모 실증 전.
    # 라벨 단문화(대표 2026-09-04 윈도 실기기: 한글 섞인 긴 라벨이 고정폭 칸에서 잘림
    # "Bridge Token (앱-애." - Tk width 단위가 한글에서 2배라 맥과 달리 안 들어간다).
    # 공유·설치 안내는 아래 NT8 안내문과 브리지 설치 버튼이 이미 말한다.
    # 라벨(2026-09-15 대표 "api 쓰지 말고 이 경로"): 브리지는 NT8이 붙는 계좌면 무엇이든 된다 - Lucid뿐
    # 아니라 자기자본 NinjaTrader 브로커리지(=Tradovate 기술) 계좌도. 새 NinjaTrader 계좌엔 REST API
    # 메뉴가 없어(지원 서면 9/15) 자기자본 선물의 Windows 경로는 이 브리지가 기본이다.
    "nt8":         {"label": "Lucid / Tradovate", "f1": "Bridge Token", "f2": None,
                    "f3": "Bridge Port (8377)", "acct": True, "futures": True,
                    "f1_secret": True},
    # Tradovate = 자기자본 주력 브로커(대표 2026-07-27). f3 = "cid:sec[:demo]"
    #   (API 키 페어 콜론 연결 — 셋째 토막 'demo'면 데모 서버). preview=데모 실검증 전.
    # f2 라벨 주의(2026-08-17 실사): "Password"로만 쓰면 마스터 로그인 비밀번호를 넣게 유도한다.
    # Tradovate 키 발급 시 "Protect with a dedicated password"로 정한 전용 비밀번호가 맞다.
    "tradovate":   {"label": "Tradovate", "f1": "Username", "f2": "API dedicated password",
                    "f3": "API cid:sec[:demo]", "f3_secret": True, "acct": True, "futures": True,
                    "status": "unverified"},
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
    if broker == "nt8":
        from eqexec.broker.nt8 import NT8Broker
        from eqexec.config import NT8Cfg
        try:
            _prt = int(str(f3 or "").strip() or 8377)
        except (TypeError, ValueError):
            _prt = 8377
        return NT8Broker(NT8Cfg(port=_prt, token=(f1 or "").strip(), accounts=acc))
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
    "broker_preview": {"ko": "(검증 전 — 테스트넷/Sim 먼저)", "en": "(unverified — testnet/Sim first)"},
    "broker_beta": {"ko": "(베타 — 일부 경로 검증 중)", "en": "(beta — some paths still verifying)"},
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
    "gate_ok": {"ko": "멤버십: {tier}, 자동청산 {u}, 자동진입 {a}{dry}",
                "en": "Membership: {tier}, auto-close {u}, auto-entry {a}{dry}"},
    "gate_dry": {"ko": ", 라이브 잠금(서버)", "en": ", LIVE locked (server)"},
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
    "copy_btn": {"ko": "복사", "en": "Copy"},
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
    "live_close": {"ko": "⚠ 실제 청산 (LIVE)", "en": "⚠ LIVE close"},
    # 약관 §14.9·Privacy 5.4와 문언 일치(2026-08-27 일치성 P2-40): '본인 계좌' 항목 누락 수리
    # 동의 문안 버전(대표 2026-09-03 증거 스탬프): consent 문구를 실질 변경하면 반드시 올릴 것
    # - 서버 원장에 "어느 버전 문안에 동의했나"가 이 값으로 남는다.
    "consent": {"ko": "동의: 본인 키, 본인 기기, 본인 계좌, 본인 책임. EdgeQuant는 거래하지 않음 (실행 동작에 필요)",
                "en": "I agree: my key, my device, my account, my responsibility. EdgeQuant does not trade. (required to act)"},
    "consent_detail": {
        "ko": ("- 크립토 진입 시 잔고가 부족하면 앱이 거래소 계좌의 레버리지 설정을 올립니다"
               "(주문이 아니라 계좌 설정 변경입니다. 내리지는 않습니다).\n"
               "- 선물 손절가에 0~6틱을 넓히는 방향으로만 얹습니다. 실제 리스크가 설정 1R을"
               " 손절거리의 0.5% 이내로 넘습니다.\n"
               "- 진입은 건별 승인 없이 나갑니다. 한 번 무장하면 그 세션 동안 신호마다 자동 발주됩니다."),
        "en": ("- On crypto entries, if the balance is short the app raises the leverage setting on your "
               "exchange account (a settings change, not an order; it is never lowered).\n"
               "- Futures stop prices get a 0-6 tick offset, widening only. Realised risk exceeds your 1R "
               "by up to about 0.5% of the stop distance.\n"
               "- Entries are placed without per-trade approval. Once armed, every signal in that session "
               "is ordered automatically.")},
    "ready": {"ko": "준비됨. 키 입력 → '연결 테스트' → 통과하면 나머지 기능이 켜집니다.",
              "en": "Ready. Enter your key → 'Test connection' → the rest unlocks once it passes."},
    "conn_first": {"ko": "※ 먼저 '연결 테스트'를 통과해야 청산, 자동 진입 기능이 활성화됩니다.",
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
    "live_start": {"ko": "▶ 라이브 시작", "en": "▶ Go Live"},
    # 프롭 비활동 경고(대표 2026-08-31). {d}=경과 일수. 문구 주의: "바로 나와"라고 쓰지
    # 않는다 - Lucid는 5초 이하 보유 거래가 이익의 절반을 넘으면 마이크로스캘핑으로
    # 자동 플래그하므로, 오래 쉰 계좌의 첫 왕복이 초단타면 그 규정을 정면으로 밟는다.
    "idle_warn": {"ko": ("프롭 계좌 주의: EQ 자동 진입이 {d}일째 없습니다. 프롭 회사는 보통 "
                         "30일 무거래 계좌를 폐쇄할 수 있습니다(홀드 신청 불가). 계좌를 "
                         "지키려면 최소 수량으로 한 번 진입했다 청산해 두십시오. 초단타로 "
                         "끝내지 말고 몇 분은 들고 있다 청산하십시오. 비활동 기준은 회사마다 "
                         "다르니 이용 중인 회사 규정을 확인하십시오."),
                  "en": ("Prop account notice: no EQ entry for {d} days. Prop firms can close "
                         "accounts after about 30 days of inactivity (no hold available). To "
                         "keep the account alive, place one minimum-size trade and close it - "
                         "hold it for a few minutes rather than seconds. Inactivity rules "
                         "differ by firm, so check the rules of the firm you use.")},
    "live_stopall": {"ko": "⏹ 전체 정지 (포지션 유지)", "en": "⏹ Stop all (positions kept)"},
    "live_1r_note": {"ko": "1R = 거래당 기본 리스크(typical risk), 시장 국면의 기대값에 따라 최대 3R"
                           "(maximum risk)까지 — 계좌 여유는 1R의 3배로 잡으세요.",
                     "en": "1R = typical risk per trade, scales up to 3R (maximum risk) with the "
                           "expectancy of the market regime — budget 3× your 1R."},
    "live_note": {"ko": "체크된 자산을 연결 테스트 후 한 번에 시작합니다(하나라도 실패하면 시작 안 함). "
                        "신호의 방향, 손절로 자동 진입, 세션 마감엔 자동 청산. 포지션은 종목별 독립 관리. "
                        "세션 마감 자동 청산을 원하지 않으면 마감 전에 [⏹ 전체 정지]를 누르세요 — 포지션은 "
                        "그대로 유지되고 앱은 어떤 주문도 내지 않습니다(앱을 꺼도 같습니다. 브로커에 "
                        "걸어둔 손절 주문은 계좌에 남습니다). 그 순간부터 그 포지션의 청산은 본인 "
                        "몫입니다 — 다시 시작해도 지나간 세션의 청산을 소급 실행하지 않습니다.",
                  "en": "Starts every checked asset at once after connection tests (one failure = nothing "
                        "starts). Auto-enters with the signal's direction & stop, auto-closes at session "
                        "end. Positions are managed independently per symbol. If you do not want the "
                        "session-end auto-close, press [⏹ Stop all] before the close — positions are "
                        "kept and the app places no orders (quitting the app works too; broker-side "
                        "stop orders remain on your account). From that moment closing that position "
                        "is on you — restarting does not retroactively run a missed session close."},
    "sec_auto": {"ko": "자산별 자동 청산 (세션 마감 자동)", "en": "Per-asset auto-close (at session close)"},
    "auto_sched": {"ko": "청산 시각: NQ 14:00 ET, GC 06:00 ET, BTC 02:00 UTC (자동)",
                   "en": "Close times: NQ 14:00 ET, GC 06:00 ET, BTC 02:00 UTC (auto)"},
    "auto_start": {"ko": "자동 청산 시작", "en": "Start auto-close"},
    "auto_stop": {"ko": "자동 청산 중지", "en": "Stop auto-close"},
    "auto_on_ind": {"ko": "  ● 자동 청산 ON  ", "en": "  ● Auto-close ON  "},
    "auto_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sec_sig": {"ko": "자산별 자동 진입 (실시간 신호)", "en": "Per-asset auto-entry (live signal)"},
    "sig_1r": {"ko": "1R ($)", "en": "1R ($)"},
    "sig_start": {"ko": "신호 대기 시작", "en": "Start signal watch"},
    "sig_stop": {"ko": "신호 대기 중지", "en": "Stop signal watch"},
    "sig_on_ind": {"ko": "  ● 신호 대기 ON  ", "en": "  ● Watching ON  "},
    "sig_off_ind": {"ko": "  ○ 정지  ", "en": "  ○ Off  "},
    "sig_note": {"ko": "※ 신호의 방향, 손절가로 자동 진입하고, 계약 수는 위 1R($ 리스크)로 앱이 자동 계산합니다 "
                       "(신호에 계약 수 없음). ⚠️ EdgeQuant는 시장 국면의 기대값에 따라 포지션을 키워 "
                       "거래당 최대 3R까지 리스크를 감수합니다 — 계좌 여유는 1R의 3배 기준으로 잡으세요. "
                       "자산은 신호의 종목으로 자동 판별(NQ→MNQ, GC→MGC, BTC→BTCUSDT.P). "
                       "'사용 계좌'만 고르면 됩니다. 포지션은 종목별로 독립 관리됩니다 — 같은 종목은 기존 "
                       "포지션이 완전히 청산된 것이 확인된 후에만 새로 진입하여 중복 포지션을 방지하고, 다른 "
                       "종목은 서로 영향을 주지 않으므로 NQ와 GC도 같은 계좌에서 동시에 독립 운용할 수 있습니다.",
                 "en": "※ Enters automatically using the signal's direction and stop; the contract count is computed "
                       "by the app from your 1R above (the signal carries no contract count). ⚠️ EdgeQuant scales "
                       "position size with the expectancy of the market regime — up to 3R risk per trade; budget your account for "
                       "3× your 1R. The instrument is detected from the signal (NQ→MNQ, GC→MGC, BTC→BTCUSDT.P). "
                       "Just pick the account. Positions are managed independently per symbol — the same "
                       "symbol re-enters only after the previous position is confirmed fully closed (no doubling), "
                       "and different symbols never affect each other, so NQ and GC can run side by side on one "
                       "account."},
    "auto_note": {"ko": "※ 앱이 떠 있고 컴퓨터가 켜져(절전 해제) 있어야 작동. 설정된 각 자산의 세션 마감 시각에 그 자산 브로커를 청산합니다.",
                  "en": "※ App must stay open and the computer awake. Each configured asset's positions are flattened at its session close."},
}


KC_SERVICE = "EQAutopilot"   # Keychain / Credential-Manager service name
_IS_MAC = sys.platform == "darwin"
# 설정 행 라벨 칸 폭: Windows Tk는 같은 width 단위에서 실제 폭이 좁게 잡혀 긴 라벨
# ("API dedicated password", "TopstepX user email")이 잘렸다(대표 2026-09-04 실기기).
_LBL_W = 18 if _IS_MAC else 24


def _kc_save(account, secret) -> bool:
    """OS 보안 저장소에 비밀 저장(macOS는 `security`, 그 외는 keyring).

    ⚠️**성공 여부를 돌려준다**(2026-08-28). 종전에는 예외를 통째로 삼켜 성공과 실패를
    구분할 수 없었다. 그 상태에서 '키체인에 옮기고 평문을 지우기'를 하면, 키체인 쓰기가
    조용히 실패한 기기에서 자격이 그대로 증발한다(헤드리스 VM, 잠긴 키체인, keyring
    백엔드 없음이 전부 현실적인 경우다). 그래서 쓰기 뒤 **되읽어 값까지 대조**한다 -
    백엔드가 성공을 반환하고도 빈 값을 돌려주는 경우가 있기 때문."""
    if not secret:
        return False
    if _IS_MAC:
        try:
            r = subprocess.run(["/usr/bin/security", "add-generic-password", "-a", account or "default",
                                "-s", KC_SERVICE, "-w", secret, "-U"], capture_output=True, timeout=8)
            if r.returncode != 0:
                return False
        except Exception:
            return False
    else:
        try:
            import keyring
            keyring.set_password(KC_SERVICE, account or "default", secret)
        except Exception:
            return False
    return _kc_load(account) == secret        # 되읽기 대조 - 이게 통과해야 진짜 저장이다


def _secret_fields(broker: str) -> tuple:
    """그 브로커에서 **비밀로 다뤄야 하는 평문 필드**(f2는 이미 키체인 전용이라 제외).

    2026-08-28 실사: 앱은 이 값들을 화면에서 마스킹하고 PIN으로 잠그면서 **저장은 평문
    YAML**로 했다. 랜딩이 "API secrets stay in your OS secret store"라고 단정하는데
    실제로는 Bitget Passphrase, Tradovate cid:sec, NT8 Bridge Token, 크립토 API Key가
    전부 평문이었다. 약관 §14.3만 정직했다(그 파일은 암호화 안 됨이라 경고까지 한다).
    비밀이 아닌 것(호스트, 포트, 이메일, 사용자명, 테스트넷 플래그)은 평문으로 둔다 -
    옮길 이유가 없고, 키체인이 없는 환경에서 앱이 못 뜨게 만들 이유는 더 없다."""
    _sp = _BROKER_SPEC.get(broker) or {}
    out = []
    if _sp.get("f1_secret"):
        out.append("f1")
    if _sp.get("f3_secret"):
        out.append("f3")
    return tuple(out)


def _kc_key(asset: str, broker: str, field: str) -> str:
    """키체인 계정 키. **값에서 파생하지 않는다** - f2가 f1 값을 계정 키로 쓰는 레거시
    방식은 f1을 파일에서 지우는 순간 f2까지 못 읽게 만든다(이번 설계의 최대 함정).
    자산, 브로커, 필드로만 만들어 값이 바뀌어도 키가 안 흔들린다."""
    return f"eq:{asset}:{broker}:{field}"


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


# ── 설정 내보내기/가져오기 암호화(대표 2026-09-04 "브로커·계좌 export/import, PIN으로 열기") ──
# 기기 이전(맥→윈도 통합 등)용. 파일 하나에 자격 비밀(f2 포함)까지 들어가므로 반드시
# 암호화한다. 키 = PIN에서 PBKDF2-HMAC-SHA256 120만 회로 파생 - PIN이 짧아도 오프라인
# 추측 1회 비용을 올린다(그래도 이전 끝나면 파일 삭제가 원칙 - UI가 안내). scrypt를
# 안 쓰는 이유: LibreSSL 파이썬(맥 시스템 등)엔 hashlib.scrypt가 없어, 내보낸 기기와
# 가져오는 기기의 파이썬이 다르면 파일을 못 연다(pbkdf2_hmac은 stdlib 어디에나 있다).
# 암호화 = SHA256-CTR + HMAC-SHA256(encrypt-then-MAC) - autopilot_crypto와 동일 원리,
# stdlib only(앱 배포 전제). 포맷: MAGIC(6)|salt(16)|nonce(16)|ct|tag(32).
_EXP_MAGIC = b"EQSET1"


def _exp_keys(pin: str, salt: bytes):
    k = hashlib.pbkdf2_hmac("sha256", (pin or "").encode(), salt, 1_200_000, dklen=64)
    return k[:32], k[32:]


def _exp_stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + i.to_bytes(8, "big")).digest()
        i += 1
    return bytes(out[:n])


def _settings_export_blob(pin: str, obj: dict) -> bytes:
    import hmac as _hm
    import json as _j
    pt = _j.dumps(obj, ensure_ascii=False).encode()
    salt, nonce = os.urandom(16), os.urandom(16)
    ek, mk = _exp_keys(pin, salt)
    ct = bytes(a ^ b for a, b in zip(pt, _exp_stream(ek, nonce, len(pt))))
    tag = _hm.new(mk, _EXP_MAGIC + salt + nonce + ct, hashlib.sha256).digest()
    return _EXP_MAGIC + salt + nonce + ct + tag


def _settings_import_blob(pin: str, raw: bytes) -> dict:
    import hmac as _hm
    import json as _j
    if len(raw) < 6 + 16 + 16 + 32 or raw[:6] != _EXP_MAGIC:
        raise ValueError("not an EQ settings file")
    salt, nonce, ct, tag = raw[6:22], raw[22:38], raw[38:-32], raw[-32:]
    ek, mk = _exp_keys(pin, salt)
    if not _hm.compare_digest(tag, _hm.new(mk, _EXP_MAGIC + salt + nonce + ct,
                                           hashlib.sha256).digest()):
        raise ValueError("wrong PIN or corrupted file")
    pt = bytes(a ^ b for a, b in zip(ct, _exp_stream(ek, nonce, len(ct))))
    return _j.loads(pt.decode())


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
# 평가 통과 익절(대표 2026-08-18): 테스트(챌린지) 계좌에서 **통과 기준 잔고**를 회원이 직접
# 입력해 두면, 앱이 (현재 잔고 + 미실현 이익)이 그 값에 닿는 순간 그 계좌의 포지션을 시장가로
# 정리한다. 잔고를 기준으로 삼는 이유(대표 질문 "목표 계좌액이야 남은 액이야"): '남은 이익'을
# 넣게 하면 잔고가 늘 때마다 회원이 숫자를 고쳐야 하고 안 고치면 엉뚱한 데서 닫힌다. 목표
# 잔고는 통과까지 한 번만 넣으면 되고, 남은 거리는 앱이 매번 다시 계산한다.
# 회원이 숫자를 정하고 앱은 집행만 한다 - 우리가 목표를 판단하거나 권하지 않는다.
# 버퍼: 수수료·체결오차로 목표에 미달하는 일이 없게 여유를 얹는다.
_TP_BUFFER_USD = 20.0
_PV_BY_ROOT = {"MNQ": 2.0, "NQ": 20.0, "ENQ": 20.0, "MGC": 10.0, "GC": 100.0, "GCE": 100.0}
_PROP_DEFAULTS = {"on": False, "type": "test", "r_test": 1200.0, "r_buffer": 300.0,
                  "r_steady": 300.0, "buffer": 6000.0, "payouts": 0, "r_live": 100.0,
                  "pass_tp": 0.0,
                  "last_bal": None}   # r_live = 라이브 초기 1R(Lucid, +$4,500 락 전. 2026-08-11)   # 직전 관측 잔고 - 출금 자동 감지용(2026-08-02)
_PCT_DEFAULTS = {"on": False, "pct": 0.4, "floor": 200.0}   # 자본 비례 모드(대표 2026-07-26 #18)
# ── 브로커별 프롭 정본 프리셋(대표 2026-08-11 "브로커 자동 감지해서 페이즈별 사이징") ──
#   projectx(Topstep) = 빅실드 정본 1200/300/방패6000 (2026-08-02 채택)
#   nt8(Lucid)        = 속도 스윕 정본: 평가 $300 / 펀디드 $150, 방패 없음(락스텝·1발 전환)
#                       라이브 락 후는 프롭 모드가 아니라 '잔고 %' 모드 2% 권장(e3d03c3)
_PROP_PRESETS = {
    "projectx": {"r_test": 1200.0, "r_buffer": 300.0, "r_steady": 300.0, "buffer": 6000.0},
    "nt8":      {"r_test": 300.0,  "r_buffer": 150.0, "r_steady": 150.0, "buffer": 0.0,
                 "r_live": 100.0},
}


def _prop_preset(broker):
    return _PROP_PRESETS.get(str(broker or "").lower())
_PAYOUT_CHUNK = 6000.0   # Topstep 회당 출금 단위(DLL 계좌 $6,000) — 방패+이 값 도달 시 출금 권장 팝업


def _new_acct(one_r=600.0, acct_id="", on=True, label="", prop=None, pct=None, manual=False,
              broker=""):
    p = dict(_PROP_DEFAULTS)
    if isinstance(prop, dict):
        p.update({k: prop[k] for k in _PROP_DEFAULTS if k in prop})
        p["on"] = bool(p["on"])
        p["type"] = p["type"] if p["type"] in ("funded", "live") else "test"
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
    # 키체인 저장 실패로 평문에 남은 필드 목록(2026-08-28) - 아래 out에 실어 앱이 경고한다.
    _kc_failed = [str(x) for x in (d.get("kc_failed") or [])]
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
                                  x.get("prop"), x.get("pct"), False, bk)
                        for x in ((d.get("accounts") or {}).get(bk) or [])]
            include = bool((d.get("asset_on") or {}).get(a, True))
        else:
            s = assets_raw.get(a) or {}
            bk = s.get("broker") if s.get("broker") in brs else brs[0]
            # avail(가용 계좌)도 함께 로드 - 화이트리스트에서 빠져 재시작마다 증발했다
            # (대표 2026-08-11 "설정해도 껐다 켜면"). 수동 등록(Lucid)이 특히 치명적이었다.
            creds = {b: {"f1": cr.get("f1", ""), "f3": cr.get("f3", ""),
                         "avail": [str(x) for x in (cr.get("avail") or []) if str(x).strip()]}
                     for b, cr in (s.get("creds") or {}).items() if b in brs}
            # 새 설치만 기본 미선택 — "자산 하나로 시작" 온보딩(대표 2026-08-15).
            # 판정은 아래 flat 마이그레이션 가드와 **같은 조건**이어야 한다: assets/broker-centric/
            # flat(f1) 어느 형태로든 설정이 있으면 기존 회원이므로 종전대로 True.
            # (assets_raw만 보면 구형 flat 설정 회원이 통째로 꺼져 매매가 멈춘다 - 회귀 테스트에서 발각.)
            _fresh = (not assets_raw and not _broker_centric
                      and not (d.get("f1") or "").strip())
            include = bool(s.get("include", not _fresh))
            if s.get("accounts"):                      # ③ 새 자산중심(현행)
                accounts = [_new_acct(x.get("one_r"), x.get("id"), x.get("on", True), x.get("label"),
                                      x.get("prop"), x.get("pct"), False,
                                      x.get("broker", ""))
                            for x in s["accounts"]]
            else:                                      # ① 옛 assets: 단일 acct → 계좌 1개
                one_r = float(s.get("one_r", 600) or 600)
                _crbk = (s.get("creds") or {}).get(bk) or {}
                acct_id = (_crbk.get("acct") or s.get("acct") or "").strip()
                accounts = [_new_acct(one_r, acct_id, True,
                                      acct_id[-4:] if acct_id else _broker_label(bk))]
        # 비밀 필드를 키체인에서 되채운다(2026-08-28). 저장 때 벗겼으므로 파일에는 빈
        # 값이고, 메모리에는 실값이 있어야 앱 전체(_kc_load(f1) 8곳 포함)가 그대로 돈다.
        # 평문에 값이 남아 있으면(구버전 설정, 또는 키체인 쓰기 실패분) 그대로 쓴다 -
        # 다음 저장 때 옮겨진다. 즉 마이그레이션은 "한 번 저장하면 끝"이고 별도 절차가 없다.
        for _b, _cr in creds.items():
            for _f in _secret_fields(_b):
                if not str(_cr.get(_f) or "").strip():
                    _kv = _kc_load(_kc_key(a, _b, _f))
                    if _kv:
                        _cr[_f] = _kv
        # 저장돼 버린 중복 계좌 행 청소(2026-08-18): 같은 id의 뒤 행은 통째로 버린다.
        _dseen, _duniq = set(), []
        for _x in accounts:
            _dk = str(_x.get("id") or "").strip().lower()
            if _dk and _dk in _dseen:
                continue
            if _dk:
                _dseen.add(_dk)
            _duniq.append(_x)
        accounts = _duniq
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
    # 키체인 쓰기가 실패해 평문으로 남은 필드 목록(2026-08-28) - 앱이 기동 시 경고한다.
    # 조용히 넘기면 회원은 랜딩·약관이 약속한 보안 상태를 받고 있다고 믿게 된다.
    out["kc_failed"] = _kc_failed
    out["dry_run"] = bool(d.get("dry_run", True))
    if "cfg_open" in d:
        out["cfg_open"] = bool(d.get("cfg_open"))
    if "acct_open" in d:
        out["acct_open"] = bool(d.get("acct_open"))
    # profile은 저장은 통째로 하는데(_save_cfg) 로드에서 public/handle만 남겨 나머지를 버리고
    # 있었다 — 알 수 없는 키를 보존한다(2026-08-15). 월 1회 실행 확인 시각(consent_at)이 여기 산다.
    # (옛 demo_runs 키는 모의 모드 폐지(2026-09-07)로 더 안 읽는다 - 남아 있어도 무해.)
    _p = dict(d.get("profile") or {})
    _p["public"] = bool(_p.get("public"))
    _p["handle"] = _p.get("handle", "")
    out["profile"] = _p
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


def _idle_days():   # 반환 int | None. ⚠️어노테이션으로 쓰지 마라 - 앱 파이썬은 3.9(Tk 8.6 제약)라 PEP 604가 기동 즉시 TypeError(2026-08-31 실사고: 배포 빌드가 안 열림)
    """EQ 자동 진입이 없었던 경과 일수(달력일). 판정 불가면 None.

    프롭 비활동 경고용(대표 2026-08-31 "걍 앱에 경고 띄워, 그걸로 끝"): 프롭 회사는 대체로
    30일 무거래 계좌를 폐쇄할 수 있고(Topstep 펀디드 "30일 초과 폐쇄 가능, 홀드 불가",
    Lucid "30일, 영구 삭제"), 우리 방법론의 실측 최장 무거래는 나스닥+금 결합 28일이라
    여유가 3일뿐이다. 21일에 울리면 실측상 9.3년에 1회꼴이라 경보 피로가 없고 10일이 남는다.
    기준은 이 앱의 진입 원장(_LEDGER_PATH) - 원장이 비어 있으면 원장 시작 표식
    (_LEDGER_SINCE_PATH)부터 잰다(앱을 켜 두고도 진입이 한 번도 없던 기간)."""
    import time as _t
    try:
        d = _ledger_load()
        if d:
            last = max(float(r.get("ts_ms") or 0) for r in d)
        else:
            # ⚠️_ledger_since()를 부르지 않는다(2026-08-31 R20 P2-4): 그 함수는 파일이
            # 없으면 '지금'을 **써서 박제**하는 쓰기 함수고, 그 시각은 트랙레코드 원장
            # 필터의 grandfather 기준점이다. 경고 틱이 기동 7초에 돌면서 기준점을 '첫
            # 실행'으로 앞당겨 버렸다 - 기준점 박제는 원래 소유자(첫 푸시 경로)에게 남긴다.
            try:
                with open(_LEDGER_SINCE_PATH, encoding="utf-8") as f:
                    last = float(f.read().strip())
            except Exception:
                return None            # 기준점 없음 = 판정 불가(경고 안 띄움이 정답)
        if not last:
            return None
        return int((_t.time() * 1000 - last) // 86400000)
    except Exception:
        return None


IDLE_WARN_DAYS = 21          # 실측 근거는 _idle_days 독스트링


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


_KC_LAST_FAILED = []       # 직전 저장에서 키체인에 못 넣고 평문으로 남은 필드(경고용)


def _save_full(lang, token, acfg, profile=None, dry_run=None, cfg_open=None, acct_open=None):
    """자산별 설정(acfg={자산:{broker,creds{broker:{f1,f3}},include,accounts[]}}) + lang/token/전역
    dry_run + 공개프로필을 yaml에 저장. 비밀(f2)은 여기서 안 씀 — 크레덴셜 저장 시 Keychain에 이미 넣음."""
    try:
        import yaml
        # ⚠️비밀 필드를 **디스크에 쓰기 직전에 벗긴다**(2026-08-28). 메모리(acfg)는 손대지
        # 않는다 - 앱 전체가 _acfg에서 f1/f3를 읽고, f2 키체인 계정 키가 f1 값이라
        # 메모리에서 지우면 f2까지 못 읽는다(이 설계의 최대 함정). 그래서 야머 페이로드
        # 사본에서만 지운다.
        # 순서가 안전의 전부다: **키체인에 쓰고 → 되읽어 대조가 통과했을 때만** 평문을
        # 비운다. 실패하면 평문을 그대로 남긴다 - 보안을 조금 늦추는 것이 자격을 잃고
        # 라이브가 멈추는 것보다 낫다(헤드리스 VM·잠긴 키체인이 현실적인 경우다).
        _kept_plain = []
        _assets = {}
        for a, c in (acfg or {}).items():
            _creds = {}
            for b, v in (c.get("creds") or {}).items():
                _v = dict(v)
                for _f in _secret_fields(b):
                    _val = str(_v.get(_f) or "")
                    if not _val:
                        continue
                    if _kc_save(_kc_key(a, b, _f), _val):
                        _v[_f] = ""              # 키체인에 안전하게 들어갔다 - 평문 제거
                    else:
                        _kept_plain.append(f"{a}/{_broker_label(b)}/{_f}")
                _creds[b] = _v
            _assets[a] = {"broker": c.get("broker"), "creds": _creds,
                          "include": bool(c.get("include", True)),
                          "accounts": [dict(x) for x in (c.get("accounts") or [])]}
        payload = {"live": False, "lang": lang, "token": token, "assets": _assets}
        if _kept_plain:
            payload["kc_failed"] = _kept_plain   # 다음 기동 경고용(영속)
            # ⚠️**그 순간에도 알린다**(2026-08-28 리뷰 P1). 종전에는 다음 기동에만
            # 경고해서, 키체인 저장이 실패한 바로 그 순간 회원이 보는 것은 초록색
            # "✓ 저장됨"뿐이었다 - 랜딩과 약관이 "보안 저장소가 없으면 앱이 알려
            # 드립니다"라고 약속한 그 시점이 정확히 여기다. 모듈 전역에 남겨
            # 호출부(_flash_saved)가 읽는다(_save_full은 App 메서드가 아니다).
            globals()["_KC_LAST_FAILED"] = list(_kept_plain)
        else:
            globals()["_KC_LAST_FAILED"] = []
        if dry_run is not None:
            payload["dry_run"] = bool(dry_run)
        if cfg_open is not None:
            payload["cfg_open"] = bool(cfg_open)
        if acct_open is not None:
            payload["acct_open"] = bool(acct_open)
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
        # 너비도 화면에 맞춰 넓힌다(2026-09-16 R34 P2-#18): 860px에서는 계좌 행 오른쪽이
        # 잘려 영어 화면이 'Topstep (Proj', 'Prop: fundec', '1R calculatc', 'Remov'로 찍혔다
        # (랜딩에 올린 스크린샷이 그 상태였다). 한국어는 라벨이 짧아 증상이 덜했을 뿐 같은 문제다.
        # 계좌 행 = 체크 + 브로커 + 라벨 + 1R + 프롭 + 계산기 + 계좌 + 삭제 여덟 칸이라 960은 필요하다.
        try:
            _h = min(1010, root.winfo_screenheight() - 60)
            _w = min(980, max(860, root.winfo_screenwidth() - 80))
        except Exception:
            _h, _w = 1010, 980
        root.geometry(f"{_w}x{_h}")
        root.minsize(860, 700)
        self.q = queue.Queue()
        self.lang = _load()["lang"]
        self.frm = None
        self._auto_on = False
        self._sig_on = False
        # Operator 등급 신호 확인(watch-only) - 자동 진입 권한(autoentry) 없이도 방향·진입가·
        # 손절가는 실시간으로 보되, 수량/계약수는 계산도 표시도 하지 않고 주문도 내지 않는다
        # (대표 2026-09-15 "Operator 등급에 앱 실시간 피드 줘" 뒤 "계약수 숨겨" - 8/10 "수량까지
        # 주면 Operator가 사실상 반자동이 된다" 결정과의 절충: 방향은 주되 사이징은 Autopilot만).
        self._watch_only = False
        # 계좌별 무장 상태(대표 2026-07-24 자산별 계좌): 자동청산/자동진입은 (자산,계좌idx)마다
        # 독립. 탭 전환은 보기 전환일 뿐 무장을 안 바꾼다. _*_on은 '루프 살아있음' 플래그.
        self._auto_accts = {}   # {(asset,idx): [job,...]}  자동청산
        self._sig_accts = {}    # {(asset,idx): cfg}        신호대기 진입
        # 무장 요청 세대(2026-08-28 리뷰 P0): 연결 테스트는 백그라운드로 수 초 걸리는데
        # 그동안 [전체 정지]·체크 해제가 다 눌린다. 해제 계열 경로가 이 번호를 올리고,
        # 테스트가 끝난 done()은 자기 번호가 낡았으면 무장을 **버린다**. 안 그러면
        # 정지가 취소되고 신호 루프가 하나 더 떠서 같은 신호에 이중 진입까지 간다.
        self._arm_seq = 0
        self._ping_lock = threading.Lock()   # 핑 직렬화(2026-08-28: 두 경로가 서로 덮었다)
        self._hb_on = False          # 상시 하트비트 스레드(아래 _hb_loop) 기동 여부
        self._watch_on = False       # 표시 전용 피드 스레드(_watch_loop) 기동 여부
        self._live_session = False   # [라이브 시작]~[전체 정지] 사이인가(무장 0이어도 True)
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
        # 이 앱이 연 포지션 흔적(청산 버튼 노출 근거) - 재시작에도 유지(2026-08-11)
        self._open_assets = set(self._profile.get("open_assets") or [])
        # 손절 청산 감지용(2026-09-02): 진입 때 쓴 브로커·심볼을 기억해 두고, 포지션이
        # 브로커에서 사라졌는지 하트비트 주기로 확인한다. 첨부 스탑은 거래소가 체결하므로
        # 앱의 청산 보고 경로(_send_fill(closed=True))가 시간 청산에서만 불렸고, 그래서
        # 서버 버킷이 영구 open으로 남아 회원 화면에 '내 계좌 확인 필요'가 36시간 떴다.
        self._open_ctx = {}          # {asset: {"b": broker, "sym": 심볼조각}}
        self._flat_seen = {}         # {asset: 연속 flat 관측 횟수} - 1회는 안 믿는다
        self._entered_at = _load_entered()   # 자산별 마지막 LIVE 진입 시각(자동청산 오살 방지)
        # 서버가 지정한 계획 청산 시각(exit_ts) — {asset: (epoch, 사유)}. 고영향 지표
        # 발표에 걸리는 날 서버가 청산을 앞당겨 보낸다(FOMC 14:00 ET ↔ NQ 청산 14:00 ET:
        # 발표를 관통해 포지션을 들고 있으면 브로커 규정 위반이라 2분 전에 평평해진다).
        # 신호 수신 때 채우고 그날 발화하면 지운다. 없으면 기존 고정 시각(_ASSET_EXITS) 그대로.
        self._exit_override = {}
        self._token = _d0.get("token", "")
        # 키체인 저장 실패 경고(2026-08-28): 평문으로 남은 비밀이 있으면 회원이 알아야 한다.
        # 랜딩과 약관이 "비밀은 OS 보안 저장소에"라고 말하는데 이 기기에서만 아니기 때문.
        self._kc_failed = list(_d0.get("kc_failed") or [])
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
        root.after(90 * 1000, self._passtp_tick)                   # 평가 통과 익절 감시(테스트기)
        root.after(7 * 1000, self._idle_warn_tick)                 # 프롭 비활동 경고(21일)
        root.after(120 * 1000, self._unsent_fill_tick)             # 미전송 체결 보고 재시도(2분)

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
                txt = txt.rstrip() + f" [{', '.join(assets)}]  "
            self.auto_ind.config(text=txt, fg="white" if on else "#666",
                                 bg="#22a722" if on else "#dddddd")
        except Exception:
            pass

    def _set_sig_ind(self, on, assets=None):
        """Green lit pill while the signal-watch loop runs; gray when off. assets=무장 자산 병기."""
        try:
            txt = self.t("sig_on_ind") if on else self.t("sig_off_ind")
            if on and assets:
                txt = txt.rstrip() + f" [{', '.join(assets)}]  "
            self.sig_ind.config(text=txt, fg="white" if on else "#666",
                                bg="#22a722" if on else "#dddddd")
        except Exception:
            pass
        # 무장 상태가 **바뀌는 순간** 서버에 즉시 알린다(대표 2026-08-28 "라이브 버튼 누르면
        # armed 바로 뜨게 해, 기다리지 말고"). 종전에는 4분 주기 핑을 기다려야 회원 화면의
        # 상태 칩이 따라왔다. 값이 실제로 달라졌을 때만 보내 핑 폭주를 막는다.
        try:
            _k = (bool(on), tuple(sorted(assets or [])))
            if getattr(self, "_sig_ind_key", None) != _k:
                self._sig_ind_key = _k
                self._alive_ping(armed=bool(on), force=True)
        except Exception:
            pass

    def _build(self):
        d = _load()
        # 재빌드 후에도 보던 자리 유지(대표 2026-08-11 "자산 버튼 옮기면 위로 리셋돼
        # 정신 없어") - 파괴 전에 스크롤 위치를 잡아둔다.
        _keep_y = None
        try:
            if getattr(self, "_scroll_cv", None) is not None:
                _keep_y = self._scroll_cv.yview()[0]
        except Exception:
            pass
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
        self._keep_y = _keep_y                    # _build 끝에서 동기 복원(그리기 전)
        _cv = tk.Canvas(host, highlightthickness=0, borderwidth=0)
        self._scroll_cv = _cv                     # 스크롤 위치 복원용(2026-08-11)
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
        # 제목은 고정(대표 2026-08-17 "루시드만 되는 거야?") - 시작 시점 첫 자산의 브로커를
        # 붙이던 단일 브로커 시절 유물이 멀티 브로커 앱을 루시드 전용처럼 보이게 했다.
        # 현재 브로커는 자산 행("Lucid (NT8) · 1계좌 …")이 이미 보여준다.
        ttk.Label(top, text="EQ Autopilot",
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
        ttk.Label(rt, text=self.t("token"), width=_LBL_W).pack(side="left")
        self.token_e = ttk.Entry(rt, show="•")  # 토큰 마스킹(잠금) — 기본 •••, 아래 토글로 표시
        self.token_e.pack(side="left", fill="x", expand=True)
        self.token_e.insert(0, d.get("token", "")); self.token_e.bind("<FocusOut>", self._save_token)
        ttk.Button(rt, text=self.t("token_show"), width=6, command=self._toggle_token_show).pack(side="left", padx=(4, 0))
        ttk.Button(rt, text=self.t("paste"), width=8, command=self._paste_token).pack(side="left", padx=(4, 0))
        ttk.Button(rt, text=self.t("token_get"), width=9, command=self._open_free).pack(side="left", padx=(4, 0))
        self.gate_lbl = tk.Label(frm, text="", foreground="#888", anchor="w", justify="left", wraplength=660)
        self.gate_lbl.pack(anchor="w", pady=(0, 4))
        # 브리지 설치를 최상단으로(대표 2026-09-04 "토큰 넣는 데 바로 아래, 눈에 잘 띄는 데") -
        # NT8 브로커 접이 안에 묻혀 있어 업데이트 순간에 못 찾던 문제. NT8은 윈도우 전용이라
        # 윈도우에서만 보인다. 자산 컨텍스트는 _nt8_install_bridge가 스스로 찾는다.
        if sys.platform.startswith("win"):
            _bridge_row = ttk.Frame(frm); _bridge_row.pack(fill="x", pady=(0, 4))
            ttk.Button(_bridge_row, text=("NT8 브리지 설치/업데이트" if self.lang == "ko"
                                          else "Install/Update NT8 bridge"),
                       command=self._nt8_install_bridge).pack(side="left")
            ttk.Label(_bridge_row,
                      text=("Lucid / Tradovate용 - 저장된 토큰 자동 주입, 설치 후 NT8 재시작"
                            if self.lang == "ko" else
                            "for Lucid / Tradovate - injects the saved token; restart NT8 after"),
                      foreground="#9ca3af").pack(side="left", padx=(8, 0))

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
        # 앱이 회원 확인 없이 하는 일 중 **계좌에 쓰기가 일어나는 것**을 체크박스 바로 아래
        # 고지한다(2026-08-15 사실기술서 감사). 지금까지 어디에도 안 적혀 있었다.
        # 둘 다 의도된 설계이고 끄는 스위치가 없으므로, 최소한 알고 켜야 한다.
        ttk.Label(frm, text=self.t("consent_detail"), foreground="#6b7280",
                  wraplength=760, justify="left").pack(anchor="w", pady=(0, 4))
        # 자산별 실행 행 — [자산] 상태 [연결테스트] [정지] ●
        self._live_include = {}
        self._live_rows = {}       # {asset: (status_lbl, dot)}
        # 하나도 안 고른 상태(새 설치)에서만 뜨는 안내 — "셋 다 해야 한다"는 인상을 막는다.
        # 자산 수와 등급(Preview/Operator/Autopilot)은 별개 축이다(대표 2026-08-15).
        self._pick_box = ttk.Frame(frm)          # 자리 고정용 - 라벨만 넣고 뺀다(순서 안 틀어짐)
        self._pick_box.pack(fill="x")
        self._pick_hint = tk.Label(
            self._pick_box, anchor="w", justify="left", wraplength=760, foreground="#6b7280",
            text=("시작할 자산을 선택하세요. 하나만 골라도 됩니다 - 나머지는 언제든 추가할 수 있습니다."
                  if self.lang == "ko" else
                  "Pick the markets to start with. One is enough - you can add the rest any time."))
        self._pick_hint.pack(anchor="w", pady=(2, 2))
        lv = ttk.Frame(frm); lv.pack(fill="x", pady=(3, 0))
        self._sync_pick_hint()
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
        # 시작 버튼은 [라이브 시작] 하나(대표 2026-09-07 모의 모드 폐지): 9/4 밤 모의 무장을
        # 라이브로 오인한 실사고의 근본 해결 - 오인할 모드 자체를 없앤다. 실주문 억제는 오직
        # 서버 게이트(force_dry_run = 라이브 잠금)가 _live_now()로 발주 순간에 건다.
        # live_dry는 내부 호환 상태로만 남고 항상 0(라이브)이다.
        self.live_dry = tk.IntVar(value=0)
        self.b_live_start = ttk.Button(lc, text=self.t("live_start"), command=self._master_start)
        self.b_live_start.pack(side="left")
        self.b_live_stop = ttk.Button(lc, text=self.t("live_stopall"), command=self._master_stop)
        self.b_live_stop.pack(side="left", padx=(6, 0))
        # 모든 자산·계좌 포지션 즉시 시장가 청산 — 패닉 버튼(대표 2026-07-27). 무장 해제(전체
        # 정지)와 별개로, 지금 열려 있는 포지션 자체를 정리한다. 확인 대화 후 실행.
        # 스타일: 네이티브 ttk 유지(대표 2026-08-11 "흉한 버튼" - tk 빨강 조합이 맥에서
        # 시뻘건 덩어리로 렌더링). 위험 신호는 넓은 간격 격리 + 확인 대화가 담당한다.
        # 상시 노출(대표 2026-08-11 "즉시 청산 버튼 필요하잖아" - 조건부 숨김 기각):
        # 패닉 버튼은 비상구라 추적 로직과 무관하게 항상 그 자리에 있어야 한다.
        self._flat_btn = ttk.Button(lc, text=("모든 포지션 청산" if self.lang == "ko"
                                              else "Close ALL positions"),
                                    command=self._close_all_positions)
        self._flat_btn.pack(side="left", padx=(24, 0))
        # [전 자산 1R 조회] 복원(대표 2026-08-11 "전계좌 1R 조회는?") - 자산 줄 상시 표시는
        # 설정값 합이고, 이 버튼은 잔고 실조회라 프롭/자본% 자동 사이징의 실계산 1R($)을
        # 보여준다. 네트워크 조회 = 상시 표시 불가 = 버튼이 정당한 자리.
        ttk.Button(lc, text=("전 계좌 1R 조회" if self.lang == "ko" else "Check 1R (all)"),
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
            + ("자산별 브로커 설정 (NQ, GC, BTC)" if self.lang == "ko"
               else "Per-asset broker setup (NQ, GC, BTC)"),
            command=self._toggle_cfg, relief="flat", anchor="w",
            font=("Helvetica", 12, "bold"), padx=0)
        self._cfg_hdr.pack(fill="x", anchor="w")
        self._cfg_body = ttk.Frame(frm)
        if self._cfg_open:
            self._cfg_body.pack(fill="x")
        _outer_frm = frm
        frm = self._cfg_body                      # 아래 설정 위젯들은 접이식 본문으로

        # ── 설정 이동(대표 2026-09-04 "export import, PIN으로 열기") - 기기 이전이 실제로
        #    잦다(맥→윈도 실행기 통합). 브로커·계좌·비밀 전부를 PIN 잠금 파일 하나로. ──
        _xr = ttk.Frame(frm); _xr.pack(fill="x", pady=(2, 2))
        ttk.Label(_xr, text=("설정 이동(기기 이전):" if self.lang == "ko"
                             else "Move settings:"),
                  foreground="#888").pack(side="left")
        ttk.Button(_xr, text=("내보내기" if self.lang == "ko" else "Export"),
                   width=9, command=self._export_settings).pack(side="left", padx=(6, 0))
        ttk.Button(_xr, text=("가져오기" if self.lang == "ko" else "Import"),
                   width=9, command=self._import_settings).pack(side="left", padx=(4, 0))
        ttk.Label(_xr, text=("PIN으로 잠근 파일 하나 - 새 기기에서 가져오면 끝"
                             if self.lang == "ko" else
                             "one PIN-locked file; import it on the new machine"),
                  foreground="#9ca3af").pack(side="left", padx=(8, 0))

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
        # ①/② 섹션 분리(대표 2026-08-11 "브로커 설정이랑 계좌 추가랑 따로"): 브로커 연결은
        # 브로커당 한 번, 계좌는 그 아래서 여러 개 - 개념이 달라 화면도 가른다.
        # (①/② 번호 라벨 제거 - 접이식 제목 두 개가 유닛 분리를 이미 말한다, 2026-08-11)
        # 브로커 선택 — 이 자산이 지원하는 브로커만 (NQ·GC=Topstep/IBKR · BTC=Bybit/Bitget)
        rb = ttk.Frame(frm); rb.pack(fill="x", pady=3)
        ttk.Label(rb, text=self.t("broker"), width=_LBL_W).pack(side="left")
        self.brokerbox = ttk.Combobox(rb, values=[_broker_label(b) for b in _ASSET_BROKERS[self._asset]],
                                      state="readonly", width=22)
        self.brokerbox.set(_broker_label(self._broker_name)); self.brokerbox.pack(side="left")
        self.brokerbox.bind("<<ComboboxSelected>>", self._on_broker)
        # 검증 상태 3단(2026-08-27 일치성 P1-11): 웹 tier_config.BROKER_STATUS 미러.
        # 약관 §14.5의 unverified/beta 라벨 정의와 표기를 앱·웹에서 일치시킨다.
        _bst = spec.get("status")
        if _bst == "unverified":
            ttk.Label(rb, text=self.t("broker_preview"), foreground="#b06f00").pack(side="left", padx=(8, 0))
        elif _bst == "beta":
            ttk.Label(rb, text=self.t("broker_beta"), foreground="#8a8f00").pack(side="left", padx=(8, 0))
        # 설정법 안내는 홈피 정본 한 줄로(대표 2026-08-11 "설명 너무 지저분 - 홈피 어디서
        # 볼 수 있는지만 딱"). 인앱 멀티라인 스텝은 제거, 연결되면 이 줄도 사라진다.
        if not self._conn_by_broker.get(self._broker_name):
            ttk.Label(frm, text=("자세한 설정법: 홈피 → EQ Autopilot → 브로커별 설정법"
                                 if self.lang == "ko" else
                                 "Full setup guide: website → EQ Autopilot → Broker Setup"),
                      foreground="#8a8f98").pack(anchor="w", pady=(2, 2))
        # f1 (브로커별 1번 필드)
        r1 = ttk.Frame(frm); r1.pack(fill="x", pady=3)
        ttk.Label(r1, text=spec["f1"], width=_LBL_W).pack(side="left")
        _f1sec = bool(spec.get("f1_secret"))          # 크립토 API Key = 비밀 취급(대표 2026-07-12)
        self._f1_unlocked = False
        self.user = ttk.Entry(r1, show=("•" if _f1sec else ""))
        self.user.pack(side="left", fill="x", expand=True)
        self.user.insert(0, c.get("f1", ""))
        if _f1sec:
            self.user.config(state="readonly")        # 잠금해제 후에만 편집, 붙여넣기
            self.b_lock_f1 = ttk.Button(r1, text=self.t("unlock"), width=11, command=self._unlock_f1)
            self.b_lock_f1.pack(side="left", padx=(4, 0))
        # Bybit/Bitget은 f1이 긴 API Key라 수동 타이핑이 고역 — 붙여넣기 버튼(대표 2026-07-12)
        ttk.Button(r1, text=self.t("paste"), width=8,
                   command=self._paste_f1).pack(side="left", padx=(4, 0))
        # 복사 버튼(대표 2026-08-10 "잠금 해제하면 카피") - 기기 이전용. 비밀 f1은 PIN 게이트
        ttk.Button(r1, text=self.t("copy_btn"), width=6,
                   command=self._copy_f1).pack(side="left", padx=(4, 0))
        # f2 (비밀 — PIN 잠금) — 있는 브로커만
        if spec.get("f2"):
            r2 = ttk.Frame(frm); r2.pack(fill="x", pady=3)
            ttk.Label(r2, text=spec["f2"], width=_LBL_W).pack(side="left")
            self.key = ttk.Entry(r2, show="•"); self.key.pack(side="left", fill="x", expand=True)
            self.key.config(state="readonly")   # 값은 _async_load_key(c["f1"])가 Keychain서 채움
            self.b_lock = ttk.Button(r2, text=self.t("unlock"), width=11, command=self._unlock)
            self.b_lock.pack(side="left", padx=(4, 0))
            ttk.Button(r2, text=self.t("paste"), width=8, command=self._paste_key).pack(side="left", padx=(4, 0))
            ttk.Button(r2, text=self.t("copy_btn"), width=6,
                       command=self._copy_key).pack(side="left", padx=(4, 0))
            # '보기' 체크박스 폐지(대표 2026-08-09) — 키는 열람 불가·교체만 가능(write-only).
            # 덕분에 PIN 재설정 시 키를 지킬 필요가 없어졌다(_pin_forgot 코드 인증 참조).
        # f3 (추가 필드) — 있는 브로커만
        if spec.get("f3"):
            r3e = ttk.Frame(frm); r3e.pack(fill="x", pady=3)
            ttk.Label(r3e, text=spec["f3"], width=_LBL_W).pack(side="left")
            _mask = "•" if ("Secret" in spec["f3"] or "Passphrase" in spec["f3"]) else ""
            self.f3 = ttk.Entry(r3e, show=_mask); self.f3.pack(side="left", fill="x", expand=True)
            self.f3.insert(0, c.get("f3", ""))
            ttk.Button(r3e, text=self.t("paste"), width=8,
                       command=lambda: self._paste_into(self.f3)).pack(side="left", padx=(4, 0))
        # 저장·테스트 버튼 폐지(대표 2026-08-11 "버튼 최소화, 유저 경험 최고로" - 자동 저장
        # 확정): 입력이 멈추면 자동 저장(+Keychain), 키가 완성되면 자동 연결 확인까지.
        # 사람이 할 일은 입력뿐 - 스텝 0.
        _svrow = ttk.Frame(frm); _svrow.pack(fill="x", pady=(6, 2))
        ttk.Label(_svrow, text=("입력하면 자동으로 저장, 연결 확인됩니다 (자산마다 한 번, 재시작해도 유지)"
                                if self.lang == "ko" else
                                "Everything saves & verifies automatically as you type (once per asset)"),
                  foreground="#9ca3af").pack(side="left")
        self._saved_lbl = ttk.Label(_svrow, text="", foreground="#6b7280")
        self._saved_lbl.pack(side="left", padx=(10, 0))
        for _w in (getattr(self, "user", None), getattr(self, "key", None),
                   getattr(self, "f3", None)):
            if _w is not None:
                _w.bind("<KeyRelease>", lambda e: self._schedule_autosave())
        # 가용 계좌(대표 2026-08-11 확정): 브로커 설정의 일부다 - Topstep은 연결되면 자동으로
        # 차고, Lucid처럼 API 목록이 없는 브로커는 여기서 수동 등록해야 ② 드롭다운에 뜬다.
        if not spec.get("acct"):
            # 크립토는 계좌ID 개념이 없다(API 키=계좌) - 가용 계좌 코너가 왜 없는지 앱이
            # 직접 답한다(대표 2026-08-11 "비트는 가용 계좌 안 써줌?").
            ttk.Label(frm, text=("가용 계좌: 크립토는 API 키가 곧 계좌입니다 - 별도 등록 없이 "
                                 "아래 계좌 설정에서 거래소별로 추가하세요."
                                 if self.lang == "ko" else
                                 "Available accounts: for crypto the API key IS the account - "
                                 "just add exchanges in the account section below."),
                      foreground="#9ca3af", wraplength=700, justify="left"
                      ).pack(anchor="w", pady=(4, 0))
        if spec.get("acct"):
            _avrow = ttk.Frame(frm); _avrow.pack(fill="x", pady=(4, 0))
            _av = self._avail_of(self._asset, self._broker_name)
            _avtxt, _avcol = self._avail_text_color(_av)
            self._avail_lbl = tk.Label(
                _avrow, text=_avtxt, foreground=_avcol, wraplength=700, justify="left", anchor="w",
                font=("Helvetica", 11, "bold" if _av else "normal"))
            self._avail_lbl.pack(side="left")
            if self._broker_name == "projectx":
                ttk.Label(_avrow, text=("(연결하면 자동으로 불러옵니다)" if self.lang == "ko"
                                        else "(loaded automatically on connect)"),
                          foreground="#9ca3af").pack(side="left", padx=(6, 0))
            elif self._broker_name == "nt8":
                # 수동 등록 폐지(대표 2026-08-12 "수동 등록 지워") - 자동 로드 단일 경로.
                # 로드 실패 시 팝업이 NT8 기동을 안내한다(_autoload_nt8_avail).
                ttk.Label(_avrow, text=("(NT8이 켜져 있으면 자동으로 불러옵니다)"
                                        if self.lang == "ko" else
                                        "(loaded automatically while NT8 is running)"),
                          foreground="#9ca3af").pack(side="left", padx=(6, 0))
                # 안심 문구(대표 2026-08-12): 첫 셋업(NT8·브리지)이 고비지, 계좌 추가는 쉽다.
                ttk.Label(frm, text=("Lucid 계좌를 더 사셨나요? 처음 NT8 셋업만 한 번이 고비고, "
                                     "계좌 추가는 쉽습니다 - NT8 재로그인하면 새 계좌가 위 목록에 "
                                     "자동으로 뜨고, 아래 계좌 설정에서 골라 1R만 정하면 끝이에요."
                                     if self.lang == "ko" else
                                     "Bought another Lucid account? The one-time NT8 setup is the "
                                     "hard part - adding accounts is easy: re-log into NT8, the new "
                                     "account appears above automatically, pick it below and set 1R.")
                          , foreground="#8a8f98", wraplength=740, justify="left"
                          ).pack(anchor="w", pady=(2, 0))
                # 브리지 설치 버튼은 앱 최상단(토큰 아래)으로 이동(대표 2026-09-04) -
                # 여기엔 위치 안내만 남긴다.
                ttk.Label(frm, text=("브리지 설치는 화면 맨 위 [NT8 브리지 설치/업데이트] "
                                     "버튼으로 - 저장된 토큰이 자동 주입됩니다."
                                     if self.lang == "ko" else
                                     "To install the bridge, use [Install/Update NT8 bridge] "
                                     "at the top of the window - the saved token is injected."),
                          foreground="#9ca3af", wraplength=740, justify="left"
                          ).pack(anchor="w", pady=(4, 0))
            else:
                _mrow = ttk.Frame(frm); _mrow.pack(fill="x", pady=(2, 0))
                self._avail_entry = ttk.Entry(_mrow, width=24)
                self._avail_entry.pack(side="left")
                ttk.Button(_mrow, text=("가용 계좌 등록" if self.lang == "ko" else "Register account"),
                           command=self._register_avail).pack(side="left", padx=(4, 0))
                ttk.Label(_mrow, text=("계좌 이름 그대로" if self.lang == "ko"
                                       else "exact account name"),
                          foreground="#9ca3af").pack(side="left", padx=(8, 0))
        if spec.get("acct"):
            # ① 브로커 키 전수 가져오기(대표 2026-09-04 "전 브로커 세팅을 다 복사해오게" -
            #    자산 단위 복사는 자산마다 브로커가 다르면 브로커 선택까지 갈아쳐
            #    엉뚱한 필드 값으로 보였다). 소스 선택 없이 버튼 하나로 브로커별 병합.
            _has_src = any(
                str((cr2 or {}).get("f1") or "").strip()
                for a2 in _ASSETS if a2 != self._asset
                for bk2, cr2 in (self._acfg.get(a2, {}).get("creds") or {}).items()
                if bk2 in _ASSET_BROKERS.get(self._asset, []))
            if _has_src:
                _bcp = ttk.Frame(frm); _bcp.pack(fill="x", pady=(2, 0))
                ttk.Button(_bcp, text=("다른 자산의 브로커 키 전부 가져오기"
                                       if self.lang == "ko"
                                       else "Pull all broker keys from other assets"),
                           command=self._pull_broker_keys).pack(side="left")
                ttk.Label(_bcp, text=("다른 자산에 저장된 키를 브로커별로 채워 넣습니다. "
                                      "이미 있는 키는 안 덮습니다."
                                      if self.lang == "ko" else
                                      "Fills in keys saved on other assets, per broker. "
                                      "Existing keys are never overwritten."),
                          foreground="#9ca3af").pack(side="left", padx=(6, 0))

        # ── 둘째 접이식: 자산별 계좌 설정(대표 2026-08-11 "브로커 설정 드롭다운, 그 아래
        #    계좌 설정 드롭다운" - 두 유닛을 독립 접이식으로). 자산 탭은 공유(_asset 하나).
        frm = _outer_frm
        if not hasattr(self, "_acct_open"):
            self._acct_open = bool(d.get("acct_open", True))
        self._acct_hdr = tk.Button(
            frm, text=("▾ " if self._acct_open else "▸ ")
            + ("자산별 계좌 설정 (NQ, GC, BTC)" if self.lang == "ko"
               else "Per-asset account setup (NQ, GC, BTC)"),
            command=self._toggle_acct, relief="flat", anchor="w",
            font=("Helvetica", 12, "bold"), padx=0)
        self._acct_hdr.pack(fill="x", anchor="w", pady=(6, 0))
        self._acct_body = ttk.Frame(frm)
        if self._acct_open:
            self._acct_body.pack(fill="x")
        frm = self._acct_body
        # 이 접이식에도 자산 탭(같은 _asset 공유 - 한쪽에서 바꾸면 둘 다 그 자산으로)
        atab2 = ttk.Frame(frm); atab2.pack(fill="x", pady=(2, 6))
        for _a in _ASSETS:
            _lbl2 = _ASSET_LABEL[_a]["ko" if self.lang == "ko" else "en"]
            tk.Button(atab2, text=_lbl2, command=lambda a=_a: self._on_asset(a),
                      relief=("sunken" if _a == self._asset else "raised"),
                      bg=("#eaf7ee" if _a == self._asset else "#e2e8f0"),
                      fg=("#178a3a" if _a == self._asset else "#333"),
                      padx=14, pady=4).pack(side="left", padx=(0, 4))

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
            ttk.Label(frm, text=("사용 계좌 — 목록에서 골라 추가 (계좌별 1R, 실행 여부)"
                                 if self.lang == "ko"
                                 else "Accounts — pick from the list (per-account 1R & on/off)"),
                      foreground="#555", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=(10, 1))
            for _i, _ac in enumerate(_accts):
                self._acct_edit_row(frm, _i, _ac, spec, deletable=True, show_on=True)
            addr = ttk.Frame(frm); addr.pack(fill="x", pady=(3, 2))
            # 행 중심 등록(대표 2026-08-11 "추가 누르면 빈 칸 등장, 행에서 브로커 고르고
            # 가용 목록에서 선택"): 하단 통합 드롭다운 폐지 - [계좌 추가]가 빈 행을 만든다.
            ttk.Button(addr, text=("계좌 추가" if self.lang == "ko" else "Add account"), width=10,
                       command=self._add_acct).pack(side="left")
            self.b_acc.pack(in_=addr, side="left", padx=(4, 0))
            # 가용 계좌 자동 로드 - 행의 계좌 콤보가 이 목록을 쓴다.
            # Topstep=API, Lucid=브리지 tick(2026-08-12부터 자동 - NT8 켜져 있을 때).
            if self._broker_of(self._asset) == "projectx":
                self._autoload_topstep_scope(self._asset)
            if "nt8" in _ASSET_BROKERS.get(self._asset, []):
                self._autoload_nt8_avail(self._asset)
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
            ttk.Label(frm, text=("② 계좌 — 거래소별 1R, 실행 여부 (동시 발주 가능)"
                                 if self.lang == "ko"
                                 else "② Accounts — per-exchange 1R & on/off"),
                      foreground="#555", font=("Helvetica", 10, "bold")).pack(anchor="w", pady=(6, 1))
            for _i, _ac in enumerate(_accts):
                self._acct_edit_row(frm, _i, _ac, spec,
                                    deletable=len(_accts) > 1, show_on=len(_accts) > 1)
            _addr = ttk.Frame(frm); _addr.pack(fill="x", pady=(3, 2))
            ttk.Button(_addr, text=("계좌 추가" if self.lang == "ko" else "Add"), width=8,
                       command=self._add_acct).pack(side="left")
        # 다음 행동 안내(2026-08-11 UX #65: "언제 연결 버튼 눌러야 하는지 모르겠다") -
        # 상태에서 계산한 딱 한 문장이 다음 버튼을 가리킨다.
        self._next_lbl = ttk.Label(frm, text="", foreground="#1d4ed8",
                                   font=("Helvetica", 11, "bold"))
        self._next_lbl.pack(anchor="w", pady=(8, 0))
        self._refresh_next_action()
        # '이 자산 전 계좌 1R 확인' 버튼 폐지(대표 2026-08-11 버튼 제로) - 1R은 라이브
        # 패널 자산 줄에 상시 표시. 낡은 "연결 테스트 먼저" 안내도 자동 검증이라 제거.
        frm = _outer_frm                          # 접이식 본문 끝 — 이후(로그)는 바깥에

        # 자동 청산·자동 진입 섹션은 라이브 패널로 통합(대표 2026-07-13) — 개별 정지도 패널 줄에서.
        ttk.Separator(frm).pack(fill="x", pady=8)
        _logrow = ttk.Frame(frm)
        _logrow.pack(fill="x", pady=(6, 0))
        ttk.Label(_logrow, text=("로그" if self.lang == "ko" else "Log"),
                  foreground="#6b7280").pack(side="left")
        ttk.Button(_logrow, text=("로그 복사" if self.lang == "ko" else "Copy log"),
                   command=self._copy_log).pack(side="right")
        ttk.Label(_logrow, text=f"v{self._APP_VER}", foreground="#6b7280").pack(
            side="right", padx=(0, 8))   # 지원 DM에 버전 필수(2026-08-11 UX 감사)
        self.out = scrolledtext.ScrolledText(frm, height=10, font=("Menlo", 11), wrap="word")
        self.out.pack(fill="both", expand=True, pady=(0, 0))

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
            self.log(f"▸ {self._asset}, {_broker_label(self._broker_name)} — "
                     + (("연결됨 ✓ (테스트 통과, 재연결 불필요)" if self.lang == "ko"
                         else "connected ✓ (test passed, no retest needed)") if _ok else
                        ("연결 테스트 필요" if self.lang == "ko" else "connection test required")))
        self._async_load_key(self._acur().get("f1", ""))
        # 스크롤 동기 복원(대표 2026-08-11 "꼭 위로 갔다 내려와야 하나") - 첫 페인트 전에
        # 레이아웃을 확정(update_idletasks)하고 위치를 맞춰 점프 프레임을 없앤다.
        # after(80) 한 발은 지연 로드로 높이가 미세하게 자랄 때의 안전빵.
        if getattr(self, "_keep_y", None):
            def _restore_scroll(y=self._keep_y):
                try:
                    self._scroll_cv.yview_moveto(y)
                except Exception:
                    pass
            try:
                self.root.update_idletasks()
                _restore_scroll()
            except Exception:
                pass
            self.root.after(80, _restore_scroll)
            self._keep_y = None

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
        """연결(_connected) + 멤버십 하트비트(_gate)로 모든 실행 버튼, LIVE를 결정한다.
        reads(계좌목록·계약조회)=연결만 필요 / 청산·자동청산=use / 수동진입=manualentry /
        자동진입=autoentry / force_dry_run=LIVE 잠금. 토큰 무효, 만료, 마스터OFF=fail-closed."""
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
        # 시작 버튼: 라이브 = 권한 + 서버 라이브 잠금 아님
        if hasattr(self, "b_live_start"):
            _pu = bool(g.get("ok") and g.get("enabled") and caps.get("use"))
            _pa = bool(g.get("ok") and g.get("enabled") and caps.get("autoentry"))
            en(self.b_live_start, (_pu or _pa) and live_ok)
        self._refresh_live_panel()
        # fail-closed: 돌던 루프가 '권한'을 잃으면(토큰 변경·강등·만료·마스터 OFF) 자동 중지한다.
        # 버튼만 끄면 이미 도는 스레드가 계속 진입/청산하는 구멍이 생긴다.
        # ⚠️ 판정은 하트비트 권한만 — 연결(_connected)은 '현재 탭' 상태라 여기 섞으면
        # 탭 전환이 돌던 루프를 죽인다(대표 2026-07-11 "자산 옮기면 다 리셋" 버그).
        perm_use = bool(g.get("ok") and g.get("enabled") and caps.get("use"))
        perm_auto = bool(g.get("ok") and g.get("enabled") and caps.get("autoentry"))
        _tripped = []
        if getattr(self, "_sig_on", False) and not (perm_auto or perm_use):
            self._sig_on = False
            self._sig_accts.clear()
            self._set_sig_ind(False)
            self.log("⏹ 신호 확인 권한 상실 → 신호 대기 자동 중지 (fail-closed).")
            _tripped.append("신호 확인")
        elif getattr(self, "_sig_on", False) and not perm_auto and not getattr(self, "_watch_only", False):
            # Autopilot→Operator 강등: 자동 발주만 멈추고 계좌 목록·연결은 유지, 확인 모드로 전환
            # (완전 해제하면 Operator가 다시 켜야 하는데, 이 등급은 수동으로 켤 수 있는 상태다).
            # ⚠️_tripped에는 안 넣는다 - 그건 15분 반복 알림(_notify_disarmed)을 켜는데, 이 상태는
            # '고장'이 아니라 등급대로 계속 도는 정상 상태다. autoentry 없는 한 영원히 안 풀려
            # Operator가 계속 반복 팝업에 시달린다(2026-08-07 리마인드 로직의 함정).
            self._watch_only = True
            self.log("⏹ 자동 진입 권한 상실 → 이제부터는 신호 확인만 합니다(수량 비공개, 자동 발주 없음).")
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
            self._disarmed_reason = ", ".join(_tripped)
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
            # 공개 표시명(대표 2026-08-18 랜딩 스샷에서 내부명 'royal' 발각): 내부 등급명은
            # 서버·코드 정본 그대로 두고 **화면에만** 공개명을 쓴다(admin은 admin 그대로).
            _TIER_PUB = {"royal": "Autopilot", "member": "Operator",
                         "guest": "Preview", "public": "Preview"}
            _tname = g.get("tier", "—")
            txt = self.t("gate_ok").format(
                tier=_TIER_PUB.get(_tname, _tname), u="✓" if caps.get("use") else "✗",
                a="✓" if caps.get("autoentry") else "✗",
                dry=self.t("gate_dry") if g.get("force_dry_run") else "")
            col = "#1a7f37"
        try:
            self.gate_lbl.config(text=txt, foreground=col)
        except Exception:
            pass

    def _toggle_token_show(self):
        """토큰 표시/잠금 토글 — 어깨너머, 스트리밍 노출 방지(기본 잠금)."""
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
            # 하트비트가 한 번이라도 돌면 상시 생존 핑을 띄운다(2026-08-28 R14 P0) -
            # 신호 루프 유무와 무관하게 앱이 떠 있는 동안 심박이 뛰어야 한다.
            try:
                self.root.after(0, self._warn_kc_failed)   # 평문 잔존 경고(1회)
                self._hb_start()
                self._watch_start()      # 표시 전용 피드(등급 무관 - 지연은 서버가 건다)
            except Exception:
                pass
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


    def _register_avail(self):
        """① 가용 계좌 수동 등록(대표 2026-08-11) - API 목록이 없는 브로커(Lucid 등)용.
        등록하면 ② 사용 계좌 드롭다운에 뜬다. 중복은 무시."""
        try:
            _v = str(self._avail_entry.get()).strip()
        except Exception:
            _v = ""
        if not _v:
            return
        _av = self._avail_of(self._asset, self._broker_name)
        if _v not in _av:
            _av.append(_v)
            self._save_cfg()
        self._build()                        # 가용 줄·② 드롭다운 즉시 반영

    def _avail_of(self, asset, broker=None):
        """이 (자산, 브로커)의 가용 계좌 목록(대표 2026-08-11 확정 설계) - ①브로커 설정에서
        등록/자동 로드되고, ②사용 계좌는 여기서 고르기만 한다. creds 블롭에 함께 저장."""
        return self._creds_of(asset, broker).setdefault("avail", [])

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
        """_save_full 래퍼 — 현재 앱 상태를 yaml로 영속화 + 저장 확인 표시(2026-08-11 UX:
        '적용됐는지 안 보인다' - 저장이 일어날 때마다 상태 라벨이 ✓와 시각으로 답한다)."""
        _save_full(self.lang, self._token, self._acfg, self._profile, **kw)
        self._flash_saved()

    def _flash_saved(self):
        """설정 영역 상단의 저장 상태 라벨을 '✓ 저장됨 HH:MM:SS'로 갱신, 잠깐 초록 강조."""
        try:
            import datetime as _dtf
            lbl = getattr(self, "_saved_lbl", None)
            if lbl is None:
                return
            # ⚠️키체인 저장이 실패했으면 **초록 '저장됨'을 띄우지 않는다**(2026-08-28
            # 리뷰 P1). 랜딩과 약관이 "보안 저장소가 없으면 앱이 알려 드립니다"라고
            # 약속하는 그 시점이 정확히 여기인데, 종전에는 실패해도 초록 확인만 떴다.
            _kcf = list(globals().get("_KC_LAST_FAILED") or [])
            if _kcf and not getattr(self, "_kc_warned_save", None) == tuple(_kcf):
                self._kc_warned_save = tuple(_kcf)
                try:
                    self._kc_warn_now(_kcf)
                except Exception:
                    pass
            lbl.config(text=(("⚠ 저장됨(평문) " if self.lang == "ko" else "⚠ saved (plain) ")
                             if _kcf else
                             ("✓ 저장됨 " if self.lang == "ko" else "✓ saved "))
                       + _dtf.datetime.now().strftime("%H:%M:%S")
                       + (" - 재시작해도 유지됩니다" if self.lang == "ko" else " - kept across restarts"),
                       foreground=("#b45309" if _kcf else "#15803d"))   # 실패는 주황
            # 1.5초 뒤 회색 복귀 - 위젯을 캡처하는 예약 콜백이라, 그 사이 화면이 재구성되면
            # 죽은 라벨을 만져 [tk] invalid command 오류가 실행 기록을 도배했다(9/4 새 기기
            # 셋업 중 브로커 연속 전환 실사고). 생존 확인 후에만 만진다.
            def _dim(l=lbl):
                try:
                    if l.winfo_exists():
                        l.config(foreground="#6b7280")
                except Exception:
                    pass
            self.root.after(1500, _dim)
        except Exception:
            pass

    def _refresh_next_action(self):
        """현재 자산 탭의 상태 → '다음 행동' 한 문장. 토큰→키→계좌→저장/테스트→가동."""
        try:
            lbl = getattr(self, "_next_lbl", None)
            if lbl is None:
                return
            ko = self.lang == "ko"
            a = self._asset
            armed = any(k[0] == a for k in (set(self._sig_accts) | set(self._auto_accts)))
            cr = self._creds_of(a)
            accts = [x for x in self._accts_of(a) if (x.get("id") or "").strip()]
            if not (self._token or "").strip():
                t = "다음: 맨 위에 멤버십 토큰을 붙여넣으세요" if ko else "Next: paste your membership token at the top"
            elif not (cr.get("f1") or "").strip():
                t = "다음: 브로커를 고르고 API 키(또는 브리지 토큰)를 입력하세요" if ko else "Next: pick a broker and enter its API key / bridge token"
            elif not accts:
                t = "다음: [계좌 추가]로 계좌 이름을 등록하세요" if ko else "Next: add your account with [Add]"
            elif a not in getattr(self, "_test_ok", set()):
                t = "다음: 키, 계좌를 입력하세요 - 저장, 연결 확인은 자동입니다" if ko else "Next: enter key & account - saving & verification are automatic"
            elif armed:
                t = "가동 중 - 신호가 오면 자동으로 실행됩니다" if ko else "Armed - runs automatically on the next signal"
            else:
                t = "준비 완료 - [▶ 라이브 시작]" if ko else "Ready - press [▶ Go Live]"
            lbl.config(text="👉 " + t)
        except Exception:
            pass

    def _acur(self):
        """현재 자산 탭의 브로커 + 그 자산 브로커 크레덴셜 {broker,f1,f3}."""
        cr = self._creds_of(self._asset)
        return {"broker": self._broker_name, "f1": cr.get("f1", ""), "f3": cr.get("f3", "")}

    def _collect_acct_widgets(self):
        """현재 자산 탭 계좌 위젯값(on, 라벨, 1R) → self._acfg[자산]['accounts'] 반영(저장은 호출부가)."""
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
                        # 브로커가 바뀌면 옛 브로커의 계좌 ID를 남기지 않는다(대표 2026-09-15):
                        # 화면 잠금(_acct_edit_row)이 1차 방어, 여기는 2차 - 남은 ID로 조용히
                        # 연결 안 된 행이 되는 대신 '계좌 선택'으로 되돌리고 알린다.
                        _prev = self._acct_broker(self._asset, accts[idx])
                        if _prev != _bv and (accts[idx].get("id") or "").strip():
                            accts[idx]["id"] = ""
                            _icb0 = w.get("acct_id")
                            if _icb0 is not None:
                                try:
                                    _icb0.set("계좌 선택" if self.lang == "ko" else "pick")
                                except Exception:
                                    pass
                            self._bk_switch_warn = True
                        accts[idx]["broker"] = _bv
            except Exception:
                pass
            try:
                _icb = w.get("acct_id")
                if _icb is not None:
                    _v = str(_icb.get()).strip()
                    # "…끝자리"/"계좌 선택" 표시값은 무시 - 가용 목록 선택이나 직접 입력만 저장
                    if _v and not _v.startswith("…") and _v not in ("계좌 선택", "pick"):
                        accts[idx]["id"] = _v
                        if not (accts[idx].get("label") or "").strip():
                            accts[idx]["label"] = _v[-4:]
            except Exception:
                pass
        if getattr(self, "_bk_switch_warn", False):
            self._bk_switch_warn = False
            try:
                messagebox.showwarning(
                    "EQ Autopilot",
                    ("브로커를 바꿔서 이 행의 계좌 선택을 비웠습니다 - 새 브로커의 가용 목록에서 "
                     "계좌를 다시 골라 주세요. 고르기 전에는 이 행으로 주문이 나가지 않습니다."
                     if self.lang == "ko" else
                     "The broker changed, so this row's account was cleared - pick an account "
                     "from the new broker's list. No orders go out on this row until you do."))
            except Exception:
                pass
        # 같은 계좌 중복 등록 가드(대표 2026-08-18 "같은 계좌가 계속 추가됨" - Lucid 실사고):
        # 한 자산 안에서 같은 계좌ID 행이 둘이면 진입이 그 계좌에 **두 번** 나간다.
        # 첫 행만 남기고 뒤 행의 id를 비운다(행은 남겨 다른 계좌를 고르게) + 경고 1회.
        try:
            _seen_ids = set()
            _cleared = False
            for _i2, _ac2 in enumerate(accts):
                _idv = str(_ac2.get("id") or "").strip().lower()
                if not _idv:
                    continue
                if _idv in _seen_ids:
                    _ac2["id"] = ""
                    _cleared = True
                    _icb2 = (getattr(self, "_acct_widgets", {}).get(_i2) or {}).get("acct_id")
                    if _icb2 is not None:
                        try:
                            _icb2.set("계좌 선택" if self.lang == "ko" else "pick")
                        except Exception:
                            pass
                else:
                    _seen_ids.add(_idv)
            if _cleared:
                messagebox.showwarning(
                    "EQ Autopilot",
                    ("이미 등록된 계좌입니다 - 같은 계좌는 자산당 한 번만 등록할 수 있습니다"
                     "(중복이면 주문이 두 번 나갑니다). 중복 행의 계좌 선택을 비웠습니다."
                     if self.lang == "ko" else
                     "That account is already registered - each account can be used only "
                     "once per asset (a duplicate would double the orders). The duplicate "
                     "row's selection was cleared."))
        except Exception:
            pass

    def _save_current_asset(self):
        """현재 탭 크레덴셜(f1,f3) → 이 자산 브로커 창고, 비밀(f2) → Keychain, 실행자산, 계좌위젯
        반영, 영속화. 계좌ID, 1R, on, 라벨은 자산 탭 계좌 위젯이 관리(자산별 accounts)."""
        bk = self._broker_name
        self._acfg[self._asset]["broker"] = bk
        cr = self._creds_of(self._asset, bk)
        # ⚠️**삭제 경로**(2026-08-28 리뷰 P0). v2026.08.28h가 비밀을 키체인으로 옮기면서
        # 저장만 옮기고 삭제를 안 옮겨, 자격을 지워도 다음 기동에 되살아났다 - 회원이
        # "이 거래소는 끊었다"고 믿는 계좌로 실주문이 다시 나가는 상태였다(대표 맥이
        # 이미 그랬다: config.yaml은 빈 값인데 키체인에 값이 살아 있었다).
        # ⚠️판정은 "지금 비어 있다"가 아니라 **"값이 있었는데 비워졌다"**여야 한다.
        # 키체인 읽기가 실패한 기동(잠긴 키체인, 헤드리스, keyring 백엔드 없음)에서는
        # _load 되채우기가 비어 메모리도 빈 값이 되는데, 그때 "비어 있으니 지운다"를
        # 하면 첫 자동저장이 자격을 영구 파괴한다.
        _prev_f1 = str(cr.get("f1") or "").strip()
        _prev_f3 = str(cr.get("f3") or "").strip()
        if hasattr(self, "user"):
            cr["f1"] = self.user.get().strip()
        if hasattr(self, "f3"):
            cr["f3"] = self._f3()
        _sec = _secret_fields(bk)
        if "f1" in _sec and _prev_f1 and not str(cr.get("f1") or "").strip():
            _kc_del(_kc_key(self._asset, bk, "f1"))
            _kc_del(_prev_f1)               # f2의 레거시 계정 키(=옛 f1 값)도 함께
            self.log("🗑 " + ("저장된 자격을 삭제했습니다(보안 저장소 포함)."
                              if self.lang == "ko" else
                              "Deleted the stored credential, including the secret store."))
        if "f3" in _sec and _prev_f3 and not str(cr.get("f3") or "").strip():
            _kc_del(_kc_key(self._asset, bk, "f3"))
        if hasattr(self, "key"):                 # 비밀(f2) → Keychain (f1 키로)
            _f1_now = str(cr.get("f1") or "").strip()
            if _f1_now:
                _kc_save(_f1_now, self.key.get())
            elif _prev_f1:
                _kc_del(_prev_f1)           # f1을 지웠으면 f2도 남기지 않는다
            # f1이 비었는데 f2를 저장하면 "default" 계정으로 새 비밀이 생겨 영영 안 지워진다
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
        서버 불통, 토큰 미입력 시 최후수단 = 구 삭제 방식(_pin_wipe_reset)."""
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
                 "다시 붙여넣어야 합니다(계좌 목록, 1R 등 다른 설정은 유지). 계속할까요?") if _ko else
                ("⚠ Resetting deletes every API secret stored by this app — you will need to "
                 "paste each broker key again (accounts, 1R and other settings are kept). "
                 "Continue?")):
            return
        # ⚠️새 키체인 항목까지 지운다(2026-08-28 리뷰 P1). 이 대화상자는 "이 앱에 저장된
        # 모든 API 비밀키가 삭제된다"고 고지하는데, v2026.08.28h 이후로는 레거시 f2 항목만
        # 지워 Bybit·Bitget의 API Key, Bitget Passphrase, NT8 Bridge Token,
        # Tradovate cid:sec가 그대로 남았다 - 고지가 거짓이 된 상태였다.
        for _a, _s in (self._acfg or {}).items():            # 등록된 브로커 비밀 전부 삭제
            for _b, _cr in (_s.get("creds") or {}).items():
                _f1 = (_cr.get("f1") or "").strip()
                if _f1:
                    _kc_del(_f1)                             # 레거시 f2(계정 키=f1 값)
                for _f in _secret_fields(_b):                # 신 스키마 eq:<자산>:<브로커>:<필드>
                    _kc_del(_kc_key(_a, _b, _f))
                    _cr[_f] = ""                             # 메모리도 비워 되채움 차단
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
        """클립보드 → 일반 Entry 교체 붙여넣기(strip). f1(API Key), f3(Passphrase)용."""
        try:
            entry.delete(0, "end")
            entry.insert(0, self.root.clipboard_get().strip())
            self._schedule_autosave()        # 붙여넣기도 자동 저장·검증(2026-08-11 버튼 제로)
        except Exception:
            pass

    def _paste_key(self):
        if not self._unlocked:
            messagebox.showinfo("PIN", self.t("locked_msg")); return
        try:
            self.key.delete(0, "end")
            self.key.insert(0, self.root.clipboard_get().strip())
            self._schedule_autosave()        # 붙여넣기도 자동 저장·검증
        except Exception:
            pass

    def _copy_log(self):
        """화면 로그 전체를 클립보드로 - 지원 DM에 붙여넣기(2026-08-11 UX 감사)."""
        try:
            txt = self.out.get("1.0", "end").strip()
            self.root.clipboard_clear()
            self.root.clipboard_append(f"[EQ Autopilot v{self._APP_VER}]\n" + txt[-8000:])
            self.log("로그를 클립보드에 복사했습니다." if self.lang == "ko"
                     else "Log copied to clipboard.")
        except Exception:
            pass

    def log(self, m):
        _log_to_file(m)          # 파일에도 영속(타임스탬프 부여) — 대표 2026-07-15
        # 화면 로그에도 시각(2026-08-11 UX 감사: 스샷 한 장 지원의 전제). 빈 줄·구분선은 그대로.
        try:
            import datetime as _dtl2
            _m = str(m)
            if _m.strip() and not _m.startswith(("═", "─", "\n═")):
                # 버전 마커(대표 2026-09-04 "각 로그 옆에 발생 시 버전"): 붙여넣은 로그만
                # 봐도 어느 빌드가 찍었는지 알게. 압축 표기 = 버전 끝 3~4자(예: 04a).
                _vp = self._APP_VER.rsplit(".", 1)[-1] if "." in self._APP_VER else self._APP_VER
                _pfx = _dtl2.datetime.now().strftime("%H:%M:%S ") + f"[{_vp}] "
                _m = ("\n" + _pfx + _m.lstrip("\n")) if _m.startswith("\n") else (_pfx + _m)
            self.q.put(_m)
            return
        except Exception:
            pass
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
            _f = ", ".join(_missing)
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
        # 동의 스탬프(대표 2026-09-03): 문안 버전이 바뀐 첫 실행 동작 때 1회 영속 -
        # 생존 핑(cv/ct)으로 서버 증거 원장에 "어느 문안에 언제 동의했나"가 남는다.
        try:
            _c = (self._profile or {}).get("consent") or {}
            if _c.get("ver") != _CONSENT_VER:
                import time as _t0
                self._profile["consent"] = {"ver": _CONSENT_VER, "at": int(_t0.time())}
                self._save_cfg()
                self._alive_ping(force=True)
        except Exception:
            pass
        return True

    def _send_ev(self, kind, asset, **extra):
        """실행 라이프사이클 마커(/eqalive ev, 대표 2026-09-03 "진입 시도 진입 성공 등등").
        서버 증거 원장 전용 단발 - 생존 상태 페이로드는 안 실어(핑 지문 오염 방지) 서버가
        ev 단독 요청으로 처리한다. 실패 무해, 비차단."""
        def _bg():
            try:
                import requests as _rq
                _rq.post(PUSH_BASE + "eqalive", timeout=6,
                         json={"t": self._token, "m": _machine_id(), "v": self._APP_VER,
                               "ev": dict({"kind": kind, "inst": asset}, **extra)})
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()

    def _net_probe(self) -> str:
        """인터넷 상태 실측 1회(대표 2026-09-04 "인터넷 불안정에 의한 문제도 로깅") -
        EQ 서버 /eqhb 왕복시간을 재서 짧은 증거 문자열로 돌려준다. 진입 실패 순간의
        '망이 어땠나'를 로그, 서버 보고에 동행시키는 용도. 호출부는 실패 경로뿐이라
        블로킹(최대 5초)이어도 무해하다. 9/4 사고: NT8 로그에만 있던 지연 8,607ms
        경고를 우리 기록으로도 갖기 위함."""
        import time as _tp
        try:
            import requests as _rq
            _s = _tp.time()
            _r = _rq.get(PUSH_BASE + "eqhb", timeout=5)
            return f"인터넷 응답 {int((_tp.time() - _s) * 1000)}ms (HTTP {_r.status_code})"
        except Exception as _pe:
            return f"인터넷 프로브 실패({type(_pe).__name__})"

    def _avail_text_color(self, av):
        """가용 계좌 라벨의 (문구, 색). 있으면 초록 굵게 + 개수, 없으면 회색 '아직 없음'
        (대표 2026-09-15 "가용 계좌를 좀 더 잘 보이게 초록색 등으로")."""
        av = list(av or [])
        if av:
            head = (f"가용 계좌 {len(av)}개: " if self.lang == "ko" else f"Available accounts ({len(av)}): ")
            return head + ", ".join(av), "#15803d"
        return (("가용 계좌: 아직 없음" if self.lang == "ko" else "Available accounts: none yet"), "#555")

    def _fill_scope(self, names, asset=None, broker=None):
        """브로커의 신선한 계좌 목록 → 가용 계좌 **미러링(교체)** + UI 갱신.
        2026-08-18b(대표 "예전 계좌가 안 지워져"): append-only였던 avail을 브로커 목록
        정본으로 교체한다 - 닫힌 평가 계좌가 드롭다운에 영원히 남던 것 정리. 등록된
        사용 계좌 행은 안 건드린다(행 관리는 회원 몫). 빈 목록이면 아무것도 안 지운다
        (일시 연결 문제 오살 방지). asset/broker 명시 = 백그라운드 로더가 탭 전환 뒤
        도착해도 자기 자산 avail만 만진다(예전엔 현재 탭 avail을 오염시킬 수 있었다)."""
        try:
            _a, _b = asset or self._asset, broker or self._broker_name
            fresh = list(dict.fromkeys(str(n) for n in (names or []) if str(n).strip()))
            if fresh:
                cr = self._creds_of(_a, _b)
                _av = cr.setdefault("avail", [])
                _add = [n for n in fresh if n not in _av]
                _gone = [n for n in _av if n not in fresh]
                if _add or _gone:
                    _av[:] = fresh
                    self._save_cfg()
                    if _add:
                        self.log(("🧾 가용 계좌 등록: " if self.lang == "ko"
                                  else "🧾 Accounts added: ") + ", ".join(_add))
                    if _gone:
                        self.log(("🧾 가용 계좌 정리(브로커에 더 없음): " if self.lang == "ko"
                                  else "🧾 Accounts removed (no longer at broker): ")
                                 + ", ".join(_gone))
            if _a == self._asset and _b == self._broker_name:   # UI는 그 탭이 떠 있을 때만
                _av = self._avail_of(self._asset, self._broker_name)
                if _av and hasattr(self, "_avail_lbl"):
                    _t, _c = self._avail_text_color(_av)
                    self._avail_lbl.config(text=_t, foreground=_c, font=("Helvetica", 11, "bold"))
                for _w in getattr(self, "_acct_widgets", {}).values():
                    _icb = _w.get("acct_id")
                    if _icb is not None:
                        _icb["values"] = list(_av)
        except Exception:
            pass

    def _autoload_nt8_avail(self, asset):
        """Lucid(NT8) 가용 계좌 자동 로드(대표 2026-08-12) - 브리지 tick의 Account.All을
        받아 이 자산 가용 목록에 병합. NT8이 꺼져 있으면 타임아웃 후 조용히 생략(수동 등록
        경로는 그대로 살아있음). (포트,토큰)별 캐시로 탭 전환마다 재조회하지 않는다."""
        cr = self._creds_of(asset, "nt8")
        f1 = (cr.get("f1") or "").strip()
        if not f1:
            return
        ck = ("nt8", cr.get("f3", ""), f1[:8])
        cache = getattr(self, "_scope_cache", None)
        if cache is None:
            cache = self._scope_cache = {}
        if ck in cache:
            names = cache[ck]
            _av = cr.setdefault("avail", [])
            _new = [n for n in names if n not in _av]
            if _new:
                _av.extend(_new)
                self._save_cfg()
            self._fill_scope(names)
            return
        import threading as _th

        def w(attempt: int = 0):
            try:
                b = _build_broker("nt8", f1, "", cr.get("f3", ""), [])
                names = [str(a.get("name")) for a in b._accounts() if a.get("name")]
            except Exception:
                names = []
            if not names:
                # ⚠️기동 직후엔 브리지가 1~2초 늦게 깬다(대표 2026-08-12 "뜨자마자 경고 주는데
                # 일이초 있다 보니 연결 댐") → 팝업 대신 조용한 재시도 3회. 최종 실패도
                # 로그 한 줄만 - NT8 기동 경고는 [연결 테스트]와 [라이브 시작]이 그 시점에 준다.
                if attempt < 3:
                    self.root.after(2500, lambda: _th.Thread(
                        target=w, args=(attempt + 1,), daemon=True).start())
                elif not (cr.get("avail") or []):
                    self.root.after(0, lambda: self.log(
                        f"  , {asset} Lucid 가용 계좌 자동 로드 대기 - NT8이 켜지면 자동 등록됩니다"
                        if self.lang == "ko" else
                        f"  , {asset} Lucid accounts pending - they register once NT8 is running"))
                return
            cache[ck] = names

            def done():
                self._fill_scope(names, asset=asset, broker="nt8")
            self.root.after(0, done)
        _th.Thread(target=w, daemon=True).start()

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
            # 캐시 히트도 이 자산의 avail에 반영(대표 2026-08-11: 자산별 creds 블롭이라
            # 각자 채워야 함) - 미러링은 _fill_scope가 한다.
            self._fill_scope(cache[ck], asset=asset, broker="projectx")
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
                # 영속·미러링·UI 갱신 전부 _fill_scope(자산·브로커 명시라 탭 전환 무해).
                self.root.after(0, lambda: self._fill_scope(names, asset=asset,
                                                            broker="projectx"))
        _th.Thread(target=w, daemon=True).start()

    def _armed_here(self, asset) -> bool:
        """그 자산이 지금 무장 중이면 설정 변경을 막는다(2026-08-28 리뷰 P1).
        무장은 [라이브 시작] 시점의 계좌·브로커 스냅샷(_sig_accts/_auto_accts)으로 돈다.
        가동 중에 계좌나 브로커를 갈아치우면 화면은 새 설정을 보여주는데 실주문은 옛
        계좌로 나간다 - 회원이 화면을 믿을 수 없게 되는 가장 나쁜 종류의 불일치다."""
        if not any(k[0] == asset for d in (getattr(self, "_sig_accts", None) or {},
                                           getattr(self, "_auto_accts", None) or {})
                   for k in d):
            return False
        try:
            messagebox.showwarning(
                self.t("sec_live"),
                (f"{asset}이(가) 지금 무장 중입니다. 가동 중에는 계좌나 브로커 설정을 "
                 f"바꿀 수 없습니다 - 화면과 실제 발주 대상이 갈라지기 때문입니다.\n\n"
                 f"{asset} 체크를 껐다가 설정을 바꾸고 다시 켜세요."
                 if self.lang == "ko" else
                 f"{asset} is armed right now. Account and broker settings cannot change "
                 f"while it runs - the screen and the actual order target would diverge."
                 f"\n\nUncheck {asset}, change the settings, then check it again."))
        except Exception:
            pass
        return True


    def _pull_broker_keys(self):
        """① 브로커 키 전수 가져오기(대표 2026-09-04 "전 브로커 세팅을 다 복사해오게").
        다른 자산들의 브로커 자격(f1·f3·가용 계좌)을 **브로커별로** 그러모아 이 자산에
        병합한다. 종전 자산 단위 복사는 자산마다 브로커가 다르면(나스닥=탑스텝, 금=루시드)
        브로커 선택까지 소스 것으로 갈아쳐 엉뚱한 필드 값이 뜬 것처럼 보였다 - 폐지.
        원칙:
          · 이 자산의 브로커 선택(드롭다운)은 안 건드린다 - 보던 화면 유지
          · 이 자산에 이미 f1이 있는 브로커는 안 덮는다(살아 있는 키 보호) - 값이 다르면
            ⚠ 로그로 알려만 준다
          · 같은 브로커가 여러 자산에 있으면 f1이 있는 첫 자산 것(NQ·GC·BTC 순)
          · f2(비밀)는 키체인이 f1 값을 키로 공유하므로 따로 옮길 게 없다
         , 사용 계좌(②) 행은 그대로(유닛 분리)"""
        import copy as _copy
        dst = self._asset
        if self._armed_here(dst):
            self.log("⏸ 무장 중에는 브로커 키를 가져오지 않습니다 - 정지 후 다시 시도하세요.")
            return
        self._save_current_asset()
        _ok = _ASSET_BROKERS.get(dst, [])
        _dstcr = self._acfg[dst].setdefault("creds", {})
        _moved, _kept = [], []
        for bk in _ok:
            _mine = str((_dstcr.get(bk) or {}).get("f1") or "").strip()
            for a2 in _ASSETS:
                if a2 == dst:
                    continue
                cr2 = (self._acfg.get(a2, {}).get("creds") or {}).get(bk) or {}
                _theirs = str(cr2.get("f1") or "").strip()
                if not _theirs:
                    continue
                if _mine:
                    if _mine != _theirs:
                        _kept.append(f"{_broker_label(bk)}({a2})")
                    break                       # 살아 있는 키 보호 - 안 덮는다
                _dstcr[bk] = _copy.deepcopy(cr2)
                _moved.append(f"{_broker_label(bk)}←{a2}")
                break
        if not _moved and not _kept:
            self.log("📋 가져올 브로커 키가 없습니다 - 다른 자산에도 키가 없거나 이미 다 있습니다.")
            return
        if _moved:
            self._save_cfg()
            self._build()
            self.log("📋 브로커 키 가져오기 완료: " + ", ".join(_moved)
                     + " (브로커 선택과 기존 키, 사용 계좌 행은 그대로)")
        if _kept:
            self.log("⚠ 값이 달라 안 덮은 키: " + ", ".join(_kept)
                     + " - 이 자산 키를 쓰려면 해당 브로커 칸을 비운 뒤 다시 가져오세요.")

    def _transfer_pin(self):
        """PIN 게이트(설정 내보내기 공용). 없으면 새로 만들고, 틀리면 재설정 흐름(_pin_forgot)."""
        if not _pin_hash():
            p = simpledialog.askstring("PIN", self.t("pin_new"), show="*", parent=self.root)
            if not p:
                return None
            _pin_set(p)
            return p
        p = simpledialog.askstring("PIN", self.t("pin_enter"), show="*", parent=self.root)
        if p is None:
            return None
        if not p or not _pin_ok(p):
            self._pin_forgot()
            return None
        return p

    def _export_settings(self):
        """설정 내보내기(대표 2026-09-04 "브로커, 계좌 export/import, PIN으로 열기").
        범위 = 언어·토큰·공개프로필·자산별 전부(브로커 선택·자격 f1/f3·가용/사용 계좌·1R).
        비밀(f2)은 키체인(f1 키)에서 그러모아 함께 - 전부 **PIN 잠금 파일**로만 나간다."""
        self._save_current_asset()                     # 화면 값 → _acfg 플러시
        pin = self._transfer_pin()
        if not pin:
            return
        import copy as _copy
        assets, n_sec = {}, 0
        for a, c in (self._acfg or {}).items():
            c2 = _copy.deepcopy(c)
            for b, cr in (c2.get("creds") or {}).items():
                _f1 = str(cr.get("f1") or "").strip()
                if _f1 and _BROKER_SPEC.get(b, {}).get("f2"):
                    _f2 = _kc_load(_f1)
                    if _f2:
                        cr["f2"] = _f2
                        n_sec += 1
            assets[a] = c2
        payload = {"kind": "eq-autopilot-settings", "v": 1, "app": self._APP_VER,
                   "lang": self.lang, "token": self._token,
                   "dry_run": bool(self.live_dry.get()) if hasattr(self, "live_dry") else True,
                   "profile": dict(self._profile or {}), "assets": assets}
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".eqset",
            initialfile=f"eq-settings-{_dt.date.today():%Y%m%d}.eqset",
            filetypes=[("EQ settings", "*.eqset")])
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(_settings_export_blob(pin, payload))
        except Exception as e:
            messagebox.showwarning("Export", str(e)); return
        self.log("📦 " + (f"설정 내보내기 완료: {path}" if self.lang == "ko"
                          else f"Settings exported: {path}"))
        messagebox.showinfo(
            ("설정 내보내기" if self.lang == "ko" else "Export settings"),
            ("저장했습니다.\n\n이 파일에는 브로커 키가 들어 있습니다(PIN으로 암호화).\n"
             "새 기기에서 가져오기가 끝나면 파일을 삭제하세요."
             if self.lang == "ko" else
             "Saved.\n\nThis file contains your broker keys (encrypted with your PIN).\n"
             "Delete it after importing on the new machine."))

    def _import_settings(self):
        """설정 가져오기 - 내보내기 파일(PIN)로 이 기기를 그 설정 그대로 맞춘다.
        무장 중엔 금지(_armed_here와 같은 이유 - 화면과 실발주 대상이 갈라진다).
        적용: f2 → 키체인(f1 키, 현행 저장 방식 그대로), 나머지 → _acfg → _save_cfg
        (가 f1/f3를 키체인으로 옮기고 평문을 벗긴다). 토큰까지 오므로 재시작 안내."""
        if (getattr(self, "_sig_accts", None) or getattr(self, "_auto_accts", None)):
            messagebox.showwarning(
                self.t("sec_live"),
                ("가동 중에는 설정을 가져올 수 없습니다. 전체 정지 후 다시 시도하세요."
                 if self.lang == "ko" else
                 "Cannot import while live. Stop everything first."))
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.root, filetypes=[("EQ settings", "*.eqset"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as e:
            messagebox.showwarning("Import", str(e)); return
        p = simpledialog.askstring(
            "PIN", ("내보낼 때 쓴 PIN을 입력하세요" if self.lang == "ko"
                    else "Enter the PIN used when exporting"),
            show="*", parent=self.root)
        if not p:
            return
        try:
            d = _settings_import_blob(p, raw)
        except Exception:
            messagebox.showwarning(
                ("설정 가져오기" if self.lang == "ko" else "Import settings"),
                ("PIN이 다르거나 파일이 손상됐습니다." if self.lang == "ko"
                 else "Wrong PIN or corrupted file."))
            return
        if d.get("kind") != "eq-autopilot-settings" or not isinstance(d.get("assets"), dict):
            messagebox.showwarning("Import", ("설정 파일이 아닙니다." if self.lang == "ko"
                                              else "Not an EQ settings file."))
            return
        if not messagebox.askyesno(
                ("설정 가져오기" if self.lang == "ko" else "Import settings"),
                ("이 기기의 브로커, 계좌 설정을 파일 내용으로 덮어씁니다.\n계속할까요?"
                 if self.lang == "ko" else
                 "This overwrites this machine's broker & account settings.\nContinue?")):
            return
        n_sec = 0
        for a, c in d["assets"].items():
            if a not in _ASSETS:
                continue
            for b, cr in (c.get("creds") or {}).items():
                _f2 = str(cr.pop("f2", "") or "")     # f2는 _acfg에 안 남긴다(키체인 전용)
                _f1 = str(cr.get("f1") or "").strip()
                if _f1 and _f2:
                    if _kc_save(_f1, _f2):
                        n_sec += 1
                    else:
                        self.log("⚠ " + (f"{a}/{_broker_label(b)}: 비밀 저장 실패 - 보안 저장소 확인 필요"
                                          if self.lang == "ko" else
                                          f"{a}/{_broker_label(b)}: secret store write failed"))
            self._acfg[a] = c
        self.lang = d.get("lang", self.lang)
        self._token = str(d.get("token") or self._token)
        if isinstance(d.get("profile"), dict):
            self._profile = d["profile"]
        if not _pin_hash():
            _pin_set(p)                               # 새 기기 PIN = 파일을 연 그 PIN
        self._save_cfg()
        self._unlocked = False
        self._build()
        self.log("📥 " + (f"설정 가져오기 완료(비밀 {n_sec}건 포함)" if self.lang == "ko"
                          else f"Settings imported ({n_sec} secrets)"))
        messagebox.showinfo(
            ("설정 가져오기" if self.lang == "ko" else "Import settings"),
            ("가져왔습니다. 앱을 종료했다가 다시 시작하면 전부 반영됩니다.\n"
             "원본 파일은 삭제하시는 게 안전합니다."
             if self.lang == "ko" else
             "Imported. Quit and restart the app to apply everything.\n"
             "Delete the file afterwards for safety."))

    def _copy_acct_setup(self, src_asset):
        """② 사용 계좌 복사: 계좌 목록, 1R, 모드 + 그 행들이 굴러가는 데 필요한
        브로커 선택, 키, 가용 계좌까지(대표 2026-08-28 "브로커 선택 자체도 다 완전 동일하게
        카피 되야 해"). ①과의 차이는 방향이다 - ①은 계좌 행을 안 건드리고 자격만 옮기고,
        ②는 계좌 행을 옮기면서 그 행이 필요로 하는 자격을 딸려 보낸다."""
        import copy as _copy
        dst = self._asset
        if self._armed_here(dst):
            return
        src_accts = self._accts_of(src_asset)
        if not src_accts or not any((x.get("id") or "").strip() for x in src_accts):
            messagebox.showinfo(self.t("btn_accts"),
                                (f"{src_asset}에 복사할 계좌가 없습니다." if self.lang == "ko"
                                 else f"No accounts to copy from {src_asset}.")); return
        if not messagebox.askyesno(
                ("계좌 설정 복사" if self.lang == "ko" else "Copy account setup"),
                (f"{src_asset}의 사용 계좌를 {dst}(으)로 덮어쓸까요?\n"
                 f"계좌, 1R, 모드와 함께 브로커 선택, 키, 가용 계좌도 {src_asset} 것으로 "
                 f"맞춥니다 - 복사 직후 바로 굴러가게." if self.lang == "ko" else
                 f"Overwrite {dst}'s accounts with {src_asset}'s?\n"
                 f"Broker choice, keys and available accounts come along, so the copy "
                 f"works as-is.")):
            return
        self._collect_acct_widgets()
        # 행 브로커 실체화(대표 2026-08-21 실사고: NQ 루시드 계좌를 GC로 복사하니 Topstep이
        # 됨): 행의 broker 칸이 비면 '그 자산의 기본 브로커'로 해석되는데, 복사 후엔 해석
        # 기준이 목적지 자산으로 바뀐다. 복사 시점에 원본 자산 기준 실효 브로커를 박는다.
        _ok_bks = _ASSET_BROKERS.get(dst, [])
        _copied, _need = [], []
        for a in src_accts:
            a2 = _copy.deepcopy(a)
            try:
                _rb = self._acct_broker(src_asset, a)
            except Exception:
                _rb = ""
            if _rb in _ok_bks:
                a2["broker"] = _rb
                _need.append(_rb)
            else:
                a2.pop("broker", None)    # 이 자산에서 못 쓰는 브로커 - dst 기본으로 해석
            _copied.append(a2)
        self._acfg[dst]["accounts"] = _copied
        # ★ 브로커 선택과 자격까지 함께 옮긴다(대표 2026-08-28 "브로커 선택 자체도 다
        # 완전 동일하게 카피 되야 해"). 종전에는 계좌 행만 옮기고 브로커·키는 dst 것을
        # 그대로 뒀는데, dst가 다른 브로커를 고르고 있으면 옮겨온 행이 **자격 없는
        # 브로커**를 가리켜 계좌 드롭다운이 비고 라이브 시작이 "키 미설정"으로 막혔다.
        # 복사 = 그대로 굴러가는 상태여야 한다.
        _srccr = (self._acfg[src_asset].get("creds") or {})
        _dstcr = self._acfg[dst].setdefault("creds", {})
        _sb = self._acfg[src_asset].get("broker")
        _moved = []
        for _bk in dict.fromkeys(_need + ([_sb] if _sb else [])):
            # 빈 자격은 실제 키를 못 덮는다(①과 같은 이유 - 리뷰 P0)
            if _bk in _ok_bks and _bk in _srccr and (
                    ((_srccr[_bk].get("f1") or "").strip()) or _bk not in _dstcr):
                _dstcr[_bk] = _copy.deepcopy(_srccr[_bk])   # 키 + 가용 계좌 목록
                if (_srccr[_bk].get("f1") or "").strip():
                    _moved.append(_bk)
        if _sb in _ok_bks:
            self._acfg[dst]["broker"] = _sb
        self._save_cfg()
        self._build()
        self.log(f"📋 {src_asset} → {dst} 사용 계좌 복사 완료 ({len(src_accts)}개, "
                 f"브로커 {self._acfg[dst].get('broker')}, 자격 {len(_moved)}종 동반)")

    def _add_acct(self):
        """[계좌 추가] = 빈 행 추가(대표 2026-08-11 행 중심 등록). 브로커, 계좌, 1R은 행에서
        고른다 - 행의 계좌 콤보가 그 행 브로커의 가용 목록을 보여준다."""
        asset = self._asset
        self._collect_acct_widgets()             # 다른 행 미저장 편집 보존
        _bk0 = self._broker_name
        _accts = self._accts_of(asset)
        # 첫 계좌가 완전 빈 슬롯이면 새 행 대신 그걸 쓰게 둔다(중복 빈 행 방지)
        if not (len(_accts) == 1 and not (_accts[0].get("id") or "").strip()
                and _BROKER_SPEC.get(self._broker_of(asset), {}).get("acct")):
            _accts.append(_new_acct(600.0, "", True,
                                    "" if _BROKER_SPEC.get(_bk0, {}).get("acct")
                                    else _broker_label(_bk0), broker=_bk0))
        self._save_cfg()
        self._build()

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
                              "  ③ 키 권한(읽기, 주문) 확인") if self.lang == "ko" else
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
            # Tradovate 데모 검증(2026-09-15): 어댑터가 문서로만 알던 엔티티 필드명(fillPair·fill·
            # cashBalanceLog)과 프론트월 선택을 로그로 확인한다. 값·키·토큰은 안 찍는다.
            if self._broker_name == "tradovate":
                try:
                    _dg = b.diagnostics() or {}
                    self.log("   🔎 Tradovate 진단(값 없음, 필드명만):" if self.lang == "ko"
                             else "   🔎 Tradovate diagnostics (field names only):")
                    for _k, _v in _dg.items():
                        self.log(f"      {_k}: {_v}")
                except Exception as _e:
                    self.log(f"   🔎 진단 실패: {_e}")
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
        # ⚠️로그로 끝내지 않는다(2026-08-28 R16 P0). 이 상태가 정확히 제품 페이지가
        # "손절 없는 포지션은 존재할 수 없습니다"라고 단정한 것의 반례다. 회원이 자리에
        # 없으면 세션 마감까지 무방비인데 스크롤백 말고는 알 길이 없었다.
        _sym = str(contract or "").upper()[:16]
        self._member_alert(
            "stop_miss",
            f"[EQ Autopilot] 손절 주문이 걸리지 않았습니다 ({_sym}). 포지션은 살아 있고 "
            f"세션 마감 자동 청산이 유일한 백스톱입니다. 브로커 화면에서 지금 손절을 "
            f"직접 걸거나 포지션을 정리하세요.",
            f"[EQ Autopilot] The protective stop did not go in ({_sym}). The position is open "
            f"and session-close auto-flatten is the only backstop. Place the stop yourself at "
            f"your broker now, or close the position.")
        try:
            self.root.after(0, lambda: messagebox.showwarning(
                "EQ Autopilot",
                (f"손절 주문이 걸리지 않았습니다 ({_sym}).\n포지션은 살아 있습니다.\n\n"
                 "브로커 화면에서 지금 손절을 직접 걸거나 포지션을 정리하세요."
                 if self.lang == "ko" else
                 f"The protective stop did not go in ({_sym}).\nThe position is open.\n\n"
                 "Place the stop yourself at your broker now, or close the position.")))
        except Exception:
            pass

    def _flatten_unprotected(self, b, aid, contract, why):
        """Market-close a just-entered position whose protective stop was rejected."""
        self.log(f"   🛑 손절 거부됨({why}) → 무방비 포지션 즉시 청산")
        try:
            b.close_contract(aid, contract)
            self.log("   ↩ 포지션 청산 완료(손절 불가로 진입 취소).")
        except Exception as ce:
            self.log(f"   ❌ 긴급 청산 실패: {ce} — 즉시 수동 확인 필요!")
            # 손절이 거부돼 청산하려 했는데 그것마저 실패 - 가장 위험한 상태다.
            self._member_alert(
                "flatten_fail",
                f"[EQ Autopilot] 손절이 거부돼 포지션을 정리하려 했으나 청산도 실패했습니다 "
                f"({str(contract or '')[:16]}). 브로커 화면에서 즉시 확인하세요.",
                f"[EQ Autopilot] The stop was rejected and the emergency close also failed "
                f"({str(contract or '')[:16]}). Check your broker now.")

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
            self.log("ℹ 타임존 DB 없음 — 청산, 진입 시각을 UTC 기준 산술 계산으로 처리합니다(정상).")

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
                # 서버 지정 청산 시각(뉴스 선행 청산). 고영향 지표 발표를 관통해 포지션을
                # 들고 있으면 브로커 규정 위반이라, 그 날만 서버가 시각을 앞당겨 보낸다.
                # 오늘·이 자산 것일 때만 쓰고, 값이 이상하면 조용히 고정 시각으로 돌아간다.
                _ovr = (self._exit_override or {}).get(j["asset"])
                _ovr_why = ""
                if _ovr:
                    try:
                        _ov_dt = _dt.datetime.fromtimestamp(_ovr[0], _dt.timezone.utc)
                        _ov_loc = _ov_dt.astimezone(now.tzinfo)
                        if _ov_loc.date() == now.date():
                            due = _ov_loc.hour * 60 + _ov_loc.minute
                            _ovr_why = _ovr[1] or ""
                    except Exception:
                        pass
                if not (due <= cur < due + AUTO_FIRE_WINDOW_MIN) or fired.get(key) == today:
                    continue
                if _ovr_why:
                    self.log(f"\n⏰ {j['asset']} " + (
                        f"선행 청산 발화 ({_ovr_why})" if self.lang == "ko"
                        else f"early close fired ({_ovr_why})"))
                    self._exit_override.pop(j["asset"], None)   # 그날 1회로 소진
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
                    if live:
                        self._send_ev("exit_attempt", j["asset"])   # 증거 원장(2026-09-03)
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
                            self._report_error("auto_close", _errs)   # 원장+회원 DM(2026-09-03)
                            _pt = ("자동청산 실패 — 수동 확인 필요" if self.lang == "ko"
                                   else "Auto-close failed — manual action needed")
                            _pm = ((f"{_lab} 자동청산이 완전히 끝나지 않았습니다.\n\n{_errs}\n\n"
                                    "브로커 화면에서 포지션을 직접 확인, 청산하세요.") if self.lang == "ko"
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
        """(요약문, 점 색) — 자산 실행 설정 완결성, 연결, 무장 상태를 한 줄로.
        미선택 자산은 '사용 안 함'으로 낸다 — 안 고른 것을 ✗ 미설정으로 찍으면 화면이
        "덜 됐다"고 말해, 한 자산으로 시작한 회원에게 계속 미완성 신호를 준다(대표 2026-08-15).
        완결성 = 크레덴셜 f1 · 켜진 계좌 최소 1개 · (acct 브로커면)계좌ID · 1R>0.
        계좌별 브로커(대표 2026-08-09): 라벨, 키, 연결 판정을 켜진 계좌들의 브로커 전체로."""
        # ⚠ 무장 판정이 먼저다. 체크 해제는 2026-08-28부터 _on_asset_toggle이 즉시
        # 무장 해제까지 하지만, 그 경로가 실패했거나 구 설정으로 살아 있을 수 있다.
        # armed를 안 보고 숨기면
        # **실주문이 나가는 자산이 화면에서 사라진다** — 미관을 안전과 맞바꾸는 것이다.
        _armed_now = any(k[0] == asset for k in getattr(self, "_sig_accts", {})) or \
                     any(k[0] == asset for k in getattr(self, "_auto_accts", {}))
        if not self._acfg.get(asset, {}).get("include", True) and not _armed_now:
            return (("— 사용 안 함 (언제든 추가할 수 있습니다)" if self.lang == "ko"
                     else "— not in use (add it any time)"), "#6b7280")
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
                    + ", ".join(missing), "#9ca3af")
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
        # 1R 상시 표시(대표 2026-08-11 정보 승격 - 조회 버튼 폐지의 대체): 고정 1R 합.
        # 프롭 계좌만 "단계값"(회원이 단계별로 입력한 상수)을 쓴다. 자본% 계좌는 2026-08-13부터
        # 회원이 1R 칸에 적은 금액을 그대로 쓰므로 합계에 포함한다 - "자동"이라 적으면 거짓말이 된다.
        _auto_sz = any((ac.get("prop") or {}).get("on") for ac in active)
        _r_sum = sum(_as_float(ac.get("one_r"), 0.0) for ac in active
                     if not (ac.get("prop") or {}).get("on"))
        _r_txt = (", 1R " + ("자동" if self.lang == "ko" else "auto")) if _auto_sz else (
            f", 1R ${_r_sum:g}" if _r_sum > 0 else "")
        base = ("+".join(_broker_label(_b) for _b in bks) + ", "
                + (f"{_n}계좌" if self.lang == "ko" else f"{_n} acct") + _r_txt)
        _untested = [_b for _b in bks if not self._conn_by_broker.get(_b)]
        if _untested:
            return base + (", 연결 테스트 필요" if self.lang == "ko" else ", test connection"), "#9ca3af"
        if armed:
            base += ", " + (("가동 중(실거래)" if live else "가동 중(주문 보류: 서버 잠금)") if self.lang == "ko"
                             else ("RUNNING live" if live else "RUNNING (orders held: server lock)"))
            return base, ("#21c55e" if live else "#eab308")
        return base + (", 준비됨" if self.lang == "ko" else ", ready"), "#9ca3af"

    def _pct_btn_text(self, pc):
        # 버튼은 이제 '계산기'다(2026-08-13). 켜져 있어도 발주 1R을 바꾸지 않는다 —
        # 표기가 사이징을 대신해 주는 것처럼 읽히면 안 된다.
        if (pc or {}).get("on"):
            return (f"계산기 {_as_float(pc.get('pct'), 0.4):g}%" if self.lang == "ko"
                    else f"calc {_as_float(pc.get('pct'), 0.4):g}%")
        return "1R 계산기" if self.lang == "ko" else "1R calculator"

    def _pct_dialog(self, idx):
        """1R 계산 도우미(2026-08-13 전면 개편). 자본금과 비율을 **회원이 직접 입력**하면
        1R을 산술로 보여주고, [1R로 적용]을 누르면 그 값이 계좌 행의 1R 칸에 채워진다.
        잔고 조회 없음·자동 반영 없음 — 결정 주체는 언제나 회원(⚖️ 사이징 개인화 제거의 일부).
        구 '자본 비례 모드'(켬/끔 + 발주 시 잔고 조회)는 폐지됐고 pct.on은 항상 False로 저장."""
        ko = self.lang == "ko"
        try:
            acct = self._accts_of(self._asset)[idx]
        except Exception:
            return
        if (acct.get("prop") or {}).get("on"):
            messagebox.showinfo("EQ", ("프롭 계좌는 단계별로 입력하신 1R을 씁니다. 계산기를 쓰려면 "
                                       "프롭 모드를 끄고 1R을 직접 입력하세요." if ko else
                                       "Prop accounts use the per-stage 1R you entered. To use the "
                                       "calculator, turn prop mode off and enter 1R directly."))
            return
        pc = dict(_PCT_DEFAULTS); pc.update(acct.get("pct") or {})
        win = tk.Toplevel(self.root)
        win.title(("1R 계산 도우미 — " + (acct.get("label") or "")) if ko
                  else ("1R helper — " + (acct.get("label") or "")))
        win.resizable(False, False); win.grab_set()
        frm = ttk.Frame(win, padding=14); frm.pack(fill="both", expand=True)
        ttk.Label(frm, wraplength=380, justify="left",
                  text=("계좌 자본금과 비율을 넣으면 1R 금액을 계산해 드립니다.\n"
                        "마음에 들면 [1R로 적용]을 눌러 주세요 — 그 값이 발주에 쓰입니다."
                        if ko else
                        "Enter your account capital and a percent to compute a 1R amount.\n"
                        "If it looks right, press [Use as 1R] — that value is what orders use.")
                  ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(frm, text=("계좌 자본금 $" if ko else "Account capital $")).grid(row=1, column=0, sticky="w")
        ce = ttk.Entry(frm, width=12); ce.grid(row=1, column=1, sticky="w", padx=(6, 0))
        _bal_v = tk.StringVar(value=("잔고 조회 중…" if ko else "reading balance…"))
        ttk.Label(frm, textvariable=_bal_v, foreground="#888").grid(row=1, column=2, sticky="w", padx=(8, 0))
        ttk.Label(frm, text=("비율 %" if ko else "Percent %")).grid(row=2, column=0, sticky="w", pady=(6, 0))
        pe = ttk.Entry(frm, width=8); pe.insert(0, f"{_as_float(pc.get('pct'), 0.4):g}")
        pe.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        res_v = tk.StringVar(value=("→ 자본금을 입력하세요" if ko else "→ enter capital"))
        ttk.Label(frm, textvariable=res_v, font=("", 13, "bold")).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 2))
        ttk.Label(frm, foreground="#888", wraplength=380, justify="left",
                  text=("참고: 자본의 0.4%를 1R로 두면 13년 최악 낙폭(약 53R)이 자본의 약 21%에 "
                        "해당합니다. 단순 산술이며 권유가 아닙니다 — 얼마를 걸지는 본인이 "
                        "결정하십시오. 앱은 잔고를 조회해 금액을 정하지 않습니다." if ko else
                        "Reference: at 0.4% of capital per 1R, the 13-year worst drawdown (~53R) "
                        "equals about 21% of capital. Plain arithmetic, not a recommendation — "
                        "you decide the amount. The app never reads your balance to set it.")
                  ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 10))

        def _calc(*_):
            try:
                cap = float((ce.get() or "").replace(",", ""))
                pct = float(pe.get())
                if cap <= 0 or pct <= 0 or pct > 100:
                    raise ValueError
                r = cap * pct / 100.0
                res_v.set((f"1R = ${r:,.0f}  (${cap:,.0f} × {pct:g}%)") if ko
                          else (f"1R = ${r:,.0f}  (${cap:,.0f} × {pct:g}%)"))
                return r
            except (TypeError, ValueError):
                res_v.set("→ 자본금, 비율을 확인하세요" if ko else "→ check capital / percent")
                return None
        ce.bind("<KeyRelease>", _calc); pe.bind("<KeyRelease>", _calc)

        # ── 자본금 프리필(대표 2026-08-13 "자본금쯤은 읽어와서 자동으로 채워줌 안 되나"):
        # 회원이 계산기를 **연 순간에만** 잔고를 조회해 자본금 칸을 미리 채운다. 값은 수정
        # 가능하고, [1R로 적용]을 눌러야만 발주에 닿는다 - 제안값+회원 확정 구조라 발주 시점
        # 자동 산출(폐지됨)과 법적으로 다르다(질문지 A0-c의 '제안값 표시' 방식). ──
        def _prefill():
            try:
                bk = self._acct_broker(self._asset, acct)
                cr = self._creds_of(self._asset, bk)
                f1 = (cr.get("f1") or "").strip()
                if not f1:
                    raise RuntimeError("no creds")
                f2 = _kc_load(f1) or ""
                f3 = cr.get("f3", "")
                is_fut = bool(_BROKER_SPEC.get(bk, {}).get("acct"))
                bal, err = self._fetch_balance_diag(bk, f1, f2, f3,
                                                    (acct.get("id") or "").strip(), is_fut)
                if bal is None:
                    raise RuntimeError(err or "no balance")
                def _fill(b=float(bal)):
                    if not win.winfo_exists():
                        return
                    _bal_v.set((f"현재 잔고 ${b:,.0f} - 수정 가능" if ko
                                else f"balance ${b:,.0f} - editable"))
                    if not ce.get().strip():
                        ce.insert(0, f"{b:,.0f}")
                        _calc()
                self.root.after(0, _fill)
            except Exception:
                self.root.after(0, lambda: (_bal_v.set("잔고 조회 실패 - 직접 입력" if ko
                                                       else "balance unavailable - type it")
                                            if win.winfo_exists() else None))
        import threading as _th2
        _th2.Thread(target=_prefill, daemon=True).start()

        def _apply():
            r = _calc()
            if r is None:
                return
            # 회원이 버튼으로 확정한 값만 1R 칸에 채운다 - 자동 반영 없음.
            acct["one_r"] = round(r, 2)
            acct["pct"] = {"on": False, "pct": _as_float(pe.get(), 0.4),
                           "floor": _as_float(pc.get("floor"), 200.0)}   # 비율만 기억(모드 아님)
            try:
                w = self._acct_widgets.get(idx) or {}
                if w.get("one_r"):
                    w["one_r"].config(state="normal")
                    w["one_r"].delete(0, "end"); w["one_r"].insert(0, f"{acct['one_r']:g}")
            except Exception:
                pass
            self._save_acct_widgets(); win.destroy()

        ttk.Button(frm, text=("1R로 적용" if ko else "Use as 1R"), command=_apply).grid(row=5, column=1, sticky="e")
        ttk.Button(frm, text=("닫기" if ko else "Close"), command=win.destroy).grid(row=5, column=2, padx=(6, 0))

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
        _saved = acct.get("prop") or {}
        pr = dict(_PROP_DEFAULTS); pr.update(_saved)
        # 프리셋은 몰래 스왑하지 않는다(2026-08-13, 대표 "시뮬 결과 액수 사용 버튼 어때 -
        # 그건 자기가 누르는 거니깐"). 아래 [시뮬 기준값 채우기] 버튼이 유일한 적용 경로.
        try:
            _bk = (acct.get("broker") or self._acfg[self._asset]["broker"] or "").lower()
        except Exception:
            _bk = ""
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
        ttk.Radiobutton(frm, text=("라이브 초기" if ko else "Live (early)"),
                        variable=ty_v, value="live").grid(row=1, column=3, sticky="w")
        ents = {}
        _rows = [("r_test", "챌린지 1R $" if ko else "Challenge 1R $"),
                 ("r_steady", "펀디드 1R $" if ko else "Funded 1R $"),
                 ("r_live", "라이브 초기 1R $" if ko else "Live-early 1R $"),
                 ("payouts", "출금 횟수 (0~5)" if ko else "Payouts so far (0~5)"),
                 ("pass_tp", "통과 목표 잔고 $ (0=끔)" if ko else "Pass target balance $ (0=off)")]
        # r_buffer·buffer 필드는 Fast-Payout 채택으로 미사용 — 저장값은 유지(마이그레이션 호환)
        for i, (k, lab) in enumerate(_rows, start=2):
            ttk.Label(frm, text=lab).grid(row=i, column=0, sticky="w", pady=1)
            e = ttk.Entry(frm, width=10)
            # 새 계좌(저장 이력 없음)는 1R 필드 0으로 비워 시작(대표 2026-08-18) - 앱이
            # 임의 기본값을 앉히지 않는다. 값 적용 경로는 직접 입력 또는 [시뮬 기준값 채우기]뿐.
            _v0 = (0.0 if (not _saved and k in ("r_test", "r_steady", "r_live"))
                   else _as_float(pr.get(k), _PROP_DEFAULTS[k]))
            e.insert(0, f"{_v0:g}")
            e.grid(row=i, column=1, sticky="w", pady=1)
            ents[k] = e
        ttk.Label(frm, foreground="#888", wraplength=380, justify="left",
                  text=(("출금 문턱에 닿으면 앱이 알려주고, 출금 횟수는 팝업에서 '예'를 누르면 "
                         "+1 됩니다(여기서 수동 조정도 가능).\n운용 방식 전체 설명: "
                         "홈페이지 → 자본 운용 → 프롭 운용 원칙") if ko else
                        ("The app notifies you at payout thresholds; the count increments when "
                         "you press Yes in the popup (adjustable here too).\nFull playbook: "
                         "website → Capital → Prop playbook."))
                  ).grid(row=8, column=0, columnspan=4, sticky="w", pady=(8, 2))
        ttk.Label(frm, foreground="#888", wraplength=380, justify="left",
                  text=(("통과 목표 잔고: 테스트기 + Topstep(ProjectX), Lucid(NT8) 계좌에서 "
                         "작동합니다(NT8은 새 브리지 애드온 필요). 프롭 화면에 보이는 "
                         "통과 기준 잔고를 그대로 넣으십시오. (현재 잔고 + 미실현 이익)이 그 값"
                         "(+계약수 비례 여유, 최소 $20)에 닿는 순간 이 계좌의 포지션을 시장가로 "
                         "정리합니다. 남은 거리는 앱이 매번 계산하니 잔고가 늘어도 고칠 필요가 "
                         "없습니다. 금액은 본인이 정하고, 규정 확인도 본인 몫입니다. 0=꺼짐(기본).")
                        if ko else
                        ("Pass target balance: test accounts on Topstep (ProjectX) and "
                         "Lucid (NT8; new bridge add-on required). Enter the balance your firm "
                         "requires to clear the evaluation. When (current balance + open profit) "
                         "reaches it (plus size-scaled headroom, min $20), the app market-closes this "
                         "account's positions. The remaining distance is recomputed each time, so "
                         "you never need to update it as the balance grows. You set the number and "
                         "you check your firm's rules. 0 disables it (default)."))
                  ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(0, 8))

        # ── [시뮬 기준값 채우기] — 홈피 시뮬레이션이 쓰는 브로커별 정본 상수를 회원이
        #    버튼으로 불러온다(전 회원 동일 공개 상수 + 회원 클릭 = 개인화 아님). ──
        _ps = _prop_preset(_bk)
        _ps_hidden = {}                       # 다이얼로그에 없는 필드(buffer 등)는 저장 시 반영

        def _fill_preset():
            if not _ps:
                return
            for k, e in ents.items():
                if k in _ps:
                    e.delete(0, "end"); e.insert(0, f"{_ps[k]:g}")
            for k in ("r_buffer", "buffer"):
                if k in _ps:
                    _ps_hidden[k] = _ps[k]
        if _ps:
            _bn = ("Topstep" if _bk == "projectx" else "Lucid" if _bk == "nt8" else _bk)
            ttk.Button(frm, text=(f"시뮬 기준값 채우기 ({_bn})" if ko
                                  else f"Fill simulation defaults ({_bn})"),
                       command=_fill_preset).grid(row=10, column=0, columnspan=4,
                                                  sticky="w", pady=(0, 2))
            ttk.Label(frm, foreground="#888", wraplength=380, justify="left",
                      text=("홈페이지 시뮬레이션과 같은 값입니다. 눌러도 저장 전엔 반영되지 않습니다."
                            if ko else
                            "Same values as the website simulation. Nothing applies until you save.")
                      ).grid(row=11, column=0, columnspan=4, sticky="w", pady=(0, 8))

        def _ok():
            newp = {"on": bool(on_v.get()), "type": ty_v.get()}
            for k in ents:
                newp[k] = _as_float(ents[k].get(), _PROP_DEFAULTS[k])
            newp["payouts"] = max(0, min(5, int(_as_float(ents["payouts"].get(), 0))))
            newp["pass_tp"] = max(0.0, _as_float(ents["pass_tp"].get(), 0.0))
            # 다이얼로그에서 뺀 레거시 필드(r_buffer·buffer)는 기존 저장값 유지 — 여기서
            # 참조하다 KeyError로 저장이 조용히 죽던 사고 수리(대표 2026-07-28 "저장 안 됨").
            for k in ("r_buffer", "buffer"):
                newp[k] = _as_float(_ps_hidden.get(k, pr.get(k)), _PROP_DEFAULTS[k])
            if newp["on"] and any(newp[k] <= 0 for k in ("r_test", "r_steady")):
                messagebox.showwarning("EQ", "1R 값은 0보다 커야 합니다." if ko
                                       else "1R values must be > 0.")
                return
            acct["prop"] = newp
            try:      # pass_tp 변경 시 이번 가동의 발동·안내 래치 리셋 → 감시 재개(2026-08-18 감사 ⑥)
                _aidl = str(acct.get("id") or "").lower()
                for _st in ("_passtp_done", "_passtp_note", "_passtp_failwarn"):
                    _d = getattr(self, _st, None)
                    if isinstance(_d, dict):
                        for _k in [k for k in _d if isinstance(k, tuple) and len(k) > 1 and k[1] == _aidl]:
                            _d.pop(_k, None)
                    elif isinstance(_d, set):
                        for _k in [k for k in _d if isinstance(k, tuple) and len(k) > 1 and k[1] == _aidl]:
                            _d.discard(_k)
            except Exception:
                pass
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

        ttk.Button(frm, text=("저장" if ko else "Save"), command=_ok).grid(row=12, column=1, pady=(4, 0))
        ttk.Button(frm, text=("취소" if ko else "Cancel"), command=win.destroy).grid(row=12, column=2, pady=(4, 0))

    def _sync_pick_hint(self):
        """자산을 하나도 안 고른 상태에서만 선택 안내를 보인다."""
        _h = getattr(self, "_pick_hint", None)
        if _h is None:
            return
        try:
            if any(self._acfg.get(a, {}).get("include", True) for a in _ASSETS):
                _h.pack_forget()
            elif not _h.winfo_ismapped():
                _h.pack(anchor="w", pady=(2, 2))
        except Exception:
            pass

    def _refresh_live_panel(self):
        self._sync_pick_hint()
        for asset, (lbl, dot) in getattr(self, "_live_rows", {}).items():
            try:
                txt, col = self._asset_row_state(asset)
                lbl.config(text=txt)
                dot.config(foreground=col)
            except Exception:
                pass

    def _acct_edit_row(self, parent, idx, acct, spec, deletable=True, show_on=True):
        """자산 탭 계좌 한 줄(편집): [on] 라벨, 1R$, (계좌ID), [삭제].
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
            bk_cb.bind("<<ComboboxSelected>>",
                       lambda e: (self._save_acct_widgets(), self._build()))
            # 계좌까지 고른 행은 브로커를 잠근다(대표 2026-09-15 "브로커를 실수로 바꾸면 에러 안 떠.
            # 나도 모르게 연결 안 되어 있어"): 계좌 ID는 그 브로커의 것이라 브로커만 바뀌면 행이
            # 조용히 죽은 계좌가 된다. 브로커를 바꾸려면 행을 삭제하고 새로 추가한다.
            if (acct.get("id") or "").strip():
                bk_cb.config(state="disabled")
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
        # 수동 모드 폐지(대표 2026-08-11 "수동 기능 없애" - 그건 Operator의 사용법이지
        # Autopilot 행의 스위치가 아니다). 로더도 manual=False 강제.
        # 1R 입력칸은 프롭 모드에서만 잠근다(프롭은 단계별 상수를 쓰므로). 자본% 모드는
        # 2026-08-13 잔고 연동 폐지로 **회원이 확정한 이 값**이 곧 발주 1R이 됐다 —
        # 잠가두면 회원이 못 고치는 낡은 상수로 발주된다(그 수리에서 딸려온 결함).
        if pr.get("on"):
            r_e.config(state="disabled")
        # 계좌 선택 콤보(대표 2026-08-11 "행에서 브로커 고르면 가용 목록"): 값 목록 = 이 행
        # 브로커의 가용 계좌. 선택하면 전체 ID를 저장하고 표시는 끝자리만(대표 "끝숫자만").
        _rbk = self._acct_broker(self._asset, acct)
        if _BROKER_SPEC.get(_rbk, {}).get("acct"):
            id_cb = ttk.Combobox(row, values=list(self._avail_of(self._asset, _rbk)), width=13)
            id_cb.set(f"…{aid[-6:]}" if aid else ("계좌 선택" if self.lang == "ko" else "pick"))
            id_cb.pack(side="left", padx=(4, 0))
            # 계좌를 고르는 순간 브로커 콤보를 잠근다(재빌드 없이도) - 대표 2026-09-15.
            def _pick(e=None, i=idx, _bk=bk_cb):
                self._save_acct_widgets()
                try:
                    if _bk is not None and (self._accts_of(self._asset)[i].get("id") or "").strip():
                        _bk.config(state="disabled")
                except Exception:
                    pass
            id_cb.bind("<<ComboboxSelected>>", _pick)
            id_cb.bind("<FocusOut>", _pick)
            self._acct_widgets[idx]["acct_id"] = id_cb
        if deletable:
            ttk.Button(row, text=("삭제" if self.lang == "ko" else "Remove"), width=6,
                       command=lambda i=idx: self._del_acct(self._asset, i)).pack(side="right")

    def _save_acct_widgets(self):
        """자산 탭 계좌 위젯값(on, 라벨, 1R) → 이 자산 accounts 반영 + 영속 + 라이브 패널 새로고침."""
        self._collect_acct_widgets()
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True)
        self._refresh_live_panel()

    def _on_asset_toggle(self):
        """실행 자산 체크 변경 → include 반영 + **가동 중이면 무장/해제까지**.

        ⚠️2026-08-28 실사고(대표 "금 설정했어. 왜 금도 1/2??"): 종전에는 include 플래그만
        저장했다. 무장(_master_arm → _sig_accts)은 [라이브 시작]을 누르는 그 순간의
        체크 목록으로만 이뤄지므로, 가동 중에 자산을 체크하면 화면은 켜진 것처럼 보이는데
        서버로 가는 arm 목록에는 없었다 - **그 자산 신호가 와도 주문이 안 나갔다**.
        표시 버그가 아니라 실행 누락이었다. 체크가 곧 무장이 되게 한다."""
        # ⚠️세션 기준이다(2026-08-28 리뷰 P0). 무장 dict가 비었는지로 판정하면,
        # 마지막 무장 자산을 껐다가 다시 켤 때 _running=False가 되어 영영 재무장되지
        # 않는다 - "체크가 곧 무장"이라는 이번 수리의 약속이 바로 그 순간 깨진다.
        _running = bool(getattr(self, "_live_session", False)
                        or getattr(self, "_sig_accts", None)
                        or getattr(self, "_auto_accts", None))
        _was = {a for a in _ASSETS if self._acfg.get(a, {}).get("include", True)}
        for a, var in getattr(self, "_live_include", {}).items():
            try:
                self._acfg[a]["include"] = bool(var.get())
            except Exception:
                pass
        _now = {a for a in _ASSETS if self._acfg.get(a, {}).get("include", True)}
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True)
        if _running:
            for a in sorted(_was - _now):
                if not self._live_disarm_asset(a):      # 회원이 취소 - 체크를 되돌린다
                    self._acfg[a]["include"] = True
                    try:
                        self._live_include[a].set(1)
                    except Exception:
                        pass
                    self._save_cfg()
            _add = sorted(_now - _was)
            if _add:
                self._live_arm_assets(_add)
        self._refresh_live_panel()

    def _live_disarm_asset(self, asset) -> bool:
        """가동 중 자산 체크 해제 → 그 자산 무장 즉시 해제(I/O 없음, 메인 스레드 안전).

        ⚠️해제는 **세션 마감 자동 청산까지** 없앤다(_auto_accts에서 그 자산 잡을 지운다).
        열린 포지션이 있는데 조용히 그러면, 회원은 청산이 예약된 줄 알고 자는 사이
        포지션이 밤을 넘긴다 - 2026-08-28 리뷰 P0. 그래서 무장 중인 자산은 반드시 묻는다.
        반환: 실제로 해제했으면 True, 회원이 취소했으면 False(호출부가 체크를 되돌린다)."""
        _keys = [k for d in (getattr(self, "_sig_accts", None) or {},
                             getattr(self, "_auto_accts", None) or {})
                 for k in d if k[0] == asset]
        _auto = [k for k in (getattr(self, "_auto_accts", None) or {}) if k[0] == asset]
        if _keys:
            _msg = (f"{asset} 자동 실행을 해제합니다.\n\n"
                    "열린 포지션이 있다면 **세션 마감 자동 청산도 함께 사라집니다** - "
                    "그 포지션은 회원이 직접 정리해야 합니다.\n\n계속할까요?"
                    if self.lang == "ko" else
                    f"Disarming {asset}.\n\nIf a position is open, its **session-close "
                    "auto-flatten goes away too** - you would have to close it yourself."
                    "\n\nContinue?") if _auto else (
                    f"{asset} 신호 대기를 해제합니다. 계속할까요?" if self.lang == "ko"
                    else f"Stop watching signals for {asset}. Continue?")
            try:
                if not messagebox.askyesno(self.t("sec_live"), _msg):
                    return False
            except Exception:
                pass
        # 진행 중인 무장 요청도 함께 무효화(리뷰 P0): 연결 테스트가 도는 중에 체크를
        # 되돌리면 pop할 키가 없어 여기는 무음으로 지나가고 done()이 무장해버렸다.
        self._arm_seq = getattr(self, "_arm_seq", 0) + 1
        _n = 0
        for d in (getattr(self, "_sig_accts", None) or {}, getattr(self, "_auto_accts", None) or {}):
            for k in [k for k in list(d) if k[0] == asset]:
                d.pop(k, None); _n += 1
        if _n:
            self.log(f"⏹ {asset} 무장 해제 — 실행 자산 체크를 껐습니다 ({_n}개 항목)"
                     if self.lang == "ko" else
                     f"⏹ {asset} disarmed - execution checkbox turned off ({_n} item(s))")
        try:
            self._set_sig_ind(bool(self._sig_accts), sorted({k[0] for k in self._sig_accts}))
            self._set_auto_ind(bool(self._auto_accts), sorted({k[0] for k in self._auto_accts}))
        except Exception:
            pass
        try:
            self._alive_ping(armed=bool(self._sig_accts or self._auto_accts), force=True)
        except Exception:
            pass
        return True

    def _live_arm_assets(self, assets):
        """가동 중 자산 체크 → 그 자산 무장. 라이브 시작과 **같은 검사**를 거친다
        (프리플라이트 → 연결 테스트 → _master_arm). 실패하면 무장하지 않고 이유를 말한다 -
        조용히 안 켜지는 것이 이 사고의 본질이었으므로 여기서는 반드시 이유를 남긴다."""
        _ctx = getattr(self, "_live_ctx", None) or {}
        live = bool(_ctx.get("live"))
        perm_auto = bool(_ctx.get("perm_auto"))
        perm_use = bool(_ctx.get("perm_use"))
        self._watch_only = bool(perm_use and not perm_auto)
        # 프리플라이트(라이브 시작과 동일 기준): 켜진 계좌·키·브로커 허용·계좌ID
        bad = []
        for a in assets:
            _act = self._active_accts(a)
            if not _act:
                bad.append(f"{a}: " + ("켜진 계좌 없음" if self.lang == "ko" else "no account on"))
                continue
            for ac in _act:
                bk = self._acct_broker(a, ac)
                _nm = ac.get("label") or _broker_label(bk)
                if not (self._creds_of(a, bk).get("f1") or "").strip():
                    bad.append(f"{a} {_nm} ({_broker_label(bk)}): "
                               + ("키 미설정" if self.lang == "ko" else "key missing"))
                elif not self._broker_allowed(bk):
                    bad.append(f"{a} {_nm} ({_broker_label(bk)}): "
                               + ("브로커 지원 꺼짐(서버)" if self.lang == "ko"
                                  else "broker disabled (server)"))
                elif _BROKER_SPEC.get(bk, {}).get("acct") and not (ac.get("id") or "").strip():
                    bad.append(f"{a} {_nm}: " + ("계좌ID" if self.lang == "ko" else "account id"))
        if bad:
            for _b in bad:
                self.log(f"⛔ {_b}")
            messagebox.showwarning(
                self.t("sec_live"),
                (("체크한 자산을 무장하지 못했습니다 - 아래를 고치고 다시 체크하세요.\n\n"
                  if self.lang == "ko" else
                  "Could not arm the checked asset(s) - fix these and check again.\n\n")
                 + "\n".join(bad)))
            for a in assets:                      # 체크를 되돌린다(켜진 척 금지)
                self._acfg[a]["include"] = False
                try:
                    self._live_include[a].set(0)
                except Exception:
                    pass
            self._save_cfg()
            return

        _seq = getattr(self, "_arm_seq", 0)      # 이 요청의 세대(아래 done에서 대조)

        def w():
            fails, tested = [], {}
            for a in assets:
                for ac in self._active_accts(a):
                    bk = self._acct_broker(a, ac)
                    _tk = (bk, (self._creds_of(a, bk).get("f1") or "").strip())
                    if _tk in tested:
                        err = tested[_tk]
                    else:
                        self.log(f"── {a}, {_broker_label(bk)} 연결 테스트 ──")
                        err = self._conn_check(a, bk)
                        tested[_tk] = err
                        self.log(f"✅ {a}, {_broker_label(bk)} 연결 OK" if not err
                                 else f"❌ {a}, {_broker_label(bk)}: {err}")
                    if err:
                        fails.append(f"{a} ({_broker_label(bk)}): {err}")

            def done():
                # ⚠️최우선 재검증(2026-08-28 리뷰 P0). 연결 테스트가 도는 수 초 동안
                # 화면은 안 잠긴다 - [전체 정지]도 체크 해제도 다 눌린다. 그 결과를
                # 안 보고 무장하면 **정지가 취소되고** 신호 루프가 하나 더 떠서 같은
                # 신호에 이중 진입까지 간다. 세대가 낡았거나, 세션이 닫혔거나, 그 사이
                # 체크가 꺼졌으면 무장하지 않고 이유를 남긴다.
                if (_seq != getattr(self, "_arm_seq", 0)
                        or not getattr(self, "_live_session", False)
                        or not all(self._acfg.get(a, {}).get("include") for a in assets)):
                    self.log("⏹ 무장 취소 — 연결 테스트 중에 정지 또는 체크 해제가 있었습니다."
                             if self.lang == "ko" else
                             "⏹ Arming cancelled - stopped or unchecked while the test ran.")
                    self._refresh_live_panel(); return
                if fails:
                    self.log("⛔ 무장 중단 — " + ", ".join(fails))
                    messagebox.showerror(self.t("sec_live"),
                                         (("연결 실패 - 무장하지 않았습니다:\n"
                                           if self.lang == "ko" else
                                           "Connection failed - not armed:\n")
                                          + "\n".join(fails)))
                    for a in assets:
                        self._acfg[a]["include"] = False
                        try:
                            self._live_include[a].set(0)
                        except Exception:
                            pass
                    self._save_cfg(); self._refresh_live_panel(); return
                _n = 0
                for a in assets:
                    for idx, ac in enumerate(self._accts_of(a)):
                        if ac.get("on"):
                            self._master_arm(a, idx, live, arm_sig=(perm_auto or perm_use))
                            _n += 1
                self.log(f"🚀 {', '.join(assets)} 무장 — {_n}개 계좌 (LIVE)")
                self._refresh_live_panel()
                self._apply_gating()
                try:
                    self._alive_ping(armed=True, force=True)   # 서버 칩 즉시 반영
                except Exception:
                    pass
            self.root.after(0, done)
        self._run(w)

    def _copy_f1(self):
        """f1 복사 - 기기 이전용(대표 2026-08-10). f1은 브로커마다 다르다(ProjectX=이메일,
        크립토=API Key, IBKR=호스트) - 'API Key'로 뭉뚱그리면 ProjectX에서 거짓 안내(D6 실사).
        비밀 취급 브로커는 잠금 해제 후에만. write-only 원칙의 예외지만 PIN 게이트 뒤라
        소유자 본인 동작이다."""
        _sp = _BROKER_SPEC.get(self._broker_name, {})
        if _sp.get("f1_secret") and not self._f1_unlocked:
            messagebox.showinfo("PIN", self.t("locked_msg")); return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.user.get())
            self.log("📋 " + ("API Key를 클립보드에 복사했습니다." if self.lang == "ko"
                              else "API Key copied to clipboard."))
        except Exception:
            pass

    def _copy_key(self):
        """비밀키(f2) 복사 - 잠금 해제 후에만."""
        if not self._unlocked:
            messagebox.showinfo("PIN", self.t("locked_msg")); return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.key.get())
            self.log("📋 " + ("비밀키를 클립보드에 복사했습니다 - 붙여넣은 뒤 클립보드를 "
                              "비우는 것을 잊지 마세요." if self.lang == "ko" else
                              "Secret copied - clear your clipboard after pasting."))
        except Exception:
            pass

    def _toggle_cfg(self):
        """자산별 브로커 설정 접기/펼치기(대표 2026-07-13)."""
        self._cfg_open = not self._cfg_open
        if self._cfg_open:
            self._cfg_body.pack(fill="x", after=self._cfg_hdr)
        else:
            self._cfg_body.pack_forget()
        self._cfg_hdr.config(text=("▾ " if self._cfg_open else "▸ ")
                             + ("자산별 브로커 설정 (NQ, GC, BTC)" if self.lang == "ko"
                                else "Per-asset broker setup (NQ, GC, BTC)"))
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True,
                       cfg_open=self._cfg_open)

    def _toggle_acct(self):
        """자산별 계좌 설정 접기/펼치기(대표 2026-08-11 독립 접이식)."""
        self._acct_open = not self._acct_open
        if self._acct_open:
            self._acct_body.pack(fill="x", after=self._acct_hdr)
        else:
            self._acct_body.pack_forget()
        self._acct_hdr.config(text=("▾ " if self._acct_open else "▸ ")
                              + ("자산별 계좌 설정 (NQ, GC, BTC)" if self.lang == "ko"
                                 else "Per-asset account setup (NQ, GC, BTC)"))
        self._save_cfg(dry_run=bool(self.live_dry.get()) if hasattr(self, "live_dry") else True,
                       acct_open=self._acct_open)

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

    def _schedule_autosave(self):
        """입력 디바운스 자동 저장(대표 2026-08-11 버튼 제로 확정). 1.2초 잠잠하면 발화."""
        try:
            if getattr(self, "_autosave_after", None):
                self.root.after_cancel(self._autosave_after)
            self._autosave_after = self.root.after(1200, self._autosave_fire)
        except Exception:
            pass

    def _autosave_fire(self):
        """자동 저장 + (키 완성 시) 무음 연결 확인. 실패는 로그만 - 팝업으로 방해하지 않는다."""
        self._autosave_after = None
        try:
            self._save_current_asset()               # 영속 + ✓저장됨 라벨
        except Exception:
            return
        asset, bk = self._asset, self._broker_name
        f1 = (self._creds_of(asset, bk).get("f1") or "").strip()
        if not f1:
            return
        # 같은 크레덴셜 재테스트 방지 - 지문이 바뀌었을 때만 무음 확인
        fp = (asset, bk, f1, len(self.key.get()) if hasattr(self, "key") else 0)
        if getattr(self, "_autotest_fp", None) == fp:
            return
        self._autotest_fp = fp

        def w():
            err = self._conn_check(asset, bk)

            def done():
                if err:
                    self.log(f"❌ {asset}, {_broker_label(bk)}: {err}")
                else:
                    self._connected = True
                    self._conn_by_broker[bk] = True
                    if not hasattr(self, "_test_ok"):
                        self._test_ok = set()
                    self._test_ok.add(asset)
                    self.log(f"✅ {asset}, {_broker_label(bk)} 연결 확인 - 자동 저장, 검증 완료")
                    try:
                        if bk == "projectx":         # 연결되면 가용 계좌 자동 채움(②로 직행)
                            self._autoload_topstep_scope(asset)
                        elif bk == "nt8":
                            self._autoload_nt8_avail(asset)
                    except Exception:
                        pass
                self._refresh_next_action()
                self._apply_gating()
                self._refresh_live_panel()
            self.root.after(0, done)
        threading.Thread(target=w, daemon=True).start()

    def _save_creds_and_test(self):
        """[계좌 정보 저장 + 연결 테스트 + 1R 확인] (대표 2026-08-09 '한 번에') — 현재 탭
        키(f1/f2/f3)·계좌 설정 즉시 영속 → 현재 브로커 연결 테스트 → 통과 시 이 자산 전
        계좌 잔고, 1R 미리보기까지 이어서. 실패는 에러 팝업, 성공 확인 = 잔고, 1R 팝업."""
        self._save_current_asset()
        asset, bk = self._asset, self._broker_name
        self.log(f"\n💾 {asset}, {_broker_label(bk)} " + ("설정 저장 완료 — 연결 테스트 중…"
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
                    self.log(f"❌ {asset}, {_broker_label(bk)}: {err}")
                    messagebox.showerror(self.t("btn_conn"), f"{_broker_label(bk)}: {err}")
                else:
                    self._connected = True
                    self.log(f"✅ {asset}, {_broker_label(bk)} 연결 OK — 저장, 검증 완료")
                    try:
                        if not hasattr(self, "_test_ok"):
                            self._test_ok = set()
                        self._test_ok.add(asset)
                        self.root.after(0, self._refresh_next_action)
                    except Exception:
                        pass
                    # 잔고 조회는 **회원이 요청할 때만**(2026-08-13). 저장·연결만 했는데 잔고를
                    # 자동으로 끌어와 보여주면, 묻지도 않은 재산 조회를 앱이 먼저 하는 셈이다.
                    # 잔고·1R 확인은 자산 탭의 [잔고 & 1R] 버튼으로 회원이 직접 누른다.
                    self.root.after(0, lambda a=asset: messagebox.showinfo(
                        "EQ", (f"{a}, 저장, 연결 OK.\n\n계좌 잔고와 1R을 확인하려면 "
                               f"[잔고 & 1R] 버튼을 눌러 주세요." if self.lang == "ko"
                               else f"{a}, saved & connected.\n\nUse the [Balance & 1R] button "
                                    f"to check balance and 1R.")))
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
                self.log(f"\n── {asset}, {_broker_label(bk)} 연결 테스트 ──")
                err = self._conn_check(asset, bk)
                self.log(f"❌ {asset}, {_broker_label(bk)}: {err}" if err
                         else f"✅ {asset}, {_broker_label(bk)} 연결 OK")
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
                # 세션 캐시 교체(2026-08-18): 연결 테스트의 신선 목록이 정본 - 안 그러면
                # 탭 재진입 때 _autoload가 낡은 캐시로 콤보를 도로 덮는다.
                try:
                    getattr(self, "_scope_cache", None) is not None or setattr(self, "_scope_cache", {})
                    self._scope_cache[("projectx", f1)] = names
                except Exception:
                    pass
                self.root.after(0, lambda n=names: self._fill_scope(
                    n, asset=asset, broker="projectx"))
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
            if bk == "nt8":
                # 구버전 브리지 조기 경보(대표 2026-09-04 협의: 자동 설치는 컴파일 실패
                # 지뢰라 기각, 대신 라이브 시작 때 검사 + 팝업 '예'로 그 자리 설치).
                # 브리지가 push하는 bridge_ver와 앱 동봉 .cs의 BridgeVer를 대조한다 -
                # 구버전 브리지는 표기 자체가 없어 빈 값 = 구버전으로 판정된다.
                try:
                    _have = ""
                    try:
                        _have = str(b._snapshot().get("bridge_ver") or "")
                    except Exception:
                        _have = ""
                    _want = self._bundled_bridge_ver()
                    if _want and _have != _want and not getattr(self, "_bridge_ver_warned", False):
                        self._bridge_ver_warned = True     # 앱 세션당 1회
                        self.log(f"   🚨 NT8 브리지 구버전 감지 — 현재 "
                                 f"{_have or '표기 없음'} / 동봉 {_want}")

                        def _ask_install():
                            if messagebox.askyesno(
                                    "EQ Autopilot",
                                    ("NT8 브리지 애드온이 구버전입니다.\n"
                                     "지금 설치할까요? (설치 후 NT8 재시작이 필요합니다)"
                                     if self.lang == "ko" else
                                     "The NT8 bridge add-on is outdated.\n"
                                     "Install now? (NT8 must be restarted afterwards)")):
                                self._nt8_install_bridge()
                        self.root.after(0, _ask_install)
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
                                    "manual": bool(acct.get("manual")),   # 수동 모드=티켓만, 자동진입 안 함
                                    "live": live, "label": acct.get("label", "")}
            if not self._sig_on:
                self._sig_on = True
                threading.Thread(target=self._sig_loop, args=(_feed_url(self._token),),
                                 daemon=True).start()
        self._set_auto_ind(bool(self._auto_accts), sorted({k[0] for k in self._auto_accts}))
        self._set_sig_ind(bool(self._sig_accts), sorted({k[0] for k in self._sig_accts}))

    # ── 월 1회 실행 확인(대표 2026-08-15) ────────────────────────────────
    # 자동 실행은 켜두면 회원이 잊어도 계속 돈다. 그게 이 도구의 장점이자 위험이라
    # 30일마다 "계속할지"를 다시 묻는다. 무응답은 **새 진입만** 막고 자동청산은 계속한다
    # (fail-closed 전체정지 사고 2026-07-14의 교훈 - 안전한 방향으로만 실패시킨다).
    CONSENT_DAYS = 30

    def _consent_left(self):
        """다음 확인까지 남은 일수. 확인 이력이 없으면 0(=지금 물어야 함)."""
        import time as _t
        ts = (self._profile or {}).get("consent_at")
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return 0.0
        return self.CONSENT_DAYS - (_t.time() - ts) / 86400.0

    def _consent_summary(self):
        """지난 30일 요약 — 이 앱이 이 계좌에서 무엇을 했는지. 없으면 빈 문자열."""
        # 로컬 원장(.eqtrades.json)에는 **진입 기록만** 있고 실현 R은 없다(브로커 조회가 별도).
        # 다이얼로그에서 네트워크를 타면 느리고 실패하므로, 즉시 확실한 것만 보여준다.
        import time as _t
        try:
            since_ms = (_t.time() - self.CONSENT_DAYS * 86400) * 1000
            rows = [r for r in _ledger_load() if (r.get("ts_ms") or 0) >= since_ms]
        except Exception:
            rows = []
        per = {}
        for r in rows:
            a = str(r.get("asset") or "?")
            per[a] = per.get(a, 0) + 1
        incl = [a for a in _ASSETS if self._acfg.get(a, {}).get("include", True)]
        brk = ", ".join(f"{a} {c}" for a, c in sorted(per.items())) or ("없음" if self.lang == "ko" else "none")
        if self.lang == "ko":
            # 회원이 읽는 화면이다 - 호칭은 중립으로(대표 2026-08-17 "앱에 대표라는 말 쓰면 어쩌냐").
            return (f"지난 30일 동안 이 앱이 이 계좌에서 낸 진입\n\n"
                    f"    합계      {len(rows)}건\n"
                    f"    자산별    {brk}\n"
                    f"    실행 대상  {', '.join(incl) or '없음'}\n")
        return (f"Entries this app placed in your account over the last 30 days:\n\n"
                f"    total     {len(rows)}\n"
                f"    by market {brk}\n"
                f"    enabled   {', '.join(incl) or 'none'}\n")

    def _consent_ask(self):
        """만료됐으면 묻는다. 계속=True(시각 갱신) / 중단, 닫기=False."""
        import time as _t
        if self._consent_left() > 0:
            return True
        body = (self._consent_summary() +
                ("\n이대로 계속 실행할까요?\n\n"
                 "자동 실행은 켜두면 잊어도 계속 돕니다. 한 달 사이 계좌도 생각도 달라질 수 있어서,\n"
                 "30일마다 여전히 원하시는지 확인합니다.\n\n"
                 "* [아니오]를 누르거나 답하지 않으면 새 진입만 멈춥니다.\n"
                 "  이미 열려 있는 포지션의 자동청산은 계속됩니다."
                 if self.lang == "ko" else
                 "\nKeep running as is?\n\n"
                 "Automated execution keeps going even when you forget it is on. Accounts and\n"
                 "intentions change over a month, so we ask every 30 days whether you still want this.\n\n"
                 "* Choosing No, or not answering, stops new entries only.\n"
                 "  Auto-close on positions already open continues."))
        ok = messagebox.askyesno(("실행 계속 확인" if self.lang == "ko" else "Confirm continued execution"),
                                 body, default="no")
        if ok:
            self._profile = {**(self._profile or {}), "consent_at": _t.time()}
            self._save_cfg()
            self.log("   ✅ 실행 확인 갱신 — 30일 뒤 다시 여쭙습니다."
                     if self.lang == "ko" else "   ✅ Consent renewed - we will ask again in 30 days.")
        return bool(ok)

    def _master_start(self):
        """[라이브 시작] — 실행 체크된 자산을 연결 테스트(자산 브로커, 같은 브로커는 중복 테스트
        방지), 전부 통과 시에만 각 자산의 켜진 계좌를 일괄 무장(하나라도 실패 시 전체 중단).
        계좌마다 자기 1R로. 모의 모드는 폐지(대표 2026-09-07) - 시작은 언제나 실거래이고, 서버가
        라이브를 잠갔으면(force_dry_run) 시작 자체를 거부한다."""
        if (self._gate or {}).get("force_dry_run"):
            messagebox.showwarning(self.t("sec_live"),
                                   "서버가 라이브를 잠갔습니다 — 지금은 시작할 수 없습니다. 공지를 확인하세요."
                                   if self.lang == "ko" else
                                   "Live is locked by the server — starting is not possible right now. "
                                   "Check the announcements."); return
        if not self._consent_ask():      # 월 1회 확인
            self.log("   ⏸ 실행 확인이 없어 라이브 시작을 중단했습니다."
                     if self.lang == "ko" else "   ⏸ Live start cancelled - no confirmation.")
            return
        self.live_dry.set(0)
        if self._sig_accts or self._auto_accts:
            messagebox.showinfo(self.t("sec_live"),
                                "이미 가동 중입니다 — 먼저 '전체 정지' 후 다시 시작하세요."
                                if self.lang == "ko" else
                                "Already armed — press 'Stop all' first."); return
        # 라이브 오발 방지(2026-08-11 UX 감사, 운영 1순위): 실거래 시작은 명시 확인을 거친다.
        if not messagebox.askyesno(
                "라이브 시작" if self.lang == "ko" else "Go Live",
                ("지금부터 실제 계좌에 실주문이 나갑니다.\n"
                 "신호가 오면 자동으로 진입, 손절, 청산합니다.\n\n"
                 "라이브를 시작할까요? (연결 테스트는 자동으로 수행됩니다)"
                 if self.lang == "ko" else
                 "Real orders will be placed on live accounts from now on.\n"
                 "Entries, stops and exits run automatically on each signal.\n\n"
                 "Start live? (connection tests run automatically)")):
            return
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
            # 연결 권한만 있는 등급(오픈 후 Preview)에는 **왜 안 되는지와 무엇이 되는지**를
            # 같이 말한다(2026-08-28 R14 P0). 종전 "멤버십 권한이 없습니다"는 앱을 정식으로
            # 내려받아 브로커까지 붙인 회원에게 막다른 골목이었다 - 그 회원이 받기로 한
            # 혜택(지연 30분 신호 표시)은 라이브 없이도 이미 돌고 있다(_watch_loop).
            _conn_ok = bool(caps.get("connect", True))
            try:
                _need_l = "Operator"
            except Exception:
                _need_l = "Operator"
            messagebox.showinfo(
                self.t("token"),
                ((f"자동 실행은 {_need_l} 등급부터입니다.\n\n"
                  "지금 등급에서도 브로커 연결과 신호 수신은 그대로 돕니다 - 신호는 "
                  "아래 로그에 지연 발행 시각에 맞춰 표시됩니다."
                  if _conn_ok else "멤버십 권한이 없습니다 - 토큰을 확인하세요.")
                 if self.lang == "ko" else
                 (f"Automated execution starts at the {_need_l} tier.\n\n"
                  "Broker connection and signal delivery keep working on your tier - "
                  "signals appear in the log below at their scheduled time."
                  if _conn_ok else "No membership permission - check your token."))); return
        live = True
        # 가동 조건을 기억한다(대표 2026-08-28 "금 설정했어"): 라이브 도중 실행 자산을
        # 체크하면 그때 같은 조건(신호대기 권한)으로 무장해야 한다.
        self._live_ctx = {"live": bool(live), "perm_auto": bool(perm_auto), "perm_use": bool(perm_use)}
        self._live_session = True
        self.b_live_start.config(state="disabled")
        _alabels = ", ".join(incl)
        self.log(f"\n══ 라이브 시작 — 연결 테스트 {_alabels} (LIVE) ══")

        def w():
            fails = []
            nt8_fail = []
            tested = {}                # (브로커,f1)별 연결 테스트 1회(같은 크레덴셜 중복 방지)
            for a in incl:
                # 수동 전용 자산(활성 계좌가 전부 수동 모드)은 발주를 안 하므로 브로커 연결 불필요.
                # 연결 테스트를 건너뛰어 '연결 실패로 전체 중단'되지 않게 한다(대표 2026-07-27).
                _act = [ac for ac in self._accts_of(a) if ac.get("on")]
                if _act and all(ac.get("manual") for ac in _act):
                    self.log(f"── {a}, " + ("수동 모드(신호 티켓만) — 연결 테스트 건너뜀"
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
                        self.log(f"── {a}, {_broker_label(bk)} — "
                                 + ("연결 확인됨(공유)" if not err else f"연결 실패(공유): {err}"))
                    else:
                        self.log(f"── {a}, {_broker_label(bk)} 연결 테스트 ──")
                        err = self._conn_check(a, bk)
                        tested[_tk] = err
                        self.log(f"✅ {a}, {_broker_label(bk)} 연결 OK" if not err
                                 else f"❌ {a}, {_broker_label(bk)}: {err}")
                    if err:
                        fails.append(f"{a} ({_broker_label(bk)}): {err}")
                        if bk == "nt8":
                            nt8_fail.append(a)

            def done():
                self.b_live_start.config(state="normal")
                if fails:
                    self.log("⛔ 전체 중단 — 아무 계좌도 가동하지 않았습니다.")
                    _msg = (("연결 실패 — 전체 중단:\n" if self.lang == "ko"
                             else "Connection failed — aborted:\n") + "\n".join(fails))
                    # Lucid 실패 = 십중팔구 NT8 꺼짐(대표 2026-08-12 "닌자 켜졌나 봐라 워닝")
                    if nt8_fail:
                        _msg += (("\n\n[Lucid] NinjaTrader 8이 켜져 있고 로그인(초록불)돼 "
                                  "있는지 확인하세요 - 같은 PC에서 NT8이 꺼져 있으면 "
                                  "Lucid는 연결되지 않습니다.")
                                 if self.lang == "ko" else
                                 ("\n\n[Lucid] Check that NinjaTrader 8 is running and "
                                  "logged in (green) on this PC - Lucid cannot connect "
                                  "while NT8 is off."))
                    messagebox.showerror(self.t("sec_live"), _msg)
                    self._refresh_live_panel(); return
                # Operator(자동진입 없음)도 신호 확인만은 켠다 - 수량은 만들지도 보이지도 않는다.
                self._watch_only = bool(perm_use and not perm_auto)
                narmed = 0
                for a in incl:
                    for idx, ac in enumerate(self._accts_of(a)):
                        if ac.get("on"):
                            self._master_arm(a, idx, live, arm_sig=(perm_auto or perm_use))
                            narmed += 1
                _what = ("자동 청산" + (" + 신호 자동 진입" if perm_auto else
                                     " + 신호 확인 (수량 비공개, 자동 발주 없음 - Autopilot 등급만 자동 진입)")
                         if self.lang == "ko" else
                         "auto-close" + (" + auto-entry on signal" if perm_auto else
                                        " + signal watch (no quantity shown, no auto orders - "
                                        "auto-entry is Autopilot tier only)"))
                try:
                    self._alive_ping(armed=True, force=True)   # 홈피 즉시 반영(이중 안전)
                except Exception:
                    pass
                self.log(f"🚀 라이브 가동 시작 — {narmed}개 계좌 [{_alabels}], {_what}, LIVE")
                self.log(f"   {self.t('warn_mix')}")
                self._refresh_live_panel()
                self._apply_gating()
            self.root.after(0, done)
        self._run(w)

    def _master_stop(self):
        """[⏹ 전체 정지] — 모든 계좌 무장 해제(루프는 무장 0이면 자연 종료)."""
        n = len(set(list(self._sig_accts) + list(self._auto_accts)))
        # 진행 중인 무장 요청 무효화(2026-08-28 리뷰 P0) - _live_ctx도 비워, 스테일 done()이
        # 정지 전 LIVE 플래그를 재사용해 실주문 상태로 부활하지 못하게 한다.
        self._arm_seq = getattr(self, "_arm_seq", 0) + 1
        self._live_ctx = {}
        self._live_session = False
        self._sig_accts.clear()
        self._auto_accts.clear()
        self._sig_on = False
        self._auto_on = False
        self._alive_ping(armed=False, force=True)   # 정상 종료 신고 - 다운 경보 대상 제외
        self._set_auto_ind(False, [])
        self._set_sig_ind(False, [])
        self.log((f"\n⏹ 전체 정지 — {n}개 계좌 가동 해제. ⚠ 열린 포지션은 그대로 유지됩니다 - "
                  "정리하려면 [모든 포지션 청산]을 누르세요.") if self.lang == "ko"
                 else (f"\n⏹ Stopped all — {n} account(s) disarmed. ⚠ Open positions stay open - "
                       "use [Close all positions] to flatten."))
        try:
            messagebox.showwarning(
                "전체 정지" if self.lang == "ko" else "Stopped",
                ("자동 실행만 멈췄습니다. **열린 포지션과 손절 주문은 그대로 살아 있습니다.**\n"
                 "포지션까지 정리하려면 [모든 포지션 청산]을 누르세요." if self.lang == "ko" else
                 "Automation stopped. **Open positions and stop orders remain live.**\n"
                 "Use [Close all positions] if you want to flatten."))
        except Exception:
            pass
        self._refresh_live_panel()
        self._apply_gating()

    def _auto_leverage(self, b, sym, size, ref_px=None):
        """잔고-맞춤 레버리지 자동 조정 (대표 2026-07-20 — 110007 잔고부족 진입실패 재발 방지).
        진입 직전 가용잔고를 앱이 직접 조회해, 이 수량이 들어가도록 레버리지를 상향한다
        (하향 안 함 · 실위험은 손절=1R 고정이라 위험 증가 아님). 실패해도 진입은 계속 시도.
        브로커가 ensure_leverage를 지원할 때만 동작 — Bybit·Bitget 둘 다 구현돼 있다
        (bitget.py:127, hasattr 게이트 통과; 'Bitget은 추후'는 옛말 - D6 실사 정정)."""
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
                     f"(명목 ${lv['notional']:,.0f}, 가용 ${lv['ab']:,.0f})")
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
        if broker == "bitget":                        # 지정가, 임계도 빗겟 좌표계로 보정
            _basis = _cross_basis_bitget()
            if _basis:
                lp = round(lp + _basis, 2)
        retries = int(pol.get("retries") or 3)
        interval = float(pol.get("retry_interval_s") or 3)
        thr = pol.get("skip_if_adverse_price")
        if not live:
            self.log(f"   DRY-RUN 체결정책: 지정가 {lp:g} ×{retries}회(간격 {interval:g}s) → "
                     f"불리 {thr}+ 스킵 → 시장가 폴백, 손절 {stop}")
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
                                 f"수량 ×{_shrink:.3f} ({size:g}→{_sz2:g}), 리스크 1R 유지, 시장가")
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
                     f"불리 {thr}+ 스킵 → 시장가 폴백, 손절 {stop}")
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
    # ── 평가 통과 익절 감시(대표 2026-08-18) ─────────────────────────────────
    # 회원이 테스트기 계좌에 "통과 목표 잔고 $"를 넣어 두면, (현재 잔고 + 미실현 이익)이
    # 그 값(+비용 비례 버퍼)에 닿는 순간 **그 계좌의 포지션만** 시장가로 정리한다.
    #   · 우리는 금액을 판단하지 않는다 - 회원이 입력한 값을 집행만 한다.
    #   · 브로커 지정가를 안 쓰는 이유: 익절 체결 뒤 남은 보호손절이 반대 포지션을 열 수
    #     있다(고아 주문). 앱이 닫고 플랫 확인 후 잔여 주문까지 취소하는 편이 안전하다.
    #   · 무장(_sig_on) + 라이브 + **Topstep(ProjectX) 전용**. 킬스위치 EQ_PASSTP_OFF=1.
    # 2026-08-18 적대검증(3렌즈) 반영 - 설계 불변식:
    #   ① confirm-after-act: 청산 발주 후 재조회로 플랫을 **확인한 뒤에만** 주문 취소·done·
    #      성공 팝업. 미확인이면 done 없이 60초 뒤 재시도(flatten_all의 STILL OPEN 패턴).
    #   ② projectx만: NT8은 평단 키가 다르고(avg_price) 시세·주문취소 API가 없으며,
    #      Tradovate는 REST 시세가 없다 - 무음 불능으로 회원을 속이느니 지원을 좁힌다.
    #   ③ 버퍼=청산측 실비용 비례(계약수×쿠션, 최소 $20) - 고정 $20은 GC 다계약에서 미달.
    #   ④ single-flight + 발사 직전 스냅샷 재확인(그 사이 청산/신규 진입이면 보류).
    #   ⑤ 발동 시 그 실계좌의 무장 행 전부 해제(이번 가동 한정) - 통과 직후 재진입 방지.
    #   ⑥ pass_tp 저장 변경 시 발동 래치 리셋(감시 재개).
    #   ⑦ ProjectX position.type 실측 전 방어: 1·2 외 값이면 판단 보류(부호 오판 방지).
    def _passtp_tick(self):
        try:
            if (str(os.environ.get("EQ_PASSTP_OFF", "")).strip() != "1"
                    and getattr(self, "_sig_on", False)
                    and not getattr(self, "_passtp_busy", False)):
                self._passtp_busy = True
                try:
                    threading.Thread(target=self._passtp_scan_wrap, daemon=True).start()
                except Exception:
                    self._passtp_busy = False
        except Exception:
            pass
        finally:
            try:
                self.root.after(60 * 1000, self._passtp_tick)
            except Exception:
                pass

    def _passtp_scan_wrap(self):
        try:
            self._passtp_scan()
        except Exception:
            pass
        finally:
            self._passtp_busy = False

    def _passtp_targets(self):
        """감시 대상 [(asset, idx, cfg, 목표$)] - 테스트기 + pass_tp>0 + 라이브 무장 계좌.
        Topstep(ProjectX) 전용·수동 모드 제외. 작동하지 않는 조합(미지원 브로커·수동 모드)에
        값이 있으면 1회 경고 로그 - 무음 불능으로 회원을 속이지 않는다."""
        out = []
        warned = getattr(self, "_passtp_warned", None)
        if warned is None:
            warned = self._passtp_warned = set()
        for (asset, idx), cfg in list(getattr(self, "_sig_accts", {}).items()):
            if not cfg.get("live"):
                continue
            if not _BROKER_SPEC.get(cfg.get("broker"), {}).get("futures"):
                continue                       # 프롭 평가 계좌 = 선물 브로커
            try:
                acct = self._accts_of(asset)[idx]
            except Exception:
                continue
            pr = dict(_PROP_DEFAULTS); pr.update(acct.get("prop") or {})
            tp = _as_float(pr.get("pass_tp"), 0.0)
            if pr.get("type") != "test" or tp <= 0:
                continue
            wk = (cfg.get("broker"), str(cfg.get("acct") or "").strip().lower())
            if cfg.get("manual"):
                if ("manual",) + wk not in warned:
                    warned.add(("manual",) + wk)
                    self.log(f"   ⚠ [{cfg.get('label') or wk[1]}] 통과 익절은 수동 모드 "
                             f"계좌에선 작동하지 않습니다")
                continue
            if cfg.get("broker") not in ("projectx", "nt8"):
                if wk not in warned:
                    warned.add(wk)
                    self.log(f"   ⚠ [{cfg.get('label') or wk[1]}] 통과 익절은 현재 "
                             f"Topstep(ProjectX), Lucid(NT8)만 지원합니다 - "
                             f"{_broker_label(cfg.get('broker'))} 계좌에선 작동하지 않습니다")
                continue
            out.append((asset, idx, cfg, tp))
        return out

    @staticmethod
    def _passtp_pv(contract_id):
        """계약ID → 포인트 가치($). 미인식이면 None(그 포지션은 계산에서 제외)."""
        s = str(contract_id or "").upper()
        parts = s.split(".")
        root = parts[3] if len(parts) >= 5 and parts[0] == "CON" else s
        if root in _PV_BY_ROOT:
            return _PV_BY_ROOT[root]
        for tok, pv in _PV_BY_ROOT.items():    # 형식이 달라도 티커 토큰 폴백
            if tok in s:
                return pv
        return None

    @staticmethod
    def _passtp_buffer(poss):
        """청산측 실비용 비례 버퍼: 계약당 쿠션(시장가 슬리피지+청산측 커미션 근사,
        마이크로 $4 · 미니/풀 $40) 합산, 최소 _TP_BUFFER_USD. 근거=GC 시장가 슬리피지
        실측(projectx.py 주석, 건당 $64/23계약 + 청산 커미션 ~$20 → 23×$4=$92로 덮음)."""
        cush = 0.0
        for p in poss:
            s = str(p.symbol).upper()
            parts = s.split(".")
            root = parts[3] if len(parts) >= 5 and parts[0] == "CON" else s.split(" ")[0]
            per = 4.0 if root.startswith("M") else 40.0
            cush += abs(int(p.net_qty)) * per
        return max(_TP_BUFFER_USD, cush)

    def _passtp_broker(self, cfg):
        """타깃별 브로커 인스턴스 캐시 - 매분 재로그인 방지(토큰 갱신은 어댑터 몫).
        키에 f2(API 키)까지 포함 - 키 교체 시 낡은 인스턴스로 인증 실패가 반복되지 않게."""
        c = getattr(self, "_passtp_bk", None)
        if c is None:
            c = self._passtp_bk = {}
        k = (cfg.get("broker"), cfg.get("f1", ""), cfg.get("f2", ""))
        b = c.get(k)
        if b is None:
            b = c[k] = _build_broker(cfg.get("broker"), cfg.get("f1", ""),
                                     cfg.get("f2", ""), cfg.get("f3", ""), [])
        return b

    @staticmethod
    def _passtp_filter(poss, aid_cfg):
        """설정 계좌(이름/ID)로 포지션 필터 - casefold(어댑터 계좌 매칭과 동일 규칙)."""
        want = str(aid_cfg or "").strip().lower()
        return [p for p in poss
                if not want or want in (str(p.account_name).lower(),
                                        str(p.account_id).lower())]

    def _passtp_close_account(self, b, cfg, aid_cfg, key, tp, total):
        """발동 확정된 계좌의 정리 전체(청산→플랫 확인→주문 취소→done→해제→팝업).
        플랫 미확인이면 **닫힌 레그의 주문만** 걷고(고아 스탑 방지) done 없이 False 반환
        → 다음 스캔(_passtp_fired 래치)이 잔여 레그를 이어서 정리한다."""
        import time as _timemod
        lbl = cfg.get("label") or aid_cfg
        poss = self._passtp_filter(b.list_open_positions(), aid_cfg)
        for p in poss:
            try:
                b.close_contract((p.raw or {}).get("_accountId") or p.account_id, p.symbol)
            except Exception as ce:
                self.log(f"   ❌ {p.symbol} 청산 발주 실패: {ce}")
        flat, left = False, poss
        _is_nt8 = cfg.get("broker") == "nt8"
        for _ in range(8):
            _timemod.sleep(1)
            try:
                # NT8 blip 가드(적대검증 D2): 연결이 끊기면 계정이 push에서 통째로 빠져
                # 빈 포지션 = '플랫'으로 오독된다. 신선 + 계정 존재일 때만 플랫 인정.
                if _is_nt8 and hasattr(b, "snapshot_fresh"):
                    if not (b.snapshot_fresh() and b.account_connected(aid_cfg)):
                        continue
                left = self._passtp_filter(b.list_open_positions(), aid_cfg)
                if not left:
                    flat = True; break
            except Exception:
                pass
        _aid = None
        try:
            _aid = ((poss[0].raw or {}).get("_accountId") or poss[0].account_id) if poss else None
        except Exception:
            pass
        if not flat:
            # 닫힌 레그의 주문(고아 스탑)만 걷는다 - 살아 있는 레그의 보호손절은 유지.
            closed_cons = {p.symbol for p in poss} - {p.symbol for p in left}
            if closed_cons and _aid is not None:
                try:
                    for o in b._open_orders(_aid):
                        if str(o.get("contractId")) in closed_cons:
                            try:
                                b._cancel_order(_aid, o.get("id"))
                            except Exception:
                                pass
                except Exception:
                    pass
            self.log(f"   🛑 [{lbl}] 청산 미확인({len(left)}건 잔존) - 살아 있는 레그의 "
                     f"손절은 유지, 60초 뒤 이어서 정리합니다. 계좌를 직접 확인하십시오.")
            fw = getattr(self, "_passtp_failwarn", None)
            if fw is None:
                fw = self._passtp_failwarn = set()
            if key not in fw:
                fw.add(key)
                self.root.after(0, lambda l=lbl: messagebox.showwarning(
                    "EQ Autopilot",
                    (f"[{l}] 통과 익절 청산이 다 확인되지 않았습니다.\n"
                     "잔여 포지션의 보호 손절은 그대로 있으며 60초마다 이어서 정리합니다.\n"
                     "프롭사 화면에서 계좌를 직접 확인하십시오."
                     if self.lang == "ko" else
                     f"[{l}] Pass take-profit close not fully confirmed.\n"
                     "Protective stops on remaining legs stay in place; retrying every 60s.\n"
                     "Please check the account at your prop firm.")))
            return False
        # ── 플랫 확인됨: 그 계좌 잔여 주문 전체 취소(고아 손절 방지) ──
        # NT8은 close=종목 Flatten이 주문 취소까지 포함하고 _open_orders API가 없다 -
        # 어댑터가 지원할 때만 수행(2026-08-20 nt8 지원).
        if _aid is not None and hasattr(b, "_open_orders"):
            try:
                for o in b._open_orders(_aid):
                    try:
                        b._cancel_order(_aid, o.get("id"))
                    except Exception:
                        pass
            except Exception:
                pass
        done = getattr(self, "_passtp_done", None)
        if done is None:
            done = self._passtp_done = {}
        done[key] = _timemod.time()
        getattr(self, "_passtp_fired", set()).discard(key)
        # 재진입 차단: 이 실계좌가 무장된 모든 (자산,행)을 이번 가동에서 해제.
        try:
            for k2 in [k2 for k2, c2 in list(self._sig_accts.items())
                       if c2.get("broker") == cfg.get("broker")
                       and str(c2.get("acct") or "").strip().lower()
                       == str(aid_cfg).strip().lower()]:
                self._sig_accts.pop(k2, None)
        except Exception:
            pass
        # 실현 잔고 재확인 - 슬리피지·수수료로 목표 미달이면 축하 대신 경고.
        try:
            # NT8은 플랫 후 NetLiq=실현 잔고와 동치라 그것으로 재확인(2026-08-20).
            bal2 = (b.account_netliq(aid_cfg) if cfg.get("broker") == "nt8"
                    else b.account_balance(aid_cfg))
        except Exception:
            bal2 = None
        _short = bal2 is not None and float(bal2) < tp
        self.log(f"   ✅ [{lbl}] 정리 완료(플랫 확인) - 신규 자동 진입 중단"
                 + (f", ⚠ 실현 잔고 ${float(bal2):,.0f} < 목표 ${tp:,.0f}" if _short else ""))
        self.root.after(0, lambda l=lbl, t=total, s=_short, b2=bal2: messagebox.showinfo(
            "EQ Autopilot",
            ((f"[{l}] 통과 목표 도달 - 미실현 ${t:,.0f}에서 포지션을 정리했습니다.\n"
              "이 계좌의 신규 자동 진입은 중단됐습니다(다음 [라이브 시작]까지).\n"
              + (f"주의: 실현 잔고(${float(b2):,.0f})가 목표에 다소 못 미칩니다 - "
                 "프롭사 화면에서 통과 여부를 확인하십시오." if s else
                 "프롭사 화면에서 통과 여부와 계좌 상태를 확인하십시오."))
             if self.lang == "ko" else
             (f"[{l}] Pass target reached - closed at ${t:,.0f} open profit.\n"
              "New automated entries for this account are paused (until the next Go Live).\n"
              + (f"Note: realized balance (${float(b2):,.0f}) landed slightly below target - "
                 "verify the pass at your prop firm." if s else
                 "Check your prop firm's dashboard for the account status.")))))
        return True

    def _passtp_scan(self):
        done = getattr(self, "_passtp_done", None)
        if done is None:
            done = self._passtp_done = {}
        fired = getattr(self, "_passtp_fired", None)
        if fired is None:
            fired = self._passtp_fired = set()
        for asset, idx, cfg, tp in self._passtp_targets():
            aid_cfg = str(cfg.get("acct") or "")
            key = (cfg.get("broker"), aid_cfg.strip().lower())
            if done.get(key):
                continue                        # 이 계좌는 이번 가동에서 이미 정리함
            try:
                b = self._passtp_broker(cfg)
                if key in fired:
                    # 직전 회차에 발동했으나 플랫 미확인 - 평가 없이 정리를 이어간다.
                    self._passtp_close_account(b, cfg, aid_cfg, key, tp, 0.0)
                    continue
                poss = self._passtp_filter(b.list_open_positions(), aid_cfg)
                if not poss:
                    continue
                if cfg.get("broker") == "nt8":
                    # ── Lucid(NT8): 시세·포인트가치 없이 NetLiq(잔고+미실현) 하나로 판정
                    # (2026-08-20). 구 애드온은 net_liq를 안 보내 None → 1회 안내 후 휴면.
                    # 신선도 게이트(적대검증 D1): NT8이 죽으면 push가 고착 - 낡은 값 발사 금지.
                    if hasattr(b, "snapshot_fresh") and not b.snapshot_fresh():
                        continue
                    nl = None
                    try:
                        nl = b.account_netliq(aid_cfg)
                    except Exception:
                        nl = None
                    _cash0 = None
                    try:
                        _cash0 = b.account_balance(aid_cfg)
                    except Exception:
                        pass
                    if nl is not None and float(nl) == 0.0 and _cash0 and float(_cash0) > 0:
                        nl = None              # 피드가 NetLiq 미지원(0 고정) - 무음 휴면 방지(D3)
                    if nl is None:
                        w2 = getattr(self, "_passtp_warned", None)
                        if w2 is None:
                            w2 = self._passtp_warned = set()
                        wk2 = ("nt8-old-addon", key[1])
                        if wk2 not in w2:
                            w2.add(wk2)
                            self.log(f"   ⚠ [{cfg.get('label') or aid_cfg}] 통과 익절: NT8 "
                                     f"브리지 애드온이 구버전입니다 - 앱 패키지의 새 "
                                     f"EQAutopilotBridge.cs로 교체(재컴파일)해야 작동합니다")
                        continue
                    cash = _cash0
                    buf = self._passtp_buffer(poss)
                    if cash is not None and float(cash) >= tp + buf:
                        note = getattr(self, "_passtp_note", None)
                        if note is None:
                            note = self._passtp_note = {}
                        if not note.get(key):
                            note[key] = True
                            self.log(f"  , [{cfg.get('label') or aid_cfg}] 잔고 "
                                     f"${float(cash):,.0f}가 이미 목표 ${tp:,.0f} 이상 - "
                                     f"통과 익절 대기 없음(설정 확인 권장)")
                        continue
                    _pv = getattr(self, "_passtp_nlprobe", None)
                    if _pv is None:
                        _pv = self._passtp_nlprobe = set()
                    if key not in _pv:
                        _pv.add(key)
                        try:
                            _u0 = b.account_unrealized(aid_cfg)
                            self.log(f"  , [{cfg.get('label') or aid_cfg}] NT8 NetLiq 실측: "
                                     f"netliq=${float(nl):,.0f} cash="
                                     f"${float(cash):,.0f} upnl=${float(_u0 or 0):,.0f} "
                                     f"(netliq≈cash+upnl이면 정상)")
                        except Exception:
                            pass
                    if float(nl) < tp + buf:
                        continue
                    if not getattr(self, "_sig_on", False):
                        return
                    if (asset, idx) not in getattr(self, "_sig_accts", {}):
                        continue
                    _upnl = None
                    try:
                        _upnl = b.account_unrealized(aid_cfg)
                    except Exception:
                        pass
                    lbl = cfg.get("label") or aid_cfg
                    self.log(f"\n🎯 [{lbl}] 통과 목표 도달 - NetLiq ${float(nl):,.0f} ≥ "
                             f"목표 ${tp:,.0f}+버퍼 ${buf:,.0f} → 이 계좌 정리")
                    fired.add(key)
                    self._passtp_close_account(b, cfg, aid_cfg, key, tp,
                                               float(_upnl) if _upnl is not None else 0.0)
                    continue
                # ProjectX position.type 실측 전 방어: 1·2 외 값이면 부호를 못 믿는다.
                if any((p.raw or {}).get("type") not in (1, 2) for p in poss):
                    continue
                total, seen = 0.0, 0
                for p in poss:
                    pv = self._passtp_pv(p.symbol)
                    avg = _as_float((p.raw or {}).get("averagePrice"), 0.0)
                    if not pv or avg <= 0:
                        continue                # 값이 불확실하면 이 포지션은 판단에서 뺀다
                    mark = b.current_market_price(p.symbol)
                    if mark is None:
                        continue
                    total += (float(mark) - avg) * int(p.net_qty) * pv
                    seen += 1
                if seen == 0 or seen != len(poss):
                    continue                    # 하나라도 값을 못 읽으면 이번 회차는 판단 보류
                if total <= 0:
                    continue                    # 이익 구간이 아니면 볼 것 없다
                try:
                    bal = b.account_balance(aid_cfg)
                except Exception:
                    bal = None
                if bal is None:
                    continue                    # 잔고를 모르면 판단 보류(오발동 방지)
                # ⚠ balance가 실현 잔고인지 에쿼티인지 VERIFY 전(라이브=정본) - 에쿼티라면
                # 미실현이 이중 가산돼 이르게 발동한다. 정리 후 '실현 잔고 재확인'이 그물이다.
                buf = self._passtp_buffer(poss)
                if float(bal) >= tp + buf:
                    # 이미 실현 잔고만으로 목표 이상 - 확정할 게 없으니 건드리지 않는다.
                    note = getattr(self, "_passtp_note", None)
                    if note is None:
                        note = self._passtp_note = {}
                    if not note.get(key):
                        note[key] = True
                        self.log(f"  , [{cfg.get('label') or aid_cfg}] 잔고 ${float(bal):,.0f}가 "
                                 f"이미 목표 ${tp:,.0f} 이상 - 통과 익절 대기 없음(설정 확인 권장)")
                    continue
                if float(bal) + total < tp + buf:
                    continue
                # ── 발사 직전 최종 가드: 무장 유지 + 스냅샷 일치 재확인 ──
                if not getattr(self, "_sig_on", False):
                    return
                if (asset, idx) not in getattr(self, "_sig_accts", {}):
                    continue
                fresh = self._passtp_filter(b.list_open_positions(), aid_cfg)
                if ({(p.symbol, int(p.net_qty)) for p in fresh}
                        != {(p.symbol, int(p.net_qty)) for p in poss}):
                    continue                    # 그 사이 포지션이 변함(청산/신규) - 보류
                lbl = cfg.get("label") or aid_cfg
                self.log(f"\n🎯 [{lbl}] 통과 목표 도달 - 잔고 ${float(bal):,.0f} + 미실현 "
                         f"${total:,.0f} ≥ 목표 ${tp:,.0f}+버퍼 ${buf:,.0f} → 이 계좌 정리")
                fired.add(key)
                self._passtp_close_account(b, cfg, aid_cfg, key, tp, total)
            except Exception as e:
                self.log(f"  , 통과 익절 감시 오류({asset}/{aid_cfg}): {e}")

    def _prop_in_use(self) -> bool:
        """켜진 계좌 중 프롭 계열 브로커(projectx=Topstep 계열, nt8=Lucid)가 있는가.
        크립토(bybit/bitget)나 자기자본(tradovate/ibkr)만 쓰면 비활동 경고는 소음이다."""
        try:
            for _a in ("NQ", "GC"):
                _cfg = self._acfg.get(_a) or {}
                _default = _cfg.get("broker") or _ASSET_BROKERS[_a][0]
                for _row in (_cfg.get("accounts") or []):
                    if not _row.get("on"):
                        continue
                    if (_row.get("broker") or _default) in ("projectx", "nt8"):
                        return True
        except Exception:
            pass
        return False

    def _bundled_bridge_ver(self) -> str:
        """앱 동봉 브리지 .cs의 BridgeVer 문자열("" = 동봉 없음/파싱 실패).
        라이브 시작 구버전 경보(_conn_check)가 실행 중 브리지와 대조하는 기준값."""
        import re as _re
        import sys as _sys
        _base = getattr(_sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        for _c in (os.path.join(_base, "nt8_addon", "EQAutopilotBridge.cs"),
                   os.path.join(_base, "_internal", "nt8_addon", "EQAutopilotBridge.cs"),
                   os.path.join(os.path.dirname(_base), "nt8_addon", "EQAutopilotBridge.cs")):
            try:
                if os.path.exists(_c):
                    m = _re.search(r'BridgeVer\s*=\s*"([^"]+)"',
                                   open(_c, encoding="utf-8").read())
                    return m.group(1) if m else ""
            except Exception:
                pass
        return ""

    def _nt8_install_bridge(self):
        """[브리지 설치/업데이트] - NT8 애드온 설치를 원클릭으로(대표 2026-08-31 "자동화
        불가능한가?" → "토큰 입력만 받음 되잖아" - 그 토큰조차 이미 저장돼 있어 입력 0).

        하는 일: 앱 패키지에 동봉된 EQAutopilotBridge.cs를 읽어 ①Token 상수에 저장된
        Bridge Token(f1)을, ②BaseUrl 포트에 f3(기본 8377)을 주입하고, ③문서/NinjaTrader 8/
        bin/Custom/AddOns/ 에 덮어쓴다(기존 파일은 .bak). 컴파일은 NT8이 재시작할 때
        스스로 한다 - 그래서 남는 수동 단계는 "NT8 재시작" 하나다.
        실사고 배경: 대표 윈도 머신의 구버전 브리지가 포지션 조회에 빈 값을 줘, 8/21의
        확인 게이트가 체결 보고를 열흘간 전부 제외했다(매매는 무사, 보고만 소실)."""
        ko = self.lang == "ko"
        # 토큰 설명을 버튼 흐름이 직접 한다(대표 2026-09-04 "루시드 토큰 설정 열라 헷갈려" -
        # "이건 루시드 브로커 토큰이고 네가 설정하고 기억해라"). 회원이 만들 것도 기억할
        # 것도 없다는 걸 팝업이 먼저 선언한다.
        if not messagebox.askyesno(
                "EQ Autopilot",
                ("NT8 브리지를 설치/업데이트합니다.\n\n"
                 "여기 쓰이는 토큰은 NinjaTrader 8 연결용 Bridge Token입니다. 앱이 자동으로 "
                 "만들어 저장하고, 브리지 파일에도 같은 값을 넣습니다.\n"
                 "직접 정할 필요도, 기억할 필요도 없습니다.\n\n계속할까요?"
                 if ko else
                 "Install/update the NT8 bridge.\n\n"
                 "The token used here is the Bridge Token for the NinjaTrader 8 connection. "
                 "The app creates and stores it automatically, and injects the same value "
                 "into the bridge file.\nNothing to type, nothing to remember.\n\nContinue?")):
            return
        try:
            # 자산 컨텍스트 자가 결정(2026-09-04, 버튼 최상단 이동에 따른 수리): 현재 탭이
            # 크립토여도 ①f1 저장된 자산 ②브로커=nt8인 자산 순으로 찾는다 - 아니면 엉뚱한
            # 자산 밑에 새 토큰을 만들어 기존 브리지와 토큰이 어긋난다(통신 두절 지뢰).
            _cands = [a for a in _ASSETS if (self._creds_of(a, "nt8") or {}).get("f1")]
            _cands += [a for a in _ASSETS if self._broker_of(a) == "nt8"]
            cr = self._creds_of(_cands[0] if _cands else self._asset, "nt8")
        except Exception:
            cr = {}
        tok = (cr.get("f1") or "").strip()
        _gen = False
        if not tok:
            # 처음 까는 사람은 토큰을 지어낼 필요도 없다(대표 2026-08-31 "처음 까는 사람은?").
            # 앱이 만들고, _save_cfg의 비밀 스윕이 키체인에 넣는다 - 필드에 친 것과 동일 경로.
            import secrets as _sec
            tok = _sec.token_urlsafe(18)
            cr["f1"] = tok
            _gen = True
            try:
                self._save_cfg()
            except Exception:
                pass
            self.log("🧩 Bridge Token 자동 생성 - 앱에 저장했고 브리지에도 같은 값을 넣습니다")
        try:
            _prt = int(str(cr.get("f3") or "").strip() or 8377)
        except (TypeError, ValueError):
            _prt = 8377
        import sys as _sys
        _base = getattr(_sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        src = None
        for _c in (os.path.join(_base, "nt8_addon", "EQAutopilotBridge.cs"),
                   os.path.join(_base, "_internal", "nt8_addon", "EQAutopilotBridge.cs"),
                   os.path.join(os.path.dirname(_base), "nt8_addon", "EQAutopilotBridge.cs")):
            if os.path.exists(_c):
                src = _c
                break
        if not src:
            messagebox.showwarning("EQ Autopilot",
                                   "앱 패키지에서 브리지 파일을 찾지 못했습니다 - 재설치 후 다시 시도하세요."
                                   if ko else
                                   "Bridge file not found in the app package - reinstall the app and retry.")
            return
        try:
            txt = open(src, encoding="utf-8").read()
        except Exception as _e:
            messagebox.showwarning("EQ Autopilot", f"브리지 파일 읽기 실패: {_e}")
            return
        import re as _re
        txt, _n1 = _re.subn(r'(private const string Token\s*=\s*")[^"]*(")',
                            lambda m: m.group(1) + tok + m.group(2), txt, count=1)
        txt, _n2 = _re.subn(r'(private const string BaseUrl\s*=\s*"http://127\.0\.0\.1:)\d+(")',
                            lambda m: m.group(1) + str(_prt) + m.group(2), txt, count=1)
        if not _n1:
            messagebox.showwarning("EQ Autopilot",
                                   "브리지 파일에서 Token 자리를 찾지 못했습니다 - 수동 절차(가이드)를 따르세요."
                                   if ko else
                                   "Could not find the Token slot in the bridge file - follow the manual steps in the guide.")
            return
        # NT8 폴더 탐색 3겹(대표 2026-08-31 "닌자 폴더가 다르면?"):
        # ①지난번 성공 경로(프로필 기억) ②윈도 레지스트리의 실제 '문서' 위치(폴더
        # 리다이렉트를 어디로 했든 이게 정답) ③기본/OneDrive 후보. 전부 실패하면
        # 폴더 선택창으로 대표가 직접 지정 - 한 번 고르면 기억한다.
        _cands = []
        _saved = str((self._profile or {}).get("nt8_custom_dir") or "")
        if _saved:
            _cands.append(_saved)
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion"
                                r"\Explorer\User Shell Folders") as _k:
                _docs = winreg.QueryValueEx(_k, "Personal")[0]
                _docs = os.path.expandvars(_docs)
                _cands.append(os.path.join(_docs, "NinjaTrader 8", "bin", "Custom"))
        except Exception:
            pass                      # 맥/리눅스 또는 레지스트리 접근 실패 - 후보로 계속
        _home = os.path.expanduser("~")
        _cands.append(os.path.join(_home, "Documents", "NinjaTrader 8", "bin", "Custom"))
        _od = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer") or ""
        if _od:
            _cands.append(os.path.join(_od, "Documents", "NinjaTrader 8", "bin", "Custom"))
            _cands.append(os.path.join(_od, "문서", "NinjaTrader 8", "bin", "Custom"))
        _dstb = next((c for c in _cands if c and os.path.isdir(c)), None)
        if not _dstb:
            # 마지막 수단: 대표가 직접 고른다. 'NinjaTrader 8' 폴더를 고르면 bin/Custom을
            # 이어 붙이고, Custom까지 고른 경우도 알아서 받는다.
            try:
                from tkinter import filedialog
                _pick = filedialog.askdirectory(
                    title=("NinjaTrader 8 폴더를 선택하세요 (문서 안의 'NinjaTrader 8')"
                           if ko else "Select your 'NinjaTrader 8' folder (inside Documents)"))
            except Exception:
                _pick = ""
            if _pick:
                for _try in (os.path.join(_pick, "bin", "Custom"), _pick):
                    if os.path.isdir(_try) and os.path.basename(_try).lower() == "custom":
                        _dstb = _try
                        break
                    if os.path.isdir(os.path.join(_try, "AddOns")):
                        _dstb = _try
                        break
        if not _dstb:
            messagebox.showwarning("EQ Autopilot",
                                   "NinjaTrader 8 폴더를 찾지 못했습니다.\n"
                                   "NT8이 설치된 이 PC에서 누르고, 선택창에서는 문서 안의 "
                                   "'NinjaTrader 8' 폴더를 고르세요." if ko else
                                   "NinjaTrader 8 folder not found.\n"
                                   "Press this on the PC where NT8 is installed and pick the "
                                   "'NinjaTrader 8' folder inside Documents.")
            return
        try:                          # 다음번을 위해 기억(수동 선택, 레지스트리 어느 쪽이든)
            self._profile["nt8_custom_dir"] = _dstb
            self._save_cfg()
        except Exception:
            pass
        _addons = os.path.join(_dstb, "AddOns")
        try:
            os.makedirs(_addons, exist_ok=True)
            _dst = os.path.join(_addons, "EQAutopilotBridge.cs")
            if os.path.exists(_dst):
                import shutil as _sh
                _sh.copyfile(_dst, _dst + ".bak")
            with open(_dst, "w", encoding="utf-8") as f:
                f.write(txt)
        except Exception as _e:
            messagebox.showwarning("EQ Autopilot", f"브리지 설치 실패: {_e}")
            return
        self.log(f"🧩 브리지 설치 완료: {_dst} (토큰, 포트 자동 주입)")
        messagebox.showinfo("EQ Autopilot",
                            ("브리지를 설치했습니다(" + ("토큰 자동 생성, " if _gen else "") + "토큰 자동 주입" +
                             (f", 포트 {_prt}" if _prt != 8377 else "") + ").\n\n"
                             "Bridge Token은 앱이 저장해 두었습니다 - 기억하지 않으셔도 "
                             "됩니다.\n\n"
                             "이제 NinjaTrader 8을 완전히 종료했다가 다시 시작하세요 - "
                             "시작할 때 자동으로 컴파일됩니다.\n"
                             "NT8 로그에 [EQBridge] started 가 뜨면 완료입니다.") if ko else
                            ("Bridge installed (" + ("token generated and " if _gen else "") + "token injected" +
                             (f", port {_prt}" if _prt != 8377 else "") + ").\n\n"
                             "The Bridge Token is stored by the app - nothing to remember.\n\n"
                             "Now fully quit and restart NinjaTrader 8 - it compiles "
                             "changed add-ons at startup.\n"
                             "You are done when the NT8 log shows [EQBridge] started."))

    def _idle_warn_tick(self):
        """프롭 비활동 경고(대표 2026-08-31 "걍 앱에 경고 띄워, 그걸로 끝").
        EQ 자동 진입이 21일째 없고 프롭 계좌가 켜져 있으면 하루 한 번 경고창 + 로그.
        21일 근거는 _idle_days 독스트링(실측 최장 28일, 30일 규정까지 10일 여유).
        판정 불가(None)면 조용히 - 경고를 못 띄우는 것보다 오경보가 더 해롭다."""
        try:
            _d = _idle_days()
            if _d is not None and _d >= IDLE_WARN_DAYS and self._prop_in_use():
                _msg = self.t("idle_warn").format(d=_d)
                self.log("⚠ " + _msg)
                _mark = os.path.join(APP_DIR, ".eqidle_warned")
                import datetime as _dtm
                _today = _dtm.date.today().isoformat()
                _prev = ""
                try:
                    _prev = open(_mark, encoding="utf-8").read().strip()
                except Exception:
                    pass
                if _prev != _today:
                    try:
                        open(_mark, "w", encoding="utf-8").write(_today)
                    except Exception:
                        pass
                    messagebox.showwarning("EQ Autopilot", _msg)
        except Exception:
            pass
        finally:
            try:      # 하루 두 번 재확인 - 켜둔 채 방치해도 날짜가 넘어가면 다시 판정
                self.root.after(12 * 3600 * 1000, self._idle_warn_tick)
            except Exception:
                pass

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
                if mins <= 0:
                    continue
                # 창마다 별도 표식 - 좁은 창이 넓은 창의 표식에 먹히면 안 된다.
                # 좁은 창부터 본다 - 앱을 진입 직전에 켠 경우 넓은 창이 먼저 걸려
                # 좁은 창을 삼키면 '직전 점검'이라는 목적 자체가 사라진다.
                for _w in sorted(PRECHECK_WINDOWS_MIN):
                    key = (bk, a, ent.isoformat(), _w)
                    if mins > _w or self._precheck_done.get(key):
                        continue
                    # 좁은 창이 돌면 넓은 창은 **포함관계라 같이 소진**시킨다. 안 그러면
                    # 진입 직전에 앱을 켠 경우 10분 창이 먼저 돌고 다음 틱에 70분 창이
                    # 또 돌아, 같은 점검이 두 번 나가고 원장에는 T-3분에 win=70이라는
                    # 뜻이 어긋난 행이 남는다.
                    for _w2 in PRECHECK_WINDOWS_MIN:
                        if _w2 >= _w:
                            self._precheck_done[(bk, a, ent.isoformat(), _w2)] = True
                    threading.Thread(target=self._precheck_run,
                                     args=(a, dict(cfg), ent.strftime("%H:%M %Z"), _w),
                                     daemon=True).start()
                    break                       # 한 틱에 한 번만(가장 좁은 미발화 창)
        except Exception:
            pass
        finally:
            self.root.after(5 * 60 * 1000, self._precheck_tick)

    def _precheck_run(self, asset, cfg, entry_label, window_min=PRECHECK_WINDOW_MIN):
        """진입 전 브로커 왕복 점검 1회. window_min = 몇 분 전 창에서 불렸는가.

        ⚠️2026-09-13: 결과를 **서버에도 보고한다**. 종전에는 self.log + messagebox뿐이라
        회원이 자리에 없으면 아무도 몰랐고, 서버는 NT8이 죽은 것을 진입 시각까지 알 수
        없었다(생존 핑은 앱 것이라 계속 간다). NT8에서 healthcheck()는 브리지 하트비트가
        5초만 낡아도 예외를 던지므로 이 점검이 곧 NT8 생존 판정이다.
        직전 창(가장 좁은 창) 실패는 회원 DM까지 보낸다 - 그때가 고칠 수 있는 마지막
        순간이고, 모달은 자리에 없으면 못 본다."""
        ko = self.lang == "ko"
        _last = (window_min == min(PRECHECK_WINDOWS_MIN))
        try:
            b = _build_broker(cfg.get("broker"), cfg.get("f1", ""), cfg.get("f2", ""),
                              cfg.get("f3", ""), [cfg.get("acct")] if cfg.get("acct") else [])
            b.healthcheck()
            # 계약 해석까지 사전 검증(2026-08-12 첫 Lucid 라이브: search_contracts 부재가
            # 사전 점검을 통과하고 진입 순간에야 터짐 - 발주 직전 경로를 여기서 미리 밟는다).
            if hasattr(b, "search_contracts"):
                _pf_sym = {"NQ": "MNQ", "GC": "MGC"}.get(str(asset))
                if _pf_sym:
                    _pf_cs = b.search_contracts(_pf_sym)
                    if not _pf_cs or not _pf_cs[0].get("id"):
                        raise RuntimeError(f"활성 계약 해석 실패({_pf_sym}) - 진입이 막힙니다")
                    self.log(f"  , 계약 해석 OK: {_pf_sym} → {_pf_cs[0]['id']}")
            warns = []
            if hasattr(b, "key_info"):
                try:
                    warns = b.key_info() or []
                except Exception:
                    warns = []
            if warns:
                _w = "\n".join(f"- {w}" for w in warns)
                self.log(f"⚠ {asset} 사전 점검 경고 (진입 {entry_label}):")
                for w in warns:
                    self.log(f"  , {w}")
                self.root.after(0, lambda: messagebox.showwarning(
                    "API 사전 점검" if ko else "API pre-check",
                    (f"{asset} 진입({entry_label}) 전 점검에서 경고가 있습니다:\n\n{_w}"
                     if ko else
                     f"Pre-entry check for {asset} ({entry_label}) has warnings:\n\n{_w}")))
            else:
                self.log(f"🩺 {asset} API 사전 점검 통과 — 진입 {entry_label} 준비 완료"
                         f" ({_broker_label(cfg.get('broker'))}, T-{window_min}분)")
            self._send_ev("preflight_ok", asset, broker=str(cfg.get("broker") or "")[:16],
                          win=int(window_min), warns=len(warns))
        except Exception as e:
            _em = str(e)[:300]
            _hint = _entry_fail_hint(_em, ko)
            self.log(f"❌ {asset} API 사전 점검 실패 (진입 {entry_label}, T-{window_min}분): {_em}")
            self._send_ev("preflight_fail", asset, broker=str(cfg.get("broker") or "")[:16],
                          win=int(window_min), err=_em[:120])
            self._report_error(f"preflight:{asset}", e)
            if _last:
                # 고칠 수 있는 마지막 순간이다 - 모달만으로는 자리에 없는 회원을 못 잡는다.
                # 서버가 텔레그램·디스코드로 보낸다(kind별 30분 스로틀은 서버 몫).
                self._member_alert(
                    f"preflight_{asset}",
                    f"{asset} 진입 {window_min}분 전 점검에서 브로커 연결이 확인되지 않았습니다"
                    f"(진입 {entry_label}). 지금 고치지 않으면 이번 세션은 진입하지 않습니다. {_hint}",
                    f"The pre-entry check {window_min} minutes before {asset} could not reach your "
                    f"broker (entry {entry_label}). If it is not fixed now, this session will not "
                    f"be entered. {_hint}")
            self.root.after(0, lambda: messagebox.showerror(
                "API 사전 점검 실패" if ko else "API pre-check failed",
                (f"{asset} 진입({entry_label}) {window_min}분 전 점검에서 API 호출이 실패했습니다:\n\n{_em}\n\n{_hint}"
                 if ko else
                 f"The pre-entry API check for {asset} ({entry_label}), {window_min} minutes out, "
                 f"failed:\n\n{_em}\n\n{_hint}")))

    # ── 공개 트랙레코드 푸시 (Phase B 2단계) ─────────────────────────────────
    @staticmethod
    def _fill_asset(symbol: str):
        """체결 심볼 → 자산. 마이크로(MGC, MNQ)와 풀사이즈(GCE, ENQ, GC, NQ)를 **모두** 인식한다
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
                    txt = (f"마지막 동기화 {_ago}, {d}일 내 동기화하면 기록이 끊김 없이 이어짐" if ko
                           else f"last sync {_ago}, sync within {d}d for a gapless record")
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
        """공개 동의 토글 = 즉시 영속(전역, 회원 단위 — 탭 전환과 무관). 다음 동기화(수동/자동)가
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
        """브로커 체결(closed PnL)을 로컬에서 R로 변환, 일별 합산해 요약만 서버로 푸시.
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
            # 계좌마다 자기 브로커(대표 2026-09-08 "Lucid 계좌 0개·Topstep 누락"): 종전엔 자산 탭의
            # 주 브로커 하나(_broker_of)만 돌아, 같은 자산에 Topstep+Lucid를 함께 쓰면 두 번째
            # 브로커 계좌의 체결이 트랙레코드에서 통째로 빠졌다. 진입은 계좌별 브로커
            # (_acct_broker)로 도는데 동기화만 한 브로커였다 - 같은 축으로 맞춘다.
            for ac in self._accts_of(a):
                bk = self._acct_broker(a, ac)
                cr = self._creds_of(a, bk)
                f1 = (cr.get("f1") or "").strip()
                if not f1:
                    continue
                _sp = _BROKER_SPEC.get(bk, {})
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
                                       "브로커, 키가 설정된 계좌가 없습니다." if self.lang == "ko"
                                       else "No account has broker credentials configured.")
            return
        tok = self._token
        self.log(f"\n📤 트랙레코드 동기화{'(일일 자동)' if auto else ''} — {'공개' if public else '비공개'}, "
                 f"최근 {TR_LOOKBACK_DAYS}일 체결 수집… (핸들, 이름은 계정에서 자동)")

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
                        # 체결이 어느 브로커에서 났는지는 **이 자리에서만** 알 수 있다
                        # (대표 2026-09-15 "빗겟은 한 번도 안 돌았네" - 원장에 브로커가
                        # 없어서 붙은 날짜로 역산해야 답할 수 있었다). closed_fills는
                        # 이 로그인(c)의 체결만 돌려주므로 c["broker"]가 곧 그 체결의 브로커다.
                        f["_broker"] = c["broker"]
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
                                       "accts": {}, "base": {}, "brokers": set()})
                if f.get("_broker"):
                    e["brokers"].add(str(f["_broker"]))
                e["pnl"] += float(f.get("pnl") or 0)
                # 체결가 수집(2026-09-06 트랙레코드 영수증): 그룹의 진입가·청산가.
                # 계좌가 여럿이면 값이 갈리므로 **첫 값 하나만** 쓴다(평균을 지어내지 않는다).
                for _k in ("entry_px", "exit_px"):
                    _v = f.get(_k)
                    if _v and e.get(_k) is None:
                        try:
                            e[_k] = round(float(_v), 4)
                        except (TypeError, ValueError):
                            pass
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
                # tid에 기기를 넣는다(2026-08-14): 종전 'agg-날짜-자산'은 머신 구분이 없어,
                # 한 회원이 두 머신에서 같은 날 같은 자산을 거래하면 서버 병합 키가 겹쳐
                # 나중 푸시가 앞 푸시를 덮었다(맥=Topstep / 윈도우=Lucid 구성에서 실제 발생).
                trades.append({"tid": f"agg-{d}-{a}" + (f"-{s}" if s else "") + f"-{MACHINE_ID}",
                               "date": d,
                               "instrument": a, "direction": v["direction"],
                               # r = 손익비 = 그날 손익 ÷ 그날 실제 1R 합(사이징 무관, 절대 비교 가능).
                               # cash_rel = r과 동일 계산이나 공개 페이지는 이를 '누적한 뒤' 곡선의
                               # 매 점을 그 시점 규모로 재정규화한다 → 자본 투입·복리가 다이나믹하게
                               # 반영(대표 2026-07-26 "곡선이 날마다 업뎃돼도 됨. 어차피 상대금액").
                               # 절대 달러는 전송하지 않는다(개인정보). 규모 정보는 서버가 별도 산출.
                               "r": round(v["pnl"] / _rsum, 3),
                               "scale": round(_rsum / _bsum, 4),   # 개인 기준선 대비 규모 배수
                               # 체결가(있을 때만) - 서버 영수증이 **둘 다 있을 때만** 회원
                               # 체결가로 인쇄하고, 아니면 신호 기준가로 채운다.
                               # 체결 브로커(2026-09-15): 같은 판단이 두 거래소에서 체결되면
                               # 둘 다. 서버는 행을 통째로 저장하므로 추가 필드는 그대로 남고,
                               # 이 필드가 없는 옛 행·구버전 앱 푸시도 그대로 동작한다.
                               **({"brokers": sorted(v["brokers"])} if v.get("brokers") else {}),
                               **{k: v[k] for k in ("entry_px", "exit_px") if v.get(k)}})
            if _mine:
                self.log(f"   ⊘ EQ 원장에 없는 체결 {_mine}건 제외(직접 하신 거래 — 트랙레코드 미포함)")
            if not trades:
                self.log("   체결 없음 — 푸시할 내용이 없습니다.")
                return
            self.log(f"   일별 합산 {len(trades)}건 → 푸시 (키, 잔고 무전송)")
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

    _APP_VER = "2026.09.16b"

    # ── 체결 수량 보고 (#53, 대표 2026-08-08 "앱은 몇 거래 체결했는지만 보내면 대") ────
    # 왜 수량만 보내는가: 나머지는 서버가 이미 안다 - 진입가·손절은 발송 카드에, 현재가는
    # 실시간으로. 수량 하나면 금액이 나온다:
    #     미실현$ = (현재가 − 진입가) × 수량 × 틱가치
    # 보내는 것: {자산, 수량, 계좌 수, 무장 여부}. 계좌번호·잔고·사이징·체결가는 안 보낸다.
    # 앱은 상시 떠들지 않는다(대표 "그 담 한 일분간만, 오분 최대") - 진입이 끝난 시점에 1회,
    # 실패하면 5분 창 안에서만 재시도. 그 뒤 미실현은 서버가 현재가로 계속 계산한다.
    # ⚠️ 발주 경로에는 손대지 않는다 - 이 호출은 전부 별도 스레드이고, 실패해도 조용하다.
    _FILL_RETRY_WINDOW_S = 900      # 15분 창(2026-09-08: 5분) - 그 뒤엔 미전송 큐가 24시간 재시도

    def _claim_entry(self, sig, acct_id) -> bool:
        """서버 진입 클레임(대표 2026-08-22 "같은 토큰 다른 머신 이중 진입 막게"):
        같은 (토큰, 신호, 계좌)에 서버가 선착 1기기만 허가한다 - 기기 간 타이밍 경쟁 소멸.
        계좌번호는 안 보낸다(sha256 해시 12자). **fail-open**: 서버 불가침·오류면 True
        (단일 기기 회원이 서버 순단에 막히면 안 됨 - 신호가 방금 서버에서 왔으니 사실상
        항상 도달한다). False = 다른 기기가 선점 → 이 계좌 양보."""
        try:
            import hashlib
            import requests as _rq
            import autopilot_crypto
            tid = autopilot_crypto.path_id(self._token or "")
            sid = str((sig or {}).get("id") or "")
            if not tid or not sid:
                return True
            ah = hashlib.sha256(str(acct_id).strip().lower().encode()).hexdigest()[:12]
            r = _rq.post(PUSH_BASE + "eqclaim", timeout=6,
                         json={"tok_id": tid, "sig_id": sid, "acct_h": ah,
                               "mid": _machine_id()})
            if r.ok and r.text.startswith("claim:taken"):
                return False
            return True
        except Exception:
            return True

    def _nt8_standby(self) -> bool:
        """대기조 모드(대표 2026-08-21): 같은 루시드 계좌를 다른 기기(집 머신)가 함께 무장한
        과도기용. config.yaml에 `nt8_standby: true` 한 줄을 넣으면 NT8 진입이 ①신호 후
        200초(폴링 창 초과) 기다렸다가 ②같은 방향 포지션이 이미 있으면 그 계좌를 양보한다.
        기본 꺼짐 - 일반 회원(1기기) 동작 불변."""
        try:
            import yaml
            with open(CFG_PATH, encoding="utf-8") as f:
                return bool((yaml.safe_load(f) or {}).get("nt8_standby"))
        except Exception:
            return False

    def _note_fill(self, asset, micro=0.0, coin=0.0):
        """진입 leg 하나가 체결될 때마다 자산별로 누적. 멀티 계좌, 멀티 leg를 합산해서
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
                _d["n"] += 1          # 계좌(leg) 수 - 예전엔 except 블록 안이라 늘 0이었다
            if not hasattr(self, "_open_assets"):
                self._open_assets = set()
            self._open_assets.add(str(asset))        # 청산 버튼 노출 근거(2026-08-11)
            try:
                self._profile["open_assets"] = sorted(self._open_assets)
                self._save_cfg()                     # 재시작에도 흔적 유지
            except Exception:
                pass
            try:
                self.root.after(0, self._refresh_live_panel)
            except Exception:
                pass
        except Exception:
            pass

    def _send_gap(self, asset, sig, b, sym_match, mkt_at_send=None):
        """체결 갭 실측 보고(/eqgap, 대표 2026-08-11): 봉마감 기준가(entry_ref) 대비 실제
        평균 체결가. 자산당 신호 1건에 1회(첫 성공 계좌 기준 - 같은 순간 시장가라 계좌 간
        차이는 무시 가능). 포지션 평균단가가 브로커에 잡힐 때까지 최대 60초 폴링.
        발주 경로 무간섭 - 별도 스레드, 실패해도 조용히."""
        _caps = (self._gate or {}).get("caps", {})
        if not ((self._gate or {}).get("ok") and _caps.get("autoentry")):
            return
        try:
            _key = (str(asset), str((sig or {}).get("id")))
            if not hasattr(self, "_gap_sent"):
                self._gap_sent = set()
            if _key in self._gap_sent:
                return
            self._gap_sent.add(_key)
        except Exception:
            return
        import threading as _th
        import time as _t
        _ref = (sig or {}).get("entry_ref")
        _dir = (sig or {}).get("direction")
        _sid = (sig or {}).get("id")
        _sent_ts = _t.time()

        def w():
            try:
                import requests
                try:
                    import autopilot_crypto
                    tid = autopilot_crypto.path_id(self._token or "")
                except Exception:
                    return
                if not (tid and _ref and _dir):
                    return
                fill = None
                _t0 = _t.time()
                while _t.time() - _t0 < 60 and fill is None:
                    try:
                        for _p in (b.list_open_positions() or []):
                            if str(sym_match) not in str(_p.symbol):
                                continue
                            _raw = getattr(_p, "raw", {}) or {}
                            for _k2 in ("avgPrice", "averagePrice", "entryPrice",
                                        "avgEntryPrice", "buyAvgPrice"):
                                try:
                                    _v = float(_raw.get(_k2) or 0)
                                except Exception:
                                    _v = 0
                                if _v > 0:
                                    fill = _v
                                    break
                            if fill:
                                break
                    except Exception:
                        pass
                    if fill is None:
                        _t.sleep(5)
                if not fill:
                    return
                body = {"tok_id": tid, "id": str(_sid or ""), "inst": str(asset),
                        "direction": str(_dir).upper(), "entry_ref": float(_ref),
                        "fill_price": float(fill), "sent_ts": _sent_ts,
                        "fill_ts": _t.time()}
                if mkt_at_send:
                    body["mkt_at_send"] = float(mkt_at_send)   # 협의 슬리피지(서버가 계산)
                for _try in range(3):
                    try:
                        r = requests.post(PUSH_BASE + "eqgap", timeout=8, json=body)
                        if r.ok and str(r.text).startswith("gap:"):
                            return
                    except Exception:
                        pass
                    _t.sleep(15)
            except Exception:
                pass
        _th.Thread(target=w, daemon=True).start()

    def _remember_open(self, asset, b, sym):
        """진입에 쓴 브로커, 심볼을 기억한다 - _check_stop_closed가 이걸로 조회한다."""
        try:
            if not hasattr(self, "_open_ctx"):
                self._open_ctx = {}
            self._open_ctx[str(asset)] = {"b": b, "sym": str(sym)}
            getattr(self, "_flat_seen", {}).pop(str(asset), None)
        except Exception:
            pass

    def _check_stop_closed(self):
        """브로커 첨부 스탑이 발동해 포지션이 사라진 경우를 감지해 서버에 청산을 보고한다.

        왜 필요한가: 손절은 거래소/브로커에 첨부된 스탑이 체결하므로 앱의 청산 경로를
        타지 않는다(_send_fill(closed=True)는 시간 청산 flatten 뒤에서만 불린다).
        그래서 서버의 체결 버킷이 영구 open으로 남고, 회원 대시보드는 손절이 날 때마다
        '시스템 기준 청산 - 내 계좌 확인 필요'를 36시간 띄웠다(실데이터: 마지막 청산
        보고 2026-08-21, 그 뒤 진입은 전부 open).

        ⚠️조회 실패를 청산으로 오판하지 않는다 - 예외면 관측을 버리고, **연속 2회**
        flat일 때만 보고한다(하트비트 60초 주기이므로 약 1~2분 확인). 읽기 전용이라
        주문 경로에 개입하지 않는다."""
        _assets = list(getattr(self, "_open_assets", set()) or [])
        if not _assets:
            return
        for _a in _assets:
            _ctx = (getattr(self, "_open_ctx", {}) or {}).get(str(_a))
            if not _ctx:
                continue                      # 이 앱이 연 포지션이 아니면 판단하지 않는다
            _b, _sym = _ctx.get("b"), _ctx.get("sym")
            if not _b or not _sym:
                continue
            try:
                _poss = _b.list_open_positions()
            except Exception:
                self._flat_seen.pop(str(_a), None)     # 조회 실패 = 무판단
                continue
            if _poss is None:
                self._flat_seen.pop(str(_a), None)
                continue
            try:
                _still = any(str(_sym) in str(getattr(_p, "symbol", "")) for _p in _poss)
            except Exception:
                self._flat_seen.pop(str(_a), None)
                continue
            if _still:
                self._flat_seen.pop(str(_a), None)
                continue
            _n = int(self._flat_seen.get(str(_a), 0)) + 1
            self._flat_seen[str(_a)] = _n
            if _n < 2:
                continue                      # 한 번은 안 믿는다(전송 지연·일시 오류)
            self._flat_seen.pop(str(_a), None)
            self.log(f"\u2139 {_a} \ud3ec\uc9c0\uc158\uc774 \ube0c\ub85c\ucee4\uc5d0\uc11c "
                     f"\uc0ac\ub77c\uc84c\uc2b5\ub2c8\ub2e4(\uc190\uc808 \ucd94\uc815) "
                     f"- \uc11c\ubc84\uc5d0 \uccad\uc0b0\uc744 \ubcf4\uace0\ud569\ub2c8\ub2e4.")
            self._open_ctx.pop(str(_a), None)
            self._send_fill(_a, closed=True)

    def _send_fill(self, asset, closed=False):
        """자산 하나의 진입이 끝난 뒤 1회 전송. closed=True면 수량 0(청산 알림).
        (2026-08-11) closed면 열린 포지션 흔적 해제 - 청산 버튼 자동 숨김.
        Autopilot(autoentry) 전용(대표 2026-08-09 '오토파일럿한테만 앱에서 서버로') —
        대시보드 앱 상태를 보는 등급도 Autopilot뿐이라 그 외 등급은 아예 안 보낸다.
        서버(/eqfill)도 같은 게이트로 이중 방어(fl:ignored)."""
        if closed:
            try:
                getattr(self, "_open_assets", set()).discard(str(asset))
                self._profile["open_assets"] = sorted(getattr(self, "_open_assets", set()))
                self._save_cfg()
                self.root.after(0, self._refresh_live_panel)
            except Exception:
                pass
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
                    # mid를 실어 보낸다(2026-09-02): 진입은 mid로 기기별 버킷을 만드는데
                    # 청산만 mid가 없어 서버가 '_'로 받았다. 서버는 mid='_'면 그 자산의
                    # 열린 버킷을 **전부** 닫는 보수적 경로를 타므로, 두 기기를 함께 쓰면
                    # 한쪽 손절이 다른 기기 보유까지 닫힌 것으로 보고된다. mid가 안 맞으면
                    # 서버의 역방향 폴백이 여전히 열린 버킷을 닫아 준다(재설치 대비).
                    body = {"tok_id": tid, "inst": str(asset), "mid": MACHINE_ID,
                            "qty_micro": 0, "qty_btc": 0}
                else:
                    if not hasattr(self, "_fill_lock"):
                        return              # 이 자산에서 체결된 게 없다
                    with self._fill_lock:
                        _d = (self._fill_acc or {}).pop(str(asset), None)
                    if not _d:
                        return
                    body = {"tok_id": tid, "inst": str(asset), "mid": MACHINE_ID,
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
                    _t.sleep(30)                # 15분 창 안에서만
                # 창 소진 → 미전송 큐(2026-09-08 GC 사고: 서버 기기 슬롯 거부로 5분 안에 못 보낸
                # 진입·청산 보고가 영영 사라졌다). 2분마다 24시간까지 다시 보낸다.
                self._queue_unsent_fill(body)
                self.log(f"   ⚠ {asset} 체결 보고 전송 실패(15분 재시도 소진) — 미전송 큐에 두고 "
                         "2분마다 24시간 재시도합니다(대시보드 수량, 금액은 성공 시 채워짐).")
            except Exception:
                pass
        _th.Thread(target=w, daemon=True).start()

    _UNSENT_PATH = os.path.join(APP_DIR, "unsent_fills.json")

    def _load_unsent(self) -> list:
        try:
            import json as _j
            with open(self._UNSENT_PATH, encoding="utf-8") as f:
                q = _j.load(f)
            return q if isinstance(q, list) else []
        except Exception:
            return []

    def _queue_unsent_fill(self, body: dict) -> None:
        """체결 보고를 미전송 큐(파일)에 둔다 - 앱을 껐다 켜도 살아남는다."""
        try:
            import json as _j
            import time as _t
            q = self._load_unsent()
            q.append({"body": body, "ts": _t.time()})
            with open(self._UNSENT_PATH, "w", encoding="utf-8") as f:
                _j.dump(q[-50:], f)
        except Exception:
            pass

    def _unsent_fill_tick(self):
        """2분마다 미전송 체결 보고를 다시 보낸다(24시간까지). 전송은 별도 스레드(UI 무정지)."""
        def w():
            try:
                import json as _j
                import time as _t
                import requests
                q = self._load_unsent()
                if not q:
                    return
                keep = []
                for it in q:
                    _age = _t.time() - float(it.get("ts") or 0)
                    if _age > 86400:
                        continue
                    try:
                        r = requests.post(PUSH_BASE + "eqfill", timeout=8, json=it.get("body") or {})
                        if r.ok and str(r.text).startswith(("fl:ok", "fl:ignored")):
                            self.log(f"   ✅ {(it.get('body') or {}).get('inst')} 지연 체결 보고 성공"
                                     f"(대기 {int(_age // 60)}분)")
                            continue
                    except Exception:
                        pass
                    keep.append(it)
                with open(self._UNSENT_PATH, "w", encoding="utf-8") as f:
                    _j.dump(keep, f)
            except Exception:
                pass
        try:
            if self._load_unsent():
                threading.Thread(target=w, daemon=True).start()
        except Exception:
            pass
        try:
            self.root.after(120 * 1000, self._unsent_fill_tick)
        except Exception:
            pass

    def _report_error(self, ctx, err):
        """예외 자동 리포트(대표 2026-07-27 "필수") — 서버 /eqerr로 익명 전송해 회원 머신의
        버그를 운영자가 본다("대표의 발견력을 회원 수만큼 스케일"). 전송 내용: 앱버전, OS·
        컨텍스트 태그·마스킹된 에러 문자열·토큰 해시 12자(익명 그룹핑)뿐 — 키·계좌번호·잔고
        무전송(5자리+ 숫자열 → # 마스킹). 중복 1시간 억제, 시간당 10건 캡. 실패해도 조용히."""
        import os as _os
        import re as _re
        import time as _t
        import threading as _th
        try:
            if _os.environ.get("EQ_ERR_REPORT", "1") == "0":
                return
            msg = _re.sub(r"\d{5,}", "#", str(err))[:400]
            _cs = str(ctx)[:40]
            # ⚠️종료 잔향은 아예 보내지 않는다(2026-09-13). 구버전 Tk는 창을 닫는 동안
            # 남은 after 콜백이 이미 파괴된 위젯을 건드려 "invalid command name
            # .!frame6.!canvas..."를 연발한다. 버그가 아니라 종료 소음인데, 위젯 경로가
            # 매번 달라 **아래 캡의 키를 10개까지 금방 채운다** - 실측: 대표 원장의
            # error 57건이 전부 이것이고 2026-09-04 15시에 32건이 몰렸다. 하필 그날이
            # NT8 진입 누락 사고 당일이라, 그 시간의 entry: 보고는 캡에 막혀 사라졌다.
            if _cs == "tk" and "invalid command name" in msg:
                return
            key = (_cs, msg[:80])
            now = _t.time()
            self._err_sent = {k: v for k, v in getattr(self, "_err_sent", {}).items()
                              if now - v < 3600}
            if key in self._err_sent:
                return
            # 계열별 예산(2026-09-13): 종전엔 전역 10건이라 어떤 소음이든 실행 계열을
            # 굶길 수 있었다. 돈이 걸린 보고는 자기 예산을 갖는다(서버 쪽도 같은 규약 -
            # web/error_report.py의 _SEVERE_PAT 버킷 분리).
            _sev = bool(_re.search(r"진입|청산|손절|entry|exit|close|stop|flatten|order",
                                   _cs, _re.I))
            _n = sum(1 for k in self._err_sent
                     if bool(_re.search(r"진입|청산|손절|entry|exit|close|stop|flatten|order",
                                        k[0], _re.I)) == _sev)
            if _n >= (20 if _sev else 10):
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

    _JITTER_TICK = {"NQ": 0.25, "GC": 0.1}   # 자산별 틱 크기(마이크로, 미니 동일 그리드)

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
        self.log(f"   🎲 [{lbl}] 손절 지터 +{j}틱 넓힘 → {new:g} (주문 프라이버시, 조기이탈 없음, 추가리스크 ≤0.5%)")
        return new

    def _run_futures_entry(self, b, sc, legs, direction, stop, sig, live, recv, asset, pub):
        """선물 진입 — 계약수 10개 이상이면 미니/마이크로 legs로 분할 체결(대표 2026-07-16).
        legs = [(심볼, 수량), ...]. 예: NQ 23계약 → [('NQ',2),('MNQ',3)], 5계약 → [('MNQ',5)].
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
            # 미진입 사유는 서버에도 반드시 남긴다(대표 2026-09-04 "못 드가진 원인 확실히
            # 기록") - 로컬 로그만 남기면 분쟁·진단 때 기기 앞에 가야만 알 수 있다.
            self.log(f"   ❌ 계좌 '{sc}' 없음.")
            if live:
                self._report_error(f"entry:{asset}", f"account missing: {sc}")
            return
        _aid = match[0]["id"]
        # leg 심볼별 활성 계약 조회
        resolved = []
        for _sym, _qty in legs:
            _con = self._resolve_contract(b, _sym)
            if not _con:
                self.log(f"   ❌ '{_sym}' 활성 계약 없음 — 이 leg 건너뜀."); continue
            resolved.append((_sym, _qty, _con))
        if not resolved:
            self.log("   ❌ 유효 계약 없음 — 진입 중단.")
            if live:
                self._report_error(f"entry:{asset}",
                                   f"no active contract: {[s for s, _ in legs]} ({sc})")
            return
        if len(resolved) > 1:
            self.log("   🧩 분할 진입: " + " + ".join(f"{q} {s}" for s, q, _ in resolved)
                     + " (미니 묶음 — 커미션 절감)")
        # ── 서버 진입 클레임(2026-08-22): 같은 토큰의 다른 기기가 이 계좌를 선점했으면 양보 ──
        if live and not self._claim_entry(sig, _aid):
            self.log(f"   🤝 다른 기기가 이 계좌의 이번 진입을 선점 — 양보(진입 안 함)")
            return
        # ── 대기조 모드(2026-08-21): 다른 기기 선진입 창을 통째로 기다린 뒤 포지션을 본다 ──
        _standby = type(b).__name__ == "NT8Broker" and self._nt8_standby()
        if _standby and live:
            _sid0 = str((sig or {}).get("id") or "")
            _sw = getattr(self, "_standby_waited", None)
            if _sw is None:
                _sw = self._standby_waited = set()
            if _sid0 and _sid0 not in _sw:
                _sw.add(_sid0)
                self.log("   ⏳ 대기조: 다른 기기 선진입 창 대기(200초) — 집 기기가 살아 있으면 양보합니다")
                _t.sleep(200)
        # ── 잔여 포지션 정리(leg 계약별) — 연속 세션 순서 보장 ──
        existing = b.list_open_positions()
        _mycons = {c for _, _, c in resolved}
        others = [p for p in existing if p.symbol not in _mycons]
        if others:
            self.log(f"   ⚠ 다른 심볼 포지션 {len(others)}개 감지 — 건드리지 않음: "
                     + ", ".join(f"{p.symbol}" for p in others[:3]))
        mine = [p for p in existing if p.symbol in _mycons]
        if mine and _standby:
            _want = 1 if str(direction).upper() in ("LONG", "BUY", "0") else -1
            if any((1 if int(p.net_qty) > 0 else -1) == _want for p in mine):
                # 같은 방향 기존 포지션 = 이번 세션을 다른 기기가 먼저 넣은 것. 청산·재진입
                # 대신 이 계좌를 통째로 양보한다(수수료 이중·손절 고아 방지).
                self.log(f"   🤝 대기조: 같은 방향 포지션 {len(mine)}개 확인 — 다른 기기 선진입, "
                         f"이 계좌 양보(진입 안 함)")
                return
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
                        self.log("   🛑 청산 확인 실패 — 진입 중단(순서 보장). 수동 확인 필요!")
                        self._report_error(f"entry:{asset}",
                                           f"stale-position close unconfirmed ({sc})")
                        return
                    self.log("   ✅ 잔여 청산 확인 — 진입 진행.")
                except Exception as _ce:
                    self.log(f"   ❌ 잔여 청산 실패: {_ce} — 진입 중단.")
                    self._report_error(f"entry:{asset}",
                                       f"stale-position close failed ({sc}): {_ce}")
                    return
        # 발주 직전 시장가 1회 샘플(2026-08-22, 21l 협의 슬리피지 실측용) - 실패해도 무해.
        _px_at_send = None
        try:
            _px_at_send = b.current_market_price(resolved[0][2])
        except Exception:
            pass
        # ── 체결 정책(GC 등 지정가) — leg 전부 동일 정책 적용 ──
        _polf = ((sig.get("exec_policy") or {}).get("entry")
                 if isinstance(sig.get("exec_policy"), dict) else None)
        _use_limit = bool(_polf and _polf.get("mode") == "limit_then_market"
                          and _polf.get("limit_price") is not None)
        _ok = 0
        # NT8 재시도(대표 2026-09-04 "안 들어가지면 재진입" + "일분 안쪽" + 8/21·9/4 조용한
        # 거절 사고): NT8 Submit은 접수=성공이 아니어서, 확인 실패 시 잔재 정리 후 재진입을
        # **시간 예산 55초** 안에서 반복한다(횟수 상한 6회). 재시도 자격 3겹 -
        #  ① 늦은 체결 없음(직전 조회 0계약 실확인 - 조회 실패면 이중 진입 방지 위해 보류)
        #  ② 손절가 기통과 아님(대표 "이미 손절 쳤음 못 가는 거고" - 죽은 트레이드 재진입 금지)
        #  ③ 예산·횟수 안. 타 브로커(ProjectX)는 동기 응답이라 기존 1회 동작 유지.
        # 재시도 전 close_contract(Flatten 기반)로 잔재를 정리하므로 이중 진입 불가.
        _is_nt8 = type(b).__name__ == "NT8Broker"
        _is_long = str(direction).upper() in ("LONG", "BUY", "0")
        for _sym, _qty, _con in resolved:
            _base_tag = f"EQ-AP-{int(recv.timestamp() * 1000)}-{_sym}"
            _placed = False          # True=확정 체결 / None=정책 스킵 / False=실패
            _last_why = ""
            _net_last = ""           # 마지막 망 프로브 결과(대표 "인터넷 불안정도 로깅")
            _res_ok = None           # 확정 시의 res(스탑 표시용)
            _tag = _base_tag
            _budget_s = 55.0 if (_is_nt8 and live) else 0.0   # "일분 안쪽"
            _t0 = _t.time()
            _try = 0                 # 지금까지의 제출 횟수(루프 끝에서 = 총 시도 수)
            res = {}
            while True:
                if _try:
                    if _t.time() - _t0 > _budget_s or _try >= 6:
                        break
                    # ① 늦은 체결 검사 - 직전 제출이 확인창(6초) 뒤에 체결됐을 수 있다
                    #    (오늘 사고의 8.6초 지연). 있으면 성공 처리, 조회 실패면 보류.
                    try:
                        _lateq = abs(int(b.position_qty(_aid, _con) or 0))
                    except Exception:
                        _lateq = -1
                    if _lateq > 0:
                        _placed = True; _res_ok = _res_ok or res
                        self.log(f"   ✅ {_sym} 늦은 체결 확인 ×{_lateq} — 재진입 불필요")
                        break
                    if _lateq < 0:
                        _last_why = (_last_why + " / 포지션 조회 실패 — 무포 확신 불가라 "
                                     "재진입 보류(이중 진입 방지)")[:220]
                        break
                    # ② 손절가 기통과 검사 - 이미 손절 레벨을 지난 트레이드는 죽은 것.
                    if stop is not None:
                        try:
                            _pxn = float(b.current_market_price(_con) or 0)
                        except Exception:
                            _pxn = 0.0
                        if _pxn and ((_is_long and _pxn <= float(stop))
                                     or ((not _is_long) and _pxn >= float(stop))):
                            _last_why = f"손절가 기통과(현재가 {_pxn} vs 손절 {stop}) — 재진입 포기"
                            self.log(f"   🛑 {_sym} {_last_why}")
                            break
                    _tag = f"{_base_tag}-r{_try}"                     # tid 멱등 우회
                    self.log(f"   ↻ {_sym} 미체결 감지({_last_why}) — 잔재 정리 후 재진입 "
                             f"{_try}회차 (예산 {int(_budget_s - (_t.time() - _t0))}초 남음)")
                    self._send_ev("entry_attempt", asset, leg=_sym, try_n=_try + 1)
                    try:
                        b.close_contract(_aid, _con)   # 고아 주문 취소+잔재 정리(Flatten 기반)
                    except Exception as _cx:
                        self.log(f"   ⚠ {_sym} 잔재 정리 실패(계속): {_cx}")
                    _t.sleep(1.5)
                try:
                    if _use_limit:
                        res = self._exec_entry_limit_fut(b, _aid, _con, direction, _qty,
                                                         stop, dict(_polf), live, _tag)
                    else:
                        res = b.place_entry(account_id=_aid, contract_id=_con, side=direction,
                                            size=_qty, order_type=2, stop_loss_price=stop,
                                            custom_tag=_tag, dry_run=not live)
                except Exception as _ee:
                    _last_why = str(_ee)[:160]
                    self.log(f"   ❌ {_sym} 진입 예외: {_ee}")
                    if _is_nt8 and live:
                        _net_last = self._net_probe()
                        self.log(f"   🌐 망 점검: {_net_last}")
                    self._report_error(f"entry:{asset}", _ee)
                    _try += 1
                    continue
                if res.get("skipped"):
                    self.log(f"   ⏭ {_sym} 정책 스킵({res.get('note') or '불리 이동'}) — "
                             "재시도, 경보 대상 아님.")
                    _placed = None; break
                if res.get("error"):
                    _last_why = str(res.get("error"))[:160]
                    self.log(f"   ❌ {_sym} 진입 거절: {_last_why}")
                    _try += 1
                    continue
                if not live:
                    self.log(f"   DRY-RUN {_sym} entry: {res.get('would_place')}")
                    if res.get("would_place_stop"):
                        self.log(f"   DRY-RUN {_sym} stop:  {res.get('would_place_stop')}")
                    _placed = True; break
                self.log(f"   📨 {_sym} 진입 접수 ×{_qty} (주문 {res.get('order_id') or _tag})")
                # ── 확인 창(대표 "체결 확인 안 된 게 왜인지"): NT8=6초(0.5×12, 애드온 push
                # 1초 주기 감안), 타 브로커=2초(기존). 포지션 잡히면 확정, NT8은 주문 상태로
                # 거절을 조기 감지해 **사유까지** 확보(신 애드온 orders 스냅샷).
                _seen_qty = 0; _rej = ""
                _rounds = 30 if _is_nt8 else 4   # NT8 15초(2026-09-08: 6초 창에 체결 확인 뒤 재진입 → 이중 진입 위험)
                for _i in range(_rounds):
                    try:
                        _seen_qty = abs(int(b.position_qty(_aid, _con) or 0))
                    except Exception:
                        _seen_qty = 0
                    if _seen_qty:
                        break
                    if _is_nt8:
                        try:
                            _os = b.order_status(_aid, _tag) or {}
                            _en0 = _os.get("entry") or {}
                            if str(_en0.get("state")) in ("Rejected", "Cancelled"):
                                _rej = str(_en0.get("reason") or _en0.get("state"))[:160]
                                break
                        except Exception:
                            pass
                    _t.sleep(0.5)
                if _seen_qty:
                    _placed = True; _res_ok = res
                    self.log(f"   ✅ {_sym} 체결 확인 ×{_seen_qty}")
                    break
                _last_why = _rej or f"포지션 미확인({'15' if _is_nt8 else '2'}초)"
                if not _is_nt8:
                    # 타 브로커: 기존 동작 유지(주문은 살아 있을 수 있음 - 보고만 제외)
                    _placed = False; _res_ok = res
                    break
                # 실패한 시도마다 망 상태를 실측해 남긴다(대표 "인터넷 불안정도 로깅") -
                # 미체결의 '왜'에 망 증거를 붙여 서버 보고까지 동행시킨다.
                _net_last = self._net_probe()
                self.log(f"   🌐 망 점검: {_net_last}")
                _try += 1
            if _placed is None:
                continue                                   # 정책 스킵 - 다음 leg
            if live and not _placed:
                if _is_nt8:
                    _whyf = (_last_why or "사유 미상") + (f" | {_net_last}" if _net_last else "")
                    self.log(f"   🚨 {_sym} 진입 실패 — {_whyf} "
                             f"({_try}회 시도, 계좌 [{sc}])")
                    self._report_error(f"entry:{asset}", f"{_sym} {sc} {_whyf} "
                                       f"({_try}tries)")
                    self._member_alert(
                        "entry_miss",
                        f"⚠️ {asset} 진입 실패 — 계좌 {sc} {_sym} {_try}회 시도 후 미체결. "
                        f"사유: {_last_why or '미상'}. 브로커 화면을 확인해 주세요.",
                        f"⚠️ {asset} entry failed — account {sc} {_sym} unfilled after "
                        f"{_try} attempts. Reason: {_last_why or 'unknown'}. "
                        "Please check your broker.")
                else:
                    self.log(f"   ⚠ {_sym} 주문은 접수됐으나 포지션이 확인되지 않아 "
                             f"보고에서 제외합니다(계좌를 직접 확인하십시오)")
                    # 기존 동작: 갭·오픈추적·원장은 진행(주문이 살아있을 수 있음)
                    self._send_gap(asset, sig, b, _con, mkt_at_send=_px_at_send)
                    self._remember_open(asset, b, _con)
                    _ledger_add(asset, _con, direction, _tag)
                continue
            if not live:
                _ok += 1; continue
            res = _res_ok or {}
            # ── 확정 체결 보고 경로(위 확인 루프에서 포지션 실확인된 계좌만) ──
            # 대시보드 보고 누적(#53): 미니/마이크로 혼합 → 마이크로 환산(미니 1=마이크로 10).
            self._note_fill(asset, micro=float(_qty) * (1 if str(_sym).upper().startswith("M") else 10))
            self._send_gap(asset, sig, b, _con,
                           mkt_at_send=_px_at_send)      # 체결 갭+협의 슬리피지 실측(21l)
            self._remember_open(asset, b, _con)          # 손절 청산 감지용(2026-09-02)
            _ledger_add(asset, _con, direction, _tag)      # 실제 사용 태그(-rN 포함)로 기록
            # 🛡 판정(대표 2026-09-04): ack의 stop_order_id 또는 NT8 주문 스냅샷의 스탑
            # 존재로 - 오늘 사고에선 정상 계좌도 ack 필드가 비어 🛡 라인이 누락됐었다.
            _stop_seen = bool(res.get("stop"))
            if not _stop_seen and _is_nt8:
                try:
                    _os2 = b.order_status(_aid, _tag) or {}
                    _stop_seen = bool(_os2.get("stop"))
                except Exception:
                    pass
            if _stop_seen:
                _sp = res.get("stop_price")
                _adj = (f" (틱 정렬 {stop} → {_sp:g})"
                        if _sp is not None and float(_sp) != float(stop) else "")
                self.log(f"   🛡 {_sym} 보호 손절 거치 @{(_sp if _sp is not None else stop)}{_adj}")
            elif res.get("stop_error"):
                self._handle_stop_failure(b, _aid, _con, direction, _qty, stop, res)
            elif stop is not None:
                _why = f" [{res.get('stop_unconfirmed')}]" if res.get("stop_unconfirmed") else ""
                self.log(f"   ⚠ {_sym} 손절 거치 확인 안 됨{_why} — 브로커 화면에서 스탑 존재를 "
                         "확인하십시오(다음 상태 push에서 재확인됩니다).")
            _ok += 1
        if live and _ok:
            self._entered_at = _mark_entered(asset)
            try:
                _tot = f"{_dtl.datetime.now().timestamp() - float(pub):.1f}s" if pub else "?"
            except (TypeError, ValueError):
                _tot = "?"
            self.log(f"   ⏱ 진입 완료({_ok}/{len(resolved)} leg), 발송 후 {_tot}")

    _HB_BROKER_KEY = {"projectx": "topstep", "nt8": "lucid", "tradovate": "tradovate",
                      "ibkr": "ibkr", "bybit": "bybit", "bitget": "bitget"}

    def _broker_allowed(self, broker: str) -> bool:
        """서버(admin) '브로커별 지원' 토글 반영 — 하트비트 brokers에서 꺼진 브로커는 자동화 제외.
        brokers 정보가 없으면(구버전 hb 등) 허용(하위호환)."""
        gb = (self._gate or {}).get("brokers") or {}
        if not gb:
            return True
        return bool(gb.get(self._HB_BROKER_KEY.get(broker, broker), False))

    def _watch_loop(self):
        """표시 전용 신호 피드(2026-08-28 R14 P0). **주문은 절대 내지 않는다.**

        ⚠️이게 없어서 오픈 후 Preview 퍼널이 끊겨 있었다: 서버는 30분 지연 피드를 큐에
        넣어 발행하는데(signals/autopilot_feed._queue_preview) 앱은 자동 진입 권한이
        있을 때만 도는 _sig_loop에서만 그 피드를 읽어, "브로커를 연결하면 60분이 30분이
        된다"는 등급표, 약관의 약속을 받을 경로가 아예 없었다. 그 파일 주석도
        "표시용 소비는 다음 앱 빌드가 얹는다"고 적어두고 있었다 - 그게 이 루프다.

        _sig_on(매매 루프)이 도는 동안에는 쉰다 - 같은 신호를 두 번 로그하지 않기 위해서다.
        진입, 손절, 청산 코드를 일절 부르지 않으므로 이 루프로는 주문이 나갈 수 없다."""
        import time as _tw
        import requests
        import autopilot_crypto
        _last = None
        while True:
            try:
                if not (self._token or "").strip():
                    _tw.sleep(_WATCH_POLL_SECS); continue
                if self._sig_on:
                    # 매매 루프가 도는 동안에는 로그하지 않는다. 다만 **마지막 id는 따라간다**
                    # (2026-08-28 리뷰 P2): 안 그러면 전체 정지 직후 이미 매매 루프가 적은
                    # 신호를 이 루프가 '새 신호'로 한 번 더 적는다.
                    try:
                        _r0 = requests.get(_feed_url(self._token),
                                           params={"t": int(_tw.time())}, timeout=8)
                        _s0 = autopilot_crypto.decrypt(self._token, _r0.text) if _r0.ok else {}
                        if _s0.get("id"):
                            _last = _s0["id"]
                    except Exception:
                        pass
                    _tw.sleep(_WATCH_POLL_SECS); continue
                r = requests.get(_feed_url(self._token),
                                 params={"t": int(_tw.time())}, timeout=8)
                sig = autopilot_crypto.decrypt(self._token, r.text) if r.ok else {}
                sid = sig.get("id")
                if sid and sid != _last:
                    _last = sid
                    self.root.after(0, lambda g=sig: self._log_watch_signal(g))
            except Exception:
                pass
            _tw.sleep(_WATCH_POLL_SECS)

    def _note_exit_ts(self, sig):
        """서버가 보낸 계획 청산 시각(exit_ts)을 기억한다. 없으면 아무것도 안 한다.

        평소엔 서버 값 = 앱이 자체 계산하는 고정 시각이라 결과가 같다. 다른 날은 하나뿐:
        고영향 지표 발표에 걸려 서버가 **앞당겨** 보낸 날(FOMC 등). 그때만 _auto_loop이
        이 시각에 청산한다. 파싱 실패·과거 시각은 조용히 무시(기존 고정 시각 유지) -
        청산은 절대 이 필드 때문에 죽으면 안 된다."""
        try:
            asset = str((sig or {}).get("instrument") or "").upper()
            ts = (sig or {}).get("exit_ts")
            if not asset or ts is None:
                return
            ts = float(ts)
            import time as _te
            if ts < _te.time() - 3600:          # 한참 지난 값은 무시
                return
            why = str((sig or {}).get("exit_early") or "")
            prev = (self._exit_override or {}).get(asset)
            self._exit_override[asset] = (ts, why)
            if why and (not prev or prev[0] != ts):
                import datetime as _dx
                _lt = _dx.datetime.fromtimestamp(ts).strftime("%H:%M")
                self.log(f"\n⏰ {asset} " + (
                    f"청산을 {_lt}(현지)로 앞당깁니다 — {why}" if self.lang == "ko"
                    else f"exit moved earlier to {_lt} (local) — {why}"))
        except Exception:
            pass

    def _log_watch_signal(self, sig):
        """표시 전용 신호 한 건을 로그에 적는다(메인 스레드). 주문 코드 없음."""
        import datetime as _dw
        _ko = self.lang == "ko"
        _inst = sig.get("instrument") or "?"
        _dir = str(sig.get("direction") or "").upper()
        _tr = bool(sig.get("tradeable") and _dir)
        # ⚠️지연 기준은 barclose_ts(봉마감 정시)다(2026-08-28 리뷰 P1). published_at은
        # 지연 발행 시점에 서버가 새로 찍으므로, 그걸로 재면 30분 지연 신호가 항상
        # "(지연 0분)"으로 찍힌다 - 회원이 받기로 한 값과 화면이 정반대를 말한다.
        # barclose_ts는 이 앱의 기존 지연 표시 관례와도 같은 기준이다.
        try:
            _bc = float(sig.get("barclose_ts") or 0) or float(sig.get("published_at") or 0)
            _lag = int((_dw.datetime.now().timestamp() - _bc) // 60) if _bc else None
        except (TypeError, ValueError):
            _lag = None
        _tail = (f" (지연 {_lag}분)" if _ko else f" ({_lag} min delay)") if _lag is not None else ""
        if _tr:
            _side = ("롱" if _dir == "LONG" else "숏") if _ko else _dir.lower()
            self.log(f"\n📩 {_inst} {_side}" + (f" 신호 수신{_tail}" if _ko else
                                                f" signal received{_tail}"))
            # ⚠️키 이름(2026-08-28 리뷰 P1): 피드 페이로드는 entry_ref/stop_price다.
            # entry/stop을 읽던 종전 코드는 **영원히 None**이라 진입가·손절가가 한 번도
            # 안 찍혔다 - 표시 전용 피드의 존재 이유가 바로 그 두 값인데.
            _e, _s = sig.get("entry_ref"), sig.get("stop_price")
            if _e is not None:
                self.log((f"   진입 {_e}" if _ko else f"   entry {_e}")
                         + ((f" ,  손절 {_s}" if _ko else f" ,  stop {_s}")
                            if _s is not None else ""))
        else:
            self.log(f"\n📭 {_inst} " + (f"거래 없음{_tail}" if _ko else f"no trade{_tail}"))
        if not (self._sig_accts or self._auto_accts):
            self.log("   " + ("표시 전용입니다 - 자동 실행은 라이브를 시작해야 돕니다."
                              if _ko else
                              "Display only - start live for automated execution."))

    def _watch_start(self):
        if getattr(self, "_watch_on", False):
            return
        self._watch_on = True
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def _member_alert(self, kind: str, ko: str, en: str):
        """회원 본인에게 즉시 알림(2026-08-28 R16 P0). 서버가 텔레그램/디스코드로 보낸다.

        ⚠️왜 필요한가: 사고가 나는 순간 회원은 대개 자리에 없다. 앱 로그 한 줄은 아무도
        안 읽는다. 특히 **손절이 안 걸린 채 살아 있는 포지션**은 제품 페이지가
        "손절 없는 포지션은 존재할 수 없습니다"라고 단정하는 바로 그 상태의 반례라,
        조용히 지나가면 안 된다. 앱이 죽었을 때 DM을 보내는 경로는 이미 있는데
        정작 더 위험한 이 상태에는 안 붙어 있었다(서버 헬퍼는 있고 호출부가 0이었다).
        백그라운드 전송이라 매매 경로를 막지 않고, 실패해도 무해하다(로그는 남는다)."""
        def _bg():
            try:
                import requests as _rq
                _rq.post(PUSH_BASE + "eqalert", timeout=8,
                         json={"t": self._token, "kind": str(kind)[:24],
                               "ko": str(ko)[:600], "en": str(en)[:600]})
            except Exception:
                pass
        try:
            if (self._token or "").strip():
                threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            pass

    def _hb_loop(self):
        """상시 생존 핑(4분). ⚠️2026-08-28 R14 P0: 주기 핑이 _sig_loop 안에만 있었다.
        오픈 후 Operator는 신호 대기 권한이 없어 _sig_loop 자체가 안 뜨므로 **핑이 0회**가
        되고, 웹은 15분 뒤부터 영구 '응답 없음'을 찍는데 앱 다운 알림은 armed=False라
        영영 안 나간다 - 랜딩과 가이드가 '응답이 멈추면 15분 이내 알림'을 약속하는데
        정작 자동 청산만 쓰는 유료 등급에서 그 약속이 깨졌다. 루프와 무관하게 앱이 떠
        있는 동안 돈다. armed는 실제 무장 상태(신호 대기 + 자동 청산)를 따라간다."""
        import time as _th
        while True:
            try:
                if (self._token or "").strip():
                    self._alive_ping(armed=bool(self._sig_accts or self._auto_accts))
            except Exception:
                pass
            # 손절 청산 감지(2026-09-02) - 열린 포지션이 있을 때만 브로커를 읽는다.
            # 하트비트에 얹는 이유: 앱이 떠 있는 동안 항상 도는 유일한 주기 루프이고,
            # 60초면 손절 뒤 최대 2분 안에 서버 버킷이 닫힌다.
            try:
                self._check_stop_closed()
            except Exception:
                pass
            _th.sleep(60)          # _alive_ping이 240초 스로틀을 갖고 있다

    def _warn_kc_failed(self):
        """평문으로 남은 비밀이 있으면 기동 시 1회 경고(2026-08-28).
        ⚠️문구 주의(리뷰 P1): 회원에게 f1/f3 같은 내부 필드 ID를 보여주면 안 되고,
        저장소 이름도 OS를 따라가야 한다(이 실패가 가장 잘 나는 쪽이 Windows인데
        '키체인을 열라'고 하면 없는 것을 열라는 말이 된다). 해결책도 사실이어야 한다 -
        '다시 켜면 자동으로 옮겨진다'는 코드에 없는 동작이었다. 실제 경로는
        '보안 저장소를 쓸 수 있게 만든 뒤 그 값을 다시 입력하고 저장'이다."""
        _f = list(getattr(self, "_kc_failed", []) or [])
        if not _f or getattr(self, "_kc_warned", False):
            return                       # 하트비트는 주기적이라 1회 가드가 필요하다
        self._kc_warned = True
        self._kc_warn_now(_f)

    def _kc_label(self) -> str:
        """이 OS의 보안 저장소 이름(회원이 실제로 찾을 수 있는 이름)."""
        if _IS_MAC:
            return "키체인" if self.lang == "ko" else "Keychain"
        if os.name == "nt":
            return "자격 증명 관리자" if self.lang == "ko" else "Credential Manager"
        return "OS 보안 저장소" if self.lang == "ko" else "the OS secret store"

    def _kc_pretty(self, items) -> str:
        """'BTC/bitget/f3' → '비트코인 (BTC) - Bitget (USDT-F)의 Passphrase'.
        회원은 f1/f3가 뭔지 모른다 - 브로커 화면에 적힌 이름으로 되돌려 준다."""
        _ko = self.lang == "ko"
        out = []
        for it in items:
            try:
                _a, _b, _f = str(it).split("/")
            except ValueError:
                out.append(str(it)); continue
            _lbl = (_BROKER_SPEC.get(_b) or {}).get(_f) or _f
            _an = (_ASSET_LABEL.get(_a) or {}).get("ko" if _ko else "en", _a)
            out.append(f"{_an} - {_broker_label(_b)}의 {_lbl}" if _ko
                       else f"{_an} - {_broker_label(_b)} {_lbl}")
        return ", ".join(out)

    def _kc_warn_now(self, items):
        """평문 잔존 경고 1건(로그 + 팝업). 저장 직후에도, 기동 시에도 같은 문구."""
        _ko = self.lang == "ko"
        _store = self._kc_label()
        _what = self._kc_pretty(items)
        self.log("⚠ " + ((f"{_store}에 저장하지 못해 설정 파일에 평문으로 남은 값이 "
                          f"있습니다: {_what}") if _ko else
                         (f"Could not store these in {_store}, so they stay in plain text "
                          f"in the config file: {_what}")))
        try:
            messagebox.showwarning(
                "EQ Autopilot",
                ((f"이 컴퓨터의 {_store}에 저장하지 못했습니다.\n"
                  f"평문으로 남은 값: {_what}\n\n"
                  f"{_store}를 쓸 수 있게 만든 뒤(잠금 해제, 원격 세션이면 로그인 세션에서 "
                  f"실행) 그 값을 다시 입력하고 저장하면 옮겨집니다.\n"
                  "그 전까지는 전체 디스크 암호화를 켜고, 공용 또는 무인 컴퓨터에서는 "
                  "실행하지 마세요.")
                 if _ko else
                 (f"Could not save to {_store} on this computer.\n"
                  f"Left in plain text: {_what}\n\n"
                  f"Make {_store} available (unlock it; in a remote session run inside a "
                  f"login session), then re-enter and save those values to move them.\n"
                  "Until then, turn on full-disk encryption and do not run on a shared or "
                  "unattended machine.")))
        except Exception:
            pass

    def _hb_start(self):
        """토큰이 생기면 한 번만 띄운다(데몬이라 종료를 막지 않는다)."""
        if getattr(self, "_hb_on", False):
            return
        self._hb_on = True
        threading.Thread(target=self._hb_loop, daemon=True).start()

    def _armed_assets(self):
        """지금 무장(신호 대기) 중인 자산 목록. _sig_accts(자산,계좌) 키의 자산만 뽑는다.
        비어 있으면 앱이 떠 있어도 '실행 대기 아님' - 회원 화면은 이걸 꺼짐으로 표시한다."""
        # ⚠️자동 청산 무장도 센다(2026-08-28 리뷰 P1). Operator 등급은 신호 대기 권한이
        # 없어 _auto_accts에만 들어가는데, _sig_accts만 보면 멀쩡히 자동 청산이 도는
        # 회원의 서버 칩이 'off'로 뜨고, 앱 다운 감시도 '무장 아님'으로 분류해 앱이
        # 죽어도 알림이 안 간다. 회원 입장에서 자동 청산은 엄연히 가동 중이다.
        try:
            return sorted({str(k[0])
                           for d in (getattr(self, "_sig_accts", None) or {},
                                     getattr(self, "_auto_accts", None) or {})
                           for k in d})
        except Exception:
            return []

    def _included_assets(self):
        """실행 자산 체크가 켜진 자산(2026-08-28 리뷰 P1). 서버가 "브로커는 붙였는데
        무장 안 됨" 경고를 띄울 때 이 목록이 없으면, 일부러 안 돌리는 자산까지 상시
        경고 대상이 된다 - 정상 구성을 문제로 부르는 화면은 곧 무시된다."""
        try:
            return [a for a in _ASSETS if (self._acfg.get(a) or {}).get("include", True)]
        except Exception:
            return []

    def _connected_assets(self):
        """브로커 자격이 실제로 입력된 자산 목록(["NQ","GC","BTC"] 부분집합).
        서버는 이것만 보고 3자산 연결 여부를 판정한다 - 키, 계좌번호는 보내지 않는다."""
        out = []
        try:
            for _a in _ASSETS:
                _c = (self._acfg.get(_a) or {})
                _b = _c.get("broker")
                _cr = ((_c.get("creds") or {}).get(_b) or {}) if _b else {}
                if _b and any(str(v or "").strip() for v in _cr.values()):
                    out.append(_a)
        except Exception:
            return []
        return out

    def _asset_brokers(self):
        """자산→브로커명 매핑(대표 2026-09-03 로그 페이지 '브로커 상태'). 자격이 실제로
        입력된 자산만. 브로커 키 이름뿐 - 자격 정보는 절대 보내지 않는다.

        ⚠️한 자산을 **여러 브로커에 나눠 건** 구성(가이드가 권장하는 Lucid+Tradovate 한 NT8)은
        이 한 겹 딕셔너리로 표현되지 않아, 웹 '연결 자산' 칸이 브로커 하나만 말했다 - 어느
        연결이 죽었는지 회원이 알 수 없었다(2026-09-16 R34 P2-#15). 값은 종전대로 대표 브로커
        하나를 유지하고(구버전 서버·구버전 화면 호환), 전체 목록은 _asset_brokers_multi가
        따로 싣는다."""
        out = {}
        try:
            for _a in _ASSETS:
                _c = (self._acfg.get(_a) or {})
                _b = _c.get("broker")
                _cr = ((_c.get("creds") or {}).get(_b) or {}) if _b else {}
                if _b and any(str(v or "").strip() for v in _cr.values()):
                    out[_a] = str(_b)[:16]
        except Exception:
            return {}
        return out

    def _asset_brokers_multi(self):
        """자산→브로커 **목록**(2026-09-16 R34 P2-#15). 계좌 행마다 브로커가 따로이므로
        켜진 계좌들의 브로커를 모은다. 자격이 없는 브로커는 넣지 않는다(연결이 아니다).
        브로커 키 이름뿐 - 자격 정보는 절대 보내지 않는다."""
        out = {}
        try:
            for _a in _ASSETS:
                _bs = []
                for _ac in self._accts_of(_a):
                    if not _ac.get("on"):
                        continue
                    _b = self._acct_broker(_a, _ac)
                    _cr = self._creds_of(_a, _b) or {}
                    if _b and any(str(v or "").strip() for v in _cr.values()) and _b not in _bs:
                        _bs.append(str(_b)[:16])
                if _bs:
                    out[_a] = _bs[:4]
        except Exception:
            return {}
        return out

    def _alive_ping(self, armed: bool = True, force: bool = False, sync: bool = False):
        """생존 핑(/eqalive, 대표 2026-08-17 '앱 다운 알림은 해') - 무장 중 4분마다.
        서버는 15분 무소식(핑 3회 결번)이면 회원에게 '앱이 죽었다'를 DM한다.
        [전체 정지]만 armed=False로 경보 대상에서 빠진다 - **무장 중 창을 닫으면 경보가
        맞다**(자동화가 실제로 멈추니까, 크래시·정전과 회원 입장에선 같은 상태).
        발송 경로에 부하를 주지 않게 백그라운드 스레드 + 실패 무해(다음 핑이 복구)."""
        import time as _t
        # ⚠️**무장 상태가 바뀌면 스로틀을 무조건 통과한다**(대표 2026-08-28 "라이브 눌렀었어
        # 근데 안 되던거야"). 종전에는 240초 스로틀만 있어, 라이브 시작으로 자산을 무장해도
        # 서버가 최대 4분간 옛 목록을 들고 있었다. 대표는 누르고 홈피를 봤는데 GC·BTC가
        # 없으니 "안 된다"고 판단할 수밖에 없었다 - 실제로는 무장이 됐는데 화면만 늦었다.
        # 경로마다 force=True를 붙이는 방식은 한 곳만 빠뜨려도 같은 일이 나므로,
        # **상태가 바뀌었다는 사실 자체**를 통과 조건으로 삼는다. 앞으로 어느 경로가
        # 무장을 바꾸든 즉시 보고된다.
        try:
            # ⚠️첫 원소는 **실제 상태**여야 한다(2026-08-28 리뷰 P2, 실측으로 확인).
            # 인자 armed를 쓰면 _sig_loop(항상 True)과 _hb_loop(실제 상태)이 서로 다른
            # 서명을 만들어 번갈아 "변했다"로 판정하고, 결국 **양쪽 다 4분 스로틀을
            # 통과해 1~3초마다 핑을 쏜다**(대표 기기 3대에서 실측: 마지막 보고 1~3초 전).
            # _bg가 전송 직전에 다시 읽는 값과도 같은 기준이어야 서명이 뜻을 갖는다.
            _sig = (bool(self._sig_accts or self._auto_accts),
                    tuple(self._armed_assets()), tuple(self._connected_assets()),
                    tuple(self._included_assets()))
        except Exception:
            _sig = None
        _changed = _sig is not None and _sig != getattr(self, "_alive_sig", None)
        if not (force or _changed) and _t.time() - getattr(self, "_alive_at", 0.0) < 240:
            return
        # ⚠️성공했을 때만 기억한다(2026-08-28 리뷰 P1). 종전에는 POST **전에** _alive_at과
        # _alive_sig를 갱신해, 전송이 실패해도 "그 상태를 보냈다"로 기억했다. 그러면
        # 다음 변화가 있을 때까지 서버가 옛 값을 들고 있고, j에서 고친 4분 스테일이
        # 그대로 재발한다. 실패는 다음 호출이 다시 시도해야 한다.
        self._alive_at = _t.time()      # 스로틀 기준은 시도 시각(도배 방지가 목적)

        def _bg():
            # ⚠️핑은 **한 번에 하나씩**(대표 2026-08-28 "라이브 눌렀었어 근데 안되던거야").
            # 2026-08-28 v2026.08.28f에서 상시 하트비트(_hb_loop)가 생기면서 핑 경로가
            # 둘이 됐다. 둘 다 백그라운드로 POST하는데, 하트비트가 무장 **직전** 상태를
            # 들고 이미 전송 중이면 라이브 시작의 즉시 핑보다 **늦게 도착해 덮어쓴다** -
            # 회원은 눌렀는데 화면이 안 바뀌는 것을 본다(다음 주기에 저절로 맞춰지지만,
            # 그 몇 분 동안 할 수 있는 판단은 "안 된다"뿐이다).
            # 락 안에서 페이로드를 **그 순간 다시 읽어** 만들면 늦게 도착한 핑도 최신값을
            # 싣는다 - 순서가 뒤집혀도 내용은 안 뒤집힌다.
            with self._ping_lock:
                try:
                    import requests as _rq      # 모듈 레벨에 requests 없음 - 지역 임포트 필수
                    # 종료 경로(sync)는 짧게 - 메인스레드가 락 대기+POST를 동기로 하므로
                    # 불통 네트워크에서 8초 타임아웃은 최악 16초 동결을 만들었다(R20 P2-5).
                    _ok = _rq.post(PUSH_BASE + "eqalive", timeout=(3 if sync else 8),
                             json={"t": self._token,
                                   "armed": bool(self._sig_accts or self._auto_accts),
                                   "v": self._APP_VER,
                                   "m": _machine_id(),
                                   # 연결된 자산 목록(2026-08-27 대표 지시): 세 자산을 모두
                                   # 연결하면 Autopilot 14일 체험 버튼이 회원 화면에서 바로
                                   # 열리도록, 서버가 '무엇이 연결됐는지'만 알게 한다.
                                   # ⚠️자격 정보는 절대 보내지 않는다 - 자산 심볼뿐이다.
                                   "a": self._connected_assets(),
                                   # 무장 중인 자산(2026-08-28 대표 "무장 안 하면 꺼진 걸로,
                                   # 자산별로"): 앱이 떠 있어도 무장 자산이 없으면 회원 화면은
                                   # '꺼짐'으로 보여야 한다. 신호 대기와 자동 청산
                                   # 둘 다 무장으로 센다(리뷰 P1 - Operator는 자동 청산만 쓴다).
                                   "arm": self._armed_assets(),
                                   # 자산별 브로커명+동의 스탬프(2026-09-03 증거 원장·로그 페이지)
                                   "bk": self._asset_brokers(),
                                   "bkl": self._asset_brokers_multi(),   # 자산별 브로커 목록(R34 P2-#15)
                                   "cv": ((self._profile or {}).get("consent") or {}).get("ver") or "",
                                   "ct": ((self._profile or {}).get("consent") or {}).get("at") or 0,
                                   # 실행 자산 체크(2026-08-28 리뷰 P1): 서버가 "붙였는데
                                   # 무장 안 됨" 경고를 띄울 때 일부러 안 돌리는 자산을
                                   # 제외하기 위한 것. 이게 없으면 정상 구성이 상시 경고가 된다.
                                   "inc": self._included_assets(),
                                   # 종료 표식(2026-08-28): 창을 닫으면 칩이 15분
                                   # 신선도 창을 기다리지 않고 즉시 꺼짐으로 바뀐다.
                                   # armed는 실상태 그대로 보낸다 - 무장 중 종료는
                                   # 다운 경보 대상이 맞기 때문이다(R14 P1).
                                   # (옛 "demo" 표식은 모의 모드 폐지(2026-09-07)로 안 싣는다 -
                                   #  서버는 키 없음 = 실무장으로 본다.)
                                   "closed": bool(getattr(self, "_closing", False))})
                    if getattr(_ok, "ok", False) and _sig is not None:
                        self._alive_sig = _sig      # **전송 성공 뒤에만** 기억한다
                except Exception:
                    pass
        # ⚠️종료 경로는 **동기 전송**(대표 2026-08-31 "앱 끄고 나서는 한참 모르네").
        # 종전엔 종료 직전 핑도 백그라운드 스레드 + 0.35초 대기였는데, 집 네트워크의
        # TLS 왕복이 그보다 길면 프로세스가 먼저 죽어 closed 마커가 유실됐다 - 그러면
        # 칩은 15분 신선도 창이 다 마를 때까지 켜진 것으로 남는다. 켤 때 빠른 이유는
        # 앱이 살아 있어 스레드가 끝까지 가기 때문이다. 종료는 몇 초 기다려도 된다.
        if sync:
            _bg()
        else:
            threading.Thread(target=_bg, daemon=True).start()

    def _live_now(self, live_flag: bool) -> bool:
        """발주 '순간'의 실거래 여부 = 시작 플래그 AND 현재 게이트의 라이브 잠금 아님.
        어드민이 라이브 잠금(force_dry_run)을 켜면(하트비트 ≤5분 반영) 이미 돌던 루프도 다음
        발주부터 주문을 보류한다 — 시작 때 캡처한 값만 믿으면 킬스위치가 기존 루프에 안 먹는 구멍.
        (회원용 모의 모드는 2026-09-07 폐지 - 이 함수가 유일한 실주문 억제 경로다.)"""
        return bool(live_flag) and not (self._gate or {}).get("force_dry_run", True)

    def _fetch_balance_diag(self, bk, f1, f2, f3, aid, is_fut):
        """잔고 직접 조회 + 실패 원인 표면화. 반환 (bal|None, err|None).
        (구 _acct_balance 캐시는 자본% 폐지와 함께 제거) 0·저잔고도 그대로 반환. 크립토 None이면
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
        """서버 신호 없이 지정 자산들의 전 계좌 잔고, 1R을 조회해 팝업+로그로 보여준다.
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
                        self.log(f"   ⚠ [{asset}, {lbl}] 잔고 조회 실패 — {err}")
                    _pr = ac.get("prop") or {}
                    _pc = ac.get("pct") or {}
                    r = None
                    if _pr.get("on"):                              # 프롭(Fast-Payout 체제)
                        mode = ("프롭" if ko else "Prop")
                        if _pr.get("type") == "funded":
                            rs = _as_float(_pr.get("r_steady"), 300.0)
                            _pcv = max(0, min(5, int(_as_float(_pr.get("payouts"), 0))))
                            if _pcv >= 5:
                                note = ("5발 완료 — 라이브 전환 대상, 진입 안 함" if ko
                                        else "5/5 — Live-transition candidate, no entry")
                            else:
                                r = rs
                                _sh = _pcv < 2
                                note = ((f"펀디드 {_pcv}/5발, " + ("방패기($12k→$6k 출금)" if _sh
                                                                    else "Fast-Payout(자격 즉시 절반)")) if ko
                                        else (f"funded {_pcv}/5, " + ("shield ($12k→$6k)" if _sh
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
                    self.log(f"   ⚙ [{asset}, {lbl}] 잔고 {balstr}, {mode} 1R {rstr} ({note})")
                    rows.append(f"   [{lbl}] {mode}, {'잔고' if ko else 'Bal'} {balstr}  |  1R {rstr}")
                blocks.append("\n".join(rows))
            msg = "\n\n".join(blocks) if blocks else ("계좌 없음" if ko else "no accounts")
            self.root.after(0, lambda m=msg, t=title: messagebox.showinfo(t, m))
        _th.Thread(target=w, daemon=True).start()

    def _preview_one_r(self, asset):
        """그 자산 전 계좌 잔고, 1R 미리보기(자산 탭 버튼)."""
        ko = self.lang == "ko"
        self._run_1r_preview([asset], (f"{asset}, 잔고 & 1R" if ko else f"{asset}, Balance & 1R"))

    def _preview_one_r_all(self):
        """전 자산 전 계좌 잔고, 1R 미리보기(라이브 패널 버튼, 대표 2026-07-26)."""
        ko = self.lang == "ko"
        self._run_1r_preview(list(_ASSETS), ("전 자산, 잔고 & 1R" if ko else "All assets, Balance & 1R"))

    def _close_all_positions(self):
        """패닉 버튼(대표 2026-07-27): 크레덴셜이 설정된 모든 자산, 계좌의 포지션을 시장가로
        전부 청산(flatten_all = 포지션 청산 + 잔여 주문 취소). 무장 상태와 무관하게 동작 —
        비상시 무조건 눌러서 정리하는 용도. (브로커,계좌) 단위 중복 제거(NQ, GC 공유 계좌 1회만)."""
        ko = self.lang == "ko"
        if not messagebox.askyesno(
                "전체 청산" if ko else "Close all",
                ("모든 자산, 모든 계좌의 열린 포지션을 지금 시장가로 전부 청산하고 잔여 주문을 "
                 "취소합니다.\n\n진행할까요?" if ko else
                 "Close every open position on every configured account at market and cancel "
                 "remaining orders.\n\nProceed?")):
            return
        self.log("\n🧹 " + ("전체 청산 — 모든 자산, 계좌 flatten" if ko else "Close all — flatten every account"))

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
                    lbl = f"{a}, {aid[-4:] if aid else _broker_label(bk)}"
                    try:
                        b = _build_broker(bk, f1, f2, f3, [aid] if aid else [])
                        res = b.flatten_all(dry_run=False)
                        closed = ", ".join(f"{p.symbol}" for p in (res.closed or [])) or "—"
                        errs = getattr(res, "errors", None) or []
                        n_closed += len(res.closed or [])
                        self.log(f"   [{lbl}] closed: {closed}"
                                 + (f", ⚠ {'; '.join(str(e)[:60] for e in errs)}" if errs else ""))
                    except AttributeError:
                        self.log(f"   ⏭ [{lbl}] 이 브로커는 일괄 청산 미지원 — 건너뜀")
                    except Exception as e:
                        self.log(f"   ❌ [{lbl}] 청산 실패: {str(e)[:100]}")
                        self._report_error("close_all", e)
            self.log("   ✅ " + (f"전체 청산 완료 — 포지션 {n_closed}개 정리" if ko
                                else f"Close-all done — {n_closed} positions flattened"))
        import threading as _th
        _th.Thread(target=w, daemon=True).start()

    def _bump_payout(self, broker, aid, lbl):
        """출금 완료 기록(+1) — 같은 (브로커, 계좌ID)를 쓰는 전 자산의 계좌(NQ, GC 공유) +
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
        """출금 **알림**(권유 아님) + '예'면 출금 횟수 +1 기록. 워커 스레드에서 호출.
        ⚖️ 2026-08-13: 이 팝업은 회원이 설정한 문턱 도달 사실만 알린다. 금액 산출·처분
        권유는 하지 않으며, 횟수 증가는 항상 회원 확인을 거친다(횟수는 설정에서 직접
        편집도 가능 — 숫자의 주인은 회원이다)."""
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
        if pr.get("type") == "live":
            # 라이브 초기(Lucid): $0 시작 최취약 구간 - 가장 얇게. +$4,500 락 확보 후엔
            # 프롭 모드를 끄고 '잔고 %' 모드 2%(절반 수확·절반 성장)로 전환 권장(정본 e3d03c3).
            r = _as_float(pr.get("r_live"), 100.0)
            self.log(f"   ⚙ [{lbl}] 라이브 초기 1R=${r:g} (락 확보 후 잔고 2% 모드 권장)")
            return r
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
                     f"새 계정, 새 챌린지로 교체하세요)")
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
            _stage = ((f"펀디드 {pcnt}/5발, " + ("방패기" if _shield else "Fast-Payout기"))
                      if _ko else (f"funded {pcnt}/5, " + ("shield" if _shield else "fast-payout")))
            self.log(f"   ⚙ [{lbl}] 프롭 {_stage} 1R=${rs:g} (잔고 ${bal:,.0f})")
            _thr = (buf + _PAYOUT_CHUNK) if _shield else 1_500.0
            if bal >= _thr:
                if _pk not in _alerted:
                    _alerted.add(_pk)
                    # ⚖️ 2026-08-13: 회원 **잔고로 개인 출금 금액을 계산해 지시하던 문안을 폐지**한다
                    # (구: "잔고 $X의 절반 ≈$Y를 바로 출금하세요"). 재산 상황을 반영해 처분을
                    # 권하는 형태라 개별 투자자문에 가장 가까운 지점이었다. 이제 팝업은
                    # ①회원이 설정한 문턱에 도달했다는 사실 ②그 설정값이 무엇인지 ③판단은 본인 몫
                    # 만 알린다 - 금액 계산·지시 없음. 프롭사 자격 요건은 프롭사 규칙의 인용이다.
                    _decide = ("\n\n출금 여부와 금액은 본인이 판단해 결정하십시오. EdgeQuant는 "
                               "회원이 설정한 알림만 전달하며 자금 처분을 권유하지 않습니다."
                               if _ko else
                               "\n\nWhether and how much to withdraw is your decision. EdgeQuant only "
                               "delivers the reminder you configured and does not advise on "
                               "disposing of funds.")
                    if _shield:
                        _msg = ((f"[{lbl}] 방패기 알림 ({pcnt + 1}번째 출금 구간).\n\n설정하신 문턱"
                                 f"(방패 ${buf:,.0f} + ${_PAYOUT_CHUNK:,.0f})에 도달했습니다. "
                                 f"프롭사 출금 자격 요건은 '$150+ 익절일 5일, 직전 출금 후 순익 "
                                 f"플러스'입니다(해당 회사 규칙).") if _ko else
                                (f"[{lbl}] Shield-phase notice (payout window #{pcnt + 1}).\n\n"
                                 f"The threshold you configured (shield ${buf:,.0f} + "
                                 f"${_PAYOUT_CHUNK:,.0f}) has been reached. The firm's stated payout "
                                 f"criteria are five $150+ winning days and net positive since the "
                                 f"last payout (their rule)."))
                    else:
                        _tail = ((" 계정 합산 5번째 출금이면 이 계정은 라이브 전환 대상이 됩니다 — 남는 "
                                  "잔고는 Live로 이월되지만 회수가 느립니다.") if pcnt == 4 else "") if _ko \
                                else ((" A 5th payout makes the login a Live-transition candidate; "
                                       "remaining balance carries into Live but recovers slowly.")
                                      if pcnt == 4 else "")
                        _msg = ((f"[{lbl}] Fast-Payout 구간 알림 ({pcnt + 1}번째 출금 구간).\n\n"
                                 f"설정하신 문턱에 도달했습니다. 계정 합산 4발째부터는 방패를 두지 않는 "
                                 f"설정이며, 프롭사 출금 자격 요건은 '$150+ 익절일 5일, 직전 출금 후 "
                                 f"순익 플러스'입니다(해당 회사 규칙).{_tail}") if _ko else
                                (f"[{lbl}] Fast-Payout notice (payout window #{pcnt + 1}).\n\n"
                                 f"The threshold you configured has been reached. From the login's "
                                 f"fourth payout your settings keep no shield. The firm's stated payout "
                                 f"criteria are five $150+ winning days and net positive since the last "
                                 f"payout (their rule).{_tail}"))
                    _msg += _decide
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

    def _watch_ticket(self, sig, asset, direction, stop):
        """Operator 등급 신호 확인 카드 - 방향, 진입가, 손절가만, 수량/계약수는 계산도 표시도 하지
        않는다(대표 2026-09-15 "Operator 등급에 앱 실시간 피드 줘" 뒤 "계약수 숨겨" - 2026-08-10
        "Operator에게 수량까지 주면 사실상 반자동이 돼 Autopilot 가치가 얇아진다" 결정과의 절충).
        주문은 내지 않는다 - 자동 진입은 Autopilot 등급 전용(perm_auto)."""
        _ko = self.lang == "ko"
        entry_ref = sig.get("entry_ref")
        _bar = "─" * 34
        self.log(f"\n👁 {_bar}")
        self.log(f"👁 신호 확인 — {asset} {direction}  (Operator - 수량은 Autopilot 등급에서만)" if _ko
                 else f"👁 Signal watch — {asset} {direction}  (Operator - quantity is Autopilot-tier only)")
        self.log(f"     {'방향' if _ko else 'Side'} : {direction}")
        self.log(f"     {'진입 참조' if _ko else 'Entry'} : {entry_ref}")
        self.log(f"     {'손절가' if _ko else 'Stop'}  : {stop}")
        self.log(("     → 직접 실행하세요. 수량은 본인 계좌 사이징으로 계산해 넣습니다(자동 발주 없음)."
                  if _ko else
                  "     → Place it yourself; size it from your own account (no auto orders)."))
        self.log(f"👁 {_bar}")

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
            # ⚖️ 법적 설계 변경(대표 2026-08-13 "앱이 편한 건 좋지만 법적 위험은 피하게"):
            # 발주 시점에 **회원 계좌 잔고를 조회해 위험 금액을 산출하던 경로를 폐지**한다.
            # 잔고(=재산 상황)를 입력값으로 금액을 정하면 한국 자본시장법상 '개별성 있는 조언'
            # (대법원 2018도4413: 재산상황 반영 여부가 기준), 독일법상 Anlageberatung의
            # 요건에 닿는다. 신호는 전원 동일한데 금액만 개인화되는 구조가 정확히 그 지점이었다.
            # → 이제 1R은 **회원이 설정에서 스스로 확정한 금액(one_r)**만 쓴다. 잔고 기반 계산은
            #   설정 화면의 '계산기'로만 남기고(회원이 눌러 확인 → 고정값으로 저장), 발주 경로는
            #   회원이 정한 상수만 읽는다. 잔고 조회는 '부족 시 차단·축소' 같은 보호 용도로 한정.
            self.log(f"   ⚙ [{lbl}] 1R=${_as_float(cfg.get('one_r'), 0):g} "
                     f"(회원 확정값 — 잔고 연동 자동 산출은 2026-08-13 폐지)")
        _eff_r = one_r * mult
        _sz = sizing.compute_size(asset, _broker, _eff_r, sig.get("entry_ref"), stop, direction)
        if not _sz:
            self.log(f"   ⏭ [{lbl}] 사이징 불가(진입/손절 확인) — 건너뜀."); return
        size = _sz["size"]; sym = _sz["symbol"]
        _legs = _sz.get("legs") or [(sym, size)]
        if size <= 0:
            # 왜 쉬는지 한눈에(대표 2026-08-13): 최소 1계약 리스크가 설정 1R의 몇 배인지 명시.
            try:
                # compute_size는 risk_per_contract를 반환하지 않는다(size/symbol/kind/unit/
                # risk_pts/legs 뿐) — 그대로 읽어 항상 $0 · 0.0배로 찍히고 있었다.
                # 대표가 2026-08-13에 "왜 쉬는지 한눈에" 넣으신 안내인데 숫자가 전부 0이었다.
                # 있는 값(risk_pts × 계약 포인트가치)으로 직접 계산한다.
                from eqexec import sizing as _szmod
                _pv = float(_szmod.FUTURES_POINT_VALUE.get(_sz.get("symbol")) or 0)
                _c1 = float(_sz.get("risk_pts") or 0) * _pv
                if _c1 <= 0:
                    raise ValueError("point value unknown")
                _ratio = (_c1 / _eff_r) if _eff_r > 0 else 0
                self.log(f"   ⏭ [{lbl}] 진입 안 함 — 오늘 손절거리 {_sz['risk_pts']}pt라 최소 "
                         f"1계약 리스크가 ${_c1:,.0f} = 설정 1R(${_eff_r:g})의 {_ratio:.1f}배. "
                         f"과대 사이징 방지 게이트(0.75계약 미만)가 막았습니다.")
            except Exception:
                self.log(f"   ⏭ [{lbl}] 1R=${one_r:g}가 손절거리({_sz['risk_pts']}) 대비 작아 수량 0 — 건너뜀.")
            return
        self.log(f"   [{lbl}] {asset} {direction} x{size} ({sym}), 손절 {stop}, "
                 f"1R=${one_r:g}×{mult:.2f}=${_eff_r:g}(거리 {_sz['risk_pts']}), "
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
                if live and not self._claim_entry(sig, lbl or sym):
                    # 서버 진입 클레임(2026-08-22): 같은 토큰 다른 기기 선점 시 양보
                    self.log(f"   🤝 [{lbl}] 다른 기기 선점 — 이 계좌 양보(진입 안 함)")
                    return
                if live:
                    self._auto_leverage(b, sym, size)
                res = b.place_entry(symbol=sym, side=direction, size=size,
                                    stop_loss_price=stop, custom_tag=_ctag, dry_run=not live)
            if res.get("skipped"):
                return
            if res.get("error"):
                self.log(f"   ❌ [{lbl}] 진입 실패: {res.get('error')}")
                _em = str(res.get("error"))[:300]; _hint = _entry_fail_hint(_em, self.lang == "ko")
                self._report_error(f"entry:{asset}", _em)   # 거절 사유도 원장+회원 DM(2026-09-03)
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
                self.log(f"   ⏱ [{lbl}] 체결 확인 {_fill.strftime('%H:%M:%S')}, 발송 후 {_tot}")
                self._entered_at = _mark_entered(asset)
                self._note_fill(asset, coin=float(size or 0))   # 대시보드 보고용(#53)
                self._send_gap(asset, sig, b, sym)               # 체결 갭 실측(2026-08-11)
                self._remember_open(asset, b, sym)               # 손절 청산 감지용(2026-09-02)
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
            self._alive_ping(armed=True)          # 생존 핑(4분 스로틀) - 다운 알림의 심박
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
            self._note_exit_ts(sig)      # 서버 지정 계획 청산 시각(뉴스 선행 청산) 반영
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
                self.log(f"\n📭 신호 수신 [{sid}] — {sig.get('instrument') or ''}, NO-TRADE (거래 없음, 포지션 안 잡음)")
                self.log(f"   ⏱ 보낸 시각 {_sent0.strftime('%H:%M:%S') if _sent0 else '?'} ,  "
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
                # 월 1회 실행 확인이 무장 중에 만료되면 **새 진입만** 멈춘다.
                # 자동청산(_auto_loop)은 별도 루프라 계속 돈다 — 열린 포지션은 방치하지 않는다.
                # 모의 모드 폐지(2026-09-07) - 시작은 언제나 실거래라 보류 규칙이 항상 적용된다.
                _live_now = True
                if _live_now and self._consent_left() <= 0:
                    self.log(f"\n⏸ 신호 [{sid}] {_asset} — 30일 실행 확인이 만료되어 새 진입을 보류했습니다.\n"
                             f"   앱에서 [라이브 시작]을 다시 누르면 확인 후 재개됩니다. "
                             f"이미 열린 포지션의 자동청산은 계속됩니다."
                             if self.lang == "ko" else
                             f"\n⏸ Signal [{sid}] {_asset} — 30-day confirmation expired; new entry held.\n"
                             f"   Press [Go Live] again to confirm and resume. "
                             f"Auto-close on open positions continues.")
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
                    # 지연 기준 = 봉마감(barclose_ts, 유저 체감) - 구 서버 피드엔 없어 published_at 폴백
                    _base = sig.get("barclose_ts") or _pub
                    _lat = f"{_recv.timestamp() - float(_base):.1f}s" + (" (마감 기준)" if sig.get("barclose_ts") else "")
                except (TypeError, ValueError):
                    _sent, _lat = None, "?"
                # 🚫 재진입 금지(계좌·세션별 하루 1회) → 발주 대상 계좌 선별
                # 수동 계좌(manual)는 자동 발주 대상이 아니라 '티켓만' 표시(진입은 사용자가 직접).
                _fire = []
                _manual = []
                _watching = bool(getattr(self, "_watch_only", False))
                for _key, _cfg in targets:
                    if _sig_day is not None and entered_day.get((_key, _dedup_key)) == _sig_day:
                        self.log(f"   ⏹ [{_cfg.get('label')}] {_asset} 오늘({_sig_day}) 이미 진입 — 재진입 금지.")
                        continue
                    entered_day[(_key, _dedup_key)] = _sig_day   # 성공/실패 무관 — 같은 날 재진입 금지
                    if _watching:
                        continue                                  # 아래에서 계좌 단위가 아니라 한 번만 확인 카드
                    (_manual if _cfg.get("manual") else _fire).append(_cfg)
                if _watching:
                    # Operator 등급 확인 카드 - 계좌별이 아니라 자산당 한 번(수량은 계좌마다 갈릴 값이라
                    # 애초에 계산하지 않으므로 계좌별로 반복해 보일 이유가 없다).
                    self._watch_ticket(sig, _asset, direction, stop)
                    _t.sleep(SIG_POLL_SECS); continue
                if _manual:                                       # 수동 티켓(발주 없음)
                    for _cfg in _manual:
                        self._manual_ticket(_cfg, sig, _asset, direction, stop, _mult)
                if not _fire:
                    _t.sleep(SIG_POLL_SECS); continue
                self.log(f"\n📶 신호 캡처 [{sid}] — {_asset} {direction}, 손절 {stop}, "
                         f"진입참조 {sig.get('entry_ref')}, size×{_mult:.2f}, {len(_fire)}개 계좌 자동 진입")
                self.log(f"   ⏱ 보낸 시각 {_sent.strftime('%H:%M:%S') if _sent else '?'} ,  "
                         f"받은 시각 {_recv.strftime('%H:%M:%S')} ,  지연 {_lat}")
                self._send_ev("entry_attempt", _asset, accounts=len(_fire))  # 증거 원장(2026-09-03)
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
    # Standard Edit menu - **macOS only**(대표 2026-09-09 "표준 편집 기능이 왜 필요해, 없애버려"):
    # 윈도·리눅스는 Ctrl+C/V가 위젯 기본 바인딩으로 이미 되고 창 상단에 메뉴 줄만 하나 더 생긴다.
    # 맥은 시스템 메뉴바에 Edit 메뉴가 있어야 Cmd+C/V 라우팅이 확실해 남긴다(창 안 공간은 안 먹음).
    try:
        if sys.platform != "darwin":
            raise RuntimeError("edit menu: non-mac skip")
        mb = tk.Menu(root)
        em = tk.Menu(mb, tearoff=0)

        def _ev(name):
            return lambda: (root.focus_get().event_generate(name) if root.focus_get() else None)
        # 단축키 표기는 플랫폼별(대표 2026-09-09 윈도 실측: 윈도 앱 Edit 메뉴에 'Cmd+X'가 찍힘).
        _mod = "Cmd" if sys.platform == "darwin" else "Ctrl"
        for label, acc, ev in (("Cut", f"{_mod}+X", "<<Cut>>"), ("Copy", f"{_mod}+C", "<<Copy>>"),
                               ("Paste", f"{_mod}+V", "<<Paste>>"), ("Select All", f"{_mod}+A", "<<SelectAll>>")):
            em.add_command(label=label, accelerator=acc, command=_ev(ev))
        mb.add_cascade(label="Edit", menu=em)
        root.config(menu=mb)
    except Exception:
        pass


def main():
    root = tk.Tk()
    # 창·작업표시줄 아이콘(대표 2026-08-11 "이상한 모양 나와") - exe 아이콘(spec)과 별개로
    # 런타임 Tk 아이콘을 명시해야 기본 깃털이 안 뜬다. 윈도=ico, 맥/기타=png 폴백.
    # 2단 폴백(대표 2026-08-12 "깃털 또 나와"): ①iconbitmap(default=)로 전 창(팝업 포함)에
    # 적용 ②실패하든 말든 iconphoto(256px)도 항상 시도 - 어느 한쪽이 죽어도 깃털은 안 뜬다.
    import sys as _sys
    if _sys.platform.startswith("win"):
        try:
            root.iconbitmap(default=_resource("eqicon.ico"))
        except Exception:
            try:
                root.iconbitmap(_resource("eqicon.ico"))
            except Exception:
                pass
    try:
        _icpng = _resource("eqlogo256.png")
        if not os.path.exists(_icpng):
            _icpng = _resource("eqlogo.png")
        root.iconphoto(True, tk.PhotoImage(file=_icpng))
    except Exception:
        pass
    try:
        ttk.Style().theme_use("aqua")
    except Exception:
        pass
    _bind_clipboard(root)
    _app = App(root)

    # 창을 닫을 때도 서버에 '꺼짐'을 알린다(대표 2026-08-28 "앱 끌 때 마지막 동작으로
    # 정지 버튼 누른다든지 해서 서버로 꺼졌다 메시지 못 보내나").
    # 종전에는 [전체 정지]를 눌러야만 종료 신고가 나갔고, 창을 그냥 닫으면 서버가 15분
    # 무응답으로 판정할 때까지 회원 화면에 '무장 중'이 남았다.
    # ⚠️무장을 해제하지는 않는다 - 열린 포지션과 자동 청산 계약을 화면 닫기로 바꾸면
    # 위험하다. 상태만 정직하게 보고하고 종료한다(다운 경보 대상에서도 빠진다).
    def _on_close():
        # ⚠️무장 중 종료는 **묻는다**(2026-08-28 리뷰 P1). 종전엔 무조건 armed=False를
        # 보내, 무장한 채 창을 닫아도 서버가 '정상 종료'로 분류해 다운 경보가 통째로
        # 무력화됐다 - 자동화가 실제로 멈추는데 아무도 안 알려주는 상태다.
        # 열린 포지션의 세션 마감 자동 청산도 함께 죽으므로 회원이 알고 닫아야 한다.
        try:
            _armed = bool(getattr(_app, "_sig_accts", None) or getattr(_app, "_auto_accts", None))
        except Exception:
            _armed = False
        if _armed:
            try:
                _ko = getattr(_app, "lang", "ko") == "ko"
                if not messagebox.askyesno(
                        "EQ Autopilot",
                        ("자동 실행이 켜져 있습니다. 지금 종료하면 신호 진입과 세션 마감 "
                         "자동 청산이 멈춥니다.\n열린 포지션과 손절 주문은 브로커에 그대로 "
                         "남습니다.\n\n종료할까요?" if _ko else
                         "Automation is running. Quitting stops signal entries and "
                         "session-close auto-flatten.\nOpen positions and stop orders stay "
                         "at the broker.\n\nQuit?")):
                    return
            except Exception:
                pass
        try:
            # 무장 중 종료는 armed=True로 신고한다 - 다운 경보가 정상 작동해야 한다.
            # 동시에 closed=True를 실어, 회원 화면의 칩은 15분을 기다리지 않고 즉시
            # 꺼짐이 된다(대표 2026-08-28 "앱 껐는데 안 변해"). 경보와 표시는 다른 축이다.
            _app._closing = True
            _app._alive_ping(armed=_armed, force=True, sync=True)   # 동기 - 마커 유실 방지
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    try:
        root.protocol("WM_DELETE_WINDOW", _on_close)       # 창 닫기(X)
    except Exception:
        pass
    # macOS Cmd+Q와 Dock 종료는 WM_DELETE_WINDOW를 안 거친다(별도 Apple Event) - 함께 묶는다.
    try:
        root.createcommand("::tk::mac::Quit", _on_close)
    except Exception:
        pass

    # ── 마지막 그물(대표 2026-09-04 "앱이 꺼져도 잘 모르나 봐. 끌 때 서버로 신호 보내줘"):
    # X·Cmd+Q는 위에서 잡지만, **윈도 재부팅·로그오프·taskkill·인터프리터 해체**는
    # WM_DELETE_WINDOW를 안 거쳐 closed 마커 없이 죽었다(윈도 실행기 전환 첫날 실측 -
    # 재부팅 테스트 후 서버가 앱 꺼짐을 몰랐다). atexit = 정상 해체 전 경로,
    # SIGTERM/SIGBREAK = 종료 시그널 경로. 강제 종료(taskkill /F·크래시·정전)는 어떤
    # 클라이언트 코드로도 못 잡는다 - 그건 서버 15분 무응답 판정이 맡는다(설계된 최후단).
    def _last_report(*_a):
        try:
            if getattr(_app, "_closing", False):
                return                        # _on_close가 이미 신고함 - 중복 방지
            _app._closing = True
            _app._alive_ping(armed=bool(getattr(_app, "_sig_accts", None)
                                        or getattr(_app, "_auto_accts", None)),
                             force=True, sync=True)
        except Exception:
            pass
    try:
        import atexit as _ax
        _ax.register(_last_report)
    except Exception:
        pass
    try:
        import signal as _sg
        for _s in ("SIGTERM", "SIGBREAK"):
            if hasattr(_sg, _s):
                _sg.signal(getattr(_sg, _s),
                           lambda *_: (_last_report(), sys.exit(0)))
    except Exception:
        pass

    root.mainloop()
    # mainloop를 어떤 경로로 빠져나오든 마지막으로 한 번 더(중복 핑은 무해).
    # ⚠️여기도 closed 마커 + 동기(2026-08-31): 이 경로로만 나온 종료는 마커 없이 나가
    # 칩이 15분을 기다렸다. 어떤 탈출이든 '앱이 꺼졌다'는 사실은 같다.
    try:
        _app._closing = True
        _app._alive_ping(armed=bool(getattr(_app, "_sig_accts", None)
                                    or getattr(_app, "_auto_accts", None)),
                         force=True, sync=True)
    except Exception:
        pass


if __name__ == "__main__":
    main()
