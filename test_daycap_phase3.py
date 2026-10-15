#!/usr/bin/env python3
# 평가 계좌 익절 3차(대표 2026-10-14 오후 확정, 앱 2026.10.14a) 오프라인 시험. 0 = 통과.
#   A 하루 상한 = 목표 x 상한 비율(내장 Topstep 0.54, Lucid 0.49) 그대로 닫는 선(청산 비용, 4% 여유 없음) - 정확히 그 금액에서 닫고 그 아래(4,858)에선 안 닫음
#   B 통과 익절 = 규모 + max(목표, 최고일 / 0.55)(Topstep), max(목표, 2 x 최고일)(Lucid) + $50, 날마다 다시, 같은 금액에서 두 번 안 닫음,
#     Topstep 최고일 = 거래 기록(Trade/search) + 앱 원장, Lucid = 앱 원장, 모르면 목표 + 로그 한 번
#   C 감시 주기: 선 가까이 1초, 포지션 5초, 없으면 30초 + ProjectX 호출 한도(시세는 로그인, 자산마다 1초에 한 번, 감시 몫 묶음, 429 쉼)
#   D 계좌 한도: Combine 50/100/150 마이크로, XFA Scaling Plan(그날 처음 본 잔고와 지금 잔고 중 작은 쪽), Lucid 100, 미니/마이크로 나눔
#   E 그림자 모드: 주문 0, '정리했을 경우' + 정확한 금액 로그 한 번(환경 변수, config.yaml 키), 기본 꺼짐
#   F Operator(확인 모드) 평가 계좌에 회원이 넣은 통과 목표 = 12e처럼 통과 익절 감시가 본다
#   G 권장 1R 표 하나, EXPRESS(규모 없음) = 150K로 봄
#   H 금액 -$120, '가까워' 대신 '닿아'/'넘어', 건너뛰는 계좌는 '분할 진입' 줄 없음, 새 문구(합니다체, 가운뎃점, 이모지 없음)
#   I 서버 값(하트비트 prop_reco): 범위 검증, 칸마다 내장값으로, 디스크 캐시, 바뀔 때만 로그 한 번, 회원 1R은 그대로
# HOME, APPDATA = 임시 폴더, 네트워크 차단, 브로커는 전부 가짜, EQ_NO_ALERT=1.
import datetime as _dt
import inspect
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_dcp3_")
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


