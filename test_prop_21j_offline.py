#!/usr/bin/env python3
# EQ Autopilot 2026.10.21j - 21i 검토 수리 시험(2026-10-21, 5f는 21k 규칙: 업그레이드만으로 1R이 커지지 않는다). 0 = 통과.
#   P0-1 추천 금액 쓰기(mode), 회원이 고른 단계(type_manual)가 저장 -> 재기동(_load)을 넘는다. [프롭 추천 금액 쓰기]는 추천 값을 저장 칸에도 복사한다.
#   P0-2 평가 통과로 표시된 Lucid = 펀디드로 본다, Topstep 통과 = 저장된 유형. [바꾸기](_apply_reco_row)는 회원이 고른 단계로 남는다.
#   P1-1 추천 금액은 계좌 규모별(_reco_one_r) - 표에 없는 규모는 추천 없음(직접 정한 1R 그대로).
#   P1-2 21d 설정을 읽을 때 회원 1R을 말없이 바꾸지 않는다(라이브 초기 = 펀디드 + 그 1R, 방식 없는 켜진 프롭 = 직접 정하기, 단계가 바뀌면 로그 한 줄).
#   P2  서버 추천 값 로그 문구, 설정 창이 회원이 고른 단계를 존중, [프롭 추천 금액 쓰기]는 BTC 탭에 없음.
# HOME = 임시 폴더, 네트워크 차단, 메시지박스 = 기록, 주문 없음, 알림 없음.
import inspect
import os
import socket
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_p21j_")
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
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:400]}]"))
    if not c:
        FAIL.append(n)


ASKS = []
E.messagebox.askyesno = lambda t, m, **k: (ASKS.append(m), True)[1]
E.messagebox.showinfo = lambda t, m, **k: ASKS.append("INFO " + m)
E.messagebox.showwarning = lambda t, m, **k: ASKS.append("WARN " + m)
SRV = {"reco_1r": {"projectx": {"eval": {"150000": 1750}, "funded": {"150000": 500}},
                   "nt8": {"eval": {"150000": 2200}, "funded": {"150000": 450}}}}
E._DAYCAP_SRV = E._prop_reco_validate(SRV)


class T(E.App):
    """App 메서드 그대로, 화면과 저장만 기록으로."""
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


def acfg(nq=(), gc=()):
    return {"NQ": {"broker": "projectx", "include": True, "creds": {}, "accounts": list(nq)},
            "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": list(gc)},
            "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}


def reload_rows(cfg):
    """실제 저장(_save_full) -> 실제 로드(_load) 왕복 뒤 그 설정."""
    E._save_full("ko", "", cfg)
    return E._load()["assets"]


TS150, TS50, XFA = "150KTC-V2-111-222", "50KTC-V2-333-444", "EXPRESS-V2-555-666"
LU150, LU25 = "LDI150A7777", "LDI025A8888"

# ===== P0-1: 저장 -> 재기동 왕복 =====
print("\n== P0-1 mode, type_manual 왕복 ==")
c = acfg(nq=[row(TS150, {"on": True, "type": "test", "mode": "reco", "set": True, "r_test": 2000.0, "r_steady": 500.0}),
             row(XFA, {"on": True, "type": "test", "type_manual": True, "mode": "own", "set": True, "r_test": 1500.0, "r_steady": 450.0})])
a1 = reload_rows(c)
p1, p2 = a1["NQ"]["accounts"][0]["prop"], a1["NQ"]["accounts"][1]["prop"]
ck("1a 재기동 뒤 추천 금액 쓰기 그대로(mode=reco)", p1.get("mode") == "reco", p1)
ck("1b 재기동 뒤 회원이 고른 단계 그대로(type_manual=True, 직접 정하기)", p2.get("type_manual") is True and p2.get("mode") == "own", p2)
t = T(a1)
ck("1c 재기동 뒤 추천 계좌 = 서버 값 1,750(저장 사본 2,000이 아님)", E.App._prop_r_info(t, p1, "projectx", TS150) == ("test", 1750.0, True),
   E.App._prop_r_info(t, p1, "projectx", TS150))
ck("1d 재기동 뒤 EXPRESS 이름이어도 회원이 고른 챌린지 1,500", E.App._prop_r_info(t, p2, "projectx", XFA) == ("test", 1500.0, False),
   E.App._prop_r_info(t, p2, "projectx", XFA))
