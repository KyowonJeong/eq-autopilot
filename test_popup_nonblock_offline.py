#!/usr/bin/env python3
# EQ Autopilot - 팝업이 거래를 막지 않는다(대표 2026-10-20 "팝업 뭐든간에 거래는 드가야", "회원앱 자주 안 봐", "차라리 dm을 쏴"). 0 = 통과.
#   1 정적: 작업 줄(threading.Thread 대상)에서 닿는 App 메서드에 화면(Tk) 직접 호출이 없다 - root.after, messagebox, simpledialog,
#     위젯 config/insert 등. 화면 일은 self._ui(fn)로만 넘긴다(대기열, 기다리지 않음). 맨 앞에서 _on_main()으로 화면 줄로
#     넘기는 메서드는 안전으로 본다.
#   2 정적: 자동 사건(진입, 청산, 감시, 사전 점검, 통과, 시계, 새 실행 파일, 출금 문턱, 수동 티켓, 무장 해제) 함수에 모달 창
#     (messagebox, simpledialog)이 없다 - 회원에게는 DM(_member_alert)으로 알린다.
#   3 실행: 진짜 Tk 화면을 만들되 화면 줄은 mainloop를 돌리지 않는다(모달 창에 갇힌 화면 줄과 같음). 그 상태에서 작업 줄이
#     시계 경고, 증거금 사전 점검, 출금 문턱, 저장 표시, 화면 일 넘기기를 부르면 Tk를 한 번도 부르지 않고 바로 끝나며,
#     DM은 기록되고 화면 일은 대기열에 쌓인다. 화면 줄이 돌아오면(_drain) 대기열이 비고 모달이 아닌 창이 뜬다.
# HOME, APPDATA = 임시 폴더, 네트워크 차단, EQ_NO_ALERT=1.
import ast
import os
import queue
import re
import socket
import sys
import tempfile
import threading
import time

HOME = tempfile.mkdtemp(prefix="eq_popup_")
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

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:600]}"))
    if not c:
        FAIL.append(n)


# ── 1, 2 정적 ─────────────────────────────────────────────────────────────────
SRC = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
TREE = ast.parse(SRC)
APP = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "App")
METHODS = {f.name: f for f in APP.body if isinstance(f, ast.FunctionDef)}
WIDGET_CALLS = {"config", "configure", "insert", "delete", "see", "itemconfig", "itemconfigure", "winfo_exists",
                "winfo_children", "grid", "pack", "place", "grid_remove", "pack_forget", "destroy", "update",
                "update_idletasks", "focus_set", "lift", "tag_configure", "bind", "selection_set", "heading", "yview",
                "xview", "after_cancel", "after", "after_idle"}
DIALOG_MODS = {"messagebox", "simpledialog", "_mb"}


def _self_attr(n, attr=None):
    return (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
            and (attr is None or n.attr == attr))


def _guarded(fn) -> bool:
    """맨 앞 문장이 'if not self._on_main(): ... return'이면 화면 줄로 넘기는 메서드다."""
    body = [b for b in fn.body if not (isinstance(b, ast.Expr) and isinstance(getattr(b, "value", None), ast.Constant))]
    if not body or not isinstance(body[0], ast.If):
        return False
    t = body[0].test
    return (isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not) and isinstance(t.operand, ast.Call)
            and _self_attr(t.operand.func, "_on_main"))


class W(ast.NodeVisitor):
    def __init__(self, fn, scope):
        self.fn, self.scope = fn, dict(scope)
        self.calls, self.local, self.threads, self.tk = set(), set(), [], []
        for n in ast.walk(fn):
            if isinstance(n, ast.FunctionDef) and n is not fn:
                self.scope.setdefault(n.name, n)

    def visit_FunctionDef(self, n):
        if n is self.fn:
            self.generic_visit(n)

    def visit_Lambda(self, n):
        pass

    def visit_Call(self, n):
        f = n.func
        if (isinstance(f, ast.Attribute) and f.attr == "Thread") or (isinstance(f, ast.Name) and f.id == "Thread"):
            self.threads += [k.value for k in n.keywords if k.arg == "target"]
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Attribute) and _self_attr(f.value, "root"):
                self.tk.append((n.lineno, "self.root." + f.attr))
            elif isinstance(f.value, ast.Name) and f.value.id in DIALOG_MODS:
                self.tk.append((n.lineno, f.value.id + "." + f.attr))
            elif isinstance(f.value, ast.Name) and f.value.id in ("tk", "ttk", "scrolledtext"):       # 위젯 만들기(Toplevel 등)
                self.tk.append((n.lineno, f.value.id + "." + f.attr))
            elif f.attr == "cget" or f.attr.startswith("winfo_"):
                self.tk.append((n.lineno, ast.unparse(f.value)[:40] + "." + f.attr))
            elif f.attr in ("get", "set") and re.search(r"(_var|_sv|_iv|_bv|Var)\b", ast.unparse(f.value)):   # tk 변수
                self.tk.append((n.lineno, ast.unparse(f.value)[:40] + "." + f.attr))
            elif f.attr in WIDGET_CALLS and not (isinstance(f.value, ast.Name) and f.value.id in ("self",)):
                rv = ast.unparse(f.value)
                if not re.fullmatch(r"(out|pr|_h|rec|busy|_r0|d|st|row|res|_st|_d|_pr|info|_m|m|kw|cfg|g)", rv):
                    self.tk.append((n.lineno, rv[:40] + "." + f.attr))
            if _self_attr(f):
                self.calls.add(f.attr)
            elif (isinstance(f.value, ast.Name) and f.value.id == "App" and n.args and isinstance(n.args[0], ast.Name)
                  and n.args[0].id == "self"):
                self.calls.add(f.attr)      # App.X(self, ...) - 루프 보호막(_sig_loop, _auto_loop)이 본문을 클래스로 부른다(21l)
        elif isinstance(f, ast.Name) and f.id in self.scope:
            self.local.add(f.id)
        self.generic_visit(n)


