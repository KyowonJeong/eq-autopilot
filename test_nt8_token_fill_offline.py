#!/usr/bin/env python3
# EQ Autopilot - [NT8 브리지 설치/업데이트] Bridge Token 오프라인 시험(2026-10-07 회원 실사례 + 반박 리뷰). 0=통과
# 실제 동봉 EQAutopilotBridge.cs를 임시 HOME의 Documents/NinjaTrader 8/bin/Custom/AddOns에 쓰는 경로까지 탄다.
# ①칸 채움(읽기 전용 칸도)·nt8 자산 전부 같은 토큰·브리지 파일 토큰 = 앱 토큰 ②nt8 없음 → 안내만
# ③기존 토큰 재사용 ④BTC 탭이면 칸 그대로 ⑤혼합 자산(자산 브로커 projectx, 행 nt8)도 설치·전파
# ⑥파일 쓰기 실패 → 앱 토큰 안 바꿈 ⑦앱 브리지 서버가 떠 있으면 그 토큰 우선
# ⑧회원 실사례 순서(버튼→자동 저장→재시작→…) 구버전 재현/수리판 ⑨화면 브로커가 nt8 아니면 칸 안 건드림
import os
import re
import sys
import tempfile
from unittest.mock import MagicMock, patch

HOME = tempfile.mkdtemp()
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
eqgui.messagebox.showinfo = lambda *a, **k: None      # 빈 칸 저장 안내 창(세션 1회) - 시험은 막히지 않게
from eqexec.broker.nt8 import NT8Broker  # noqa: E402

FAIL = []
CUSTOM = os.path.join(HOME, "Documents", "NinjaTrader 8", "bin", "Custom")
CS = os.path.join(CUSTOM, "AddOns", "EQAutopilotBridge.cs")


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


class FakeEntry:
    def __init__(self, val="", state="normal"):
        self.v, self.state = val, state

    def cget(self, k):
        return self.state

    def config(self, state=None, **k):
        if state is not None:
            self.state = state

    def delete(self, a, b):
        if self.state != "normal":
            raise RuntimeError("readonly")
        self.v = ""

    def insert(self, i, s):
        if self.state != "normal":
            raise RuntimeError("readonly")
        self.v = s

    def get(self):
        return self.v


def mk(brokers, cur, entry_state="readonly", existing=(), rows=None, screen=None):
    me = MagicMock()
    me.lang = "ko"
    me._profile = {}
    me._asset = cur
    me._acfg = {a: {"broker": brokers.get(a, ""), "creds": {}, "accounts": []} for a in eqgui._ASSETS}
    for a, t in existing:
        me._acfg[a]["creds"]["nt8"] = {"f1": t, "f3": ""}
    for a, rws in (rows or {}).items():
        me._acfg[a]["accounts"] = rws
    for fn in ("_broker_of", "_creds_of", "_acct_broker", "_uses_nt8", "_nt8_store_token", "_nt8_fill_token_field"):
        setattr(me, fn, (lambda f: (lambda *a, **k: getattr(eqgui.App, f)(me, *a, **k)))(fn))
    me._broker_name = screen or me._broker_of(cur)
    me.user = FakeEntry("", entry_state)
    return me


def press(me, fresh=True, fail_write=False):
    import shutil
    shutil.rmtree(os.path.join(HOME, "Documents"), ignore_errors=True)
    os.makedirs(CUSTOM, exist_ok=True)
    if fail_write:
        open(os.path.join(CUSTOM, "AddOns"), "w").close()      # 디렉터리 자리에 파일 → makedirs 실패
    info, warn = [], []
    with patch.object(eqgui.messagebox, "askyesno", return_value=True), \
         patch.object(eqgui.messagebox, "showinfo", side_effect=lambda *a, **k: info.append(a)), \
         patch.object(eqgui.messagebox, "showwarning", side_effect=lambda *a, **k: warn.append(a)):
        eqgui.App._nt8_install_bridge(me)
    return info, warn


def cs_tok():
    try:
        return re.search(r'private const string Token\s*=\s*"([^"]*)"', open(CS, encoding="utf-8").read()).group(1)
    except Exception:
        return None


NT8 = {"NQ": "nt8", "GC": "nt8", "BTC": "bybit"}
f1 = lambda me, a: (me._acfg[a]["creds"].get("nt8") or {}).get("f1", "")

