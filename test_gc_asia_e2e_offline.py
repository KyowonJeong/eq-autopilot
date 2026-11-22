#!/usr/bin/env python3
# EQ Autopilot - 금 아시아 그리드(진입 08:00 UTC, 청산 12:00 UTC) 처음부터 끝까지 오프라인 시험(2026-11-05). 0 = 통과.
# 서버 sched {"GC": {"entry": ["UTC", 8], "exit": ["UTC", 12]}}를 받은 뒤 겨울(2026-11-16 월), 여름(2027-06-14 월)에서:
#   1 사전 점검 T-70, T-10과 진입 예열, 빠른 피드 확인이 08:00 UTC 기준(뉴욕 02:00 기준이 아님)
#   2 08:00:05 UTC 신호 = 새 신호(지연은 봉 마감 기준), 늦은 진입 아님, 신호 손절로 주문(dry run)
#   3 놓친 신호: 11:30에 처음 본 신호는 자동 진입 안 함, [놓친 신호 진입]은 청산 40분 넘게 남았을 때만(11:15 들어감, 11:30, 12:05 안 됨)
#   4 마감 청산 12:00 UTC, 서버가 앞당긴 청산(11:00)은 따르고 늦춘 값(13:00)은 따르지 않음
#   5 같은 실행에서 나스닥(뉴욕 10→14), 비트코인(일 22 UTC, 판정 4시간마다)은 그대로
#   6 프롭 거래일(Topstep, Lucid): 금 08:00 UTC 거래와 나스닥 10:00 뉴욕 거래가 같은 거래일
#   7 서버 sched 없음(옛 회원과 같은 동작) = 금 뉴욕 02:00 진입, 06:00 청산
# 진짜 앱 함수를 쓴다. 가짜는 네트워크(차단), 브로커, 시계(time.time, datetime.now)뿐. 임시 HOME, 알림 끔.
# 사용: python executor/test_gc_asia_e2e_offline.py   (EQ_TEST_TZ=Asia/Seoul 처럼 PC 시간대를 바꿔 돌릴 수 있다, 기본 베를린)
import os
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_gcasia_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ["EQ_STOP_JITTER"] = "0"           # 손절 지터 끔 - 주문 손절 = 신호 손절 그대로 비교
os.environ["TZ"] = os.environ.get("EQ_TEST_TZ") or "Europe/Berlin"
time.tzset()


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402
import requests  # noqa: E402
import autopilot_crypto  # noqa: E402
import datetime as DT  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402
from eqexec import sizing  # noqa: E402

assert E.APP_DIR.startswith(HOME)
FAIL, NPASS = [], [0]


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:600]}"))
    if c:
        NPASS[0] += 1
    else:
        FAIL.append(n)


# ── 가짜 시계: time.time과 datetime.now(앱이 쓰는 두 시계)를 같은 값으로 ─────────────────
_RDT = DT.datetime
UTC = DT.timezone.utc
NYZ = ZoneInfo("America/New_York")
CLK = {"t": _RDT(2026, 11, 16, tzinfo=UTC).timestamp()}


class FakeDT(_RDT):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(CLK["t"], tz)

    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(CLK["t"], UTC).replace(tzinfo=None)


DT.datetime = FakeDT                         # E._dt.datetime, 함수 안의 'from datetime import datetime'이 모두 이것
_real_sleep = time.sleep
time.time = lambda: CLK["t"]


def T(y, mo, d, h, mi=0, s=0, tz=UTC):
    return _RDT(y, mo, d, h, mi, s, tzinfo=tz).timestamp()


def hm(t, tz=UTC):
    return _RDT.fromtimestamp(t, tz).strftime("%a %H:%M:%S")


# ── 가짜 브로커(dry run - 주문은 기록만) ───────────────────────────────────────────────
PRICE = {"NQ": 20000.0, "GC": 4136.0, "BTC": 90000.0}
PLACED, FLAT, QTY, HEALTH, PXHIST = [], [], [], [], []


class FakeBroker:
    def __init__(self, broker, acct):
        self.name, self.acct = broker, acct

    def healthcheck(self):
        HEALTH.append((CLK["t"], self.name, self.acct))

    def _accounts(self):
        return [{"name": self.acct, "id": self.acct}]

    def _symbol(self, sym):
        return str(sym).replace(".P", "")

    def list_open_positions(self):
        return []

    def position_qty(self, *a):
        QTY.append((CLK["t"], self.name, a))
        return 0

    def current_market_price(self, con):
        c = str(con)
        return PRICE["NQ"] if "NQ" in c else (PRICE["GC"] if "GC" in c else PRICE["BTC"])

    def close_contract(self, *a):
        pass

    def place_entry(self, account_id=None, contract_id=None, side=None, size=None, order_type=None, stop_loss_price=None,
                    custom_tag=None, dry_run=True, symbol=None, **k):
        PLACED.append({"t": CLK["t"], "acct": self.acct, "con": contract_id or symbol, "size": size, "stop": stop_loss_price,
                       "dry": dry_run, "side": side})
        return {"would_place": {"contract": contract_id or symbol, "size": size, "stop": stop_loss_price}}

    def flatten_all(self, dry_run=True, only=None):
        FLAT.append((CLK["t"], self.name, self.acct, dry_run))
        return SimpleNamespace(planned=[SimpleNamespace(account_name=self.acct, symbol="X")], closed=[], errors=[])

    def trades(self, aid, start, timeout=None):
        return list(PXHIST)


