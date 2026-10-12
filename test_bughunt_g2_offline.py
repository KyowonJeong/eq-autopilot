#!/usr/bin/env python3
# EQ Autopilot - 오픈 준비 버그 조사(2026-10-06) 1단계 g2 오프라인 시험: 진입 경로 B·U·M
#  B  비NT8(ProjectX·IBKR) 진입이 예외·미확인이면 포지션을 더 보고, 보이면 확정 경로(손절 확인·손절 실패 처리·
#     체결 보고)로, 끝내 모르면 회원 알림 + '미확인' 감시. 크립토는 타임아웃·5xx를 '거절'이 아니라 '미확인'으로 보고.
#  U  NT8 진입 예외 뒤 늦은 체결 확인 경로의 _seen_qty 미초기화(UnboundLocalError) + 둘째 leg의 묵은 수량.
#  M  NT8 list_open_positions를 그 인스턴스 계좌로 거름(다른 계좌 포지션을 '이전 세션 잔여'로 보지 않게).
# 가짜 시계·가짜 브로커 - 실제 주문·네트워크·알림 0. 모듈 전역의 파일 쓰기(원장·진입 시각·감시 맥락)는 임시로 돌린다.
# 사용: python executor/test_bughunt_g2_offline.py   (0=통과, 1=실패)
import ast
import datetime as _d
import io
import json
import os
import sys
import tempfile
import textwrap
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
from eqexec.broker import nt8 as N8  # noqa: E402

fails = []


def chk(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{d}]"))
    if not c:
        fails.append(n)


# ── 파일 쓰기 격리 ─────────────────────────────────────────────────────────────
_TMP = tempfile.mkdtemp()
eqgui._OPENCTX_PATH = os.path.join(_TMP, ".open_ctx.json")
LEDGER = []
eqgui._ledger_add = lambda *a, **k: LEDGER.append(a)
eqgui._mark_entered = lambda a: {}
eqgui._record_real_r = lambda *a, **k: None

# ── 가짜 시계 ──────────────────────────────────────────────────────────────────
CLK = {"t": 1_000_000.0}
_real_time, _real_sleep = time.time, time.sleep
time.time = lambda: CLK["t"]
time.sleep = lambda s: CLK.__setitem__("t", CLK["t"] + float(s))


class Stub:
    """App 자리. 확정·미확인 경로가 부르는 것만 기록하고 나머지는 무해하게."""
    lang = "ko"

    def __init__(self):
        self.logs, self.alerts, self.remembered, self.fills = [], [], [], []
        self.stopfail, self.marked, self.watched = [], [], []

    def log(self, m):
        self.logs.append(str(m))

    def _claim_entry(self, *a, **k):
        return True

    def _resolve_contract(self, b, s):
        return {"NQ": "NQ 12-26", "MNQ": "MNQ 12-26"}.get(s, s) if "NT8" in type(b).__name__ else f"CON.F.US.{s}.Z26"

    def _nt8_standby(self):
        return False

    def _net_probe(self):
        return "ok"

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def _remember_open(self, asset, b, sym, **k):
        self.remembered.append((asset, sym, k))

    def _note_fill(self, asset, **k):
        self.fills.append((asset, k))

    def _handle_stop_failure(self, b, aid, con, d, size, stop, res):
        self.stopfail.append((size, dict(res)))

    def _mark_unconfirmed(self, asset):
        self.marked.append(asset)

    def _unconf_watch_add(self, asset, b, aid, con, **k):     # leg 단위 미확인 감시(버그 조사 재검증 2026-10-06 2)
        self.watched.append((asset, aid, con, k))
        return True

    def __getattr__(self, n):
        return lambda *a, **k: None


def run(b, legs, direction="LONG", stop=19900.0, acct="ACC1"):
    app = Stub()
    LEDGER.clear()
    try:
        eqgui.App._run_futures_entry(app, b, acct, legs, direction, stop,
                                     {"id": "t-NQ-1", "entry_ref": 20000.0}, True,
                                     _d.datetime.now(), "NQ", None)
    except Exception as e:                       # 크래시도 판정 대상(U) - 삼키되 로그로 남긴다
        app.logs.append(f"EXC {type(e).__name__}: {e}")
    return app