me = mk(NT8, "NQ", "readonly")
press(me)
t = f1(me, "NQ")
ck("① 토큰 생성·NQ 저장", len(t) >= 20, t)
ck("① 지금 탭 칸에 같은 토큰(읽기 전용 칸)", me.user.get() == t, me.user.get())
ck("① 칸 상태 원래대로(readonly)", me.user.state == "readonly", me.user.state)
ck("① GC도 같은 토큰", f1(me, "GC") == t, f1(me, "GC"))
ck("① BTC(bybit)엔 nt8 토큰 안 씀", f1(me, "BTC") == "", f1(me, "BTC"))
ck("① 브리지 파일 토큰 = 앱 토큰", cs_tok() == t, cs_tok())

me = mk({"NQ": "projectx", "GC": "projectx", "BTC": "bybit"}, "BTC")
info, _ = press(me)
ck("② nt8 없음 → 안내", len(info) == 1 and "브로커" in info[0][1], info)
ck("② 토큰 안 만듦·파일 안 씀", all(f1(me, a) == "" for a in eqgui._ASSETS) and cs_tok() is None)

me = mk(NT8, "GC", "normal", existing=[("NQ", "OLDTOKEN123")])
press(me)
ck("③ 기존 토큰 재사용(NQ·GC)", f1(me, "NQ") == "OLDTOKEN123" and f1(me, "GC") == "OLDTOKEN123")
ck("③ 칸 = 기존 토큰, 파일 = 기존 토큰", me.user.get() == "OLDTOKEN123" and cs_tok() == "OLDTOKEN123")

me = mk(NT8, "BTC", "readonly")
me.user = FakeEntry("BYBITKEY", "readonly")
press(me)
ck("④ BTC 탭 칸 그대로", me.user.get() == "BYBITKEY", me.user.get())
ck("④ NQ·GC 저장값은 채움", f1(me, "NQ") and f1(me, "NQ") == f1(me, "GC"))

# ⑤ 혼합 자산: NQ 자산 브로커는 projectx(Topstep), 행 하나는 nt8(Lucid) - 리뷰 P2 회귀
me = mk({"NQ": "projectx", "GC": "projectx", "BTC": "bybit"}, "NQ",
        rows={"NQ": [{"id": "LDI150X", "broker": "nt8"}, {"id": "150KTC1", "broker": "projectx"}]})
info, _ = press(me)
ck("⑤ 혼합 자산: '브로커 먼저' 안내 없이 설치", not any("브로커를" in str(i) for i in info) and cs_tok(), (info, cs_tok()))
ck("⑤ 혼합 자산 NQ nt8 창고에 토큰, GC도 같은 토큰(브로커 연결 공용 2026-10-07)",
   f1(me, "NQ") == cs_tok() and f1(me, "GC") == cs_tok() and me._acfg["NQ"]["creds"]["nt8"] is me._acfg["GC"]["creds"]["nt8"])
ck("⑤ 화면 브로커 projectx → 칸(TopstepX 이메일) 안 건드림", me.user.get() == "", me.user.get())

# ⑥ 파일 쓰기 실패 → 앱 토큰을 바꾸지 않는다
me = mk(NT8, "NQ", "readonly")
_, warn = press(me, fail_write=True)
ck("⑥ 쓰기 실패 경고", any("실패" in str(w) for w in warn), warn)
ck("⑥ 쓰기 실패 → 토큰 저장·칸 채움 없음", f1(me, "NQ") == "" and f1(me, "GC") == "" and me.user.get() == "")

# ⑦ 앱 브리지 서버가 그 포트에 떠 있으면 그 토큰 우선(리뷰 P3 - NT8만 재시작으로 붙게)
NT8Broker._BRIDGES[8377] = (None, None, "SERVERTOK")
try:
    me = mk(NT8, "NQ", "readonly", existing=[("NQ", "DISKTOK")])
    press(me)
    ck("⑦ 서버 토큰 우선: 파일·NQ·GC·칸", cs_tok() == "SERVERTOK" and f1(me, "NQ") == "SERVERTOK"
       and f1(me, "GC") == "SERVERTOK" and me.user.get() == "SERVERTOK", (cs_tok(), f1(me, "NQ")))
finally:
    NT8Broker._BRIDGES.pop(8377, None)


