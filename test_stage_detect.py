#!/usr/bin/env python3
# 평가 계좌 단계 자동 판별(대표 2026-10-14 결정 4, 회원 설정 없음) 오프라인 시험. 0 = 통과.
#   1 Topstep: 이름(50KTC/100KTC/150KTC = 평가와 규모, EXPRESS = 펀디드, PRAC = 연습, 그 밖 = 모름) x 잔고 확인(±10%) x 저장된 프롭 유형
#     - 이름이 정해 주면 이름이 이긴다, 옛 기본값 'test'가 저장된 EXPRESS 계좌는 상한이 걸리지 않는다(진입 계획 None, 브로커 호출 없음)
#   2 Lucid: 이름(L + D/F/P + I/E + 025/050/100/150)에 단계가 없어 한 번 묻는다 - 묻는 대상, 한 번만(창을 닫아도, 재시작해도),
#     답하기 전 상한 없음(안전 쪽), 답은 같은 계좌 모든 자산 행에, 새 계좌가 생기면 그 계좌만 묻기, 펀디드 답 = 상한 없음, NT8의 다른 이름 = 끔
#   3 통과 표시: 어느 경로로든 통과 목표에 닿으면 'passed'(상한, 통과 익절 끔) - Lucid 같은 계좌 펀디드 전환을 덮는다, 프롭 설정을 다시
#     고르면 풀린다, Topstep은 잔고가 규모 이하(초기화)면 풀린다
#   4 실제 Tk 창: Lucid 질문 창 [평가] 누르면 저장, 닫으면 다시 묻지 않음(문구 KO/EN)
# HOME, APPDATA = 임시 폴더, 네트워크 차단, EQ_NO_ALERT=1.
import datetime as _dt
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_stage_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_DAYCAP_OFF", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui as E  # noqa: E402

assert E.APP_DIR.startswith(HOME), E.APP_DIR
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


P0 = dict(E._PROP_DEFAULTS)
S = E._daycap_stage

# ── 1 Topstep 판별 행렬 ──────────────────────────────────────────────────────────
_mx = [  # (이름, 잔고, 저장된 프롭, 기대 단계, 기대 규모)
    ("150KTC-V2-26505604", None, P0, "eval", 150000),
    ("150ktc-v2-1", 151200.0, P0, "eval", 150000),
    ("100KTC-A", 104000.0, P0, "eval", 100000),
    ("50KTC-B", 53000.0, P0, "eval", 50000),
    ("50KTC-B", 56000.0, P0, "eval", 50000),              # 통과 목표(53,000) 이상은 띠 밖이어도 평가 - 진입, 감시가 '통과'로 표시(2차 리뷰 P2)
    ("50KTC-B", 60000.0, P0, "mismatch", 50000),          # 규모 + 목표 x 3(59,000)을 넘으면 평가로 보지 않는다(끔)
    ("50KTC-B", 44000.0, P0, "mismatch", 50000),          # 규모 -12% = 이름을 믿지 않는다(끔)
    ("150KTC-A", 50000.0, P0, "mismatch", 150000),
    ("150KTC-A", 140000.0, P0, "eval", 150000),          # -6.7%(최대 손실 안)
    ("150KTC-A", None, dict(P0, on=True, type="funded"), "eval", 150000),   # 이름이 이긴다(저장된 유형은 안 본다)
    ("150KTC-A", None, dict(P0, on=True, type="live"), "eval", 150000),
    ("EXPRESS-V2-DLL-123", 0.0, dict(P0, on=True, type="test"), "funded", 0),   # 옛 기본값 test + 펀디드 이름 = 펀디드
    ("EXPRESS-V2-DLL-123", None, P0, "funded", 0),
    ("express-v2-9", 2500.0, P0, "funded", 0),
    ("XFA-150K-1", None, P0, "funded", 0),
    ("PRAC-V2-150K-1", 150000.0, dict(P0, on=True, type="test"), "practice", 0),
    ("prac-1", None, P0, "practice", 0),
    ("77777", 150000.0, dict(P0, on=True, type="test"), "unknown", 0),     # 숫자 id(이름 모름) + 프롭 test = 끔
    ("S1-150KTC", None, P0, "unknown", 0),               # 앞이 Combine 꼴이 아니면 모름
    ("", None, P0, "unknown", 0),
]
_bad = [(nm, bal, S("projectx", pr, nm, bal)) for nm, bal, pr, st, sz in _mx if S("projectx", pr, nm, bal) != (st, sz)]
ck(f"1a Topstep 이름 x 잔고 x 저장 유형 판별 행렬 {len(_mx)}칸", not _bad, _bad)
ck("1b 평가만 상한(_daycap_resolve): EXPRESS + 옛 test = None, PRAC = None, 모르는 이름 = None, Combine = 상한 4,860",
   E._daycap_resolve("projectx", dict(P0, on=True, type="test"), "EXPRESS-V2-DLL-1") is None
   and E._daycap_resolve("projectx", P0, "PRAC-V2-1") is None
   and E._daycap_resolve("projectx", dict(P0, on=True, type="test"), "77777") is None
   and E._daycap_resolve("projectx", P0, "150KTC-V2-1")["cap"] == 4860.0)
