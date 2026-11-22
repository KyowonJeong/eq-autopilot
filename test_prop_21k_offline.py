#!/usr/bin/env python3
# EQ Autopilot 2026.10.21k - 21j 두 번째 리뷰 수리 시험(2026-10-21). 0 = 통과.
# 원칙: 업그레이드만으로(회원이 아무것도 누르지 않았는데) 주문 1R이 21d보다 커지지 않는다. 작아지는 쪽은 로그 한 줄과 함께 허용.
#   S1 Lucid: 21d 유형 펀디드 + 낡은 Lucid 단계 답 '평가' -> 판별 단계 1R이 더 크므로 저장된 펀디드로 고정(type_manual) + 로그 한 줄
#   S2 Topstep 숫자 id(기동 때 이름 모름) -> 나중에 이름(150KTC)을 알게 돼도 1R이 커지지 않음(from21d 표식, _prop_r_info)
#   S3 5발(출금 5회) 펀디드 -> 기동 로그 한 줄(5발 진입 중단은 없어졌고 이제 펀디드 1R로 진입)
#   M  21d 설정 묶음 전체: 21k 주문 1R <= 21d 주문 1R(5발 계좌 제외 - 21d는 진입 안 함)
#   P2-3 내장 권장표 = 서버 값(Topstep 1750/500, Lucid 1750/450)
#   P2-4 Lucid 설정 창 저장은 회원 고정(type_manual)으로 굳히지 않는다 - 통과 표시 뒤 펀디드 자동 전환이 먹는다
# HOME = 임시 폴더, 네트워크 차단, 주문 없음, 알림 없음.
import itertools
import os
import socket
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_p21k_")
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

FAIL = []


def ck(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:500]}]"))
    if not c:
        FAIL.append(n)


ASKS = []
E.messagebox.askyesno = lambda t, m, **k: (ASKS.append(m), True)[1]
E.messagebox.showinfo = lambda t, m, **k: ASKS.append("INFO " + m)
E.messagebox.showwarning = lambda t, m, **k: ASKS.append("WARN " + m)

# ===== P2-3: 내장 권장표 = 서버 값 (서버 값을 받기 전) =====
print("\n== P2-3 내장 권장표 ==")
E._DAYCAP_SRV = {}
ck("0a 내장 권장 1R(서버 값 전): Topstep 평가 1,750 / 펀디드 500, Lucid 평가 1,750 / 펀디드 450",
   (E._reco_one_r("projectx", "eval", 150000), E._reco_one_r("projectx", "funded", 150000),
    E._reco_one_r("nt8", "eval", 150000), E._reco_one_r("nt8", "funded", 150000)) == (1750.0, 500.0, 1750.0, 450.0))
SRV = {"reco_1r": {"projectx": {"eval": {"150000": 1750}, "funded": {"150000": 500}},
                   "nt8": {"eval": {"150000": 1750}, "funded": {"150000": 450}}}}
E._DAYCAP_SRV = E._prop_reco_validate(SRV)
ck("0b 서버 값과 내장값이 같다(하트비트 전후로 추천 금액이 바뀌지 않음)",
   all(E._reco_table()[bk][st][150000] == E._RECO_1R[bk][st][150000] for bk in ("projectx", "nt8") for st in ("eval", "funded")))


class T(E.App):
    def __init__(self, acfg, asset="NQ"):
        self._acfg, self._asset, self.lang, self.logs, self.saved = acfg, asset, "ko", [], 0
        self._acct_widgets = {}

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

    def _dc_tchg_update(self):
        pass

    def _crypto_unique_ok(self, asset):
        return True


def row(aid, prop, bk="projectx", one_r=500.0, label=""):
    return {"id": aid, "one_r": one_r, "on": True, "label": label or aid[-4:], "broker": bk, "prop": prop, "pct": {}, "one_lim": 0.0}


def acfg(nq=()):
    return {"NQ": {"broker": "projectx", "include": True, "creds": {}, "accounts": list(nq)},
            "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": []},
            "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}


def reload_rows(cfg):
    E._save_full("ko", "", cfg)
    return E._load()["assets"]


LU, TS, XFA, NUM = "LDI150A7777", "150KTC-V2-111-222", "EXPRESS-V2-555-666", "12345678"

# ===== S1, S2, S3 (리뷰 재현 upgrade_scen) =====
print("\n== S1~S3 21d 설정 업그레이드 ==")
E._PROP_TYPE_CHECK.clear()
E._PROP_LEGACY_NOTES.clear()
d21 = acfg(nq=[
    row(LU, {"on": True, "type": "funded", "set": True, "r_test": 2200.0, "r_steady": 450.0, "r_buffer": 450.0, "buffer": 0.0,
             "stage": "eval", "stage_for": LU.casefold()}, bk="nt8", label="S1"),
    row(NUM, {"on": True, "type": "funded", "set": True, "r_test": 2200.0, "r_steady": 450.0}, label="S2"),
    row(XFA, {"on": True, "type": "funded", "set": True, "r_test": 2200.0, "r_steady": 450.0, "payouts": 5}, label="S3")])
