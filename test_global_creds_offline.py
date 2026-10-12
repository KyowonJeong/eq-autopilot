#!/usr/bin/env python3
# EQ Autopilot - 브로커 연결 공용(NQ·GC가 브로커마다 한 연결) 오프라인 시험. 0=통과
# 대표 2026-10-07 "저장 방식 그대로. 자산별 말고 브로커별. 다만 자산별 계좌 설정에서 적용되는 브로커만 보여주기."
# 판정 문서 3.7 T1~T16 + 계좌 행 브로커 드롭다운 거르기. 실제 eqgui.App을 실제 Tk 위젯으로 띄운다.
# HOME = 임시 폴더, 네트워크 차단(socket), 키체인 = dict(실패 기기 흉내 가능), 메시지박스·PIN 창 = 기록/큐,
# 브로커 = 가짜(_build_broker 대체), 스레드는 지정 작업만 동기 실행, root.after는 0ms만 실행. 옛 빌드(03b·06a·09.30b)는
# git show로 꺼내 _load·_save_full·_connected_assets만 쓴다(키체인 함수는 같은 dict로 바꿔 끼움).
import copy
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_gc_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_SHARE", None)
CUSTOM = os.path.join(HOME, "Documents", "NinjaTrader 8", "bin", "Custom")
os.makedirs(CUSTOM, exist_ok=True)
NT8_CS = os.path.join(CUSTOM, "AddOns", "EQAutopilotBridge.cs")

import socket  # noqa: E402


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.socket.connect_ex = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk

import threading  # noqa: E402
RUN_SYNC = ("_async_load_key", "_master_start", "_autosave_fire", "_panel_conn_test", "_live_arm_assets", "_send_log", "_load_accounts_for_asset",
            "_save_creds_and_test", "_autoload_nt8_avail", "_autoload_topstep_scope", "_run.<locals>.wrap")


class FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **k):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        qn = getattr(self.target, "__qualname__", "")
        if any(s in qn for s in RUN_SYNC):
            self.target(*self.args, **self.kwargs)

    def join(self, *a, **k):
        pass

    def is_alive(self):
        return False


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui  # noqa: E402
threading.Thread = FakeThread
import tkinter as tk  # noqa: E402

import yaml  # noqa: E402

assert eqgui.CFG_PATH.startswith(HOME), eqgui.CFG_PATH

# ── 키체인(dict) - KC_FAIL이면 쓰기 실패·읽기 빈 값(키체인이 안 되는 기기) ──
KC, KC_FAIL, KC_DELS = {}, [False], []


def _kc_save(acc, s):
    if not s or KC_FAIL[0]:
        return False
    KC[acc or "default"] = s
    return True


def _kc_load(acc):
    return "" if KC_FAIL[0] else KC.get(acc or "default", "")


def _kc_del(acc):
    KC_DELS.append(acc)
    KC.pop(acc or "default", None)


eqgui._kc_save, eqgui._kc_load, eqgui._kc_del = _kc_save, _kc_load, _kc_del
MSG, PINS, ASK = [], [], [True]
eqgui.messagebox.askyesno = lambda *a, **k: (MSG.append(("ask",) + tuple(str(x) for x in a)) or ASK[0])
eqgui.messagebox.showinfo = lambda *a, **k: MSG.append(("info",) + tuple(str(x) for x in a))
eqgui.messagebox.showwarning = lambda *a, **k: MSG.append(("warn",) + tuple(str(x) for x in a))
eqgui.messagebox.showerror = lambda *a, **k: MSG.append(("error",) + tuple(str(x) for x in a))
eqgui.simpledialog.askstring = lambda *a, **k: (PINS.pop(0) if PINS else None)

EMAIL, PXKEY, TOKEN = "member@mail.com", "PX-API-KEY-777", "TOK-bridge-abcdef123456"
BUILDS = []


class FakePX:
    def __init__(self, f1, f2):
        self.f1, self.f2 = f1, f2

    def healthcheck(self):
        if not self.f2:
            raise RuntimeError("ProjectX auth failed (test)")

    def _accounts(self):
        return [{"name": "150KTC-A", "id": 9001}, {"name": "50KTC-B", "id": 9002}]

    def list_open_positions(self):
        return []


class FakeNT8:
    def __init__(self, f1):
        self.f1 = f1

    def healthcheck(self):
        return None

    def _accounts(self):
        return [{"name": "LDI-1"}]

    def _snapshot(self):
        return {"bridge_ver": ""}

    def list_open_positions(self):
        return []


class FakeCrypto:
    def __init__(self, b, f1, f2):
        self.name, self.f1, self.f2 = b, f1, f2

    def healthcheck(self):
        if not (self.f1 and self.f2):
            raise RuntimeError("crypto auth failed (test)")

    def list_open_positions(self):
        return []

    def entry_info(self):
        return []


def _fake_build(broker, f1, f2, f3, accounts):
    BUILDS.append((broker, f1))
    if broker == "projectx":
        return FakePX(f1, f2)
    if broker == "nt8":
        return FakeNT8(f1)
    if broker in ("bybit", "bitget"):
        return FakeCrypto(broker, f1, f2)
    raise RuntimeError("not in test")


eqgui._build_broker = _fake_build
LOGS = []
_orig_log = eqgui.App.log


def _log(self, m):
    LOGS.append(str(m))
    try:
        _orig_log(self, m)
    except Exception:
        pass


eqgui.App.log = _log
eqgui.App._bundled_bridge_ver = lambda self: ""
ROOT = tk.Tk()
ROOT.withdraw()
_orig_after = ROOT.after


def _after(ms, func=None, *args):
    if func is None:
        return _orig_after(ms)
    if ms == 0 or ms == "idle":
        return _orig_after(0, func, *args)
    return "dropped"


ROOT.after = _after
_oc = ROOT.after_cancel
ROOT.after_cancel = lambda i: None if i == "dropped" else _oc(i)


def flush():
    for _ in range(3):
        ROOT.update()


def start():
    for w in list(ROOT.winfo_children()):
        try:
            w.destroy()
        except Exception:
            pass
    eqgui._ENC_SECRETS.clear()
    eqgui._ENC_PIN = None
    eqgui._ENC_DROPPED.clear()
    app = eqgui.App(ROOT)
    app._gate = {"ok": True, "tier": "royal", "enabled": True, "force_dry_run": False,
                 "caps": {"use": True, "manualentry": True, "autoentry": True, "connect": True},
                 "brokers": {}, "reason": ""}
    try:
        app.consent.set(1)
    except Exception:
        pass
    flush()
    return app


def seed(assets, kc=None, cs_token=None, extra=None):
    d = {"lang": "ko", "token": "MEMBERTOKEN", "assets": copy.deepcopy(assets)}
    d.update(extra or {})
    with open(eqgui.CFG_PATH, "w") as f:
        yaml.safe_dump(d, f)
    KC.clear()
    KC.update(kc or {})
    KC_DELS.clear()
    KC_FAIL[0] = False
    try:
        os.remove(NT8_CS)
    except OSError:
        pass
    if cs_token:
        os.makedirs(os.path.dirname(NT8_CS), exist_ok=True)
        with open(NT8_CS, "w", encoding="utf-8") as f:
            f.write(f'private const string Token   = "{cs_token}";\n'
                    f'private const string BaseUrl = "http://127.0.0.1:8377";\n')


def row(id_, broker, one_r=600, label="", on=True):
    return {"id": id_, "one_r": one_r, "on": on, "label": label, "broker": broker}


def yraw():
    return open(eqgui.CFG_PATH, encoding="utf-8").read()


def ydisk():
    return yaml.safe_load(yraw()) or {}


def type_into(entry, text):
    st = str(entry.cget("state"))
    entry.config(state="normal")
    entry.delete(0, "end")
    entry.insert(0, text)
    if st != "normal":
        entry.config(state=st)


def logs_since(n):
    return "\n".join(LOGS[n:])


FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


BTC0 = {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "", "f3": ""}}, "accounts": [row("", "")]}

# ═════ T1: 10/7 회원 모양 - GC Topstep 아이디 칸에 Bridge Token, NQ Topstep 이메일+f2, nt8 토큰 ═════
T1 = {"NQ": {"broker": "projectx", "include": True,
             "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A"]},
                       "nt8": {"f1": "", "f3": "8377", "avail": ["LDI-1"]}},
             "accounts": [row("150KTC-A", "projectx"), row("LDI-1", "nt8")]},
      "GC": {"broker": "projectx", "include": True,
             "creds": {"projectx": {"f1": TOKEN, "f3": "", "avail": []}, "nt8": {"f1": "", "f3": ""}},
             "accounts": [row("LDI-1", "nt8")]},
      "BTC": BTC0}
seed(T1, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
n0 = len(LOGS)
app = start()
A = app._acfg
ck("T1 오염 걸러냄: GC Topstep 아이디 = NQ 이메일(토큰 아님)", A["GC"]["creds"]["projectx"]["f1"] == EMAIL,
   A["GC"]["creds"]["projectx"])
ck("T1 별칭: projectx·nt8 같은 dict", A["NQ"]["creds"]["projectx"] is A["GC"]["creds"]["projectx"]
   and A["NQ"]["creds"]["nt8"] is A["GC"]["creds"]["nt8"])
ck("T1 nt8 공용 토큰", A["GC"]["creds"]["nt8"]["f1"] == TOKEN)
ck("T1 split 없음", not app._creds_split, app._creds_split)
_l = logs_since(n0)
ck("T1 기동 로그 1회: 합쳤습니다 + 걸러낸 칸 노트", "공용으로 합쳤습니다" in _l and "TopstepX 로그인 이메일 칸에 다른 브로커 키" in _l, _l[-400:])
ck("T1 로그에 값 없음(토큰·이메일·키)", TOKEN not in _l and PXKEY not in _l, "")
app._save_cfg()
raw = yraw()
ck("T1 YAML 평문 토큰 0·앵커 0", TOKEN not in raw and "&id" not in raw and "*id" not in raw)
ck("T1 eq:GC:nt8:f1 미러(GC 행이 nt8 사용)", KC.get("eq:GC:nt8:f1") == TOKEN)
ck("T1 f2(이메일 키) 유지", KC.get(EMAIL) == PXKEY)
ck("T1 YAML GC Topstep 아이디 = 이메일", ((ydisk()["assets"]["GC"]["creds"].get("projectx")) or {}).get("f1") == EMAIL)
ck("T1 키체인 삭제 0", KC_DELS == [], KC_DELS)
app._on_asset("GC")
flush()
ck("T1 GC 탭 칸 = 이메일, 공용 안내 보임", app.user.get() == EMAIL
   and any("같이 씁니다" in str(w.cget("text")) for w in app._cfg_body.winfo_children() if w.winfo_class() == "TLabel"))
ck("T1 붙여 둔 노트는 프로필 표시(다음 기동엔 '합쳤습니다' 다시 안 냄)", app._profile.get("creds_shared_v1") is True)
n0 = len(LOGS)
app = start()
ck("T1 재기동 멱등: 같은 값·로그 '합쳤습니다' 없음", app._acfg["GC"]["creds"]["projectx"]["f1"] == EMAIL
   and "공용으로 합쳤습니다" not in logs_since(n0) and not app._creds_split)

# T1b: NQ Topstep이 비어 있고 GC Topstep 아이디 칸에만 Bridge Token - 걸러내지 않으면 토큰이 공용 '이메일'이 된다
T1b = copy.deepcopy(T1)
T1b["NQ"]["creds"]["projectx"] = {"f1": "", "f3": ""}
seed(T1b, {"eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._save_cfg()
ck("T1b 토큰이 공용 Topstep 아이디가 되지 않음·YAML 평문 0", app._acfg["NQ"]["creds"]["projectx"]["f1"] == ""
   and app._acfg["GC"]["creds"]["projectx"]["f1"] == "" and TOKEN not in yraw(), app._acfg["GC"]["creds"]["projectx"])
# 없는 창고를 _creds_of가 만들 때 다른 holder의 dict에 별칭으로 건다(split이면 따로)
_nq_px = app._acfg["NQ"]["creds"]["projectx"]
app._acfg["GC"]["creds"].pop("projectx", None)
ck("_creds_of 별칭: 없는 GC 창고 = NQ dict", app._creds_of("GC", "projectx") is _nq_px)
app._acfg["GC"]["creds"].pop("projectx", None)
app._creds_split.add("projectx")
ck("_creds_of split이면 따로", app._creds_of("GC", "projectx") is not _nq_px)
app._creds_split.discard("projectx")

# ═════ T2: 두 유효 Topstep 로그인 → split. GC [연결 지우기] 뒤 NQ로 합쳐지고 NQ f2 남음 ═════
T2 = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": "a@x.com", "f3": ""}},
             "accounts": [row("150KTC-A", "projectx")]},
      "GC": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": "b@x.com", "f3": ""}},
             "accounts": [row("50KTC-B", "projectx")]},
      "BTC": BTC0}
seed(T2, {"a@x.com": "KA", "b@x.com": "KB"})
n0 = len(LOGS)
app = start()
ck("T2 둘 다 API Key 있음 → split", "projectx" in app._creds_split
   and app._acfg["NQ"]["creds"]["projectx"] is not app._acfg["GC"]["creds"]["projectx"])
ck("T2 split 안내 로그", "서로 다른 Topstep 로그인" in logs_since(n0))
app._on_asset("GC"); flush()
ck("T2 GC 탭에 split 안내", any("서로 다른 Topstep" in str(w.cget("text"))
                               for w in app._cfg_body.winfo_children() if w.winfo_class() == "TLabel"))
MSG.clear()
app._clear_broker_conn(asset="GC")      # split - GC 줄의 [GC 연결 지우기]
flush()
ck("T2 split 해소 확인창: NQ 연결을 같이 쓰게 되고 GC 계좌 1개를 다시 골라야 함, 값 없음",
   any(m[0] == "ask" and "NQ의 Topstep 연결을 같이 씁니다" in m[2] and "켜진 계좌 1개" in m[2] and "b@x.com" not in m[2]
       for m in MSG), MSG)
ck("T2 [연결 지우기] 뒤 합쳐짐(NQ 값)", "projectx" not in app._creds_split
   and app._acfg["GC"]["creds"]["projectx"] is app._acfg["NQ"]["creds"]["projectx"]
   and app._acfg["GC"]["creds"]["projectx"]["f1"] == "a@x.com")
ck("T2 NQ f2 남음, GC f2 지움", KC.get("a@x.com") == "KA" and "b@x.com" not in KC, KC)
app = start()
ck("T2 재기동 뒤에도 합쳐진 상태", not app._creds_split and app._acfg["GC"]["creds"]["projectx"]["f1"] == "a@x.com")

# ═════ T3: nt8 - .cs 승자 / .cs 없으면 split / 같은 토큰 포트 한쪽만 비면 합침 ═════
T3 = {"NQ": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "8377", "avail": ["L1"]}},
             "accounts": [row("L1", "nt8")]},
      "GC": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "8377", "avail": ["L2"]}},
             "accounts": [row("L2", "nt8")]},
      "BTC": BTC0}
seed(T3, {"eq:NQ:nt8:f1": "TOKA-1111111111", "eq:GC:nt8:f1": "TOKB-2222222222"}, cs_token="TOKB-2222222222")
app = start()
ck("T3 .cs 토큰(GC 것)이 승자", app._acfg["NQ"]["creds"]["nt8"]["f1"] == "TOKB-2222222222"
   and app._acfg["NQ"]["creds"]["nt8"] is app._acfg["GC"]["creds"]["nt8"] and not app._creds_split)
_u = {"NQ": {"broker": "nt8", "creds": {"nt8": {"f1": "T", "f3": "", "avail": ["L1", "L0"]}}, "accounts": []},
      "GC": {"broker": "nt8", "creds": {"nt8": {"f1": "T", "f3": "8377", "avail": ["L2", "L1"]}}, "accounts": []},
      "BTC": {"broker": "bybit", "creds": {}, "accounts": []}}
eqgui._unify_creds(_u, bridge=("", ""))
ck("T3 가용 계좌 = 순서 지킨 합집합(NQ 먼저)", _u["NQ"]["creds"]["nt8"]["avail"] == ["L1", "L0", "L2"]
   and _u["GC"]["creds"]["nt8"] is _u["NQ"]["creds"]["nt8"], _u["NQ"]["creds"]["nt8"])
ck("T3 진 토큰 키체인 항목 지우지 않음(이주 중 삭제 0)", KC_DELS == [] and "eq:NQ:nt8:f1" in KC)
seed(T3, {"eq:NQ:nt8:f1": "TOKA-1111111111", "eq:GC:nt8:f1": "TOKB-2222222222"})
app = start()
ck("T3 .cs 없음 → split(종전 자산별)", "nt8" in app._creds_split
   and app._acfg["NQ"]["creds"]["nt8"]["f1"] == "TOKA-1111111111" and app._acfg["GC"]["creds"]["nt8"]["f1"] == "TOKB-2222222222")
T3b = copy.deepcopy(T3)
T3b["GC"]["creds"]["nt8"]["f3"] = ""
seed(T3b, {"eq:NQ:nt8:f1": "TOKA-1111111111", "eq:GC:nt8:f1": "TOKA-1111111111"})
app = start()
ck("T3 같은 토큰·포트 한쪽만 빔 → 합침(포트 채움)", not app._creds_split
   and app._acfg["GC"]["creds"]["nt8"]["f3"] == "8377" and app._acfg["GC"]["creds"]["nt8"] is app._acfg["NQ"]["creds"]["nt8"])

# ═════ T4: PIN 미해제 기동 - 합치기 0, 저장 뒤 블롭·kc_failed 유지, 다음 기동 PIN으로 복원 뒤 합침 ═════
_blob = eqgui._enc_pack("2468", {"NQ|nt8|f1": "TOKP-333333333", "GC|nt8|f1": "TOKP-333333333"})
T4 = copy.deepcopy(T3)
seed(T4, {}, extra={"kc_enc": {"blob": _blob, "fields": ["NQ|nt8|f1", "GC|nt8|f1"]}, "kc_failed": ["GC/nt8/f1"]})
KC_FAIL[0] = True
PINS[:] = []                                   # 기동 PIN 창 취소
app = start()
ck("T4 PIN 취소 → _enc_locked·합치기 0(nt8 split)", app._enc_locked is True and "nt8" in app._creds_split
   and app._acfg["NQ"]["creds"]["nt8"] is not app._acfg["GC"]["creds"]["nt8"])
app._save_cfg()
d = ydisk()
ck("T4 저장 뒤 블롭·kc_failed 유지", (d.get("kc_enc") or {}).get("blob") == _blob and "GC/nt8/f1" in (d.get("kc_failed") or []), d)
app._on_asset("GC"); app._on_asset("NQ")
ck("T4 탭 전환 저장 뒤에도 블롭 유지", (ydisk().get("kc_enc") or {}).get("blob") == _blob)
PINS[:] = ["2468"]
app = start()
ck("T4 다음 기동 PIN 입력 → 복원 뒤 합침", app._enc_locked is False and not app._creds_split
   and app._acfg["GC"]["creds"]["nt8"]["f1"] == "TOKP-333333333"
   and app._acfg["GC"]["creds"]["nt8"] is app._acfg["NQ"]["creds"]["nt8"])
KC_FAIL[0] = False

# ═════ T5: 키체인 실패 기기 - YAML에 크립토 f1(API Key)·모든 비밀 0, fields 해시 ═════
T5 = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": ""}},
             "accounts": [row("150KTC-A", "projectx")]},
      "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("", "")]},
      "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYBIT-APIKEY-SENTINEL", "f3": "0"}},
              "accounts": [row("", "bybit")]}}
seed(T5, {})
KC_FAIL[0] = True
app = start()
eqgui._f2_save(EMAIL, "PX-SECRET-SENTINEL")
eqgui._f2_save("BYBIT-APIKEY-SENTINEL", "BYBIT-SECRET-SENTINEL")
PINS[:] = ["1357", "1357"]
app._save_cfg()
raw = yraw()
ck("T5 YAML에 크립토 API Key·비밀 센티널 0", not any(s in raw for s in ("BYBIT-APIKEY-SENTINEL", "BYBIT-SECRET-SENTINEL",
                                                                     "PX-SECRET-SENTINEL")), raw[:300])
ck("T5 kc_enc.fields = 해시 이름", all(not f.startswith("f2|") or f.startswith("f2|#") for f in ydisk()["kc_enc"]["fields"]))
KC_FAIL[0] = False

# ═════ T6: 빈 칸 자동 저장·명시 저장·탭 전환 = 키체인 삭제 0, [연결 지우기] = 전부 삭제, 무장 중 거절 ═════
T6 = {"NQ": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "8377"}},
             "accounts": [row("LDI-1", "nt8")]},
      "GC": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": ""}}, "accounts": [row("LDI-1", "nt8")]},
      "BTC": BTC0}