# ══ U: NT8 진입 예외 뒤 늦은 체결 확인 ═══════════════════════════════════════════
class NT8Broker:
    """이름이 NT8Broker여야 진입 경로가 NT8로 본다. raise_cons = 첫 제출이 ack 타임아웃(20초)으로 끝나는 계약,
    pos_after = 제출 뒤 몇 초에 포지션이 보이나, qty = 보이는 수량."""
    def __init__(self, raise_cons=(), pos_after=None, qty=None):
        self.raise_cons, self.raised = set(raise_cons), set()
        self.pos_after, self.qty = dict(pos_after or {}), dict(qty or {})
        self.sub_t, self.placed, self.flattened = {}, [], 0

    def _accounts(self):
        return [{"name": "SIM1", "id": "SIM1"}]

    def place_entry(self, **k):
        con = k.get("contract_id")
        self.placed.append((con, k.get("custom_tag")))
        self.sub_t.setdefault(con, time.time())
        if con in self.raise_cons and con not in self.raised:
            self.raised.add(con)
            time.sleep(20)
            raise TimeoutError("nt8 ack timeout (entry, tid=x) — NT8 애드온 연결 상태를 확인하세요")
        return {"order_id": "o"}

    def position_qty(self, aid, con):
        t = self.sub_t.get(con)
        if t is None or con not in self.pos_after:
            return 0
        return self.qty[con] if time.time() - t >= self.pos_after[con] else 0

    def order_status(self, aid, tag):
        return {"entry": {"state": "Working"}}

    def list_open_positions(self):
        return []

    def close_contract(self, aid, con):
        self.flattened += 1

    def current_market_price(self, con):
        return 0


b = NT8Broker(raise_cons={"MNQ 12-26"}, pos_after={"MNQ 12-26": 5}, qty={"MNQ 12-26": 3})
a = run(b, [("MNQ", 3)], acct="SIM1")
chk("U1 첫 제출 예외 → 늦은 체결 확정, 크래시 없음", not any(m.startswith("EXC") for m in a.logs),
    [m for m in a.logs if m.startswith("EXC")])
chk("U1 감시 기억(_remember_open)·원장·체결 보고까지 진행", [r[2].get("size") for r in a.remembered] == [3]
    and len(LEDGER) == 1 and len(a.fills) == 1, (a.remembered, LEDGER, a.fills))
chk("U1 재진입 없음(같은 주문 1회)", len(b.placed) == 1 and b.flattened == 0, (b.placed, b.flattened))

b = NT8Broker(raise_cons={"MNQ 12-26"}, pos_after={"NQ 12-26": 1, "MNQ 12-26": 5},
              qty={"NQ 12-26": 2, "MNQ 12-26": 3})
a = run(b, [("NQ", 2), ("MNQ", 3)], acct="SIM1")
_sz = {r[1]: r[2].get("size") for r in a.remembered}
chk("U2 둘째 leg는 자기 실측 수량(앞 leg 2가 남지 않음)", _sz == {"NQ 12-26": 2, "MNQ 12-26": 3}, _sz)
chk("U2 거짓 '부분 체결' 경보 없음", not [k for k, *_ in a.alerts if k == "partial_fill"], a.alerts)


