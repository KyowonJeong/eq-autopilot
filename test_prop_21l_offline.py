#!/usr/bin/env python3
# EQ Autopilot 2026.10.21l - 21k 리뷰 수리 시험(2026-10-27, 대표 "바로 다 고쳐"). 0 = 통과.
# 원칙: 주문 1R은 회원이 직접 정한 값, 또는 그 회사, 규모, 단계의 서버 권장 값을 넘지 않는다. 모르면 작은 쪽이거나 정확한 이유와 함께 건너뛴다.
#   P1-1 '추천 금액 쓰기' 행의 계좌를 바꾸면 옛 계좌의 추천 금액 사본(150K $1,750)을 새 계좌(50K)에 쓰지 않는다 - 정확한 이유로 건너뜀
#   P1-2 설정 가져오기 = 설정 로드와 같은 정리(21d 행이 재시작 전까지 더 큰 1R로 주문하지 않음)
#   P1-3 Lucid 평가 통과 = 설정 파일에 펀디드(서버 목표가 올라도, 다른 PC로 옮겨도 평가 1R로 돌아가지 않음)
#   P2   회원이 친 1R을 추천 금액이 말없이 바꾸지 않음, 그대로 저장이 1R을 키우지 않음, 서버 권장 1R 띠, 모르는 단계는 회원이 고름,
#        Tradovate Lucid 한 계약 한도, 버튼 = 주문 단계, 한 계약 한도 안내, 창 조기 반환 뒤 칸 잠금, 앱 고정 단계 글, 영어 버튼 폭, 1R 도우미 숫자
#   21l 검증(두 검증자 소견): 단계를 모르는 Lucid 계좌 = 작은 쪽 1R, 단계 고정이 남은 Lucid 줄도 통과하면 펀디드, 150K 이름 표식 전 줄은
#        서버 값을 따라감(그 밖은 작은 쪽, 그대로 저장해도 규칙 유지, 창 글 사실대로), 비중 로그 사유, 1R 도우미 옛 비율 그대로
#   검증 P2(2026-10-27): 내장값 없는 규모(25K, 50K, 100K)의 서버 권장 1R은 위 끝을 넘으면 버리지 않고 위 끝으로, '추천 금액 쓰기' 줄의 사본도
#        그 규모 위 끝까지만(주문, 진입 로그, 설정 창 칸이 같은 금액), 버린 칸 안내는 사실대로
# HOME = 임시 폴더, 네트워크 차단, 주문 없음, 알림 없음.
import inspect
import os
import socket
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_p21l_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ.pop("EQ_DAYCAP_OFF", None)


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402

E._kc_load = lambda *a, **k: ""
E._kc_save = lambda *a, **k: False
FAIL = []


def ck(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:600]}]"))
    if not c:
        FAIL.append(n)


ASKS = []
E.messagebox.askyesno = lambda t, m, **k: (ASKS.append(m), True)[1]
E.messagebox.showinfo = lambda t, m, **k: ASKS.append("INFO " + m)
E.messagebox.showwarning = lambda t, m, **k: ASKS.append("WARN " + m)
E._DAYCAP_SRV = {}


class T(E.App):
    def __init__(self, acfg, asset="NQ", lang="ko"):
        self._acfg, self._asset, self.lang, self.logs, self.saved = acfg, asset, lang, [], 0
        self._acct_widgets = {}
        self.skips = []

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

    def _dc_tchg_update(self):
        pass

    def _crypto_unique_ok(self, asset):
        return True

    def _daycap_pass_prompt(self, *a, **k):
        pass

    def _entry_skip_ev(self, asset, why):
        self.skips.append((asset, why))

    def _ui(self, fn, *a):
        pass

    def _live_now(self, v=None):
        return False

    def _creds_of(self, asset, bk):
        return {}


def row(aid, prop, bk="projectx", one_r=500.0, label=""):
    return {"id": aid, "one_r": one_r, "on": True, "label": label or aid[-4:], "broker": bk, "prop": prop, "pct": {}, "one_lim": 0.0}