seed(T6, {"eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("GC"); flush()
_t6msg = []
MSG.clear()
type_into(app.user, "")
app._autosave_fire()
_t6msg += MSG
ck("T6 자동 저장은 비운 칸을 다시 채우지 않음 + 칸 옆 '저장된 키 유지 중'(2026-10-08 P2-1)", app.user.get() == ""
   and str(app._kept_lbls["f1"].cget("text")) == "저장된 키 유지 중" and app._acfg["GC"]["creds"]["nt8"]["f1"] == TOKEN,
   (app.user.get(), app._kept_lbls["f1"].cget("text")))
MSG.clear()
type_into(app.user, "")
app._save_current_asset()
_t6msg += MSG
ck("T6 명시 저장(탭·패널 전환·라이브 시작)은 저장값으로 다시 채움(•••)·표시 지움", app.user.get() == TOKEN
   and str(app.user.cget("show")) == "•" and str(app._kept_lbls["f1"].cget("text")) == "", app.user.get())
app._on_asset("NQ"); flush()
type_into(app.user, "")
app._on_broker()
ck("T6 빈 칸 자동·명시 저장·탭 전환 → 키체인 삭제 0, 토큰 유지", KC_DELS == [] and app._acfg["NQ"]["creds"]["nt8"]["f1"] == TOKEN
   and KC.get("eq:GC:nt8:f1") == TOKEN, (KC_DELS, app._acfg["NQ"]["creds"]["nt8"]))
ck("T6 칸 비움 안내: 창 세션 1회·칸은 저장값으로 다시 채움", sum(1 for m in _t6msg if m[0] == "info" and "칸을 비워도" in m[2]) == 1
   and app.user.get() == TOKEN, (_t6msg, app.user.get()))
app._sig_accts[("NQ", 0)] = {"broker": "nt8", "f1": TOKEN, "f3": "8377", "acct": "LDI-1"}
MSG.clear()
ck("T6 무장 중 [연결 지우기] 거절", app._clear_broker_conn() is False and KC.get("eq:NQ:nt8:f1") == TOKEN
   and any("가동 중이라 지금은 지울 수 없습니다" in m[2] for m in MSG if m[0] == "warn"), MSG)
app._sig_accts.clear()
app._clear_broker_conn()
ck("T6 [연결 지우기] → NQ·GC 키체인 모두 삭제·칸 비움", "eq:NQ:nt8:f1" not in KC and "eq:GC:nt8:f1" not in KC
   and app._acfg["GC"]["creds"]["nt8"]["f1"] == "" and app._acfg["GC"]["creds"]["nt8"] is app._acfg["NQ"]["creds"]["nt8"])
app = start()
ck("T6 재기동 뒤에도 지워진 채", app._acfg["NQ"]["creds"]["nt8"]["f1"] == "" and app._acfg["GC"]["creds"]["nt8"]["f1"] == "")

# ═════ T7: 무장 가드 - NQ가 Topstep으로 무장 중이면 GC 탭에서 이메일을 바꿔도 쓰이지 않는다 ═════
T7 = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": ""}},
             "accounts": [row("150KTC-A", "projectx")]},
      "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("", "")]},
      "BTC": BTC0}
seed(T7, {EMAIL: PXKEY})
app = start()
app._sig_accts[("NQ", 0)] = {"broker": "projectx", "f1": EMAIL, "f3": "", "acct": "150KTC-A"}
app._on_asset("GC"); flush()
MSG.clear()
type_into(app.user, "other@mail.com")
app._autosave_fire()
ck("T7 NQ 무장 중 GC 편집 → 안 씀·칸 되돌림·안내", app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL
   and app.user.get() == EMAIL and any("가동 중이라 연결 정보를 바꾸지 않았습니다" in m[2] for m in MSG if m[0] == "warn"),
   (app._acfg["NQ"]["creds"]["projectx"], app.user.get(), MSG))
_cfg = eqgui.App._live_acct_cfg(app, ("NQ", 0), dict(app._sig_accts[("NQ", 0)], one_r=600))
ck("T7 신호 때 NQ 진입은 그대로(키 안 바뀜)", _cfg[0] is not None, _cfg)
app._sig_accts.clear()

# ═════ T8: 칸 출처 가드 + 쓰기 시점 오염 가드 ═════
seed(T1, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("GC"); flush()                   # GC 화면 = Topstep(projectx) 칸
app._broker_name = "nt8"                       # 화면 칸은 Topstep인데 브로커 이름만 nt8(옛 복사 경로가 만들던 어긋남)
type_into(app.user, EMAIL + "x")
app._save_current_asset()
ck("T8 칸 출처 가드: nt8 창고에 이메일 안 씀·projectx도 그대로", app._acfg["GC"]["creds"]["nt8"]["f1"] == TOKEN
   and app._acfg["GC"]["creds"]["projectx"]["f1"] == EMAIL and app._acfg["GC"]["broker"] == "projectx")
app._broker_name = "projectx"
app._build(); flush()
MSG.clear()
type_into(app.user, TOKEN)                      # Topstep 아이디 칸에 Bridge Token 붙여넣기
app._autosave_fire()
ck("T8 오염 가드: Topstep 아이디에 토큰 → 안 씀·칸 되돌림·안내", app._acfg["GC"]["creds"]["projectx"]["f1"] == EMAIL
   and app.user.get() == EMAIL and any("다른 브로커의 키가 들어 있어 저장하지 않았습니다" in m[2] for m in MSG), MSG)
ck("T8 YAML에 토큰 평문 0", TOKEN not in yraw())

# ═════ T9: f2 덮어쓰기 가드 - 아이디만 바꾸고 키 칸을 안 건드리면 새 아이디의 기존 키를 덮지 않는다 ═════
seed(T7, {EMAIL: PXKEY, "other@mail.com": "OTHER-KEY"})
app = start()
flush()
ck("T9 키 칸 = 지금 아이디의 키(비동기 로드)", app.key.get() == PXKEY, app.key.get())
type_into(app.user, "other@mail.com")
app._autosave_fire()
ck("T9 새 아이디의 기존 키 유지·키 칸 = 그 키", KC.get("other@mail.com") == "OTHER-KEY" and app.key.get() == "OTHER-KEY"
   and KC.get(EMAIL) == PXKEY, (KC, app.key.get()))
type_into(app.user, "fresh@mail.com")
app._autosave_fire()
ck("T9 키가 없는 새 아이디로는 키를 옮긴다(덮을 것 없음)", KC.get("fresh@mail.com") == "OTHER-KEY", KC)
app._unlocked = True
app.key.config(state="normal")
type_into(app.key, "TYPED-NEW-KEY")
type_into(app.user, "other@mail.com")
app._autosave_fire()
ck("T9 아이디와 키를 같이 넣으면 그 키로 저장", KC.get("other@mail.com") == "TYPED-NEW-KEY", KC)


# ═════ T10: 옛 빌드(03b·06a·09.30b) 롤백 - _load 값·옛 _connected_assets가 이주 전과 같고, 옛 저장 뒤 재기동 멱등 ═════
def _old(rev, name):
    path = os.path.join(HOME, f"eqgui_{name}.py")
    src = b""
    for _g in (HERE, os.path.dirname(os.path.realpath(os.path.join(HERE, "eqexec")))):   # 저장소 밖 사본에서 돌릴 때도
        try:
            src = subprocess.run(["git", "-C", _g, "show", f"{rev}:executor/eqgui.py"], capture_output=True,
                                 timeout=30).stdout
        except Exception:
            src = b""
        if len(src) >= 10000:
            break
    if len(src) < 10000:
        return None
    open(path, "wb").write(src)
    sp = importlib.util.spec_from_file_location(f"eqgui_old_{name}", path)
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    m._kc_save, m._kc_load, m._kc_del = _kc_save, _kc_load, _kc_del
    m.CFG_PATH = eqgui.CFG_PATH
    return m


def _old_conn(M):
    me = MagicMock()
    me._acfg = M._load()["assets"]
    for fn in ("_accts_of", "_acct_broker", "_creds_of", "_broker_of"):
        if hasattr(M.App, fn):
            setattr(me, fn, (lambda f: (lambda *a, **k: getattr(M.App, f)(me, *a, **k)))(fn))
    return M.App._connected_assets(me)


RB = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A"]}},
             "accounts": [row("150KTC-A", "projectx")]},
      "GC": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": "", "f3": ""}},
             "accounts": [row("", "", 600, "Topstep (ProjectX)")]},
      "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": "0"}}, "accounts": [row("", "bybit")]}}
SCEN = {"rb_ping(NQ Topstep, GC 손대지 않음, BTC)": (RB, {EMAIL: PXKEY, "eq:BTC:bybit:f1": "BK"}, None),
        "10/7 회원 모양": (T1, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, TOKEN),
        "두 로그인 split": (T2, {"a@x.com": "KA", "b@x.com": "KB"}, None)}
_olds = [(n, _old(r, n)) for r, n in (("d23796e5", "03b"), ("d910c67a", "06a"), ("eb41b834", "0930b"))]
ck("T10 옛 빌드 셋을 git에서 꺼냄", all(m is not None for _, m in _olds), [n for n, m in _olds if m is None])
for sname, (cfg, kc, cs) in SCEN.items():
    for oname, M in _olds:
        if M is None:
            continue
        seed(cfg, kc, cs_token=cs)
        before = _old_conn(M)
        _ol = M._load()["assets"]
        before_vals = {(a, b): (cr.get("f1"), cr.get("f3")) for a in ("NQ", "GC") for b, cr in _ol[a]["creds"].items()}
        app = start()
        sig1 = ({b: (app._acfg["NQ"]["creds"].get(b) or {}).get("f1") for b in ("projectx", "nt8")}, set(app._creds_split))
        app._on_asset("GC"); app._on_asset("NQ")             # 저장 두 번(탭 전환)
        after = _old_conn(M)
        ck(f"T10 [{sname}] {oname} 롤백: 옛 _connected_assets 그대로", after == before, (before, after))
        _ol2 = M._load()["assets"]
        _pairs_ok = ((_ol2["NQ"]["creds"].get("projectx") or {}).get("f1", "")
                     == (before_vals.get(("NQ", "projectx")) or ("",))[0] or "")
        ck(f"T10 [{sname}] {oname} 롤백: 옛 _load 값이 이주 전과 맞음(Topstep 아이디가 엉뚱한 값 아님)",
           _pairs_ok and TOKEN not in str(_ol2["GC"]["creds"].get("projectx")), (before_vals, _ol2["GC"]["creds"]))
        M._save_full("ko", "MEMBERTOKEN", _ol2, {"handle": ""})   # 옛 빌드가 다시 저장
        app = start()
        sig2 = ({b: (app._acfg["NQ"]["creds"].get(b) or {}).get("f1") for b in ("projectx", "nt8")}, set(app._creds_split))
        ck(f"T10 [{sname}] {oname} 옛 저장 뒤 재기동 멱등", sig1 == sig2, (sig1, sig2))

# ═════ T11: 핑 새 정의 ═════
seed(RB, {EMAIL: PXKEY, "eq:BTC:bybit:f1": "BK"})
app = start()
ck("T11 NQ만 설정 → GC 연결 아님(빈 기본 행), BTC 연결", app._connected_assets() == ["NQ", "BTC"], app._connected_assets())
ck("T11 _asset_brokers·multi", app._asset_brokers() == {"NQ": "projectx", "BTC": "bybit"}
   and app._asset_brokers_multi() == {"NQ": ["projectx"], "BTC": ["bybit"]}, (app._asset_brokers(), app._asset_brokers_multi()))
_av = {"NQ": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "", "avail": ["LDI-1"]}},
              "accounts": [row("LDI-1", "nt8")]}, "GC": RB["GC"], "BTC": BTC0}
seed(_av, {})
app = start()
ck("T11 가용 계좌만 있음 → 연결 아님", app._connected_assets() == [], app._connected_assets())

# ═════ T12: h1(a) - nt8 캐시 적중이 화면 브로커(projectx) 가용 목록을 바꾸지 않는다 ═════
T12 = {"NQ": {"broker": "projectx", "include": True,
              "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A", "50KTC-B"]},
                        "nt8": {"f1": "", "f3": "8377", "avail": ["LDI-1"]}},
              "accounts": [row("150KTC-A", "projectx"), row("LDI-1", "nt8")]},
       "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("150KTC-A", "projectx")]},
       "BTC": BTC0}
seed(T12, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("GC"); flush()
app._scope_cache = {("nt8", "8377", TOKEN[:8]): ["LDI-1", "LDI-2"]}
n0 = len(LOGS)
_before = list(app._acfg["GC"]["creds"]["projectx"]["avail"])
app._autoload_nt8_avail("GC"); flush()
ck("T12 projectx 가용 목록 그대로·'정리' 로그 없음", app._acfg["GC"]["creds"]["projectx"]["avail"] == _before
   and "가용 계좌 정리" not in logs_since(n0), (app._acfg["GC"]["creds"]["projectx"]["avail"], logs_since(n0)))
ck("T12 nt8 가용 목록에 반영", "LDI-2" in app._acfg["GC"]["creds"]["nt8"]["avail"])

# ═════ T13: h1(b) - 같은 Topstep 연결이어도 GC 행 계좌 ID를 대조한다(두 진입점, 네트워크 1회) ═════
T13 = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": ""}},
              "accounts": [row("150KTC-A", "projectx")]},
       "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("LDI-NOT-TOPSTEP", "projectx")]},
       "BTC": BTC0}
seed(T13, {EMAIL: PXKEY})
app = start()
for _a in ("NQ", "GC"):
    app._live_include[_a].set(1)
BUILDS.clear(); MSG.clear()
app._master_start(); flush()
ck("T13 라이브 시작: GC 행이 목록에 없음 → 전체 중단", not app._sig_accts
   and any(m[0] == "error" and "GC" in m[2] and "목록에 없음" in m[2] for m in MSG), MSG)
ck("T13 라이브 시작 네트워크 1회", len([b for b in BUILDS if b[0] == "projectx"]) == 1, BUILDS)
app._live_session = True
app._live_ctx = {"live": True, "perm_auto": True, "perm_use": True}
BUILDS.clear(); MSG.clear()
app._live_arm_assets(["NQ", "GC"]); flush()
ck("T13 가동 중 체크(_live_arm_assets): GC 거부·네트워크 1회", not app._sig_accts
   and any(m[0] == "error" and "GC" in m[2] for m in MSG) and len(BUILDS) == 1, (MSG, BUILDS))
app._live_session = False

# ═════ T14: 내보내기·가져오기 왕복, 옛 v1 충돌 파일 ═════
import tkinter.filedialog as fd  # noqa: E402
EXP = os.path.join(HOME, "x.eqset")
fd.asksaveasfilename = lambda *a, **k: EXP
fd.askopenfilename = lambda *a, **k: EXP
seed(RB, {EMAIL: PXKEY, "eq:BTC:bybit:f1": "BK"})
app = start()
PINS[:] = ["9999", "9999"]
app._export_settings()
_x = eqgui._settings_import_blob("9999", open(EXP, "rb").read())
ck("T14 내보내기 v1·미러 대상 쌍만(GC Topstep 사본 없음)·f2 포함", _x.get("v") == 1
   and "projectx" not in (_x["assets"]["GC"].get("creds") or {}) and _x["assets"]["NQ"]["creds"]["projectx"].get("f2") == PXKEY,
   _x["assets"]["GC"].get("creds"))
seed(BTC0 and {"NQ": RB["GC"], "GC": RB["GC"], "BTC": BTC0}, {})
app = start()
PINS[:] = ["9999"]
app._import_settings(); flush()
ck("T14 가져오기 → 공용으로 합침(GC가 NQ 연결 사용)", app._acfg["GC"]["creds"]["projectx"] is app._acfg["NQ"]["creds"]["projectx"]
   and app._acfg["GC"]["creds"]["projectx"]["f1"] == EMAIL and KC.get(EMAIL) == PXKEY)
_v1 = {"kind": "eq-autopilot-settings", "v": 1, "app": "old", "lang": "ko", "token": "MEMBERTOKEN", "dry_run": True,
       "profile": {}, "assets": {
           "NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": "a@x.com", "f3": "", "f2": "KA"}},
                  "accounts": [row("150KTC-A", "projectx")]},
           "GC": {"broker": "bybit", "include": True,
                  "creds": {"projectx": {"f1": "b@x.com", "f3": "", "f2": "KB"}, "bybit": {"f1": "STRAY", "f3": ""}},
                  "accounts": [row("50KTC-B", "projectx")]},
           "BTC": BTC0}}
open(EXP, "wb").write(eqgui._settings_export_blob("9999", _v1))
PINS[:] = ["9999"]
n0 = len(LOGS)
app._import_settings(); flush()
ck("T14 옛 v1 충돌 파일 → split(두 로그인 다 키 있음)", "projectx" in app._creds_split
   and app._acfg["GC"]["creds"]["projectx"]["f1"] == "b@x.com" and app._acfg["NQ"]["creds"]["projectx"]["f1"] == "a@x.com")
ck("T14 목록 밖 브로커(GC bybit) 걸러냄·로그", "bybit" not in app._acfg["GC"]["creds"] and app._acfg["GC"]["broker"] == "projectx"
   and "쓸 수 없는 브로커 설정은 가져오지 않았습니다" in logs_since(n0))

# ═════ T15: 브리지 버튼이 nt8 split을 해소 ═════
seed(T3, {"eq:NQ:nt8:f1": "TOKA-1111111111", "eq:GC:nt8:f1": "TOKB-2222222222"})
app = start()
ck("T15 시작 = nt8 split", "nt8" in app._creds_split)
app._nt8_install_bridge(); flush()
_cs = re.search(r'Token\s*=\s*"([^"]*)"', open(NT8_CS, encoding="utf-8").read()).group(1)
ck("T15 버튼 뒤 합쳐짐·두 자산·.cs 같은 토큰", "nt8" not in app._creds_split
   and app._acfg["NQ"]["creds"]["nt8"]["f1"] == app._acfg["GC"]["creds"]["nt8"]["f1"] == _cs, (_cs, app._creds_split))
app = start()
ck("T15 재기동 뒤에도 합쳐짐", "nt8" not in app._creds_split and app._acfg["GC"]["creds"]["nt8"]["f1"] == _cs)

