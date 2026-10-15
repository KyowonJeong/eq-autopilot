#!/usr/bin/env python3
# 권장 1R(대표 2026-10-14 결정 6, 법적 선: 크기는 회원이 정한다 - 앱은 제안하고 미리 채울 뿐) 오프라인 시험. 0 = 통과.
#   1 권장 값: 내장 프리셋(평가 2,200 / 펀디드 450)은 150K만. 다른 규모는 권하지 않는다.
#   2 행별 권장: Topstep 이름, Lucid 단계 답, Topstep 펀디드 이름엔 규모가 없어 회원이 고른 규모가 있을 때만, 라이브 초기 = 없음
#   3 새 계좌 미리 채우기: 새 행에서 계좌를 처음 고를 때 1R 칸이 새 계좌 기본값이면 권장 값을 칸에, 기존 1R은 말없이 바꾸지 않음,
#     Lucid는 단계 답 뒤에
#   4 [전 계좌 1R 조회]: 조회만으로는 아무것도 안 바뀐다, 전후를 보여 주고 [권장 1R로 맞추기] 한 번에 바뀐다(프롭 켜진 행은 그 단계 칸과 유형)
#   5 통과 뒤 한 번 묻기: '평가를 통과했습니다. 펀디드 권장 1R $450로 바꿀까요?' [바꾸기] [그대로] - 한 번만, 누를 때만 바뀜, 150K 아니면 안내만
# 실제 Tk 창으로 버튼을 눌러 본다(창 없이 도는 기기면 4b, 5 일부를 건너뛰고 알린다). HOME, APPDATA = 임시 폴더, 네트워크 차단.
import copy
import os
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_reco_")
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
R = E._reco_one_r

# ── 1 권장 값 ──────────────────────────────────────────────────────────────────
ck("1a 150K: Topstep, Lucid 평가 2,200 / 펀디드(통과 포함) 450 = 코드 프리셋",
   (R("projectx", "eval", 150000), R("projectx", "funded", 150000), R("projectx", "passed", 150000),
    R("nt8", "eval", 150000), R("nt8", "funded", 150000))
   == (E._PROP_PRESETS["projectx"]["r_test"], 450.0, 450.0, E._PROP_PRESETS["nt8"]["r_test"], 450.0) == (2200.0, 450.0, 450.0, 2200.0, 450.0))
ck("1b 다른 규모(25K, 50K, 100K), 연습, 모르는 계좌, 다른 브로커 = 권하지 않음(지어내지 않는다)",
   all(R(bk, st, sz) is None for bk, st, sz in (("projectx", "eval", 50000), ("projectx", "eval", 100000), ("nt8", "eval", 25000),
                                               ("nt8", "funded", 100000), ("projectx", "practice", 150000),
                                               ("projectx", "unknown", 150000), ("tradovate", "eval", 150000),
                                               ("projectx", "eval", 0))))


class Stub:
    lang = "ko"

    def __init__(self):
        self.logs, self.saved, self.built, self.prompts = [], 0, 0, []
        self._acfg, self._sig_accts, self._dc_mon, self._acct_widgets = {}, {}, {}, {}
        self._asset = "GC"
        self.root = SimpleNamespace(after=lambda ms, fn=None, *a: fn() if fn else None)

    def log(self, m):
        self.logs.append(str(m))

    def _acct_broker(self, asset, acct):
        return (acct or {}).get("broker") or "projectx"

    def _accts_of(self, asset):
        return self._acfg[asset]["accounts"]

    def _save_cfg(self, **k):
        self.saved += 1

    def _build(self):
        self.built += 1

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n, _v in E.App.__dict__.items():
    if _n.startswith(("_daycap", "_dc_", "_lucid", "_reco", "_apply_reco", "_one_r_window", "_run_1r_preview")):
        setattr(Stub, _n, _v)
Stub._planned_one_r = E.App.__dict__["_planned_one_r"]


def _reset():
    for f in (E._DAYCAP_MON_PATH, E._DAYCAP_STATE_PATH):
        try:
            os.remove(f)
        except OSError:
            pass


