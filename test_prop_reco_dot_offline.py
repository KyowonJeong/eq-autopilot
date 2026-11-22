# EQ Autopilot 2026.10.21m - [프롭 추천 금액 쓰기] 표시등, 계좌 줄 [프롭: …] 표시등 오프라인 시험(2026-10-27, 대표 "연결 테스트 처럼 활성화시
# 초록색 불 비활성화시 회색", "사용계좌에 프롭: 펀디드 옆에도 활성화된 애들만 초록색"). 0 = 통과.
import inspect
import os
import socket
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_pdot_")
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
E._DAYCAP_SRV = {}
GREEN, GRAY = "#21c55e", "#9ca3af"


class Dot:
    def __init__(self, alive=True):
        self.alive, self.fg = alive, None

    def winfo_exists(self):
        return self.alive

    def config(self, **k):
        if "foreground" in k:
            self.fg = k["foreground"]


class Root:
    def __init__(self):
        self.calls = []

    def after(self, ms, fn):
        self.calls.append(ms)


class T(E.App):
    def __init__(self, acfg, asset="NQ", lang="ko"):
        self._acfg, self._asset, self.lang, self.logs, self.saved = acfg, asset, lang, [], 0
        self._acct_widgets = {}
        self.root = Root()

    def log(self, m):
        self.logs.append(str(m))

    def _save_acct_widgets(self):
        self.saved += 1

    def _collect_acct_widgets(self):
        pass


XFA, TS150, TS50 = "EXPRESS-V2-555-666", "150KTC-V2-111-222", "50KTC-V2-9-9"


def row(aid, prop, bk="projectx"):
    return {"id": aid, "one_r": 500.0, "on": True, "label": aid[-4:], "broker": bk, "prop": prop, "pct": {}, "one_lim": 0.0}


def own(ty, **k):
    return dict({"on": True, "type": ty, "mode": "own", "set": True, "type_manual": True, "r_test": 1000.0, "r_steady": 300.0,
                 "r_buffer": 300.0}, **k)