def acfg(nq=(), gc=(), nq_bk="projectx", gc_bk="projectx"):
    return {"NQ": {"broker": nq_bk, "include": True, "creds": {}, "accounts": list(nq)},
            "GC": {"broker": gc_bk, "include": True, "creds": {}, "accounts": list(gc)},
            "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}


def reload_rows(cfg):
    E._save_full("ko", "", cfg)
    return E._load()["assets"]


def info(t, r, bk=None):
    return E.App._prop_r_info(t, r["prop"], bk or r.get("broker") or "projectx", r["id"])


def switch(t, asset, r, new_id):
    """_collect_acct_widgets가 하는 일(계좌 콤보에서 다른 계좌를 고름)."""
    t._daycap_reset_row(asset, r, r["id"], new_id)
    r["id"] = new_id


TS150, TS50, XFA, NUM = "150KTC-V2-111-222", "50KTC-V2-9-9", "EXPRESS-V2-555-666", "87654321"
LU150, LU050 = "LDI150A1111", "LDI050A2222"

ck("0a 앱 버전 2026.10.21z 이상", E.App._APP_VER >= "2026.10.21z", E.App._APP_VER)
ck("0b 내장 권장 1R 그대로(Topstep 1,750/500, Lucid 1,750/450)",
   (E._reco_one_r("projectx", "eval", 150000), E._reco_one_r("projectx", "funded", 150000),
    E._reco_one_r("nt8", "eval", 150000), E._reco_one_r("nt8", "funded", 150000)) == (1750.0, 500.0, 1750.0, 450.0))

# ===== Tk(설정 창) =====
try:
    import tkinter as tk
    from tkinter import ttk
    from tkinter import font as tkfont
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


def entries(w):
    return [x for x in _walk(w) if isinstance(x, ttk.Entry)]


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


# ===== P1-1 =====
print("\n== P1-1 추천 금액 쓰기 행의 계좌를 바꿈 ==")
if ROOT is not None:
    r = E._new_acct(500, TS150, True, "a", None, None, False, "projectx", topstep=True)
    t = T(acfg(nq=[r]))
    w = open_dlg(t)
    next(x for x in _walk(w) if isinstance(x, ttk.Checkbutton)).invoke()
    ck("1a 150K 새 줄: 단계 챌린지 미리 고름, 추천 금액 칸 잠김(1,750 / 500)",
       radio(w, "챌린지").instate(["selected"]) and [(str(e.cget("state")), e.get()) for e in entries(w)][:2]
       == [("disabled", "1750"), ("disabled", "500")], [(str(e.cget("state")), e.get()) for e in entries(w)])
    ASKS.clear()
    save(w)
    p = r["prop"]
    ck("1b 저장: 추천 금액 쓰기, 따라가는 칸 두 개, 사본의 계좌 = 이 계좌, 주문 1R 1,750",
       p.get("mode") == "reco" and sorted(p.get("reco_keys") or []) == ["r_steady", "r_test"]
       and p.get("reco_acct") == TS150.casefold() and info(t, r) == ("test", 1750.0, True), (p, ASKS))
    switch(t, "NQ", r, TS50)
    ck("1c 행의 계좌를 50K 챌린지로 바꿈: 150K 사본 $1,750을 쓰지 않음(1R None, 21k는 1,750)", info(t, r) == ("test", None, False), info(t, r))
    t.logs.clear()
    ck("1d 진입 경로: 1R을 못 정해 이 계좌만 건너뜀 + 정확한 이유(추천 금액 없음, 직접 정하라) 한 줄",
       E.App._prop_one_r(t, r["prop"], {"broker": "projectx", "acct": TS50}, "a") is None
       and any("1R을 정하지 못해 이번 신호에 진입하지 않습니다" in m and "추천 금액이" in m and "[프롭: …]" in m for m in t.logs)
       and not any("사이징 불가" in m for m in t.logs), t.logs)
    t.skips.clear()
    t.logs.clear()
    cfg = {"broker": "projectx", "f1": "u", "f2": "k", "f3": "", "acct": TS50, "one_r": 500.0, "label": "a", "live": False,
           "prop": r["prop"], "pct": {}, "one_lim": 0.0}
    E.App._enter_account(t, cfg, {"entry_ref": 20000.0}, "NQ", "LONG", 19950.0, 1.0)
    ck("1e _enter_account: 서버에 'prop_r' 건너뜀 한 번, 수량 계산까지 가지 않음",
       t.skips == [("NQ", "prop_r")] and not any("사이징" in m or "x" in m.split("]")[-1][:3] for m in t.logs), (t.skips, t.logs))
    ck("1f 계좌 줄 1R 칸 = '-'(주문 1R 없음)", t._row_r_text(r) == "-", t._row_r_text(r))
    switch(t, "NQ", r, "150KTC-V2-333-444")
    ck("1g 다른 150K 챌린지로 바꾸면 그 계좌의 서버 권장 1R(1,750)", info(t, r) == ("test", 1750.0, True), info(t, r))
    close(w)

    # 반대: EXPRESS(펀디드) 줄 - 챌린지 칸 0
    r = E._new_acct(500, XFA, True, "x", None, None, False, "projectx", topstep=True)
    t = T(acfg(nq=[r]))
    w = open_dlg(t)
    next(x for x in _walk(w) if isinstance(x, ttk.Checkbutton)).invoke()
    save(w)
    p = r["prop"]
    ck("1h EXPRESS 새 줄 저장: 펀디드 추천 500만 따라감, 챌린지 칸 0", p.get("reco_keys") == ["r_steady"] and p.get("r_test") == 0.0
       and info(t, r) == ("funded", 500.0, True), p)
    switch(t, "NQ", r, TS50)
    t.logs.clear()
    ck("1i 50K 챌린지로 바꿈: 0 칸은 추천 금액을 따르는데 50K에는 없음 - '추천 금액 없음' 이유로 건너뜀('사이징 불가' 아님)",
       info(t, r) == ("test", None, False) and E.App._prop_one_r(t, r["prop"], {"broker": "projectx", "acct": TS50}, "x") is None
       and E.App._prop_r_gap(t, r["prop"], "projectx", TS50, "test") == "no_reco", t.logs)
    switch(t, "NQ", r, TS150)
    ck("1j 150K 챌린지로 바꾸면 그 규모 서버 권장 1,750", info(t, r) == ("test", 1750.0, True), info(t, r))
    close(w)

# Lucid 150K -> 50K
ra = E._new_acct(500, LU150, True, "l", {"on": True, "type": "test", "mode": "reco", "set": True, "type_manual": False,
                                          "r_test": 1750.0, "r_steady": 450.0, "stage": "eval", "stage_for": LU150.casefold(),
                                          "reco_keys": ["r_test", "r_steady"], "reco_acct": LU150.casefold()},
                 None, False, "nt8", lucid=True)
t = T(acfg(nq=[ra], nq_bk="nt8"))
ck("1k Lucid 150K 평가 추천 금액 = 1,750", info(t, ra, "nt8") == ("test", 1750.0, True), info(t, ra, "nt8"))
switch(t, "NQ", ra, LU050)
ck("1l Lucid 50K로 바꿈: 단계 답, 사본 모두 따라가지 않음 - 1R None(21k는 1,750)", info(t, ra, "nt8") == ("test", None, False)
   and ra["prop"].get("stage") is None, (info(t, ra, "nt8"), ra["prop"]))
# 표식 없는 옛 추천 금액 쓰기 행(21i~21k에서 메모리에 바로 만든 dict)도 바꾸면 사본을 옛 계좌 것으로 적는다
p_old = dict(E._PROP_DEFAULTS, on=True, type="test", mode="reco", set=True, r_test=1750.0, r_steady=500.0)
rl = row(TS150, p_old)
t = T(acfg(nq=[rl]))
switch(t, "NQ", rl, TS50)
ck("1m 표식 없는 사본도 계좌를 바꾸면 쓰지 않음", info(t, rl) == ("test", None, False) and rl["prop"]["reco_acct"] == TS150.casefold(),
   rl["prop"])
# 회원이 고른 단계도 옛 계좌의 것 - 새 계좌는 앱이 다시 알아낸다
rm = E._new_acct(500, XFA, True, "m", {"on": True, "type": "funded", "mode": "own", "set": True, "type_manual": True,
                                        "r_test": 1500.0, "r_steady": 400.0}, None, False, "projectx", topstep=True)
t = T(acfg(nq=[rm]))
switch(t, "NQ", rm, TS50)
ck("1n 직접 정하기 줄의 계좌를 바꾸면 단계 고정을 풀고 회원 1R은 그대로(50K 챌린지 = 직접 정한 1,500) + 안내 한 줄",
   rm["prop"]["type_manual"] is False and info(t, rm) == ("test", 1500.0, False)
   and any("옛 계좌에서 정한 프롭 단계는 쓰지 않고" in m for m in t.logs), (rm["prop"], t.logs))

# 묶음: 추천 금액 쓰기 행을 여러 계좌 사이에서 바꿔도 1R은 None이거나 새 계좌 그 단계의 서버 권장 값
print("\n== P1-1 묶음 ==")
accts = [("projectx", TS150), ("projectx", TS50), ("projectx", XFA), ("projectx", NUM), ("nt8", LU150), ("nt8", LU050), ("nt8", "LDI150B3333")]
bad, n = [], 0
for (bk1, a1) in accts:
    for (bk2, a2) in accts:
        if bk1 != bk2 or a1 == a2:
            continue
        sz1 = E._dc_name_info(bk1, a1)[1] or (150000 if a1 == XFA else 0)
        cp = {k: E._reco_one_r(bk1, st, sz1) for k, st in (("r_test", "eval"), ("r_steady", "funded"))}
        pr = {"on": True, "type": ("funded" if a1 == XFA else "test"), "mode": "reco", "set": True, "type_manual": False,
              "r_test": cp["r_test"] or 0.0, "r_steady": cp["r_steady"] or 0.0, "reco_keys": [k for k, v in cp.items() if v],
              "reco_acct": a1.casefold()}
        if bk1 == "nt8":
            pr["stage"], pr["stage_for"] = "eval", a1.casefold()
        rr = E._new_acct(500, a1, True, "b", pr, None, False, bk1, topstep=(bk1 == "projectx"), lucid=(bk1 == "nt8"))
        tt = T(acfg(nq=[rr], nq_bk=bk1))
        switch(tt, "NQ", rr, a2)
        ty, r1, _rc = info(tt, rr, bk2)
        # Topstep 펀디드는 이름에 규모가 없으면 150K 추천 금액(대표 2026-10-14, _prop_reco_r) - EXPRESS, 숫자 id 모두
        sz2 = E._dc_name_info(bk2, a2)[1] or (150000 if (bk2 == "projectx" and ty == "funded") else 0)
        exp = E._reco_one_r(bk2, "eval" if ty == "test" else "funded", sz2)
        n += 1
        if not (r1 is None or (exp is not None and abs(r1 - exp) < 1e-9)):
            bad.append((a1, a2, ty, r1, exp))
ck(f"1o 계좌 바꾸기 {n}가지: 1R = None 또는 새 계좌, 그 단계의 서버 권장 값", not bad and n >= 18, bad[:5])

# ===== P2-1 =====
print("\n== P2-1 회원이 친 1R은 추천 금액이 말없이 바꾸지 않음 ==")
if ROOT is not None:
    r = E._new_acct(500, NUM, True, "n", None, None, False, "projectx", topstep=True)
    t = T(acfg(nq=[r]))
    w = open_dlg(t)
    next(x for x in _walk(w) if isinstance(x, ttk.Checkbutton)).invoke()
    ck("2a 숫자 id 새 줄(단계 모름): 단계를 미리 고르지 않음, 안내 한 줄",
       not radio(w, "챌린지").instate(["selected"]) and not radio(w, "펀디드").instate(["selected"])
       and any("단계를 알지 못합니다" in x for x in labels(w)), labels(w))
    radio(w, "챌린지").invoke()
    e0 = entries(w)[0]
    ck("2b 챌린지 칸은 추천 금액이 없어 열려 있음(펀디드는 500 잠김)", str(e0.cget("state")) == "normal"
       and str(entries(w)[1].cget("state")) == "disabled", [(str(e.cget("state")), e.get()) for e in entries(w)])
    e0.delete(0, "end")
    e0.insert(0, "1000")
    ASKS.clear()
    save(w)
    p = r["prop"]
    ck("2c 저장: 따라가는 칸 = 펀디드만, 챌린지 1,000은 회원 금액", p.get("reco_keys") == ["r_steady"] and p.get("r_test") == 1000.0
       and info(t, r) == ("test", 1000.0, False), (p, ASKS))
    E.App._daycap_learn(t, "projectx", NUM, name="150KTC-V2-87654321")
    ck("2d 나중에 이름(150KTC)을 알게 돼도 챌린지 1R 1,000 그대로(21k는 1,750)", info(t, r) == ("test", 1000.0, False), info(t, r))
    close(w)
    w = open_dlg(t)
    ck("2e 다시 연 창: 챌린지 칸은 회원 금액 1,000(열림), 그 사실을 한 줄로", entries(w)[0].get() == "1000"
       and str(entries(w)[0].cget("state")) == "normal" and any("직접 넣으신 챌린지 1R은 그대로 씁니다" in x for x in labels(w)),
       ([(str(e.cget("state")), e.get()) for e in entries(w)], labels(w)))
    save(w)
    ck("2f 그대로 저장해도 1,000", info(t, r) == ("test", 1000.0, False) and r["prop"].get("reco_keys") == ["r_steady"], r["prop"])
    w = open_dlg(t)
    radio(w, "직접 정하기").invoke()
    radio(w, "추천 금액 쓰기").invoke()
    ck("2g 회원이 '추천 금액 쓰기'를 다시 고르면 그때 챌린지도 추천 금액(1,750) - 회원이 누른 것",
       entries(w)[0].get() == "1750" and str(entries(w)[0].cget("state")) == "disabled", [e.get() for e in entries(w)])
    save(w)
    ck("2h 저장 뒤 두 칸 모두 따라감, 1,750", info(t, r) == ("test", 1750.0, True)
       and sorted(r["prop"].get("reco_keys") or []) == ["r_steady", "r_test"], r["prop"])
    close(w)

# 표식 전(21k 창에서 저장한) 추천 금액 쓰기 줄: 따라가는 칸 표식이 없다 - 저장 값이 추천 금액보다 작으면 회원 금액(작은 쪽)
NUM3 = "55556666"
rlg = E._new_acct(500, NUM3, True, "g", {"on": True, "type": "test", "mode": "reco", "set": True, "type_manual": True,
                                          "r_test": 1000.0, "r_steady": 500.0}, None, False, "projectx", topstep=True)
t = T(acfg(nq=[rlg]))
E.App._daycap_learn(t, "projectx", NUM3, name="150KTC-V2-55556666")
ck("2i 21k에서 저장한 줄(표식 없음): 이름을 알게 돼도 챌린지 1,000(추천 1,750보다 작은 저장 값 - 회원이 친 금액)",
   info(t, rlg) == ("test", 1000.0, False), info(t, rlg))
if ROOT is not None:
    w = open_dlg(t)
    ck("2j 그 줄의 창: 챌린지 칸 1,000 열림(그대로 저장해도 1,750이 되지 않게)", entries(w)[0].get() == "1000"
       and str(entries(w)[0].cget("state")) == "normal", [(str(e.cget("state")), e.get()) for e in entries(w)])
    save(w)
    # 21l 검증 4: 그대로 저장은 표식 전 규칙(작은 쪽)을 그대로 둔다 - 챌린지 칸을 '회원 금액'으로 굳히지 않는다(서버가 내리면 따라가게)
    ck("2k 그대로 저장: 1,000, 표식 전 규칙 그대로(따라가는 칸 표식 없음)", info(t, rlg) == ("test", 1000.0, False)
       and rlg["prop"].get("reco_keys") is None and rlg["prop"].get("reco_acct") == NUM3, rlg["prop"])
    close(w)
E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": {"projectx": {"eval": {"150000": 1500}}}})
rlg2 = E._new_acct(500, TS150, True, "g2", {"on": True, "type": "test", "mode": "reco", "set": True, "r_test": 1750.0, "r_steady": 500.0},
                   None, False, "projectx", topstep=True)
ck("2l 표식 없는 줄도 서버가 추천 금액을 내리면 따라감(1,750 사본 -> 1,500)", info(T(acfg(nq=[rlg2])), rlg2) == ("test", 1500.0, True))
E._DAYCAP_SRV = {}

# ===== P1-2 =====
print("\n== P1-2 설정 가져오기 = 설정 로드와 같은 정리 ==")
E._PROP_TYPE_CHECK.clear()
E._PROP_LEGACY_NOTES.clear()
PAY = {"kind": "eq-autopilot-settings", "v": 1, "app": "2026.10.21d", "lang": "ko", "token": "tok", "profile": {}, "assets": {
    "NQ": {"broker": "projectx", "include": True, "creds": {"projectx": {"f1": "user1", "f3": ""}}, "accounts": [
        {"id": TS150, "one_r": 600, "on": True, "label": "ts", "broker": "projectx",
         "prop": {"on": True, "type": "funded", "r_test": 2200.0, "r_buffer": 450.0, "r_steady": 450.0, "buffer": 3000.0, "set": True}},
        {"id": "100KTC-V2-7-7", "one_r": 600, "on": True, "label": "old", "broker": "projectx",
         "prop": {"on": True, "type": "test", "r_test": 900.0, "r_buffer": 300.0, "r_steady": 300.0, "buffer": 4000.0}},
        {"id": TS50, "one_r": 700, "on": True, "label": "lim", "broker": "projectx", "prop": {"on": False}, "one_lim": 300}]},
    "GC": {"broker": "nt8", "include": True, "creds": {}, "accounts": [
        {"id": "LFE15012345678", "one_r": 600, "on": True, "label": "luc", "broker": "nt8",
         "prop": {"on": True, "type": "live", "r_test": 2200.0, "r_steady": 450.0, "r_live": 100.0, "set": True,
                  "stage": "funded", "stage_for": "lfe15012345678"}}]},
    "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": [{"id": "", "one_r": 500, "on": True, "label": "Bybit"}]}}}
