#!/usr/bin/env python3
# EQ Autopilot 2026.11.10e - 리뷰 수리 시험(2026-11-10). 0 = 통과.
#   P0 통과로 표시된 평가 계좌(Topstep, Lucid)는 회원이 '통과 표시 풀기'를 고르기 전에는 다시 진입하지 않는다 - 같은 실행, [라이브 시작], 재시작 뒤
#      모두. _prop_auto_type = 단계 없음, 프롭 창은 펀디드를 미리 고르지 않고 저장해도 통과 기록을 지우지 않는다, 줄 글 = 진입 안 함.
#      서버가 이익 목표를 올려 잔고가 새 통과 금액 아래면 회원에게 묻고(답 전에는 통과 표시 그대로) 답에 따라 푼다.
#   P1 통과 확인 대기 안내가 없어진 칸(통과 목표 잔고)을 말하지 않는다, [평가로 두기]가 그 거래일의 멈춤을 푼다(하루 상한만).
#   P2 21d 출금 5회 펀디드 줄은 진입하지 않는다(회원이 다시 켜기 전), 행을 숫자 id Topstep 계좌로 바꾸면 이름을 알기 전에는 프롭 1R 없음.
# HOME = 임시 폴더, 네트워크 차단, 주문 없음, 알림 없음.
import inspect
import os
import socket
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_r1110e_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ.pop("EQ_DAYCAP_OFF", None)
os.environ.pop("EQ_PROFILE", None)


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402

assert E.APP_DIR.startswith(HOME), E.APP_DIR
_KC_LOAD_REAL = E._kc_load
E._kc_load = lambda *a, **k: ""
E._kc_save = lambda *a, **k: False
E._DAYCAP_SRV = {}
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:600]}"))
    if not c:
        FAIL.append(n)


ASKS = []
E.messagebox.askyesno = lambda t, m, **k: (ASKS.append(m), True)[1]
E.messagebox.showinfo = lambda t, m, **k: ASKS.append("INFO " + m)
E.messagebox.showwarning = lambda t, m, **k: ASKS.append("WARN " + m)


class T(E.App):
    def __init__(self, acfg, asset="NQ", lang="ko"):
        self._acfg, self._asset, self.lang, self.logs, self.saved = acfg, asset, lang, [], 0
        self._acct_widgets = {}
        self.skips, self.uiq, self.notices, self.alerts = [], [], [], []

    def log(self, m):
        self.logs.append(str(m))

    def _save_cfg(self, **k):
        self.saved += 1

    def _save_acct_widgets(self):
        self.saved += 1

    def _collect_acct_widgets(self):
        pass

    def _build(self):
        pass

    def _daycap_ui_refresh(self):
        pass

    def _daycap_rows_refresh(self):
        pass

    def _prop_reco_dot_paint(self):
        pass

    def _dc_tchg_update(self):
        pass

    def _daycap_pass_prompt(self, *a, **k):
        pass

    def _entry_skip_ev(self, asset, why):
        self.skips.append((asset, why))

    def _ui(self, fn, *a):
        self.uiq.append(fn)

    def _notice(self, title, msg, buttons=None):
        self.notices.append((str(msg), list(buttons or [])))

    def _member_alert(self, *a, **k):
        self.alerts.append(a)

    def _live_now(self, v=None):
        return False

    def _creds_of(self, asset, bk):
        return {}

    def _send_ev(self, *a, **k):
        pass


def run_ui(t):
    q, t.uiq = list(t.uiq), []
    for fn in q:
        fn()


def press(t, text):
    """마지막 알림 창의 버튼을 누른다(_notice가 기록한 버튼)."""
    msg, btns = t.notices[-1]
    fn = next(f for (x, f) in btns if str(x).startswith(text))
    if fn is not None:
        fn()


def acfg(nq=(), gc=(), nq_bk="projectx", gc_bk="projectx"):
    return {"NQ": {"broker": nq_bk, "include": True, "creds": {}, "accounts": list(nq)},
            "GC": {"broker": gc_bk, "include": True, "creds": {}, "accounts": list(gc)},
            "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}


def reset_state():
    for f in (E._DAYCAP_MON_PATH, E._DAYCAP_STATE_PATH):
        try:
            os.remove(f)
        except OSError:
            pass


class FakeBal:
    name = "nt8"

    def __init__(self, bal=None, name="nt8"):
        self.bal, self.calls, self.name = bal, [], name

    def account_balance(self, a):
        self.calls.append(("account_balance", a))
        return self.bal

    def account_netliq(self, a):
        return self.bal

    def day_realized(self, *a, **k):
        return 0.0

    def snapshot_fresh(self, *a):
        return True

    def closed_fills(self, s):
        return []


def plan(t, bk, sc, pr, arow, b, asset="NQ"):
    return E.App._daycap_plan(t, b, {"broker": bk, "pr": pr, "lbl": sc[-4:], "sc": sc, "risk": 1000.0}, arow, 101,
                              [("MNQ", 3, "MNQ 12-26")], asset, True)


LU, LU_UN, TS = "LDI150A7777", "LFE150B3333", "150KTC-V2-111-222"
P0 = dict(E._PROP_DEFAULTS)
LPR = dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=450.0, stage="eval", stage_for=LU.casefold())


