# EQ Autopilot 2026.10.21p - 라이브 패널 보유 계약, 평가 손익 줄 오프라인 시험(대표 2026-10-27, 회원 "오늘 진입이 됐는지 앱에서 보기 어렵다",
# 대표 "가능함 손익액도"). 2026-10-27 밤 설계 변경(대표 "IBKR은 웹처럼 계산해 보여주면 되잖아 ... 아님 다른 애들도 진입가격, 현재가격으로 계산",
# "체결가격으로 쓰면", "보유계약 손익은 진입 후 좀 이따 보여줘두 대 이삼초 후. 진입이 젤 중요"): 브로커를 읽지 않는다 - 보유 = 앱의 체결 기록,
# 청산 = 앱의 청산 경로, 체결가 = 체결 갭 보고(없으면 신호 기준가), 현재가 = 우리 서버 /eqpx. 네트워크 없음, 가짜 서버. 0 = 통과.
import inspect
import json
import os
import socket
import sys
import tempfile
import threading
import time
import datetime as dt

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_pos_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402
import autopilot_crypto  # noqa: E402
import requests  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402
from eqexec.broker.base import Position  # noqa: E402
from eqexec import sizing as _sz  # noqa: E402

E._kc_load = lambda *a, **k: ""
E._kc_save = lambda *a, **k: False
E._POS_TODAY_PATH = os.path.join(HOME, ".pos_today.json")
FAIL = []
TEXTS = []
NCHK = [0]


def ck(n, c, d=""):
    NCHK[0] += 1
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:700]}]"))
    if not c:
        FAIL.append(n)


def et(y, mo, d, h, mi=0, s=0):
    """America/New_York 벽시계 → epoch(2026-10월은 EDT, UTC-4)."""
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone(dt.timedelta(hours=-4))).timestamp()


def utc(y, mo, d, h, mi=0, s=0):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).timestamp()


# ── 가짜 시계, 가짜 서버, 브로커 호출 감시 ──────────────────────────────────
CLOCK = [0.0]
E._pos_clock = lambda: CLOCK[0]


def at(t):
    CLOCK[0] = float(t)
    return CLOCK[0]


TOKEN = "tok-abc"
TID = autopilot_crypto.path_id(TOKEN)
REQ = []                      # (url, params, timeout, 그 순간 시계)
SRV = {}                      # inst → ("ok", px, at[, stale]) | ("code", n) | ("exc",) | ("badjson",) | ("hook", fn, 다음 응답)


class Resp:
    def __init__(self, code, body=None, bad=False):
        self.status_code, self._b, self._bad = code, body, bad
        self.ok = 200 <= code < 300
        self.text = "x"

    def json(self):
        if self._bad:
            raise ValueError("bad json")
        return self._b


def fake_srv_get(url, params=None, timeout=None, **k):
    REQ.append((url, dict(params or {}), timeout, CLOCK[0]))
    beh = SRV.get((params or {}).get("inst"), ("code", 404))
    if beh[0] == "hook":
        beh[1]()
        beh = beh[2]
    if beh[0] == "ok":
        body = {"px": beh[1]}
        if beh[2] is not None:
            body["at"] = beh[2]
        if len(beh) > 3 and beh[3] is not None:
            body["stale"] = beh[3]
        return Resp(200, body)
    if beh[0] == "code":
        return Resp(beh[1], {})
    if beh[0] == "badjson":
        return Resp(200, None, bad=True)
    raise requests.exceptions.Timeout("read timeout")


E._srv_get = fake_srv_get
BROKER_CALLS = []


def _no_build(*a, **k):
    BROKER_CALLS.append(("build", a))
    raise RuntimeError("the position line must not build a broker")


E._build_broker = _no_build


class SpyBroker:
    """어떤 메서드든 부르면 기록한다(이 기능은 이름, 계좌 목록 속성만 읽어야 한다)."""
    def __init__(self, name, accounts):
        self.__dict__["name"] = name
        self.__dict__["cfg"] = type("C", (), {"accounts": list(accounts)})()

    def __getattr__(self, k):
        def _f(*a, **kw):
            BROKER_CALLS.append((self.name, k))
            return []
        return _f


class T(E.App):
    def __init__(self, lang="ko"):
        self.lang = lang
        self._token = TOKEN
        self._open_assets = set()
        self._open_ctx = {}
        self._sig_accts = {}
        self._auto_accts = {}
        self._auto_fired = {}
        self._exit_override = {}
        self._entering_at = {}
        self._auto_retry = {}
        self._pos_today = {}
        self._pos_px = {}
        self._acfg = {a: {"accounts": [], "creds": {}} for a in E._ASSETS}
        self._profile = {}
        self._gate = {"ok": False, "caps": {}}
        self._flat_seen = {}
        self.UI = []
        self.REFRESH = 0
        self.UI_THREADS = []
        self.LOGS = []

    def _ui(self, fn, ms=0):
        self.UI.append(fn)
        self.UI_THREADS.append(threading.get_ident())

    def _refresh_live_panel(self):
        self.REFRESH += 1

    def _save_cfg(self, **k):
        pass

    def log(self, m):
        self.LOGS.append(str(m))

    def _clock_skew_now(self):
        return None

    def _forget_open_ctx(self, a):
        pass

    def _restore_open_ctx(self, a):
        return False


def txt(app, a, now=None):
    s = E.App._open_pos_text(app, a, CLOCK[0] if now is None else now)
    TEXTS.append(s)
    return s


def fill(app, asset, sym, q, acct, sid="S1", side="LONG", filled=None):
    """진짜 _note_fill 경로(체결 보고 누적 + 최근 진입 기록 + 열린 자산)."""
    E.App._note_fill(app, asset, micro=q, acct=acct, leg=(sym, q, q if filled is None else filled), sid=sid, side=side)


def tick(app, t=None):
    if t is not None:
        at(t)
    return E.App._pos_poll_tick(app)


def reset():
    REQ.clear()
    SRV.clear()
    BROKER_CALLS.clear()


NOON = et(2026, 10, 27, 12, 0)           # 화 12:00 ET - NQ 보유 시간, 어느 신호 시각과도 멀다
NQ_ENT, NQ_EXIT = et(2026, 10, 27, 10), et(2026, 10, 27, 14)
GC_ENT, GC_EXIT = et(2026, 10, 27, 2), et(2026, 10, 27, 6)
T0 = et(2026, 10, 27, 10, 0, 32)         # NQ 진입 봉 마감 32초 뒤 체결(첫 요청은 봉 마감 30초 뒤부터, 재검토 P2-1)

# ═════ 0 버전, 브로커 읽기 코드가 없다 ═════
ck("0a 앱 버전 2026.10.21z 이상", E.App._APP_VER >= "2026.10.21z", E.App._APP_VER)


def _names(fn):
    """함수가 실제로 쓰는 이름(전역, 속성) - 주석, 설명문은 빼고 코드만(안쪽 함수 포함)."""
    out, todo = set(), [getattr(fn, "__code__", None)]
    while todo:
        c = todo.pop()
        if c is None:
            continue
        out |= set(c.co_names)
        todo += [k for k in c.co_consts if hasattr(k, "co_names")]
    return out


_fns = [getattr(E.App, n) for n in dir(E.App) if n.startswith("_pos_") and callable(getattr(E.App, n))]
_fns += [E.App._open_pos_text, E._load_pos_today, E._pos_rec_clean, E._pos_rec_until, E._pos_next_entry_after, E._pos_entry_quiet]
_used = set().union(*[_names(f) for f in _fns])
_bad = sorted(_used & {"list_open_positions", "open_positions", "_accounts", "_build_broker", "snapshot_pair", "mark_price", "position_qty",
                       "IBKRBroker", "_passtp_broker", "_dc_gate", "current_market_price", "_JSON_LOCK", "_write_json_atomic"})
