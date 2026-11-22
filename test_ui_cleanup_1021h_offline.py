#!/usr/bin/env python3
# EQ Autopilot 2026.10.21h 화면 정리(대표 2026-10-21 '다 해') 오프라인 시험. 0 = 통과.
#   1 [전 계좌 1R 조회] = 주문이 쓰는 1R(옛 자본 비례 행도 1R 칸 값, 프롭 행은 _prop_r_info), 프롭 행은 [권장 1R로 맞추기] 대상 아님
#     ([프롭 추천 금액 쓰기]로 안내), 전후 줄에 '테스트기', '라이브 초기' 없음. 설정을 읽을 때 pct.on = False.
#   2 자산 줄 [정지] 없음   3 [무료 사용] 없음, [토큰 받기] 하나, 무료 토큰 문구 사실대로   4 scope_note 사실대로
#   5 [복사] 없음   6 [1R 도우미] + 한 계약 허용 한도 구역, 한도 저장 그대로   7 Lucid 단계 = 창 없이 로그 한 줄(계좌마다 한 번)
#   8 [동기화(푸시)] 화면에 없음   9 NT8 브리지 버튼은 NT8/Tradovate 설정 때만   10 구버전 브리지 = 모달 아닌 알림 창
#   11 죽은 코드 제거(진입 경로 의미 그대로)   12 문구(합니다체, 번호, 프롭 체크 이름, ** 없음, 이모지 없음)
# 실제 eqgui.App을 실제 Tk로 띄운다(창 숨김). HOME = 임시 폴더, 네트워크 차단, 스레드 실행 안 함, 지연 타이머 버림, 알림 0.
import inspect
import os
import re
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_ui1021h_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_SHARE", None)
os.environ.pop("EQ_DAYCAP_OFF", None)
import socket  # noqa: E402


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.socket.connect_ex = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk


class FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **k):
        self.name = name or "fake"

    def start(self):
        pass

    def join(self, *a, **k):
        pass

    def is_alive(self):
        return False


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:400]}]"))
    if not c:
        FAIL.append(n)


SRC = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()


def src(n):
    return inspect.getsource(getattr(E.App, n))


ck("0 앱 버전 2026.10.21z 이상", E.App._APP_VER >= "2026.10.21z", E.App._APP_VER)

# ── 1 [전 계좌 1R 조회] ───────────────────────────────────────────────────────
_na = E._new_acct(700.0, "", pct={"on": True, "pct": 1.0, "floor": 200.0})
ck("1a 설정 로드: 옛 자본 비례 켜짐(pct.on) = 늘 끔, 비율 숫자는 남김", _na["pct"]["on"] is False and _na["pct"]["pct"] == 1.0, _na["pct"])


class Stub:
    lang = "ko"

    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(str(m))

    def _ui(self, fn, ms=0):
        fn()

    def _save_cfg(self, **k):
        pass

    def _build(self):
        pass

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        try:
            a = inspect.getattr_static(E.App, n)
        except AttributeError:
            return lambda *a, **k: None
        if isinstance(a, staticmethod):
            return a.__func__
        if callable(a):
            return a.__get__(self)
        return a


P0 = dict(E._PROP_DEFAULTS)
st = Stub()
st._acfg = {"NQ": {"broker": "projectx", "accounts": [
    {"id": "LEGACYPCT1", "one_r": 650.0, "on": True, "broker": "ibkr", "label": "PC", "prop": dict(P0),
     "pct": {"on": True, "pct": 1.0, "floor": 200.0}},
    {"id": "150KTC-V2-7", "one_r": 2200.0, "on": True, "broker": "projectx", "label": "PR",
     "prop": dict(P0, on=True, type="test", mode="own", set=True, r_test=1234.0, r_steady=450.0)},
    {"id": "150KTC-V2-8", "one_r": 600.0, "on": True, "broker": "projectx", "label": "KT", "prop": dict(P0)}]},
    "GC": {"accounts": []}, "BTC": {"accounts": []}}
