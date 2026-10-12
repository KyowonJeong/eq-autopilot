#!/usr/bin/env python3
# EQ Autopilot - 브로커 키 섞임 오프라인 시험(2026-10-07 회원 실사례 하네스를 브로커 연결 공용 구조에 맞춰 다시 씀). 0=통과
# 실제 eqgui.App을 실제 Tk 위젯으로 띄운다(_build 포함). HOME = 임시 폴더(설정·로그·NT8 폴더 전부 그 안).
# 네트워크: socket 연결 전면 차단, 브로커는 가짜(_build_broker 대체). 키체인 = dict. 메시지박스 = 기록만.
# 스레드: 지정한 작업만 동기 실행. root.after: 0ms만 실행, 지연 타이머는 버림(자동 저장 등은 명시적으로 부른다).
# matrix: 시작 상태 3(회원 모양, Bridge Token 칸에 이메일, 포트가 다른 nt8 split) × 행동 9개의 1~2개 순열.
#   행동 = 탭 전환·패널 브로커 고르기(Lucid·Bybit·IBKR)·칸 비우기·브리지 버튼·BTC에서 Bitget 화면 보기·설정 가져오기·무장 중 편집·(옛 판) 복사·가져오기.
# trig: 화면 칸 브로커와 화면 브로커 이름이 갈린 상태를 직접 만든 뒤 저장 트리거 17가지.
# 섞임 판정(이번 구조 기준): Topstep 아이디에 토큰, Bridge Token에 이메일(메모리·키체인), YAML 평문 토큰, Topstep API Key 삭제,
#   NQ Topstep 이메일 사라짐, 드롭다운·화면 칸 브로커 어긋남, 패널 선택으로 자산·행 실행 브로커가 바뀜, BTC 행 실행 거래소 바뀜, nt8을 쓰는 자산의 토큰·포트가
#   설치된 .cs와 다름, 무장 사본(NQ)이 다음 신호를 건너뜀.
# 기대: 수리판 0. 회원판(d23796e5)과 일부러 고장 낸 새 코드에서는 0이 아니어야 한다(변이 검사로 확인).
import itertools
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_bm_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_SHARE", None)
os.makedirs(os.path.join(HOME, "Documents", "NinjaTrader 8", "bin", "Custom"), exist_ok=True)
NT8_CS = os.path.join(HOME, "Documents", "NinjaTrader 8", "bin", "Custom", "AddOns", "EQAutopilotBridge.cs")

import socket  # noqa: E402


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.socket.connect_ex = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk

import threading  # noqa: E402
RUN_SYNC = ("_async_load_key", "_master_start", "_autosave_fire", "_panel_conn_test",
            "_save_creds_and_test", "_autoload_nt8_avail", "_autoload_topstep_scope", "_run.<locals>.wrap")


class FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **k):
        self.target, self.args, self.kwargs = target, args, kwargs or {}
        self.name = name or "fake"

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
import secrets as _secrets  # noqa: E402
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
KC_DELS = []


def _kc_del_rec(acc):
    KC_DELS.append(acc)
    KC.pop(acc or "default", None)


eqgui._kc_del = _kc_del_rec
_TOKN = [0]


def _tok(n=18):
    _TOKN[0] += 1
    return f"BRGTOK{_TOKN[0]}"


_secrets.token_urlsafe = _tok
MSG = []
eqgui.messagebox.askyesno = lambda *a, **k: (MSG.append(("ask",) + tuple(str(x)[:160] for x in a)) or True)
eqgui.messagebox.showinfo = lambda *a, **k: MSG.append(("info",) + tuple(str(x)[:300] for x in a))
eqgui.messagebox.showwarning = lambda *a, **k: MSG.append(("warn",) + tuple(str(x)[:300] for x in a))
eqgui.messagebox.showerror = lambda *a, **k: MSG.append(("error",) + tuple(str(x)[:400] for x in a))
eqgui.simpledialog.askstring = lambda *a, **k: "1234"
TS_EMAIL, TS_KEY = "me@topstep.com", "TSKEY"
BRIDGE = {"token": None}
APP = []