E._build_broker = lambda broker, f1, f2, f3, accounts: FakeBroker(broker, (accounts or [broker])[0])
E._f2_load = lambda f1: "secret"             # 키체인을 읽지 않는다
ASK, INFO = [], []
E.messagebox.askyesno = lambda t, m, **k: (ASK.append(m), True)[1]
E.messagebox.showinfo = lambda t, m, **k: INFO.append(m)
E.messagebox.showwarning = lambda *a, **k: None


# ── 앱 자리: 진짜 메서드를 붙이고 나머지 App 메서드는 아무것도 안 함 ───────────────────────
class Stub:
    lang = "ko"
    STOP_ORDER_CHECK_SEC = E.App.STOP_ORDER_CHECK_SEC
    _JITTER_TICK = E.App._JITTER_TICK

    def __init__(self, acfg=None):
        self.logs, self.skips, self.evs, self.alerts = [], [], [], []
        self._acfg = acfg or {}
        self._sig_accts, self._auto_accts, self._sig_parked = {}, {}, {}
        self._token = "tok-gc-asia-1234567890"
        self._sig_on, self._auto_on = False, False
        self._entered_at, self._exit_override, self._precheck_done = {}, {}, {}
        self._open_ctx, self._unconf_watch = {}, {}
        self.root = SimpleNamespace(after=lambda *a, **k: None, after_cancel=lambda *a, **k: None)

    def log(self, m):
        self.logs.append((CLK["t"], str(m)))

    def _live_now(self, f):
        return bool(f)

    def _live_acct_cfg(self, key, cfg):
        return dict(cfg), ""

    def _claim_entry(self, *a, **k):
        return True

    def _nt8_standby(self):
        return False

    def _resolve_contract(self, b, s, **k):
        return f"{s} 12-26"

    def _clock_skew_now(self):
        return None

    def _entry_skip_ev(self, a, why):
        self.skips.append((CLK["t"], a, why))

    def _send_ev(self, kind, asset, **k):
        self.evs.append((CLK["t"], kind, asset, k))

    def _member_alert(self, kind, ko, en):
        self.alerts.append((CLK["t"], kind, ko))

    def _accts_of(self, a):
        return self._acfg[a]

    def _acct_broker(self, a, ac):
        return ac["broker"]

    def _creds_of(self, a, bk):
        return {"f1": "127.0.0.1" if bk == "ibkr" else "key", "f3": "7497" if bk == "ibkr" else ""}

    def __getattr__(self, n):
        if n.startswith("__") or not callable(getattr(E.App, n, None)):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_sig_loop_body", "_enter_account", "_run_futures_entry", "_jitter_stop", "_fill_asset", "_missed_entry", "_note_exit_ts",
           "_precheck_tick", "_precheck_run", "_px_warm_tick", "_entry_warm", "_sig_poll_secs", "_sched_apply", "_master_arm",
           "_auto_loop_body", "_close_mode", "_daycap_key", "_dc_state", "_dc_save", "_dc_map", "_dc_gate", "_daycap_note_once",
           "_dc_hist_refresh", "_daycap_hit_today", "_daycap_set_hit"):
    setattr(Stub, _n, E.App.__dict__[_n])


def acct(aid, broker, one_r):
    return {"id": aid, "one_r": one_r, "label": aid, "broker": broker, "prop": {}, "pct": {}, "one_lim": 0.0, "manual": False}


ACFG = {"GC": [acct("GC-A", "ibkr", 1120.0)], "NQ": [acct("NQ-A", "ibkr", 1000.0)], "BTC": [acct("", "bybit", 300.0)]}


def armed(acfg=None):
    """진짜 _master_arm으로 세 자산 계좌 무장(dry run) - 마감 청산 잡은 그때의 _ASSET_EXITS로 만들어진다."""
    st = Stub(acfg or ACFG)
    for a in st._acfg:
        for i in range(len(st._acfg[a])):
            E.App._master_arm(st, a, i, False, True)
    return st


class SyncThread:
    """사전 점검 스레드를 그 자리에서 돌린다(가짜 시계 그대로)."""
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **k):
        self._f, self._a, self._k = target, tuple(args or ()), dict(kwargs or {})

    def start(self):
        self._f(*self._a, **self._k)


def precheck_sim(st, t0, t1, step=300):
    """_precheck_tick을 5분마다(진짜 주기) 돌려 사전 점검이 돈 (시각, 자산, 창) 목록."""
    n0 = len(st.evs)
    orig = threading.Thread
    threading.Thread = SyncThread
    try:
        t = t0
        while t <= t1:
            CLK["t"] = t
            E.App._precheck_tick(st)
            t += step
    finally:
        threading.Thread = orig
    return [(t, a, k.get("win")) for (t, kind, a, k) in st.evs[n0:] if kind == "preflight_ok"]


