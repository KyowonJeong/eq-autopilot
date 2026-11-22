#!/usr/bin/env python3
# 프롭 단순화(대표 2026-10-21): 단계(챌린지/펀디드, 앱이 알아낸 값 기본 + 회원이 바꾸면 회원 값) x 1R(추천 금액 = 서버 값 따라감 / 직접 정하기).
# 출금 횟수, 출금 알림, 5발 중단, 주문 전 잔고 조회 없음. 프롭 행 [1R 계산기] 숨김. 0 = 통과.
import os, socket, sys, tempfile, time, inspect
HOME = tempfile.mkdtemp(prefix="eq_ptidy_"); os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"; os.environ["EQ_ERR_REPORT"] = "0"
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("blocked"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
FAIL = []
def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:300]}")); (None if c else FAIL.append(n))
class St:
    lang = "ko"
    def __init__(self, auto): self.logs, self._auto = [], auto
    def log(self, m): self.logs.append(str(m))
    def _prop_auto_type(self, *a): return self._auto
St._prop_r_info = eqgui.App.__dict__["_prop_r_info"]
eqgui.App._prop_auto_type = lambda self, *a: getattr(self, "_auto", None)   # 앱 판별 대신 시험 값
calls = []
eqgui._build_broker = lambda *a, **k: (calls.append(1), (_ for _ in ()).throw(RuntimeError("x")))[1]
P = {"on": True, "type": "test", "r_test": 1800, "r_steady": 450, "payouts": 0, "mode": "own"}
cfg = {"broker": "projectx", "acct": "150KTC-V2-111-222", "f1": "", "f2": "", "f3": ""}   # 21j: 추천 금액은 계좌 규모별 - 150K 이름
one = lambda st, pr: eqgui.App._prop_one_r(st, pr, cfg, "A")
ck("1 직접 정하기 + 단계 모름 = 저장된 챌린지 1R 1800", one(St(None), dict(P)) == 1800)
ck("2 직접 정하기 + 앱이 펀디드로 알아냄 = 펀디드 1R 450", one(St("funded"), dict(P)) == 450)
ck("3 추천 금액 + 챌린지 = 권장표 값 1750(21k 내장 = 서버 값, 저장 사본 1800이 아님)", one(St("test"), dict(P, mode="reco")) == 1750)
ck("4 추천 금액 + 펀디드 = 서버 값 500", one(St("funded"), dict(P, mode="reco")) == 500)
_old = {k: v for k, v in P.items() if k != "mode"}
ck("5a 방식 없는 옛 설정 + 저장한 적 있음 = 직접 정하기(회원 1R 그대로 1800)", one(St("test"), dict(_old, set=True)) == 1800)
ck("5b 방식 없는 옛 설정 + 저장한 적 없음 = 추천 금액 1750", one(St("test"), dict(_old)) == 1750)
ck("6 추천 금액인데 서버 값을 못 읽음 = 저장 사본", eqgui.App._prop_one_r(St("test"), dict(P, mode="reco"), dict(cfg, broker="nt8"), "A") == 1800)
ck("7 회원이 단계를 바꿨으면(type_manual) 회원 선택이 이김", one(St("funded"), dict(P, type="test", type_manual=True)) == 1800)
ck("8 옛 '라이브 초기' = 펀디드로 읽음", one(St(None), dict(P, type="live")) == 450)
st = St("funded"); t0 = time.monotonic(); r = one(st, dict(P, type="funded", payouts=5)); dt = time.monotonic() - t0
ck("9 5발 완료 옛 계좌도 진입(5발 중단 없음)", r == 450, r)
ck("10 주문 전 브로커 접속, 잔고 조회 없음", not calls and dt < 0.2, (calls, dt))
src = open(eqgui.__file__, encoding="utf-8").read()
ck("11 프롭 행은 [1R 계산기]를 붙이지 않음", 'if not pr.get("on"):                     # 프롭 계좌는 단계별 1R' in src)
ck("12 설정 창: 추천/직접 두 가지, 단계 두 가지, 출금 횟수 칸 없음",
   '"추천 금액 쓰기' in src and '("직접 정하기" if ko' in src and '"출금 횟수 (0~5)"' not in src and '"라이브 초기" if ko else "Live (early)"' not in src)
# 13~15: 계좌 줄 1R 칸 = 프롭이 켜졌으면 주문에 쓰는 프롭 1R(대표 2026-10-21 '그 액수로 1R 칸에')
class Row(St):
    _asset = "NQ"
    def _acct_broker(self, a, ac): return "projectx"
