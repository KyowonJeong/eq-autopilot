# EQ Autopilot 2026.11.09d 출금 알림 오프라인 시험(2026-11-09 대표 "앱에서 때되면 출금 신청하라 말해주나", "이번에 넣어. 관문 다시 해"). 0 = 통과.
# 정본 출금 때(Topstep XFA 잔고 $10,000 이상 = $5,000, Lucid 150K 이익 중 $4,600 넘는 부분 $500 이상)에 로그(금액)와 회원 DM(숫자 없음),
# 계좌마다 3일에 한 번(재시작 뒤에도), 프롭 끈 줄, 평가, 수동 모드, 잔고 모름은 말하지 않음, 진입 전 점검에 붙음.
import inspect
import os
import socket
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_1109d_")
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


class FakeB:
    def __init__(self, bal):
        self.bal = bal

    def account_balance(self, sc):
        if isinstance(self.bal, Exception):
            raise self.bal
        return self.bal


class T(E.App):
    def __init__(self, acfg, sig, bal, lang="ko"):
        self._acfg, self._sig_accts, self.lang = acfg, sig, lang
        self._asset = "NQ"
        self.logs, self.dms, self.bal = [], [], bal

    def log(self, m):
        self.logs.append(str(m))

    def _member_alert(self, kind, ko, en):
        self.dms.append((kind, ko, en))

    def _passtp_broker(self, cfg):
        return FakeB(self.bal)


XFA, TS150 = "EXPRESS-V2-555-666", "150KTC-V2-111-222"
LDF, LDF50 = "LDF15012345670001", "LDF05012345670001"
PROP_ON = {"on": True, "set": True, "type": "funded", "mode": "reco"}


def setup(aid, bk, prop=None, manual=False, asset="NQ"):
    row = {"id": aid, "one_r": 500.0, "on": True, "label": "row-" + aid[-4:], "broker": bk,
           "prop": dict(PROP_ON) if prop is None else prop}
    acfg = {"NQ": {"broker": bk, "accounts": [row] if asset == "NQ" else []},
            "GC": {"broker": bk, "accounts": [row] if asset == "GC" else []},
            "BTC": {"broker": "bybit", "accounts": []}}
    sig = {(asset, 0): {"broker": bk, "acct": aid, "label": "row-" + aid[-4:], "live": True, "manual": manual}}
    return acfg, sig


def fresh(acfg, sig, bal, lang="ko", keep=False):
    """keep=False면 출금 알림 기록(상태 파일 payout_note)을 비운다 - 상태 파일 경로는 import 때 정해져 시험마다 공유된다."""
    t = T(acfg, sig, bal, lang)
    t._dc_st = None
    if not keep:
        t._dc_state()["payout_note"] = {}
    return t


ck("0 앱 버전 2026.11.09d 이상", E.App._APP_VER >= "2026.11.09d", E.App._APP_VER)

# ── 1. 정본 판정(순수 함수) ──
A = E._payout_advice
ck("1a Topstep $9,999 = 아직", A("projectx", 9_999.0) is None)
ck("1b Topstep $10,000 = $5,000", A("projectx", 10_000.0) == (5_000.0, "ts"), A("projectx", 10_000.0))
ck("1c Topstep $40,000도 $5,000(회당 상한)", A("projectx", 40_000.0) == (5_000.0, "ts"))
ck("1d Lucid 150K 이익 $5,099 = 아직(넘는 부분 $499)", A("nt8", 155_099.0, 150_000) is None)
ck("1e Lucid 150K 이익 $5,100 = $500", A("nt8", 155_100.0, 150_000) == (500.0, "lu"), A("nt8", 155_100.0, 150_000))
ck("1f Lucid 150K 이익 $8,000 = $3,400", A("nt8", 158_000.0, 150_000) == (3_400.0, "lu"))
ck("1g Lucid 50K = 정본 없음", A("nt8", 160_000.0, 50_000) is None)
ck("1h 잔고 모름, 숫자 아님, 다른 브로커 = 없음",
   A("projectx", None) is None and A("projectx", "x") is None and A("bybit", 99_999.0) is None and A("nt8", 160_000.0, None) is None)
ck("1i 정본 상수: 쿠션 $3,000 + 회당 $5,000, 잔고 50% → $10,000 문턱",
   E._PAYOUT_TS_MIN_BAL == max(E._PAYOUT_TS_KEEP + E._PAYOUT_TS_AMT, 2 * E._PAYOUT_TS_AMT))

# ── 2. Topstep XFA ──
acfg, sig = setup(XFA, "projectx")
t = fresh(acfg, sig, 12_000.0)
t._payout_check("NQ", "projectx")
ck("2a 잔고 $12,000 = DM 한 통", len(t.dms) == 1 and t.dms[0][0].startswith("payout"), t.dms)
_k, _dko, _den = t.dms[0] if t.dms else ("", "", "")
ck("2b DM에 잔고 숫자 없음, 규칙($10,000, $5,000)과 직접 신청 문장", "$12,000" not in _dko and "$12,000" not in _den
   and "$10,000" in _dko and "$5,000" in _dko and "직접 신청" in _dko and "앱은 출금하지 않습니다" in _dko
   and ("…" + XFA[-4:]) in _dko and XFA not in _dko, _dko)
