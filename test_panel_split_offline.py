#!/usr/bin/env python3
# EQ Autopilot - 브로커 연결 설정 패널 split·값 위치 행렬(1b 반박 리뷰 2026-10-08 하네스 이식: s4·s4b·s6). 0=통과
# 기대: Topstep split 0/1156, Lucid split 0/784, 값 위치 0. 리뷰 판(7c9381f3)은 Topstep split·s4b에서 섞임이 났다.
# 실제 eqgui.App을 실제 Tk 위젯으로 띄운다. HOME = 임시 폴더, 네트워크 차단, 키체인 = dict, 메시지박스 = 기록.
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_1b_")
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
RUN_SYNC = ("_async_load_key", "_master_start", "_autosave_fire", "_panel_conn_test", "_load_accounts_for_asset",
            "_live_arm_assets", "_save_creds_and_test", "_autoload_nt8_avail", "_autoload_topstep_scope", "_run.<locals>.wrap")


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


SRC = os.environ.get("SRC") or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)
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
eqgui._kc_del = lambda acc: KC.pop(acc or "default", None)
_TOKN = [0]


def _tok(n=18):
    _TOKN[0] += 1
    return f"GENTOK{_TOKN[0]:018d}"


_secrets.token_urlsafe = _tok
MSG = []
eqgui.messagebox.askyesno = lambda *a, **k: (MSG.append(("ask",) + tuple(str(x)[:400] for x in a)) or True)
eqgui.messagebox.showinfo = lambda *a, **k: MSG.append(("info",) + tuple(str(x)[:400] for x in a))
eqgui.messagebox.showwarning = lambda *a, **k: MSG.append(("warn",) + tuple(str(x)[:400] for x in a))
eqgui.messagebox.showerror = lambda *a, **k: MSG.append(("error",) + tuple(str(x)[:400] for x in a))
eqgui.simpledialog.askstring = lambda *a, **k: "1234"
# 로그인 표: (이메일, 키) → 계좌 목록
PX_LOGINS = {}
BRIDGE = {"token": None, "accounts": ["LTACC1"]}
BUILDS = []


class FakePX:
    name = "projectx"

    def __init__(self, f1, f2):
        self.f1, self.f2 = f1, f2

    def healthcheck(self):
        if PX_LOGINS.get(self.f1, {}).get("key") != self.f2 or not self.f2:
            raise RuntimeError("ProjectX auth failed (errorCode=3): test")

    def _accounts(self):
        self.healthcheck()
        return [{"name": n, "id": i + 1, "balance": 50000, "canTrade": True}
                for i, n in enumerate(PX_LOGINS[self.f1]["accts"])]

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
        return [{"name": n} for n in BRIDGE["accounts"]]

    def _snapshot(self):
        return {"bridge_ver": ""}

    def list_open_positions(self):
        return []


class FakeCrypto:
    def __init__(self, b):
        self.name = b

    def healthcheck(self):
        return None

    def list_open_positions(self):
        return []


def _fake_build_broker(broker, f1, f2, f3, accounts):
    BUILDS.append((broker, f1))
    if broker == "projectx":
        return FakePX(f1, f2)
    if broker == "nt8":
        return FakeNT8(f1)
    if broker in ("bybit", "bitget"):
        return FakeCrypto(broker)
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

import tkinter.filedialog as fd  # noqa: E402
EXPORT = os.path.join(HOME, "eq.eqset")
fd.asksaveasfilename = lambda *a, **k: EXPORT
fd.askopenfilename = lambda *a, **k: EXPORT
fd.askdirectory = lambda *a, **k: ""


def flush():
    for _ in range(3):
        ROOT.update()


GATE = {"ok": True, "tier": "royal", "enabled": True, "force_dry_run": False,
        "caps": {"use": True, "manualentry": True, "autoentry": True, "connect": True},
        "brokers": {}, "reason": ""}