# ══ B: 비NT8 진입 미확인 ═══════════════════════════════════════════════════════
class _FutBase:
    """비NT8 선물 브로커 모양. raise_entry = Order/place 예외(접수 뒤 타임아웃),
    pos_at = 제출 뒤 몇 초에 포지션이 보이나(None=안 보임), pos_none_until = 그때까지 조회 실패(None)."""
    name = "fut"

    def __init__(self, raise_entry=True, pos_at=None, qty=2, orders=(), stop_res=None, entry_res=None,
                 pos_none_until=None):
        self.raise_entry, self.pos_at, self.qty = raise_entry, pos_at, qty
        self.orders = list(orders)
        self.stop_res = stop_res if stop_res is not None else {"stop": {"id": 9}, "stop_price": 19900.0}
        self.entry_res = entry_res or {}
        self.pos_none_until = pos_none_until
        self.placed, self.stops, self.closed, self.t_sub = [], [], 0, None

    def _accounts(self):
        return [{"name": "ACC1", "id": 101}]

    def place_entry(self, **k):
        self.placed.append(k)
        self.t_sub = time.time()
        time.sleep(0.3)
        if self.raise_entry:
            raise RuntimeError("HTTPSConnectionPool(host='api.topstepx.com', port=443): Read timed out.")
        return dict(self.entry_res)

    def position_qty(self, aid, con):
        if self.t_sub is None:
            return 0
        age = time.time() - self.t_sub
        if self.pos_none_until is not None and age < self.pos_none_until:
            return None
        return self.qty if (self.pos_at is not None and age >= self.pos_at) else 0

    def place_protective_stop(self, aid, con, side, size, px, *, custom_tag=None):
        self.stops.append((size, px))
        return dict(self.stop_res)

    def close_contract(self, aid, con):
        self.closed += 1

    def list_open_positions(self):
        return []

    def current_market_price(self, con):
        return None


class PXBroker(_FutBase):
    """ProjectX 모양: 앱이 주문 목록(_open_orders)을 볼 수 있다."""
    name = "projectx"

    def _open_orders(self, aid):
        return list(self.orders)


class IBLike(_FutBase):
    """IBKR 모양: 앱이 주문 목록을 못 본다(_open_orders 없음)."""
    name = "ibkr"


CON = "CON.F.US.MNQ.Z26"

b = PXBroker(raise_entry=True, pos_at=5)
a = run(b, [("MNQ", 2)])
chk("B1 예외 → 재확인으로 체결 확인 → 손절을 실측 수량으로 건다", b.stops == [(2, 19900.0)], b.stops)
chk("B1 확정 경로: 체결 보고·감시 기억(수량 2)·원장", len(a.fills) == 1 and [r[2].get("size") for r in a.remembered] == [2]
    and len(LEDGER) == 1, (a.fills, a.remembered, LEDGER))
chk("B1 손절 거치 로그·미확인 알림 없음·재주문 없음",
    any("보호 손절 거치" in m for m in a.logs) and not a.alerts and not a.marked
    and len(b.placed) == 1 and b.closed == 0, (a.alerts, a.marked, b.placed, b.closed))

b = PXBroker(raise_entry=True, pos_at=3, stop_res={"stop_error": "Read timed out", "stop_rejected": False})
a = run(b, [("MNQ", 2)])
chk("B2 늦게 건 손절이 실패 → _handle_stop_failure(실측 수량)",
    len(a.stopfail) == 1 and a.stopfail[0][0] == 2 and a.stopfail[0][1].get("stop_error"), a.stopfail)

b = PXBroker(raise_entry=True, pos_at=2, orders=[{"id": 5, "type": 4, "contractId": CON, "side": 1,
                                                  "stopPrice": 19890.0}])
a = run(b, [("MNQ", 2)])
chk("B3 브로커에 손절이 이미 있으면 다시 걸지 않는다(이중 손절 방지)", b.stops == [] and
    any("이미 있습니다" in m for m in a.logs) and any("보호 손절 거치 @19890" in m for m in a.logs),
    (b.stops, a.logs[-6:]))

b = PXBroker(raise_entry=True, pos_at=2, orders=[{"id": 6, "type": 4, "contractId": CON, "side": 0,
                                                  "stopPrice": 20100.0}])
a = run(b, [("MNQ", 2)])
chk("B3b 같은 방향 스탑(회원 스탑 진입 주문)은 보호 손절로 치지 않는다", b.stops == [(2, 19900.0)], b.stops)

b = PXBroker(raise_entry=True, pos_at=None)
t0 = CLK["t"]
a = run(b, [("MNQ", 2)])
_ka = [k for k, *_ in a.alerts]
# 재검증 2: 미확인 감시는 자산 ctx(_remember_open·_mark_unconfirmed)가 아니라 leg 단위 목록(_unconf_watch_add)으로 간다
chk("B4 끝내 모름 → 회원 알림(entry_unconfirmed) + 미확인 감시", _ka == ["entry_unconfirmed"]
    and [(w[0], w[2]) for w in a.watched] == [("NQ", CON)] and not a.marked and not a.remembered,
    (_ka, a.watched, a.marked, a.remembered))