import tkinter.filedialog as _fd  # noqa: E402
_fpath = os.path.join(HOME, "x.eqset")
open(_fpath, "wb").write(b"blob")
_fd.askopenfilename = lambda **k: _fpath
E.simpledialog.askstring = lambda *a, **k: "1234"
E._settings_import_blob = lambda pin, raw: __import__("copy").deepcopy(PAY)


class TI(T):
    _profile = {}
    _token = ""
    root = None

    def _golive_refuse(self, warn=True):
        return False

    def _sync_tab_broker(self):
        pass

    def _log_creds_notes(self):
        pass

    def _mirror_targets(self):
        return None


ti = TI(acfg())
ASKS.clear()
E.App._import_settings(ti)
nq = ti._acfg["NQ"]["accounts"]
gc = ti._acfg["GC"]["accounts"]
ck("3a 가져온 행이 정리됨(단계 표식, 방식, 21d 표식 칸이 생김)", all("type_manual" in x["prop"] and "reco_keys" in x["prop"] for x in nq + gc),
   [x["prop"] for x in nq])
ck("3b 21d가 펀디드로 저장한 150KTC: 앱이 알아낸 챌린지 1R이 더 커서 저장된 펀디드 450으로(21k 가져오기 직후 2,200)",
   info(ti, nq[0]) == ("funded", 450.0, False) and nq[0]["prop"]["type_pin"] is True, (info(ti, nq[0]), nq[0]["prop"]))
ck("3c 표식 'set' 전(10/6 전) 행: 직접 정하기로 900 그대로(21k 가져오기 직후 1,750)", info(ti, nq[1]) == ("test", 900.0, False), info(ti, nq[1]))
ck("3d 옛 '라이브 초기' Lucid: 펀디드 + 그 1R $100(21k 가져오기 직후 450)", info(ti, gc[0], "nt8") == ("funded", 100.0, False), info(ti, gc[0], "nt8"))
ck("3e 가져오기 로그: 21d 단계 고정, 라이브 초기 이행, 쓰지 않는 한 계약 한도 안내",
   any("…-222" in m and "저장된 단계로 계속 주문합니다" in m for m in ti.logs)
   and any("라이브 초기" in m and "$100" in m for m in ti.logs)
   and any("…-9-9" in m and "한 계약 허용 한도($300)를 쓰지 않습니다" in m for m in ti.logs), ti.logs)
ck("3f 저장 한 번 이상, 오류 창 없음", ti.saved >= 1 and not any(m.startswith("WARN") for m in ASKS), ASKS)

# ===== P1-3 =====
print("\n== P1-3 Lucid 통과 = 그 행 멈춤(2026.11.09b: Lucid는 통과 뒤 새 펀디드 계좌를 준다 - 21l '설정에 펀디드'를 되돌림) ==")
LU = "LDI150A7777"


def lucid_rows():
    base = {"on": True, "type": "test", "mode": "reco", "set": True, "type_manual": False, "r_test": 1750.0, "r_steady": 450.0,
            "stage": "eval", "stage_for": LU.casefold(), "reco_keys": ["r_test", "r_steady"], "reco_acct": LU.casefold(),
            "pass_tp": 159100.0, "pass_set": True}
    return (E._new_acct(500, LU, True, "lq", dict(base), None, False, "nt8", lucid=True),
            E._new_acct(500, LU, True, "lg", dict(base), None, False, "nt8", lucid=True))


rq, rg = lucid_rows()
t = T(acfg(nq=[rq], gc=[rg], nq_bk="nt8", gc_bk="nt8"))
ck("4a 통과 전: 평가 1,750", info(t, rq, "nt8") == ("test", 1750.0, True), info(t, rq, "nt8"))
_s0 = t.saved
E.App._daycap_mark_passed(t, "nt8", LU, bal=159200.0, pass_tp=159100.0, lbl="lq", size=150000, via="watch", target=9000.0, src="auto")
ck("4b 통과 표시 = 그 계좌 두 줄을 펀디드로 저장하지 않는다(단계 평가, 유형 챌린지 그대로)", all(x["prop"]["stage"] == "eval" and x["prop"]["stage_for"] == LU.casefold()
                                                     and x["prop"]["type"] == "test" for x in (rq, rg)),
   (rq["prop"], rg["prop"], t.saved))
ck("4c 통과 기록이 있는 동안 단계 'passed'(이 계좌 진입 멈춤)",
   t._daycap_stage_cfg("nt8", LU, rq["prop"])[0] == "passed", (t._daycap_stage_cfg("nt8", LU, rq["prop"]), info(t, rq, "nt8")))
ck("4d 통과 로그: 이 계좌로는 더 진입하지 않음 + 새 펀디드 계좌(LDF)로 바꾸라(펀디드로 저장했다는 말 없음)",
   any("이 계좌로는 더 진입하지 않습니다" in m and "새 펀디드 계좌" in m for m in t.logs)
   and not any("계좌 단계를 펀디드로 저장해" in m for m in t.logs), t.logs)