def start():
    for w in list(ROOT.winfo_children()):
        try:
            w.destroy()
        except Exception:
            pass
    try:
        eqgui._ENC_SECRETS.clear()
    except Exception:
        pass
    app = eqgui.App(ROOT)
    app._gate = dict(GATE)
    try:
        app.consent.set(1)
    except Exception:
        pass
    flush()
    return app


def seed(assets=None, kc=None, raw=None):
    if raw is not None or assets is not None:
        with open(eqgui.CFG_PATH, "w") as f:
            yaml.safe_dump(raw if raw is not None else {"lang": "ko", "token": "MEMBERTOKEN", "assets": assets}, f)
    else:
        try:
            os.remove(eqgui.CFG_PATH)
        except OSError:
            pass
    KC.clear()
    KC.update(kc or {})
    BRIDGE["token"] = None
    try:
        os.remove(NT8_CS)
    except OSError:
        pass


def row(id_, broker, one_r=600, label="", on=True):
    return {"id": id_, "one_r": one_r, "on": on, "label": label, "broker": broker}


LBL = {b: eqgui._broker_label(b) for b in ("projectx", "nt8", "ibkr", "bybit", "bitget")}


def pick(app, b):
    app.brokerbox.set(LBL[b])
    app._on_broker()
    flush()


def type_into(w, s):
    st = str(w.cget("state"))
    w.config(state="normal")
    w.delete(0, "end")
    w.insert(0, s)
    if st != "normal":
        w.config(state=st)


def texts(w):
    out = []

    def _walk(x):
        for c in x.winfo_children():
            try:
                out.append((c.winfo_class(), str(c.cget("text"))))
            except Exception:
                pass
            _walk(c)
    _walk(w)
    return out


def yml():
    try:
        return yaml.safe_load(open(eqgui.CFG_PATH)) or {}
    except Exception:
        return {}


def cs():
    try:
        txt = open(NT8_CS, encoding="utf-8").read()
    except Exception:
        return None, None
    m = re.search(r'private const string Token\s*=\s*"([^"]*)"', txt)
    p = re.search(r"127\.0\.0\.1:(\d+)", txt)
    return (m.group(1) if m else None), (p.group(1) if p else None)


FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:600]}"))
    if not c:
        FAIL.append(n)
    return c


def logs_since(n0):
    return "\n".join(LOGS[n0:])

import itertools  # noqa: E402


