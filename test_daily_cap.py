#!/usr/bin/env python3
# 평가 계좌 익절(하루 이익 상한 + 통과 목표, 대표 2026-10-14) 오프라인 시험. 2차 결정(10/14 "앱이 보다가 정리하는 방식 고") 기준. 0 = 통과.
#   1 규모, 기본값(3차 결정 A, B, 내장 상한 비율): Topstep 54% = 150K 4,860 / 100K 3,240 / 50K 1,620, Lucid 49% = 150K 4,410 / 100K 2,940 /
#     50K 1,470 / 25K 613, 통과 익절 기본 = 규모 + 목표 + $50(최고일 모름), 옛 빌드의 통과 목표 0은 '안 정함', 회원 값은 덮지 않음,
#     0 = 끔(pass_set)
#   2 거래일 경계 17:00 CT(= 18:00 ET), tzdata 없을 때도 같음
#   3 진입: 브로커 익절 주문 없음(ProjectX 어댑터에 익절 메서드 자체가 없음), 감시 기록, 로그, 하루 실현 조회 시간 제한
#   4 건너뜀: 이미 닿음(day_cap + 오늘 표시), 남은 한도 < 0.5 x 1R x 배수(cap_room, 표시 없음), 통과 목표(pass_reached + 통과 표시),
#     실현 손익 모름 = 종전대로 진입
#   5 감시(ProjectX): 여유를 둔 상한에서 그 계좌만 정리, confirm-after-act, 한 번에 하나, 발사 직전 재확인, 거래일 래치,
#     exit_ok cap_tp는 자산마다 한 번, 맥락 넘김, 남은 주문(손절) 취소, 다른 계좌 손대지 않음, 통과 목표 → 통과 표시, 재시작 뒤에도 감시,
#     [전체 정지]는 기록 지움, 진입 뒤 실현 손익을 못 읽어도 통과 목표는 본다
#   6 Lucid(NT8): NetLiq - 거래일 시작 잔고, 상한, 통과
#   7 통과 익절 감시와 겹치지 않음, 상태 줄 KO/EN, 새 문구(이모지, 가운뎃점, em 대시 없음), 청산 보고 본문 why, dry-run
#   8 비대상 경로 = 12e(5bc4374c)와 같음: 펀디드, 연습, 모르는 이름, Lucid 미답/펀디드, 프롭 아닌 브로커 - 브로커 호출 비교 + 함수 원문 비교
# HOME, APPDATA = 임시 폴더, 네트워크 차단, 브로커는 전부 가짜, EQ_NO_ALERT=1.
import datetime as _dt
import importlib.util
import inspect
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_dcap_")
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
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:500]}"))
    if not c:
        FAIL.append(n)


E = eqgui
P0 = dict(E._PROP_DEFAULTS)
GC, MGC = "CON.F.US.GC.Z26", "CON.F.US.MGC.Z26"

# ── 1 규모·기본값 ────────────────────────────────────────────────────────────────
ck("1a 이름 → 규모(Topstep Combine, Lucid L+요금제+드로다운+규모)",
   (E._daycap_size_from_name("projectx", "150KTC-V2-26505604"), E._daycap_size_from_name("projectx", "50KTC-B"),
    E._daycap_size_from_name("projectx", "100ktc-x"), E._daycap_size_from_name("projectx", "EXPRESS-V2-1"),
    E._daycap_size_from_name("nt8", "150KTC-A"), E._daycap_size_from_name("nt8", "LDI150123"),
    E._daycap_size_from_name("nt8", "LFE025-9"), E._daycap_size_from_name("nt8", "Sim101"))
   == (150000, 50000, 100000, None, None, 150000, 25000, None))
ck("1b 잔고 → 규모(±10%)", (E._daycap_size_from_balance("projectx", 148700), E._daycap_size_from_balance("projectx", 52000),
                         E._daycap_size_from_balance("projectx", 0), E._daycap_size_from_balance("projectx", 120000),
                         E._daycap_size_from_balance("nt8", 25600), E._daycap_size_from_balance("projectx", 25600))
   == (150000, 50000, None, None, 25000, None))
_exp = {("projectx", "150KTC-A"): (4860.0, 159050.0), ("projectx", "100KTC-A"): (3240.0, 106050.0),
        ("projectx", "50KTC-A"): (1620.0, 53050.0), ("nt8", "LDI150A"): (4410.0, 159050.0),
        ("nt8", "LDI100A"): (2940.0, 106050.0), ("nt8", "LDE050A"): (1470.0, 53050.0), ("nt8", "LDI025A"): (613.0, 26300.0),
        ("nt8", "LFI025A"): (0.0, 26300.0), ("nt8", "LPE150A"): (0.0, 159050.0)}
_got = {k: (lambda r: (r["cap"], r["pass_tp"]))(E._daycap_resolve(k[0], dict(P0, stage="eval"), k[1])) for k in _exp}
ck("1c 하루 상한(내장 비율 Topstep 54%, LucidDaily 49% - LucidFlex, LucidPro는 자동 상한 없이 통과 목표만)과 통과 익절 기본값(규모 + 목표 + $50)",
   _got == _exp, _got)
r = E._daycap_resolve("projectx", dict(P0, day_cap=0.0), "150KTC-A")
ck("1d 하루 상한 0 = 끔(통과 목표는 그대로)", r["cap"] == 0.0 and r["cap_src"] == "off" and r["pass_tp"] == 159050.0, r)
r = E._daycap_resolve("projectx", dict(P0, day_cap=4000.0), "150KTC-A")
ck("1e 회원이 넣은 하루 상한 우선", r["cap"] == 4000.0 and r["cap_src"] == "set", r)
r = E._daycap_resolve("projectx", dict(P0, pass_tp=157500.0), "150KTC-A")
ck("1f 회원이 저장한 0 아닌 통과 목표는 그대로(수동 우선)", r["pass_tp"] == 157500.0 and r["pass_src"] == "set", r)
r = E._daycap_resolve("projectx", dict(P0, pass_tp=0.0, pass_set=True), "150KTC-A")
ck("1g 이 빌드에서 0으로 저장(pass_set) = 통과 목표 끔", r["pass_tp"] == 0.0, r)
r = E._daycap_resolve("projectx", dict(P0, pass_tp=0.0), "150KTC-A")
ck("1h 옛 빌드의 0(표시 없음) = 안 정함 → 기본값", r["pass_tp"] == 159050.0 and r["pass_need"] == 159000.0 and r["pass_src"] == "auto", r)
_a = E._new_acct(500.0, "LDI150A", prop={"on": True, "type": "test", "day_cap": "abc", "size": 123, "stage": "maybe", "set": True})
ck("1i 설정 로드: 못 읽는 상한 = 자동, 이상한 규모 = 0, 모르는 단계 값 = None",
   _a["prop"]["day_cap"] is None and _a["prop"]["size"] == 0 and _a["prop"]["stage"] is None, _a["prop"])
_a = E._new_acct(500.0, "LDI150A", prop={"on": False, "stage": "eval", "set": True})
ck("1j 설정 로드: Lucid 단계 답 유지", _a["prop"]["stage"] == "eval", _a["prop"])
ck("1k 옛 설정(새 키 없음) = 단계 None", E._new_acct(500.0, "X", prop={"on": True, "set": True})["prop"]["stage"] is None)