st._accts_of = lambda a: st._acfg[a]["accounts"]
st._acct_broker = lambda a, ac: ac.get("broker") or "projectx"
st._creds_of = lambda a, bk: {"f1": "u", "f3": ""}
st._fetch_balance_diag = lambda bk, f1, f2, f3, aid, fut: (150000.0, None)
_win = []
st._one_r_window = lambda t, m, rows: _win.append((t, m, rows))
_thr, _f2 = threading.Thread, E._f2_load
threading.Thread = lambda target=None, daemon=None, **k: SimpleNamespace(start=target)
E._f2_load = lambda f1: "k"
try:
    E.App._run_1r_preview(st, ["NQ", "GC", "BTC"], "전 자산, 잔고 & 1R")
finally:
    threading.Thread, E._f2_load = _thr, _f2
_body = _win[0][1] if _win else ""
_rows = _win[0][2] if _win else []
ck("1b 옛 자본 비례 행 = 1R 칸 값 $650(주문이 쓰는 값), 잔고 x % ($1,500) 아님", "[PC] 고정, 잔고 $150,000  |  1R $650" in _body
   and "$1,500" not in _body and "자본비례" not in _body, _body)
_pr_r = E.App._prop_r_info(st, st._acfg["NQ"]["accounts"][1]["prop"], "projectx", "150KTC-V2-7")[1]
ck("1c 프롭 행 = 진입 경로와 같은 풀이(_prop_r_info, 직접 정한 $1,234)", _pr_r == 1234.0 and "[PR] 프롭, 잔고 $150,000  |  1R $1,234" in _body,
   (_pr_r, _body))
ck("1d 프롭 행은 [권장 1R로 맞추기] 대상이 아님, 직접 정한 값이 추천과 다르면 [프롭 추천 금액 쓰기]로 안내",
   [r["lbl"] for r in _rows] == ["KT"] and "[프롭 추천 금액 쓰기]" in _body, (_rows, _body))
ck("1e 남은 전후 줄은 프롭이 아닌 Topstep 행(유형 바꿈 없음)", all(r["type_from"] is None and r["type_to"] is None for r in _rows), _rows)
_rl = E.App._reco_line(st, {"asset": "NQ", "lbl": "X", "before": 600.0, "after": 2000.0, "stage": "eval", "broker": "projectx",
                            "size": 150000, "type_from": "test", "type_to": "funded"})
ck("1f _reco_line: '테스트기', '라이브 초기' 없음, 챌린지 → 펀디드", "테스트기" not in _rl and "라이브 초기" not in _rl and "챌린지 → 펀디드" in _rl, _rl)
st.lang = "en"
_rle = E.App._reco_line(st, {"asset": "NQ", "lbl": "X", "before": 600.0, "after": 2000.0, "stage": "eval", "broker": "projectx",
                             "size": 150000, "type_from": "test", "type_to": "live"})
ck("1g _reco_line EN: 'live-early' 없음", "live-early" not in _rle, _rle)
st.lang = "ko"
ck("1h [전 계좌 1R 조회]에 잔고 x % 계산이 없다", "bal * pct" not in src("_run_1r_preview") and '_pc.get("on")' not in src("_run_1r_preview"))

# ── 2, 5, 11 지운 것 ─────────────────────────────────────────────────────────
for _n in ("_panel_stop", "_copy_f1", "_copy_key", "_payout_popup", "_bump_payout", "_set_prop_lastbal", "_manual_ticket",
           "_save_creds_and_test", "_preview_one_r", "_armed_here", "accounts", "flatten", "_lucid_stage_ask"):
    ck(f"11 {_n} 없음", not hasattr(E.App, _n))
