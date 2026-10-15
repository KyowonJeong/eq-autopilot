#!/usr/bin/env python3
# 평가 계좌 익절 3차 리뷰 수리(앱 2026.10.14a, 2026-10-14) 오프라인 시험. 0 = 통과. 리뷰 소견마다 그것을 잡았을 시험:
#   P0-1 앱이 넣은 포지션만 정리(손으로 연 계약, 손으로 더한 수량, 무장만 된 계좌, 재시작 뒤 끝난 leg) - 회원이 넣은 통과 목표만 종전(12e) 계좌 전체
#   P0-2 ProjectX 시세: 0, 음수, NaN, 2분 넘은 봉 버림, 15초 안 1.5% 넘게 튄 값은 1초 뒤 다른 값으로 확인, 1초 뒤 두 번째 읽기, 잔고 - 실현 맞춰 보기
#   P0-3 NT8: 서로 다른 두 push, (NetLiq - 현금)과 브리지 미실현 맞춰 보기, 브리지 평균가
#   P0-4 통과 익절 여유 = max($50, 2틱 x 정리할 계약)
#   P0-5 Lucid 원장: 시작 잔고 이력으로 끝 손익을 고침, 빈 날이 있으면 통과 표시 안 함
#   P0-6 정리 직전 포지션 → 잔고 → 실현 → 포지션 새로 읽기
#   P1-7 거래 기록 시각(소수 초 5자리, 파이썬 3.9), 못 읽은 행 = 기록 불완전
#   P1-8 라이브 잠금(force_dry_run), 그림자 모드 = 모든 주문 경로(청산, 남은 래치, 주문 걷기), 서버 불통 폴백은 주문
#   P1-9 서버 핵심 값 회사, 규모별 범위 + 통째로 거절 + 목표가 올라가면 통과 표시 풀기 + 오늘 바뀐 목표로 통과 표시 안 함
#   P1-10 같은 금액 막기는 그 거래일만 + 모자란 만큼 다음 날 여유
#   P1-11 XFA 그날 첫 잔고 $0.00
#   P2-12 같은 로그인 다른 계좌가 1초 구간이면 거래 기록 조회 안 함
#   P2-13 규모 모르는 XFA 한도 = 50K 줄, P2-14 상한 비율 < 일관성 선, P2-15 최고일을 다 모르면 통과 표시 안 함
# HOME, APPDATA = 임시 폴더, 네트워크 차단, 브로커는 전부 가짜, EQ_NO_ALERT=1.
import datetime as _dt
import inspect
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_dcr3_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_DAYCAP_OFF", None)
os.environ.pop("EQ_DAYCAP_SHADOW", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui  # noqa: E402
eqgui._TEST_BUILD = False   # 시험판 분기(_TEST_BUILD True)에서도 회원판 기본값(그림자 모드 꺼짐)으로 시험한다 - 시험판은 test_test_build_flag_offline

assert eqgui.APP_DIR.startswith(HOME), eqgui.APP_DIR
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:600]}"))
    if not c:
        FAIL.append(n)


E = eqgui
P0 = dict(E._PROP_DEFAULTS)
GC, MGC = "CON.F.US.GC.Z26", "CON.F.US.MGC.Z26"


# ── 가짜 브로커와 앱 ─────────────────────────────────────────────────────────────
class FakePX:
    """ProjectX 가짜: 로그인 하나에 계좌 여럿. 포지션 (계좌 id, 계약) → 방향 있는 수량, 평균가, 시세, 계좌별 오늘 실현."""
    name = "projectx"

    def __init__(self, accts=None, realized=None):
        self.cfg = SimpleNamespace(user_name="u@x", accounts=[])
        self.accts = accts or [{"id": 101, "name": "150KTC-V2-1", "balance": 151000.0}]
        self.realized = dict(realized if realized is not None else {101: 1000.0})
        self.pos, self.avgs, self.marks = {}, {}, {}
        self.orders, self.ctr, self.calls = [], {"oid": 1000}, []
        self.close_fail = set()
        self.after_open_positions = None
        self.only = None                 # 진입 경로의 브로커는 그 계좌만 보인다(실제 _build_broker(..., [sc])와 같게)

    def view(self, acct):
        """같은 상태를 나눠 쓰고 계좌 목록, 포지션 목록만 그 계좌로 거른 사본(진입용 브로커)."""
        import copy as _cp
        v = _cp.copy(self)
        v.only = {str(acct)}
        return v

    def _mine(self, a):
        return self.only is None or str(a["id"]) in self.only or a["name"] in self.only

    def _aid(self, x):
        for a in self.accts:
            if str(x) in (str(a["id"]), a["name"]):
                return a["id"]
        return int(x)

    def _accounts(self, *a, **k):
        self.calls.append(("_accounts",))
        return [dict(a) for a in self.accts if self._mine(a)]

    def account_balance(self, acct):
        self.calls.append(("account_balance", acct))
        for a in self.accts:
            if str(acct) in (str(a["id"]), a["name"]):
                return a["balance"]
        return None

    def list_open_positions(self):
        self.calls.append(("list_open_positions",))
        out = []
        for (aid, con), q in self.pos.items():
            if q and self._mine(next(a for a in self.accts if a["id"] == aid)):
                nm = next(a["name"] for a in self.accts if a["id"] == aid)
                out.append(SimpleNamespace(account_id=str(aid), account_name=nm, symbol=con, net_qty=q,
                                           raw={"_accountId": aid, "contractId": con, "type": 1 if q > 0 else 2,
                                                "averagePrice": self.avgs.get((aid, con))}))
        return out

    def open_positions(self, aid, timeout=None):
        self.calls.append(("open_positions", aid))
        out = [{"contractId": c, "type": 1 if q > 0 else 2, "size": abs(q), "averagePrice": self.avgs.get((a, c))}
               for (a, c), q in self.pos.items() if a == aid and q]
        if self.after_open_positions:
            self.after_open_positions(self)
        return out

    def place_entry(self, **k):
        self.calls.append(("place_entry", dict(k)))
        aid, con, q = int(k["account_id"]), k["contract_id"], int(k["size"])
        if k.get("dry_run"):
            return {"dry_run": True, "would_place": {"contractId": con}, "would_place_stop": {"stopPrice": k.get("stop_loss_price")}}
        lg = str(k["side"]).upper() == "LONG"
        self.pos[(aid, con)] = self.pos.get((aid, con), 0) + (q if lg else -q)
        self.avgs[(aid, con)] = 2650.0
        self.ctr["oid"] += 2
        _oid = self.ctr["oid"]
        self.orders.append({"id": _oid, "aid": aid, "type": 4, "side": 1 if lg else 0, "size": q, "contractId": con,
                            "stopPrice": k.get("stop_loss_price")})
        return {"entry": {"orderId": _oid - 1}, "stop": {"orderId": _oid, "success": True},
                "stop_price": k.get("stop_loss_price")}

    def position_qty(self, aid, con):
        self.calls.append(("position_qty", con))
        return abs(int(self.pos.get((int(aid), con), 0)))

    def current_market_price(self, con):
        self.calls.append(("current_market_price", con))
        return self.marks.get(con)

    def close_contract(self, aid, con):
        """시장가 청산: 그 계좌, 그 계약 포지션을 지금 시세(없으면 평균가)로 실현해 잔고와 오늘 실현에 더한다."""
        self.calls.append(("close_contract", int(aid), con))
        if con in self.close_fail:
            return
        q = self.pos.pop((int(aid), con), 0)
        avg = self.avgs.get((int(aid), con)) or 0.0
        mk = self.marks.get(con, avg)
        pnl = (float(mk) - float(avg)) * q * E._PV_BY_ROOT.get(E._daycap_root(con), 0.0)
        for a in self.accts:
            if a["id"] == int(aid):
                a["balance"] = float(a["balance"]) + pnl
        if not isinstance(self.realized.get(int(aid)), Exception):
            self.realized[int(aid)] = float(self.realized.get(int(aid)) or 0.0) + pnl

    def day_realized(self, aid, start_iso, timeout=None):
        self.calls.append(("day_realized", int(aid), timeout))
        v = self.realized.get(int(aid), 0.0)
        if isinstance(v, Exception):
            raise v
        return float(v)

    def _tick_size(self, con):
        return 0.1 if "GC" in con else 0.25

    def _open_orders(self, aid):
        self.calls.append(("_open_orders", int(aid)))
        return [dict(o) for o in self.orders if o["aid"] == int(aid)]

    def _cancel_order(self, aid, oid):
        self.calls.append(("_cancel_order", int(aid), oid))
        self.orders[:] = [o for o in self.orders if o["id"] != oid]

    def cancel_order(self, aid, oid):
        self._cancel_order(aid, oid)