# [프롭 추천 금액 쓰기]: 추천 값을 저장 칸에도 복사, 재기동 뒤에도 추천
c = acfg(nq=[row(TS150, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 2200.0, "r_steady": 450.0})])
t = T(c)
E.App._prop_all_reco(t)
pr = c["NQ"]["accounts"][0]["prop"]
ck("2a [프롭 추천 금액 쓰기] = 추천 금액 쓰기 + 저장 칸에 추천 값 사본(챌린지 1,750, 펀디드 500)",
   pr.get("mode") == "reco" and pr.get("r_test") == 1750.0 and pr.get("r_steady") == 500.0 and t.saved == 1, pr)
a2 = reload_rows(c)
p = a2["NQ"]["accounts"][0]["prop"]
ck("2b 그 뒤 재기동해도 추천 금액(종전 21i: 옛 2,200으로 돌아감)",
   p.get("mode") == "reco" and E.App._prop_r_info(T(a2), p, "projectx", TS150)[1:] == (1750.0, True), p)
# 서버 값이 바뀌면(재기동 없이) 추천 계좌만 따라간다
own = row(TS150 + "B", {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 2200.0, "r_steady": 450.0})
t = T(acfg(nq=[a2["NQ"]["accounts"][0], own]))
E.App._prop_reco_apply(t, {"reco_1r": {"projectx": {"eval": {"150000": 1600}, "funded": {"150000": 500}}}})
ck("2c 서버 추천 1,750 -> 1,600: 추천 계좌는 재기동 없이 1,600, 직접 정하기는 2,200 그대로",
   E.App._prop_r_info(t, p, "projectx", TS150)[1] == 1600.0 and E.App._prop_r_info(t, own["prop"], "projectx", own["id"])[1] == 2200.0)
ck("2d 서버 값 로그가 사실대로('직접 정한 1R은 바뀌지 않습니다', '추천 금액 쓰기'로 둔 계좌는 새 값)",
   any("직접 정한 1R은 바뀌지 않습니다" in m and "'추천 금액 쓰기'로 둔 프롭 계좌는 다음 진입부터 새 값을 씁니다" in m for m in t.logs)
   and not any("회원이 저장한 1R은 바뀌지 않습니다" in m for m in t.logs), t.logs)
E._DAYCAP_SRV = E._prop_reco_validate(SRV)

# ===== P0-2: 통과 표시 =====
print("\n== P0-2 통과 표시 ==")
lu = row(LU150, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 2200.0, "r_steady": 450.0,
                 "stage": "eval", "stage_for": LU150.casefold()}, bk="nt8")
t = T(acfg(nq=[lu]))
ck("3a 통과 전 Lucid 평가 = 챌린지 2,200", E.App._prop_r_info(t, lu["prop"], "nt8", LU150) == ("test", 2200.0, False))
t._dc_state()["passed"][E.App._daycap_key("nt8", LU150)] = {"day": "x", "bal": 159500.0, "pass_tp": 159000.0, "size": 150000,
                                                            "ts": time.time(), "via": "watch", "target": None, "src": "set"}
# 2026-11-10 리뷰 P0: Lucid도 통과하면 새 펀디드 계좌를 준다 - 통과한 평가 줄은 단계 없음(펀디드로 보지 않는다, 진입은 _daycap_plan이 막는다)
ck("3b 통과로 표시된 Lucid = 단계 없음(펀디드로 보지 않는다)", E.App._prop_auto_type(t, "nt8", LU150, lu["prop"]) is None)
E.App._apply_reco_account(t, "nt8", LU150, 450.0, "funded")
ck("3c [바꾸기](펀디드 권장 450) = 펀디드 450으로 주문, 회원이 고른 단계로 남음(21i: 평가 2,200)",
   E.App._prop_r_info(t, lu["prop"], "nt8", LU150) == ("funded", 450.0, False) and lu["prop"].get("type_manual") is True, lu["prop"])
ts = row(TS150, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 2200.0, "r_steady": 450.0})
t2 = T(acfg(nq=[ts]))
t2._dc_state()["passed"][E.App._daycap_key("projectx", TS150)] = {"day": "x", "bal": None, "pass_tp": 159000.0, "size": 150000,
                                                                  "ts": time.time(), "via": "watch", "target": None, "src": "set"}
ck("3d 통과로 표시된 Topstep 챌린지 = 판별하지 않음(저장된 유형)", E.App._prop_auto_type(t2, "projectx", TS150, ts["prop"]) is None)