chk("B4 재확인 창을 기다렸다(≥12초)·체결 보고 없음·재주문 없음",
    CLK["t"] - t0 >= eqgui.ENTRY_RECHECK_S and not a.fills and len(b.placed) == 1 and b.closed == 0,
    (CLK["t"] - t0, a.fills, b.placed, b.closed))
_ko, _en = a.alerts[0][1], a.alerts[0][2]
chk("B4 알림 문구: 사실(미확인)·KO/EN 한 쌍·지시형 없음",
    "확인하지 못했습니다" in _ko and "could not be confirmed" in _en and "하세요" not in _ko
    and "하십시오" not in _ko and "없으면 겁니다" in _ko, _ko)

b = PXBroker(raise_entry=False, pos_at=0, pos_none_until=3,
             entry_res={"entry": {"orderId": 1}, "stop_error": "Read timed out", "stop_rejected": False})
a = run(b, [("MNQ", 2)])
chk("B5 변형: 진입 접수+손절 일시 실패+조회 2초 실패 → 재확인 뒤 _handle_stop_failure",
    len(a.stopfail) == 1 and a.stopfail[0][1].get("stop_error") == "Read timed out" and b.stops == []
    and len(a.fills) == 1, (a.stopfail, b.stops, a.fills))

b = IBLike(raise_entry=True, pos_at=2)
a = run(b, [("MNQ", 2)])
chk("B6 주문 목록을 못 보는 브로커(IBKR): 손절을 새로 걸지 않고(이중 방지) 회원 알림",
    b.stops == [] and [k for k, *_ in a.alerts] == ["stop_unconfirmed"] and len(a.fills) == 1,
    (b.stops, a.alerts, a.fills))

b = PXBroker(raise_entry=False, pos_at=0.5, entry_res={"entry": {"orderId": 1}, "stop": {"id": 1},
                                                      "stop_price": 19900.0})
t0 = CLK["t"]
a = run(b, [("MNQ", 2)])
chk("B7 종전 정상 경로 그대로(2초 창 확정, 재확인·알림 없음)",
    len(a.fills) == 1 and not a.alerts and not a.marked and b.stops == [] and CLK["t"] - t0 < 5,
    (a.fills, a.alerts, CLK["t"] - t0))

# 예산 0(비NT8)은 경과 0초여도 1회로 끝낸다 - 시계를 멈춘 채(윈도 15ms 해상도 재현) 예외를 낸다.
class PXFast(PXBroker):
    def place_entry(self, **k):
        self.placed.append(k)
        self.t_sub = time.time()
        raise RuntimeError("Connection refused")


b = PXFast(raise_entry=True, pos_at=None)
a = run(b, [("MNQ", 2)])
chk("B8 비NT8은 경과 0초 예외여도 재진입·잔재 정리 없음(1회)", len(b.placed) == 1 and b.closed == 0,
    (len(b.placed), b.closed))


# ══ B: 하트비트 감시(_check_stop_closed)의 '미확인' 처리 ══════════════════════════
NAMES = ("_check_stop_closed", "_check_stop_alive", "_check_stop_size", "_learn_seen_stop",
         "_restore_open_ctx", "_persist_open_ctx", "_forget_open_ctx", "_remember_open",
         "_remember_stop", "_mark_unconfirmed")
src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
segs = {n.name: ast.get_source_segment(src, n) for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name in NAMES}
chk("감시 메서드 전부 있음", all(n in segs for n in NAMES), [n for n in NAMES if n not in segs])
ns = dict(eqgui.__dict__)
exec("class T:\n    STOP_ORDER_CHECK_SEC = 0\n"
     + "\n".join(textwrap.indent(segs[n], "    ") for n in NAMES), ns)
T = ns["T"]