def lrow(aid=LU, prop=None, label="lq"):
    return E._new_acct(500, aid, True, label, dict(prop or LPR), None, False, "nt8", lucid=True)


# ===== P0: 통과한 평가 계좌는 다시 진입하지 않는다 =====
print("\n== P0 통과 계좌 ==")
reset_state()
rq = lrow()
t = T(acfg(nq=[rq], nq_bk="nt8"))
E.App._daycap_mark_passed(t, "nt8", LU, bal=159200.0, pass_tp=159100.0, lbl="lq", size=150000, via="watch", target=9000.0, src="auto")
_pl1 = plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159200.0))
ck("a1 Lucid 통과 표시 뒤 같은 실행 = 건너뜀(pass_reached)", _pl1 == {"skip": True} and t.skips[-1] == ("NQ", "pass_reached"), (_pl1, t.skips))
ck("a1b 건너뜀 로그 = 진입하지 않음 + '통과 표시 풀기' 길(없어진 '통과 목표 잔고' 칸을 말하지 않음)",
   any("이번 신호에 진입하지 않습니다" in m and "통과 표시 풀기" in m for m in t.logs)
   and not any("통과 목표 잔고를 올리" in m or "통과 목표 잔고를 넣" in m for m in t.logs), t.logs[-2:])
t._dc_passed_run.clear()                                          # 새 [라이브 시작](종전엔 여기서 멈춤을 풀었다)
_pl2 = plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(150000.0))
ck("a2 [라이브 시작] 뒤(잔고가 규모로 돌아와도) = 여전히 건너뜀(pass_done)", _pl2 == {"skip": True} and t.skips[-1] == ("NQ", "pass_done"),
   (_pl2, t.skips))
ck("a2b [라이브 시작]이 통과 멈춤을 풀지 않는다(배선)", '_dc_passed_run").clear()' not in inspect.getsource(E.App._master_start))
t2 = T(acfg(nq=[rq], nq_bk="nt8"))                                # 재시작 = 메모리 새로, 상태 파일만
_pl3 = plan(t2, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(150000.0))
ck("a3 재시작 뒤 = 디스크의 통과 기록으로 건너뜀", _pl3 == {"skip": True} and t2.skips == [("NQ", "pass_done")]
   and t2._daycap_stage_cfg("nt8", LU, rq["prop"])[0] == "passed", (_pl3, t2.skips))
ck("a4 _prop_auto_type: 통과한 Lucid 평가 줄 = None(펀디드 아님)", E.App._prop_auto_type(t2, "nt8", LU, rq["prop"]) is None)
ck("a5 줄 글: 진입하지 않는다고 말함", "진입하지 않습니다" in E.App._daycap_row_text(t2, "NQ", rq), E.App._daycap_row_text(t2, "NQ", rq))
ck("a6 [프롭: …] 글자 = 통과(진입 안 함)", E.App._prop_btn_text(t2, rq["prop"], rq) == "프롭: 통과(진입 안 함)",
   E.App._prop_btn_text(t2, rq["prop"], rq))
t2.lang = "en"
ck("a6b EN 줄 글, 버튼", "does not enter" in E.App._daycap_row_text(t2, "NQ", rq)
   and E.App._prop_btn_text(t2, rq["prop"], rq) == "Prop: passed (no entries)")
t2.lang = "ko"
# 단계를 펀디드로 저장해도(단계 답만) 통과 기록은 그대로
E.App._lucid_stage_set(t2, LU, "funded")
ck("a7 단계 답을 펀디드로 저장해도 통과 기록 그대로, 'passed'",
   E.App._daycap_key("nt8", LU) in t2._dc_state()["passed"] and t2._daycap_stage_cfg("nt8", LU, rq["prop"])[0] == "passed",
   rq["prop"])
E.App._lucid_stage_set(t2, LU, "eval")
ck("a8 단계 답을 평가로 저장해도(unpass 없이) 통과 기록 그대로", E.App._daycap_key("nt8", LU) in t2._dc_state()["passed"])
# 단계 답이 없는 Lucid 이름 + 통과 기록 = passed
reset_state()
ru = lrow(LU_UN, dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=450.0))
t3 = T(acfg(nq=[ru], nq_bk="nt8"))
ck("a9 전제: 단계 답 없는 Lucid 이름은 unanswered", t3._daycap_stage_cfg("nt8", LU_UN, ru["prop"])[0] == "unanswered")
E.App._daycap_mark_passed(t3, "nt8", LU_UN, bal=159200.0, pass_tp=159100.0, lbl="u", size=150000, via="member", target=9000.0, src="member")
ck("a9b 단계 답이 없어도 통과 기록이 있으면 'passed' + 진입 계획 건너뜀",
   t3._daycap_stage_cfg("nt8", LU_UN, ru["prop"])[0] == "passed"
   and plan(t3, "nt8", LU_UN, ru["prop"], {"id": LU_UN, "name": LU_UN}, FakeBal(159200.0)) == {"skip": True})
