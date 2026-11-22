#!/usr/bin/env python3
# EQ Autopilot - 계좌 설정 빈 목록 오프라인 시험. 0=통과
# 대표 2026-11-06: "브로커가 비어 있으면 사용 계좌도 비워 놔야지, 당연하지"
#                 "여기서 계좌 추가 누르면 가능한 브로커가 없다, 브로커 먼저 해라"
#                 삭제를 눌렀는데 빈 행이 다시 생겨 지워지지 않았다.
# 1) 연결된 브로커가 없는 자산 = 계좌 목록이 비어 있다(새 설치, 기존 회원의 손대지 않은 빈 행)
# 2) 연결된 브로커가 없을 때 [계좌 추가] = 알림만, 행 안 만듦
# 3) 삭제는 마지막 행까지 지운다(빈 행이 다시 생기지 않음, 재시작해도)
# (a) 기동 때 빼는 행은 손대지 않은 빈 행뿐 - 계좌 ID, 행 브로커, 수동, 고친 라벨, 고친 1R, 프롭, 연결된 브로커, PIN 잠김, 키 저장 실패는 그대로
# (b) [연결 지우기] = 그 브로커의 계좌 ID 없는 행은 목록에서 빠지고 계좌 ID 있는 행은 남는다, 무장된 자산은 그대로
# (c) BTC에 키를 넣어도 행이 저절로 생기지 않는다 - [계좌 추가]가 그 거래소 행을 만든다
# (d) 빈 목록 아래 '연결된 브로커가 없습니다' 한 줄
# 실제 eqgui.App을 실제 Tk 위젯으로 띄운다(test_global_creds_offline.py와 같은 틀). HOME = 임시 폴더, 네트워크 차단, 키체인 = dict,
# 메시지박스·PIN 창 = 기록/큐, 브로커 = 가짜.
import copy
import os
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_ae_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_SHARE", None)

import socket  # noqa: E402


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.socket.connect_ex = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk

import threading  # noqa: E402
RUN_SYNC = ("_async_load_key", "_master_start", "_autosave_fire", "_panel_conn_test", "_live_arm_assets",
            "_save_creds_and_test", "_run.<locals>.wrap")


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

KC = {}


def _kc_save(acc, s):
    if not s:
        return False
    KC[acc or "default"] = s
    return True


eqgui._kc_save = _kc_save
eqgui._kc_load = lambda acc: KC.get(acc or "default", "")
eqgui._kc_del = lambda acc: KC.pop(acc or "default", None)
MSG, PINS, ASK = [], [], [True]
eqgui.messagebox.askyesno = lambda *a, **k: (MSG.append(("ask",) + tuple(str(x) for x in a)) or ASK[0])
eqgui.messagebox.showinfo = lambda *a, **k: MSG.append(("info",) + tuple(str(x) for x in a))
eqgui.messagebox.showwarning = lambda *a, **k: MSG.append(("warn",) + tuple(str(x) for x in a))
eqgui.messagebox.showerror = lambda *a, **k: MSG.append(("error",) + tuple(str(x) for x in a))
eqgui.App._notice = lambda self, t, m, buttons=None: MSG.append(("notice", str(t), str(m)))
eqgui.simpledialog.askstring = lambda *a, **k: (PINS.pop(0) if PINS else None)

EMAIL, PXKEY = "member@mail.com", "PX-API-KEY-777"
BYK, BGK = "BYKEY-123456789012", "BGKEY-123456789012"


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
    if broker == "projectx":
        return FakePX(f1, f2)
    if broker in ("bybit", "bitget", "okx"):
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
eqgui.App._autoload_topstep_scope = lambda self, a: None    # 가용 목록 자동 로드(네트워크) - 이 시험과 무관
eqgui.App._autoload_nt8_avail = lambda self, a: None
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
    MSG.clear()
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


