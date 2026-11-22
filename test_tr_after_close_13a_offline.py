#!/usr/bin/env python3
# EQ Autopilot 2026.11.13a 오프라인 시험 - 청산마다 트랙레코드 동기화(대표 2026-11-13 "거래 종료 직후 싱크하게 바꿔 앱을, 거래 종료 때마다").
# _send_fill(closed=True)(마감 청산, 평가 계좌 익절, 손절 감지, 청산 버튼이 모두 지나는 곳)가 3분 뒤 push_profile(auto=True)를 한 번 건다.
# 같은 창의 청산 여럿은 한 번으로, 다른 동기화가 돌면 2분 뒤 다시(최대 5번), 동의 해제, 비 Autopilot은 안 함. 0 = 통과. 네트워크 차단.
import os
import socket
import sys
import tempfile
from types import SimpleNamespace

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_13a_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402

assert E.APP_DIR.startswith(HOME), E.APP_DIR
src = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:500]}"))
    if not c:
        FAIL.append(n)


class Root:
    def __init__(self):
        self.q = []

    def after(self, ms, fn=None):
        self.q.append((ms, fn))
        return len(self.q)

    def run_next(self):
        ms, fn = self.q.pop(0)
        fn()
        return ms


class Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def mk(consent=True, tier="royal"):
    a = object.__new__(E.App)
    a.root = Root()
    a.lang = "ko"
    a.logs = []
    a.log = lambda m: a.logs.append(m)
    a._ui = lambda fn, ms=0: a.root.after(ms, fn)
    a.consent = Var(consent)
    a._gate = {"ok": True, "tier": tier, "caps": {}}
    a.pushes = []
    a.push_profile = lambda auto=False: a.pushes.append(auto)
    a._profile = {}
    a._open_assets = {"NQ", "GC"}
    a._save_cfg = lambda: None
    a._refresh_live_panel = lambda: None
    return a


ck("0 앱 버전 2026.11.13a 이상", E.App._APP_VER >= "2026.11.13a", E.App._APP_VER)
# 1. 청산 한 번 → 3분 뒤 한 번
a = mk()
E.App._send_fill(a, "NQ", closed=True)
ck("1a 청산 보고가 3분(180초) 뒤 동기화를 건다", [m for m, _ in a.root.q if m == 180000] == [180000], [m for m, _ in a.root.q])
ck("1b 화면에 한 줄", any("3분 뒤 트랙레코드를 동기화" in x for x in a.logs), a.logs)
# 같은 창에 다른 자산도 닫힘 → 더 걸지 않음
E.App._send_fill(a, "GC", closed=True)
ck("1c 같은 창의 두 번째 청산은 한 번으로 묶음", len([m for m, _ in a.root.q if m == 180000]) == 1, [m for m, _ in a.root.q])
_i = next(i for i, (m, _) in enumerate(a.root.q) if m == 180000)
a.root.q[_i][1]()
ck("1d 3분 뒤: push_profile(auto=True) 한 번", a.pushes == [True], a.pushes)
E.App._send_fill(a, "BTC", closed=True)
ck("1e 그 뒤 새 청산은 다시 건다", len([m for m, _ in a.root.q if m == 180000]) == 2, [m for m, _ in a.root.q])
# 2. 다른 동기화가 돌고 있으면 2분 뒤 다시, 최대 5번 뒤에는 그냥 부른다
b = mk()
b._tr_push_busy = True
E.App._tr_after_close(b, "NQ")
b.root.q.pop(0)[1]()
ck("2a 동기화 중이면 2분(120초) 뒤 다시", b.pushes == [] and [m for m, _ in b.root.q] == [120000], [m for m, _ in b.root.q])
for _ in range(5):
    b.root.q.pop(0)[1]()
ck("2b 다섯 번 미뤄도 돌고 있으면 그다음엔 부른다(무한 대기 없음)", b.pushes == [True] and b.root.q == [], (b.pushes, b.root.q))
b2 = mk()
b2._tr_push_busy = True
E.App._tr_after_close(b2, "NQ")
b2.root.q.pop(0)[1]()
b2._tr_push_busy = False
b2.root.q.pop(0)[1]()
ck("2c 동기화가 끝나면 다음 시도에서 부름", b2.pushes == [True], b2.pushes)
# 3. 조건: 동의 해제, 비 Autopilot 등급은 동기화 안 함(일일 자동과 같다)
c = mk(consent=False)
E.App._send_fill(c, "NQ", closed=True)
c.root.q[[m for m, _ in c.root.q].index(180000)][1]()
ck("3a 동의 해제면 안 함", c.pushes == [], c.pushes)
d = mk(tier="member")
E.App._send_fill(d, "NQ", closed=True)
d.root.q[[m for m, _ in d.root.q].index(180000)][1]()
ck("3b Operator(member) 등급이면 안 함", d.pushes == [], d.pushes)
ck("3c 끝나면 다시 걸 수 있게 표시를 푼다", not getattr(c, "_tr_after_close_set", True) and not getattr(d, "_tr_after_close_set", True))
# 4. 진입 보고(closed=False)는 걸지 않음
e = mk()
E.App._send_fill(e, "NQ", closed=False)
ck("4 진입 보고는 동기화를 걸지 않음", not [m for m, _ in e.root.q if m == 180000], [m for m, _ in e.root.q])
# 5. 배선
_sf = src[src.index("    def _send_fill(self, asset, closed=False"):]
_sf = _sf[:_sf.index("        _caps = (self._gate or {}).get(\"caps\", {})")]
ck("5a _send_fill 청산 갈래가 _tr_after_close를 부름(Autopilot 게이트 앞, 예외는 가둠)", "App._tr_after_close(self, asset)" in _sf)
_wg = src[src.index("        def _w_guard():"):]
_wg = _wg[:_wg.index("threading.Thread(target=_w_guard, daemon=True).start()")]
ck("5b 동기화 스레드가 돌 때 _tr_push_busy를 세우고 끝나면(실패해도) 내림",
   "self._tr_push_busy = True" in _wg and "finally:\n                self._tr_push_busy = False" in _wg)
ck("5c 하루 한 번 자동 동기화는 그대로", "AUTOPUSH_EVERY_S = 24 * 3600" in src and "self.push_profile(auto=True)" in src)
print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