seen, found = set(), []


def walk(name, node, scope, path):
    key = (name, getattr(node, "lineno", 0))
    if node is None or key in seen:
        return
    seen.add(key)
    if isinstance(node, ast.Lambda):
        node = ast.FunctionDef(name="lambda", args=node.args, body=[ast.Expr(node.body)], decorator_list=[],
                               lineno=node.lineno)
    if isinstance(node, ast.FunctionDef) and _guarded(node):
        return
    w = W(node, scope)
    w.visit(node)
    found.extend((ln, what, " > ".join(path + [name])) for ln, what in w.tk)
    for m in w.calls:
        if m in METHODS and m not in ("log", "_member_alert", "_ui", "_on_main"):
            walk(m, METHODS[m], {}, path + [name])
    for loc in w.local:
        walk(loc, w.scope[loc], w.scope, path + [name])


entries = 0
for mname, mf in METHODS.items():
    w = W(mf, {})
    w.visit(mf)
    for t in w.threads:
        entries += 1
        if _self_attr(t):
            walk(t.attr, METHODS.get(t.attr), {}, [f"{mname}:Thread"])
        elif isinstance(t, ast.Name) and t.id in w.scope:
            walk(t.id, w.scope[t.id], w.scope, [f"{mname}:Thread"])
        elif isinstance(t, ast.Lambda):
            walk("lambda", t, w.scope, [f"{mname}:Thread"])
ck(f"1 작업 줄({entries}개 진입점)에서 닿는 곳에 화면 직접 호출 0", not found and entries >= 30,
   "\n".join(f"{a}\t{b}\t{c}" for a, b, c in sorted(set(found))[:20]))

AUTO = ["_apply_gating", "_enter_account", "_exec_entry_limit", "_run_futures_entry", "_handle_stop_failure", "_auto_loop", "_auto_loop_body",
        "_passtp_close_account", "_passtp_scan", "_precheck_run", "_margin_precheck", "_daycap_pass_prompt",
        "_daycap_pass_prompt_show", "_daycap_mark_passed", "_clock_warn_check", "_build_watch", "_idle_warn_tick",
        "_notify_disarmed", "_notify_unlatched", "_resume_parked_sig", "_kc_warn_now",
        "_daycap_mon_scan", "_daycap_mon_one", "_send_fill", "_note_fill", "_check_stop_closed", "_watch_loop"]
bad = []
for a in AUTO:
    fn = METHODS.get(a)
    if fn is None:
        bad.append(f"{a}: 없음")
        continue
    for n in ast.walk(fn):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in DIALOG_MODS:
            bad.append(f"{a}:{n.lineno} {n.value.id}.{n.attr}")
ck("2 자동 사건 함수에 모달 창(messagebox, simpledialog) 0 - 회원에게는 DM", not bad, bad)
# 21h(대표 2026-10-21 '다 해'): 출금 질문 창(_payout_popup, 출금 횟수와 함께 2026-10-21e에 쓰임이 끊김)과 수동 티켓(_manual_ticket, 수동 모드
# 2026-08-11 폐지 - 로더가 manual=False)은 지웠다. 구버전 NT8 브리지 묻기 창도 모달이 아닌 알림 창(_notice)이다.
ck("2b 지운 함수 없음(출금 질문, 출금 기록, 수동 티켓)", all(n not in METHODS for n in ("_payout_popup", "_bump_payout", "_manual_ticket")))
_cc = METHODS.get("_conn_check")
ck("2c 구버전 NT8 브리지: 모달 묻기 창 없음(_notice로)", _cc is not None and not any(
    isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in DIALOG_MODS and n.attr == "askyesno"
    for n in ast.walk(_cc)))

# ── 3 실행: 화면 줄이 멈춘 채 작업 줄 ─────────────────────────────────────────────
import tkinter as tk  # noqa: E402