ck("0b 보유 줄 코드가 쓰는 이름에 브로커 호출, 원장 잠금(_JSON_LOCK), 잠그는 원장 쓰기가 없다", not _bad, _bad)
ck("0b2 기록 파일은 잠금 없는 쓰기(_write_json_atomic_nolock) + 전용 잠금(_POS_FILE_LOCK)",
   {"_write_json_atomic_nolock", "_POS_FILE_LOCK"} <= _names(E.App._pos_flush_today), _names(E.App._pos_flush_today))
ck("0c 옛 브로커 읽기 함수가 없다(IBKR clientId 충돌, 손으로 연 포지션 집계를 원천 제거)",
   not any(hasattr(E.App, n) for n in ("_pos_read_px", "_pos_read_nt8", "_pos_read_crypto", "_pos_read_ibkr", "_pos_broker", "_pos_sources"))
   and not hasattr(E, "_POS_BROKERS"))

# ═════ 1 문구(KO/EN) - '오늘' 없음(대표 결정 A) ═════
ck("1a 보유 KO", E._pos_line_hold("NQ", {"MNQ": 5}, 2, 812.4, True) == "보유 MNQ 5계약(2계좌), 평가 손익 +$812")
ck("1b 보유 EN", E._pos_line_hold("NQ", {"MNQ": 5}, 2, 812.4, False) == "Holding MNQ 5 (2 accounts), open P&L +$812")
ck("1c 청산 KO/EN '최근 진입 MNQ 5계약, 청산됨' / 'Last entry MNQ 5, closed'",
   E._pos_line_closed("NQ", {"MNQ": 5}, True) == "최근 진입 MNQ 5계약, 청산됨"
   and E._pos_line_closed("NQ", {"MNQ": 5}, False) == "Last entry MNQ 5, closed",
   (E._pos_line_closed("NQ", {"MNQ": 5}, True), E._pos_line_closed("NQ", {"MNQ": 5}, False)))
ck("1d 확인 중 KO/EN '최근 진입 MNQ 5계약, 보유 확인 중' / 'Last entry MNQ 5, checking position'",
   E._pos_line_pending("NQ", {"MNQ": 5}, True) == "최근 진입 MNQ 5계약, 보유 확인 중"
   and E._pos_line_pending("NQ", {"MNQ": 5}, False) == "Last entry MNQ 5, checking position")
ck("1e EN 1계좌 단수, 손실 -$, 0은 $0, 섞인 심볼 미니 먼저",
   E._pos_line_hold("GC", {"MGC": 2}, 1, -45.4, False) == "Holding MGC 2 (1 account), open P&L -$45"
   and E._pos_usd(0.3) == "$0" and E._pos_usd(1234.6) == "+$1,235"
   and E._pos_line_hold("NQ", {"MNQ": 3, "NQ": 1}, 1, 10, True) == "보유 NQ 1 + MNQ 3계약(1계좌), 평가 손익 +$10")
ck("1f BTC 코인 수량, 손익 모름 '-'", E._pos_line_hold("BTC", {"BTC": 0.0120000001}, 1, None, False) == "Holding BTC 0.012 (1 account), open P&L -"
   and E._pos_line_closed("BTC", {"BTC": 0.02}, True) == "최근 진입 BTC 0.02, 청산됨")
ck("1g 낡은 값 KO/EN", E._pos_age_txt(185, True) == " (3분 전 기준)" and E._pos_age_txt(185, False) == " (as of 3 min ago)"
   and E._pos_age_txt(3 * 3600, True) == " (3시간 전 기준)")
ck("1h 뿌리: ProjectX ENQ/GCE → NQ/GC, NT8, IB, 크립토, 사이징 심볼",
   [E._pos_root(s) for s in ("CON.F.US.ENQ.Z26", "CON.F.US.GCE.Z26", "CON.F.US.MNQ.Z26", "MNQ 12-26", "NQZ6", "BTCUSDT", "BTC", "MGC")]
   == ["NQ", "GC", "MNQ", "MNQ", "NQ", "BTC", "BTC", "MGC"])
ck("1i 포인트 가치 MNQ 2, NQ 20, MGC 10, GC 100, BTC 1(코인), 사이징 정본(FUTURES_POINT_VALUE)과 일치",
   E._POS_PV == {"MNQ": 2.0, "NQ": 20.0, "MGC": 10.0, "GC": 100.0, "BTC": 1.0}
   and all(E._POS_PV[k] == v for k, v in _sz.FUTURES_POINT_VALUE.items()), (E._POS_PV, _sz.FUTURES_POINT_VALUE))

# ═════ 2 체결 직후 = 체결 기록만으로 '확인 중'(네트워크 없음), 진입이 끝나고 3초 뒤 첫 시세 → 보유 + 손익 ═════
reset()
SRV["NQ"] = ("ok", 20081.2, None)
me = T()
me._sig_accts = {("NQ", 0): {"broker": "projectx", "acct": "A1"}, ("NQ", 1): {"broker": "ibkr", "acct": "DU1"},
                 ("NQ", 2): {"broker": "projectx", "acct": "A3"}}
at(T0 - 1)
_seen_busy = []


def _enter(cfg, sig, asset, direction, stop, mult):
    _seen_busy.append(dict(me._pos_busy))
    me._entering_at[asset] = CLOCK[0]
    at(T0)
    fill(me, "NQ", "MNQ", 3, "projectx:A1")
    n_in = tick(me, T0 + 0.5)                       # 진입 스레드가 아직 도는 중
    _seen_busy.append(("tick", n_in, len(REQ)))
    at(T0 + 1)
    fill(me, "NQ", "MNQ", 2, "ibkr:DU1")
    at(T0 + 2)


me._enter_account = _enter
E.App._pos_entry_run(me, {"acct": "A1"}, {"id": "S1"}, "NQ", "LONG", 19900.0, 1.0)
ck("2a 진입 스레드 몸통 = _enter_account 그대로, 도는 동안 진입 중 1, 끝나면 0",
   _seen_busy[0].get("NQ") == 1 and me._pos_busy.get("NQ") == 0, (_seen_busy, me._pos_busy))
ck("2b 진입 스레드가 도는 동안 바퀴 = 서버 0회", _seen_busy[1] == ("tick", 0, 0), _seen_busy)
ck("2c 체결 직후 줄 = '최근 진입 MNQ 5계약, 보유 확인 중'(체결 기록만)", txt(me, "NQ", T0 + 2) == "최근 진입 MNQ 5계약, 보유 확인 중",
   txt(me, "NQ", T0 + 2))
n = tick(me, T0 + 4)
ck("2d 진입 스레드 끝 2초 뒤 = 아직 묻지 않음(_POS_IDLE_S 3초), 줄은 '확인 중'", n == 0 and not REQ
   and txt(me, "NQ") == "최근 진입 MNQ 5계약, 보유 확인 중", REQ)
n = tick(me, T0 + 5.5)
ck("2e 진입 끝 3초 넘음 = 서버 시세 1회", n == 1 and len(REQ) == 1, REQ)
ck("2f 요청 = GET PUSH_BASE+'eqpx', params inst=NQ, tid=path_id(토큰), 시간 제한 2초",
   REQ[0][0] == E.PUSH_BASE + "eqpx" and REQ[0][1] == {"inst": "NQ", "tid": TID} and REQ[0][2] == 2, REQ[0])
s = txt(me, "NQ", T0 + 6)
ck("2g 보유 = 앱이 체결한 계좌만(무장했지만 체결 없는 A3 제외), 체결가, 기준가 모르면 손익 '-'",
   s == "보유 MNQ 5계약(2계좌), 평가 손익 -", s)
ck("2h 기능 전체에서 브로커 호출 0(IBKR 계좌 포함)", not BROKER_CALLS, BROKER_CALLS)