# ── 2 행별 권장 ────────────────────────────────────────────────────────────────
_reset()
st = Stub()
_rows = {
    "ktc": {"id": "150KTC-V2-1", "one_r": 500.0, "prop": dict(P0), "broker": "projectx"},
    "ktc50": {"id": "50KTC-V2-1", "one_r": 500.0, "prop": dict(P0), "broker": "projectx"},
    "xfa": {"id": "EXPRESS-V2-DLL-1", "one_r": 2200.0, "prop": dict(P0), "broker": "projectx"},
    "xfa_sz": {"id": "EXPRESS-V2-DLL-2", "one_r": 2200.0, "prop": dict(P0, size=150000), "broker": "projectx"},
    "lu_q": {"id": "LDI150A1", "one_r": 500.0, "prop": dict(P0), "broker": "nt8"},
    "lu_f": {"id": "LDI150A2", "one_r": 500.0, "prop": dict(P0, stage="funded"), "broker": "nt8"},
    "live": {"id": "LDI150A3", "one_r": 500.0, "prop": dict(P0, on=True, type="live", stage="funded"), "broker": "nt8"},
}
_got = {k: E.App._reco_for_row(st, "GC", v) for k, v in _rows.items()}
ck("2a 행별 권장: Combine 150K 2,200, 50K 없음, 펀디드 이름(규모 없음) = 150K로 보고 450(3차 결정 G), 회원이 고른 규모 150K면 450, "
   "Lucid 미답 없음, Lucid 펀디드 450, 라이브 초기 없음",
   (_got["ktc"][0], _got["ktc50"][0], _got["xfa"][0], _got["xfa_sz"][0], _got["lu_q"][0], _got["lu_f"][0], _got["live"][0])
   == (2200.0, None, 450.0, 450.0, None, 450.0, None) and _got["xfa"][2] == 150000, _got)


# ── 3 새 계좌 미리 채우기 ─────────────────────────────────────────────────────────
class FEntry:
    def __init__(self, v):
        self.v, self.state = str(v), "normal"

    def get(self):
        return self.v

    def cget(self, k):
        return self.state

    def config(self, **k):
        self.state = k.get("state", self.state)

    def delete(self, a, b=None):
        self.v = ""

    def insert(self, i, t):
        self.v = str(t)


def _prefill(row, picked, typed=None):
    s = Stub()
    s._acfg = {"GC": {"accounts": [row]}}
    w = {"acct_id": FEntry(picked), "one_r": FEntry(typed if typed is not None else f"{float(row.get('one_r', 500)):g}")}
    E.App._reco_prefill_row(s, 0, w)
    return w["one_r"].get(), s


_v, _s = _prefill({"id": "", "one_r": 500.0, "prop": dict(P0), "broker": "projectx"}, "150KTC-V2-9")
ck("3a 새 행에서 Combine 150K를 고르면 1R 칸에 2,200을 미리 채움 + 로그(바꾸려면 1R 칸을 고치라는 안내)", _v == "2200"
   and any("권장 값 $2,200을 넣었습니다" in m and "1R 칸을 고치십시오" in m for m in _s.logs), (_v, _s.logs))
ck("3b 미리 채우기는 칸만 바꾼다(저장은 종전 행 저장 그대로 - 설정 값은 아직 그대로)", _s._acfg["GC"]["accounts"][0]["one_r"] == 500.0)
_v, _sx = _prefill({"id": "", "one_r": 500.0, "prop": dict(P0), "broker": "projectx"}, "EXPRESS-V2-DLL-9")
_v2, _ = _prefill({"id": "", "one_r": 500.0, "prop": dict(P0), "broker": "projectx"}, "50KTC-V2-9")
ck("3c 규모를 모르는 펀디드 이름 = 150K로 보고 450을 미리 채움(로그에 '150K로 봄', 3차 결정 G), 50K Combine = 미리 채우지 않음(권장 값 없음)",
   _v == "450" and _v2 == "500" and any("규모가 없어 150K로 봄" in m for m in _sx.logs), (_v, _v2, _sx.logs))
