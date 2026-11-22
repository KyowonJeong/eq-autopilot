#!/usr/bin/env python3
# EQ Autopilot 2026.11.11f - [라이브 시작]을 왼쪽 메뉴로(2026-11-11) 오프라인 시험. 0 = 통과.
# =========================
# 왜(대표 2026-11-11): "라이브 시작 버튼이 왼쪽에 오게 / 실행 화면 말고 / 젤 많이 누르는 건데 거깄어".
#   [▶ 라이브 시작]을 실행 쪽 버튼 줄에서 왼쪽 메뉴 아래 묶음(_dock) 맨 위로 옮긴다.
#   1 버튼의 부모 = _dock, 묶음 순서 = [라이브 시작] → [프롭 추천 금액 쓰기 ●] → [전 계좌 1R 조회] → [모든 포지션 청산] → [자산별 청산]
#   2 실행 쪽 버튼 줄에는 [전체 정지], [놓친 신호 진입]만(라이브 시작 없음), 어느 쪽에서든 [라이브 시작]이 보인다, 다시 그려도 그대로
#   3 켬/끔(en, config(state=...))은 종전 그대로: 권한 있음 = 켬, 서버 라이브 잠금, 권한 없음 = 끔
#   4 실행 쪽에만 있는 조건(동의 체크, 실행 자산 체크, 이미 가동 중의 [전체 정지])에서 막히면 종전 경고 + 실행 쪽으로 넘어간다(조용히 실패 안 함)
#   리뷰 P2-9(2026-11-11): 낮은 창(768, 708, 660, 600px)에서 묶음 버튼, 메뉴, 진입 시각 칸이 모두 보인다(여백만 줄임), 꺼진 이유 줄과 말풍선
#
# 사용: HOME=$(mktemp -d) EQ_NO_ALERT=1 python executor/test_golive_dock_offline.py
# =========================
import copy
import os
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_gl_")
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