class _Pos:
    def __init__(self, sym, qty=2, aid=101):
        self.symbol, self.net_qty, self.account_id = sym, qty, aid
        self.raw = {"_accountId": aid, "contractId": sym}


class PXWatch:
    name = "projectx"

    def __init__(self, poss):
        self.poss, self.orders, self.placed = poss, [], []

    def list_open_positions(self):
        return list(self.poss)

    def _open_orders(self, aid):
        return list(self.orders)

    def _cancel_order(self, aid, oid):
        pass

    def place_protective_stop(self, aid, con, d, n, px, *, custom_tag=None):
        self.placed.append((n, px))
        self.orders.append({"id": 99, "type": 4, "contractId": con, "size": n, "stopPrice": px, "side": 1})
        return {"stop": {"id": 99}}


def _app(b, open_assets=()):
    t = T()
    t.logs, t.alerts, t.sent = [], [], []
    t.log = t.logs.append
    t._member_alert = lambda k, ko, en: t.alerts.append(k)
    t._send_fill = lambda a, closed=False: t.sent.append((a, closed))
    t._creds_of = lambda a, bk=None: {"f1": "K", "f3": ""}
    t._profile, t._save_cfg = {}, (lambda: None)
    t._open_assets, t._flat_seen, t._nostop_seen, t._stop_chk_at = set(open_assets), {}, {}, {}
    t._open_ctx = {}
    t._remember_open("NQ", b, CON, stop=19900.0, aid=101, direction="LONG", size=2)
    return t


def _disk():
    try:
        return json.load(open(eqgui._OPENCTX_PATH, encoding="utf-8"))
    except Exception:
        return {}


b = PXWatch([])
t = _app(b)
t._mark_unconfirmed("NQ")
chk("W1 미확인 표시 → 감시 집합에 들어감 + 디스크에도", "NQ" in t._open_assets and t._open_ctx["NQ"].get("unconfirmed")
    and _disk().get("NQ", {}).get("unconfirmed") and _disk().get("NQ", {}).get("unconf_added"),
    (t._open_assets, t._open_ctx.get("NQ"), _disk().get("NQ")))
for _ in range(2):
    t._check_stop_closed()
chk("W1 끝내 포지션 없음 → 청산 보고 없이 놓음(감시 집합·디스크에서 빠짐)",
    t.sent == [] and "NQ" not in t._open_assets and "NQ" not in t._open_ctx and "NQ" not in _disk(),
    (t.sent, t._open_assets, _disk()))

b = PXWatch([_Pos(CON, 2)])
t = _app(b)
t._mark_unconfirmed("NQ")
t.STOP_ORDER_CHECK_SEC = 180                  # 실제 주기 - 방금 확인한 것처럼 둬도 미확인 첫 발견은 기다리지 않아야 한다
t._stop_chk_at["NQ"] = time.time()
t._check_stop_closed()
chk("W2 포지션이 보이면 손절 확인을 바로 돌린다", t._open_ctx["NQ"].get("unconf_seen")
    and t._nostop_seen.get("NQ") == 1, (t._open_ctx["NQ"], t._nostop_seen))
t._stop_chk_at = {}
t._check_stop_closed()
chk("W2 손절 없음 연속 2회 → 기억한 자리·포지션 수량으로 재거치", b.placed == [(2, 19900.0)]
    and t.alerts == ["stop_restored"], (b.placed, t.alerts))
b.poss = []
for _ in range(2):
    t._check_stop_closed()
chk("W2 그 뒤 포지션이 사라져도 청산 보고 없음(열림을 보고한 적 없음)", t.sent == [] and "NQ" not in t._open_ctx,
    (t.sent, list(t._open_ctx)))

b = PXWatch([])
t = _app(b, open_assets={"NQ"})            # 다른 계좌가 이미 확정한 자산
t._mark_unconfirmed("NQ")
for _ in range(2):
    t._check_stop_closed()
chk("W3 다른 계좌가 확정한 자산이면 감시 집합에서 빼지 않는다", "NQ" in t._open_assets and t.sent == [],
    (t._open_assets, t.sent))

