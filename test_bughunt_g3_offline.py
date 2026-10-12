#!/usr/bin/env python3
# EQ Autopilot - 오픈 준비 버그 조사(2026-10-06) g3 청산 경로 오프라인 시험. 0=통과
# F  _auto_loop: 실패하면 fired를 적지 않고 같은 창 안에서 60초마다 재시도, 첫 실패만 팝업·서버 보고,
#    재시도로 끝나면 회원에게 결과, 창이 닫히도록 못 끝내면 한 번 알림. 선행(FOMC) 청산 실패해도 정규 시각 창까지
#    이어서 시도, 같은 자산 다계좌 모두 앞당긴 시각에 발화(서버 값 pop 제거), BTC에는 서버 시각을 쓰지 않음.
#    BTC 24h 만기(k=5): 봉·포지션 조회 실패여도 청산 경로로(수량·목록 불일치는 재시도).
# G  ProjectX flatten_all: 청산 실패·미확인 계약의 주문(보호 손절)은 남기고 플랫 확인된 계약의 주문만 취소,
#    closeContract 1회 재시도, 재조회 실패면 청산 대상 계약 주문을 남김.
# X  [모든 포지션 청산]: 오류가 있으면 '완료'가 아니라 '미완료'+계좌 라벨, 팝업, 서버 보고.
# 네트워크·브로커·실제 설정을 건드리지 않는다(브로커·시계·팝업·스레드 전부 가짜).
import datetime as _dt
import os
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
from eqexec.broker import projectx as PX  # noqa: E402
from eqexec.broker.base import FlattenResult, Position  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


AP = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True, "fullexit": True}}
ET = _dt.timezone(_dt.timedelta(hours=-4))          # 10월 = EDT
UTC = _dt.timezone.utc


def _res(errors=(), planned=(1,), closed=()):
    r = MagicMock()
    r.planned = list(planned); r.closed = list(closed); r.errors = list(errors)
    return r


def _me(jobs, ovr=None, live=True):
    me = MagicMock()
    me.lang = "ko"
    me._gate = AP
    me._auto_on = True
    me._exit_override = dict(ovr or {})
    me._entered_at = {}
    me._entering_at = {}
    me._auto_accts = {(j["asset"], i): [j] for i, j in enumerate(jobs)}
    me._full_exit = lambda: eqgui.App._full_exit(me)
    me._close_mode = lambda j: (live, False)
    me._btc_x2be_step = MagicMock(return_value=True)
    return me


def _job(asset="NQ", hour=14, acct="a1", broker="projectx"):
    tz = "UTC" if asset == "BTC" else "America/New_York"
    return {"asset": asset, "hour": hour, "tz": tz, "broker": broker, "acct": acct,
            "f1": "k", "f2": "s", "f3": "", "live": True}


def run_loop(me, times, broker):
    """times = 사이클마다의 현재 시각(aware). 사이클 사이 sleep에서 다음 시각으로 옮기고, 다 쓰면 루프를 끈다."""
    seq = list(times)
    clock = {"now": seq.pop(0)}
    orig = (eqgui._build_broker, eqgui._now_in, time.sleep, time.time)
    eqgui._build_broker = lambda *a, **k: broker
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


def _ctxs(mock):
    return [c[0][0] for c in mock.call_args_list]


def T(h, m, s=0, day=6, tz=ET):
    return _dt.datetime(2026, 10, day, h, m, s, tzinfo=tz)