class FakeThread:
    """백그라운드 스레드는 돌리지 않는다(이 시험은 화면 배치와 시작 버튼의 앞단 검사만 본다)."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **k):
        self.target = target

    def start(self):
        pass

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

fails = []


def ck(name, cond, info=None):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond or info is None else f"   -> {info!r}"[:500]))
    if not cond:
        fails.append(name)


KC = {}
eqgui._kc_save = lambda acc, s: bool(s) and (KC.__setitem__(acc or "default", s) or True)
eqgui._kc_load = lambda acc: KC.get(acc or "default", "")
eqgui._kc_del = lambda acc: KC.pop(acc or "default", None)
MSG, ASK = [], [True]
eqgui.messagebox.askyesno = lambda *a, **k: (MSG.append(("ask",) + tuple(str(x) for x in a)) or ASK[0])
eqgui.messagebox.showinfo = lambda *a, **k: MSG.append(("info",) + tuple(str(x) for x in a))
eqgui.messagebox.showwarning = lambda *a, **k: MSG.append(("warn",) + tuple(str(x) for x in a))
eqgui.messagebox.showerror = lambda *a, **k: MSG.append(("error",) + tuple(str(x) for x in a))
eqgui.App._notice = lambda self, t, m, buttons=None: MSG.append(("notice", str(t), str(m)))
eqgui.App._bundled_bridge_ver = lambda self: ""
BUILDS = []


def _no_build(*a, **k):
    BUILDS.append(a)
    raise RuntimeError("broker build not expected in this test")


eqgui._build_broker = _no_build
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
_oc = ROOT.after_cancel
ROOT.after_cancel = lambda i: None if i == "dropped" else _oc(i)

GATE_OK = {"ok": True, "tier": "royal", "enabled": True, "force_dry_run": False,
           "caps": {"use": True, "manualentry": True, "autoentry": True, "connect": True}, "brokers": {}, "reason": ""}


def flush():
    for _ in range(3):
        ROOT.update()


def seed(lang="ko"):
    d = {"lang": lang, "token": "MEMBERTOKEN",
         "assets": {"NQ": {"broker": "projectx", "include": True,
                           "accounts": [{"id": "150KTC-A", "one_r": 600, "on": True, "label": "", "broker": "projectx"}]}}}
    with open(eqgui.CFG_PATH, "w") as f:
        yaml.safe_dump(copy.deepcopy(d), f)


def start(lang="ko"):
    for w in list(ROOT.winfo_children()):
        try:
            w.destroy()
        except Exception:
            pass
    seed(lang)
    app = eqgui.App(ROOT)
    app._gate = dict(GATE_OK)
    try:
        app.consent.set(1)
    except Exception:
        pass
    app._apply_gating()
    flush()
    return app


def under(w, top):
    """w가 top 아래(자신 포함) 위젯인가."""
    while w is not None:
        if w is top:
            return True
        w = w.master
    return False


ROOT.deiconify()
ROOT.geometry("1100x900")
for _lang in ("ko", "en"):
    app = start(_lang)
    b = app.b_live_start
    # ── 1 자리와 순서 ─────────────────────────────────────────────────────────────────────
    ck(f"1a[{_lang}] [라이브 시작]의 부모 = 왼쪽 메뉴 아래 묶음(_dock), ttk 버튼, 글 = live_start, 누름 = _master_start",
       b.master is app._dock and isinstance(b, eqgui.ttk.Button) and str(b.cget("text")) == eqgui.T["live_start"][_lang]
       and str(b.cget("command")) != "", (b.master, str(b.cget("text"))))
    _sl = app._dock.pack_slaves()
    _reco = app._prop_reco_dot.master
    ck(f"1b[{_lang}] 묶음 순서: 라이브 시작 → 프롭 추천 금액 줄 → 전 계좌 1R 조회 → 모든 포지션 청산 → 자산별 청산",
       _sl == [b, _reco, app._b_1r_all, app._flat_btn, app._flat_asset_mb], [str(x) for x in _sl])
    _pi = b.pack_info()
    ck(f"1c[{_lang}] 묶음 다른 줄과 같은 폭(fill=x), 아래 여백", _pi.get("fill") == "x" and str(_pi.get("pady")) not in ("0", "", "(0, 0)"),
       _pi)
    # ── 2 실행 쪽 버튼 줄 ────────────────────────────────────────────────────────────────
    app._show_page("run"); flush()
    _row = app.b_live_stop.master
    _row_txt = [str(w.cget("text")) for w in _row.winfo_children()]
    ck(f"2a[{_lang}] 실행 쪽 버튼 줄 = [전체 정지], [놓친 신호 진입]만(라이브 시작 없음)",
       not under(b, app._pages["run"]) and _row_txt == [eqgui.T["live_stopall"][_lang],
                                                        "놓친 신호 진입" if _lang == "ko" else "Enter missed signal"]
       and under(app.b_live_stop, app._pages["run"]), _row_txt)
    _left = app.b_live_stop.winfo_rootx() - _row.winfo_rootx()
    ck(f"2b[{_lang}] [전체 정지]가 줄 맨 왼쪽(옛 버튼 자리를 비워 두지 않음)", _left == 0, _left)
    for _pk in eqgui._PAGE_KEYS:
        app._show_page(_pk); flush()
        ck(f"2c[{_lang}] 쪽 {_pk}: [라이브 시작]과 청산 버튼이 보이고 [라이브 시작]이 [전 계좌 1R 조회] 위",
           b.winfo_ismapped() and app._flat_btn.winfo_ismapped() and b.winfo_rooty() < app._b_1r_all.winfo_rooty()
           < app._flat_btn.winfo_rooty())
    app._show_page("acct"); app._build(); flush()
    ck(f"2d[{_lang}] 다시 그려도(_build) 새 버튼이 묶음 맨 위, 실행 쪽에는 없음",
       app.b_live_start.master is app._dock and app._dock.pack_slaves()[0] is app.b_live_start
       and not under(app.b_live_start, app._pages["run"]) and app._page == "acct")
    # 리뷰 P2-9: 낮은 창(1366x768 화면의 기본 창 708px, 최소 600px)에서 묶음 버튼 다섯도, 진입 시각 칸도, 메뉴 일곱도 잘리지 않는다
    # (모자라면 _side_fit이 여백만 줄인다). [라이브 시작]이 꺼져 이유 줄(두 줄까지)이 붙어도 같다. 다시 커지면 종전 여백으로.
    def _full(w):
        return bool(w.winfo_ismapped()) and w.winfo_height() >= w.winfo_reqheight()

    _bad = {}
    for _gname, _g in (("ok", dict(GATE_OK)), ("lock", dict(GATE_OK, force_dry_run=True)),
                       ("offline", {"ok": False, "offline": True})):
        app._gate = _g; app._apply_gating(); flush()
        for _h in (768, 708, 660, 600):
            ROOT.geometry(f"1160x{_h}"); flush(); flush()
            _sb = app._sched_box
            _probs = [w.winfo_class() for w in app._dock.pack_slaves() if not _full(w)]
            _probs += [k for k, nb in app._nav_btns.items() if not _full(nb)]
            if not _full(_sb):
                _probs.append("sched")
            if _sb.winfo_y() + _sb.winfo_height() > app._dock.winfo_y():
                _probs.append("overlap")
            if _probs:
                _bad[(_gname, _h)] = _probs
    ROOT.geometry("1100x900"); flush(); flush()
    _norm = (not app._side_compact and str(app._nav_btns["run"].cget("pady")) == "7"
             and app._sched_box.pack_info().get("pady") in ((14, 0), "14 0"))
    app._gate = dict(GATE_OK); app._apply_gating(); flush()
    ck(f"2e[{_lang}] 768, 708, 660, 600px(켬, 라이브 잠금, 서버 끊김 이유 줄): 묶음 다섯, 메뉴 일곱, 진입 시각 칸 모두 다 보이고 겹침 없음",
       not _bad and app._dock.pack_slaves()[0] is app.b_live_start, _bad)
    ck(f"2f[{_lang}] 다시 900px이면 종전 여백(좁게 풀림)", _norm, (app._side_compact, app._nav_btns["run"].cget("pady")))
    b = app.b_live_start
    # ── 3 켬/끔 ──────────────────────────────────────────────────────────────────────────
    app._gate = dict(GATE_OK); app._apply_gating(); flush()
    _on = str(b.cget("state"))
    app._gate = dict(GATE_OK, force_dry_run=True); app._apply_gating(); flush()
    _lock = str(b.cget("state"))
    app._gate = {"ok": False, "reason": "no token"}; app._apply_gating(); flush()
    _off = str(b.cget("state"))
    app._gate = dict(GATE_OK); app._apply_gating(); flush()
    ck(f"3a[{_lang}] 켬/끔(en): 권한 있음 = normal, 서버 라이브 잠금 = disabled, 권한 없음 = disabled",
       (_on, _lock, _off) == ("normal", "disabled", "disabled"), (_on, _lock, _off))
    b.config(state="disabled"); _d = str(b.cget("state")); b.config(state="normal")
    ck(f"3b[{_lang}] config(state=...) 경로(시작 중 끄기, 끝나면 켜기)", _d == "disabled" and str(b.cget("state")) == "normal")
    # 리뷰 P2-9 (ii): 꺼진 이유 = 버튼 바로 아래 회색 줄(_gate_line의 ' - ' 앞)과 말풍선(전체 문장). 켜지면 줄이 빠진다.
    app._gate = dict(GATE_OK, force_dry_run=True); app._apply_gating(); flush()
    _why = app._golive_why
    _sl = app._dock.pack_slaves()
    _full_txt = app._gate_line()
    ck(f"3c[{_lang}] 라이브 잠금: 버튼 아래 회색 줄 = 이유 앞부분, 말풍선 = 이유 전체(실행, 멤버십 쪽과 같은 문장)",
       _why.winfo_manager() == "pack" and _sl.index(_why) == _sl.index(b) + 1
       and str(_why.cget("text")) == _full_txt.split(" - ")[0] and app._golive_tip() == _full_txt and _full_txt,
       (str(_why.cget("text")), _full_txt))
    app._gate = {"ok": False, "offline": True}; app._apply_gating(); flush()
    ck(f"3d[{_lang}] 서버 끊김: 같은 줄이 그 이유로", str(_why.cget("text")) == app._gate_line().split(" - ")[0]
       and _why.winfo_manager() == "pack")
    app._gate = dict(GATE_OK); app._apply_gating(); flush()
    ck(f"3e[{_lang}] 다시 켜지면 줄 빠짐, 말풍선 없음", not _why.winfo_manager() and app._golive_tip() == "")
    # ── 4 실행 쪽 조건에서 막히면 경고 + 실행 쪽으로 ──────────────────────────────────────────
    app._show_page("acct"); flush()
    app.consent.set(0)
    MSG.clear(); BUILDS.clear()
    app._master_start(); flush()
    ck(f"4a[{_lang}] 동의 체크 꺼짐: 확인 창 뒤 종전 동의 경고, 실행 쪽으로 넘어감, 무장, 연결 테스트 없음",
       ("warn", eqgui.T["need_consent"][_lang], eqgui.T["need_consent"][_lang]) in MSG and app._page == "run"
       and not app._sig_accts and not BUILDS and app._pages["run"].winfo_ismapped(), (MSG, app._page))
    app.consent.set(1)
    app._show_page("member"); flush()
    for _a in eqgui._ASSETS:
        app._live_include[_a].set(0)
    MSG.clear()
    app._master_start(); flush()
    ck(f"4b[{_lang}] 실행 자산 체크 없음: 종전 경고 + 실행 쪽으로", any(m[0] == "warn" for m in MSG) and app._page == "run",
       (MSG, app._page))
    app._live_include["NQ"].set(1)
    app._show_page("tr"); flush()
    app._sig_accts[("NQ", 0)] = {"broker": "projectx"}
    MSG.clear()
    app._master_start(); flush()
    ck(f"4c[{_lang}] 이미 가동 중: 종전 안내('전체 정지' 먼저) + [전체 정지]가 있는 실행 쪽으로", any(m[0] == "info" for m in MSG)
       and app._page == "run", (MSG, app._page))
    app._sig_accts.clear()
    app._show_page("acct"); flush()
    app._gate = dict(GATE_OK, force_dry_run=True)
    MSG.clear()
    app._master_start(); flush()
    ck(f"4d[{_lang}] 서버 라이브 잠금은 종전 그대로(경고만, 쪽 그대로)", any(m[0] == "warn" for m in MSG) and app._page == "acct",
       (MSG, app._page))
    app._gate = dict(GATE_OK)
    app._show_page("run"); flush()

ROOT.withdraw()

# ── 5 실행 쪽 위치를 말하는 글이 없다(버튼 줄 주석 외) ─────────────────────────────────────────
src = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
ck("5a 생성 줄이 _dock에 하나", src.count('self.b_live_start = ttk.Button(self._dock, text=self.t("live_start"), command=self._master_start)') == 1
   and "self.b_live_start = ttk.Button(lc," not in src)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
try:
    ROOT.destroy()
except Exception:
    pass
sys.exit(1 if fails else 0)