ck("1c 다른 브로커(Tradovate, IBKR, Bybit, Bitget)는 판별 대상 아님", all(S(bk, P0, "150KTC-A")[0] == "none"
                                                             for bk in ("tradovate", "ibkr", "bybit", "bitget")))


class Stub:
    lang = "ko"

    def __init__(self):
        self.logs, self.skips, self.asked, self.saved, self.built = [], [], [], 0, 0
        self._acfg, self._sig_accts, self._dc_mon = {}, {}, {}
        self.root = SimpleNamespace(after=lambda *a, **k: None)

    def log(self, m):
        self.logs.append(str(m))

    def _acct_broker(self, asset, acct):
        return (acct or {}).get("broker") or "projectx"

    def _entry_skip_ev(self, asset, why):
        self.skips.append((asset, why))

    def _save_cfg(self, **k):
        self.saved += 1

    def _build(self):
        self.built += 1

    def _lucid_stage_ask(self, aid, lbl):
        self.asked.append(aid)

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


_OWN = {"_lucid_stage_ask"}
for _n, _v in E.App.__dict__.items():
    if _n.startswith(("_daycap", "_dc_", "_lucid", "_reco", "_apply_reco", "_passtp_buffer")) and _n not in _OWN:
        setattr(Stub, _n, _v)
Stub._planned_one_r = E.App.__dict__["_planned_one_r"]


class FakeBal:
    """진입 계획용: 잔고만 읽히는 가짜(호출 기록)."""
    name = "projectx"

    def __init__(self, bal=None):
        self.bal, self.calls = bal, []

    def account_balance(self, a):
        self.calls.append(("account_balance", a))
        return self.bal

    def day_realized(self, *a, **k):
        self.calls.append(("day_realized",))
        return 0.0

    def snapshot_fresh(self, *a):
        return True

    def account_netliq(self, a):
        self.calls.append(("account_netliq", a))
        return self.bal

    def closed_fills(self, s):
        return []


def _reset():
    for f in (E._DAYCAP_MON_PATH, E._DAYCAP_STATE_PATH):
        try:
            os.remove(f)
        except OSError:
            pass


def plan(st, bk, sc, pr, arow, b, asset="GC"):
    return E.App._daycap_plan(st, b, {"broker": bk, "pr": pr, "lbl": sc[-4:], "sc": sc, "risk": 1000.0}, arow, 101,
                              [("MGC", 3, "CON.F.US.MGC.Z26")], asset, True)


_reset()
st = Stub()
b = FakeBal()
ck("1d 진입 계획: 펀디드 이름 + 저장된 'test'(옛 기본값) = None(상한 없음), 브로커 호출 0",
   plan(st, "projectx", "EXPRESS-V2-DLL-1", dict(P0, on=True, type="test"),
        {"id": 101, "name": "EXPRESS-V2-DLL-1", "balance": 0.0}, b) is None and b.calls == [] and not st.skips, b.calls)
ck("1e 진입 계획: 연습, 모르는 이름 = None, 브로커 호출 0",
   plan(st, "projectx", "PRAC-V2-1", P0, {"id": 101, "name": "PRAC-V2-1", "balance": 150000.0}, b) is None
   and plan(st, "projectx", "77777", dict(P0, on=True, type="test"), {"id": 77777, "name": "77777", "balance": 150000.0}, b) is None
   and b.calls == [])
ck("1f 행에 숫자 id만 있어도 계좌 목록의 이름(Combine)으로 평가 판별 + 이름 기억",
   plan(st, "projectx", "26505604", P0, {"id": 26505604, "name": "150KTC-V2-26505604", "balance": 150500.0}, b).get("monitor")
   and E.App._dc_state(st)["names"].get("projectx|26505604") == "150KTC-V2-26505604")