# 기준가 대체(entry_ref): 신호 피드의 같은 신호 id
reset()
SRV["NQ"] = ("ok", 20081.2, None)
me = T()
me._last_feed_sig = {"id": "S2", "entry_ref": 20000.0}
at(NOON)
fill(me, "NQ", "MNQ", 3, "projectx:A1", sid="S2")
fill(me, "NQ", "MNQ", 2, "ibkr:DU1", sid="S2")
tick(me, NOON + 4)
s = txt(me, "NQ", NOON + 4)
ck("2i 체결가를 아직 모르면 신호 기준가(entry_ref): (20081.2 - 20000) x 5 x $2 = +$812(IBKR 계좌도 같은 식)",
   s == "보유 MNQ 5계약(2계좌), 평가 손익 +$812", s)
# 체결 갭 보고가 읽은 평균 체결가가 오면 그것으로(실제 _send_gap 경로)
me._gate = {"ok": True, "caps": {"autoentry": True}}
_posted = []
_o_post = requests.post
requests.post = lambda url, **k: (_posted.append(url), type("R", (), {"ok": True, "text": "gap:ok"})())[1]


class GapB:
    name = "projectx"

    class cfg:
        accounts = ["A1"]

    def list_open_positions(self):
        return [Position("A1", "A1", "CON.F.US.MNQ.Z26", 3, {"averagePrice": 20010.0})]


try:
    E.App._send_gap(me, "NQ", {"id": "S2", "entry_ref": 20000.0, "direction": "LONG"}, GapB(), "CON.F.US.MNQ.Z26")
    _t0 = time.time()
    while time.time() - _t0 < 5 and not me._pos_today["NQ"].get("avg"):
        time.sleep(0.02)
    _t0 = time.time()
    while time.time() - _t0 < 3 and not _posted:
        time.sleep(0.02)
finally:
    requests.post = _o_post
rec = me._pos_today["NQ"]
ck("2j 체결 갭 보고(/eqgap)가 읽은 평균 체결가가 기록에(계좌 'projectx:A1', 뿌리 MNQ), 갭 보고는 종전대로 나감",
   rec.get("avg") == {"MNQ": 20010.0} and rec.get("avg_by") == {"projectx:A1": {"MNQ": 20010.0}} and _posted == [E.PUSH_BASE + "eqgap"],
   (rec, _posted))
s = txt(me, "NQ", NOON + 5)
ck("2k 체결가 20010(갭 보고 대표값, 같은 신호 같은 순간 시장가) → (20081.2 - 20010) x 5 x $2 = +$712",
   s == "보유 MNQ 5계약(2계좌), 평가 손익 +$712", s)
E.App._pos_fill_px(me, "NQ", "S2", SpyBroker("ibkr", ["DU1"]), "MNQZ6", 20020.0)
s = txt(me, "NQ", NOON + 5)
ck("2l 계좌별 체결가가 있으면 그 계좌는 그 값: A1 3 x 71.2 x 2 + DU1 2 x 61.2 x 2 = +$672, 브로커 메서드 호출 0",
   s == "보유 MNQ 5계약(2계좌), 평가 손익 +$672" and not BROKER_CALLS, (s, BROKER_CALLS))
E.App._pos_fill_px(me, "NQ", "OTHER", SpyBroker("ibkr", ["DU1"]), "MNQZ6", 1.0)
ck("2m 다른 신호의 갭 값은 이 기록에 넣지 않음", me._pos_today["NQ"]["avg_by"]["ibkr:DU1"] == {"MNQ": 20020.0})
# IBKR 계좌만: 계약과 손익을 같은 식으로(종전 '-' 아님)
reset()
SRV["GC"] = ("ok", 4012.5, None)
me = T()
at(et(2026, 10, 27, 3))
fill(me, "GC", "MGC", 3, "ibkr:DU111", sid="I1")
E.App._pos_fill_px(me, "GC", "I1", SpyBroker("ibkr", ["DU111"]), "MGCZ6", 4000.0)
tick(me, CLOCK[0] + 4)
ck("2n IBKR: 'MGC 3계약(1계좌), (4012.5 - 4000) x 3 x $10 = +$375', 브로커 0",
   txt(me, "GC") == "보유 MGC 3계약(1계좌), 평가 손익 +$375" and not BROKER_CALLS, (txt(me, "GC"), BROKER_CALLS))
# 체결가도 기준가도 없으면 '-'
reset()
SRV["GC"] = ("ok", 4010.0, None)
me = T()
at(et(2026, 10, 27, 3))
fill(me, "GC", "MGC", 2, "nt8:LT-1", sid="G1", side="SHORT")
tick(me, CLOCK[0] + 4)
ck("2o 체결가, 기준가 모두 모르면 손익 '-'(지어내지 않음)", txt(me, "GC") == "보유 MGC 2계약(1계좌), 평가 손익 -", txt(me, "GC"))
E.App._pos_fill_px(me, "GC", "G1", SpyBroker("nt8", ["LT-1"]), "MGC 12-26", 4000.0)
ck("2p 숏: (4010 - 4000) x -2 x $10 = -$200", txt(me, "GC") == "보유 MGC 2계약(1계좌), 평가 손익 -$200", txt(me, "GC"))
me.lang = "en"
ck("2q 같은 상태 EN", txt(me, "GC") == "Holding MGC 2 (1 account), open P&L -$200", txt(me, "GC"))
me.lang = "ko"
fill(me, "GC", "MGC", 1, "nt8:LT-2", sid="G1", side="LONG")
tick(me, CLOCK[0] + 31)
ck("2r 같은 기록에 방향이 섞이면(모름) 손익 '-'", txt(me, "GC") == "보유 MGC 3계약(2계좌), 평가 손익 -", txt(me, "GC"))
# 미니 + 마이크로, 풀사이즈 포인트 가치
reset()
SRV["NQ"] = ("ok", 20010.0, None)
me = T()
at(NOON)
fill(me, "NQ", "NQ", 1, "projectx:A1", sid="S3", side="SHORT")
fill(me, "NQ", "MNQ", 3, "projectx:A1", sid="S3", side="SHORT")
E.App._pos_fill_px(me, "NQ", "S3", SpyBroker("projectx", ["A1"]), "CON.F.US.ENQ.Z26", 20000.0)
tick(me, NOON + 4)
ck("2s 숏 NQ 1 + MNQ 3: -10 x (20 + 3 x 2) = -$260(미니 체결가를 같은 계좌 마이크로에도)",
   txt(me, "NQ") == "보유 NQ 1 + MNQ 3계약(1계좌), 평가 손익 -$260", txt(me, "NQ"))

# ═════ 3 서버 시세 클라이언트 ═════
reset()
me = T()
at(NOON)
fill(me, "NQ", "MNQ", 5, "projectx:A1", sid="S4")
me._pos_today["NQ"]["ref"] = 20000.0
SRV["NQ"] = ("code", 403)
tick(me, NOON + 4)
ck("3a 403 = 창구 없음, 손익 '-'(보유는 체결 기록 그대로)", txt(me, "NQ") == "보유 MNQ 5계약(1계좌), 평가 손익 -" and len(REQ) == 1,
   (txt(me, "NQ"), REQ))
tick(me, NOON + 40)
tick(me, NOON + 200)
ck("3b 403 뒤 5분은 다시 묻지 않음", len(REQ) == 1, REQ)
SRV["NQ"] = ("ok", 20050.0, None)
tick(me, NOON + 4 + 301)
ck("3c 5분 뒤 다시 물음 → +$500", len(REQ) == 2 and txt(me, "NQ") == "보유 MNQ 5계약(1계좌), 평가 손익 +$500", (REQ, txt(me, "NQ")))
SRV["NQ"] = ("code", 404)
tick(me, CLOCK[0] + 31)
ck("3d 404도 창구 없음 = '-'", txt(me, "NQ") == "보유 MNQ 5계약(1계좌), 평가 손익 -", txt(me, "NQ"))
# 시간 초과, 5xx, 깨진 JSON, px 없음 = 지난 시세를 그 시각 그대로(손익 깜빡임 없음), 2분 넘으면 낡음 표시
reset()
me = T()
at(NOON)
fill(me, "NQ", "MNQ", 5, "projectx:A1", sid="S5")
me._pos_today["NQ"]["ref"] = 20000.0
SRV["NQ"] = ("ok", 20050.0, None)
tick(me, NOON + 4)
t_good = NOON + 4
_mid = []
for beh in (("exc",), ("code", 502), ("badjson",), ("ok", None, NOON + 30), ("ok", 0, NOON + 30)):
    SRV["NQ"] = beh
    tick(me, CLOCK[0] + 31)
    _mid.append(txt(me, "NQ"))