# Topstep: 재시작 뒤에도 멈춤, 1R 단계 없음
reset_state()
rt = E._new_acct(500, TS, True, "ts", dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=500.0),
                 None, False, "projectx", topstep=True)
t4 = T(acfg(gc=[rt]), asset="GC")
E.App._daycap_mark_passed(t4, "projectx", TS, bal=159200.0, pass_tp=159050.0, lbl="ts", size=150000, via="watch", target=9000.0)
t5 = T(acfg(gc=[rt]), asset="GC")
_pl5 = plan(t5, "projectx", TS, rt["prop"], {"id": 1, "name": TS, "balance": 159300.0}, FakeBal(None, "projectx"), asset="GC")
ck("a10 Topstep 통과 = 재시작 뒤에도 건너뜀, _prop_auto_type None", _pl5 == {"skip": True}
   and E.App._prop_auto_type(t5, "projectx", TS, rt["prop"]) is None, _pl5)
ck("a11 Topstep 통과 로그: 없어진 '통과 목표 잔고' 칸을 말하지 않고 '통과 표시 풀기'",
   any("통과 표시 풀기" in m for m in t4.logs + t5.logs) and not any("통과 목표 잔고를 올리" in m for m in t4.logs + t5.logs),
   t4.logs + t5.logs)

# ===== P0: 서버가 이익 목표를 올림 =====
print("\n== P0 목표 상향 = 묻고 답에 따라 ==")
reset_state()
rq = lrow()
t = T(acfg(nq=[rq], nq_bk="nt8"))
E.App._daycap_mark_passed(t, "nt8", LU, bal=159200.0, pass_tp=159100.0, lbl="lq", size=150000, via="watch", target=9000.0, src="auto")
E._DAYCAP_SRV = E._prop_reco_validate({"targets": {"nt8": {"150000": 9500}}})
t.logs.clear()
ck("b1 잔고를 모를 때(화면, 1R) = 묻지 않고 통과 그대로",
   t._daycap_stage_cfg("nt8", LU, rq["prop"])[0] == "passed" and not t.uiq and not t.logs, (t.uiq, t.logs))
_pl = plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159200.0))
run_ui(t)
ck("b2 잔고 159,200 < 새 통과 금액 159,500 = 건너뜀 + 알림 창 한 번(통과 표시 풀기 / 통과로 두기) + 로그, 통과 기록 그대로",
   _pl == {"skip": True} and len(t.notices) == 1 and [str(x) for x, _f in t.notices[0][1]] == ["통과 표시 풀기", "통과로 두기"]
   and any("$9,500" in m and "통과 표시를 그대로" in m for m in t.logs)
   and E.App._daycap_key("nt8", LU) in t._dc_state()["passed"], (t.notices, t.logs))
plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159200.0))
run_ui(t)
ck("b3 같은 목표로는 다시 묻지 않음", len(t.notices) == 1, len(t.notices))
press(t, "통과로 두기")
ck("b4 [통과로 두기] = 통과 그대로, 그 목표가 기록됨(회원 확인)",
   (t._dc_state()["passed"].get(E.App._daycap_key("nt8", LU)) or {}).get("target") == 9500.0
   and t._dc_state()["passed"][E.App._daycap_key("nt8", LU)].get("src") == "member")
reset_state()
rt = E._new_acct(500, TS, True, "ts", dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=500.0),
                 None, False, "projectx", topstep=True)
t = T(acfg(gc=[rt]), asset="GC")
E._DAYCAP_SRV = {}
E.App._daycap_mark_passed(t, "projectx", TS, bal=159100.0, pass_tp=159050.0, lbl="ts", size=150000, via="watch", target=9000.0)
E._DAYCAP_SRV = E._prop_reco_validate({"targets": {"projectx": {"150000": 9500}}})
_pl = plan(t, "projectx", TS, rt["prop"], {"id": 1, "name": TS, "balance": 159200.0}, FakeBal(None, "projectx"), asset="GC")
run_ui(t)
ck("b5 Topstep도 종전처럼 저절로 풀지 않는다 - 건너뜀 + 물음", _pl == {"skip": True} and len(t.notices) == 1
   and E.App._daycap_key("projectx", TS) in t._dc_state()["passed"], (_pl, t.notices))
press(t, "통과 표시 풀기")
ck("b6 [통과 표시 풀기] = 기록 지움 + 로그", E.App._daycap_key("projectx", TS) not in t._dc_state()["passed"]
   and any("통과 표시를 풀었습니다" in m for m in t.logs), t.logs[-1:])
E._DAYCAP_SRV = {}

# ===== P0: 프롭 창 =====
print("\n== P0 프롭 창 ==")
try:
    import tkinter as tk
    from tkinter import ttk
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as _e:
    ROOT = None
    ck("Tk 사용 가능", False, _e)


def _walk(w):
    out = [w]
    for ch in w.winfo_children():
        out.extend(_walk(ch))
    return out