def seed(assets, kc=None, extra=None, raw=None):
    if raw is not None:
        d = raw
    elif assets is None:
        d = None                                   # 설정 파일 없음 = 새 설치
    else:
        d = {"lang": "ko", "token": "MEMBERTOKEN", "assets": copy.deepcopy(assets)}
        d.update(extra or {})
    if d is None:
        try:
            os.remove(eqgui.CFG_PATH)
        except OSError:
            pass
    else:
        with open(eqgui.CFG_PATH, "w") as f:
            yaml.safe_dump(d, f)
    KC.clear()
    KC.update(kc or {})


def row(id_, broker, one_r=600, label="", on=True, **kw):
    r = {"id": id_, "one_r": one_r, "on": on, "label": label, "broker": broker}
    r.update(kw)
    return r


def blank(asset_bk, one_r=500):
    """옛 앱이 채워 두던 빈 행 그대로(계좌 ID, 행 브로커 없음, 라벨 = 자산 브로커 기본 이름, 1R 기본값)."""
    return row("", "", one_r=one_r, label=eqgui._broker_label(asset_bk))


def ydisk():
    return yaml.safe_load(open(eqgui.CFG_PATH, encoding="utf-8").read()) or {}


def type_into(entry, text):
    st = str(entry.cget("state"))
    entry.config(state="normal")
    entry.delete(0, "end")
    entry.insert(0, text)
    if st != "normal":
        entry.config(state=st)


def _pick(app, b):
    app.brokerbox.set(eqgui._broker_label(b)); app._on_broker(); flush()


def _enter_crypto(app, b, key, sec, f3=None):
    _pick(app, b)
    app._f1_unlocked = True; app.user.config(state="normal"); type_into(app.user, key)
    app._unlocked = True; app.key.config(state="normal"); type_into(app.key, sec)
    if f3 is not None and hasattr(app, "f3"):
        type_into(app.f3, f3)
    app._autotest_fp = None
    app._autosave_fire(); flush()


def _golive(app, assets):
    for _a in eqgui._ASSETS:
        app._live_include[_a].set(1 if _a in assets else 0)
    m0 = len(MSG)
    app._master_start(); flush()
    return [m for m in MSG[m0:] if m[0] in ("warn", "error")]


def _armed_bk(app):
    return {k: (v[0] if isinstance(v, list) else v).get("broker") for k, v in app._sig_accts.items()}


def _acct_texts(app):
    """계좌 설정에 보이는(싸인) 위젯 글자 - pack_forget으로 숨긴 줄은 뺀다."""
    out = []

    def _w(x):
        for c in x.winfo_children():
            if not c.winfo_manager():
                continue
            try:
                out.append(str(c.cget("text")))
            except Exception:
                pass
            _w(c)
    _w(app._acct_body)
    return out


def _del_buttons(app):
    return [t for t in _acct_texts(app) if t in ("삭제", "Remove")]


def rows(app, a):
    return app._acfg[a]["accounts"]


FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