def run_loop(st, sig, at):
    """신호 루프 한 바퀴(피드 한 번 → 처리 → 잠 = 멈춤). 반환 이번에 나간 주문."""
    CLK["t"] = at
    n0 = len(PLACED)
    orig = (requests.get, autopilot_crypto.decrypt, time.sleep)

    def _get(url, **k):
        return SimpleNamespace(ok=True, text="x", headers={})
    _get.__module__ = "harness"                 # 루프가 requests.get을 쓰게(바꿔 끼운 시험 규칙)
    requests.get = _get
    autopilot_crypto.decrypt = lambda tok, txt: dict(sig)

    def _sleep(s):
        CLK["t"] += float(s)
        st._sig_on = False
    time.sleep = _sleep
    st._sig_on = True
    try:
        E.App._sig_loop_body(st, "http://feed", None)
    finally:
        requests.get, autopilot_crypto.decrypt, time.sleep = orig
    return PLACED[n0:]


def run_auto(st, at):
    """마감 청산 루프 한 바퀴. 반환 이번 바퀴의 (청산, BTC 포지션 조회)."""
    CLK["t"] = at
    f0, q0 = len(FLAT), len(QTY)
    orig = time.sleep

    def _sleep(s):
        st._auto_on = False
    time.sleep = _sleep
    st._auto_on = True
    try:
        E.App._auto_loop_body(st, None)
    finally:
        time.sleep = orig
    return FLAT[f0:], QTY[q0:]


def logs_at(st, t0, t1=None):
    return " | ".join(m for t, m in st.logs if t0 <= t <= (t1 if t1 is not None else t0 + 120))


def sig_of(asset, sid, bc, ext, direction="LONG"):
    ref, stp = {"NQ": (20000.0, 19950.0), "GC": (4136.0, 4130.4), "BTC": (90000.0, 89000.0)}[asset]
    return {"id": sid, "instrument": asset, "tradeable": True, "direction": direction, "barclose_ts": bc, "published_at": bc + 3.0,
            "exit_ts": ext, "entry_ref": ref, "stop_price": stp, "size_mult": 1.0}


def micro(rows):
    return sum(int(p["size"]) * (1 if str(p["con"]).startswith("M") else sizing.MINI_RATIO) for p in rows)


# ═════ 0 서버 sched 전 = 기본 표, 무장 뒤 sched 적용 ═════════════════════════════════════════
ck("0a 기본 표(서버 sched 전): 금 뉴욕 02→06 평일, 나스닥 뉴욕 10→14 평일, 비트 일 22 UTC",
   E._ASSET_ENTRIES == {"NQ": [("America/New_York", 10)], "GC": [("America/New_York", 2)], "BTC": [("UTC", 22)]}
   and E._ASSET_EXITS["GC"] == [("America/New_York", 6)] and E._ASSET_EXITS["NQ"] == [("America/New_York", 14)]
   and E._ENTRY_DAYS == {"NQ": (0, 1, 2, 3, 4), "GC": (0, 1, 2, 3, 4), "BTC": (6,)}, (E._ASSET_ENTRIES, E._ENTRY_DAYS))
CLK["t"] = T(2026, 11, 15, 12)
ARM = armed()                                   # 첫 하트비트 전에 무장한 앱(기동 순서 그대로)
ck("0b 무장(진짜 _master_arm): 금 잡 뉴욕 06, 나스닥 잡 뉴욕 14, 비트 잡 여섯(2, 6, 10, 14, 18, 22 UTC)",
   [(j["tz"], j["hour"]) for j in ARM._auto_accts[("GC", 0)]] == [("America/New_York", 6)]
   and [(j["tz"], j["hour"]) for j in ARM._auto_accts[("NQ", 0)]] == [("America/New_York", 14)]
   and [j["hour"] for j in ARM._auto_accts[("BTC", 0)]] == [2, 6, 10, 14, 18, 22] and set(ARM._sig_accts) == {("GC", 0), ("NQ", 0), ("BTC", 0)},
   ARM._auto_accts)
ASIA = {"GC": {"entry": ["UTC", 8], "exit": ["UTC", 12]}}
_nq_jobs0, _btc_jobs0 = [dict(j) for j in ARM._auto_accts[("NQ", 0)]], [dict(j) for j in ARM._auto_accts[("BTC", 0)]]
E.App._sched_apply(ARM, ASIA)
ck("0c sched 적용: 금 표 08→12 UTC 평일, 무장된 금 잡도 12 UTC로(브로커, 계좌 칸 그대로)",
   E._ASSET_ENTRIES["GC"] == [("UTC", 8)] and E._ASSET_EXITS["GC"] == [("UTC", 12)] and E._ENTRY_DAYS["GC"] == (0, 1, 2, 3, 4)
   and [(j["tz"], j["hour"], j["broker"], j["acct"], j["live"]) for j in ARM._auto_accts[("GC", 0)]] == [("UTC", 12, "ibkr", "GC-A", False)],
   ARM._auto_accts[("GC", 0)])
ck("5a 같은 실행: 나스닥, 비트 표와 무장 잡은 그대로(잡은 다시 만들지 않음), 비트 블록 표 그대로",
   E._ASSET_ENTRIES["NQ"] == [("America/New_York", 10)] and E._ASSET_EXITS["NQ"] == [("America/New_York", 14)]
   and E._ASSET_ENTRIES["BTC"] == [("UTC", 22)] and E._ENTRY_DAYS["BTC"] == (6,) and E._ENTRY_DAYS["NQ"] == (0, 1, 2, 3, 4)
   and E._BTC_BLOCK_K == {2: 0, 6: 1, 10: 2, 14: 3, 18: 4, 22: 5}
   and ARM._auto_accts[("NQ", 0)] == _nq_jobs0 and ARM._auto_accts[("BTC", 0)] == _btc_jobs0)