def open_dlg(t, idx=0):
    t.root = ROOT
    E.App._prop_dialog(t, idx)
    ROOT.update()
    return [x for x in ROOT.winfo_children() if isinstance(x, tk.Toplevel)][-1]


def radio(w, text):
    return next(x for x in _walk(w) if isinstance(x, ttk.Radiobutton) and str(x.cget("text")).startswith(text))


def cbox(w, text):
    return next((x for x in _walk(w) if isinstance(x, ttk.Checkbutton) and str(x.cget("text")).startswith(text)), None)


def save(w):
    next(x for x in _walk(w) if isinstance(x, ttk.Button) and str(x.cget("text")) in ("저장", "Save")).invoke()
    ROOT.update()


def labels(w):
    return [str(x.cget("text")) for x in _walk(w) if isinstance(x, ttk.Label)]


def close(w):
    try:
        if w.winfo_exists():
            w.destroy()
    except Exception:
        pass


if ROOT is not None:
    reset_state()
    rq = lrow()
    rg = lrow(label="lg")
    t = T(acfg(nq=[rq], gc=[rg], nq_bk="nt8", gc_bk="nt8"))
    E.App._daycap_mark_passed(t, "nt8", LU, bal=159200.0, pass_tp=159100.0, lbl="lq", size=150000, via="watch", target=9000.0, src="auto")
    w = open_dlg(t)
    ck("c1 통과한 Lucid 줄: 단계를 미리 고르지 않음(펀디드 아님), 안내 글 + '통과 표시 풀기' 체크",
       not radio(w, "펀디드").instate(["selected"]) and not radio(w, "챌린지").instate(["selected"])
       and any("평가 통과로 표시되어" in x for x in labels(w)) and cbox(w, "통과 표시 풀기") is not None, labels(w))
    save(w)
    ck("c2 그대로 저장 = 통과 기록 그대로, 단계 답 그대로(평가), 저장됨, 다음 신호도 건너뜀",
       E.App._daycap_key("nt8", LU) in t._dc_state()["passed"] and rq["prop"]["stage"] == "eval" and rq["prop"]["set"] is True
       and plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(150000.0)) == {"skip": True}, (rq["prop"], ASKS[-1:]))
    close(w)
    w = open_dlg(t)
    radio(w, "펀디드").invoke()
    save(w)
    ck("c3 펀디드를 골라 저장해도(풀기 없이) 통과 기록 그대로 - 'passed', 출금 알림 대상(펀디드) 아님, 건너뜀",
       E.App._daycap_key("nt8", LU) in t._dc_state()["passed"] and rq["prop"]["stage"] == "funded"
       and t._daycap_stage_cfg("nt8", LU, rq["prop"])[0] == "passed"
       and plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(150000.0)) == {"skip": True}, rq["prop"])
    close(w)
    w = open_dlg(t)
    cbox(w, "통과 표시 풀기").invoke()
    radio(w, "챌린지").invoke()
    save(w)
    ck("c4 '통과 표시 풀기' + 챌린지 저장 = 통과 기록 지움, 단계 평가, 로그",
       E.App._daycap_key("nt8", LU) not in t._dc_state()["passed"] and rq["prop"]["stage"] == "eval"
       and t._daycap_stage_cfg("nt8", LU, rq["prop"])[0] == "eval" and any("통과 표시를 풀었습니다" in m for m in t.logs), rq["prop"])
    close(w)
    # Topstep 통과 줄: 풀기
    reset_state()
    rt = E._new_acct(500, TS, True, "ts", dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=500.0),
                     None, False, "projectx", topstep=True)
    t = T(acfg(gc=[rt]), asset="GC")
    E.App._daycap_mark_passed(t, "projectx", TS, bal=159100.0, pass_tp=159050.0, lbl="ts", size=150000, via="watch", target=9000.0)
    w = open_dlg(t)
    ck("c5 Topstep 통과 줄: 단계 미리 고르지 않음 + 풀기 체크", not radio(w, "챌린지").instate(["selected"])
       and cbox(w, "통과 표시 풀기") is not None)
    save(w)
    ck("c6 그대로 저장 = 통과 그대로, 저장된 유형 그대로(챌린지)", E.App._daycap_key("projectx", TS) in t._dc_state()["passed"]
       and rt["prop"]["type"] == "test" and not any(a.startswith("WARN") for a in ASKS[-1:]), (rt["prop"], ASKS[-1:]))
    close(w)
    w = open_dlg(t)
    cbox(w, "통과 표시 풀기").invoke()
    save(w)
    ck("c7 풀기만 고르고 단계를 안 고르면 저장 안 함(단계를 고르라는 안내)", E.App._daycap_key("projectx", TS) in t._dc_state()["passed"]
       and ASKS and "계좌 단계" in ASKS[-1], ASKS[-1:])
    radio(w, "챌린지").invoke()
    save(w)
    ck("c8 풀기 + 챌린지 = 통과 기록 지움", E.App._daycap_key("projectx", TS) not in t._dc_state()["passed"])
    close(w)
    # 통과 아닌 줄에는 풀기 체크가 없다
    w = open_dlg(t)
    ck("c9 통과 아닌 줄 = 풀기 체크 없음", cbox(w, "통과 표시 풀기") is None)
    close(w)