s = txt(me, "NQ")
ck("3e 시간 초과, 502, 깨진 JSON, px null, px 0 = 지난 손익 그대로(중간에 '-'로 깜빡이지 않음), 2분 넘으면 낡음 표시",
   all("+$500" in x for x in _mid) and s == f"보유 MNQ 5계약(1계좌), 평가 손익 +$500{E._pos_age_txt(CLOCK[0] - t_good, True)}"
   and len(REQ) == 6, (_mid, s, REQ))
SRV["NQ"] = ("ok", 20060.0, None, True)
tick(me, CLOCK[0] + 31)
ck("3f stale=참인데 at 없음 = 쓰지 않음(언제 값인지 모름)", txt(me, "NQ").startswith("보유 MNQ 5계약(1계좌), 평가 손익 +$500 ("), txt(me, "NQ"))
SRV["NQ"] = ("ok", 20060.0, CLOCK[0] + 31 - 200, True)
tick(me, CLOCK[0] + 31)
ck("3g stale=참 + at = 그 값, 나이는 at 기준(3분 전)", txt(me, "NQ") == "보유 MNQ 5계약(1계좌), 평가 손익 +$600 (3분 전 기준)", txt(me, "NQ"))
SRV["NQ"] = ("ok", 20060.0, CLOCK[0] + 31 - 5)
tick(me, CLOCK[0] + 31)
ck("3h 새 시세 = 낡음 표시 없음", txt(me, "NQ") == "보유 MNQ 5계약(1계좌), 평가 손익 +$600", txt(me, "NQ"))
me.lang = "en"
ck("3i EN 낡음 '(as of 2 min ago)'", txt(me, "NQ", CLOCK[0] + 130).endswith("open P&L +$600 (as of 2 min ago)"), txt(me, "NQ", CLOCK[0] + 130))
me.lang = "ko"
me._clock_skew_now = lambda: 60.0               # PC가 서버보다 60초 빠름 - at(서버 시각) 나이는 서버 시각으로
ck("3j 시세 나이는 서버 시각으로(PC 시계 차이 뺌)", txt(me, "NQ", CLOCK[0] + 150) == "보유 MNQ 5계약(1계좌), 평가 손익 +$600",
   txt(me, "NQ", CLOCK[0] + 150))
me._clock_skew_now = lambda: None
me._token = ""
_nreq = len(REQ)
tick(me, CLOCK[0] + 31)
ck("3k 토큰 없음 = 묻지 않고 '-'", txt(me, "NQ") == "보유 MNQ 5계약(1계좌), 평가 손익 -" and len(REQ) == _nreq, REQ[_nreq:])
me._token = TOKEN
# 어제 시세는 오늘 체결에 쓰지 않는다(P2-2)
reset()
me = T()
at(NOON - 86400)
fill(me, "NQ", "MNQ", 1, "projectx:A1", sid="Y1")
me._pos_today["NQ"]["ref"] = 20000.0
SRV["NQ"] = ("ok", 20100.0, None)
tick(me, NOON - 86400 + 4)
E.App._send_fill(me, "NQ", closed=True)
at(et(2026, 10, 27, 10, 0, 3))
fill(me, "NQ", "MNQ", 2, "projectx:A1", sid="Y2")
me._pos_today["NQ"]["ref"] = 20000.0
ck("3l 새 체결 직후 = 어제 시세로 보유를 보이지 않고 '확인 중'", txt(me, "NQ") == "최근 진입 MNQ 2계약, 보유 확인 중", txt(me, "NQ"))
SRV["NQ"] = ("exc",)
tick(me, CLOCK[0] + 32)
ck("3m 새 체결 뒤 첫 요청이 실패 = 보유는 체결 기록, 손익은 '-'(어제 시세로 +$400 아님)",
   txt(me, "NQ") == "보유 MNQ 2계약(1계좌), 평가 손익 -" and len(REQ) == 2, (txt(me, "NQ"), REQ))

# ═════ 4 쉬는 때(결정 B) ═════
reset()
me = T()
me._sig_accts = {("NQ", 0): {"acct": "A1"}}
ck("4a 진입 봉 마감 90초 전부터 쉼, 91초 전은 묻는다", E.App._pos_net_quiet(me, NQ_ENT - 89) and not E.App._pos_net_quiet(me, NQ_ENT - 91))
ck("4b 봉 마감 29초 뒤 쉼, 30초 뒤부터(진입이 없으면) 묻는다(재검토 P2-1)", E.App._pos_net_quiet(me, NQ_ENT + 29) and not E.App._pos_net_quiet(me, NQ_ENT + 30.5))
me._pos_busy = {"GC": 1}
ck("4c 어느 자산이든 진입 스레드가 돌면 쉼", E.App._pos_net_quiet(me, NOON))
me._pos_busy = {"GC": 0}
me._pos_act = {"GC": NOON - 2}
ck("4d 진입 스레드 끝(또는 체결) 2초 = 쉼, 3초 = 묻는다", E.App._pos_net_quiet(me, NOON) and not E.App._pos_net_quiet(me, NOON + 1.01))
me._pos_act = {}
me._entering_at = {"NQ": NOON - 30}
ck("4e 진입 시작(_entering_at) 뒤 끝난 진입 스레드가 없으면(신호 루프 밖 진입) 90초 쉼", E.App._pos_net_quiet(me, NOON)
   and not E.App._pos_net_quiet(me, NOON + 61))
me._pos_busy_end = {"NQ": NOON - 10}
ck("4f 그 진입 스레드가 끝났으면 진입 시작 기록으로는 쉬지 않음", not E.App._pos_net_quiet(me, NOON))
me._entering_at, me._pos_busy_end = {}, {}
me._sig_accts = {("NQ", 0): {"acct": "A1"}, ("GC", 0): {"acct": "A1"}}
ck("4g 무장된 다른 자산(GC 02:00 ET) 신호 앞뒤도 쉼", E.App._pos_net_quiet(me, GC_ENT - 30) and E.App._pos_net_quiet(me, GC_ENT + 29)
   and not E.App._pos_net_quiet(me, GC_ENT + 31))
# 마감 청산: 90초 전 ~ 그 청산 일이 끝남(+3초)
me._auto_accts = {("NQ", 0): [{"asset": "NQ", "hour": 14, "tz": "America/New_York", "acct": "A1", "broker": "projectx"},
                              {"asset": "NQ", "hour": 14, "tz": "America/New_York", "acct": "A2", "broker": "projectx"}]}
ck("4h 마감 청산 89초 전 쉼, 91초 전 묻는다", E.App._pos_net_quiet(me, NQ_EXIT - 89) and not E.App._pos_net_quiet(me, NQ_EXIT - 91))
me._auto_fired = {("NQ", 14, "A1"): "2026-10-27"}
ck("4i 마감 청산 일이 하나라도 남으면(A2) 2분 뒤에도 쉼", E.App._pos_net_quiet(me, NQ_EXIT + 120))
me._auto_fired[("NQ", 14, "A2")] = "2026-10-27"
ck("4j 다 끝난 것을 처음 본 바퀴 = 쉼, 그 3초 뒤부터 묻는다", E.App._pos_net_quiet(me, NQ_EXIT + 125) and E.App._pos_net_quiet(me, NQ_EXIT + 127)
   and not E.App._pos_net_quiet(me, NQ_EXIT + 128.5))
