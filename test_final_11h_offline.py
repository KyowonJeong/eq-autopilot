#!/usr/bin/env python3
# EQ Autopilot 2026.11.11h - 최종 묶음 수리(2026-11-11) 앱 쪽 오프라인 시험. 0 = 통과.
# =========================
# 어댑터 쪽(Bitget 트리거 손절, 청산 뒤 손절 정리, 계정 유형 판별 정책, 계정 번호)은 test_bitget_uta_offline.py F, D 절.
# 여기: 1 거래소 계정 번호 클레임, 3 BTC 브로커 둘의 손절 기억, 4 eval_target DM 숫자 없음, 7 OKX 50123 범위, 8 저장 유형,
#       9 시각표 미룸과 지난 청산, 10 BTC 블록 표, 11 BTC 미확인 감시 26시간, 14 not_sent, 15 unconfirmed, 22 비밀 지우기,
#       23 OKX 앱 종목만 BTCUSDT, 대표 2026-11-11 UTA 테스트 중 안내와 손절 이동 실패 DM 포지션마다 한 번.
# 사용: HOME=$(mktemp -d) EQ_NO_ALERT=1 python executor/test_final_11h_offline.py
# =========================
import datetime as _dt
import inspect
import json
import os
import sys
import tempfile
import time
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["EQ_NO_ALERT"] = "1"
import eqgui as E  # noqa: E402
import eqexec.broker.okx as OK  # noqa: E402
import eqexec.broker.bybit as BY  # noqa: E402
from eqexec.config import OkxCfg  # noqa: E402

fails = []


def ck(name, cond, info=None):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond or info is None else f"   -> {info!r}"[:700]))
    if not cond:
        fails.append(name)


class Stub:
    lang = "ko"

    def __init__(self):
        self.logs, self.alerts, self.evs, self.errs, self.remembered = [], [], [], [], []
        self._open_ctx = {}

    def log(self, m):
        self.logs.append(str(m))

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def _send_ev(self, kind, asset, **kw):
        self.evs.append((kind, asset, kw))

    def _report_error(self, where, err, member_dm=True):
        self.errs.append((where, str(err), member_dm))

    def _remember_stop(self, asset, stop):
        self.remembered.append((asset, stop))

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


SRC = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()

# ── 1 크립토 진입 클레임 = 거래소 계정 번호(모르면 키 지문), 진입 길에서는 묻지 않는다 ───────────────────
E._CRYPTO_UID.clear()


class FakeB:
    name = "okx"

    def __init__(self, uid, key="K1"):
        self.uid, self.key, self.n = uid, key, 0

    def account_uid(self):
        self.n += 1
        return self.uid


ck("1a 계정 번호를 모르면 종전 키 기준", E._crypto_claim_id("okx", "K1", "lbl") == "okx|K1")
fb = FakeB("777")
E._crypto_uid_learn("okx", "K1", fb)
E._crypto_uid_learn("okx", "K1", fb)
ck("1b 연결 확인에서 읽어 두면 두 번째 클레임 열쇠 = 계정 번호(한 키는 한 번만 묻는다), 첫 클레임은 늘 키(검증 P1-1)",
   E._crypto_claim_id("okx", "K1", "lbl") == "okx|K1" and E._crypto_claim_uid("okx", "K1") == "okx|uid:777" and fb.n == 1,
   (E._crypto_claim_id("okx", "K1", "lbl"), E._crypto_claim_uid("okx", "K1"), fb.n))
E._crypto_uid_learn("okx", "K2", FakeB("777", "K2"))
ck("1c 같은 거래소 계정에 PC마다 다른 키 = 같은 번호 열쇠(이중 진입 막음), 번호를 모르면 None",
   E._crypto_claim_uid("okx", "K2") == E._crypto_claim_uid("okx", "K1") and E._crypto_claim_uid("okx", "K9") is None)
ck("1d 키가 비면 행 이름, 다른 거래소는 따로", E._crypto_claim_id("okx", "", "lbl") == "lbl"
   and E._crypto_claim_id("bybit", "K1", "lbl") == "bybit|K1")
ck("1e 키 원문을 기억에 두지 않는다(지문만)", "K1" not in json.dumps([list(k) for k in E._CRYPTO_UID]))
E._crypto_uid_learn("okx", "K3", FakeB(None, "K3"))
ck("1f 읽기 실패 = 키 기준 그대로(번호 클레임 없음)", E._crypto_claim_id("okx", "K3", "l") == "okx|K3"
   and E._crypto_claim_uid("okx", "K3") is None)

