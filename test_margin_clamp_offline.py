#!/usr/bin/env python3
# 증거금에 맞춰 계약 줄이기(대표 2026-10-16) 오프라인 시험. 0 = 통과.
#   1 넉넉하면 그대로, 모자라면 들어가는 만큼(미니는 마이크로로 풀어 셈), 마이크로 한 계약도 안 되면 건너뜀(사유 margin)
#   2 쓸 수 있는 증거금 = 새 애드온 증거금 여유(0보다 클 때만), 아니면 현금 / 모르면 그대로(종전처럼 브로커가 판단)
#   3 로그와 회원 알림 문안(KO/EN, 계좌 끝 4자리, 약 xR), 서버 표 값이 오면 그 값
#   4 NT8 어댑터 account_margin_avail, 애드온 필드, 진입 경로 배선(ProjectX 등 메서드 없는 브로커는 그대로)
# HOME, APPDATA = 임시 폴더, 네트워크 차단, EQ_NO_ALERT=1.
import inspect
import os
import socket
import sys
import tempfile
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_mclamp_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402
from eqexec.broker.nt8 import NT8Broker  # noqa: E402

assert E.APP_DIR.startswith(HOME), E.APP_DIR
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


E._DAYCAP_SRV = {}                     # 내장 표: MNQ $100, MGC $200(마이크로 한 계약)


class FakeB:
    def __init__(self, v, src):
        self.v, self.src = v, src

    def account_margin_avail(self, a):
        return self.v, self.src


class Stub:
    lang = "ko"

    def __init__(self):
        self.logs, self.alerts, self.skips = [], [], []

    def log(self, m):
        self.logs.append(str(m))

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def _entry_skip_ev(self, asset, why):
        self.skips.append((asset, why))

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None
for _n in ("_ui", "_on_main", "_dm_kind"):   # 2026-10-20 화면 줄 대기열(_ui) - 가짜 객체의 __getattr__가 삼키지 않게
    setattr(Stub, _n, E.App.__dict__[_n])


for _n in ("_margin_clamp", "_daycap_money"):
    setattr(Stub, _n, getattr(E.App, _n))
SC = "SIM-ACCT-5471"

# ── 1 줄이기 ─────────────────────────────────────────────────────────────────
st = Stub()
ck("1a 넉넉함(현금 $5,000, MNQ 10 = $1,000) = 그대로, 로그, 알림 없음",
   st._margin_clamp(FakeB(5000.0, "cash"), SC, [("MNQ", 10)], "NQ", 1.0) == [("MNQ", 10)] and not st.logs and not st.alerts)
st = Stub()
r = st._margin_clamp(FakeB(550.0, "cash"), SC, [("MNQ", 10)], "NQ", 1.0)
ck("1b 대표 예: MNQ 10계약, 현금 $550 = 마이크로 5계약", r == [("MNQ", 5)], r)
ck("1c 로그: '증거금 부족으로 10계약 중 5계약만 진입(약 0.50R' + 끝 4자리", any("증거금 부족으로 10계약 중 5계약만 진입(약 0.50R" in m and "…5471" in m
                                                                              for m in st.logs), st.logs)
ck("1d 회원 알림 KO/EN 한 번, 계좌 끝 4자리만", len(st.alerts) == 1 and st.alerts[0][0].startswith("margin_clamp_")
   and "10계약 중 5계약만 진입(약 0.50R)" in st.alerts[0][1] and "entered 5 of 10 contracts (about 0.50R)" in st.alerts[0][2]
   and SC not in st.alerts[0][1] and "…5471" in st.alerts[0][1], st.alerts)
st = Stub()
r = st._margin_clamp(FakeB(1700.0, "cash"), SC, [("NQ", 2), ("MNQ", 3)], "NQ", 1.75)
ck("1e 미니 2 + 마이크로 3 = 23, 현금 $1,700 = 17 → 미니 1 + 마이크로 7", r == [("NQ", 1), ("MNQ", 7)], r)
ck("1f 배수 반영 약 xR = 1.75 x 17/23 = 1.29R", any("(약 1.29R" in m for m in st.logs), st.logs)
st = Stub()
r = st._margin_clamp(FakeB(950.0, "cash"), SC, [("NQ", 2)], "NQ", 1.0)
ck("1g 미니 2 = 20, $950 = 9 → 미니 없이 마이크로 9(미니를 마이크로로 풀어 셈)", r == [("MNQ", 9)], r)
st = Stub()
r = st._margin_clamp(FakeB(150.0, "broker"), SC, [("MGC", 3)], "GC", 1.0)
ck("1h 금 MGC 3, 브로커 증거금 여유 $150 < $200 = 건너뜀(skip), 사유 margin, 알림 margin_skip", r == "skip" and st.skips == [("GC", "margin")]
   and st.alerts and st.alerts[0][0].startswith("margin_skip_") and any("증거금 부족으로 진입하지 않습니다" in m for m in st.logs), (r, st.skips, st.alerts))
st = Stub()
r = st._margin_clamp(FakeB(150.0, "cash"), SC, [("MGC", 3)], "GC", 1.0)
ck("1i 현금 기준으로는 건너뛰지 않는다(그대로, 브로커가 판단 - 검토 P1-1)", r == [("MGC", 3)] and not st.alerts, (r, st.alerts))

