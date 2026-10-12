#!/usr/bin/env python3
# EQ Autopilot - 빈 계좌 목록 청산 금지(최종 반박 리뷰 2026-10-08 P0) 오프라인 시험. 0=통과
# 선물 로그인 하나에 계좌가 여럿이다. 계좌 목록이 빈 어댑터의 flatten_all은 그 로그인의 계좌 전부를 닫는다.
#   A  어댑터(ProjectX·Tradovate·IBKR·NT8): 계좌 목록이 비면 flatten_all이 아무것도 하지 않고 오류를 돌려준다
#      (dry_run이어도 계획을 세우지 않는다). 계좌를 지정하면 그 계좌만.
#   B  _auto_loop: 계좌 ID 없는 선물 마감 청산 잡은 브로커를 만들지도 않고 오류만 남긴다. 크립토(계좌 ID 없음이 정상)는 그대로.
#   C  _master_arm: 계좌 ID 없는 선물 행은 무장하지 않는다(잡·사본 0).
# 네트워크·실제 설정을 건드리지 않는다(HTTP·시계·스레드 전부 가짜).
import datetime as _dt
import os
import socket
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
from eqexec.broker import ibkr as IB  # noqa: E402
from eqexec.broker import nt8 as N8  # noqa: E402
from eqexec.broker import projectx as PX  # noqa: E402
from eqexec.broker import tradovate as TV  # noqa: E402
from eqexec.broker.base import Position, configured_accounts  # noqa: E402
from eqexec.config import IBKRCfg, NT8Cfg, ProjectXCfg, TradovateCfg  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


# ── A: 어댑터 ────────────────────────────────────────────────────────────────
PX._CLOSE_RETRY_WAIT = 0
PX._RECHECK_WAIT = 0
CALLS = []


def _px_post(self, path, body):
    CALLS.append(path)
    if path == "/api/Account/search":
        return {"accounts": [{"id": 101, "name": "150KTC-A"}, {"id": 102, "name": "50KTC-B"},
                             {"id": 103, "name": "50KTC-C"}]}
    if path == "/api/Position/searchOpen":
        return {"positions": [{"contractId": "CON.F.US.MNQ.Z26", "size": 2, "type": 1}]}
    return {"success": True, "orders": []}


PX.ProjectXBroker._post = _px_post
for _acc in ([], [""], ["  "]):
    CALLS.clear()
    b = PX.ProjectXBroker(ProjectXCfg(user_name="m@x.com", api_key="K", accounts=list(_acc)))
    for _dry in (True, False):
        r = b.flatten_all(dry_run=_dry)
        ck(f"A ProjectX accounts={_acc!r} dry_run={_dry} → 거절(계획·청산 0, 오류 1)",
           not r.planned and not r.closed and len(r.errors) == 1 and "no account id" in r.errors[0], r)
    ck(f"A ProjectX accounts={_acc!r} → HTTP 호출 0", CALLS == [], CALLS)
b = PX.ProjectXBroker(ProjectXCfg(user_name="m@x.com", api_key="K", accounts=["150KTC-A"]))
r = b.flatten_all(dry_run=True)
ck("A ProjectX 계좌 지정 → 그 계좌만 계획", sorted({p.account_name for p in r.planned}) == ["150KTC-A"] and not r.errors, r)

tv = TV.TradovateBroker(TradovateCfg(accounts=[]))
tv.list_open_positions = MagicMock(return_value=[Position("1", "A", "MNQZ6", 1, {"contractId": 5})])
tv._post = MagicMock()
for _dry in (True, False):
    r = tv.flatten_all(dry_run=_dry)
    ck(f"A Tradovate 빈 계좌 dry_run={_dry} → 거절", r.errors and not r.planned, r)
ck("A Tradovate 빈 계좌 → 조회·주문 0", tv.list_open_positions.call_count == 0 and tv._post.call_count == 0)

ib = IB.IBKRBroker(IBKRCfg(accounts=[]))
ib._connect = MagicMock(side_effect=AssertionError("must not connect"))
for _dry in (True, False):
    r = ib.flatten_all(dry_run=_dry)
    ck(f"A IBKR 빈 계좌 dry_run={_dry} → 거절·접속 0", r.errors and not r.planned and ib._connect.call_count == 0, r)

nb = N8.NT8Broker(NT8Cfg(accounts=[]))
nb._enqueue_and_wait = MagicMock()
nb.list_open_positions = MagicMock(return_value=[Position("LT-A", "LT-A", "MNQ", 1, {})])
for _dry in (True, False):
    r = nb.flatten_all(dry_run=_dry)
    ck(f"A NT8 빈 계좌 dry_run={_dry} → 거절", r.errors and not r.planned, r)
ck("A NT8 빈 계좌 → 브리지 명령 0(accounts=None = 전부 청산 명령이 안 나감)", nb._enqueue_and_wait.call_count == 0)
nb2 = N8.NT8Broker(NT8Cfg(accounts=["LT-A"]))
nb2._enqueue_and_wait = MagicMock(return_value={})
nb2.list_open_positions = MagicMock(return_value=[Position("LT-A", "LT-A", "MNQ", 1, {})])
r = nb2.flatten_all(dry_run=False)
ck("A NT8 계좌 지정 → 명령의 accounts = 그 계좌", not r.errors
   and nb2._enqueue_and_wait.call_args[0][0].get("accounts") == ["LT-A"], nb2._enqueue_and_wait.call_args)