_v, _ = _prefill({"id": "150KTC-V2-1", "one_r": 500.0, "prop": dict(P0), "broker": "projectx"}, "150KTC-V2-9")
ck("3d 이미 계좌가 있던 행은 1R을 말없이 바꾸지 않음", _v == "500", _v)
_v, _ = _prefill({"id": "", "one_r": 500.0, "prop": dict(P0), "broker": "projectx"}, "150KTC-V2-9", typed="800")
ck("3e 회원이 이미 고친 1R 칸은 그대로", _v == "800", _v)
_v, _ = _prefill({"id": "", "one_r": 500.0, "prop": dict(P0, on=True, type="test"), "broker": "projectx"}, "150KTC-V2-9")
ck("3f 프롭 자동 사이징 행은 그대로(1R 칸을 쓰지 않는다)", _v == "500", _v)
_reset()
_v, _s = _prefill({"id": "", "one_r": 500.0, "prop": dict(P0), "broker": "nt8"}, "LDI150A5555")
ck("3g Lucid 새 행: 고르는 순간엔 단계를 몰라 그대로(답 뒤에 채운다 - 표시만 남김)", _v == "500"
   and "nt8|ldi150a5555" in _s._dc_map("_dc_fresh"), _s.__dict__.get("_dc_fresh"))
_s._acfg["GC"]["accounts"][0]["id"] = "LDI150A5555"
E.App._lucid_stage_set(_s, "LDI150A5555", "eval")
ck("3h Lucid 단계 답(평가) 뒤 새 계좌 1R = 권장 2,200 + 로그 + 화면 다시 그림", _s._acfg["GC"]["accounts"][0]["one_r"] == 2200.0
   and any("권장 값 $2,200을 넣었습니다" in m for m in _s.logs) and _s.built == 1, _s.logs[-2:])
_s2 = Stub()
_s2._acfg = {"GC": {"accounts": [{"id": "LDI150A6666", "one_r": 500.0, "prop": dict(P0), "broker": "nt8"}]}}
E.App._lucid_stage_set(_s2, "LDI150A6666", "eval")
ck("3i 새로 고른 행이 아니면(기존 Lucid 계좌의 단계 답) 1R은 그대로", _s2._acfg["GC"]["accounts"][0]["one_r"] == 500.0)
ck("3j 배선: 계좌 고르기(_pick)가 저장 전에 미리 채우기를 부른다",
   "self._reco_prefill_row(i, self._acct_widgets.get(i))" in __import__("inspect").getsource(E.App._acct_edit_row))

# ── 4 [전 계좌 1R 조회] ───────────────────────────────────────────────────────────
_reset()
st = Stub()
st._acfg = {"GC": {"accounts": [
    {"id": "EXPRESS-V2-DLL-1", "one_r": 500.0, "on": True, "broker": "projectx", "label": "XF",
     "prop": dict(P0, on=True, type="test", r_test=2200.0, r_steady=450.0, size=150000)},
    {"id": "150KTC-V2-1", "one_r": 600.0, "on": True, "broker": "projectx", "label": "KT", "prop": dict(P0)},
    {"id": "150KTC-V2-2", "one_r": 2200.0, "on": True, "broker": "projectx", "label": "OK", "prop": dict(P0)},
    {"id": "LDI150A9", "one_r": 500.0, "on": True, "broker": "nt8", "label": "LU",
     "prop": dict(P0, on=True, type="live", r_live=100.0, stage="funded")}]},
    "NQ": {"accounts": []}, "BTC": {"accounts": []}}
_before = copy.deepcopy(st._acfg)
st._creds_of = lambda a, bk: {"f1": "u", "f3": ""}
st._fetch_balance_diag = lambda bk, f1, f2, f3, aid, fut: (150000.0, None)
_win = []
st._one_r_window = lambda t, m, rows: _win.append((t, m, rows))
_thr = threading.Thread
threading.Thread = lambda target=None, daemon=None, **k: SimpleNamespace(start=target)
_f2 = E._f2_load
E._f2_load = lambda f1: "k"
try:
    E.App._run_1r_preview(st, ["GC", "NQ", "BTC"], "전 자산, 잔고 & 1R")
finally:
    threading.Thread = _thr
    E._f2_load = _f2
