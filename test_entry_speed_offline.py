#!/usr/bin/env python3
# 진입 1초 목표(대표 2026-10-16) 오프라인 시험. 0 = 통과.
#   1 ProjectX 읽기 캐시: max_age 없으면 매번 읽기(종전), 있으면 그 초 안 재사용, 같은 키 동시 요청은 한 번, 실패는 그대로 올림, 자격증명이 다르면 따로
#   2 _px_call: 인자를 모르는 어댑터, 시험 객체는 인자 없이 종전 호출 / _BgCall: 결과, 예외, default
#   3 신호 확인 주기: 진입 3초 전 ~ 30초 뒤 = 0.5초, 그 밖 = 종전 3초, 무장 계좌 없음 = 종전
#   4 배선: 진입 경로의 계좌 목록, 계약, 포지션은 캐시 인자, 현금가 조회는 주문을 막지 않음, 단계 시각 로그, 진입 전 예열
# HOME, APPDATA = 임시 폴더, 네트워크 차단, EQ_NO_ALERT=1.
import inspect
import os
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_speed_")
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

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


class B(PX.ProjectXBroker):
    def __init__(self, user="u", key="k", accounts=None, delay=0.0):
        super().__init__(SimpleNamespace(base_url="https://x.test", user_name=user, api_key=key, accounts=accounts or []))
        self.calls, self.delay, self.fail = [], delay, False

    def _post(self, path, body, timeout=None):
        self.calls.append(path)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("503")
        if path == "/api/Account/search":
            return {"accounts": [{"id": 11, "name": "S1", "balance": 150000.0}, {"id": 12, "name": "S2", "balance": 0.0}]}
        if path == "/api/Contract/search":
            return {"contracts": [{"id": "CON.F.US.MGC.Z26", "activeContract": True, "tickSize": 0.1}]}
        if path == "/api/Position/searchOpen":
            return {"positions": []}
        return {}


# ── 1 캐시 ───────────────────────────────────────────────────────────────────
PX._RD_CACHE.clear()
b = B()
b._accounts(); b._accounts()
ck("1a max_age 없음 = 매번 읽기(종전)", b.calls.count("/api/Account/search") == 2, b.calls)
b = B()
b._accounts(max_age=150); b.account_balance("S1", max_age=150); b.list_open_positions(acct_max_age=150)
ck("1b max_age = 계좌 목록 한 번, 포지션은 매번 새로", b.calls.count("/api/Account/search") == 1
   and b.calls.count("/api/Position/searchOpen") == 2, b.calls)
b2 = B()
b2._accounts(max_age=150)
ck("1c 같은 자격증명의 다른 객체(다른 계좌 스레드)도 같은 목록을 쓴다", b2.calls == [], b2.calls)
b3 = B(user="v")
b3._accounts(max_age=150)
ck("1d 자격증명이 다르면 따로 읽는다", b3.calls == ["/api/Account/search"], b3.calls)
PX._RD_CACHE.clear()
bs = [B(delay=0.3) for _ in range(3)]
th = [threading.Thread(target=x._accounts, kwargs={"max_age": 150}) for x in bs]
[t.start() for t in th]; [t.join() for t in th]
ck("1e 세 스레드가 동시에 찾아도 요청은 한 번", sum(x.calls.count("/api/Account/search") for x in bs) == 1, [x.calls for x in bs])
PX._RD_CACHE.clear()
bf = B(); bf.fail = True
try:
    bf._accounts(max_age=150); _raised = False
except RuntimeError:
    _raised = True
ck("1f 실패는 그대로 올리고 캐시에 남기지 않는다", _raised and not PX._RD_CACHE, PX._RD_CACHE)
PX._RD_CACHE.clear()
b = B(); b.search_contracts("MGC", max_age=1800); B().search_contracts("MGC", max_age=1800)
ck("1g 계약 조회 캐시(같은 base, 검색어)", b.calls == ["/api/Contract/search"], b.calls)
ck("1h 틱 크기 캐시는 프로세스 공용", B()._ticks is PX._TICKS)
PX._RD_CACHE.clear()
b = B()
b._accounts(max_age=150)
PX._RD_CACHE[("acct",) + PX._tok_key(b.cfg) + (True,)] = (time.time() - 200, PX._RD_CACHE[("acct",) + PX._tok_key(b.cfg) + (True,)][1])
b._accounts(max_age=150)
ck("1i 150초 지난 값은 다시 읽는다", b.calls.count("/api/Account/search") == 2, b.calls)

# ── 2 도우미 ─────────────────────────────────────────────────────────────────
ck("2a _px_call: 인자를 모르는 함수 = 인자 없이", E._px_call(lambda: 7, {"max_age": 1}) == 7)
ck("2b _px_call: 인자를 아는 함수 = 그대로, kw 없음 = 인자 없이", E._px_call(lambda max_age=None: max_age, {"max_age": 5}) == 5
   and E._px_call(lambda max_age=None: max_age, None) is None)
ck("2c _BgCall 결과", E._BgCall(lambda: 3).get(timeout=2) == 3)
_bg = E._BgCall(lambda: (_ for _ in ()).throw(ValueError("x")))
try:
    _bg.get(timeout=2); _r = False
except ValueError:
    _r = True
ck("2d _BgCall 예외는 get이 올리고, default가 있으면 그 값", _r and _bg.get(timeout=2, default="d") == "d")
ck("2e _BgCall 시간 초과 default", E._BgCall(lambda: time.sleep(1)).get(timeout=0.05, default=None) is None)