class FakePX:
    name = "projectx"

    def __init__(self, f1, f2):
        self.f1, self.f2 = f1, f2

    def healthcheck(self):
        if (self.f1, self.f2) != (TS_EMAIL, TS_KEY):
            raise RuntimeError("ProjectX auth failed (errorCode=3): test")

    def _accounts(self):
        self.healthcheck()
        return [{"name": "S1TOPSTEP1", "id": 1, "balance": 50000, "canTrade": True}]

    def list_open_positions(self):
        return []


class FakeNT8:
    name = "nt8"

    def __init__(self, f1):
        self.f1 = f1

    def healthcheck(self):
        if not BRIDGE["token"]:
            raise RuntimeError("nt8 bridge not running (test)")
        if self.f1 != BRIDGE["token"]:
            raise RuntimeError("nt8 bridge 401 token mismatch (test)")

    def _accounts(self):
        self.healthcheck()
        return [{"name": "LTACC1"}]

    def _snapshot(self):
        return {"bridge_ver": APP[0]._bundled_bridge_ver() if APP else ""}

    def list_open_positions(self):
        return []


def _fake_build_broker(broker, f1, f2, f3, accounts):
    if broker == "projectx":
        return FakePX(f1, f2)
    if broker == "nt8":
        return FakeNT8(f1)
    raise RuntimeError(f"{broker} not in test")


eqgui._build_broker = _fake_build_broker
LOGS = []
_orig_log = eqgui.App.log


def _log(self, m):
    LOGS.append(str(m))
    try:
        _orig_log(self, m)
    except Exception:
        pass


eqgui.App.log = _log
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
_orig_cancel = ROOT.after_cancel
ROOT.after_cancel = lambda i: None if i == "dropped" else _orig_cancel(i)


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
    app = eqgui.App(ROOT)
    APP[:] = [app]
    app._gate = {"ok": True, "tier": "royal", "enabled": True, "force_dry_run": False,
                 "caps": {"use": True, "manualentry": True, "autoentry": True, "connect": True},
                 "brokers": {}, "reason": ""}
    try:
        app.consent.set(1)
    except Exception:
        pass
    flush()
    return app


def seed(assets, kc=None):
    with open(eqgui.CFG_PATH, "w") as f:
        yaml.safe_dump({"lang": "ko", "token": "MEMBERTOKEN", "assets": assets}, f)
    KC.clear()
    KC.update(kc or {})
    BRIDGE["token"] = None
    try:
        os.remove(NT8_CS)
    except OSError:
        pass


def row(id_, broker, one_r=600, label=""):
    return {"id": id_, "one_r": one_r, "on": True, "label": label, "broker": broker}


LBL = {b: eqgui._broker_label(b) for b in ("projectx", "nt8", "ibkr", "bybit", "bitget")}