_fresh = armed()
ck("0d sched 뒤 새로 무장해도 같은 잡(금 12 UTC 하나)",
   [(j["tz"], j["hour"]) for j in _fresh._auto_accts[("GC", 0)]] == [("UTC", 12)])

DATES = (("겨울", (2026, 11, 16)), ("여름", (2027, 6, 14)))
for TAG, (Y, M, D) in DATES:
    ny = lambda h, mi=0, s=0: T(Y, M, D, h, mi, s, NYZ)          # noqa: E731
    ut = lambda h, mi=0, s=0: T(Y, M, D, h, mi, s)               # noqa: E731
    _sun = _RDT(Y, M, D) - DT.timedelta(days=1)
    SY, SM, SD = _sun.year, _sun.month, _sun.day
    print(f"\n== {TAG} {Y}-{M:02d}-{D:02d}(월): 금 08:00 UTC = 뉴욕 {hm(ut(8), NYZ)}, 나스닥 10:00 뉴욕 = {hm(ny(10))} UTC ==")

    # ═════ 1 사전 점검, 예열, 빠른 피드 확인 ═════════════════════════════════════════════════
    CLK["t"] = ut(0)
    _ge, _ne, _be = E._next_entry_dt("GC"), E._next_entry_dt("NQ"), E._next_entry_dt("BTC")
    ck(f"1a {TAG} 다음 진입(_next_entry_dt): 금 = 그날 08:00 UTC, 나스닥 = 그날 10:00 뉴욕, 비트 = 다음 일요일 22:00 UTC",
       _ge.timestamp() == ut(8) and _ne.timestamp() == ny(10) and _be.timestamp() == T(SY, SM, SD, 22) + 7 * 86400,
       (_ge, _ne, _be))
    for _off in (0, 197):                     # 틱이 정각에 걸릴 때, 아무 데나 걸릴 때(3분 17초 어긋남)
        st = armed()
        got = precheck_sim(st, ut(5) + _off, ut(15) + _off)
        g = [(t, w) for t, a, w in got if a == "GC"]
        n = [(t, w) for t, a, w in got if a == "NQ"]
        b = [(t, w) for t, a, w in got if a == "BTC"]
        _w10 = [t for t, w in g if w == 10]
        ck(f"1b {TAG} 사전 점검(틱 +{_off}초): 금 T-70 한 번(06:50 UTC 뒤 첫 틱), T-10 한 번(07:50~08:00 UTC)"
           + (" = 정확히 07:50:00 UTC" if not _off else ""),
           [w for t, w in g] == [70, 10] and ut(6, 50) <= g[0][0] < ut(6, 55) and _w10 and ut(7, 50) <= _w10[0] < ut(8)
           and (_off or _w10[0] == ut(7, 50)), [(hm(t), w) for t, w in g])
        ck(f"1c {TAG} 금 T-10은 옛 시각(뉴욕 01:50~02:00)이 아님, 로그는 '진입 08:00 UTC', 'T-10분'",
           _w10 and not (ny(1, 50) <= _w10[0] < ny(2)) and "진입 08:00 UTC" in logs_at(st, _w10[0], _w10[0]) and "T-10분" in logs_at(st, _w10[0], _w10[0]),
           logs_at(st, _w10[0], _w10[0]) if _w10 else got)
        ck(f"5b {TAG} 나스닥 사전 점검(틱 +{_off}초) = 뉴욕 08:50 뒤, 09:50 뒤(종전 그대로), 월요일 비트는 없음",
           [w for t, w in n] == [70, 10] and ny(8, 50) <= n[0][0] < ny(8, 55) and ny(9, 50) <= n[1][0] < ny(10) and not b,
           ([(hm(t, NYZ), w) for t, w in n], b))
    st = armed()
    got = precheck_sim(st, T(SY, SM, SD, 19, 0, 0), T(SY, SM, SD, 22, 5, 0))
    ck(f"5c {TAG} 일요일: 비트 T-70 20:50, T-10 21:50 UTC(종전 그대로), 금, 나스닥은 없음(다음 진입이 월요일)",
       [(t, a, w) for t, a, w in got] == [(T(SY, SM, SD, 20, 50), "BTC", 70), (T(SY, SM, SD, 21, 50), "BTC", 10)],
       [(hm(t), a, w) for t, a, w in got])
    # 진입 예열(15초 틱): ProjectX 계좌 하나 더
    _acfg_w = dict(ACFG, GC=[acct("GC-A", "ibkr", 1120.0), acct("PX-1", "projectx", 1120.0)])
    st = armed(_acfg_w)
    TIM, THR = [], []

    class FakeTimer:
        def __init__(self, sec, fn, args=()):
            TIM.append((CLK["t"], float(sec), getattr(fn, "__name__", ""), args))
            self.daemon = False

        def start(self):
            pass

    class RecThread:
        def __init__(self, target=None, args=(), daemon=None, **k):
            THR.append((CLK["t"], getattr(target, "__name__", "")))

        def start(self):
            pass
    _ot, _oth = threading.Timer, threading.Thread
    threading.Timer, threading.Thread = FakeTimer, RecThread
    try:
        t = ut(5, 55, 7)                      # 옛 시각(뉴욕 01:58:30~02:00)이 겨울, 여름 모두 들어가게
        while t <= ut(8, 1, 7):
            CLK["t"] = t
            E.App._px_warm_tick(st)
            t += 15
    finally:
        threading.Timer, threading.Thread = _ot, _oth
    _gt = [x for x in TIM if x[3] and x[3][0] == "GC"]
    ck(f"1d {TAG} 진입 예열: 금 _entry_warm 타이머 한 번, 07:59:52 UTC(진입 8초 전)에 터지게, 금 계좌 둘 다, 나스닥 예열 없음",
       len(TIM) == 1 and len(_gt) == 1 and _gt[0][2] == "_entry_warm" and abs(_gt[0][0] + _gt[0][1] - (ut(8) - 8)) < 1e-6
       and len(_gt[0][3][1]) == 2, [(hm(t), s, nm, a[0] if a else None) for t, s, nm, a in TIM])
    ck(f"1e {TAG} ProjectX 계좌 목록, 계약 예열 한 번(08:00 UTC 90초 안), 뉴욕 02:00 앞(옛 시각)에는 없음",
       [nm for t, nm in THR] == ["_warm"] and ut(7, 58, 30) <= THR[0][0] < ut(8), [(hm(t), nm) for t, nm in THR])
    st = armed()
    _fast = []
    for _t in (ny(1, 59, 58), ny(2, 0, 10), ut(7, 59, 50), ut(7, 59, 58), ut(8, 0, 10), ut(8, 0, 40)):
        CLK["t"] = _t
        _fast.append(E.App._sig_poll_secs(st) < 1.0)
    ck(f"1f {TAG} 빠른 피드 확인(0.5초): 07:59:58~08:00:10 UTC만, 뉴욕 01:59:58, 02:00:10(옛 시각), 07:59:50, 08:00:40은 보통(3초)",
       _fast == [False, False, False, True, True, False], _fast)

    # ═════ 2 08:00:05 UTC 금 신호 → 주문(dry run) ════════════════════════════════════════════
    st = armed()
    sg = sig_of("GC", f"{Y}{M:02d}{D:02d}-GC-1", ut(8), ut(12))
    _pl = run_loop(st, sg, ut(8, 0, 5))
    _want = sizing.compute_size("GC", "ibkr", 1120.0, 4136.0, 4130.4, "LONG")["legs"]
    ck(f"2a {TAG} 08:00:05 UTC 금 신호 = 새 신호, 늦은 진입 아님, 계산 수량 그대로(GC 2), 신호 손절 4130.4, dry run, 건너뜀 없음",
       [(p["con"], p["size"], p["stop"], p["dry"], p["side"]) for p in _pl] == [(f"{s} 12-26", q, 4130.4, True, "LONG") for s, q in _want]
       and _want == [("GC", 2)] and not st.skips and "놓친 신호" not in logs_at(st, ut(8), ut(9)) and "늦은 진입" not in logs_at(st, ut(8), ut(9)),
       (_pl, st.skips, logs_at(st, ut(8), ut(9))[:400]))
    ck(f"2b {TAG} 지연은 봉 마감(08:00:00) 기준 5.0초(발행 08:00:03 기준 2.0초가 아님)",
       "지연 5.0s (마감 기준)" in logs_at(st, ut(8), ut(9)), logs_at(st, ut(8), ut(9))[:500])
    ck(f"2c {TAG} 신호의 계획 청산 시각(12:00 UTC)을 기억(뉴스 선행 청산 재료)", st._exit_override.get("GC") == (ut(12), ""), st._exit_override)
    _pl2 = run_loop(st, sg, ut(8, 0, 8))
    ck(f"2d {TAG} 같은 신호를 다시 보면 주문 없음", not _pl2, _pl2)
    st2 = armed()
    _pl = run_loop(st2, dict(sg, published_at=ut(8) - 90.0), ut(8, 0, 5))
    ck(f"2e {TAG} 대조: 발행이 95초 전인 신호는 오래됨으로 건너뜀(나이 관문은 그대로 발행 시각 기준)",
       not _pl and [w for t, a, w in st2.skips] == ["stale_signal"], (_pl, st2.skips))
    # 같은 실행에서 나스닥(뉴욕 10:00), 비트(일 22:00 UTC)
    _pl = run_loop(st, sig_of("NQ", f"{Y}{M:02d}{D:02d}-NQ-1", ny(10), ny(14)), ny(10, 0, 5))
    _wq = sizing.compute_size("NQ", "ibkr", 1000.0, 20000.0, 19950.0, "LONG")["legs"]
    ck(f"5d {TAG} 같은 실행 나스닥 10:00:05 뉴욕 신호 → 계산 수량, 신호 손절 19950으로 주문(종전 그대로)",
       [(p["con"], p["size"], p["stop"], p["dry"]) for p in _pl] == [(f"{s} 12-26", q, 19950.0, True) for s, q in _wq], _pl)
    stb = armed()
    _pl = run_loop(stb, sig_of("BTC", f"{SY}{SM:02d}{SD:02d}-BTC-1", T(SY, SM, SD, 22), T(SY, SM, SD, 22) + 86400), T(SY, SM, SD, 22, 0, 5))
    ck(f"5e {TAG} 같은 실행 비트 일요일 22:00:05 UTC 신호 → 0.3 BTC, 신호 손절 89000으로 주문(종전 그대로)",
       [(p["con"], p["size"], p["stop"], p["dry"]) for p in _pl] == [("BTCUSDT.P", 0.3, 89000.0, True)], _pl)

    # ═════ 3 놓친 신호 ═══════════════════════════════════════════════════════════════════════
    for _at, _ok, _why in ((ut(11, 15), True, "청산 45분 전"), (ut(11, 30), False, "청산 30분 전"), (ut(12, 5), False, "청산 지남, 발행 4시간 넘음")):
        st = armed()
        _pl = run_loop(st, sg, _at)                     # 앱을 이때 켰다 - 피드에 08:00 신호가 남아 있다
        ck(f"3a {TAG} {hm(_at)} UTC에 처음 본 08:00 신호 = 자동 진입 안 함(오래됨)", not _pl and [w for t, a, w in st.skips] == ["stale_signal"],
           (_pl, st.skips))
        st._sig_on = True
        ASK.clear(); INFO.clear()
        CLK["t"] = _at + 1
        E.App._missed_entry(st)
        if _ok:
            ck(f"3b {TAG} {hm(_at)} [놓친 신호 진입] = 확인 창, 요청 걸림({_why})",
               ASK and not INFO and getattr(st, "_sig_force_id", None) == sg["id"], (ASK, INFO))
            _pl = run_loop(st, sg, _at + 2)
            ck(f"3c {TAG} {hm(_at)} 놓친 신호 진입 = 지금 가격으로 위험을 다시 재 줄이기만(마이크로 환산 20 → {micro(_pl)}), 신호 손절 4130.4",
               _pl and 0 < micro(_pl) < 20 and all(p["stop"] == 4130.4 and p["dry"] for p in _pl)
               and "놓친 신호 진입" in logs_at(st, _at, _at + 60) and "늦은 진입: 손절까지 거리" in logs_at(st, _at, _at + 60),
               (_pl, logs_at(st, _at, _at + 60)[:500]))
        else:
            ck(f"3b {TAG} {hm(_at)} [놓친 신호 진입] = 확인 창 없이 안 된다고 알림({_why}), 요청 없음",
               not ASK and INFO and "40분" in INFO[-1] and getattr(st, "_sig_force_id", None) is None, (ASK, INFO))
            _pl = run_loop(st, sg, _at + 2)
            ck(f"3c {TAG} {hm(_at)} 그 뒤 루프도 주문 없음", not _pl, _pl)
    st = armed()
    run_loop(st, sg, ut(11, 19))
    st._sig_on = True
    ASK.clear(); INFO.clear()
    CLK["t"] = ut(11, 19, 1)
    E.App._missed_entry(st)
    _pl = run_loop(st, sg, ut(11, 21))
    ck(f"3d {TAG} 11:19 요청(41분 남음)이 11:21(39분 남음)에야 처리되면 루프가 다시 재서 안 들어감",
       ASK and not _pl and [w for t, a, w in st.skips] == ["stale_signal", "stale_signal"], (ASK, _pl, st.skips))

    # ═════ 4 마감 청산 ═══════════════════════════════════════════════════════════════════════
    ARM._auto_fired, ARM._auto_retry = {}, {}
    ARM._exit_override = {}
    ARM._entered_at = {"GC": ut(8, 0, 6), "NQ": ny(10, 0, 6), "BTC": T(SY, SM, SD, 22, 0, 6)}
    CLK["t"] = ut(8, 0, 5)
    E.App._note_exit_ts(ARM, sg)                      # 신호 루프가 하는 대로(평소 서버 값 = 표 시각 12:00 UTC)
    ev = {}
    for _t in sorted({ut(2), ny(6), ut(11, 59, 45), ut(12), ut(12, 0, 20), ut(12, 29), ny(13, 59, 50), ny(14), ny(14, 0, 30)}):
        ev[_t] = run_auto(ARM, _t)
    _gcf = [(t, a) for t, (fl, q) in ev.items() for (_x, bk, a, dry) in fl if a == "GC-A"]
    _nqf = [(t, a) for t, (fl, q) in ev.items() for (_x, bk, a, dry) in fl if a == "NQ-A"]
    ck(f"4a {TAG} 금 마감 청산 = 12:00:00 UTC 한 번(dry run), 옛 시각 뉴욕 06:00({hm(ny(6))} UTC)과 11:59:45에는 없음",
       _gcf == [(ut(12), "GC-A")] and all(dry for t, (fl, q) in ev.items() for (_x, bk, a, dry) in fl), [(hm(t), a) for t, a in _gcf])
    ck(f"4b {TAG} 청산 로그 'GC 12:00 UTC 세션 마감', 선행 청산 로그 없음(서버 값 = 표 시각)",
       "GC 12:00 UTC 세션 마감" in logs_at(ARM, ut(12), ut(12)) and "선행 청산" not in logs_at(ARM, ut(8), ut(13)), logs_at(ARM, ut(12), ut(12)))
    ck(f"5f {TAG} 나스닥 마감 청산 = 14:00 뉴욕 한 번(종전 그대로), 12:00 UTC에는 금만", _nqf == [(ny(14), "NQ-A")]
       and [a for (_x, bk, a, dry) in ev[ut(12)][0]] == ["GC-A"], [(hm(t, NYZ), a) for t, a in _nqf])
    ck(f"5g {TAG} 비트 판정 블록 02:00 UTC에 포지션 조회(종전 그대로), 12:00 UTC에는 비트 조회 없음",
       len(ev[ut(2)][1]) == 1 and ev[ut(2)][1][0][1] == "bybit" and not ev[ut(12)][1], (ev[ut(2)][1], ev[ut(12)][1]))
    # 서버가 앞당긴 청산(뉴스): 11:00 UTC
    ARM._auto_fired, ARM._auto_retry = {}, {}
    CLK["t"] = ut(8, 0, 5)
    E.App._note_exit_ts(ARM, dict(sg, exit_ts=ut(11), exit_early="CPI"))
    ev = {_t: run_auto(ARM, _t) for _t in (ut(10, 59, 50), ut(11), ut(11, 0, 30), ut(12), ut(12, 10))}
    _gcf = [t for t, (fl, q) in ev.items() for (_x, bk, a, dry) in fl if a == "GC-A"]
    ck(f"4c {TAG} 서버가 앞당긴 청산 11:00 UTC = 그때 한 번(10:59:50 없음, 12:00 다시 없음), 로그 '선행 청산 발화 (CPI)'",
       _gcf == [ut(11)] and "선행 청산 발화 (CPI)" in logs_at(ARM, ut(11), ut(11)), [hm(t) for t in _gcf])
    # 다음 날: 어제 앞당긴 값이 남아 있어도 오늘 표 시각(12:00)
    ev = {_t: run_auto(ARM, _t) for _t in (ut(11) + 86400, ut(12) + 86400)}
    _gcf = [t for t, (fl, q) in ev.items() for (_x, bk, a, dry) in fl if a == "GC-A"]
    ck(f"4d {TAG} 다음 날(화): 어제 앞당긴 값은 버리고 12:00 UTC", _gcf == [ut(12) + 86400], [hm(t) for t in _gcf])
    # 서버가 늦춘 값(13:00 UTC)은 따르지 않는다
    ARM._auto_fired, ARM._auto_retry = {}, {}
    CLK["t"] = ut(8, 0, 5)
    E.App._note_exit_ts(ARM, dict(sg, exit_ts=ut(13)))
    ev = {_t: run_auto(ARM, _t) for _t in (ut(12), ut(12, 0, 30), ut(13), ut(13, 0, 30))}
    _gcf = [t for t, (fl, q) in ev.items() for (_x, bk, a, dry) in fl if a == "GC-A"]
    ck(f"4e {TAG} 서버 청산 시각이 표보다 늦으면(13:00 UTC) 표 시각 12:00 UTC 그대로, 13:00 없음", _gcf == [ut(12)], [hm(t) for t in _gcf])

    # ═════ 6 프롭 거래일(Topstep 17:00 CT = 뉴욕 18:00 시작, Lucid 같은 경계) ═════════════════
    _days = {}
    for _nm, _t in (("금 진입 08:00 UTC", ut(8, 0, 5)), ("금 청산 12:00 UTC", ut(12)), ("나스닥 진입 10:00 뉴욕", ny(10, 0, 5)),
                    ("나스닥 청산 14:00 뉴욕", ny(14))):
        CLK["t"] = _t
        _days[_nm] = E._daycap_day_start()
    _key = f"{Y}-{M:02d}-{D:02d}"
    ck(f"6a {TAG} 거래일 키: 금 08:00~12:00 UTC와 나스닥 10:00~14:00 뉴욕이 모두 {_key}, 같은 시작(일 18:00 뉴욕)",
       {v[1] for v in _days.values()} == {_key} and {v[0] for v in _days.values()} == {_RDT(SY, SM, SD, 18, tzinfo=NYZ).astimezone(UTC)},
       {k: (str(v[0]), v[1]) for k, v in _days.items()})
    D6 = Stub()
    CLK["t"] = ut(12, 0, 30)
    E.App._daycap_set_hit(D6, "projectx", "S1", E._daycap_day_start()[1])       # 금 거래로 오늘 하루 상한에 닿음
    CLK["t"] = ny(10, 0, 5)
    _hit_nq = E.App._daycap_hit_today(D6, "projectx", "S1", E._daycap_day_start()[1])
    CLK["t"] = ut(8, 0, 5) + 86400
    _hit_next = E.App._daycap_hit_today(D6, "projectx", "S1", E._daycap_day_start()[1])
    ck(f"6b {TAG} 금 거래로 하루 상한에 닿으면 같은 날 나스닥 10:00 뉴욕은 '오늘 이미 닿음', 다음 날 금 08:00 UTC는 새 거래일",
       _hit_nq is True and _hit_next is False, (_hit_nq, _hit_next))
    PXHIST[:] = [{"creationTimestamp": _RDT.fromtimestamp(ut(12, 0, 1), UTC).isoformat(), "profitAndLoss": 300.0},
                 {"creationTimestamp": _RDT.fromtimestamp(ny(14, 0, 1), UTC).isoformat(), "profitAndLoss": 500.0},
                 {"creationTimestamp": _RDT.fromtimestamp(ut(12, 0, 1) + 86400, UTC).isoformat(), "profitAndLoss": -100.0}]
    D7 = Stub()
    CLK["t"] = ny(15)
    E.App._dc_hist_refresh(D7, FakeBroker("projectx", "S1"), "projectx", "S1", 1, _key)
    _hd = (D7._dc_st["hist"].get(E.App._daycap_key("projectx", "S1")) or {}).get("days")
    _nxt = (_RDT(Y, M, D) + DT.timedelta(days=1)).strftime("%Y-%m-%d")
    ck(f"6c {TAG} Topstep 거래 기록(최고일 재료): 금 12:00 UTC 청산 +300과 나스닥 14:00 뉴욕 청산 +500이 한 거래일 +800, 다음 날 금은 다음 거래일",
       _hd == {_key: 800.0, _nxt: -100.0}, _hd)