# ── 3 신호 확인 주기 ─────────────────────────────────────────────────────────
_orig_ne = E._next_entry_dt
NOW = datetime.now(timezone.utc)
st = SimpleNamespace(_sig_accts={("GC", 0): {"broker": "nt8"}})
try:
    E._next_entry_dt = lambda a: NOW + timedelta(seconds=2)
    ck("3a 진입 2초 전 = 0.5초 ± 0.15(지터)", 0.35 <= E.App._sig_poll_secs(st) <= 0.65)
    E._next_entry_dt = lambda a: NOW + timedelta(seconds=60)
    ck("3b 진입 60초 전 = 종전 3초", E.App._sig_poll_secs(st) == E.SIG_POLL_SECS)
    st2 = SimpleNamespace(_sig_accts={("GC", 0): {}})
    E._next_entry_dt = lambda a: NOW - timedelta(seconds=1)            # 한 바퀴 전에 본 진입 시각(이제 1초 지남)
    E.App._sig_poll_secs(st2)
    E._next_entry_dt = lambda a: NOW + timedelta(hours=4)              # 진입 시각이 지나 다음 진입으로 넘어감
    ck("3c 방금 지난 진입(30초 안) = 0.5초 ± 0.15", 0.35 <= E.App._sig_poll_secs(st2) <= 0.65)
    st2._sig_ent_last["GC"] = datetime.now(timezone.utc) - timedelta(seconds=40)
    ck("3d 40초 지남 = 종전 3초", E.App._sig_poll_secs(st2) == E.SIG_POLL_SECS)
    ck("3e 무장 계좌 없음 = 종전", E.App._sig_poll_secs(SimpleNamespace(_sig_accts={})) == E.SIG_POLL_SECS)
finally:
    E._next_entry_dt = _orig_ne

# ── 4 배선 ───────────────────────────────────────────────────────────────────
src = inspect.getsource(E.App._run_futures_entry)
ck("4a 진입 경로: 계좌 목록, 계약, 포지션에 캐시 인자(ProjectX만)", '_px_call(b._accounts, _cmax and {"max_age": _cmax})' in src
   and '_cmax and {"max_age": _cmax}' in src and "list_open_positions" in src and '_pxe = str(getattr(b, "name", "") or "") == "projectx"' in src)
ck("4b 클레임과 포지션 조회 동시(실제 진입, NT8 대기조 제외), 현재가는 _BgCall로 주문을 막지 않음, 체결 보고 때 꺼냄",
   "_pos_f = (_BgCall(" in src and "_px_box = _BgCall(" in src and "mkt_at_send=_px_at_send_v()" in src
   and "b.current_market_price(resolved[0][2])\n" not in src.replace("_BgCall(lambda: b.current_market_price(resolved[0][2]))", ""))
ck("4c 단계 시각 로그(entry_timing)", "self._entry_timing_log(asset, sc, _tm)" in src)
ck("4d 진입 전 예열 예약(20초 뒤 시작, 15초마다)", "root.after(20 * 1000, self._px_warm_tick)" in inspect.getsource(E.App.__init__)
   and "self.root.after(15 * 1000, self._px_warm_tick)" in inspect.getsource(E.App._px_warm_tick))
_sl = inspect.getsource(E.App._sig_loop_body)
# 10 = 종전 9 + 21l(2026-10-27 실행 경로 리뷰 P2) size_mult 없는 신호 건너뜀 한 자리
ck("4e 신호 루프: 주기는 _sig_poll_secs, 연결 재사용", "_t.sleep(SIG_POLL_SECS)" not in _sl and _sl.count("_t.sleep(self._sig_poll_secs())") == 10
   and "_sess = requests.Session()" in _sl)

# ── 5 NT8 애드온 자동 업데이트 ───────────────────────────────────────────────
_d = tempfile.mkdtemp(prefix="eq_nt8c_")
os.makedirs(os.path.join(_d, "AddOns"))
_inst = os.path.join(_d, "AddOns", "EQAutopilotBridge.cs")
_new = os.path.join(HERE, "nt8_addon", "EQAutopilotBridge.cs")
open(_inst, "w", encoding="utf-8").write('private const string BaseUrl = "http://127.0.0.1:8399";\nprivate const string Token   = "TOK-abc";\n'
                                         'private const string BridgeVer = "2026.09.23a";\n')
_od = E._nt8_custom_dirs
E._nt8_custom_dirs = lambda profile=None: [_d]
try:
    r = E._bridge_file_update({}, _new)
    _t2 = open(_inst, encoding="utf-8").read()
    ck("5a 옛 버전 = 새 파일로, 토큰과 포트는 설치된 값 그대로, .bak", r[0] == "updated" and 'Token   = "TOK-abc"' in _t2
       and "127.0.0.1:8399" in _t2 and 'BridgeVer = "2026.10.16c"' in _t2 and os.path.isfile(_inst + ".bak"), (r, _t2[:200]))
    ck("5b 같은 버전 = 그대로(same)", E._bridge_file_update({}, _new)[0] == "same")
    open(_inst, "w", encoding="utf-8").write('private const string Token   = "CHANGE-ME-SHARED-TOKEN";\nprivate const string BridgeVer = "2026.09.23a";\n')
    ck("5c 토큰이 배포 기본값이면 쓰지 않음(no_token)", E._bridge_file_update({}, _new)[0] == "no_token")
    E._nt8_custom_dirs = lambda profile=None: [tempfile.mkdtemp()]
    ck("5d 설치된 파일 없음 = no_file(새 설치는 버튼으로)", E._bridge_file_update({}, _new)[0] == "no_file")
finally:
    E._nt8_custom_dirs = _od
_au = inspect.getsource(E.App._nt8_bridge_update_safe)
ck("5e 진입 15분 전 ~ 10분 뒤, NT8 포지션이 있으면 미룬다", "15 * 60" in _au and "10 * 60" in _au and "net_qty" in _au)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