# ===== P1-1: 계좌 규모별 추천 금액 =====
print("\n== P1-1 규모별 추천 ==")
t = T(acfg())
R = lambda bk, aid, ty, pr=None: E.App._prop_reco_r(t, bk, aid, pr or {}, ty)
ck("4a Topstep 150K 챌린지 = 1,750, 펀디드 = 500", (R("projectx", TS150, "test"), R("projectx", TS150, "funded")) == (1750.0, 500.0))
ck("4b Topstep 50K 챌린지 = 추천 없음(최대 손실 $2,000 계좌에 150K 값을 권하지 않는다)", R("projectx", TS50, "test") is None)
ck("4c Topstep EXPRESS(이름에 규모 없음) 펀디드 = 150K로 보고 500", R("projectx", XFA, "funded") == 500.0)
ck("4d Lucid 150K = 2,200 / 450, Lucid 25K = 추천 없음",
   (R("nt8", LU150, "test"), R("nt8", LU150, "funded"), R("nt8", LU25, "test")) == (2200.0, 450.0, None))
ck("4e 이름을 모르는 계좌, 프롭 회사가 아닌 브로커 = 추천 없음", R("projectx", "ABC-1", "test") is None and R("ibkr", TS150, "test") is None)
p50 = {"on": True, "type": "test", "mode": "reco", "set": True, "r_test": 900.0, "r_steady": 300.0}
# 2026-10-27 검증 P2: 표식 없는 '추천 금액 쓰기' 줄의 저장 값은 회원이 친 금액인지 추천 금액 사본(21i의 150K $1,750)인지 모른다 - 그 규모에 추천
# 금액이 없으면 저장 값과 그 규모 권장 1R 위 끝(150K 1,750 x 50/150 +30% = 758.33) 가운데 작은 쪽(종전 900 그대로). 직접 정하기 줄은 그대로(4f2).
ck("4f 50K 계좌가 추천 금액 쓰기 = 저장 값 900과 50K 위 끝 758.33 가운데 작은 쪽(추천 없음)",
   E.App._prop_r_info(t, p50, "projectx", TS50) == ("test", 758.33, False), E.App._prop_r_info(t, p50, "projectx", TS50))
ck("4f2 같은 금액을 직접 정하기로 저장한 줄 = 900 그대로", E.App._prop_r_info(t, dict(p50, mode="own"), "projectx", TS50) == ("test", 900.0, False))
E._DAYCAP_SRV = E._prop_reco_validate({"reco_1r": dict(SRV["reco_1r"], projectx={"eval": {"150000": 1750, "50000": 600},
                                                                                 "funded": {"150000": 500}})})
ck("4g 서버 표에 50K 칸이 생기면 그 값(600)", E.App._prop_r_info(t, p50, "projectx", TS50) == ("test", 600.0, True))
E._DAYCAP_SRV = E._prop_reco_validate(SRV)
ASKS.clear()
c = acfg(nq=[row(TS50, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 900.0, "r_steady": 300.0}),
             row(TS150, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 2200.0, "r_steady": 450.0})])
t = T(c)
E.App._prop_all_reco(t)
# 21m(대표 2026-10-27 "눌러놓으면 계속 자동 갱신하게"): 추천 금액이 없는 규모의 계좌도 켠다 - 지금은 저장 금액(그 규모 위 끝 이하), 추천이 정해지면 따라감
ck("4h [프롭 추천 금액 쓰기]: 50K 계좌도 켜짐(전후 줄에 '추천 금액이 정해지면 따라감', 900 → 50K 위 끝 758), 150K도 바뀜",
   ASKS and "추천 금액이 정해지면 따라감" in ASKS[-1] and "$900 → $758" in ASKS[-1]
   and c["NQ"]["accounts"][0]["prop"]["mode"] == "reco" and c["NQ"]["accounts"][0]["prop"]["reco_keys"] == ["r_test", "r_steady"]
   and c["NQ"]["accounts"][0]["prop"]["r_test"] == 900.0
   and c["NQ"]["accounts"][1]["prop"]["mode"] == "reco", (ASKS[-1:], c["NQ"]["accounts"][0]["prop"]))