def act(app, name):
    MSG.clear()
    n0 = len(LOGS)
    if name == "restart":
        app = start()
    elif name.startswith("tab"):
        app._on_asset(name[3:])
    elif name.startswith("pick:"):
        app.brokerbox.set(LBL[name[5:]])
        app._on_broker()
    elif name.startswith("panel:"):              # 브로커 연결 설정 패널에서 브로커를 고르고 칸 저장
        app.brokerbox.set(LBL[name[6:]])
        app._on_broker(); flush()
        app._autosave_fire()
    elif name == "btcview":                      # BTC 탭에서 위 콤보로 Bitget 키 화면을 보고 돌아온다
        app._on_asset("BTC"); flush()
        app.brokerbox.set(LBL["bitget"]); app._on_broker(); flush()
        app._on_asset("GC")
    elif name.startswith("copy:"):
        if hasattr(app, "_copy_acct_setup"):
            app._copy_acct_setup(name[5:])
    elif name == "pull":
        if hasattr(app, "_pull_broker_keys"):
            app._pull_broker_keys()
    elif name == "bridge":
        app._nt8_install_bridge()
        ARMED.pop("imported", None)
    elif name == "autosave":
        app._autosave_fire()
    elif name == "clear":
        st = str(app.user.cget("state"))
        app.user.config(state="normal")
        app.user.delete(0, "end")
        if st != "normal":
            app.user.config(state=st)
        app._autosave_fire()
    elif name == "import":
        make_import_file()
        app._import_settings()
        ARMED["imported"] = True                # 가져온 파일의 토큰은 설치된 .cs와 다를 수 있다(가져오기 안내 = 다시 시작·브리지)
        BASE["exec"] = {a: (app._acfg[a].get("broker"), tuple(app._acct_broker(a, r) for r in (app._acfg[a].get("accounts") or [])))
                        for a in ("NQ", "GC", "BTC")}   # 가져오기는 실행 브로커를 바꾸는 정당한 경로 - 새 기준
    elif name == "armedit":                      # NQ 무장 중 지금 탭 Topstep 칸을 다른 값으로 고침
        ARMED["cfg"] = {"broker": "projectx", "f1": TS_EMAIL, "f3": "", "acct": "S1TOPSTEP1", "one_r": 600.0,
                        "prop": {}, "pct": {}, "one_lim": 0.0, "manual": False, "live": True, "label": "Topstep"}
        app._sig_accts[("NQ", 0)] = dict(ARMED["cfg"])
        if app._broker_name == "projectx":
            st = str(app.user.cget("state"))
            app.user.config(state="normal")
            app.user.delete(0, "end")
            app.user.insert(0, "other@topstep.com")
            if st != "normal":
                app.user.config(state=st)
            app._autosave_fire()
    elif name == "nt8load":
        if hasattr(app, "_scope_cache"):
            app._scope_cache.clear()
        app._autoload_nt8_avail(app._asset)
    elif name == "live":
        app._master_start()
    else:
        raise ValueError(name)
    flush()
    return app, LOGS[n0:], list(MSG)


def _yaml():
    try:
        return yaml.safe_load(open(eqgui.CFG_PATH)) or {}
    except Exception:
        return {}


def _cs():
    try:
        txt = open(NT8_CS, encoding="utf-8").read()
    except Exception:
        return None, None
    m = re.search(r'private const string Token\s*=\s*"([^"]*)"', txt)
    p = re.search(r"127\.0\.0\.1:(\d+)", txt)
    return (m.group(1) if m else None), (p.group(1) if p else None)