rw = Row("funded")
ck("13 프롭 켜짐 + 추천 금액 + 펀디드 = 1R 칸에 500", eqgui.App._row_r_text(rw, {"one_r": 600, "id": "X", "prop": dict(P, mode="reco")}) == "500")
ck("14 프롭 켜짐 + 직접 정하기 + 펀디드 = 1R 칸에 450", eqgui.App._row_r_text(rw, {"one_r": 600, "id": "X", "prop": dict(P)}) == "450")
ck("15 프롭 꺼짐 = 수동 1R 600", eqgui.App._row_r_text(rw, {"one_r": 600, "id": "X", "prop": dict(P, on=False)}) == "600")
# 16~19: [프롭 추천 금액 쓰기]
class AllR(Row):
    def __init__(self):
        super().__init__("funded"); self.saved = 0
        self.rows = {"NQ": [{"id": "PA-1", "label": "A", "one_r": 600, "prop": dict(P, mode="own")},
                            {"id": "PA-2", "label": "B", "one_r": 600, "prop": dict(P, on=False)}],
                     "GC": [{"id": "PA-3", "label": "C", "one_r": 600, "prop": dict(P, mode="reco")}], "BTC": []}
        self._acct_widgets = {}
    def _accts_of(self, a): return self.rows[a]
    def _collect_acct_widgets(self): pass
    def _save_acct_widgets(self): self.saved += 1
_asks = []
eqgui.messagebox.askyesno = lambda t, m: (_asks.append(m), True)[1]
eqgui.messagebox.showinfo = lambda t, m: _asks.append("INFO " + m)
al = AllR(); eqgui.App._prop_all_reco(al)
# 2026-11-09 c: 확인 창 끝에 '프롭이 꺼져 있어 그대로인 계좌' 목록이 붙는다 - 꺼진 B는 바뀌는 줄이 아니라 그 목록에만
_m16 = _asks[-1] if _asks else ""
_h16 = "프롭이 꺼져 있어 그대로인 계좌"
ck("16 확인 창에 바뀌는 계좌만 전후로(A 펀디드 $450 → $500), 꺼진 B는 끝의 '그대로인 계좌' 목록에만, 이미 추천인 C는 없음",
   _m16.startswith("아래 프롭 계좌들") and "[NQ, A] 펀디드 $450 → $500" in _m16 and _h16 in _m16
   and "[NQ, B]" not in _m16.split(_h16)[0] and "[NQ, B]" in _m16.split(_h16)[1] and "[GC, C]" not in _m16, _asks[-1:])
ck("17 '예' = A가 추천 금액으로 바뀌고 저장", al.rows["NQ"][0]["prop"]["mode"] == "reco" and al.saved == 1 and al.rows["NQ"][1]["prop"].get("mode") == "own")
eqgui.messagebox.askyesno = lambda t, m: (_asks.append(m), False)[1]
al2 = AllR(); eqgui.App._prop_all_reco(al2)
ck("18 '아니요' = 아무것도 안 바뀜", al2.rows["NQ"][0]["prop"]["mode"] == "own" and al2.saved == 0)
eqgui.App._prop_all_reco(al)
# 21o(대표 2026-10-27 "켤 수만 있고 끌 수가 없어"): 다 켜진 상태에서 누르면 끄기 확인 - 여기선 '아니요'라 그대로 켜짐
ck("19 이미 모두 추천 금액 = 끄기 확인, '아니요'면 그대로", _asks[-1].startswith("프롭 추천 금액 쓰기를 끕니다")
   and al.rows["NQ"][0]["prop"]["mode"] == "reco" and al.rows["GC"][0]["prop"]["mode"] == "reco", _asks[-1:])
# 20~25: [프롭: …] 버튼은 프롭이 될 수 있는 계좌에만(대표 2026-10-21 'Tradovate 계좌인데 프롭 끔이 다 나타남')
class Cap:
    _asset = "NQ"
    def __init__(self, bk): self.bk = bk
    def _acct_broker(self, a, ac): return self.bk
cap = lambda bk, aid, on=False: eqgui.App._row_is_prop_capable(Cap(bk), {"id": aid, "prop": {"on": on}})
ck("20 Topstep(ProjectX) 계좌 = 보임", cap("projectx", "S1-150K-1"))
ck("21 NinjaTrader의 Lucid 형식 계좌 = 보임", cap("nt8", "LDI150A8888"))
ck("22 NinjaTrader의 개인 Tradovate 계좌 = 안 보임", not cap("nt8", "TV195551"))
ck("23 Sim101 = 안 보임", not cap("nt8", "Sim101"))
ck("24 크립토 = 안 보임", not cap("bybit", ""))
ck("25 이미 프롭을 켠 계좌는 어떤 이름이든 보임(끌 수 있게)", cap("nt8", "TV195551", on=True))
print("FAIL:", FAIL or "none"); sys.exit(1 if FAIL else 0)