me._auto_retry = {("NQ", 14, "A2"): {"n": 1}}
ck("4k 마감 청산 재시도 중 = 쉼", E.App._pos_net_quiet(me, NQ_EXIT + 600))
me._auto_retry, me._auto_accts, me._pos_act = {}, {}, {}
ck("4l 무장된 마감 청산이 없으면 청산 시각 30초 뒤까지만", E.App._pos_net_quiet(me, NQ_EXIT + 29) and not E.App._pos_net_quiet(me, NQ_EXIT + 31))
me._sig_force_id = "2026-10-27-NQ-1"
ck("4m 놓친 신호 수동 진입이 걸려 있으면 쉼(재검토 P2-1)", E.App._pos_net_quiet(me, NQ_ENT + 3600))
me._sig_force_id = None
import inspect as _ins
_kick = _ins.getsource(E.App._pos_poll_kick)
ck("4n 조회 스레드는 기동 30초 뒤에 늘 띄운다(진입 스레드 안에서 처음 띄우지 않게, 재검토 P3)", "self._pos_poll_start()" in _kick and "_pos_open_assets" not in _kick)
me._exit_override = {"NQ": (et(2026, 10, 27, 13, 0), "FOMC")}
ck("4m 서버가 앞당긴 청산(13:00 ET) 90초 전부터 쉼", E.App._pos_net_quiet(me, et(2026, 10, 27, 12, 58, 40))
   and not E.App._pos_net_quiet(me, et(2026, 10, 27, 12, 58, 20)))
me._exit_override = {}
me._sig_accts = {("BTC", 0): {"acct": ""}}
ck("4n BTC 4시간 봉 판정(14:00 UTC) 앞 쉼", E.App._pos_net_quiet(me, utc(2026, 10, 26, 13, 59)) and not E.App._pos_net_quiet(me, utc(2026, 10, 26, 12)))
me._pos_act = {"BTC": utc(2026, 10, 26, 12) + 3600}
ck("4o PC 시계가 뒤로 크게 뛰어 사건 시각이 미래여도 영원히 쉬지 않음", not E.App._pos_net_quiet(me, utc(2026, 10, 26, 12)))
me._pos_act = {}
me._entering_at = "broken"
me._pos_busy = None
ck("4p 상태가 이상해도 예외 없이 판단", E.App._pos_net_quiet(me, utc(2026, 10, 26, 12)) is False)
_o = E._pos_entry_quiet
E._pos_entry_quiet = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
ck("4q 계산이 터지면 묻지 않는 쪽(True)", E.App._pos_net_quiet(me, NOON) is True)
E._pos_entry_quiet = _o

# 묻기 직전마다 시계를 새로 읽는다(P2-1): 첫 자산 요청이 2초 걸린 사이 진입이 시작되면 다음 자산은 묻지 않는다
reset()
me = T()
at(NOON)
fill(me, "BTC", "BTC", 0.01, "bybit:", sid="B1")
fill(me, "GC", "MGC", 1, "projectx:A1", sid="G1")
fill(me, "NQ", "MNQ", 1, "projectx:A1", sid="N1")


def _slow():
    at(CLOCK[0] + 2)
    E.App._pos_busy_mark(me, "NQ", 1)


SRV["BTC"] = ("hook", _slow, ("ok", 70000.0, None))
SRV["GC"] = ("ok", 4000.0, None)
SRV["NQ"] = ("ok", 20000.0, None)
n = tick(me, NOON + 10)
ck("4r 바퀴 안에서 자산마다 새 시계: BTC 요청 중 진입 시작 → GC, NQ는 묻지 않음", n == 1 and [r[1]["inst"] for r in REQ] == ["BTC"], REQ)
E.App._pos_busy_mark(me, "NQ", -1)
REQ.clear()
_o_q = E.App._pos_net_quiet
_qt = []
E.App._pos_net_quiet = lambda self, t: (_qt.append(t), _o_q(self, t))[1]
SRV["GC"] = ("hook", lambda: at(CLOCK[0] + 2), ("ok", 4000.0, None))
try:
    n = tick(me, NOON + 30)
finally:
    E.App._pos_net_quiet = _o_q
ck("4s 쉬는 때 판단 시각 = 묻기 직전 시계(바퀴 시작 시각 아님): GC 판단 30초, NQ 판단 32초",
   [round(x - NOON, 1) for x in _qt] == [30.0, 32.0] and n == 2, (_qt, REQ))

# ═════ 5 청산: 앱 청산 경로(마감 청산, 손절 감지) ═════
reset()
SRV["NQ"] = ("ok", 20020.0, None)
me = T()
me._sig_accts = {("NQ", 0): {"acct": "A1"}, ("NQ", 1): {"acct": "A2"}}
at(T0)
fill(me, "NQ", "MNQ", 3, "projectx:A1")
fill(me, "NQ", "MNQ", 2, "projectx:A2")
me._pos_today["NQ"]["ref"] = 20000.0
tick(me, T0 + 12)
ck("5a 보유 2계좌 (20020 - 20000) x 5 x $2 = +$200", txt(me, "NQ") == "보유 MNQ 5계약(2계좌), 평가 손익 +$200", txt(me, "NQ"))
at(NQ_EXIT + 5)
E.App._send_fill(me, "NQ", closed=True)
ck("5b 마감 청산 보고(_send_fill closed) → 열린 자산에서 빠지고 '최근 진입 MNQ 5계약, 청산됨'",
   "NQ" not in me._open_assets and txt(me, "NQ") == "최근 진입 MNQ 5계약, 청산됨", txt(me, "NQ"))
me._auto_retry = {("NQ", 14, "A2"): {"n": 1}}
_nreq = len(REQ)
tick(me, NQ_EXIT + 70)
ck("5c 한 계좌 마감 청산 재시도 중이면 그 계좌는 보유로(앱이 아직 닫는 중): A2 2 x 20 x $2 = +$80, 재시도 중엔 서버에도 안 물어 낡음 표시",
   txt(me, "NQ") == "보유 MNQ 2계약(1계좌), 평가 손익 +$80" + E._pos_age_txt(NQ_EXIT + 70 - (T0 + 12), True) and len(REQ) == _nreq,
   (txt(me, "NQ"), REQ[_nreq:]))
me._auto_retry = {}
REQ.clear()
for k in range(5):
    tick(me, NQ_EXIT + 200 + 31 * k)
ck("5d 청산 뒤 = 서버에도 묻지 않음, 줄 '청산됨'", not REQ and txt(me, "NQ") == "최근 진입 MNQ 5계약, 청산됨", (REQ, txt(me, "NQ")))
ck("5e 손으로 연 포지션은 셀 수 없다(브로커를 읽지 않음) - 청산 뒤 4시간 = 같은 줄, 요청 0, 브로커 0",
   txt(me, "NQ", NQ_EXIT + 4 * 3600) == "최근 진입 MNQ 5계약, 청산됨" and not REQ and not BROKER_CALLS)
ck("5f 다음 진입(수 10:00 ET)부터 줄 없음", txt(me, "NQ", et(2026, 10, 28, 9, 59)) == "최근 진입 MNQ 5계약, 청산됨"
   and txt(me, "NQ", et(2026, 10, 28, 10, 0, 1)) == "")
me.lang = "en"
ck("5g 청산 EN", txt(me, "NQ", NQ_EXIT + 60) == "Last entry MNQ 5, closed")
me.lang = "ko"
# 손절 감지(_check_stop_closed - 앱의 기존 감시)가 결론을 내면 청산
reset()
me = T()
at(NOON)
fill(me, "GC", "MGC", 4, "projectx:A1", sid="G9", side="SHORT")


class FlatB:
    def list_open_positions(self):
        return []