# ── 2 출처 ───────────────────────────────────────────────────────────────────
st = Stub()
ck("2a 모름(None) = 그대로, 로그 없음", st._margin_clamp(FakeB(None, None), SC, [("MNQ", 10)], "NQ", 1.0) == [("MNQ", 10)] and not st.logs)
st = Stub()
r = st._margin_clamp(FakeB(300.0, "broker"), SC, [("MNQ", 10)], "NQ", 1.0)
ck("2b 브로커 증거금 여유 $300 = 3계약, 로그에 '브로커가 알려 준 증거금 여유'", r == [("MNQ", 3)]
   and any("브로커가 알려 준 증거금 여유" in m for m in st.logs), (r, st.logs))


class Bridge(NT8Broker):
    def __init__(self, row):
        self.row = row

    def _acct_row(self, a):
        return self.row


ck("2c NT8 어댑터: 증거금 여유가 0보다 크면 그 값(broker)", Bridge({"excess_margin": 800.0, "cash_value": 9000.0}).account_margin_avail("x") == (800.0, "broker"))
ck("2d 증거금 여유 0이나 음수 = 현금(cash), 현금도 0 이하 = 모름 - 0 하나로 막지 않는다",
   Bridge({"excess_margin": 0.0, "cash_value": 9000.0}).account_margin_avail("x") == (9000.0, "cash")
   and Bridge({"excess_margin": -5.0, "cash_value": 400.0}).account_margin_avail("x") == (400.0, "cash")
   and Bridge({"excess_margin": 0.0, "cash_value": 0.0}).account_margin_avail("x") == (None, None))
ck("2e 옛 애드온(필드 없음) = 현금, 계좌 없음 = (None, None)", Bridge({"cash_value": 400.0}).account_margin_avail("x") == (400.0, "cash")
   and Bridge(None).account_margin_avail("x") == (None, None))

# ── 3 서버 표 ────────────────────────────────────────────────────────────────
E._DAYCAP_SRV = E._prop_reco_validate({"rules": {"margin": {"per_micro": {"NQ": 500}}}})     # 시험용 숫자(실제 서버 값 아님)
st = Stub()
r = st._margin_clamp(FakeB(2600.0, "cash"), SC, [("MNQ", 10)], "NQ", 1.0)
ck("3a 서버 표 값(마이크로 $500)이 오면 그 값: $2,600 = 5계약", r == [("MNQ", 5)], r)
E._DAYCAP_SRV = {}
st = Stub()
st.lang = "en"
st._margin_clamp(FakeB(550.0, "cash"), SC, [("MNQ", 10)], "NQ", 1.0)
ck("3b EN 로그", any("Margin short: entering 5 of 10 contracts (about 0.50R" in m for m in st.logs), st.logs)

# ── 3c 계좌마다 따로, 모의 실행이면 DM 없음(앱 출시 전 검토 2026-10-20) ─────────────
st = Stub()
st._margin_clamp(FakeB(550.0, "cash"), SC, [("MNQ", 10)], "NQ", 1.0)
st._margin_clamp(FakeB(550.0, "cash"), "LDI150B9999", [("MNQ", 10)], "NQ", 1.0)
ck("3c 두 계좌 = DM 종류 둘(서버 30분 묶음에 둘째 계좌가 사라지지 않게)", len({a[0] for a in st.alerts}) == 2, st.alerts)
st = Stub()
r = st._margin_clamp(FakeB(150.0, "broker"), SC, [("MGC", 3)], "GC", 1.0, live=False)
st._margin_clamp(FakeB(550.0, "cash"), SC, [("MNQ", 10)], "NQ", 1.0, live=False)
ck("3d 모의 실행(live=False)이면 줄이기와 건너뜀은 그대로, DM은 없음", r == "skip" and not st.alerts, st.alerts)

# ── 4 배선 ───────────────────────────────────────────────────────────────────
src = inspect.getsource(E.App._run_futures_entry)
ck("4a 진입 경로: 계좌 한도 줄이기 뒤 account_margin_avail이 있는 브로커만, skip이면 진입 안 함(None은 그대로), 계산 실패면 그대로",
   'if hasattr(b, "account_margin_avail"):' in src and "self._margin_clamp(b, sc, legs, asset, mult, live=live)" in src
   and src.index("_size_clamp(") < src.index("_margin_clamp(") and "_ml = legs" in src and 'if _ml == "skip":' in src)
ck("4b _enter_account가 배수를 넘긴다", src.count("mult") >= 2 and "mult=mult" in inspect.getsource(E.App._enter_account))
_cs = open(os.path.join(HERE, "nt8_addon", "EQAutopilotBridge.cs"), encoding="utf-8").read()
ck("4c 애드온이 excess_margin(장중 증거금 여유)을 보낸다", '{ "excess_margin", a.Get(AccountItem.ExcessIntradayMargin, Currency.UsDollar) }' in _cs)
from eqexec.broker.projectx import ProjectXBroker  # noqa: E402
ck("4d ProjectX에는 account_margin_avail이 없다(프롭 한도만, 펀디드 잔고 $0 시작이라 잔고로 막지 않는다)",
   not hasattr(ProjectXBroker, "account_margin_avail"))

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