E._DAYCAP_SRV = E._prop_reco_validate({"targets": {"nt8": {"150000": 9500}}})
t.logs.clear()
_ = info(t, rq, "nt8")
_st4e = t._daycap_stage_cfg("nt8", LU, rq["prop"], bal=159200.0)[0]
# 2026-11-10 리뷰 P0: 저절로 되돌리지 않고, 잔고가 새 통과 금액(159,500) 아래면 회원에게 묻는다(답 전에는 통과 그대로)
ck("4e 서버 이익 목표가 올라도(9,000 -> 9,500) 통과 기록을 지우지 않는다(통과로 계속 봄) + 잔고가 새 통과 금액 아래면 회원에게 묻는 안내 한 줄",
   E.App._daycap_key("nt8", LU) in t._dc_state()["passed"] and t._daycap_stage_cfg("nt8", LU, rq["prop"])[0] == "passed"
   and _st4e == "passed" and any("통과 표시를 그대로" in m and "[통과 표시 풀기]" in m for m in t.logs), t.logs)
E._DAYCAP_SRV = {}
cfgs = {a: {"broker": "nt8", "include": True, "creds": {}, "accounts": [dict(x) for x in t._acfg[a]["accounts"]]} for a in ("NQ", "GC")}
cfgs["BTC"] = {"broker": "bybit", "include": False, "creds": {}, "accounts": []}
a2 = reload_rows(cfgs)
t2 = T(a2)
t2._dc_st = {k: {} for k in ("names", "sizes", "hit", "nt8_base", "status", "passed", "asked", "pass_prompted", "days", "hist", "tp_fired",
                             "daybal", "nt8_bases", "tchg", "pass_hold")}              # 다른 PC: 통과 기록 없음
ck("4f 다른 PC(통과 기록 없음, 설정 파일만): 평가 그대로(통과는 이 PC 기록뿐 - Topstep Combine과 같다)",
   info(t2, a2["NQ"]["accounts"][0], "nt8")[0] == "test"
   and t2._daycap_stage_cfg("nt8", LU, a2["NQ"]["accounts"][0]["prop"])[0] == "eval", info(t2, a2["NQ"]["accounts"][0], "nt8"))
ck("4g 설정의 유형도 그대로(챌린지)", a2["NQ"]["accounts"][0]["prop"]["type"] == "test", a2["NQ"]["accounts"][0]["prop"])
E.App._lucid_stage_set(t2, LU, "eval")
ck("4h 회원이 프롭 설정에서 평가를 고르면(회원 선택) 평가로", info(t2, a2["NQ"]["accounts"][0], "nt8")[0] == "test", a2["NQ"]["accounts"][0]["prop"])
# 기동: 21k 전에 통과로 표시된 계좌(설정에는 아직 평가)
rq, rg = lucid_rows()
t3 = T(acfg(nq=[rq], gc=[rg], nq_bk="nt8", gc_bk="nt8"))
t3._dc_state()["passed"][E.App._daycap_key("nt8", LU)] = {"day": "x", "bal": 159200.0, "pass_tp": 159100.0, "size": 150000,
                                                          "ts": time.time(), "via": "watch", "target": 9000.0, "src": "auto"}
E.App._lucid_passed_persist(t3)
ck("4i 기동: 통과 기록이 있는 Lucid 계좌를 설정에 펀디드로 바꾸지 않는다(로그 없음)",
   all(x["prop"]["stage"] == "eval" for x in (rq, rg)) and not any("펀디드로 설정에 저장했습니다" in m for m in t3.logs), t3.logs)
E.App._lucid_passed_persist(t3)
ck("4j 다시 불러도 그대로", all(x["prop"]["stage"] == "eval" for x in (rq, rg)) and not t3.logs, t3.logs)

# ===== P2-2, P2-c =====
print("\n== P2-2 그대로 저장이 1R을 키우지 않음, P2-c 앱 고정 단계 글 ==")
if ROOT is not None:
    E._PROP_TYPE_CHECK.clear()
    E._PROP_LEGACY_NOTES.clear()
    a = reload_rows(acfg(nq=[row("11112222", {"on": True, "type": "funded", "set": True, "r_test": 2200.0, "r_steady": 450.0})]))
    r = a["NQ"]["accounts"][0]
    t = T(a)
    E.App._log_prop_stage_changes(t)
    E.App._daycap_learn(t, "projectx", "11112222", name="150KTC-V2-11112222")
    ck("5a 21d 숫자 id 행: 이름을 알게 돼도 펀디드 450", info(t, r) == ("funded", 450.0, False), info(t, r))
    w = open_dlg(t)
    ck("5b 창이 주문 단계(펀디드)를 미리 고르고 '이전 버전에서 저장된 단계'라고 말함('직접 고르신' 아님)",
       radio(w, "펀디드").instate(["selected"]) and any("이전 버전에서 저장된 단계입니다" in x for x in labels(w))
       and not any("직접 고르신" in x for x in labels(w)), labels(w))
    save(w)
    ck("5c 그대로 저장: 1R 450 그대로(21k는 2,200), 앱 고정 표식 유지", info(t, r) == ("funded", 450.0, False)
       and r["prop"]["type_manual"] is True and r["prop"]["type_pin"] is True, (info(t, r), r["prop"]))
    w = open_dlg(t)
    ck("5d 다시 열어도 같은 글", any("이전 버전에서 저장된 단계입니다" in x for x in labels(w)), labels(w))
    radio(w, "챌린지").invoke()
    save(w)
    ck("5e 회원이 챌린지를 고르면(회원 선택) 챌린지 2,200, 앱 고정 표식 없어짐", info(t, r) == ("test", 2200.0, False)
       and r["prop"]["type_pin"] is False, r["prop"])
    w = open_dlg(t)
    ck("5f 회원이 고른 단계가 앱 판별과 같으면 안내 글 없음", not any("직접 고르신" in x or "이전 버전" in x for x in labels(w)), labels(w))
    close(w)
    # 회원이 직접 고른 단계(앱 고정 아님)
    rmn = E._new_acct(500, XFA, True, "m", {"on": True, "type": "test", "mode": "own", "set": True, "type_manual": True,
                                             "r_test": 1500.0, "r_steady": 400.0}, None, False, "projectx", topstep=True)
    t = T(acfg(nq=[rmn]))
    w = open_dlg(t)
    ck("5g 회원이 고른 단계는 '직접 고르신 단계입니다'", any("직접 고르신 단계입니다" in x for x in labels(w)), labels(w))
    close(w)
    # Lucid 21d: 저장 펀디드 + 답 평가 -> 기동 고정 -> 그대로 저장해도 단계 답(평가)과 1R(450) 그대로
    E._PROP_TYPE_CHECK.clear()
    E._PROP_LEGACY_NOTES.clear()
    LQ = "LDI150B9999"
    a = reload_rows(acfg(nq=[row(LQ, {"on": True, "type": "funded", "set": True, "r_test": 2200.0, "r_steady": 450.0,
                                      "stage": "eval", "stage_for": LQ.casefold()}, bk="nt8")], nq_bk="nt8"))
    r = a["NQ"]["accounts"][0]
    t = T(a)
    E.App._log_prop_stage_changes(t)
    w = open_dlg(t)
    ck("5h Lucid 21d 고정 줄: 펀디드 미리 고름 + 앱 고정 글", radio(w, "펀디드").instate(["selected"])
       and any("이전 버전에서 저장된 단계입니다" in x for x in labels(w)), labels(w))
    save(w)
    ck("5i 그대로 저장: 1R 450, 단계 답 '평가' 그대로(평가 계좌 익절 유지)", info(t, r, "nt8") == ("funded", 450.0, False)
       and r["prop"]["stage"] == "eval", r["prop"])
    close(w)