HINT_KO = "연결된 브로커가 없습니다. 왼쪽 [브로커 연결]에서 먼저 연결하세요."
HINT_EN = "No broker is connected. Connect one in Broker connection on the left first."
ADD_KO = "가능한 브로커가 없습니다. 브로커 연결을 먼저 하세요."
ADD_EN = "No broker is available. Connect a broker first."
PX_NQ = {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A", "50KTC-B"]}}

# ═════ 1, 2, (d): 새 설치 = 세 자산 모두 빈 목록, [계좌 추가] = 알림만 ═════
for _lg in ("ko", "en"):
    seed(None)
    app = start()
    if _lg == "en":
        app.lang = "en"; app._build(); flush()
    ck(f"1[{_lg}] 새 설치: NQ, GC, BTC 계좌 목록이 모두 비어 있음", all(rows(app, a) == [] for a in eqgui._ASSETS),
       {a: rows(app, a) for a in eqgui._ASSETS})
    for _a in ("NQ", "BTC"):
        app._on_asset(_a); flush()
        _t = _acct_texts(app)
        ck(f"d[{_lg}] {_a} 빈 목록 아래 '연결된 브로커가 없습니다' 한 줄", (HINT_KO if _lg == "ko" else HINT_EN) in _t, _t)
        ck(f"d[{_lg}] {_a} 연결 없음 줄이 있으면 '다른 브로커는 연결하면 나옵니다' 줄은 없음",
           not any(("다른 브로커는" in t) or ("Other brokers appear" in t) for t in _t), _t)
        MSG.clear()
        app._add_acct(); flush()
        ck(f"2[{_lg}] {_a} 브로커 없음 [계좌 추가] → 알림 '{ADD_KO if _lg == 'ko' else ADD_EN}', 행 안 만듦",
           rows(app, _a) == [] and any(m[0] == "info" and m[-1] == (ADD_KO if _lg == "ko" else ADD_EN) for m in MSG), (rows(app, _a), MSG))
    _nx = str(app._next_lbl.cget("text"))
    ck(f"1[{_lg}] '다음:' 줄은 브로커 연결부터(토큰 있음)", ("브로커를 고르고" if _lg == "ko" else "pick a broker") in _nx
       or ("멤버십 토큰" if _lg == "ko" else "membership token") in _nx, _nx)
    ck(f"1[{_lg}] 화면을 다시 그려도(_accts_of) 빈 행이 생기지 않음", all(rows(app, a) == [] for a in eqgui._ASSETS))

# ═════ (a) 기존 회원 설정: 손대지 않은 빈 행만 빠진다 ═════
OLD = {"NQ": {"broker": "projectx", "include": True, "creds": PX_NQ, "accounts": [row("150KTC-A", "projectx")]},
       "GC": {"broker": "projectx", "include": False, "creds": {"projectx": {"f1": "", "f3": ""}},
              "accounts": [blank("projectx", one_r=600)]},
       "BTC": {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "", "f3": ""}},
               "accounts": [blank("bybit")]}}
seed(OLD, {EMAIL: PXKEY})
LOGS.clear()
app = start()
ck("a 연결 없는 BTC의 손대지 않은 빈 행은 기동 때 빠짐", rows(app, "BTC") == [], rows(app, "BTC"))
ck("a GC는 NQ와 Topstep 연결을 같이 써 '연결 있음' - 빈 행 그대로(종전)", len(rows(app, "GC")) == 1, rows(app, "GC"))
ck("a NQ 계좌 행 그대로", [r["id"] for r in rows(app, "NQ")] == ["150KTC-A"], rows(app, "NQ"))
ck("a 뺐다는 로그 한 줄(자산과 개수)", any("빈 계좌 행을 뺐습니다(BTC 1개)" in m for m in LOGS), [m for m in LOGS if "빈 계좌" in m])
app._save_cfg(); flush()
ck("a 다음 저장 때 파일에도 BTC accounts = []", (ydisk()["assets"]["BTC"].get("accounts")) == [], ydisk()["assets"]["BTC"])
app = start()
ck("a 재시작해도 BTC 빈 목록(빈 행이 되살아나지 않음)", rows(app, "BTC") == [], rows(app, "BTC"))

# 남겨야 하는 행들(연결된 브로커 없음)
KEEP = [("계좌 ID 있음", row("150KTC-A", "", label="")),
        ("행 브로커 있음", row("", "bybit", label="Bybit (USDT perp)", one_r=500)),
        ("회원이 고친 라벨", row("", "", label="내 비트", one_r=500)),
        ("회원이 고친 1R", row("", "", label="Bybit (USDT perp)", one_r=150)),
        ("프롭 켬", row("", "", label="Bybit (USDT perp)", one_r=500, prop={"on": True, "type": "test"})),
        ("프롭 저장함", row("", "", label="Bybit (USDT perp)", one_r=500, prop={"on": False, "set": True})),
        ("한 계약 한도", row("", "", label="Bybit (USDT perp)", one_r=500, one_lim=300))]