def run_s4():
    # S4: Topstep split(NQ a@x.com·KA / GC b@x.com·KB, 둘 다 유효) - 패널 자산과 계좌 탭 자산이 다를 때의 섞임 행렬.
    # 행동 1~2개 순열 × 시작(패널 NQ·GC) × 계좌 탭(NQ·GC). 판정: 로그인·키·가용 계좌·행 콤보·YAML·화면 칸 출처.

    A, KA, B, KB, N = "a@x.com", "KA-KEY", "b@x.com", "KB-KEY", "n@x.com"
    TOKEN = "TOKN-ABCDEFGHIJKLMNOP"
    NQ = {"broker": "projectx", "include": True,
          "creds": {"projectx": {"f1": A, "f3": "", "avail": ["A1"]}, "nt8": {"f1": "", "f3": "", "avail": []}},
          "accounts": [row("A1", "projectx", 600, "A1")]}
    GC = {"broker": "projectx", "include": True,
          "creds": {"projectx": {"f1": B, "f3": "", "avail": ["B1"]}},
          "accounts": [row("B1", "projectx", 600, "B1")]}
    BTC = {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "", "f3": ""}},
           "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": "bybit"}]}
    KC0 = {A: KA, B: KB, "eq:NQ:nt8:f1": TOKEN}
    ST = {}


    def boot(panel_asset, tab):
        PX_LOGINS.clear()
        PX_LOGINS[A] = {"key": KA, "accts": ["A1"]}
        PX_LOGINS[B] = {"key": KB, "accts": ["B1"]}
        seed({"NQ": NQ, "GC": GC, "BTC": BTC}, dict(KC0))
        BRIDGE["token"] = TOKEN
        app = start()
        assert "projectx" in app._creds_split, app._creds_split
        if tab != "NQ":
            app._on_asset(tab); flush()
        if panel_asset == "GC":
            app._on_panel_asset("GC"); flush()
        ST.clear(); ST.update(typed=None, cleared=None)
        ST["exec"] = {a: (app._acfg[a]["broker"], tuple(app._acct_broker(a, r) for r in app._acfg[a]["accounts"])) for a in ("NQ", "GC", "BTC")}
        return app


    def act(app, a):
        MSG.clear()
        if a.startswith("tab"):
            app._on_asset(a[3:])
        elif a.startswith("fix"):
            app._on_panel_asset(a[3:])
        elif a.startswith("pick:"):
            pick(app, a[5:])
        elif a == "type":                          # 칸에 새 아이디를 치고(저장 전) 다음 행동이 저장을 일으킨다
            if app._broker_name == "projectx":
                ST["typed"] = app._panel_asset
                type_into(app.user, N)
        elif a == "autosave":
            app._autosave_fire()
        elif a == "load":
            app._scope_cache = {}
            app.b_acc.invoke()
        elif a == "lang":
            app.langbox.set("English" if app.lang == "ko" else "한국어"); app._set_lang()
        elif a == "conntest":
            app._save_creds_and_test()
        elif a == "panelconn":
            app._panel_conn_test(app._asset)
        elif a == "restart":
            app = start()
        elif a == "exportimport":
            app._export_settings(); flush()
            app._import_settings()
        elif a.startswith("clear"):
            if app._broker_name == "projectx" and "projectx" in app._creds_split:
                ST["cleared"] = a[5:]
                app._clear_broker_conn(asset=a[5:])
        elif a == "bridge":
            app._nt8_install_bridge()
        else:
            raise ValueError(a)
        flush()
        return app


    def anomalies(app):
        out = []
        cr = {a: (app._acfg[a].get("creds") or {}).get("projectx") or {} for a in ("NQ", "GC")}
        cl = ST.get("cleared")
        ok = {"NQ": {A}, "GC": {B}}
        if ST.get("typed"):
            ok[ST["typed"]].add(N)
        if cl:
            other = "GC" if cl == "NQ" else "NQ"
            ok[cl] = set(ok[other])
        for a in ("NQ", "GC"):
            if str(cr[a].get("f1") or "") not in ok[a]:
                out.append(f"{a} Topstep 아이디={cr[a].get('f1')}")
        if not cl and cr["NQ"] is cr["GC"]:
            out.append("split인데 같은 dict")
        if KC.get(A) != KA and cl != "NQ":
            out.append("KA 바뀜/지움")
        if KC.get(B) != KB and cl != "GC":
            out.append("KB 바뀜/지움")
        # 가용 계좌는 그 로그인의 계좌만
        accts_of = {A: {"A1"}, B: {"B1"}, N: set()}
        for a in ("NQ", "GC"):
            f1 = str(cr[a].get("f1") or "")
            bad = [x for x in (cr[a].get("avail") or []) if x not in accts_of.get(f1, set())]
            if f1 == N:
                bad = []                          # 아이디를 바꾼 직후 옛 목록이 남는 것은 종전 동작(새 목록이 오면 교체) - 이 리뷰 범위 밖
            if bad and not cl:
                out.append(f"{a} 가용 계좌에 남의 계좌 {bad}")
        # 지금 계좌 탭 행 콤보 = 그 자산 avail
        t = app._asset
        if t in ("NQ", "GC"):
            av = set((app._acfg[t]["creds"].get("projectx") or {}).get("avail") or [])
            for ix, w in getattr(app, "_acct_widgets", {}).items():
                cb = w.get("acct_id")
                if cb is None:
                    continue
                vals = set(cb.cget("values") or ())
                if vals - av:
                    out.append(f"{t} 행 콤보에 {sorted(vals - av)}")
        y = yml()
        for a in ("NQ", "GC"):
            yf = (((((y.get("assets") or {}).get(a) or {}).get("creds") or {}).get("projectx")) or {}).get("f1")
            if yf is not None and str(yf) != str(cr[a].get("f1") or ""):
                out.append(f"YAML {a}≠메모리")
        # 화면 칸 출처
        pa = eqgui._panel_asset_of(app)
        if getattr(app, "_fields_pa", pa) != pa:
            out.append("칸 자산≠패널 자산")
        if app._broker_name == "projectx":
            if app.user.get() != str(app._creds_of(pa, "projectx").get("f1") or "") and not (ST.get("typed") and app.user.get() == N):
                out.append(f"칸 값({app.user.get()})≠패널 자산({pa}) 아이디")
        if app.brokerbox.get() != LBL.get(app._broker_name):
            out.append("드롭다운≠패널 브로커")
        ex = {a: (app._acfg[a]["broker"], tuple(app._acct_broker(a, r) for r in app._acfg[a]["accounts"])) for a in ("NQ", "GC", "BTC")}
        if ex != ST["exec"]:
            out.append("실행 브로커 바뀜")
        return out


    ACTS = ["tabNQ", "tabGC", "tabBTC", "fixNQ", "fixGC", "pick:nt8", "pick:bybit", "type", "autosave", "load", "lang",
            "conntest", "panelconn", "restart", "exportimport", "clearNQ", "clearGC"]
    SEQS = [[a] for a in ACTS] + [list(p) for p in itertools.permutations(ACTS, 2)]
    bad, n = [], 0
    for pa in ("NQ", "GC"):
        for tab in ("NQ", "GC"):
            for sq in SEQS:
                n += 1
                app = boot(pa, tab)
                hits = []
                for a in sq:
                    try:
                        app = act(app, a)
                    except Exception as e:
                        hits.append(f"{a}:EXC {type(e).__name__}: {e}")
                        break
                    hits += [f"{a}:{x}" for x in anomalies(app)]
                if hits:
                    bad.append(f"P{pa}/T{tab}|{'>'.join(sq)}: {'; '.join(dict.fromkeys(hits))[:300]}")
    print(f"split matrix 섞임 {len(bad)}/{n}")
    for b in bad[:60]:
        print("  ", b)

    return len(bad), n, bad