# 1k 두 기기 시뮬레이션(검증 sim_claim 경우 전부): 실제 진입 경로(_enter_account)와 서버 클레임 규칙(web/entry_claims - 같은 열쇠는 먼저 잡은
# 기기만, 같은 기기의 다시 잡기는 허가). 각 경우 정확히 한 기기만 주문하고, 둘 다 양보하는 경우가 없어야 한다.
import hashlib as _hl  # noqa: E402


class _Srv:
    def __init__(self):
        self.db = {}

    def claim(self, sig, cid, mid):
        k = (str((sig or {}).get("id")), _hl.sha256(str(cid).strip().lower().encode()).hexdigest()[:12])
        cur = self.db.get(k)
        if cur and cur != mid:
            return False
        self.db[k] = mid
        return True


class _CBg:
    name = "bitget"

    def __init__(self, key):
        self.key, self.placed = key, []

    def _symbol(self, s):
        return "BTCUSDT"

    def current_market_price(self, s):
        return 90000.0

    def list_open_positions(self):
        return []

    def place_entry(self, **k):
        self.placed.append(k)
        return {"entry": {"orderId": "1"}, "stop": True}

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


class _Dev(Stub):
    lang = "en"

    def __init__(self, mid, srv):
        Stub.__init__(self)
        self.mid, self.srv, self._token = mid, srv, "tok"

    def _live_now(self, f):
        return True

    def _entry_skip_ev(self, a, why):
        self.evs.append(("skip", a, {"why": why}))

    def _claim_entry(self, sig, aid, **k):
        return self.srv.claim(sig, aid, self.mid)

    def _dm_kind(self, *a):
        return a[0]


for _n in ("_enter_account", "_mark_entering"):
    setattr(_Dev, _n, E.App.__dict__[_n])
_bb0, _cb0 = E._build_broker, getattr(E, "_cross_basis_for", None)
E._cross_basis_for = lambda *a, **k: 0.0


def _dev_run(dev, key, uid, sig):
    E._CRYPTO_UID.clear()
    if uid:
        E._CRYPTO_UID[("bitget", _hl.sha256(key.encode()).hexdigest()[:16])] = uid
    b = _CBg(key)
    E._build_broker = lambda *a, **k: b
    cfg = {"broker": "bitget", "f1": key, "f2": "SEC", "f3": "PP", "acct": "", "one_r": 300.0, "live": True,
           "label": "Bitget", "prop": {}, "pct": {}, "one_lim": 0.0}
    dev._enter_account(cfg, sig, "BTC", "LONG", 89000.0, 1.0)
    return len(b.placed)


_cases = [("uid A 먼저, B 번호 모름(같은 키)", ("A", "KEY1", "U1"), ("B", "KEY1", None)),
          ("번호 모르는 B 먼저, A uid", ("B", "KEY1", None), ("A", "KEY1", "U1")),
          ("11g(키만) 먼저, 11h(키+번호)", ("G", "KEY1", None), ("H", "KEY1", "U1")),
          ("11h 먼저, 11g", ("H", "KEY1", "U1"), ("G", "KEY1", None)),
          ("다른 키, 같은 계정(둘 다 번호)", ("A", "KEY1", "U1"), ("C", "KEY2", "U1")),
          ("다른 키, 같은 계정(순서 바꿈)", ("C", "KEY2", "U1"), ("A", "KEY1", "U1")),
          ("같은 키, 둘 다 번호", ("A", "KEY1", "U1"), ("D", "KEY1", "U1"))]
_res = []
for _i, (_nm, _d1, _d2) in enumerate(_cases):
    _srv = _Srv()
    _sig = {"id": f"S-{_i}", "instrument": "BTC", "entry_ref": 90000.0, "stop_price": 89000.0, "direction": "LONG",
            "published_at": time.time()}
    _n1 = _dev_run(_Dev(_d1[0], _srv), _d1[1], _d1[2], _sig)
    _n2 = _dev_run(_Dev(_d2[0], _srv), _d2[1], _d2[2], _sig)
    _res.append((_nm, _n1, _n2))
E._build_broker = _bb0
if _cb0 is not None:
    E._cross_basis_for = _cb0
E._CRYPTO_UID.clear()
ck("1k 두 기기 7가지(번호 있음/없음, 11g/11h, 다른 키 같은 계정): 매번 정확히 한 기기만 주문(둘 다 진입도, 둘 다 양보도 없음)",
   all(n1 + n2 == 1 for _nm, n1, n2 in _res), _res)