# ═════ 계좌 행 브로커 드롭다운: 이 자산에 적용되는(연결이 설정된) 브로커 + 그 행이 쓰는 브로커만 ═════
seed(T7, {EMAIL: PXKEY})
app = start()
app._on_asset("GC"); flush()
_vals = [tuple(w["broker"].cget("values")) for w in app._acct_widgets.values() if w.get("broker") is not None]
ck("드롭다운: GC 행 = Topstep만(Lucid·IBKR 미설정)", _vals and all(v == (eqgui._broker_label("projectx"),) for v in _vals), _vals)
seed(T1, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("GC"); flush()
_vals = [set(w["broker"].cget("values")) for w in app._acct_widgets.values() if w.get("broker") is not None]
ck("드롭다운: 설정된 Topstep·Lucid 둘, IBKR 없음", _vals and all(v == {eqgui._broker_label("projectx"), eqgui._broker_label("nt8")}
                                                       for v in _vals), _vals)
seed({"NQ": T7["NQ"], "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("U1", "ibkr")]},
      "BTC": BTC0}, {EMAIL: PXKEY})
app = start()
app._on_asset("GC"); flush()
_vals = [set(w["broker"].cget("values")) for w in app._acct_widgets.values() if w.get("broker") is not None]
ck("드롭다운: 행이 쓰는 브로커(미설정 IBKR)는 남긴다", _vals and eqgui._broker_label("ibkr") in _vals[0], _vals)

# ═════ brokermix 3·7·9 ═════
T1x = copy.deepcopy(T1)
T1x["GC"]["accounts"] = [row("150KTC-A", "projectx"), row("", "nt8")]
seed(T1x, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("GC"); flush()
app._acct_edit = True
app.brokerbox.set(eqgui._broker_label("nt8")); app._on_broker(); flush()   # Lucid 화면
app._fill_scope(["LDI-9"], asset="GC", broker="nt8"); flush()
_rows = app._acfg["GC"]["accounts"]
_cbv = {i: tuple(w["acct_id"].cget("values")) for i, w in app._acct_widgets.items() if w.get("acct_id") is not None}
ck("BM3 nt8 목록이 Topstep 행 계좌 콤보에 안 들어감", all(("LDI-9" in v) == (app._acct_broker("GC", _rows[i]) == "nt8")
                                                    for i, v in _cbv.items()) and _cbv, _cbv)
app._acfg["GC"]["creds"]["nt8"]["f1"] = EMAIL       # 옛 판이 Bridge Token 칸에 남긴 이메일(기동 정리를 건너뛴 경우)
n0 = len(LOGS)
app._nt8_install_bridge(); flush()
_cs = re.search(r'Token\s*=\s*"([^"]*)"', open(NT8_CS, encoding="utf-8").read()).group(1)
ck("BM7 이메일은 .cs Token에 안 들어감·로그", _cs != EMAIL and "@" not in _cs and "토큰이 아니라 쓰지 않았습니다" in logs_since(n0), _cs)
seed({"NQ": T7["NQ"], "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("", "")]}, "BTC": BTC0},
     {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("GC"); flush()
app.brokerbox.set(eqgui._broker_label("nt8")); app._on_broker(); flush()
app._save_current_asset(); flush()
_r0 = app._acfg["GC"]["accounts"][0]
ck("BM9 패널에서 Lucid를 골라도 GC 빈 행·자산 브로커는 그대로(Topstep)", not _r0.get("broker")
   and app._acct_broker("GC", _r0) == "projectx" and app._acfg["GC"]["broker"] == "projectx", _r0)

# BM2: [연결 테스트] 응답이 오기 전에 화면 브로커를 바꿔도 Topstep 목록은 Topstep 창고에만
seed(T7, {EMAIL: PXKEY})
app = start()
app._on_asset("GC"); flush()
app.healthcheck()                               # 스레드는 동기, 목록 반영(after 0)은 아직 대기
app.brokerbox.set(eqgui._broker_label("nt8")); app._on_broker()
flush()
ck("BM2 응답 도착 전 브로커 전환 → nt8 가용 목록에 Topstep 계좌 없음", "150KTC-A" not in (app._acfg["GC"]["creds"].get("nt8") or {}).get("avail", [])
   and "150KTC-A" in app._acfg["GC"]["creds"]["projectx"].get("avail", []), app._acfg["GC"]["creds"])

# ═════ 반박 리뷰(2026-10-07) 수리 ═════
# R-P1: Bridge Token 칸(nt8 f1)에 Topstep 이메일 - 진짜 Topstep 이메일은 남고, nt8 쪽만 버린다(값의 모양으로 판정)
T1c = copy.deepcopy(T1)
T1c["GC"]["creds"]["projectx"] = {"f1": "", "f3": ""}
seed(T1c, {EMAIL: PXKEY, "eq:GC:nt8:f1": EMAIL})
n0 = len(LOGS)
app = start()
ck("R-P1 nt8 칸 이메일 → NQ Topstep 이메일 유지·nt8에서만 버림", app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL
   and app._acfg["GC"]["creds"]["nt8"]["f1"] == "" and "projectx" not in app._creds_split, app._acfg["NQ"]["creds"])
ck("R-P1 노트 = 'Bridge Token 칸에 이메일'(다른 브로커 키 아님)", "Bridge Token 칸에 이메일이 들어 있어" in logs_since(n0)
   and "TopstepX 로그인 이메일 칸에 다른 브로커 키" not in logs_since(n0), logs_since(n0)[-300:])
app._save_cfg()
app = start()
ck("R-P1 재기동에도 NQ 이메일 유지(키체인 항목은 그대로)", app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL
   and KC.get("eq:GC:nt8:f1") == EMAIL)
# 반대 방향(Topstep 아이디 칸에 토큰)은 T1·T1b가 본다. 모양으로 못 정하면 지우지 않고 split
T1d = copy.deepcopy(T1)
T1d["GC"]["creds"]["projectx"] = {"f1": "samevalue", "f3": ""}
seed(T1d, {EMAIL: PXKEY, "eq:GC:nt8:f1": "samevalue"})
n0 = len(LOGS)
app = start()
ck("R-P1 모양으로 못 정함 → 안 지우고 split·안내", app._acfg["GC"]["creds"]["projectx"]["f1"] == "samevalue"
   and app._acfg["GC"]["creds"]["nt8"]["f1"] == "samevalue" and {"projectx", "nt8"} <= app._creds_split
   and "어느 쪽 것인지 알 수 없습니다" in logs_since(n0), (app._creds_split, app._acfg["GC"]["creds"]))

# R-P2-1: BTC 빈 브로커 행 - 위 콤보로 Bitget 키 화면을 봐도 행 실행 거래소는 Bybit 그대로
BTCK = {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}, "bitget": {"f1": "", "f3": ""}},
        "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": ""}]}
seed({"NQ": T7["NQ"], "GC": T7["GC"], "BTC": BTCK}, {EMAIL: PXKEY, "eq:BTC:bybit:f1": "BYBITKEY-123456789012",
                                                    "BYBITKEY-123456789012": "BS", "eq:BTC:bitget:f1": "BITGETKEY-12345678901"})
app = start()
app._on_asset("BTC"); flush()
app.brokerbox.set(eqgui._broker_label("bitget")); app._on_broker(); flush()
_r = app._acfg["BTC"]["accounts"][0]
ck("R-P2-1 Bitget 화면 보기 → BTC 행은 Bybit 그대로", app._acct_broker("BTC", _r) == "bybit", _r)
app._acct_edit = True
app._add_acct(); flush()
_rows = app._acfg["BTC"]["accounts"]
ck("R-P2-1 [계좌 추가] → 기존 행 Bybit 1R 300 유지, 새 행 Bitget",
   app._acct_broker("BTC", _rows[0]) == "bybit" and float(_rows[0]["one_r"]) == 300
   and len(_rows) == 2 and app._acct_broker("BTC", _rows[1]) == "bitget", _rows)

# R-P2-3: 브리지 버튼이 포트가 다른 nt8 split을 합칠 때 .cs에 쓴 포트를 앱도 쓰고, 화면 칸도 다시 그린다
T15p = copy.deepcopy(T3)
T15p["NQ"]["creds"]["nt8"]["f3"] = "8390"
seed(T15p, {"eq:NQ:nt8:f1": "TOKA-1111111111", "eq:GC:nt8:f1": "TOKB-2222222222"})
app = start()
app._on_asset("GC"); flush()
app._nt8_install_bridge(); flush()
_txt = open(NT8_CS, encoding="utf-8").read()
_csp = re.search(r"127\.0\.0\.1:(\d+)", _txt).group(1)
ck("R-P2-3 합친 뒤 앱 포트 = .cs 포트(NQ·GC 같음)", app._acfg["NQ"]["creds"]["nt8"]["f3"] == _csp
   and app._acfg["GC"]["creds"]["nt8"] is app._acfg["NQ"]["creds"]["nt8"] and "nt8" not in app._creds_split,
   (_csp, app._acfg["NQ"]["creds"]["nt8"], app._acfg["GC"]["creds"]["nt8"]))
app._autosave_fire(); flush()
ck("R-P2-3 이어서 자동 저장해도 포트 유지(화면 칸 다시 그림)", app._acfg["GC"]["creds"]["nt8"]["f3"] == _csp
   and app.f3.get() == _csp, (app.f3.get(), app._acfg["GC"]["creds"]["nt8"]))

# R-P3 잠긴 세션: 블롭에만 값이 있는 행 전용 nt8 창고가 YAML에서 빠지지 않고, 다음 PIN 기동에 되채워진다. fields 원문 이름 정리
_blob2 = eqgui._enc_pack("2468", {"NQ|nt8|f1": "TOKL-4444444444", "f2|BYBITKEY-raw": "SEC"})
TL = {"NQ": {"broker": "projectx", "include": True,
             "creds": {"projectx": {"f1": EMAIL, "f3": ""}, "nt8": {"f1": "", "f3": "", "avail": []}},
             "accounts": [row("150KTC-A", "projectx"), row("LDI-1", "nt8")]},
      "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("", "")]}, "BTC": BTC0}
seed(TL, {}, extra={"kc_enc": {"blob": _blob2, "fields": ["NQ|nt8|f1", "f2|BYBITKEY-raw"]}})
KC_FAIL[0] = True
PINS[:] = []
app = start()
app._on_asset("GC"); app._on_asset("NQ")
d = ydisk()
ck("R-P3 잠긴 저장: NQ creds.nt8 항목 유지·블롭 유지", "nt8" in d["assets"]["NQ"]["creds"]
   and d["kc_enc"]["blob"] == _blob2, d["assets"]["NQ"]["creds"])
ck("R-P3 잠긴 저장: kc_enc.fields에 f1 원문 없음", "BYBITKEY-raw" not in yraw(), d["kc_enc"]["fields"])
_s = ydisk(); _s["assets"]["NQ"]["creds"].pop("nt8", None)             # 옛 저장이 빼먹은 경우도
open(eqgui.CFG_PATH, "w").write(yaml.safe_dump(_s))
PINS[:] = ["2468"]
app = start()
ck("R-P3 다음 PIN 기동 → 창고가 없어도 만들어 되채움", app._acfg["NQ"]["creds"]["nt8"]["f1"] == "TOKL-4444444444",
   app._acfg["NQ"]["creds"].get("nt8"))
KC_FAIL[0] = False
# 잠긴 세션 문구: 자동 저장 줄 = '이 자산에만 적용', 빈 칸 안내 = PIN을 넣은 뒤 지우라
seed(T4, {}, extra={"kc_enc": {"blob": _blob, "fields": ["NQ|nt8|f1", "GC|nt8|f1"]}})
KC_FAIL[0] = True
PINS[:] = []
app = start()
_t = [str(w.cget("text")) for w in app._cfg_body.winfo_children() for w in [w] + list(w.winfo_children())
      if w.winfo_class() == "TLabel"]
ck("R-P3 잠긴 세션 자동 저장 줄 = '(재시작해도 유지)'만(같이 씁니다 아님)", any(x.endswith("(재시작해도 유지)") for x in _t)
   and not any("같이 씁니다" in x for x in _t), _t)
KC_FAIL[0] = False

# R-P3 h1(b): 숫자 계좌 id로 적은 행은 통과(주문 경로와 같은 기준), 목록에 없는 행은 할 일을 말한다
T13n = copy.deepcopy(T13)
T13n["GC"]["accounts"] = [row("9002", "projectx")]
seed(T13n, {EMAIL: PXKEY})
app = start()
for _a in ("NQ", "GC"):
    app._live_include[_a].set(1)
MSG.clear()
app._master_start(); flush()
ck("R-P3 h1(b) 숫자 id 행 → 통과·무장", bool(app._sig_accts) and not any(m[0] == "error" for m in MSG), MSG)
app._sig_accts.clear(); app._auto_accts.clear(); app._live_session = False
seed(T13, {EMAIL: PXKEY})
app = start()
for _a in ("NQ", "GC"):
    app._live_include[_a].set(1)
MSG.clear()
app._master_start(); flush()
ck("R-P3 h1(b) 목록에 없는 행 → 끄거나 지우라는 안내", any(m[0] == "error" and "끄거나 지우세요" in m[2] for m in MSG), MSG)

# R-P3 오염 거절 문구가 그 칸 이름을 말한다(Bridge Port 칸에 토큰)
seed(T6, {"eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
MSG.clear()
type_into(app.f3, TOKEN)
app._autosave_fire()
ck("R-P3 Bridge Port에 토큰 → '포트 번호' 칸 이름으로 거절", any("포트 번호(기본 8377) 값을 넣으세요" in m[2] for m in MSG)
   and app._acfg["NQ"]["creds"]["nt8"]["f3"] == "8377", MSG)

# R-P3 [로그 보내기] 실패 창(NameError 없이 뜬다)
import requests as _rq  # noqa: E402
_orig_post = _rq.post
_rq.post = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))
app._collect_log = lambda *a, **k: "x"
MSG.clear()
ASK[0] = True
eqgui.App._send_log(app); flush()
_rq.post = _orig_post
ck("R-P3 [로그 보내기] 실패 창이 뜸(ConnectionError)", any(m[0] == "warn" and "ConnectionError" in m[2] for m in MSG), MSG)

# R-P3 가져오기 거름 로그 = 표시 이름(내부 키 아님)
seed(RB, {EMAIL: PXKEY})
app = start()
_v2 = copy.deepcopy(_v1)
open(EXP, "wb").write(eqgui._settings_export_blob("9999", _v2))
PINS[:] = ["9999"]
n0 = len(LOGS)
app._import_settings(); flush()
ck("R-P3 가져오기 거름 로그 = 'GC Bybit (USDT perp)'", "GC Bybit (USDT perp)" in logs_since(n0) and "GC/bybit" not in logs_since(n0))

# ═════ 브로커 연결 설정 패널(대표 2026-10-07 "오픈 전에 해야 해" - 자산 탭 없이 브로커마다 한 곳) ═════
def _panel_texts(app):
    out = []

    def _walk(w):
        for c in w.winfo_children():
            try:
                out.append((c.winfo_class(), str(c.cget("text"))))
            except Exception:
                pass
            _walk(c)
    _walk(app._cfg_body)
    return out


seed(T1, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
_pt = _panel_texts(app)
ck("PANEL 제목 = 브로커 연결 설정", str(app._cfg_hdr.cget("text")).endswith("브로커 연결 설정"), app._cfg_hdr.cget("text"))
ck("PANEL 자산 탭 버튼 없음(나스닥·금·비트코인)", not any(k == "Button" and t in ("나스닥 (NQ)", "금 (GC)", "비트코인 (BTC)")
                                                 for k, t in _pt), [t for k, t in _pt if k == "Button"])
ck("PANEL 드롭다운 = 브로커 다섯 전부", tuple(app.brokerbox.cget("values")) == tuple(eqgui._broker_label(b) for b in
   ("projectx", "nt8", "ibkr", "bybit", "bitget")), app.brokerbox.cget("values"))
ck("PANEL 기동 기본 = NQ 첫 켜진 행 브로커(Topstep)", app._broker_name == "projectx" and app._panel_asset == "NQ")
_snap = lambda a: ({x: a._acfg[x]["broker"] for x in eqgui._ASSETS},
                   {x: [a._acct_broker(x, r) for r in a._acfg[x]["accounts"]] for x in eqgui._ASSETS})
_s0 = _snap(app)
for _tab in ("NQ", "GC", "BTC"):
    app._on_asset(_tab); flush()
    for _b in ("projectx", "nt8", "ibkr", "bybit", "bitget", "projectx"):
        app.brokerbox.set(eqgui._broker_label(_b)); app._on_broker(); flush()
_s1 = _snap(app)
ck("PANEL 브로커를 어느 탭에서 무엇으로 골라도 자산·행 실행 브로커 그대로", _s1 == _s0, (_s0, _s1))
app._on_asset("GC"); flush()
app.brokerbox.set(eqgui._broker_label("bybit")); app._on_broker(); flush()
ck("PANEL 계좌 탭(GC)과 패널 브로커(Bybit)는 따로 - 칸은 BTC Bybit 창고", app._asset == "GC" and app._panel_asset == "BTC"
   and app._broker_name == "bybit")
BUILDS.clear()
type_into(app.user, "BYBIT-PANEL-KEY-0001")
app._autosave_fire(); flush()
ck("PANEL 연결 확인은 패널 브로커(Bybit)로·GC Topstep 아님", any(b[0] == "bybit" for b in BUILDS)
   and not any(b[0] == "projectx" for b in BUILDS), BUILDS)
ck("PANEL Bybit 키는 BTC 창고에", app._acfg["BTC"]["creds"]["bybit"]["f1"] == "BYBIT-PANEL-KEY-0001"
   and "bybit" not in app._acfg["GC"]["creds"])
BUILDS.clear()
app.b_acc.invoke(); flush()                     # 계좌 설정의 [계좌 목록 불러오기] 버튼 그대로
ck("PANEL [계좌 목록 불러오기] = 계좌 탭 자산(GC)의 Topstep·Lucid(패널 Bybit 아님)", any(b[0] == "projectx" for b in BUILDS)
   and not any(b[0] == "bybit" for b in BUILDS), BUILDS)
app.brokerbox.set(eqgui._broker_label("projectx")); app._on_broker(); flush()
type_into(app.user, "new@mail.com")
app._autosave_fire(); flush()
ck("PANEL 공용 칸 편집 = NQ·GC 같은 dict에 한 번", app._acfg["NQ"]["creds"]["projectx"] is app._acfg["GC"]["creds"]["projectx"]
   and app._acfg["GC"]["creds"]["projectx"]["f1"] == "new@mail.com")
ck("PANEL 공용 안내·[연결 지우기] 하나", any("같이 씁니다" in t for k, t in _panel_texts(app))
   and sum(1 for k, t in _panel_texts(app) if k == "TButton" and "연결 지우기" in t) == 1, _panel_texts(app))
# 브리지 버튼: 패널이 Bybit여도 nt8 토큰은 NQ·GC에, 패널은 그대로
app.brokerbox.set(eqgui._broker_label("bybit")); app._on_broker(); flush()
app._nt8_install_bridge(); flush()
ck("PANEL 브리지 버튼(패널 Bybit) → 토큰은 NQ·GC nt8, 패널·Bybit 칸 그대로", app._broker_name == "bybit"
   and app._acfg["GC"]["creds"]["nt8"]["f1"] == TOKEN and app.user.get() == "BYBIT-PANEL-KEY-0001", (app._broker_name, app.user.get()))
# split 표시: 자산별 값·자산별 [연결 지우기]·고치기 전환
seed(T2, {"a@x.com": "KA", "b@x.com": "KB"})
app = start()
_pt = [t for k, t in _panel_texts(app)]
ck("PANEL split: 안내 + NQ·GC 값 둘 다 보임 + 자산별 [연결 지우기]", any("서로 다른 Topstep 로그인" in t for t in _pt)
   and any(t.startswith("NQ: a@x.com") for t in _pt) and any(t.startswith("GC: b@x.com") for t in _pt)
   and "NQ 연결 지우기" in _pt and "GC 연결 지우기" in _pt, _pt)
ck("PANEL split: 칸 = NQ 것(기본)", app.user.get() == "a@x.com" and app._panel_asset == "NQ")
app._panel_asset = "GC"                         # 칸은 NQ 것으로 그려져 있는데 패널 자산만 GC(다시 그리기 전)
type_into(app.user, "zz@x.com")
app._save_current_asset(); flush()
ck("PANEL split: 칸 출처 가드(자산) - NQ 칸 값이 GC에 안 씀·NQ도 그대로", app._acfg["GC"]["creds"]["projectx"]["f1"] == "b@x.com"
   and app._acfg["NQ"]["creds"]["projectx"]["f1"] == "a@x.com", (app._acfg["NQ"]["creds"]["projectx"], app._acfg["GC"]["creds"]["projectx"]))
app._panel_asset = "NQ"; app._build(); flush()
app._on_panel_asset("GC"); flush()
ck("PANEL split: [고치기] GC → 칸 = GC 것", app.user.get() == "b@x.com" and app._panel_asset == "GC")
type_into(app.user, "b2@x.com")
BUILDS.clear()
app._autosave_fire(); flush()
ck("PANEL split: 계좌 탭이 NQ여도 연결 확인은 패널 칸(GC 로그인)으로", app._asset == "NQ"
   and ("projectx", "b2@x.com") in BUILDS and ("projectx", "a@x.com") not in BUILDS, BUILDS)
ck("PANEL split: GC 칸 편집은 GC에만", app._acfg["GC"]["creds"]["projectx"]["f1"] == "b2@x.com"
   and app._acfg["NQ"]["creds"]["projectx"]["f1"] == "a@x.com")
app._on_asset("BTC"); flush()
ck("PANEL split: 계좌 탭을 바꿔도 패널(GC 칸) 그대로", app._panel_asset == "GC" and app.user.get() == "b2@x.com")
# 기동 기본: NQ 첫 켜진 행이 Lucid면 Lucid
seed(T3, {"eq:NQ:nt8:f1": "TOKA-1111111111", "eq:GC:nt8:f1": "TOKA-1111111111"})
app = start()
ck("PANEL 기동 기본 = NQ 첫 켜진 행 브로커(Lucid)", app._broker_name == "nt8")

# ═════ 라이브 패널 문구 자리(대표 2026-10-07 "라이브 버튼 근처 텍스트가 너무 많아. 맨 아래로") ═════
# 11e(대표 2026-10-09 확정): 로그 아래 = 일괄 청산 경고(warn_mix) → 트랙레코드 → 주의 사항(접이식) → 회색 한 줄(caut_foot).
# 창을 실제로 띄워야(deiconify) 캔버스 안 위젯 높이·위치가 진짜 값이 된다 - 숨긴 창에선 캔버스 창 항목 크기가 안 맞춰져
# 옛 시험이 미표시(rooty 67 등)를 잘못 읽었다.
_CAUT_KEYS = ["caut_auto_h", "caut_auto_1", "caut_auto_2", "caut_stop_h", "caut_stop_1", "caut_stop_2",
              "caut_conn_h", "caut_conn_1", "caut_conn_2", "caut_risk_h", "caut_risk_1", "caut_risk_2", "caut_risk_3"]
ck("LIVE 옛 문구 키 정리(consent_detail·live_note·live_1r_note·subtitle 없음)",
   not any(k in eqgui.T for k in ("consent_detail", "live_note", "live_1r_note", "subtitle")))
ck("LIVE 동의 문안 버전 11f", eqgui._CONSENT_VER == "golive-6item-2026-10-09f", eqgui._CONSENT_VER)
ck("LIVE warn_mix 확정 문안(KO/EN)",
   eqgui.T["warn_mix"]["ko"] == "⚠ 청산하면 그 계좌의 포지션이 모두 닫힙니다. 다른 거래는 섞지 말고 전용 계좌를 쓰세요."
   and eqgui.T["warn_mix"]["en"] == "⚠ Closing flattens every position on that account. Keep other trades off it and use a dedicated account.")
ROOT.deiconify()
ROOT.geometry("1000x900")
for _lang in ("ko", "en"):
    seed(T1, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN, extra={"lang": _lang})
    app = start()
    flush()
    ck(f"LIVE[{_lang}] 동의 꺼진 채 처음 그리면 주의 사항 펼침", app._caut_open and bool(app._caut_body.winfo_ismapped()))
    _bt = [str(w.cget("text")) for w in app._caut_body.winfo_children()]
    _want = [eqgui.T[k][_lang] for k in _CAUT_KEYS]
    ck(f"LIVE[{_lang}] 주의 사항 = 머리 + 소제목 넷과 줄(확정 문안 순서 그대로)",
       _bt == _want and str(app._caut_hdr.cget("text")) == "▾ " + eqgui.T["caut_head"][_lang], _bt)
    ck(f"LIVE[{_lang}] 회색 마지막 줄 = caut_foot(접이식 밖)",
       str(app._caut_foot.cget("text")) == eqgui.T["caut_foot"][_lang]
       and app._caut_foot.master is not app._caut_body and app._caut_foot.master is not app._caut_box)
    _all = []

    def _w2(w):
        for c in w.winfo_children():
            try:
                _all.append((str(c.cget("text")), c.winfo_rooty()))
            except Exception:
                pass
            _w2(c)
    _w2(app._scroll_host)
    _btn_y = app.b_live_start.winfo_rooty()
    _txt = _want + [eqgui.T["caut_foot"][_lang], eqgui.T["warn_mix"][_lang]]
    ck(f"LIVE[{_lang}] 주의 사항·경고 글은 버튼 줄 아래에만(라이브 제목과 버튼 줄 사이에 설명 없음, 각 한 번)",
       all(y > _btn_y for t, y in _all if t in _txt)
       and all(sum(1 for t, y in _all if t == x) == 1 for x in _txt), [(t[:20], y) for t, y in _all if t in _txt])
    for _state in ("펼침", "접힘", "다시 펼침"):
        if _state != "펼침":
            app._toggle_caut()
        flush()
        _boxes = [app.out, app._hdr_note_box, app._tr_box, app._caut_box, app._caut_foot]
        _ys = [w.winfo_rooty() for w in _boxes]
        _geo = [(w.winfo_rooty(), w.winfo_height(), w.winfo_reqheight(), w.winfo_ismapped()) for w in _boxes]
        print(f"  geo[{_lang} {_state}] out/warn/tr/caut/foot (rooty,h,req,mapped) =", _geo)
        ck(f"LIVE[{_lang} {_state}] 로그 < 경고 < 트랙레코드 < 주의 사항 < 회색 줄, 모두 보임·안 눌림",
           all(w.winfo_ismapped() for w in _boxes)
           and all(w.winfo_height() >= w.winfo_reqheight() for w in _boxes)
           and _ys == sorted(_ys) and len(set(_ys)) == len(_ys)
           and app._caut_body.winfo_ismapped() == (_state != "접힘")
           and str(app._caut_hdr.cget("text")).startswith("▸ " if _state == "접힘" else "▾ "), _geo)
    ck(f"LIVE[{_lang}] 경고 묶음 = warn_mix 확정 문안",
       eqgui.T["warn_mix"][_lang] in [str(w.cget("text")) for w in app._hdr_note_box.winfo_children()])
    ck(f"LIVE[{_lang}] 버튼 줄 아래 한 줄 = 다음 할 일", bool(str(app._live_next_lbl.cget("text")).strip())
       and app._live_next_lbl.winfo_rooty() > _btn_y and app._live_next_lbl.winfo_rooty() < app.out.winfo_rooty())
    # 동의 켠 채 다시 그리면(회원이 안 만졌으면) 접힘, 회원이 만졌으면 그 세션 상태 유지
    app._caut_user_set = False
    app.consent.set(1); app._build(); flush()
    ck(f"LIVE[{_lang}] 동의 켠 채 다시 그리면 접힘", not app._caut_open and not app._caut_body.winfo_ismapped()
       and app._caut_box.winfo_ismapped() and app._caut_foot.winfo_ismapped())
    app._toggle_caut(); app._build(); flush()
    ck(f"LIVE[{_lang}] 회원이 펼친 상태는 다시 그려도 유지", app._caut_open and bool(app._caut_body.winfo_ismapped()))
ROOT.withdraw()

# ═════ 1b 반박 리뷰(2026-10-08) 수리 ═════
def _pick(app, b):
    app.brokerbox.set(eqgui._broker_label(b)); app._on_broker(); flush()


def _acct_texts(app):
    out = []

    def _w(x):
        for c in x.winfo_children():
            try:
                out.append(str(c.cget("text")))
            except Exception:
                pass
            _w(c)
    _w(app._acct_body)
    return out


# P0: 계좌 설정 모양은 계좌 탭 자산이 정한다(패널 Bybit여도 NQ는 선물 모양, 꺼 둔 행은 꺼진 채)
P0 = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": ""}},
             "accounts": [row("150KTC-A", "projectx", on=False)]},
      "GC": {"broker": "projectx", "include": False, "creds": {}, "accounts": [row("", "")]},
      "BTC": {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "", "f3": ""}}, "accounts": [row("", "bybit")]}}
