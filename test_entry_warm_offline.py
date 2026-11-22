#!/usr/bin/env python3
# 진입 직전 연결 재사용과 예열(대표 2026-10-20 진입 단계 시각: 모든 계좌 클레임 0.75~0.98초, Topstep 주문 접수 1.5~2초). 0 = 통과.
#   1 ProjectX _post와 로그인은 프로세스 공용 세션(_HTTP)으로 - requests.post를 바꿔 끼운 시험에서는 그것(종전 시험 그대로)
#   2 서버 클레임(_claim_entry)도 공용 세션(_SRV_HTTP)으로, 같은 규칙
#   3 _entry_warm: 무장 계좌 수만큼(최대 8) 서버 연결, ProjectX는 자격증명마다 계좌 수의 두 배(최대 8)까지 캐시를 거치지 않는 직접 호출,
#     주문은 없다. 실패해도 예외 없음
#   4 _px_warm_tick: 진입 90초 안이면 자산마다 진입 8초 전 예열 타이머를 한 번만 건다(모든 브로커)
# HOME, APPDATA = 임시 폴더, 네트워크 차단, EQ_NO_ALERT=1.
import os
import socket
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_warm_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402
import eqexec.broker.projectx as PX  # noqa: E402
import requests  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


class R:
    status_code = 200
    ok = True
    text = "claim:granted"

    def raise_for_status(self):
        pass

    def json(self):
        return {"success": True, "accounts": []}


# ── 1 ProjectX 세션 ───────────────────────────────────────────────────────────
_calls = []
PX._HTTP.post = lambda url, **kw: (_calls.append(("sess", url)), R())[1]
b = PX.ProjectXBroker(SimpleNamespace(base_url="https://x.test", user_name="u", api_key="k", accounts=[]))
b._token, b._token_at = "tok", __import__("time").time()
b._post("/api/Account/search", {"onlyActiveAccounts": True})
ck("1a ProjectX _post = 공용 세션", _calls == [("sess", "https://x.test/api/Account/search")], _calls)
_calls.clear()
_orig_post = requests.post
requests.post = lambda url, **kw: (_calls.append(("patched", url)), R())[1]
b._post("/api/Account/search", {"onlyActiveAccounts": True})
ck("1b requests.post를 바꿔 끼운 시험이면 그것(종전 시험 그대로)", _calls == [("patched", "https://x.test/api/Account/search")], _calls)
requests.post = _orig_post
_calls.clear()
b._token = None
PX._TOK_CACHE.clear()
PX._HTTP.post = lambda url, **kw: (_calls.append(("sess", url)), SimpleNamespace(
    status_code=200, raise_for_status=lambda: None, json=lambda: {"success": True, "token": "T"}))[1]
try:
    b.authenticate()
except Exception:
    pass
ck("1c 로그인도 공용 세션", _calls and _calls[0] == ("sess", "https://x.test/api/Auth/loginKey"), _calls)

# ── 2 서버 클레임 세션 ────────────────────────────────────────────────────────
_scalls = []
E._SRV_HTTP.post = lambda url, **kw: (_scalls.append(("sess", url, kw.get("json", {}).get("sig_id"))), R())[1]
app = object.__new__(E.App)
app._token = "tok-abc"
ok = E.App._claim_entry(app, {"id": "2026-10-20-NQ-1"}, "ACC1", per_process=True)
ck("2a 클레임 = 공용 세션(POST eqclaim), 허가", ok is True and _scalls and _scalls[0][1].endswith("/eqclaim")
   and _scalls[0][2] == "2026-10-20-NQ-1", _scalls)
_scalls.clear()
E._SRV_HTTP.post = lambda url, **kw: (_scalls.append(("sess", url)), SimpleNamespace(ok=True, text="claim:taken:abcd1234"))[1]
ck("2b 다른 기기가 먼저면 양보(종전 그대로)", E.App._claim_entry(app, {"id": "s"}, "ACC1") is False)
E._SRV_HTTP.post = lambda url, **kw: (_ for _ in ()).throw(OSError("down"))
ck("2c 서버 오류는 허가(fail-open, 종전 그대로)", E.App._claim_entry(app, {"id": "s"}, "ACC1") is True)