me._open_ctx = {"GC": {"b": FlatB(), "sym": "CON.F.US.MGC.Z26"}}
E.App._check_stop_closed(me)
ck("5h 손절 감지 1회 = 아직 보유(확인 중)", "GC" in me._open_assets and txt(me, "GC") == "최근 진입 MGC 4계약, 보유 확인 중", txt(me, "GC"))
at(NOON + 60)
E.App._check_stop_closed(me)
ck("5i 손절 감지 2회(기존 규칙) → 청산 보고 → '최근 진입 MGC 4계약, 청산됨'", "GC" not in me._open_assets
   and txt(me, "GC") == "최근 진입 MGC 4계약, 청산됨", txt(me, "GC"))
at(NOON + 3600)
fill(me, "GC", "MGC", 1, "projectx:A1", sid="G10")
ck("5j 청산 뒤 새 체결 = 새 기록 '최근 진입 MGC 1계약, 보유 확인 중'(앞 수량을 끌고 오지 않음)",
   txt(me, "GC") == "최근 진입 MGC 1계약, 보유 확인 중", txt(me, "GC"))
me._open_assets.discard("GC")
ck("5k 기록은 열려 있는데 앱이 연 포지션으로 보지 않으면 줄 없음(지어내지 않음)", txt(me, "GC") == "", txt(me, "GC"))

# ═════ 6 BTC 24시간 만기 청산이 진입 다음 날(P2-4), 요일 규칙 ═════
reset()
SRV["BTC"] = ("ok", 70500.0, None)
me = T()
B0 = utc(2026, 10, 25, 22, 0, 6)            # 일 22:00 UTC 진입
at(B0)
fill(me, "BTC", "BTC", 0.012, "bybit:", sid="B9")
fill(me, "BTC", "BTC", 0.008, "bitget:", sid="B9")
E.App._pos_fill_px(me, "BTC", "B9", SpyBroker("bybit", []), "BTCUSDT", 70000.0)
tick(me, B0 + 32)
ck("6a BTC 2계좌 (70500 - 70000) x 0.02 = +$10", txt(me, "BTC") == "보유 BTC 0.02(2계좌), 평가 손익 +$10", txt(me, "BTC"))
at(utc(2026, 10, 26, 22, 0, 40))            # 월 22:00 UTC 24시간 만기
E.App._send_fill(me, "BTC", closed=True)
ck("6b 월 22:00 UTC 만기 청산 = 일요일 기록에 청산 표시 '최근 진입 BTC 0.02, 청산됨'",
   me._pos_today["BTC"].get("closed") == CLOCK[0] and txt(me, "BTC") == "최근 진입 BTC 0.02, 청산됨", (me._pos_today["BTC"], txt(me, "BTC")))
ck("6c 다음 BTC 진입(다음 일요일 22:00 UTC) 전까지 남음, 그 뒤 없음",
   txt(me, "BTC", utc(2026, 10, 29, 12)) == "최근 진입 BTC 0.02, 청산됨" and txt(me, "BTC", utc(2026, 11, 1, 21, 59)) != ""
   and txt(me, "BTC", utc(2026, 11, 1, 22, 0, 1)) == "")
me2 = T()
at(et(2026, 10, 23, 10, 0, 5))
fill(me2, "NQ", "MNQ", 2, "projectx:A1", sid="F1")
E.App._send_fill(me2, "NQ", closed=True)
ck("6d NQ 금요일 진입 = 토, 일에도 남고 월 10:00 ET부터 없음",
   txt(me2, "NQ", et(2026, 10, 25, 12)) == "최근 진입 MNQ 2계약, 청산됨" and txt(me2, "NQ", et(2026, 10, 26, 10, 0, 1)) == "")
_cons = []
for _a in ("NQ", "GC", "BTC"):
    _ne = E._next_entry_dt(_a)
    _cons.append(abs(E._pos_next_entry_after(_a, time.time()) - _ne.timestamp()) < 1.5)
ck("6e 다음 진입 시각 = _next_entry_dt와 같은 요일 규칙(지금 시각, 세 자산)", all(_cons), _cons)
at(et(2026, 10, 27, 9, 59, 50))             # PC 시계가 몇 초 느려 진입 시각 직전에 적힌 체결
fill(me2, "NQ", "MNQ", 1, "projectx:A1", sid="F2")
ck("6f 진입 시각 몇 초 전에 적힌 체결도 그 진입의 기록으로 남음", txt(me2, "NQ", et(2026, 10, 27, 10, 0, 30))
   == "최근 진입 MNQ 1계약, 보유 확인 중", txt(me2, "NQ", et(2026, 10, 27, 10, 0, 30)))

# ═════ 7 기록 파일: 원장 잠금 없이, 진입 중에는 쓰지 않음 ═════
reset()
if os.path.exists(E._POS_TODAY_PATH):
    os.remove(E._POS_TODAY_PATH)
me = T()
at(GC_ENT + 5)
fill(me, "GC", "MGC", 6, "projectx:A1", sid="G7", side="SHORT")
fill(me, "GC", "GC", 1, "projectx:A1", sid="G7", side="SHORT", filled=0)       # 체결 0 = 안 더함
E.App._pos_fill_px(me, "GC", "G7", SpyBroker("projectx", ["A1"]), "CON.F.US.MGC.Z26", 4000.0)
at(GC_EXIT + 10)
E.App._send_fill(me, "GC", closed=True)
me._pos_busy = {"NQ": 1}
ck("7a 진입 스레드가 도는 동안은 파일을 쓰지 않음(쓸 것은 남김)", E.App._pos_flush_today(me) is False and me._pos_dirty is True
   and not os.path.exists(E._POS_TODAY_PATH))
me._pos_busy = {"NQ": 0}
_held = threading.Event()
_done = threading.Event()


def _hold_ledger_lock():
    with E._JSON_LOCK:                     # 진입 스레드가 원장(_record_real_r, _ledger_add)을 쓰는 중
        _held.set()
        _done.wait(5)


_lt = threading.Thread(target=_hold_ledger_lock)
_lt.start()
_held.wait(2)
_res = []
_ft = threading.Thread(target=lambda: _res.append(E.App._pos_flush_today(me)))
_ft.start()
_ft.join(2)
_ok_nolock = (not _ft.is_alive()) and _res == [True]
_done.set()
_lt.join(2)
_ft.join(2)
ck("7b 원장 잠금(_JSON_LOCK)을 다른 줄이 쥐고 있어도 기록 파일 쓰기는 기다리지 않고 끝남", _ok_nolock, _res)
back = E._load_pos_today()
g = back.get("GC") or {}
ck("7c 파일에 쓰고 다시 읽음(계좌, 방향, 체결가, 청산 시각, 체결 0 제외)",
   g.get("acc") == {"projectx:A1": {"MGC": 6.0}} and g.get("side") == "SHORT" and g.get("avg_by") == {"projectx:A1": {"MGC": 4000.0}}
   and g.get("closed") == GC_EXIT + 10 and g.get("sid") == "G7", back)
me2 = T()
me2._pos_today = back
ck("7d 재시작 뒤에도 다음 진입 전까지 '최근 진입 MGC 6계약, 청산됨'", txt(me2, "GC", GC_EXIT + 3600) == "최근 진입 MGC 6계약, 청산됨")
with open(E._POS_TODAY_PATH, "w") as _f:
    json.dump({"NQ": {"day": "2026-10-27", "by": {"MNQ": 5}, "ts": NOON, "closed": None, "flat_ok": False}}, _f)
ck("7e 21p 형식(계좌, 방향 없음) 기록은 버림(손익을 지어내지 않게)", E._load_pos_today() == {}, E._load_pos_today())
with open(E._POS_TODAY_PATH, "w") as _f:
    _f.write("{broken")
ck("7f 깨진 파일 = 빈 기록(예외 없음)", E._load_pos_today() == {})

# ═════ 8 계좌 진입 스레드 몸통 ═════
me = T()


def _boom(*a):
    raise RuntimeError("entry error")


me._enter_account = _boom
_raised = False
try:
    E.App._pos_entry_run(me, {}, {}, "GC", "LONG", 1.0, 1.0)
