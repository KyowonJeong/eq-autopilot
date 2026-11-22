#!/usr/bin/env python3
# EQ Autopilot 2026.10.21l - 실행 경로 리뷰 수리 오프라인 시험(2026-10-27, 대표 "바로 다 고쳐"). 0 = 통과.
#   P1-1 한 계약 바닥: 신호 배수 < 1이면 한도에도 배수를 곱해 비교(영어 로그도 실제 이유) - 한국어 표는 test_one_lim_offline
#   P1-2 늦은 진입 줄이기: 마이크로 환산 합에 한 번 - 표는 test_late_entry_offline
#   P2-3 size_mult가 없는 신호 = 건너뜀(영어 로그) - 한국어, 숫자 문자열, 확인 모드는 test_bughunt_g4_offline
#   P2-4 계좌 진입 스레드 기다림 = 계좌 전부에 한 기한(종전 계좌마다 45초)
#   P2-5 _sig_loop 오류 수가 한동안 오류 없이 돈 뒤 다시 센다, _auto_loop 재시작 보호막(본문은 그대로)
#   검증 P3(2026-10-27) 확인 모드를 한 신호 처리에서 한 번만 읽음(size_mult 판단과 발주 판단이 같은 값)
import datetime as _dt
import inspect
import os
import sys
import tempfile
import threading
import time
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import MagicMock

