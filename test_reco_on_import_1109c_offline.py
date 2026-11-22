# EQ Autopilot 2026.11.09c 오프라인 시험(2026-11-09 대표 "프롭 추천 금액 쓰기만 눌러선 안 되던데", "이번 빌드에 넣어",
# "계좌 설정 복사 버튼 넣자. 대신 금 쪽에만", "현재 금 설정을 나스닥 설정으로 대체하는 거", "복사가 아니라 통째로 가져오기"). 0 = 통과.
#   A. [프롭 추천 금액 쓰기]가 이름으로 프롭 단계가 확실하고 프롭을 저장한 적 없는 계좌는 프롭까지 켠다(추천 금액이 있을 때만).
#   B. 금 탭 [NQ 설정 가져오기]: 금 계좌 줄을 나스닥 계좌 줄로 통째로(키, 가용 계좌, 자산 기본 브로커는 그대로).
import copy
import inspect
import os
import socket
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_1109c_")
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
E._DAYCAP_SRV = {}
FAIL = []


def ck(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:700]}]"))
    if not c:
        FAIL.append(n)


ASKS = []
ANSWER = {"v": True}
E.messagebox.askyesno = lambda t, m, **k: (ASKS.append(m), ANSWER["v"])[1]
E.messagebox.showinfo = lambda t, m, **k: ASKS.append("INFO " + m)
E.messagebox.showwarning = lambda t, m, **k: ASKS.append("WARN " + m)


class Dot:
    def winfo_exists(self):
        return True

    def config(self, **k):
        pass


class Root:
    def after(self, ms, fn):
        pass


class T(E.App):
    def __init__(self, acfg, asset="NQ", lang="ko"):
        self._acfg, self._asset, self.lang, self.logs, self.saved, self.built = acfg, asset, lang, [], 0, 0
        self._acct_widgets = {}
        self.root = Root()
        self._sig_accts, self._auto_accts, self._sig_parked = {}, {}, {}
        self.busy = False

    def log(self, m):
        self.logs.append(str(m))

    def _save_acct_widgets(self):
        self.saved += 1

    def _save_cfg(self, **k):
        self.saved += 1

    def _collect_acct_widgets(self):
        pass

    def _build(self):
        self.built += 1

    def _golive_refuse(self, warn=True):
        return self.busy


TS150, XFA, TS50, PRAC = "150KTC-V2-111-222", "EXPRESS-V2-555-666", "50KTC-V2-9-9", "PRAC-V2-1-1"
LDI, LDF = "LDI15012345670001", "LDF15012345670001"


def row(aid, prop=None, bk="projectx", one_r=500.0, label=None):
    return {"id": aid, "one_r": one_r, "on": True, "label": label or aid[-4:], "broker": bk,
            "prop": ({"set": False} if prop is None else prop), "pct": {}, "one_lim": 0.0}