# ── 3 _entry_warm ─────────────────────────────────────────────────────────────
_gets, _pxp = [], []
_lk = threading.Lock()
E._SRV_HTTP.get = lambda url, **kw: (_lk.acquire(), _gets.append(url), _lk.release(), R())[3]


class FB:
    def _post(self, path, body, timeout=None):
        with _lk:
            _pxp.append(path)
        return {"accounts": []}

    def _accounts(self, *a, **k):
        raise AssertionError("캐시 경로를 쓰면 안 됨(동시 요청이 하나로 묶임)")

    def place_entry(self, *a, **k):
        raise AssertionError("예열은 주문하지 않는다")


_orig_build = E._build_broker
E._build_broker = lambda *a, **k: FB()
cfgs = [{"broker": "nt8", "acct": "L1"}, {"broker": "nt8", "acct": "T1"},
        {"broker": "projectx", "f1": "u", "f2": "k", "acct": "A"}, {"broker": "projectx", "f1": "u", "f2": "k", "acct": "B"},
        {"broker": "projectx", "f1": "u", "f2": "k", "acct": "C"}]
E.App._entry_warm(app, "NQ", cfgs)
ck("3a 서버 연결 = 무장 계좌 수(5), GET eqclaim", len(_gets) == 5 and all(u.endswith("/eqclaim") for u in _gets), _gets)
ck("3b ProjectX = 같은 자격증명 계좌 3개의 두 배(6), 직접 호출, 주문 없음",
   len(_pxp) == 6 and set(_pxp) == {"/api/Account/search"}, _pxp)
_gets.clear()
E.App._entry_warm(app, "GC", [{"broker": "nt8"}] * 20)
ck("3c 서버 연결 상한 8", len(_gets) == 8, len(_gets))
E._SRV_HTTP.get = lambda url, **kw: (_ for _ in ()).throw(OSError("down"))
E._build_broker = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no creds"))
try:
    E.App._entry_warm(app, "NQ", cfgs)
    ck("3d 실패해도 예외 없음", True)
except Exception as e:
    ck("3d 실패해도 예외 없음", False, e)
E._build_broker = _orig_build

# ── 4 예열 예약 ───────────────────────────────────────────────────────────────
_timers = []


class FT:
    def __init__(self, sec, fn, args=()):
        _timers.append((round(sec), fn, args))
        self.daemon = False

    def start(self):
        pass


_orig_timer, _orig_next = threading.Timer, E._next_entry_dt
threading.Timer = FT
_ent = datetime.now(timezone.utc) + timedelta(seconds=60)
E._next_entry_dt = lambda a: _ent if a == "NQ" else None
app2 = object.__new__(E.App)
app2._sig_accts = {("NQ", 0): {"broker": "nt8"}, ("NQ", 1): {"broker": "projectx", "f1": "u", "f2": "k"}}
app2.root = SimpleNamespace(after=lambda *a, **k: None)
E.App._px_warm_tick(app2)
E.App._px_warm_tick(app2)
ck("4a 진입 60초 전 → 52초 뒤(진입 8초 전) 예열 타이머 한 번(두 번 불러도), 계좌 둘 다 넘김",
   len([t for t in _timers if getattr(t[1], "__name__", "") == "_entry_warm"]) == 1
   and [t for t in _timers if getattr(t[1], "__name__", "") == "_entry_warm"][0][0] in (51, 52)
   and len([t for t in _timers if getattr(t[1], "__name__", "") == "_entry_warm"][0][2][1]) == 2, _timers)
_timers.clear()
_ent = datetime.now(timezone.utc) + timedelta(seconds=300)
app3 = object.__new__(E.App)
app3._sig_accts = dict(app2._sig_accts)
app3.root = app2.root
E.App._px_warm_tick(app3)
ck("4b 진입 90초 밖이면 예약 없음", not [t for t in _timers if getattr(t[1], "__name__", "") == "_entry_warm"], _timers)
threading.Timer, E._next_entry_dt = _orig_timer, _orig_next