_rows4 = _win[0][2] if _win else []
ck("4a 조회만으로는 설정이 하나도 안 바뀐다(말없이 바꾸지 않음)", st._acfg == _before)
ck("4b 권장과 다른 계좌만 전후 목록: 펀디드 이름 + 옛 'test'(2,200 → 450, 유형 테스트기 → 펀디드), Combine 600 → 2,200 / 같은 값, 라이브 초기 제외",
   [(r["lbl"], r["before"], r["after"], r["type_from"], r["type_to"]) for r in _rows4]
   == [("XF", 2200.0, 450.0, "test", "funded"), ("KT", 600.0, 2200.0, None, None)], _rows4)
ck("4c 조회 창 본문에 권장 값 표시(KO)", _win and "(권장 $450)" in _win[0][1] and "(권장 $2,200)" in _win[0][1], _win[0][1] if _win else "")
_lines = [E.App._reco_line(st, r) for r in _rows4]
ck("4d 전후 줄 KO", _lines == ["   [GC, XF] 1R $2,200 → $450 (Topstep 150K 펀디드, 프롭 유형 테스트기 → 펀디드)",
                               "   [GC, KT] 1R $600 → $2,200 (Topstep 150K 평가)"], _lines)
_done = E.App._apply_reco_rows(st, _rows4)
_a = st._acfg["GC"]["accounts"]
ck("4e [권장 1R로 맞추기] = 보인 그대로 바뀜: 프롭 켜진 행은 유형 펀디드 + 펀디드 1R 450, 꺼진 행은 1R 칸 2,200, 나머지 그대로",
   (_a[0]["prop"]["type"], _a[0]["prop"]["r_steady"], _a[0]["prop"]["r_buffer"], _a[0]["prop"]["r_test"], _a[0]["one_r"],
    _a[1]["one_r"], _a[2]["one_r"], _a[3]["prop"]["r_live"], _a[3]["prop"]["type"])
   == ("funded", 450.0, 450.0, 2200.0, 500.0, 2200.0, 2200.0, 100.0, "live")
   and [(d[2], d[3]) for d in _done] == [(2200.0, 450.0), (600.0, 2200.0)] and st.saved == 1, (_a, _done))
ck("4f 바꾼 계좌마다 전후 로그(회원이 누름)", sum(1 for m in st.logs if "1R 권장 값 적용" in m and "[권장 1R로 맞추기]를 누름" in m) == 2)
_rows_stale = [dict(_rows4[1], id="150KTC-OTHER")]
ck("4g 창을 연 사이 행이 바뀌었으면 건너뜀", E.App._apply_reco_rows(st, _rows_stale) == [])

# ── 5 통과 뒤 한 번 묻기 + 실제 Tk 창 ───────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as _te:
    ROOT = None
    print(f"SKIP 5b~5f, 4h Tk를 열 수 없어 창 시험을 건너뜁니다({_te})")