ck("1g 기억한 이름으로 행 상태 줄도 평가(네트워크 없이)", E.App._daycap_row_text(
    st, "GC", {"id": "26505604", "prop": dict(P0), "on": True, "broker": "projectx"}).startswith("익절: 하루 상한 $4,860"))
_r = plan(st, "projectx", "150KTC-Z", P0, {"id": 1, "name": "150KTC-Z", "balance": 50000.0}, b)
_r2 = plan(st, "projectx", "150KTC-Z", P0, {"id": 1, "name": "150KTC-Z", "balance": 50000.0}, b)
ck("1h 이름과 잔고가 안 맞으면 상한 끔(None) + 안내는 한 번", _r is None and _r2 is None
   and sum(1 for m in st.logs if "계좌 이름의 규모(150K)와 잔고($50,000)가 맞지 않아" in m) == 1, st.logs[-3:])

# ── 2 Lucid 한 번 묻기 ───────────────────────────────────────────────────────────
LU, LU2 = "LDI150A1234", "LFE050B9876"
ck("2a Lucid 이름 판별: 단계 답 없음 = unanswered(규모는 이름에서), 평가 답 = eval, 펀디드 답 = funded, 다른 NT8 이름 = unknown",
   S("nt8", P0, LU) == ("unanswered", 150000) and S("nt8", dict(P0, stage="eval"), LU) == ("eval", 150000)
   and S("nt8", dict(P0, stage="funded"), LU) == ("funded", 150000) and S("nt8", dict(P0, stage="eval"), "Sim101") == ("unknown", 0)
   and S("nt8", dict(P0, stage="eval", on=True, type="test"), "NT12345") == ("unknown", 0)
   and S("nt8", dict(P0, stage="eval"), "lpi025x") == ("eval", 25000)
   and S("nt8", dict(P0, stage="eval"), "LDP025X") == ("unknown", 0))
ck("2b 옛 판별(프롭 켜짐 + test)만으로는 Lucid 평가가 아님 - 답하기 전엔 상한 없음(안전 쪽)",
   E._daycap_resolve("nt8", dict(P0, on=True, type="test"), LU) is None)
_reset()
st = Stub()
st._acfg = {"NQ": {"accounts": [{"id": LU, "prop": dict(P0), "on": True, "broker": "nt8", "label": "Lucid A"},
                                {"id": "Sim101", "prop": dict(P0), "on": True, "broker": "nt8"}]},
            "GC": {"accounts": [{"id": LU, "prop": dict(P0), "on": True, "broker": "nt8"},
                                {"id": LU2, "prop": dict(P0, stage="funded"), "on": True, "broker": "nt8"}]}}
E.App._lucid_stage_check(st)
ck("2c 단계 답이 없는 Lucid 계좌만 묻는다(Sim101, 이미 답한 계좌 제외), 계좌 하나에 한 번(NQ, GC 행 공통)",
   st.asked == [LU], st.asked)
E.App._lucid_stage_check(st)
ck("2d 창이 떠 있는 동안 다시 묻지 않음", st.asked == [LU], st.asked)
st._lucid_asking = False                                   # 회원이 답하지 않고 창을 닫음
E.App._lucid_stage_check(st)
ck("2e 창을 닫아도 다시 묻지 않음(한 번만)", st.asked == [LU], st.asked)
_pl = plan(st, "nt8", LU, dict(P0), {"id": LU, "name": LU}, FakeBal(150000.0), asset="NQ")
ck("2f 답하기 전 = 상한 없이 종전대로(None) + 안내 한 번 + 잔고도 안 읽음", _pl is None
   and any("평가인지 펀디드인지 아직 답을 받지 못해" in m for m in st.logs), st.logs[-2:])
_row = st._acfg["NQ"]["accounts"][0]
ck("2g 답하기 전 상태 줄 = 차분한 안내(빨간 오류 아님)",
   E.App._daycap_row_text(st, "NQ", _row) == "Lucid 계좌가 평가인지 펀디드인지 아직 모릅니다. 프롭 설정에서 고르시면 평가 계좌 익절이 켜집니다")
