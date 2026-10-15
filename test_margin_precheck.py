#!/usr/bin/env python3
# 증거금 사전 점검(대표 2026-10-14 결정 7 "이번 앱에 포함해") 오프라인 시험. 0 = 통과.
#   1 표: 내장값 = 공개 장중 증거금(MNQ $100, MGC $200)과 마이크로 한 계약 비교(계약 수 어림 없음), 서버 값(시험용 숫자)이 오면
#     예상 계약(1R x 3 ÷ 마이크로 한 계약 위험) x 마이크로당 증거금, 최소 한 계약, 천장, 비선물
#   2 계좌마다 경고: NT8 현금 부족, 0 이하, 현금 없으면 NetLiq, 넉넉하면 없음, ProjectX Combine 넉넉함, Topstep 펀디드(잔고 $0 시작) 제외,
#     프롭 라이브 초기 제외, 잔고 모름 = 말 안 함, Tradovate, IBKR, 크립토 = 종전 그대로(점검 없음), 같은 계좌 한 번
#   3 문안 KO/EN 그대로(대표 문안), 계좌 끝 4자리, 경고 창은 계좌, 진입 시각마다 한 번(로그는 매번), 막지 않음
#   4 배선: 사전 점검(T-70, T-10)이 브로커 확인을 통과한 뒤 증거금 점검을 부른다, 실패해도 점검은 그대로
# HOME, APPDATA = 임시 폴더, 네트워크 차단, EQ_NO_ALERT=1.
import inspect
import os
import socket
import sys
import tempfile
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_margin_")
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

# ── 1 표 ──────────────────────────────────────────────────────────────────────
E._DAYCAP_SRV = {}
ck("1a 내장값 = 공개 장중 증거금(MNQ $100, MGC $200), 마이크로 한 계약 위험은 내장값 없음(서버 값만), 배수 3, 천장 400",
   E._MARGIN_PER_MICRO == {"NQ": 100.0, "GC": 200.0} and E._MARGIN_MICRO_RISK == {} and E._MARGIN_MAX_MULT == 3.0
   and E._MARGIN_MAX_MICROS == 400 and not E._margin_est("NQ") and not E._margin_est("GC"))
ck("1b 서버 값 없음 = 계약 수를 어림하지 않고 마이크로 한 계약과 비교: NQ (1, $100), GC (1, $200), 1R이 커도 같다",
   (E._margin_need("NQ", 500.0), E._margin_need("GC", 2200.0), E._margin_need("NQ", 1_000_000.0))
   == ((1, 100.0), (1, 200.0), (1, 100.0)), (E._margin_need("NQ", 500.0), E._margin_need("GC", 2200.0)))
ck("1c 1R 모름, 0, 비트코인 = 점검 안 함(0)", E._margin_need("NQ", None) == (0, 0.0) and E._margin_need("GC", 0) == (0, 0.0)
   and E._margin_need("BTC", 500.0) == (0, 0.0))
# 서버 값(시험용 숫자 - 실제 서버 값이 아니다): 마이크로당 증거금 NQ $500, GC $900, 마이크로 한 계약 위험 NQ $250, GC $300
_SRV_M = E._prop_reco_validate({"rules": {"margin": {"per_micro": {"NQ": 500, "GC": 900}, "micro_risk": {"NQ": 250, "GC": 300}}}})
E._DAYCAP_SRV = _SRV_M
ck("1d 서버 값을 받으면 예상 계약을 어림한다: NQ 1R $500 = 500 x 3 ÷ 250 = 6계약 x $500 = $3,000, GC 1R $2,200 = 6,600 ÷ 300 = 22계약 x $900",
   (E._margin_est("NQ"), E._margin_need("NQ", 500.0), E._margin_need("GC", 2200.0)) == (True, (6, 3000.0), (22, 19800.0)),
   (_SRV_M, E._margin_need("NQ", 500.0), E._margin_need("GC", 2200.0)))
ck("1e 서버 값: 1R이 작아도 최소 한 계약, 천장 마이크로 400, 1R 모름과 비트코인은 0", E._margin_need("NQ", 10.0) == (1, 500.0)
   and E._margin_need("NQ", 1_000_000.0) == (400, 200000.0) and E._margin_need("NQ", None) == (0, 0.0)
   and E._margin_need("BTC", 500.0) == (0, 0.0))
_SRV_RISK_ONLY = E._prop_reco_validate({"rules": {"margin": {"micro_risk": {"NQ": 250}}}})
E._DAYCAP_SRV = _SRV_RISK_ONLY
ck("1f 위험만 서버 값 = 증거금은 공개 장중 값: NQ 6계약 x $100, GC는 위험 값이 없어 한 계약",
   (E._margin_need("NQ", 500.0), E._margin_need("GC", 2200.0)) == ((6, 600.0), (1, 200.0)))