# ===== P1: 통과 확인 대기 =====
print("\n== P1 통과 확인 대기 ==")
reset_state()
rq = lrow()
t = T(acfg(nq=[rq], nq_bk="nt8"))
_pl = plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
run_ui(t)
ck("d1 처음 보는 Lucid 계좌가 통과 금액 이상 = 표시 없이 건너뜀 + 알림 창([통과로 표시] / [평가로 두기])",
   _pl == {"skip": True} and t.skips == [("NQ", "pass_reached")] and len(t.notices) == 1
   and [str(x) for x, _f in t.notices[0][1]] == ["통과로 표시", "평가로 두기"], (t.skips, t.notices))
ck("d2 안내가 없어진 칸('통과 목표 잔고를 넣으십시오')을 말하지 않고 두 버튼을 말함",
   not any("통과 목표 잔고를 넣" in m for m in t.logs) and any("[평가로 두기]" in m and "[통과로 표시]" in m for m in t.logs)
   and "누르기 전에는 이 계좌로 진입하지 않습니다" in t.notices[0][0], t.logs)
press(t, "평가로 두기")
_pl2 = plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
ck("d3 [평가로 두기] 뒤 = 그 거래일에는 진입(감시 계획, 통과 익절 없음, 하루 상한 4,410 그대로)",
   bool(_pl2 and _pl2.get("monitor")) and _pl2["pass_tp"] == 0.0 and _pl2["cap"] == 4410.0, _pl2)
ck("d4 [평가로 두기] 로그: 통과 익절 없이 다시 진입(하루 이익 상한이 있는 요금제만 상한, 11h 최종 리뷰 C P2-7)",
   any("통과 익절 없이 다시 진입합니다(하루 이익 상한이 있는 요금제는 그 상한을 지킵니다)" in m for m in t.logs), t.logs[-1:])
_k = E.App._daycap_key("nt8", LU)
t._dc_state()["pass_hold"][_k]["ans"]["day"] = "2000-01-01"
ck("d5 다음 거래일(답의 날짜가 지남) = 다시 멈춤", plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0)) == {"skip": True})
# 재검토 P2(2026-11-10): 답 없이 닫은 창은 다음 신호에 다시
reset_state()
rq = lrow()
t = T(acfg(nq=[rq], nq_bk="nt8"))
plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
run_ui(t)
_n1 = len(t.notices)
t._dc_map("_dc_tn_open")[E.App._daycap_key("nt8", LU)] = True                    # 창이 떠 있음(화면 줄이 적는 표시)
plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
run_ui(t)
ck("d7 창이 떠 있는 동안 다음 신호 = 겹쳐 띄우지 않음", _n1 == 1 and len(t.notices) == 1, len(t.notices))
t._dc_map("_dc_tn_open").pop(E.App._daycap_key("nt8", LU), None)                # X로 닫음(Destroy가 표시를 뺀다)
for _ in range(3):                                         # 감시 회차(신호 아님) = 다시 띄우지 않음
    E.App._dc_pass_hold(t, "nt8", LU, "lq", 159050.0, 159000.0, "best", "Lucid", size=150000, target=9000.0)