for _nm, _r in KEEP:
    seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}},
                                   "accounts": [_r, blank("bybit")]}}, {EMAIL: PXKEY})
    app = start()
    _b = rows(app, "BTC")
    ck(f"a 남김: {_nm} 행은 그대로, 옆 빈 행만 빠짐", len(_b) == 1 and _b[0].get("id") == _r["id"] and _b[0].get("label") == _r["label"]
       and float(_b[0].get("one_r")) == float(_r["one_r"]), _b)
# 연결된 브로커가 있으면 빈 행 그대로(종전 동작)
seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": BYK, "f3": ""}},
                               "accounts": [blank("bybit")]}}, {EMAIL: PXKEY, BYK: "S1"})
app = start()
ck("a 남김: 연결된 브로커가 있는 자산의 빈 행(키를 넣은 Bybit로 매매 중일 수 있다)", len(rows(app, "BTC")) == 1, rows(app, "BTC"))
# PIN 보관분을 열지 못한 기동: 보이는 키가 전부가 아니다 - 빼지 않는다
seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}},
                               "accounts": [blank("bybit")]}}, {EMAIL: PXKEY},
     extra={"kc_enc": {"blob": "not-opened", "fields": []}})
PINS[:] = []
app = start()
ck("a 남김: PIN 보관분을 못 연 세션(_enc_locked)은 빈 행을 빼지 않음", app._enc_locked is True and len(rows(app, "BTC")) == 1,
   (app._enc_locked, rows(app, "BTC")))
# 키 저장 실패 목록(kc_failed)에 그 자산이 있으면 빼지 않음
seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}},
                               "accounts": [blank("bybit")]}}, {EMAIL: PXKEY},
     extra={"kc_failed": ["BTC/Bybit (USDT perp)/f1"]})
app = start()
ck("a 남김: 키 저장 실패 목록에 BTC가 있으면 빈 행을 빼지 않음", len(rows(app, "BTC")) == 1, rows(app, "BTC"))
# 키체인 읽기 자체가 실패한 기동(2026-11-10 리뷰 P2): 비어 보이는 API Key는 '없음'이 아니라 '못 읽음' - 빼지 않는다(종전엔 크립토 행이 지워졌다)
_kc_ok = eqgui._kc_load


def _kc_fail(acc):
    if str(acc).startswith("eq:BTC:"):
        eqgui._KC_READ_ERRS.add(acc)
        return ""
    return _kc_ok(acc)


seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}},
                               "accounts": [blank("bybit")]}}, {EMAIL: PXKEY})
eqgui._kc_load = _kc_fail
try:
    app = start()
finally:
    eqgui._kc_load = _kc_ok
ck("a 남김: 키체인 읽기가 실패한 자산(BTC API Key)은 빈 행을 빼지 않음 + 읽기 실패 기록",
   len(rows(app, "BTC")) == 1 and any(str(x).startswith("BTC/bybit/") for x in app._kc_read_failed), (rows(app, "BTC"), app._kc_read_failed))
eqgui._KC_READ_ERRS.clear()
seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}},
                               "accounts": [blank("bybit")]}}, {EMAIL: PXKEY})
app = start()
ck("a 뺌: 읽기는 됐고 값이 없으면(없음) 종전대로 빈 행을 뺀다", rows(app, "BTC") == [] and app._kc_read_failed == [], rows(app, "BTC"))
# 무장 중이면 빼지 않음(행 번호)
seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}},
                               "accounts": [blank("bybit")]}}, {EMAIL: PXKEY})
app = start()
rows(app, "BTC").append(eqgui._new_acct(500, "", True, "Bybit (USDT perp)"))
app._sig_accts = {("BTC", 0): {"broker": "bybit"}}
ck("a 남김: 무장된 행이 있는 자산은 빼지 않음", app._prune_blank_rows(["BTC"]) == [] and len(rows(app, "BTC")) == 1, rows(app, "BTC"))
app._sig_accts = {}