class Stub:
    lang = "ko"
    STOP_ORDER_CHECK_SEC = E.App.STOP_ORDER_CHECK_SEC
    # 3차 리뷰 P1: 감시 주문은 라이브 잠금(force_dry_run)과 그림자 모드를 따른다 - 시험 기본은 라이브(잠금 꺼짐)
    _gate = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True}}

    def __init__(self, b=None):
        self.logs, self.alerts, self.evs, self.skips, self.fills = [], [], [], [], []
        self._open_ctx, self._unconf_watch = {}, {}
        self._dc_mon = {}
        self.root = SimpleNamespace(after=lambda *a, **k: None)
        self._acfg = {}
        self._sig_accts = {}
        self._sig_on = True
        self._flat_seen = {}
        self._fake_b = b
        self.prompts, self.saved = [], 0

    def log(self, m):
        self.logs.append(str(m))

    def _claim_entry(self, sig, aid, **k):
        return True

    def _resolve_contract(self, b, s):
        return f"CON.F.US.{s}.Z26"

    def _nt8_standby(self):
        return False

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def _send_ev(self, kind, asset, **k):
        self.evs.append((kind, asset, k))

    def _entry_skip_ev(self, asset, why):
        self.skips.append((asset, why))

    def _send_fill(self, asset, closed=False, why=None):
        self.fills.append((asset, closed, why))

    def _restore_open_ctx(self, asset):
        return False

    def _acct_broker(self, asset, acct):
        return (acct or {}).get("broker") or "projectx"

    def _creds_peek(self, asset, bk):
        return {"f1": "u@x", "f3": ""}

    def _passtp_broker(self, cfg):
        return self._fake_b

    def _save_cfg(self, **k):
        self.saved += 1

    def _daycap_pass_prompt_show(self, broker, acct, lbl, reco):
        self.prompts.append((broker, acct, lbl, reco))

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


_STUB_OWN = {"_passtp_broker", "_acct_broker", "_creds_peek", "_save_cfg", "_daycap_pass_prompt_show"}
for _n, _v in E.App.__dict__.items():
    if _n.startswith(("_daycap", "_dc_", "_lucid", "_reco", "_apply_reco", "_margin", "_passtp")) and _n not in _STUB_OWN:
        setattr(Stub, _n, _v)
for _n in ("_run_futures_entry", "_remember_open", "_persist_open_ctx", "_forget_open_ctx", "_fill_asset", "_planned_one_r",
           "_unconf_watch_add", "_unconf_watch_drop", "_unconf_save", "_unconf_key", "_check_stop_closed",
           "_size_clamp", "_prop_reco_apply", "_prop_reco_desc", "_prop_reco_leaves", "_live_now", "_fallback_kind"):     # 3차: 계좌 한도, 서버 권장 값
    setattr(Stub, _n, E.App.__dict__[_n])
Stub._passtp_broker = lambda self, cfg: self._fake_b
Stub._daycap_pass_prompt_show = lambda self, broker, acct, lbl, reco: self.prompts.append((broker, acct, lbl, reco))
# 다이얼로그는 메인 스레드 after(0) 경유 - 시험에선 바로 부른다
_real_sleep, _real_time = time.sleep, time.time
_clk = {"t": _real_time()}
time.time = lambda: _clk["t"]
time.sleep = lambda s: _clk.__setitem__("t", _clk["t"] + float(s))
SIG = {"id": "t-GC-dc", "entry_ref": 2650.0}
SC = "150KTC-V2-1"
CAP = {"broker": "projectx", "pr": dict(P0), "lbl": "TS150", "sc": SC, "risk": 2200.0 * 1.59}
_DAY0 = E._daycap_day_start


def _reset_state():
    for f in (E._DAYCAP_MON_PATH, E._DAYCAP_STATE_PATH, E._OPENCTX_PATH):
        try:
            os.remove(f)
        except OSError:
            pass


def _arm(st, rows):
    """rows = [(자산, 계좌 이름, prop, 브로커)] → _acfg + _sig_accts(라이브 무장)."""
    st._acfg = {}
    st._sig_accts = {}
    for asset, nm, pr, bk in rows:
        accts = st._acfg.setdefault(asset, {"broker": bk, "accounts": []})["accounts"]
        accts.append({"id": nm, "prop": dict(pr), "on": True, "broker": bk, "one_r": 500.0})
        st._sig_accts[(asset, len(accts) - 1)] = {"broker": bk, "acct": nm, "live": True, "label": nm[-4:], "f1": "u@x",
                                                  "f2": "k", "f3": "", "prop": dict(pr)}


def run_entry(st, b, legs, direction="LONG", stop=2645.0, cap=CAP, live=True, sc=SC, asset="GC"):
    if cap is None:
        E.App._run_futures_entry(st, b, sc, legs, direction, stop, SIG, live, _dt.datetime.now(), asset, None)
    else:
        E.App._run_futures_entry(st, b, sc, legs, direction, stop, SIG, live, _dt.datetime.now(), asset, None, cap=cap)


def scan(st):
    E.App._daycap_mon_scan(st)


def _due_now(st):
    st._dc_due = {}
    E.App._dc_cache_clear(st)            # 3차: 시세, 포지션, 계좌 목록 캐시와 호출 한도 셈(가짜 시계가 멈춰 있어 시험마다 새로)


NOW_DAY = lambda: _DAY0()[1]    # noqa: E731
K1 = "projectx|150ktc-v2-1"
LU = "LDI150A1234"


class HistPX(FakePX):
    """Trade/search(거래 기록)까지 주는 ProjectX 가짜. hist = [(UTC datetime, profitAndLoss, fees)]. hist_err = 예외면 그것을 던진다."""

    def __init__(self, *a, hist=None, hist_err=None, slip=0.0, **k):
        FakePX.__init__(self, *a, **k)
        self.hist, self.hist_err, self.slip = list(hist or []), hist_err, float(slip)

    def trades(self, aid, start_iso, timeout=None):
        self.calls.append(("trades", int(aid), timeout))
        if self.hist_err is not None:
            raise self.hist_err
        return [{"id": i, "creationTimestamp": ts.isoformat().replace("+00:00", "Z"), "profitAndLoss": p, "fees": f,
                 "voided": False} for i, (ts, p, f) in enumerate(self.hist)]

    def close_contract(self, aid, con):
        if self.slip:
            _m = dict(self.marks)
            self.marks = {c: (v - self.slip) for c, v in _m.items()}
            try:
                return FakePX.close_contract(self, aid, con)
            finally:
                self.marks = _m
        return FakePX.close_contract(self, aid, con)


class FakeNT8:
    name = "nt8"

    def __init__(self, acct=LU):
        self.acct, self.cash, self.nl, self.pos, self.closed, self.execs, self.calls = acct, 150000.0, 150000.0, {}, [], [], []

    def snapshot_fresh(self, max_age=10.0):
        return True

    def account_connected(self, a):
        return True

    def account_balance(self, a):
        self.calls.append(("account_balance", a))
        return self.cash

    def account_netliq(self, a):
        return self.nl

    def account_unrealized(self, a):                      # 브리지 미실현 = NetLiq - 현금(3차 리뷰: 정리 전에 맞춰 본다)
        return self.nl - self.cash

    def closed_fills(self, start_ms):
        return [e for e in self.execs if e["ts_ms"] >= start_ms]

    def list_open_positions(self):
        return [SimpleNamespace(account_id=self.acct, account_name=self.acct, symbol=s, net_qty=q,
                                raw={"_accountId": self.acct}) for s, q in self.pos.items() if q]

    def close_contract(self, aid, sym):
        self.closed.append(sym)
        self.pos.pop(sym, None)
        self.cash = self.nl