# ── F① 예외 → 같은 창 60초 뒤 재시도 → 성공 ─────────────────────────────────────
me = _me([_job()])
b = MagicMock()
b.flatten_all.side_effect = [RuntimeError("HTTP 502"), _res()]
lg = run_loop(me, [T(14, 0, 5), T(14, 0, 30), T(14, 1, 10), T(14, 1, 40), T(14, 5)], b)
ck("F① 실패 뒤 재시도(60초 전엔 안 함) → 성공 후 소진: flatten 2회", b.flatten_all.call_count == 2, b.flatten_all.call_count)
ck("F① 서버 보고는 첫 실패 1회(auto_close)", _ctxs(me._report_error) == ["auto_close"], me._report_error.call_args_list)
ck("F① 첫 실패 팝업 1회", me.root.after.call_count == 1, me.root.after.call_count)
ck("F① 재시도로 끝나면 회원에게 결과(autoclose_ok)", _ctxs(me._member_alert) == ["autoclose_ok"], me._member_alert.call_args_list)
ck("F① 로그: 재시도 예고·재시도 표시", "60초 뒤 다시 시도" in lg and "재시도 1" in lg, lg)
_ko = me._member_alert.call_args[0][1]
ck("F① 회원 문구: 사실만(지시형 없음)", "2번째 시도" in _ko and "하세요" not in _ko and "하십시오" not in _ko, _ko)

# ── F② res.errors 계속 → 창 끝에 한 번 알림, 그 뒤 조용 ─────────────────────────
me = _me([_job()])
b = MagicMock()
b.flatten_all.return_value = _res(errors=["STILL OPEN after flatten: X/MNQ net=2"])
lg = run_loop(me, [T(14, 0, 5), T(14, 1, 10), T(14, 29, 30), T(14, 30, 5), T(14, 31), T(14, 45)], b)
ck("F② 창 안에서만 재시도(14:00·14:01·14:29 = 3회)", b.flatten_all.call_count == 3, b.flatten_all.call_count)
ck("F② 팝업은 첫 실패 1회", me.root.after.call_count == 1, me.root.after.call_count)
ck("F② 서버 보고: 첫 실패 + 포기 1회씩", _ctxs(me._report_error) == ["auto_close", "auto_close_gaveup"],
   me._report_error.call_args_list)
ck("F② 회원 포기 알림 1회", _ctxs(me._member_alert) == ["autoclose_gaveup"], me._member_alert.call_args_list)
_ko, _en = me._member_alert.call_args[0][1], me._member_alert.call_args[0][2]
ck("F② 포기 문구 KO/EN 한 쌍, 사실만", "3번 시도" in _ko and "더 시도하지 않습니다" in _ko and "3 times" in _en
   and "하세요" not in _ko and "하십시오" not in _ko, (_ko, _en))

# ── F③ 선행(FOMC 13:58) 청산 실패 → 정규 시각 창까지 이어서 시도 ───────────────────
ovr = {"NQ": (T(13, 58).timestamp(), "FOMC")}
me = _me([_job()], ovr=ovr)
b = MagicMock()
b.flatten_all.side_effect = [RuntimeError("502"), RuntimeError("502"), _res()]
lg = run_loop(me, [T(13, 58, 5), T(13, 59, 10), T(14, 0, 15), T(14, 1, 30), T(14, 20)], b)
ck("F③ 13:58 실패 → 13:59 재시도 → 14:00 성공 후 소진(3회)", b.flatten_all.call_count == 3, b.flatten_all.call_count)
ck("F③ 선행 발화 로그", "선행 청산 발화 (FOMC)" in lg, lg)
# 정규 창 끝(14:30) 직전까지 실패하면 그때까지 시도
me = _me([_job()], ovr=ovr)
b = MagicMock()
b.flatten_all.side_effect = RuntimeError("502")
run_loop(me, [T(13, 58, 5), T(14, 29, 10), T(14, 30, 10)], b)
ck("F③ 선행 실패 시 정규 시각+30분(14:29)까지 시도", b.flatten_all.call_count == 2, b.flatten_all.call_count)

# ── F④ 같은 자산 다계좌 + 선행: 두 계좌 모두 13:58에(서버 값 소진 안 함) ─────────────
me = _me([_job(acct="a1"), _job(acct="a2")], ovr=ovr)
b = MagicMock()
b.flatten_all.return_value = _res()
run_loop(me, [T(13, 58, 5)], b)
ck("F④ 둘째 계좌도 앞당긴 시각에 청산(2회)", b.flatten_all.call_count == 2, b.flatten_all.call_count)
ck("F④ 서버 지정 시각을 pop하지 않음", "NQ" in me._exit_override, me._exit_override)