a = reload_rows(d21)
s1, s2, s3 = a["NQ"]["accounts"]
t = T(a)
E.App._log_prop_stage_changes(t)
L = list(t.logs)
ck("S1a Lucid 펀디드 + 낡은 평가 답: 저장된 펀디드로 고정(type_manual=True), 주문 1R 450(21d와 같음, 21j는 2,200)",
   s1["prop"]["type_manual"] is True and E.App._prop_r_info(t, s1["prop"], "nt8", LU) == ("funded", 450.0, False), s1["prop"])
ck("S1b 기동 로그 한 줄: 판별 단계 챌린지지만 1R이 커지므로 저장된 단계(펀디드)로 계속 주문, 바꾸려면 '계좌 단계'를 고르라는 안내",
   sum(1 for m in L if "…7777" in m and "알아낸 단계는 '챌린지'" in m and "저장된 단계는 '펀디드'" in m
       and "$450에서 $2,200로 커지므로 저장된 단계로 계속 주문합니다" in m and "'계좌 단계'를 고르십시오" in m) == 1, L)
ck("S2a Topstep 숫자 id(이름 모름) = 저장된 펀디드 450, 21d 표식(from21d) 저장됨",
   E.App._prop_r_info(t, s2["prop"], "projectx", NUM) == ("funded", 450.0, False) and s2["prop"].get("from21d") is True, s2["prop"])
E.App._daycap_learn(t, "projectx", NUM, name="150KTC-V2-12345678")
ck("S2b 나중에 이름(150KTC, 챌린지)을 알게 돼도 주문 1R 450 그대로(21j는 2,200)",
   E.App._prop_r_info(t, s2["prop"], "projectx", NUM) == ("funded", 450.0, False), E.App._prop_r_info(t, s2["prop"], "projectx", NUM))
t.logs.clear()
ck("S2c 진입 로그 한 줄에 '21d 설정: 앱이 알아낸 단계는 1R이 커서 저장된 단계로 주문'",
   E.App._prop_one_r(t, s2["prop"], {"broker": "projectx", "acct": NUM}, "S2") == 450.0
   and "21d 설정: 앱이 알아낸 단계는 1R이 커서 저장된 단계로 주문" in t.logs[-1], t.logs)
a2 = reload_rows(a)
ck("S2d 저장 -> 재기동 뒤에도 21d 표식 유지, 이름을 아는 상태에서 1R 450",
   a2["NQ"]["accounts"][1]["prop"].get("from21d") is True
   and E.App._prop_r_info(T(a2), a2["NQ"]["accounts"][1]["prop"], "projectx", NUM)[1] == 450.0, a2["NQ"]["accounts"][1]["prop"])
# 2026-11-10 리뷰 P2: 21d처럼 진입하지 않는다(p5_hold) - 기동 로그는 다시 켜는 길을 말한다
ck("S3a 5발 펀디드 = 기동 로그 한 줄(21d처럼 진입하지 않음, 프롭 창에서 다시 켜기)",
   sum(1 for m in L if "…-666" in m and "이 계좌로 진입하지 않습니다" in m and "출금 5회로 멈춘 진입 다시 켜기" in m) == 1, L)
E.App._log_prop_stage_changes(t)
ck("S3b 같은 실행에서 다시 불러도 로그 늘지 않음", not [m for m in t.logs if "ℹ" in m], t.logs)
# 판별 단계의 1R이 더 작으면 판별 단계로(로그 한 줄)
E._PROP_TYPE_CHECK.clear()
E._PROP_LEGACY_NOTES.clear()
a3 = reload_rows(acfg(nq=[row(XFA, {"on": True, "type": "test", "set": True, "r_test": 2200.0, "r_steady": 450.0}, label="DN")]))
t3 = T(a3)
E.App._log_prop_stage_changes(t3)
ck("S4 저장 챌린지 + 이름은 펀디드(EXPRESS): 1R이 작아지므로 판별 단계로(2,200 -> 450) + 로그 한 줄",
   E.App._prop_r_info(t3, a3["NQ"]["accounts"][0]["prop"], "projectx", XFA) == ("funded", 450.0, False)
   and a3["NQ"]["accounts"][0]["prop"]["type_manual"] is False
   and sum(1 for m in t3.logs if "알아낸 단계로 주문합니다(1R $2,200 → $450)" in m) == 1, t3.logs)

# ===== M: 21d 설정 묶음 - 21k 주문 1R <= 21d =====
print("\n== M 21d 설정 묶음 ==")


def r21d(p):
    t_ = p.get("type")
    if t_ == "live":
        return float(p.get("r_live", 100.0))
    if t_ != "funded":
        return float(p.get("r_test", 1200.0))
    if int(p.get("payouts", 0)) >= 5:
        return None
    return float(p.get("r_steady", 300.0))