try:
    # ════════ A 하루 상한 = 목표 x 상한 비율 그대로(내장값) ════════
    _caps = {(bk, sz): E._dc_cap_amount(bk, E._DAYCAP_FIRMS[bk]["targets"][sz])
             for bk in ("projectx", "nt8") for sz in E._DAYCAP_FIRMS[bk]["targets"]}
    ck("A1 상한 = 목표 x 비율(한 표, 내장값): Topstep 54% 50K 1,620 / 100K 3,240 / 150K 4,860, Lucid 49% 25K 613 / 50K 1,470 / 100K 2,940 / 150K 4,410",
       E._DAYCAP_FIRMS["projectx"]["pct"] == 0.54 and E._DAYCAP_FIRMS["nt8"]["pct"] == 0.49
       and _caps == {("projectx", 50000): 1620.0, ("projectx", 100000): 3240.0, ("projectx", 150000): 4860.0,
                     ("nt8", 25000): 613.0, ("nt8", 50000): 1470.0, ("nt8", 100000): 2940.0, ("nt8", 150000): 4410.0}, _caps)
    _st0 = Stub(None)
    ck("A2 감시 문턱 = 상한 그대로(청산 비용, 상한의 4% 여유를 더 빼지 않음 - 2차 상수 걷어냄)",
       all(E.App._daycap_trigger(_st0, 4860.0, lg) == 4860.0 for lg in ([], [{"con": GC, "qty": 7}, {"con": MGC, "qty": 9}],
                                                                          [{"con": MGC, "qty": 40}]))
       and not hasattr(E, "_DAYCAP_MOVE_PCT"))
    # Topstep: 오늘 실현 1,000 + GC 2(포인트당 $200) - 2669.30이면 오늘 4,860.00, 2669.29이면 4,858
    b = FakePX(realized={101: 1000.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2669.29}
    _due_now(st)
    scan(st)
    _a3 = list(_cl(b))
    b.marks = {GC: 2669.30}
    _due_now(st)
    scan(st)
    ck("A3 Topstep 150K: 오늘 손익 $4,858 = 그대로, $4,860.00(상한 그대로)에서 그 계좌 정리 + 오늘 표시",
       not _a3 and sorted(c[2] for c in _cl(b)) == [GC] and E.App._daycap_hit_today(st, "projectx", SC, NOW_DAY()),
       (_a3, _cl(b), st.logs[-4:]))
    ck("A4 정리 로그 = '하루 이익 상한($4,860)에 닿아'('가까워' 없음, 여유 금액 없음)",
       any("하루 이익 상한($4,860)에 닿아 앱이 넣은 포지션(GC 2)을 정리합니다" in m for m in st.logs)
       and not any("가까워" in m or "여유 $" in m for m in st.logs), st.logs[-6:])
    # Lucid 150K LucidDaily: 시작 150,000, NetLiq 154,409 = 그대로, 154,410 = 정리
    n8 = FakeNT8()
    st = _new_st(n8, [("NQ", LU, dict(P0, stage="eval"), "nt8")])
    n8.pos = {"NQ 12-26": 1}
    _app_leg(st, n8, LU, "NQ 12-26", 1)
    n8.nl = 154409.0
    scan(st)
    _a5 = list(n8.closed)
    n8.nl = 154410.0
    _due_now(st)
    scan(st)
    ck("A5 Lucid 150K: 오늘 손익 $4,409 = 그대로, $4,410(목표의 49%)에서 정리 + 오늘 표시",
       not _a5 and n8.closed == ["NQ 12-26"] and E.App._daycap_hit_today(st, "nt8", LU, NOW_DAY()), (_a5, n8.closed))
    ck("A6 넘어서 닿으면 '을 넘어'(이미 선을 넘었는데 '가까워'라고 하지 않음)",
       "을 넘어" in __import__("inspect").getsource(E.App._daycap_mon_fire)
       and "가까워" not in __import__("inspect").getsource(E.App._daycap_mon_fire))

    # ════════ B 통과 익절 = 실제로 통과하는 잔고 + $50 ════════
    r = E._daycap_resolve("projectx", P0, "150KTC-A", best=6000.0)
    r2 = E._daycap_resolve("nt8", dict(P0, stage="eval"), "LDI150A", best=5000.0)
    r3 = E._daycap_resolve("nt8", dict(P0, stage="eval"), "LPI150A", best=5000.0)
    r4 = E._daycap_resolve("projectx", P0, "150KTC-A", best=3000.0)
    ck("B1 공식: Topstep 150K 최고일 6,000 → 150,000 + 6,000 / 0.55 = 160,909.09, 익절 160,959.09 / Lucid 최고일 5,000 → "
       "150,000 + 2 x 5,000 = 160,000, 익절 160,050 / 최고일이 작으면 목표(159,050) / LucidPro(평가 일관성 없음) = 159,050",
       abs(r["pass_need"] - 160909.09) < 0.01 and abs(r["pass_tp"] - 160959.09) < 0.01 and r2["pass_need"] == 160000.0
       and r2["pass_tp"] == 160050.0 and r4["pass_tp"] == 159050.0 and r3["pass_tp"] == 159050.0 and r["margin"] == 50.0,
       (r, r2, r3, r4))
    # B2 Topstep 거래 기록에서 최고일: 이틀 전 3,000(두 건), 어제 6,000(수수료 50 - 최고일은 수수료 전으로 크게 잡는다), 취소 거래는 뺀다
    _hist = [(_past(2, 1), 1800.0, 20.0), (_past(2, 3), 1200.0, 20.0), (_past(1, 2), 6000.0, 50.0)]
    b = HistPX(accts=[{"id": 101, "name": SC, "balance": 160000.0}], realized={101: 0.0}, hist=_hist)
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    _h = E.App._dc_state(st)["hist"].get(K1) or {}
    ck("B2 잔고 160,000(규모 + 목표 넘음)인 Combine: 진입 때 거래 기록을 읽어(최고일 6,000) 통과 아님 → 진입 + 계획 로그 '통과 $160,959'",
       any(c[0] == "trades" for c in b.calls) and sum(1 for c in b.calls if c[0] == "place_entry") == 1
       and not E.App._dc_state(st)["passed"] and max((_h.get("days") or {}).values() or [0]) == 6000.0
       and any("통과 $160,959" in m for m in st.logs), (b.calls[:6], _h, st.logs[-3:]))
    b.marks = {GC: 2654.0}                                 # 미실현 800 → 잔고 + 미실현 160,800 < 160,959.09
    _due_now(st)
    scan(st)
    _b3 = list(_cl(b))
    b.marks = {GC: 2654.8}                                 # 미실현 960 → 160,960 ≥ 160,959.09
    _due_now(st)
    scan(st)
    ck("B3 감시: 160,800 = 그대로, 160,960 ≥ 160,959.09에서 정리 → 실현 잔고 160,960 ≥ 160,909.09 = 통과 표시",
       not _b3 and _cl(b) and K1 in E.App._dc_state(st)["passed"]
       and abs(float(E.App._dc_state(st)["tp_fired"][K1]["amt"]) - 160959.09) < 0.01, (_b3, _cl(b), st.logs[-4:]))
    ck("B3b 거래 기록은 거래일마다 한 번만 읽는다(감시 회차마다 다시 읽지 않음)", sum(1 for c in b.calls if c[0] == "trades") == 1,
       [c for c in b.calls if c[0] == "trades"])
    # B4 날마다 다시: 오늘 7,000을 번 날(회원이 상한을 끔)이 지나면 다음 거래일 통과 잔고 = 150,000 + 7,000 / 0.55 = 162,727.27
    st = _new_st(None)
    E.App._dc_ledger_note(st, "projectx", SC, NOW_DAY(), 7000.0)
    _rt = E.App._daycap_resolve_cfg(st, "projectx", SC, dict(P0, day_cap=0.0), name=SC, bal=152000.0, today=7000.0)
    _nx = lambda now_utc=None: _DAY0((now_utc or _dt.datetime.now(_dt.timezone.utc)) + _dt.timedelta(days=1))   # noqa: E731
    E._daycap_day_start = _nx
    try:
        _rn = E.App._daycap_resolve_cfg(st, "projectx", SC, dict(P0, day_cap=0.0), name=SC, bal=157000.0)
    finally:
        E._daycap_day_start = _DAY0
    ck("B4 날마다 다시: 오늘 손익 7,000이면 오늘도 다음 날도 162,727.27(+ $50) - 최고일은 오늘 손익까지 넣는다",
       abs(_rt["pass_need"] - 162727.27) < 0.01 and abs(_rn["pass_need"] - 162727.27) < 0.01 and _rn["best_src"] == "local",
       (_rt, _rn))
    # B5 같은 금액에서 두 번 안 닫음: 최고일 모름(목표 159,000 + 50), 정리했는데 미끄러짐 $100으로 158,960(통과 아님) → 다시 159,050이어도 안 닫음
    b = HistPX(accts=[{"id": 101, "name": SC, "balance": 157000.0}], realized={101: 0.0}, slip=0.5)
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2660.3}                                 # 미실현 2,060 → 159,060 ≥ 159,050
    _due_now(st)
    scan(st)
    _bal1 = b.accts[0]["balance"]
    ck("B5 159,060에서 정리, 미끄러짐으로 실현 158,960 < 159,000 = 통과 표시 없음 + 정리 금액 159,050 기록",
       len(_cl(b)) == 1 and abs(_bal1 - 158960.0) < 0.01 and K1 not in E.App._dc_state(st)["passed"]
       and E.App._dc_state(st)["tp_fired"][K1]["amt"] == 159050.0, (_cl(b), _bal1, st.logs[-3:]))
    b.pos[(101, GC)] = 2
    b.avgs[(101, GC)] = 2650.0
    _app_leg(st, b, SC, GC, 2, asset="GC")                 # 앱의 새 진입
    b.marks = {GC: 2650.6}                                 # 158,960 + 120 = 159,080 ≥ 159,050 - 같은 금액
    for _ in range(2):
        _due_now(st)
        scan(st)
    ck("B6 같은 금액(159,050)에 다시 닿아도 정리하지 않음 + 차분한 로그 한 번",
       len(_cl(b)) == 1 and sum(1 for m in st.logs if "같은 금액에서 다시 정리하지 않고" in m) == 1, (_cl(b), st.logs[-3:]))
    E.App._dc_ledger_note(st, "projectx", SC, "2026-01-05", 5500.0)      # 지난날 5,500이 원장에 있다(예: 거래 기록) → 160,000 + 50
    b.marks = {GC: 2655.5}                                 # 158,960 + 1,100 = 160,060 ≥ 160,050
    _due_now(st)
    scan(st)
    ck("B7 금액이 새로 계산돼 바뀌면(최고일 5,500 → 160,050) 그 금액에서 다시 정리",
       len(_cl(b)) == 2 and E.App._dc_state(st)["tp_fired"][K1]["amt"] == 160050.0, (_cl(b), st.logs[-3:]))
    b.pos.clear()
    b.accts[0]["balance"] = 150000.0                       # 초기화(새로 시작한 평가) - 지난 평가의 통과 익절 금액 기록은 놓는다
    _due_now(st)
    scan(st)
    ck("B7b 잔고가 규모 이하로 돌아오면(초기화) 통과 익절 금액 기록과 통과 표시를 놓는다(새 평가를 막지 않게)",
       K1 not in E.App._dc_state(st)["tp_fired"] and K1 not in E.App._dc_state(st)["passed"], E.App._dc_state(st)["tp_fired"])
    # B8 Lucid 최고일 = 앱 원장(NT8 실현): 지난날 5,000과 3,000(시작 잔고 158,000) → 통과 = 150,000 + 2 x 5,000 = 160,000, 익절 160,050.
    # 오늘 2,040이면 그대로, 2,060이면 정리(오늘이 최고일보다 작아 최고일 5,000이 묶는다). 감시는 오늘 실현(현금 - 시작 잔고)을 원장에 적는다.
    n8 = FakeNT8()
    st = _new_st(n8, [("NQ", LU, dict(P0, stage="eval"), "nt8")])
    _lucid_chain(st, [150000.0, 155000.0])                # 처음(150,000)부터 본 계좌: 이틀 전 5,000, 어제 3,000(오늘 시작 158,000)
    n8.cash = n8.nl = 158000.0
    n8.pos = {"NQ 12-26": 1}
    _app_leg(st, n8, LU, "NQ 12-26", 1)
    n8.nl = 160040.0
    scan(st)
    _b8 = list(n8.closed)
    n8.nl = 160060.0
    _due_now(st)
    scan(st)
    ck("B8 Lucid: 원장 최고일 5,000 → 통과 익절 160,050(160,040 그대로, 160,060 정리) + 통과 표시",
       not _b8 and n8.closed == ["NQ 12-26"] and "nt8|ldi150a1234" in E.App._dc_state(st)["passed"]
       and any("통과 익절 잔고($160,050" in m for m in st.logs), (_b8, n8.closed, st.logs[-3:]))
    _rl = E._daycap_resolve("nt8", dict(P0, stage="eval"), "LDI150A", best=10060.0)
    ck("B8b Lucid 오늘 하루가 최고일이면(10,060) 통과 = 150,000 + 2 x 10,060 = 170,120(오늘 손익까지 넣는다)",
       _rl["pass_need"] == 170120.0, _rl)
    n8 = FakeNT8()
    st = _new_st(n8, [("NQ", LU, dict(P0, stage="eval"), "nt8")])
    n8.execs = [{"acct": LU, "pnl": 700.0, "ts_ms": int(_DAY0()[0].timestamp() * 1000) + 60000}]
    n8.cash = n8.nl = 150700.0
    scan(st)
    ck("B9 Lucid 감시가 오늘 실현(현금 - 거래일 시작 잔고 = 700)을 원장에 적는다",
       (E.App._dc_state(st)["days"].get("nt8|ldi150a1234") or {}).get(NOW_DAY()) == 700.0, E.App._dc_state(st)["days"])
    # B10 최고일 모름: 거래 기록 없음(어댑터에 trades 없음) + 원장 없음 → 목표 기준 + 로그 한 번(진입, 감시 몇 번이어도)
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 151000.0}], realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2651.0}
    for _ in range(3):
        _due_now(st)
        scan(st)
    ck("B10 최고일 모름 = 통과 익절 159,050(목표 기준) + '하루 최고 이익을 몰라' 로그 한 번",
       sum(1 for m in st.logs if "지금까지 하루 최고 이익을 몰라 통과 익절을 이익 목표 기준($159,050)으로 잡습니다" in m) == 1
       and any("통과 $159,050" in m for m in st.logs), st.logs[-6:])
    # B11 거래 기록 읽기 실패 → 앱 원장으로(로그 한 번씩), 10분 뒤에만 다시 읽는다
    b = HistPX(accts=[{"id": 101, "name": SC, "balance": 151000.0}], realized={101: 0.0}, hist_err=RuntimeError("HTTP 500"))
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    E.App._dc_ledger_note(st, "projectx", SC, "2026-01-05", 2000.0)
    for _ in range(3):
        _due_now(st)
        scan(st)
    ck("B11 거래 기록 실패 = 로그 한 번 + 앱이 본 날로(원장 2,000) + 10분 안엔 다시 안 읽음",
       sum(1 for c in b.calls if c[0] == "trades") == 1 and sum(1 for m in st.logs if "거래 기록을 읽지 못해" in m) == 1
       and (E.App._daycap_resolve_cfg(st, "projectx", SC, P0, name=SC, bal=151000.0) or {}).get("best_src") == "local",
       (st.logs[-4:], [c for c in b.calls if c[0] == "trades"]))

    # ════════ C 감시 주기, 호출 한도 ════════
    b = FakePX(realized={101: 1000.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])               # 감시 기록 위험 = 1R x 배수 3,498
    _rec_risk = (st._dc_mon.get(K1) or {}).get("risk")
    b.marks = {GC: 2651.0, MGC: 2651.0}                    # 오늘 1,290, 상한까지 3,570 > max(25% x 3,860, 3,498)
    _due_now(st)
    _t0 = time.time()
    scan(st)
    _d1 = st._dc_due[K1] - _t0
    b.marks = {GC: 2652.0, MGC: 2652.0}                    # 오늘 1,580, 상한까지 3,280 ≤ 3,498
    _due_now(st)
    scan(st)
    _d2 = st._dc_due[K1] - _t0
    ck("C1 선 가까이(남은 거리 ≤ max(남은 한도의 25%, 1R x 배수 3,498)) = 1초, 아니면 포지션 있을 때 5초",
       _rec_risk == 3498.0 and abs(_d1 - 5.0) < 1e-6 and abs(_d2 - 1.0) < 1e-6, (_rec_risk, _d1, _d2))
    st._dc_mon[K1]["risk"] = 100.0                         # 위험이 작으면 25% 규칙: 남은 한도 3,860의 25% = 965
    b.marks = {GC: 2659.8, MGC: 2659.8}                    # 오늘 3,842, 남은 1,018 > 965
    _due_now(st)
    scan(st)
    _d3 = st._dc_due[K1] - _t0
    b.marks = {GC: 2660.0, MGC: 2660.0}                    # 오늘 3,900, 남은 960 ≤ 965
    _due_now(st)
    scan(st)
    _d4 = st._dc_due[K1] - _t0
    ck("C2 남은 한도의 25% 규칙(위험이 작을 때): 1,018 남음 = 5초, 960 남음 = 1초", abs(_d3 - 5.0) < 1e-6 and abs(_d4 - 1.0) < 1e-6,
       (_d3, _d4))
    b.pos.clear()
    st._dc_mon.clear()
    _due_now(st)
    scan(st)
    _d5 = st._dc_due[K1] - _t0
    ck("C3 포지션 없고 앱 진입 기록 없음 = 30초", abs(_d5 - 30.0) < 1e-6, _d5)
    _aft = []
    st.root = SimpleNamespace(after=lambda ms, fn=None, *a: _aft.append(ms))
    _thr = E.threading.Thread
    E.threading.Thread = lambda *a, **k: SimpleNamespace(start=lambda: None)
    try:
        st._dc_busy = False
        E.App._daycap_tick(st)
    finally:
        E.threading.Thread = _thr
    ck("C4 감시 틱 = 1초마다(root.after 1000)", _aft == [1000], _aft)
    # C5 같은 로그인의 Combine 둘이 GC 2 + MGC 9씩: 한 회차 시세 읽기 1번(계좌마다, leg마다 아님), 계좌 목록 1번, 포지션은 계좌마다 1번
    _accs = [{"id": 101, "name": SC, "balance": 151000.0}, {"id": 102, "name": "150KTC-V2-2", "balance": 151000.0}]
    b = FakePX(accts=_accs, realized={101: 1000.0, 102: 1000.0})
    st = _new_st(b, [("GC", SC, P0, "projectx"), ("GC", "150KTC-V2-2", P0, "projectx")])
    for _a in (101, 102):
        for _c, _q in ((GC, 2), (MGC, 9)):
            b.pos[(_a, _c)] = _q
            b.avgs[(_a, _c)] = 2650.0
    b.marks = {GC: 2652.0, MGC: 2652.0}
    b.calls.clear()
    scan(st)
    _cnt = lambda n: sum(1 for c in b.calls if c[0] == n)   # noqa: E731
    _c5 = (_cnt("current_market_price"), _cnt("_accounts"), _cnt("open_positions"))
    st._dc_due = {}
    b.calls.clear()
    scan(st)                                               # 같은 1초 안의 다음 회차 = 다시 읽지 않음
    _c5b = (_cnt("current_market_price"), _cnt("_accounts"), _cnt("open_positions"))
    ck("C5 시세는 로그인, 자산마다 1초에 한 번(계좌 둘 x leg 둘인데 1번), 계좌 목록 1번, 포지션 계좌마다 1번; 1초 안 다음 회차는 다시 안 읽음",
       _c5 == (1, 1, 2) and _c5b == (0, 0, 0), (_c5, _c5b))
    # C6 2분 동안 1초마다(두 계좌 모두 1초 구간): 감시 호출이 문서 한도 아래(시세 30초 50회, 그 밖 60초 200회) - 감시 몫 40 / 120 이하
    b.marks = {GC: 2655.0, MGC: 2655.0}                    # 오늘 2,450 - 1초 구간
    b.calls.clear()
    _ts = []
    for _i in range(120):
        _clk["t"] += 1.0
        st._dc_due = {}
        _n0 = len(b.calls)
        scan(st)
        _ts.extend((_clk["t"], c[0]) for c in b.calls[_n0:])

    def _maxwin(kinds, win):
        _xs = [t for t, n in _ts if n in kinds]
        return max([sum(1 for y in _xs if x <= y < x + win) for x in _xs] or [0])
    _bars = _maxwin({"current_market_price"}, 30.0)
    _oth = _maxwin({"_accounts", "open_positions", "day_realized", "trades"}, 60.0)
    ck("C6 1초 감시 2분: 시세 30초 최대 {0}회 ≤ 40, 그 밖 60초 최대 {1}회 ≤ 120(문서 한도 50 / 200 아래)".format(_bars, _oth),
       0 < _bars <= 40 and 0 < _oth <= 120 and not _cl(b), (_bars, _oth))
    # C6b 거래 기록(긴 조회)은 1초 구간에서는 읽지 않고, 포지션이 없거나 1초 구간 밖일 때 읽는다
    b = HistPX(accts=[{"id": 101, "name": SC, "balance": 151000.0}], realized={101: 1000.0}, hist=[(_past(1, 2), 500.0, 5.0)])
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    st._dc_fastk = {K1: True}
    b.pos[(101, GC)], b.avgs[(101, GC)] = 2, 2650.0
    b.marks = {GC: 2665.0}                                 # 오늘 4,000 - 1초 구간
    scan(st)
    _h0 = sum(1 for c in b.calls if c[0] == "trades")
    b.pos.clear()
    _due_now(st)
    scan(st)
    ck("C6b 1초 구간에서는 거래 기록을 읽지 않고(0), 포지션이 닫힌 뒤 읽는다(1)",
       _h0 == 0 and sum(1 for c in b.calls if c[0] == "trades") == 1, (_h0, [c for c in b.calls if c[0] == "trades"]))
    _g = E._DcGate()
    _g.note_error(RuntimeError("429 Client Error: Too Many Requests"))
    _p429 = _g.take("other")
    _clk["t"] += 11
    ck("C7 429를 받으면 10초 쉰다(그 뒤 다시)", _p429 is False and _g.take("other") is True and _g.take("bars", force=True) is True)

    # ════════ D 계좌 한도 ════════
    L = E._size_limit
    ck("D1 Topstep Combine 한도(마이크로): 50K 50, 100K 100, 150K 150 / PRAC, 모르는 이름, IBKR = 줄이지 않음",
       (L("projectx", "50KTC-V2-1")[0], L("projectx", "100KTC-V2-1")[0], L("projectx", "150KTC-V2-1")[0],
        L("projectx", "PRAC-V2-1")[0], L("projectx", "12345")[0], L("ibkr", "150KTC-V2-1")[0]) == (50, 100, 150, 0, 0, 0))
    _xfa = {(sz, bal): L("projectx", "EXPRESS-V2-1", bal, sz)[0] for sz in (0, 50000, 100000, 150000)
            for bal in (0.0, 1499.0, 1500.0, 2000.0, 3000.0, 4500.0)}
    _xexp = {}
    for _sz, _row in ((0, (20, 20, 30, 50, 50, 50)), (150000, (30, 30, 40, 50, 100, 150)),
                      (100000, (30, 30, 40, 50, 100, 100)), (50000, (20, 20, 30, 50, 50, 50))):
        for _bal, _v in zip((0.0, 1499.0, 1500.0, 2000.0, 3000.0, 4500.0), _row):
            _xexp[(_sz, _bal)] = _v
    ck("D2 XFA Scaling Plan(잔고 단계, 규모 모르면 가장 엄한 50K 줄 - 3차 리뷰 P2): 150K 3/3/4/5/10/15 미니, 100K 3/3/4/5/10/10, 50K 2/2/3/5/5/5", _xfa == _xexp,
       {k: (v, _xexp[k]) for k, v in _xfa.items() if v != _xexp[k]})
    ck("D3 Lucid 100 마이크로(NT8, Tradovate의 Lucid 이름), Lucid 아닌 NT8 = 줄이지 않음",
       (L("nt8", "LDI150A1")[0], L("tradovate", "LFE050B2")[0], L("nt8", "Sim101")[0]) == (100, 100, 0))
    C = E._clamp_legs
    ck("D4 미니/마이크로 나눔이 한도를 지킨다: 157 → 15 미니, 25 마이크로 → 2 미니 + 2 마이크로(22), 한도 8 → 마이크로 8, 한도 안이면 그대로",
       C([("GC", 15), ("MGC", 7)], 150) == ([("GC", 15)], 157, 150)
       and C([("MGC", 25)], 22) == ([("GC", 2), ("MGC", 2)], 25, 22)
       and C([("MGC", 25)], 8) == ([("MGC", 8)], 25, 8)
       and C([("NQ", 10), ("MNQ", 5)], 100) == ([("NQ", 10)], 105, 100)
       and C([("GC", 1), ("MGC", 9)], 150) == ([("GC", 1), ("MGC", 9)], 19, 19))
    # D5 진입 경로: 150K Combine에 GC 20(200 마이크로) → 15 + 로그 한 줄 + 손절 위험도 같은 비율
    b = FakePX(realized={101: 0.0})
    st = _new_st(b, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 20)], cap=dict(CAP, risk=4000.0))
    _sz = [c[1]["size"] for c in b.calls if c[0] == "place_entry"]
    ck("D5 Combine 150K: 계획 GC 20 → 15 미니만 진입 + '계좌 한도에 맞춰 줄였습니다' 한 줄(경보 없음) + 감시 위험 4,000 x 150/200 = 3,000",
       _sz == [15] and sum(1 for m in st.logs if "계좌 한도에 맞춰 줄였습니다: 마이크로 환산 200계약 → 150계약" in m) == 1
       and not st.alerts and (st._dc_mon.get(K1) or {}).get("risk") == 3000.0, (_sz, st.logs[-6:], st._dc_mon.get(K1)))
    st.lang = "en"
    b = FakePX(accts=[{"id": 201, "name": "EXPRESS-V2-DLL-1", "balance": 1400.0}], realized={201: 0.0})
    st = _new_st(b)
    st.lang = "en"
    run_entry(st, b, [("GC", 4), ("MGC", 5)], sc="EXPRESS-V2-DLL-1", cap=dict(CAP, sc="EXPRESS-V2-DLL-1"))
    _sz1 = [(c[1]["contract_id"], c[1]["size"]) for c in b.calls if c[0] == "place_entry"]
    b.accts[0]["balance"] = 2100.0                         # 같은 거래일 장중에 잔고가 2,000 단계를 넘어도 그날은 3 미니
    b.pos.clear()
    b.calls.clear()
    run_entry(st, b, [("GC", 4), ("MGC", 5)], sc="EXPRESS-V2-DLL-1", cap=dict(CAP, sc="EXPRESS-V2-DLL-1"))
    _sz2 = [(c[1]["contract_id"], c[1]["size"]) for c in b.calls if c[0] == "place_entry"]
    ck("D6 XFA(규모 모름 = 50K 줄) 잔고 1,400 = 2 미니(45 → 20), 장중에 2,100이 돼도 그날은 처음 본 잔고 단계(20) + EN 로그 쌍",
       _sz1 == [(f"CON.F.US.GC.Z26", 2)] and _sz2 == [(f"CON.F.US.GC.Z26", 2)]
       and any("Reduced to the account limit: 45 → 20 micro-equivalent contracts" in m for m in st.logs), (_sz1, _sz2, st.logs[-3:]))
    E._daycap_day_start = _nx
    try:
        b.pos.clear()
        b.calls.clear()
        run_entry(st, b, [("GC", 4), ("MGC", 5)], sc="EXPRESS-V2-DLL-1", cap=dict(CAP, sc="EXPRESS-V2-DLL-1"))
    finally:
        E._daycap_day_start = _DAY0
    _sz3 = [(c[1]["contract_id"], c[1]["size"]) for c in b.calls if c[0] == "place_entry"]
    ck("D7 다음 거래일 잔고 2,100 = 5 미니 단계(45 그대로 - GC 4 + MGC 5)", _sz3 == [(GC, 4), (MGC, 5)], _sz3)

    class FakeNT8E(FakeNT8):
        def _accounts(self):
            return [{"id": self.acct, "name": self.acct}]

        def place_entry(self, **k):
            self.calls.append(("place_entry", dict(k)))
            return {"dry_run": True}

    n8 = FakeNT8E()
    st = _new_st(n8)
    E.App._run_futures_entry(st, n8, LU, [("NQ", 12), ("MNQ", 3)], "LONG", 20900.0, SIG, False, _dt.datetime.now(), "NQ", None)
    _nsz = [(c[1]["contract_id"], c[1]["size"]) for c in n8.calls if c[0] == "place_entry"]
    ck("D8 Lucid(NT8) NQ 12 + MNQ 3(123) → 10 미니(킬스위치와 상관없이, cap 없이도) + 로그",
       [s for _c, s in _nsz] == [10] and any("계좌 한도에 맞춰 줄였습니다: 마이크로 환산 123계약 → 100계약" in m for m in st.logs),
       (_nsz, st.logs[-4:]))
    b = FakePX(accts=[{"id": 301, "name": "PRAC-V2-1", "balance": 150000.0}], realized={301: 0.0})
    st = _new_st(b)
    run_entry(st, b, [("GC", 20)], sc="PRAC-V2-1", cap=dict(CAP, sc="PRAC-V2-1"))
    ck("D9 연습 계좌는 줄이지 않는다(GC 20 그대로, 로그 없음)", [c[1]["size"] for c in b.calls if c[0] == "place_entry"] == [20]
       and not any("계좌 한도" in m for m in st.logs))

    # ════════ E 그림자 모드 ════════
    os.environ["EQ_DAYCAP_SHADOW"] = "1"
    try:
        b = FakePX(realized={101: 1000.0})
        st = _new_st(b, [("GC", SC, P0, "projectx")])
        run_entry(st, b, [("GC", 2), ("MGC", 9)])
        _o0 = len(b.orders)
        b.marks = {GC: 2663.5, MGC: 2663.5}                # 오늘 4,915 ≥ 4,860
        for _ in range(3):
            _due_now(st)
            scan(st)
        ck("E1 그림자 모드: 닿아도 주문 0(청산, 주문 취소 없음), 오늘 표시 없음, 포지션 그대로",
           not _cl(b) and not any(c[0] == "_cancel_order" for c in b.calls) and len(b.orders) == _o0
           and b.pos.get((101, GC)) == 2 and not E.App._daycap_hit_today(st, "projectx", SC, NOW_DAY()), b.calls[-5:])
        ck("E2 '정리했을 경우' 로그 한 번 + 정확한 금액(오늘 손익 $4,915.00, 상한 $4,860.00) + 주문 안 냈다는 말",
           sum(1 for m in st.logs if "정리했을 경우: 오늘 손익 $4,915.00가 하루 이익 상한($4,860.00)을 넘어" in m
               and "그림자 모드라 주문은 내지 않았습니다" in m) == 1, [m for m in st.logs if "그림자" in m or "정리했을" in m])
        ck("E3 진입 계획 로그가 그림자 모드라고 말한다", any("그림자 모드라 정리 주문은 내지 않고" in m for m in st.logs))
    finally:
        os.environ.pop("EQ_DAYCAP_SHADOW", None)
    _off = E._daycap_shadow()
    with open(E.CFG_PATH, "w", encoding="utf-8") as f:
        f.write("lang: ko\ndaycap_shadow: true\n")
    _on = E._daycap_shadow()
    time.sleep(0.01)
    with open(E.CFG_PATH, "w", encoding="utf-8") as f:
        f.write("lang: ko\n")
    os.utime(E.CFG_PATH, (_real_time() + 5, _real_time() + 5))
    _off2 = E._daycap_shadow()
    ck("E4 기본 꺼짐, config.yaml `daycap_shadow: true` 한 줄이면 켜짐, 지우면 꺼짐", _off is False and _on is True and _off2 is False,
       (_off, _on, _off2))
    with open(E.CFG_PATH, "w", encoding="utf-8") as f:
        f.write("lang: ko\ndaycap_shadow: true\n")
    E._save_full("ko", "tok-x", {})                       # 앱이 설정을 저장해도(회원이 무엇이든 저장) 숨은 키는 남는다
    import yaml as _y
    with open(E.CFG_PATH, encoding="utf-8") as f:
        _saved = _y.safe_load(f) or {}
    os.utime(E.CFG_PATH, (_real_time() + 9, _real_time() + 9))
    ck("E5 앱이 config.yaml을 다시 저장해도 daycap_shadow 키는 남고 그대로 켜져 있다",
       _saved.get("daycap_shadow") is True and _saved.get("token") == "tok-x" and E._daycap_shadow() is True, _saved)
    os.remove(E.CFG_PATH)

    # ════════ F Operator 평가 계좌 + 회원 통과 목표 ════════
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 158900.0}], realized={101: 0.0})
    st = _new_st(b)
    _pr_op = dict(P0, on=True, type="test", pass_tp=159000.0)
    st._acfg = {"GC": {"broker": "projectx", "accounts": [{"id": SC, "prop": _pr_op, "on": True, "broker": "projectx"}]}}
    st._accts_of = lambda a: st._acfg[a]["accounts"]
    st._sig_accts = {("GC", 0): {"broker": "projectx", "acct": SC, "live": True, "label": "OP", "f1": "u@x", "f2": "k", "f3": ""}}
    st.root = SimpleNamespace(after=lambda *a, **k: None)   # 통과 익절 완료 팝업(messagebox)은 띄우지 않는다
    st._passtp_done, st._passtp_fired, st._passtp_warned, st._passtp_note = {}, set(), set(), {}
    st._passtp_nlprobe, st._passtp_failwarn = set(), set()
    st._watch_only = True
    _tgo = E.App._passtp_targets(st)
    st._watch_only = False
    _tga = E.App._passtp_targets(st)
    ck("F1 Operator(확인 모드): 평가 Combine에 회원이 넣은 통과 목표를 종전 통과 익절 감시가 본다, Autopilot이면 평가 감시가 맡아 뺀다",
       [t[3] for t in _tgo] == [159000.0] and _tga == [], (_tgo, _tga))
    st._watch_only = True
    b.pos[(101, GC)] = 2
    b.avgs[(101, GC)] = 2650.0
    b.marks = {GC: 2651.0}                                 # 158,900 + 200 = 159,100 ≥ 159,000 + 버퍼 80
    E.App._passtp_scan(st)
    ck("F2 Operator 평가 계좌가 회원 통과 목표에 닿으면 12e처럼 정리(평가 감시는 이 계좌를 보지 않음)",
       sorted(c[2] for c in _cl(b)) == [GC] and E.App._daycap_mon_targets(st) == [], (_cl(b), st.logs[-3:]))

    # ════════ G 권장 1R 표 ════════
    ck("G1 권장 1R 표 하나(_RECO_1R): 150K Topstep, Lucid 평가 2,200 / 펀디드 450, 다른 규모 없음",
       E._RECO_1R == {"projectx": {"eval": {150000: 2200.0}, "funded": {150000: 450.0}},
                      "nt8": {"eval": {150000: 2200.0}, "funded": {150000: 450.0}}}
       and E._reco_one_r("projectx", "eval", 50000) is None and E._reco_one_r("nt8", "passed", 150000) == 450.0)

    # ════════ H 문구 ════════
    ck("H1 금액: -$120(종전 '$-120'), $0, -$0.4 → $0, 센트", (E._money(-120), E._money(0), E._money(-0.4), E._money(4915.0, True),
                                                        E.App._daycap_money(Stub(None), -120.0))
       == ("-$120", "$0", "$0", "$4,915.00", "-$120"))
    b = FakePX(realized={101: 4950.0})
    st = _new_st(b)
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    ck("H2 건너뛰는 계좌는 '분할 진입' 줄을 찍지 않는다(건너뜀 로그만)",
       st.skips == [("GC", "day_cap")] and not any("분할 진입" in m for m in st.logs), st.logs)
    b = FakePX(realized={101: 0.0})
    st = _new_st(b)
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    _ix = [i for i, m in enumerate(st.logs) if "분할 진입" in m or "평가 계좌 익절(앱 감시)" in m]
    ck("H3 진입하는 계좌는 계획 로그 뒤에 '분할 진입'", len(_ix) == 2 and "평가 계좌 익절" in st.logs[_ix[0]], [st.logs[i] for i in _ix])
    _src = inspect.getsource(E)
    _new_fns = ("_daycap_mon_one", "_daycap_mon_fire", "_size_clamp", "_prop_reco_apply", "_prop_reco_desc", "_dc_hist_refresh",
                "_dc_best_note", "_daycap_plan", "_lucid_rule_text", "_reco_prefill_row", "_one_r_window")
    _txt = "".join(inspect.getsource(getattr(E.App, n)) for n in _new_fns) + inspect.getsource(E._size_limit)
    import re as _re
    _strs = [s for s in _re.findall(r'"([^"\n]*[가-힣][^"\n]*)"', _txt)]
    _bad = [s for s in _strs if "·" in s or _re.search(r"[\U0001F300-\U0001FAFF☀-➿]", s)
            or _re.search(r"(요|어요|에요|해요)[.)]?$", s.strip())]
    ck("H4 3차 새 문구: 가운뎃점, 이모지, 해요체 없음(합니다체)", not _bad, _bad)

    # ════════ I 서버 값 ════════
    E._DAYCAP_SRV = {}
    try:
        os.remove(E._PROP_RECO_PATH)
    except OSError:
        pass
    _raw = {"cap_pct": {"projectx": 0.53, "nt8": 0.9, "tradovate": 0.5},
            "targets": {"nt8": {"150000": 8000, "75000": 5000, "50000": 50000}, "projectx": {"150000": "abc"}},
            "pass_margin": 75,
            "reco_1r": {"projectx": {"eval": {"150000": 1900, "50000": 1000, "100000": 50}, "live": {"150000": 300},
                                     "funded": {"150000": True}}}}
    _v = E._prop_reco_validate(_raw)
    ck("I1 검증: 범위 안 칸만(상한 0.53, Lucid 150K 목표 8,000, 여유 75, 권장 1R 1,900/1,000), 범위 밖, 모르는 브로커, 단계, 규모, 글자, 참거짓은 버림",
       _v == {"cap_pct": {"projectx": 0.53}, "targets": {"nt8": {150000: 8000.0}}, "pass_margin": 75.0,
              "reco_1r": {"projectx": {"eval": {150000: 1900.0, 50000: 1000.0}}}}, _v)
    # 3차 리뷰 P1: 핵심 값(상한 비율, 이익 목표)이 회사, 규모별 범위를 벗어난 서버 값(_raw: Lucid 0.9, 50K 목표 50,000, 글자)은 통째로 쓰지 않는다.
    # 정상 핵심 값 + 권장 1R, 단계 등 그 밖 칸의 잘못은 그 칸만 버린다(_raw_ok).
    _raw_ok = {"cap_pct": {"projectx": 0.53, "tradovate": 0.5},
               "targets": {"nt8": {"150000": 8000, "75000": 5000}},
               "pass_margin": 75,
               "reco_1r": {"projectx": {"eval": {"150000": 1900, "50000": 1000, "100000": 50}, "live": {"150000": 300},
                                        "funded": {"150000": True}}}}
    _v_ok = E._prop_reco_validate(_raw_ok)
    st = Stub(None)
    st._acfg = {"GC": {"accounts": [{"id": SC, "one_r": 500.0, "prop": dict(P0), "broker": "projectx"}]}}
    E.App._prop_reco_apply(st, _raw_ok)
    _r1 = E._daycap_resolve("projectx", P0, "150KTC-A")
    _r2 = E._daycap_resolve("nt8", dict(P0, stage="eval"), "LDI150A")
    ck("I2 서버 값이 상한, 통과 익절, 권장 1R 계산을 바꾼다: Topstep 9,000 x 0.53 = 4,770 + 159,075, Lucid 목표 8,000 x 0.49 = 3,920 + "
       "158,075(Lucid 비율은 안 보냄 = 내장값), 권장 1R 150K 평가 1,900, 50K 1,000, 펀디드는 내장 450",
       (_r1["cap"], _r1["pass_tp"], _r2["cap"], _r2["pass_tp"], E._reco_one_r("projectx", "eval", 150000),
        E._reco_one_r("projectx", "eval", 50000), E._reco_one_r("projectx", "funded", 150000))
       == (4770.0, 159075.0, 3920.0, 158075.0, 1900.0, 1000.0, 450.0), (_r1, _r2))
    ck("I3 받은 값은 로그 한 번(값 목록, '회원이 저장한 1R은 바뀌지 않습니다') + 버린 칸 안내 한 번, 회원 1R은 그대로(저장 없음)",
       sum(1 for m in st.logs if "서버 권장 값을 받았습니다" in m and "회원이 저장한 1R은 바뀌지 않습니다" in m
           and "Topstep 목표의 53.0%" in m) == 1
       and sum(1 for m in st.logs if "범위를 벗어나거나 읽을 수 없어 앱에 들어 있는 값을 씁니다" in m) == 1
       and st._acfg["GC"]["accounts"][0]["one_r"] == 500.0 and st.saved == 0, st.logs)
    _n_logs = len(st.logs)
    E.App._prop_reco_apply(st, _raw)
    ck("I3b 핵심 값이 범위 밖인 서버 값(Lucid 비율 0.9, 50K 목표 50,000, 글자 목표) = 통째로 쓰지 않고 마지막 정상 값 그대로 + 로그 한 번",
       E._DAYCAP_SRV == _v_ok and E._dc_pct("projectx") == 0.53
       and sum(1 for m in st.logs[_n_logs:] if "허용 범위를 벗어나" in m and "마지막으로 받은 정상 값을 그대로" in m) == 1,
       (E._DAYCAP_SRV, st.logs[_n_logs:]))
    _n_logs = len(st.logs)
    E.App._prop_reco_apply(st, _raw_ok)
    E.App._prop_reco_apply(st, _raw)
    ck("I4 같은 값이 다시 오면 로그 없음(정상 값, 거절한 값 모두)", len(st.logs) == _n_logs, st.logs[_n_logs:])
    _disk = E._load_prop_reco()
    E._DAYCAP_SRV = {}
    E._prop_reco_boot()
    ck("I5 마지막 정상 값은 디스크에 두고 다음 기동(첫 하트비트 전)에 쓴다", _disk == _v_ok and E._DAYCAP_SRV == _v_ok, (_disk, E._DAYCAP_SRV))
    _n_logs = len(st.logs)
    E.App._prop_reco_apply(st, None)
    E.App._prop_reco_apply(st, {})
    ck("I6a 서버가 아무것도 안 보내면(파일 없음 - 인계된 보조서버, 깨진 파일) 마지막 정상 값 그대로 + 디스크도 그대로 + 로그 없음(3차 마무리 리뷰)",
       E._DAYCAP_SRV == _v_ok and E._load_prop_reco() == _v_ok and E._dc_pct("projectx") == 0.53 and len(st.logs) == _n_logs,
       (E._DAYCAP_SRV, st.logs[_n_logs:]))
    E.App._prop_reco_apply(st, {"reset": True})
    ck("I6b 서버가 {reset: true}를 보낼 때만 내장값으로 돌아가고 로그 한 번 + 디스크도 비움",
       E._DAYCAP_SRV == {} and E._load_prop_reco() == {} and E._dc_pct("projectx") == 0.54
       and sum(1 for m in st.logs[_n_logs:] if "서버가 권장 값을 앱에 들어 있는 값으로 되돌렸습니다" in m) == 1, st.logs[_n_logs:])
    # I7 하트비트 경로: autopilot.prop_reco를 그대로 받아 적용(가짜 requests, 복호화)
    import autopilot_crypto as _acm
    import requests as _rqm
    _hb = {"ok": True, "tier": "autopilot", "aead": False, "exp": _real_time() + 600,
           "autopilot": {"enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True}, "brokers": {},
                         "p150": None, "prop_reco": {"cap_pct": {"nt8": 0.48}, "pass_margin": 60}}}
    _og, _od, _ot = _rqm.get, _acm.decrypt, E.threading.Thread
    _rqm.get = lambda *a, **k: SimpleNamespace(ok=True, status_code=200, text="x", headers={})
    _acm.decrypt = lambda tok, txt: dict(_hb)

    class _SyncT:
        def __init__(self, target=None, daemon=None, **k):
            self.t = target

        def start(self):
            self.t()
    E.threading.Thread = _SyncT
    st = Stub(None)
    st.token_e = SimpleNamespace(get=lambda: "tok")
    st.root = SimpleNamespace(after=lambda *a, **k: None)
    try:
        E.App._heartbeat(st)
    finally:
        _rqm.get, _acm.decrypt, E.threading.Thread = _og, _od, _ot
    ck("I7 하트비트 게이트가 prop_reco를 받아 적용(Lucid 상한 비율 0.48, 여유 $60) - p150과 같은 길",
       (st._gate or {}).get("prop_reco") == {"cap_pct": {"nt8": 0.48}, "pass_margin": 60}
       and E._dc_pct("nt8") == 0.48 and E._dc_margin() == 60.0 and E._dc_cap_amount("nt8", 9000.0) == 4320.0,
       (getattr(st, "_gate", None), E._DAYCAP_SRV))
    E.App._prop_reco_apply(st, {"reset": True})
finally:
    time.sleep, time.time = _real_sleep, _real_time
    E._daycap_day_start = _DAY0

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