seed(P0, {EMAIL: PXKEY})
app = start()
_pick(app, "bybit")
app._on_asset("GC"); app._on_asset("NQ"); app._autosave_fire(); app._save_current_asset(); flush()
ck("P0 패널 Bybit + 계좌 탭 NQ: 꺼 둔 행은 저장 뒤에도 꺼짐", app._acfg["NQ"]["accounts"][0]["on"] is False,
   app._acfg["NQ"]["accounts"])
ck("P0 패널 Bybit + 계좌 탭 NQ: 선물 모양(사용 계좌·[계좌 추가]·[계좌 목록 불러오기])",
   any(t.startswith("사용 계좌") for t in _acct_texts(app)) and eqgui.T["btn_accts"]["ko"] in _acct_texts(app), _acct_texts(app))
MSG.clear()
app._live_include["NQ"].set(1)
app._master_start(); flush()
ck("P0 꺼 둔 행만 있는 NQ → 라이브 시작 무장 0(켜진 계좌 없음)", not app._sig_accts
   and any("켜진 계좌 없음" in m[-1] for m in MSG if m[0] == "warn"), MSG)
_pick(app, "projectx")
app._on_asset("BTC"); flush()
ck("P0 패널 Topstep + 계좌 탭 BTC: 크립토 모양", any(t.startswith("② 계좌") for t in _acct_texts(app))
   and not any(t.startswith("사용 계좌") for t in _acct_texts(app)), _acct_texts(app))

# P1: 새 설치에서 Lucid / Tradovate를 처음 고른 회원 - 브리지 버튼으로 토큰·.cs, 빈 슬롯이 Lucid 행으로
for _only in ("NQ", "GC"):
    seed({}, {})
    app = start()
    _pick(app, "nt8")
    MSG.clear()
    app._nt8_install_bridge(); flush()
    _ct = re.search(r'Token\s*=\s*"([^"]*)"', open(NT8_CS, encoding="utf-8").read()).group(1) if os.path.exists(NT8_CS) else None
    ck(f"P1[{_only}] 새 설치 패널 Lucid → 브리지 설치·NQ·GC 공용 토큰 = .cs", bool(_ct)
       and app._acfg["NQ"]["creds"]["nt8"]["f1"] == _ct and app._acfg["GC"]["creds"]["nt8"] is app._acfg["NQ"]["creds"]["nt8"],
       (MSG, _ct))
    app._on_asset(_only); flush()
    app._add_acct(); flush()
    _r = app._acfg[_only]["accounts"]
    ck(f"P1[{_only}] [계좌 추가] → 빈 기본 슬롯이 Lucid 행, 행 드롭다운에 Lucid / Tradovate",
       len(_r) == 1 and app._acct_broker(_only, _r[0]) == "nt8"
       and any(eqgui._broker_label("nt8") in w["broker"].cget("values") for w in app._acct_widgets.values() if w.get("broker")),
       _r)
seed({}, {})
app = start()
MSG.clear()
app._nt8_install_bridge(); flush()
ck("P1 패널이 Lucid가 아니고 nt8 연결도 없으면 안내(브로커 연결 설정에서 고르라)",
   any("브로커 연결 설정에서 고른 뒤 다시 누르세요" in m[-1] for m in MSG) and not os.path.exists(NT8_CS), MSG)

# P2-1: 키 칸을 지우고 1.2초 넘게 비운 뒤 Ctrl+V → 새 키만(되채운 값은 전부 선택돼 바꿔 끼운다)
_OLDK, _NEWK = "PX-API-KEY-777-OLD", "PX-API-KEY-999-NEW"
seed(T7, {EMAIL: _OLDK})
app = start()
app._unlocked = True; app.key.config(state="normal")
app.key.focus_force(); flush()
app.key.selection_range(0, "end"); app.key.event_generate("<<Clear>>"); flush()
app._autosave_fire(); flush()
ROOT.clipboard_clear(); ROOT.clipboard_append(_NEWK); flush()
app.key.event_generate("<<Paste>>"); flush()
app._autosave_fire(); flush()
ck("P2-1 지우고 멈춘 뒤 붙여넣기 = 새 키만 저장(옛 키+새 키 아님)", KC.get(EMAIL) == _NEWK, KC.get(EMAIL))
BUILDS.clear()
type_into(app.key, "PX-API-KEY-999-NEX")       # 길이 같은 다른 키
app._autosave_fire(); flush()
ck("P2-1 길이가 같은 다른 키로 바꿔도 연결 다시 확인(지문 = 해시)", any(b[0] == "projectx" for b in BUILDS), BUILDS)

# P2-2: Bridge Port 빈 칸 = 8377 - 다른 자산의 8390으로 채우지 않는다
P22 = {"NQ": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "", "avail": []}},
              "accounts": [row("LDI-1", "nt8")]},
       "GC": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "8390", "avail": []}},
              "accounts": [row("LDI-2", "nt8")]},
       "BTC": BTC0}
seed(P22, {"eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._save_cfg()
ck("P2-2 빈 포트(=8377)가 .cs(8377)와 같은 쪽으로 합침 - 8390으로 채우지 않음",
   str(app._acfg["NQ"]["creds"]["nt8"].get("f3") or "8377") == "8377"
   and str((ydisk()["assets"]["NQ"]["creds"]["nt8"].get("f3")) or "8377") == "8377", app._acfg["NQ"]["creds"]["nt8"])

# P2-3: IBKR만 연결한 회원 - [계좌 추가]가 빈 기본 슬롯을 IBKR 행으로
P23 = {"NQ": {"broker": "projectx", "include": True, "creds": {"ibkr": {"f1": "127.0.0.1", "f3": "7497"}},
              "accounts": [row("", "")]}, "GC": {"broker": "projectx", "include": False, "creds": {}, "accounts": [row("", "")]},
       "BTC": BTC0}
seed(P23, {})
app = start()
ck("P2-5 기동 패널 = 연결된 첫 브로커(IBKR, 빈 Topstep 아님)", app._broker_name == "ibkr", app._broker_name)
app._add_acct(); flush()
ck("P2-3 IBKR 회원 [계좌 추가] → 빈 슬롯이 IBKR 행", app._acct_broker("NQ", app._acfg["NQ"]["accounts"][0]) == "ibkr"
   and len(app._acfg["NQ"]["accounts"]) == 1, app._acfg["NQ"]["accounts"])

# P2-4: 공용 Topstep 자동 확인은 자산마다 행 대조 - NQ 낡은 행 오류가 GC 확인을 막지 않는다
P24 = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": ""}},
              "accounts": [row("OLD-CLOSED", "projectx")]},
       "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("150KTC-A", "projectx")]}, "BTC": BTC0}
seed(P24, {EMAIL: PXKEY})
app = start()
app._test_ok = set()
n0 = len(LOGS)
app._autotest_fp = None
type_into(app.user, EMAIL)
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, PXKEY)
app._autosave_fire(); flush()
ck("P2-4 GC 확인됨·NQ만 오류(자산 이름 그대로)", "GC" in app._test_ok and "NQ" not in app._test_ok
   and "❌ NQ" in logs_since(n0) and "❌ GC" not in logs_since(n0), (app._test_ok, logs_since(n0)[-300:]))

# P2-5: 새 Lucid 회원 - NQ 빈 기본 행(Topstep)보다 GC의 Lucid 행
P25 = {"NQ": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("", "")]},
       "GC": {"broker": "projectx", "include": True, "creds": {"nt8": {"f1": "", "f3": ""}}, "accounts": [row("LDI-1", "nt8")]},
       "BTC": BTC0}
seed(P25, {"eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
ck("P2-5 기동 패널 = 실행 체크 자산의 계좌 ID 있는 행 브로커(Lucid)", app._broker_name == "nt8", app._broker_name)

# P2-6: 라이브 버튼 아래 한 줄 - 여덟 상태 KO/EN
for _lang in ("ko", "en"):
    seed(T13, {EMAIL: PXKEY}, extra={"lang": _lang})
    app = start()
    T = lambda: str(app._live_next_lbl.cget("text"))
    _want = {}
    app.consent.set(0); app._refresh_live_panel()
    _want["E"] = T()
    app.consent.set(1); app._refresh_live_panel()
    app._live_include["NQ"].set(0); app._live_include["GC"].set(0); app._live_include["BTC"].set(0); app._refresh_live_panel()
    _want["F"] = T()
    app._live_include["NQ"].set(1); app._live_include["GC"].set(1); app._refresh_live_panel()
    _want["G"] = T()                               # GC 행은 목록에 없지만 키·계좌 ID는 있다 → 준비 완료로 보임(연결 테스트가 가린다)
    app._acfg["GC"]["accounts"][0]["id"] = ""; app._refresh_live_panel()
    _want["G2"] = T()
    app._acfg["GC"]["accounts"][0]["id"] = "50KTC-B"; app._refresh_live_panel()
    _want["H"] = T()
    app._gate = dict(app._gate, force_dry_run=True); app._refresh_live_panel()
    _want["C"] = T()
    app._gate = dict(app._gate, force_dry_run=False, caps={"use": False, "autoentry": False}); app._refresh_live_panel()
    _want["D"] = T()
    app._gate["caps"] = {"use": True, "autoentry": True}
    app._auto_accts[("NQ", 0)] = [{"broker": "projectx", "f1": EMAIL}]; app._refresh_live_panel()
    _want["B"] = T()
    app._sig_accts[("NQ", 0)] = {"broker": "projectx", "f1": EMAIL}; app._refresh_live_panel()
    _want["A"] = T()
    app._sig_accts.clear(); app._auto_accts.clear()
    _exp = ({"A": "가동 중 - NQ", "B": "마감 청산만 가동 중 - NQ", "C": "서버가 라이브를 잠갔습니다", "D": "Operator 등급부터",
             "E": "동의에 체크", "F": "시작할 자산을 체크", "G2": "준비 안 됨: GC(계좌 미선택)", "H": "준비 완료"}
            if _lang == "ko" else
            {"A": "Running - NQ", "B": "Session-close only - NQ", "C": "Live is locked", "D": "Operator tier",
             "E": "tick the agreement", "F": "check the assets", "G2": "Not ready: GC (no account picked)", "H": "Ready"})
    for _k, _sub in _exp.items():
        ck(f"P2-6[{_lang}] 상태 {_k}: '{_sub}'", _sub in _want[_k], _want[_k])

# P3: 제목은 접었다 펴도 새 이름
seed(T7, {EMAIL: PXKEY})
app = start()
app._toggle_cfg(); app._toggle_cfg()
ck("P3 제목 접기·펴기 두 번 뒤에도 '브로커 연결 설정'", str(app._cfg_hdr.cget("text")).endswith("브로커 연결 설정"), app._cfg_hdr.cget("text"))
ck("P3 IBKR Host 칸 이름에 한글 없음(EN 화면)", not re.search(r"[가-힣]", eqgui._BROKER_SPEC["ibkr"]["f1"]))
# P3: [계좌 목록 불러오기] 결과를 알린다 + IBKR만 쓰는 자산 안내
n0 = len(LOGS)
app.b_acc.invoke(); flush()
ck("P3 [계좌 목록 불러오기] 결과 로그(계좌 n개)", "Topstep 계좌 목록 2개를 불러왔습니다" in logs_since(n0), logs_since(n0)[-200:])
seed(P23, {})
app = start()
MSG.clear()
app.b_acc.invoke(); flush()
ck("P3 IBKR만 쓰는 자산 → [가용 계좌 등록] 안내", any("[가용 계좌 등록]" in m[-1] for m in MSG), MSG)
# P3: IBKR split 줄에 포트도 보인다 + split 문구 버튼 이름
P3s = {"NQ": {"broker": "ibkr", "include": True, "creds": {"ibkr": {"f1": "127.0.0.1", "f3": "7497"}}, "accounts": [row("U1", "ibkr")]},
       "GC": {"broker": "ibkr", "include": True, "creds": {"ibkr": {"f1": "127.0.0.1", "f3": "7496"}}, "accounts": [row("U2", "ibkr")]},
       "BTC": BTC0}
seed(P3s, {})
app = start()
_pick(app, "ibkr")
_pt = [t for k, t in _panel_texts(app)]
ck("P3 IBKR split 줄 = 'NQ: 127.0.0.1 : 7497' / 'GC: 127.0.0.1 : 7496'", "NQ: 127.0.0.1 : 7497" in _pt
   and "GC: 127.0.0.1 : 7496" in _pt, _pt)
ck("P3 split 안내 = 'IBKR'·실제 버튼 이름([NQ 연결 지우기]·[GC 연결 지우기])", any("[NQ 연결 지우기] 또는 [GC 연결 지우기]" in t for t in _pt)
   and "NQ 연결 지우기" in _pt, _pt)
# P3: 라이브 시작 연결 테스트 중에는 패널 연결 정보를 바꾸지 않는다
seed(T7, {EMAIL: PXKEY})
app = start()
app._golive_busy_mark(True)
MSG.clear()
type_into(app.user, "other@mail.com")
app._autosave_fire(); flush()
ck("P3 라이브 시작 테스트 중 패널 편집 거절", app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL
   and any("연결 테스트 중이라" in m[-1] for m in MSG), MSG)
app._golive_busy_mark(False)
# P3: Topstep 사용자명 + 같은 값이 .cs 토큰 - 그 아이디로 API Key가 있으면 지우지 않는다(keys/fixcand)
_UN = "member_username_long"
P3k = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": _UN, "f3": ""}, "nt8": {"f1": "", "f3": ""}},
              "accounts": [row("150KTC-A", "projectx")]}, "GC": {"broker": "projectx", "include": True, "creds": {},
              "accounts": [row("LDI-1", "nt8")]}, "BTC": BTC0}
seed(P3k, {_UN: PXKEY, "eq:GC:nt8:f1": _UN}, cs_token=_UN)
app = start()
app._save_cfg()
ck("P3 API Key가 있는 Topstep 사용자명은 기동 정리가 안 지움(메모리·YAML)", app._acfg["NQ"]["creds"]["projectx"]["f1"] == _UN
   and (ydisk()["assets"]["NQ"]["creds"]["projectx"]["f1"]) == _UN, app._acfg["NQ"]["creds"]["projectx"])

# ═════ 2117fb56 재리뷰(2026-10-08) 수리 ═════
# 새 설치, BTC를 Bitget으로만 - 기본 행이 Bybit로 박히지 않고 Bitget으로 무장(NQ도 무장)
seed({"NQ": T13["NQ"], "GC": {"broker": "projectx", "include": False, "creds": {}, "accounts": [row("", "")]},
      "BTC": {"broker": "bybit", "include": True, "creds": {}, "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": ""}]}},
     {EMAIL: PXKEY})
app = start()
app._on_asset("BTC"); flush()
_pick(app, "bitget")
type_into(app.user, "BITGET-KEY-0000000001")
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BITGET-SECRET-01")
type_into(app.f3, "PASSPH")
app._autosave_fire(); flush()
app._on_asset("NQ"); app._on_asset("BTC"); flush()
for _a in ("NQ", "BTC"):
    app._live_include[_a].set(1)
MSG.clear()
app._master_start(); flush()
ck("RR 새 설치 Bitget만 → BTC 행 Bitget으로 무장·NQ도 무장(Bybit 키 미설정으로 멈추지 않음)",
   (app._sig_accts.get(("BTC", 0)) or {}).get("broker") == "bitget" and ("NQ", 0) in app._sig_accts,
   (app._sig_accts, MSG))
app._sig_accts.clear(); app._auto_accts.clear(); app._live_session = False

# 빈 칸 다시 채움 + 새 값 입력: 저장값 = 새 값만(이메일·API Key·Bitget Passphrase)
for _fld in ("user", "key"):
    seed(T7, {EMAIL: PXKEY, "new@mail.com": "NEWK"})
    app = start()
    app._unlocked = True; app.key.config(state="normal")
    _w = getattr(app, _fld)
    _w.focus_force(); flush()
    _w.selection_range(0, "end"); _w.event_generate("<<Clear>>"); flush()
    app._autosave_fire(); flush()
    _new = "new@mail.com" if _fld == "user" else "PX-KEY-REPLACED-1"
    ROOT.clipboard_clear(); ROOT.clipboard_append(_new); flush()
    _w.event_generate("<<Paste>>"); flush()
    app._autosave_fire(); flush()
    _got = app._acfg["NQ"]["creds"]["projectx"]["f1"] if _fld == "user" else KC.get(EMAIL)
    ck(f"RR 칸 비움 → 자동 저장 → 붙여넣기 → 자동 저장: {_fld} = 새 값만", _got == _new, _got)
seed({"NQ": T13["NQ"], "GC": T13["GC"], "BTC": {"broker": "bitget", "include": True,
      "creds": {"bitget": {"f1": "BITGET-KEY-0000000001", "f3": "OLDPASS"}}, "accounts": [row("", "bitget")]}},
     {EMAIL: PXKEY, "BITGET-KEY-0000000001": "BS"})
