#!/usr/bin/env python3
# 평가 계좌 익절 3차 마무리(앱 2026.10.14a 그대로, 대표 2026-10-14 결정) 오프라인 시험. 0 = 통과.
#   1 프롭 규칙 서버 값: 일관성 선(요금제별), 상한 요금제, 이름 패턴(안전한 꼴만, 틀린 패턴은 평가 아님 쪽), 계약 한도, 증거금 표, 거래일 시작 -
#     내장값 = 지금 공개 값, 범위, 칸마다 내장값(선택 칸) / 통째로 거절(핵심 칸), 마지막 정상 값 디스크, 바뀔 때 로그 한 줄
#   2 감시 사건 서버 보고: 그림자, 라이브 잠금, 실제 정리, 시험 정리 → 사건 줄(파일, 50개, 7일, 같은 id 한 번) → 생존 핑 'dce' + 'dce:' 답에만 지움,
#     계좌는 끝 4자리만
#   3 시험용 스위치(config.yaml daycap_test, daycap_test_cap): Topstep PRAC, NinjaTrader Sim101만 평가처럼 + 작은 상한, 손으로 연 포지션까지 정리,
#     남은 손절 주문 걷기, 그날 다음 진입 건너뜀, 그림자 모드여도 연습 계좌는 정리, 라이브 잠금은 따름, 다른 계좌는 전혀 바뀌지 않음
#   4 화면 금액: 상태 줄, 최고일 안내 = 감시가 실제로 닫는 통과 익절 금액(2틱 여유 포함), 계좌 한도 로그 괄호 겹침 없음
#   5 여러 계좌 1초 감시: 한 로그인 Combine 8개 중 선에 가까운 계좌는 약 1초마다, 호출 한도 안
#   3차 마무리 리뷰: 서버 값은 공개 값보다 엄한 쪽으로만(1l, 1q~1u), 연습 계좌 건너뜀은 증거 원장 밖(3i, 3l3), 시험 + 그림자 + 잠금 이유(3l2),
#     사건의 계약별 연 시각과 pos_ts(2a, 3h), 1초 구간 계좌가 많아도 먼 계좌 최소 보장(5f)
#   7 NinjaTrader 1초 감시(대표 2026-10-15): NT8 평가, 시험 계좌는 포지션과 상관없이 1초마다(디스크 쓰기 없음), ProjectX는 종전 1/5/30초 그대로,
#     NT8 시간은 같은 회차의 0.5초 몫에서 빼고 굶은 계좌 자리도 쓰지 않는다, 순간 플랫 하나로 앱 기록을 놓지 않는다
# HOME, APPDATA = 임시 폴더, 네트워크 차단, 브로커는 전부 가짜, EQ_NO_ALERT=1.
import datetime as _dt
import inspect
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_dcp3b_")
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
for _n in ("_ui", "_on_main", "_dm_kind"):   # 2026-10-20 화면 줄 대기열(_ui) - 가짜 객체의 __getattr__가 삼키지 않게
    setattr(Stub, _n, eqgui.App.__dict__[_n])


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
    """오늘 거래일에서 거래일 days개 전(주말 건너뜀) 시작 + hour시간(UTC) - 그 거래일 안의 시각.
    2026-10-18: 일요일에 돌리면 오늘 키가 월요일이라(주말 = 월요일 거래일) 달력 하루 전(토요일)이 오늘과 같은 키로 묶였다."""
    _k = _dt.date.fromisoformat(_DAY0()[1])
    for _ in range(days):
        _k -= _dt.timedelta(days=1)
        while _k.weekday() >= 5:
            _k -= _dt.timedelta(days=1)
    _st = _DAY0(_dt.datetime(_k.year, _k.month, _k.day, 12, tzinfo=_dt.timezone.utc))[0]   # 그 키 날 12:00 UTC = 그 거래일 안
    return _st + _dt.timedelta(hours=hour)


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


Stub._dce_note = E.App.__dict__["_dce_note"]
Stub._dce_on = E.App.__dict__["_dce_on"]
Stub._alive_ping = lambda self, *a, **k: self.__dict__.setdefault("pings", []).append(dict(k))
Stub._gate = dict(Stub._gate, dce=True)                 # 감시 사건 보고 스위치(서버가 대표 토큰에만 켠다) - 이 시험은 켠 상태가 기본
_cfg_n = [0]