# ═════ 7 서버 sched 없음 = 마지막 정상 값 그대로, 서버가 {"default": true}로 되돌리면 금 뉴욕 02:00/06:00(2026-11-10 리뷰 P1) ═════
print("\n== 서버가 sched를 보내지 않음 / 되돌림 ==")
E.App._sched_apply(ARM, None)
ck("7a0 sched 없음(하트비트 하나가 빠짐) = 마지막 정상 값(아시아) 그대로, 디스크 값, 무장된 금 잡 그대로",
   E._ASSET_EXITS["GC"] == [("UTC", 12)] and os.path.exists(E._SCHED_PATH)
   and [(j["tz"], j["hour"]) for j in ARM._auto_accts[("GC", 0)]] == [("UTC", 12)])
E.App._sched_apply(ARM, {"default": True})
ck("7a 서버가 되돌림({\"default\": true}) = 금 표 뉴욕 02→06, 디스크 값 지움, 무장된 금 잡도 뉴욕 06",
   E._ASSET_ENTRIES["GC"] == [("America/New_York", 2)] and E._ASSET_EXITS["GC"] == [("America/New_York", 6)]
   and not os.path.exists(E._SCHED_PATH) and [(j["tz"], j["hour"]) for j in ARM._auto_accts[("GC", 0)]] == [("America/New_York", 6)]
   and ARM._auto_accts[("NQ", 0)] == _nq_jobs0 and ARM._auto_accts[("BTC", 0)] == _btc_jobs0)
