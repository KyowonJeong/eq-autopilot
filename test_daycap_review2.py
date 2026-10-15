#!/usr/bin/env python3
# 평가 계좌 익절 2차 리뷰 수리(2026-10-14, 앱 2026.10.14a) 오프라인 시험 - 리뷰 소견마다 그 실패를 다시 일으키는 시험. 0 = 통과.
#   R1 (P0) 정리 뒤 손절 취소 실패 + 재시작, [전체 정지], 청산 직후 앱 종료에도 남은 손절을 걷는다(디스크 'fired' 기록)
#   R2 (P1) 같은 회차에 닿은 여러 계좌를 거의 동시에 정리(뒤 계좌가 앞 계좌 확인을 기다리지 않음), 시세는 로그인, 자산마다 1초에 한 번
#   R3 (P1) 남은 래치가 새 포지션을 닫지 않는다(다음 날, [전체 정지], [라이브 시작], 플랫 뒤 다시 연 포지션), 멈춘 래치는 보고를 막지 않음
#   R4 (P0, P1, P2) Lucid 단계 답은 막 고른 새 행(그 행 객체)의 1R만 채운다 - 기존 행 1R은 그대로
#   R5 (P1) Operator(확인 모드) 회원의 손 포지션을 닫지 않고 상태 줄도 없음
#   R6 (P1 일부) 통과 표시 문구: 멈추는 것 + 아직 통과 전일 때 푸는 길, 건너뜀 보고 pass_reached 한 번 뒤 pass_done
#   R7 (P2) 건너뜀 규칙 = 정리선까지 남은 한도   R8 (P2) 감시 계좌 목록 짧은 시간 제한, 일시 오류 뒤 5초
#   R9 (P2) 마무리의 주문 걷기는 정리한 계약 + 플랫인 계약만, 정리 중엔 새 진입 안 함
#   R10 (P2) 실현 손익을 못 읽으면 한 번 알리고 상태 줄 '모름'
#   R11 (P1) Lucid 단계 답은 계좌에 붙는다(행의 계좌를 바꾸면 다시 묻고 평가 설정은 자동으로)
#   R12 (P1) 계좌 이름의 규모가 저장된 규모를 이긴다   R13 (P2) 띠 밖으로 통과를 넘긴 계좌도 통과
#   R16 (P1) LucidPro, LucidFlex는 자동 하루 상한 없음(문구도)   R17, R18 (P2) Lucid 아닌 NT8, 펀디드 답과 옛 통과 목표
#   T 실제 Tk: 프롭 창 자동값은 평가에만, Lucid 아닌 NT8 창, LucidPro 문구, 질문 창 1R 안내와 닫기
# HOME, APPDATA = 임시 폴더, 네트워크 차단, 브로커는 전부 가짜, EQ_NO_ALERT=1.
import datetime as _dt
import inspect
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_dcr2_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_DAYCAP_OFF", None)
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



LU = "LDI150A1234"
K1 = "projectx|150ktc-v2-1"


def _day():
    return _DAY0()[1]


def _left(b, aid=101):
    return sorted(o["contractId"] for o in b.orders if o["aid"] == aid)


def _setup_fire(realized=2000.0):
    """150K Combine, 앱 진입 GC 2 + MGC 9(손절 둘), 시세 2660 = 오늘 2,000 + 미실현 2,900 = 4,900 ≥ 문턱 4,636."""
    _reset_state()
    b = FakePX(realized={101: realized})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    b.marks = {GC: 2660.0, MGC: 2660.0}
    return b, st


_orig_cancel = FakePX._cancel_order
_fail = {"on": False}


def _cancel_maybe(self, aid, oid):
    if _fail["on"]:
        self.calls.append(("_cancel_FAIL", int(aid), oid))
        raise RuntimeError("HTTP 503")
    return _orig_cancel(self, aid, oid)