_ent0 = inspect.getsource(E.App._enter_account)
ck("1l 진입 경로: 키 클레임 다음 번호 클레임(차례로), 하나라도 잡혔으면 양보",
   "if not self._claim_entry(sig, _claim_id):" in _ent0 and "return self._claim_entry(sig, _claim_uid)" in _ent0
   and _ent0.index("self._claim_entry(sig, _claim_id)") < _ent0.index("self._claim_entry(sig, _claim_uid)"))
_ent = inspect.getsource(E.App._enter_account)
ck("1g 진입 경로는 계정 번호를 묻지 않는다(기억만) - 연결 테스트, 사전점검, 라이브 시작, 연결 감시가 작업 스레드에서 읽는다",
   "account_uid" not in _ent and "_crypto_uid_learn" not in _ent
   and "_crypto_uid_learn" in inspect.getsource(E.App._conn_check)
   and "_crypto_uid_learn" in inspect.getsource(E.App._precheck_run)
   and "_crypto_uid_learn" in inspect.getsource(E.App._conn_watch_run)
   and "_crypto_uid_learn" in inspect.getsource(E.App.healthcheck))


class _BQ:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    def __call__(self, m, path, params=None, body=None):
        self.calls.append((m, path))
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


_by = BY.BybitBroker.__new__(BY.BybitBroker)
_by._req = _BQ({"id": "1", "userID": 24617703, "apiKey": "x"})
ck("1h Bybit 계정 번호 = GET /v5/user/query-api의 userID", _by.account_uid() == "24617703"
   and _by._req.calls == [("GET", "/v5/user/query-api")], _by._req.calls)
_by._req = _BQ(RuntimeError("net"))
ck("1i Bybit 실패 = None", _by.account_uid() is None)
_ok = OK.OkxBroker.__new__(OK.OkxBroker)
_ok._config = lambda: {"uid": "44705892343619584", "acctLv": "2"}
ck("1j OKX 계정 번호 = account/config의 uid", _ok.account_uid() == "44705892343619584")


# ── 3 BTC 브로커 둘: 기억(_open_ctx['BTC']) 브로커의 이동만 기억에 적는다 ────────────────────────────
class Pos:
    def __init__(self, sym="BTCUSDT", avg=90000.0):
        self.symbol, self.raw = sym, {"avgPrice": str(avg)}


class Brk:
    def __init__(self, name, key, uta=False, err=None):
        self.name, self.key, self._uta, self._err, self.moves = name, key, uta, err, []

    def set_stop(self, sym, px):
        self.moves.append(px)
        return {"error": self._err}

    def account_type(self):
        return "uta" if self._uta else "classic"


s = Stub()
_bybit, _bitget = Brk("bybit", "KB"), Brk("bitget", "KG")
s._open_ctx["BTC"] = {"b": Brk("bybit", "KB"), "stop": 85000.0}
E.App._btc_move_stop_be(s, _bitget, Pos(), True, target=90100.0)
ck("3a 기억 브로커가 아닌 브로커(Bitget)가 옮기면 기억에 적지 않는다(거짓 '손으로 옮김' 알림 방지)",
   _bitget.moves == [90100.0] and not s.remembered, s.remembered)
E.App._btc_move_stop_be(s, _bybit, Pos(), True, target=90100.0)
ck("3b 기억 브로커(같은 이름, 같은 키, 다른 인스턴스)가 옮기면 적는다", s.remembered == [("BTC", 90100.0)], s.remembered)

# 대표 2026-11-11: UTA 손절 이동 실패 DM은 포지션마다 한 번, 로그는 매번
Stub._stop_move_alert = E.App.__dict__["_stop_move_alert"]
s = Stub()
_u = Brk("bitget", "KU", uta=True, err="modify: code=25580 x / trigger: code=40017 y")
for _tg in (90100.0, 90200.0, 90300.0):
    E.App._btc_move_stop_be(s, _u, Pos(), True, target=_tg)
_smf = [a for a in s.alerts]
_fl = [l for l in s.logs if "손절 이동 실패" in l]
ck("3c Bitget 통합 계정: 손절 이동 실패 회원 알림은 포지션마다 한 번(블록 세 번에 한 번), 로그는 매번",
   len(_fl) == 3 and len(_smf) == 1, (len(_fl), len(_smf)))
E.App._btc_move_stop_be(s, _u, Pos(avg=91000.0), True, target=91100.0)
ck("3d 다른 포지션(새 진입)이면 다시 한 번", len(s.alerts) == 2, len(s.alerts))
s = Stub()
_c = Brk("bitget", "KC", uta=False, err="Bitget HTTP 400: code=40017")
for _tg in (90100.0, 90200.0):
    E.App._btc_move_stop_be(s, _c, Pos(), True, target=_tg)
ck("3e 클래식 키는 종전대로(실패마다 알림)", len(s.alerts) == 2, len(s.alerts))