# ── 5 라이브 동안 절전 막기(회원 기기가 하루 10~28번 잠들었다 깨어 신호를 늦게 받음, 2026-10-20) ──
import inspect  # noqa: E402
_src_ka = inspect.getsource(E._keep_awake)
ck("5a 윈도 = SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED), 끌 때는 ES_CONTINUOUS만",
   "SetThreadExecutionState" in _src_ka and "0x80000000" in _src_ka and "0x00000001" in _src_ka
   and "_ES_SYSTEM_REQUIRED if on else 0" in _src_ka)
_pop = []


class FP:
    def __init__(self, args, **kw):
        _pop.append(list(args))

    def terminate(self):
        _pop.append("terminate")


_orig_popen, _orig_plat = E.subprocess.Popen, E.sys.platform
E.subprocess.Popen = FP
E.sys.platform = "darwin"
E._CAFF = None
_r1 = E._keep_awake(True)
_r2 = E._keep_awake(True)                      # 이미 켜져 있으면 또 띄우지 않는다
_r3 = E._keep_awake(False)
ck("5b 맥 = caffeinate -i -w <앱 pid> 한 번, 끄면 종료", _r1 and not _r2 and _r3 and _pop[0][:3] == ["caffeinate", "-i", "-w"]
   and _pop[0][3] == str(os.getpid()) and _pop[1] == "terminate" and len(_pop) == 2, _pop)
E.subprocess.Popen, E.sys.platform = _orig_popen, _orig_plat
_ms = inspect.getsource(E.App._master_start)
_mt = inspect.getsource(E.App._master_stop)
ck("5c 라이브 시작에 켜고(화면 줄 _ui), 전체 정지에 끈다(화면 줄 _ui)",
   "_keep_awake(True)" in _ms and "self._ui(lambda: self.log" in _ms and "self._ui(lambda: _keep_awake(False))" in _mt)

# ── 6 앱 출시 전 검토(2026-10-20) ─────────────────────────────────────────────
PX._RD_CACHE.clear()
_n = []
PX._rd_cached(("t6", 1), 150, lambda: (_n.append(1), {"accounts": []})[1])
PX._rd_cached(("t6", 1), 150, lambda: (_n.append(1), {"accounts": [{"id": 1}]})[1])
_v = PX._rd_cached(("t6", 1), 150, lambda: (_n.append(1), {"accounts": []})[1])
ck("6a 빈 계좌 목록은 캐시에 남기지 않고, 채워진 답은 남긴다", len(_n) == 2 and _v == {"accounts": [{"id": 1}]}, (_n, _v))
_calls6 = []


def _flaky(url, **kw):
    _calls6.append(url)
    if len(_calls6) == 1:
        raise requests.exceptions.ConnectionError("stale pooled socket")
    return R()


PX._HTTP.post = _flaky
PX._http_post("https://x.test/api/Account/search", json={})
ck("6b 조회는 연결 오류면 한 번 더", len(_calls6) == 2, _calls6)
_calls6.clear()
try:
    PX._http_post("https://x.test/api/Order/place", json={})
    _raised = False
except requests.exceptions.ConnectionError:
    _raised = True
ck("6c 주문은 다시 보내지 않는다(이중 주문 금지)", _raised and len(_calls6) == 1, _calls6)
_c7 = []


def _flaky_srv(url, **kw):
    _c7.append(kw.get("json", {}).get("mid"))
    if len(_c7) == 1:
        raise requests.exceptions.ConnectionError("stale pooled socket")
    return SimpleNamespace(ok=True, text="claim:taken:abcd1234")


E._SRV_HTTP.post = _flaky_srv
ck("6d 서버 클레임: 연결 오류면 같은 mid로 한 번 더, 답(taken)을 따른다(fail-open으로 두 기기 이중 진입 금지)",
   E.App._claim_entry(app, {"id": "2026-10-20-NQ-1"}, "ACC1", per_process=True) is False and len(_c7) == 2 and _c7[0] == _c7[1], _c7)

print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