# ── 2 거래일 경계 ───────────────────────────────────────────────────────────────
U = lambda *a: _dt.datetime(*a, tzinfo=_dt.timezone.utc)   # noqa: E731
_cases = [(U(2026, 10, 14, 13, 0), U(2026, 10, 13, 22, 0), "2026-10-14"),
          (U(2026, 10, 14, 22, 30), U(2026, 10, 14, 22, 0), "2026-10-15"),
          (U(2026, 10, 14, 21, 59), U(2026, 10, 13, 22, 0), "2026-10-14"),
          (U(2026, 12, 1, 23, 30), U(2026, 12, 1, 23, 0), "2026-12-02"),
          (U(2026, 12, 1, 7, 0), U(2026, 11, 30, 23, 0), "2026-12-01")]
_bad = [(c[0], E._daycap_day_start(c[0])) for c in _cases if E._daycap_day_start(c[0]) != (c[1], c[2])]
ck("2a 거래일 시작 = 17:00 CT(18:00 ET), 키 = 끝나는 날", not _bad, _bad)
_zi = E.ZoneInfo
E.ZoneInfo = None
try:
    _bad2 = [c[0] for c in _cases if E._daycap_day_start(c[0]) != (c[1], c[2])]
finally:
    E.ZoneInfo = _zi
ck("2b tzdata 없음(ZoneInfo None)도 같은 결과", not _bad2, _bad2)


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