# ── F⑤ BTC에는 서버 지정 시각을 쓰지 않음(판정 시각 여섯 개로 퍼지지 않게) ──────────────
me = _me([_job("BTC", 6, broker="bybit"), _job("BTC", 22, broker="bybit")],
         ovr={"BTC": (T(2, 0, tz=UTC).timestamp(), "x")})
b = MagicMock(); b.position_qty.return_value = 0.2; b.flatten_all.return_value = _res()
run_loop(me, [T(2, 0, 5, tz=UTC)], b)
ck("F⑤ BTC 06·22시 잡이 02:00에 발화하지 않음", b.flatten_all.call_count == 0 and me._btc_x2be_step.call_count == 0,
   (b.flatten_all.call_count, me._btc_x2be_step.call_count))

# ── F⑥ 첫 시도 성공이면 종전 그대로(1회, 알림 없음) ─────────────────────────────
me = _me([_job()])
b = MagicMock(); b.flatten_all.return_value = _res()
run_loop(me, [T(14, 0, 5), T(14, 1, 10), T(14, 10)], b)
ck("F⑥ 성공 1회 후 그날 다시 안 함", b.flatten_all.call_count == 1, b.flatten_all.call_count)
ck("F⑥ 성공이면 보고·팝업·DM 없음", me._report_error.call_count == 0 and me.root.after.call_count == 0
   and me._member_alert.call_count == 0)

# ── F⑦ dry-run 실패: 팝업·회원 DM 없음(재시도·운영 보고는 함) ──────────────────────
me = _me([_job()], live=False)
b = MagicMock(); b.flatten_all.side_effect = RuntimeError("read failed")
run_loop(me, [T(14, 0, 5), T(14, 30, 5)], b)
ck("F⑦ dry-run: 팝업 없음", me.root.after.call_count == 0, me.root.after.call_count)
ck("F⑦ dry-run: 회원 포기 DM 없음", me._member_alert.call_count == 0, me._member_alert.call_args_list)

# ── F⑧ BTC 만기 블록에서 포지션 상태 불일치(None) → flatten 없이 실패·재시도 ─────────────
me = _me([_job("BTC", 22, broker="bybit")])
me._btc_x2be_step = MagicMock(side_effect=[None, True])
b = MagicMock(); b.position_qty.return_value = 0.2; b.flatten_all.return_value = _res()
lg = run_loop(me, [T(22, 0, 5, day=5, tz=UTC), T(22, 1, 10, day=5, tz=UTC)], b)
ck("F⑧ None → 이번엔 청산 안 함, 60초 뒤 재판정 → 청산", me._btc_x2be_step.call_count == 2
   and b.flatten_all.call_count == 1, (me._btc_x2be_step.call_count, b.flatten_all.call_count, lg))
ck("F⑧ None 실패는 서버 보고 1회", _ctxs(me._report_error) == ["auto_close"], me._report_error.call_args_list)

# ── F⑨ x2be_decide: 24h 만기는 봉 없이도 close, 그 밖은 종전 그대로 ──────────────────
xd = eqgui.App.x2be_decide
ck("F⑨ k=5 봉 조회 실패여도 close", xd(5, None, None, True) == "close" and xd(5, None, None, False) == "close")
ck("F⑨ k<5 봉 조회 실패는 hold(종전)", all(xd(k, None, None, True) == "hold" for k in range(5)))
ck("F⑨ 봉 있으면 종전과 같음", xd(5, 100, 90, True) == "close" and xd(2, 100, 90, True) == "close"
   and xd(2, 100, 110, True) == "trail" and xd(0, 100, 110, False) == "close" and xd(0, 100, 90, False) == "trail")