_reset()
st = Stub()
_shown = []
st._daycap_pass_prompt_show = lambda bk, a, l, reco: _shown.append((bk, a, l, reco))
E.App._daycap_pass_prompt(st, "projectx", "150KTC-V2-1", "KT", 150000)
E.App._daycap_pass_prompt(st, "projectx", "150KTC-V2-1", "KT", 150000)
E.App._daycap_pass_prompt(st, "projectx", "100KTC-V2-1", "K1", 100000)
ck("5a 통과 뒤 제안은 계좌마다 한 번, 150K는 펀디드 권장 $450, 100K는 권장 값 없음(안내만)",
   _shown == [("projectx", "150KTC-V2-1", "KT", 450.0), ("projectx", "100KTC-V2-1", "K1", None)], _shown)
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
    st = Stub()
    st.root = ROOT
    st._acfg = {"GC": {"accounts": [{"id": "150KTC-V2-1", "one_r": 2200.0, "prop": dict(P0), "broker": "projectx", "label": "KT"}]},
                "NQ": {"accounts": [{"id": "150KTC-V2-1", "one_r": 2200.0, "prop": dict(P0, on=True, type="test", r_test=2200.0),
                                     "broker": "projectx", "label": "KT"}]}}
    E.App._daycap_pass_prompt_show(st, "projectx", "150KTC-V2-1", "KT", 450.0)
    _t = _tops()
    ck("5b 제안 창 문구 그대로(KO): '평가를 통과했습니다. 펀디드 권장 1R $450로 바꿀까요?' [바꾸기] [그대로]",
       len(_t) == 1 and "평가를 통과했습니다. 펀디드 권장 1R $450로 바꿀까요?" in _texts(_t[0])
       and "바꾸기" in _texts(_t[0]) and "그대로" in _texts(_t[0]), _texts(_t[0]) if _t else None)
    ck("5c 창이 뜬 것만으로는 1R이 안 바뀐다", st._acfg["GC"]["accounts"][0]["one_r"] == 2200.0
       and st._acfg["NQ"]["accounts"][0]["prop"]["r_test"] == 2200.0)
    _btn(_t[0], "그대로").invoke()
    ROOT.update()
    ck("5d [그대로] = 아무것도 안 바뀜 + 안내 로그 + 창 닫힘", st._acfg["GC"]["accounts"][0]["one_r"] == 2200.0 and not _tops()
       and any("1R은 그대로 둡니다" in m for m in st.logs))
    E.App._daycap_pass_prompt_show(st, "projectx", "150KTC-V2-1", "KT", 450.0)
    _btn(_tops()[0], "바꾸기").invoke()
    ROOT.update()
    ck("5e [바꾸기] = 그 계좌 모든 자산 행: 1R 칸 행은 450, 프롭 켜진 행은 유형 펀디드 + 펀디드 1R 450",
       st._acfg["GC"]["accounts"][0]["one_r"] == 450.0 and st._acfg["NQ"]["accounts"][0]["prop"]["type"] == "funded"
       and st._acfg["NQ"]["accounts"][0]["prop"]["r_steady"] == 450.0 and not _tops(), st._acfg)
    st.lang = "en"
    E.App._daycap_pass_prompt_show(st, "projectx", "150KTC-V2-1", "KT", 450.0)
    _t = _tops()
    ck("5f EN 짝: 'The evaluation has passed. Change to the recommended funded 1R of $450?' [Change] [Keep]",
       _t and "The evaluation has passed. Change to the recommended funded 1R of $450?" in _texts(_t[0])
       and "Change" in _texts(_t[0]) and "Keep" in _texts(_t[0]), _texts(_t[0]) if _t else None)
    _t[0].destroy()
    # 4h 실제 1R 창: [권장 1R로 맞추기]가 보이고, 누르면 바뀌고, 결과 줄
    st = Stub()
    st.root = ROOT
    st._acfg = {"GC": {"accounts": [{"id": "150KTC-V2-1", "one_r": 600.0, "on": True, "broker": "projectx", "label": "KT",
                                     "prop": dict(P0)}]}}
    _rr = [{"asset": "GC", "idx": 0, "id": "150KTC-V2-1", "lbl": "KT", "before": 600.0, "after": 2200.0, "stage": "eval",
            "broker": "projectx", "type_from": None, "type_to": None}]
    E.App._one_r_window(st, "전 자산, 잔고 & 1R", "● GC\n   [KT] ...", _rr)
    _t = _tops()
    ck("4h 1R 창: 전후 줄 + [권장 1R로 맞추기] 버튼(누르기 전엔 그대로)", _t and "권장 1R로 맞추기" in _texts(_t[0])
       and "   [GC, KT] 1R $600 → $2,200 (Topstep 150K 평가)" in _texts(_t[0]) and st._acfg["GC"]["accounts"][0]["one_r"] == 600.0,
       _texts(_t[0]) if _t else None)
    _btn(_t[0], "권장 1R로 맞추기").invoke()
    ROOT.update()
    ck("4i 한 번 누르면 바뀌고 버튼이 꺼지며 결과 줄", st._acfg["GC"]["accounts"][0]["one_r"] == 2200.0
       and str(_btn(_t[0], "권장 1R로 맞추기").cget("state")) == "disabled"
       and any(s.startswith("맞췄습니다: GC KT $600 → $2,200") for s in _texts(_t[0])), _texts(_t[0]))
    _t[0].destroy()
    E.App._one_r_window(st, "t", "m", [])
    _t = _tops()
    ck("4j 권장과 다른 계좌가 없으면 버튼 없음(닫기만)", _t and "권장 1R로 맞추기" not in _texts(_t[0]) and "닫기" in _texts(_t[0]))
    _t[0].destroy()
    ROOT.destroy()

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