# ── 대표 2026-11-11: UTA 키에만 '테스트 중' 안내 한 줄 ─────────────────────────────────────────────
ck("U1 UTA 판별 = 'Bitget 통합 계정: 테스트 중, 오류 보상 없음(약관상 책임 그대로)' 한 줄(KO / EN), 클래식, 다른 브로커는 아님",
   E._bg_is_uta(Brk("bitget", "k", uta=True)) and not E._bg_is_uta(Brk("bitget", "k", uta=False))
   and not E._bg_is_uta(Brk("bybit", "k", uta=True))
   and E._BG_UTA_TESTING_KO == "Bitget 통합 계정: 테스트 중인 지원입니다. 이 기능의 오류는 보상(한 달 무료, 할인 쿠폰) 대상이 아닙니다(약관상 책임은 그대로)."
   and E._BG_UTA_TESTING_EN == ("Bitget unified account: support is in testing. Errors in this feature are not eligible for "
                                "compensation (a free month or a discount coupon); our liability under the Terms is unchanged."))
ck("U2 연결 테스트(버튼)와 라이브 시작 연결 확인(_conn_check)에 그 줄",
   "_BG_UTA_TESTING_KO" in inspect.getsource(E.App.healthcheck) and "_BG_UTA_TESTING_KO" in inspect.getsource(E.App._conn_check))

# ── 4 eval_target 회원 DM에는 잔고, 통과 금액이 없다(알림 창과 로그에는 있다) ─────────────────────────


class DcStub(Stub):
    def __init__(self):
        Stub.__init__(self)
        self._st = {"pass_hold": {}, "names": {}, "sizes": {}, "passed": {}}
        self._maps = {}
        self.ui = []

    def _dc_state(self):
        return self._st

    def _dc_map(self, n):
        return self._maps.setdefault(n, {})

    def _ui(self, fn, ms=0):
        self.ui.append(fn)


for _n in ("_daycap_key", "_daycap_money", "_dm_kind"):
    setattr(DcStub, _n, E.App.__dict__[_n])
s = DcStub()
E.App._dc_pass_target_notice(s, "projectx", "150KTC-V2-12345-5604", "…5604", 159123.0, 159050.0, "consistency", "Topstep",
                             size=150000)
_et = [a for a in s.alerts if a[0].startswith("eval_target")]
ck("4a eval_target DM: 잔고, 통과 금액 숫자 없음($, 159…) - 말로만(KO / EN)",
   len(_et) == 1 and "$" not in _et[0][1] + _et[0][2] and "159" not in _et[0][1] + _et[0][2]
   and "통과 목표 잔고에 닿았습니다" in _et[0][1] and "pass target balance was reached" in _et[0][2], _et)
_src_n = inspect.getsource(E.App._dc_pass_target_notice)
ck("4b 알림 창 문장에는 숫자가 그대로(_hk에 잔고, 통과 금액)", "(잔고 {_bal}, 통과 금액 {_need})" in _src_n)

# ── 7 OKX 50123은 OKX의 code= / sCode=만 ──────────────────────────────────────────────────────────
ck("7a NT8 계좌 번호의 50123은 OKX 안내가 아니다",
   not E._is_okx_50123("NT8 account LDI15012345670001 disconnected", "nt8")
   and "NinjaTrader" in E._preflight_hint_dm("NT8 account LDI15012345670001 disconnected", "nt8")[1])
ck("7b OKX 거절 sCode=50123 / code=50123 = OKX 안내",
   E._is_okx_50123("okx /api/v5/trade/order failed: code=1 sCode=50123 This API Key does not have trading permission")
   and E._is_okx_50123("OKX /api/v5/trade/order HTTP 400: code=50123 x", "okx")
   and E._entry_fail_hint_dm("OKX /api/v5/trade/order failed: code=1 sCode=50123 x")[0] == E._OKX_50123_KO)
ck("7c 다른 거래소, 다른 숫자에 섞인 50123은 아니다",
   not E._is_okx_50123("Bybit order 150123 rejected code=10001") and not E._is_okx_50123("OKX order id 501234 failed: code=51000")
   and not E._is_okx_50123("OKX code=50123", "bybit"))
ck("7d OKX 연결 테스트, 라이브 시작 확인, 사전점검 통과에 키 코인 목록 안내 한 줄",
   'self.log("   ℹ OKX: " + (_OKX_50123_KO' in inspect.getsource(E.App.healthcheck)
   and 'self.log("   ℹ OKX: " + (_OKX_50123_KO' in inspect.getsource(E.App._conn_check)
   and 'self.log("   ℹ OKX: " + (_OKX_50123_KO' in inspect.getsource(E.App._precheck_run))