ck("11 _PAYOUT_CHUNK 없음", not hasattr(E, "_PAYOUT_CHUNK"))
for _k in ("sec_flat", "live_close", "conn_first", "need_creds", "pick_acct", "sec_auto", "auto_sched", "auto_start", "auto_stop",
           "sec_sig", "sig_1r", "sig_start", "sig_stop", "sig_note", "auto_note", "btn_free", "copy_btn", "auto_on_ind", "auto_off_ind",
           "live_confirm", "acct_topstep_only"):
    ck(f"11 T['{_k}'] 없음", _k not in E.T)
_unused = [k for k in E.T if not re.search(r"[\"']" + re.escape(k) + r"[\"']", SRC.replace(f'    "{k}": {{', ""))]
ck("11 쓰지 않는 T 키 없음", not _unused, _unused)
_sl = src("_sig_loop_body")
_slc = "\n".join(l.split("#")[0] for l in _sl.splitlines())     # 주석 뺀 코드
ck("11 신호 루프: 수동 티켓 분기 없음, 켜진 계좌는 전부 _fire(재진입 금지 표시 뒤)", "_manual" not in _slc and "manual" not in _slc and "_fire.append(_cfg)" in _sl
   and _sl.index("_marks.append((_key, _dedup_key))") < _sl.index("_fire.append(_cfg)"))
ck("11 신호 루프: 유효한 계좌가 하나도 안 남으면 그대로 no_account", 'if _changed:\n                        self._entry_skip_ev(_asset, "no_account")' in _sl)
_ea = src("_enter_account")
# 21l(21k 리뷰 P1-1): 1R을 정하지 못한 프롭 줄(다른 계좌의 추천 금액 사본, 0인 칸)은 None - 'prop_r'로 건너뛴다. 5발 졸업(prop_done)은 그대로 없다.
# 2026-11-10 리뷰 P2: 21d 출금 5회 줄(p5_hold)만 prop_done으로 건너뛴다(회원이 다시 켜기 전) - 5발 졸업 분기 자체(출금 횟수로 판단)는 없다
ck("11 _enter_account: prop_done은 p5_hold 줄에서만, 프롭 1R은 _prop_one_r, None이면 'prop_r'로 건너뜀",
   _ea.count('"prop_done"') == 1 and '_pr.get("on") and _pr.get("p5_hold")' in _ea and 'get("payouts")' not in _ea
   and "one_r = self._prop_one_r(_pr, cfg, lbl)" in _ea and 'if one_r is None:' in _ea and '"prop_r"' in _ea)
_pr_ok = E.App._prop_one_r(st, dict(P0, on=True, type="test", mode="own", set=True, r_test=1500.0), {"broker": "projectx", "acct": "X"}, "L")
_pr_0 = E.App._prop_one_r(st, dict(P0, on=True, type="test", mode="own", set=True, r_test=0.0), {"broker": "projectx", "acct": "X"}, "L")
ck("11 _prop_one_r: 정한 1R은 숫자, 0인 칸은 None(건너뜀)", isinstance(_pr_ok, float) and _pr_ok == 1500.0 and _pr_0 is None, (_pr_ok, _pr_0))
_ai = SimpleNamespace()
ck("11 _set_auto_ind = 아무것도 안 함(없는 위젯에 쓰지 않음)", E.App._set_auto_ind(_ai, True, ["NQ"]) is None and not vars(_ai)
   and "auto_ind" not in src("_set_auto_ind").split('"""')[-1])

# ── 3, 4, 12 문구 ────────────────────────────────────────────────────────────
_fm = E.T["free_msg"]
ck("3a 무료 토큰 문구: '자동 청산이 열린다' 거짓 없음, Preview 지연 신호 + 자동 청산 Operator + 자동 진입 Autopilot(KO/EN)",
   "자동 청산이 열립니다" not in _fm["ko"] and "unlock auto-close" not in _fm["en"]
   and all(x in _fm["ko"] for x in ("Preview", "지연", "Operator", "Autopilot")) and all(x in _fm["en"] for x in ("Preview", "delayed", "Operator", "Autopilot")),
   _fm)