def anomalies(app):
    out = []
    cur = app._asset
    if KC_DELS:                                  # 키체인 삭제는 [연결 지우기] 하나뿐 - 이 행렬엔 그 행동이 없다(재리뷰 P3)
        out.append(f"키체인 삭제 {len(KC_DELS)}건")
    if app.brokerbox.get() != LBL.get(app._broker_name):
        out.append("드롭다운≠화면 브로커")
    # 패널 분리 뒤(2026-10-07): 패널 브로커를 무엇으로 골라도 자산·행 실행 브로커는 바뀌면 안 된다(가져오기 뒤는 새 기준)
    _now = {a: (app._acfg[a].get("broker"), tuple(app._acct_broker(a, r) for r in (app._acfg[a].get("accounts") or [])))
            for a in ("NQ", "GC", "BTC")}
    if BASE.get("exec") is not None and _now != BASE["exec"]:
        out.append("자산·행 실행 브로커 바뀜")
    if getattr(app, "_fields_bk", app._broker_name) != app._broker_name:
        out.append("화면 칸 브로커≠패널 브로커")
    y = _yaml()
    raw = open(eqgui.CFG_PATH).read() if os.path.exists(eqgui.CFG_PATH) else ""
    for t in TOKENS:
        if t in raw:
            out.append("YAML 평문 토큰")
    for a in ("NQ", "GC"):
        cr = app._acfg[a].get("creds") or {}
        px = str((cr.get("projectx") or {}).get("f1") or "")
        n8 = str((cr.get("nt8") or {}).get("f1") or "")
        if px in TOKENS or px.startswith("BRGTOK"):
            out.append(f"{a} Topstep 아이디=토큰")
        if "@" in n8:
            out.append(f"{a} Bridge Token=이메일")
        if "@" in str(KC.get(f"eq:{a}:nt8:f1") or "") and KC.get(f"eq:{a}:nt8:f1") != START_KC.get(f"eq:{a}:nt8:f1"):
            out.append(f"키체인 eq:{a}:nt8:f1에 새로 이메일")
        ycr = (((y.get("assets") or {}).get(a) or {}).get("creds") or {})
        if str((ycr.get("projectx") or {}).get("f1") or "") in TOKENS:
            out.append(f"{a} YAML Topstep 아이디=토큰")
    if str(((app._acfg["NQ"].get("creds") or {}).get("projectx") or {}).get("f1") or "") != TS_EMAIL:
        out.append("NQ Topstep 이메일 사라짐")
    if KC.get(TS_EMAIL) != TS_KEY:
        out.append("Topstep API Key(f2) 삭제")
    for i, r in enumerate(app._acfg["BTC"].get("accounts") or []):
        if app._acct_broker("BTC", r) != BTC_ROW0.get(i, "bybit"):
            out.append("BTC 행 실행 거래소 바뀜")
    ctok, cport = _cs()
    if ctok and not ARMED.get("imported"):
        for a in ("NQ", "GC"):
            if any(app._acct_broker(a, r) == "nt8" for r in (app._acfg[a].get("accounts") or [])):
                c8 = (app._acfg[a].get("creds") or {}).get("nt8") or {}
                if str(c8.get("f1") or "") != ctok:
                    out.append(f"{a} 토큰≠.cs")
                if str(c8.get("f3") or "8377") != str(cport or "8377"):
                    out.append(f"{a} 포트≠.cs")
    if ARMED.get("cfg") and hasattr(app, "_live_acct_cfg"):
        try:
            _c, _why = app._live_acct_cfg(("NQ", 0), dict(ARMED["cfg"]))
            if _c is None:
                out.append("NQ 무장 사본이 다음 신호를 건너뜀")
        except Exception:
            pass
    return out


BASE = {}
TOKENS = {"TOKX-000000000000", "TOK1-111111111111", "TOK2-222222222222"}
ARMED = {}
BTC_ROW0 = {}
START_KC = {}
NQ0 = {"broker": "projectx", "include": True,
       "creds": {"projectx": {"f1": TS_EMAIL, "f3": "", "avail": ["S1TOPSTEP1"]},
                 "nt8": {"f1": "", "f3": "8390", "avail": ["LTACC1"]}},
       "accounts": [row("S1TOPSTEP1", "projectx", 600, "Topstep"), row("LTACC1", "nt8", 600, "Lucid")]}
GC0 = {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": "", "f3": ""}},
       "accounts": [row("", "", 600, "")]}
BTC0 = {"broker": "bybit", "include": True,
        "creds": {"bybit": {"f1": "", "f3": ""}, "bitget": {"f1": "", "f3": ""}},
        "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": ""}]}
BTCKC = {"eq:BTC:bybit:f1": "BYBITKEY-123456789012", "BYBITKEY-123456789012": "BS",
         "eq:BTC:bitget:f1": "BITGETKEY-1234567890"}