try:
    # ── 3 진입: 브로커 익절 주문 없음 ──────────────────────────────────────────────
    from eqexec.broker import projectx as _pxmod
    ck("3a ProjectX 어댑터에 익절 주문 메서드가 없다(place_take_profit, 수량 고치기, 주문 상태 조회 걷어냄)",
       not any(hasattr(_pxmod.ProjectXBroker, m) for m in ("place_take_profit", "modify_order_size", "position_avg",
                                                            "order_state", "orders_since", "trades_since", "position_signed"))
       and hasattr(_pxmod.ProjectXBroker, "day_realized") and hasattr(_pxmod.ProjectXBroker, "open_positions"))
    _src_all = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
    ck("3b 앱 어디에도 익절 주문 발주가 없다(place_take_profit, take_profit_ticks=, takeProfitBracket, 지정가 익절 감시 파일)",
       "place_take_profit" not in _src_all and "take_profit_ticks=" not in _src_all and "takeProfitBracket" not in _src_all
       and ".daycap_watch.json" not in _src_all)
    _reset_state()
    b = FakePX()
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    _orders = [c for c in b.calls if c[0] == "place_entry"]
    ck("3c 평가 계좌 진입 = 시장가 진입 + 손절만(주문은 type 4 손절 두 개뿐, 지정가 0)",
       len(_orders) == 2 and all(c[1].get("order_type") == 2 for c in _orders)
       and sorted(o["type"] for o in b.orders) == [4, 4], (b.orders, _orders))
    _rec = (st._dc_mon or {}).get("projectx|150ktc-v2-1") or {}
    ck("3d 감시 기록 = 그 계좌, leg 둘(계약, 손절, 방향, 수량) + 디스크(비밀, 브로커 객체 없음)",
       sorted((l["con"], l["size"]) for l in _rec.get("legs", [])) == [(GC, 2), (MGC, 9)]
       and set(E._load_daycap_mon().get("projectx|150ktc-v2-1", {})) == set(E._DAYCAP_MON_FIELDS) - {"fired"}
       and "b" not in E._load_daycap_mon()["projectx|150ktc-v2-1"], _rec)
    ck("3e 하루 실현 조회는 짧은 시간 제한(3, 5)", any(c[0] == "day_realized" and c[2] == (3, 5) for c in b.calls), b.calls[:4])
    # 통과 익절 여유 = max($50, 2틱 x 이번 계약: GC 2 x $10 + MGC 9 x $1 = $29 x 2 = $58) - 3차 리뷰 P0
    ck("3f 계획 로그 KO(하루 상한, 남은, 통과 159,000 + 여유 $58, 앱 감시, 브로커 익절 주문 없음)",
       any("하루 상한 $4,860(오늘 남은 $3,860)" in m and "통과 $159,058(남은 $8,058)" in m and "앱 감시" in m
           and "브로커 익절 주문 없음" in m for m in st.logs), st.logs)
    ck("3g 맥락 = 평가 leg(cap 표시), 경보 없음", (st._open_ctx.get("GC") or {}).get("cap") is True and not st.alerts)

    # ── 4 건너뜀 ────────────────────────────────────────────────────────────────
    _reset_state()
    b = FakePX(realized={101: 4950.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    ck("4a 오늘 실현 4,950 ≥ 상한 4,860 → 진입 안 함 + entry_skip day_cap + 오늘 표시 + 차분한 로그(경보 없음)",
       not any(c[0] == "place_entry" for c in b.calls) and st.skips == [("GC", "day_cap")] and not st.alerts
       and E.App._daycap_hit_today(st, "projectx", SC, _DAY0()[1])
       and any("오늘 하루 이익 상한($4,860)에 닿아" in m and "17:00 CT" in m for m in st.logs), (st.skips, st.logs[-2:]))
    _n0 = sum(1 for c in b.calls if c[0] == "day_realized")
    run_entry(st, b, [("GC", 2)])
    ck("4b 같은 거래일 두 번째 신호는 조회 없이 건너뜀(day_cap)", sum(1 for c in b.calls if c[0] == "day_realized") == _n0
       and st.skips[-1] == ("GC", "day_cap"))
    # 4c cap_room: 남은 1,610 < 0.5 x 3,498 = 1,749 → 이번 신호만 건너뜀(자기 코드, 표시 없음)
    _reset_state()
    b = FakePX(realized={101: 3250.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    ck("4c 남은 한도 $1,610 < 0.5 x 1R x 배수($3,498) → entry_skip cap_room(day_cap 아님), 오늘 표시 없음",
       not any(c[0] == "place_entry" for c in b.calls) and st.skips == [("GC", "cap_room")]
       and not E.App._daycap_hit_today(st, "projectx", SC, _DAY0()[1])
       and any("손절 위험($3,498)의 절반보다 작아" in m for m in st.logs), (st.skips, st.logs[-2:]))
    _reset_state()
    b = FakePX(realized={101: 2800.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    ck("4d 상한(4,860)까지 남은 $2,060 ≥ 0.5 x $3,498 → 진입(감시)", sum(1 for c in b.calls if c[0] == "place_entry") == 2
       and not st.skips, st.skips)
    _reset_state()
    b = FakePX(realized={101: 3200.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2)], cap=dict(CAP, risk=4000.0))
    ck("4e 손절 위험은 cap['risk'](= _enter_account의 1R x 배수)를 쓴다: $4,000이면 남은 $1,660 < $2,000 → cap_room",
       st.skips == [("GC", "cap_room")], st.skips)
    _src_ea = inspect.getsource(E.App._enter_account)
    ck("4f _enter_account가 넘기는 위험 = _eff_r(1R x 배수)", '"risk": _eff_r' in _src_ea)
    # 4g 통과 목표 잔고 이상 → 통과 표시 + pass_reached + 1R 제안 창 한 번, '아직 평가 중' 말 없음(P1-a)
    _reset_state()
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 159200.0}], realized={101: 0.0})
    b.trades = lambda aid, start_iso, timeout=None: []      # 거래 기록을 다 읽음(3차 리뷰: 통과 표시는 최고일을 다 알 때만)
    st = Stub(b)
    st.root = SimpleNamespace(after=lambda ms, fn=None, *a: fn() if fn else None)
    run_entry(st, b, [("GC", 2)])
    ck("4g 잔고 159,200 ≥ 통과 159,000 → 진입 안 함 + pass_reached + 통과 표시 + 1R 제안 한 번($450)",
       not any(c[0] == "place_entry" for c in b.calls) and st.skips == [("GC", "pass_reached")]
       and "projectx|150ktc-v2-1" in E.App._dc_state(st)["passed"] and st.prompts == [("projectx", SC, "TS150", 450.0)],
       (st.skips, st.prompts))
    ck("4h 로그가 '아직 평가 중'이라고 하지 않음(P1-a)", not any("평가 중" in m or "still in evaluation" in m for m in st.logs),
       st.logs)
    run_entry(st, b, [("GC", 2)])
    ck("4i 통과한 Topstep Combine은 다음 신호도 건너뜀(사유 pass_done = 알림 없는 기록, 처음 한 번만 pass_reached), 1R 제안은 다시 안 뜸",
       st.skips[-2:] == [("GC", "pass_reached"), ("GC", "pass_done")] and len(st.prompts) == 1
       and not any(c[0] == "place_entry" for c in b.calls), st.skips)
    _a0 = {"id": SC, "prop": dict(P0), "on": True, "broker": "projectx"}
    st._acfg = {"GC": {"accounts": [_a0]}}
    ck("4j 상태 줄: 통과 표시", E.App._daycap_row_text(st, "GC", _a0).startswith("평가 통과 목표에 닿았습니다"),
       E.App._daycap_row_text(st, "GC", _a0))
    b.accts[0]["balance"] = 150000.0
    run_entry(st, b, [("GC", 2)])
    ck("4k Topstep 잔고가 규모 이하로 돌아오면(초기화) 통과 표시를 풀고 다시 평가로 진입",
       "projectx|150ktc-v2-1" not in E.App._dc_state(st)["passed"] and any(c[0] == "place_entry" for c in b.calls))
    # 4l 회원 통과 목표가 규모에 안 맞으면(53,000) 통과로 표시하지 않고 하루 상한만
    _reset_state()
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 151000.0}], realized={101: 0.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2)], cap=dict(CAP, pr=dict(P0, pass_tp=53000.0, pass_set=True)))
    ck("4l 규모에 안 맞는 회원 통과 목표(53,000)는 통과 표시 없이 하루 상한만으로 진입(안내 한 번)",
       any(c[0] == "place_entry" for c in b.calls) and not E.App._dc_state(st)["passed"]
       and any("계좌 규모에도 맞지 않아 보여" in m for m in st.logs), st.logs[-3:])
    # 4m 실현 손익을 못 읽으면 진입은 종전대로(감시는 진입 뒤 다시 읽는다)
    _reset_state()
    b = FakePX(realized={101: RuntimeError("Trade/search down")})
    st = Stub(b)
    run_entry(st, b, [("GC", 2)])
    ck("4m 실현 손익 조회 실패 = 진입 그대로 + 로그 한 줄 + 감시 기록(경보 없음)",
       any(c[0] == "place_entry" for c in b.calls) and any("오늘 실현 손익을 읽지 못해" in m for m in st.logs)
       and st._dc_mon and not st.alerts and not st.skips, st.logs[-3:])
    # 4n dry-run: 주문 없음, 로그만
    _reset_state()
    b = FakePX(realized={101: 0.0})
    st = Stub(b)
    run_entry(st, b, [("GC", 2)], live=False)
    ck("4n dry-run = 감시 기록 없음, DRY-RUN 로그(브로커 익절 주문 없음)", not st._dc_mon
       and any("DRY-RUN" in m and "평가 계좌 익절" in m and "브로커 익절 주문 없음" in m for m in st.logs), st.logs[-2:])

    # ── 5 감시(ProjectX) ─────────────────────────────────────────────────────────
    # 150K: 상한 4,860(목표 9,000 x 내장 비율 0.54) = 감시가 닫는 오늘 손익 그대로(3차 결정 A - 청산 비용, 움직임 여유를 더 빼지 않는다).
    # 오늘 실현 1,000 + 미실현(포인트당 2 x 100 + 9 x 10 = $290).
    _reset_state()
    b = FakePX(realized={101: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    ck("5a 하루 상한 감시 문턱 = 상한 그대로 $4,860(150K, GC 2 + MGC 9 - 여유를 더 빼지 않음)",
       abs(E.App._daycap_trigger(st, 4860.0, [{"con": GC, "qty": 2}, {"con": MGC, "qty": 9}]) - 4860.0) < 1e-6)
    b.marks = {GC: 2662.0, MGC: 2662.0}                  # 미실현 3,480 → 오늘 4,480 < 4,860
    _due_now(st)
    scan(st)
    ck("5b 문턱 아래면 그대로(청산 없음), 상태 줄 재료 = 남은 $380",
       not any(c[0] == "close_contract" for c in b.calls)
       and abs(E.App._dc_state(st)["status"]["projectx|150ktc-v2-1"]["room"] - 380.0) < 1e-6,
       E.App._dc_state(st)["status"])
    st._open_assets = {"GC"}
    b.marks = {GC: 2663.5, MGC: 2663.5}                  # 미실현 3,915 → 오늘 4,915 ≥ 4,860
    _due_now(st)
    scan(st)
    ck("5c 문턱에 닿으면 그 계좌 모든 leg 시장가 정리(close_contract 둘)",
       sorted(c[2] for c in b.calls if c[0] == "close_contract") == sorted([GC, MGC]) and not b.pos, b.pos)
    ck("5d 플랫 확인 뒤 그 계좌 남은 주문(손절 둘) 취소 - 고아 주문 없음", not b.orders, b.orders)
    ck("5e 오늘 닿음 표시 + exit_ok cap_tp는 자산마다 한 번(leg 둘인데 한 번) + 청산 보고 why=cap_tp 한 번, 경보 없음",
       E.App._daycap_hit_today(st, "projectx", SC, _DAY0()[1])
       and [e for e in st.evs if e[0] == "exit_ok"] == [("exit_ok", "GC", {"why": "cap_tp"})]
       and st.fills == [("GC", True, "cap_tp")] and "GC" not in st._open_ctx and not st.alerts, (st.evs, st.fills))
    ck("5f 래치 풀림 + 감시 기록 놓음 + 로그(17:00 CT)", not getattr(st, "_dc_fired", {}) and not st._dc_mon
       and any("하루 이익 상한 익절을 마쳤습니다" in m and "17:00 CT" in m for m in st.logs), st.logs[-3:])
    # 5g 거래일 래치: 오늘 닿은 뒤 회원이 직접 연 포지션은 건드리지 않고, 다음 신호는 건너뛴다. 17:00 CT 뒤엔 다시 진입.
    b.pos[(101, GC)] = 1
    b.avgs[(101, GC)] = 2600.0
    _nclose = sum(1 for c in b.calls if c[0] == "close_contract")
    _due_now(st)
    scan(st)
    ck("5g 오늘 닿은 뒤 열린 포지션(회원이 연 것)은 닫지 않는다(수동 우선)",
       sum(1 for c in b.calls if c[0] == "close_contract") == _nclose and b.pos.get((101, GC)) == 1)
    b.pos.clear()
    run_entry(st, b, [("GC", 1)])
    ck("5h 같은 거래일 다음 신호 = 건너뜀(day_cap)", st.skips[-1:] == [("GC", "day_cap")], st.skips)
    E._daycap_day_start = lambda now_utc=None: _DAY0((now_utc or _dt.datetime.now(_dt.timezone.utc)) + _dt.timedelta(days=1))
    try:
        b.realized[101] = 0.0
        run_entry(st, b, [("GC", 1)])
        ck("5i 다음 거래일(17:00 CT 뒤)엔 다시 진입", sum(1 for c in b.calls if c[0] == "place_entry") == 3, st.skips)
    finally:
        E._daycap_day_start = _DAY0

    # 5j confirm-after-act: GC 청산이 안 되면 표시, 보고 없음, GC 손절은 남기고 닫힌 MGC 손절만 걷음, 다음 회차에 이어서
    _reset_state()
    b = FakePX(realized={101: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    b.marks = {GC: 2663.5, MGC: 2663.5}
    b.close_fail = {GC}
    _due_now(st)
    scan(st)
    ck("5j 플랫 미확인 = 오늘 표시, exit_ok, 청산 보고 없음 + 닫힌 MGC 손절만 취소(살아 있는 GC 손절 유지) + 래치 남음",
       not E.App._daycap_hit_today(st, "projectx", SC, _DAY0()[1]) and not st.evs and not st.fills
       and [o["contractId"] for o in b.orders] == [GC] and "projectx|150ktc-v2-1" in st._dc_fired,
       (b.orders, st.evs, st.fills))
    b.close_fail = set()
    b.marks = {GC: 2640.0}                                # 그 사이 가격이 내려가도 래치는 정리를 이어간다
    scan(st)
    ck("5k 다음 회차에 이어서 닫고 마무리(표시 + exit_ok 한 번 + 보고)", not b.pos and not b.orders
       and E.App._daycap_hit_today(st, "projectx", SC, _DAY0()[1])
       and [e for e in st.evs if e[0] == "exit_ok"] == [("exit_ok", "GC", {"why": "cap_tp"})] and not st._dc_fired,
       (b.pos, st.evs))
    # 5l 한 번에 하나: 감시 스레드가 돌고 있으면 새로 띄우지 않는다
    _started = []
    _thr = E.threading.Thread
    E.threading.Thread = lambda *a, **k: SimpleNamespace(start=lambda: _started.append(k.get("target")))
    try:
        st._dc_busy = True
        E.App._daycap_tick(st)
        _busy_started = list(_started)
        st._dc_busy = False
        E.App._daycap_tick(st)
    finally:
        E.threading.Thread = _thr
    ck("5l 한 번에 하나: 바쁠 땐 감시 스레드를 새로 띄우지 않고, 한가하면 하나", not _busy_started and len(_started) == 1,
       (_busy_started, _started))
    # 5m 발사 직전 재확인: 그 사이 포지션이 바뀌면 이번 회차엔 닫지 않는다
    _reset_state()
    b = FakePX(realized={101: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    b.marks = {GC: 2663.5, MGC: 2663.5}
    _hits = {"n": 0}

    def _shrink(bb):
        _hits["n"] += 1
        if _hits["n"] == 1:
            bb.pos[(101, MGC)] = 5                        # 첫 조회 뒤 회원이 MGC 4계약을 손으로 정리
    b.after_open_positions = _shrink
    _due_now(st)
    scan(st)
    ck("5m 발사 직전 다시 본 포지션이 다르면 닫지 않는다(다음 회차에 새로 판단)",
       not any(c[0] == "close_contract" for c in b.calls) and not getattr(st, "_dc_fired", {}), b.calls[-4:])
    b.after_open_positions = None
    # 5n 무장이 풀렸고 앱 진입 기록도 없으면 닫지 않는다
    st._dc_mon = {}
    st._sig_on = False
    _due_now(st)
    scan(st)
    ck("5n 신호 대기 꺼짐 + 앱 진입 기록 없음 = 감시 대상 아님(닫지 않음)", not any(c[0] == "close_contract" for c in b.calls))

    # 5o 그 계좌만: 같은 로그인의 다른 평가 계좌는 그대로
    _reset_state()
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 151000.0}, {"id": 102, "name": "150KTC-V2-2", "balance": 150000.0}],
               realized={101: 1000.0, 102: 0.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx"), ("GC", "150KTC-V2-2", P0, "projectx")])
    run_entry(st, b.view(SC), [("GC", 2), ("MGC", 9)])
    run_entry(st, b.view("150KTC-V2-2"), [("GC", 2), ("MGC", 9)], sc="150KTC-V2-2", cap=dict(CAP, sc="150KTC-V2-2", lbl="TS2"))
    ck("5o 맥락 = 나중에 확정된 평가 계좌(둘 다 평가면 덮는다)", st._open_ctx["GC"]["aid"] == 102)
    b.marks = {GC: 2663.5, MGC: 2663.5}                  # 101: 1,000 + 3,915 ≥ 4,860 / 102: 0 + 3,915 < 4,860
    _due_now(st)
    scan(st)
    ck("5p 상한에 닿은 101만 정리 - 102 포지션, 손절 그대로",
       {c[1] for c in b.calls if c[0] == "close_contract"} == {101} and b.pos.get((102, GC)) == 2
       and sorted(o["contractId"] for o in b.orders if o["aid"] == 102) == sorted([GC, MGC])
       and not [o for o in b.orders if o["aid"] == 101], (b.pos, b.orders))
    ck("5q 맥락이 다른 계좌(102)면 손대지 않고 청산 보고 없음, exit_ok만 한 번", st._open_ctx["GC"]["aid"] == 102
       and not st.fills and [e[0] for e in st.evs] == ["exit_ok"], (st.fills, st.evs))
    # 5r 맥락 넘김: 102가 맥락일 때 102가 먼저 닫히면 아직 열린 평가 계좌(103)로 맥락을 넘긴다
    _reset_state()
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 151000.0}, {"id": 102, "name": "150KTC-V2-2", "balance": 150000.0}],
               realized={101: 0.0, 102: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx"), ("GC", "150KTC-V2-2", P0, "projectx")])
    run_entry(st, b.view(SC), [("GC", 2), ("MGC", 9)])
    run_entry(st, b.view("150KTC-V2-2"), [("GC", 2), ("MGC", 9)], sc="150KTC-V2-2", cap=dict(CAP, sc="150KTC-V2-2", lbl="TS2"))
    b.marks = {GC: 2663.5, MGC: 2663.5}                  # 102만 닿음(맥락 계좌)
    _due_now(st)
    scan(st)
    ck("5r 맥락 계좌(102)가 닫히면 아직 열린 평가 계좌(101)로 맥락을 넘기고 자산 청산 보고는 하지 않는다",
       st._open_ctx["GC"]["aid"] in (101, "101") and st._open_ctx["GC"].get("cap") is True and not st.fills,
       (st._open_ctx.get("GC"), st.fills))
    b.realized[101] = 1000.0                              # 101의 다른 체결이 실현됐다(실현 손익은 60초마다 다시 읽는다)
    b.accts[0]["balance"] += 1000.0                       # 잔고도 같이(3차 리뷰: 정리 직전 잔고 - 오늘 실현을 맞춰 본다)
    _clk["t"] += 61
    _due_now(st)
    scan(st)
    ck("5s 마지막 평가 계좌까지 닫히면 그때 청산 보고 why=cap_tp 한 번", st.fills == [("GC", True, "cap_tp")], st.fills)
    # 5t 통과 익절: 잔고 157,000 + 미실현 ≥ 159,000 + $50(자동 = 실제로 통과하는 잔고 + 여유, 3차 결정 B) → 정리 + 통과 표시 + 1R 제안
    _reset_state()
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 157000.0}], realized={101: 0.0})
    b.trades = lambda aid, start_iso, timeout=None: []      # 거래 기록을 다 읽음(통과 표시 조건, 3차 리뷰)
    st = Stub(b)
    st.root = SimpleNamespace(after=lambda ms, fn=None, *a: fn() if fn else None)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    b.marks = {GC: 2657.3, MGC: 2657.3}                  # 미실현 2,117 → 잔고 + 미실현 159,117 ≥ 159,050
    _due_now(st)
    scan(st)
    ck("5t 통과 목표(가까운 쪽)에 닿으면 정리 + 통과 표시 + 1R 제안 한 번 + exit_ok cap_tp",
       not b.pos and not b.orders and "projectx|150ktc-v2-1" in E.App._dc_state(st)["passed"]
       and st.prompts == [("projectx", SC, SC[-4:], 450.0)] and ("exit_ok", "GC", {"why": "cap_tp"}) in st.evs
       and not E.App._daycap_hit_today(st, "projectx", SC, _DAY0()[1]), (b.pos, st.prompts, st.logs[-3:]))
    run_entry(st, b, [("GC", 2)])
    ck("5u 통과 뒤 다음 신호 = pass_reached(P1-a: 더 진입하지 않음)", st.skips[-1:] == [("GC", "pass_reached")], st.skips)
    # 5v 진입 뒤 실현 손익을 못 읽어도 통과 목표는 본다
    _reset_state()
    b = FakePX(accts=[{"id": 101, "name": SC, "balance": 157000.0}], realized={101: RuntimeError("down")})
    b.trades = lambda aid, start_iso, timeout=None: []
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    b.marks = {GC: 2657.3, MGC: 2657.3}
    _due_now(st)
    scan(st)
    ck("5v 실현 손익 조회가 계속 실패해도 통과 목표 감시는 돈다", not b.pos and "projectx|150ktc-v2-1" in E.App._dc_state(st)["passed"])
    # 5w 시세를 못 읽으면 그 회차는 판단하지 않는다
    _reset_state()
    b = FakePX(realized={101: 4900.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    b.pos[(101, GC)] = 2
    b.avgs[(101, GC)] = 2650.0
    _due_now(st)
    scan(st)
    ck("5w 시세 None = 판단 보류(닫지 않음)", not any(c[0] == "close_contract" for c in b.calls))
    # 5x 남은 주문 취소 실패 → 표시, 보고는 한 번, 다음 회차에 주문 정리만 다시
    _reset_state()
    b = FakePX(realized={101: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2)])
    b.marks = {GC: 2669.5}                                # GC 2: 문턱 4,860, 오늘 1,000 + 3,900
    _cx = b._cancel_order
    b._cancel_order = lambda aid, oid: (_ for _ in ()).throw(RuntimeError("cancel failed"))
    _due_now(st)
    scan(st)
    _ev1 = list(st.evs)
    ck("5x 손절 취소 실패 = 표시, 보고 한 번 + 래치 유지(주문 정리 다시)", "projectx|150ktc-v2-1" in st._dc_fired
       and _ev1 == [("exit_ok", "GC", {"why": "cap_tp"})] and b.orders, (st._dc_fired, b.orders))
    b._cancel_order = _cx
    scan(st)
    ck("5y 다음 회차에 남은 손절을 걷고 래치 풀림, 보고는 더 없음", not b.orders and not st._dc_fired and st.evs == _ev1,
       (b.orders, st.evs))

    # 5z 재시작: 디스크 기록만으로 [라이브 시작] 전에도 지켜보고 닫는다. [전체 정지]는 기록을 지운다.
    _reset_state()
    b = FakePX(realized={101: 1000.0})
    st = Stub(b)
    _arm(st, [("GC", SC, P0, "projectx")])
    run_entry(st, b, [("GC", 2), ("MGC", 9)])
    st2 = Stub(b)
    st2._acfg = st._acfg
    st2._dc_mon = E._load_daycap_mon()
    st2._sig_accts, st2._sig_on = {}, False
    _tg = E.App._daycap_mon_targets(st2)
    ck("5z1 재시작 뒤 무장 없이도 감시 대상(앱 진입 기록, 설정 + 키체인으로 브로커)",
       [(t["key"], t["armed"], t["cfg"].get("f1")) for t in _tg] == [("projectx|150ktc-v2-1", False, "u@x")], _tg)
    b.marks = {GC: 2663.5, MGC: 2663.5}
    scan(st2)
    ck("5z2 재시작 뒤 상한에 닿으면 정리 + 표시 + 보고", not b.pos and not b.orders
       and E.App._daycap_hit_today(st2, "projectx", SC, _DAY0()[1]) and ("exit_ok", "GC", {"why": "cap_tp"}) in st2.evs,
       (b.pos, st2.evs))
    _reset_state()
    st3 = Stub(b)
    st3._dc_mon = {"projectx|x": {"broker": "projectx", "sc": "x", "legs": [], "ts": time.time()}}
    E.App._daycap_mon_clear(st3)
    ck("5z3 [전체 정지] = 앱 진입 감시 기록 지움(회원이 넘겨받음) + 디스크", not st3._dc_mon and not E._load_daycap_mon())
    ck("5z4 [전체 정지]가 감시 기록을 지운다(배선)", "self._daycap_mon_clear()" in inspect.getsource(E.App._master_stop))
    ck("5z5 [라이브 시작]이 이번 실행 통과 멈춤을 푼다(배선)", '_dc_passed_run").clear()' in inspect.getsource(E.App._master_start))

    # ── 6 Lucid(NT8) ────────────────────────────────────────────────────────────
    class FakeNT8:
        name = "nt8"

        def __init__(self, acct="LDI150A1234"):
            self.acct, self.cash, self.nl, self.pos, self.closed, self.execs, self.calls = acct, 156000.0, 156000.0, {}, [], [], []

        def snapshot_fresh(self, max_age=10.0):
            return True

        def account_connected(self, a):
            return True

        def account_balance(self, a):
            self.calls.append(("account_balance", a))
            return self.cash

        def account_netliq(self, a):
            return self.nl

        def account_unrealized(self, a):                  # 브리지 미실현 = NetLiq - 현금(3차 리뷰: 정리 전에 맞춰 본다)
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

    LU = "LDI150A1234"

    def _app_leg(st, b, sc, con, q, asset="NQ"):
        """앱이 넣은 leg 기록(감시는 앱이 넣은 수량만 정리한다, 3차 리뷰 P0) - 시험이 포지션을 손으로 심을 때 같이 심는다."""
        E.App._daycap_mon_note(st, asset, b, {"broker": getattr(b, "name", "nt8"), "sc": sc}, sc, con, None,
                               "LONG" if q > 0 else "SHORT", abs(q), {"day": _DAY0()[1], "lbl": sc[-4:], "stage": "eval"})

    def _lucid_chain(st, bases):
        """오늘 앞 거래일들의 시작 잔고(처음 = 계좌 규모) - 앱이 처음부터 빠짐없이 본 Lucid 계좌(통과 표시 조건, 3차 리뷰 P0)."""
        days, d = [], _DAY0()[1]
        for _ in bases:
            x = _dt.date.fromisoformat(d) - _dt.timedelta(days=1)
            while x.weekday() >= 5:
                x -= _dt.timedelta(days=1)
            d = x.isoformat()
            days.append(d)
        E.App._dc_state(st)["nt8_bases"]["nt8|ldi150a1234"] = dict(zip(reversed(days), bases))
    _prl = dict(P0, stage="eval")
    _reset_state()
    n8 = FakeNT8()
    st = Stub(n8)
    _arm(st, [("NQ", LU, _prl, "nt8")])
    _lucid_chain(st, [150000.0, 152700.0])                # 처음부터 본 계좌(오늘 시작 155,700) - 최고일 3,000
    _s0 = _DAY0()[0]
    n8.execs = [{"acct": LU, "pnl": 300.0, "ts_ms": int(_s0.timestamp() * 1000) + 60000}]
    n8.pos = {"NQ 12-26": 2}
    _app_leg(st, n8, LU, "NQ 12-26", 2)
    n8.nl = 159000.0                                      # 오늘 손익 3,300(시작 155,700) < 4,410, 통과 익절 159,050 아래
    _due_now(st)
    scan(st)
    _base = E.App._dc_state(st)["nt8_base"].get("nt8|ldi150a1234")
    ck("6a Lucid 거래일 시작 잔고 = 지금 현금 - 오늘 실현(156,000 - 300), 문턱 아래면 그대로",
       _base and _base["base"] == 155700.0 and not n8.closed, (_base, n8.closed))
    st._open_ctx = {"NQ": {"aid": LU, "sym": "NQ 12-26"}}
    n8.nl = 159950.0                                      # 오늘 4,250 < 4,410 / 통과 익절 159,000 + 50 = 159,050 넘음 → 통과 쪽
    _due_now(st)
    scan(st)
    ck("6b Lucid 통과 목표(가까운 쪽)에 닿으면 정리 + 통과 표시 + 청산 보고 why=cap_tp",
       n8.closed == ["NQ 12-26"] and "nt8|ldi150a1234" in E.App._dc_state(st)["passed"] and st.fills == [("NQ", True, "cap_tp")],
       (n8.closed, st.fills, st.logs[-3:]))
    _reset_state()
    n8 = FakeNT8()
    n8.cash = n8.nl = 150000.0
    st = Stub(n8)
    _arm(st, [("NQ", LU, _prl, "nt8")])
    n8.pos = {"NQ 12-26": 2}
    _app_leg(st, n8, LU, "NQ 12-26", 2)
    n8.nl = 154500.0                                      # 오늘 4,500 ≥ 4,410, 통과 159,050 아래 → 하루 상한
    _due_now(st)
    scan(st)
    ck("6c Lucid 하루 상한(4,410 = 목표의 49%, 여유를 더 빼지 않음)에 닿으면 정리 + 오늘 표시, 통과 표시 없음",
       n8.closed == ["NQ 12-26"] and E.App._daycap_hit_today(st, "nt8", LU, _DAY0()[1])
       and not E.App._dc_state(st)["passed"], (n8.closed, st.logs[-3:]))
    _pl = E.App._daycap_plan(st, n8, {"broker": "nt8", "pr": _prl, "lbl": "LUC", "sc": LU, "risk": 1000.0}, {"name": LU, "id": LU},
                             LU, [("MNQ", 3, "MNQ 12-26")], "NQ", True)
    ck("6d Lucid: 오늘 닿은 계좌는 다음 신호 건너뜀(day_cap)", _pl == {"skip": True} and ("NQ", "day_cap") in st.skips, _pl)
    # 6e Lucid 통과 뒤: 이번 실행은 건너뛰고, 새 [라이브 시작] 뒤엔 상한 없이 종전대로(같은 계좌 펀디드 전환)
    _reset_state()
    n8 = FakeNT8()
    n8.cash = 159100.0
    st = Stub(n8)
    _arm(st, [("NQ", LU, _prl, "nt8")])
    _lucid_chain(st, [150000.0, 153100.0, 156100.0])      # 처음부터 본 계좌, 최고일 3,100 → 통과 159,000
    _capl = {"broker": "nt8", "pr": _prl, "lbl": "LUC", "sc": LU, "risk": 1000.0}
    _pl1 = E.App._daycap_plan(st, n8, _capl, {"name": LU, "id": LU}, LU, [("MNQ", 3, "MNQ 12-26")], "NQ", True)
    _pl2 = E.App._daycap_plan(st, n8, _capl, {"name": LU, "id": LU}, LU, [("MNQ", 3, "MNQ 12-26")], "NQ", True)
    st._dc_passed_run.clear()                            # 새 [라이브 시작]
    _pl3 = E.App._daycap_plan(st, n8, _capl, {"name": LU, "id": LU}, LU, [("MNQ", 3, "MNQ 12-26")], "NQ", True)
    ck("6e Lucid 통과: 이번 실행 두 번 건너뜀(pass_reached 한 번 + pass_done), 새 [라이브 시작] 뒤엔 상한 없이 종전대로(None)",
       _pl1 == {"skip": True} and _pl2 == {"skip": True} and _pl3 is None
       and st.skips == [("NQ", "pass_reached"), ("NQ", "pass_done")], (_pl1, _pl2, _pl3, st.skips))

    # ── 7 통과 익절 감시와 겹치지 않음, 상태 줄, 문구, 청산 보고 본문 ──────────────────
    _reset_state()
    st = Stub(None)
    st._acfg = {"GC": {"broker": "projectx", "accounts": [
        {"id": "150KTC-V2-1", "prop": dict(P0, on=True, type="test", pass_tp=159000.0), "on": True, "broker": "projectx"},
        {"id": "77777", "prop": dict(P0, on=True, type="test", pass_tp=53000.0), "on": True, "broker": "projectx"},
        {"id": "EXPRESS-V2-DLL-1", "prop": dict(P0, on=True, type="test", pass_tp=9000.0), "on": True, "broker": "projectx"}]}}
    st._accts_of = lambda a: st._acfg[a]["accounts"]
    st._sig_accts = {("GC", i): {"broker": "projectx", "acct": st._acfg["GC"]["accounts"][i]["id"], "live": True}
                     for i in range(3)}
    _tg = E.App._passtp_targets(st)
    ck("7a 통과 익절 감시(종전)는 평가 계좌를 빼고(평가 감시가 맡음), 모르는 이름과 회원이 넣은 그 밖 계좌는 종전 그대로",
       [t[1] for t in _tg] == [1, 2], _tg)
    _row = {"id": "150KTC-V2-1", "prop": dict(P0), "on": True, "broker": "projectx"}
    _t_ko = E.App._daycap_row_text(st, "GC", _row)
    st.lang = "en"
    _t_en = E.App._daycap_row_text(st, "GC", _row)
    st.lang = "ko"
    E.App._daycap_set_status(st, "projectx", "150KTC-V2-1", day=_DAY0()[1], room=2300.0, cap=4860.0, pass_tp=159050.0)
    _t_ko2 = E.App._daycap_row_text(st, "GC", _row)
    st.lang = "en"
    _t_en2 = E.App._daycap_row_text(st, "GC", _row)
    st.lang = "ko"
    ck("7b 상태 줄 KO/EN(결정 8 문안 그대로)", _t_ko == "익절: 하루 상한 $4,860, 통과 $159,050 중 가까운 쪽"
       and _t_ko2 == "익절: 하루 상한 $4,860, 통과 $159,050 중 가까운 쪽 (오늘 남은 $2,300)"
       and _t_en2 == "Take-profit: the nearer of daily cap $4,860 and pass $159,050 ($2,300 left today)", (_t_ko2, _t_en2))
    ck("7c 펀디드, 연습, 모르는 이름, 다른 브로커, 꺼진 행, 수동 행은 상태 줄 없음",
       all(E.App._daycap_row_text(st, "GC", dict(_row, **kw)) == "" for kw in (
           {"id": "EXPRESS-V2-DLL-1"}, {"id": "PRAC-V2-1"}, {"id": "77777"}, {"broker": "tradovate"}, {"on": False},
           {"manual": True})))
    ck("7d Lucid 미답 상태 줄(차분한 안내)", E.App._daycap_row_text(st, "NQ", {"id": LU, "prop": dict(P0), "on": True,
                                                                     "broker": "nt8"}).startswith("Lucid 계좌가 평가인지"))
    _names = [n for n in E.App.__dict__ if n.startswith(("_daycap", "_lucid", "_reco", "_apply_reco", "_margin", "_one_r_window"))]
    _src_new = "".join(inspect.getsource(getattr(E.App, n)) for n in _names)
    _strs = [s for s in re.findall(r'"([^"\n]*)"', _src_new) if any(ord(ch) > 127 for ch in s)]
    _badc = [s for s in _strs if "·" in s or "—" in s or any(0x1F300 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF
                                                              for ch in s)]
    ck("7e 이 기능의 새 문구에 이모지, 가운뎃점, em 대시 없음", not _badc, _badc)
    _kos = [s for s in _strs if re.search(r"[가-힣]", s) and s.rstrip().endswith(("요.", "요", "세요."))]
    ck("7f 이 기능의 한국어 문장은 합니다체('~요' 해요체 없음)", not _kos, _kos)
    # 7g 청산 보고 본문: why는 줄 때만
    import requests as _rq
    _posts = []

    class _R:
        ok = True
        text = "fl:ok"
    _rp = _rq.post
    _rq.post = lambda url, timeout=None, json=None: (_posts.append(json), _R())[1]
    import autopilot_crypto as _ac
    _pid = _ac.path_id
    _ac.path_id = lambda tok: "TID1"
    time.sleep, time.time = _real_sleep, _real_time
    try:
        class FStub:
            _gate = {"ok": True, "caps": {"autoentry": True}}
            _token = "tok"
            _profile = {}
            _FILL_RETRY_WINDOW_S = 5
            root = SimpleNamespace(after=lambda *a, **k: None)

            def _save_cfg(self, **k):
                pass
        FStub._send_fill = E.App.__dict__["_send_fill"]
        _fs = FStub()
        E.App._send_fill(_fs, "GC", closed=True, why="cap_tp")
        E.App._send_fill(_fs, "NQ", closed=True)
        _real_sleep(0.6)
    finally:
        _rq.post, _ac.path_id = _rp, _pid
        time.time = lambda: _clk["t"]
        time.sleep = lambda s: _clk.__setitem__("t", _clk["t"] + float(s))
    _byinst = {b0["inst"]: b0 for b0 in _posts}
    ck("7g 청산 보고: 평가 계좌 익절이면 why=cap_tp, 아니면 종전 본문(why 키 없음)",
       _byinst.get("GC", {}).get("why") == "cap_tp" and "why" not in _byinst.get("NQ", {"why": 1})
       and set(_byinst.get("NQ", {})) == {"tok_id", "inst", "mid", "qty_micro", "qty_btc", "armed"}, _posts)
    ck("7h 앱 버전 2026.10.14a", E.App._APP_VER == "2026.10.14a", E.App._APP_VER)
    # 7i _check_stop_closed: 감시가 정리 중인 계좌의 맥락은 사유 없는 청산으로 보고하지 않는다(그 밖은 종전 그대로)
    st = Stub(None)
    _b0 = SimpleNamespace(list_open_positions=lambda: [])
    st._open_assets = {"GC"}
    st._open_ctx = {"GC": {"b": _b0, "sym": GC, "aid": 101}}
    st._dc_fired = {"projectx|150ktc-v2-1": {"aid": 101, "sc": SC}}
    E.App._check_stop_closed(st)
    E.App._check_stop_closed(st)
    ck("7i 정리 중인 계좌의 맥락은 플랫이어도 _check_stop_closed가 보고하지 않음", not st.fills, st.fills)
    st._dc_fired = {}
    E.App._check_stop_closed(st)
    E.App._check_stop_closed(st)
    ck("7j 정리 중이 아니면 종전대로(두 번 플랫 → 청산 보고)", st.fills == [("GC", True, None)], st.fills)

    # ── 8 비대상 경로 = 12e(5bc4374c)와 같다 ───────────────────────────────────────
    def _trace(run, acct_name, pr, cap_on, bk="projectx"):
        _reset_state()
        b2 = FakePX(accts=[{"id": 101, "name": acct_name, "balance": 150000.0}], realized={101: 0.0})
        s2 = Stub(b2)
        if cap_on:
            run(s2, b2, acct_name, [("GC", 2), ("MGC", 3)], "LONG", 2645.0, SIG, True, _dt.datetime.now(), "GC", None,
                cap={"broker": bk, "pr": pr, "lbl": "L", "sc": acct_name, "risk": 2000.0})
        else:
            run(s2, b2, acct_name, [("GC", 2), ("MGC", 3)], "LONG", 2645.0, SIG, True, _dt.datetime.now(), "GC", None)
        _norm = [(c[0], {k: (re.sub(r"EQ-AP-\d+", "EQ-AP-T", v) if isinstance(v, str) else v) for k, v in c[1].items()})
                 if c[0] == "place_entry" else c for c in b2.calls]
        return _norm, s2
    _cases = (("EXPRESS-V2-DLL-1", dict(P0, on=True, type="test"), "펀디드 이름 + 저장된 test(옛 기본값)"),
              ("PRAC-V2-1", dict(P0, on=True, type="test"), "연습 계좌"),
              ("12345", dict(P0, on=True, type="test"), "모르는 이름 + 프롭 켜짐 test"),
              ("EXPRESS-V2-DLL-2", dict(P0, on=True, type="funded"), "펀디드 유형"))
    for _nm, _pr, _why in _cases:
        _c1, _s1 = _trace(E.App._run_futures_entry, _nm, _pr, True)
        _c0, _ = _trace(E.App._run_futures_entry, _nm, _pr, False)
        ck(f"8a 비대상({_why}): cap을 넘겨도 브로커 호출 순서, 인자가 cap 없는 경로와 똑같음(조회, 감시 없음)",
           _c1 == _c0 and not any(c[0] in ("day_realized", "open_positions") for c in _c1) and not _s1._dc_mon
           and not _s1.skips, (_c1, _c0))
    # 12e 원본과 비교: git에서 5bc4374c의 eqgui.py를 꺼내 같은 가짜로 돌린다(없으면 건너뜀을 알린다)
    _base_src = None
    try:
        _base_src = subprocess.run(["git", "-C", HERE, "show", "5bc4374c:executor/eqgui.py"], capture_output=True,
                                   text=True, timeout=30).stdout or None
    except Exception:
        _base_src = None
    if not _base_src:
        print("SKIP 8b~8d 12e 원본(5bc4374c)을 git에서 읽지 못해 원본 비교를 건너뜁니다")
    else:
        _bp = os.path.join(HOME, "eqgui_12e_base.py")
        with open(_bp, "w", encoding="utf-8") as f:
            f.write(_base_src)
        _spec = importlib.util.spec_from_file_location("eqgui_12e_base", _bp)
        B = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(B)
        ck("8b 12e 원본 = 2026.10.12e", B.App._APP_VER == "2026.10.12e")

        def _base_run(s2, b2, sc, legs, d, stop, sig, live, recv, asset, pub, cap=None):
            return B.App._run_futures_entry(s2, b2, sc, legs, d, stop, sig, live, recv, asset, pub)
        for _nm, _pr, _why in _cases + (("150KTC-V2-1", dict(P0), "Combine 이름(12e 비교용 - 새 빌드는 감시 대상, 아래 별도)"),):
            _c1, _s1 = _trace(E.App._run_futures_entry, _nm, _pr, True)
            _cb, _sb = _trace(_base_run, _nm, _pr, False)
            if _nm.startswith("150KTC"):
                _extra = [c for c in _c1 if c not in _cb]
                ck("8c Combine(평가): 12e 대비 더해진 브로커 호출은 오늘 실현 조회(day_realized) 하나뿐, 주문은 같다",
                   [c[0] for c in _extra] == ["day_realized"]
                   and [c for c in _c1 if c[0] == "place_entry"] == [c for c in _cb if c[0] == "place_entry"], _extra)
                continue
            ck(f"8b 비대상({_why}): 브로커 호출이 12e 원본과 똑같음", _c1 == _cb, (_c1, _cb))
        # 진입 밖 경로: 함수 원문이 12e와 같다(시간 청산, 전체 청산, 손절 유지, 손절 실패 처리, 통과 익절 판정)
        _same = ("_auto_loop", "_close_all_positions", "_check_stop_alive", "_check_stop_size", "_flatten_unprotected",
                 "_handle_stop_failure", "_passtp_scan", "_passtp_tick", "_passtp_buffer", "_passtp_pv", "_passtp_filter",
                 "_prop_one_r", "_precheck_tick", "_live_acct_cfg", "_master_arm")
        _diff = [n for n in _same if inspect.getsource(getattr(E.App, n)) != inspect.getsource(getattr(B.App, n))]
        ck("8d 진입 밖 경로 함수 원문이 12e와 같음(시간 청산, 전체 청산, 손절, 통과 익절 판정, 프롭 1R, 무장)", not _diff, _diff)
    # 8e _enter_account: Topstep, Lucid만 cap 인자, 나머지 브로커는 종전 호출. 킬스위치면 Topstep도 종전.
    _seen_kw = []

    class EStub(Stub):
        def _run_futures_entry(self, *a, **k):
            _seen_kw.append((a[0].name, sorted(k), (k.get("cap") or {}).get("risk")))

        def _jitter_stop(self, asset, ref, stop, lbl):
            return stop

        def _live_now(self, v):
            return True
    EStub._enter_account = E.App.__dict__["_enter_account"]
    _bb = E._build_broker
    try:
        for _bk in ("projectx", "nt8", "ibkr"):
            E._build_broker = (lambda bk: (lambda *a, **k: SimpleNamespace(name=bk)))(_bk)
            E.App._enter_account(EStub(None), {"broker": _bk, "f1": "u", "f2": "k", "f3": "", "acct": "A1", "one_r": 2200.0,
                                               "label": "L", "live": True, "prop": {}, "pct": {}},
                                 {"id": "s1", "entry_ref": 21000.0}, "NQ", "LONG", 20950.0, 1.5)
        os.environ["EQ_DAYCAP_OFF"] = "1"
        E._build_broker = lambda *a, **k: SimpleNamespace(name="projectx")
        E.App._enter_account(EStub(None), {"broker": "projectx", "f1": "u", "f2": "k", "f3": "", "acct": "A1", "one_r": 2200.0,
                                           "label": "L", "live": True, "prop": {}, "pct": {}},
                             {"id": "s1", "entry_ref": 21000.0}, "NQ", "LONG", 20950.0, 1.5)
    finally:
        E._build_broker = _bb
        os.environ.pop("EQ_DAYCAP_OFF", None)
    ck("8e _enter_account: projectx, nt8만 cap(위험 = 1R x 배수 3,300), ibkr는 종전 호출, 킬스위치면 Topstep도 종전",
       _seen_kw == [("projectx", ["cap"], 3300.0), ("nt8", ["cap"], 3300.0), ("ibkr", [], None), ("projectx", [], None)],
       _seen_kw)
    # 8f 킬스위치: 감시 대상 없음
    os.environ["EQ_DAYCAP_OFF"] = "1"
    try:
        st = Stub(FakePX())
        _arm(st, [("GC", SC, P0, "projectx")])
        ck("8f 킬스위치 EQ_DAYCAP_OFF=1 = 감시 대상 없음, 판별 none", E.App._daycap_mon_targets(st) == []
           and E._daycap_stage("projectx", P0, SC) == ("none", 0))
    finally:
        os.environ.pop("EQ_DAYCAP_OFF", None)
    # 8g 펀디드, 연습, 모르는 계좌는 무장돼 있어도 감시가 브로커를 부르지 않는다(호출 0)
    _reset_state()
    b = FakePX(accts=[{"id": 201, "name": "EXPRESS-V2-DLL-1", "balance": 0.0}, {"id": 202, "name": "PRAC-V2-1", "balance": 50000.0}])
    st = Stub(b)
    _arm(st, [("GC", "EXPRESS-V2-DLL-1", dict(P0, on=True, type="test"), "projectx"), ("GC", "PRAC-V2-1", P0, "projectx"),
              ("GC", "55555", P0, "projectx"), ("NQ", "Sim101", P0, "nt8"), ("NQ", LU, P0, "nt8")])
    _due_now(st)
    scan(st)
    ck("8g 무장된 펀디드(옛 'test' 저장 포함), 연습, 모르는 이름, NT8 Sim, Lucid 미답 = 감시 대상 아님 + 브로커 호출 0",
       E.App._daycap_mon_targets(st) == [] and b.calls == [], b.calls)
finally:
    time.sleep, time.time = _real_sleep, _real_time
    E._daycap_day_start = _DAY0

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