FakePX._cancel_order = _cancel_maybe
try:
    # ── R1 (P0) 고아 손절: 정리 뒤 손절 취소가 실패해도 재시작, [전체 정지], 정리 도중 종료를 넘어 남은 손절을 걷는다 ─────────────
    b, st = _setup_fire()
    _fail["on"] = True
    _due_now(st)
    scan(st)
    _rec = E._load_daycap_mon().get(K1) or {}
    ck("R1a 정리 뒤 손절 취소 실패 = 포지션은 플랫, 손절 둘 남음, 디스크 기록에 정리 중('fired', done, 발동 계약)이 남는다",
       not any(b.pos.values()) and _left(b) == sorted([GC, MGC]) and (_rec.get("fired") or {}).get("done") is True
       and (_rec.get("fired") or {}).get("pos") == {GC: 2, MGC: 9}, _rec)
    _fail["on"] = False
    st2 = Stub(b)                                         # 재시작: 새 앱 객체, 메모리 비움, 디스크만
    st2._acfg, st2._dc_mon = st._acfg, E._load_daycap_mon()
    st2._sig_accts, st2._sig_on = {}, False
    _due_now(st2)
    scan(st2)
    ck("R1b 재시작 뒤 첫 회차가 남은 손절 둘을 걷고 기록을 놓는다(표시, 보고는 다시 안 함)",
       _left(b) == [] and not E._load_daycap_mon() and not st2.evs and not getattr(st2, "_dc_fired", {}),
       (b.orders, E._load_daycap_mon(), st2.evs))
    b, st = _setup_fire()
    _fail["on"] = True
    _due_now(st)
    scan(st)
    _fail["on"] = False
    st._sig_accts.clear()
    st._sig_on = False
    E.App._daycap_mon_clear(st)                           # [전체 정지]
    ck("R1c [전체 정지]는 정리 중 기록을 지우지 않고 포지션 정리만 멈춘다(halt)",
       "fired" in (E._load_daycap_mon().get(K1) or {}) and st._dc_fired[K1]["halt"] is True)
    _due_now(st)
    scan(st)
    ck("R1d [전체 정지] 뒤에도 감시가 남은 손절을 걷고 래치, 기록을 놓는다", _left(b) == [] and not E._load_daycap_mon()
       and not st._dc_fired, (b.orders, st._dc_fired))
    _src_stop, _src_start = inspect.getsource(E.App._master_stop), inspect.getsource(E.App._master_start)
    ck("R1e 배선: [전체 정지] = _daycap_mon_clear(멈춤 + 정리 중 기록 유지), [라이브 시작] = _daycap_latch_halt_all",
       "self._daycap_mon_clear()" in _src_stop and "self._daycap_latch_halt_all()" in _src_start
       and "self._daycap_latch_halt_all()" in inspect.getsource(E.App._daycap_mon_clear))
    b, st = _setup_fire()
    _oc = FakePX.close_contract

    def _close_then_die(self, aid, con):
        _oc(self, aid, con)
        if not any(self.pos.values()):
            raise SystemExit("app killed")                # 청산은 나갔고 확인, 손절 정리 전에 앱이 죽는다
    FakePX.close_contract = _close_then_die
    try:
        _due_now(st)
        try:
            scan(st)
        except SystemExit:
            pass
    finally:
        FakePX.close_contract = _oc
    _rec = E._load_daycap_mon().get(K1) or {}
    ck("R1f 청산 직후 앱이 죽어도 디스크에 정리 중 기록(발동 때 먼저 적음, 아직 done 아님)",
       not any(b.pos.values()) and (_rec.get("fired") or {}).get("done") is False and _left(b) == sorted([GC, MGC]), _rec)
    st3 = Stub(b)
    st3._dc_mon = E._load_daycap_mon()
    _arm(st3, [("GC", SC, P0, "projectx")])
    _due_now(st3)
    scan(st3)
    ck("R1g 재시작 뒤: 손절 둘 걷음 + 오늘 닿음 표시 + exit_ok(cap_tp) 한 번 + 기록 놓음",
       _left(b) == [] and E.App._daycap_hit_today(st3, "projectx", SC, _day())
       and [e for e in st3.evs if e[0] == "exit_ok"] == [("exit_ok", "GC", {"why": "cap_tp"})] and not E._load_daycap_mon(),
       (b.orders, st3.evs, E._load_daycap_mon()))
    ck("R1h 로드: 정리 중 기록은 12시간이 지나도 7일까지 싣는다(남은 주문 걷기)",
       E._DAYCAP_FIRED_MAX_AGE == 7 * 86400 and "fired" in E._DAYCAP_MON_FIELDS)

    # ── R2 (P1) 같은 회차에 여러 평가 계좌가 닿으면 청산을 거의 동시에 - 뒤 계좌가 앞 계좌 확인을 기다리다 상한을 넘지 않는다 ──────────
    _reset_state()
    _T0 = _clk["t"]

    class DynPX(FakePX):
        """시세가 초당 $0.2 오르고, 브로커 호출 한 번에 0.3초가 걸린다(가짜 시계)."""

        def mk(self):
            return 2652.3 + 0.2 * (_clk["t"] - _T0)

        def current_market_price(self, con):
            self.calls.append(("current_market_price", con))
            return self.mk()

        def close_contract(self, aid, con):
            self.calls.append(("close_contract", int(aid), con, round(_clk["t"] - _T0, 2)))
            q = self.pos.pop((int(aid), con), 0)
            pnl = (self.mk() - (self.avgs.get((int(aid), con)) or 0.0)) * q * E._PV_BY_ROOT.get(E._daycap_root(con), 0.0)
            for a in self.accts:
                if a["id"] == int(aid):
                    a["balance"] += pnl
            self.realized[int(aid)] = self.realized.get(int(aid), 0.0) + pnl

        def _accounts(self, *a, **k):
            _clk["t"] += 0.3
            return FakePX._accounts(self, *a, **k)

        def open_positions(self, aid, timeout=None):
            _clk["t"] += 0.3
            return FakePX.open_positions(self, aid, timeout)

    _accs = [{"id": 100 + i, "name": f"150KTC-V2-{i}", "balance": 151000.0} for i in range(1, 5)]
    b = DynPX(accts=_accs, realized={100 + i: 4200.0 for i in range(1, 5)})   # 첫 판단 시세 2,652.42 → 오늘 4,902 ≥ 상한 4,860
    for _a in _accs:
        for _c, _q in ((GC, 2), (MGC, 9)):
            b.pos[(_a["id"], _c)] = _q
            b.avgs[(_a["id"], _c)] = 2650.0
            b.orders.append({"id": _a["id"] * 10 + _q, "aid": _a["id"], "type": 4, "side": 1, "size": _q, "contractId": _c,
                             "stopPrice": 2640.0})
    st = Stub(b)
    _arm(st, [("GC", _a["name"], P0, "projectx") for _a in _accs])
    for _a in _accs:                                        # 앱이 넣은 leg(감시는 앱이 넣은 수량만 정리한다, 3차 리뷰 P0)
        for _c, _q in ((GC, 2), (MGC, 9)):
            E.App._daycap_mon_note(st, "GC", b, {"broker": "projectx", "sc": _a["name"]}, _a["id"], _c, 2640.0, "LONG", _q,
                                   {"day": _DAY0()[1], "lbl": _a["name"][-4:], "stage": "eval"})
    scan(st)
    _cl = [c for c in b.calls if c[0] == "close_contract"]
    _real = {a["id"]: b.realized[a["id"]] for a in _accs}
    # 3차(결정 A): 상한 = 4,860(내장 비율, 일관성 선 4,950 아래)이 그대로 닫는 선이다. 이 가짜 시세는 초당 $58(0.2pt x $290)로 오르는 극단이라
    # 정리 뒤 실현이 55% 선을 넘을 수 있다 - 여기서는 동시성만 본다: 네 계좌의 정리 뒤 실현 차이가 계좌마다 차례로 닫던 때(약 $590)보다 훨씬 작다.
    ck("R2a 같은 Combine 넷이 한 회차에 닿으면 네 계좌 모두 그 회차에 정리, 정리 뒤 오늘 실현 차이 $100 미만(동시 정리)",
       {c[1] for c in _cl} == {101, 102, 103, 104} and _real and max(_real.values()) - min(_real.values()) < 100.0
       and not b.orders, (_cl, _real))
    # 3차 리뷰 P0: 닫기 전에 1초 뒤 두 번째 읽기(포지션, 잔고, 실현, 포지션, 시세)를 한다. 가짜 시계는 네 스레드의 호출 시간을 모두 더하므로
    # (실제로는 동시에 돈다) 절대 시각 대신 네 계좌 청산이 함께 나가는지(차이 0.5초 안)와 종전 차례 확인(11.4초)보다 훨씬 이른지를 본다.
    ck("R2b 네 계좌 청산 발주가 함께 나간다(차이 0.5초 안) + 첫 판단 뒤 6초 안(1초 두 번째 읽기 포함, 종전: 계좌마다 8초 확인을 기다려 11.4초)",
       bool(_cl) and max(c[3] for c in _cl) - min(c[3] for c in _cl) <= 0.5 and max(c[3] for c in _cl) <= 6.0,
       [c[3] for c in _cl])
    ck("R2c 시세 재사용은 1초 안(로그인, 자산마다 1초에 한 번 - 뒤 계좌가 낡은 시세로 판단하지 않게)", E._DAYCAP_MARK_TTL_S < 1.0)

    # ── R3 (P1) 남은 래치가 새 포지션을 닫지 않는다(다음 거래일, [전체 정지], [라이브 시작], 플랫 확인 뒤 다시 연 포지션) ──────────────
    b, st = _setup_fire()
    FakePX._cancel_order = lambda self, aid, oid: (_ for _ in ()).throw(RuntimeError("HTTP 503"))
    _due_now(st)
    scan(st)
    FakePX._cancel_order = _cancel_maybe
    st._sig_accts.clear()
    st._sig_on = False
    E.App._daycap_mon_clear(st)
    b.orders.clear()                                     # 회원이 브로커에서 손절을 손으로 지움
    _clk["t"] += 24 * 3600
    b.realized[101] = 0.0
    E._daycap_day_start = lambda now_utc=None: _DAY0(_dt.datetime.fromtimestamp(_clk["t"], _dt.timezone.utc))
    try:
        _arm(st, [("GC", SC, P0, "projectx")])
        st._sig_on = True
        E.App._daycap_latch_halt_all(st)                  # [라이브 시작]
        b.marks.clear()
        run_entry(st, b, [("GC", 2), ("MGC", 9)])
        _n0 = sum(1 for c in b.calls if c[0] == "close_contract")
        b.marks = {GC: 2650.5, MGC: 2650.5}
        _due_now(st)
        scan(st)
        ck("R3a 지난 거래일 래치가 남아도 다음 날 새 진입(같은 계약, 같은 수량)을 닫지 않는다 + 래치 놓음 + 새 진입 감시 기록은 남음",
           sum(1 for c in b.calls if c[0] == "close_contract") == _n0 and b.pos.get((101, GC)) == 2 and not st._dc_fired
           and [l["con"] for l in (E._load_daycap_mon().get(K1) or {}).get("legs", [])] == [GC, MGC]
           and "fired" not in (E._load_daycap_mon().get(K1) or {}), (sum(1 for c in b.calls if c[0] == "close_contract"), _n0, b.pos, st._dc_fired, [l["con"] for l in (E._load_daycap_mon().get(K1) or {}).get("legs", [])], list(E._load_daycap_mon().get(K1) or {})))
    finally:
        E._daycap_day_start = _DAY0
    # 같은 날, 플랫 확인 뒤(손절 취소 실패로 래치가 남음) 회원이 같은 계약을 손으로 다시 연다 → 닫지 않는다
    b, st = _setup_fire()
    _fail["on"] = True
    _due_now(st)
    scan(st)
    _fail["on"] = False
    b.orders.clear()
    b.pos[(101, GC)] = 1
    b.avgs[(101, GC)] = 2660.0
    _n0 = sum(1 for c in b.calls if c[0] == "close_contract")
    _due_now(st)
    scan(st)
    ck("R3b 같은 날 플랫 확인 뒤 다시 연 포지션(회원)은 남은 래치가 닫지 않는다(수동 우선)",
       sum(1 for c in b.calls if c[0] == "close_contract") == _n0 and b.pos.get((101, GC)) == 1 and not st._dc_fired)
    # 정리 도중(GC가 안 닫힘) [전체 정지]를 누르면 더 닫지 않는다
    b, st = _setup_fire()
    b.close_fail = {GC}
    _due_now(st)
    scan(st)
    ck("R3c 정리 도중(GC 청산 안 됨): 래치 활성, 닫힌 MGC 손절만 걷음", K1 in st._dc_fired and not st._dc_fired[K1].get("done")
       and _left(b) == [GC], (st._dc_fired, b.orders))
    ck("R3d 정리 중인 계좌는 _check_stop_closed가 보고하지 않는다(활성 래치), 멈춘 래치는 아니다",
       E._daycap_ctx_closing(st, {"aid": 101}) is True)
    b.close_fail = set()
    E.App._daycap_mon_clear(st)                           # [전체 정지]
    ck("R3e 멈춘 래치는 '정리 중' 아님(청산 보고가 막히지 않음)", E._daycap_ctx_closing(st, {"aid": 101}) is False)
    _n0 = sum(1 for c in b.calls if c[0] == "close_contract")
    _due_now(st)
    scan(st)
    ck("R3f [전체 정지] 뒤에는 남은 GC를 더 닫지 않는다(회원이 넘겨받음), GC 손절 그대로, 래치 놓음",
       sum(1 for c in b.calls if c[0] == "close_contract") == _n0 and b.pos.get((101, GC)) == 2 and _left(b) == [GC]
       and not st._dc_fired, (b.calls[-4:], b.orders))
    ck("R3g 이어 닫기 조건: 같은 거래일 + 멈춤 아님 + 플랫 확인 전 + 발동 때 포지션의 부분(같은 방향, 수량 같거나 작음)",
       E.App._daycap_may_close({"day": "D", "pos": {GC: 2, MGC: 9}}, [{"con": GC, "qty": 1}], "D")
       and not E.App._daycap_may_close({"day": "D", "pos": {GC: 2}}, [{"con": GC, "qty": 3}], "D")
       and not E.App._daycap_may_close({"day": "D", "pos": {GC: 2}}, [{"con": GC, "qty": -1}], "D")
       and not E.App._daycap_may_close({"day": "D", "pos": {GC: 2}}, [{"con": GC, "qty": 1}], "E")
       and not E.App._daycap_may_close({"day": "D", "pos": {GC: 2}, "halt": True}, [{"con": GC, "qty": 1}], "D")
       and not E.App._daycap_may_close({"day": "D", "pos": {GC: 2}, "done": True}, [{"con": GC, "qty": 1}], "D"))

    # ── R4 (P0, P1, P2) Lucid 단계 답이 기존 행 1R을 바꾸지 않는다 - 막 고른 새 행(그 행 객체)만, 아직 기본값일 때만 ─────────────────
    class FW:
        def __init__(self, v):
            self.v, self.state = str(v), "normal"

        def get(self):
            return self.v

        def cget(self, k):
            return self.state

        def config(self, **k):
            self.state = k.get("state", self.state)

        def delete(self, a, b=None):
            self.v = ""

        def insert(self, i, t):
            self.v = str(t)

        def set(self, v):
            self.v = str(v)

    def _lu_stub(nq_prop):
        _reset_state()
        s = Stub(None)
        s._acfg = {"NQ": {"accounts": [{"id": LU, "broker": "nt8", "one_r": 500.0, "prop": dict(nq_prop), "on": True}]},
                   "GC": {"accounts": [{"id": "", "broker": "nt8", "one_r": 500.0, "prop": dict(P0), "on": True}]},
                   "BTC": {"accounts": []}}
        s._asset = "GC"
        s._accts_of = lambda a: s._acfg[a]["accounts"]
        s.built = 0
        s._build = lambda: setattr(s, "built", s.built + 1)
        return s
    st = _lu_stub(P0)
    _w = {"acct_id": FW(LU), "one_r": FW("500")}
    E.App._reco_prefill_row(st, 0, _w)
    _fr = (st._dc_map("_dc_fresh").get("nt8|ldi150a1234") or [])
    ck("R4a 미답 Lucid 계좌를 새 GC 행에 고름 = 1R 칸 그대로, 그 행 객체 하나만 표시", _w["one_r"].get() == "500"
       and len(_fr) == 1 and _fr[0]["row"] is st._acfg["GC"]["accounts"][0], _fr)
    st._acfg["GC"]["accounts"][0]["id"] = LU                       # 행 저장
    E.App._lucid_stage_set(st, LU, "eval")
    ck("R4b 단계 답(평가) = 기존 NQ 행 1R $500 그대로, 새 GC 행만 $2,200 + 로그 '새로 추가한 행'",
       st._acfg["NQ"]["accounts"][0]["one_r"] == 500.0 and st._acfg["GC"]["accounts"][0]["one_r"] == 2200.0
       and any("새로 추가한 행의 1R 칸에 권장 값 $2,200" in m for m in st.logs) and not st._dc_map("_dc_fresh"),
       (st._acfg, st.logs[-2:]))
    # 이미 물었던(창을 닫은) 계좌: 표시를 남기지 않는다 → 나중에 프롭 창에서 답해도 1R은 그대로
    st = _lu_stub(P0)
    with E._DAYCAP_STATE_LOCK:
        E.App._dc_state(st)["asked"]["nt8|ldi150a1234"] = time.time()
    _w = {"acct_id": FW(LU), "one_r": FW("500")}
    E.App._reco_prefill_row(st, 0, _w)
    st._acfg["GC"]["accounts"][0]["id"] = LU
    E.App._lucid_stage_set(st, LU, "eval")
    ck("R4c 이미 물었던 계좌(창을 닫음) = 표시 없음, 나중 답에도 두 행 1R 그대로",
       not st._dc_map("_dc_fresh") and st._acfg["NQ"]["accounts"][0]["one_r"] == 500.0
       and st._acfg["GC"]["accounts"][0]["one_r"] == 500.0)
    # 다른 행에서 이미 답한 계좌(펀디드): 새 행은 고르는 순간 그 단계로 바로 채운다(표시 없음), 그 뒤 단계를 바꿔도 다른 행 1R 그대로
    st = _lu_stub(dict(P0, stage="funded", stage_for=LU.casefold()))
    _w = {"acct_id": FW(LU), "one_r": FW("500")}
    E.App._reco_prefill_row(st, 0, _w)
    ck("R4d 다른 행에서 답한 Lucid 계좌(펀디드) = 새 행 1R 칸을 바로 $450로(표시 없음)", _w["one_r"].get() == "450"
       and not st._dc_map("_dc_fresh") and any("앞서 고른 단계" in m for m in st.logs), (_w["one_r"].get(), st.logs[-1:]))
    st._acfg["GC"]["accounts"][0]["id"] = LU
    E.App._lucid_stage_set(st, LU, "eval")
    ck("R4e 그 뒤 단계를 평가로 바꿔도 기존 NQ 행($500), 새 GC 행 설정 값($500 - 칸 저장 전) 그대로(말없이 바꾸지 않음)",
       st._acfg["NQ"]["accounts"][0]["one_r"] == 500.0 and st._acfg["GC"]["accounts"][0]["one_r"] == 500.0)

    # ── R5 (P1) Operator(확인 모드): 회원이 손으로 연 포지션을 앱이 닫지 않는다 ──────────────────────────────────────────
    _reset_state()
    b = FakePX(realized={101: 0.0})
    st = Stub(b)
    st._watch_only = True
    _arm(st, [("GC", SC, P0, "projectx")])
    for _c, _q in ((GC, 3), (MGC, 10)):
        b.pos[(101, _c)] = _q
        b.avgs[(101, _c)] = 2650.0
    b.orders.append({"id": 9, "aid": 101, "type": 4, "side": 1, "size": 3, "contractId": GC, "stopPrice": 2630.0})
    b.marks = {GC: 2662.0, MGC: 2662.0}
    _due_now(st)
    scan(st)
    ck("R5a 확인 모드 = 무장 행을 감시하지 않음(브로커 호출 0) - 회원 포지션, 회원 손절 그대로",
       E.App._daycap_mon_targets(st) == [] and b.calls == [] and b.pos[(101, GC)] == 3 and _left(b) == [GC], b.calls)
    st._watch_only = False
    st._gate = {"ok": True, "enabled": True, "caps": {"use": True, "autoentry": False}}
    _row = {"id": SC, "prop": dict(P0), "on": True, "broker": "projectx"}
    _t_op = E.App._daycap_row_text(st, "GC", _row)
    st._gate = {"ok": True, "enabled": True, "caps": {"use": True, "autoentry": True}}
    ck("R5b 상태 줄: Operator 권한(자동 진입 없음)이면 비움(없는 보호를 말하지 않음), Autopilot이면 그대로",
       _t_op == "" and E.App._daycap_row_text(st, "GC", _row).startswith("익절: 하루 상한 $4,860"))

    # ── R6 (P1 일부) 통과 표시: 무엇이 멈추는지와 아직 통과 전일 때 푸는 길을 말한다, 알림은 계좌마다 한 번 ──────────────────────────
    _reset_state()
    st = Stub(None)
    E.App._daycap_mark_passed(st, "projectx", SC, bal=159100.0, pass_tp=159000.0, lbl="TS", size=150000, via="watch")
    E.App._daycap_mark_passed(st, "nt8", LU, bal=159100.0, pass_tp=159000.0, lbl="LU", size=150000, via="watch")
    ck("R6a 통과 표시 로그: Topstep = '이 계좌로는 더 진입하지 않습니다' + 통과 목표 잔고를 올리면 풀림, Lucid = 단계를 '평가'로 다시 고르면 풀림",
       any("이 계좌로는 더 진입하지 않습니다" in m and "통과 목표 잔고를 올리십시오" in m for m in st.logs)
       and any("단계를 '평가'로 다시 고르십시오" in m for m in st.logs), st.logs)
    st._acfg = {"GC": {"accounts": [{"id": SC, "prop": dict(P0), "on": True, "broker": "projectx"}]}}
    ck("R6b Topstep 통과 행 상태 줄: '이 계좌로는 더 진입하지 않습니다'",
       "이 계좌로는 더 진입하지 않습니다" in E.App._daycap_row_text(st, "GC", st._acfg["GC"]["accounts"][0]))
    E.App._daycap_pass_skip_ev(st, "projectx", SC, "GC")
    E.App._daycap_pass_skip_ev(st, "projectx", SC, "NQ")
    E.App._daycap_pass_skip_ev(st, "projectx", SC, "GC")
    ck("R6c 통과 계좌 건너뜀 보고: 처음 한 번 pass_reached(서버 DM 한 번), 그 뒤 pass_done(기록만)",
       st.skips == [("GC", "pass_reached"), ("NQ", "pass_done"), ("GC", "pass_done")], st.skips)

    # ── R7 (P2) 건너뜀 규칙은 감시가 실제로 닫는 선까지 남은 한도로 잰다 - 3차(결정 A)부터 그 선 = 상한 그대로(4,860, 여유를 더 빼지 않음) ──
    _reset_state()
    b = FakePX(realized={101: 4640.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)], cap=dict(CAP, risk=600.0))
    ck("R7a 상한(4,860)까지 $220 < 0.5 x $600 = cap_room(이번 신호만, 표시 없음, 차분한 로그)",
       not any(c[0] == "place_entry" for c in b.calls) and st.skips == [("GC", "cap_room")]
       and not E.App._daycap_hit_today(st, "projectx", SC, _day())
       and any("오늘 남은 하루 이익 한도($220)" in m for m in st.logs), (st.skips, st.logs[-2:]))
    _reset_state()
    b = FakePX(realized={101: 3300.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2), ("MGC", 9)], cap=dict(CAP, risk=3000.0))
    ck("R7b 상한까지 $1,560 ≥ 0.5 x $3,000 = 진입(2차의 '청산 비용 + 4%' 여유는 걷어냄 - 그때는 cap_room이었다)",
       sum(1 for c in b.calls if c[0] == "place_entry") == 2 and not st.skips
       and not any("정리 여유를 빼고" in m for m in st.logs), (st.skips, st.logs[-1:]))

    # ── R8 (P2) 감시 눈 감기: 계좌 목록 짧은 시간 제한, 포지션이 열린 계좌의 일시 오류는 5초 뒤 다시 ─────────────────────────────
    class TOPX(FakePX):
        def _accounts(self, *a, **k):
            self.calls.append(("_accounts_kw", k.get("timeout")))
            return FakePX._accounts(self, *a, **k)
    _reset_state()
    b = TOPX(realized={101: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    b.marks = {GC: 2651.0, MGC: 2651.0}
    _due_now(st)
    scan(st)
    ck("R8a 감시의 계좌 목록 읽기 = 짧은 시간 제한(3, 5)초", ("_accounts_kw", (3, 5)) in b.calls, [c for c in b.calls if c[0].startswith("_acc")])
    _op = b.open_positions
    b.open_positions = lambda aid, timeout=None: (_ for _ in ()).throw(RuntimeError("503"))
    _due_now(st)
    _t0 = time.time()
    scan(st)
    b.open_positions = _op
    ck("R8b 포지션이 열린 계좌는 일시 오류 뒤 5초 뒤 다시(종전 30초)", abs(st._dc_due[K1] - _t0 - 5) < 1e-6, st._dc_due)
    _px_src = open(os.path.join(HERE, "eqexec", "broker", "projectx.py"), encoding="utf-8").read()
    ck("R8c 어댑터 _accounts(timeout=None): 기본 호출은 종전과 같은 _post(경로, 본문)", "def _accounts(self, active_only: bool = True, timeout=None)"
       in _px_src and 'else self._post("/api/Account/search", _body))' in _px_src)

    # ── R9 (P2) 마무리의 주문 걷기는 정리한 계약만, 다시 열린 계약 손절은 둔다, 정리 중엔 새 진입 안 함 ──────────────────────────────
    b, st = _setup_fire()
    b.orders.append({"id": 77, "aid": 101, "type": 1, "side": 0, "size": 1, "contractId": "CON.F.US.MNQ.Z26", "limitPrice": 21000.0})
    _due_now(st)
    scan(st)
    ck("R9a 정리 뒤 걷는 주문 = 정리한 계약(GC, MGC)의 손절만 - 회원의 다른 계약 주문(MNQ 지정가)은 그대로",
       _left(b) == ["CON.F.US.MNQ.Z26"] and not b.pos.get((101, GC)), b.orders)
    _reset_state()
    b = FakePX(realized={101: 0.0})
    st = Stub(b)
    b.pos[(101, GC)] = 2
    b.avgs[(101, GC)] = 2650.0
    b.orders += [{"id": 1, "aid": 101, "type": 4, "contractId": GC}, {"id": 2, "aid": 101, "type": 4, "contractId": MGC}]
    ck("R9b 걷기 = 주문 목록 먼저, 포지션 나중 - 열린 계약(GC) 손절은 두고 플랫 계약(MGC)만",
       E.App._daycap_cleanup(st, b, 101, {GC, MGC}) == 0 and _left(b) == [GC]
       and [c[0] for c in b.calls].index("_open_orders") < [c[0] for c in b.calls].index("open_positions"), b.calls)
    _reset_state()
    b = FakePX(realized={101: 0.0})
    st = Stub(b)
    st._dc_fired = {K1: {"mode": "cap", "day": _day(), "pos": {GC: 2}, "done": False, "halt": False, "ts": time.time()}}
    run_entry(st, b, [("GC", 2)])
    _pl_halt = None
    st._dc_fired[K1]["halt"] = True
    run_entry(st, b, [("GC", 2)])
    ck("R9c 앱이 이 계좌를 정리하는 중(활성 래치)이면 새 진입 안 함(day_cap), 멈춘 래치면 종전대로 진입",
       st.skips[:1] == [("GC", "day_cap")] and sum(1 for c in b.calls if c[0] == "place_entry") == 1, (st.skips, b.calls[-3:]))

    # ── R10 (P2) 오늘 실현 손익을 못 읽으면 조용히 상한이 꺼지지 않게 - 한 번 알리고 상태 줄이 '모름' ───────────────────────────────
    _reset_state()
    b = FakePX(realized={101: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2651.0, MGC: 2651.0}
    _due_now(st)
    scan(st)
    b.realized[101] = RuntimeError("Trade/search down")
    b.pos[(101, MGC)] = 3                                  # 포지션 구성이 바뀌어 실현을 다시 읽어야 한다
    b.avgs[(101, MGC)] = 2651.0
    _due_now(st)
    scan(st)
    _n1 = sum(1 for m in st.logs if "오늘 실현 손익을 읽지 못해 지금은 하루 이익 상한을 확인하지 못합니다" in m)
    _clk["t"] += 61
    _due_now(st)
    scan(st)
    _due_now(st)
    scan(st)
    _row = {"id": SC, "prop": dict(P0), "on": True, "broker": "projectx"}
    st._acfg = {"GC": {"accounts": [_row]}}
    ck("R10a 실현 손익을 60초 넘게 못 읽으면 차분한 로그 한 번(그 전엔 없음) + 상태 줄 '남은 금액을 모릅니다'(낡은 금액 아님)",
       _n1 == 0 and sum(1 for m in st.logs if "오늘 실현 손익을 읽지 못해 지금은 하루 이익 상한을 확인하지 못합니다" in m) == 1
       and E.App._daycap_row_text(st, "GC", _row).endswith("(오늘 손익을 지금 읽지 못해 남은 금액을 모릅니다)"),
       E.App._daycap_row_text(st, "GC", _row))

    # ── R11 (P1) Lucid 단계 답은 계좌에 붙는다 - 행의 계좌를 바꾸면 새 계좌는 다시 묻고, 평가 설정은 자동으로 ───────────────────────
    _reset_state()
    st = Stub(None)
    st._asset = "NQ"
    _row = {"id": "LDI150A1111", "broker": "nt8", "one_r": 2200.0, "label": "LA", "on": True,
            "prop": dict(P0, size=150000, day_cap=4000.0, pass_tp=159000.0, pass_set=True)}
    st._acfg = {"NQ": {"broker": "nt8", "accounts": [_row]}, "GC": {"accounts": []}, "BTC": {"accounts": []}}
    st._accts_of = lambda a: st._acfg[a]["accounts"]
    E.App._lucid_stage_set(st, "LDI150A1111", "eval")
    ck("R11a 답 = 단계 + 답을 받은 계좌(stage_for)", _row["prop"]["stage"] == "eval" and _row["prop"]["stage_for"] == "ldi150a1111")
    ck("R11b 다른 계좌 id로 보면 그 답은 쓰지 않는다(unanswered)",
       E.App._daycap_stage_cfg(st, "nt8", "LDI150B2222", _row["prop"])[0] == "unanswered"
       and E.App._daycap_stage_cfg(st, "nt8", "LDI150A1111", _row["prop"])[0] == "eval")
    st._acct_widgets = {0: {"on": FW(1), "label": FW("LA"), "one_r": FW("2200"), "acct_id": FW("LDI150B2222")}}
    st._bk_switch_warn = False
    E.App._collect_acct_widgets(st)
    ck("R11c 행에서 계좌를 바꾸면(_collect_acct_widgets) 옛 계좌의 단계, 규모, 하루 상한, 통과 목표를 자동으로 + 로그 한 줄, 1R은 그대로",
       _row["id"] == "LDI150B2222" and _row["prop"]["stage"] is None and _row["prop"]["size"] == 0
       and _row["prop"]["day_cap"] is None and _row["prop"]["pass_tp"] == 0.0 and _row["prop"]["pass_set"] is False
       and _row["one_r"] == 2200.0 and any("옛 계좌의 평가 계좌 익절 설정" in m for m in st.logs), (_row, st.logs[-1:]))
    _asked = []
    st._lucid_stage_ask = lambda aid, lbl: _asked.append(aid)
    E.App._lucid_stage_check(st)
    ck("R11d 새 계좌는 한 번 묻는다(답 전엔 상한 없음)", _asked == ["LDI150B2222"], _asked)
    _n = E._new_acct(500.0, LU, prop={"on": False, "stage": "eval", "stage_for": "LDI150A1234", "set": True})["prop"]
    ck("R11e 설정 로드: stage_for 소문자 유지, 단계 없으면 None", _n["stage_for"] == "ldi150a1234"
       and E._new_acct(500.0, LU, prop={"on": False, "stage_for": "x", "set": True})["prop"]["stage_for"] is None)

    # ── R12 (P1) 규모: 계좌 이름이 말하는 규모가 이긴다 ────────────────────────────────────────────────────────────
    _r = E._daycap_resolve("projectx", dict(P0, size=150000), "50KTC-V2-9", 50400.0)
    ck("R12a 저장된 150K + 이름 50KTC = 50K 기준(상한 $1,620, 통과 익절 $53,050), 충돌 표시",
       (_r["size"], _r["cap"], _r["pass_tp"], _r["size_src"], _r["size_conflict"]) == (50000, 1620.0, 53050.0, "name", 150000), _r)
    _r2 = E._daycap_resolve("projectx", dict(P0, size=100000), "EXPRESS-V2-1")
    ck("R12b 이름에 규모가 없는 계좌는 저장된 규모(펀디드는 평가 아님 = None 그대로)", _r2 is None
       and E._daycap_resolve("nt8", dict(P0, stage="eval", size=50000), "LDI150A1")["size"] == 150000)
    _reset_state()
    st = Stub(None)
    _fb = SimpleNamespace(day_realized=lambda *a, **k: 0.0, trades=lambda *a, **k: [], name="projectx")   # 거래 기록 다 읽음(통과 표시 조건)
    for _ in range(2):
        E.App._daycap_plan(st, _fb, {"broker": "projectx", "pr": dict(P0, size=150000), "lbl": "K50", "sc": "50KTC-V2-9",
                                     "risk": 500.0}, {"id": 9, "name": "50KTC-V2-9", "balance": 50400.0}, 9,
                           [("MGC", 3, MGC)], "GC", True)
    ck("R12c 진입 계획이 규모 충돌을 한 번 알린다", sum(1 for m in st.logs if "계좌 이름의 규모(50K)와 달라" in m) == 1, st.logs)

    # ── R13 (P2) 앱이 안 보던 사이 통과 목표를 넘긴 계좌(잔고 +10% 띠 밖)도 '통과' ───────────────────────────────────────
    _reset_state()
    st = Stub(None)
    _p1 = E.App._daycap_plan(st, _fb, {"broker": "projectx", "pr": dict(P0), "lbl": "K50", "sc": "50KTC-B", "risk": 500.0},
                             {"id": 9, "name": "50KTC-B", "balance": 55200.0}, 9, [("MGC", 3, MGC)], "GC", True)
    _p2 = E.App._daycap_plan(st, _fb, {"broker": "projectx", "pr": dict(P0), "lbl": "K50", "sc": "50KTC-B", "risk": 500.0},
                             {"id": 9, "name": "50KTC-B", "balance": 55200.0}, 9, [("MGC", 3, MGC)], "GC", True)
    ck("R13a 50K 잔고 55,200(통과 53,000 넘음, 띠 밖) = 통과 표시 + 건너뜀, 다음 신호도 건너뜀",
       _p1 == {"skip": True} and _p2 == {"skip": True} and "projectx|50ktc-b" in E.App._dc_state(st)["passed"]
       and st.skips == [("GC", "pass_reached"), ("GC", "pass_done")], (_p1, _p2, st.skips))
    ck("R13b Lucid 25K 잔고 27,600(통과 26,250 넘음) = 평가로 판별(통과 처리 대상), 규모 + 목표 x 3 넘으면 mismatch",
       E._daycap_stage("nt8", dict(P0, stage="eval"), "LDI025A", 27600.0) == ("eval", 25000)
       and E._daycap_stage("projectx", P0, "50KTC-B", 60000.0) == ("mismatch", 50000))

    # ── R16 (P1) LucidPro, LucidFlex 평가에는 LucidDaily 50% 상한을 걸지 않는다 ─────────────────────────────────────────
    ck("R16a 자동 하루 상한: LucidDaily(LD…) 4,410, LucidPro(LP…), LucidFlex(LF…) 0 - 통과 익절은 셋 다 159,050, 회원이 넣은 상한은 그대로",
       [(E._daycap_resolve("nt8", dict(P0, stage="eval"), n)["cap"], E._daycap_resolve("nt8", dict(P0, stage="eval"), n)["pass_tp"])
        for n in ("LDI150A", "LPI150B", "LFE150C")] == [(4410.0, 159050.0), (0.0, 159050.0), (0.0, 159050.0)]
       and E._daycap_resolve("nt8", dict(P0, stage="eval", day_cap=3000.0), "LPI150B")["cap"] == 3000.0)
    ck("R16b 문구도 요금제대로: LucidDaily = 목표의 49.0%(내장 비율, 50% 규칙 바로 아래), LucidPro = 하루 상한 없음",
       "목표의 49.0%, LucidDaily 50% 규칙 바로 아래" in E.App._lucid_rule_text("LDI150A", True)
       and "LucidPro 평가에는 하루 이익 상한을 걸지 않습니다" in E.App._lucid_rule_text("LPI150B", True)
       and "no daily profit cap on a LucidFlex evaluation" in E.App._lucid_rule_text("LFE150C", False))
    _reset_state()
    st = Stub(None)
    _lp = dict(P0, stage="eval", stage_for="lpi150b5678")
    st._acfg = {"NQ": {"accounts": [{"id": "LPI150B5678", "prop": _lp, "on": True, "broker": "nt8"}]}}
    ck("R16c LucidPro 평가 상태 줄 = '익절: 통과 $159,050'(하루 상한 없음)",
       E.App._daycap_row_text(st, "NQ", st._acfg["NQ"]["accounts"][0]) == "익절: 통과 $159,050",
       E.App._daycap_row_text(st, "NQ", st._acfg["NQ"]["accounts"][0]))

    # ── R17, R18 (P2) Lucid가 아닌 NT8 계좌, 펀디드 답과 옛 통과 목표 ────────────────────────────────────────────────
    _reset_state()
    st = Stub(None)
    st._acfg = {"NQ": {"accounts": [{"id": "Sim101", "prop": dict(P0), "on": True, "broker": "nt8"}]}}
    E.App._lucid_stage_set(st, "Sim101", "eval")
    ck("R17a Lucid 이름이 아닌 NT8 계좌에 단계 저장 = 아무것도 안 함(로그, 저장 없음 - 없는 기능을 말하지 않음)",
       not st.logs and st.saved == 0 and st._acfg["NQ"]["accounts"][0]["prop"].get("stage") is None, st.logs)
    st._acfg = {"NQ": {"accounts": [{"id": "LDI150A1", "prop": dict(P0, pass_tp=159000.0), "on": True, "broker": "nt8"}]}}
    E.App._lucid_stage_set(st, "LDI150A1", "funded")
    ck("R18a 펀디드로 답했는데 옛 통과 목표(테스트기 + 159,000)가 남아 있으면 그대로 켜져 있다고 말한다(말없이 지우지 않음)",
       any("통과 목표 잔고 $159,000는 그대로 켜져 있어" in m and "0으로 바꾸십시오" in m for m in st.logs)
       and st._acfg["NQ"]["accounts"][0]["prop"]["pass_tp"] == 159000.0, st.logs)
finally:
    FakePX._cancel_order = _orig_cancel
    time.sleep, time.time = _real_sleep, _real_time
    E._daycap_day_start = _DAY0

# ── 실제 Tk: Lucid 질문 창, 프롭 창(자동값은 평가에만, Lucid 아닌 NT8, 요금제 문구) ───────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as _te:
    ROOT = None
    print(f"SKIP T Tk를 열 수 없어 창 시험을 건너뜁니다({_te})")
if ROOT is not None:
    def _walk(w):
        out = [w]
        for c in w.winfo_children():
            out += _walk(c)
        return out

    def _texts(win):
        return [str(x.cget("text")) for x in _walk(win) if isinstance(x, (ttk.Label, ttk.Button, ttk.Radiobutton))]

    def _tops():
        return [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]

    def _entries(win):
        return [x.get() for x in _walk(win) if isinstance(x, ttk.Entry)]

    class TkStub(Stub):
        pass
    for _pn in ("_prop_dialog", "_prop_btn_text", "_lucid_stage_ask"):
        setattr(TkStub, _pn, E.App.__dict__[_pn])
    _reset_state()
    ts = TkStub(None)
    ts.root = ROOT
    ts._acct_widgets = {}
    ts._asset = "NQ"
    ts._acfg = {"NQ": {"broker": "nt8", "accounts": [
        {"id": LU, "prop": dict(P0, stage="funded", stage_for=LU.casefold()), "on": True, "broker": "nt8", "label": "LF"},
        {"id": "LDI150B9999", "prop": dict(P0), "on": True, "broker": "nt8", "label": "LQ"},
        {"id": "Sim101", "prop": dict(P0), "on": True, "broker": "nt8", "label": "SM"},
        {"id": "LPI150C5678", "prop": dict(P0, stage="eval", stage_for="lpi150c5678"), "on": True, "broker": "nt8", "label": "LP"}]},
        "GC": {"broker": "nt8", "accounts": []}, "BTC": {"accounts": []}}
    ts._accts_of = lambda a: ts._acfg[a]["accounts"]
    E.App._prop_dialog(ts, 0)
    _t = _tops()
    _e_f = _entries(_t[0])
    ck("T1 프롭 창(Lucid 펀디드): 통과 목표, 하루 상한 칸 비움 + '자동값은 평가 계좌에만 적용'(걸리지 않는 값을 보이지 않음)",
       _e_f[-2:] == ["", ""] and "(자동값은 평가 계좌에만 적용)" in _texts(_t[0]), (_e_f, _texts(_t[0])))
    _ev = next(x for x in _walk(_t[0]) if isinstance(x, ttk.Radiobutton) and str(x.cget("text")) == "평가")
    _stage_rbs = [x for x in _ev.master.winfo_children() if isinstance(x, ttk.Radiobutton)]   # 단계 고르기(계좌 유형 '펀디드'와 따로)
    _ev.invoke()
    ROOT.update()
    ck("T2 단계를 평가로 고르면 자동값이 칸에 채워진다(159050, 4410), 펀디드로 되돌리면 다시 비움",
       _entries(_t[0])[-2:] == ["159050", "4410"], _entries(_t[0]))
    next(x for x in _stage_rbs if str(x.cget("text")) == "펀디드").invoke()
    ROOT.update()
    _e_back = _entries(_t[0])[-2:]
    _t[0].destroy()
    ck("T2b (되돌림) 펀디드 = 비움", _e_back == ["", ""], _e_back)
    E.App._prop_dialog(ts, 1)
    _t = _tops()
    ck("T3 프롭 창(Lucid 미답): 칸 비움", _entries(_t[0])[-2:] == ["", ""], _entries(_t[0]))
    _t[0].destroy()
    E.App._prop_dialog(ts, 2)
    _t = _tops()
    _tx = _texts(_t[0])
    ck("T4 프롭 창(Lucid 이름이 아닌 NT8 'Sim101'): 단계 고르기, Lucid 규칙 문구 없음 + '계좌 이름이 Lucid 형식이 아니라' 한 줄",
       "평가" not in _tx and not any("Lucid 계좌 단계" in x for x in _tx)
       and "계좌 이름이 Lucid 형식이 아니라 평가 계좌 익절을 쓰지 않습니다." in _tx, _tx)
    _t[0].destroy()
    E.App._prop_dialog(ts, 3)
    _t = _tops()
    _tx = _texts(_t[0])
    ck("T5 프롭 창(LucidPro 평가): 하루 상한 칸 비움, 문구 'LucidPro 평가에는 하루 이익 상한을 걸지 않습니다', 50% 규칙 문구 없음",
       _entries(_t[0])[-2:] == ["159050", ""] and any("LucidPro 평가에는 하루 이익 상한을 걸지 않습니다" in x for x in _tx)
       and not any("50%" in x for x in _tx), (_entries(_t[0]), _tx))
    _t[0].destroy()
    # Lucid 질문 창: 막 고른 새 행이 있으면 답에 따라 채워질 1R을 미리 말하고, 창을 닫으면(답 없음) 표시를 버린다
    _reset_state()
    ts._dc_fresh = {"nt8|ldi150b9999": [{"asset": "NQ", "idx": 1, "row": ts._acfg["NQ"]["accounts"][1]}]}
    E.App._lucid_stage_ask(ts, "LDI150B9999", "LQ")
    _t = _tops()
    _tx = _texts(_t[0])
    ck("T6 질문 창: 새 행 1R 안내('평가를 고르시면 새로 추가한 행의 1R 칸에 권장 $2,200이, 펀디드를 고르시면 $450이'), LucidDaily 규칙 문구",
       any("평가를 고르시면 새로 추가한 행의 1R 칸에 권장 $2,200이, 펀디드를 고르시면 $450이 채워집니다" in x for x in _tx)
       and any("LucidDaily 50% 규칙 바로 아래" in x for x in _tx), _tx)
    ROOT.tk.call(_t[0].protocol("WM_DELETE_WINDOW"))
    ROOT.update()
    ck("T7 창을 닫으면(답 없음) 새 행 표시를 버린다 - 나중 답이 1R을 바꾸지 않게", not ts._dc_fresh and not _tops(), ts._dc_fresh)
    # 통과로 표시된 Lucid 계좌: 프롭 창에서 '평가'를 다시 고르고 저장하면 통과 표시가 풀린다(통과 로그, 상태 줄이 안내하는 길)
    _reset_state()
    ts._dc_st = None
    ts._acfg["NQ"]["accounts"][1]["prop"] = dict(P0, stage="eval", stage_for="ldi150b9999")
    E.App._daycap_mark_passed(ts, "nt8", "LDI150B9999", bal=159100.0, pass_tp=159000.0, lbl="LQ", size=150000, via="watch")
    ts._save_acct_widgets = lambda: None
    ts._daycap_rows_refresh = lambda: None
    _row_lq = ts._acfg["NQ"]["accounts"][1]
    _tx_before = E.App._daycap_row_text(ts, "NQ", _row_lq)
    E.App._prop_dialog(ts, 1)
    _t = _tops()
    next(x for x in _walk(_t[0]) if isinstance(x, ttk.Radiobutton) and str(x.cget("text")) == "평가").invoke()
    next(x for x in _walk(_t[0]) if isinstance(x, ttk.Button) and str(x.cget("text")) == "저장").invoke()
    ROOT.update()
    ck("T8 Lucid 통과 표시 계좌: 상태 줄이 '단계를 평가로 다시 고르십시오'라고 말하고, 프롭 창에서 평가를 다시 저장하면 표시가 풀린다",
       "'평가'로 다시 고르십시오" in _tx_before and "nt8|ldi150b9999" not in E.App._dc_state(ts)["passed"],
       (_tx_before, E.App._dc_state(ts)["passed"]))
    for _w in _tops():
        _w.destroy()
    ROOT.destroy()

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