# ── 8 계좌 이름이 단계를 정하면 저장 유형(prop.type)도 그 단계(21d로 되돌려도 맞는 1R) ─────────────────
_orig_auto = E.App._prop_auto_type
E.App._prop_auto_type = lambda self, bk, acct, pr: ("funded" if "EXPRESS" in str(acct) else
                                                    ("test" if "KTC" in str(acct) else None))
s = Stub()
s._acct_broker = lambda asset, row: "projectx"
_rows = [{"id": "EXPRESS-V2-1-0001", "prop": {"on": True, "type": "test"}},
         {"id": "EXPRESS-V2-1-0002", "prop": {"on": True, "type": "test", "type_manual": True}},
         {"id": "EXPRESS-V2-1-0003", "prop": {"on": True, "type": "test", "from21d": True}},
         {"id": "123456", "prop": {"on": True, "type": "test"}},
         {"id": "50KTC-V2-1-0004", "prop": {"on": True, "type": "funded"}}]
for _r in _rows:
    E.App._prop_type_sync(s, "NQ", _r)
E.App._prop_auto_type = _orig_auto
ck("8a 이름이 단계를 정하면 type을 그 단계로(XFA → funded, Combine → test), 회원 고정, 21d 표식, 모르는 이름은 그대로",
   [r["prop"]["type"] for r in _rows] == ["funded", "test", "test", "test", "test"], [r["prop"]["type"] for r in _rows])
ck("8b 계좌 칸 저장(_collect_acct_widgets)이 행마다 부른다(계좌를 바꾼 줄 포함)",
   "self._prop_type_sync(self._asset, accts[idx])" in inspect.getsource(E.App._collect_acct_widgets))

# ── 9 시각표 바뀜: 마지막 진입 뒤 청산이 끝났으면 미루지 않는다(새 그리드 금 진입을 일찍 닫지 않게) ───────────
_now = time.time()
_ent_ts = _now - (25 * 3600 + 50 * 60)                     # 26시간 안(종전 규칙이면 '열려 있을 수 있음')
_x = _dt.datetime.fromtimestamp(_ent_ts + 3600, tz=_dt.timezone.utc)
_job = {"asset": "GC", "hour": _x.hour, "tz": "UTC", "acct": "A1"}
s = Stub()
s._entered_at = {"GC": _ent_ts}
s._entering_at = {}
s._auto_fired = {("GC", _x.hour, "A1"): _x.strftime("%Y-%m-%d")}
_old_exits = list(E._ASSET_EXITS.get("GC") or [])
E._ASSET_EXITS["GC"] = [("UTC", 0)]                        # 새 청산 시각은 오늘 이미 지남 → 종전 규칙이면 미룸
try:
    _d1 = E.App._sched_retime_defer(s, "GC", [_job])
    s._auto_fired = {("GC", _x.hour, "A1"): (_x - _dt.timedelta(days=1)).strftime("%Y-%m-%d")}   # 진입 전에 끝난 청산
    _d2 = E.App._sched_retime_defer(s, "GC", [_job])
finally:
    E._ASSET_EXITS["GC"] = _old_exits
ck("9a 어제 진입이 어제 청산됐으면 오늘 시각표 바뀜에서 청산 잡을 옛 시각에 묶지 않는다", _d1 is False, _d1)
ck("9b 진입 뒤에 끝난 청산이 없으면 종전대로 미룸(열려 있을 수 있음)", _d2 is True, _d2)

# ── 10 BTC 블록 표: 미뤄 둔 옛 청산 시각을 남기고, 미룸이 끝나면 지금 시각만 ────────────────────────────
_save = dict(E._BTC_BLOCK_K), list(E._ASSET_EXITS["BTC"])
_ref = E._BTC_BLOCK_K
E._ASSET_EXITS["BTC"] = [("UTC", (20 + 4 * (k + 1)) % 24) for k in range(6)]
E._btc_block_rebuild([("UTC", (22 + 4 * (k + 1)) % 24) for k in range(6)])
_both = dict(E._BTC_BLOCK_K)
E._btc_block_rebuild()
_new = dict(E._BTC_BLOCK_K)
E._BTC_BLOCK_K.clear(); E._BTC_BLOCK_K.update(_save[0]); E._ASSET_EXITS["BTC"] = _save[1]
ck("10a 미룸 동안 옛 시각(2, 6, …, 22)도 블록 표에(같은 dict 객체, 비우지 않고 바꿈)",
   all(_both.get(h) == k for k, h in enumerate([2, 6, 10, 14, 18, 22])) and all(h in _both for h in (0, 4, 8, 12, 16, 20))
   and _ref is E._BTC_BLOCK_K, _both)