ck("3b gate_none: [무료 사용] 대신 [토큰 받기]", "무료 사용" not in E.T["gate_none"]["ko"] and "토큰 받기" in E.T["gate_none"]["ko"]
   and "Use free" not in E.T["gate_none"]["en"] and "Get token" in E.T["gate_none"]["en"])
ck("4 scope_note: '(전체 = 모든 활성 계좌)' 없음, [모든 포지션 청산] = 설정된 모든 계좌와 브로커(KO/EN)",
   "전체 = 모든 활성 계좌" not in E.T["scope_note"]["ko"] and "[모든 포지션 청산]" in E.T["scope_note"]["ko"]
   and "모든 계좌와 브로커" in E.T["scope_note"]["ko"] and "every account and broker" in E.T["scope_note"]["en"])
_new_txt = [E.T[k][l] for k in ("free_msg", "gate_none", "scope_note", "unlock", "lock") for l in ("ko", "en")]
ck("12a 새 문구에 가운뎃점, 이모지 없음", not any("·" in t or re.search("[\U0001F300-\U0001FAFF]", t) for t in _new_txt), _new_txt)
ck("12b 잠금 라벨 이모지 없음", (E.T["unlock"]["ko"], E.T["lock"]["ko"], E.T["unlock"]["en"], E.T["lock"]["en"])
   == ("잠금해제", "잠금", "Unlock", "Lock"))
ck("12c Lucid 안내 합니다체('끝이에요', '사셨나요' 없음)", "끝이에요" not in SRC and "더 사셨나요" not in SRC and "1R만 정하면 됩니다." in SRC)
ck("12d 크립토 계좌 머리에 번호(②) 없음", "② 계좌" not in SRC and "② Accounts" not in SRC)
ck("12e 프롭 체크 이름 = '프롭 계좌로 사용(단계별 1R)' / 'Use as a prop account (1R by stage)'",
   "프롭 계좌로 사용(단계별 1R)" in src("_prop_dialog") and "Use as a prop account (1R by stage)" in src("_prop_dialog")
   and "프롭 자동 사이징 사용" not in SRC)
ck("12f [전체 정지] 창에 ** 없음", "**" not in src("_master_stop"))

# ── 7 Lucid 단계: 창 없이 로그 ───────────────────────────────────────────────
ck("7a 단계 확인은 창을 만들지 않는다(Toplevel, messagebox 없음)", "Toplevel" not in src("_lucid_stage_check")
   and "messagebox" not in src("_lucid_stage_check") and "Toplevel" not in src("_lucid_stage_note"))
ck("7b 시작 때, 계좌 고를 때 부르는 곳은 그대로(로그 한 줄)", "root.after(4 * 1000, self._lucid_stage_check)" in SRC
   and "self.root.after(200, self._lucid_stage_check)" in src("_acct_edit_row"))
ck("7c 상한 정책 그대로: 답 없는 Lucid = unanswered(익절 없음)",
   E.App._daycap_stage_cfg(st, "nt8", "LDI150A7777", dict(P0))[0] == "unanswered")

# ── 10 구버전 브리지: 모달 아님 ─────────────────────────────────────────────
_cc = src("_conn_check")
ck("10 구버전 브리지 = self._notice(설치/나중에), askyesno 없음", "askyesno" not in _cc and "self._notice(" in _cc
   and '"설치" if self.lang == "ko" else "Install"' in _cc and "self._nt8_install_bridge)" in _cc)
ck("9a 브리지 버튼은 _nt8_bridge_wanted()가 참일 때만", 'sys.platform.startswith("win") and self._nt8_bridge_wanted()' in src("_build"))

# ── 실제 Tk ──────────────────────────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk
    E_thr = threading.Thread
    threading.Thread = FakeThread
    for _f in ("askyesno", "showinfo", "showwarning", "showerror"):
        setattr(E.messagebox, _f, lambda *a, **k: True)
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as _te:
    ROOT = None
    print(f"SKIP Tk 시험(Tk 없음: {_te})")