st2 = Stub()
st2._acfg = st._acfg
E.App._lucid_stage_check(st2)
ck("2h 재시작해도(상태 파일) 다시 묻지 않음", st2.asked == [], st2.asked)
E.App._lucid_stage_set(st, LU, "eval")
ck("2i 답(평가)은 같은 계좌의 모든 자산 행에 저장 + 설정 저장 + 로그",
   st._acfg["NQ"]["accounts"][0]["prop"]["stage"] == "eval" and st._acfg["GC"]["accounts"][0]["prop"]["stage"] == "eval"
   and st._acfg["NQ"]["accounts"][1]["prop"].get("stage") is None and st.saved >= 1
   and any("평가로 저장했습니다" in m for m in st.logs), st.logs[-1:])
_pl = plan(st, "nt8", LU, dict(P0), {"id": LU, "name": LU}, FakeBal(150000.0), asset="NQ")
ck("2j 평가 답 뒤 = 상한 + 통과 감시 계획(GC 행 prop만 답을 들고 있어도 같은 계좌로 본다)", bool(_pl and _pl.get("monitor"))
   and _pl["cap"] == 4410.0 and _pl["pass_tp"] == 159050.0, _pl)
st._acfg["NQ"]["accounts"].append({"id": "LDE100C5555", "prop": dict(P0), "on": True, "broker": "nt8"})
st._lucid_asking = False
E.App._lucid_stage_check(st)
ck("2k 새 Lucid 계좌가 생기면 그 계좌만 한 번 묻는다", st.asked == [LU, "LDE100C5555"], st.asked)
E.App._lucid_stage_set(st, LU, "funded")
ck("2l 펀디드로 바꾸면 상한 없음(None)", plan(st, "nt8", LU, dict(P0), {"id": LU, "name": LU}, FakeBal(150000.0), asset="NQ") is None)
ck("2m 프롭 창 저장도 같은 경로(단계를 고르면 묻기 기록 + _lucid_stage_set)",
   "self._lucid_stage_set(_aidm, newp.get(\"stage\"))" in __import__("inspect").getsource(E.App._prop_dialog))

# ── 3 통과 표시 ─────────────────────────────────────────────────────────────────
_reset()
st = Stub()
st._acfg = {"NQ": {"accounts": [{"id": LU, "prop": dict(P0, stage="eval"), "on": True, "broker": "nt8"}]}}
_pl = plan(st, "nt8", LU, dict(P0, stage="eval"), {"id": LU, "name": LU}, FakeBal(159050.0), asset="NQ")
# 3차 리뷰 P0, P2: 처음 보는 Lucid 계좌(그 전 거래일 손익을 모름)는 잔고가 목표 기준 통과 금액 이상이어도 통과로 표시하지 않는다(표시는 상한을
# 끄고 다음 [라이브 시작]부터 펀디드로 본다) - 건너뛰고 확인 안내.
ck("3a0 처음 보는 Lucid 계좌 잔고 ≥ 159,000 → 통과 표시 없이 건너뜀 + pass_reached 한 번 + Lucid 화면 확인 안내",
   _pl == {"skip": True} and st.skips == [("NQ", "pass_reached")] and "nt8|ldi150a1234" not in E.App._dc_state(st)["passed"]
   and any("통과로 표시하지 않았습니다" in m and "Lucid 화면에서" in m for m in st.logs), (st.skips, st.logs[-2:]))
_reset()
st = Stub()
st._acfg = {"NQ": {"accounts": [{"id": LU, "prop": dict(P0, stage="eval"), "on": True, "broker": "nt8"}]}}
_ds, _d = [], E._daycap_day_start()[1]
for _ in range(3):                                         # 처음(150,000)부터 빠짐없이 본 계좌: 3,000 / 3,000 / 3,050
    _x = _dt.date.fromisoformat(_d) - _dt.timedelta(days=1)
    while _x.weekday() >= 5:
        _x -= _dt.timedelta(days=1)
    _d = _x.isoformat()
    _ds.insert(0, _d)
E.App._dc_state(st)["nt8_bases"]["nt8|ldi150a1234"] = dict(zip(_ds, (150000.0, 153000.0, 156000.0)))
E.App._dc_state(st)["days"]["nt8|ldi150a1234"] = {_ds[0]: 3000.0, _ds[1]: 3000.0}
_pl = plan(st, "nt8", LU, dict(P0, stage="eval"), {"id": LU, "name": LU}, FakeBal(159050.0), asset="NQ")
ck("3a Lucid 잔고 ≥ 통과 목표(150K + 9,000, 처음부터 본 계좌) → 통과 표시 + 이번 신호 pass_reached",
   _pl == {"skip": True} and st.skips == [("NQ", "pass_reached")] and "nt8|ldi150a1234" in E.App._dc_state(st)["passed"],
   (st.skips, st.logs[-3:]))