ck("10b 미룸이 끝나면 지금 시각만", _new == {0: 0, 4: 1, 8: 2, 12: 3, 16: 4, 20: 5}, _new)
ck("10c 시각표 바뀜 뒤 청산 잡 옮기기가 끝나면 BTC 표를 미룸 기록으로 다시 짠다, 청산 잡 표는 잠금 안에서",
   "_btc_block_rebuild((App._sched_pend_map(self).get(\"BTC\") or {}).get(\"exits\"))" in inspect.getsource(E.App._sched_retime)
   and "with _AUTO_ACCTS_LOCK:" in inspect.getsource(E.App._sched_retime)
   and "with _AUTO_ACCTS_LOCK:" in inspect.getsource(E.App._master_stop))
ck("18 .sched.json은 원자 저장", "_write_text_atomic(_SCHED_PATH" in inspect.getsource(E.App._sched_apply)
   and 'open(_SCHED_PATH, "w"' not in inspect.getsource(E.App._sched_apply))

# ── 11 미확인 진입 감시 수명: BTC 26시간, 그 밖 12시간 ────────────────────────────────────────────────
E._UNCONF_PATH = os.path.join(tempfile.mkdtemp(prefix="eq11h_"), ".unconf_watch.json")
with open(E._UNCONF_PATH, "w", encoding="utf-8") as f:
    json.dump({"BTC|bitget|BTCUSDT": {"asset": "BTC", "ts": time.time() - 20 * 3600},
               "NQ|1|CON.F.US.MNQ": {"asset": "NQ", "ts": time.time() - 20 * 3600},
               "BTC|bybit|BTCUSDT": {"asset": "BTC", "ts": time.time() - 27 * 3600}}, f)
_lw = E._load_unconf_watch()
ck("11a 재시작 복구: 20시간 된 BTC 감시는 남고(24시간 보유), NQ는 12시간 넘어 버리고, 27시간 된 BTC도 버린다",
   sorted(_lw) == ["BTC|bitget|BTCUSDT"] and E._unconf_max_age("btc") == 26 * 3600 and E._unconf_max_age("GC") == 12 * 3600,
   sorted(_lw))
_cu = inspect.getsource(E.App._check_unconf_watch)
ck("11b 감시를 놓는 로그가 BTC에는 '최대 보유 24시간'(세션 마감 아님)", "최대 보유 24시간이 지났습니다" in _cu
   and "the 24 h maximum hold has passed" in _cu)

# ── 14 'not sent:' = 사유 not_sent(거절 아님), 앱 알림 하나(서버는 DM하지 않는다) ───────────────────────
ck("14a 'not sent:'는 거절도 모름도 아님(주문이 앱을 떠나지 않음)",
   not E._entry_err_unknown("not sent: OKX instrument spec unavailable: x")
   and not E._entry_err_unknown("not sent: Bitget account is changing between Classic and Unified Trading Account"))
_k1, _e1 = E._not_sent_why_dm("not sent: Bitget account is changing between Classic and Unified Trading Account - nothing was sent")
_k2, _e2 = E._not_sent_why_dm("not sent: OKX instrument spec unavailable: timeout")
ck("14b DM 이유: Bitget 유형 바뀌는 중 / OKX 종목 정보 못 받음(KO / EN), 원문 없음",
   "바뀌는 중" in _k1 and "switching" in _e1 and "종목 정보" in _k2 and "instrument details" in _e2 and "timeout" not in _k2 + _e2)
_i_ns = _ent.index("App._entry_not_sent(self, asset, cfg, lbl, _em0)")
_ns_src = inspect.getsource(E.App._entry_not_sent)
ck("14c 진입 경로: 'not sent:' 갈래가 거절(rejected) 갈래 앞, 서버 DM 없이(member_dm=False) 앱 알림 entry_notsent",
   _i_ns < _ent.index('self._send_ev("entry_fail", asset, why="rejected"') and '_em0.startswith("not sent:")' in _ent
   and 'why="not_sent"' in _ns_src and "member_dm=False" in _ns_src and '"entry_notsent"' in _ns_src)


# 14e(검증 P2-3): 실제 Bitget '바뀌는 중'은 주문 전 포지션 조회에서 BitgetModeError - 일반 오류(why error, 서버 DM)가 아니라 not_sent
class _SwBg(_CBg):
    def __init__(self, key, mode):
        _CBg.__init__(self, key)
        self.mode = mode

    def list_open_positions(self):
        if self.mode == "switching":
            raise _BGM("Bitget account is changing between Classic and Unified Trading Account - nothing was sent; "
                       "the app checks again within a minute")
        return []

    def place_entry(self, **k):
        self.placed.append(k)
        return {"error": "not sent: Bitget account is changing between Classic and Unified Trading Account - nothing was sent",
                "stop_attached": False}