def _cfg(text):
    """config.yaml을 쓰거나(text) 지운다(None). 숨은 설정 캐시 열쇠(시각, 크기)가 늘 바뀌게 시각을 올린다."""
    if text is None:
        try:
            os.remove(E.CFG_PATH)
        except OSError:
            pass
        return
    with open(E.CFG_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    _cfg_n[0] += 1
    _ns = int(_real_time() * 1e9) + _cfg_n[0] * 1000000
    os.utime(E.CFG_PATH, ns=(_ns, _ns))


def _srv(raw):
    """서버 값을 하트비트로 받은 것처럼(검증 + 적용). 로그는 돌려준 Stub에."""
    st0 = Stub(None)
    E.App._prop_reco_apply(st0, raw)
    return st0


def _dce_clear():
    try:
        os.remove(E._DCE_PATH)
    except OSError:
        pass


PRAC = "PRAC-V2-55555-66666"
K_PRAC = "projectx|" + PRAC.casefold()

RULES_OK = {
    "cons": {"projectx": {"": 0.548}, "nt8": {"D": 0.5, "F": 0.5, "P": 0.4}},
    "cap_plans": {"nt8": ["D", "F"]},
    "names": {"projectx": {"eval": "^(50|100|150)TC", "funded": "(EXPRESS|XFA|FUNDED)", "practice": "^(PRAC|DEMO)"},
              "nt8": {"eval": "^L([DFPX])([IE])(025|050|100|150)"}},
    "limits": {"combine": {"150000": 100, "50000": 40},
               "xfa": {"150000": [[4500, 150], [3000, 100], [2000, 50], [1500, 40], [None, 30]]},
               "xfa_fallback": 50000, "lucid": 80},
    "margin": {"per_micro": {"NQ": 500, "GC": 900}, "micro_risk": {"NQ": 250}, "max_mult": 2.5, "max_micros": 300},
    "day_start": {"hour": 17, "minute": 30}}

try:
    # ════════ 1 프롭 규칙 서버 값 ════════
    E._DAYCAP_SRV = {}
    try:
        os.remove(E._PROP_RECO_PATH)
    except OSError:
        pass
    _b1 = (E._dc_cons("projectx"), E._dc_cons("nt8", "LDI150A"), E._dc_cons("nt8", "LFE150A"), E._dc_cons("nt8", "LPI150A"),
           E._dc_cap_plans(), E._dc_day_hm(), E._dc_day_txt(), E._lim_combine(50000), E._lim_combine(150000), E._lim_lucid(),
           E._lim_xfa_fallback(), E._margin_need("NQ", 2200.0), E._margin_need("GC", 2200.0))
    ck("1a 내장값 = 지금 공개 값(일관성 Topstep 0.55, Lucid Daily/Flex 0.50, Pro 없음, 상한 요금제 D, 거래일 18:00 ET = 17:00 CT, Combine 한도 "
       "50/150, Lucid 100, XFA 모름 = 50K 줄, 증거금 = 마이크로 한 계약의 공개 장중 증거금 NQ $100, GC $200 - 위험 값은 서버 값만)",
       _b1 == (0.55, 0.50, 0.50, 0.0, ("D",), (18, 0), "17:00 CT", 50, 150, 100, 50000, (1, 100.0), (1, 200.0)), _b1)
    _ni = [E._dc_name_info("projectx", n)[:3] for n in ("150KTC-V2-26505604", "50KTC-A", "EXPRESS-V2-1", "PRAC-V2-1", "XFA-150-1",
                                                           "12345", "150KTC-EXPRESS")]
    _ni += [E._dc_name_info("nt8", n)[:3] for n in ("LDI150A1234", "LPE025X", "Sim101", "LXI150A")]
    ck("1b 이름 판별(내장): Combine 규모, 펀디드, 연습, 모르는 이름, 펀디드가 먼저(150KTC-EXPRESS = 펀디드), Lucid 요금제와 규모",
       _ni == [("eval", 150000, ""), ("eval", 50000, ""), ("funded", 0, None), ("practice", 0, None), ("funded", 0, None),
               ("unknown", 0, None), ("funded", 0, None), ("eval", 150000, "D"), ("eval", 25000, "P"), ("unknown", 0, None),
               ("unknown", 0, None)], _ni)
    _vr = (E._prop_reco_validate({"rules": RULES_OK}) or {}).get("rules")
    ck("1c 범위 안 규칙은 그대로 받는다(일관성 선, 상한 요금제, 이름 패턴, 계약 한도, XFA 표, 증거금 표, 거래일 시작) + 핵심 값 이상 없음",
       _vr == {"cons": {"projectx": {"": 0.548}, "nt8": {"D": 0.5, "F": 0.5, "P": 0.4}}, "cap_plans": {"nt8": ["D", "F"]},
               "names": RULES_OK["names"],
               "limits": {"combine": {150000: 100, 50000: 40},
                          "xfa": {150000: ((4500.0, 150), (3000.0, 100), (2000.0, 50), (1500.0, 40), (None, 30))},
                          "xfa_fallback": 50000, "lucid": 80},
               "margin": {"per_micro": {"NQ": 500.0, "GC": 900.0}, "micro_risk": {"NQ": 250.0}, "max_mult": 2.5, "max_micros": 300},
               "day_start": {"hour": 17, "minute": 30}}
       and E._prop_reco_core_bad({"rules": RULES_OK}) == [], (_vr, E._prop_reco_core_bad({"rules": RULES_OK})))
    st1 = _srv({"rules": RULES_OK, "reco_1r": {"projectx": {"eval": {"150000": 1900}, "funded": {"150000": 400}}}})
    _e1 = (E._dc_cons("projectx"), E._dc_pass_need("projectx", 150000, 9000.0, 5480.0),
           E._dc_cons("nt8", "LPI150A1"), E._dc_pass_need("nt8", 150000, 9000.0, 4000.0, "LPI150A1"),
           E._daycap_resolve("nt8", dict(P0, stage="eval"), "LFE150A1")["cap"],
           E._daycap_stage("projectx", P0, "150TC-V3-1"), E._daycap_stage("projectx", P0, "DEMO-1"),
           E._daycap_stage("projectx", P0, "150TC-FUNDED-1"), E._daycap_stage("nt8", dict(P0, stage="eval"), "LXI150A"),
           E._dc_cons("nt8", "LXI150A"))
    ck("1d 받은 규칙이 판단을 바꾼다: Topstep 일관성 0.548(최고일 5,480 → 통과 160,000), LucidPro 0.40(4,000 → 160,000), LucidFlex 상한 4,410, "
       "새 이름 150TC = 평가 150K, DEMO = 연습, FUNDED가 붙으면 펀디드(펀디드가 먼저), 요금제 X = 평가(모르는 요금제 일관성 0.50)",
       _e1 == (0.548, 160000.0, 0.4, 160000.0, 4410.0, ("eval", 150000), ("practice", 0), ("funded", 0), ("eval", 150000), 0.5), _e1)
    _l1 = (E._size_limit("projectx", "150KTC-V2-1")[0], E._size_limit("projectx", "50KTC-1")[0],
           E._size_limit("projectx", "EXPRESS-V2-1", 3200.0)[0], E._size_limit("projectx", "EXPRESS-V2-1", 3200.0, 150000)[0],
           E._size_limit("nt8", "LDI150A1")[0], E._size_limit("tradovate", "LDI150A1")[0],
           E._margin_need("NQ", 2200.0), E._margin_need("GC", 2200.0))
    ck("1e 계약 한도, 증거금 표: Combine 150K 100, 50K 40, XFA 규모 모름 = 50K 줄(잔고 3,200 → 50), 150K 서버 줄(→ 100), Lucid 80, "
       "증거금 NQ 22계약 $11,000, GC는 위험 값이 없어 마이크로 한 계약 $900",
       _l1 == (100, 40, 50, 100, 80, 80, (22, 11000.0), (1, 900.0)), _l1)
    _u1 = _dt.datetime(2026, 10, 14, 21, 0, tzinfo=_dt.timezone.utc)
    _u2 = _dt.datetime(2026, 10, 14, 21, 45, tzinfo=_dt.timezone.utc)
    _ds = (E._dc_day_hm(), E._dc_day_txt(), E._daycap_day_start(_u1), E._daycap_day_start(_u2))
    ck("1f 거래일 시작 17:30 ET(16:30 CT): 17:00 EDT = 전날 17:30 시작, 17:45 EDT = 그날 17:30 시작(다음 거래일 키)",
       _ds == ((17, 30), "16:30 CT", (_dt.datetime(2026, 10, 13, 21, 30, tzinfo=_dt.timezone.utc), "2026-10-14"),
               (_dt.datetime(2026, 10, 14, 21, 30, tzinfo=_dt.timezone.utc), "2026-10-15")), _ds)
    _lg1 = [m for m in st1.logs if "서버 권장 값을 받았습니다" in m]
    ck("1g 바뀔 때 로그 한 줄: 어느 규칙 묶음을 서버 값으로 쓰는지만(패턴, 일관성 숫자는 적지 않는다) + 권장 1R 1,900/400",
       len(_lg1) == 1 and "계좌 규칙 서버 값(일관성 선, 상한 요금제, 계좌 이름 형식, 계약 한도, 증거금 표, 거래일 시작)" in _lg1[0]
       and "^(" not in _lg1[0] and "0.548" not in _lg1[0] and "Topstep 150K 평가 $1,900, 펀디드 $400" in _lg1[0], _lg1)
    _disk = E._load_prop_reco()
    _keep = dict(E._DAYCAP_SRV)
    E._DAYCAP_SRV = {}
    E._prop_reco_boot()
    ck("1h 마지막 정상 규칙은 디스크에 두고 다음 기동에 그대로(XFA 표, 정수 규모 열쇠 포함)",
       _disk == _keep and E._DAYCAP_SRV == _keep and E._dc_day_hm() == (17, 30) and E._lim_combine(150000) == 100, (_disk, _keep))
    _n = len(st1.logs)
    E.App._prop_reco_apply(st1, {"rules": RULES_OK, "reco_1r": {"projectx": {"eval": {"150000": 1900}, "funded": {"150000": 400}}}})
    ck("1i 같은 값이 다시 오면 로그 없음", len(st1.logs) == _n, st1.logs[_n:])
    # 선택 칸의 잘못 = 그 칸만 내장값
    BAD_OPT = {"names": {"projectx": {"eval": ".*", "funded": "(EXPRESS|X)", "practice": "PRAC|DEMO"},
                         "nt8": {"eval": "^(L)(D)(150)|X"}},
               "limits": {"combine": {"150000": 5000, "50000": "abc"},
                          "xfa": {"150000": [[1500, 40], [3000, 100], [None, 30]], "100000": [[4500, 100], [2000, 50]],
                                  "50000": [[2000, 20], [None, 50]]},
                          "lucid": 0},
               "margin": {"per_micro": {"NQ": 10}, "max_mult": 50, "max_micros": 2.5}, "cap_plans": {"nt8": ["DD"]}}
    ck("1j 선택 칸(이름 패턴, 계약 한도, XFA 표, 증거금 표, 상한 요금제)의 잘못은 그 칸만 버린다 - 핵심 값 이상 아님",
       E._prop_reco_validate({"rules": BAD_OPT}) == {} and E._prop_reco_core_bad({"rules": BAD_OPT}) == [],
       (E._prop_reco_validate({"rules": BAD_OPT}), E._prop_reco_core_bad({"rules": BAD_OPT})))
    st2 = _srv({"rules": BAD_OPT, "pass_margin": 70})
    ck("1k 그 서버 값은 받되(여유 $70) 버린 칸은 내장값 + 안내 한 번 - 이름 판별, 한도, 증거금은 내장 그대로",
       E._DAYCAP_SRV == {"pass_margin": 70.0} and E._dc_margin() == 70.0 and E._lim_combine(150000) == 150
       and E._daycap_stage("projectx", P0, "150TC-V3-1") == ("unknown", 0) and E._margin_need("NQ", 2200.0) == (1, 100.0)
       and sum(1 for m in st2.logs if "범위를 벗어나거나 읽을 수 없어 쓰지 않습니다. 그 칸은 앱에 들어 있는 값이 있으면 그 값을 쓰고" in m) == 1, (E._DAYCAP_SRV, st2.logs))
    # 핵심 값의 잘못 = 통째로 거절, 마지막 정상 값
    _srv({"rules": RULES_OK})
    _good = dict(E._DAYCAP_SRV)
    _cores = ({"cons": {"projectx": {"": 0.62}}}, {"cons": {"projectx": {"": 0}}}, {"cons": {"nt8": {"D": 0.2}}},
              {"cons": {"projectx": {"": 0.54}}}, {"cons": {"nt8": {"D": 0.49}}}, {"day_start": {"hour": 21}},
              {"day_start": "18:00"}, {"day_start": {"hour": 18, "minute": 75}},
              {"cons": {"projectx": {"": 0.56}}}, {"cons": {"nt8": {"D": 0.55}}}, {"cons": {"nt8": {"D": 0}}}, {"cons": {"nt8": {"X": 0.52}}},
              {"cons": {"nt8": {"X": 0}}}, {"day_start": {"hour": 16}}, {"day_start": {"hour": 18, "minute": 30}}, {"day_start": {"hour": 19}})
    _cb = [E._prop_reco_core_bad({"rules": c}) for c in _cores]
    ck("1l 핵심 값 범위 밖: Topstep 일관성 0.62, 0(Lucid만 0 가능), Lucid 0.2, 내장 상한 비율 0.54와 같은 일관성 0.54, Lucid Daily 0.49(내장 상한 비율 0.49와 같음), "
       "거래일 21시, 글자, 75분 + (3차 마무리 리뷰) 공개 선보다 높은 일관성(Topstep 0.56, Daily 0.55), 상한 요금제에 0, 모르는 요금제 0.52와 0, "
       "거래일 16:00, 18:30, 19:00 = 모두 핵심 이상", all(_cb), _cb)
    st3 = Stub(None)
    for c in _cores:
        E.App._prop_reco_apply(st3, dict(c and {"rules": c}, pass_margin=90))
    ck("1m 핵심 이상이 있는 서버 값은 통째로 쓰지 않고 마지막 정상 값 그대로(여유 $90도 쓰지 않음) + 서버 값마다 로그 한 번",
       E._DAYCAP_SRV == _good and E._dc_margin() == 50.0 and E._dc_day_hm() == (17, 30)
       and sum(1 for m in st3.logs if "허용 범위를 벗어나" in m and "마지막으로 받은 정상 값을 그대로" in m) == len(_cores),
       (E._DAYCAP_SRV == _good, st3.logs))
    ck("1n 서버가 일관성 선을 낮추면서 상한 비율도 같이 낮추면 받는다(Topstep 0.53 + 상한 0.526)",
       E._prop_reco_core_bad({"cap_pct": {"projectx": 0.526}, "rules": {"cons": {"projectx": {"": 0.53}}}}) == [], "")
    # 이름 패턴: 안전한 꼴만, 틀린 패턴이 펀디드나 모르는 계좌를 평가로 만들지 못한다
    _pats = [("projectx", "eval", p) for p in (".*", "^(50|100|150)", "(50|100|150)KTC", "^(50|100|150)KTC|^A", "^[A-Z](50)KTC",
                                               "^(50|100|150)K", "^(5|1)PR", "^(12)345", "^S(im)1", "^(50|100|150)KTC\\d",
                                               "^(50|100|150)KTC{2}", "^(50|100|150)KT+", "^(EXPRESS|50)KT", "^(1)" + "A" * 70)]
    _pats += [("projectx", "funded", p) for p in ("KTC", "(EXPRESS|X)", "(EX|PR|SIM)", "1")]
    _pats += [("nt8", "eval", p) for p in ("^L(D)(I)", "L([DFP])([IE])(150)", "^S([I])(m)(101)", "^L(DF)([IE])(150)",
                                           "^L([DF])([IE])(1A0)")]
    _acc = [x for x in _pats if E._srv_pat_ok(*x)]
    ck("1o 받지 않는 패턴: 와일드카드, 글자 없음, ^ 없음, 맨 위 |, 범위, 짧은 이름, 이스케이프, 반복, 규모 묶음에 글자, 64자 넘음, "
       "평가 이름에 맞는 펀디드 패턴, 한 글자 갈래, 숫자만, 묶음 수가 다른 Lucid, ^ 없는 Lucid, Sim101에 맞는 Lucid, 요금제 묶음이 두 글자, "
       "Lucid 규모 묶음에 글자", not _acc, _acc)
    E._DAYCAP_SRV = {"rules": {"names": {"projectx": {"eval": "^(150)KT"}}}}      # 검증을 지난 넓은 평가 패턴이라도
    _fs = (E._daycap_stage("projectx", P0, "150KT-EXPRESS-1"), E._daycap_stage("projectx", P0, "150KT-XFA"),
           E._daycap_stage("projectx", P0, "PRAC-150KT"), E._daycap_stage("projectx", P0, "ABC-12345"))
    ck("1p 판별은 내장 펀디드, 연습 패턴을 먼저 본다: 넓은 서버 평가 패턴도 EXPRESS, XFA, PRAC 이름과 모르는 이름을 평가로 만들지 못한다",
       _fs == (("funded", 0), ("funded", 0), ("practice", 0), ("unknown", 0)), _fs)
    E._DAYCAP_SRV = {}
    # ── 3차 마무리 리뷰: 서버 값은 공개 값보다 엄한 쪽으로만 ──
    _okc = [E._prop_reco_core_bad({"rules": c}) for c in ({"cons": {"projectx": {"": 0.55}}}, {"cons": {"projectx": {"": 0.545}}},
                                                          {"cons": {"nt8": {"P": 0}}}, {"cons": {"nt8": {"P": 0.4}}},
                                                          {"cons": {"nt8": {"X": 0.48}}}, {"day_start": {"hour": 17, "minute": 0}},
                                                          {"day_start": {"hour": 18, "minute": 0}}, {"day_start": {"hour": 17, "minute": 59}})]
    ck("1q 엄한 쪽 핵심 값은 받는다: Topstep 0.55, 0.545, LucidPro 0과 0.40, 모르는 요금제 0.48, 거래일 17:00, 18:00, 17:59",
       not any(_okc), _okc)
    _lim = (E._prop_reco_validate({"rules": {"limits": {"combine": {"50000": 300, "100000": 49, "150000": 150}, "lucid": 300}}}),
            E._prop_reco_validate({"rules": {"limits": {"combine": {"50000": 25}, "lucid": 50}}}),
            E._prop_reco_validate({"rules": {"limits": {"xfa": {"50000": [[None, 200], ], "100000": [[4500, 100], [None, 30]],
                                                                 "150000": [[4500, 150], [3000, 150], [None, 30]]}}}}),
            E._prop_reco_validate({"rules": {"limits": {"xfa_fallback": 150000}}}),
            E._prop_reco_validate({"rules": {"limits": {"xfa": {"150000": [[4500, 50], [2000, 30], [None, 20]]}, "xfa_fallback": 150000}}}),
            E._prop_reco_core_bad({"rules": {"limits": {"combine": {"50000": 300}, "lucid": 300}}}))
    ck("1r 계약 한도는 공개 한도보다 크게 받지 않는다(그 칸만 버림, 핵심 이상 아님): Combine 50K 300, 100K 49(절반 아래), Lucid 300 = 버림, "
       "150K 150 = 받음, 50K 25, Lucid 50 = 받음, XFA 50K 한 줄 200, 150K 3,000 줄 150 = 버림, 100K 엄한 표 = 받음, 규모 모를 때 150K 표 = 버림, "
       "서버가 150K 표를 50K보다 엄하게 하면 150K = 받음",
       _lim[0] == {"rules": {"limits": {"combine": {150000: 150}}}}
       and _lim[1] == {"rules": {"limits": {"combine": {50000: 25}, "lucid": 50}}}
       and _lim[2] == {"rules": {"limits": {"xfa": {100000: ((4500.0, 100), (None, 30))}}}} and _lim[3] == {}
       and _lim[4] == {"rules": {"limits": {"xfa": {150000: ((4500.0, 50), (2000.0, 30), (None, 20))}, "xfa_fallback": 150000}}}
       and _lim[5] == [], _lim)
    _mg = E._prop_reco_validate({"rules": {"margin": {"per_micro": {"NQ": 99, "GC": 3201}, "micro_risk": {"NQ": 24, "GC": 1001},
                                                      "max_mult": 2.2, "max_micros": 299}}})
    _mg2 = E._prop_reco_validate({"rules": {"margin": {"per_micro": {"NQ": 100, "GC": 3200}, "micro_risk": {"NQ": 25, "GC": 1000},
                                                       "max_mult": 2.25, "max_micros": 300}}})
    ck("1s 증거금 표(경고만)는 경고가 줄어드는 쪽으로 옮기지 못한다: 마이크로당 증거금은 공개 장중 값(NQ $100, GC $200)부터 16배까지, "
       "마이크로 한 계약 위험 $25~$1,000, 배수 내장 3의 0.75배 이상, 최대 400의 0.75배 이상 - 그 밖은 칸만 버림",
       _mg == {} and _mg2 == {"rules": {"margin": {"per_micro": {"NQ": 100.0, "GC": 3200.0}, "micro_risk": {"NQ": 25.0, "GC": 1000.0},
                                                    "max_mult": 2.25, "max_micros": 300}}}
       and E._prop_reco_core_bad({"rules": {"margin": {"per_micro": {"NQ": 99}}}}) == [], (_mg, _mg2))
    _srv({"rules": {"cap_plans": {"nt8": ["F"]}}})
    _cp = (E._dc_cap_plans(), E._daycap_resolve("nt8", dict(P0, stage="eval"), "LDI150A1234")["cap"],
           E._daycap_resolve("nt8", dict(P0, stage="eval"), "LFE150A1234")["cap"],
           E._prop_reco_core_bad({"rules": {"cap_plans": {"nt8": ["F"]}, "cons": {"nt8": {"D": 0.49}}}}))
    ck("1t 서버 상한 요금제는 더하기만: ['F']를 받아도 LucidDaily 상한 $4,410 그대로 + Flex도 $4,410, 상한 요금제 D의 일관성 0.49(상한 비율과 같음)는 핵심 이상",
       _cp[:3] == (("D", "F"), 4410.0, 4410.0) and _cp[3], _cp)
    E._DAYCAP_SRV = {}
    # 1u 서버 펀디드 패턴은 계약 한도를 넓히지 않는다(12e 대조 8a, 8b와 같은 비대상 계좌 + 모든 한도를 넘는 계획, 서버 규칙을 받은 채로)
    _RULES_FUND = {"names": {"projectx": {"funded": "(EXPRESS|XFA|FUNDED)", "practice": "^(PRAC|DEMO)"}}}

    def _entry_sizes(nm):
        _reset_state()
        _b = FakePX(accts=[{"id": 101, "name": nm, "balance": 150000.0}], realized={101: 0.0})
        _s = Stub(_b)
        run_entry(_s, _b, [("GC", 12), ("MGC", 3)], sc=nm, cap=dict(CAP, sc=nm, lbl="L"))
        return [(c[1]["contract_id"].split(".")[3], c[1]["size"]) for c in _b.calls if c[0] == "place_entry"], _s.logs
    _names = ("TDY-FUNDED-150K-01", "DEMO-12345", "12345", "EXPRESS-V2-1")
    _base = {n: _entry_sizes(n)[0] for n in _names}
    _srv({"rules": _RULES_FUND})
    _withr = {n: _entry_sizes(n) for n in _names}
    E._DAYCAP_SRV = {}
    ck("1u 서버 펀디드, 연습 패턴을 받아도 다른 회사 펀디드 이름, DEMO, 모르는 이름의 진입은 서버 규칙 없을 때와 같다(GC 12 + MGC 3), "
       "Topstep EXPRESS는 내장 표 그대로",
       all(_withr[n][0] == _base[n] for n in _names) and _base["TDY-FUNDED-150K-01"] == [("GC", 12), ("MGC", 3)]
       and not any("계좌 한도" in m for n in _names[:3] for m in _withr[n][1]),
       ({n: _withr[n][0] for n in _names}, _base))

    # ════════ 3 시험용 스위치 ════════
    _cfg(None)
    _t0 = (E._daycap_test_on(), E._daycap_stage("projectx", P0, PRAC), E._daycap_stage("nt8", P0, "Sim101"))
    ck("3a 기본 꺼짐: 연습 계좌는 연습, Sim101은 모르는 이름(평가 계좌 익절 없음)",
       _t0 == (False, ("practice", 0), ("unknown", 0)), _t0)
    _cfg("lang: ko\ndaycap_test: true\n")
    _r3 = E._daycap_resolve("projectx", P0, PRAC, 50000.0)
    _t1 = (E._daycap_test_on(), E._daycap_test_cap(), E._daycap_stage("projectx", P0, PRAC), E._daycap_stage("nt8", P0, "Sim101"),
           E._daycap_stage("nt8", P0, "sim101"), (_r3 or {}).get("cap"), (_r3 or {}).get("test"), (_r3 or {}).get("pass_tp"))
    ck("3b 켜면: PRAC, Sim101(대소문자 무관) = 평가, 상한 $50, 통과 익절 없음", _t1 == (True, 50.0, ("eval", 0), ("eval", 0), ("eval", 0),
                                                                       50.0, True, 0.0), _t1)
    _t2 = (E._daycap_stage("projectx", P0, SC), E._daycap_resolve("projectx", P0, SC, 151000.0)["cap"],
           E._daycap_stage("projectx", P0, "EXPRESS-V2-1"), E._daycap_stage("projectx", P0, "PRAC-EXPRESS-1"),
           E._daycap_stage("projectx", P0, "12345"), E._daycap_stage("nt8", P0, "Sim102"), E._daycap_stage("nt8", P0, "Sim101x"),
           E._daycap_stage("nt8", P0, LU), E._daycap_stage("tradovate", P0, "Sim101"), E._daycap_stage("ibkr", P0, PRAC),
           E._daycap_test_acct("tradovate", "Sim101"), E._daycap_test_acct("projectx", "Sim101"), E._daycap_test_acct("nt8", PRAC))
    ck("3c 시험 모드도 연습 계좌 밖은 그대로: Combine 상한 4,860, 펀디드, PRAC가 붙은 EXPRESS = 펀디드, 모르는 이름, Sim102, Lucid 미답, "
       "다른 브로커의 Sim101/PRAC",
       _t2 == (("eval", 150000), 4860.0, ("funded", 0), ("funded", 0), ("unknown", 0), ("unknown", 0), ("unknown", 0),
               ("unanswered", 150000), ("none", 0), ("none", 0), False, False, False), _t2)
    _caps = []
    for _v in ("80", "5", "900", "abc", "true", "250.5"):
        _cfg(f"lang: ko\ndaycap_test: true\ndaycap_test_cap: {_v}\n")
        _caps.append(E._daycap_test_cap())
    ck("3d daycap_test_cap: 80 → $80, 10~500 밖(5, 900), 글자, 참거짓 = $50, 250.5 그대로", _caps == [80.0, 50.0, 50.0, 50.0, 50.0, 250.5], _caps)
    _cfg("lang: ko\ndaycap_test: true\n")
    E._DAYCAP_SRV = {"rules": {"names": {"projectx": {"practice": "^(PRAC|DEMO)"}}}}
    ck("3e 시험 대상은 앱 내장 연습 패턴만(서버가 더한 DEMO 연습 이름은 시험 대상 아님)",
       E._daycap_stage("projectx", P0, "DEMO-1") == ("practice", 0) and E._daycap_stage("projectx", P0, PRAC) == ("eval", 0))
    E._DAYCAP_SRV = {}
    # 3f 연습 계좌에 손으로 연 MNQ 1 + 손으로 건 손절 → +$50에서 정리 + 손절 걷기 + 그날 다음 진입 건너뜀 + 사건 'test'
    _dce_clear()
    b = MarkPX(accts=[{"id": 301, "name": PRAC, "balance": 50000.0}], realized={301: 0.0})
    st = _new_st(b, [("NQ", PRAC, P0, "projectx")])
    b.pos[(301, MNQ)] = 1
    b.avgs[(301, MNQ)] = 25000.0
    b.orders.append({"id": 9001, "aid": 301, "type": 4, "side": 1, "size": 1, "contractId": MNQ, "stopPrice": 24900.0})
    b.marks = {MNQ: 25010.0}                           # +$20
    _due_now(st)
    scan(st)
    _c0 = list(_cl(b))
    b.marks = {MNQ: 25026.0}                           # +$52 ≥ $50
    for _ in range(3):
        _clk["t"] += 2
        _due_now(st)
        scan(st)
    _rt = E.App._daycap_row_text(st, "NQ", st._acfg["NQ"]["accounts"][0])
    ck("3f 시험 모드: 연습 계좌의 손으로 연 MNQ 1을 +$20에선 두고 +$52에서 시장가 정리 + 손으로 건 손절 걷기 + 오늘 닿음 표시",
       not _c0 and [c[1:] for c in _cl(b)] == [(301, MNQ)] and [c[2] for c in _cancels(b)] == [9001] and not b.orders
       and E.App._daycap_hit_today(st, "projectx", PRAC, NOW_DAY()), (b.calls[-8:], st.logs[-4:]))
    ck("3g 로그와 상태 줄이 시험 모드라고 말한다(KO) + 증거 원장 exit_ok는 보내지 않음",
       any("[시험 모드]" in m and "하루 이익 상한($50)" in m and "손으로 연 포지션도 정리하고 남은 손절 주문을 걷습니다" in m for m in st.logs)
       and any("[시험 모드] 하루 이익 상한 익절을 마쳤습니다" in m for m in st.logs)
       and _rt.startswith("시험 모드: 시험 상한($50)에 닿아") and not [e for e in st.evs if e[0] == "exit_ok"], (st.logs[-5:], _rt, st.evs))
    _q = E._dce_pending()
    ck("3h 서버 보고 사건 하나: mode test, 단계 test, 끝 4자리만, 계약별 수량과 평균가, 선 $50, 오늘 손익 $52 + 손으로 연 MNQ를 감시가 처음 본 "
       "시각(계약별 o, 계좌 pos_ts - 1분봉 다시 셈의 창 시작, 3차 마무리 리뷰)",
       len(_q) == 1 and _q[0]["mode"] == "test" and _q[0]["stage"] == "test" and _q[0]["a4"] == "6666" and _q[0]["kind"] == "cap"
       and _q[0]["line"] == 50.0 and _q[0]["val"] == 52.0
       and [{k: v for k, v in x.items() if k != "o"} for x in _q[0]["pos"]] == [{"c": MNQ, "q": 1, "avg": 25000.0, "pv": 2.0}]
       and _q[0]["pos"][0]["o"] and _q[0]["pos"][0]["o"] == _q[0]["pos_ts"] and _q[0]["pos_ts"] < _q[0]["ts"]
       and PRAC not in str(_q) and st.__dict__.get("pings"), _q)
    _n0 = len([c for c in b.calls if c[0] == "place_entry"])
    run_entry(st, b, [("MNQ", 2)], stop=24900.0, sc=PRAC, asset="NQ", cap=dict(CAP, sc=PRAC, lbl="PRAC"))
    ck("3i 그날 다음 진입은 건너뜀(day_cap) - 실제 상한과 같음. 연습 계좌라 서버 증거 원장 건너뜀 사건은 보내지 않음(3차 마무리 리뷰)",
       len([c for c in b.calls if c[0] == "place_entry"]) == _n0 and not st.skips
       and any("더 진입하지 않습니다" in m for m in st.logs[-3:]), (st.skips, st.logs[-2:]))
    st.lang = "en"
    _rte = E.App._daycap_row_text(st, "NQ", st._acfg["NQ"]["accounts"][0])
    st.lang = "ko"
    ck("3j 상태 줄 EN 짝", _rte.startswith("Test mode: the test cap ($50) was reached"), _rte)
    # 3k 그림자 모드가 켜져 있어도 연습 계좌는 실제로 정리, 같은 로그인의 Combine(앱 진입)은 그림자 그대로
    os.environ["EQ_DAYCAP_SHADOW"] = "1"
    _dce_clear()
    try:
        b = MarkPX(accts=[{"id": 101, "name": SC, "balance": 150000.0}, {"id": 301, "name": PRAC, "balance": 50000.0}],
                   realized={101: 0.0, 301: 0.0})
        st = _new_st(b, [("GC", SC, P0, "projectx"), ("NQ", PRAC, P0, "projectx")])
        run_entry(st, b, [("GC", 2)])
        b.pos[(301, MNQ)] = 1
        b.avgs[(301, MNQ)] = 25000.0
        b.marks = {GC: 2675.0, MNQ: 25030.0}
        for _ in range(3):
            _clk["t"] += 2
            _due_now(st)
            scan(st)
        _md = sorted(e["mode"] for e in E._dce_pending())
        ck("3k 그림자 모드 + 시험 모드: 연습 계좌만 실제 정리(MNQ), Combine GC는 '정리했을 경우' 로그만 + 사건 shadow, test",
           [c[1:] for c in _cl(b)] == [(301, MNQ)] and b.pos.get((101, GC)) == 2
           and any("정리했을 경우" in m and "그림자 모드라" in m for m in st.logs) and _md == ["shadow", "test"], (_cl(b), _md, st.logs[-4:]))
    finally:
        os.environ.pop("EQ_DAYCAP_SHADOW", None)
    # 3l 라이브 잠금은 따른다(연습 계좌도 정리하지 않음) + 사건 lock
    _dce_clear()
    b = MarkPX(accts=[{"id": 301, "name": PRAC, "balance": 50000.0}], realized={301: 0.0})
    st = _new_st(b, [("NQ", PRAC, P0, "projectx")])
    st._gate = {"ok": True, "enabled": True, "force_dry_run": True, "caps": {"use": True, "autoentry": True}, "dce": True}
    b.pos[(301, MNQ)] = 1
    b.avgs[(301, MNQ)] = 25000.0
    b.marks = {MNQ: 25030.0}
    for _ in range(2):
        _clk["t"] += 2
        _due_now(st)
        scan(st)
    ck("3l 어드민 라이브 잠금이면 연습 계좌도 정리하지 않는다('정리했을 경우 ... 라이브 잠금 중이라') + 사건 lock 하나",
       not _cl(b) and any("정리했을 경우" in m and "라이브 잠금 중이라" in m for m in st.logs)
       and [e["mode"] for e in E._dce_pending()] == ["lock"], (b.calls[-4:], st.logs[-3:]))
    # 3l2 그림자 모드 + 시험 모드 + 라이브 잠금: 연습 계좌를 막은 것은 라이브 잠금(시험 모드가 그림자 모드보다 우선) - 로그 이유가 같은 규칙을 따른다
    _cfg("lang: ko\ndaycap_test: true\ndaycap_shadow: true\n")
    _dce_clear()
    b = MarkPX(accts=[{"id": 301, "name": PRAC, "balance": 50000.0}], realized={301: 0.0})
    st = _new_st(b, [("NQ", PRAC, P0, "projectx")])
    st._gate = {"ok": True, "enabled": True, "force_dry_run": True, "caps": {"use": True, "autoentry": True}, "dce": True}
    b.pos[(301, MNQ)] = 1
    b.avgs[(301, MNQ)] = 25000.0
    b.marks = {MNQ: 25030.0}
    for _ in range(2):
        _clk["t"] += 2
        _due_now(st)
        scan(st)
    _l2 = [m for m in st.logs if "정리했을 경우" in m]
    ck("3l2 그림자 + 시험 + 라이브 잠금: '[시험 모드] 정리했을 경우 ... 이 계좌 포지션 ... 라이브 잠금 중이라'(그림자 모드라고 하지 않음) + 사건 lock",
       len(_l2) == 1 and "[시험 모드] 정리했을 경우" in _l2[0] and "라이브 잠금 중이라" in _l2[0] and "그림자 모드라" not in _l2[0]
       and "이 계좌 포지션" in _l2[0] and not _cl(b) and [e["mode"] for e in E._dce_pending()] == ["lock"], (_l2, b.calls[-3:]))
    _cfg("lang: ko\ndaycap_test: true\n")
    # 3l3 시험 모드: 연습 계좌의 cap_room 건너뜀은 증거 원장으로 보내지 않고, 실제 Combine의 cap_room은 그대로 보낸다
    _reset_state()
    b = FakePX(accts=[{"id": 202, "name": "PRAC-V2-1", "balance": 50000.0}], realized={202: 0.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2)], sc="PRAC-V2-1", cap=dict(CAP, sc="PRAC-V2-1", lbl="PRAC", risk=2000.0))
    _s1 = (list(st.skips), [c for c in b.calls if c[0] == "place_entry"], any("cap_room" in m or "절반보다 작아" in m for m in st.logs))
    _reset_state()
    b2 = FakePX(accts=[{"id": 101, "name": SC, "balance": 154500.0}], realized={101: 4500.0})
    st2 = Stub(b2)
    run_entry(st2, b2, [("GC", 2)])
    ck("3l3 시험 모드: 연습 계좌 신호는 $50 상한 때문에 건너뛰되(cap_room) 증거 원장 사건은 없음, 같은 때 실제 Combine의 cap_room은 사건을 보낸다",
       _s1 == ([], [], True) and st2.skips == [("GC", "cap_room")] and not [c for c in b2.calls if c[0] == "place_entry"],
       (_s1, st2.skips, st2.logs[-2:]))
    # 3l4 시험 모드의 진입 건너뜀 로그 두 줄(cap_room, day_cap)에도 [시험 모드] / [Test mode] 표시 - 실제 Combine 줄에는 없다
    _rl, _rl2 = [m for m in st.logs if "절반보다 작아" in m], [m for m in st2.logs if "절반보다 작아" in m]

    def _prac_skip_logs(real, lang):
        _reset_state()
        _b = FakePX(accts=[{"id": 202, "name": "PRAC-V2-1", "balance": 50000.0}], realized={202: real})
        _s = Stub(_b)
        _s.lang = lang
        run_entry(_s, _b, [("GC", 2)], sc="PRAC-V2-1", cap=dict(CAP, sc="PRAC-V2-1", lbl="PRAC", risk=2000.0))
        return _s.logs
    _dl, _dle, _rle = _prac_skip_logs(60.0, "ko"), _prac_skip_logs(60.0, "en"), _prac_skip_logs(0.0, "en")
    _dl = [m for m in _dl if "더 진입하지 않습니다" in m]
    _dle = [m for m in _dle if "takes no more entries today" in m]
    _rle = [m for m in _rle if "less than half of" in m]
    ck("3l4 시험 모드 건너뜀 로그: cap_room KO/EN, day_cap KO/EN 모두 [시험 모드] / [Test mode] 표시, 같은 때 실제 Combine의 cap_room 줄에는 없음",
       len(_rl) == 1 and "[PRAC] [시험 모드] 오늘 남은 하루 이익 한도" in _rl[0]
       and len(_rle) == 1 and "[PRAC] [Test mode] Today's remaining daily profit room" in _rle[0]
       and len(_dl) == 1 and "[PRAC] [시험 모드] 오늘 하루 이익 상한($50)에 닿아" in _dl[0]
       and len(_dle) == 1 and "[PRAC] [Test mode] This account reached its daily profit cap ($50)" in _dle[0]
       and len(_rl2) == 1 and "[시험 모드]" not in _rl2[0] and "[Test mode]" not in _rl2[0], (_rl, _rle, _dl, _dle, _rl2))
    # 3m NinjaTrader Sim101: 손으로 연 MNQ 1 → +$52에서 정리
    _dce_clear()
    n8 = BridgeNT8(acct="Sim101")
    n8.cash, n8.nl = 50000.0, 50020.0
    n8.pos = {"MNQ 12-26": 1}
    st = _new_st(n8, [("NQ", "Sim101", P0, "nt8")])
    _due_now(st)
    scan(st)
    _x0 = list(n8.closed)
    n8.nl = 50052.0
    for _ in range(3):
        _clk["t"] += 2
        _due_now(st)
        scan(st)
    ck("3m NinjaTrader Sim101: +$20 그대로, +$52에서 MNQ 정리 + 오늘 닿음 + 사건 test(끝 4자리 m101)",
       not _x0 and n8.closed == ["MNQ 12-26"] and E.App._daycap_hit_today(st, "nt8", "Sim101", NOW_DAY())
       and [(e["mode"], e["a4"], e["bk"]) for e in E._dce_pending()] == [("test", "m101", "nt8")], (n8.closed, st.logs[-4:]))
    # 3n 시험 모드여도 연습 계좌가 아닌 Combine의 손으로 연 포지션은 정리하지 않는다(수동 우선 그대로)
    b = MarkPX(realized={101: 3000.0})
    b.accts[0]["balance"] = 153000.0
    st = _new_st(b, [("NQ", SC, P0, "projectx")])
    b.pos[(101, MNQ)] = 20
    b.avgs[(101, MNQ)] = 25000.0
    b.marks = {MNQ: 25050.0}                            # 3,000 + 2,000 ≥ 4,860
    for _ in range(2):
        _clk["t"] += 2
        _due_now(st)
        scan(st)
    ck("3n 시험 모드에서도 Combine의 손으로 연 MNQ 20은 정리하지 않음(앱 진입 없음) - 오늘 닿음 표시만",
       not _cl(b) and E.App._daycap_hit_today(st, "projectx", SC, NOW_DAY()), (b.calls[-4:], st.logs[-2:]))
    # 3o [라이브 시작] 안내(KO/EN), 설정 저장이 숨은 키를 지우지 않음
    st = Stub(None)
    _cfg("lang: ko\ndaycap_test: true\ndaycap_test_cap: 900\n")
    E.App._daycap_mode_notice(st)
    st.lang = "en"
    E.App._daycap_mode_notice(st)
    ck("3o [라이브 시작] 안내: 시험 모드(연습 계좌만, $50, 손으로 연 포지션까지, 그림자 모드여도 정리, 다른 계좌 안 걸림) + 10~500 밖 안내, EN 짝",
       any("평가 계좌 익절 시험 모드가 켜져 있습니다(daycap_test)" in m and "$50" in m and "다른 계좌에는 걸리지 않습니다" in m
           and "10~500 밖이라 $50을 씁니다" in m for m in st.logs)
       and any("Evaluation take-profit test mode is on (daycap_test)" in m and "outside 10 to 500" in m for m in st.logs), st.logs)
    _cfg("lang: ko\ndaycap_test: true\ndaycap_test_cap: 80\ndaycap_shadow: true\n")
    E._save_full("ko", "tok-x", {})
    import yaml as _y
    with open(E.CFG_PATH, encoding="utf-8") as f:
        _saved = _y.safe_load(f) or {}
    ck("3p 앱이 config.yaml을 다시 저장해도 daycap_test, daycap_test_cap, daycap_shadow는 남는다",
       _saved.get("daycap_test") is True and _saved.get("daycap_test_cap") == 80 and _saved.get("daycap_shadow") is True
       and _saved.get("token") == "tok-x", _saved)
    _cfg(None)
    ck("3q 설정을 지우면 다시 꺼짐(연습 계좌는 연습)", not E._daycap_test_on() and E._daycap_stage("projectx", P0, PRAC) == ("practice", 0))

    # ════════ 2 감시 사건 서버 보고 ════════
    import json as _json
    _dce_clear()
    os.environ["EQ_DAYCAP_SHADOW"] = "1"
    try:
        b = MarkPX(realized={101: 0.0})
        st = _new_st(b, [("GC", SC, P0, "projectx")])
        run_entry(st, b, [("GC", 2)])
        b.marks = {GC: 2675.0}                          # 오늘 5,000 ≥ 4,860
        for _ in range(3):
            _clk["t"] += 2
            _due_now(st)
            scan(st)
        _q = E._dce_pending()
    finally:
        os.environ.pop("EQ_DAYCAP_SHADOW", None)
    _keys = {"id", "ts", "t0", "day", "bk", "firm", "stage", "plan", "size", "kind", "line", "val", "real", "bal", "pos", "open_ts",
             "pos_ts", "mode", "a4"}
    ck("2a 그림자 모드: 사건 하나(shadow, cap, Topstep 150K 평가, 선 $4,860, 오늘 손익 $5,000, 실현 $0, 잔고, GC 2 평균가 2,650 포인트 $100, "
       "앱 진입 시각, 끝 4자리) + 핑 바로 한 번",
       len(_q) == 1 and set(_q[0]) == _keys and _q[0]["mode"] == "shadow" and _q[0]["kind"] == "cap" and _q[0]["bk"] == "projectx"
       and _q[0]["firm"] == "Topstep" and _q[0]["stage"] == "eval" and _q[0]["size"] == 150000 and _q[0]["line"] == 4860.0
       and _q[0]["val"] == 5000.0 and _q[0]["real"] == 0.0 and _q[0]["bal"] == 151000.0
       and [{k: v for k, v in x.items() if k != "o"} for x in _q[0]["pos"]] == [{"c": GC, "q": 2, "avg": 2650.0, "pv": 100.0}]
       and _q[0]["open_ts"] and _q[0]["a4"] == "V2-1"
       and _q[0]["open_ts"] <= _q[0]["pos"][0]["o"] <= _q[0]["ts"] and _q[0]["pos_ts"] == _q[0]["pos"][0]["o"]
       and _q[0]["day"] == NOW_DAY() and len(_q[0]["id"]) == 16 and len(st.__dict__.get("pings") or []) == 1, (_q, st.__dict__.get("pings")))
    ck("2b 계좌 이름 전체, 키, 비밀번호, PIN 칸은 사건에 없다(끝 4자리만)",
       SC not in _json.dumps(_q) and not any(k in _json.dumps(_q).lower() for k in ('"pin"', "api", "secret", "password", "token")), _q)
    _ok_dup = E._dce_add(dict(_q[0]))
    ck("2c 같은 사건(같은 계좌, 거래일, 선, 종류, 방식)은 한 번만 - 재시작 뒤 다시 '정리했을 경우'가 나와도", _ok_dup is False
       and len(E._dce_load()) == 1)
    # 실제 정리 = real
    _dce_clear()
    b = MarkPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2675.0}
    for _ in range(3):
        _clk["t"] += 2
        _due_now(st)
        scan(st)
    _q = E._dce_pending()
    ck("2d 실제 정리 = 사건 real 하나(정리 주문을 낸 뒤 쌓는다)", [c[2] for c in _cl(b)] == [GC] and [e["mode"] for e in _q] == ["real"]
       and _q[0]["val"] == 5000.0, (_cl(b), _q))
    # 줄 50개, 7일
    _dce_clear()
    for i in range(60):
        E._dce_add({"id": f"ev{i:02d}", "ts": 1.0, "kind": "cap"})
    _ld = E._dce_load()
    with open(E._DCE_PATH, encoding="utf-8") as f:
        _raw_q = _json.load(f)
    _raw_q[0]["q_ts"] = _real_time() - 8 * 86400       # 가짜 시계(_clk)는 실제 시각 근처 - 8일 전으로
    _raw_q[0]["q_ts"] = _clk["t"] - 8 * 86400
    with open(E._DCE_PATH, "w", encoding="utf-8") as f:
        _json.dump(_raw_q, f)
    ck("2e 줄은 50개(오래된 것부터 버림), 7일 넘은 사건은 버림, 앱을 다시 켜도 파일에 남는다",
       len(_ld) == 50 and _ld[0]["id"] == "ev10" and _ld[-1]["id"] == "ev59" and len(E._dce_load()) == 49
       and E._dce_load()[0]["id"] == "ev11", (len(_ld), _ld[0], len(E._dce_load())))
    # 생존 핑: 'dce' 10개, 'dce:' 답에만 지움, 종료 핑은 안 실음
    import requests as _rqm
    from unittest.mock import MagicMock

    class _SyncT:
        def __init__(self, target=None, daemon=None, args=(), **k):
            self.t, self.a = target, args

        def start(self):
            self.t(*self.a)
    _dce_clear()
    for i in range(12):
        E._dce_add({"id": f"ev{i:02d}", "ts": 1.0, "kind": "cap"})
    me = MagicMock()
    me._ping_lock = E.threading.Lock()
    me._sig_accts, me._auto_accts, me._token, me.lang, me._profile = {}, {}, "tok", "ko", {}
    me._APP_VER = "2026.10.14a"
    me._dce_on = lambda: True
    _sent, _resp = [], {"text": "al:ok"}
    _op, _ot = _rqm.post, E.threading.Thread
    _rqm.post = lambda url, timeout=None, json=None: (_sent.append(dict(json or {})), SimpleNamespace(ok=True, text=_resp["text"]))[1]
    E.threading.Thread = _SyncT
    try:
        E.App._alive_ping(me, armed=True, force=True)
        _n1 = len(E._dce_load())
        _resp["text"] = "al:ok dce:10"
        E.App._alive_ping(me, armed=True, force=True)
        _n2 = len(E._dce_load())
        E.App._alive_ping(me, armed=True, force=True, sync=True)
    finally:
        _rqm.post, E.threading.Thread = _op, _ot
    ck("2f 생존 핑에 사건 10개('dce', 쌓은 시각 빼고), 옛 서버 답(al:ok)이면 그대로 두고 'dce:' 답이면 보낸 것만 지운다, 종료 핑은 싣지 않는다",
       len(_sent) == 3 and [e["id"] for e in _sent[0].get("dce", [])] == [f"ev{i:02d}" for i in range(10)]
       and "q_ts" not in _sent[0]["dce"][0] and _n1 == 12 and _n2 == 2 and "dce" not in _sent[2]
       and {"t", "armed", "v", "m", "arm", "closed"} <= set(_sent[0]), (_n1, _n2, [list(x) for x in _sent]))
    # 2g 서버 스위치가 꺼져 있으면(회원 하트비트 - 지금은 대표 토큰만 켠다) 쌓지도 보내지도 않는다
    me._dce_on = lambda: False
    _sent.clear()
    _rqm.post = lambda url, timeout=None, json=None: (_sent.append(dict(json or {})), SimpleNamespace(ok=True, text="al:ok dce:0"))[1]
    E.threading.Thread = _SyncT
    try:
        E.App._alive_ping(me, armed=True, force=True)
    finally:
        _rqm.post, E.threading.Thread = _op, _ot
    _dce_clear()
    st = Stub(None)
    st._gate = dict(Stub._gate, dce=False)
    E.App._dce_note(st, {"broker": "projectx", "sc": SC, "key": K1}, {"firm": "Topstep", "size": 150000}, "cap", 4860.0, 4900.0,
                    None, 0.0, {"name": SC, "bal": 150000.0, "poss": []}, {"day": NOW_DAY()}, "real")
    ck("2g 서버 스위치 꺼짐(회원): 핑에 'dce'를 싣지 않고(쌓여 있던 사건도 보내지 않음), 새 사건을 쌓지 않는다",
       "dce" not in _sent[0] and E._dce_pending() == [] and not st.__dict__.get("pings"), (_sent, E._dce_pending()))
    # 2h 하트비트: 서버가 autopilot.dce를 켜면 게이트에 dce True, 없으면 False
    import autopilot_crypto as _acm
    _gates = []
    for _flag in (True, None):
        _hb = {"ok": True, "tier": "admin", "aead": False, "exp": _real_time() + 600,
               "autopilot": {"enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True}, "brokers": {},
                             "p150": None, **({"dce": True} if _flag else {})}}
        _og, _od = _rqm.get, _acm.decrypt
        _rqm.get = lambda *a, **k: SimpleNamespace(ok=True, status_code=200, text="x", headers={})
        _acm.decrypt = lambda tok, txt, _h=_hb: dict(_h)
        E.threading.Thread = _SyncT
        sth = Stub(None)
        sth.token_e = SimpleNamespace(get=lambda: "tok")
        try:
            E.App._heartbeat(sth)
        finally:
            _rqm.get, _acm.decrypt, E.threading.Thread = _og, _od, _ot
        _gates.append(((sth._gate or {}).get("dce"), E.App._dce_on(sth)))
    ck("2h 하트비트의 autopilot.dce가 게이트 스위치(켬 = True, 키 없음 = False)", _gates == [(True, True), (False, False)], _gates)
    _dce_clear()

    # ════════ 4 화면 금액 ════════
    b = MarkPX(realized={101: 0.0})
    b.accts[0]["balance"] = 150000.0
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    _row = st._acfg["GC"]["accounts"][0]
    _t_flat = E.App._daycap_row_text(st, "GC", _row)
    run_entry(st, b, [("GC", 7), ("MGC", 9)])
    _t_pos = E.App._daycap_row_text(st, "GC", _row)
    ck("4a 상태 줄 = 감시가 실제로 닫는 통과 금액: 포지션 없을 때 $159,050, GC 7 + MGC 9를 들면 $159,158(2틱 x 계약)",
       "통과 $159,050" in _t_flat and "통과 $159,158" in _t_pos, (_t_flat, _t_pos))
    ck("4b 진입 로그와 '최고일 모름' 안내도 같은 금액($159,158)",
       any("통과 $159,158" in m for m in st.logs) and any("통과 익절을 이익 목표 기준($159,158)으로 잡습니다" in m for m in st.logs)
       and not any("$159,050" in m for m in st.logs), [m for m in st.logs if "159," in m])
    b = MarkPX(realized={101: 0.0}, hist_err=RuntimeError("history down"))    # 거래 기록을 못 읽음 = 최고일 모름
    b.accts[0]["balance"] = 150000.0
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    b.pos[(101, GC)], b.pos[(101, MGC)] = 7, 9
    b.avgs[(101, GC)], b.avgs[(101, MGC)] = 2650.0, 2650.0
    _legs_note(st, b, SC, GC, 7)
    _legs_note(st, b, SC, MGC, 9)
    b.marks = {GC: 2652.0, MGC: 2652.0}
    _due_now(st)
    scan(st)
    _tr = E.App._dc_lines(st, {"broker": "projectx", "sc": SC, "key": K1},
                          E.App._daycap_resolve_cfg(st, "projectx", SC, P0, name=SC, bal=150000.0),
                          [{"con": GC, "qty": 7}, {"con": MGC, "qty": 9}], 1580.0, 151580.0, NOW_DAY())[2]
    ck("4c 감시 경로의 '최고일 모름' 안내 = 감시 선($159,158)과 같은 금액",
       _tr == 159158.0 and any("통과 익절을 이익 목표 기준($159,158)으로 잡습니다" in m for m in st.logs), (_tr, st.logs[-3:]))
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 150000.0}], realized={101: 0.0})
    st = _new_st(b)
    run_entry(st, b, [("GC", 20)])
    _cl_log = [m for m in st.logs if "계좌 한도에 맞춰 줄였습니다" in m]
    _x = E._size_limit("projectx", "EXPRESS-V2-1", 3200.0, 150000)
    ck("4d 계좌 한도 로그의 괄호가 겹치지 않는다: '(Topstep 150K 평가 한도 미니 15계약)', XFA는 '미니 10계약, 잔고 $3,200 기준', EN 짝",
       len(_cl_log) == 1 and "(Topstep 150K 평가 한도 미니 15계약)." in _cl_log[0] and "))" not in _cl_log[0]
       and _x[1] == "Topstep 150K 펀디드 Scaling Plan 한도 미니 10계약, 잔고 $3,200 기준"
       and _x[2] == "Topstep 150K funded Scaling Plan limit of 10 mini contracts, at a balance of $3,200", (_cl_log, _x))

    # ════════ 5 여러 계좌 1초 감시 ════════
    import math as _math

    class SlowPX(MarkPX):
        """호출마다 가짜 시계를 LAT초 늦춘다(실제 왕복 시간) + 호출 시각 기록."""
        LAT = 0.12

        def __init__(self, *a, **k):
            MarkPX.__init__(self, *a, **k)
            self.tcalls = []

        def _lat(self, n):
            _clk["t"] += self.LAT
            self.tcalls.append((n, _clk["t"]))

        def _accounts(self, *a, **k):
            self._lat("other")
            return MarkPX._accounts(self, *a, **k)

        def open_positions(self, aid, timeout=None):
            self._lat("other")
            return MarkPX.open_positions(self, aid, timeout)

        def mark_price(self, con, max_age_s=120.0, timeout=None):
            self._lat("bars")
            return MarkPX.mark_price(self, con, max_age_s, timeout)

        def day_realized(self, aid, start_iso, timeout=None):
            self._lat("other")
            return MarkPX.day_realized(self, aid, start_iso, timeout)

        def trades(self, aid, start_iso, timeout=None):
            self._lat("other")
            return MarkPX.trades(self, aid, start_iso, timeout)

    def _multi(n_near=1, secs=150, lat=0.12):
        accts = [{"id": 400 + i, "name": f"150KTC-V2-{i}", "balance": 150000.0} for i in range(8)]
        b = SlowPX(accts=accts, realized={400 + i: 0.0 for i in range(8)})
        b.LAT = lat
        st = _new_st(b, [("GC", a["name"], P0, "projectx") for a in accts])
        for i, a in enumerate(accts):
            b.pos[(a["id"], GC)] = 7
            b.avgs[(a["id"], GC)] = 2650.0 if i < n_near else 2655.0
            _legs_note(st, b, a["name"], GC, 7)
        b.marks = {GC: 2656.43}                         # 가까운 계좌 +$4,501(상한 4,860까지 395 = 1초 구간), 나머지 +$1,001(5초 주기)
        ev = {}
        _orig = Stub._daycap_mon_pnl

        def _wrap(self, b_, t, snap, s_utc, day, marks=None):
            ev.setdefault(t["sc"], []).append(_clk["t"])
            return _orig(self, b_, t, snap, s_utc, day, marks)
        Stub._daycap_mon_pnl = _wrap
        try:
            _clk["t"] = float(_math.floor(_clk["t"]))
            t0 = _clk["t"]
            nxt = t0
            while _clk["t"] < t0 + secs:
                _clk["t"] = max(_clk["t"], nxt)
                scan(st)
                nxt = t0 + _math.ceil(_clk["t"] - t0 + 1e-9)    # 다음 1초 틱(감시가 바쁘면 그 틱은 건너뛴다)
        finally:
            Stub._daycap_mon_pnl = _orig
        return b, st, ev, t0

    def _max_win(ts, win):
        ts = sorted(ts)
        best, j = 0, 0
        for i in range(len(ts)):
            while ts[i] - ts[j] >= win:
                j += 1
            best = max(best, i - j + 1)
        return best

    b, st, ev, t0 = _multi()
    _near = [x for x in ev.get("150KTC-V2-0", []) if x >= t0 + 40]
    _gaps = [round(y - x, 2) for x, y in zip(_near, _near[1:])]
    _far_gap = max(max((y - x) for x, y in zip(v, v[1:])) for k, v in ev.items() if k != "150KTC-V2-0")
    _bars = _max_win([t for n, t in b.tcalls if n == "bars"], 30.0)
    _oth = _max_win([t for n, t in b.tcalls if n == "other"], 60.0)
    print(f"INFO 5 Combine 8개(가까운 계좌 1개, 호출 0.12초): 가까운 계좌 간격 평균 {sum(_gaps) / max(1, len(_gaps)):.2f}초, 최대 {max(_gaps or [0]):.2f}초, "
          f"나머지 최대 {_far_gap:.1f}초, 시세 30초 최대 {_bars}회, 그 밖 60초 최대 {_oth}회")
    ck("5a 한 로그인 Combine 8개: 선에 가까운 계좌는 약 1초마다 본다(평균 1.2초 이하, 최대 2초 이하), 나머지는 미뤄도 15초 안",
       _gaps and sum(_gaps) / len(_gaps) <= 1.2 and max(_gaps) <= 2.0 and _far_gap <= 15.0, (_gaps[:20], _far_gap))
    ck("5b 호출 한도 안: 시세 30초 40회 이하, 그 밖 60초 120회 이하(ProjectX 문서 50 / 200 아래)", _bars <= 40 and _oth <= 120, (_bars, _oth))
    ck("5c 그 동안 정리는 없음(상한 아래)", not _cl(b) and not st.__dict__.get("_dc_fired"), _cl(b))
    b8, st8, ev8, t08 = _multi(n_near=8)
    _g8 = [y - x for k, v in ev8.items() for x, y in zip(v, v[1:]) if x >= t08 + 40]
    _b8 = _max_win([t for n, t in b8.tcalls if n == "bars"], 30.0)
    _o8 = _max_win([t for n, t in b8.tcalls if n == "other"], 60.0)
    print(f"INFO 5 Combine 8개 모두 1초 구간: 간격 평균 {sum(_g8) / max(1, len(_g8)):.2f}초, 최대 {max(_g8 or [0]):.2f}초, "
          f"시세 30초 최대 {_b8}회, 그 밖 60초 최대 {_o8}회")
    ck("5d 8개가 모두 1초 구간이어도 계좌마다 고르게(평균 2.5초 이하, 최대 5초 이하 - 몫을 몰아 쓰고 30초 막히던 것) + 호출 한도 안",
       _g8 and sum(_g8) / len(_g8) <= 2.5 and max(_g8) <= 5.0 and _b8 <= 40 and _o8 <= 120, (max(_g8 or [0]), _b8, _o8))
    # 5f (3차 마무리 리뷰 P1, 최소 보장) 한 로그인에 1초 구간 150K Combine 3개 또는 5개 + 먼 50K Combine(GC 2, 5초 주기): 먼 계좌도 15초 안에
    # 손익을 보고, 60초 뒤 GC가 올라 먼 계좌 하나만 상한 $1,620를 넘으면(+$1,654) 15초 안에 정리한다. 호출은 감시 몫 + 굶은 계좌 여유 안
    # (시세 30초 44회, 그 밖 60초 140회 - 문서 한도 50 / 200 아래). 수정 전에는 먼 계좌가 3~4분 눈을 감았고 300초 안에 정리하지 못했다.
    def _mix(n_near, jump=60, after=30):
        accts = ([{"id": 400 + i, "name": f"150KTC-V2-{i}", "balance": 150000.0} for i in range(n_near)]
                 + [{"id": 500 + i, "name": f"50KTC-V2-{i}", "balance": 50000.0} for i in range(8 - n_near)])
        last = 500 + (8 - n_near) - 1
        b = SlowPX(accts=accts, realized={a["id"]: 0.0 for a in accts})
        st = _new_st(b, [("GC", a["name"], P0, "projectx") for a in accts])
        for a in accts:
            if a["name"].startswith("150K"):
                q, avg, risk = 3, 2651.43, 3500.0
            else:
                q, risk = 2, 500.0
                avg = 2653.73 if a["id"] == last else 2654.93
            b.pos[(a["id"], GC)] = q
            b.avgs[(a["id"], GC)] = avg
            _legs_note(st, b, a["name"], GC, q)
            st._dc_mon[E.App._daycap_key("projectx", a["name"])]["risk"] = risk
        b.marks = {GC: 2656.43}
        ev = {}
        _orig = Stub._daycap_mon_pnl

        def _wrap(self, b_, t, snap, s_utc, day, marks=None):
            ev.setdefault(t["sc"], []).append(_clk["t"])
            return _orig(self, b_, t, snap, s_utc, day, marks)
        Stub._daycap_mon_pnl = _wrap
        closed_at, others = None, set()
        try:
            _clk["t"] = float(_math.floor(_clk["t"]))
            t0 = _clk["t"]
            nxt, jumped = t0, False
            while _clk["t"] < t0 + jump + after:
                _clk["t"] = max(_clk["t"], nxt)
                if not jumped and _clk["t"] >= t0 + jump:
                    b.marks[GC] = 2662.0
                    jumped = True
                n0 = len(b.calls)
                scan(st)
                for c in b.calls[n0:]:
                    if c[0] in ("close_contract", "partial_close_contract"):
                        if c[1] == last and closed_at is None:
                            closed_at = _clk["t"]
                        elif c[1] != last:
                            others.add(c[1])
                nxt = t0 + _math.ceil(_clk["t"] - t0 + 1e-9)
                if closed_at:
                    break
        finally:
            Stub._daycap_mon_pnl = _orig
        _far = [a["name"] for a in accts if a["name"].startswith("50K")]
        _fg = max(max(y - x for x, y in zip(v, v[1:])) for v in ([t0 + 20] + [x for x in ev.get(k, []) if t0 + 20 <= x <= t0 + jump]
                                                                 + [t0 + jump] for k in _far))
        return ((closed_at - (t0 + jump)) if closed_at else None, round(_fg, 1),
                _max_win([t for n, t in b.tcalls if n == "bars"], 30.0), _max_win([t for n, t in b.tcalls if n == "other"], 60.0), others)
    _r5f = {n: _mix(n) for n in (3, 5)}
    print("INFO 5f 1초 구간 Combine n개 + 먼 50K: " + ", ".join(f"{n}개 = 정리 {r[0] if r[0] is None else round(r[0], 1)}초, 먼 계좌 최대 간격 {r[1]}초, "
                                                            f"시세 30초 {r[2]}회, 그 밖 60초 {r[3]}회" for n, r in _r5f.items()))
    ck("5f 1초 구간 Combine 3개, 5개 + 먼 50K Combine: 먼 계좌도 15초 안에 보고, 상한을 넘은 먼 계좌는 15초 안에 정리(다른 계좌는 안 닫음), "
       "호출은 시세 30초 44회, 그 밖 60초 140회 이하",
       all(r[0] is not None and r[0] <= 15.0 and r[1] <= 15.0 and r[2] <= 44 and r[3] <= 140 and not r[4] for r in _r5f.values()), _r5f)
    # 5e 진입 직후 낡은 '플랫' 목록으로 앱 진입 기록을 놓지 않는다(진입 때 포지션 목록을 버리고, 20초 안의 leg는 기다린다)
    b = MarkPX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    _due_now(st)
    scan(st)                                            # 플랫 목록을 읽어 둔다
    _had = any(str(k[1]) == "101" for k in st._dc_pcache)
    _legs_note(st, b, SC, GC, 2)                        # 앱 진입 기록(브로커에는 아직 안 보이는 경우까지)
    _gone = not any(str(k[1]) == "101" for k in st._dc_pcache)
    for _ in range(3):
        _clk["t"] += 1
        st._dc_due = {}
        scan(st)
    _kept = K1 in st._dc_mon
    _clk["t"] += 25
    for _ in range(2):
        _clk["t"] += 1
        st._dc_due = {}
        scan(st)
    ck("5e 진입 때 그 계좌의 읽어 둔 포지션 목록을 버리고, 20초 안의 leg는 '플랫 두 번'이어도 기록을 놓지 않는다(20초 뒤에는 놓는다)",
       _had and _gone and _kept and K1 not in st._dc_mon, (_had, _gone, _kept, st._dc_mon.get(K1)))

    # ════════ 7 NinjaTrader 1초 감시(대표 2026-10-15) ════════
    # NinjaTrader 평가, 시험 계좌는 포지션과 상관없이 1초마다 본다(로컬 브리지 push, 브로커 호출 한도 없음 - 손으로 연 Sim101 포지션이 첫 확인까지
    # 30초를 기다리던 것). ProjectX는 종전 그대로(1/5/30초, 호출 몫). 1초 NT8 읽기는 디스크에 쓰지 않고, 같은 회차의 0.5초 몫과 굶은 계좌 자리를
    # 쓰지 않는다(ProjectX 먼 계좌가 NinjaTrader 때문에 밀리거나 굶지 않게).
    class MultiNT8:
        """NinjaTrader 브리지 하나에 계좌 여럿(로컬 push). LAT = 읽기마다 가짜 시계를 늦춘다. fresh False = 브리지 멈춤(새 push 없음).
        PUSH = push 시각을 고정한다(같은 push를 여러 번 읽음) - None이면 읽을 때마다 새 push(지금 시각)."""
        name = "nt8"
        LAT = 0.0
        PUSH = None

        def __init__(self, accts):
            self.a = {n: {"cash": float(c), "nl": float(c), "pos": {}} for n, c in accts.items()}
            self.fresh, self.reads, self.calls, self.fresh_n = True, [], [], 0

        def snapshot_fresh(self, max_age=10.0):
            self.fresh_n += 1
            return self.fresh

        def account_connected(self, acct):
            return acct in self.a

        def state_ts(self):
            return self.PUSH if self.PUSH is not None else time.time()

        def snapshot_pair(self, acct):
            _clk["t"] += self.LAT
            self.reads.append((acct, _clk["t"]))
            x = self.a.get(acct)
            if x is None:
                return None
            return (self.state_ts(), x["cash"], x["nl"], x["nl"] - x["cash"],
                    [{"account": acct, "instrument": s, "net_qty": q, "avg_price": 25000.0} for s, q in x["pos"].items() if q])

        def account_balance(self, acct):
            return (self.a.get(acct) or {}).get("cash")

        def account_netliq(self, acct):
            return (self.a.get(acct) or {}).get("nl")

        def account_unrealized(self, acct):
            x = self.a.get(acct)
            return (x["nl"] - x["cash"]) if x else None

        def closed_fills(self, start_ms):
            return []

        def close_contract(self, aid, sym):
            self.calls.append(("close_contract", aid, sym))

    LUS = ["LDI150A0001", "LDI150A0002", "LDI150A0003"]
    _K8 = ["nt8|" + n.casefold() for n in LUS]

    def _n8_st(px, n8, px_rows):
        st = _new_st(px, px_rows + [("NQ", n, dict(P0, stage="eval"), "nt8") for n in LUS])
        st._passtp_broker = lambda cfg: n8 if (cfg or {}).get("broker") == "nt8" else px
        return st

    def _wcount():
        """감시가 디스크에 쓰는 길(감시 상태, 감시 기록, 파일 로그)을 센다. 반환 (센 값, 되돌리기)."""
        w = {"state": 0, "mon": 0, "logf": 0}
        o = (E._save_daycap_state, E._save_daycap_mon, E._log_to_file)

        def _c(name, fn):
            def _f(*a, **k):
                w[name] += 1
                return fn(*a, **k)
            return _f
        E._save_daycap_state, E._save_daycap_mon, E._log_to_file = _c("state", o[0]), _c("mon", o[1]), _c("logf", o[2])

        def _undo():
            E._save_daycap_state, E._save_daycap_mon, E._log_to_file = o
        return w, _undo

    n8 = MultiNT8({n: 150000.0 for n in LUS})
    px = MarkPX(realized={101: 1000.0})
    st = _n8_st(px, n8, [("GC", SC, P0, "projectx")])
    _clk["t"] = float(_math.floor(_clk["t"]))
    t0 = _clk["t"]
    scan(st)                                            # 첫 회차: Lucid 시작 잔고, 상태 줄(그날 한 번 쓰는 디스크 기록은 여기서)
    _d7 = {k: round(float(st._dc_due.get(k, 0)) - t0, 3) for k in _K8 + [K1]}
    ck("7a 플랫: NinjaTrader 평가 계좌 다음 확인 기한 0.35초 뒤(0.5초 타이머마다 읽게, 2026-10-16, 종전 30초), ProjectX 플랫 계좌는 종전 그대로 30초",
       all(_d7[k] == E._DAYCAP_NT8_POLL_S == 0.35 for k in _K8) and _d7[K1] == 30.0, _d7)
    _px0 = len([c for c in px.calls if c[0] in ("open_positions", "_accounts", "day_realized")])
    _w7, _undo7 = _wcount()
    try:
        for _i in range(1, 21):
            _clk["t"] = t0 + _i
            scan(st)
    finally:
        _undo7()
    _r7 = {n: [t for a, t in n8.reads if a == n and t > t0] for n in LUS}
    _px1 = len([c for c in px.calls if c[0] in ("open_positions", "_accounts", "day_realized")])
    ck("7b 20초 플랫: NinjaTrader 계좌마다 20번(1초마다) 읽고, ProjectX 플랫 계좌는 호출을 더하지 않음(30초 주기 그대로)",
       all(len(v) == 20 for v in _r7.values()) and _px1 == _px0, ({n: len(v) for n, v in _r7.items()}, _px0, _px1))
    ck("7c 플랫 1초 감시 20초 동안 디스크 쓰기 0번(감시 상태, 감시 기록, 파일 로그)", _w7 == {"state": 0, "mon": 0, "logf": 0}, _w7)
    # 7d 손으로 연 포지션(선에서 먼 곳, NetLiq가 1초마다 바뀜): 그래도 1초마다 보고, 디스크에 1초마다 쓰지 않는다(종전 5초 주기)
    n8.a[LUS[0]]["pos"] = {"MNQ 12-26": 2}
    t1 = _clk["t"]
    _w7d, _undo7d = _wcount()
    try:
        for _i in range(1, 21):
            _clk["t"] = t1 + _i
            n8.a[LUS[0]]["nl"] = 150000.0 + 40.0 + (_i % 5) * 7.0
            scan(st)
    finally:
        _undo7d()
    _r7d = [t for a, t in n8.reads if a == LUS[0] and t > t1]
    _st7d = (E.App._dc_state(st)["status"].get(_K8[0]) or {}).get("room")
    ck("7d 포지션이 열린 NinjaTrader 계좌(선에서 멂)도 1초마다(종전 5초), 상태 줄 '오늘 남은'은 따라 바뀌고, 디스크 쓰기 0번",
       len(_r7d) == 20 and _st7d is not None and _w7d == {"state": 0, "mon": 0, "logf": 0} and not n8.calls,
       (len(_r7d), _st7d, _w7d, n8.calls))
    # 7e 앱이 넣은 leg가 끝난 뒤(손절 등) 플랫: push 하나의 순간 플랫이나 같은 플랫 push를 여러 번 읽은 것으로는 기록을 놓지 않고, 서로 다른
    #    push 두 개에서 플랫이면 놓는다(검증 소견 5 - 종전 '플랫이 5초 넘게'는 그 5초 안에 손으로 연 같은 방향 포지션을 앱 몫으로 남겼다, 7m).
    n8.a[LUS[1]]["pos"] = {"MNQ 12-26": 3}
    _legs_note(st, n8, LUS[1], "MNQ 12-26", 3, asset="NQ")
    t2 = _clk["t"] + E._DAYCAP_LEG_GRACE_S + 1
    _clk["t"] = t2
    scan(st)
    n8.a[LUS[1]]["pos"] = {}                             # 순간 값 하나: push 하나만 플랫, 다음 push에 다시 보임
    _clk["t"] = t2 + 1
    scan(st)
    n8.a[LUS[1]]["pos"] = {"MNQ 12-26": 3}
    _clk["t"] = t2 + 2
    scan(st)
    _blip = _K8[1] in st._dc_mon and [lg.get("size") for lg in st._dc_mon[_K8[1]]["legs"]] == [3]
    n8.a[LUS[1]]["pos"] = {}                             # 이번엔 정말 끝남(손절) - 그런데 브리지가 같은 push를 네 번 보인다(push 시각 그대로)
    n8.PUSH = t2 + 2.5
    _same = []
    for _i in range(3, 7):
        _clk["t"] = t2 + _i
        scan(st)
        _same.append(_K8[1] in st._dc_mon)
    n8.PUSH = None                                       # 다음 push도 플랫
    _clk["t"] = t2 + 7
    scan(st)
    _gone = _K8[1] not in st._dc_mon
    ck("7e 앱 leg가 끝난 플랫: push 하나의 순간 플랫, 같은 플랫 push를 네 번 읽은 것으로는 기록을 그대로 두고, 다른 push에서도 플랫이면 놓는다",
       _blip and _same == [True] * 4 and _gone, (_blip, _same, _gone))

    # 7f 같은 회차의 ProjectX 몫(5f 장면: 한 로그인에 1초 구간 150K Combine 3개, 5개 + 먼 50K)에 NinjaTrader 평가 계좌 셋을 더한다(읽기마다 0.01초).
    #    먼 ProjectX 계좌는 5f와 같은 기준(15초 안에 보고, 상한을 넘으면 15초 안에 정리, 호출 한도 안), NinjaTrader는 회차마다 맨 앞에서 읽는다
    #    (ProjectX 호출이 길어 회차가 1초를 넘으면 그 회차 간격 - 3초 안). 7g = 브리지가 멈춰 NinjaTrader 계좌 손익을 못 매기는 90초.
    def _mix8(n_near, n8_lat, n8_fresh, jump=60, after=30):
        accts = ([{"id": 400 + i, "name": f"150KTC-V2-{i}", "balance": 150000.0} for i in range(n_near)]
                 + [{"id": 500 + i, "name": f"50KTC-V2-{i}", "balance": 50000.0} for i in range(8 - n_near)])
        last = 500 + (8 - n_near) - 1
        b = SlowPX(accts=accts, realized={a["id"]: 0.0 for a in accts})
        nb = MultiNT8({n: 150000.0 for n in LUS})
        nb.LAT, nb.fresh = n8_lat, n8_fresh
        st = _n8_st(b, nb, [("GC", a["name"], P0, "projectx") for a in accts])
        for a in accts:
            if a["name"].startswith("150K"):
                q, avg, risk = 3, 2651.43, 3500.0
            else:
                q, risk = 2, 500.0
                avg = 2653.73 if a["id"] == last else 2654.93
            b.pos[(a["id"], GC)] = q
            b.avgs[(a["id"], GC)] = avg
            _legs_note(st, b, a["name"], GC, q)
            st._dc_mon[E.App._daycap_key("projectx", a["name"])]["risk"] = risk
        b.marks = {GC: 2656.43}
        ev = {}
        _orig = Stub._daycap_mon_pnl

        def _wrap(self, b_, t, snap, s_utc, day, marks=None):
            ev.setdefault(t["sc"], []).append(_clk["t"])
            return _orig(self, b_, t, snap, s_utc, day, marks)
        Stub._daycap_mon_pnl = _wrap
        closed_at, others = None, set()
        try:
            _clk["t"] = float(_math.floor(_clk["t"]))
            t0 = _clk["t"]
            nxt, jumped = t0, False
            while _clk["t"] < t0 + jump + after:
                _clk["t"] = max(_clk["t"], nxt)
                if not jumped and _clk["t"] >= t0 + jump:
                    b.marks[GC] = 2662.0
                    jumped = True
                n0 = len(b.calls)
                scan(st)
                for c in b.calls[n0:]:
                    if c[0] in ("close_contract", "partial_close_contract"):
                        if c[1] == last and closed_at is None:
                            closed_at = _clk["t"]
                        elif c[1] != last:
                            others.add(c[1])
                nxt = t0 + _math.ceil(_clk["t"] - t0 + 1e-9)
                if closed_at:
                    break
        finally:
            Stub._daycap_mon_pnl = _orig
        _far = [a["name"] for a in accts if a["name"].startswith("50K")]
        _fg = max(max(y - x for x, y in zip(v, v[1:])) for v in ([t0 + 20] + [x for x in ev.get(k, []) if t0 + 20 <= x <= t0 + jump]
                                                                 + [t0 + jump] for k in _far))
        _ng = max(max([y - x for x, y in zip(v, v[1:])] or [99.0])
                  for v in ([t for a, t in nb.reads if a == n and t0 + 5 <= t <= t0 + jump] for n in LUS))
        return {"close": (round(closed_at - (t0 + jump), 1) if closed_at else None), "far_gap": round(_fg, 1),
                "bars": _max_win([t for n, t in b.tcalls if n == "bars"], 30.0),
                "other": _max_win([t for n, t in b.tcalls if n == "other"], 60.0), "others": others,
                "n8_gap": round(_ng, 2), "n8_closes": list(nb.calls)}
    _r7f = {n: _mix8(n, 0.01, True) for n in (3, 5)}
    _r7g = {n: _mix8(n, 0.0, False, jump=90) for n in (3, 5)}
    print("INFO 7f 5f + NinjaTrader 셋: " + ", ".join(f"{n}개 = 정리 {r['close']}초, 먼 계좌 최대 간격 {r['far_gap']}초(5f {_r5f[n][1]}초), "
                                                   f"NT8 최대 간격 {r['n8_gap']}초, 시세 30초 {r['bars']}회, 그 밖 60초 {r['other']}회"
                                                   for n, r in _r7f.items()))
    print("INFO 7g 브리지 멈춤 90초: " + ", ".join(f"{n}개 = 정리 {r['close']}초, 먼 계좌 최대 간격 {r['far_gap']}초, 시세 30초 {r['bars']}회, "
                                                 f"그 밖 60초 {r['other']}회" for n, r in _r7g.items()))
    _ok5f = lambda r: (r["close"] is not None and r["close"] <= 15.0 and r["far_gap"] <= 15.0 and r["bars"] <= 44   # noqa: E731
                       and r["other"] <= 140 and not r["others"] and not r["n8_closes"])
    ck("7f 5f + NinjaTrader 평가 계좌 셋: 먼 ProjectX 계좌도 15초 안에 보고 상한을 넘으면 15초 안에 정리(다른 계좌는 안 닫음), 호출은 시세 30초 "
       "44회, 그 밖 60초 140회 이하, NinjaTrader는 회차마다 읽음(간격 3초 이하)",
       all(_ok5f(r) and r["n8_gap"] <= 3.0 for r in _r7f.values()), _r7f)
    ck("7g 브리지가 멈춰 NinjaTrader 손익을 못 매기는 90초에도 먼 ProjectX 계좌는 15초 안에 보고 15초 안에 정리(굶은 계좌 자리를 뺏기지 않음)",
       all(_ok5f(r) for r in _r7g.values()), _r7g)

    # 7h, 7i 한 회차를 정해 두고 본다(시간 몫, 굶은 계좌 자리)
    def _one_scan(px_lat, n8_lat, n8_fresh, far_evt_ago, n8_evt_ago):
        accts = [{"id": 600, "name": "150KTC-V2-N", "balance": 150000.0}, {"id": 601, "name": "50KTC-V2-F", "balance": 50000.0}]
        b = SlowPX(accts=accts, realized={600: 0.0, 601: 0.0})
        b.LAT = px_lat
        nb = MultiNT8({n: 150000.0 for n in LUS})
        nb.LAT, nb.fresh = n8_lat, n8_fresh
        st = _n8_st(b, nb, [("GC", a["name"], P0, "projectx") for a in accts])
        for a, avg in zip(accts, (2651.43, 2654.93)):
            b.pos[(a["id"], GC)] = 3 if a["id"] == 600 else 2
            b.avgs[(a["id"], GC)] = avg
            _legs_note(st, b, a["name"], GC, b.pos[(a["id"], GC)])
        b.marks = {GC: 2656.43}
        kn, kf = E.App._daycap_key("projectx", "150KTC-V2-N"), E.App._daycap_key("projectx", "50KTC-V2-F")
        for _k in (kn, kf):
            st._dc_mon[_k]["risk"] = 3500.0 if _k == kn else 500.0
            for lg in st._dc_mon[_k]["legs"]:
                lg["ts"] = _clk["t"] - 300                  # 막 진입한 계좌가 아니다(먼저 볼 계좌는 1초 구간 kn 하나)
        _now = _clk["t"]
        st._dc_fastk = {kn: True}
        st._dc_kb = {kn: id(b)}
        st._dc_lastopen = {kn: True, kf: True}
        st._dc_eval_ts = {kn: _now, kf: _now - far_evt_ago}
        for _k in _K8:
            st._dc_eval_ts[_k] = _now - n8_evt_ago
        scan(st)
        return float(st._dc_eval_ts.get(kf, 0.0)) >= _now, [a for a, t in nb.reads], nb.fresh_n
    _h_ok, _h_reads, _ = _one_scan(0.05, 0.25, True, 2.0, 0.0)
    ck("7h 1초 구간 ProjectX 계좌 + NinjaTrader 셋(읽기 0.75초, 0.5초 몫보다 김) + 먼 ProjectX 계좌: NinjaTrader 시간은 몫에서 빼 먼 계좌도 그 회차에 본다",
       _h_ok and sorted(_h_reads) == sorted(LUS), (_h_ok, _h_reads))
    _i_ok, _i_reads, _i_fn = _one_scan(0.15, 0.0, False, 20.0, 120.0)
    ck("7i 1초 구간 계좌가 몫을 다 쓰고 브리지가 멈춘 NinjaTrader 계좌가 오래 손익을 못 매겨도(그 회차에도 셋 다 확인), 굶은 계좌 자리는 20초 밀린 "
       "먼 ProjectX 계좌가 쓴다", _i_ok and not _i_reads and _i_fn == 3, (_i_ok, _i_reads, _i_fn))

    # 7j 타이머 흔들림(검증 소견 1): 감시는 1초 타이머(root.after, 앞 틱이 끝난 뒤 1000ms)마다 돌고 그 늦음은 0~16ms, NT8 읽기는 1~8ms로 흔들린다.
    #    기한을 '읽은 때 + 1.0초'로 두면 다음 회차의 읽기가 몇 ms만 일러도 그 계좌는 한 틱을 건너뛰어 2초 간격이 됐다. 8계좌 60틱 모두 틱마다 읽는다.
    import random as _rnd7

    class JitNT8(MultiNT8):
        def __init__(self, accts, rng):
            MultiNT8.__init__(self, accts)
            self.rng = rng

        def snapshot_pair(self, acct):
            self.LAT = self.rng.uniform(0.001, 0.008)
            return MultiNT8.snapshot_pair(self, acct)
    L8 = [f"LDI150A00{i:02d}" for i in range(10, 18)]
    _rj = _rnd7.Random(20261015)
    n8j = JitNT8({n: 150000.0 for n in L8}, _rj)
    stj = _new_st(n8j, [("NQ", n, dict(P0, stage="eval"), "nt8") for n in L8])
    stj._passtp_broker = lambda cfg: n8j
    _clk["t"] = float(_math.floor(_clk["t"]))
    _tick = _clk["t"]
    scan(stj)
    _tj0 = _clk["t"]
    for _i in range(60):
        _tick = max(_tick + 1.0 + _rj.uniform(0.0, 0.016), _clk["t"])
        _clk["t"] = _tick
        scan(stj)
    _rjr = {n: [t for a, t in n8j.reads if a == n and t > _tj0] for n in L8}
    _rjg = {n: round(max(y - x for x, y in zip(v, v[1:])), 3) for n, v in _rjr.items()}
    print("INFO 7j 타이머 흔들림 60틱: 계좌별 읽기 " + str([len(v) for v in _rjr.values()]) + ", 최대 간격 " + str(sorted(set(_rjg.values()))))
    ck("7j 타이머 늦음 0~16ms, 읽기 1~8ms로 흔들려도 NinjaTrader 8계좌 모두 60틱에 60번(틱마다) 읽음, 최대 간격 1.1초 이하(종전 2초 간격이 섞였다)",
       all(len(v) == 60 for v in _rjr.values()) and max(_rjg.values()) <= 1.1, ({n: len(v) for n, v in _rjr.items()}, _rjg))

    # 7k 두 push 확인(검증 소견 2): 앱 포지션 없이 상한에 닿아 '오늘 닿음'으로 두는 것, 플랫 잔고로 '통과'로 표시하는 것은 서로 다른 push 두 개에서
    #    같은 판단일 때만. 튄 push 하나(일관된 NetLiq + 9,000 같은 값)로 그날 남은 신호를 잃지 않는다.
    n8k = MultiNT8({n: 150000.0 for n in LUS})
    stk = _new_st(n8k, [("NQ", n, dict(P0, stage="eval"), "nt8") for n in LUS])
    stk._passtp_broker = lambda cfg: n8k
    _lucid_chain(stk, [150000.0, 155000.0], acct=LUS[1])           # LUS[1]: 처음부터 본 계좌(통과 표시 가능), 오늘 시작 158,000
    n8k.a[LUS[1]]["cash"] = n8k.a[LUS[1]]["nl"] = 158000.0
    _clk["t"] = float(_math.floor(_clk["t"])) + 1
    scan(stk)                                                       # 첫 회차: 거래일 시작 잔고
    _rk = E.App._daycap_resolve_cfg(stk, "nt8", LUS[0], dict(P0, stage="eval"), name=LUS[0], bal=150000.0)
    _capk = float(_rk["cap"])
    _rk1 = E.App._daycap_resolve_cfg(stk, "nt8", LUS[1], dict(P0, stage="eval"), name=LUS[1], bal=158000.0)
    _needk = float(_rk1["pass_need"])
    n8k.a[LUS[0]]["pos"] = {"MNQ 12-26": 4}                        # 손으로 연 포지션(앱 leg 없음)
    _day = NOW_DAY()

    def _k_step(nl0=None, cash1=None, push=None):
        if nl0 is not None:
            n8k.a[LUS[0]]["nl"] = nl0
        if cash1 is not None:
            n8k.a[LUS[1]]["cash"] = n8k.a[LUS[1]]["nl"] = cash1
        n8k.PUSH = push
        _clk["t"] += 1
        scan(stk)
        return (E.App._daycap_hit_today(stk, "nt8", LUS[0], _day), _K8[1] in E.App._dc_state(stk)["passed"])
    _over, _back = 150000.0 + _capk + 300.0, 150200.0
    _seq = [_k_step(_over, _needk + 100.0),                        # 튄 push 하나
            _k_step(_back, 158000.0),                              # 다음 push는 정상
            _k_step(_over, _needk + 100.0, push=_clk["t"] + 0.5),   # 같은 push를 두 번 읽음
            _k_step(push=_clk["t"] - 0.5),
            _k_step(_over, _needk + 100.0)]                        # 다른 push에서도 같은 판단 = 확정
    ck("7k 튄 push 하나(상한 넘는 NetLiq, 통과 잔고)나 같은 push 두 번 읽기로는 오늘 닿음, 통과 표시를 하지 않고, 다른 push에서도 같으면 한다",
       _seq == [(False, False), (False, False), (False, False), (False, False), (True, True)]
       and not n8k.calls and _over < _needk, (_seq, n8k.calls, _capk, _needk))

    # 7l NetLiq 불일치 파일 로그(검증 소견 4): NT8을 1초마다 읽어도 어긋남이 이어지는 동안 파일 로그는 60초에 한 줄(종전 1초마다 한 줄)
    class MisNT8(MultiNT8):
        def snapshot_pair(self, acct):
            sp = MultiNT8.snapshot_pair(self, acct)
            return None if sp is None else (sp[0], sp[1], sp[2], sp[3] + 400.0, sp[4])
    n8l = MisNT8({n: 150000.0 for n in LUS})
    stl = _new_st(n8l, [("NQ", LUS[0], dict(P0, stage="eval"), "nt8")])
    stl._passtp_broker = lambda cfg: n8l
    n8l.a[LUS[0]]["pos"] = {"MNQ 12-26": 2}
    n8l.a[LUS[0]]["nl"] = 150100.0
    _clk["t"] = float(_math.floor(_clk["t"])) + 1
    scan(stl)
    _wl, _undol = _wcount()
    try:
        for _i in range(1, 21):
            _clk["t"] += 1
            scan(stl)
        _w20 = dict(_wl)
        _clk["t"] += E._DAYCAP_NL_LOG_EVERY_S
        scan(stl)
    finally:
        _undol()
    ck("7l NetLiq 불일치가 이어지는 20초(20번 읽음) 동안 파일 로그 0줄(첫 줄은 그 앞), 60초 지나면 한 줄 더, 감시 상태와 기록 쓰기 0",
       _w20 == {"state": 0, "mon": 0, "logf": 0} and _wl == {"state": 0, "mon": 0, "logf": 1}, (_w20, _wl))

    # 7m 손절로 플랫이 된 뒤 2초 안에 회원이 같은 방향으로 손으로 연 포지션(검증 소견 5): 앱 leg는 서로 다른 push 두 개의 플랫에서 이미 놓였으니
    #    상한에 닿아도 그 포지션을 앱 몫으로 닫지 않는다(수동이 우선). 종전 '플랫 5초'는 기록을 남겨 손으로 연 3계약을 앱 몫으로 닫았다.
    n8m = MultiNT8({n: 150000.0 for n in LUS})
    stm = _new_st(n8m, [("NQ", n, dict(P0, stage="eval"), "nt8") for n in LUS])
    stm._passtp_broker = lambda cfg: n8m
    n8m.a[LUS[2]]["pos"] = {"MNQ 12-26": 3}
    _legs_note(stm, n8m, LUS[2], "MNQ 12-26", 3, asset="NQ")
    _clk["t"] = float(_math.floor(_clk["t"])) + E._DAYCAP_LEG_GRACE_S + 1
    scan(stm)
    n8m.a[LUS[2]]["pos"] = {}                                      # 손절 체결
    _m_rec = []
    for _i in range(2):
        _clk["t"] += 1
        scan(stm)
        _m_rec.append(_K8[2] in stm._dc_mon)
    n8m.a[LUS[2]]["pos"] = {"MNQ 12-26": 3}                        # 1초 뒤 회원이 같은 방향 3계약을 손으로 연다, 곧 상한을 넘는다
    n8m.a[LUS[2]]["nl"] = 150000.0 + _capk + 300.0
    for _i in range(4):
        _clk["t"] += 1
        scan(stm)
    _m_cl = [c for c in n8m.calls if c[0] == "close_contract"]
    ck("7m 손절 플랫을 다른 push 두 개에서 보면(2초) 앱 기록을 놓고, 곧바로 손으로 연 같은 방향 3계약은 상한을 넘어도 닫지 않고 오늘 닿음만",
       _m_rec == [True, False] and not _m_cl and E.App._daycap_hit_today(stm, "nt8", LUS[2], NOW_DAY()), (_m_rec, _m_cl))

    # 7n 화면 상태 줄 다시 쓰기(검증 소견 9): 포지션이 열린 NT8 계좌 8개가 1초마다 '오늘 남은'을 바꿔도 다시 쓰기는 1초에 한 번까지(종전 1초에 8번)
    _q7 = []

    def _after7(ms, fn=None, *a):
        _q7.append((_clk["t"] + (ms or 0) / 1000.0, fn))

    def _flush7():
        for _it in sorted([x for x in _q7 if x[0] <= _clk["t"] + 1e-9], key=lambda x: x[0]):
            _q7.remove(_it)
            if _it[1]:
                _it[1]()
    n8n = MultiNT8({n: 150000.0 for n in L8})
    stn = _new_st(n8n, [("NQ", n, dict(P0, stage="eval"), "nt8") for n in L8])
    stn._passtp_broker = lambda cfg: n8n
    for n in L8:
        n8n.a[n]["pos"] = {"MNQ 12-26": 2}
    _rr7 = []
    stn._daycap_rows_refresh = lambda: _rr7.append(_clk["t"])
    stn.root = SimpleNamespace(after=_after7)
    _clk["t"] = float(_math.floor(_clk["t"])) + 1
    for _i in range(20):
        for _j, n in enumerate(L8):
            n8n.a[n]["nl"] = 150000.0 + 40.0 + ((_i + _j) % 5) * 7.0
        _clk["t"] += 1
        scan(stn)
        _flush7()
    _clk["t"] += 1.0
    _flush7()
    _room_last = {n: (E.App._dc_state(stn)["status"].get("nt8|" + n.casefold()) or {}).get("room") for n in L8}
    ck("7n 8계좌 20초: 상태 줄 다시 쓰기 21번 이하(1초에 한 번까지, 종전 160번), 마지막 값은 남음",
       0 < len(_rr7) <= 21 and all(v is not None for v in _room_last.values()), (len(_rr7), _room_last))

    # 7o 체결 DM 수량 다시 읽기(검증 소견 6): 주문보다 적게 보여 _settle_fill_qty(최대 3초)를 하더라도 감시 기록(앱 leg)은 그 전에 남긴다 - 그동안
    #    1초 NT8 감시가 이 포지션을 손으로 연 것으로 보지 않게
    class PartPX7(FakePX):
        def __init__(self, *a, **k):
            FakePX.__init__(self, *a, **k)
            self.first = {}

        def position_qty(self, aid, con):
            q = FakePX.position_qty(self, aid, con)
            if not self.first.get(con):
                self.first[con] = True
                return max(0, q - 1)
            return q
    b7o = PartPX7(realized={101: 0.0})
    st7o = _new_st(b7o, [("GC", SC, P0, "projectx")])
    _ord7 = []
    _om7 = Stub._daycap_mon_note

    def _mn7(self, *a, **k):
        _ord7.append("leg")
        return _om7(self, *a, **k)

    def _sf7(b_, aid, con, tag, qty, seen, is_nt8, *a, **k):
        _ord7.append(("settle", K1 in st7o._dc_mon))
        return qty
    Stub._daycap_mon_note = _mn7
    Stub._settle_fill_qty = staticmethod(_sf7)
    try:
        run_entry(st7o, b7o, [("GC", 2)])
    finally:
        Stub._daycap_mon_note = _om7
        del Stub._settle_fill_qty
    ck("7o 체결 수량 다시 읽기(_settle_fill_qty)는 감시 기록(_daycap_mon_note) 뒤 - 다시 읽는 동안에도 앱 leg가 있다",
       _ord7 == ["leg", ("settle", True)], _ord7)

    # 7p 발주가 감시를 붙잡지 않음(검증 소견 7, 실제 시간): 같은 신호로 들어간 Lucid 셋 중 A가 먼저 상한을 넘어 정리 중(애드온 ack 3초)일 때
    #    1.2초 뒤 B가 넘으면, A의 발주와 정리 확인을 기다리지 않고 B도 곧(넘은 때에서 2.5초 안) 정리 주문을 받는다. A에는 한 번만.
    class AckNT8(MultiNT8):
        def __init__(self, accts):
            MultiNT8.__init__(self, accts)
            self.ct = []

        def close_contract(self, aid, sym):
            self.ct.append((aid, sym, time.time()))
            if aid == LUS[0]:
                time.sleep(3.0)                                    # 애드온 ack을 기다리는 동안(실제 시간)
            x = self.a.get(aid)
            if x is not None:
                x["pos"] = {}
                x["cash"] = x["nl"]
    _ft = (time.time, time.sleep)
    time.time, time.sleep = _real_time, _real_sleep
    try:
        n8p = AckNT8({n: 150000.0 for n in LUS})
        stp = _new_st(n8p, [("NQ", n, dict(P0, stage="eval"), "nt8") for n in LUS])
        stp._passtp_broker = lambda cfg: n8p
        for n in LUS:
            n8p.a[n]["pos"] = {"MNQ 12-26": 3}
            _legs_note(stp, n8p, n, "MNQ 12-26", 3, asset="NQ")
        E.App._daycap_mon_scan(stp)                                # 시작 잔고
        _real_sleep(0.8)
        n8p.a[LUS[0]]["nl"] = 150000.0 + _capk + 300.0
        _tb = {}

        def _cross_b():
            _real_sleep(1.2)
            n8p.a[LUS[1]]["nl"] = 150000.0 + _capk + 300.0
            _tb["t"] = _real_time()
        _thb = __import__("threading").Thread(target=_cross_b, daemon=True)
        _thb.start()
        _tp0 = _real_time()
        while _real_time() - _tp0 < 15 and not (any(c[0] == LUS[1] for c in n8p.ct) and any(c[0] == LUS[0] for c in n8p.ct)):
            E.App._daycap_mon_scan(stp)
            _real_sleep(0.2)
        _thb.join(3)
        _ca = [c for c in n8p.ct if c[0] == LUS[0]]
        _cb = [c for c in n8p.ct if c[0] == LUS[1]]
        _dtb = round(_cb[0][2] - _tb["t"], 2) if (_cb and _tb.get("t")) else None
        print(f"INFO 7p A 정리 중 B가 넘은 때에서 B 정리 주문까지 {_dtb}초, A 정리 주문 {len(_ca)}번")
        ck("7p A가 정리 중(ack 3초)이어도 B는 넘은 때에서 2.5초 안에 정리 주문, A에는 한 번, C는 그대로",
           _dtb is not None and _dtb <= 2.5 and len(_ca) == 1 and not [c for c in n8p.ct if c[0] == LUS[2]], (_dtb, n8p.ct))
    finally:
        time.time, time.sleep = _ft

    # ════════ 6 문구 ════════
    import re as _re6
    _fns = ("_daycap_mode_notice", "_daycap_row_text", "_daycap_mon_fire", "_daycap_mon_finish", "_daycap_plan", "_prop_reco_apply",
            "_prop_reco_desc", "_lucid_rule_text", "_dc_best_note")
    _txt = "".join(inspect.getsource(getattr(E.App, n)) for n in _fns) + inspect.getsource(E._size_limit)
    _strs = [x for x in _re6.findall(r'"([^"\n]*[가-힣][^"\n]*)"', _txt)]
    _bad6 = [x for x in _strs if "·" in x or _re6.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", x)
             or _re6.search(r"(요|어요|에요|해요)[.)]?$", x.strip())]
    ck("6a 새 문구: 가운뎃점, 이모지, 해요체 없음(합니다체) + 시험 모드 문구 KO/EN 짝", not _bad6
       and "Test mode" in _txt and "시험 모드" in _txt and "Evaluation take-profit test mode is on" in _txt, _bad6)
finally:
    time.sleep, time.time = _real_sleep, _real_time
    E._daycap_day_start = _DAY0
    os.environ.pop("EQ_DAYCAP_SHADOW", None)
    E._DAYCAP_SRV = {}
    try:
        os.remove(E.CFG_PATH)
    except OSError:
        pass

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