app = start()
_pick(app, "bitget")
app.f3.focus_force(); flush()
app.f3.selection_range(0, "end"); app.f3.event_generate("<<Clear>>"); flush()
app._autosave_fire(); flush()
ck("RR Bitget Passphrase 비움 → 저장값 유지", app._acfg["BTC"]["creds"]["bitget"]["f3"] == "OLDPASS")
ROOT.clipboard_clear(); ROOT.clipboard_append("NEWPASS"); flush()
app.f3.event_generate("<<Paste>>"); flush()
app._autosave_fire(); flush()
ck("RR Bitget Passphrase 붙여넣기 → 새 값만", app._acfg["BTC"]["creds"]["bitget"]["f3"] == "NEWPASS",
   app._acfg["BTC"]["creds"]["bitget"]["f3"])

# PIN을 열지 못한 기동에서도 오염 걸러내기는 돈다(토큰이 YAML 평문에 남지 않게)
_blobm = eqgui._enc_pack("2468", {"NQ|nt8|f1": TOKEN})
seed(T1, {EMAIL: PXKEY}, cs_token=TOKEN, extra={"kc_enc": {"blob": _blobm, "fields": ["NQ|nt8|f1"]}})
KC_FAIL[0] = True
PINS[:] = []
app = start()
app._save_cfg()
ck("RR PIN 미해제 기동: GC Topstep 칸 토큰 걸러냄·YAML 평문 0", app._enc_locked is True
   and app._acfg["GC"]["creds"]["projectx"]["f1"] == "" and TOKEN not in yraw(), app._acfg["GC"]["creds"]["projectx"])
KC_FAIL[0] = False

# h1(b) 오류: 라벨이 없으면 괄호 생략
seed(T13, {EMAIL: PXKEY})
app = start()
for _a in ("NQ", "GC"):
    app._live_include[_a].set(1)
MSG.clear()
app._master_start(); flush()
_err = " ".join(m[-1] for m in MSG if m[0] == "error")
ck("RR h1(b) 라벨 없는 행 → \"'LDI-NOT-TOPSTEP'가\"(괄호 없음)", "'LDI-NOT-TOPSTEP'가" in _err, _err)
# [연결 지우기] 확인창: 실행 체크가 꺼진 자산은 세지 않는다
seed({"NQ": T13["NQ"], "GC": {"broker": "projectx", "include": False, "creds": {}, "accounts": [row("50KTC-B", "projectx")]},
      "BTC": BTC0}, {EMAIL: PXKEY})
app = start()
MSG.clear(); ASK[0] = False
app._clear_broker_conn(); flush()
ASK[0] = True
_ask = " ".join(m[2] for m in MSG if m[0] == "ask")
ck("RR [연결 지우기] 확인창: 실행 꺼진 GC는 안 셈('NQ 1개'만)", "NQ 1개" in _ask and "GC" not in _ask, _ask)

# Topstep 아이디 = .cs 토큰 = Bridge Token(그 아이디로 API Key 있음) - 둘 다 정상, 아무것도 안 지우고 split 없음
_UT = "same-user-and-token"
seed({"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": _UT, "f3": ""}, "nt8": {"f1": "", "f3": ""}},
             "accounts": [row("150KTC-A", "projectx"), row("LDI-1", "nt8")]}, "GC": T13["GC"], "BTC": BTC0},
     {_UT: PXKEY, "eq:NQ:nt8:f1": _UT}, cs_token=_UT)
app = start()
ck("RR Topstep 아이디 = Bridge Token = .cs(API Key 있음) → 둘 다 유지·split 없음",
   app._acfg["NQ"]["creds"]["projectx"]["f1"] == _UT and app._acfg["NQ"]["creds"]["nt8"]["f1"] == _UT and not app._creds_split,
   (app._acfg["NQ"]["creds"], app._creds_split))
# T5 판별 가능: GC Bridge Token 칸의 Topstep 아이디 + .cs는 진짜 토큰 → nt8 쪽만 빼고 split 없이 .cs 토큰으로
_UN2 = "short_user"
seed({"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": _UN2, "f3": ""}, "nt8": {"f1": "", "f3": ""}},
             "accounts": [row("150KTC-A", "projectx"), row("LDI-1", "nt8")]},
      "GC": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": ""}}, "accounts": [row("LDI-1", "nt8")]},
      "BTC": BTC0}, {_UN2: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": _UN2}, cs_token=TOKEN)
n0 = len(LOGS)
app = start()
ck("RR T5 판별 가능 → GC Bridge Token = .cs 토큰, nt8 split 없음, 노트 'Topstep 아이디'",
   app._acfg["GC"]["creds"]["nt8"]["f1"] == TOKEN and "nt8" not in app._creds_split
   and "Bridge Token 칸에 Topstep 아이디가 들어 있어" in logs_since(n0), (app._acfg["GC"]["creds"]["nt8"], app._creds_split))
# 쓰기 가드: 이메일은 늘 받는다, .cs가 없을 때 짧은 회원 토큰은 Topstep 칸에서 거절
_T7n = copy.deepcopy(T7); _T7n["NQ"]["creds"]["nt8"] = {"f1": "", "f3": ""}
seed(_T7n, {EMAIL: PXKEY, "eq:NQ:nt8:f1": "short-tok"})
app = start()
MSG.clear()
type_into(app.user, "short-tok")
app._autosave_fire(); flush()
ck("RR 쓰기 가드: .cs 없음 + 짧은 저장 토큰 → Topstep 칸 거절", app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL
   and any("다른 브로커의 키가 들어 있어" in m[-1] for m in MSG), MSG)
app._acfg["NQ"]["creds"]["nt8"]["f1"] = "other@mail.com"
type_into(app.user, "other@mail.com")
app._autosave_fire(); flush()
ck("RR 쓰기 가드: '@' 값은 Bridge Token 칸에 같은 값이 있어도 받는다", app._acfg["NQ"]["creds"]["projectx"]["f1"] == "other@mail.com")
# 핑 의도: GC 기본 빈 행(S4)·BTC 행 꺼둠(S7)은 연결이 아니다
seed({"NQ": RB["NQ"], "GC": RB["GC"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": "0"}},
      "accounts": [row("", "bybit", on=False)]}}, {EMAIL: PXKEY, "eq:BTC:bybit:f1": "BK"})
app = start()
ck("RR 핑 의도: GC 기본 빈 행·BTC 행 꺼둠 → 연결 아님(NQ만)", app._connected_assets() == ["NQ"], app._connected_assets())

# ═════ 9f0572d7 확인 재리뷰(2026-10-08) ═════
def _armed_bk(app):
    return {k: (v[0] if isinstance(v, list) else v).get("broker") for k, v in app._sig_accts.items()}


def _enter_crypto(app, b, key, sec, f3=None):
    _pick(app, b)
    type_into(app.user, key)
    app._unlocked = True; app.key.config(state="normal"); type_into(app.key, sec)
    if f3 is not None and hasattr(app, "f3"):
        type_into(app.f3, f3)
    app._autosave_fire(); flush()


# F: Bitget만 쓰던 새 회원이 Bybit 키를 더 넣어도 BTC 실행 거래소는 Bitget 그대로(행·무장)
seed({}, {})
app = start()
_enter_crypto(app, "bitget", "BGKEY-123456789012", "BGSECRET-12345", "BGPASS")
app._live_include["BTC"].set(1); app._acfg["BTC"]["include"] = True
app._master_start(); flush()
ck("F 첫 라이브 시작 = Bitget 무장", _armed_bk(app).get(("BTC", 0)) == "bitget", _armed_bk(app))
ck("F 라이브 줄에 거래소 이름 'BTC(Bitget)'", "BTC(Bitget)" in str(app._live_next_lbl.cget("text")), app._live_next_lbl.cget("text"))
app._master_stop(); flush()
_enter_crypto(app, "bybit", "BYKEY-123456789012", "BYSECRET-12345")
_r0 = app._acfg["BTC"]["accounts"][0]
ck("F Bybit 키를 더해도 BTC 행 = Bitget", app._acct_broker("BTC", _r0) == "bitget", _r0)
app._on_asset("BTC"); flush(); app._on_asset("NQ"); flush()
app._master_start(); flush()
ck("F 계좌 탭을 열고 다시 시작해도 Bitget 무장", _armed_bk(app).get(("BTC", 0)) == "bitget", _armed_bk(app))
app._master_stop(); flush()

# A2/A3: [계좌 추가]가 회원이 행에서 고른 브로커를 덮지 않는다
_CRA = {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A"]}, "nt8": {"f1": "", "f3": "", "avail": ["LDI-1"]}}


def _setrowbk(app, b):
    app._acct_edit = True; app._build(); flush()
    app._acct_widgets[0]["broker"].set(eqgui._broker_label(b)); app._save_acct_widgets(); app._build(); flush()


seed({"NQ": {"broker": "projectx", "include": True, "creds": copy.deepcopy(_CRA), "accounts": [row("150KTC-A", "projectx")]},
      "GC": {"broker": "projectx", "include": True, "creds": copy.deepcopy(_CRA), "accounts": [row("", "")]}, "BTC": BTC0},
     {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("GC"); flush()
_setrowbk(app, "nt8")
app._add_acct(); flush()
ck("A2 패널 Topstep + GC 행을 Lucid로 고른 뒤 [계좌 추가] → Lucid 그대로", app._acct_broker("GC", app._acfg["GC"]["accounts"][0]) == "nt8",
   app._acfg["GC"]["accounts"])
seed({"NQ": {"broker": "projectx", "include": True, "creds": copy.deepcopy(_CRA), "accounts": [row("", "")]},
      "GC": {"broker": "projectx", "include": True, "creds": copy.deepcopy(_CRA), "accounts": [row("150KTC-A", "projectx")]},
      "BTC": BTC0}, {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN, "eq:BTC:bybit:f1": "BYBITKEY-123456789012"},
     cs_token=TOKEN)
app = start()
_pick(app, "bybit")
app._on_asset("NQ"); flush()
_setrowbk(app, "nt8")
app._add_acct(); flush()
ck("A3 패널 Bybit + NQ 행을 Lucid로 고른 뒤 [계좌 추가] → Lucid 그대로", app._acct_broker("NQ", app._acfg["NQ"]["accounts"][0]) == "nt8",
   app._acfg["NQ"]["accounts"])

# 자동 확인은 실행 체크된 자산의 행만 대조
seed({"NQ": {"broker": "projectx", "include": False, "creds": {"projectx": {"f1": EMAIL, "f3": ""}}, "accounts": [row("OLD1", "projectx")]},
      "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": [row("150KTC-A", "projectx")]}, "BTC": BTC0}, {EMAIL: PXKEY})
app = start()
n0 = len(LOGS)
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, PXKEY + "x")
app._autosave_fire(); flush()
type_into(app.key, PXKEY)
app._autosave_fire(); flush()
ck("RV 자동 확인: 실행 꺼진 NQ의 낡은 행은 대조 안 함(❌ NQ 없음)·GC 확인", "❌ NQ" not in logs_since(n0) and "GC" in app._test_ok,
   logs_since(n0)[-300:])

# 라이브 줄: 시작 버튼이 꺼진 이유 / 하트비트 전
for _lang in ("ko", "en"):
    seed(T13, {EMAIL: PXKEY}, extra={"lang": _lang})
    app = start()
    _exp = {"revoked": ("멤버십 권한이 확인되지 않아", "Membership is not active"),
            "offline": ("서버에 연결되지 않아", "Can't reach the server"),
            "init": ("서버에서 멤버십을 확인하는 중", "Checking your membership")}
    for _st, (_k, _e) in _exp.items():
        app._gate = {"ok": False, "reason": "x", _st: True} if _st != "init" else {"ok": False, "reason": "no token"}
        app._apply_gating(); app._refresh_live_panel()
        _txt = str(app._live_next_lbl.cget("text"))
        ck(f"RV[{_lang}] 시작 버튼 꺼짐({_st}) → 줄이 이유를 말함·'준비 완료' 아님",
           (_k if _lang == "ko" else _e) in _txt and str(app.b_live_start.cget("state")) == "disabled", _txt)

# 계좌 설정 '다음:': 재시작 뒤 설정이 다 된 자산 = '연결 확인 전'(키 입력하라 아님)
seed(T7, {EMAIL: PXKEY})
app = start()
app._apply_gating(); flush()                       # 하트비트 권한이 들어온 뒤(기동 직후엔 '멤버십 확인 중' - 라이브 줄과 같음)
ck("RV 재시작 뒤 설정 완료 자산 → '연결 확인 전'", "연결 확인 전" in str(app._next_lbl.cget("text")), app._next_lbl.cget("text"))
app._sig_accts[("NQ", 0)] = {"broker": "projectx", "f1": EMAIL}; app._test_ok = set(); app._refresh_next_action()
ck("RV 무장 중이면 _test_ok와 상관없이 '가동 중'", "가동 중" in str(app._next_lbl.cget("text")), app._next_lbl.cget("text"))
app._sig_accts.clear()
# 라이브 시작 성공 = 확인됨
for _a in ("NQ",):
    app._live_include[_a].set(1)
app._live_include["GC"].set(0); app._live_include["BTC"].set(0)
app._test_ok = set()
app._master_start(); flush()
ck("RV 라이브 시작으로 무장한 자산은 확인됨(_test_ok)", "NQ" in app._test_ok and bool(app._sig_accts), (app._test_ok, MSG[-2:]))
app._master_stop(); flush()
# 동의 체크박스를 누르면 라이브 줄이 바로 바뀐다
_cb = None


def _findcb(w):
    global _cb
    for c in w.winfo_children():
        try:
            if c.winfo_class() == "TCheckbutton" and str(c.cget("text")) == eqgui.T["consent"]["ko"]:
                _cb = c
        except Exception:
            pass
        _findcb(c)


_findcb(app._scroll_host)
_cb.invoke(); flush()
ck("RV 동의 해제 클릭 → 라이브 줄 '동의에 체크'", "동의에 체크" in str(app._live_next_lbl.cget("text")), app._live_next_lbl.cget("text"))
_cb.invoke(); flush()
# 라이브 시작 시험 중 [연결 지우기] 거절
app._golive_busy_mark(True)
MSG.clear()
ck("RV 라이브 시작 시험 중 [연결 지우기] 거절", app._clear_broker_conn() is False and any("연결 테스트 중이라" in m[-1] for m in MSG))
app._golive_busy_mark(False)

# 빈 칸: 그 칸에 커서가 있는 동안 자동 저장은 다시 채우지 않는다(입력 중) + 실제 키 입력으로 새 값
seed(T7, {EMAIL: PXKEY})
app = start()
_FOC = [app.user]
ROOT.focus_get = lambda: _FOC[0]                 # 이 셸에서는 창이 앞에 없어 실제 포커스를 못 받는다 - 포커스 판정만 흉내
app.user.focus_force(); flush()
app.user.selection_range(0, "end"); app.user.event_generate("<<Clear>>"); flush()
app._autosave_fire(); flush()
ck("RV 커서가 칸에 있는 자동 저장 → 다시 채우지 않음(빈 칸 그대로)·저장값 유지", app.user.get() == ""
   and app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL, app.user.get())
for _ch in "z@x.io":
    ROOT.tk.call("ttk::entry::Insert", str(app.user), _ch); flush()   # 키 입력 바인딩 그대로(선택 영역이 있으면 지우고 넣음)
app._autosave_fire(); flush()
ck("RV 실제 키 입력(ttk::entry::Insert 바인딩) 뒤 저장값 = 친 값만", app._acfg["NQ"]["creds"]["projectx"]["f1"] == "z@x.io",
   app._acfg["NQ"]["creds"]["projectx"]["f1"])
app.user.focus_force(); flush()
app.user.selection_range(0, "end"); app.user.event_generate("<<Clear>>"); flush()
_FOC[0] = None
app._save_current_asset(); flush()                # 칸을 떠난 명시 저장 → 다시 채우고 전부 선택
_FOC[0] = app.user
for _ch in "q@y.io":
    ROOT.tk.call("ttk::entry::Insert", str(app.user), _ch); flush()   # 키 입력 바인딩 그대로(선택 영역이 있으면 지우고 넣음)
app._autosave_fire(); flush()
ck("RV 다시 채운(전부 선택된) 칸에 실제 키 입력 → 새 값만(이어 붙지 않음)", app._acfg["NQ"]["creds"]["projectx"]["f1"] == "q@y.io",
   app._acfg["NQ"]["creds"]["projectx"]["f1"])
del ROOT.focus_get

# 재확인 지문에 f3: 포트만 바꿔도 다시 확인
seed(T6, {"eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._autosave_fire(); flush()
BUILDS.clear()
type_into(app.f3, "8378")
app._autosave_fire(); flush()
ck("RV 포트(f3)만 바꿔도 연결 다시 확인", any(b[0] == "nt8" for b in BUILDS), BUILDS)

# IBKR 빈 포트 = 7497로 비교(포트 한쪽 빈 IBKR은 합침)
_ib = {"NQ": {"broker": "ibkr", "include": True, "creds": {"ibkr": {"f1": "127.0.0.1", "f3": ""}}, "accounts": [row("U1", "ibkr")]},
       "GC": {"broker": "ibkr", "include": True, "creds": {"ibkr": {"f1": "127.0.0.1", "f3": "7497"}}, "accounts": [row("U2", "ibkr")]},
       "BTC": BTC0}
seed(_ib, {})
app = start()
ck("RV IBKR 빈 포트(=7497) + 7497 → 합침(split 아님)", "ibkr" not in app._creds_split
   and app._acfg["NQ"]["creds"]["ibkr"] is app._acfg["GC"]["creds"]["ibkr"], app._creds_split)
_ib2 = copy.deepcopy(_ib); _ib2["GC"]["creds"]["ibkr"]["f3"] = "7496"
seed(_ib2, {})
app = start()
ck("RV IBKR 빈 포트(=7497) + 7496 → 다른 연결이라 split(NQ를 7496으로 옮기지 않음)", "ibkr" in app._creds_split
   and str(app._acfg["NQ"]["creds"]["ibkr"]["f3"] or "7497") == "7497", (app._creds_split, app._acfg["NQ"]["creds"]["ibkr"]))
# .cs 토큰으로 고른 승자는 .cs 포트
_cp = {"NQ": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "8390"}}, "accounts": [row("L1", "nt8")]},
       "GC": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": ""}}, "accounts": [row("L2", "nt8")]}, "BTC": BTC0}
seed(_cp, {"eq:NQ:nt8:f1": "TOKA-1111111111", "eq:GC:nt8:f1": "TOKB-2222222222"}, cs_token="TOKA-1111111111")
app = start()
ck("RV .cs 승자 포트 = .cs 포트(8377, 진 쪽·옛 8390 아님)", app._acfg["NQ"]["creds"]["nt8"]["f3"] == "8377"
   and not app._creds_split, app._acfg["NQ"]["creds"]["nt8"])

# 비밀 집합·쓰기 가드: 그 이름으로 Topstep API Key가 있는 값은 비밀로 보지 않는다
_V = "abcdefghijklmnopqrstuvwx"                    # 24자 앱 생성 모양
_ac = {"NQ": {"broker": "ibkr", "creds": {"ibkr": {"f1": "127.0.0.1", "f3": _V}, "nt8": {"f1": _V, "f3": ""}}, "accounts": []},
       "GC": {"broker": "ibkr", "creds": {}, "accounts": []}, "BTC": {"broker": "bybit", "creds": {}, "accounts": []}}
KC[_V] = PXKEY
eqgui._unify_creds(_ac, bridge=("", ""))
ck("RV 기동 정리: API Key가 있는 값은 비밀 집합에서 뺌(IBKR 칸 안 지움)", _ac["NQ"]["creds"]["ibkr"]["f3"] == _V)
seed({"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": ""}, "ibkr": {"f1": "", "f3": ""},
                                                            "nt8": {"f1": "", "f3": ""}}, "accounts": [row("150KTC-A", "projectx")]},
      "GC": T7["GC"], "BTC": BTC0}, {EMAIL: PXKEY, "eq:NQ:nt8:f1": _V, _V: PXKEY})
app = start()
_pick(app, "ibkr")
type_into(app.user, _V)
app._autosave_fire(); flush()
ck("RV 쓰기 가드: API Key가 있는 저장 nt8 값은 IBKR Host 칸에서 거절하지 않음", app._acfg["NQ"]["creds"]["ibkr"]["f1"] == _V,
   app._acfg["NQ"]["creds"]["ibkr"])
_pick(app, "projectx")
app._bridge_inst = (_V, "")
type_into(app.user, _V)
app._autosave_fire(); flush()
ck("RV 쓰기 가드: API Key가 있는 Topstep 아이디는 .cs 토큰과 같아도 받음", app._acfg["NQ"]["creds"]["projectx"]["f1"] == _V,
   app._acfg["NQ"]["creds"]["projectx"])

# 크립토 키 미설정 사전점검 안내 + 기본 라벨 = 실제 거래소
seed({"NQ": T13["NQ"], "GC": T13["GC"], "BTC": {"broker": "bybit", "include": True, "creds": {"bitget": {"f1": "BG-ONLY-KEY-000001", "f3": "P"}},
      "accounts": [{"id": "", "one_r": 300, "on": True, "label": "Bybit (USDT perp)", "broker": ""}]}}, {"BG-ONLY-KEY-000001": "S"})