from eqexec.broker.bitget import BitgetModeError as _BGM  # noqa: E402
E._cross_basis_for = lambda *a, **k: 0.0
_ns_res = []
for _mode in ("switching", "place_not_sent"):
    _d = _Dev("M", _Srv())
    _b = _SwBg("KEY9", _mode)
    E._build_broker = lambda *a, **k: _b
    _d._enter_account({"broker": "bitget", "f1": "KEY9", "f2": "S", "f3": "P", "acct": "", "one_r": 300.0, "live": True,
                       "label": "Bitget", "prop": {}, "pct": {}, "one_lim": 0.0},
                      {"id": "S-NS", "instrument": "BTC", "entry_ref": 90000.0, "stop_price": 89000.0, "direction": "LONG",
                       "published_at": time.time()}, "BTC", "LONG", 89000.0, 1.0)
    _ns_res.append((_mode, len(_b.placed), [(e[0], e[2].get("why")) for e in _d.evs], [x[2] for x in _d.errs],
                    [a[0] for a in _d.alerts]))
E._build_broker = _bb0
if _cb0 is not None:
    E._cross_basis_for = _cb0
ck("14e Bitget '바뀌는 중'이 포지션 조회에서 나도, 주문이 'not sent:'로 끝나도: why not_sent, 서버 DM 없음, 앱 알림 entry_notsent 하나",
   _ns_res[0][1] == 0 and all(r[2] == [("entry_fail", "not_sent")] and r[3] == [False] and r[4] == ["entry_notsent"]
                              for r in _ns_res), _ns_res)
OK.requests.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))
_okb = OK.OkxBroker(OkxCfg(api_key="K", api_secret="S", passphrase="P", demo=False, host=""))
_okb._market = lambda: ("SWAP", "BTC-USDT-SWAP")
_okb._spec = lambda inst: (_ for _ in ()).throw(RuntimeError("spec down"))
_re = _okb.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=80000, custom_tag="EQ-AP-1", dry_run=False)
ck("14d OKX 종목 정보를 못 받으면 'not sent:'(주문 없음)", str(_re.get("error", "")).startswith("not sent: OKX instrument spec"),
   _re)

# ── 15 선물 결과 모름: 사유 unconfirmed 하나, 서버 일반 DM 없음, 앱 알림 하나 ─────────────────────────────
_fut = inspect.getsource(E.App._run_futures_entry)
ck("15 선물 결과 모름: entry_fail why=unconfirmed, 예외의 서버 DM은 실주문 '모름'에서 끔(확정 거절만 종전대로)",
   'why="unconfirmed"' in _fut and "member_dm=not (live and not _hard_rej)" in _fut
   and _fut.index('why="unconfirmed"') < _fut.index('"entry_unconfirmed"'))

# ── 16 서버 NO TRADE 사유 ───────────────────────────────────────────────────────────────────────
ck("16 no_trade_reason app_update_needed = 다른 PC 옛 버전 안내(KO / EN), 모르는 코드는 원문",
   "옛 버전" in E._ntr_why_text("app_update_needed", True) and "old app version" in E._ntr_why_text("app_update_needed", False)
   and E._ntr_why_text("x_y", True) == "사유(서버): x_y"
   and "no_trade_reason" in inspect.getsource(E.App._sig_loop_body) and "_ntr_note" in inspect.getsource(E.App._sched_paint))

# ── 22 서버로 나가는 글에서 이 PC의 브로커 비밀을 지운다 ─────────────────────────────────────────────
E._REDACT.clear()
E._redact_note("bybit", "ABCDEFGH12345678", "SuperSecretValue99", "0")
E._redact_note("bitget", "BgKey00000000001", "BgSecret00000001", "BgPassphrase01")
ms = Stub()
ms._acfg = {"BTC": {"accounts": [], "creds": {"okx": {"f1": "OkxApiKey1234567", "f3": "OkxPassphrase9"}}}}
_out = E.App._mask_ids(ms, "Bybit 10004 error sign! origin_string[1731300000000ABCDEFGH12345678 5000{...}] "
                           "BgPassphrase01 SuperSecretValue99 OkxApiKey1234567 OkxPassphrase9")