STATES = {
    # 회원 모양: NQ = Topstep + Lucid(포트 8390), GC는 손대지 않음, BTC Bybit(+Bitget 키)
    "member": ({"NQ": NQ0, "GC": GC0, "BTC": BTC0},
               dict({TS_EMAIL: TS_KEY, "eq:NQ:nt8:f1": "TOKX-000000000000"}, **BTCKC)),
    # 옛 판이 남긴 섞임: GC Bridge Token 칸(키체인)에 Topstep 이메일, GC는 Lucid 행
    "gc_nt8_email": ({"NQ": NQ0, "GC": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": ""}},
                                      "accounts": [row("LTACC1", "nt8", 600, "")]}, "BTC": BTC0},
                     dict({TS_EMAIL: TS_KEY, "eq:NQ:nt8:f1": "TOKX-000000000000", "eq:GC:nt8:f1": TS_EMAIL}, **BTCKC)),
    # 회원 실사례 모양(재리뷰 P3): NQ 자산 브로커가 nt8, Topstep 행과 Lucid 행
    "member_lit": ({"NQ": dict(NQ0, broker="nt8", creds={"projectx": NQ0["creds"]["projectx"],
                                                        "nt8": {"f1": "", "f3": "", "avail": ["LTACC1"]}}),
                    "GC": GC0, "BTC": BTC0},
                   dict({TS_EMAIL: TS_KEY, "eq:NQ:nt8:f1": "TOKX-000000000000"}, **BTCKC)),
    # 자산마다 다른 토큰·포트(nt8 split), 둘 다 Lucid 행
    "nt8_split": ({"NQ": dict(NQ0, creds={"projectx": NQ0["creds"]["projectx"], "nt8": {"f1": "", "f3": "8390", "avail": []}}),
                   "GC": {"broker": "nt8", "include": True, "creds": {"nt8": {"f1": "", "f3": "8377"}},
                          "accounts": [row("LTACC2", "nt8", 600, "")]}, "BTC": BTC0},
                  dict({TS_EMAIL: TS_KEY, "eq:NQ:nt8:f1": "TOK1-111111111111", "eq:GC:nt8:f1": "TOK2-222222222222"}, **BTCKC)),
}

import tkinter.filedialog as fd  # noqa: E402
EXPORT = os.path.join(HOME, "eq.eqset")
fd.asksaveasfilename = lambda *a, **k: EXPORT
fd.askopenfilename = lambda *a, **k: EXPORT


def make_import_file():
    import copy
    payload = {"kind": "eq-autopilot-settings", "v": 1, "app": "x", "lang": "ko", "token": "MEMBERTOKEN",
               "dry_run": True, "profile": {}, "assets": {
                   "NQ": copy.deepcopy(NQ0),
                   "GC": {"broker": "nt8", "include": True,
                          "creds": {"projectx": {"f1": TS_EMAIL, "f3": ""}, "nt8": {"f1": "TOKX-000000000000", "f3": "8390"}},
                          "accounts": [row("LTACC1", "nt8", 600, "")]},
                   "BTC": copy.deepcopy(BTC0)}}
    payload["assets"]["NQ"]["creds"]["nt8"]["f1"] = "TOKX-000000000000"
    with open(EXPORT, "wb") as f:
        f.write(eqgui._settings_export_blob("1234", payload))


def boot(state):
    assets, kc = STATES[state]
    seed(assets, kc)
    START_KC.clear(); START_KC.update(kc)
    ARMED.clear()
    BTC_ROW0.clear()
    KC_DELS.clear()
    _TOKN[0] = 0
    app = start()
    for i, r in enumerate(app._acfg["BTC"].get("accounts") or []):
        BTC_ROW0[i] = app._acct_broker("BTC", r)
    BASE["exec"] = {a: (app._acfg[a].get("broker"), tuple(app._acct_broker(a, r) for r in (app._acfg[a].get("accounts") or [])))
                    for a in ("NQ", "GC", "BTC")}
    return app


FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