# ①: 옛 단일 계좌 설정(accounts 칸 없음)은 종전대로 계좌 1개, 계좌 칸이 비었으면 빈 목록
seed(None, raw={"lang": "ko", "token": "T", "assets": {
    "NQ": {"broker": "projectx", "creds": {"projectx": {"f1": EMAIL, "f3": "", "acct": "150KTC-A"}}, "one_r": 900},
    "GC": {"broker": "projectx", "creds": {"projectx": {"f1": EMAIL, "f3": ""}}, "accounts": []},
    "BTC": {"broker": "bybit", "creds": {"bybit": {"f1": "", "f3": ""}}, "accounts": []}}}, )
KC.update({EMAIL: PXKEY})
_d = eqgui._load()
ck("① 옛 단일 계좌 설정 = 계좌 1개(ID, 1R 그대로)", [(r["id"], r["one_r"]) for r in _d["assets"]["NQ"]["accounts"]] == [("150KTC-A", 900.0)],
   _d["assets"]["NQ"]["accounts"])
ck("① 계좌 칸이 빈 목록이면 빈 목록 그대로(빈 행 안 만듦)", _d["assets"]["GC"]["accounts"] == [] and _d["assets"]["BTC"]["accounts"] == [],
   (_d["assets"]["GC"]["accounts"], _d["assets"]["BTC"]["accounts"]))
ck("① _dedupe_accounts([]) = []", eqgui._dedupe_accounts([], "projectx") == [])

# ═════ 3: 삭제는 마지막 행까지 ═════
seed({"NQ": {"broker": "projectx", "include": False, "creds": PX_NQ, "accounts": [row("150KTC-A", "projectx")]}}, {EMAIL: PXKEY})
app = start()
app._on_asset("NQ"); flush()
ck("3 NQ 행 하나에 [삭제] 버튼", len(_del_buttons(app)) == 1, _acct_texts(app))
MSG.clear()
app._del_acct("NQ", 0); flush()
ck("3 마지막 행 삭제 → NQ 목록 비어 있음(빈 행 안 생김)", rows(app, "NQ") == [], rows(app, "NQ"))
ck("3 다시 그린 화면에도 행 없음([삭제] 버튼 0)", _del_buttons(app) == [], _acct_texts(app))
ck("3 파일에도 accounts = []", ydisk()["assets"]["NQ"].get("accounts") == [], ydisk()["assets"]["NQ"])
ck("3 연결이 있으니 '연결된 브로커가 없습니다' 줄은 없음", HINT_KO not in _acct_texts(app), _acct_texts(app))
_nx = str(app._next_lbl.cget("text"))
ck("3 '다음:' = [계좌 추가]로 계좌 등록", "[계좌 추가]로 계좌 이름을 등록하세요" in _nx, _nx)
app = start()
ck("3 재시작 뒤에도 NQ 빈 목록", rows(app, "NQ") == [], rows(app, "NQ"))
app._on_asset("NQ"); flush()
app._add_acct(); flush()
ck("3 연결이 있으면 [계좌 추가]는 종전대로 Topstep 빈 행 하나", [(r["id"], r["broker"]) for r in rows(app, "NQ")] == [("", "projectx")],
   rows(app, "NQ"))