E._DAYCAP_SRV = _SRV_M


# ── 2 계좌마다 경고 ──────────────────────────────────────────────────────────────
class FakeB:
    def __init__(self, cash=None, nl=None):
        self.cash, self.nl, self.calls = dict(cash or {}), dict(nl or {}), []

    def account_balance(self, a):
        self.calls.append(("account_balance", a))
        v = self.cash.get(a)
        if isinstance(v, Exception):
            raise v
        return v

    def account_netliq(self, a):
        return self.nl.get(a)


class Stub:
    lang = "ko"

    def __init__(self, brokers):
        self.logs, self.after = [], []
        self._brokers = brokers
        self._acfg, self._sig_accts = {}, {}
        self.root = SimpleNamespace(after=lambda ms, fn=None, *a: self.after.append(fn))

    def log(self, m):
        self.logs.append(str(m))

    def _passtp_broker(self, cfg):
        return self._brokers[cfg["broker"]]

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_margin_warns", "_margin_precheck", "_dc_map", "_dc_f", "_dc_state", "_dc_save", "_daycap_key", "_daycap_money",
           "_planned_one_r"):
    setattr(Stub, _n, E.App.__dict__[_n])


def arm(st, rows):
    st._acfg, st._sig_accts = {}, {}
    for asset, bk, acct, one_r, pr in rows:
        a = st._acfg.setdefault(asset, {"accounts": []})["accounts"]
        a.append({"id": acct, "one_r": one_r, "prop": dict(pr), "on": True})
        st._sig_accts[(asset, len(a) - 1)] = {"broker": bk, "acct": acct, "live": True, "f1": "u", "f2": "k", "f3": ""}


nt8 = FakeB(cash={"OWN-1234": 1000.0, "OWN-RICH": 50000.0, "OWN-ZERO": 0.0, "LDI150A7777": 150000.0, "LIVE-0": 0.0},
            nl={"OWN-NONE": 900.0})
px = FakeB(cash={"150KTC-V2-1": 150000.0, "EXPRESS-V2-DLL-5678": 0.0, "PRAC-V2-0000": 0.0})
st = Stub({"nt8": nt8, "projectx": px})
arm(st, [("NQ", "nt8", "OWN-1234", 500.0, P0), ("NQ", "nt8", "OWN-RICH", 500.0, P0), ("NQ", "nt8", "OWN-ZERO", 100.0, P0),
         ("NQ", "nt8", "OWN-NONE", 500.0, P0), ("NQ", "nt8", "OWN-UNK", 500.0, P0),
         ("NQ", "nt8", "LDI150A7777", 500.0, dict(P0, on=True, type="test", r_test=2200.0)),
         ("NQ", "nt8", "LIVE-0", 500.0, dict(P0, on=True, type="live", r_live=100.0)),
         ("NQ", "projectx", "150KTC-V2-1", 500.0, dict(P0, on=True, type="test", r_test=2200.0)),
         ("NQ", "projectx", "EXPRESS-V2-DLL-5678", 500.0, dict(P0, on=True, type="funded", r_steady=450.0)),
         ("NQ", "projectx", "PRAC-V2-0000", 500.0, P0),
         ("NQ", "tradovate", "TV-1", 500.0, P0), ("NQ", "ibkr", "U1", 500.0, P0),
         ("GC", "nt8", "OWN-1234", 500.0, P0)])
_w_nt8 = E.App._margin_warns(st, "NQ", "nt8")
_keys = [w[4][1] for w in _w_nt8]
ck("2a NT8(서버 값): 현금 $1,000 < $3,000 경고, 0 이하 경고, 현금 없으면 NetLiq($900)로 경고 / 넉넉함, 잔고 모름, Lucid 150K, 라이브 초기 = 없음",
   _keys == ["own-1234", "own-zero", "own-none"], _keys)
_w_px = E.App._margin_warns(st, "NQ", "projectx")
ck("2b ProjectX: Combine 150K 넉넉함 = 없음, Topstep 펀디드(EXPRESS, 잔고 $0 시작) = 보지 않음, 잔고 0인 연습 계좌 = 경고",
   [w[4][1] for w in _w_px] == ["prac-v2-0000"], _w_px)
ck("2c Tradovate, IBKR = 점검 없음(종전 그대로, 잔고도 안 읽음)", E.App._margin_warns(st, "NQ", "tradovate") == []
   and E.App._margin_warns(st, "NQ", "ibkr") == [] and E.App._margin_warns(st, "BTC", "bybit") == [])