# ── F⑩ _btc_x2be_step: 만기 블록 조회 실패 → 청산 경로, 그 밖은 종전 홀드 ─────────────
def step(hour, qty=0.2, qty_exc=None, lst=None, lst_exc=None, blk=(100.0, 101.0)):
    m = MagicMock(); m.x2be_decide = eqgui.App.x2be_decide
    bb = MagicMock()
    if qty_exc:
        bb.position_qty.side_effect = qty_exc
    else:
        bb.position_qty.return_value = qty
    if lst_exc:
        bb.list_open_positions.side_effect = lst_exc
    else:
        bb.list_open_positions.return_value = lst if lst is not None else [
            Position(account_id="", account_name="", symbol="BTCUSDT", net_qty=0.2, raw={})]
    bb.block_4h.return_value = blk
    return eqgui.App._btc_x2be_step(m, bb, {"hour": hour}, True, UTC), m


r, _ = step(22, qty_exc=RuntimeError("net"))
ck("F⑩ k=5 수량 조회 실패 → True(청산 경로)", r is True, r)
r, _ = step(22, lst_exc=RuntimeError("net"))
ck("F⑩ k=5 목록 조회 실패 → True", r is True, r)
r, _ = step(22, lst=[Position(account_id="", account_name="", symbol="ETHUSDT", net_qty=1, raw={})])
ck("F⑩ k=5 수량 있는데 목록에 BTC 없음 → None(재시도, 거짓 '플랫' 방지)", r is None, r)
r, _ = step(22, blk=None)
ck("F⑩ k=5 봉 조회 실패 → True(24h 만기)", r is True, r)
r, _ = step(6, qty_exc=RuntimeError("net"))
ck("F⑩ k=1 수량 조회 실패 → False(종전 홀드)", r is False, r)
r, _ = step(6, blk=None)
ck("F⑩ k=1 봉 조회 실패 → False(종전 홀드)", r is False, r)
r, _ = step(6, lst=[])
ck("F⑩ k=1 목록 불일치 → False(종전)", r is False, r)

# ── G: ProjectX flatten_all ──────────────────────────────────────────────────
PX._CLOSE_RETRY_WAIT = 0
PX._RECHECK_WAIT = 0
NQC, GCC, ESC = "CON.F.US.MNQ.Z26", "CON.F.US.MGC.Z26", "CON.F.US.EP.Z26"


def _pos(con, acct=11, net=2):
    return Position(account_id=str(acct), account_name=f"ACC{acct}", symbol=con, net_qty=net,
                    raw={"_accountId": acct, "contractId": con, "size": abs(net), "type": 1})


def px(plan, rechecks, orders, close_fail=None):
    """plan=첫 조회, rechecks=이후 조회 결과 목록(Exception이면 raise), orders={aid:[...]}, close_fail={con: 실패 횟수}."""
    bx = PX.ProjectXBroker.__new__(PX.ProjectXBroker)
    bx.cfg = SimpleNamespace(accounts=["ACC11"])     # 계좌를 지정한 인스턴스(빈 목록이면 flatten_all이 거절 - 2026-10-08 P0)
    log = {"close": [], "cancel": []}
    seq = [plan] + list(rechecks)
    fails = dict(close_fail or {})

    def _lop():
        v = seq.pop(0) if seq else []
        if isinstance(v, Exception):
            raise v
        return list(v)

    def _post(path, body):
        if path.endswith("closeContract"):
            con = body["contractId"]
            log["close"].append(con)
            if fails.get(con, 0) > 0:
                fails[con] -= 1
                raise RuntimeError(f"ProjectX closeContract failed ({con})")
            return {"success": True}
        raise AssertionError(path)
    bx.list_open_positions = _lop
    bx._post = _post
    bx._accounts = lambda active_only=True: [{"id": a, "name": f"ACC{a}"} for a in orders]
    bx._open_orders = lambda aid: list(orders.get(aid, []))
    bx._cancel_order = lambda aid, oid: log["cancel"].append(oid)
    return bx.flatten_all(dry_run=False), log


ORD = {11: [{"id": 1, "contractId": NQC, "type": 4}, {"id": 2, "contractId": GCC, "type": 4},
            {"id": 3, "contractId": ESC, "type": 4}]}