# BTC 마지막 행도 [삭제]
seed({"BTC": {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": BYK, "f3": ""}}, "accounts": [row("", "bybit", one_r=150)]}},
     {BYK: "S1"})
app = start()
app._on_asset("BTC"); flush()
ck("3 BTC 행 하나에도 [삭제] 버튼(실행 체크는 숨김 그대로)", len(_del_buttons(app)) == 1, _acct_texts(app))
app._del_acct("BTC", 0); flush()
ck("3 BTC 마지막 행 삭제 → 빈 목록", rows(app, "BTC") == [], rows(app, "BTC"))
_nx = str(app._next_lbl.cget("text"))
ck("3 BTC 빈 목록 '다음:' = [계좌 추가]로 거래소 행", "[계좌 추가]로 거래소 행을 만드세요" in _nx, _nx)
# 사전점검: 실행 체크된 자산에 행이 없으면 '켜진 계좌 없음'(종전 문장)
_w = _golive(app, ["BTC"])
ck("3 BTC 체크 + 빈 목록 [라이브 시작] → 'BTC: 켜진 계좌 없음'으로 멈춤", not app._sig_accts and any("BTC: 켜진 계좌 없음" in m[-1] for m in _w),
   (_w, app._sig_accts))

# ═════ (c) BTC: 키를 넣어도 행이 저절로 생기지 않음 → [계좌 추가]가 그 거래소 행 ═════
seed({"NQ": OLD["NQ"]}, {EMAIL: PXKEY})
app = start()
ck("c 새 BTC 빈 목록", rows(app, "BTC") == [])
app._on_asset("BTC"); flush()
_enter_crypto(app, "bitget", BGK, "BGSECRET-12345", "BGPASS")
app._on_asset("BTC"); flush()
ck("c Bitget 키를 넣어도 BTC 행이 저절로 생기지 않음", rows(app, "BTC") == [], rows(app, "BTC"))
ck("c 연결이 생기면 '연결된 브로커가 없습니다' 줄은 사라짐", HINT_KO not in _acct_texts(app), _acct_texts(app))
_nx = str(app._next_lbl.cget("text"))
ck("c '다음:' = [계좌 추가]로 거래소 행", "[계좌 추가]로 거래소 행을 만드세요" in _nx, _nx)
MSG.clear()
app._add_acct(); flush()
ck("c [계좌 추가] → Bitget 행 하나(키를 넣은 거래소)", [(r["broker"], r["label"]) for r in rows(app, "BTC")] == [("bitget", "Bitget (USDT-F)")],
   rows(app, "BTC"))
ck("c [계좌 추가]에 '이 행이 그 거래소를 씁니다' 안내 없음", not any("이 행이 그 거래소를 씁니다" in m[-1] for m in MSG), MSG)
app._add_acct(); flush()
ck("c 다시 누르면 같은 거래소 행은 안 만듦(키 없는 거래소 안내)", len(rows(app, "BTC")) == 1
   and any("키를 넣은 거래소마다 행이 하나" in m[-1] for m in MSG if m[0] == "info"), (rows(app, "BTC"), MSG))
_w = _golive(app, ["NQ", "BTC"])
ck("c [라이브 시작 NQ+BTC] 통과, BTC = Bitget 무장", not _w and _armed_bk(app).get(("BTC", 0)) == "bitget", (_w, _armed_bk(app)))
app._master_stop(); flush()

# [계좌 추가]의 새 행 브로커 = 연결된 브로커(패널 브로커가 이 자산에서 연결 안 됐으면)
seed({"NQ": OLD["NQ"]}, {EMAIL: PXKEY})
app = start()
app._on_asset("GC"); flush()
app._broker_name = "ibkr"                       # 패널이 연결 안 된 IBKR을 보고 있다
app._add_acct(); flush()
ck("2 패널 브로커(IBKR)가 연결 안 됐으면 새 행은 연결된 Topstep", [r["broker"] for r in rows(app, "GC")] == ["projectx"], rows(app, "GC"))

# ═════ (b) [연결 지우기] ═════
# BTC Bybit 하나: 행이 빠지고 목록이 빈다. 확인창은 '라이브 시작이 멈춥니다' 대신 '계좌 설정에서 지웁니다'
for _lg in ("ko", "en"):
    seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": BYK, "f3": ""}},
                                   "accounts": [row("", "bybit", one_r=150, label="Bybit (USDT perp)")]}},
         {EMAIL: PXKEY, BYK: "S1"}, extra={"lang": _lg})
    app = start()
    app._on_asset("BTC"); flush()
    _pick(app, "bybit")
    MSG.clear(); LOGS.clear()
    ck(f"b[{_lg}] [연결 지우기] Bybit 반환 True", app._clear_broker_conn() is True)
    _q = [m[-1] for m in MSG if m[0] == "ask"]
    ck(f"b[{_lg}] 확인창: 그 거래소 행을 계좌 설정에서 지운다고 말함, '라이브 시작 전체가 멈춥니다'는 없음",
       _q and (("Bybit (USDT perp) 계좌 행(BTC 1개)은 계좌 설정에서 지웁니다." in _q[0]) if _lg == "ko" else
               ("The Bybit (USDT perp) account rows (BTC 1) are removed from Accounts." in _q[0]))
       and ("라이브 시작 전체가 멈춥니다" not in _q[0]) and ("Go Live stops" not in _q[0]), _q)
    ck(f"b[{_lg}] BTC 목록 비어 있음", rows(app, "BTC") == [], rows(app, "BTC"))
    ck(f"b[{_lg}] '연결된 브로커가 없습니다' 줄", (HINT_KO if _lg == "ko" else HINT_EN) in _acct_texts(app), _acct_texts(app))
    ck(f"b[{_lg}] 로그에 행도 지웠다는 말", any((("계좌 설정에서 이 연결의 행도 지웠습니다(BTC 1개)" in m) if _lg == "ko" else
                                                ("Its rows were removed from Accounts too (BTC 1)" in m)) for m in LOGS),
       [m for m in LOGS if "Bybit" in m or "연결" in m])
    ck(f"b[{_lg}] 파일에도 BTC accounts = []", ydisk()["assets"]["BTC"].get("accounts") == [], ydisk()["assets"]["BTC"])