def _app_leg(st, b, sc, con, q, asset="NQ"):
    """앱이 넣은 leg 기록(감시는 앱이 넣은 수량만 정리한다, 3차 리뷰 P0) - 시험이 포지션을 손으로 심을 때 같이 심는다."""
    E.App._daycap_mon_note(st, asset, b, {"broker": getattr(b, "name", "projectx"), "sc": sc}, sc, con, None,
                           "LONG" if q > 0 else "SHORT", abs(q), {"day": _DAY0()[1], "lbl": sc[-4:], "stage": "eval"})


def _tdays_back(n):
    """오늘 앞 거래일 n개(오래된 것부터, 주말 건너뜀)."""
    out, d = [], _DAY0()[1]
    for _ in range(n):
        x = _dt.date.fromisoformat(d) - _dt.timedelta(days=1)
        while x.weekday() >= 5:
            x -= _dt.timedelta(days=1)
        d = x.isoformat()
        out.append(d)
    return list(reversed(out))


def _lucid_chain(st, bases, acct=None):
    """Lucid 계좌를 처음(시작 잔고 = 규모)부터 빠짐없이 본 거래일들 - 시작 잔고 이력과 그 차(거래일 끝 손익). 통과 표시 조건(3차 리뷰 P0)."""
    k = "nt8|" + str(acct or LU).casefold()
    days = _tdays_back(len(bases))
    E.App._dc_state(st)["nt8_bases"][k] = dict(zip(days, bases))
    dd = E.App._dc_state(st)["days"].setdefault(k, {})
    for (d1, b1), b2 in zip(zip(days, bases), bases[1:]):
        dd[d1] = round(b2 - b1, 2)


def _new_st(b, rows=None):
    _reset_state()
    st = Stub(b)
    st.root = SimpleNamespace(after=lambda ms, fn=None, *a: fn() if fn else None)
    if rows:
        _arm(st, rows)
    _due_now(st)
    return st


def _cl(b):
    return [c for c in b.calls if c[0] == "close_contract"]


def _past(days, hour=1):
    """오늘 거래일 시작에서 days일 전 + hour시간(UTC) - 그 거래일 안의 시각."""
    return _DAY0()[0] - _dt.timedelta(days=days) + _dt.timedelta(hours=hour)


# ── 3차 리뷰 가짜 ────────────────────────────────────────────────────────────────
class MarkPX(HistPX):
    """시세를 봉 시각과 함께 주는 ProjectX 가짜(mark_price). seq[계약] = 다음 mark_price 응답 목록(값 또는 (값, 봉 시각) 또는 None) -
    다 쓰면 marks의 값과 지금 시각. 앱 몫만 정리(partial_close_contract)는 PartPX만."""

    def __init__(self, *a, **k):
        HistPX.__init__(self, *a, **k)
        self.seq = {}

    def mark_price(self, con, max_age_s=120.0, timeout=None):
        self.calls.append(("mark_price", con))
        q = self.seq.get(con)
        if q:
            v = q.pop(0)
            if v is None:
                return None
            if isinstance(v, tuple):
                return v
            return (float(v), time.time() - 1.0)
        if con in self.marks:
            return (float(self.marks[con]), time.time() - 1.0)
        return None

    def current_market_price(self, con):
        raise AssertionError("watch must use mark_price on ProjectX")


class PartPX(MarkPX):
    def partial_close_contract(self, aid, con, size):
        self.calls.append(("partial_close_contract", int(aid), con, int(size)))
        q = self.pos.get((int(aid), con), 0)
        sgn = 1 if q > 0 else -1
        mk = self.marks.get(con, self.avgs.get((int(aid), con)) or 0.0)
        pnl = (float(mk) - float(self.avgs.get((int(aid), con)) or 0.0)) * sgn * int(size) * E._PV_BY_ROOT.get(E._daycap_root(con), 0.0)
        self.pos[(int(aid), con)] = q - sgn * int(size)
        for a in self.accts:
            if a["id"] == int(aid):
                a["balance"] = float(a["balance"]) + pnl
        self.realized[int(aid)] = float(self.realized.get(int(aid)) or 0.0) + pnl


class BridgeNT8(FakeNT8):
    """새 브리지: 한 push의 (시각, 현금, NetLiq, 미실현, 포지션). ts_fn이 None이면 push 시각이 멈춰 있다."""

    def __init__(self, *a, **k):
        FakeNT8.__init__(self, *a, **k)
        self.u = None
        self.ts_fn = lambda: time.time()
        self.ts0 = 1000.0

    def state_ts(self):
        return self.ts_fn() if self.ts_fn else self.ts0

    def snapshot_pair(self, acct):
        u = self.u if self.u is not None else (self.nl - self.cash)
        return (self.state_ts(), self.cash, self.nl, u,
                [{"account": self.acct, "instrument": s, "net_qty": q, "avg_price": 25000.0} for s, q in self.pos.items() if q])


MNQ = "CON.F.US.MNQ.Z26"


def _legs_note(st, b, sc, con, q, stop=None, asset="GC"):
    E.App._daycap_mon_note(st, asset, b, {"broker": getattr(b, "name", "projectx"), "sc": sc}, sc, con, stop,
                           "LONG" if q > 0 else "SHORT", abs(q), {"day": _DAY0()[1], "lbl": sc[-4:], "stage": "eval"})


def _pcl(b):
    return [c for c in b.calls if c[0] == "partial_close_contract"]


def _cancels(b):
    return [c for c in b.calls if c[0] == "_cancel_order"]