# ===== P1-2: 21d 설정을 읽을 때 =====
print("\n== P1-2 21d 설정 ==")
E._PROP_LEGACY_NOTES.clear()
E._PROP_TYPE_CHECK.clear()
d21 = acfg(nq=[row(LU150, {"on": True, "type": "live", "set": True, "r_test": 2200.0, "r_steady": 450.0, "r_live": 100.0}, bk="nt8",
                   label="LIVE"),
               row(TS150, {"on": True, "type": "test", "r_test": 1500.0, "r_buffer": 450.0, "r_steady": 450.0, "buffer": 3000.0},
                   label="OLD"),                                                     # 10/6 표식(set) 전 설정
               row(TS150 + "C", {"on": True, "type": "funded", "set": True, "r_test": 2200.0, "r_steady": 450.0}, label="MIS"),
               row("", None, label="NEW")])
a3 = reload_rows(d21)
rl, ro, rm, rn = a3["NQ"]["accounts"]
t = T(a3)
ck("5a 옛 '라이브 초기' = 펀디드 + 그 1R 100 + 직접 정하기 + 회원이 고른 단계(21i: 450)",
   rl["prop"]["type"] == "funded" and rl["prop"]["r_steady"] == 100.0 and rl["prop"]["mode"] == "own" and rl["prop"]["type_manual"] is True
   and E.App._prop_r_info(t, rl["prop"], "nt8", LU150)[1] == 100.0, rl["prop"])
ck("5b 표식(set) 없는 켜진 프롭 = 직접 정하기, 직접 넣은 1,500 그대로(21i: 서버 1,750)",
   ro["prop"]["mode"] == "own" and E.App._prop_r_info(t, ro["prop"], "projectx", ro["id"]) == ("test", 1500.0, False), ro["prop"])
ck("5c set=True, 방식 없음 = 직접 정하기", rm["prop"]["mode"] == "own")
ck("5d 새 계좌(set=False) = 방식 없음(설정 창에서 고른다)", rn["prop"].get("mode") is None and rn["prop"].get("set") is False, rn["prop"])
E.App._log_prop_stage_changes(t)
_l1 = list(t.logs)
ck("5e 기동 로그: 라이브 초기를 펀디드로 옮김(1R 그대로 $100) 한 줄",
   sum(1 for m in _l1 if "없어진 '라이브 초기' 유형을 펀디드로 옮겼습니다" in m and "$100" in m) == 1, _l1)
ck("5f 기동 로그(21k): 저장 유형 펀디드인데 앱이 알아낸 단계가 챌린지인 Topstep 계좌 = 1R이 커지므로($450 -> $2,200) 저장된 단계로 고정, 한 줄",
   sum(1 for m in _l1 if "…222C" in m and "저장된 단계는 '펀디드'" in m and "알아낸 단계는 '챌린지'" in m
       and "$450에서 $2,200로 커지므로 저장된 단계로 계속 주문합니다" in m) == 1
   and rm["prop"]["type_manual"] is True and E.App._prop_r_info(t, rm["prop"], "projectx", rm["id"]) == ("funded", 450.0, False),
   (_l1, rm["prop"]))
ck("5g 단계가 같은 계좌(OLD)는 로그 없음", not any("…1-222" in m or ("…-222" in m and "OLD" in m) for m in _l1) and len(_l1) == 2, _l1)
E.App._log_prop_stage_changes(t)
ck("5h 같은 실행에서 다시 불러도 로그 늘지 않음", len(t.logs) == len(_l1), t.logs[len(_l1):])
a4 = reload_rows(a3)
E._PROP_TYPE_CHECK.clear()
_r4 = E._load()["assets"]
ck("5i 한 번 저장한 뒤 다시 읽으면 21d 설정이 아니다(단계 확인 대상 아님, 라이브 초기 이행 없음)",
   not E._PROP_TYPE_CHECK and _r4["NQ"]["accounts"][0]["prop"]["r_steady"] == 100.0, (E._PROP_TYPE_CHECK, _r4["NQ"]["accounts"][0]["prop"]))

# ===== P2: 화면 =====
print("\n== P2 화면 ==")
# 21m(대표 2026-10-27 "계정 수정 버튼도 자산별로 하지 말구"): 버튼은 자산 탭 위 한 줄에 한 번(금, 나스닥 프롭 계좌를 한꺼번에)
ck("6a [프롭 추천 금액 쓰기] 버튼 = 왼쪽 메뉴 아래 한 곳(_dock_reco_row, 2026-11-02), 자산 탭 안에는 없음",
   "command=self._prop_all_reco" in inspect.getsource(E.App._dock_reco_row)
   and "command=self._prop_all_reco" not in inspect.getsource(E.App._build))
try:
    import tkinter as tk
    from tkinter import ttk
    ROOT = tk.Tk()
    ROOT.withdraw()