ACTS = ["tabGC", "pick:nt8", "clear", "bridge", "btcview", "import", "armedit", "panel:bybit", "panel:ibkr", "copy:NQ", "pull"]
SEQS = [[a] for a in ACTS] + [list(p) for p in itertools.permutations(ACTS, 2)]
bad_m, n_m = [], 0
for st in STATES:
    for sq in SEQS:
        n_m += 1
        app = boot(st)
        app, *_ = act(app, "tabGC")
        hits = []
        for a in sq:
            app, *_ = act(app, a)
            if a == "armedit":
                app._sig_accts.clear()
                hits += [f"{a}:{x}" for x in anomalies(app)]
                ARMED.pop("cfg", None)
            else:
                hits += [f"{a}:{x}" for x in anomalies(app)]
        if hits:
            bad_m.append(f"{st}|{'>'.join(sq)}: {'; '.join(dict.fromkeys(hits))[:240]}")
ck(f"matrix {n_m}순서 섞임 0", not bad_m, f"{len(bad_m)}/{n_m}: " + " || ".join(bad_m[:4]))
_n_valid = sum(1 for sq in SEQS if not any(a in ("copy:NQ", "pull") for a in sq)) * len(STATES)
print(f"matrix 섞임 {len(bad_m)}/{n_m} (이 판에 있는 행동만으로 된 순서 {_n_valid}개 - 복사·가져오기는 없어진 버튼이라 건너뜀)")

# ── trig: 화면 칸은 Topstep(projectx)인데 화면 브로커 이름만 nt8(옛 판 복사가 만들던 어긋남) → 저장 트리거 17가지 ──


def trig(app, t):
    MSG.clear()
    if t == "setlang":
        app.langbox.set("English")
        app._set_lang()
    elif t == "savetoken":
        app._save_token()
    elif t == "healthcheck":
        app.healthcheck()
    elif t == "panelconn":
        app._panel_conn_test(app._asset)
    elif t == "export":
        app._export_settings()
    elif t == "autopush":
        eqgui._last_pushed = lambda: 0.0
        app._autopush_tick()
    elif t == "addacct":
        app._add_acct()                          # 회원이 누른 [계좌 추가]는 빈 슬롯 브로커를 바꿀 수 있다(1b 반박 리뷰 P1·P2-3) - 새 기준
        BASE["exec"] = {a: (app._acfg[a].get("broker"), tuple(app._acct_broker(a, r) for r in (app._acfg[a].get("accounts") or [])))
                        for a in ("NQ", "GC", "BTC")}
    elif t == "none":
        pass
    else:
        app, _, _ = act(app, t)
        return app
    flush()
    return app


TRIGS = ["none", "tabNQ", "pick:nt8", "pick:projectx", "autosave", "setlang", "savetoken", "healthcheck",
         "panelconn", "export", "live", "autopush", "addacct", "clear", "bridge", "restart", "import"]
bad_t = []
for t in TRIGS:
    app = boot("member")
    app, *_ = act(app, "tabGC")
    app._broker_name = "nt8"                     # 화면 칸(Topstep 이메일)과 화면 브로커 이름이 갈림
    st = str(app.user.cget("state"))
    app.user.config(state="normal"); app.user.delete(0, "end"); app.user.insert(0, TS_EMAIL)
    if st != "normal":
        app.user.config(state=st)
    app = trig(app, t)
    flush()                                      # 하네스가 직접 다시 그리지 않는다 - 칸 출처 가드의 '다음 틱 맞춤'(after 0)을 그대로 본다(재리뷰 P3)
    if t == "none":
        app._sync_tab_broker(); app._build(); flush()   # 저장이 안 일어나는 트리거만 하네스가 맞춘다
    an = [x for x in anomalies(app)]
    if an:
        bad_t.append(f"{t}: {'|'.join(an)}")
ck(f"trig {len(TRIGS)}트리거 섞임 0", not bad_t, f"{len(bad_t)}/{len(TRIGS)}: " + " || ".join(bad_t[:4]))
print(f"trig 섞임 {len(bad_t)}/{len(TRIGS)}")

try:
    ROOT.destroy()
except Exception:
    pass
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