HOME = tempfile.mkdtemp(prefix="eq_ep21l_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
import socket  # noqa: E402
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("network blocked in test"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
import requests  # noqa: E402
import autopilot_crypto  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


ck("0 앱 버전 2026.10.21z 이상", eqgui.App._APP_VER >= "2026.10.21z", eqgui.App._APP_VER)

# ── P1-1 한 계약 바닥, 영어 로그 ──────────────────────────────────────────────
def enter(one_r, lim, mult, lang="en"):
    me = MagicMock()
    me.lang = lang
    me._live_now.return_value = False
    me._jitter_stop.side_effect = lambda a, e, s, l: s
    cfg = {"broker": "ibkr", "f1": "u", "f2": "k", "f3": "7497", "acct": "U1234567", "one_r": one_r, "label": "t",
           "live": False, "prop": {}, "pct": {}, "one_lim": lim}
    _ob = eqgui._build_broker
    eqgui._build_broker = MagicMock()
    try:
        eqgui.App._enter_account(me, cfg, {"entry_ref": 4136.0, "published_at": None}, "GC", "LONG", 4106.0, mult)
    finally:
        eqgui._build_broker = _ob
    legs = me._run_futures_entry.call_args[0][2] if me._run_futures_entry.called else None
    return legs, [c[0][1] for c in me._entry_skip_ev.call_args_list], [str(c[0][0]) for c in me.log.call_args_list]


legs, sk, lg = enter(300.0, 400.0, 0.5)          # 리뷰 사례: IBKR 1R $300, 금 마이크로 위험 $300, 한도 $400
ck("1a IBKR 1R $300, 한도 $400, 배수 0.5: 한 계약을 넣지 않음(종전 1계약 = 의도 $150의 두 배)", legs is None and sk == ["size_cap"], (legs, sk))
ck("1b 영어 로그 = 실제 이유(한도 x 배수 $200)", any("limit times this signal's multiplier (0.50) = $200" in l for l in lg)
   and not any("exceeds your one-contract limit $400" in l for l in lg), lg[-3:])
legs, sk, lg = enter(300.0, 400.0, 0.25)
ck("1c 배수 0.25: 한 계약을 넣지 않음", legs is None and sk == ["size_cap"], (legs, sk))
legs, sk, lg = enter(200.0, 400.0, 0.78)
ck("1d 1R $200, 배수 0.78: 한도 $400 x 0.78 = $312 안 → 1계약, 영어 로그에 곱한 한도", legs == [("MGC", 1)]
   and any("$400 × this signal's multiplier 0.78 = $312" in l for l in lg), (legs, lg[-2:]))
for _m in (1.0, 1.5, 3.0):
    legs, sk, lg = enter(100.0, 400.0, _m)       # 0.33배 x 배수 < 0.75 → 바닥 → 한도 $400 그대로
    _raw = 100.0 * _m / 300.0
    _want = [("MGC", int(round(_raw)))] if _raw >= 0.75 else [("MGC", 1)]
    ck(f"1e 배수 {_m}(1 이상): 종전과 같음 {_want}", legs == _want, (legs, lg[-2:]))

# ── P2-3 size_mult 없음, 영어 로그 ────────────────────────────────────────────
def loop_once(sig_extra, lang="en", watch=False, accts=1, enter_fn=None, join_s=None):
    me = MagicMock()
    me.lang = lang
    me._sig_on = True
    me._token = "tok"
    if callable(watch):
        type(me)._watch_only = property(lambda self: watch())      # 읽을 때마다 값이 바뀌는 확인 모드(하트비트가 바꾸는 순간, 검증 P3)
    else:
        me._watch_only = watch
    me._feed_errs = 0
    me._clock_skew, me._clock_skew_ts = None, 0.0
    me._clock_skew_now = lambda: 0.0
    me._note_clock = lambda *a, **k: None
    me._sig_accts = {("NQ", i): {"label": f"A{i}", "manual": False} for i in range(accts)}
    if enter_fn is not None:
        me._enter_account = enter_fn
    sig = dict({"id": f"s{time.time()}", "instrument": "NQ", "tradeable": True, "direction": "LONG", "stop_price": 19900.0,
                "entry_ref": 20000.0, "published_at": time.time() - 2}, **sig_extra)
    o_get, o_dec, o_sleep, o_join = requests.get, autopilot_crypto.decrypt, time.sleep, eqgui._ENTRY_JOIN_S
    requests.get = lambda *a, **k: SimpleNamespace(ok=True, text="x", headers={"Date": formatdate(time.time(), usegmt=True)})
    autopilot_crypto.decrypt = lambda tok, txt: dict(sig)
    time.sleep = lambda _s: setattr(me, "_sig_on", False)
    if join_s is not None:
        eqgui._ENTRY_JOIN_S = join_s
    t0 = time.monotonic()
    try:
        eqgui.App._sig_loop(me, "u")
    finally:
        requests.get, autopilot_crypto.decrypt, time.sleep, eqgui._ENTRY_JOIN_S = o_get, o_dec, o_sleep, o_join
    return me, time.monotonic() - t0


me, _ = loop_once({})
_lg = " ".join(str(c[0][0]) for c in me.log.call_args_list)
ck("3a size_mult 없음(영어) → 진입 안 함, size_mult0, 실제 이유", not me._enter_account.called
   and [c[0][1] for c in me._entry_skip_ev.call_args_list] == ["size_mult0"]
   and "has no size multiplier (size_mult)" in _lg and "rather than entered at 1x" in _lg, _lg)
me, _ = loop_once({"size_mult": "1.0"})
ck("3b 숫자 문자열 '1.0' → 1배 진입", me._enter_account.called and me._enter_account.call_args[0][5] == 1.0,
   me._enter_account.call_args_list)

# 2026-10-27 검증 P3: 확인 모드는 한 신호 처리에서 한 번만 읽는다 - size_mult 판단(확인 모드라 1배)과 발주 판단 사이에 하트비트가
# 확인 모드를 풀어도(Operator → Autopilot) size_mult 없는 신호를 1배로 주문하지 않는다
_rd = {"n": 0}


def _flip():
    _rd["n"] += 1
    return _rd["n"] == 1                 # 첫 읽기만 확인 모드(True), 그 뒤는 자동 진입(False)


me, _ = loop_once({}, watch=_flip)
ck("3c size_mult 없는 신호를 읽는 사이 확인 모드가 풀려도 1배로 주문하지 않음(확인 모드 값 한 번 읽기)",
   not me._enter_account.called and _rd["n"] >= 1, (me._enter_account.call_args_list, _rd))
_src3 = inspect.getsource(eqgui.App._sig_loop_body)
ck("3d 신호 처리 본문에서 확인 모드(_watch_only) 읽기는 한 곳", _src3.count('getattr(self, "_watch_only"') == 1,
   _src3.count('getattr(self, "_watch_only"'))

# ── P2-4 계좌 스레드 기다림 = 한 기한 ──────────────────────────────────────────
_rel = threading.Event()
_started = []


def _slow_enter(cfg, sig, asset, direction, stop, mult):
    _started.append(cfg.get("label"))
    _rel.wait(30)


me, el = loop_once({"size_mult": 1.0}, accts=3, enter_fn=_slow_enter, join_s=0.6)
_rel.set()
ck("4a 계좌 3개가 다 늦어도 루프는 한 기한(0.6초)만 기다림(종전 계좌마다 = 1.8초)", len(_started) == 3 and el < 1.4, (el, _started))
ck("4b 기한 안에 안 끝난 계좌 셋은 종전처럼 후속 보고로", me._fill_followup.called
   and len(me._fill_followup.call_args[0][2]) == 3, me._fill_followup.call_args_list)
_src = inspect.getsource(eqgui.App._sig_loop_body)
ck("4c 기한 = _ENTRY_JOIN_S(45초) 하나, join(timeout=45) 없음", eqgui._ENTRY_JOIN_S == 45 and "join(timeout=45)" not in _src
   and "_th.join(timeout=max(0.0, _jdl - _t.monotonic()))" in _src)

# ── P2-5a _sig_loop 오류 수 초기화 ────────────────────────────────────────────
def guard_run(loop, plan, on_attr, gen=None, gen_attr=None):
    """loop = App._sig_loop/_auto_loop. plan = [(진행 초, 'raise'|'ok')]: 본문 한 번마다 가짜 시계를 진행 초만큼 넘기고 예외 또는 정상 끝."""
    me = MagicMock()
    me.lang = "ko"
    setattr(me, on_attr, True)
    if gen_attr:
        setattr(me, gen_attr, gen)
    clk = {"t": 1000.0}
    calls = []
    body_name = "_sig_loop_body" if loop is eqgui.App._sig_loop else "_auto_loop_body"
    o_body, o_mono, o_sleep = getattr(eqgui.App, body_name), time.monotonic, time.sleep
    steps = list(plan)

    def _body(self, *a):
        calls.append(a)
        dt, what = steps.pop(0) if steps else (0, "ok")
        clk["t"] += dt
        if what == "raise":
            raise RuntimeError(f"boom{len(calls)}")
        setattr(self, on_attr, False)
    setattr(eqgui.App, body_name, _body)
    time.monotonic = lambda: clk["t"]
    time.sleep = lambda s: clk.__setitem__("t", clk["t"] + float(s))
    try:
        if loop is eqgui.App._sig_loop:
            loop(me, "u", gen)
        else:
            loop(me, gen)
    finally:
        setattr(eqgui.App, body_name, o_body)
        time.monotonic, time.sleep = o_mono, o_sleep
    logs = [str(c[0][0]) for c in me.log.call_args_list]
    reps = [c[0] for c in me._report_error.call_args_list]
    return calls, logs, reps


calls, logs, reps = guard_run(eqgui.App._sig_loop, [(0, "raise")] * 5 + [(120, "raise"), (0, "ok")], "_sig_on")
ck("5a 연달아 5번 = 로그 3줄, 보고 3번(종전과 같음) + 2분 정상 뒤 새 오류는 다시 로그, 보고(합 4, 4)",
   len(calls) == 7 and len([l for l in logs if "신호 루프 오류" in l]) == 4 and len(reps) == 4
   and reps[-1][0] == "sig_loop" and "boom6" in reps[-1][1], (len(calls), logs, reps))
calls, logs, reps = guard_run(eqgui.App._sig_loop, [(0, "raise")] * 25 + [(0, "ok")], "_sig_on")
ck("5b 정상 구간 없이 되풀이 = 종전처럼 보고 3번, 로그는 1분에 한 줄(1~3, 20번째)",
   len([l for l in logs if "신호 루프 오류" in l]) == 4 and len(reps) == 3, (logs, reps))

# ── P2-5b _auto_loop 재시작 보호막 ───────────────────────────────────────────
calls, logs, reps = guard_run(eqgui.App._auto_loop, [(0, "raise"), (0, "ok")], "_auto_on")
ck("5c 마감 청산 루프 예외 → 한 줄 남기고 다시 돈다(본문 2번, 보고 auto_loop 1번)", len(calls) == 2
   and any("마감 청산 루프 오류(RuntimeError: boom1)" in l for l in logs) and [r[0] for r in reps] == ["auto_loop"], (calls, logs, reps))
calls, logs, reps = guard_run(eqgui.App._auto_loop, [(0, "ok")], "_auto_on", gen=4, gen_attr="_auto_gen")
ck("5d 내 세대면 본문을 세대 인자로 부른다", calls == [(4,)], calls)
calls, logs, reps = guard_run(eqgui.App._auto_loop, [(0, "ok")], "_auto_on", gen=4, gen_attr="_auto_gen")
_me5 = MagicMock(); _me5._auto_on = True; _me5._auto_gen = 5
_o5 = eqgui.App._auto_loop_body
_hit5 = []
eqgui.App._auto_loop_body = lambda self, *a: _hit5.append(a)
try:
    eqgui.App._auto_loop(_me5, 4)
finally:
    eqgui.App._auto_loop_body = _o5
ck("5e 낡은 세대(세대 4, 앱 세대 5)면 본문을 부르지 않는다(종전 본문의 세대 확인과 같음)", not _hit5, _hit5)


# 실제 본문: 첫 바퀴에 예상 못 한 예외가 나도 다시 돈 루프가 그 창 안에서 청산한다(종전엔 스레드가 끝나 그날 마감 청산이 없었다)
def auto_real(raise_first):
    me = MagicMock()
    me.lang = "ko"
    me._gate = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True}}
    me._auto_on = True
    me._exit_override = {}
    me._entered_at, me._entering_at = {}, {}
    me._auto_fired, me._auto_retry = {}, {}
    _fired0 = me._auto_fired
    me._auto_accts = {("NQ", 0): [{"asset": "NQ", "hour": 14, "tz": "America/New_York", "broker": "projectx", "acct": "A1",
                                    "f1": "k", "f2": "s", "f3": "", "live": False}]}
    me._close_mode = lambda j: (True, False)
    b = MagicMock()
    res = MagicMock(); res.planned = []; res.closed = []; res.errors = []          # 정리할 포지션 없음 = 그 창의 일 끝
    b.flatten_all.return_value = res
    b.flatten_asset.return_value = res
    n = {"now": 0}
    orig = (eqgui._build_broker, eqgui._now_in, time.sleep, eqgui._flatten_asset)

    def _now(_tz):
        n["now"] += 1
        if raise_first and n["now"] == 1:
            raise RuntimeError("tz blip")
        return _dt.datetime(2026, 10, 27, 14, 0, 30)
    eqgui._build_broker = lambda *a, **k: b
    eqgui._now_in = _now
    eqgui._flatten_asset = lambda bb, asset, dry: res
    time.sleep = lambda s: setattr(me, "_auto_on", False) if s >= 15 else None
    died = None
    try:
        eqgui.App._auto_loop(me)
    except Exception as e:  # noqa: BLE001
        died = e
    finally:
        eqgui._build_broker, eqgui._now_in, time.sleep, eqgui._flatten_asset = orig
    return me, died, _fired0


me, died, f0 = auto_real(True)
_lg = " ".join(str(c[0][0]) for c in me.log.call_args_list)
ck("5f 첫 바퀴 예외 뒤에도 그 창 안에서 청산 기록(같은 _auto_fired 사전)", died is None and me._auto_fired is f0
   and f0.get(("NQ", 14, "A1")) == "2026-10-27" and "마감 청산 루프 오류" in _lg, (died, f0, _lg[-300:]))
me2, died2, f02 = auto_real(False)
ck("5g 예외가 없으면 종전과 같은 청산(같은 기록, 보호막 로그 없음)", died2 is None and f02 == f0 and bool(f02)
   and "마감 청산 루프 오류" not in " ".join(str(c[0][0]) for c in me2.log.call_args_list), (f02, f0))

print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