# BTC 두 거래소: Bybit만 지우면 Bybit 행만 빠지고 Bitget 행은 그대로
seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True,
                               "creds": {"bybit": {"f1": BYK, "f3": ""}, "bitget": {"f1": BGK, "f3": "P"}},
                               "accounts": [row("", "bybit", one_r=150), row("", "bitget", one_r=250)]}},
     {EMAIL: PXKEY, BYK: "S1", BGK: "S2"})
app = start()
_pick(app, "bybit")
app._clear_broker_conn(confirm=False); flush()
ck("b 두 거래소 중 Bybit만 지움 → Bybit 행만 빠지고 Bitget 행(1R 250) 그대로",
   [(r["broker"], r["one_r"]) for r in rows(app, "BTC")] == [("bitget", 250.0)], rows(app, "BTC"))

# NQ, GC Topstep(공용) + NQ Lucid: Topstep을 지우면 계좌를 고른 행은 남고, 계좌를 고르지 않은 Topstep 행만 빠진다
seed({"NQ": {"broker": "projectx", "include": True,
             "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A"]}, "nt8": {"f1": "TOK-bridge-abcdef123456", "f3": "8377", "avail": ["LDI-1"]}},
             "accounts": [row("", "projectx", one_r=700), row("150KTC-A", "projectx", prop={"on": True, "type": "funded", "set": True}),
                          row("LDI-1", "nt8")]},
      "GC": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": ""}},
             "accounts": [row("", "", label="Topstep (ProjectX)")]}},
     {EMAIL: PXKEY})
app = start()
app._on_asset("NQ"); flush()
_pick(app, "projectx")
MSG.clear(); LOGS.clear()
ck("b Topstep [연결 지우기] 반환 True", app._clear_broker_conn() is True)
_q = [m[-1] for m in MSG if m[0] == "ask"]
ck("b 확인창: 계좌를 고르지 않은 Topstep 행(NQ 1개, GC 1개)을 지운다 + 남는 켜진 계좌는 종전 경고 그대로",
   _q and "계좌를 고르지 않은 Topstep 행(NQ 1개, GC 1개)은 계좌 설정에서 지웁니다." in _q[0]
   and "이 연결을 쓰는 켜진 계좌: NQ 1개" in _q[0] and "라이브 시작 전체가 멈춥니다" in _q[0], _q)
ck("b NQ: 계좌 ID 있는 Topstep 행(프롭 설정 포함)과 Lucid 행은 남고 빈 Topstep 행만 빠짐",
   [(r["id"], r["broker"]) for r in rows(app, "NQ")] == [("150KTC-A", "projectx"), ("LDI-1", "nt8")]
   and (rows(app, "NQ")[0].get("prop") or {}).get("on") is True, rows(app, "NQ"))