# ===== P2-5, P2-b =====
print("\n== P2-5 모르는 단계는 회원이 고름, P2-b 조기 반환 뒤 칸 잠금 ==")
if ROOT is not None:
    NUM2 = "99887766"                                    # 위 2d에서 NUM의 이름을 배웠다 - 이름 모르는 다른 숫자 id
    r = E._new_acct(500, NUM2, True, "n", None, None, False, "projectx", topstep=True)
    t = T(acfg(nq=[r]))
    w = open_dlg(t)
    next(x for x in _walk(w) if isinstance(x, ttk.Checkbutton)).invoke()
    ASKS.clear()
    save(w)
    ck("6a 단계를 고르지 않고 저장: 저장하지 않고 고르라는 안내(창은 열린 채)",
       r["prop"].get("on") is False and any("계좌 단계(챌린지 또는 펀디드)를 고른 뒤 저장하십시오" in m for m in ASKS) and w.winfo_exists(), ASKS)
    ck("6b 그 뒤에도 추천 금액 칸(펀디드 500)은 잠김", str(entries(w)[1].cget("state")) == "disabled", str(entries(w)[1].cget("state")))
    radio(w, "챌린지").invoke()
    entries(w)[0].delete(0, "end")
    entries(w)[0].insert(0, "1.2.3")
    ASKS.clear()
    save(w)
    ck("6c 읽지 못한 칸으로 저장 안 됨 - 그 뒤에도 추천 금액 칸 잠김", any("읽지 못한 칸" in m for m in ASKS)
       and str(entries(w)[1].cget("state")) == "disabled" and r["prop"].get("on") is False, (ASKS, str(entries(w)[1].cget("state"))))
    entries(w)[0].delete(0, "end")
    entries(w)[0].insert(0, "0")
    ASKS.clear()
    save(w)
    ck("6d 0인 칸으로 저장 안 됨 - 그 뒤에도 잠김", any("0보다 커야" in m for m in ASKS) and str(entries(w)[1].cget("state")) == "disabled", ASKS)
    close(w)
    # Lucid 단계 답이 없는 21d 줄(저장 이력 있음): 미리 고르지 않음
    ru = E._new_acct(500, "LPI150A4444", True, "u", {"on": True, "type": "test", "set": True, "r_test": 2200.0, "r_steady": 450.0},
                     None, False, "nt8", lucid=True)
    t = T(acfg(nq=[ru], nq_bk="nt8"))
    w = open_dlg(t)
    ck("6e Lucid 단계 답 없는 줄: 챌린지를 미리 고르지 않음(그대로 저장해 '평가' 답이 남지 않게)",
       not radio(w, "챌린지").instate(["selected"]) and not radio(w, "펀디드").instate(["selected"]), labels(w))
    ASKS.clear()
    save(w)
    ck("6f 그대로 저장 = 고르라는 안내, 단계 답 없음 그대로", ru["prop"].get("stage") is None and any("계좌 단계" in m for m in ASKS), ASKS)
    radio(w, "펀디드").invoke()
    save(w)
    ck("6g 펀디드를 고르면 그 계좌 단계 답 '펀디드', 1R 450", ru["prop"].get("stage") == "funded" and info(t, ru, "nt8")[0] == "funded",
       ru["prop"])
    close(w)

# ===== P2-3 =====
print("\n== P2-3 서버 권장 1R 띠 ==")


def srv(bk, st, sz, v):
    return ((E._prop_reco_validate({"reco_1r": {bk: {st: {str(sz): v}}}}).get("reco_1r") or {}).get(bk) or {}).get(st, {}).get(sz)


ck("7a 오타 17,500(평가 150K)은 받지 않음 - 내장 1,750", srv("projectx", "eval", 150000, 17500) is None)
ck("7b 내장값 +30% 넘는 2,300은 받지 않음, 2,200은 받음", srv("projectx", "eval", 150000, 2300) is None
   and srv("projectx", "eval", 150000, 2200) == 2200.0)
ck("7c 내리는 쪽은 받음(펀디드 300, 평가 900)", srv("projectx", "funded", 150000, 300) == 300.0 and srv("nt8", "eval", 150000, 900) == 900.0)
# 2026-10-27 검증 P2: 내장값이 없는 규모는 위 끝을 넘는 값을 버리지 않고 위 끝(센트 내림)으로 낮춰 받는다 - 버리면 돌아갈 내장값이 없어 저장 칸의
# 사본(150K $1,750 등)으로 주문했다. 넓은 범위(100~5,000) 밖, 글자는 종전처럼 버린다.
ck("7d 내장 표에 없는 규모는 150K 값 x 규모 비율 +30%까지(Topstep 50K 평가 600 받음, 900은 위 끝 758.33으로 낮춰 받음, 6,000, 50, 글자는 버림)",
   srv("projectx", "eval", 50000, 600) == 600.0 and srv("projectx", "eval", 50000, 900) == 758.33
   and srv("projectx", "eval", 50000, 6000) is None and srv("projectx", "eval", 50000, 50) is None
   and srv("projectx", "eval", 50000, "abc") is None)
ck("7e Lucid 펀디드 150K 600(450의 +33%) 안 받음", srv("nt8", "funded", 150000, 600) is None)
E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": {"projectx": {"eval": {"150000": 17500}}}})
ck("7f 받지 않은 칸은 내장값(1,750)", E._reco_one_r("projectx", "eval", 150000) == 1750.0)
E._DAYCAP_SRV = {}

# ===== 2026-10-27 검증 P2: 서버 권장 1R 띠와 사본 =====
print("\n== 검증 P2 내장값 없는 규모의 서버 값, 사본의 위 끝 ==")
# 검증자 하네스의 서버 파일(오늘 파일 + 규모별 값, 계좌 최대 손실에 맞춘 값): 21l 첫 판은 셋을 버리고 저장 사본 $1,750으로 주문했다(21k는 800/800/400)
SRV_SIZES = {"reco_1r": {"projectx": {"eval": {"150000": 1750, "50000": 800, "100000": 1150}, "funded": {"150000": 500}},
                         "nt8": {"eval": {"150000": 1750, "50000": 800, "25000": 400}, "funded": {"150000": 450}}}}
_cl = []
_vs = E._prop_reco_validate(SRV_SIZES, _cl)["reco_1r"]
ck("7g 규모별 서버 값: Topstep 50K 800 → 758.33, 100K 1,150 그대로, Lucid 50K 800 → 758.33, 25K 400 → 379.16, 150K 그대로",
   (_vs["projectx"]["eval"], _vs["nt8"]["eval"]) == ({150000: 1750.0, 50000: 758.33, 100000: 1150.0}, {150000: 1750.0, 50000: 758.33, 25000: 379.16})
   and sorted((b, n, f) for b, st, n, v, f in _cl) == [("nt8", 25000, 379.16), ("nt8", 50000, 758.33), ("projectx", 50000, 758.33)], (_vs, _cl))
ck("7h 낮춘 값은 위 끝 이하(다시 검증해도 그대로 - 디스크의 마지막 정상 값)",
   E._prop_reco_validate({"reco_1r": _vs}).get("reco_1r") == _vs
   and all(f <= E._srv_reco_bounds(b, st, n)[1] for b, st, n, v, f in _cl))
st7 = T(acfg())
E.App._prop_reco_apply(st7, SRV_SIZES)
ck("7i 받을 때 로그: 낮춘 칸 한 줄(회사, 단계, 규모, 서버 값 → 받은 값), '버린 칸' 안내 없음",
   sum(1 for m in st7.logs if "3칸은 앱에 들어 있는 값이 없는 규모에서 허용 위 끝" in m and "$800 → $758.33" in m) == 1
   and not any("쓰지 않습니다. 그 칸은" in m for m in st7.logs), st7.logs)
E.App._prop_reco_apply(st7, {"reco_1r": {"projectx": {"eval": {"50000": 9000}}}})
ck("7j 버린 칸 안내는 사실대로(내장값이 없으면 그 규모에는 추천 금액이 없음)",
   any("1칸은 범위를 벗어나거나 읽을 수 없어 쓰지 않습니다" in m and "없으면(권장 1R은 150K 말고는 앱에 들어 있는 값이 없습니다) 그 규모에는 추천 금액이 없는"
       in m for m in st7.logs), st7.logs[-2:])
E._DAYCAP_SRV = {}

# 21i~21k '추천 금액 쓰기' 줄(표식 없음, 사본 $1,750) - 그 규모에 추천 금액이 없으면 사본을 쓰되 그 규모 권장 1R의 위 끝까지만
P_LEG = dict(E._PROP_DEFAULTS, on=True, type="test", mode="reco", set=True, r_test=1750.0, r_steady=500.0)
LU025 = "LDI025A5555"
t7 = T(acfg())
_leg = {nm: (bk, row(nm, dict(P_LEG, stage="eval", stage_for=nm.casefold()) if bk == "nt8" else dict(P_LEG), bk=bk))
        for nm, bk in ((TS50, "projectx"), (LU050, "nt8"), (LU025, "nt8"), (TS150, "projectx"), ("100KTC-V2-7-7", "projectx"))}
_got = {nm: info(t7, r, bk) for nm, (bk, r) in _leg.items()}
ck("7k 오늘 서버 값(150K만): 50K 사본 1,750 → 758.33, Lucid 50K → 758.33, Lucid 25K → 379.16, 100K → 1,516.66, 150K는 추천 1,750 그대로",
   _got == {TS50: ("test", 758.33, False), LU050: ("test", 758.33, False), LU025: ("test", 379.16, False),
            TS150: ("test", 1750.0, True), "100KTC-V2-7-7": ("test", 1516.66, False)}, _got)
E._DAYCAP_SRV = E._prop_reco_validate(SRV_SIZES)
_got2 = {nm: info(t7, r, bk) for nm, (bk, r) in _leg.items()}
ck("7l 규모별 서버 값: 추천 금액(낮춘 값)을 따름 - 21k(800/800/400) 이하, 100K 1,150",
   _got2 == {TS50: ("test", 758.33, True), LU050: ("test", 758.33, True), LU025: ("test", 379.16, True),
             TS150: ("test", 1750.0, True), "100KTC-V2-7-7": ("test", 1150.0, True)}
   and _got2[TS50][1] <= 800 and _got2[LU025][1] <= 400, _got2)
E._DAYCAP_SRV = {}
_own = row(TS50, dict(P_LEG, mode="own"))
_num = row(NUM, dict(P_LEG))
_x50 = row(XFA, dict(P_LEG, type="funded", type_manual=True, size=50000))
_xd = row(XFA, dict(P_LEG, type="funded", type_manual=True))
ck("7m 바뀌지 않는 것: 직접 정하기 1,750, 규모를 모르는 숫자 id 줄의 사본 1,750, 규모 표식 없는 EXPRESS = 150K 펀디드 추천 500",
   (info(t7, _own), info(t7, _num)[1], info(t7, _xd)) == (("test", 1750.0, False), 1750.0, ("funded", 500.0, True)),
   (info(t7, _own), info(t7, _num), info(t7, _xd)))
