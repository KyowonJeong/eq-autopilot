#!/usr/bin/env python3
# 2026-10-21 21h 검토(실행 검토자 재현 스크립트를 시험으로): Lucid 새 줄 + 프롭 끈 채 저장 = 1R과 단계 그대로. 0 = 통과.
# Scratch driver for eqgui 2026.10.21h diff review. Real App, real Tk (withdrawn), HOME=tmp, net blocked, keychain=dict.
import os, sys, tempfile, traceback, threading, io, re

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_rev21h_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
for _k in ("EQ_PROFILE", "EQ_SHARE", "EQ_DAYCAP_OFF"):
    os.environ.pop(_k, None)
os.makedirs(os.path.join(HOME, "Documents", "NinjaTrader 8", "bin", "Custom", "AddOns"), exist_ok=True)

import socket


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.socket.connect_ex = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk

import subprocess
_REAL_POPEN = subprocess.Popen
SUBP = []


def _no_sub(*a, **k):
    SUBP.append(a[0] if a else k.get("args"))
    raise OSError("subprocess blocked by test")


subprocess.Popen = _no_sub
subprocess.run = _no_sub

RUN_SYNC = ("_async_load_key", "_master_start", "_autosave_fire", "_panel_conn_test", "_load_accounts_for_asset",
            "_live_arm_assets", "_autoload_nt8_avail", "_autoload_topstep_scope", "_run.<locals>.wrap",
            "_run_1r_preview")
THREAD_SKIPPED = []


class FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **k):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        qn = getattr(self.target, "__qualname__", "")
        if any(s in qn for s in RUN_SYNC):
            self.target(*self.args, **self.kwargs)
        else:
            THREAD_SKIPPED.append(qn)

    def join(self, *a, **k):
        pass

    def is_alive(self):
        return False


WT = os.environ.get("EQSRC") or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WT)
import eqgui as E
threading.Thread = FakeThread
import tkinter as tk
from tkinter import ttk
import yaml
assert E.CFG_PATH.startswith(HOME), E.CFG_PATH

KC = {}
E._kc_save = lambda acc, s: (KC.__setitem__(acc or "default", s) or True) if s else False
E._kc_load = lambda acc: KC.get(acc or "default", "")
E._kc_del = lambda acc: KC.pop(acc or "default", None)
E._keep_awake = lambda on: True
import webbrowser
webbrowser.open = lambda *a, **k: True

MSG = []
ASK_ANS = [True]
E.messagebox.askyesno = lambda *a, **k: (MSG.append(("ask",) + tuple(str(x)[:300] for x in a)) or ASK_ANS[0])
E.messagebox.askokcancel = lambda *a, **k: (MSG.append(("okc",) + tuple(str(x)[:300] for x in a)) or ASK_ANS[0])
E.messagebox.showinfo = lambda *a, **k: MSG.append(("info",) + tuple(str(x)[:300] for x in a))
E.messagebox.showwarning = lambda *a, **k: MSG.append(("warn",) + tuple(str(x)[:300] for x in a))
E.messagebox.showerror = lambda *a, **k: MSG.append(("error",) + tuple(str(x)[:300] for x in a))
E.simpledialog.askstring = lambda *a, **k: "1234"
import tkinter.filedialog as fd
EXPORT = os.path.join(HOME, "eq.eqset")
fd.asksaveasfilename = lambda *a, **k: EXPORT
fd.askopenfilename = lambda *a, **k: EXPORT
fd.askdirectory = lambda *a, **k: ""

BRIDGE = {"token": "TOKN", "accounts": ["LDI150A5555", "Sim101"], "ver": ""}


class FakePX:
    name = "projectx"

    def __init__(self, f1, f2):
        self.f1, self.f2 = f1, f2

    def healthcheck(self):
        return None

    def _accounts(self):
        return [{"name": "150KTC-V2-1111", "id": 1, "balance": 150000, "canTrade": True},
                {"name": "PRAC-V2-2222", "id": 2, "balance": 50000, "canTrade": True}]

    def entry_info(self):
        return []

    def list_open_positions(self):
        return []

    def get_balance(self, *a, **k):
        return 150000.0


