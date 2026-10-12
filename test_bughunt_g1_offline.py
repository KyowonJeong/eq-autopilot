#!/usr/bin/env python3
# EQ Autopilot - 버그 조사 1단계 g1(시작·정지·무장 상태) 오프라인 시험 (오픈 준비 버그 조사 2026-10-06). 0=통과
# A  정지→시작이 빨라도 신호 루프·청산 루프는 하나: 세대 번호가 아니면 while·발주 직전에 빠진다.
# E  서버 불통(유예 넘김·하트비트 exp 경과)으로 꺼진 신호 대기는 서버가 돌아오면 되살린다. 명시 거부·정지·킬스위치는 아님.
#    화면(자산 줄·안내 줄)·서버 핑(wo)이 '마감 청산만'을 사실대로 말하고, [라이브 시작]이 '이미 가동 중'에 막히지 않는다.
# P  [라이브 시작] 연결 테스트 도중 [전체 정지]를 누르면 done()이 무장하지 않는다.
# S  가동 중 계좌 행 하나를 지우면 그 계좌만 무장 해제, 뒤 행 키는 한 칸씩 당긴다(같은 자산 다른 계좌·마감 청산 유지).
# C  가동 중 바꾼 1R·프롭·한 계약 한도·라벨은 다음 신호에 반영, 실행 체크 끔·계좌 ID·브로커 변경은 진입 안 함,
#    새 행·키 변경은 '다시 무장해야 적용'으로 표시.
# 사용: python executor/test_bughunt_g1_offline.py   (0=통과, 1=실패)
import os
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock

os.environ.setdefault("EQ_NO_ALERT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
import requests  # noqa: E402
import autopilot_crypto  # noqa: E402
import tkinter.messagebox as _mb  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


subprocess.Popen = lambda *a, **k: None          # macOS 알림 억제
eqgui._machine_id = lambda: "test"               # 설정 폴더에 기기 id 파일을 만들지 않게
SHOWN = []
for _n in ("showinfo", "showwarning", "showerror"):
    setattr(_mb, _n, (lambda *a, **k: SHOWN.append(a)))
    setattr(eqgui.messagebox, _n, (lambda *a, **k: SHOWN.append(a)))

NORMAL = {"ok": True, "enabled": True, "force_dry_run": False, "tier": "royal",
          "caps": {"use": True, "autoentry": True, "connect": True}}
OPER = {"ok": True, "enabled": True, "force_dry_run": False, "tier": "member",
        "caps": {"use": True, "autoentry": False, "connect": True}}
OFF = {"ok": False, "offline": True, "reason": "heartbeat error"}
REV = {"ok": False, "revoked": True, "reason": "locked"}
KILL = {"ok": True, "enabled": True, "force_dry_run": True, "caps": {"use": True, "autoentry": True}}


def logs(me):
    return " ".join(str(c[0][0]) for c in me.log.call_args_list)


def acct(aid="111", one_r=600.0, on=True, label="A", broker="projectx", prop=None, one_lim=0.0):
    return {"id": aid, "one_r": one_r, "on": on, "label": label, "broker": broker,
            "prop": prop or {}, "pct": {}, "one_lim": one_lim, "manual": False}


def snap(aid="111", one_r=600.0, label="A", broker="projectx", f1="u", f3=""):
    return {"broker": broker, "f1": f1, "f2": "k", "f3": f3, "acct": aid, "one_r": one_r,
            "prop": {}, "pct": {}, "one_lim": 0.0, "manual": False, "live": True, "label": label}


def bind(me, *names):
    for n in names:
        fn = getattr(eqgui.App, n)
        setattr(me, n, (lambda f: (lambda *a, **k: f(me, *a, **k)))(fn))


def app(accts_nq=None, creds=None):
    me = MagicMock()
    me.lang = "ko"
    me._acfg = {"NQ": {"broker": "projectx", "include": True,
                       "creds": creds or {"projectx": {"f1": "u", "f3": ""}},
                       "accounts": accts_nq if accts_nq is not None else [acct()]},
                "GC": {"broker": "projectx", "include": False, "creds": {}, "accounts": []},
                "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}
    me._sig_accts = {}
    me._auto_accts = {}
    me._sig_parked = {}
    me._feed_errs = 0
    me._watch_only = False
    me._sig_on = True
    me._auto_on = True
    me._sig_gen = 1
    me._auto_gen = 1
    me._killed = False
    me._fallback_mode = None
    me._kill_noted = False
    me._live_session = True
    me._live_ctx = {"live": True, "perm_auto": True, "perm_use": True}
    me._token = "tok"
    me._conn_by_broker = {"projectx": True}
    # PC 시계 차이(g4 W): 잰 적 없음 = 종전처럼 PC 시계. 진짜 메서드를 붙여 MagicMock 값이 나이 계산에 끼지 않게.
    me._clock_skew, me._clock_skew_ts = None, 0.0
    bind(me, "_live_acct_cfg", "_acct_broker", "_broker_of", "_accts_of", "_active_accts", "_creds_of",
         "_rearm_needs", "_fallback_kind", "_resume_parked_sig",
         "_note_clock", "_clock_skew_now", "_clock_skew_text", "_clock_warn_check",
         "_arm_fp", "_arm_fps", "_arm_tested_rows", "_kill_on", "_close_held_line")      # 무장 = 테스트 직전 지문이 같은 행만(2026-10-08 P0)
    me._golive_refuse = lambda *a, **k: False            # 연결 테스트 중 편집 거절은 따로 시험(test_global_creds P0)
    return me


# ── 신호 루프 한 바퀴 하네스 ──────────────────────────────────────────────
def run_sig(me, gen, sig, on_cfg=None):
    """_sig_loop를 한 바퀴 돌린다. 반환 (피드 GET 횟수, _enter_account에 간 cfg 목록)."""
    entered = []
    me._enter_account = lambda cfg, *a: entered.append(cfg)
    gets = []

    class _R:
        ok = True
        text = "x"
    orig_get, orig_dec, orig_sleep = requests.get, autopilot_crypto.decrypt, time.sleep
    requests.get = lambda *a, **k: (gets.append(1), _R())[1]
    autopilot_crypto.decrypt = lambda tok, txt: dict(sig)

    def _stop(_s):
        me._sig_on = False
    time.sleep = _stop
    if on_cfg is not None:                       # 사본을 다시 읽는 순간에 끼어들 훅
        _real = me._live_acct_cfg

        def _hook(k, c):
            on_cfg()
            return _real(k, c)
        me._live_acct_cfg = _hook
    try:
        eqgui.App._sig_loop(me, "http://feed", gen)
    finally:
        requests.get, autopilot_crypto.decrypt, time.sleep = orig_get, orig_dec, orig_sleep
    # _enter_account는 스레드로 돈다 - join(45)이 끝났으니 목록은 채워져 있다
    return len(gets), entered


def sig_nq(sid="s1"):
    return {"id": sid, "tradeable": True, "direction": "long", "instrument": "NQ",
            "published_at": time.time(), "stop_price": 19900.0, "entry_ref": 20000.0, "size_mult": 1.0}


# ═════ A: 세대 토큰 ═════
me = app()
me._sig_accts = {("NQ", 0): snap()}
me._sig_gen = 2
n_get, ent = run_sig(me, 1, sig_nq())
ck("A 낡은 신호 루프(세대 1, 현재 2)는 피드도 안 읽고 바로 끝남", n_get == 0 and not ent, (n_get, ent))

me = app()
me._sig_accts = {("NQ", 0): snap()}
n_get, ent = run_sig(me, 1, sig_nq())
ck("A 자기 세대 루프는 종전처럼 한 번 진입", n_get == 1 and len(ent) == 1, (n_get, ent))


def _bump():
    me._sig_gen = 2                              # 신호 처리 도중 새 루프가 떴다
me = app()
me._sig_accts = {("NQ", 0): snap()}
n_get, ent = run_sig(me, 1, sig_nq(), on_cfg=_bump)
ck("A 처리 도중 새 세대가 뜨면 발주 직전에 빠짐(이중 진입 없음)", not ent, ent)

me = app()
me._sig_accts = {("NQ", 0): snap()}
n_get, ent = run_sig(me, None, sig_nq())
ck("A gen=None(옛 호출)은 세대 확인 없이 종전 동작", len(ent) == 1, ent)

# _auto_loop: 낡은 세대면 잡을 하나도 처리하지 않는다
me = app()
me._exit_override = {}
me._entered_at = {}
me._auto_gen = 5
me._auto_accts = {("NQ", 0): [{"asset": "NQ", "hour": 14, "tz": "America/New_York", "broker": "projectx",
                                "acct": "111", "f1": "u", "f2": "k", "f3": "", "live": True}]}
_b = MagicMock()
orig_bb, orig_now, orig_sleep = eqgui._build_broker, eqgui._now_in, time.sleep
eqgui._build_broker = lambda *a, **k: _b
import datetime as _dt  # noqa: E402
eqgui._now_in = lambda _tz: _dt.datetime(2026, 10, 7, 14, 1)
time.sleep = lambda s: setattr(me, "_auto_on", False)
try:
    eqgui.App._auto_loop(me, 4)
    _stale_fl = _b.flatten_all.call_count
    me._auto_on = True
    me._close_mode = lambda j: (True, False)
    eqgui.App._auto_loop(me, 5)
    _mine_fl = _b.flatten_all.call_count - _stale_fl
finally:
    eqgui._build_broker, eqgui._now_in, time.sleep = orig_bb, orig_now, orig_sleep
ck("A 낡은 청산 루프(세대 4, 현재 5)는 마감 청산을 쏘지 않음", _stale_fl == 0, _stale_fl)
ck("A 자기 세대 청산 루프는 마감 청산 한 번", _mine_fl == 1, _mine_fl)

# _start_sig_loop/_start_auto_loop: 띄울 때마다 세대가 오른다
me = app()
me._sig_gen = 3
me._auto_gen = 7
_started = []
_orig_thread = eqgui.threading.Thread


class _FakeThread:
    def __init__(self, target=None, args=(), daemon=None, **k):
        _started.append((getattr(target, "__name__", str(target)), args))

    def start(self):
        pass
eqgui.threading.Thread = _FakeThread
try:
    eqgui.App._start_sig_loop(me)
    eqgui.App._start_auto_loop(me)
finally:
    eqgui.threading.Thread = _orig_thread
ck("A _start_sig_loop: 세대 4로 올리고 그 번호를 루프 인자로", me._sig_gen == 4 and me._sig_on is True
   and _started and _started[0][1][-1] == 4, _started)
ck("A _start_auto_loop: 세대 8로 올리고 그 번호를 루프 인자로", me._auto_gen == 8 and me._auto_on is True
   and len(_started) == 2 and _started[1][1] == (8,), _started)

# ═════ C: 가동 중 바뀐 계좌 설정 ═════
me = app([acct(one_r=300.0, label="새이름", one_lim=250.0, prop={"on": False})])
me._sig_accts = {("NQ", 0): snap(one_r=600.0, label="A")}
_, ent = run_sig(me, 1, sig_nq())
ck("C 1R을 600→300으로 낮추면 다음 신호는 300으로 진입(라벨·한 계약 한도도 지금 값)",
   len(ent) == 1 and ent[0]["one_r"] == 300.0 and ent[0]["label"] == "새이름" and ent[0]["one_lim"] == 250.0, ent)
ck("C 무장 사본 자체는 건드리지 않음(새 사본으로 진입)", me._sig_accts[("NQ", 0)]["one_r"] == 600.0)

me = app([acct(prop={"on": True, "type": "funded", "r_steady": 450.0, "payouts": 2})])
me._sig_accts = {("NQ", 0): snap()}
_, ent = run_sig(me, 1, sig_nq())
ck("C 가동 중 켠 프롭 설정이 다음 신호에 실림", ent and ent[0]["prop"].get("on") is True
   and ent[0]["prop"].get("r_steady") == 450.0, ent)

me = app([acct(on=False)])
me._sig_accts = {("NQ", 0): snap()}
_, ent = run_sig(me, 1, sig_nq())
ck("C 계좌 실행 체크를 끄면 그 계좌는 진입하지 않음 + 사실 한 줄", not ent and "실행 체크가 꺼져" in logs(me), logs(me))
ck("C 남은 계좌가 없으면 서버에 no_account 건너뜀 보고",
   any(c[0][1] == "no_account" for c in me._entry_skip_ev.call_args_list), me._entry_skip_ev.call_args_list)

me = app([acct(aid="999")])
me._sig_accts = {("NQ", 0): snap(aid="111")}
_, ent = run_sig(me, 1, sig_nq())
ck("C 계좌 ID가 무장 뒤 바뀌면 진입 안 함(시험 안 한 계좌로 주문 금지)", not ent and "바뀌어" in logs(me), logs(me))

me = app([acct(), acct(aid="222", label="B", on=False)])
me._sig_accts = {("NQ", 0): snap(), ("NQ", 1): snap(aid="222", label="B")}
_, ent = run_sig(me, 1, sig_nq())
ck("C 다계좌 중 하나만 끄면 나머지는 그대로 진입", len(ent) == 1 and ent[0]["acct"] == "111", ent)

me = app()
me._sig_accts = {("NQ", 3): snap()}
c2, why = eqgui.App._live_acct_cfg(me, ("NQ", 3), snap())
ck("C 행이 없어진 키는 진입 안 함", c2 is None and "없어져" in why, why)

me = app()
me.lang = "en"
c2, why = eqgui.App._live_acct_cfg(me, ("NQ", 0), snap(broker="nt8"))
ck("C EN 문장 + 브로커 불일치도 건너뜀", c2 is None and "changed after arming" in why, why)

me = app()
me._watch_only = True                            # Operator - 확인 카드만, 사본 대조 안 함
me._sig_accts = {("NQ", 0): snap()}
me._acfg["NQ"]["accounts"][0]["on"] = False
_, ent = run_sig(me, 1, sig_nq())
ck("C Operator(확인 모드)는 종전처럼 확인 카드 한 번(건너뜀 문장 없음)",
   me._watch_ticket.call_count == 1 and "실행 체크가 꺼져" not in logs(me), logs(me))

# 재무장이 필요한 변경 표시
me = app([acct(), acct(aid="222", label="새행")])
me._sig_accts = {("NQ", 0): snap()}
me._auto_accts = {("NQ", 0): [{"broker": "projectx", "f1": "u", "f3": "", "acct": "111"}]}
ck("C 가동 중 추가한 새 행 = '다시 무장해야 적용' 목록", eqgui.App._rearm_needs(me, "NQ") == ["새행"],
   eqgui.App._rearm_needs(me, "NQ"))
me._acfg["NQ"]["accounts"][1]["on"] = False
ck("C 새 행을 꺼 두면 목록에서 빠짐", eqgui.App._rearm_needs(me, "NQ") == [])
me._acfg["NQ"]["accounts"][0]["one_r"] = 150.0
ck("C 1R만 바꾼 건 재무장 불필요(다음 신호에 반영)", eqgui.App._rearm_needs(me, "NQ") == [])
me._acfg["NQ"]["creds"]["projectx"]["f1"] = "다른키"
ck("C 키(f1)를 바꾸면 재무장 필요", eqgui.App._rearm_needs(me, "NQ") == ["A"], eqgui.App._rearm_needs(me, "NQ"))
me2 = app([acct(), acct(aid="222", label="새행")])
ck("C 무장 안 한 자산은 목록 없음", eqgui.App._rearm_needs(me2, "NQ") == [])

# 패널 줄·로그 한 줄
me = app([acct(), acct(aid="222", label="새행")])
me._sig_accts = {("NQ", 0): snap()}
me._auto_accts = {("NQ", 0): [{"broker": "projectx", "f1": "u", "f3": "", "acct": "111", "live": True}]}
txt, col = eqgui.App._asset_row_state(me, "NQ")
ck("C 패널 자산 줄에 '다시 무장해야 적용: 새행'(노랑)", "다시 무장해야 적용: 새행" in txt and col == "#eab308", (txt, col))
eqgui.App._note_rearm_needs(me)
eqgui.App._note_rearm_needs(me)
_n = [c for c in me.log.call_args_list if "다시 무장하기 전까지 새 신호에 진입하지 않습니다" in str(c[0][0])]   # 문구 사실화(버그 조사 재검증 2026-10-06 1)
ck("C 재무장 필요 로그는 상태가 바뀔 때 한 번만", len(_n) == 1, _n)
ck("C 문구에 지시형 없음", "하세요" not in logs(me) and "하십시오" not in logs(me), logs(me))

# ═════ S: 계좌 하나 삭제 = 그 계좌만 해제 ═════
d = {("NQ", 0): "a", ("NQ", 1): "b", ("NQ", 2): "c", ("NQ", 3): "d", ("GC", 1): "g"}
hit = eqgui.App._rekey_after_delete(d, "NQ", 1)
ck("S 키 당기기: 지운 행만 빠지고 뒤 행은 한 칸씩", hit and d == {("NQ", 0): "a", ("NQ", 1): "c", ("NQ", 2): "d",
                                                         ("GC", 1): "g"}, d)
ck("S 무장 안 된 행 삭제는 hit 없음", eqgui.App._rekey_after_delete({("NQ", 0): "a"}, "NQ", 2) is False)

me = app([acct(aid="111", label="A"), acct(aid="222", label="B"), acct(aid="333", label="C")])
me._asset = "NQ"
me._rekey_after_delete = eqgui.App._rekey_after_delete
me._sig_accts = {("NQ", 0): snap("111"), ("NQ", 1): snap("222"), ("NQ", 2): snap("333"), ("GC", 0): snap("g")}
me._auto_accts = {("NQ", 0): [{"acct": "111"}], ("NQ", 1): [{"acct": "222"}], ("NQ", 2): [{"acct": "333"}]}
_q = []
_orig_ask = eqgui.messagebox.askyesno
eqgui.messagebox.askyesno = lambda t, m: (_q.append(m), True)[1]
try:
    eqgui.App._del_acct(me, "NQ", 1)
finally:
    eqgui.messagebox.askyesno = _orig_ask
ck("S 삭제 확인창이 '무장 중, 이 계좌의 자동 진입·마감 청산도 해제, 다른 계좌는 그대로'를 말함",
   _q and "무장 중" in _q[0] and "다른 계좌는 그대로" in _q[0], _q)
ck("S 같은 자산의 다른 계좌 진입 무장 유지(키는 행 번호에 맞게)",
   me._sig_accts.get(("NQ", 0), {}).get("acct") == "111" and me._sig_accts.get(("NQ", 1), {}).get("acct") == "333"
   and ("NQ", 2) not in me._sig_accts and ("GC", 0) in me._sig_accts, me._sig_accts)
ck("S 다른 계좌의 마감 청산 무장 유지",
   me._auto_accts.get(("NQ", 0)) == [{"acct": "111"}] and me._auto_accts.get(("NQ", 1)) == [{"acct": "333"}]
   and len(me._auto_accts) == 2, me._auto_accts)
ck("S 루프는 계속(_sig_on·_auto_on 그대로)", me._sig_on is True and me._auto_on is True)
ck("S 행 목록에서 B가 빠짐", [a["id"] for a in me._acfg["NQ"]["accounts"]] == ["111", "333"])
ck("S 해제 사실 로그 한 줄", "무장을 해제했습니다" in logs(me), logs(me))
ck("S 삭제 뒤 신호 때 사본 대조가 맞음(당긴 키 = 같은 계좌)",
   eqgui.App._live_acct_cfg(me, ("NQ", 1), me._sig_accts[("NQ", 1)])[0] is not None)

me = app([acct(aid="111", label="A")])
me._asset = "NQ"
me._rekey_after_delete = eqgui.App._rekey_after_delete
me._sig_accts = {("NQ", 0): snap("111")}
me._auto_accts = {("NQ", 0): [{"acct": "111"}]}
eqgui.messagebox.askyesno = lambda t, m: True
try:
    eqgui.App._del_acct(me, "NQ", 0)
finally:
    eqgui.messagebox.askyesno = _orig_ask
ck("S 마지막 무장 계좌를 지우면 루프 플래그도 내려감(종전과 같음)", me._sig_on is False and me._auto_on is False)

# ═════ E: 서버 불통으로 꺼진 신호 대기 ═════
def gated(gate, **kw):
    me = app()
    me._gate = dict(gate)
    me._connected = True
    me._sig_accts = {("NQ", 0): snap()}
    me._auto_accts = {("NQ", 0): [{"asset": "NQ", "live": True, "broker": "projectx", "acct": "111",
                                   "f1": "u", "f3": ""}]}
    me._start_sig_loop = MagicMock(side_effect=lambda: setattr(me, "_sig_on", True))
    me.root.after = lambda ms, fn: fn() if ms < 1000 else None
    for k, v in kw.items():
        setattr(me, k, v)
    return me


me = gated(OFF)
eqgui.App._apply_gating(me)
ck("E 불통: 신호 대기 꺼짐 + 무장 사본 남김 + 마감 청산 유지",
   me._sig_on is False and not me._sig_accts and ("NQ", 0) in me._sig_parked and me._auto_accts, me._sig_parked)
SHOWN.clear()
me._gate = dict(NORMAL)
eqgui.App._apply_gating(me)
ck("E 복구: 신호 대기를 되살림(같은 키, 새 루프 하나)",
   me._sig_on is True and ("NQ", 0) in me._sig_accts and me._start_sig_loop.call_count == 1
   and not me._sig_parked, (me._sig_accts, me._start_sig_loop.call_count))
ck("E 복구: 사실 한 줄 + 안내창 1회", "신호 대기를 다시 켰습니다" in logs(me)
   and sum(1 for s in SHOWN if "다시 켰습니다" in str(s)) == 1, logs(me))
ck("E 복구: Autopilot은 자동 진입 문장, 확인 모드 아님", me._watch_only is False and "자동 진입합니다" in logs(me))
eqgui.App._apply_gating(me)
ck("E 같은 상태로 다시 불려도 루프를 또 띄우지 않음", me._start_sig_loop.call_count == 1)

me = gated(OFF)
eqgui.App._apply_gating(me)
me._gate = dict(OPER)
eqgui.App._apply_gating(me)
ck("E Operator 복구 = 확인 모드로 되살림", me._sig_on is True and me._watch_only is True
   and "신호 확인만 합니다" in logs(me), logs(me))

me = gated(REV)
eqgui.App._apply_gating(me)
ck("E 명시 거부(잠금)로 꺼지면 사본을 남기지 않음(수동 재무장)", me._sig_on is False and not me._sig_parked)

me = gated(OFF)
eqgui.App._apply_gating(me)
me._gate = dict(REV)
eqgui.App._apply_gating(me)
ck("E 불통 뒤 서버가 명시 거부하면 사본 버림 + 사실 한 줄", not me._sig_parked and me._sig_on is False
   and "저절로 다시 켜지 않습니다" in logs(me), logs(me))
me._gate = dict(NORMAL)
eqgui.App._apply_gating(me)
ck("E 그 뒤 권한이 돌아와도 저절로 켜지 않음", me._start_sig_loop.call_count == 0)

me = gated(OFF)
eqgui.App._apply_gating(me)
me._gate = dict(KILL)
eqgui.App._apply_gating(me)
ck("E 복구 때 킬스위치가 켜져 있으면 되살리지 않고 사본 유지", me._start_sig_loop.call_count == 0 and me._sig_parked)
me._gate = dict(NORMAL)
eqgui.App._apply_gating(me)
ck("E 킬스위치가 풀리면 되살림", me._start_sig_loop.call_count == 1 and me._sig_on is True)

me = gated(OFF, _killed=True)
eqgui.App._apply_gating(me)
ck("E 서버가 킬스위치를 켠 뒤 불통이면 사본 없음(멈춘 걸 되살리지 않음)", not me._sig_parked)

me = gated(OFF)
eqgui.App._apply_gating(me)
me._auto_accts.pop(("NQ", 0))                   # 불통 동안 회원이 그 자산을 해제
me._gate = dict(NORMAL)
eqgui.App._apply_gating(me)
ck("E 불통 동안 회원이 해제한 계좌는 되살리지 않음", me._start_sig_loop.call_count == 0 and not me._sig_accts)

me = gated(OFF)
eqgui.App._apply_gating(me)
me._live_session = False                         # 불통 동안 [전체 정지]
me._gate = dict(NORMAL)
eqgui.App._apply_gating(me)
ck("E 불통 동안 전체 정지했으면 되살리지 않음", me._start_sig_loop.call_count == 0 and not me._sig_parked)

# 경보창 문장
me = gated(OFF)
me._gate = {"ok": False, "offline": True}
me._sig_accts = {}
me._sig_parked = {("NQ", 0): snap()}
SHOWN.clear()
eqgui.App._notify_disarmed(me, first=True)
_w = " ".join(str(s) for s in SHOWN)
ck("E 불통 경보 = '서버 연결이 돌아오면 앱이 다시 켭니다'(따를 수 없는 Go Live 안내 아님)",
   "다시 켭니다" in _w and "Go Live" not in _w, _w)
me._sig_accts = {("NQ", 0): snap()}
me._gate = dict(OPER)                            # Operator(autoentry 없음)도 다시 무장되면 반복 경보 끝
me._disarmed_reason = "신호 확인"
SHOWN.clear()
eqgui.App._notify_disarmed(me, first=True)
ck("E 신호 대기가 다시 켜졌으면 반복 경보 끝(Operator 포함)", not SHOWN and me._disarmed_reason is None, SHOWN)

# 화면·서버 핑이 '마감 청산만'을 사실대로
me = app()
me._gate = dict(NORMAL)                          # 서버 잠금 아님(잠금 중 '마감 청산 보류'는 test_global_creds 8b P2-3)
me._sig_accts = {}
me._auto_accts = {("NQ", 0): [{"broker": "projectx", "f1": "u", "f3": "", "acct": "111", "live": True}]}
txt, col = eqgui.App._asset_row_state(me, "NQ")
ck("E 자산 줄 = '마감 청산만 가동(신호 대기 꺼짐)'(가동 중(실거래) 아님)",
   "마감 청산만 가동" in txt and "실거래" not in txt, txt)
me._asset = "NQ"
me._next_lbl = MagicMock()
me._test_ok = {"NQ"}
eqgui.App._refresh_next_action(me)
_t = str(me._next_lbl.config.call_args)
ck("E 안내 줄 = '신호 대기는 꺼져 있어 새 신호에 진입하지 않습니다'", "신호 대기는 꺼져" in _t, _t)

_sent = {}
_orig_post = requests.post
requests.post = lambda url, timeout=None, json=None: _sent.update(json or {}) or MagicMock(ok=True)
me._ping_lock = threading.Lock()
try:
    eqgui.App._alive_ping(me, armed=True, force=True, sync=True)
finally:
    requests.post = _orig_post
ck("E 서버 핑: armed는 그대로(마감 청산은 돈다), wo=True(자동 진입 안 함)",
   _sent.get("armed") is True and _sent.get("wo") is True, _sent)

# 하트비트 exp 경과 = 명시 거부가 아니라 유예·폴백 경로
class _SyncThread:
    def __init__(self, target=None, args=(), daemon=None, **k):
        self._t, self._a = target, args

    def start(self):
        self._t(*self._a)


def hb_run(gate_good_age_s, skew=None, exp_off=-30):
    me = MagicMock()
    me.lang = "ko"
    del me.token_e
    me._token = "tok"
    me._gate_good = dict(NORMAL)
    me._gate_good_ts = time.time() - gate_good_age_s
    # 시계 차이(g4 W): skew=None = 잰 적 없음(exp를 PC 시계로 판정), 값이면 방금 잰 PC-서버 차이(초)
    me._clock_skew, me._clock_skew_ts = (None, 0.0) if skew is None else (float(skew), time.time())
    bind(me, "_note_clock", "_clock_skew_now", "_clock_skew_text", "_clock_warn_check")

    class _R:
        ok = True
        status_code = 200
        text = "x"
    o_get, o_dec, o_thr = requests.get, autopilot_crypto.decrypt, eqgui.threading.Thread
    requests.get = lambda *a, **k: _R()
    autopilot_crypto.decrypt = lambda tok, txt: {"ok": True, "tier": "royal", "exp": time.time() + exp_off,
                                                 "autopilot": {"enabled": True, "force_dry_run": False,
                                                               "caps": {"use": True, "autoentry": True}}}
    eqgui.threading.Thread = _SyncThread
    try:
        eqgui.App._heartbeat(me)
    finally:
        requests.get, autopilot_crypto.decrypt, eqgui.threading.Thread = o_get, o_dec, o_thr
    return me


me = hb_run(5 * 60)
ck("E hb exp 경과 + 최근 정상 권한 = 유예(권한 유지, 즉시 잠금 아님)",
   me._gate.get("ok") is True and "갱신되지 않고" in logs(me), (me._gate, logs(me)))
me = hb_run(eqgui.HB_GRACE_MIN * 60 + 60)
ck("E hb exp 경과 + 유예 넘김 = offline(명시 거부 revoked 아님 - 복구 때 되살림 대상)",
   me._gate.get("ok") is False and me._gate.get("offline") is True and not me._gate.get("revoked"), me._gate)
# g1 E × g4 W 합침: exp 판정은 서버 시각 기준, 경과면 즉시 잠금이 아니라 유예·폴백
me = hb_run(5 * 60, skew=1200.0, exp_off=-900)      # PC 20분 빠름 - 서버 시각으로는 exp 5분 남음
ck("E×W PC 시계가 빨라도 서버 시각으로 exp 전이면 정상 권한(만료·유예 아님)",
   me._gate.get("ok") is True and not me._gate.get("reason") and "갱신되지 않고" not in logs(me), (me._gate, logs(me)))
me = hb_run(eqgui.HB_GRACE_MIN * 60 + 60, skew=-1200.0, exp_off=900)   # PC 20분 느림 - 서버 시각으로는 exp 5분 지남
ck("E×W PC 시계가 느려도 서버 시각으로 exp 지났으면 경과 = offline 폴백(revoked 아님)",
   me._gate.get("ok") is False and me._gate.get("offline") is True and not me._gate.get("revoked"), me._gate)


# ═════ P: 연결 테스트 중 [전체 정지] ═════
class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def start_run(stop_during_test=False, sig_accts=None, auto_accts=None):
    me = app()
    me._gate = dict(NORMAL)
    me._arm_seq = 0
    me._live_session = False
    me._sig_accts = dict(sig_accts or {})
    me._auto_accts = dict(auto_accts or {})
    me._live_include = {"NQ": _Var(1), "GC": _Var(0), "BTC": _Var(0)}
    me._consent_ok = lambda: True
    me._broker_allowed = lambda b: True
    me._run = lambda fn: fn()
    me.root.after = lambda ms, fn: fn()
    me._master_arm = MagicMock()

    def _conn(a, bk):
        if stop_during_test:                     # 테스트가 도는 사이 [전체 정지]
            me._arm_seq += 1
            me._live_session = False
            me._live_ctx = {}
        return None
    me._conn_check = _conn
    o_ask = eqgui.messagebox.askyesno
    eqgui.messagebox.askyesno = lambda *a, **k: True
    SHOWN.clear()
    try:
        eqgui.App._master_start(me)
    finally:
        eqgui.messagebox.askyesno = o_ask
    return me


me = start_run(stop_during_test=True)
ck("P 연결 테스트 중 정지 → done()이 무장하지 않음", me._master_arm.call_count == 0, me._master_arm.call_args_list)
ck("P 취소 사실 로그 + '라이브 가동 시작' 없음", "무장 취소" in logs(me) and "라이브 가동 시작" not in logs(me), logs(me))
ck("P 세션은 닫힌 상태로 남음(무장 0)", me._live_session is False)
me = start_run(stop_during_test=False)
ck("P 정지가 없으면 종전처럼 켜진 계좌 무장", me._master_arm.call_count == 1 and "라이브 가동 시작" in logs(me))

# E: 마감 청산만 남은 상태에서 [라이브 시작]이 막히지 않는다
me = start_run(auto_accts={("NQ", 0): [{"acct": "111"}]})
ck("E 마감 청산만 남은 상태 → [라이브 시작] 진행(이미 가동 중으로 막지 않음)",
   me._master_arm.call_count == 1 and not any("이미 가동 중" in str(s) for s in SHOWN), SHOWN)
me = start_run(sig_accts={("NQ", 0): snap()}, auto_accts={("NQ", 0): [{"acct": "111"}]})
ck("E 신호 대기가 돌고 있으면 종전처럼 '이미 가동 중'", me._master_arm.call_count == 0
   and any("이미 가동 중" in str(s) for s in SHOWN), SHOWN)

# 정적: 세대 인자가 실제 스레드 기동에 실린다
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("정적: _master_arm이 루프를 _start_*_loop로만 띄움(세대 없는 직접 기동 없음)",
   "target=self._sig_loop, args=(_feed_url(self._token),)," not in src
   and "threading.Thread(target=self._auto_loop, daemon=True)" not in src)
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}: {FAIL}"))
sys.exit(1 if FAIL else 0)