run_ui(t)
ck("d8 답 없이 닫은 뒤 감시 회차 = 다시 띄우지 않음", len(t.notices) == 1, len(t.notices))
plan(t, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
run_ui(t)
ck("d9 답 없이 닫은 뒤 다음 신호 = 다시 띄움 + 버튼을 말하는 로그", len(t.notices) == 2
   and any("아직 답이 없어 다시 띄웁니다" in m and "[평가로 두기]" in m for m in t.logs), (len(t.notices), t.logs[-2:]))
t2 = T(acfg(nq=[rq], nq_bk="nt8"))                         # 재시작(창 없음, 답 없음) = 다음 신호에 다시
plan(t2, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
run_ui(t2)
ck("d10 재시작 뒤 답이 없으면 다음 신호에 다시", len(t2.notices) == 1, len(t2.notices))
press(t2, "평가로 두기")
t3 = T(acfg(nq=[rq], nq_bk="nt8"))
_pl3 = plan(t3, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
run_ui(t3)
ck("d11 답한 뒤(재시작해도) 다시 띄우지 않음, 그 거래일 진입", not t3.notices and bool(_pl3 and _pl3.get("monitor")), (t3.notices, _pl3))
if ROOT is not None:                                       # 실제 알림 창: 떠 있는 동안 표시, 제목 줄 X(창 닫힘)로 표시가 빠진다
    reset_state()
    t4 = T(acfg(nq=[rq], nq_bk="nt8"))
    t4.root = ROOT
    t4._notice = lambda title, msg, buttons=None: E.App._notice(t4, title, msg, buttons)
    plan(t4, "nt8", LU, rq["prop"], {"id": LU, "name": LU}, FakeBal(159050.0))
    run_ui(t4)
    ROOT.update()
    _k4 = E.App._daycap_key("nt8", LU)
    _wins = [x for x in ROOT.winfo_children() if isinstance(x, tk.Toplevel) and x.title() == "EQ Autopilot"]
    _open1 = _k4 in t4._dc_map("_dc_tn_open")
    for _w in _wins:
        _w.destroy()
    ROOT.update()
    ck("d12 실제 창: 떠 있는 동안 표시, 닫으면(X) 표시가 빠져 다음 신호에 다시 띄울 수 있다",
       _wins and _open1 and _k4 not in t4._dc_map("_dc_tn_open"), (len(_wins), _open1))
ck("d6 _dc_pass_hold, _daycap_mark_passed 원문에 없어진 칸 안내 없음",
   "통과 목표 잔고를 넣으십시오" not in inspect.getsource(E.App._dc_pass_hold)
   and "통과 목표 잔고를 올리십시오" not in inspect.getsource(E.App._daycap_mark_passed)
   and "통과 목표 잔고를 올리십시오" not in inspect.getsource(E.App._daycap_plan))

# ===== P2: 21d 출금 5회 =====
print("\n== P2 21d 출금 5회 ==")
XFA = "EXPRESS-V2-555-666"
E._PROP_LEGACY_NOTES.clear()
r5 = E._new_acct(500, XFA, True, "x", {"on": True, "type": "funded", "payouts": 5, "r_test": 1200.0, "r_steady": 300.0,
                                       "buffer": 6000.0}, None, False, "projectx", topstep=True)
ck("e1 21d 펀디드 + 출금 5회 = p5_hold", r5["prop"]["p5_hold"] is True and r5["prop"]["from21d"] is True, r5["prop"])
r5b = E._new_acct(500, XFA, True, "x", {"on": True, "type": "funded", "payouts": 5, "type_manual": False, "from21d": True,
                                        "mode": "own", "set": True}, None, False, "projectx", topstep=True)
ck("e2 21e~09d로 올라온 같은 줄(from21d 남음, 회원 선택 없음)도 p5_hold", r5b["prop"]["p5_hold"] is True, r5b["prop"])
r5c = E._new_acct(500, XFA, True, "x", {"on": True, "type": "funded", "payouts": 5, "type_manual": True, "mode": "own", "set": True},
                  None, False, "projectx", topstep=True)
ck("e3 회원이 프롭 창에서 고른 줄(type_manual) = 멈추지 않음", r5c["prop"]["p5_hold"] is False, r5c["prop"])
r5d = E._new_acct(500, XFA, True, "x", dict(r5["prop"], p5_hold=False), None, False, "projectx", topstep=True)
ck("e4 회원이 다시 켠 줄(p5_hold False 저장) = 다시 멈추지 않음", r5d["prop"]["p5_hold"] is False, r5d["prop"])
t = T(acfg(gc=[r5]), asset="GC")
E.App._log_prop_migrated(t) if hasattr(E.App, "_log_prop_migrated") else None
_cfg = {"broker": "projectx", "f1": "u", "f2": "k", "f3": "", "acct": XFA, "one_r": 500.0, "label": "x", "live": False,
        "prop": dict(r5["prop"])}
E.App._enter_account(t, _cfg, {"entry_ref": 20000.0}, "GC", "LONG", 19990.0, 1.0)
ck("e5 진입: 출금 5회 멈춤 줄 = 건너뜀(prop_done) + 다시 켜는 길 안내",
   t.skips == [("GC", "prop_done")] and any("출금 5회로 멈춘 진입 다시 켜기" in m for m in t.logs), (t.skips, t.logs))
ck("e6 [프롭: …] 글자 = 멈춤(출금 5회)", E.App._prop_btn_text(t, r5["prop"]) == "프롭: 멈춤(출금 5회)")
if ROOT is not None:
    w = open_dlg(t)
    ck("e7 프롭 창: 다시 켜기 체크 + 안내", cbox(w, "출금 5회로 멈춘 진입 다시 켜기") is not None)
    radio(w, "펀디드").invoke()
    save(w)
    ck("e8 그냥 저장 = 멈춤 그대로", t._acfg["GC"]["accounts"][0]["prop"].get("p5_hold") is True, t._acfg["GC"]["accounts"][0]["prop"])
    close(w)
    w = open_dlg(t)
    cbox(w, "출금 5회로 멈춘 진입 다시 켜기").invoke()
    radio(w, "펀디드").invoke()
    save(w)
    ck("e9 다시 켜기 + 저장 = 멈춤 풀림", t._acfg["GC"]["accounts"][0]["prop"].get("p5_hold") is False, t._acfg["GC"]["accounts"][0]["prop"])
    close(w)

# ===== P2: 숫자 id Topstep 계좌로 바꾼 줄 =====
print("\n== P2 숫자 id Topstep ==")
reset_state()
NUM = "87654321"
rn = E._new_acct(500, TS, True, "n", dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=500.0),
                 None, False, "projectx", topstep=True)
t = T(acfg(nq=[rn]))
ck("f0 전제: 150K Combine 줄 = 챌린지 1,750", E.App._prop_r_info(t, rn["prop"], "projectx", TS)[:2] == ("test", 1750.0))
t._daycap_reset_row("NQ", rn, TS, NUM)
rn["id"] = NUM
ck("f1 숫자 id로 바꿈 = stage_wait, 이름을 모르는 동안 1R None(옛 계좌 챌린지 1,750을 쓰지 않음)",
   rn["prop"]["stage_wait"] == NUM and E.App._prop_r_info(t, rn["prop"], "projectx", NUM)[1] is None)
ck("f2 1R 못 정함 = 이유 로그(Topstep 단계 모름)", E.App._prop_one_r(t, rn["prop"], {"broker": "projectx", "acct": NUM}, "n") is None
   and any("이 Topstep 계좌가 평가인지 펀디드인지 아직 몰라" in m for m in t.logs), t.logs[-1:])

ck("f2b [프롭: …] 글자 = 단계 선택(이름을 모르는 동안)", E.App._prop_btn_text(t, rn["prop"], rn) == "프롭: 단계 선택",
   E.App._prop_btn_text(t, rn["prop"], rn))
if ROOT is not None:
    w = open_dlg(t)
    ck("f2c 프롭 창: 옛 계좌의 단계를 미리 고르지 않음 + 고르라는 안내",
       not radio(w, "챌린지").instate(["selected"]) and not radio(w, "펀디드").instate(["selected"])
       and any("단계를 알지 못합니다" in x for x in labels(w)), labels(w))
    close(w)


class FakePX:
    name = "projectx"

    def __init__(self, nm):
        self.nm = nm

    def _accounts(self, **k):
        return [{"id": int(NUM), "name": self.nm, "balance": 0.0}]


E._build_broker = lambda bk, f1, f2, f3, accts: FakePX("EXPRESS-V2-999-" + NUM)
ck("f3 진입 직전 이름 읽기(_prop_px_learn) = 이름 기억", E.App._prop_px_learn(t, {"broker": "projectx", "acct": NUM, "f1": "u", "f2": "k"})
   and t._dc_state()["names"].get("projectx|" + NUM) == "EXPRESS-V2-999-" + NUM)
ck("f4 이름을 알면 = 펀디드 500(옛 계좌 챌린지 아님)", E.App._prop_r_info(t, rn["prop"], "projectx", NUM)[:2] == ("funded", 500.0),
   E.App._prop_r_info(t, rn["prop"], "projectx", NUM))
rn2 = E._new_acct(500, TS, True, "n", dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=500.0),
                  None, False, "projectx", topstep=True)
t._daycap_reset_row("NQ", rn2, TS, "50KTC-V2-1-2")
ck("f5 이름 있는 id로 바꾸면 stage_wait 없음", rn2["prop"]["stage_wait"] is None)
rr = E._new_acct(500, NUM, True, "n", dict(rn["prop"]), None, False, "projectx", topstep=True)
ck("f6 stage_wait는 설정 저장, 다시 읽기를 넘어 남는다", rr["prop"]["stage_wait"] == NUM, rr["prop"])
# 재검토 P2(2026-11-10): stage_wait이 끝없이 막지 않는다
reset_state()
NUM2 = "55554444"
rk = E._new_acct(700, TS, True, "k", dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=500.0),
                 None, False, "projectx", topstep=True)