def acfg(nq=(), gc=(), nq_bk="projectx", gc_bk="projectx"):
    return {"NQ": {"broker": nq_bk, "include": True, "creds": {"projectx": {"f1": "nq@x"}}, "accounts": list(nq)},
            "GC": {"broker": gc_bk, "include": True, "creds": {"projectx": {"f1": "gc@x"}, "nt8": {"f1": "tok"}},
                   "accounts": list(gc)},
            "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}


ck("0 앱 버전 2026.11.09c 이상", E.App._APP_VER >= "2026.11.09c", E.App._APP_VER)

# ── A. 프롭 자동 켜기 ──
_r150 = E._reco_one_r("projectx", "eval", 150000)
_rf = E._reco_one_r("projectx", "funded", 150000)
ck("A0 내장 추천 금액(150K 평가, 펀디드)이 있다", bool(_r150) and bool(_rf), (_r150, _rf))
a1, a2 = row(TS150, one_r=700.0), row(XFA, one_r=650.0)
t = T(acfg(nq=[a1], gc=[a2]))
ck("A1 자동 켜기 단계: 150KTC = 챌린지, EXPRESS = 펀디드",
   t._prop_auto_on_type("projectx", TS150, a1) == "test" and t._prop_auto_on_type("projectx", XFA, a2) == "funded",
   (t._prop_auto_on_type("projectx", TS150, a1), t._prop_auto_on_type("projectx", XFA, a2)))
ASKS.clear()
t._prop_all_reco()
p1, p2 = a1["prop"], a2["prop"]
ck("A2 두 줄 모두 프롭 켜짐 + 추천 금액 쓰기", p1.get("on") and p2.get("on") and p1.get("mode") == "reco" and p2.get("mode") == "reco",
   (p1, p2))
ck("A3 단계: NQ 챌린지, GC 펀디드, 회원 선택 아님(type_manual False)",
   p1.get("type") == "test" and p2.get("type") == "funded" and not p1.get("type_manual") and not p2.get("type_manual"), (p1, p2))
ck("A4 주문 1R = 추천 금액(챌린지 150K, 펀디드)",
   E.App._prop_r_info(t, p1, "projectx", TS150)[1:] == (_r150, True) and E.App._prop_r_info(t, p2, "projectx", XFA)[1:] == (_rf, True),
   (E.App._prop_r_info(t, p1, "projectx", TS150), E.App._prop_r_info(t, p2, "projectx", XFA)))
ck("A5 수동 1R은 그대로 남는다(프롭을 다시 끄면 이 금액)", a1["one_r"] == 700.0 and a2["one_r"] == 650.0, (a1["one_r"], a2["one_r"]))
_q = ASKS[-1] if ASKS else ""
ck("A6 확인 창: '프롭 켬' 줄, 전 금액 = 수동 1R, 이유 문장",
   "프롭 켬, 1R $700 → 챌린지" in _q and "프롭 켬, 1R $650 → 펀디드" in _q and "계좌 이름으로 Topstep, Lucid 계좌인 것이 확실해" in _q, _q)
ck("A7 켠 줄이 있으면 화면을 다시 그린다", t.built == 1, t.built)
ck("A8 로그에 바뀐 줄", any("프롭 켬" in x for x in t.logs), t.logs[-3:])

# 직접 끈 줄(저장한 적 있음), 연습, 추천 금액 없는 규모, 프롭 회사가 아닌 브로커
off_set = {"on": False, "set": True, "type": "test", "mode": "own", "r_test": 1000.0, "r_steady": 300.0}
b1, b2, b3, b4 = row(TS150, prop=dict(off_set)), row(PRAC), row(TS50), row("U1234567", bk="ibkr")
t = T(acfg(nq=[b1, b2, b3, b4]))
ck("A9 직접 끈 줄, 연습, 50K(추천 금액 없음), IBKR은 자동 켜기 대상이 아님",
   [t._prop_auto_on_type(r["broker"], r["id"], r) for r in (b1, b2, b4)] == [None, None, None],
   [t._prop_auto_on_type(r["broker"], r["id"], r) for r in (b1, b2, b4)])
ASKS.clear()
t._prop_all_reco()
_m = ASKS[-1] if ASKS else ""
ck("A10 바꿀 줄이 없으면 안내: 켜는 법 + 그대로인 프롭 회사 계좌 목록(IBKR 줄은 목록에 없음)",
   _m.startswith("INFO ") and "[프롭: 끔]을 눌러 '프롭 계좌로 사용'에 체크" in _m and "프롭이 꺼져 있어 그대로인 계좌" in _m
   and f"[NQ, {b1['label']}]" in _m and f"[NQ, {b2['label']}]" in _m and f"[NQ, {b3['label']}]" in _m and "U1234567"[-4:] not in _m, _m)
ck("A11 아무 줄도 바뀌지 않음", not b1["prop"].get("on") and not (b2["prop"] or {}).get("on") and not (b3["prop"] or {}).get("on")
   and t.built == 0, (b1["prop"], b2["prop"], b3["prop"]))

# 켜진 줄과 꺼진 줄이 섞이면 확인 창 끝에 목록
own_on = {"on": True, "set": True, "type": "test", "mode": "own", "type_manual": True, "r_test": 1000.0, "r_steady": 300.0, "r_buffer": 300.0}
c1, c2 = row(TS150, prop=dict(own_on)), row(PRAC)
t = T(acfg(gc=[c1, c2]))
ASKS.clear()
t._prop_all_reco()
_q = ASKS[-1] if ASKS else ""
ck("A12 확인 창 끝에 '프롭이 꺼져 있어 그대로인 계좌'와 [프롭: 끔] 안내, '프롭 켬' 이유 문장은 없음",
   "프롭이 꺼져 있어 그대로인 계좌" in _q and f"[GC, {c2['label']}]" in _q and "이번에 프롭도 켭니다" not in _q, _q)
ck("A13 켜져 있던 줄은 추천 금액으로, 연습 줄은 그대로", c1["prop"].get("mode") == "reco" and not (c2["prop"] or {}).get("on"),
   (c1["prop"], c2["prop"]))
ck("A14 켠 줄이 없으면 다시 그리지 않음(종전 그대로)", t.built == 0, t.built)

# '아니오' = 아무것도 안 바뀜
d1 = row(TS150)
t = T(acfg(nq=[d1]))
ANSWER["v"] = False
t._prop_all_reco()
ANSWER["v"] = True
ck("A15 확인 창 '아니오' = 프롭 그대로 꺼짐", not (d1["prop"] or {}).get("on") and t.built == 0, d1["prop"])

# Lucid 이름(LDI 평가, LDF 펀디드) - NT8
e1, e2 = row(LDI, bk="nt8"), row(LDF, bk="nt8")
t = T(acfg(gc=[e1, e2], gc_bk="nt8"))
ck("A16 Lucid LDI = 챌린지, LDF = 펀디드",
   t._prop_auto_on_type("nt8", LDI, e1) == "test" and t._prop_auto_on_type("nt8", LDF, e2) == "funded",
   (t._prop_auto_on_type("nt8", LDI, e1), t._prop_auto_on_type("nt8", LDF, e2)))
_lr = (E._reco_one_r("nt8", "eval", 150000), E._reco_one_r("nt8", "funded", 150000))
t._prop_all_reco()
ck("A17 Lucid 두 줄 프롭 켜짐 + 추천 금액(150K)",
   e1["prop"].get("on") and e2["prop"].get("on")
   and E.App._prop_r_info(t, e1["prop"], "nt8", LDI)[1] == _lr[0] and E.App._prop_r_info(t, e2["prop"], "nt8", LDF)[1] == _lr[1],
   (e1["prop"], e2["prop"], _lr))
ck("A18 프롭 켠 뒤 버튼 글자 = 단계", t._prop_btn_text(e2["prop"], e2) == "프롭: 펀디드" and t._prop_btn_text(e1["prop"], e1) == "프롭: 챌린지",
   (t._prop_btn_text(e2["prop"], e2), t._prop_btn_text(e1["prop"], e1)))

# ── B. [NQ 설정 가져오기] ──
src_s = inspect.getsource(E.App._build)
ck("B0 버튼은 금 탭에만", 'if self._asset == "GC":' in src_s and "command=self._import_nq_setup" in src_s
   and src_s.index('if self._asset == "GC":') < src_s.index("command=self._import_nq_setup"))
imp_s = inspect.getsource(E.App._import_nq_setup)
_body = imp_s.split('"""', 2)[2]
ck("B1 키, 가용 계좌, 자산 기본 브로커를 건드리지 않는 코드(본문에 creds, avail, [dst][\"broker\"] 없음)",
   "creds" not in _body and "avail" not in _body and '["broker"] =' not in _body.replace('a2["broker"] =', ""), _body[:300])

reco_on = {"on": True, "set": True, "type": "funded", "mode": "reco", "type_manual": False, "reco_keys": ["r_test", "r_steady"],
           "reco_acct": XFA.casefold(), "r_test": 2200.0, "r_steady": 450.0, "r_buffer": 450.0}
n1 = row(XFA, prop=dict(reco_on), label="XFA-1")
n2 = row(LDI, bk="", one_r=300.0, label="Lucid-1")              # 빈 브로커 칸 = NQ 기본 브로커(nt8)로 읽힌다
n3 = row("", label="blank")                                       # 빈 줄 - 가져오지 않는다
g1 = row(TS150, label="old-gc", one_r=900.0)
cfg = acfg(nq=[n1, n2, n3], gc=[g1], nq_bk="nt8", gc_bk="projectx")
cr0 = copy.deepcopy(cfg["GC"]["creds"])
t = T(cfg, asset="GC")
ASKS.clear()
t._import_nq_setup()
gc = cfg["GC"]["accounts"]
_q = next((m for m in ASKS if not m.startswith("INFO")), "")
ck("B2 확인 창: 지금 금 줄과 가져올 나스닥 줄, 연결과 키는 옮기지 않는다는 문장(11h 최종 리뷰 C P2-8 - 나뉜 로그인이면 '같이 쓴다'가 틀림)",
   "지금 금:" in _q and "old-gc" in _q and "가져올 나스닥:" in _q and "XFA-1" in _q and "Lucid-1" in _q
   and "브로커 연결과 키는 옮기지 않습니다(금 탭의 연결을 그대로 씁니다)" in _q and "blank" not in _q, _q)
ck("B3 금 줄 = 나스닥 줄(빈 줄 제외) 통째로", [r["label"] for r in gc] == ["XFA-1", "Lucid-1"]
   and gc[0]["prop"] == n1["prop"] and gc[1]["one_r"] == 300.0, gc)
ck("B4 빈 브로커 칸은 나스닥 기준 실효 브로커(nt8)로 박힘", gc[1].get("broker") == "nt8" and gc[0].get("broker") == "projectx",
   [r.get("broker") for r in gc])
ck("B5 키, 가용 계좌, 금 기본 브로커 그대로", cfg["GC"]["creds"] == cr0 and cfg["GC"]["broker"] == "projectx", (cfg["GC"]["creds"], cfg["GC"]["broker"]))
gc[0]["prop"]["r_steady"] = 1.0
gc[1]["label"] = "changed"
ck("B6 깊은 복사: 금 줄을 고쳐도 나스닥 줄은 그대로", n1["prop"]["r_steady"] == 450.0 and n2["label"] == "Lucid-1", (n1["prop"], n2))
ck("B7 저장, 다시 그리기, 로그", t.saved >= 1 and t.built == 1 and any("NQ 설정 가져오기" in x for x in t.logs), (t.saved, t.built, t.logs[-2:]))
ck("B8 나스닥 프롭 줄은 금에서도 같은 추천 금액",
   E.App._prop_r_info(t, n1["prop"], "projectx", XFA)[1] == E.App._prop_r_info(t, dict(n1["prop"]), "projectx", XFA)[1])

# 금 무장 중 = 거절
cfg = acfg(nq=[row(TS150, label="nq1")], gc=[row(XFA, label="gc1")])
t = T(cfg, asset="GC")
t._sig_accts = {("GC", 0): {"id": XFA}}
ASKS.clear()
t._import_nq_setup()
ck("B9 금이 무장 중이면 안 바꾸고 안내", [r["label"] for r in cfg["GC"]["accounts"]] == ["gc1"] and ASKS and "무장 중" in ASKS[-1], ASKS[-1:])
t._sig_accts = {("NQ", 0): {"id": TS150}}
t._import_nq_setup()
ck("B10 나스닥만 무장 중이면 가져온다(금 무장 아님)", [r["label"] for r in cfg["GC"]["accounts"]] == ["nq1"], cfg["GC"]["accounts"])

# 연결 테스트 중 = 거절, '아니오' = 그대로, 나스닥 비면 안내
cfg = acfg(nq=[row(TS150, label="nq1")], gc=[row(XFA, label="gc1")])
t = T(cfg, asset="GC")
t.busy = True
t._import_nq_setup()
ck("B11 연결 테스트 중이면 그대로", [r["label"] for r in cfg["GC"]["accounts"]] == ["gc1"])
t.busy = False
ANSWER["v"] = False
t._import_nq_setup()
ANSWER["v"] = True
ck("B12 확인 창 '아니오' = 그대로", [r["label"] for r in cfg["GC"]["accounts"]] == ["gc1"] and t.built == 0)
cfg = acfg(nq=[row("", label="blank")], gc=[row(XFA, label="gc1")])
t = T(cfg, asset="GC")
ASKS.clear()
t._import_nq_setup()
ck("B13 나스닥에 계좌가 없으면 안내만", [r["label"] for r in cfg["GC"]["accounts"]] == ["gc1"] and ASKS and "나스닥에 가져올 계좌가 없습니다" in ASKS[-1],
   ASKS[-1:])

# 영어
cfg = acfg(nq=[row(TS150, label="nq1")], gc=[])
t = T(cfg, asset="GC", lang="en")
ASKS.clear()
t._import_nq_setup()
_q = next((m for m in ASKS if not m.startswith("INFO")), "")
ck("B14 영어 확인 창", "Gold's account rows will be replaced with Nasdaq's" in _q and "Gold now:" in _q and "(none)" in _q
   and [r["label"] for r in cfg["GC"]["accounts"]] == ["nq1"], _q)

# 10/7 키 복사 버튼 부재 래칫과 겹치지 않는다
src_all = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
ck("B15 옛 키 복사 함수, 문구 없음(_copy_nq_settings, _pull_broker_keys, _copy_acct_setup)",
   all(not hasattr(E.App, f) for f in ("_copy_nq_settings", "_pull_broker_keys", "_copy_acct_setup"))
   and '"NQ 설정 복사하기"' not in src_all and '"계좌 설정 복사 ←"' not in src_all)

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}: {FAIL}"))
sys.exit(1 if FAIL else 0)