def run_s4b():
    # S4b: Lucid(nt8) split(NQ 토큰 T1·포트 8390 / GC 토큰 T2·포트 8377, 설치된 .cs 없음) - 패널 자산·계좌 탭·브리지 버튼 행렬.

    T1, T2 = "TOKA-1111111111111111", "TOKB-2222222222222222"
    NQ = {"broker": "nt8", "include": True,
          "creds": {"nt8": {"f1": "", "f3": "8390", "avail": ["L1"]}},
          "accounts": [row("L1", "nt8", 600, "L1")]}
    GC = {"broker": "nt8", "include": True,
          "creds": {"nt8": {"f1": "", "f3": "8377", "avail": ["L2"]}},
          "accounts": [row("L2", "nt8", 600, "L2")]}
    BTC = {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "", "f3": ""}},
           "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": "bybit"}]}
    KC0 = {"eq:NQ:nt8:f1": T1, "eq:GC:nt8:f1": T2}
    ST = {}


    def boot(pa, tab):
        seed({"NQ": NQ, "GC": GC, "BTC": BTC}, dict(KC0))
        app = start()
        assert "nt8" in app._creds_split, app._creds_split
        pick(app, "nt8")
        if tab != "NQ":
            app._on_asset(tab); flush()
        if pa == "GC":
            app._on_panel_asset("GC"); flush()
        ST.clear(); ST.update(bridged=None, cleared=None)
        return app


    def act(app, a):
        MSG.clear()
        if a.startswith("tab"):
            app._on_asset(a[3:])
        elif a.startswith("fix"):
            app._on_panel_asset(a[3:])
        elif a.startswith("pick:"):
            pick(app, a[5:])
        elif a == "port":                         # 패널 칸의 Bridge Port를 9000으로(저장 전)
            if app._broker_name == "nt8" and hasattr(app, "f3"):
                ST.setdefault("port", set()).add(eqgui._panel_asset_of(app))
                type_into(app.f3, "9000")
        elif a == "autosave":
            app._autosave_fire()
        elif a == "load":
            app._scope_cache = {}
            app.b_acc.invoke()
        elif a == "lang":
            app.langbox.set("English" if app.lang == "ko" else "한국어"); app._set_lang()
        elif a == "restart":
            app = start()
        elif a == "exportimport":
            app._export_settings(); flush()
            app._import_settings()
        elif a == "bridge":
            app._nt8_install_bridge()
            ST["bridged"] = cs()
        elif a.startswith("clear"):
            if app._broker_name == "nt8" and "nt8" in app._creds_split:
                ST["cleared"] = a[5:]
                app._clear_broker_conn(asset=a[5:])
        else:
            raise ValueError(a)
        flush()
        return app


    def anomalies(app):
        out = []
        c = {a: (app._acfg[a].get("creds") or {}).get("nt8") or {} for a in ("NQ", "GC")}
        exp = {"NQ": ({T1}, {"8390"}), "GC": ({T2}, {"8377"})}
        for a in ST.get("port", ()):
            exp[a][1].add("9000")
        if ST.get("bridged") and ST["bridged"][0]:
            tok, prt = ST["bridged"]
            exp = {"NQ": ({tok}, {prt}), "GC": ({tok}, {prt})}
        cl = ST.get("cleared")
        if cl and not ST.get("bridged"):
            o = "GC" if cl == "NQ" else "NQ"
            exp[cl] = exp[o]
        for a in ("NQ", "GC"):
            if str(c[a].get("f1") or "") not in exp[a][0]:
                out.append(f"{a} 토큰={c[a].get('f1')}")
            if str(c[a].get("f3") or "8377") not in exp[a][1] and not ST.get("bridged"):
                out.append(f"{a} 포트={c[a].get('f3')}")
            kc = KC.get(f"eq:{a}:nt8:f1")
            if kc is not None and kc != str(c[a].get("f1") or "") and str(c[a].get("f1") or ""):
                out.append(f"키체인 eq:{a}:nt8:f1≠메모리")
        pa = eqgui._panel_asset_of(app)
        if getattr(app, "_fields_pa", pa) != pa:
            out.append("칸 자산≠패널 자산")
        if app._broker_name == "nt8" and hasattr(app, "f3"):
            shown = app.f3.get()
            want = str(app._creds_of(pa, "nt8").get("f3") or "")
            if shown != want and not (shown == "9000"):
                out.append(f"포트 칸({shown})≠{pa} 포트({want})")
        if ST.get("bridged") and ST["bridged"][0]:
            tok, prt = ST["bridged"]
            for a in ("NQ", "GC"):
                if str(c[a].get("f1") or "") != tok or str(c[a].get("f3") or "8377") != str(prt or "8377"):
                    out.append(f"{a} ≠ .cs")
        return out


    ACTS = ["tabNQ", "tabGC", "fixNQ", "fixGC", "pick:projectx", "port", "autosave", "load", "lang", "restart",
            "exportimport", "bridge", "clearNQ", "clearGC"]
    SEQS = [[a] for a in ACTS] + [list(p) for p in itertools.permutations(ACTS, 2)]
    bad, n = [], 0
    for pa in ("NQ", "GC"):
        for tab in ("NQ", "GC"):
            for sq in SEQS:
                n += 1
                app = boot(pa, tab)
                hits = []
                for a in sq:
                    try:
                        app = act(app, a)
                    except Exception as e:
                        hits.append(f"{a}:EXC {type(e).__name__}: {e}")
                        break
                    hits += [f"{a}:{x}" for x in anomalies(app)]
                if hits:
                    bad.append(f"P{pa}/T{tab}|{'>'.join(sq)}: {'; '.join(dict.fromkeys(hits))[:300]}")
    print(f"nt8 split matrix 섞임 {len(bad)}/{n}")
    for b in bad[:60]:
        print("  ", b)

    return len(bad), n, bad