# ⑧ 회원 실사례 순서(2026-10-07): 버튼 → 자동 저장(1.2초) → 앱 재시작 → (빈 칸) → 버튼 다시 → 바로 재시작 → 채워짐.
#    수리판 = 첫 버튼 뒤 자동 저장·재시작에도 유지. (구버전 재현 줄은 뺐다 - 빈 칸 저장 = 삭제 경로가 없어져 재현 자체가 안 된다)
def autosave(me):
    me._live_include = {}
    with patch.object(eqgui, "_kc_del", side_effect=lambda *a, **k: me._kc_dels.append(a)), \
         patch.object(eqgui, "_f2_del", side_effect=lambda *a, **k: None), \
         patch.object(eqgui, "_f2_save", side_effect=lambda *a, **k: None):
        eqgui.App._save_current_asset(me)


def restart(me):
    """재시작 = 저장된 creds로 칸을 다시 그림(_build의 self.user.insert(0, _acur()['f1']))."""
    me.user = FakeEntry(eqgui.App._acur(me)["f1"], "readonly")


def member_flow(fixed):
    me = mk(NT8, "NQ", "readonly")
    me._kc_dels = []
    if not fixed:
        me._nt8_fill_token_field = lambda t: None
    press(me)
    autosave(me)
    restart(me)
    after_first = me.user.get()
    press(me)
    restart(me)
    return me, after_first, me.user.get()


me, a1, a2 = member_flow(fixed=False)
ck("⑧ 칸 채움이 없어도(옛 동작) 자동 저장이 저장된 토큰을 지우지 않음", me._kc_dels == [] and f1(me, "NQ") == cs_tok(),
   (me._kc_dels, f1(me, "NQ")))
me, a1, a2 = member_flow(fixed=True)
ck("⑧ 수리판: 첫 버튼 뒤 자동 저장·재시작에도 채워짐", a1 != "" and a1 == f1(me, "NQ") == cs_tok(), a1)
ck("⑧ 수리판: 키체인 삭제 없음", me._kc_dels == [], me._kc_dels)
ck("⑧ 수리판: 두 번째 버튼은 같은 토큰 재사용", a2 == a1, (a1, a2))

# ⑨ 계좌 설정 복사 뒤 화면 브로커 어긋남(리뷰 P2): 화면은 projectx인데 자산 브로커는 nt8 → _acur은 화면 브로커 창고
me = mk({"NQ": "nt8", "GC": "nt8", "BTC": "bybit"}, "GC", existing=[("GC", "BRIDGETOK")], screen="projectx")
me._acfg["GC"]["creds"]["projectx"] = {"f1": "user@mail", "f3": ""}
ck("⑨ _acur = 화면 브로커(projectx) 창고 값", eqgui.App._acur(me)["f1"] == "user@mail", eqgui.App._acur(me))
me._conn_by_broker = {"nt8": True}
eqgui.App._sync_tab_broker(me)
ck("⑨ _sync_tab_broker(패널 분리 뒤): 패널 브로커는 자산 브로커를 따라가지 않음, 연결 상태는 패널 브로커 것",
   me._broker_name == "projectx" and me._connected is False, (me._broker_name, me._connected))

# ⑩ nt8 자산 없음 + 저장된 토큰은 BTC 칸에만(옛 버그 잔재) → 그 자산에 저장(폴더 경로로 새지 않음, 재리뷰 회귀)
NT8Broker._BRIDGES[8377] = (None, None, "SRV10")
try:
    me = mk({"NQ": "projectx", "GC": "projectx", "BTC": "bybit"}, "NQ", existing=[("BTC", "STRAY")])
    press(me)
    ck("⑩ 저장 대상 = 자산(BTC)에 서버 토큰, .cs와 일치(폴더 경로로 새지 않음)",
       f1(me, "BTC") == "SRV10" and cs_tok() == "SRV10", (f1(me, "BTC"), cs_tok()))
finally:
    NT8Broker._BRIDGES.pop(8377, None)

_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
def _body(name):
    i = _src.index(f"    def {name}(")
    j = _src.index("\n    def ", i + 10)
    return _src[i:j]
ck("⑨ [계좌 설정 복사 ←] 없음(대표 2026-10-07 폐지)", "    def _copy_acct_setup(" not in _src)
for _fn in ("_import_settings",):
    _b = _body(_fn)
    ck(f"⑨ {_fn}: _build 전에 _sync_tab_broker", "self._sync_tab_broker()" in _b
       and _b.index("self._sync_tab_broker()") < _b.rindex("self._build()"))

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