try:
    # ════════ P0-1 앱이 넣은 포지션만 정리(수동 우선) ════════
    # R1a 무장만 된 Combine(앱 진입 없음), 회원이 손으로 연 MNQ 20, 오늘 3,000 + 2,000 → 정리 주문 없음 + 오늘 닿음 표시 + 차분한 로그
    b = MarkPX(realized={101: 3000.0})
    b.accts[0]["balance"] = 153000.0
    st = _new_st(b, [("NQ", SC, P0, "projectx")])
    b.pos[(101, MNQ)] = 20
    b.avgs[(101, MNQ)] = 25000.0
    b.marks = {MNQ: 25000.0}
    scan(st)
    b.marks = {MNQ: 25050.0}
    _due_now(st)
    scan(st)
    ck("R1a 앱 진입 없는 무장 계좌의 손 포지션(MNQ 20)은 닫지 않는다 + 오늘 닿음 표시(앱은 오늘 더 진입 안 함) + '그대로 둡니다'",
       not _cl(b) and not _pcl(b) and E.App._daycap_hit_today(st, "projectx", SC, NOW_DAY())
       and any("앱이 넣은 포지션이 없어 정리하지 않습니다" in m and "손으로 연 포지션(MNQ 20)은 그대로 둡니다" in m for m in st.logs),
       (b.calls[-6:], st.logs[-3:]))
    # R1b 앱 GC 2 + 손으로 GC 3 더함(같은 계약 5) → ProjectX는 앱 몫 2만 partialCloseContract, 회원 3은 그대로
    b = PartPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.pos[(101, GC)] = 5
    b.marks = {GC: 2660.0}                                  # 5 x 10 x 100 = 5,000 ≥ 4,860
    _due_now(st)
    scan(st)
    ck("R1b 같은 계약에 회원이 더한 수량(GC 2 + 3) → 앱 몫 2만 partialCloseContract, closeContract 없음, 회원 3 남음",
       _pcl(b) == [("partial_close_contract", 101, GC, 2)] and not _cl(b) and b.pos.get((101, GC)) == 3,
       (b.calls[-8:], b.pos, st.logs[-3:]))
    ck("R1b2 정리 로그가 앱 몫과 손 포지션을 나눠 말한다",
       any("앱이 넣은 포지션(GC 2)을 정리합니다" in m and "손으로 연 포지션(GC 3)은 그대로 둡니다" in m for m in st.logs), st.logs[-4:])
    ck("R1b3 앱 몫이 정리되면(회원 3 남음) 마무리: 오늘 닿음 표시 + 앱 손절(1002)만 걷음",
       E.App._daycap_hit_today(st, "projectx", SC, NOW_DAY()) and K1 not in getattr(st, "_dc_fired", {})
       and [c[2] for c in _cancels(b)] == [1002], (_cancels(b), b.orders))
    # R1b4 NT8처럼 앱 몫만 따로 닫을 수 없는 브로커 = 닫지 않고 로그 + 오늘 닿음 표시
    b = MarkPX(realized={101: 0.0})                         # 앱 몫만 닫는 주문이 없는 브로커
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.pos[(101, GC)] = 5
    b.marks = {GC: 2660.0}
    _due_now(st)
    scan(st)
    ck("R1b4 앱 몫만 따로 닫을 수 없으면(종목 전체 정리뿐) 닫지 않는다 + '따로 정리하지 않습니다' + 오늘 닿음",
       not _cl(b) and E.App._daycap_hit_today(st, "projectx", SC, NOW_DAY())
       and any("따로 정리하지 않습니다" in m for m in st.logs), st.logs[-3:])
    # R1c 앱 GC 2 + 회원 MNQ 10(다른 계약) → GC만 닫고 MNQ와 그 손절, 회원 지정가는 그대로
    b = MarkPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.pos[(101, MNQ)] = 10
    b.avgs[(101, MNQ)] = 25000.0
    b.orders.append({"id": 7001, "aid": 101, "type": 4, "side": 1, "size": 10, "contractId": MNQ, "stopPrice": 24900.0})
    b.orders.append({"id": 7002, "aid": 101, "type": 1, "side": 0, "size": 1, "contractId": GC, "limitPrice": 2600.0})
    b.marks = {GC: 2660.0, MNQ: 25200.0}                    # GC +2,000, MNQ +4,000 → 6,000
    _due_now(st)
    scan(st)
    ck("R1c 앱 GC만 정리, 회원 MNQ 10은 그대로 + 걷는 주문은 앱 GC 손절뿐(회원 MNQ 손절, GC 지정가는 그대로)",
       [c[2] for c in _cl(b)] == [GC] and b.pos.get((101, MNQ)) == 10 and [c[2] for c in _cancels(b)] == [1002]
       and {o["id"] for o in b.orders} == {7001, 7002}, (_cl(b), _cancels(b), b.orders))
    # R1d 재시작, 무장 없음: 앱 GC는 회원이 이미 닫았고(leg 20초 넘음) 회원이 MNQ 30을 손으로 → 닫지 않는다
    b = MarkPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.pos.pop((101, GC), None)
    b.pos[(101, MNQ)] = 30
    b.avgs[(101, MNQ)] = 25000.0
    _clk["t"] += 30
    st2 = Stub(b)
    st2.root = SimpleNamespace(after=lambda ms, fn=None, *a: fn() if fn else None)
    st2._sig_on = False
    st2._dc_mon = E._load_daycap_mon()
    _due_now(st2)
    b.marks = {MNQ: 25082.0}
    scan(st2)
    ck("R1d 재시작 뒤 무장 없음 + 앱 leg는 끝남(플랫) + 회원 MNQ 30 = 닫지 않음, 끝난 leg 기록은 놓는다",
       not _cl(b) and not _pcl(b) and K1 not in st2._dc_mon, (b.calls[-5:], st2._dc_mon))
    # R1e 앱 GC leg가 손절로 끝난 뒤(20초 넘게 플랫) 회원이 같은 방향 GC 2를 손으로 → 앱 몫으로 보지 않는다
    b = MarkPX(realized={101: 0.0})
    b.accts.append({"id": 102, "name": "150KTC-V2-2", "balance": 150000.0})
    b.realized[102] = 0.0
    st = _new_st(b, [("GC", SC, P0, "projectx"), ("GC", "150KTC-V2-2", P0, "projectx")])
    run_entry(st, b.view(SC), [("GC", 2)])
    b.pos[(101, MNQ)] = 1                                   # 다른 손 포지션이 있어 계좌는 플랫이 아니다(기록이 남는다)
    b.avgs[(101, MNQ)] = 25000.0
    b.pos.pop((101, GC), None)                              # 앱 GC 손절
    b.marks = {GC: 2650.0, MNQ: 25000.0}
    _clk["t"] += 25
    _due_now(st)
    scan(st)
    b.pos[(101, GC)] = 2                                    # 회원이 손으로 다시 GC 2
    b.avgs[(101, GC)] = 2650.0
    b.marks = {GC: 2675.0, MNQ: 25000.0}                    # +5,000
    _due_now(st)
    scan(st)
    ck("R1e 끝난 앱 leg(플랫 20초 넘음)는 기록에서 빠져, 그 뒤 회원이 연 같은 계약은 닫지 않는다",
       not _cl(b) and not ((st._dc_mon.get(K1) or {}).get("legs")), (_cl(b), st._dc_mon.get(K1)))
    # R1f 회원이 넣은 통과 목표(종전 12e 규약 '그 잔고에 이를 때 계좌 포지션을 정리') = 계좌 전체(손 포지션 포함)
    b = MarkPX(realized={101: 0.0})
    b.accts[0]["balance"] = 157000.0
    _prs = dict(P0, pass_tp=158500.0, pass_set=True)
    st = _new_st(b, [("GC", SC, _prs, "projectx")])
    run_entry(st, b, [("GC", 2)], cap=dict(CAP, pr=_prs))
    b.pos[(101, MNQ)] = 5
    b.avgs[(101, MNQ)] = 25000.0
    b.marks = {GC: 2655.0, MNQ: 25100.0}                    # 157,000 + 1,000 + 1,000 = 159,000 ≥ 158,500 + 비용 여유
    _due_now(st)
    scan(st)
    ck("R1f 회원이 넣은 통과 목표는 종전(12e)대로 계좌 전체를 정리(GC, MNQ)",
       sorted(c[2] for c in _cl(b)) == sorted([GC, MNQ]), (_cl(b), st.logs[-3:]))

    # ════════ P0-2 ProjectX 시세 한 번으로 닫지 않는다 ════════
    from eqexec.broker import projectx as PXM
    _ad = PXM.ProjectXBroker.__new__(PXM.ProjectXBroker)
    _nowu = _dt.datetime.fromtimestamp(time.time(), _dt.timezone.utc)

    def _bars(*bs):
        _ad._post = lambda path, body, timeout=None: {"bars": list(bs)}
        return PXM.ProjectXBroker.mark_price(_ad, GC)
    _fresh_t = (_nowu - _dt.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.52329+00:00")
    _old_t = (_nowu - _dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    _a1 = _bars({"t": _fresh_t, "c": 2651.2})
    _a2 = _bars({"t": _fresh_t, "c": 0})
    _a3 = _bars({"t": _fresh_t, "c": -5.0})
    _a4 = _bars({"t": _fresh_t, "c": float("nan")})
    _a5 = _bars({"t": _old_t, "c": 2690.0})
    _a6 = _bars({"t": _old_t, "c": 2690.0}, {"t": _fresh_t, "c": 2651.0})
    ck("R2a 어댑터 mark_price: 0, 음수, NaN, 3시간 지난 봉은 None / 새 봉(소수 초 5자리)은 (종가, 봉 시각) / 여러 봉이면 가장 새 봉",
       _a1 and _a1[0] == 2651.2 and abs(_a1[1] - (time.time() - 30)) < 2 and _a2 is None and _a3 is None and _a4 is None
       and _a5 is None and _a6 and _a6[0] == 2651.0, (_a1, _a2, _a3, _a4, _a5, _a6))
    _src_cmp = inspect.getsource(PXM.ProjectXBroker.current_market_price)
    ck("R2a2 지정가 경로가 쓰는 current_market_price는 종전 그대로(비대상 경로 12e와 같음)",
       "hours=6" in _src_cmp and "return float(bars[-1].get(\"c\")) if bars else None" in _src_cmp)
    # R2b 숏 GC 2, 한 번 0.0 → 닫지 않음(그 회차는 앞 정상 시세)
    b = MarkPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)], direction="SHORT", stop=2660.0)
    b.marks = {GC: 2649.0}
    scan(st)
    b.seq = {GC: [0.0]}
    _due_now(st)
    E.App._dc_map(st, "_dc_mgood").clear()
    st._dc_mgood[(id(b), "GC", "Z26")] = (time.time(), 2649.0)
    scan(st)
    ck("R2b 시세 0.0 한 번 = 닫지 않음", not _cl(b), (_cl(b), st.logs[-2:]))
    # R2c 롱 GC 2, 한 번 튄 값(+1.9%) = 닫지 않음, 같은 수준이 1초 넘어 새 봉으로 이어지면 받아들여 정리
    b = MarkPX(realized={101: 1000.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2651.0}
    scan(st)
    _clk["t"] += 1
    b.seq = {GC: [2701.0]}                                  # 15초 안의 정상 2,651에서 +1.9% - 바로 쓰지 않는다
    st._dc_due = {}
    for _n in ("_dc_acache", "_dc_pcache", "_dc_mcache"):
        E.App._dc_map(st, _n).clear()
    scan(st)
    _c1 = list(_cl(b))
    _clk["t"] += 1
    b.marks = {GC: 2651.2}                                  # 다음 회차는 원래 수준 - 튐이었다
    st._dc_due = {}
    for _n in ("_dc_acache", "_dc_pcache", "_dc_mcache"):
        E.App._dc_map(st, _n).clear()
    scan(st)
    ck("R2c 한 번 튄 시세(+1.9%) = 닫지 않음, 다음 회차 원래 수준", not _c1 and not _cl(b), (_c1, _cl(b)))
    _clk["t"] += 1
    b.seq = {GC: [(2701.0, time.time() - 1.0)]}
    st._dc_due = {}
    for _n in ("_dc_acache", "_dc_pcache", "_dc_mcache"):
        E.App._dc_map(st, _n).clear()
    scan(st)
    _c2 = list(_cl(b))
    _clk["t"] += 1.5
    b.seq = {GC: [(2701.5, time.time() - 1.0), (2701.5, time.time() - 0.5)]}
    b.marks = {GC: 2701.5}
    st._dc_due = {}
    for _n in ("_dc_acache", "_dc_pcache", "_dc_mcache"):
        E.App._dc_map(st, _n).clear()
    scan(st)
    ck("R2c2 큰 움직임도 1초 넘어 새 봉이 같은 수준(0.3% 안)이면 받아들여 정리", not _c2 and [c[2] for c in _cl(b)] == [GC],
       (_c2, _cl(b), st.logs[-3:]))
    # R2d 두 번 읽기: 첫 읽기는 선(2,669.48)을 넘지만 1초 뒤 새로 읽은 시세가 아래면 닫지 않는다
    b = MarkPX(realized={101: 1000.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2669.0}
    scan(st)
    b.seq = {GC: [2670.0, 2665.0]}                          # 첫 읽기 5,000, 두 번째 4,000
    st._dc_due = {}
    for _n in ("_dc_acache", "_dc_pcache", "_dc_mcache"):
        E.App._dc_map(st, _n).clear()
    _t0 = time.time()
    scan(st)
    _mr = [c for c in b.calls if c[0] == "mark_price"]
    ck("R2d 첫 읽기만 선을 넘고 1초 뒤 두 번째 읽기(캐시 없이)는 아래 = 닫지 않음", not _cl(b) and len(_mr) >= 3
       and time.time() - _t0 >= 1.0, (_cl(b), len(_mr)))
    # R2e 잔고 - 오늘 실현(그 거래일 시작 잔고)이 앞서 본 값과 $100 넘게 다르면 이번엔 닫지 않고, 다음 회차(맞는 값)에 닫는다
    b = MarkPX(realized={101: 1000.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2655.0}
    scan(st)                                                # 기준 = 151,000 - 1,000 = 150,000
    b.realized[101] = 3000.0                                # 실현 응답만 2,000 많다(잔고는 그대로)
    _clk["t"] += 61                                         # 실현 캐시(60초)가 지나 새로 읽는다
    b.marks = {GC: 2660.0}                                  # 3,000 + 2,000 = 5,000(틀린 실현으로) ≥ 4,860
    _due_now(st)
    scan(st)
    _c3 = list(_cl(b))
    ck("R2e 잔고와 오늘 실현이 서로 맞지 않으면($2,000 차이) 닫지 않음 + 차분한 로그 한 번",
       not _c3 and sum(1 for m in st.logs if "맞지 않아" in m and "다시 읽어 확인합니다" in m) == 1, st.logs[-3:])

    # ════════ P0-3 Lucid NetLiq 한 번으로 닫지 않는다 ════════
    n8 = BridgeNT8()
    st = _new_st(n8, [("NQ", LU, dict(P0, stage="eval"), "nt8")])
    n8.pos = {"NQ 12-26": 1}
    _legs_note(st, n8, LU, "NQ 12-26", 1, asset="NQ")
    n8.nl = 150500.0
    scan(st)
    n8.nl = 155000.0
    n8.u = 500.0                                            # NetLiq만 튐(브리지 미실현은 그대로 +500)
    _due_now(st)
    scan(st)
    ck("R3a NetLiq 한 번 튐(현금과의 차 5,000 vs 브리지 미실현 500) = 닫지 않음 + 로그 한 번",
       not n8.closed and sum(1 for m in st.logs if "브리지 미실현" in m and "맞지 않아" in m) == 1, (n8.closed, st.logs[-2:]))
    n8.u = None
    n8.nl = 154500.0                                        # 4,500 ≥ 4,410(맞는 값)인데 push 시각이 멈춤
    n8.ts_fn = None
    _due_now(st)
    scan(st)
    ck("R3b 다음 push가 오지 않으면(브리지 시각 그대로 5초) 같은 스냅샷으로 닫지 않는다", not n8.closed, n8.closed)
    n8.ts_fn = lambda: time.time()
    _due_now(st)
    scan(st)
    ck("R3c 서로 다른 두 push 모두 선을 넘고 NetLiq와 미실현이 맞으면 정리 + 오늘 닿음",
       n8.closed == ["NQ 12-26"] and E.App._daycap_hit_today(st, "nt8", LU, NOW_DAY()), (n8.closed, st.logs[-3:]))
    _snap = E.App._daycap_mon_snap(st, BridgeNT8(), "nt8", LU)
    ck("R3d 감시 스냅샷은 브리지 평균가를 든다(avg 버리지 않음)", _snap is not None and _snap["poss"] == [], _snap)
    _bn = BridgeNT8()
    _bn.pos = {"NQ 12-26": 2}
    _snap = E.App._daycap_mon_snap(st, _bn, "nt8", LU)
    ck("R3d2 포지션 avg = 브리지 avg_price, 한 push의 시각, 미실현", _snap["poss"][0]["avg"] == 25000.0 and _snap["ts"]
       and _snap["u"] == 0.0, _snap)

    # ════════ P0-4 통과 익절 여유 = max($50, 2틱 x 계약) ════════
    ck("R4a 틱 가치: NQ $5, MNQ $0.50, GC $10, MGC $1 → 2틱 x (GC 15) = $300, (MNQ 51) = $51, (NQ 'NQ 12-26' 6) = $60",
       (E._dc_tick_value("CON.F.US.ENQ.Z26"), E._dc_tick_value(MNQ), E._dc_tick_value(GC), E._dc_tick_value(MGC),
        E._dc_slip_margin([(GC, 15)]), E._dc_slip_margin([(MNQ, 51)]), E._dc_slip_margin([("NQ 12-26", 6)]))
       == (5.0, 0.5, 10.0, 1.0, 300.0, 51.0, 60.0))
    b = MarkPX(realized={101: 0.0}, slip=0.2, hist=[])      # 청산이 시세보다 2틱(0.2) 나쁘게 체결
    b.accts[0]["balance"] = 158000.0
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 15)], stop=2640.0)
    b.marks = {GC: 2650.0 + 1050.0 / 1500.0 + 0.0001}       # 잔고 + 미실현 159,050 - 종전 $50 여유면 닫았다
    _due_now(st)
    scan(st)
    _c4 = list(_cl(b))
    b.marks = {GC: 2650.0 + 1301.0 / 1500.0}                # 159,301 ≥ 159,000 + 2틱 x 15 x $10 = 159,300
    _due_now(st)
    scan(st)
    ck("R4b GC 15(Combine 한도): 159,050 = 그대로, 159,301 ≥ 159,300(여유 $300)에서 정리 → 2틱 미끄러져도 실현 159,001 ≥ 159,000 = 통과 표시",
       not _c4 and _cl(b) and b.accts[0]["balance"] >= 159000.0 and K1 in E.App._dc_state(st)["passed"]
       and any("여유 $300" in m for m in st.logs), (_c4, _cl(b), b.accts[0]["balance"], st.logs[-3:]))

    # ════════ P0-5 Lucid 원장: 앱이 중간에 꺼진 날 ════════
    n8 = BridgeNT8()
    st = _new_st(n8, [("NQ", LU, dict(P0, stage="eval"), "nt8")])
    _yd = _tdays_back(1)[0]
    E.App._dc_state(st)["nt8_bases"]["nt8|" + LU.casefold()] = {_yd: 150000.0}
    E.App._dc_state(st)["days"]["nt8|" + LU.casefold()] = {_yd: 3000.0}       # 앱이 낮에 본 +3,000(끝은 +5,000)
    n8.cash = n8.nl = 155000.0
    n8.pos = {"NQ 12-26": 1}
    _legs_note(st, n8, LU, "NQ 12-26", 1, asset="NQ")
    scan(st)
    n8.nl = 159060.0
    _due_now(st)
    scan(st)
    ck("R5a 어제 원장 3,000(덜 적힘) → 오늘 시작 잔고 155,000 - 어제 시작 150,000 = 5,000으로 고침 → 통과 160,000 + 여유, 159,060은 그대로",
       not n8.closed and E.App._dc_state(st)["days"]["nt8|" + LU.casefold()][_yd] == 5000.0
       and E.App._dc_best(st, "nt8", LU, NOW_DAY()) == (5000.0, "ledger"), (n8.closed, E.App._dc_best(st, "nt8", LU, NOW_DAY())))
    # R5b 빈 날이 있는 계좌(처음 본 날 시작 잔고 155,000 ≠ 규모): 통과 익절은 목표 기준으로 닫지만 통과로 표시하지 않는다 + 진입은 건너뜀
    n8 = BridgeNT8()
    st = _new_st(n8, [("NQ", LU, dict(P0, stage="eval"), "nt8")])
    E.App._dc_state(st)["days"]["nt8|" + LU.casefold()] = {_yd: 3000.0}
    n8.cash = n8.nl = 155000.0
    n8.pos = {"NQ 12-26": 1}
    _legs_note(st, n8, LU, "NQ 12-26", 1, asset="NQ")
    scan(st)
    n8.nl = 159060.0
    _due_now(st)
    scan(st)
    ck("R5b 모르는 날이 있으면 통과 익절(159,050)에서 정리하되 통과로 표시하지 않음 + Lucid 화면 확인 안내",
       n8.closed == ["NQ 12-26"] and "nt8|" + LU.casefold() not in E.App._dc_state(st)["passed"]
       and any("통과로 표시하지 않았습니다" in m and "Lucid 화면에서" in m for m in st.logs), (n8.closed, st.logs[-3:]))
    _capl = {"broker": "nt8", "pr": dict(P0, stage="eval"), "lbl": "LUC", "sc": LU, "risk": 1000.0}
    _pa = E.App._daycap_plan(st, n8, _capl, {"name": LU, "id": LU}, LU, [("MNQ", 3, "MNQ 12-26")], "NQ", True)
    _pb = E.App._daycap_plan(st, n8, _capl, {"name": LU, "id": LU}, LU, [("MNQ", 3, "MNQ 12-26")], "NQ", True)
    ck("R5c 통과 금액 이상이지만 표시 못 한 계좌 = 진입 건너뜀(pass_reached 한 번, 그 뒤 pass_check), 펀디드로 넘기지 않음",
       _pa == {"skip": True} and _pb == {"skip": True} and st.skips[-2:] == [("NQ", "pass_reached"), ("NQ", "pass_check")]
       and E.App._daycap_stage_cfg(st, "nt8", LU, dict(P0, stage="eval"), bal=150000.0)[0] == "eval", st.skips)
    # R5d Topstep 거래 기록이 없으면(못 읽음) 잔고 159,200이어도 통과로 표시하지 않고 건너뜀
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 159200.0}], realized={101: 0.0})
    st = _new_st(b)
    run_entry(st, b, [("GC", 2)])
    ck("R5d Topstep: 거래 기록 없이(앱 원장만) 159,200 ≥ 159,000 = 통과 표시 없이 건너뜀 + 안내",
       not any(c[0] == "place_entry" for c in b.calls) and K1 not in E.App._dc_state(st)["passed"]
       and st.skips == [("GC", "pass_reached")] and any("통과로 표시하지 않았습니다" in m for m in st.logs), (st.skips, st.logs[-2:]))

    # ════════ P0-6 정리 직전 모든 입력을 새로 읽는다 ════════
    b = MarkPX(realized={101: 3000.0})
    b.accts[0]["balance"] = 153000.0
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2655.0}
    scan(st)                                                # 실현 3,000 캐시(60초)
    b.realized[101] = 1500.0                                # 다른 계약 손 단타가 열렸다 닫힘(-1,500): 포지션 목록은 그대로
    b.accts[0]["balance"] = 151500.0
    _clk["t"] += 10
    b.marks = {GC: 2660.0}                                  # 캐시 실현이면 5,000, 실제 1,500 + 2,000 = 3,500
    st._dc_due = {}
    for _n in ("_dc_pcache", "_dc_mcache"):
        E.App._dc_map(st, _n).clear()
    scan(st)
    ck("R6a 캐시된 실현(3,000)으로 닿아도 정리 직전 새로 읽은 실현(1,500)으로 다시 계산 = 닫지 않음",
       not _cl(b) and any(c[0] == "day_realized" for c in b.calls[-12:]), (_cl(b), b.calls[-10:]))

    class RacePX(MarkPX):
        def __init__(self, *a, **k):
            MarkPX.__init__(self, *a, **k)
            self.race = 0

        def _accounts(self, *a, **k):
            out = MarkPX._accounts(self, *a, **k)
            if self.race:
                self.race -= 1
                if self.race == 0:                          # 계좌 목록(잔고)을 읽은 바로 뒤 GC 손절 체결
                    q = self.pos.pop((101, GC), 0)
                    loss = (2640.0 - 2650.0) * q * 100.0
                    self.accts[0]["balance"] += loss
                    self.realized[101] = float(self.realized.get(101) or 0.0) + loss
            return out
    for _when, _nm in ((1, "R6b 감시 회차"), (2, "R6c 정리 직전 확인")):
        b = RacePX(realized={101: 0.0}, hist=[])
        b.accts[0]["balance"] = 158000.0
        st = _new_st(b, [("GC", SC, P0, "projectx")])
        run_entry(st, b, [("GC", 2)])
        b.pos[(101, MNQ)] = 15
        b.avgs[(101, MNQ)] = 25000.0
        _legs_note(st, b, SC, MNQ, 15, stop=24900.0, asset="NQ")
        b.marks = {GC: 2647.5, MNQ: 25050.0}                # GC -500, MNQ +1,500 → 159,000 < 159,050
        _due_now(st)
        scan(st)
        b.marks = {GC: 2650.0, MNQ: 25050.0}                # 159,500 ≥ 159,050(GC가 손절되면 실제 157,500)
        b.race = _when
        _due_now(st)
        scan(st)
        ck(f"{_nm.split()[0]} 잔고를 읽은 뒤 손절 체결(포지션이 바뀜) - {_nm.split(maxsplit=1)[1]}에서 손실 전 잔고 + 손실 뒤 포지션으로 닫지 않는다",
           not _cl(b) and not _pcl(b), (_cl(b), st.logs[-3:]))

    # ════════ P1-7 거래 기록 시각(파이썬 3.9) ════════
    ck("R7a 시각 읽기: '.52329+00:00'(5자리), 'Z', 7자리 소수 초, 앱 _iso_utc와 어댑터 _parse_iso 같은 값",
       E._iso_utc("2026-10-06T15:00:01.52329+00:00") == _dt.datetime(2026, 10, 6, 15, 0, 1, 523290, tzinfo=_dt.timezone.utc)
       and E._iso_utc("2026-10-06T15:00:01Z") == _dt.datetime(2026, 10, 6, 15, 0, 1, tzinfo=_dt.timezone.utc)
       and E._iso_utc("2026-10-06T15:00:01.1234567+00:00").microsecond == 123456
       and PXM._parse_iso("2026-10-06T15:00:01.52329+00:00") == E._iso_utc("2026-10-06T15:00:01.52329+00:00"))
    b = HistPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    _d6 = _past(2, 1).strftime("%Y-%m-%dT%H:%M:%S.52329+00:00")
    _d7 = _past(1, 1).strftime("%Y-%m-%dT%H:%M:%S.523291+00:00")
    b.trades = lambda aid, start_iso, timeout=None: [
        {"creationTimestamp": _d6, "profitAndLoss": 6000.0, "fees": 10.0, "voided": False},
        {"creationTimestamp": _d7, "profitAndLoss": 1000.0, "fees": 10.0, "voided": False}]
    E.App._dc_hist_refresh(st, b, "projectx", SC, 101, NOW_DAY())
    _r7 = E.App._daycap_resolve_cfg(st, "projectx", SC, P0, name=SC, bal=151000.0)
    ck("R7b 5자리 소수 초 행도 읽어 최고일 6,000 → 통과 160,909.09 + 출처 hist",
       abs(_r7["pass_need"] - 160909.09) < 0.01 and _r7["best_src"] == "hist", _r7)
    b.trades = lambda aid, start_iso, timeout=None: [
        {"creationTimestamp": "garbage", "profitAndLoss": 6000.0, "fees": 10.0, "voided": False},
        {"creationTimestamp": _d7, "profitAndLoss": 1000.0, "fees": 10.0, "voided": False}]
    E.App._dc_state(st)["hist"].pop(K1, None)
    E.App._dc_hist_refresh(st, b, "projectx", SC, 101, NOW_DAY())
    _r7b = E.App._daycap_resolve_cfg(st, "projectx", SC, P0, name=SC, bal=151000.0)
    ck("R7c 못 읽은 행이 있으면 기록은 완전하지 않음(출처 partial) = 통과 표시 안 함 + 로그 한 번",
       _r7b["best_src"] == "partial" and E.App._dc_pass_markable(st, "projectx", _r7b, SC) == (False, "best")
       and any("시각을 읽지 못했습니다" in m for m in st.logs), (_r7b, st.logs[-2:]))
    _src_cf = inspect.getsource(PXM.ProjectXBroker.closed_fills)
    ck("R7d 트랙레코드 체결 읽기(closed_fills)도 같은 시각 읽기(_parse_iso)", "_parse_iso(ts)" in _src_cf)

    # ════════ P1-8 라이브 잠금, 그림자 모드는 모든 주문 경로에 ════════
    b = MarkPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    st._gate = {"ok": True, "enabled": True, "force_dry_run": True, "caps": {"use": True, "autoentry": True}}
    b.marks = {GC: 2675.0}
    _due_now(st)
    scan(st)
    _due_now(st)
    scan(st)
    ck("R8a 어드민 라이브 잠금(force_dry_run) = 청산 주문 없음 + '정리했을 경우 ... 라이브 잠금 중이라' 한 번(다시 확인 읽기 안 함)",
       not _cl(b) and sum(1 for m in st.logs if "정리했을 경우" in m and "라이브 잠금 중이라" in m) == 1, st.logs[-3:])
    st._gate = {"ok": False, "offline": True}
    st._killed = False
    _due_now(st)
    scan(st)
    ck("R8b 서버 불통 폴백(offline)은 마감 청산과 같이 주문을 낸다", [c[2] for c in _cl(b)] == [GC], _cl(b))
    b = MarkPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.close_fail = {GC}
    b.marks = {GC: 2675.0}
    _due_now(st)
    scan(st)
    _n1 = len(_cl(b))
    os.environ["EQ_DAYCAP_SHADOW"] = "1"
    try:
        for _ in range(3):
            _clk["t"] += 6
            _due_now(st)
            scan(st)
    finally:
        os.environ.pop("EQ_DAYCAP_SHADOW", None)
    ck("R8c 앞서 남은 정리 래치도 그림자 모드가 켜지면 청산, 주문 걷기를 더 내지 않는다(차분한 로그 한 번)",
       _n1 >= 1 and len(_cl(b)) == _n1 and not _cancels(b)
       and sum(1 for m in st.logs if "그림자 모드나 라이브 잠금이라 정리 주문을 내지 않았습니다" in m) == 1, (_n1, len(_cl(b)), st.logs[-3:]))

    # ════════ P1-9 서버 값: 회사, 규모별 범위 + 통째로 거절 + 통과 표시 ════════
    E._DAYCAP_SRV = {}
    st = _new_st(None)
    E.App._prop_reco_apply(st, {"targets": {"projectx": {"150000": 1000}}, "cap_pct": {"projectx": 0.40}})
    _r9 = E._daycap_resolve("projectx", P0, SC, 152000.0)
    ck("R9a 150K 목표 1,000, 비율 0.40(오타) = 통째로 거절, 상한 4,860, 통과 159,000 그대로 + 로그 한 번",
       E._DAYCAP_SRV == {} and _r9["cap"] == 4860.0 and _r9["pass_need"] == 159000.0
       and sum(1 for m in st.logs if "허용 범위를 벗어나" in m) == 1, (_r9, st.logs))
    ck("R9b 이익 목표 범위 = 내장값 ±25%(150K 6,750~11,250, 50K 2,250~3,750), 상한 비율 = 내장값 ±0.02 그리고 일관성 선 - 0.001 이하",
       E._srv_target_bounds("projectx", 150000) == (6750.0, 11250.0) and E._srv_target_bounds("projectx", 50000) == (2250.0, 3750.0)
       and E._srv_pct_bounds("projectx") == (0.52, 0.549) and tuple(round(x, 3) for x in E._srv_pct_bounds("nt8")) == (0.47, 0.499))
    # 내장값 -0.02부터 일관성 선 - 0.001까지의 서버 상한 비율은 모두 받는다(0.001 간격 전부, 핵심 이상 없음, 그 값 그대로 씀)
    _r9d = [(bk, round(x / 1000.0, 3)) for bk, lo, hi in (("projectx", 520, 549), ("nt8", 470, 499)) for x in range(lo, hi + 1)]
    _r9d_bad = [(bk, v) for bk, v in _r9d if E._prop_reco_core_bad({"cap_pct": {bk: v}})
                or (E._prop_reco_validate({"cap_pct": {bk: v}}).get("cap_pct") or {}).get(bk) != v]
    ck("R9d 상한 비율 서버 값: Topstep 0.520~0.549, Lucid 0.470~0.499를 모두 받고 그 값을 쓴다(일관성 선 - 0.001 이하), 0.55, 0.50은 거절",
       not _r9d_bad and E._prop_reco_core_bad({"cap_pct": {"projectx": 0.55}}) and E._prop_reco_core_bad({"cap_pct": {"nt8": 0.50}}), _r9d_bad)
    st = _new_st(None)
    E.App._daycap_mark_passed(st, "projectx", SC, bal=159100.0, pass_tp=159000.0, lbl="A", size=150000, via="watch",
                              target=9000.0, src="auto")
    E._DAYCAP_SRV = {"targets": {"projectx": {150000: 9500.0}}}
    _st9 = E.App._daycap_stage_cfg(st, "projectx", SC, P0, bal=159100.0)[0]
    ck("R9c 통과로 표시할 때 쓴 목표(9,000)보다 지금 목표(9,500)가 높으면 표시를 푼다(로그)",
       _st9 == "eval" and K1 not in E.App._dc_state(st)["passed"] and any("통과 표시를 풀었습니다" in m for m in st.logs),
       (_st9, st.logs[-2:]))
    E.App._dc_tchg_update(st)
    _rr = {"pass_src": "auto", "best_src": "hist", "size": 150000}
    _m0 = E.App._dc_pass_markable(st, "projectx", _rr, SC)
    _nx1 = lambda now_utc=None: _DAY0((now_utc or _dt.datetime.now(_dt.timezone.utc)) + _dt.timedelta(days=1))   # noqa: E731
    E._daycap_day_start = _nx1
    try:
        _m1 = E.App._dc_pass_markable(st, "projectx", _rr, SC)
    finally:
        E._daycap_day_start = _DAY0
    E._DAYCAP_SRV = {}
    ck("R9d 서버가 오늘 바꾼 목표로는 통과 표시를 하지 않고(target), 다음 거래일엔 한다 / 내장값 목표는 늘 된다",
       _m0 == (False, "target") and _m1 == (True, "") and E.App._dc_pass_markable(st, "projectx", _rr, SC) == (True, ""), (_m0, _m1))

    # ════════ P1-10 같은 금액에서 다시 닫지 않음 = 그 거래일만 ════════
    b = HistPX(accts=[{"id": 101, "name": SC, "balance": 157000.0}], realized={101: 0.0}, slip=0.5,
               hist=[(_past(2, 1), 3000.0, 10.0)])
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2660.3}
    _due_now(st)
    scan(st)
    _tf = dict(E.App._dc_state(st)["tp_fired"].get(K1) or {})
    _nx3 = lambda now_utc=None: _DAY0((now_utc or _dt.datetime.now(_dt.timezone.utc)) + _dt.timedelta(days=3))   # noqa: E731
    E._daycap_day_start = _nx3
    try:
        b.realized[101] = 0.0
        b.pos.clear()
        b.calls.clear()
        run_entry(st, b, [("GC", 2)])
        b.marks = {GC: 2655.2}                              # 158,960 + 1,040 = 160,000
        for _ in range(2):
            _clk["t"] += 1
            _due_now(st)
            scan(st)
        _tf3 = dict(E.App._dc_state(st)["tp_fired"].get(K1) or {})
    finally:
        E._daycap_day_start = _DAY0
    ck("R10a 미끄러진 날(159,050, 실현 158,960 = $40 모자람) 다음 거래일엔 다시 닫는다: 159,000 + $50 + 모자란 $40 = 159,090",
       _tf.get("amt") == 159050.0 and abs(float(_tf.get("short") or 0) - 40.0) < 0.01 and _cl(b)
       and abs(float(_tf3.get("amt") or 0) - 159090.0) < 0.01, (_tf, _tf3, _cl(b)))

    # ════════ P1-11 XFA 처음 본 잔고 $0.00 ════════
    XN = "EXPRESS-V2-DLL-9"
    st = _new_st(None)
    _d0 = NOW_DAY()
    ck("R11a 그날 처음 본 잔고 $0.00도 그대로(뒤 잔고 2,100이어도 0 단계)",
       E.App._dc_day_bal(st, "projectx", XN, 0.0, _d0) == 0.0 and E.App._dc_day_bal(st, "projectx", XN, 2100.0, _d0) == 0.0)
    b = FakePX(accts=[{"id": 301, "name": XN, "balance": 0.0}], realized={301: 0.0})
    st = _new_st(b)
    _cx = dict(CAP, sc=XN, pr=dict(P0, size=150000))
    run_entry(st, b, [("GC", 2)], sc=XN, cap=_cx)
    b.pos.clear()
    b.calls.clear()
    b.accts[0]["balance"] = 2100.0
    run_entry(st, b, [("NQ", 4), ("MNQ", 5)], sc=XN, cap=_cx, asset="NQ")
    _s11 = [(c[1]["contract_id"], c[1]["size"]) for c in b.calls if c[0] == "place_entry"]
    ck("R11b 150K XFA: $0.00으로 시작한 날 2,100이 돼도 그날 한도 3 미니(45 → 30)", _s11 == [("CON.F.US.NQ.Z26", 3)], _s11)

    # ════════ P2-12 거래 기록 조회가 1초 구간 계좌를 붙잡지 않는다 ════════
    class SlowHist(HistPX):
        def trades(self, aid, start_iso, timeout=None):
            self.calls.append(("trades", int(aid), timeout))
            if int(aid) == 101:
                time.sleep(8.0)
                raise RuntimeError("ReadTimeout")
            return []
    _accs = [{"id": 101, "name": "150KTC-V2-A", "balance": 151000.0}, {"id": 102, "name": "150KTC-V2-B", "balance": 151000.0}]
    b = SlowHist(accts=_accs, realized={101: 0.0, 102: 1000.0})
    st = _new_st(b, [("GC", "150KTC-V2-A", P0, "projectx"), ("GC", "150KTC-V2-B", P0, "projectx")])
    b.pos[(102, GC)] = 2
    b.avgs[(102, GC)] = 2650.0
    _legs_note(st, b, "150KTC-V2-B", GC, 2)
    b.marks = {GC: 2668.0}                                  # B: 1,000 + 3,600 = 4,600 = 1초 구간
    for _ in range(3):
        _clk["t"] += 1.0
        scan(st)
    _n12 = sum(1 for c in b.calls if c[0] == "trades" and c[1] == 101)
    for _ in range(700):
        _clk["t"] += 1.0
        scan(st)
    ck("R12a 같은 로그인의 다른 계좌가 1초 구간이면 거래 기록(최대 8초)을 읽지 않는다(10분 재시도 때도)",
       sum(1 for c in b.calls if c[0] == "trades" and c[1] == 101) == _n12, [c for c in b.calls if c[0] == "trades"][:4])

    # ════════ P2-13, P2-14 ════════
    ck("R13a 규모를 모르는 XFA 한도 = 가장 엄한 50K 줄(잔고 4,600 → 50, 종전 150) / 회원이 150K를 고르면 150",
       E._size_limit("projectx", "EXPRESS-V2-1", 4600.0, 0)[0] == 50 and E._size_limit("projectx", "EXPRESS-V2-1", 4600.0, 150000)[0] == 150)
    ck("R13b 권장 1R은 여전히 150K로 본다(대표 결정 G)", E._XFA_DEFAULT_SIZE == 150000)
    _v14 = E._prop_reco_validate({"cap_pct": {"nt8": 0.554, "projectx": 0.559}})
    _v14b = E._prop_reco_validate({"cap_pct": {"nt8": 0.50, "projectx": 0.55}})
    _v14c = E._prop_reco_validate({"cap_pct": {"nt8": 0.499, "projectx": 0.549}})
    ck("R14a 상한 비율은 회사 일관성 선 아래만: Lucid 0.554, 0.50 / Topstep 0.559, 0.55 거절, 0.499 / 0.549 받음",
       _v14 == {} and _v14b == {} and _v14c == {"cap_pct": {"nt8": 0.499, "projectx": 0.549}}
       and len(E._prop_reco_core_bad({"cap_pct": {"nt8": 0.554, "projectx": 0.559}})) == 2, (_v14, _v14b, _v14c))
finally:
    time.sleep, time.time = _real_sleep, _real_time
    E._daycap_day_start = _DAY0
    os.environ.pop("EQ_DAYCAP_SHADOW", None)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