ck("2c 로그에 잔고와 금액, 자격 확인 문장", t.logs and "$12,000" in t.logs[-1] and "$5,000" in t.logs[-1] and "Topstep 화면" in t.logs[-1], t.logs[-1:])
ck("2d 영어 DM", "it is time to request a payout" in _den and "$12,000" not in _den, _den)
t._payout_check("NQ", "projectx")
ck("2e 같은 날 두 번째 점검 = 더 안 보냄", len(t.dms) == 1, len(t.dms))
t2 = fresh(acfg, sig, 12_000.0, keep=True)
t2._payout_check("NQ", "projectx")
ck("2f 재시작(새 앱, 상태 파일) 뒤에도 3일 안에는 안 보냄", len(t2.dms) == 0, t2.dms)
_key = E.App._daycap_key("projectx", XFA)
st = t2._dc_state()
st["payout_note"][_key] = float(st["payout_note"][_key]) - E._PAYOUT_EVERY_S - 1
t2._payout_check("NQ", "projectx")
ck("2g 3일이 지나고 조건이 이어지면 다시 한 통", len(t2.dms) == 1, t2.dms)

acfg, sig = setup(XFA, "projectx")
t = fresh(acfg, sig, 9_500.0)
t._payout_check("NQ", "projectx")
ck("2h 잔고 $9,500 = 말하지 않고 기록도 안 남김", not t.dms and not t.logs and not t._dc_state()["payout_note"].get(_key + "x"), (t.dms, t.logs))

# ── 3. 말하지 않는 경우 ──
for name, (aid, bk, prop, manual, bal) in {
    "3a 프롭을 끈 줄": (XFA, "projectx", {"on": False, "set": True}, False, 20_000.0),
    "3b 평가 계좌(150KTC)": (TS150, "projectx", None, False, 200_000.0),
    "3c 수동 모드 줄": (XFA, "projectx", None, True, 20_000.0),
    "3d 잔고를 못 읽음": (XFA, "projectx", None, False, RuntimeError("down")),
    "3e Lucid 50K(정본 없음)": (LDF50, "nt8", None, False, 70_000.0),
}.items():
    _home = tempfile.mkdtemp(prefix="eq_1109d_x_")
    os.environ["HOME"] = os.environ["APPDATA"] = _home
    acfg, sig = setup(aid, bk, prop, manual)
    t = fresh(acfg, sig, bal)
    t._payout_check("NQ", bk)
    ck(name + " = DM, 로그 없음", not t.dms and not t.logs, (t.dms, t.logs))
os.environ["HOME"] = os.environ["APPDATA"] = HOME

# 다른 자산, 다른 브로커로 부르면 이 줄을 보지 않는다
acfg, sig = setup(XFA, "projectx", asset="GC")
_h2 = tempfile.mkdtemp(prefix="eq_1109d_y_")
os.environ["HOME"] = os.environ["APPDATA"] = _h2
t = fresh(acfg, sig, 20_000.0)
t._payout_check("NQ", "projectx")
t._payout_check("GC", "nt8")
t._payout_check("GC", "bybit")
ck("3f 다른 자산(NQ), 다른 브로커(nt8, bybit) 점검은 GC Topstep 줄을 보지 않음", not t.dms, t.dms)
t._payout_check("GC", "projectx")
ck("3g 같은 자산, 브로커 점검에서 한 통", len(t.dms) == 1, t.dms)

# ── 4. Lucid ──
_h3 = tempfile.mkdtemp(prefix="eq_1109d_z_")
os.environ["HOME"] = os.environ["APPDATA"] = _h3
acfg, sig = setup(LDF, "nt8")
t = fresh(acfg, sig, 158_000.0)
ck("4a LDF150 = 펀디드, 규모 150K", t._daycap_stage_cfg("nt8", LDF, dict(E._PROP_DEFAULTS, **PROP_ON)) == ("funded", 150_000),
   t._daycap_stage_cfg("nt8", LDF, dict(E._PROP_DEFAULTS, **PROP_ON)))
t._payout_check("NQ", "nt8")
_k, _dko, _den = t.dms[0] if t.dms else ("", "", "")
ck("4b Lucid 이익 $8,000 = DM 한 통, 숫자는 규칙($4,600, $500)만", len(t.dms) == 1 and "$3,400" not in _dko and "$158,000" not in _dko
   and "$8,000" not in _dko and "$4,600" in _dko and "$500" in _dko and "로그에 있습니다" in _dko, t.dms)
ck("4c 로그에 잔고, 이익, 출금 금액", t.logs and "$158,000" in t.logs[-1] and "$8,000" in t.logs[-1] and "$3,400" in t.logs[-1], t.logs[-1:])
t = fresh(acfg, sig, 155_000.0)
_st = t._dc_state()
_st["payout_note"] = {}
t._payout_check("NQ", "nt8")
ck("4d Lucid 이익 $5,000(넘는 부분 $400) = 아직", not t.dms, t.dms)

# ── 5. 영어 로그 ──
_h4 = tempfile.mkdtemp(prefix="eq_1109d_w_")
os.environ["HOME"] = os.environ["APPDATA"] = _h4
acfg, sig = setup(XFA, "projectx")
t = fresh(acfg, sig, 10_000.0, lang="en")
t._payout_check("NQ", "projectx")
ck("5a 영어 로그", t.logs and "Payout reminder" in t.logs[-1] and "$10,000" in t.logs[-1], t.logs[-1:])

# ── 6. 진입 전 점검에 붙음 ──
src = inspect.getsource(E.App._precheck_run)
ck("6a 진입 전 점검이 증거금 경고 다음에 출금 알림을 부르고, 실패해도 점검 결과는 그대로(try)",
   "self._payout_check(asset, cfg.get(\"broker\"))" in src
   and src.index("_margin_precheck(") < src.index("_payout_check(")
   and "try:" in src[src.index("_margin_precheck("):src.index("_payout_check(")])
ck("6b 상태 파일 키 payout_note", "\"payout_note\"" in inspect.getsource(E.App._dc_state))

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}: {FAIL}"))
sys.exit(1 if FAIL else 0)