_l25f = row(LU025, dict(P_LEG, type="funded", stage="funded", stage_for=LU025.casefold(), r_steady=450.0), bk="nt8")
ck("7n 규모를 50K로 저장한 EXPRESS 줄: 펀디드 사본 500 → 50K 펀디드 위 끝 216.66, Lucid 25K 펀디드 사본 450 → 97.5(서버 값은 받지 않는 칸도 위 끝은 있음)",
   info(t7, _x50) == ("funded", 216.66, False) and info(t7, _l25f, "nt8") == ("funded", 97.5, False)
   and E._srv_reco_bounds("nt8", "funded", 25000) is None, (info(t7, _x50), info(t7, _l25f, "nt8")))
t7.logs.clear()
ck("7o 진입 로그: 낮춘 사실과 저장된 금액, [프롭: …]",
   E.App._prop_one_r(t7, _leg[TS50][1]["prop"], {"broker": "projectx", "acct": TS50}, "a") == 758.33
   and any("1R=$758.33" in m and "추천 금액이 없어 저장된 금액 $1,750 대신 이 규모의 권장 1R 위 끝 $758.33" in m for m in t7.logs), t7.logs)
t7e = T(acfg(), lang="en")
E.App._prop_one_r(t7e, _leg[TS50][1]["prop"], {"broker": "projectx", "acct": TS50}, "a")
ck("7p 영어 로그", any("instead of the saved $1,750" in m and "$758.33 is used" in m for m in t7e.logs), t7e.logs)
if ROOT is not None:
    rd = row(TS50, dict(P_LEG))
    td = T(acfg(nq=[rd]))
    _before = info(td, rd)
    w = open_dlg(td)
    _ev = [e.get() for e in entries(w)][:2]
    save(w)
    ck("7q 설정 창: 사본 칸은 주문이 쓰는 금액(758.33, 펀디드 216.66)으로 보이고 그대로 저장해도 주문 1R이 커지지 않음",
       _ev == ["758.33", "216.66"] and _before == ("test", 758.33, False) and info(td, rd)[1] <= _before[1] + 1e-9,
       (_ev, _before, info(td, rd), rd["prop"]))
    close(w)

# ===== P2-7 =====
print("\n== P2-7 Tradovate Lucid 계좌 ==")
ck("8a Tradovate 어댑터의 Lucid 형식 계좌 = 프롭 회사 계좌(계약 한도와 같은 판정)",
   E._prop_firm_acct("tradovate", "LFE15012345678") is True and E._size_limit("tradovate", "LFE15012345678")[0] > 0)
ck("8b 개인 Tradovate 계좌는 아님", E._prop_firm_acct("tradovate", "DEMO1234567") is False and E._size_limit("tradovate", "DEMO1234567")[0] == 0)
ck("8c 한 계약 한도를 쓰는 계좌 = 계약 한도(_size_limit)가 프롭 한도를 거는 계좌(nt8, tradovate, projectx 이름 묶음)",
   all(E._prop_firm_acct(bk, nm) == (E._size_limit(bk, nm, bal=1000.0, size_hint=50000)[0] > 0 or bk == "projectx")
       for bk in ("nt8", "tradovate") for nm in ("LFE15012345678", "LDI050A1", "Sim101", "DEMO1234567", "APEX-1234-01")))

# ===== P2-8, P2-d =====
print("\n== P2-8 버튼 = 주문 단계, P2-d 영어 버튼 폭 ==")
rx = E._new_acct(500, XFA, True, "x", {"on": True, "type": "test", "mode": "own", "set": True, "type_manual": False,
                                        "r_test": 2200.0, "r_steady": 450.0}, None, False, "projectx", topstep=True)
t = T(acfg(nq=[rx]))
ck("9a 저장 유형 챌린지인 EXPRESS 줄: 버튼은 주문 단계 '프롭: 펀디드'(acct 없이 부르면 저장 유형)",
   t._prop_btn_text(rx["prop"], rx) == "프롭: 펀디드" and t._prop_btn_text(rx["prop"]) == "프롭: 챌린지")
_src = inspect.getsource(E.App)
ck("9b 영어 버튼 폭 16칸(한국어 13칸 - '프롭: 단계 선택')", 'width=(13 if self.lang == "ko" else 16)' in _src)
if ROOT is not None:
    _f = tkfont.nametofont("TkDefaultFont")
    ck("9c 'Prop: challenge'가 16칸 안에 들어감", _f.measure("Prop: challenge") <= 16 * _f.measure("0"),
       (_f.measure("Prop: challenge"), _f.measure("0")))

# ===== P2-a =====
print("\n== P2-a 한 계약 허용 한도 안내 ==")
rl = row(TS50, dict(E._PROP_DEFAULTS), one_r=50.0)
rl["one_lim"] = 300.0
t = T(acfg(nq=[rl]))
cfg = {"broker": "projectx", "f1": "u", "f2": "k", "f3": "", "acct": TS50, "one_r": 50.0, "label": "lim", "live": False,
       "prop": {"on": False}, "pct": {}, "one_lim": 300.0}
E.App._enter_account(t, cfg, {"entry_ref": 20000.0}, "NQ", "LONG", 19900.0, 1.0)
ck("10a Topstep 프롭 끔 줄 수량 0: '한도를 넘어'라고 하지 않고 Topstep, Lucid에는 한도를 쓰지 않는다고 말함",
   not any("한 계약 한도 $300를 넘어" in m for m in t.logs) and any("한 계약 허용 한도를 쓰지 않아 한 계약을 넣지 않았습니다" in m for m in t.logs)
   and ("NQ", "size_cap") in t.skips, (t.logs, t.skips))
t.logs.clear()
E.App._log_one_lim_ignored(t)
ck("10b 기동 로그: 한도를 저장해 둔 Topstep 줄 한 줄", sum(1 for m in t.logs if "…-9-9" in m and "쓰지 않습니다" in m) == 1, t.logs)
ro = row("U1234567", dict(E._PROP_DEFAULTS), bk="ibkr", one_r=50.0)
ro["one_lim"] = 300.0
t = T({"NQ": {"broker": "ibkr", "include": True, "creds": {}, "accounts": [ro]}, "GC": {"broker": "projectx", "accounts": []},
       "BTC": {"broker": "bybit", "accounts": []}})
E.App._log_one_lim_ignored(t)
ck("10c 개인 선물 계좌(IBKR)에는 그 안내 없음", not t.logs, t.logs)

# ===== 1R 도우미 =====
print("\n== 1R 도우미 권장 비율, 낙폭 ==")
ck("11a 내장값 0.5%, 32R", E._risk_ref() == (0.005, 32.0), E._risk_ref())
E._DAYCAP_SRV = E._prop_reco_validate({"risk": {"pct": 0.006, "mdd_r": 30}})
ck("11b 서버 값(범위 안)", E._risk_ref() == (0.006, 30.0), E._risk_ref())
E._DAYCAP_SRV = E._prop_reco_validate({"risk": {"pct": 0.05, "mdd_r": 500}})
ck("11c 범위 밖 서버 값은 내장값", E._risk_ref() == (0.005, 32.0), E._risk_ref())
E._DAYCAP_SRV = {}
if ROOT is not None:
    for lang, want in (("ko", ("0.5%", "약 32R", "약 16%")), ("en", ("0.5%", "~32R", "about 16%"))):
        rh = row("U1234567", dict(E._PROP_DEFAULTS), bk="ibkr")
        rh["pct"] = {"on": False, "pct": 0.4, "floor": 200.0}
        t = T({"NQ": {"broker": "ibkr", "include": True, "creds": {}, "accounts": [rh]}, "GC": {"broker": "projectx", "accounts": []},
               "BTC": {"broker": "bybit", "accounts": []}}, lang=lang)
        t.root = ROOT
        E.App._pct_dialog(t, 0)
        ROOT.update()
        w = [x for x in ROOT.winfo_children() if isinstance(x, tk.Toplevel)][-1]
        _ref = [x for x in labels(w) if x.startswith(("참고:", "Reference:"))]
        ck(f"11d 참고 문장({lang}) 숫자가 0.5%, 32R, 16%(종전 0.4%, 53R, 21%)", _ref and all(s_ in _ref[0] for s_ in want)
           and "53R" not in _ref[0] and "21%" not in _ref[0], _ref)
        # 21l 검증 7: 옛 설정의 0.4는 회원이 고른 값일 수 있다 - 그대로 보이고 권장 비율은 옆 줄로만(칸을 올려 [1R로 적용] 한 번에 1R이 커지지 않게)
        ck(f"11e 비율 칸({lang}): 옛 설정의 0.4는 그대로, 옆에 지금 권장 비율 0.5%", entries(w)[1].get() == "0.4"
           and any(("저장된 비율입니다(지금 권장 비율 0.5%)" if lang == "ko" else "recommended now: 0.5%") in x for x in labels(w)),
           ([e.get() for e in entries(w)], labels(w)))
        close(w)
    rh = row("U1234567", dict(E._PROP_DEFAULTS), bk="ibkr")
    rh["pct"] = E._new_acct(500, "U1234567", True, "", None, None)["pct"]          # 새 계좌(고른 비율 없음)
    t = T({"NQ": {"broker": "ibkr", "include": True, "creds": {}, "accounts": [rh]}, "GC": {"broker": "projectx", "accounts": []},
           "BTC": {"broker": "bybit", "accounts": []}})
    t.root = ROOT
    E.App._pct_dialog(t, 0)
    ROOT.update()
    w = [x for x in ROOT.winfo_children() if isinstance(x, tk.Toplevel)][-1]
    ck("11e2 새 계좌(고른 비율 없음): 권장 비율 0.5, '저장된 비율' 글 없음", entries(w)[1].get() == "0.5"
       and not any("저장된 비율" in x for x in labels(w)), ([e.get() for e in entries(w)], labels(w)))
    close(w)
    rh = row("U1234567", dict(E._PROP_DEFAULTS), bk="ibkr")
    rh["pct"] = {"on": False, "pct": 0.7, "floor": 200.0, "set": True}
    t = T({"NQ": {"broker": "ibkr", "include": True, "creds": {}, "accounts": [rh]}, "GC": {"broker": "projectx", "accounts": []},
           "BTC": {"broker": "bybit", "accounts": []}})
    t.root = ROOT
    E.App._pct_dialog(t, 0)
    ROOT.update()
    w = [x for x in ROOT.winfo_children() if isinstance(x, tk.Toplevel)][-1]
    ck("11f 회원이 정한 비율(0.7)은 그대로", entries(w)[1].get() == "0.7", [e.get() for e in entries(w)])
    close(w)