ck("2d 자산마다 따로(GC 행은 GC 점검에서만)", [w[4][1] for w in E.App._margin_warns(st, "GC", "nt8")] == ["own-1234"])
nt8.cash["OWN-RICH"] = RuntimeError("bridge down")
ck("2e 잔고 읽기 실패 = 말하지 않음(연결은 사전 점검 본문이 본다)", "own-rich" not in [w[4][1] for w in E.App._margin_warns(st, "NQ", "nt8")])
# 2f 서버 값 없음(내장값): 잔고가 0 이하이거나 마이크로 한 계약의 공개 장중 증거금보다 적을 때만 경고, 근거 문구도 그렇게 말한다
E._DAYCAP_SRV = {}
nt8b = FakeB(cash={"OWN-1234": 1000.0, "OWN-ZERO": 0.0, "OWN-TINY": 50.0})
stb = Stub({"nt8": nt8b})
arm(stb, [("NQ", "nt8", "OWN-1234", 500.0, P0), ("NQ", "nt8", "OWN-ZERO", 500.0, P0), ("NQ", "nt8", "OWN-TINY", 500.0, P0)])
_wb = E.App._margin_warns(stb, "NQ", "nt8")
ck("2f 서버 값 없음: 0 이하이거나 마이크로 한 계약($100)보다 적을 때만 경고(…1234 $1,000은 말 안 함), 근거 = 마이크로 한 계약의 증거금",
   [w[4][1] for w in _wb] == ["own-zero", "own-tiny"] and _wb[1][2] == "(잔고 $50, 마이크로 한 계약의 증거금 약 $100)"
   and _wb[1][3] == "(balance $50, margin for one micro contract about $100)", _wb)
E._DAYCAP_SRV = _SRV_M

# ── 3 문안, 창 ─────────────────────────────────────────────────────────────────
_w = _w_nt8[0]
ck("3a KO 문안 그대로(대표 결정 7), 계좌 끝 4자리", _w[0] == "계좌 …1234의 잔고가 주문에 필요한 증거금보다 적어 보입니다. "
                                                  "브로커에서 확인하시거나 이 계좌를 꺼 두십시오.", _w[0])
ck("3b EN 짝", _w[1] == "The balance of account …1234 looks lower than the margin the order needs. Please check it at your "
                       "broker or turn this account off.", _w[1])
ck("3c 근거(잔고, 예상 계약, 증거금) KO", _w[2] == "(잔고 $1,000, 이번 세션 예상 최대 마이크로 환산 6계약의 증거금 약 $3,000)", _w[2])
_all = [x for w in (_w_nt8 + _w_px) for x in w[:4]]
ck("3d 문구에 이모지, 가운뎃점, em 대시 없음 + 합니다체", not any(("·" in s or "—" in s or "요." in s) for s in _all), _all)
st.after.clear()
st.logs.clear()
E.App._margin_precheck(st, "NQ", "nt8", "10:00 EDT")
E.App._margin_precheck(st, "NQ", "nt8", "10:00 EDT")
ck("3e 로그는 매번(계좌마다 한 줄), 경고 창은 같은 진입 시각에 한 번", len(st.logs) == 6 and len(st.after) == 1,
   (len(st.logs), len(st.after)))
E.App._margin_precheck(st, "NQ", "nt8", "10:00 EDT +1d")
ck("3f 다음 진입 시각엔 다시 한 번", len(st.after) == 2)
ck("3g 막지 않는다(경고 함수는 무장, 계좌 설정을 바꾸지 않음)", all(k in st._sig_accts for k in [("NQ", i) for i in range(12)])
   and all(a.get("on") for a in st._acfg["NQ"]["accounts"]))

# ── 4 배선 ────────────────────────────────────────────────────────────────────
_src = inspect.getsource(E.App._precheck_run)
_i_hc, _i_ok, _i_m = _src.find("b.healthcheck()"), _src.find('"preflight_ok"'), _src.find("self._margin_precheck(")
ck("4a 사전 점검: 브로커 확인(healthcheck) 통과 뒤에 증거금 점검을 부른다(T-70, T-10 같은 함수)",
   0 < _i_hc < _i_ok < _i_m, (_i_hc, _i_ok, _i_m))


class PStub(Stub):
    pass


PStub._precheck_run = E.App.__dict__["_precheck_run"]
PStub._margin_precheck = lambda self, *a: (_ for _ in ()).throw(RuntimeError("boom"))
_called = []
PStub._send_ev = lambda self, kind, asset, **k: _called.append(kind)
_bb = E._build_broker
E._build_broker = lambda *a, **k: SimpleNamespace(healthcheck=lambda: True, search_contracts=lambda s: [{"id": "CON.F.US.MNQ.Z26"}])
try:
    ps = PStub({})
    E.App._precheck_run(ps, "NQ", {"broker": "nt8", "acct": "OWN-1234"}, "10:00 EDT", 10)
finally:
    E._build_broker = _bb
ck("4b 증거금 점검이 실패해도 사전 점검 결과는 그대로(통과 보고, 실패 보고 없음)", _called == ["preflight_ok"]
   and not any("사전 점검 실패" in m for m in ps.logs), (_called, ps.logs[-2:]))

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