b = PXWatch([])
t = _app(b, open_assets={"NQ"})
for _ in range(2):
    t._check_stop_closed()
chk("W4 종전: 확정 포지션이 사라지면 청산 보고 그대로", t.sent == [("NQ", True)], t.sent)

b = PXWatch([])
t = _app(b)
t._mark_unconfirmed("NQ")
ns["_build_broker"] = lambda bk, f1, f2, f3, acc: b
ns["_f2_load"] = lambda f1: "S"
t._open_ctx = {}                              # 재시작 재현
t._ctx_retry_at = {}
ok = t._restore_open_ctx("NQ")
chk("W5 재시작 뒤 복구해도 미확인 표시가 남는다", ok and t._open_ctx["NQ"].get("unconfirmed")
    and t._open_ctx["NQ"].get("unconf_added"), t._open_ctx.get("NQ"))


# ══ B: 크립토 - 타임아웃·5xx는 '거절'이 아니라 '미확인' ═══════════════════════════
_cases = [
    ("Bybit /v5/order/create failed: retCode=110007 ab not enough for new order", False),
    ("HTTPSConnectionPool(host='api.bybit.com', port=443): Read timed out. (read timeout=15)", True),
    ("('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))", True),
    ("502 Server Error: Bad Gateway for url: https://api.bybit.com/v5/order/create", True),
    ("403 Client Error: Forbidden for url: https://api.bybit.com/v5/order/create", False),
    ("Bitget /api/v2/mix/order/place-order HTTP 502: code=None <html>", True),
    ("Bitget /api/v2/mix/order/place-order HTTP 400: code=40774 side mismatch", False),
    ("Bitget /api/v2/mix/order/place-order failed: code=40762 balance", False),
    ("invalid side: 'X' (expected LONG/SHORT)", False),
    ("qty<=0", False),
]
_bad = [(m, w) for m, w in _cases if eqgui._entry_err_unknown(m) != w]
chk("C1 오류 분류(거래소 거절=거절, 타임아웃·끊김·5xx=미확인)", not _bad, _bad)


def run_crypto(err):
    me = MagicMock()
    me.lang = "ko"
    me._live_now.return_value = True
    me._claim_entry.return_value = True
    fake = MagicMock()
    fake._symbol.return_value = "BTCUSDT"
    fake.list_open_positions.return_value = []
    fake.current_market_price.return_value = None
    fake.place_entry.return_value = {"error": err}
    eqgui._build_broker = MagicMock(return_value=fake)
    eqgui.messagebox = MagicMock()
    cfg = {"broker": "bybit", "f1": "k", "f2": "s", "f3": "", "acct": "", "one_r": 600.0, "label": "t",
           "live": True, "prop": {}, "pct": {}}
    eqgui.App._enter_account(me, cfg, {"entry_ref": 60000.0, "published_at": None}, "BTC", "LONG", 59500.0, 1.0)
    for c in me.root.after.call_args_list:
        c[0][1]()
    evs = [c.kwargs.get("why") for c in me._send_ev.call_args_list if c[0][0] == "entry_fail"]
    return me, evs


me, evs = run_crypto("HTTPSConnectionPool(host='api.bybit.com', port=443): Read timed out. (read timeout=15)")
_w = eqgui.messagebox.showwarning.call_args
chk("C2 크립토 타임아웃 → '진입 결과 미확인' 경고(거절 팝업 아님)·서버 사유 error",
    _w is not None and _w[0][0] == "진입 결과 미확인" and "손절은 주문에 함께 붙어" in _w[0][1]
    and not eqgui.messagebox.showerror.called and evs == ["error"], (_w, evs))
chk("C2 미확인 경로는 체결 보고·감시 기억 안 함(종전과 같음)", not me._note_fill.called and not me._remember_open.called)
me, evs = run_crypto("Bybit /v5/order/create failed: retCode=110007 ab not enough for new order")
_e = eqgui.messagebox.showerror.call_args
chk("C3 거래소 거절은 종전대로 '거절'", _e is not None and "거절되었습니다" in _e[0][1] and evs == ["rejected"], (_e, evs))