ck("A configured_accounts: 공백 제외", configured_accounts(SimpleNamespace(cfg=SimpleNamespace(accounts=["", " A "]))) == ["A"])
ck("A configured_accounts: cfg 없음 = []", configured_accounts(SimpleNamespace()) == [])

# ── B: _auto_loop ────────────────────────────────────────────────────────────
ET = _dt.timezone(_dt.timedelta(hours=-4))
AP = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True, "fullexit": True}}


def _me(jobs):
    me = MagicMock()
    me.lang = "ko"
    me._gate = AP
    me._auto_on = True
    me._exit_override = {}
    me._entered_at = {}
    me._entering_at = {}
    me._auto_accts = {(j["asset"], i): [j] for i, j in enumerate(jobs)}
    me._full_exit = lambda: eqgui.App._full_exit(me)
    me._close_mode = lambda j: (True, False)
    me._btc_x2be_step = MagicMock(return_value=True)
    return me


def run_loop(me, times, builds):
    seq = list(times)
    clock = {"now": seq.pop(0)}
    orig = (eqgui._build_broker, eqgui._now_in, time.sleep, time.time)

    def _bb(broker, f1, f2, f3, accounts):
        bb = MagicMock()
        bb.flatten_all.return_value = SimpleNamespace(planned=[], closed=[], errors=[])
        bb.position_qty.return_value = 0.5
        builds.append((broker, list(accounts), bb))
        return bb
    eqgui._build_broker = _bb
    eqgui._now_in = lambda _tz: clock["now"]
    time.time = lambda: clock["now"].timestamp()

    def _sleep(_s):
        if seq:
            clock["now"] = seq.pop(0)
        else:
            me._auto_on = False
    time.sleep = _sleep
    try:
        eqgui.App._auto_loop(me)
    finally:
        eqgui._build_broker, eqgui._now_in, time.sleep, time.time = orig
    return " ".join(str(c[0][0]) for c in me.log.call_args_list)


for _bk in ("projectx", "nt8", "tradovate", "ibkr"):
    _j = {"asset": "NQ", "hour": 14, "tz": "America/New_York", "broker": _bk, "acct": "", "f1": "k", "f2": "s", "f3": "", "live": True}
    me = _me([_j])
    builds = []
    lg = run_loop(me, [_dt.datetime(2026, 10, 6, 14, 0, 5, tzinfo=ET), _dt.datetime(2026, 10, 6, 14, 2, tzinfo=ET)], builds)
    ck(f"B {_bk} 계좌 ID 없는 마감 청산 잡 → 브로커 안 만듦·청산 0", builds == [], builds)
    ck(f"B {_bk} 오류 로그 + 서버 보고", "마감 청산을 하지 않았습니다" in lg and "계좌 ID가 없습니다" in lg
       and me._report_error.call_count == 1, (lg[-200:], me._report_error.call_args_list))
_j = {"asset": "NQ", "hour": 14, "tz": "America/New_York", "broker": "projectx", "acct": "150KTC-A", "f1": "k", "f2": "s", "f3": "", "live": True}
builds = []
lg = run_loop(_me([_j]), [_dt.datetime(2026, 10, 6, 14, 0, 5, tzinfo=ET)], builds)
ck("B 계좌 ID 있는 선물 잡은 그대로 청산(그 계좌로)", len(builds) == 1 and builds[0][1] == ["150KTC-A"]
   and builds[0][2].flatten_all.call_count == 1, builds)
_j = {"asset": "BTC", "hour": 22, "tz": "UTC", "broker": "bybit", "acct": "", "f1": "k", "f2": "s", "f3": "", "live": True}
builds = []
lg = run_loop(_me([_j]), [_dt.datetime(2026, 10, 5, 22, 0, 5, tzinfo=_dt.timezone.utc)], builds)   # 월요일 22 UTC = 24h 만기
ck("B 크립토(계좌 ID 없음이 정상)는 그대로 청산", len(builds) == 1 and builds[0][2].flatten_all.call_count == 1,
   (builds, lg[-200:]))

# ── C: _master_arm ───────────────────────────────────────────────────────────
me = MagicMock()
me.lang = "ko"
me._auto_accts, me._sig_accts, me._sig_parked = {}, {}, {}
me._auto_on, me._sig_on = True, True
me._acfg = {"NQ": {"accounts": [{"id": "", "on": True, "broker": "projectx", "one_r": 600},
                                {"id": "150KTC-A", "on": True, "broker": "projectx", "one_r": 600}]}}
me._accts_of = lambda a: me._acfg[a]["accounts"]
me._acct_broker = lambda a, ac: ac.get("broker") or "projectx"
me._creds_of = lambda a, bk: {"f1": "m@x.com", "f3": ""}
_of2 = eqgui._f2_load
eqgui._f2_load = lambda f1: "K"
try:
    r0 = eqgui.App._master_arm(me, "NQ", 0, True, True)
    ck("C 계좌 ID 없는 선물 행 → 무장 안 함(False)·잡·사본 0", r0 is False and not me._auto_accts and not me._sig_accts,
       (r0, me._auto_accts, me._sig_accts))
    ck("C 이유 로그", any("계좌 ID가 없어" in str(c[0][0]) for c in me.log.call_args_list), me.log.call_args_list)
    r1 = eqgui.App._master_arm(me, "NQ", 1, True, True)
    ck("C 계좌 ID 있는 행은 무장(True)", r1 is True and ("NQ", 1) in me._auto_accts and ("NQ", 1) in me._sig_accts
       and all(j["acct"] == "150KTC-A" for j in me._auto_accts[("NQ", 1)]), (r1, me._auto_accts))
finally:
    eqgui._f2_load = _of2

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