ck("b GC: 빈 Topstep 행 빠짐 → 빈 목록", rows(app, "GC") == [], rows(app, "GC"))
ck("b NQ는 Lucid가 연결돼 있어 '연결된 브로커가 없습니다' 줄 없음", HINT_KO not in _acct_texts(app), _acct_texts(app))

# 무장된 자산은 행을 빼지 않는다(행 번호 - 무장 사본 키가 어긋나지 않게)
seed({"NQ": {"broker": "projectx", "include": True,
             "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["150KTC-A"]}, "nt8": {"f1": "TOK-bridge-abcdef123456", "f3": "8377", "avail": ["LDI-1"]}},
             "accounts": [row("", "projectx"), row("LDI-1", "nt8")]},
      "GC": {"broker": "projectx", "include": False, "creds": {"projectx": {"f1": EMAIL, "f3": ""}}, "accounts": []}},
     {EMAIL: PXKEY})
app = start()
app._sig_accts = {("NQ", 1): {"broker": "nt8", "acct": "LDI-1", "live": True}}
_pick(app, "projectx")
ck("b NQ가 Lucid로 무장 중이어도 Topstep [연결 지우기]는 됨", app._clear_broker_conn(confirm=False) is True)
ck("b 무장된 자산(NQ)의 빈 Topstep 행은 그대로(행 번호 유지 - 무장 키 ('NQ', 1) = LDI-1)",
   [(r["id"], r["broker"]) for r in rows(app, "NQ")] == [("", "projectx"), ("LDI-1", "nt8")]
   and app._sig_accts.get(("NQ", 1), {}).get("acct") == "LDI-1", (rows(app, "NQ"), app._sig_accts))
app._sig_accts = {}

# 연결 테스트 중이면 행을 빼지 않는다(_drop_conn_rows 재확인)
seed({"NQ": OLD["NQ"], "BTC": {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": BYK, "f3": ""}},
                               "accounts": [row("", "bybit")]}}, {EMAIL: PXKEY, BYK: "S1"})
app = start()
_drop = app._conn_drop_rows("bybit", ["BTC"])
ck("b 뺄 행 고르기: BTC Bybit 행 하나", list(_drop) == ["BTC"] and len(_drop["BTC"]) == 1, _drop)
import time as _t  # noqa: E402
app._golive_tok = {"x": (_t.time() + 60, None)}
ck("b 연결 테스트 중이면 고르지 않음", app._conn_drop_rows("bybit", ["BTC"]) == {})
ck("b 연결 테스트 중이면 빼지 않음", app._drop_conn_rows("bybit", _drop) == {} and len(rows(app, "BTC")) == 1)
app._golive_tok = {}

# _row_pristine 단위
_P = eqgui.App._row_pristine
ck("a _row_pristine: 옛 빈 행(라벨 = 기본 이름, 1R 500/600) = True",
   _P("NQ", eqgui._new_acct(500, "", True, "Topstep (ProjectX)")) and _P("BTC", eqgui._new_acct(600, "", True, "Bybit (USDT perp)"))
   and _P("BTC", eqgui._new_acct(500, "", True, "")))
ck("a _row_pristine: ID, 브로커, 수동, 라벨, 1R, 프롭, 한도가 하나라도 있으면 False",
   not any(_P("NQ", x) for x in (eqgui._new_acct(500, "X1", True, ""), eqgui._new_acct(500, "", True, "", broker="projectx"),
                                eqgui._new_acct(500, "", True, "", manual=True), eqgui._new_acct(500, "", True, "my"),
                                eqgui._new_acct(450, "", True, ""), eqgui._new_acct(500, "", True, "", prop={"on": True}),
                                eqgui._new_acct(500, "", True, "", one_lim=200.0))))

print(f"\n{'OK' if not FAIL else 'FAIL'} - fails: {len(FAIL)}")
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