if ROOT is not None:
    _oa = ROOT.after
    ROOT.after = lambda ms, func=None, *a: (_oa(ms) if func is None else (_oa(0, func, *a) if ms in (0, "idle") else "dropped"))
    _oc = ROOT.after_cancel
    ROOT.after_cancel = lambda i: None if i == "dropped" else _oc(i)
    import yaml
    with open(E.CFG_PATH, "w") as f:
        yaml.safe_dump({"lang": "ko", "token": "T", "assets": {
            "NQ": {"broker": "projectx", "include": True,
                   "accounts": [{"id": "", "one_r": 700.0, "on": True, "label": "A", "pct": {"on": True, "pct": 1.0}}]},
            "GC": {"broker": "projectx", "include": False, "accounts": []},
            "BTC": {"broker": "bybit", "include": False, "accounts": []}}}, f)

    def _walk(w):
        out = [w]
        for c in w.winfo_children():
            out += _walk(c)
        return out

    def _btns(w):
        return [str(x.cget("text")) for x in _walk(w) if isinstance(x, (ttk.Button, tk.Button))]

    def _texts(w):
        return [str(x.cget("text")) for x in _walk(w) if isinstance(x, (ttk.Label, tk.Label, ttk.Button, ttk.Checkbutton))]

    def _tops():
        return [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]

    app = E.App(ROOT)
    for _ in range(3):
        ROOT.update()
    _b = _btns(ROOT)
    ck("1i 설정 로드 뒤 계좌 pct.on = False", app._acfg["NQ"]["accounts"][0]["pct"]["on"] is False, app._acfg["NQ"]["accounts"][0])
    ck("2 자산 줄 [정지] 없음", "정지" not in _b and "Stop" not in _b, _b)
    ck("3c [무료 사용] 없음, [토큰 받기] 하나", "무료 사용" not in _b and _b.count("토큰 받기") == 1, _b)
    ck("5 [복사] 없음", "복사" not in _b and "Copy" not in _b, _b)
    ck("8 [동기화(푸시)]는 만들되 화면에 붙이지 않음(매일 자동 동기화)", hasattr(app, "b_tr") and not app.b_tr.winfo_manager()
       and "동기화(푸시)" not in [str(x.cget("text")) for x in _walk(ROOT) if isinstance(x, ttk.Button) and x.winfo_manager()],
       app.b_tr.winfo_manager())
    ck("9b NT8, Tradovate 설정 없음 = 브리지 버튼 안 보일 조건", app._nt8_bridge_wanted() is False)
    ck("9c 맥(윈도 아님)에는 브리지 버튼 없음", "NT8 브리지 설치/업데이트" not in _b)
    app._acfg["GC"]["accounts"] = [E._new_acct(500.0, "LDI150A1234", broker="nt8", lucid=True)]
    ck("9d 어느 자산이든 NT8 계좌가 있으면 보일 조건", app._nt8_bridge_wanted() is True)
    app._acfg["GC"]["accounts"] = []
    app._acfg["GC"]["broker"] = "nt8"
    ck("9e 자산 브로커가 NT8이어도 보일 조건", app._nt8_bridge_wanted() is True)
    app._acfg["GC"]["broker"] = "projectx"
    ck("4b scope_note가 화면에 새 문구로", E.T["scope_note"]["ko"] in _texts(ROOT), [t for t in _texts(ROOT) if t.startswith("※")])
    ck("6a 계좌 행 버튼 = [1R 도우미](옛 '계산기 X%' 표기 없음)", "1R 도우미" in _b and not any(t.startswith("계산기") for t in _b), _b)
    # 6 1R 도우미 창: Topstep 계좌는 한 계약 한도 구역 없음(2026-10-21 '프롭에도 안 나오게'), 개인 선물 계좌는 구역 + 한도 저장 그대로
    app._pct_dialog(0)
    ROOT.update()
    _t = _tops()
    _tx0 = _texts(_t[0]) if _t else []
    ck("6d Topstep 계좌의 1R 도우미에는 한 계약 한도 구역 없음", _t and "한 계약 허용 한도 $" not in _tx0 and "한도만 저장" not in _tx0, _tx0)
    for _w in _t:
        _w.destroy()
    app._acfg["NQ"]["accounts"][0]["broker"] = "nt8"
    app._acfg["NQ"]["accounts"][0]["id"] = "123456"
    app._pct_dialog(0)
    ROOT.update()
    _t = _tops()
    _tx = _texts(_t[0]) if _t else []
    ck("6b 1R 도우미 창에 '한 계약 허용 한도' 굵은 제목과 칸, 버튼", "한 계약 허용 한도(선물, 수량이 0일 때)" in _tx and "한 계약 허용 한도 $" in _tx
       and "한도만 저장" in _tx and "1R로 적용" in _tx, _tx)
    _ents = [x for x in _walk(_t[0]) if isinstance(x, ttk.Entry)]
    _le = _ents[-1]
    _le.delete(0, "end")
    _le.insert(0, "350")
    next(x for x in _walk(_t[0]) if isinstance(x, ttk.Button) and str(x.cget("text")) == "한도만 저장").invoke()
    ROOT.update()
    ck("6c [한도만 저장] = one_lim 350, 1R은 그대로 700", app._acfg["NQ"]["accounts"][0]["one_lim"] == 350.0
       and app._acfg["NQ"]["accounts"][0]["one_r"] == 700.0 and not _tops(), app._acfg["NQ"]["accounts"][0])
    # 12e 프롭 창 체크 이름
    app._prop_dialog(0)
    ROOT.update()
    _t = _tops()
    ck("12g 프롭 창 체크 = '프롭 계좌로 사용(단계별 1R)'", _t and "프롭 계좌로 사용(단계별 1R)" in _texts(_t[0]), _texts(_t[0]) if _t else None)
    for _w in _tops():
        _w.destroy()
    # 7 Lucid 단계: 창 없이 로그 한 줄, 계좌마다 한 번
    app._acfg["NQ"]["accounts"].append(E._new_acct(500.0, "LDI150A5555", broker="nt8", lucid=True, label="LU"))
    _LG = []
    app.log = lambda m: _LG.append(str(m))
    app._lucid_stage_check()
    ROOT.update()
    _new = list(_LG)
    ck("7d 단계 답이 없는 Lucid = 창 없음 + 로그 한 줄('계좌 단계'를 고르십시오)", not _tops()
       and sum(1 for m in _new if "LDI150 …5555" in str(m) and "'계좌 단계'를 고르십시오" in str(m)) == 1, _new)
    _LG.clear()
    app._lucid_stage_check()
    ck("7e 같은 계좌는 다시 알리지 않음", not any("…5555" in m for m in _LG), _LG)
    app.lang = "en"
    app._lucid_stage_note("LDI150A6666", "")
    ck("7f EN 짝", any("Choose the 'Account stage'" in m for m in _LG), _LG)
    app.lang = "ko"
    # 10 구버전 브리지 알림 창: 모달 아님, [설치]만 설치
    _inst = []
    app._nt8_install_bridge = lambda: _inst.append(1)
    app._notice("EQ Autopilot", "NT8 브리지 애드온이 구버전입니다.",
                buttons=[("설치", app._nt8_install_bridge), ("나중에", None)])
    ROOT.update()
    _t = _tops()
    ck("10b 알림 창은 화면 줄을 잡지 않는다(grab 없음)", _t and not ROOT.grab_current())
    next(x for x in _walk(_t[0]) if isinstance(x, ttk.Button) and str(x.cget("text")) == "나중에").invoke()
    ck("10c [나중에] = 설치 안 함", _inst == [] and not _tops())
    for _w in _tops():
        _w.destroy()
    threading.Thread = E_thr
    ROOT.destroy()

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