def acfg(nq=(), gc=()):
    return {"NQ": {"broker": "projectx", "include": True, "creds": {}, "accounts": list(nq)},
            "GC": {"broker": "projectx", "include": True, "creds": {}, "accounts": list(gc)},
            "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}


ck("0 앱 버전 2026.10.21z 이상", E.App._APP_VER >= "2026.10.21z", E.App._APP_VER)

# 1 프롭 계좌가 없으면 꺼짐(회색), 읽기만(빈 슬롯을 만들지 않는다)
t = T(acfg())
ck("1a 프롭 계좌 없음 = 회색", t._prop_reco_lit() is False)
ck("1b 판정이 설정을 바꾸지 않음(빈 계좌 목록 그대로)", all(t._acfg[a]["accounts"] == [] for a in ("NQ", "GC", "BTC")), t._acfg)

# 2 직접 정하기 Topstep 펀디드(추천 금액 있음) = 회색, 줄도 회색
r1 = row(XFA, own("funded"))
t = T(acfg(nq=[r1]))
ck("2a 직접 정하기 펀디드 = 회색(버튼이 바꿀 계좌가 있음)", t._prop_reco_lit() is False)
ck("2b 줄 표시등 회색", t._prop_row_lit("NQ", r1) is False)

# 3 [프롭 추천 금액 쓰기]를 누르면 초록, 줄도 초록, 표시등을 바로 칠한다
t._prop_reco_dot = Dot()
t._acct_widgets = {0: {"prop_dot": Dot()}}
t._prop_all_reco()
ck("3a 누른 뒤 추천 금액 쓰기로 저장", (r1["prop"].get("mode") == "reco"), r1["prop"])
ck("3b 위 표시등 초록", t._prop_reco_lit() is True and t._prop_reco_dot.fg == GREEN, t._prop_reco_dot.fg)
ck("3c 줄 표시등 초록", t._acct_widgets[0]["prop_dot"].fg == GREEN, t._acct_widgets[0]["prop_dot"].fg)

# 4 서버 추천 금액이 바뀌면(하트비트) 그 값을 따라가고 초록 그대로
E._DAYCAP_SRV = {"reco_1r": {"projectx": {"funded": {150000: 450.0}}}}
ty, r, rc = E.App._prop_r_info(t, r1["prop"], "projectx", XFA)
ck("4a 서버 펀디드 450 → 주문 1R 450(추천 금액 표식)", (ty, r, rc) == ("funded", 450.0, True), (ty, r, rc))
ck("4b 초록 그대로", t._prop_reco_lit() is True and t._prop_row_lit("NQ", r1) is True)
E._DAYCAP_SRV = {}

# 5 다른 자산에 직접 정하기 계좌(추천 금액 있음)가 있으면 위 표시등은 회색, 그 줄은 회색, 따라가는 줄은 초록
r2 = row(TS150, own("test"))
t = T(acfg(nq=[r1], gc=[r2]))
ck("5a GC 직접 정하기 150K 평가 = 위 표시등 회색(버튼과 같은 범위: 모든 자산)", t._prop_reco_lit() is False)
ck("5b 줄: NQ 따라감 초록, GC 직접 회색", t._prop_row_lit("NQ", r1) is True and t._prop_row_lit("GC", r2) is False)

# 6 21m: 지금 추천 금액이 없는 직접 정하기 계좌도 버튼이 켠다(21l은 건너뛰어 다시 눌러야 했다) - 누르기 전 회색, 누르면 초록, 저장 금액 그대로 쓰다가 추천이 정해지면 따라감
r3 = row(TS50, own("test"))
t = T(acfg(nq=[r1], gc=[r3]))
ck("6a 50K 평가(추천 금액 없음) 직접 정하기가 있으면 = 회색", t._prop_reco_lit() is False)
ck("6b 50K 줄은 회색", t._prop_row_lit("GC", r3) is False)
ASKS.clear()
t._prop_all_reco()
ck("6c 한 번 누르면 50K 줄도 켜짐(두 칸 따라감), 확인 창에 '추천 금액이 정해지면 따라감'",
   r3["prop"].get("mode") == "reco" and r3["prop"].get("reco_keys") == ["r_test", "r_steady"]
   and any("[GC, " in m and "추천 금액이 정해지면 따라감" in m for m in ASKS), (r3["prop"], ASKS[-1:]))
ck("6d 위 표시등, 50K 줄 표시등 초록", t._prop_reco_lit() is True and t._prop_row_lit("GC", r3) is True)
ck("6e 50K 줄 주문 1R = 추천 없음이라 저장 금액(50K 평가 위 끝 이하)", E.App._prop_r_info(t, r3["prop"], "projectx", TS50)[2] is False)

# 13 이미 '추천 금액 쓰기'인데 한 칸만 따라가는 줄(21l 저장): 회색, 버튼이 다시 켜서 펀디드 칸도 서버 값을 따라간다
r5 = row(XFA, dict(own("funded"), mode="reco", reco_keys=["r_test"], reco_acct=XFA.casefold(), r_steady=500.0))
t = T(acfg(gc=[r5]))
E._DAYCAP_SRV = {"reco_1r": {"projectx": {"funded": {150000: 450.0}}}}
ck("13a 한 칸만 따라가는 펀디드 줄 = 주문 1R 저장 500, 회색", E.App._prop_r_info(t, r5["prop"], "projectx", XFA)[:2] == ("funded", 500.0)
   and t._prop_reco_lit() is False and t._prop_row_lit("GC", r5) is False)
t._prop_all_reco()
ck("13b 누르면 펀디드 칸도 따라감 = 450, 초록", E.App._prop_r_info(t, r5["prop"], "projectx", XFA) == ("funded", 450.0, True)
   and t._prop_reco_lit() is True)
E._DAYCAP_SRV = {}

# 14 나스닥 탭에서 한 번: 금, 나스닥 프롭 계좌가 모두 켜진다(대표 2026-10-27 "아까 나스닥만 바뀌었어") - 금 줄이 추천 없음, 한 칸만이어도
_g1, _g2 = row(TS50, own("test")), row(XFA, dict(own("funded"), mode="reco", reco_keys=["r_test"], reco_acct=XFA.casefold()))
_n1 = row(XFA, own("funded"))
t = T(acfg(nq=[_n1], gc=[_g1, _g2]), asset="NQ")
t._prop_all_reco()
ck("14 나스닥 탭에서 한 번 누름 = 금 두 줄, 나스닥 한 줄 모두 다 켜짐",
   all(t._prop_reco_full(r["prop"], r["id"]) for r in (_n1, _g1, _g2)) and t._prop_reco_lit() is True,
   [r["prop"] for r in (_n1, _g1, _g2)])

# 7 프롭 끈 계좌는 위 표시등 셈에서 빠지고 줄은 회색
r4 = row(TS150, dict(own("test"), on=False))
t = T(acfg(nq=[r1, r4]))
ck("7a 프롭 끈 직접 정하기 계좌는 셈 밖 = 초록", t._prop_reco_lit() is True)
ck("7b 프롭 끈 줄 = 회색", t._prop_row_lit("NQ", r4) is False)

# 8 따라가는 계좌가 하나도 없으면(프롭 끈 계좌뿐) 회색
t = T(acfg(nq=[r4]))
ck("8 프롭 끈 계좌뿐 = 회색", t._prop_reco_lit() is False)

# 9 칠하기: 줄 수보다 많은 위젯, 없어진 표시등, 판정 예외는 조용히 넘긴다
t = T(acfg(nq=[r1]))
gone = Dot(alive=False)
t._prop_reco_dot = Dot()
t._acct_widgets = {0: {"prop_dot": Dot()}, 1: {"prop_dot": Dot()}, 2: {"prop_dot": gone}}
t._prop_reco_dot_paint()
ck("9a 줄 밖 위젯, 없어진 표시등은 건드리지 않음", t._acct_widgets[1]["prop_dot"].fg is None and gone.fg is None)
t2 = T(acfg(nq=[r1]))
t2._prop_reco_dot = Dot()
t2._prop_reco_lit = lambda: (_ for _ in ()).throw(RuntimeError("x"))
try:
    t2._prop_reco_dot_paint()
    ck("9b 판정 예외 = 조용히", True)
except Exception as e:
    ck("9b 판정 예외 = 조용히", False, e)

# 10 2초마다 다시 칠하기: 표시등이 있으면 다음 차례를 걸고, 없어지면 멈춘다(표시등마다 따로 - 다시 그려도 겹쳐 쌓이지 않음)
t = T(acfg(nq=[r1]))
d = Dot()
t._prop_reco_dot_tick(d)
ck("10a 살아 있는 표시등 = 칠하고 2초 뒤 다시", d.fg == GREEN and t.root.calls == [2000], (d.fg, t.root.calls))
t = T(acfg(nq=[r1]))
t._prop_reco_dot_tick(Dot(alive=False))
ck("10b 없어진 표시등 = 멈춤", t.root.calls == [], t.root.calls)

# 15 앱이 업그레이드 때 '챌린지'로 묶어 둔 Topstep EXPRESS 줄(type_pin): 회색, 누르면 단계까지 풀려 펀디드 추천 금액을 따른다(대표 2026-10-27 금 계좌가 안 바뀐 경우)
_pin = row(XFA, dict(own("test"), type_manual=True, type_pin=True, r_test=500.0, r_steady=500.0))
_mine = row("EXPRESS-V2-777-888", dict(own("test"), type_manual=True, type_pin=False, r_test=500.0, r_steady=500.0))
t = T(acfg(gc=[_pin, _mine]))
E._DAYCAP_SRV = {"reco_1r": {"projectx": {"funded": {150000: 450.0}}}}
ck("15a 묶인 줄 = 챌린지 $500, 회색", E.App._prop_r_info(t, _pin["prop"], "projectx", XFA)[:2] == ("test", 500.0)
   and t._prop_row_lit("GC", _pin) is False)
ASKS.clear()
t._prop_all_reco()
ck("15b 누르면 묶임이 풀려 펀디드 450(추천 금액), 확인 창에 '챌린지 $500 → 펀디드 $450'",
   E.App._prop_r_info(t, _pin["prop"], "projectx", XFA) == ("funded", 450.0, True)
   and _pin["prop"].get("type_manual") is False and _pin["prop"].get("type_pin") is False
   and any("챌린지 $500 → 펀디드 $450" in m for m in ASKS), (ASKS[-1:], _pin["prop"]))
ck("15c 회원이 직접 고른 단계는 그대로(챌린지 고정 유지)", _mine["prop"].get("type_manual") is True
   and E.App._prop_r_info(t, _mine["prop"], "projectx", "EXPRESS-V2-777-888")[0] == "test", _mine["prop"])
E._DAYCAP_SRV = {}

# 12 다 켜진 상태(초록)에서 누르면 끄기(대표 2026-10-27 "켤 수만 있고 끌 수가 없어"): 묻고, '예'면 지금 금액 그대로 직접 정하기, 회색. 다시 누르면 다시 켜짐
E._DAYCAP_SRV = {"reco_1r": {"projectx": {"funded": {150000: 450.0}}}}
r1b = row(XFA, own("funded"))
t = T(acfg(nq=[r1b]))
t._prop_all_reco()                                        # 켜기
_cur = E.App._prop_r_info(t, r1b["prop"], "projectx", XFA)
ck("12a 켠 뒤 = 펀디드 450 추천 금액, 초록", _cur == ("funded", 450.0, True) and t._prop_reco_lit() is True, _cur)
ASKS.clear()
t._prop_all_reco()                                        # 끄기
_off = E.App._prop_r_info(t, r1b["prop"], "projectx", XFA)
ck("12b 다시 누름 = 끄기 확인 창('끕니다', '그대로'), 직접 정하기, 1R 450 그대로, 회색",
   len(ASKS) == 1 and "프롭 추천 금액 쓰기를 끕니다" in ASKS[0] and "펀디드 $450 그대로" in ASKS[0]
   and r1b["prop"].get("mode") == "own" and _off == ("funded", 450.0, False) and t._prop_reco_lit() is False, (ASKS, r1b["prop"], _off))
E._DAYCAP_SRV = {"reco_1r": {"projectx": {"funded": {150000: 440.0}}}}
ck("12c 끈 뒤 서버 값이 바뀌어도 그대로 450", E.App._prop_r_info(t, r1b["prop"], "projectx", XFA)[:2] == ("funded", 450.0))
t._prop_all_reco()                                        # 다시 켜기
ck("12d 한 번 더 누름 = 다시 켜짐, 서버 440 따라감", E.App._prop_r_info(t, r1b["prop"], "projectx", XFA) == ("funded", 440.0, True)
   and t._prop_reco_lit() is True)
E.messagebox.askyesno = lambda tt, m, **k: (ASKS.append(m), False)[1]
t._prop_all_reco()
ck("12e 끄기에서 '아니요' = 그대로 켜짐", r1b["prop"].get("mode") == "reco" and t._prop_reco_lit() is True)
E.messagebox.askyesno = lambda tt, m, **k: (ASKS.append(m), True)[1]
E._DAYCAP_SRV = {}
t = T(acfg())
ASKS.clear()
t._prop_all_reco()
ck("12f 프롭 계좌 없음 = 안내 + 켜는 법(2026-11-09 c)", len(ASKS) == 1 and "이 버튼은 프롭 옵션을 켠 계좌에 적용됩니다" in ASKS[0]
   and "[프롭: 끔]을 눌러 '프롭 계좌로 사용'에 체크" in ASKS[0], ASKS)

# 11 배선: 버튼 옆 표시등, 줄 [프롭: …] 옆 표시등, 프롭 창 저장과 계좌 고르기 뒤 다시 칠하기
src = inspect.getsource(E.App)
_cr = inspect.getsource(E.App._dock_reco_row)
ck("11a 왼쪽 메뉴 아래(2026-11-02): [프롭 추천 금액 쓰기] 바로 뒤 표시등과 2초 칠하기, [계좌 수정] 잠금은 없음(2026-11-03)",
   _cr.index("command=self._prop_all_reco") < _cr.index("self._prop_reco_dot_tick(self._prop_reco_dot)")
   and not hasattr(E.App, "_toggle_acct_edit") and not hasattr(E.App, "_acct_common_row"))
_bs = inspect.getsource(E.App._build)
ck("11f 추천 금액 버튼은 자산 탭마다 그리지 않는다(대표 '계정 수정 버튼도 자산별로 하지 말구')",
   _bs.count("command=self._prop_all_reco") == 0, (_bs.count("command=self._prop_all_reco"),))
ck("11b 줄 표시등은 [프롭: …]과 함께 보이고 위젯에 남는다",
   "pb.pack(side=\"left\", padx=(4, 0))\n            pdot.pack(side=\"left\", padx=(2, 0))" in src
   and "self._acct_widgets[idx][\"prop_dot\"] = pdot" in src)
ck("11c 계좌를 골라 [프롭: …]을 끼울 때 표시등도 그 뒤에", "_wr[\"prop_dot\"].pack(side=\"left\", padx=(2, 0), after=_pbw)" in src)
_pd_src = inspect.getsource(E.App._prop_dialog)
ck("11d 프롭 창 저장 뒤 다시 칠하기, [1R 도우미]는 표시등 뒤에",
   "self._prop_reco_dot_paint()" in _pd_src and "after=(_pd if _pd is not None and _pd.winfo_manager() else w.get(\"prop_btn\"))" in _pd_src)
ck("11e 연결 테스트 표시등과 같은 색", f'"{GREEN}"' in inspect.getsource(E.App._prop_reco_dot_paint)
   and f'"{GRAY}"' in inspect.getsource(E.App._prop_reco_dot_paint))

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}"))
sys.exit(1 if FAIL else 0)