ck("3b 통과 = 단계 'passed'(상한, 통과 익절 꺼짐) - 같은 계좌가 펀디드가 되어도 상한이 걸리지 않는다",
   E.App._daycap_stage_cfg(st, "nt8", LU, dict(P0, stage="eval"), bal=150000.0)[0] == "passed"
   and E.App._daycap_resolve_cfg(st, "nt8", LU, dict(P0, stage="eval"), bal=150000.0) is None)
st._dc_passed_run.clear()                                  # 새 [라이브 시작]
ck("3c Lucid 통과 계좌는 새 [라이브 시작]부터 상한 없이 종전대로 진입(잔고가 규모로 돌아와도 표시 유지)",
   plan(st, "nt8", LU, dict(P0, stage="eval"), {"id": LU, "name": LU}, FakeBal(150000.0), asset="NQ") is None
   and "nt8|ldi150a1234" in E.App._dc_state(st)["passed"])
E.App._lucid_stage_set(st, LU, "eval")
ck("3d 프롭 설정(또는 질문)에서 단계를 다시 고르면 통과 표시가 풀린다", "nt8|ldi150a1234" not in E.App._dc_state(st)["passed"])
_reset()
st = Stub()
E.App._daycap_mark_passed(st, "projectx", "150KTC-A", bal=159100.0, pass_tp=159000.0, lbl="A", size=150000, via="watch")
ck("3e Topstep 통과 표시 + 1R 제안 기록(한 번)", "projectx|150ktc-a" in E.App._dc_state(st)["passed"]
   and "projectx|150ktc-a" in E.App._dc_state(st)["pass_prompted"])
ck("3f Topstep 잔고가 규모 이하(초기화, 새로 시작)면 통과 표시를 푼다",
   E.App._daycap_stage_cfg(st, "projectx", "150KTC-A", P0, bal=150000.0)[0] == "eval"
   and "projectx|150ktc-a" not in E.App._dc_state(st)["passed"])

# ── 4 실제 Tk 창: Lucid 질문 ──────────────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as _te:
    ROOT = None
    print(f"SKIP 4 Tk를 열 수 없어 창 시험을 건너뜁니다({_te})")