t = T(acfg(nq=[rk]))
t._daycap_reset_row("NQ", rk, TS, NUM2)
rk["id"] = NUM2
os.environ["EQ_DAYCAP_OFF"] = "1"
ck("f7 킬스위치(EQ_DAYCAP_OFF=1) = 기다리지 않음(단계 판별을 하지 않는다)", not E.App._prop_px_wait(t, rk["prop"], "projectx", NUM2)
   and E.App._prop_px_settle(t, "projectx", NUM2) == 0 and rk["prop"]["stage_wait"] == NUM2)
os.environ.pop("EQ_DAYCAP_OFF", None)
ck("f8 전제: 이름을 모르면 기다림", E.App._prop_px_wait(t, rk["prop"], "projectx", NUM2))
E._build_broker = lambda bk, f1, f2, f3, accts: type("PX", (), {"_accounts": lambda self, **k: [{"id": int(NUM2), "name": "MY-OWN-ACCOUNT"}]})()
t.uiq.clear()
t.logs.clear()
_cfgk = {"broker": "projectx", "f1": "u", "f2": "k", "f3": "", "acct": NUM2, "one_r": 700.0, "label": "k", "live": False,
         "prop": rk["prop"]}
_sized = []
_orig_cs = None
try:
    from eqexec import sizing as _sz_mod
    _orig_cs = _sz_mod.compute_size
    _sz_mod.compute_size = lambda asset, broker, r, entry, stop, d: (_sized.append(r), None)[1]
except Exception:
    _sz_mod = None
E.App._enter_account(t, _cfgk, {"entry_ref": 20000.0}, "NQ", "LONG", 19990.0, 1.0)
if _sz_mod is not None:
    _sz_mod.compute_size = _orig_cs