except RuntimeError:
    _raised = True
ck("8a _enter_account 예외는 그대로 올라가고(종전 스레드와 같음) 진입 중 수는 0으로", _raised and me._pos_busy.get("GC") == 0
   and me._pos_busy_end.get("GC") is not None, me.__dict__.get("_pos_busy"))
_body = inspect.getsource(E.App._sig_loop_body)
ck("8b 신호 루프의 계좌 스레드 몸통 = App._pos_entry_run(self, cfg, ...)", "target=App._pos_entry_run" in _body
   and "args=(self, _cfg, sig, _asset, direction, stop, _mult)" in _body)
_run = inspect.getsource(E.App._pos_entry_run) + inspect.getsource(E.App._pos_busy_mark)
ck("8c 몸통이 더하는 것 = 메모리 사전 세기뿐(네트워크, 파일, 원장 잠금, 기록 잠금 없음)",
   not any(w in _run for w in ("_srv_get", "requests", "open(", "_write_json", "_JSON_LOCK", "_POS_LOCK")), _run)

# ═════ 9 읽을 것이 없으면 아무것도 묻지 않음, 실패는 밖으로 안 나감 ═════
reset()
me = T()
me._sig_accts = {("NQ", 0): {"acct": "A1"}}
me._open_assets = {"NQ"}                           # 체결 기록 없이 열린 자산(재시작 전 진입 등)
n = tick(me, NOON)
ck("9a 체결 기록이 없으면 묻지 않음, 줄 없음(지어내지 않음)", n == 0 and not REQ and txt(me, "NQ") == "", REQ)


class Boom(T):
    def _ui(self, fn, ms=0):
        raise RuntimeError("ui gone")


bm = Boom()
at(NOON)
fill(bm, "NQ", "MNQ", 1, "projectx:A1")
_o = E._srv_get
E._srv_get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    n = E.App._pos_poll_tick(bm, NOON + 30)
    _ok = True
except Exception:
    _ok = False
E._srv_get = _o
ck("9b 서버 요청이 터지고 _ui가 터져도 예외 없음", _ok)
bm2 = T()
_o2 = E.App._pos_held_assets
E.App._pos_held_assets = lambda self, now: (_ for _ in ()).throw(RuntimeError("x"))
try:
    at(NOON)
    fill(bm2, "NQ", "MNQ", 1, "projectx:A1")
    bm2.UI.clear()
    E.App._pos_poll_tick(bm2, NOON + 30)
    _ok = True
except Exception:
    _ok = False
E.App._pos_held_assets = _o2
ck("9c 자산 고르기가 터져도 예외 없음(다시 그리기는 그대로)", _ok and bm2.UI, bm2.UI)

# ═════ 10 자산 줄: 종전 글 + 줄바꿈 + 보유 줄, 점 색 그대로 ═════
_orig_base = E.App._asset_row_base
E.App._asset_row_base = lambda self, a: ("Topstep, 2계좌, 1R $500, 가동 중(실거래)", "#21c55e")
reset()
SRV["NQ"] = ("ok", 20081.2, None)
me = T()
at(NOON)
fill(me, "NQ", "MNQ", 3, "projectx:A1")
fill(me, "NQ", "MNQ", 2, "projectx:A2")
me._pos_today["NQ"]["ref"] = 20000.0
tick(me, NOON + 5)
t1, c1 = E.App._asset_row_state(me, "NQ")
ck("10a 종전 줄 + '\\n보유 MNQ 5계약(2계좌), 평가 손익 +$812', 점 색 그대로",
   t1 == "Topstep, 2계좌, 1R $500, 가동 중(실거래)\n보유 MNQ 5계약(2계좌), 평가 손익 +$812" and c1 == "#21c55e", (t1, c1))
t2, _ = E.App._asset_row_state(me, "GC")
ck("10b 기록 없는 자산은 종전 줄 그대로", t2 == "Topstep, 2계좌, 1R $500, 가동 중(실거래)", t2)
mm = MagicMock()
mm.lang = "ko"
t3, _ = E.App._asset_row_state(mm, "NQ")
ck("10c 시험용 가짜 객체(MagicMock)에서도 종전 줄 그대로", t3 == "Topstep, 2계좌, 1R $500, 가동 중(실거래)", t3)
E.App._asset_row_base = _orig_base

# ═════ 11 화면 줄 규칙: 작업 줄에서 _ui만 ═════
reset()
SRV["NQ"] = ("ok", 20000.0, None)
me = T()
at(NOON)
fill(me, "NQ", "MNQ", 1, "projectx:A1")
me.UI.clear()
me.UI_THREADS.clear()
at(NOON + 5)
_th = threading.Thread(target=lambda: E.App._pos_poll_tick(me))
_th.start()
_th.join(10)
ck("11a 작업 줄에서 돈 바퀴: 다시 그리기는 _ui로 한 번, 위젯 직접 호출 0, 맡긴 줄 = 작업 줄",
   me.UI == [me._refresh_live_panel] and me.REFRESH == 0 and me.UI_THREADS == [_th.ident], (me.UI, me.REFRESH))
tick(me, NOON + 10)
ck("11b 글이 같으면 다시 그리기를 맡기지 않음", len(me.UI) == 1, me.UI)

# ═════ 12 줄 띄우기: 진짜 앱에서 한 번만 ═════
started = []


class FakeThread:
    def __init__(self, target=None, name=None, daemon=None, **k):
        self.t = target

    def start(self):
        started.append(self.t)


_RealThread = E.threading.Thread
E.threading.Thread = FakeThread
try:
    fk = T()
    ck("12a 가짜 객체(_pos_poll_ok 없음)는 줄을 띄우지 않음", fk._pos_poll_start() is False and not started)
    fk._pos_poll_ok = True
    r1, r2 = fk._pos_poll_start(), fk._pos_poll_start()
    ck("12b 한 번만(두 번째는 아무것도 안 함)", r1 is True and r2 is False and len(started) == 1, started)
    fk2 = T()
    fk2._pos_poll_ok = True
    E.App._pos_poll_kick(fk2)
    ck("12c 기동 30초 뒤에는 늘 띄움(보유가 없어도 - 첫 체결 때 진입 스레드 안에서 띄우지 않게, 재검토 P3)", len(started) == 2)
    fk2._open_assets = {"GC"}
    E.App._pos_poll_kick(fk2)
    ck("12d 다시 불러도 한 번만", len(started) == 2)
    fk3 = T()
    fk3._pos_poll_ok = True
    at(et(2026, 10, 20, 12))
    E.App._pos_today_note(fk3, "NQ", ("MNQ", 1, 1), acct="projectx:A1", sid="X", side="LONG")
    at(NOON)
    E.App._pos_poll_kick(fk3)
    ck("12e 기록과 상관없이 기동 때 띄움", len(started) == 3)
    E.App._pos_today_note(fk3, "NQ", ("MNQ", 1, 1), acct="projectx:A1", sid="Y", side="LONG")
    E.App._pos_poll_kick(fk3)
    ck("12f 이미 떠 있으면 그대로", len(started) == 3)
finally:
    E.threading.Thread = _RealThread

# ═════ 13 _note_fill, _send_fill 경로가 보유 줄 때문에 깨지지 않음 ═════


class Stub:
    _profile = {}
    root = None
    lang = "ko"

    def _save_cfg(self, **k):
        pass


st = Stub()
E.App._note_fill(st, "NQ", micro=3, acct="px:A", leg=("MNQ", 3, 3), sid="X", side="SHORT")
ck("13a _ui, _pos_poll_start가 없는 옛 가짜 객체에서도 체결 누적 그대로, 기록에 계좌, 방향, 신호",
   st._fill_acc[("NQ", "X")]["legs"] == 1 and st._pos_today["NQ"]["acc"] == {"px:A": {"MNQ": 3.0}}
   and st._pos_today["NQ"]["side"] == "SHORT" and st._pos_today["NQ"]["sid"] == "X", getattr(st, "_pos_today", None))