# ══ M: NT8 포지션을 그 인스턴스 계좌로 거름 ═══════════════════════════════════════
def nt8_with(accounts, positions, accts=("LT-A", "LT-B", "Sim101")):
    st = SimpleNamespace(lock=threading.Lock(), last_state={
        "positions": positions, "accounts": [{"name": n} for n in accts]}, last_state_ts=time.time())
    nb = N8.NT8Broker(SimpleNamespace(port=18399, token="t", accounts=list(accounts), symbol_map={},
                                      journal_path=os.path.join(_TMP, "j.jsonl")))
    nb._state = st                                   # 브리지 서버를 열지 않는다(authenticate는 _state가 있으면 바로 돌아감)
    return nb, st


_P = [{"account": "LT-A", "instrument": "MNQ 12-26", "net_qty": 3},
      {"account": "LT-B", "instrument": "MNQ 12-26", "net_qty": 2},
      {"account": "Sim101", "instrument": "MGC 12-26", "net_qty": -1}]
nb, st = nt8_with(["LT-A"], [dict(p) for p in _P])
got = nb.list_open_positions()
chk("M1 계좌를 정한 인스턴스는 자기 계좌 포지션만", [(p.account_id, p.symbol) for p in got] == [("LT-A", "MNQ 12-26")],
    [(p.account_id, p.symbol) for p in got])
chk("M1 raw에 _accountId(그 포지션의 계좌), 공유 스냅샷은 그대로",
    got[0].raw.get("_accountId") == "LT-A" and "_accountId" not in st.last_state["positions"][0], got[0].raw)
nb, _ = nt8_with([" lt-a "], [dict(p) for p in _P])
chk("M2 계좌 비교는 casefold·공백 무시(ProjectX와 같은 규칙)", [p.account_id for p in nb.list_open_positions()] == ["LT-A"])
nb, _ = nt8_with([], [dict(p) for p in _P])
chk("M3 계좌를 안 정한 인스턴스는 종전대로 전부", len(nb.list_open_positions()) == 3)
nb, _ = nt8_with(["LT-A"], [dict(p) for p in _P])
chk("M4 flatten_all 계획도 자기 계좌만(애드온 명령의 accounts와 일치)",
    [p.account_id for p in nb.flatten_all(dry_run=True).planned] == ["LT-A"])


class NT8Broker(N8.NT8Broker):                       # noqa: F811 - 진입 경로가 이름으로 NT8을 가린다
    """실제 list_open_positions(계좌 필터)를 쓰고 발주·조회만 가짜로."""
    def place_entry(self, **k):
        self.placed = getattr(self, "placed", []) + [k.get("custom_tag")]
        return {"order_id": "o"}

    def position_qty(self, aid, con):
        return 3 if getattr(self, "placed", None) and aid == "LT-A" else 0

    def order_status(self, aid, tag):
        return {"entry": {"state": "Working"}}

    def close_contract(self, aid, con):
        self.flattened = getattr(self, "flattened", 0) + 1

    def current_market_price(self, con):
        return 0


st = SimpleNamespace(lock=threading.Lock(), last_state={
    "positions": [{"account": "LT-B", "instrument": "MNQ 12-26", "net_qty": 2}],
    "accounts": [{"name": "LT-A"}, {"name": "LT-B"}]}, last_state_ts=time.time())
nb = NT8Broker(SimpleNamespace(port=18399, token="t", accounts=["LT-A"], symbol_map={},
                               journal_path=os.path.join(_TMP, "j.jsonl")))
nb._state = st
a = run(nb, [("MNQ", 3)], acct="LT-A")
chk("M5 다른 계좌(LT-B)의 MNQ를 '이전 세션 잔여'로 보지 않고 LT-A 진입 진행",
    len(getattr(nb, "placed", [])) == 1 and getattr(nb, "flattened", 0) == 0
    and not any("청산 확인 실패" in m or "이전 세션 잔여" in m for m in a.logs),
    (getattr(nb, "placed", []), getattr(nb, "flattened", 0), [m for m in a.logs if "잔여" in m]))

time.time, time.sleep = _real_time, _real_sleep
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