app = start()
app._on_asset("BTC"); flush()
ck("RV 기본 라벨 = 실제 실행 거래소(Bitget)", app._acct_widgets[0]["label"].get() == eqgui._broker_label("bitget"),
   app._acct_widgets[0]["label"].get())
seed({"NQ": T13["NQ"], "GC": T13["GC"], "BTC": {"broker": "bybit", "include": True, "creds": {},
      "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": "bybit"}]}}, {EMAIL: PXKEY})
app = start()
for _a in ("NQ", "GC", "BTC"):
    app._live_include[_a].set(1 if _a == "BTC" else 0)
MSG.clear()
app._master_start(); flush()
ck("RV 크립토 '키 미설정' 사전점검(행 하나) = 키를 넣거나 BTC 실행 체크를 끄라고 안내(2026-10-08b P2-2)",
   any("키 미설정 - Bybit 키를 브로커 연결 설정에 넣거나 라이브 패널에서 BTC 체크를 끄세요" in m[-1] for m in MSG if m[0] == "warn"), MSG)

# ═════ 최종 반박 리뷰(2026-10-08, 8bda56ef 대상) ═════
# P0: 연결 테스트가 끝나고 무장(done)하기 전 사이에 [계좌 추가] → 계좌 ID 없는 선물 행이 마감 청산 잡을 받으면
# 그 잡이 로그인의 계좌 전부를 닫는다. (1) done은 테스트 직전 지문이 같은 행만 무장 (2) _master_arm은 빈 계좌 선물 행 무장 안 함
# (3) 테스트 중에는 [계좌 추가]·[계좌 수정]·행 브로커·계좌 고르기·삭제·[설정 가져오기]를 거절.
P0S = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A", "50KTC-B"]}},
              "accounts": [row("150KTC-A", "projectx")]},
       "GC": {"broker": "projectx", "include": False, "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A", "50KTC-B"]}},
              "accounts": [row("50KTC-B", "projectx")]},
       "BTC": BTC0}
HELD = []


def _hold_done(pat):
    def _a(ms, func=None, *args):
        if func is not None and pat in getattr(func, "__qualname__", ""):
            HELD.append(func)
            return "held"
        return _after(ms, func, *args)
    ROOT.after = _a


def _empty_fut_jobs(app):
    return [(k, j["broker"]) for k, v in app._auto_accts.items() for j in v
            if eqgui._BROKER_SPEC.get(j["broker"], {}).get("acct") and not j["acct"]]


for _mode in ("start", "livearm"):
    _asset = "NQ" if _mode == "start" else "GC"
    for _how in ("button", "direct", "changed"):
        seed(P0S, {EMAIL: PXKEY})
        app = start()
        HELD.clear()
        if _mode == "start":
            for _a in eqgui._ASSETS:
                app._live_include[_a].set(1 if _a == "NQ" else 0)
            _hold_done("_master_start.<locals>.w.<locals>.done")
            app._master_start()
        else:
            for _a in eqgui._ASSETS:
                app._live_include[_a].set(1 if _a == "NQ" else 0)
            app._master_start(); flush()
            _hold_done("_live_arm_assets.<locals>.w.<locals>.done")
            app._live_include["GC"].set(1)
            app._on_asset_toggle()
        flush()
        ROOT.after = _after
        ck(f"P0[{_mode}] 연결 테스트 끝·무장 전 done 붙잡음", len(HELD) == 1, HELD)
        app._on_asset(_asset); flush()
        _n0 = len(app._acfg[_asset]["accounts"])
        MSG.clear()
        if _how == "button":
            app._add_acct(); flush()
            ck(f"P0[{_mode}] 테스트 중 [계좌 추가] 거절(행 수 그대로·경고)",
               len(app._acfg[_asset]["accounts"]) == _n0 and any("연결 테스트 중이라 계좌 설정을" in m[-1] for m in MSG),
               (app._acfg[_asset]["accounts"], MSG))
        elif _how == "direct":            # 거절을 뚫고 행이 생긴 경우(다른 경로) - 지문 대조가 막는다
            app._acfg[_asset]["accounts"].append(row("", "projectx"))
        else:                             # 테스트한 행의 계좌 ID가 그 사이에 바뀜
            app._acfg[_asset]["accounts"][0]["id"] = "50KTC-B" if _asset == "NQ" else "150KTC-A"
        n0 = len(LOGS)
        for _d in HELD:
            _d()
        flush()
        ck(f"P0[{_mode}/{_how}] 무장 뒤 계좌 ID 없는 선물 마감 청산 잡 0", _empty_fut_jobs(app) == [], app._auto_accts)
        _keys = sorted(k for k in app._auto_accts if k[0] == _asset)
        if _how == "changed":
            ck(f"P0[{_mode}/changed] 테스트 뒤 계좌가 바뀐 행은 무장 안 함 + 이유 로그", _keys == []
               and "연결 테스트가 도는 사이에 생기거나 바뀐 계좌라" in logs_since(n0), (_keys, logs_since(n0)[-300:]))
        else:
            ck(f"P0[{_mode}/{_how}] 테스트한 행(0)만 무장", _keys == [(_asset, 0)], _keys)
        if _how == "direct":
            ck(f"P0[{_mode}/direct] 새 행 건너뜀 이유 로그", "연결 테스트가 도는 사이에 생기거나 바뀐 계좌라" in logs_since(n0),
               logs_since(n0)[-300:])
        app._master_stop(); flush()

# P0 (d): 겹친 테스트(라이브 시작 중 가동 중 체크) - 먼저 끝난 쪽이 잠금을 풀지 않는다, 시간 초과는 그 표만 푼다
_t1 = app._golive_busy_mark(True); _t2 = app._golive_busy_mark(True)
app._golive_busy_mark(False, _t1)
ck("P0 겹친 테스트: 하나 끝나도 다른 테스트가 돌면 잠금 유지", app._golive_busy())
app._golive_busy_mark(False, _t2)
ck("P0 겹친 테스트: 둘 다 끝나면 풀림", not app._golive_busy())
_t3 = app._golive_busy_mark(True); app._golive_tok[_t3] = (0.0, None)
ck("P0 120초 넘은 표는 풀림(스레드가 안 돌아와도)", not app._golive_busy() and not app._golive_tok)
# P0: 테스트 뒤 키(f2)만 바뀐 행도 무장하지 않음(f1 같음)
seed(P0S, {EMAIL: PXKEY})
app = start()
HELD.clear()
for _a in eqgui._ASSETS:
    app._live_include[_a].set(1 if _a == "NQ" else 0)
_hold_done("_master_start.<locals>.w.<locals>.done")
app._master_start(); flush()
ROOT.after = _after
KC[EMAIL] = PXKEY + "-ROTATED"
n0 = len(LOGS)
for _d in HELD:
    _d()
flush()
ck("P0 테스트 뒤 키만 바뀐 행은 무장 안 함(시험 안 한 키)", not app._auto_accts and not app._sig_accts
   and "연결 테스트가 도는 사이에 생기거나 바뀐 계좌라" in logs_since(n0), (app._auto_accts, logs_since(n0)[-200:]))
app._master_stop(); flush()
# BTC(크립토, 계좌 ID 없음이 정상)는 지문 대조·빈 계좌 규칙에 걸리지 않고 무장·마감 청산 잡 그대로
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""}},
                     "accounts": [row("", "bybit")]}}, {EMAIL: PXKEY, "BYKEY-123456789012": "BYSEC"})
app = start()
for _a in eqgui._ASSETS:
    app._live_include[_a].set(1 if _a in ("NQ", "BTC") else 0)
app._master_start(); flush()
ck("P0 크립토 행(계좌 ID 없음)은 그대로 무장·마감 청산 잡", ("BTC", 0) in app._sig_accts and ("BTC", 0) in app._auto_accts
   and all(j["broker"] == "bybit" and j["acct"] == "" for j in app._auto_accts[("BTC", 0)]), (app._sig_accts.keys(), app._auto_accts))
app._master_stop(); flush()
# P0(3): 테스트 중 다른 편집 경로 거절
seed(P0S, {EMAIL: PXKEY})
app = start()
app._on_asset("NQ"); flush()
app._golive_busy_mark(True)
MSG.clear()
app._toggle_acct_edit(); flush()
ck("P0 테스트 중 [계좌 수정] 열기 거절", not getattr(app, "_acct_edit", False) and any("계좌 설정을" in m[-1] for m in MSG), MSG)
app._golive_busy_mark(False)
app._toggle_acct_edit(); flush()                     # 열어 둔 뒤
app._golive_busy_mark(True)
_rows0 = copy.deepcopy(app._acfg["NQ"]["accounts"])
MSG.clear()
app._del_acct("NQ", 0); flush()
ck("P0 테스트 중 행 삭제 거절", app._acfg["NQ"]["accounts"] == _rows0 and MSG, (app._acfg["NQ"]["accounts"], MSG))
_idcb = app._acct_widgets[0].get("acct_id")
_idcb.set("50KTC-B"); _idcb.event_generate("<<ComboboxSelected>>"); flush()
ck("P0 테스트 중 행 계좌 고르기 거절(값 되돌림·저장 안 함)", app._acfg["NQ"]["accounts"][0]["id"] == "150KTC-A"
   and "50KTC-B" not in _idcb.get(), (app._acfg["NQ"]["accounts"], _idcb.get()))
MSG.clear()
_oaof = eqgui.filedialog.askopenfilename if hasattr(eqgui, "filedialog") else None
import tkinter.filedialog as _tfd  # noqa: E402
_ASKED = []
_tfd.askopenfilename = lambda *a, **k: (_ASKED.append(1) or "")
app._import_settings(); flush()
ck("P0 테스트 중 [설정 가져오기] 거절(파일 창 안 뜸)", not _ASKED and any("계좌 설정을" in m[-1] for m in MSG), (MSG, _ASKED))
app._golive_busy_mark(False)
# 행 브로커 콤보(브로커 둘 이상인 자산) - NQ에 Lucid 연결을 더해 콤보가 보이게
seed({"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A"]},
                                                              "nt8": {"f1": "", "f3": "", "avail": ["LDI-1"]}},
             "accounts": [row("", "projectx")]}, "GC": P0S["GC"], "BTC": BTC0},
     {EMAIL: PXKEY, "eq:NQ:nt8:f1": TOKEN, "eq:GC:nt8:f1": TOKEN}, cs_token=TOKEN)
app = start()
app._on_asset("NQ"); flush()
app._acct_edit = True; app._build(); flush()
_bkcb = app._acct_widgets[0]["broker"]
app._golive_busy_mark(True)
MSG.clear()
_bkcb.set(eqgui._broker_label("nt8")); _bkcb.event_generate("<<ComboboxSelected>>"); flush()
ck("P0 테스트 중 행 브로커 바꾸기 거절(값 되돌림·저장 안 함)",
   app._acct_broker("NQ", app._acfg["NQ"]["accounts"][0]) == "projectx" and _bkcb.get() == eqgui._broker_label("projectx") and MSG,
   (app._acfg["NQ"]["accounts"], _bkcb.get(), MSG))
app._golive_busy_mark(False)

# P1: BTC 탭을 Bitget 키 전에 그려 두면(콤보 표시 = Bybit) 박아 둔 Bitget이 다음 _collect_acct_widgets에서 Bybit로 덮였다
P1S = {"NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A"]}},
              "accounts": [row("150KTC-A", "projectx")]}}


def _btc_rows(app):
    return [app._acct_broker("BTC", r) for r in app._acfg["BTC"]["accounts"]]


def _autosave(app):
    app._autotest_fp = None
    app._autosave_fire(); flush()


def _golive(app, assets):
    for _a in eqgui._ASSETS:
        app._live_include[_a].set(1 if _a in assets else 0)
    m0 = len(MSG)
    app._master_start(); flush()
    return [m for m in MSG[m0:] if m[0] in ("warn", "error")]


# b3: 칸마다 따로 붙여넣기(사이마다 자동 저장), 계좌 탭 = BTC
seed(P1S, {EMAIL: PXKEY})
app = start()
app._on_asset("BTC"); flush()
_pick(app, "bitget")
app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, "BGKEY-123456789012"); _autosave(app)
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BGSECRET-12345"); _autosave(app)
type_into(app.f3, "PASS"); _autosave(app)
ck("P1 b3 칸마다 붙여넣기 뒤 BTC 행 = Bitget", _btc_rows(app) == ["bitget"], app._acfg["BTC"]["accounts"])
_w = _golive(app, ["NQ", "BTC"])
ck("P1 b3 [라이브 시작 NQ+BTC] 경고 없음·BTC Bitget 무장", not _w and _armed_bk(app).get(("BTC", 0)) == "bitget",
   (_w, _armed_bk(app), app._acfg["BTC"]["accounts"]))
ck("P1 b3 BTC 행 라벨이 낡은 거래소 이름(Bybit)이 아님", all(r.get("label") != eqgui._broker_label("bybit")
                                                      for r in app._acfg["BTC"]["accounts"]), app._acfg["BTC"]["accounts"])
ck("P1 b3 디스크 BTC 행 = bitget 또는 빈 칸(Bitget으로 해석)",
   all((r.get("broker") or "bitget") == "bitget" for r in ydisk()["assets"]["BTC"]["accounts"]), ydisk()["assets"]["BTC"]["accounts"])
app._master_stop(); flush()
# b2: Passphrase를 틀렸다가 고침(두 번째 자동 저장)
for _step2 in ("pass", "keys"):
    seed(P1S, {EMAIL: PXKEY})
    app = start()
    app._on_asset("BTC"); flush()
    _pick(app, "bitget")
    app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, "BGKEY-123456789012")
    app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BGSECRET-12345")
    type_into(app.f3, "WRONGPASS"); _autosave(app)
    if _step2 == "pass":
        type_into(app.f3, "PASS")
    else:
        type_into(app.user, "BGKEY-223456789012"); type_into(app.key, "BGSECRET-22345")
    _autosave(app)
    _w = _golive(app, ["NQ", "BTC"])
    ck(f"P1 b2({_step2}) 고친 뒤 [라이브 시작] 경고 없음·Bitget 무장·콤보 Bitget",
       not _w and _armed_bk(app).get(("BTC", 0)) == "bitget"
       and app._acct_widgets[0]["broker"].get() == eqgui._broker_label("bitget"),
       (_w, _armed_bk(app), app._acfg["BTC"]["accounts"], app._acct_widgets[0]["broker"].get()))
    ck(f"P1 b2({_step2}) 행 라벨 = Bitget 이름", app._acfg["BTC"]["accounts"][0].get("label") == eqgui._broker_label("bitget"),
       app._acfg["BTC"]["accounts"])
    app._master_stop(); flush()
# b4: NQ 가동 중 BTC 탭에서 Bitget 키 → BTC 체크(가동 중 무장) → 탭을 NQ로 옮겨도 행은 Bitget
seed(P1S, {EMAIL: PXKEY})
app = start()
_golive(app, ["NQ"])
app._on_asset("BTC"); flush()
_pick(app, "bitget")
app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, "BGKEY-123456789012")
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BGSECRET-12345")
type_into(app.f3, "PASS"); _autosave(app)
app._live_include["BTC"].set(1); app._on_asset_toggle(); flush()
ck("P1 b4 가동 중 BTC 체크 → Bitget 무장", _armed_bk(app).get(("BTC", 0)) == "bitget", (_armed_bk(app), MSG[-2:]))
app._on_asset("NQ"); flush()
ck("P1 b4 계좌 탭을 옮긴 뒤에도 BTC 행 = Bitget(메모리·디스크)", _btc_rows(app) == ["bitget"]
   and all((r.get("broker") or "bitget") == "bitget" for r in ydisk()["assets"]["BTC"]["accounts"]),
   (app._acfg["BTC"]["accounts"], ydisk()["assets"]["BTC"]["accounts"]))
ck("P1 b4 신호 때 판정: 무장 사본과 행이 일치(진입함)", app._live_acct_cfg(("BTC", 0), app._sig_accts[("BTC", 0)])[0] is not None,
   app._live_acct_cfg(("BTC", 0), app._sig_accts[("BTC", 0)]))
app._master_stop(); flush()
# 둘째 방어(위젯 동기가 빠진 경로): 그린 뒤 행 브로커가 바뀌었는데 콤보는 옛 값 - 수집은 회원이 바꾼 값만 쓴다
seed(P1S, {EMAIL: PXKEY})
app = start()
app._on_asset("BTC"); flush()
_shown0 = app._acct_widgets[0]["broker"].get()
app._acfg["BTC"]["creds"].setdefault("bitget", {})["f1"] = "BGKEY-123456789012"
app._acfg["BTC"]["accounts"][0]["broker"] = "bitget"          # 다른 경로가 박음(콤보는 그대로)
app._collect_acct_widgets()
ck("P1 수집: 콤보 표시가 낡은 값(Bybit)이어도 박힌 Bitget을 덮지 않음", _shown0 == eqgui._broker_label("bybit")
   and app._acfg["BTC"]["accounts"][0]["broker"] == "bitget", (_shown0, app._acfg["BTC"]["accounts"]))
seed(P0S, {EMAIL: PXKEY})
app = start()
app._on_asset("NQ"); flush()
app._acfg["NQ"]["accounts"][0]["broker"] = "nt8"              # 그린 뒤 행 브로커가 바뀜(콤보 = Topstep 그대로)
app._acct_widgets[0]["broker"] = ttk_cb = eqgui.ttk.Combobox(ROOT, values=[eqgui._broker_label("projectx")])
ttk_cb.set(eqgui._broker_label("projectx"))
app._acct_widgets[0]["broker_shown"] = "projectx"
app._collect_acct_widgets()
ck("P1 수집: 낡은 콤보 표시로 계좌 ID를 지우거나 브로커를 덮지 않음",
   app._acfg["NQ"]["accounts"][0]["id"] == "150KTC-A" and app._acfg["NQ"]["accounts"][0]["broker"] == "nt8",
   app._acfg["NQ"]["accounts"])
# 콤보를 회원이 실제로 바꾸면 그 값이 행 브로커(종전 동작 유지)
seed({**P1S, "BTC": {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""},
                                                                   "bitget": {"f1": "BGKEY-123456789012", "f3": "P"}},
                     "accounts": [row("", "bybit")]}},
     {EMAIL: PXKEY, "BYKEY-123456789012": "S1", "BGKEY-123456789012": "S2"})
app = start()
app._on_asset("BTC"); flush()
app._acct_edit = True; app._build(); flush()
app._acct_widgets[0]["broker"].set(eqgui._broker_label("bitget")); app._save_acct_widgets(); flush()
ck("P1 회원이 행 콤보를 Bitget으로 바꾸면 행 = Bitget", app._acfg["BTC"]["accounts"][0].get("broker") == "bitget",
   app._acfg["BTC"]["accounts"])

# P2-1: 자동 저장 뒤 칸을 마우스로 누르고(선택 풀림) 붙여넣어도 새 값만 - f1(이메일)·f2(API Key)·f3(Bitget Passphrase)
eqgui._bind_clipboard(ROOT)