# G① NQ 청산 두 번 다 실패(포지션 남음), GC 성공 → NQ 손절(1)은 남기고 GC(2)·고아(3)만 취소
res, lg = px([_pos(NQC), _pos(GCC)], 내부 기록, ORD, close_fail={NQC: 2})
ck("G① 청산 실패 계약의 보호 손절은 취소 안 함", 1 not in lg["cancel"], lg)
ck("G① 플랫 확인된 계약·포지션 없던 계약의 주문은 취소", sorted(lg["cancel"]) == [2, 3], lg)
ck("G① closeContract 1회 재시도(NQ 2번 호출)", lg["close"].count(NQC) == 2 and lg["close"].count(GCC) == 1, lg)
ck("G① 오류에 청산 실패 + STILL OPEN", any("closeContract failed" in e for e in res.errors)
   and any(e.startswith("STILL OPEN after flatten") for e in res.errors), res.errors)
ck("G① closed = GC만", [p.symbol for p in res.closed] == [GCC], res.closed)
# G② 첫 시도만 실패 → 재시도 성공 → 플랫 → 전부 정리, 오류 없음
res, lg = px([_pos(NQC)], [[]], ORD, close_fail={NQC: 1})
ck("G② 재시도로 청산되면 오류 없음·주문 정리", not res.errors and sorted(lg["cancel"]) == [1, 2, 3], (res.errors, lg))
# G③ 재조회 실패 → 청산 대상(NQ) 주문은 남기고, 포지션 없던 계약 주문만 정리
res, lg = px([_pos(NQC)], [RuntimeError("timeout")], ORD)
ck("G③ 재조회 실패면 청산 대상 계약 주문 유지", 1 not in lg["cancel"] and sorted(lg["cancel"]) == [2, 3], lg)
ck("G③ 재조회 실패는 오류로", any("re-check failed" in e for e in res.errors), res.errors)
# G④ 청산은 실패했지만 재조회가 플랫(그 사이 손절 체결) → 남은 주문은 고아라 취소
res, lg = px([_pos(NQC)], [[]], ORD, close_fail={NQC: 2})
ck("G④ 플랫 확인이면 청산 실패여도 고아 주문 취소", 1 in lg["cancel"], lg)
# G⑤ 청산 반영 지연: 첫 재조회엔 남아 보이고 두 번째에 플랫 → STILL OPEN 아님, 주문 정리
res, lg = px([_pos(NQC)], [[_pos(NQC)], []], ORD)
ck("G⑤ 반영 지연은 한 번 더 보고 판단(거짓 STILL OPEN 없음)", not res.errors and 1 in lg["cancel"], (res.errors, lg))
# G⑥ 다른 계좌의 같은 계약은 따로 판단
ORD2 = {11: [{"id": 1, "contractId": NQC, "type": 4}], 12: [{"id": 9, "contractId": NQC, "type": 4}]}
res, lg = px([_pos(NQC, 11), _pos(NQC, 12)], [[_pos(NQC, 12)], [_pos(NQC, 12)]], ORD2, close_fail={})  # 지연 재조회에도 12 열림
ck("G⑥ 계좌별: 11 플랫 → 취소, 12 열림 → 유지", lg["cancel"] == [1], lg)
# G⑦ 정리 단계 계좌 조회 실패는 예외가 아니라 오류로(재조회 결과는 남음)
bx = PX.ProjectXBroker.__new__(PX.ProjectXBroker)
bx.cfg = SimpleNamespace(accounts=["ACC11"])
_s = [[_pos(NQC)], []]
bx.list_open_positions = lambda: _s.pop(0)
bx._post = lambda p, bd: {"success": True}


def _acc_fail(active_only=True):
    raise RuntimeError("gateway down")
bx._accounts = _acc_fail
try:
    r7 = bx.flatten_all(dry_run=False)
    ck("G⑦ 계좌 조회 실패 → 오류 기록, closed 유지", any("cleanup skipped" in e for e in r7.errors)
       and [p.symbol for p in r7.closed] == [NQC], r7)