class FakeNT8:
    name = "nt8"

    def __init__(self, f1):
        self.f1 = f1

    def healthcheck(self):
        return None

    def _accounts(self):
        return [{"name": n} for n in BRIDGE["accounts"]]

    def _snapshot(self):
        return {"bridge_ver": BRIDGE["ver"], "accounts": BRIDGE["accounts"]}

    def entry_info(self):
        return []

    def list_open_positions(self):
        return []


class FakeCrypto:
    def __init__(self, b):
        self.name = b

    def healthcheck(self):
        return None

    def entry_info(self):
        return []

    def list_open_positions(self):
        return []


def _fake_build_broker(broker, f1, f2, f3, accounts):
    if broker == "projectx":
        return FakePX(f1, f2)
    if broker == "nt8":
        return FakeNT8(f1)
    if broker in ("bybit", "bitget"):
        return FakeCrypto(broker)
    raise RuntimeError(f"{broker} not in test")


E._build_broker = _fake_build_broker

LOGS = []
_orig_log = E.App.log


def _log(self, m):
    LOGS.append(str(m))
    try:
        _orig_log(self, m)
    except Exception:
        pass


E.App.log = _log

TKERR = []
ROOT = tk.Tk()
ROOT.withdraw()


def _rce(exc, val, tb):
    TKERR.append("".join(traceback.format_exception(exc, val, tb)))


ROOT.report_callback_exception = _rce
_orig_after = ROOT.after
DROPPED = []


def _after(ms, func=None, *args):
    if func is None:
        return _orig_after(ms)
    if ms == 0 or ms == "idle":
        return _orig_after(0, func, *args)
    if os.environ.get("REAL_AFTER") and isinstance(ms, int) and ms <= 2000:
        return _orig_after(ms, func, *args)
    DROPPED.append(getattr(func, "__qualname__", str(func)))
    return "dropped"


ROOT.after = _after
_orig_cancel = ROOT.after_cancel
ROOT.after_cancel = lambda i: None if i == "dropped" else _orig_cancel(i)


def flush():
    import time as _tt
    for _ in range(4):
        ROOT.update()
    if os.environ.get("REAL_AFTER"):
        _end = _tt.time() + 0.6
        while _tt.time() < _end:
            ROOT.update(); _tt.sleep(0.02)


RESULTS = []


def step(name, fn):
    n0 = len(TKERR)
    try:
        r = fn()
        flush()
        if len(TKERR) > n0:
            RESULTS.append(("TKERR", name, "\n".join(TKERR[n0:])))
        else:
            RESULTS.append(("OK", name, "" if r is None else str(r)[:300]))
        return r
    except Exception:
        flush()
        RESULTS.append(("EXC", name, traceback.format_exc()))
        return None


def walk(w):
    out = [w]
    for c in w.winfo_children():
        out += walk(c)
    return out


def tops():
    return [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]


def btn(w, text):
    for x in walk(w):
        if isinstance(x, (ttk.Button, tk.Button, ttk.Radiobutton, ttk.Checkbutton)):
            try:
                if str(x.cget("text")) == text or str(x.cget("text")).startswith(text):
                    return x
            except Exception:
                pass
    raise LookupError(f"button {text!r} not found among "
                      + repr([str(x.cget('text')) for x in walk(w) if isinstance(x, (ttk.Button, tk.Button))]))