try:
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as e:                          # 화면이 없는 환경 - 정적 시험만으로 판정
    ROOT = None
    print(f"SKIP 3 실행 시험(Tk 없음: {e})")

if ROOT is not None:
    app = object.__new__(E.App)
    app.root, app.lang = ROOT, "ko"
    app.q, app._uiq = queue.Queue(), queue.Queue()
    app._main_ident = threading.get_ident()
    import collections
    app._log_ring = collections.deque(maxlen=100)
    app.out = tk.Text(ROOT)
    app._token = "t"
    DM = []
    app._member_alert = lambda kind, ko, en: DM.append((kind, ko, en))
    app._saved_lbl = tk.Label(ROOT)
    app._sig_accts = {"GC": [1]}
    app._clock_skew_now = lambda: 120.0
    app._margin_warns = lambda asset, bk: [("잔고가 증거금보다 적어 보입니다.", "Balance looks below margin.",
                                            "(잔고 $100, 증거금 $1,000)", "(balance $100, margin $1,000)",
                                            ("nt8", "acct1"))]
    app._dc_map = lambda name: app.__dict__.setdefault(name, {})

    TK_OFF = []
    _tkfile = os.path.join("tkinter", "__init__.py")

    def _prof(frame, event, arg):
        if event == "call" and frame.f_code.co_filename.endswith(_tkfile) and threading.get_ident() != app._main_ident:
            TK_OFF.append(f"{frame.f_code.co_name} <- {frame.f_back.f_code.co_name}:{frame.f_back.f_lineno}")

    def work():
        app._clock_warn_check()
        app._margin_precheck("GC", "nt8", "08:00")
        app._flash_saved()
        app._ui(lambda: None)
        app._ui(lambda: app._notice("EQ Autopilot", "시험"))

    threading.setprofile(_prof)
    t0 = time.time()
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(5)                                  # 화면 줄은 mainloop를 돌리지 않는다 - 모달에 갇힌 화면 줄과 같다
    dt = time.time() - t0
    threading.setprofile(None)
    ck("3a 화면 줄이 멈춘 채 작업 줄이 끝까지 감(1초 안)", not th.is_alive() and dt < 1.0, f"alive={th.is_alive()} {dt:.2f}s")
    ck("3b 작업 줄의 Tk 호출 0", not TK_OFF, TK_OFF[:8])
    kinds = [k for k, _, _ in DM]
    ck("3c 회원 DM: 시계, 증거금 사전 점검(계좌별 종류 이름)", "clock" in kinds
       and any(k.startswith("margin_pre_") for k in kinds), kinds)
    ck("3c2 DM에 잔고 숫자 없음(증거금 근거)", not any("$" in ko or "$" in en for k, ko, en in DM
                                                if k.startswith("margin_pre_")), DM)
    ck("3c3 DM 한국어는 합니다체(~요 해요체 없음), em 대시와 가운뎃점 없음",
       not any(re.search(r"(요\.|요$|세요)", ko) or "—" in ko or "·" in ko for _, ko, _ in DM), [ko for _, ko, _ in DM])
    ck("3c4 종류 이름은 계좌마다 다르다(두 계좌의 같은 사건이 한 통으로 묶이지 않게)",
       E.App._dm_kind("payout", "projectx", "S1") != E.App._dm_kind("payout", "projectx", "S2")
       and len(E.App._dm_kind("passtp_unconf", ("projectx", "S1"))) <= 24)
    ck("3d DM 문구: 시계 KO/EN 둘 다, 접두 [EQ Autopilot]", any(k == "clock" and "PC 시계" in ko and "clock" in en
                                                            and ko.startswith("[EQ Autopilot]") for k, ko, en in DM), DM[:1])
    ck("3e 화면 일은 대기열에(저장 표시, 넘긴 일 둘)", app._uiq.qsize() >= 3, app._uiq.qsize())
    # 화면 줄 복귀: _drain이 대기열을 root.after로 넘기고, Tk가 돌면 모달 아닌 창이 뜬다(창이 떠 있어도 화면 줄은 계속 돈다)
    app._drain()
    ck("3f _drain 뒤 대기열 비움", app._uiq.empty(), app._uiq.qsize())
    t1 = time.time()
    while time.time() - t1 < 0.6:
        ROOT.update()
        time.sleep(0.02)
    tops = [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]
    ck("3g 모달이 아닌 창이 떴고(시험 알림) 화면 줄은 계속 돈다", len(tops) >= 1 and not ROOT.grab_current(),
       (len(tops), ROOT.grab_current()))
    ck("3i 저장 표시는 화면 줄에서 라벨을 바꿈", "저장" in str(app._saved_lbl.cget("text")), app._saved_lbl.cget("text"))
    # 시계 DM은 무장 한 번에 한 번(종전 창과 같은 주기)
    DM.clear()
    app._clock_warn_check()
    ck("3j 시계 DM은 어긋남 한 번에 한 번", not [k for k, _, _ in DM if k == "clock"], DM)
    ROOT.destroy()

print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