ck("f9 이름이 Topstep 형식이 아님 = 프롭 계좌가 아닌 것으로 보고 이 줄의 1R(700)로 사이징(건너뛰지 않음)",
   _sized == [700.0] and ("NQ", "prop_r") not in t.skips, (_sized, t.skips, t.logs[-2:]))
t._sig_accts = {("NQ", 0): {"broker": "projectx", "acct": NUM2, "prop": dict(rk["prop"])}}
run_ui(t)
ck("f10 그 줄의 프롭 끔 + stage_wait 지움 + 로그 한 줄(무장 사본도)", rk["prop"]["on"] is False and rk["prop"]["stage_wait"] is None
   and t._sig_accts[("NQ", 0)]["prop"]["on"] is False and t._sig_accts[("NQ", 0)]["prop"]["stage_wait"] is None
   and sum(1 for m in t.logs if "프롭 계좌가 아닌 것으로 보고" in m) == 1 and t.saved >= 1, (rk["prop"], t.logs[-2:]))
ck("f11 다시 정리해도 로그 늘지 않음", E.App._prop_px_settle(t, "projectx", NUM2) == 0
   and sum(1 for m in t.logs if "프롭 계좌가 아닌 것으로 보고" in m) == 1)
reset_state()
rk2 = E._new_acct(700, TS, True, "k", dict(P0, on=True, type="test", mode="own", set=True, r_test=1750.0, r_steady=500.0),
                  None, False, "projectx", topstep=True)
t = T(acfg(nq=[rk2]))
t._daycap_reset_row("NQ", rk2, TS, NUM2)
rk2["id"] = NUM2
E.App._prop_px_seen(t, NUM2, "EXPRESS-V2-1-" + NUM2)
E.App._prop_px_settle(t, "projectx", NUM2)
ck("f12 이름이 형식에 맞으면 stage_wait만 지우고 프롭은 그대로(펀디드 500)", rk2["prop"]["on"] is True and rk2["prop"]["stage_wait"] is None
   and E.App._prop_r_info(t, rk2["prop"], "projectx", NUM2)[:2] == ("funded", 500.0), rk2["prop"])

# ===== P2: 키체인 읽기 실패와 없음 가르기 =====
print("\n== P2 키체인 읽기 실패 ==")
import subprocess as _sp
import types as _types
_run0, _mac0 = _sp.run, E._IS_MAC
try:
    E._IS_MAC = True
    for _rc, _want in ((0, False), (44, False), (36, True), (51, True)):
        E._KC_READ_ERRS.clear()
        _sp.run = lambda *a, _r=_rc, **k: _types.SimpleNamespace(returncode=_r, stdout=("v" if _r == 0 else ""))
        _v = _KC_LOAD_REAL("eq:BTC:bybit:f1")
        ck(f"h1 macOS security 종료 코드 {_rc} = " + ("읽기 실패" if _want else "읽기 됨"),
           ("eq:BTC:bybit:f1" in E._KC_READ_ERRS) == _want and _v == ("v" if _rc == 0 else ""), (_v, E._KC_READ_ERRS))

    def _raise(*a, **k):
        raise OSError("timeout")
    _sp.run = _raise
    E._KC_READ_ERRS.clear()
    ck("h2 macOS 호출 예외(시간 초과) = 읽기 실패", _KC_LOAD_REAL("eq:X") == "" and "eq:X" in E._KC_READ_ERRS)
    E._IS_MAC = False
    _kr = _types.ModuleType("keyring")
    _kr.get_password = lambda svc, acc: (_ for _ in ()).throw(RuntimeError("backend"))
    sys.modules["keyring"] = _kr
    E._KC_READ_ERRS.clear()
    ck("h3 keyring 예외 = 읽기 실패", _KC_LOAD_REAL("eq:Y") == "" and "eq:Y" in E._KC_READ_ERRS)
    _kr.get_password = lambda svc, acc: None
    ck("h4 keyring 없음(None) = 읽기 됨(실패 기록 지움)", _KC_LOAD_REAL("eq:Y") == "" and "eq:Y" not in E._KC_READ_ERRS)
finally:
    _sp.run, E._IS_MAC = _run0, _mac0
    sys.modules.pop("keyring", None)
    E._KC_READ_ERRS.clear()

# ===== 문구 =====
print("\n== 문구 ==")
_srcs = [inspect.getsource(getattr(E.App, n)) for n in ("_dc_target_up_ask", "_dc_pass_hold", "_dc_pass_target_notice", "_daycap_plan",
                                                         "_daycap_mark_passed", "_prop_px_wait", "_sched_retime", "_prune_blank_rows")]
ck("g1 새 문구에 가운뎃점 없음", all("·" not in s_ for s_ in _srcs) and "·" not in E._UNPASS_HOW_KO)

if ROOT is not None:
    try:
        ROOT.destroy()
    except Exception:
        pass
print("\n" + ("FAILED: " + "; ".join(FAIL) if FAIL else "전부 통과"))
sys.exit(1 if FAIL else 0)