ck("22a _mask_ids(회원 알림, 이벤트, 오류 보고 공통): API Key, Secret, Passphrase 지움(Bybit retMsg가 되돌린 키 포함)",
   all(x not in _out for x in ("ABCDEFGH12345678", "SuperSecretValue99", "BgPassphrase01", "OkxApiKey1234567", "OkxPassphrase9"))
   and "[secret]" in _out and "Bybit 10004" in _out, _out)
ck("22b 테스트넷 칸('0') 같은 짧은 값은 건드리지 않는다", "0" in E._redact_secrets("code 0") and "[secret]" not in E._redact_secrets("0"))
ms2 = Stub()
ms2._acfg = {}
ms2.__class__._ACCT_LIKE = None
try:
    _lg = E.App._mask_log(ms2, "❌ entry failed: retMsg origin_string[ABCDEFGH12345678]")
except Exception as _e:
    _lg = repr(_e)
ck("22c 앱 로그(화면, 로그 보내기)도 비밀을 지운다", "ABCDEFGH12345678" not in _lg, _lg)
_b0 = E._build_broker("bybit", "NewBybitKey00001", "NewBybitSecret01", "0", [])
ck("22d 어댑터를 만들 때(_build_broker) 그 키, 시크릿을 지울 목록에 넣는다",
   "NewBybitKey00001" in E._REDACT and "NewBybitSecret01" in E._REDACT)


# ── 23 OKX: 앱 종목(_market)만 BTCUSDT, 다른 BTC 스왑은 'OKX:원래 이름'(자산별 BTC 청산이 건드리지 않음) ───────────
class _R:
    def __init__(self, obj):
        self.status_code, self._o, self.text = 200, obj, json.dumps(obj)

    def json(self):
        return self._o


def _okx_get(url, headers=None, timeout=None, params=None):
    p = urlsplit(url).path
    if p == "/api/v5/account/positions":
        return _R({"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "pos": "3", "posSide": "net", "avgPx": "90000"},
                                         {"instId": "BTC-USDC-SWAP", "pos": "-2", "posSide": "net", "avgPx": "90010"},
                                         {"instId": "BTC-USD-SWAP", "pos": "1", "posSide": "net", "avgPx": "90020"}]})
    return _R({"code": "0", "data": []})


OK.requests.get = _okx_get
_okb = OK.OkxBroker(OkxCfg(api_key="K", api_secret="S", passphrase="P", demo=False, host=""))
_okb._market = lambda: ("SWAP", "BTC-USDT-SWAP")
_okb._host_ok = True
_okb._to_coin = lambda inst, n: n * 0.01
_okb._warm = lambda: None
_ps = _okb.list_open_positions()
from eqexec import asset_scope as _asc  # noqa: E402
_m = _asc.matcher("BTC")
ck("23a OKX 포지션: 앱 종목만 BTCUSDT, 회원의 다른 BTC 스왑은 OKX:원래 이름",
   [p.symbol for p in _ps] == ["BTCUSDT", "OKX:BTC-USDC-SWAP", "OKX:BTC-USD-SWAP"], [p.symbol for p in _ps])
ck("23b 자산별 BTC 청산 범위(asset_scope)는 앱 종목만", [p.symbol for p in _ps if _m(p.symbol)] == ["BTCUSDT"],
   [p.symbol for p in _ps if _m(p.symbol)])
ck("23c X2+TR 판정은 BTCUSDT와 정확히 같은 포지션만", 'str(p.symbol).upper() == "BTCUSDT"' in inspect.getsource(E.App._btc_x2be_step)
   and '"BTC" in str(p.symbol).upper()' not in inspect.getsource(E.App._btc_x2be_step))

_sl_src = inspect.getsource(E.App._sig_loop_body)
_sp_src = inspect.getsource(E.App._sched_paint)
ck("16b 옛 버전 안내 줄: 같은 자산의 거래 신호(NO TRADE 아님)가 오면 지우고, 자산 이름은 신호의 자산(하드코딩 'Gold' 없음)(검증 P2-5)",
   "self._ntr_note = None" in _sl_src and "if not _ntr and" in _sl_src and '_an = str(_nn.get("asset") or "").upper()' in _sp_src
   and '"Gold: not entered' not in _sp_src and '"금: 다른 PC' not in _sp_src)
ck("21b 무장(_master_arm)의 청산 잡 표 쓰기도 같은 잠금 안(검증 P2-6)",
   __import__("re").search(r"with _AUTO_ACCTS_LOCK:[^\n]*\n\s+self\._auto_accts\[key\] = jobs", inspect.getsource(E.App._master_arm))
   is not None)
ck("V 앱 버전 2026.11.11h 이상", E.App._APP_VER >= "2026.11.11h", E.App._APP_VER)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