def run_s6():
    # S6: 패널 브로커 5 × 계좌 탭 3 × (칸 입력·자동 저장·탭 전환·언어·가져오기) 뒤 creds 키가 자산이 쓸 수 있는 브로커뿐인가, 값이 제 창고에만 있는가

    EMAIL, KEY = "me@topstep.com", "TSKEY"
    PX_LOGINS[EMAIL] = {"key": KEY, "accts": ["S1"]}
    NQ = {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["S1"]}},
          "accounts": [row("S1", "projectx", 600, "S1")]}
    GC = {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": EMAIL, "f3": "", "avail": ["S1"]}},
          "accounts": [row("S1", "projectx", 600, "S1")]}
    BTC = {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "", "f3": ""}, "bitget": {"f1": "", "f3": ""}},
           "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": "bybit"}]}
    VAL = {"projectx": ("user", "p@x.com"), "nt8": ("f3", "8399"), "ibkr": ("f3", "7496"),
           "bybit": ("user", "BYBITKEY-PANEL-00000001"), "bitget": ("user", "BITGETKEY-PANEL-0000001")}
    bad = []
    for tab in ("NQ", "GC", "BTC"):
        for b in ("projectx", "nt8", "ibkr", "bybit", "bitget"):
            seed({"NQ": NQ, "GC": GC, "BTC": BTC}, {EMAIL: KEY, "p@x.com": KEY})
            app = start()
            app._on_asset(tab); flush()
            pick(app, b)
            fld, v = VAL[b]
            w = app.user if fld == "user" else app.f3
            if eqgui._BROKER_SPEC[b].get("f1_secret") and fld == "user":
                app._unlock_f1(); flush()
            type_into(w, v)
            app._autosave_fire(); flush()
            for nxt in ("tabNQ", "tabGC", "tabBTC", "lang"):
                if nxt == "lang":
                    app.langbox.set("English"); app._set_lang()
                else:
                    app._on_asset(nxt[3:])
                flush()
            y = yml()
            for a in ("NQ", "GC", "BTC"):
                ks = set((app._acfg[a].get("creds") or {}).keys())
                yk = set(((((y.get("assets") or {}).get(a) or {}).get("creds")) or {}).keys())
                extra = (ks | yk) - set(eqgui._ASSET_BROKERS[a])
                if extra:
                    bad.append(f"tab{tab}/panel {b}: {a} creds에 {sorted(extra)}")
            holders = eqgui._bk_holders(b)
            for a in holders:
                got = str((app._acfg[a]["creds"].get(b) or {}).get("f1" if fld == "user" else "f3") or "")
                if got != v:
                    bad.append(f"tab{tab}/panel {b}: {a} {b} 값 {got!r}≠{v!r}")
            for a in ("NQ", "GC", "BTC"):
                if a in holders:
                    continue
                for bb, cr in (app._acfg[a].get("creds") or {}).items():
                    if v in (str(cr.get("f1") or ""), str(cr.get("f3") or "")):
                        bad.append(f"tab{tab}/panel {b}: 값이 {a} {bb}에")
    print("S6 섞임", len(bad))
    for x in bad[:20]:
        print("  ", x)

    return len(bad), bad


_n4, _t4, _b4 = run_s4()
ck(f"Topstep split 행렬(패널 자산 × 계좌 탭 × 행동 17의 1~2순열) 섞임 0/{_t4}", _n4 == 0, " || ".join(_b4[:4]))
_n4b, _t4b, _b4b = run_s4b()
ck(f"Lucid split 행렬(포트 다른 두 토큰 × 브리지 버튼 포함) 섞임 0/{_t4b}", _n4b == 0, " || ".join(_b4b[:4]))
_n6, _b6 = run_s6()
ck("값 위치(패널 브로커 5 × 계좌 탭 3): creds 키는 자산이 쓸 수 있는 브로커뿐, 값은 제 창고에만", _n6 == 0, " || ".join(_b6[:4]))
try:
    ROOT.destroy()
except Exception:
    pass
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