# seed config
TOKEN = "MEMBERTOKEN"
P0 = dict(E._PROP_DEFAULTS)
cfg = {"lang": "ko", "token": TOKEN, "assets": {
    "NQ": {"broker": "projectx", "include": True,
           "creds": {"projectx": {"f1": "a@x.com", "f3": "", "avail": ["150KTC-V2-1111", "PRAC-V2-2222"]}},
           "accounts": [
               {"id": "150KTC-V2-1111", "one_r": 600, "on": True, "label": "TS", "broker": "projectx",
                "prop": dict(P0, on=True, type="test", mode="own", set=True, r_test=1234.0, r_steady=450.0)},
               {"id": "PRAC-V2-2222", "one_r": 650, "on": True, "label": "PR", "broker": "projectx",
                "pct": {"on": True, "pct": 1.0, "floor": 200}}]},
    "GC": {"broker": "nt8", "include": True,
           "creds": {"nt8": {"f1": "TOKN", "f3": "", "avail": ["LDI150A5555", "Sim101"]}},
           "accounts": [{"id": "LDI150A5555", "one_r": 500, "on": True, "label": "LU", "broker": "nt8", "prop": dict(P0)}]},
    "BTC": {"broker": "bybit", "include": False, "creds": {"bybit": {"f1": "BYKEY", "f3": ""}},
            "accounts": [{"id": "", "one_r": 300, "on": True, "label": "", "broker": "bybit"}]}}}
with open(E.CFG_PATH, "w") as f:
    yaml.safe_dump(cfg, f)
KC.update({"a@x.com": "KA-KEY", "BYKEY": "BYSEC"})

GATE = {"ok": True, "tier": "royal", "enabled": True, "force_dry_run": False,
        "caps": {"use": True, "manualentry": True, "autoentry": True, "connect": True},
        "brokers": {}, "reason": ""}


cfg["assets"]["GC"]["creds"]["nt8"]["avail"] = ["LDI150A5555", "LDI150A8888", "Sim101"]
with open(E.CFG_PATH, "w") as f:
    yaml.safe_dump(cfg, f)
app = E.App(ROOT); app._gate = dict(GATE); app.consent.set(1); flush()
BRIDGE["accounts"] = ["LDI150A5555", "LDI150A8888", "Sim101"]
app._on_asset("GC"); flush()
app._add_acct(); flush()
i = len(app._accts_of("GC")) - 1
w = app._acct_widgets[i]
w["broker"].set(E._broker_label("nt8")); w["broker"].event_generate("<<ComboboxSelected>>"); flush()
w = app._acct_widgets[i]
print("row before pick", app._accts_of("GC")[i].get("broker"), app._accts_of("GC")[i].get("one_r"), list(w["acct_id"].cget("values")) if w.get("acct_id") else None)
w["acct_id"].set("LDI150A8888"); w["acct_id"].event_generate("<<ComboboxSelected>>"); flush()
print("fresh map:", dict(app._dc_map("_dc_fresh")))
n0 = len(LOGS)
app._lucid_stage_check(); flush()
print("after pick: one_r", app._accts_of("GC")[i].get("one_r"), "id", app._accts_of("GC")[i].get("id"))
print("logs:", [l for l in LOGS[n0:] if "Lucid" in l])
# open prop dialog, leave prop OFF, just Save
app._prop_dialog(i); flush()
t = tops()[-1]
print("dialog checkbox selected:", [x.instate(["selected"]) for x in walk(t) if isinstance(x, ttk.Checkbutton)])
n0 = len(LOGS)
btn(t, "저장").invoke(); flush()
r = app._accts_of("GC")[i]
print("after prop-dialog Save (prop off, untouched): one_r", r.get("one_r"), "prop.on", r["prop"].get("on"), "stage", r["prop"].get("stage"))
print("logs:", LOGS[n0:])

# 21h 검토 재발 방지: 새로 고른 Lucid 줄(단계 미답)에서 프롭을 끈 채 설정 창 저장만 눌러도 1R과 단계가 바뀌면 안 된다.
_ok = float(r.get("one_r") or 0) == 500.0 and not r["prop"].get("stage")
print(("PASS" if _ok else "FAIL") + " 프롭 끈 채 저장 = 1R 500 그대로, 단계 미답 그대로")
sys.exit(0 if _ok else 1)