ck("11g 설정을 다시 읽어도 회원이 정한 비율 표식(set) 유지",
   E._new_acct(500, "U1", True, "", None, {"on": False, "pct": 0.7, "set": True})["pct"]["set"] is True)

# ===== 21l 검증(2026-10-27, 두 검증자 소견 수리) =====
print("\n== 21l 검증 1: 단계를 모르는 Lucid 계좌는 두 단계 가운데 작은 1R ==")
LA, LB = "LDI150A1212", "LDI150B3434"
rv = E._new_acct(500, LA, True, "v", {"on": True, "type": "test", "mode": "reco", "set": True, "type_manual": False,
                                       "r_test": 1750.0, "r_steady": 450.0, "stage": "eval", "stage_for": LA.casefold(),
                                       "reco_keys": ["r_test", "r_steady"], "reco_acct": LA.casefold()}, None, False, "nt8", lucid=True)
t = T(acfg(nq=[rv], nq_bk="nt8"))
ck("v1a 바꾸기 전: 평가 1,750(추천 금액)", info(t, rv, "nt8") == ("test", 1750.0, True), info(t, rv, "nt8"))
switch(t, "NQ", rv, LB)
ck("v1b 같은 규모의 다른 Lucid 계좌로 바꿈(단계 모름): 작은 쪽 펀디드 450(첫 21l 수리는 평가 1,750)",
   info(t, rv, "nt8") == ("funded", 450.0, True) and t._daycap_stage_cfg("nt8", LB, rv["prop"])[0] == "unanswered",
   (info(t, rv, "nt8"), rv["prop"]))
t.logs.clear()
ck("v1c 주문 로그: 단계를 몰라 작은 쪽이라고 말함", E.App._prop_one_r(t, rv["prop"], {"broker": "nt8", "acct": LB}, "v") == 450.0
   and any("Lucid 계좌 단계를 아직 몰라 두 단계 1R 가운데 작은 쪽" in m for m in t.logs), t.logs)
t.lang = "en"
t.logs.clear()
E.App._prop_one_r(t, rv["prop"], {"broker": "nt8", "acct": LB}, "v")
ck("v1d 영어 로그도 같은 말", any("smaller of the two stage 1Rs" in m for m in t.logs), t.logs)
ck("v1e 버튼(영어) = 'Prop: pick stage'", t._prop_btn_text(rv["prop"], rv) == "Prop: pick stage", t._prop_btn_text(rv["prop"], rv))
t.lang = "ko"
ck("v1f 버튼 = '프롭: 단계 선택'(앱이 단계를 아는 것처럼 보이지 않게)", t._prop_btn_text(rv["prop"], rv) == "프롭: 단계 선택",
   t._prop_btn_text(rv["prop"], rv))
ro_ = E._new_acct(500, LA, True, "o", {"on": True, "type": "test", "mode": "own", "set": True, "type_manual": False,
                                        "r_test": 2200.0, "r_steady": 450.0, "stage": "eval", "stage_for": LA.casefold()},
                  None, False, "nt8", lucid=True)
t = T(acfg(nq=[ro_], nq_bk="nt8"))
switch(t, "NQ", ro_, LB)
ck("v1g 직접 정하기 줄도 단계를 모르면 회원 값 가운데 작은 쪽(펀디드 450, 평가 2,200 아님)", info(t, ro_, "nt8") == ("funded", 450.0, False),
   info(t, ro_, "nt8"))
ro_["prop"]["r_steady"] = 0.0
t.logs.clear()
t.skips.clear()
cfg = {"broker": "nt8", "f1": "u", "f2": "k", "f3": "", "acct": LB, "one_r": 500.0, "label": "o", "live": False,
       "prop": ro_["prop"], "pct": {}, "one_lim": 0.0}
E.App._enter_account(t, cfg, {"entry_ref": 20000.0}, "NQ", "LONG", 19950.0, 1.0)
ck("v1h 한 단계 1R이 0이면 어느 쪽인지 몰라 건너뜀(평가 2,200으로 들어가지 않음) + 이유 한 줄 + 'prop_r'",
   info(t, ro_, "nt8")[1] is None and E.App._prop_r_gap(t, ro_["prop"], "nt8", LB, "test") == "stage"
   and t.skips == [("NQ", "prop_r")] and any("챌린지(평가)인지 펀디드인지 아직 몰라" in m for m in t.logs)
   and not any("사이징 불가" in m for m in t.logs), (t.skips, t.logs))
ro_["prop"]["r_steady"] = 450.0
E.App._lucid_stage_set(t, LB, "eval")
ck("v1i 회원이 단계(평가)를 고르면 그 단계 1R(2,200)", info(t, ro_, "nt8") == ("test", 2200.0, False), (info(t, ro_, "nt8"), ro_["prop"]))
E._PROP_TYPE_CHECK.clear()
E._PROP_LEGACY_NOTES.clear()
a = reload_rows(acfg(nq=[row("LDI150C5656", {"on": True, "type": "test", "set": True, "r_test": 2200.0, "r_steady": 450.0}, bk="nt8")],
                     nq_bk="nt8"))
r21 = a["NQ"]["accounts"][0]
t = T(a)
E.App._log_prop_stage_changes(t)
ck("v1j 21d에서 올라온 단계 답 없는 Lucid 줄은 21d 그대로(저장된 유형 = 회원이 21d 창에서 고른 단계, 2,200)",
   r21["prop"].get("from21d") is True and info(t, r21, "nt8") == ("test", 2200.0, False), (r21["prop"], info(t, r21, "nt8")))
bad = []
for a1, a2 in ((LA, LB), (LB, "LDI150D7878"), ("LDI150E1111", "LFE15022222222")):
    for mode in ("reco", "own"):
        pr = {"on": True, "type": "test", "mode": mode, "set": True, "type_manual": False, "r_test": 1750.0, "r_steady": 450.0,
              "stage": "eval", "stage_for": a1.casefold()}
        if mode == "reco":
            pr.update(reco_keys=["r_test", "r_steady"], reco_acct=a1.casefold())
        rr = E._new_acct(500, a1, True, "b", pr, None, False, "nt8", lucid=True)
        tt = T(acfg(nq=[rr], nq_bk="nt8"))
        switch(tt, "NQ", rr, a2)
        _r = info(tt, rr, "nt8")[1]
        if not (_r is None or _r <= 450.0 + 1e-9):
            bad.append((a1, a2, mode, info(tt, rr, "nt8")))
ck("v1k Lucid 150K 사이 계좌 바꾸기(추천, 직접): 1R = None 또는 작은 쪽(450 이하)", not bad, bad)