except Exception as _e:                                    # 화면이 없는 기기 - 설정 창 시험은 건너뛴다(실패로 셈)
    ROOT = None
    ck("6b Tk 사용 가능", False, _e)
if ROOT is not None:
    def _walk(w):
        out = [w]
        for ch in w.winfo_children():
            out.extend(_walk(ch))
        return out

    def _tops():
        return [w for w in ROOT.winfo_children() if isinstance(w, tk.Toplevel)]

    def _dialog(r):
        t = T(acfg(nq=[r]))
        t.root = ROOT
        E.App._prop_dialog(t, 0)
        ROOT.update()
        w = _tops()[-1]
        rbs = [x for x in _walk(w) if isinstance(x, ttk.Radiobutton)]
        txt = [str(x.cget("text")) for x in _walk(w) if isinstance(x, (ttk.Label, ttk.Radiobutton))]
        stage = ROOT.getvar(str(next(x for x in rbs if str(x.cget("text")) == "챌린지(평가)").cget("variable")))
        return w, stage, txt

    w, stg, txt = _dialog(row(XFA, {"on": True, "type": "test", "type_manual": True, "mode": "own", "set": True,
                                     "r_test": 1500.0, "r_steady": 450.0}))
    ck("6c 설정 창: 회원이 고른 챌린지를 그대로 미리 고름(앱 판별 펀디드로 돌아가지 않음) + '직접 고르신 단계' 안내",
       stg == "test" and any("직접 고르신 단계입니다(앱이 알아낸 단계: 펀디드)" in x for x in txt), (stg, txt))
    w.destroy()
    w, stg, txt = _dialog(row(XFA, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 1500.0, "r_steady": 450.0}))
    ck("6d 설정 창: 회원이 고르지 않았으면 앱이 알아낸 펀디드를 미리 고름", stg == "funded" and any("앱이 계좌로 알아낸 단계로" in x for x in txt),
       (stg, txt))
    w.destroy()
    w, stg, txt = _dialog(row(TS150, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 1500.0, "r_steady": 450.0}))
    ck("6e 설정 창(Topstep 150K): '추천 금액 쓰기 (챌린지 $1,750, 펀디드 $500)'", "추천 금액 쓰기 (챌린지 $1,750, 펀디드 $500)" in txt, txt)
    w.destroy()
    w, stg, txt = _dialog(row(TS50, {"on": True, "type": "test", "mode": "own", "set": True, "r_test": 900.0, "r_steady": 300.0}))
    ck("6f 설정 창(Topstep 50K): 추천 금액 고르기 없음 + '이 계좌 규모에는 추천 금액이 없습니다' 안내",
       not any(x.startswith("추천 금액 쓰기") for x in txt) and any("이 계좌 규모에는 추천 금액이 없습니다" in x for x in txt), txt)
    w.destroy()
    # 새 EXPRESS 계좌: 프롭을 켜고 추천 금액 그대로 저장 = 펀디드 500(이름에 규모가 없어 챌린지 추천은 없음 - 쓰지 않을 챌린지 칸을 채우라고 막지 않는다)
    rx = row(XFA, None)
    rx["prop"] = E._new_acct(500, XFA, True, "x", None, None, False, "projectx", topstep=True)["prop"]
    tx = T(acfg(nq=[rx]))
    tx.root = ROOT
    E.App._prop_dialog(tx, 0)
    ROOT.update()
    w = _tops()[-1]
    next(x for x in _walk(w) if isinstance(x, ttk.Checkbutton)).invoke()
    ASKS.clear()
    next(x for x in _walk(w) if isinstance(x, ttk.Button) and str(x.cget("text")) == "저장").invoke()
    ROOT.update()
    _px = tx._acfg["NQ"]["accounts"][0]["prop"]
    ck("6g 새 EXPRESS 계좌 저장: 펀디드 + 추천 금액 쓰기 + 저장 사본 500, 경고 없음, 주문 1R 500",
       _px.get("on") is True and _px.get("type") == "funded" and _px.get("mode") == "reco" and _px.get("r_steady") == 500.0
       and not any(m.startswith("WARN") for m in ASKS) and E.App._prop_r_info(tx, _px, "projectx", XFA) == ("funded", 500.0, True),
       (_px, ASKS))
    try:
        if w.winfo_exists():
            w.destroy()
    except Exception:
        pass
    try:
        ROOT.destroy()
    except Exception:
        pass

print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