names = [("projectx", TS), ("projectx", XFA), ("projectx", "50KTC-V2-9-9"), ("projectx", NUM), ("nt8", LU), ("nt8", "LDI025A1111")]
stages = [None, "eval", "funded"]
types = ["test", "funded", "live"]
vals = [(2200.0, 450.0, 100.0), (900.0, 1200.0, 300.0)]
setk = [True, None]
bad, n = [], 0
for (bk, aid), stg, ty, (rt, rs, rl), sk in itertools.product(names, stages, types, vals, setk):
    if bk == "projectx" and stg:
        continue
    pr = {"on": True, "type": ty, "r_test": rt, "r_steady": rs, "r_buffer": rs, "r_live": rl, "buffer": 0.0}
    if sk is not None:
        pr["set"] = sk
    if stg:
        pr["stage"], pr["stage_for"] = stg, aid.casefold()
    E._PROP_TYPE_CHECK.clear()
    E._PROP_LEGACY_NOTES.clear()
    am = reload_rows(acfg(nq=[row(aid, dict(pr), bk=bk)]))
    tm = T(am)
    E.App._log_prop_stage_changes(tm)
    p = am["NQ"]["accounts"][0]["prop"]
    old = r21d(pr)
    new = E.App._prop_r_info(tm, p, bk, aid)[1]
    if aid == NUM:                                       # 이름을 나중에 알게 되는 Topstep 숫자 id
        E.App._daycap_learn(tm, "projectx", NUM, name="150KTC-V2-12345678")
        new = max(new, E.App._prop_r_info(tm, p, bk, aid)[1])
    n += 1
    if old is not None and new > old + 1e-9:
        bad.append((bk, aid, stg, ty, rt, rs, rl, sk, old, new))
ck(f"M1 21d 설정 {n}가지: 업그레이드만으로 주문 1R이 21d보다 커지는 경우 0", not bad and n > 100, bad[:5])

# ===== P2-4: Lucid 설정 창 저장 =====
print("\n== P2-4 Lucid 설정 창 ==")
try:
    import tkinter as tk
    from tkinter import ttk
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as _e:
    ROOT = None
    ck("P0 Tk 사용 가능", False, _e)
if ROOT is not None:
    def _walk(w):
        out = [w]
        for ch in w.winfo_children():
            out.extend(_walk(ch))
        return out

    LQ = "LDI150B9999"
    rq = row(LQ, E._new_acct(500, LQ, True, "q", None, None, False, "nt8", lucid=True)["prop"], bk="nt8")
    tq = T(acfg(nq=[rq]))
    tq._acfg["NQ"]["broker"] = "nt8"
    tq.root = ROOT
    E.App._prop_dialog(tq, 0)
    ROOT.update()
    w = [x for x in ROOT.winfo_children() if isinstance(x, tk.Toplevel)][-1]
    next(x for x in _walk(w) if isinstance(x, ttk.Checkbutton)).invoke()             # 프롭 계좌로 사용
    next(x for x in _walk(w) if isinstance(x, ttk.Radiobutton) and str(x.cget("text")) == "챌린지(평가)").invoke()
    ASKS.clear()
    next(x for x in _walk(w) if isinstance(x, ttk.Button) and str(x.cget("text")) == "저장").invoke()
    ROOT.update()
    pq = tq._acfg["NQ"]["accounts"][0]["prop"]
    ck("P1 Lucid 단계 미답 계좌에서 챌린지를 골라 저장 = 단계 답 '평가', 회원 고정 안 함(type_manual=False), 주문 1R 챌린지 1,750",
       pq.get("on") is True and pq.get("stage") == "eval" and pq.get("type_manual") is False
       and E.App._prop_r_info(tq, pq, "nt8", LQ) == ("test", 1750.0, True) and not any(m.startswith("WARN") for m in ASKS), (pq, ASKS))
    tq._dc_state()["passed"][E.App._daycap_key("nt8", LQ)] = {"day": "x", "bal": 159500.0, "pass_tp": 159000.0, "size": 150000,
                                                              "ts": time.time(), "via": "watch", "target": None, "src": "set"}
    # 2026-11-10 리뷰 P0: 통과한 Lucid 평가 줄은 펀디드로 바꾸지 않는다(Lucid는 새 펀디드 계좌를 준다) - 단계 없음, 이 줄은 진입하지 않는다
    ck("P2 그 뒤 평가 통과로 표시되면 펀디드로 자동 전환하지 않음(단계 없음, 'passed' = 진입 안 함)",
       E.App._prop_auto_type(tq, "nt8", LQ, pq) is None and tq._daycap_stage_cfg("nt8", LQ, pq)[0] == "passed"
       and E.App._prop_r_info(tq, pq, "nt8", LQ)[0] == "test", E.App._prop_r_info(tq, pq, "nt8", LQ))
    try:
        if w.winfo_exists():
            w.destroy()
        ROOT.destroy()
    except Exception:
        pass

print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