E._sched_load()
E.App._sched_apply(ARM, None)
ck("7b 디스크 값 없이 다시 기동해도(_sched_load), 그 뒤 sched 없는 하트비트(옛 서버)에도 기본 그대로", E._ASSET_ENTRIES["GC"] == [("America/New_York", 2)]
   and E._ASSET_EXITS["GC"] == [("America/New_York", 6)])
for TAG, (Y, M, D) in DATES:
    ny = lambda h, mi=0, s=0: T(Y, M, D, h, mi, s, NYZ)          # noqa: E731
    ut = lambda h, mi=0, s=0: T(Y, M, D, h, mi, s)               # noqa: E731
    CLK["t"] = ut(0)
    ck(f"7c {TAG} 다음 금 진입 = 뉴욕 02:00({hm(ny(2))} UTC)", E._next_entry_dt("GC").timestamp() == ny(2), E._next_entry_dt("GC"))
    st = armed()
    g = [(t, w) for t, a, w in precheck_sim(st, ut(0), ut(9)) if a == "GC"]
    ck(f"7d {TAG} 금 사전 점검 = 뉴욕 00:50(T-70), 01:50(T-10)", g == [(ny(0, 50), 70), (ny(1, 50), 10)], [(hm(t, NYZ), w) for t, w in g])
    sg = sig_of("GC", f"{Y}{M:02d}{D:02d}-GC-old", ny(2), ny(6))
    _pl = run_loop(st, sg, ny(2, 0, 5))
    ck(f"7e {TAG} 뉴욕 02:00:05 금 신호 → GC 2, 신호 손절로 주문", [(p["con"], p["size"], p["stop"]) for p in _pl] == [("GC 12-26", 2, 4130.4)], _pl)
    ARM._auto_fired, ARM._auto_retry, ARM._exit_override = {}, {}, {}
    ARM._entered_at = {"GC": ny(2, 0, 6)}
    ev = {_t: run_auto(ARM, _t) for _t in (ny(5, 59, 50), ny(6), ny(6, 0, 30), ut(12))}
    _gcf = [t for t, (fl, q) in ev.items() for (_x, bk, a, dry) in fl if a == "GC-A"]
    ck(f"7f {TAG} 금 마감 청산 = 뉴욕 06:00({hm(ny(6))} UTC) 한 번, 12:00 UTC 없음", _gcf == [ny(6)], [hm(t) for t in _gcf])

DT.datetime = _RDT
print(f"\nPASS {NPASS[0]}, FAIL {len(FAIL)}" + (": " + "; ".join(FAIL) if FAIL else ""))
sys.exit(1 if FAIL else 0)