except Exception as e:
    ck("G⑦ 계좌 조회 실패 → 예외 없이 오류 기록", False, e)
# G⑧ dry-run은 아무것도 안 보냄(종전)
bx = PX.ProjectXBroker.__new__(PX.ProjectXBroker)
bx.cfg = SimpleNamespace(accounts=["ACC11"])
bx.list_open_positions = lambda: [_pos(NQC)]
bx._post = MagicMock()
r8 = bx.flatten_all(dry_run=True)
ck("G⑧ dry-run: 주문 호출 없음", bx._post.call_count == 0 and len(r8.planned) == 1)

# ── X: [모든 포지션 청산] ───────────────────────────────────────────────────────
class _SyncThread:
    def __init__(self, target=None, daemon=None, **k):
        self._t = target

    def start(self):
        self._t()


def close_all(outcome):
    """outcome: FlattenResult 또는 Exception. NQ 계좌 하나(projectx)."""
    me = MagicMock(); me.lang = "ko"
    me._accts_of = lambda a: [{"id": "50KTC-V2-1234"}] if a == "NQ" else []
    me._acct_broker = lambda a, ac: "projectx"
    me._creds_of = lambda a, bk: {"f1": "user"} if a == "NQ" else {}
    bb = MagicMock()
    if isinstance(outcome, Exception):
        bb.flatten_all.side_effect = outcome
    else:
        bb.flatten_all.return_value = outcome
    orig = (eqgui._build_broker, eqgui._f2_load, eqgui.messagebox.askyesno, threading.Thread)
    eqgui._build_broker = lambda *a, **k: bb
    eqgui._f2_load = lambda f1: "key"
    eqgui.messagebox.askyesno = lambda *a, **k: True
    threading.Thread = _SyncThread
    try:
        eqgui.App._close_all_positions(me)
    finally:
        eqgui._build_broker, eqgui._f2_load, eqgui.messagebox.askyesno, threading.Thread = orig
    return me, [str(c[0][0]) for c in me.log.call_args_list]


_ok = FlattenResult(dry_run=False, planned=[_pos(NQC)], closed=[_pos(NQC)])
me, lines = close_all(_ok)
ck("X① 전부 성공 → '✅ 전체 청산 완료', 팝업 없음", "전체 청산 완료" in lines[-1] and me.root.after.call_count == 0, lines)
_bad = FlattenResult(dry_run=False, planned=[_pos(NQC)], closed=[],
                     errors=["ACC11/MNQ: closeContract failed", "STILL OPEN after flatten: ACC11/MNQ net=2"])
me, lines = close_all(_bad)
ck("X② 오류 있으면 마지막 줄이 '완료'가 아님", "전체 청산 완료" not in lines[-1] and "미완료" in lines[-1]
   and "NQ, 1234" in lines[-1], lines)
ck("X② 실패 팝업", me.root.after.call_count == 1, me.root.after.call_count)
ck("X② errors 경로도 서버 보고(close_all)", _ctxs(me._report_error) == ["close_all"], me._report_error.call_args_list)
me, lines = close_all(RuntimeError("auth failed"))
ck("X③ 예외 → '미완료' + 팝업", "미완료" in lines[-1] and me.root.after.call_count == 1, lines)
me, lines = close_all(AttributeError("no flatten"))
ck("X④ 일괄 청산 미지원(건너뜀)도 완료로 세지 않음", "미완료" in lines[-1], lines)
# 팝업 문구: 사실만(지시형 없음), KO/EN 한 쌍
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
_x = _src[_src.index("전체 청산이 다 끝나지 않았습니다"):][:600]
ck("X⑤ 팝업 KO/EN 둘 다, 지시형 없음", "Close-all did not fully finish" in _x and "하세요" not in _x
   and "하십시오" not in _x, _x)

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}: {FAIL}"))
sys.exit(1 if FAIL else 0)