def _click_paste(app, w, new, x_mode):
    w.focus_force(); flush()
    w.selection_range(0, "end"); w.event_generate("<<Clear>>"); flush()
    _FOC[0] = None                                   # 1.2초 안에 앱을 떠남(브라우저로) - 자동 저장 때 포커스 없음
    app._autosave_fire(); flush()
    _mid = (w.get(), str(app._kept_lbls[{"user": "f1", "key": "f2", "f3": "f3"}[x_mode[0]]].cget("text")))
    ROOT.clipboard_clear(); ROOT.clipboard_append(new); flush()
    ROOT.deiconify(); ROOT.geometry("1000x900"); ROOT.update(); flush()
    _wp = w.winfo_width()
    _x = {"start": 2, "mid": max(2, _wp // 8), "end": max(2, _wp - 3)}[x_mode[1]]
    w.event_generate("<Button-1>", x=_x, y=5); w.event_generate("<ButtonRelease-1>", x=_x, y=5); flush()
    if x_mode[1] == "end":
        w.icursor("end")
    w.event_generate("<<Paste>>"); flush()
    _FOC[0] = w
    app._autosave_fire(); flush()
    ROOT.withdraw(); flush()
    return _mid


for _pos in ("start", "mid", "end"):
    seed(T7, {EMAIL: PXKEY, "new@mail.com": "NEWK"})
    app = start()
    app._unlocked = True; app.key.config(state="normal")
    _mid = _click_paste(app, app.key, "PX-API-KEY-999-NEW", ("key", _pos))
    ck(f"P2-1 키(f2) 비움 → 자동 저장 → 칸 누름({_pos}) → 붙여넣기 = 새 키만", KC.get(EMAIL) == "PX-API-KEY-999-NEW"
       and _mid == ("", "저장된 키 유지 중"), (KC.get(EMAIL), _mid))
    seed(T7, {EMAIL: PXKEY, "new@mail.com": "NEWK"})
    app = start()
    _mid = _click_paste(app, app.user, "new@mail.com", ("user", _pos))
    ck(f"P2-1 이메일(f1) 비움 → 자동 저장 → 칸 누름({_pos}) → 붙여넣기 = 새 이메일만",
       app._acfg["NQ"]["creds"]["projectx"]["f1"] == "new@mail.com" and _mid[0] == "", (app._acfg["NQ"]["creds"]["projectx"], _mid))
    seed({"NQ": T13["NQ"], "GC": T13["GC"], "BTC": {"broker": "bitget", "include": True,
          "creds": {"bitget": {"f1": "BITGET-KEY-0000000001", "f3": "OLDPASS"}}, "accounts": [row("", "bitget")]}},
         {EMAIL: PXKEY, "BITGET-KEY-0000000001": "BS"})
    app = start()
    _pick(app, "bitget")
    _mid = _click_paste(app, app.f3, "NEWPASS", ("f3", _pos))
    ck(f"P2-1 Passphrase(f3) 비움 → 자동 저장 → 칸 누름({_pos}) → 붙여넣기 = 새 값만",
       app._acfg["BTC"]["creds"]["bitget"]["f3"] == "NEWPASS" and _mid[0] == "", (app._acfg["BTC"]["creds"]["bitget"], _mid))
ck("P2-1 새 값이 들어가면 '저장된 키 유지 중' 지움", str(app._kept_lbls["f3"].cget("text")) == "", app._kept_lbls["f3"].cget("text"))
for _lg, _exp in (("en", "Saved key kept"), ("ko", "저장된 키 유지 중")):
    seed(T7, {EMAIL: PXKEY}, extra={"lang": _lg})
    app = start()
    app._unlocked = True; app.key.config(state="normal")
    type_into(app.user, ""); type_into(app.key, ""); _FOC[0] = None
    app._autosave_fire(); flush()
    ck(f"P2-1 {_lg} 키 칸 옆 '{_exp}'", str(app._kept_lbls["f2"].cget("text")) == _exp, app._kept_lbls["f2"].cget("text"))
    ck(f"P3(d) {_lg} 이메일(아이디) 칸 옆에는 안 붙임(저장값은 유지)", str(app._kept_lbls["f1"].cget("text")) == ""
       and app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL, (app._kept_lbls["f1"].cget("text"), app._acfg["NQ"]["creds"]["projectx"]))

# P2-5: PIN을 취소한 세션은 보이는 키만으로 BTC 빈 행을 박지 않는다(다음 PIN 세션 무장 거래소가 그대로)
_BYK, _BYS, _BGK, _BGS = "BYBIT-APIKEY-000000001", "BYBIT-SECRET-1", "BITGET-APIKEY-0000002", "BITGET-SECRET-2"


def _p25_build(asset_bk, kc_bk, enc_bk):
    seed({}, {})
    os.remove(eqgui.CFG_PATH)
    K = {"bybit": (_BYK, _BYS, "0"), "bitget": (_BGK, _BGS, "BGPASS")}
    a = {"NQ": {"broker": "projectx", "include": False, "creds": {"projectx": {"f1": "", "f3": ""}}, "accounts": [row("", "")]},
         "GC": {"broker": "projectx", "include": False, "creds": {"projectx": {"f1": "", "f3": ""}}, "accounts": [row("", "")]},
         "BTC": {"broker": asset_bk, "include": True, "creds": {}, "accounts": [row("", "", label="")]}}
    a["BTC"]["creds"][kc_bk] = {"f1": K[kc_bk][0], "f3": K[kc_bk][2], "avail": []}
    KC[K[kc_bk][0]] = K[kc_bk][1]
    eqgui._save_full("ko", "MEMBERTOKEN", copy.deepcopy(a), {"handle": "", "public": False})
    KC_FAIL[0] = True                              # 둘째 키는 키체인이 안 되는 동안 저장 → PIN 블롭(kc_enc)
    eqgui._ENC_PIN = "1234"
    eqgui._ENC_SECRETS.clear()
    a2 = copy.deepcopy(a)
    a2["BTC"]["creds"][kc_bk] = {"f1": "", "f3": K[kc_bk][2] if kc_bk == "bybit" else "", "avail": []}
    a2["BTC"]["creds"][enc_bk] = {"f1": K[enc_bk][0], "f3": K[enc_bk][2], "avail": []}
    eqgui._f2_save(K[enc_bk][0], K[enc_bk][1])
    eqgui._save_full("ko", "MEMBERTOKEN", a2, {"handle": "", "public": False})
    KC_FAIL[0] = False
    eqgui._ENC_PIN = None
    eqgui._ENC_SECRETS.clear()
    KC_DELS.clear()


for _abk, _kbk, _ebk in (("bybit", "bitget", "bybit"), ("bybit", "bybit", "bitget"),
                         ("bitget", "bybit", "bitget"), ("bitget", "bitget", "bybit")):
    _p25_build(_abk, _kbk, _ebk)
    _tag = f"[자산={_abk} 키체인={_kbk} 블롭={_ebk}]"
    _raw0, _kc0 = yraw(), dict(KC)
    PINS[:] = ["1234"]
    app = start()
    _golive(app, ["BTC"]); _a1 = set(_armed_bk(app).values()); app._master_stop(); flush()
    open(eqgui.CFG_PATH, "w").write(_raw0); KC.clear(); KC.update(_kc0); KC_DELS.clear()
    PINS[:] = []                                   # PIN 취소
    app = start()
    _lk = app._enc_locked
    _golive(app, ["BTC"]); app._master_stop(); flush()
    _rows_lk = [r.get("broker") for r in app._acfg["BTC"]["accounts"]]
    app._save_current_asset(); flush()
    PINS[:] = ["1234"]
    app = start()
    _golive(app, ["BTC"]); _a3 = set(_armed_bk(app).values()); app._master_stop(); flush()
    ck(f"LK(PIN 취소) {_tag} PIN 취소 세션이 빈 행을 박지 않음", _lk is True and _rows_lk == [""], (_lk, _rows_lk))
    ck(f"LK(PIN 취소) {_tag} 그 뒤 PIN 세션 BTC 무장 거래소 = PIN 세션 처음과 같음", _a3 == _a1, (_a1, _a3))
    ck(f"LK(PIN 취소) {_tag} 키체인 삭제 0", KC_DELS == [], KC_DELS)

# P2-5 두 조건 각각: PIN 블롭을 못 연 세션 / 키 저장 실패 목록에 있고 메모리에 없는 거래소 API Key
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bitget": {"f1": "BGKEY-123456789012", "f3": "P"}},
                     "accounts": [row("", "")]}}, {EMAIL: PXKEY, "BGKEY-123456789012": "S2"})
app = start()
_r = app._acfg["BTC"]["accounts"][0]
ck("LK 평소: Bitget 키만 있으면 빈 행 = Bitget", app._acct_broker("BTC", _r) == "bitget")
app._enc_locked = True
ck("LK PIN 블롭을 못 연 세션: 빈 행 = 자산 기본(Bybit, 사전점검에서 멈춤)", app._acct_broker("BTC", _r) == "bybit")
app._enc_locked = False
app._kc_failed = [f"BTC/{eqgui._broker_label('bybit')}/f1"]
ck("LK Bybit API Key가 저장 실패 목록에 있고 메모리에 없음: 빈 행 = 자산 기본", app._acct_broker("BTC", _r) == "bybit")
app._kc_failed = [f"BTC/{eqgui._broker_label('bitget')}/f1"]
ck("LK 실패 목록 항목이라도 메모리에 값이 있으면(다시 넣음) 유일 해석 유지", app._acct_broker("BTC", _r) == "bitget")
app._kc_failed = []

# P2-4: 무장 중 라이브 줄·'다음:' 줄은 상태대로 - 서버 잠금·확인 모드(Operator, 체험 만료)·다시 무장해야 적용
for _lg in ("ko", "en"):
    seed(P0S, {EMAIL: PXKEY}, extra={"lang": _lg})
    app = start()
    _golive(app, ["NQ"])
    _ko = _lg == "ko"
    _l0 = str(app._live_next_lbl.cget("text"))
    ck(f"P2-4[{_lg}] 평소 무장 = 자동 실행 문장", ("자동으로 실행됩니다" if _ko else "Runs automatically") in _l0, _l0)
    app._gate = dict(app._gate, force_dry_run=True); app._apply_gating(); flush()
    _l1 = str(app._live_next_lbl.cget("text")); _n1 = str(app._next_lbl.cget("text"))
    ck(f"P2-4[{_lg}] 서버 잠금(킬스위치) 중 = '주문 보류'·자동 실행 아님(라이브 줄·다음 줄)",
       ("주문 보류: 서버 잠금" if _ko else "orders held: server lock") in _l1
       and ("자동으로 실행" if _ko else "automatically") not in _l1
       and ("주문 보류: 서버 잠금" if _ko else "orders held: server lock") in _n1, (_l1, _n1))
    _r1 = str(app._live_rows["NQ"][0].cget("text"))
    ck(f"P2-4[{_lg}] 서버 잠금 중 자산 줄 = '가동 중(서버 잠금)'(실거래 아님)",
       ("가동 중(서버 잠금)" if _ko else "RUNNING (server lock)") in _r1, _r1)
    MSG.clear()
    app._live_include["GC"].set(1); app._on_asset_toggle(); flush()
    ck(f"P2-4[{_lg}] 서버 잠금 중 가동 중 체크 → 무장 거절·체크 되돌림(라이브 시작과 같은 기준)",
       not any(k[0] == "GC" for k in app._sig_accts) and not app._live_include["GC"].get()
       and any(("서버가 라이브를 잠갔습니다" if _ko else "Live is locked by the server") in m[-1] for m in MSG), (app._sig_accts.keys(), MSG))
    app._gate = dict(app._gate, force_dry_run=False); app._apply_gating(); flush()
    app._gate = dict(app._gate, caps={"use": True, "manualentry": True, "autoentry": False, "connect": True})
    app._apply_gating(); flush()                         # 체험 만료 = 확인 모드로
    _l2 = str(app._live_next_lbl.cget("text")); _n2 = str(app._next_lbl.cget("text"))
    ck(f"P2-4[{_lg}] 확인 모드(체험 만료·Operator) = '확인 모드'·자동 실행 아님", app._watch_only is True
       and ("확인 모드" if _ko else "confirm mode") in _l2 and ("자동으로 실행됩니다" if _ko else "Runs automatically") not in _l2
       and ("확인 모드" if _ko else "confirm mode") in _n2, (_l2, _n2))
    app._gate = dict(app._gate, caps={"use": True, "manualentry": True, "autoentry": True, "connect": True})
    app._apply_gating(); flush()
    app._acfg["NQ"]["accounts"].append(row("50KTC-B", "projectx", label="B"))
    app._refresh_live_panel(); flush()
    _l3 = str(app._live_next_lbl.cget("text"))
    ck(f"P2-4[{_lg}] 다시 무장해야 적용되는 계좌가 있으면 그 사실을 붙임", ("다시 무장해야 적용: NQ(B)" if _ko else "Re-arm to apply: NQ(B)") in _l3, _l3)
    app._master_stop(); flush()

# P3: [연결 지우기]는 빈 크립토 행을 지우기 전 거래소로 박는다 - 남은 다른 거래소로 조용히 무장하지 않음
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""},
                                                                   "bitget": {"f1": "BGKEY-123456789012", "f3": "P"}},
                     "accounts": [row("", "bybit")]}},
     {EMAIL: PXKEY, "BYKEY-123456789012": "S1", "BGKEY-123456789012": "S2"})
app = start()
app._acfg["BTC"]["accounts"][0]["broker"] = ""            # 빈 행(자산 기본 bybit로 해석)
_pick(app, "bybit")
ck("P3 [연결 지우기] Bybit 반환 True", app._clear_broker_conn(confirm=False) is True)
ck("P3 지운 뒤 BTC 행 = Bybit 그대로(Bitget으로 넘어가지 않음)", app._acct_broker("BTC", app._acfg["BTC"]["accounts"][0]) == "bybit",
   app._acfg["BTC"]["accounts"])
_w = _golive(app, ["BTC"])
ck("P3 지운 뒤 [라이브 시작]은 'Bybit 키 미설정'으로 멈춤(확인창 문장대로)", not app._sig_accts and any("키 미설정" in m[-1] for m in _w),
   (_w, app._sig_accts))

# P2-2: BTC [계좌 추가]는 키를 넣은 거래소 행만 만든다 + 사전점검은 '이 행을 지우거나 끄세요'부터
for _lg in ("ko", "en"):
    seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""}},
                         "accounts": [row("", "bybit", one_r=150)]}}, {EMAIL: PXKEY, "BYKEY-123456789012": "S1"}, extra={"lang": _lg})
    app = start()
    app._on_asset("BTC"); flush()
    MSG.clear()
    app._add_acct(); flush()
    ck(f"P2-2[{_lg}] Bybit 키만 있는 BTC [계좌 추가] → 키 없는 Bitget 행 안 만듦·1R은 있는 행에 넣으라고 안내",
       len(app._acfg["BTC"]["accounts"]) == 1 and any(("1R은 그 행에 넣으세요" if _lg == "ko" else "enter the 1R in that row") in m[-1]
                                                      for m in MSG if m[0] == "info"), (app._acfg["BTC"]["accounts"], MSG))
    ck(f"P2-2[{_lg}] 있는 행의 1R(150) 그대로", app._acfg["BTC"]["accounts"][0]["one_r"] == 150, app._acfg["BTC"]["accounts"])
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""},
                                                                   "bitget": {"f1": "BGKEY-123456789012", "f3": "P"}},
                     "accounts": [row("", "bybit")]}}, {EMAIL: PXKEY, "BYKEY-123456789012": "S1", "BGKEY-123456789012": "S2"})
app = start()
app._on_asset("BTC"); flush()
app._add_acct(); flush()
ck("P2-2 두 거래소 키가 다 있으면 Bitget 행 추가(종전)", [app._acct_broker("BTC", r) for r in app._acfg["BTC"]["accounts"]] == ["bybit", "bitget"],
   app._acfg["BTC"]["accounts"])
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""}},
                     "accounts": [row("", "bybit"), row("", "bitget", one_r=150)]}}, {EMAIL: PXKEY, "BYKEY-123456789012": "S1"})
app = start()
_w = _golive(app, ["BTC"])
ck("11a 재확인1 다른 행이 이미 쓰는 거래소(Bybit)로는 바꾸라고 하지 않음 = 두 행이면 '이 행을 지우거나 끄세요'",
   any("BTC Bitget (USDT-F): 키 미설정 - 이 행을 지우거나 끄세요(행 거래소는 [계좌 수정]에서 바꿉니다)" in m[-1] for m in _w)
   and not any("Bybit으로 바꾸거나" in m[-1] for m in _w), _w)
app._acfg["BTC"]["accounts"][1]["on"] = False             # 안내대로 그 행을 끔
_w = _golive(app, ["BTC"])
ck("11a 재확인1 안내대로 Bitget 행을 끄면 시작·Bybit 무장", not _w and _armed_bk(app).get(("BTC", 0)) == "bybit", (_w, _armed_bk(app)))
app._master_stop(); flush()

# ═════ 1008b 후속(2026-10-08b, a6432591 집중 리뷰) ═════
# P2-1: 키 넣기 전 BTC [계좌 추가] → 기본 행을 키 없는 Bybit로 박지 않음 → Bitget 키만 넣어도 시작됨, '다음:' 줄은 사실대로
seed(P1S, {EMAIL: PXKEY})
app = start()
app._on_asset("BTC"); flush()
_pick(app, "bitget")
MSG.clear()
app._add_acct(); flush()
ck("8b P2-1 키 없을 때 BTC [계좌 추가] → 행 추가 안 함·빈 행 안 박음·'키를 넣으면 이 행이 그 거래소로' 안내",
   [r.get("broker") for r in app._acfg["BTC"]["accounts"]] == [""]
   and any("이 행이 그 거래소를 씁니다" in m[-1] for m in MSG if m[0] == "info"), (app._acfg["BTC"]["accounts"], MSG))
app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, "BGKEY-123456789012")
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BGSECRET-12345")
type_into(app.f3, "PASS"); _autosave(app)
app._on_asset("BTC"); flush()
_nx = str(app._next_lbl.cget("text"))
_w = _golive(app, ["BTC"])
ck("8b P2-1 그 뒤 Bitget 키만 넣으면 [라이브 시작] 통과·Bitget 무장", not _w and _armed_bk(app).get(("BTC", 0)) == "bitget",
   (_w, _armed_bk(app), app._acfg["BTC"]["accounts"]))
app._master_stop(); flush()
# 행이 키 없는 Bybit로 박혀 있으면(옛 빌드가 남긴 행) 연결 확인 기록이 있어도 '준비 완료'라고 하지 않음
app._acfg["BTC"]["accounts"][0]["broker"] = "bybit"
app._test_ok = {"BTC"}
app._refresh_next_action(); flush()
_nx = str(app._next_lbl.cget("text"))
ck("8b P2-1 사전점검이 막히는 BTC는 _test_ok가 있어도 '준비 완료' 아님 + 할 일(행 거래소를 Bitget으로)", "준비 완료" not in _nx
   and "다음: [계좌 수정]에서 이 행 거래소를 Bitget으로 바꾸거나 Bybit 키를 브로커 연결 설정에 넣으세요" in _nx, _nx)

# P2-2: 크립토 키 미설정 문구 - 행 하나 KO/EN, 행 둘은 종전
for _lg, _exp, _exp2 in (("ko", "BTC Bybit (USDT perp): 키 미설정 - Bybit 키를 브로커 연결 설정에 넣거나 라이브 패널에서 BTC 체크를 끄세요",
                         "BTC Bybit (USDT perp): 키 미설정 - Bybit 키를 브로커 연결 설정에 넣은 뒤 BTC를 다시 체크하세요"),
                        ("en", "BTC Bybit (USDT perp): key missing - add your Bybit key in Broker connections, or uncheck BTC in the live panel",
                         "BTC Bybit (USDT perp): key missing - add your Bybit key in Broker connections, then check BTC again")):
    seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {}, "accounts": [row("", "bybit", label="")]}},
         {EMAIL: PXKEY}, extra={"lang": _lg})
    app = start()
    _w = _golive(app, ["BTC"])
    ck(f"8b P2-2[{_lg}] 행 하나 = 키를 넣거나 BTC 실행 체크를 끄라고", any(_exp in m[-1] for m in _w), _w)
    app._live_include["BTC"].set(0); app._acfg["BTC"]["include"] = False
    _golive(app, ["NQ"])
    MSG.clear()
    app._live_include["BTC"].set(1); app._on_asset_toggle(); flush()
    ck(f"11a P3[{_lg}] 가동 중 체크(앱이 체크를 되돌림) = '키를 넣은 뒤 다시 체크하세요'", any(_exp2 in m[-1] for m in MSG), MSG)
    app._master_stop(); flush()

# P2-3: 마감 청산만 남은 무장 + 킬스위치 → 라이브 줄·👉 줄·자산 줄 모두 '마감 청산 보류(서버 잠금)'
for _lg in ("ko", "en"):
    _ko = _lg == "ko"
    seed(P0S, {EMAIL: PXKEY}, extra={"lang": _lg})
    app = start()
    _golive(app, ["NQ"])
    app._sig_accts.clear()                                 # 신호 대기 꺼짐(서버 불통 뒤 parked·통과 익절 등) - 마감 청산만
    app._refresh_live_panel(); flush()
    ck(f"8b P2-3[{_lg}] 잠금 전 = '마감 청산만 가동 중'", ("마감 청산만 가동 중" if _ko else "Session-close only") in
       str(app._live_next_lbl.cget("text")), app._live_next_lbl.cget("text"))
    app._gate = dict(app._gate, force_dry_run=True); app._apply_gating(); flush()
    app._on_asset("NQ"); flush()
    _l = str(app._live_next_lbl.cget("text")); _n = str(app._next_lbl.cget("text")); _r = str(app._live_rows["NQ"][0].cget("text"))
    _k = "마감 청산 보류(서버 잠금)" if _ko else "ession-close held (server lock)"
    ck(f"8b P2-3[{_lg}] 서버 잠금 = 라이브 줄·👉 줄·자산 줄 '마감 청산 보류(서버 잠금)'", _k in _l and _k in _n and _k in _r
       and ("지금은 청산하지 않습니다" if _ko else "No exits right now") in _l, (_l, _n, _r))
    ck(f"8b P2-3[{_lg}] 실제 마감 청산도 보류(_close_mode live=False)", app._close_mode(next(iter(app._auto_accts.values()))[0])[0] is False)
    app._master_stop(); flush()

# P3(a): 거절된 행 콤보는 그릴 때 값이 아니라 지금 값으로 되돌아가고, 다음 수집이 그걸 회원 변경으로 적용하지 않는다
seed(P1S, {EMAIL: PXKEY})
app = start()
app._on_asset("BTC"); flush()
app._acct_edit = True; app._build(); flush()           # 키 없을 때 그림(콤보 = Bybit)
_pick(app, "bitget")
app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, "BGKEY-123456789012")
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BGSECRET-12345")
type_into(app.f3, "PASS"); _autosave(app)
app._collect_acct_widgets()
_cbw = app._acct_widgets[0]["broker"]
ck("8b P3(a) 키 뒤 행 = Bitget·콤보 목록에 Bitget", app._acfg["BTC"]["accounts"][0].get("broker") == "bitget"
   and eqgui._broker_label("bitget") in tuple(_cbw.cget("values")), (app._acfg["BTC"]["accounts"], _cbw.cget("values")))