print("\n== 21l 검증 3 → 2026.11.09b: 단계 고정이 남은 Lucid 줄도 통과하면 멈춤(펀디드로 바꾸지 않음 - 새 LDF 계좌) ==")
for nm, extra in (("21i~21j 창 고정", {}), ("21d 앱 고정", {"type_pin": True})):
    LG = "LDI150A8989" if not extra else "LDI150A9898"
    pg = dict({"on": True, "type": "test", "mode": "own", "set": True, "type_manual": True, "r_test": 1750.0, "r_steady": 450.0,
               "stage": "eval", "stage_for": LG.casefold()}, **extra)
    rgq = E._new_acct(500, LG, True, "g", pg, None, False, "nt8", lucid=True)
    t = T(acfg(nq=[rgq], nq_bk="nt8"))
    ck(f"v3a {nm}: 통과 전 평가 1,750", info(t, rgq, "nt8") == ("test", 1750.0, False), info(t, rgq, "nt8"))
    t.logs.clear()
    E.App._daycap_mark_passed(t, "nt8", LG, bal=159100.0, pass_tp=159050.0, lbl="g", size=150000, via="watch", target=9000.0)
    ck(f"v3b {nm}: 통과 표시 뒤 단계 'passed'(이 줄 진입 멈춤), 줄 설정은 그대로(고정, 유형 챌린지 - 펀디드로 바꾸지 않음)",
       t._daycap_stage_cfg("nt8", LG, rgq["prop"])[0] == "passed" and rgq["prop"]["type_manual"] is True
       and rgq["prop"].get("type_pin", False) is bool(extra.get("type_pin")) and rgq["prop"]["type"] == "test", rgq["prop"])
    ck(f"v3c {nm}: 통과 로그 = 새 펀디드 계좌(LDF)로 바꾸라(펀디드 1R을 이 줄에 쓴다는 말 없음)",
       any("새 펀디드 계좌" in m for m in t.logs) and not any("계좌 단계를 펀디드로 저장해" in m for m in t.logs), t.logs)
    t._dc_state()["passed"].clear()
    ck(f"v3d {nm}: 통과 기록이 없어지면(다른 PC) 평가 그대로 1,750(통과는 이 PC 기록뿐 - Topstep Combine과 같다)",
       info(t, rgq, "nt8") == ("test", 1750.0, False), info(t, rgq, "nt8"))

print("\n== 21l 검증 4: 표식 전 '추천 금액 쓰기' 줄 ==")
E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": {"projectx": {"eval": {"150000": 2000}}}})
rl4 = E._new_acct(500, "150KTC-V2-5-5", True, "s", {"on": True, "type": "test", "mode": "reco", "set": True, "r_test": 1750.0,
                                                     "r_steady": 500.0}, None, False, "projectx", topstep=True)
t = T(acfg(nq=[rl4]))
ck("v4a 150K 이름 계좌의 표식 전 줄(그 창이 칸을 잠갔다 = 사본): 서버가 올린 추천 금액 2,000을 따라감(첫 21l 수리는 1,750)",
   info(t, rl4) == ("test", 2000.0, True), info(t, rl4))
NUM4, NUM6 = "44445555", "33334444"
rl5 = E._new_acct(500, NUM4, True, "n4", {"on": True, "type": "test", "mode": "reco", "set": True, "type_manual": True,
                                           "r_test": 1000.0, "r_steady": 500.0}, None, False, "projectx", topstep=True)
rl6 = E._new_acct(500, NUM6, True, "n6", {"on": True, "type": "test", "mode": "reco", "set": True, "type_manual": True,
                                           "r_test": 1750.0, "r_steady": 500.0}, None, False, "projectx", topstep=True)
if ROOT is not None:
    w = open_dlg(t)
    ck("v4b 그 줄의 창: 챌린지 칸 잠김 2,000, '직접 넣으신' 글 없음",
       [(str(e.cget("state")), e.get()) for e in entries(w)][:2] == [("disabled", "2000"), ("disabled", "500")]
       and not any("직접 넣으신" in x or "이전 버전에서 저장된 챌린지" in x for x in labels(w)),
       ([(str(e.cget("state")), e.get()) for e in entries(w)], labels(w)))
    save(w)
    ck("v4c 그대로 저장: 두 칸 모두 따라감(표식), 2,000", sorted(rl4["prop"].get("reco_keys") or []) == ["r_steady", "r_test"]
       and info(t, rl4) == ("test", 2000.0, True), rl4["prop"])
    close(w)
    t = T(acfg(nq=[rl5]))
    E.App._daycap_learn(t, "projectx", NUM4, name="150KTC-V2-44445555")
    ck("v4d 숫자 id 표식 전 줄: 작은 쪽 1,000(서버 2,000)", info(t, rl5) == ("test", 1000.0, False), info(t, rl5))
    w = open_dlg(t)
    ck("v4e 창 글: 이전 버전에서 저장된 금액이라고 사실대로('직접 넣으신'이 아님)",
       any("이전 버전에서 저장된 챌린지 1R($1,000)이 지금 추천 금액($2,000)보다 작아" in x for x in labels(w))
       and not any("직접 넣으신 챌린지 1R은 그대로 씁니다" in x for x in labels(w)), labels(w))
    save(w)
    ck("v4f 그대로 저장해도 표식 전 규칙 그대로(따라가는 칸 표식 없음, 저장 값 1,000)", rl5["prop"].get("reco_keys") is None
       and rl5["prop"]["r_test"] == 1000.0 and info(t, rl5) == ("test", 1000.0, False), rl5["prop"])
    E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": {"projectx": {"eval": {"150000": 900}}}})
    ck("v4g 그 뒤 서버가 900으로 내리면 따라감(굳지 않았다)", info(t, rl5) == ("test", 900.0, True), info(t, rl5))
    E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": {"projectx": {"eval": {"150000": 2000}}}})
    w = open_dlg(t)
    radio(w, "추천 금액 쓰기").invoke()
    ROOT.update()
    ck("v4h '추천 금액 쓰기'를 한 번 더 누르면 챌린지도 추천 금액 2,000으로 잠김", entries(w)[0].get() == "2000"
       and str(entries(w)[0].cget("state")) == "disabled", [(str(e.cget("state")), e.get()) for e in entries(w)])
    save(w)
    ck("v4i 저장 뒤 두 칸 모두 따라감, 2,000(회원이 누른 것)", sorted(rl5["prop"].get("reco_keys") or []) == ["r_steady", "r_test"]
       and info(t, rl5) == ("test", 2000.0, True), rl5["prop"])
    close(w)
    E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": {"projectx": {"eval": {"150000": 1500}}}})
    t = T(acfg(nq=[rl6]))
    E.App._daycap_learn(t, "projectx", NUM6, name="150KTC-V2-33334444")
    w = open_dlg(t)
    ck("v4j 저장 값(1,750)이 추천 금액(1,500) 이상인 표식 전 줄: 칸은 추천 금액 1,500으로 잠김", entries(w)[0].get() == "1500"
       and str(entries(w)[0].cget("state")) == "disabled", [(str(e.cget("state")), e.get()) for e in entries(w)])
    save(w)
    ck("v4k 그대로 저장: 저장 값 1,750과 표식 전 규칙이 남음, 주문 1,500", rl6["prop"].get("reco_keys") is None
       and rl6["prop"]["r_test"] == 1750.0 and info(t, rl6) == ("test", 1500.0, True), rl6["prop"])
    E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": {"projectx": {"eval": {"150000": 2000}}}})
    ck("v4l 그 뒤 서버가 2,000으로 올려도 1,750(회원이 친 금액일 수 있는 저장 값을 넘지 않음)", info(t, rl6) == ("test", 1750.0, False),
       info(t, rl6))
    close(w)
E._DAYCAP_SRV = {}

print("\n== 21l 검증 6: 비중 로그 ==")
rw = E._new_acct(500, TS150, True, "w", {"on": True, "type": "test", "mode": "reco", "set": True, "type_manual": False, "r_test": 1750.0,
                                          "r_steady": 500.0, "reco_keys": ["r_test", "r_steady"], "reco_acct": TS150.casefold()},
                 None, False, "projectx", topstep=True)
rw2 = row("LX1", dict(E._PROP_DEFAULTS), one_r=600.0)
rgc = row("GX1", dict(E._PROP_DEFAULTS), one_r=600.0)
for lang, want, nope in (("ko", "1R을 정하지 못한 프롭 1개 제외", "5발완료"), ("en", "excluding 1 prop account(s) whose 1R could not be set", "5발")):
    t = T(acfg(nq=[rw, rw2], gc=[rgc]), lang=lang)
    if rw["id"] == TS150:
        switch(t, "NQ", rw, TS50)
    t.logs.clear()
    _wt = E.App._portfolio_risk_weights(t)
    ck(f"v6a 비중 로그({lang}): 1R을 정하지 못한 프롭 계좌라고 말함('5발완료' 아님)",
       _wt and _wt["NQ"] == _wt["GC"] and any(want in m for m in t.logs) and not any(nope in m for m in t.logs), (_wt, t.logs))

# ===== 정리 =====
print("\n== 정리 ==")
ck("12a 쓰지 않던 _prop_preset_now, _prop_preset 없음", not hasattr(E, "_prop_preset_now") and not hasattr(E, "_prop_preset"))
ck("12b [시뮬 기준값 채우기]를 '유일한 적용 경로'라고 하던 낡은 주석 없음", "버튼이 유일한 적용 경로" not in inspect.getsource(E))
ck("12c 새 앱 문구에 가운뎃점 없음(이번 수리 문장)", all("·" not in s_ for s_ in (
    inspect.getsource(E.App._prop_one_r), inspect.getsource(E.App._lucid_mark_funded), inspect.getsource(E.App._lucid_passed_persist),
    inspect.getsource(E.App._log_one_lim_ignored))))

if ROOT is not None:
    try:
        ROOT.destroy()
    except Exception:
        pass
print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