st._gate = {}
try:
    E.App._send_fill(st, "NQ", closed=True)
    _ok = True
except Exception:
    _ok = False
ck("13b 옛 가짜 객체에서 청산 보고도 예외 없음, 청산 표시", _ok and st._pos_today["NQ"].get("closed"), st._pos_today)

# ═════ 15 보유 줄 색, 계좌별 펼침(대표 2026-10-29 "회색 말고 색깔 줘", "계좌별로도 보이게 해 줘 펼치면") ═════
ck("15a 계좌 끝 4자리 = 체결 DM 규칙", E._pos_acct_tail("projectx:12345872") == "…5872" and E._pos_acct_tail("nt8:LDE150-0001") == "…0001"
   and E._pos_acct_tail("bybit:") == "…", [E._pos_acct_tail(x) for x in ("projectx:12345872", "nt8:LDE150-0001", "bybit:")])
reset()
SRV["NQ"] = ("ok", 20081.2, None)
me = T()
at(NOON)
fill(me, "NQ", "MNQ", 3, "projectx:12345872")
fill(me, "NQ", "MNQ", 2, "nt8:LDE150-0001")
me._pos_today["NQ"]["ref"] = 20000.0
tick(me, NOON + 5)
line, col, rows = E.App._open_pos_ui(me, "NQ", NOON + 5)
TEXTS.extend([line] + [r[0] for r in rows])
ck("15b 이익 = 초록, 합계 줄은 종전 글 그대로", line == "보유 MNQ 5계약(2계좌), 평가 손익 +$812" and col == E._POS_UP, (line, col))
ck("15c 계좌별 줄 = 끝 4자리 순, 계좌마다 손익, 합 = 합계", rows == [("…0001  MNQ 2계약  +$325", E._POS_UP), ("…5872  MNQ 3계약  +$487", E._POS_UP)], rows)
me.lang = "en"
_l_en, _, _r_en = E.App._open_pos_ui(me, "NQ", NOON + 5)
TEXTS.extend([_l_en] + [r[0] for r in _r_en])
ck("15d EN 계좌별 줄", [r[0] for r in _r_en] == ["…0001  MNQ 2  +$325", "…5872  MNQ 3  +$487"], _r_en)
me.lang = "ko"
SRV["NQ"] = ("ok", 19950.0, None)
tick(me, NOON + 40)
_l2, _c2, _r2 = E.App._open_pos_ui(me, "NQ", NOON + 40)
ck("15e 손실 = 빨강, 계좌 줄도 빨강", _c2 == E._POS_DOWN and all(c == E._POS_DOWN for _s, c in _r2) and "-$500" in _l2, (_l2, _c2, _r2))
me._pos_today["NQ"]["avg_by"] = {"nt8:LDE150-0001": {}}
me._pos_today["NQ"].pop("ref", None)
me._pos_today["NQ"]["avg"] = {}
_, _c3, _r3 = E.App._open_pos_ui(me, "NQ", NOON + 40)
ck("15f 체결가를 모르면 '-', 짙은 글자색(숫자를 지어내지 않는다)", _c3 == E._POS_INK and all(s.endswith("  -") and c == E._POS_INK for s, c in _r3), (_c3, _r3))
reset()
SRV["GC"] = ("ok", 4040.0, None)
m1 = T()
at(NOON)
fill(m1, "GC", "MGC", 4, "projectx:99991085")
m1._pos_today["GC"]["ref"] = 4036.0
tick(m1, NOON + 5)
_l4, _c4, _r4 = E.App._open_pos_ui(m1, "GC", NOON + 5)
ck("15g 한 계좌면 펼침 없음, 초록", _r4 == [] and _c4 == E._POS_UP and _l4.endswith("+$160"), (_l4, _c4, _r4))
ck("15h 기록 없는 자산 = 빈 줄", E.App._open_pos_ui(m1, "NQ", NOON + 5) == ("", E._POS_INK, []), E.App._open_pos_ui(m1, "NQ", NOON + 5))
# 실제 Tk 위젯: 누르면 펼치고 다시 누르면 접는다, 글이 없으면 숨는다
try:
    import tkinter as _tk
    from tkinter import ttk as _ttk
    _root = _tk.Tk()
    _root.withdraw()
except Exception as _e:
    _root = None
    print(f"SKIP  15i~15l 화면 없음({_e})")
if _root is not None:
    reset()
    SRV["NQ"] = ("ok", 20081.2, None)
    w = T()
    at(NOON)
    fill(w, "NQ", "MNQ", 3, "projectx:12345872")
    fill(w, "NQ", "MNQ", 2, "nt8:LDE150-0001")
    w._pos_today["NQ"]["ref"] = 20000.0
    tick(w, NOON + 5)
    _box = _ttk.Frame(_root); _box.pack()
    _pl = _tk.Label(_box, text=""); _pd = _ttk.Frame(_box)
    w._live_pos = {"NQ": (_pl, _pd), "GC": (_tk.Label(_box), _ttk.Frame(_box))}
    _orig_clock = E._pos_clock
    E._pos_clock = lambda: NOON + 5
    E.App._pos_paint(w, "NQ")
    E.App._pos_paint(w, "GC")
    ck("15i 접힌 줄: 합계 + '계좌별 보기', 초록, 계좌 틀 숨김, 기록 없는 자산 줄은 숨김",
       _pl.cget("text").endswith("+$812   계좌별 보기") and str(_pl.cget("foreground")) == E._POS_UP and not _pd.winfo_manager()
       and _pl.winfo_manager() == "pack" and not w._live_pos["GC"][0].winfo_manager(), (_pl.cget("text"), _pl.cget("foreground")))
    E.App._pos_toggle(w, "NQ")
    _kids = _pd.winfo_children()
    ck("15j 누르면 펼침: '접기', 계좌 줄 둘(색 그대로), 보유 줄 아래",
       _pl.cget("text").endswith("   접기") and _pd.winfo_manager() == "pack" and [k.cget("text") for k in _kids]
       == ["…0001  MNQ 2계약  +$325", "…5872  MNQ 3계약  +$487"] and all(str(k.cget("foreground")) == E._POS_UP for k in _kids)
       and _box.pack_slaves() == [_pl, _pd], [k.cget("text") for k in _kids])
    E.App._pos_paint(w, "NQ")
    ck("15k 다시 그려도 같은 줄이면 위젯을 다시 만들지 않는다", _pd.winfo_children() == _kids, _pd.winfo_children())
    E.App._pos_toggle(w, "NQ")
    ck("15l 다시 누르면 접힘", not _pd.winfo_manager() and _pl.cget("text").endswith("계좌별 보기"), _pl.cget("text"))
    E._pos_clock = _orig_clock
    _root.destroy()

# ═════ 14 문구 규칙 ═════
_all = " ".join(TEXTS + [E._pos_line_hold("NQ", {"NQ": 1, "MNQ": 3}, 2, 1, k) for k in (True, False)]
                + [E._pos_line_closed("BTC", {"BTC": 0.1}, k) for k in (True, False)]
                + [E._pos_line_pending("GC", {"MGC": 1}, k) for k in (True, False)])
ck("14a 가운뎃점, 이모지 없음", "·" not in _all and "•" not in _all and not any(ord(ch) >= 0x1F300 for ch in _all), _all)
ck("14b '오늘', 'today' 없음(대표 결정 A)", "오늘" not in _all and "today" not in _all.lower(), _all)
ck("14c 브로커 호출 0(시험 전체, 기존 손절 감지 시험의 가짜 브로커 제외)", not BROKER_CALLS, BROKER_CALLS)

print()
if FAIL:
    print(f"FAIL {len(FAIL)} / {NCHK[0]}: " + ", ".join(FAIL))
    sys.exit(1)
print(f"ALL PASS ({NCHK[0]})")