app._golive_busy_mark(True)
MSG.clear()
_cbw.set(eqgui._broker_label("bybit")); _cbw.event_generate("<<ComboboxSelected>>"); flush()
ck("8b P3(a) 테스트 중 거절 → 콤보는 지금 값(Bitget)으로", _cbw.get() == eqgui._broker_label("bitget"), _cbw.get())
app._golive_busy_mark(False)
app._collect_acct_widgets()
ck("8b P3(a) 다음 수집이 행을 Bybit로 바꾸지 않음", app._acfg["BTC"]["accounts"][0].get("broker") == "bitget", app._acfg["BTC"]["accounts"])
seed(P0S, {EMAIL: PXKEY})
app = start()
app._on_asset("NQ"); flush()
app._acct_edit = True; app._build(); flush()
app._acfg["NQ"]["accounts"][0]["id"] = "50KTC-B"                # 그린 뒤 행 계좌가 바뀜(입력 칸 수집 등)
_idw = app._acct_widgets[0]["acct_id"]
app._golive_busy_mark(True)
_idw.set("150KTC-A"); _idw.event_generate("<<ComboboxSelected>>"); flush()
app._golive_busy_mark(False)
ck("8b P3(a) 계좌 콤보 거절 → 행의 지금 계좌 ID로 되돌림", _idw.get() == "…0KTC-B" and app._acfg["NQ"]["accounts"][0]["id"] == "50KTC-B",
   (_idw.get(), app._acfg["NQ"]["accounts"]))

# P3(b): 연결 테스트 중 거절은 시험 중인 연결만 - 무관한 브로커의 첫 키는 저장
seed(P1S, {EMAIL: PXKEY})
app = start()
_gt = app._golive_busy_mark(True, conns=app._golive_conns(["NQ"]))
_pick(app, "bybit")
MSG.clear()
app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, "BYKEY-123456789012")
app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BYSECRET-1"); _autosave(app)
ck("8b P3(b) 테스트 중 무관한 브로커(Bybit) 첫 키 → 저장·칸 그대로·거절창 없음",
   app._acfg["BTC"]["creds"]["bybit"]["f1"] == "BYKEY-123456789012" and app.user.get() == "BYKEY-123456789012"
   and not any("연결 테스트 중이라" in m[-1] for m in MSG), (app._acfg["BTC"]["creds"].get("bybit"), MSG))
_pick(app, "projectx")
MSG.clear()
type_into(app.user, "other@mail.com"); _autosave(app)
ck("8b P3(b) 시험 중인 연결(Topstep)은 그대로 거절", app._acfg["NQ"]["creds"]["projectx"]["f1"] == EMAIL
   and any("연결 테스트 중이라 연결 정보를" in m[-1] for m in MSG), (app._acfg["NQ"]["creds"]["projectx"], MSG))
app._golive_busy_mark(False, _gt)

# P3(c): 긴 상태 줄(서버 잠금 + 다시 무장 꼬리, EN 확인 모드)은 줄바꿈 - 요청 너비가 줄바꿈 폭을 넘지 않음
seed(P0S, {EMAIL: PXKEY}, extra={"lang": "en"})
app = start()
_golive(app, ["NQ"])
app._acfg["NQ"]["accounts"].append(row("50KTC-B", "projectx", label="SECOND-ACCOUNT-LABEL"))
app._gate = dict(app._gate, force_dry_run=True); app._apply_gating(); flush()
_rl = app._live_rows["NQ"][0]; _ll = app._live_next_lbl
ck("8b P3(c) 자산 줄·라이브 줄 줄바꿈 폭 설정·요청 너비가 폭 안", int(str(_rl.cget("wraplength"))) == 600
   and int(str(_ll.cget("wraplength"))) == 880 and _rl.winfo_reqwidth() <= 600 + 10 and _ll.winfo_reqwidth() <= 880 + 10,
   (_rl.cget("text"), _rl.winfo_reqwidth(), _ll.cget("text"), _ll.winfo_reqwidth()))
app._master_stop(); flush()

# P3(e): EN 상태어 = Running, 거절 문구는 출처 없이, em 대시 없음
seed(P0S, {EMAIL: PXKEY}, extra={"lang": "en"})
app = start()
_golive(app, ["NQ"])
app._on_asset("NQ"); flush()
ck("8b P3(e) EN 👉 줄 = 'Running - runs automatically'", "Running - runs automatically" in str(app._next_lbl.cget("text")),
   app._next_lbl.cget("text"))
app._master_stop(); flush()
_SRC8 = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
ck("8b P3(e) 'Armed - runs'·'Armed (orders held'·'라이브 시작 연결 테스트 중이라' 문구 없음",
   "Armed - runs" not in _SRC8 and "Armed (orders held" not in _SRC8 and "라이브 시작 연결 테스트 중이라" not in _SRC8)
ck("8b P3(e) 서버 잠금 시작 거절 문구에 em 대시 없음", "잠갔습니다 —" not in _SRC8 and "server —" not in _SRC8)

# ═════ 2026-10-11a(32730827 재리뷰) ═════
# P2: BTC 거래소를 옮기는 회원(Bybit 연결 지우기 → Bitget 키 → 라이브 시작) - 행 거래소를 바꾸라는 안내 + 그대로 하면 시작
for _lg, _exp in (("ko", "BTC Bybit (USDT perp): 키 미설정 - [계좌 수정]에서 이 행 거래소를 Bitget으로 바꾸거나 Bybit 키를 브로커 연결 설정에 넣으세요"),
                  ("en", "BTC Bybit (USDT perp): key missing - switch this row to Bitget with [Edit accounts], or add your Bybit key in Broker connections")):
    seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""}},
                         "accounts": [row("", "", label="")]}}, {EMAIL: PXKEY, "BYKEY-123456789012": "S1"}, extra={"lang": _lg})
    app = start()
    _pick(app, "bybit")
    app._clear_broker_conn(confirm=False)
    _pick(app, "bitget")
    app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, "BGKEY-123456789012")
    app._unlocked = True; app.key.config(state="normal"); type_into(app.key, "BGSECRET-12345")
    type_into(app.f3, "PASS"); _autosave(app)
    _w = _golive(app, ["BTC"])
    ck(f"11a P2[{_lg}] 거래소 옮기기: 사전점검 = 행 거래소를 바꾸거나 옛 키를 넣으라고", any(_exp in m[-1] for m in _w) and not app._sig_accts, _w)
    app._on_asset("BTC"); flush(); app._refresh_next_action()
    _nx = str(app._next_lbl.cget("text"))
    ck(f"11a P2[{_lg}] 계좌 칸 '다음:' 줄도 같은 할 일", ("이 행 거래소를 Bitget으로" if _lg == "ko" else "switch this row to Bitget") in _nx, _nx)
    app._acct_edit = True; app._build(); flush()
    app._acct_widgets[0]["broker"].set(eqgui._broker_label("bitget")); app._save_acct_widgets(); flush()
    _w = _golive(app, ["BTC"])
    ck(f"11a P2[{_lg}] 안내대로 행 거래소를 Bitget으로 → 시작·Bitget 무장", not _w and _armed_bk(app).get(("BTC", 0)) == "bitget", (_w, _armed_bk(app)))
    app._master_stop(); flush()
# P3: PIN 보관분을 못 연 세션 = PIN 안내
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {}, "accounts": [row("", "")]}}, {EMAIL: PXKEY})
app = start()
app._enc_locked = True
app._kc_failed = [f"BTC/{eqgui._broker_label('bybit')}/f1"]     # 그 자산 API Key가 블롭에(재확인 3)
_w = _golive(app, ["BTC"])
ck("11a P3 PIN 못 연 세션 = 'PIN으로 보관한 키를 이번 실행에서 열지 못했습니다. 다시 시작해 PIN을 넣으세요'",
   any("키 미설정 - PIN으로 보관한 키를 이번 실행에서 열지 못했습니다. 다시 시작해 PIN을 넣으세요" in m[-1] for m in _w), _w)
# P3: 행 이름이 거래소 이름과 다르면 괄호 유지, 같으면 한 번만
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {}, "accounts": [row("", "bybit", label="MAIN")]}}, {EMAIL: PXKEY})
app = start()
_w = _golive(app, ["BTC"])
ck("11a P3 행 이름이 다르면 'BTC MAIN (Bybit (USDT perp))' 유지", any("BTC MAIN (Bybit (USDT perp)): 키 미설정" in m[-1] for m in _w), _w)
# P3: 계좌 칸 '다음:' 줄 = 이유·서버·등급 상태(라이브 줄과 같은 판정)
for _lg in ("ko", "en"):
    _ko = _lg == "ko"
    seed(P0S, {EMAIL: PXKEY}, extra={"lang": _lg})
    app = start()
    app._test_ok = {"NQ"}
    app._on_asset("NQ"); flush()

    def _nx():
        app._refresh_next_action(); return str(app._next_lbl.cget("text"))
    ck(f"11a P3[{_lg}] 정상 = 준비 완료", ("준비 완료" if _ko else "Ready") in _nx(), _nx())
    app._acfg["NQ"]["accounts"][0]["on"] = False
    ck(f"11a P3[{_lg}] 켜진 계좌 없음 = '준비 안 됨 - 켜진 계좌 없음'", ("다음: 준비 안 됨 - 켜진 계좌 없음" if _ko else "Next: not ready - no account on") in _nx(), _nx())
    app._acfg["NQ"]["accounts"][0]["on"] = True
    _g0 = dict(app._gate)
    app._gate = {"ok": False, "offline": True}
    ck(f"11a P3[{_lg}] 서버 불통 = 라이브 줄과 같은 이유", ("서버에 연결되지 않아" if _ko else "Can't reach the server") in _nx(), _nx())
    app._gate = dict(_g0, caps={"use": False, "autoentry": False, "connect": True})
    ck(f"11a P3[{_lg}] Preview(자동 실행 권한 없음) = 'Operator 등급부터'", "Operator" in _nx() and ("준비 완료" if _ko else "Ready") not in _nx(), _nx())
    app._gate = dict(_g0)
    app._broker_allowed = lambda b: b != "projectx"
    ck(f"11a P3[{_lg}] 서버가 브로커 지원을 끔 = '브로커 지원 꺼짐(서버)'", ("브로커 지원 꺼짐(서버)" if _ko else "broker disabled (server)") in _nx(), _nx())
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""}},
                     "accounts": [row("", "bybit"), row("", "bybit")]}}, {EMAIL: PXKEY, "BYKEY-123456789012": "S1"})
app = start()
app._on_asset("BTC"); flush(); app._refresh_next_action()
ck("11a P3 같은 거래소 행 둘 = '준비 안 됨 - 같은 거래소 행 중복'", "다음: 준비 안 됨 - 같은 거래소 행 중복" in str(app._next_lbl.cget("text")),
   app._next_lbl.cget("text"))

# 2026-10-11a 재확인 2: 바꿔 갈 거래소를 서버가 꺼 두었으면 그쪽으로 바꾸라고 하지 않음
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {"bitget": {"f1": "BGKEY-123456789012", "f3": "P"}},
                     "accounts": [row("", "bybit")]}}, {EMAIL: PXKEY, "BGKEY-123456789012": "S2"})
app = start()
app._acfg["BTC"]["accounts"][0]["broker"] = "bybit"
app._broker_allowed = lambda b: b != "bitget"
_w = _golive(app, ["BTC"])
ck("11a 재확인2 서버가 끈 거래소(Bitget)로는 바꾸라고 하지 않음(행 하나 = 키 넣기·체크 끄기)",
   any("BTC Bybit (USDT perp): 키 미설정 - Bybit 키를 브로커 연결 설정에 넣거나 라이브 패널에서 BTC 체크를 끄세요" in m[-1] for m in _w)
   and not any("Bitget으로 바꾸거나" in m[-1] for m in _w), _w)
# 재확인 3: PIN 갈래는 그 자산의 API Key가 블롭에 있을 때만(블롭이 Topstep 키뿐이면 보통 안내)
seed({**P0S, "BTC": {"broker": "bybit", "include": True, "creds": {}, "accounts": [row("", "bybit")]}}, {EMAIL: PXKEY})
app = start()
app._enc_locked = True
app._kc_failed = ["API secret (memb…)"]
_w = _golive(app, ["BTC"])
ck("11a 재확인3 블롭에 BTC 키 없음 = PIN 안내 아님", not any("PIN으로 보관한 키" in m[-1] for m in _w)
   and any("Bybit 키를 브로커 연결 설정에 넣거나" in m[-1] for m in _w), _w)
app._kc_failed = [f"BTC/{eqgui._broker_label('bitget')}/f1"]   # 행 해석(Bybit)과 다른 거래소 키가 블롭에 - 자산 단위로 PIN 안내
_w = _golive(app, ["BTC"])
ck("11a 재확인3 블롭에 그 자산 키(다른 거래소라도) = PIN 안내", any("키 미설정 - PIN으로 보관한 키를 이번 실행에서 열지 못했습니다" in m[-1]
                                                              for m in _w), _w)
app._enc_locked = False
ck("11a 재확인3 블롭을 연 세션이면 PIN 안내 아님", "PIN" not in app._crypto_keymiss_hint("BTC", "bybit"), app._crypto_keymiss_hint("BTC", "bybit"))
# 재확인 4: [계좌 수정]에서 거래소만 바꾸고 잠그지 않은 채 다시 체크해도 기본 라벨 = 새 거래소 이름(진입 로그·무장 사본)
seed({**P0S, "BTC": {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""},
                                                                    "bitget": {"f1": "BGKEY-123456789012", "f3": "P"}},
                     "accounts": [row("", "bybit", label="Bybit (USDT perp)")]}},
     {EMAIL: PXKEY, "BYKEY-123456789012": "S1", "BGKEY-123456789012": "S2"})
app = start()
_golive(app, ["NQ"])
app._on_asset("BTC"); flush()
app._acct_edit = True; app._build(); flush()
_cbw = app._acct_widgets[0]["broker"]
_cbw.set(eqgui._broker_label("bitget")); _cbw.event_generate("<<ComboboxSelected>>"); flush()
app._live_include["BTC"].set(1); app._on_asset_toggle(); flush()
_lab = app._acfg["BTC"]["accounts"][0].get("label")
ck("11a 재확인4 거래소만 바꾸고 바로 체크 → 라벨·무장 사본 라벨 = Bitget 이름", _lab == eqgui._broker_label("bitget")
   and _armed_bk(app).get(("BTC", 0)) == "bitget" and app._sig_accts[("BTC", 0)].get("label") == eqgui._broker_label("bitget"),
   (_lab, _armed_bk(app), app._sig_accts.get(("BTC", 0), {}).get("label")))
ck("11a 재확인4 화면 라벨 칸도 새 이름", app._acct_widgets[0]["label"].get() == eqgui._broker_label("bitget"), app._acct_widgets[0]["label"].get())
app._master_stop(); flush()
seed({**P0S, "BTC": {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "BYKEY-123456789012", "f3": ""},
                                                                    "bitget": {"f1": "BGKEY-123456789012", "f3": "P"}},
                     "accounts": [row("", "bybit", label="MAIN")]}},
     {EMAIL: PXKEY, "BYKEY-123456789012": "S1", "BGKEY-123456789012": "S2"})
app = start()
app._on_asset("BTC"); flush()
app._acct_edit = True; app._build(); flush()
_cbw = app._acct_widgets[0]["broker"]
_cbw.set(eqgui._broker_label("bitget")); _cbw.event_generate("<<ComboboxSelected>>"); flush()
ck("11a 재확인4 회원이 붙인 라벨(MAIN)은 그대로", app._acfg["BTC"]["accounts"][0].get("label") == "MAIN", app._acfg["BTC"]["accounts"])

# ═════ T16: 래칫 ═════
SRC = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()


def _func_spans():
    out = []
    _cs = SRC.index("\nclass App")
    _ce = min([i for i in (SRC.find("\ndef ", _cs + 5), SRC.find("\nclass ", _cs + 5), SRC.find("\nif __name__", _cs + 5))
               if i > 0] or [len(SRC)])
    for m in re.finditer(r"^( *)def (\w+)\(", SRC, re.M):
        _ind = len(m.group(1))
        if _ind == 0 or (_ind == 4 and _cs < m.start() < _ce):   # 모듈 함수·App 메서드만(안쪽 도우미는 바깥 함수로)
            out.append((m.start(), m.group(2)))
    return out


_spans = _func_spans()


def _func_at(pos):
    name = None
    for st, nm in _spans:
        if st > pos:
            break
        name = nm
    return name


_asg = [(m.start(), _func_at(m.start())) for m in
        re.finditer(r'(\["creds"\]|\.setdefault\("creds", \{\}\))\내부 기록+\]\s*=(?!=)', SRC)]
ck("T16 creds[브로커] = 대입은 _load·_unify_creds·_creds_of 안에서만",
   all(fn in ("_load", "_unify_creds", "_creds_of") for _, fn in _asg) and len(_asg) >= 2, _asg)
_ini = SRC[SRC.index("    def __init__(self, root):"):]
ck("T16 __init__: 합치기가 _enc_unlock_startup 뒤", _ini.index("self._enc_unlock_startup()") < _ini.index("_unify_creds(")
   < _ini.index("self._build()"))
ck("T16 지운 버튼 함수 부재", not any(hasattr(eqgui.App, f) for f in ("_copy_nq_settings", "_pull_broker_keys", "_copy_acct_setup")))


def _body(name):
    i = SRC.index(f"    def {name}(")
    j = SRC.index("\n    def ", i + 10)
    return SRC[i:j]


for _fn in ("_save_current_asset", "_save_creds_fields"):
    ck(f"T16 {_fn}에 _kc_del/_f2_del 0", "_kc_del(" not in _body(_fn) and "_f2_del(" not in _body(_fn))
ck("T16 공용 브로커 holder = NQ·GC(안내 문구 'NQ와 GC'가 사실)",
   all(eqgui._bk_holders(b) == ["NQ", "GC"] for b in ("projectx", "nt8", "ibkr"))
   and not eqgui._bk_shared("bybit") and not eqgui._bk_shared("bitget"))
ck("T16 kc_enc fields 이름 해시", eqgui._enc_field_name("f2|someone@x.com").startswith("f2|#")
   and "someone" not in eqgui._enc_field_name("f2|someone@x.com"))

try:
    ROOT.destroy()
except Exception:
    pass
# T17 TopstepX 아이디가 이메일이 아닌 긴 사용자명일 때(재리뷰 사전 지적): 토큰 모양만으로 Topstep 아이디를 지우지 않는다
UN = "trader_john_2024_x"                   # 영숫자·밑줄 18자 - 12자 이상 토큰 모양이지만 앱 브리지 토큰(24자)은 아님
APP_TOK = "AbCdEfGhIjKlMnOpQrStUv_-"           # token_urlsafe(18) 모양 24자
def _mk(px_nq, px_gc, nt_nq, nt_gc):
    return {"NQ": {"broker": "projectx", "creds": {"projectx": {"f1": px_nq, "f3": "", "avail": []},
                                                    "nt8": {"f1": nt_nq, "f3": "", "avail": []}}, "accounts": []},
            "GC": {"broker": "projectx", "creds": {"projectx": {"f1": px_gc, "f3": "", "avail": []},
                                                    "nt8": {"f1": nt_gc, "f3": "", "avail": []}}, "accounts": []},
            "BTC": {"broker": "bybit", "creds": {}, "accounts": []}}
# (a) 사용자명이 GC Bridge Token 칸에 섞임 + 설치된 .cs 토큰은 다름 → Topstep 아이디 유지, nt8 칸만 메모리에서 뺌
_u = _mk(UN, UN, APP_TOK, UN)
eqgui._unify_creds(_u, bridge=(APP_TOK, "8377"))
ck("T17a 사용자명 Topstep 아이디 유지(NQ·GC)", _u["NQ"]["creds"]["projectx"]["f1"] == UN and _u["GC"]["creds"]["projectx"]["f1"] == UN,
   (_u["NQ"]["creds"]["projectx"], _u["GC"]["creds"]["projectx"]))
ck("T17a Bridge Token 칸의 사용자명은 쓰지 않음, 토큰은 .cs 것", _u["GC"]["creds"]["nt8"]["f1"] in ("", APP_TOK)
   and _u["NQ"]["creds"]["nt8"]["f1"] == APP_TOK, (_u["NQ"]["creds"]["nt8"], _u["GC"]["creds"]["nt8"]))
# (b) .cs를 못 읽음 + 같은 사용자명이 두 자리에 → 지우지 않고 split
_u = _mk(UN, "", UN, "")
_sp, _nt = eqgui._unify_creds(_u, bridge=("", ""))
ck("T17b .cs 없음·모양 판별 불가 → Topstep 아이디 안 지움", _u["NQ"]["creds"]["projectx"]["f1"] == UN, _u["NQ"]["creds"]["projectx"])
# (c) 앱 생성 토큰(24자)이 Topstep 아이디 칸에 섞임(회원 10/7 모양) → Topstep 칸에서 뺌(.cs 없어도)
_u = _mk("", APP_TOK, APP_TOK, APP_TOK)
eqgui._unify_creds(_u, bridge=("", ""))
ck("T17c 앱 토큰이 Topstep 아이디 칸에 섞이면 뺌", _u["GC"]["creds"]["projectx"]["f1"] == "", _u["GC"]["creds"]["projectx"])

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