if ROOT is not None:
    def _walk(w):
        out = [w]
        for c in w.winfo_children():
            out += _walk(c)
        return out

    def _texts(win):
        return [str(x.cget("text")) for x in _walk(win) if isinstance(x, (ttk.Label, ttk.Button))]

    def _btn(win, txt):
        return next(x for x in _walk(win) if isinstance(x, ttk.Button) and str(x.cget("text")) == txt)

    def _tops():
        return [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]

    class TkStub(Stub):
        pass
    TkStub._lucid_stage_ask = E.App.__dict__["_lucid_stage_ask"]
    _reset()
    st = TkStub()
    st.root = ROOT
    st._acfg = {"NQ": {"accounts": [{"id": LU, "prop": dict(P0), "on": True, "broker": "nt8"}]}}
    E.App._lucid_stage_check(st)
    _wins = [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]
    ck("4a 질문 창 하나 + 문구 그대로(KO)", len(_wins) == 1 and "이 Lucid 계좌는 평가입니까, 펀디드입니까?" in _texts(_wins[0])
       and "평가" in _texts(_wins[0]) and "펀디드" in _texts(_wins[0]) and f"Lucid LDI150 …{LU[-4:]}" in _texts(_wins[0]),
       _texts(_wins[0]) if _wins else None)
    _btn(_wins[0], "평가").invoke()
    ROOT.update()
    ck("4b [평가] 누르면 저장 + 창 닫힘", st._acfg["NQ"]["accounts"][0]["prop"]["stage"] == "eval"
       and not [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)])
    st.lang = "en"
    st._acfg["NQ"]["accounts"].append({"id": LU2, "prop": dict(P0), "on": True, "broker": "nt8"})
    E.App._lucid_stage_check(st)
    _w2 = [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]
    ck("4c EN 문구 짝", len(_w2) == 1 and "Is this Lucid account an evaluation or funded?" in _texts(_w2[0])
       and "Evaluation" in _texts(_w2[0]) and "Funded" in _texts(_w2[0]), _texts(_w2[0]) if _w2 else None)
    ROOT.tk.call(_w2[0].protocol("WM_DELETE_WINDOW"))
    ROOT.update()
    ck("4d 창을 닫으면(답 없음) 저장 없음 + 안내 로그 + 다시 묻지 않음",
       st._acfg["NQ"]["accounts"][1]["prop"].get("stage") is None and any("no stage was chosen" in m for m in st.logs)
       and (E.App._lucid_stage_check(st) or True) and not [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)])

    # 4e~4h 프롭 설정 창(실제 Tk): Topstep은 판별 결과를 보여 주기만, Lucid는 단계를 고른다(같은 계좌 모든 행에 저장, 다시 묻지 않음)
    class PStub(Stub):
        pass
    for _pn in ("_prop_dialog", "_prop_btn_text"):
        setattr(PStub, _pn, E.App.__dict__[_pn])
    _reset()
    ps = PStub()
    ps.root = ROOT
    ps._acct_widgets = {}
    ps._asset = "NQ"
    ps._acfg = {"NQ": {"broker": "nt8", "accounts": [
        {"id": LU, "prop": dict(P0), "on": True, "broker": "nt8", "label": "LA"},
        {"id": "150KTC-V2-1", "prop": dict(P0), "on": True, "broker": "projectx", "label": "KT"},
        {"id": "EXPRESS-V2-DLL-1", "prop": dict(P0, on=True, type="test"), "on": True, "broker": "projectx", "label": "XF"}]},
        "GC": {"broker": "nt8", "accounts": [{"id": LU, "prop": dict(P0), "on": True, "broker": "nt8"}]}}
    ps._accts_of = lambda a: ps._acfg[a]["accounts"]
    ps.lang = "ko"
    E.App._prop_dialog(ps, 1)
    _t = _tops()
    ck("4e 프롭 창(Topstep Combine): 단계 = '평가 150K (계좌 이름으로 판별)'(고르는 칸 없음)",
       _t and "평가 150K (계좌 이름으로 판별)" in _texts(_t[0]), _texts(_t[0]) if _t else None)
    _t[0].destroy()
    E.App._prop_dialog(ps, 2)
    _t = _tops()
    ck("4f 프롭 창(EXPRESS + 옛 'test'): 단계 = 펀디드(익절 상한 없음)",
       _t and "펀디드 (계좌 이름으로 판별, 익절 상한 없음)" in _texts(_t[0]), _texts(_t[0]) if _t else None)
    _t[0].destroy()
    E.App._prop_dialog(ps, 0)
    _t = _tops()
    _ev_rb = next(x for x in _walk(_t[0]) if isinstance(x, ttk.Radiobutton) and str(x.cget("text")) == "평가")
    _rbs = [x for x in _ev_rb.master.winfo_children() if isinstance(x, ttk.Radiobutton)]
    ck("4g 프롭 창(Lucid): 'Lucid 계좌 단계(익절 상한용)' 줄에 고르기 셋(평가, 펀디드, 모름) - 사이징용 '계좌 유형'과 따로",
       [str(x.cget("text")) for x in _rbs] == ["평가", "펀디드", "모름(익절 꺼짐)"] and "Lucid 계좌 단계(익절 상한용)" in _texts(_t[0]),
       [str(x.cget("text")) for x in _rbs])
    next(x for x in _rbs if str(x.cget("text")) == "평가").invoke()
    _btn(_t[0], "저장").invoke()
    ROOT.update()
    ck("4h 저장 = 같은 Lucid 계좌의 모든 자산 행에 '평가' + 다시 묻지 않음 + 상태 줄이 평가 계좌 익절로",
       ps._acfg["NQ"]["accounts"][0]["prop"]["stage"] == "eval" and ps._acfg["GC"]["accounts"][0]["prop"]["stage"] == "eval"
       and "nt8|ldi150a1234" in E.App._dc_state(ps)["asked"]
       and E.App._daycap_row_text(ps, "NQ", ps._acfg["NQ"]["accounts"][0]).startswith("익절: 하루 상한 $4,410"),
       (ps._acfg["NQ"]["accounts"][0]["prop"], E.App._daycap_row_text(ps, "NQ", ps._acfg["NQ"]["accounts"][0])))
    for _w in _tops():
        _w.destroy()
    ROOT.destroy()

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
