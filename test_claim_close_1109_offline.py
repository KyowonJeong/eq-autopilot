# EQ Autopilot 2026.11.09a 오프라인 시험(2026-11-09 대표 실기기 시험 세 건). 0 = 통과. 네트워크 없음(서버 호출, 브로커 전부 가짜).
#   A 크립토 서버 클레임 = 행 이름이 아니라 그 행의 API Key 지문(맥북 OKX 유럽 계정과 윈도 OKX 한국 계정이 기본 라벨이 같아 서로 양보하던 것).
#     선물 클레임(계좌 id, 프로세스 난수)은 그대로.
#   B 양보(claim_yield), 잔여 청산 미확인(entry_fail), 구 지정가 경로 건너뜀이 서버에 사유를 보낸다(종전엔 조용히 끝남).
#   C 앱의 청산 버튼으로 닫힌 자산은 바로 청산 보고(why=manual) 한 번, 손절 감시는 두 번째 보고를 하지 않는다. 닫힌 게 없으면 보내지 않는다.
#     손절 감시 문구는 손절로 단정하지 않는다('손절 추정' → '손절 또는 직접 청산').
# 사용: cd executor && python3.9 test_claim_close_1109_offline.py
import hashlib
import inspect
import json
import os
import sys
import tempfile
import threading
from types import SimpleNamespace

os.environ["HOME"] = tempfile.mkdtemp()            # 앱 설정 폴더(APP_DIR)를 임시로 - 실제 설정, 원장을 건드리지 않는다
os.environ.setdefault("EQ_NO_ALERT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui as E  # noqa: E402
import autopilot_crypto  # noqa: E402
from eqexec.broker.base import FlattenResult, Position  # noqa: E402

fails = []


def ck(n, ok, got=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"  got={got!r}"[:600]))
    if not ok:
        fails.append(n)


def h12(s):
    return hashlib.sha256(str(s).strip().lower().encode()).hexdigest()[:12]


# ── 네트워크, 디스크 차단 ─────────────────────────────────────────────────────────────
E._OPENCTX_PATH = os.path.join(tempfile.mkdtemp(), ".open_ctx.json")
E._record_real_r = lambda *a, **k: None
E._mark_entered = lambda *a, **k: {}
E._ledger_add = lambda *a, **k: None
E._exit_note = lambda *a, **k: None
E._cross_basis_for = lambda *a, **k: 0.0
E._f2_load = lambda f1: "SECRET"
autopilot_crypto.path_id = lambda tok: "pid" if tok else ""
_real_post = E._rq_mod.post
E._rq_mod.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call in offline test"))
E._SRV_HTTP.post = E._rq_mod.post


class ClaimServer:
    """/eqclaim 흉내: (토큰, 신호, 계좌 해시) 선착 1기기(mid)만 허가, 같은 mid의 재요청은 허가."""

    def __init__(self):
        self.first, self.bodies = {}, []

    def post(self, url, **kw):
        assert url.endswith("eqclaim"), url
        b = dict(kw.get("json") or {})
        self.bodies.append(b)
        k = (b["tok_id"], b["sig_id"], b["acct_h"])
        own = self.first.setdefault(k, b["mid"])
        return SimpleNamespace(ok=True, text=("claim:ok" if own == b["mid"] else "claim:taken"))


SRV = ClaimServer()
E._srv_post = SRV.post


class FakeCrypto:
    """크립토 어댑터 흉내(OKX 모양): 브로커 심볼 'BTCUSDT', 손절은 주문에 붙는다."""
    name = "okx"

    def __init__(self, poss=(), close_ok=True):
        self.poss, self.close_ok = list(poss), close_ok
        self.placed, self.closed_calls = [], 0

    def _symbol(self, s):
        return "BTCUSDT"

    def current_market_price(self, s):
        return 100000.0

    def list_open_positions(self):
        return list(self.poss)

    def close_symbol(self, s, dry_run=False):
        self.closed_calls += 1
        return {"closed": self.close_ok, "error": None if self.close_ok else "request timed out"}

    def place_entry(self, **k):
        self.placed.append(k)
        return {"entry": {"orderId": "1"}, "stop": True}


class Stub:
    lang = "ko"
    _token = "tok"

    def __init__(self):
        self.logs, self.evs, self.fills = [], [], []
        self._open_assets, self._open_ctx, self._flat_seen = set(), {}, {}
        self._profile, self._skip_ev_at = {}, None       # _skip_ev_at: 앱처럼 처음엔 없음(가짜 __getattr__가 삼키지 않게)

    def log(self, m):
        self.logs.append(str(m))

    def _send_ev(self, kind, asset, **kw):
        self.evs.append((kind, asset, kw))

    def _send_fill(self, asset, closed=False, why=None, sid=None, followup=False):
        self.fills.append((asset, closed, why))

    def _live_now(self, v):
        return bool(v)

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_enter_account", "_claim_entry", "_entry_skip_ev", "_close_all_positions", "_manual_close_report",
           "_close_report_take", "_check_stop_closed", "_fill_asset", "_forget_open_ctx", "_persist_open_ctx",
           "_restore_open_ctx", "_APP_VER"):
    setattr(Stub, _n, E.App.__dict__[_n])

LABEL = "OKX (USDT/USDC perp)"          # 기본 라벨 - 두 기기가 같았다


def cfg(key, label=LABEL, broker="okx"):
    return {"broker": broker, "f1": key, "f2": "SECRET", "f3": "PP", "acct": "", "one_r": 100.0, "label": label,
            "live": True, "prop": {}, "pct": {}, "one_lim": 0.0}


def enter(machine, c, sid, b=None):
    E._machine_id = lambda: machine
    b = b or FakeCrypto()
    E._build_broker = lambda *a, **k: b
    me = Stub()
    sig = {"id": sid, "entry_ref": 100000.0, "published_at": None}
    me._enter_account(c, sig, "BTC", "LONG", 99000.0, 1.0)
    return me, b, SRV.bodies[-1] if SRV.bodies else None


# ══ 0 버전 ═════════════════════════════════════════════════════════════════════════
ck("0 앱 버전 2026.11.09a 이상", E.App._APP_VER >= "2026.11.09a", E.App._APP_VER)

# ══ A 클레임 계좌 식별 ═══════════════════════════════════════════════════════════════
ck("A1 키가 있으면 '브로커|키'", E._crypto_claim_id("okx", " KEY-EU ", LABEL) == "okx|KEY-EU", E._crypto_claim_id("okx", " KEY-EU ", LABEL))
ck("A1 키가 비면 종전대로 행 이름", E._crypto_claim_id("okx", "", LABEL) == LABEL and E._crypto_claim_id("okx", None, "BTCUSDT.P") == "BTCUSDT.P")

# A2 대표 사례: 맥북(OKX 유럽 키)과 윈도(OKX 한국 키), 행 이름은 둘 다 기본 라벨 - 둘 다 허가, 둘 다 주문
mac, bm, bd_mac = enter("macbook00001", cfg("KEY-EU-1111"), "2026-11-09-BTC-1")
win, bw, bd_win = enter("winpc0000001", cfg("KEY-KR-2222"), "2026-11-09-BTC-1")
ck("A2 같은 라벨, 다른 키 → 클레임 해시가 다르다", bd_mac["acct_h"] != bd_win["acct_h"], (bd_mac, bd_win))
ck("A2 해시 = sha256('okx|키') 12자(행 이름 해시가 아니다)",
   bd_mac["acct_h"] == h12("okx|KEY-EU-1111") and bd_win["acct_h"] == h12("okx|KEY-KR-2222") and bd_mac["acct_h"] != h12(LABEL), (bd_mac, bd_win))
ck("A2 두 기기 다 허가 → 두 계좌 다 주문(종전: 윈도가 양보해 진입 0)", len(bm.placed) == 1 and len(bw.placed) == 1, (bm.placed, bw.placed, win.logs))
ck("A2 양보 사유를 보내지 않음", not [e for e in mac.evs + win.evs if e[0] == "entry_skip"], mac.evs + win.evs)
_all = json.dumps(SRV.bodies) + "\n".join(mac.logs + win.logs)
ck("A2 키 원문은 서버 본문에도 로그에도 없다", "KEY-EU-1111" not in _all and "KEY-KR-2222" not in _all)

# A3 같은 키(같은 계좌)를 두 기기가 돌림 → 해시 같음, 늦은 쪽은 양보 + claim_yield
m1, b1, bd1 = enter("macbook00001", cfg("KEY-SAME-3333"), "2026-11-09-BTC-2")
w1, b2, bd2 = enter("winpc0000001", cfg("KEY-SAME-3333", label="내 OKX"), "2026-11-09-BTC-2")
ck("A3 같은 키 → 라벨이 달라도 해시 같음", bd1["acct_h"] == bd2["acct_h"] == h12("okx|KEY-SAME-3333"), (bd1, bd2))
ck("A3 먼저 잡은 기기만 주문, 늦은 기기는 양보(주문 없음)", len(b1.placed) == 1 and not b2.placed, (b1.placed, b2.placed))

# A4 키가 빈 행 → 행 이름으로(종전 그대로)
SRV.bodies.clear()
m4, b4, bd4 = enter("macbook00001", cfg(""), "2026-11-09-BTC-3")
ck("A4 빈 키 → 행 이름 해시(종전)", bd4 is not None and bd4["acct_h"] == h12(LABEL), bd4)

# A5 선물 클레임은 그대로: 계좌 id 해시 + 프로세스 난수, 진입 경로 원문도 그대로
E._machine_id = lambda: "macbook00001"
SRV.bodies.clear()
_ok = E.App._claim_entry(Stub(), {"id": "2026-11-09-NQ-1"}, "50KTC-V2-1234", per_process=True)
_fb = SRV.bodies[-1]
ck("A5 선물: acct_h = sha256(계좌 id), mid = 기기 12자 + 프로세스 난수", _ok is True and _fb["acct_h"] == h12("50KTC-V2-1234")
   and _fb["mid"] == "macbook00001"[:12] + E._CLAIM_NONCE, _fb)
_src_fut = inspect.getsource(E.App._run_futures_entry)
ck("A5 선물 진입 경로는 계좌 id로 클레임(키 지문 아님)", "self._claim_entry(sig, _aid, per_process=True)" in _src_fut
   and "_crypto_claim_id" not in _src_fut)
_src_ent = inspect.getsource(E.App._enter_account)
ck("A6 크립토 진입의 클레임 호출은 키 지문 하나뿐(행 이름 직접 사용 없음)",
   "self._claim_entry(sig, lbl or sym)" not in _src_ent and _src_ent.count("self._claim_entry(sig, _claim_id)") == 1
   and "_crypto_claim_id(_broker, user, lbl or sym)" in _src_ent)

# ══ B 건너뜀, 실패 사유 ═══════════════════════════════════════════════════════════════
# B1 양보 → entry_skip claim_yield, 잔여 포지션이 있어도 닫지 않는다(그 기기가 방금 연 포지션)
SRV.first.clear()
enter("macbook00001", cfg("KEY-B1"), "2026-11-09-BTC-4")                                 # 맥북이 먼저 잡는다
_left = FakeCrypto(poss=[Position("okx", "okx", "BTCUSDT", 1, {})])
w, bw, _ = enter("winpc0000001", cfg("KEY-B1"), "2026-11-09-BTC-4", b=_left)
ck("B1 양보 → entry_skip(why=claim_yield)", ("entry_skip", "BTC", {"why": "claim_yield"}) in w.evs, w.evs)
ck("B1 양보면 잔여 청산도 주문도 없음", bw.closed_calls == 0 and not bw.placed, (bw.closed_calls, bw.placed))
ck("B1 로그 '다른 기기 선점' 그대로", any("다른 기기 선점" in m for m in w.logs), w.logs)
# B1b 같은 자산, 같은 사유는 10분에 한 번(_entry_skip_ev 묶음)
E._build_broker = lambda *a, **k: FakeCrypto()
E._machine_id = lambda: "winpc0000001"
w._enter_account(cfg("KEY-B1"), {"id": "2026-11-09-BTC-4", "entry_ref": 100000.0}, "BTC", "LONG", 99000.0, 1.0)
ck("B1b 10분 안 두 번째 양보는 다시 보내지 않음", len([e for e in w.evs if e[0] == "entry_skip"]) == 1, w.evs)

# B2 잔여 청산 미확인 → entry_fail(why=error), 주문 없음
SRV.first.clear()
_b = FakeCrypto(poss=[Position("okx", "okx", "BTCUSDT", 1, {})], close_ok=False)
m, bb, _ = enter("macbook00001", cfg("KEY-B2"), "2026-11-09-BTC-5", b=_b)
_ef = [e for e in m.evs if e[0] == "entry_fail"]
ck("B2 잔여 청산 미확인 → entry_fail(why=error), 짧은 사유", len(_ef) == 1 and _ef[0][2].get("why") == "error"
   and "leftover close not confirmed" in _ef[0][2].get("err", "") and len(_ef[0][2]["err"]) <= 120, m.evs)
ck("B2 진입 중단(주문 없음), 로그 그대로", not bb.placed and bb.closed_calls == 1 and any("잔여 청산 미확인" in x for x in m.logs), (bb.placed, m.logs))
# B2b 잔여 청산 성공이면 종전대로 진입
SRV.first.clear()
_b = FakeCrypto(poss=[Position("okx", "okx", "BTCUSDT", 1, {})], close_ok=True)
m, bb, _ = enter("macbook00001", cfg("KEY-B2"), "2026-11-09-BTC-6", b=_b)
ck("B2b 잔여 청산 확인 → 진입, 실패 보고 없음", len(bb.placed) == 1 and not [e for e in m.evs if e[0] in ("entry_fail", "entry_skip")], m.evs)

# B3 구 지정가 경로의 건너뜀(종전엔 조용히 return)
for note, want in (("skipped_adverse", ("entry_skip", "adverse_move")), ("fill_unconfirmed", ("entry_fail", "error"))):
    SRV.first.clear()
    E._machine_id = lambda: "macbook00001"
    E._build_broker = lambda *a, **k: FakeCrypto()
    me = Stub()
    me._exec_entry_limit = lambda *a, _n=note, **k: {"skipped": True, "error": None, "entry": None, "note": _n}
    me._enter_account(cfg("KEY-B3"), {"id": "2026-11-09-BTC-7" + note, "entry_ref": 100000.0,
                                      "exec_policy": {"entry": {"mode": "limit_then_market", "limit_price": 100000.0}}},
                      "BTC", "LONG", 99000.0, 1.0)
    _hit = [e for e in me.evs if e[0] == want[0] and e[2].get("why") == want[1]]
    ck(f"B3 구 지정가 {note} → {want[0]}({want[1]})", len(_hit) == 1, me.evs)

# ══ C 앱의 청산 버튼 = 바로 청산 보고 한 번 ════════════════════════════════════════════


class _SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **k):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class CloseBroker:
    """flatten 결과를 정해 둔 어댑터. remaining = 청산 뒤 list_open_positions(손절 감시가 본다)."""

    def __init__(self, name, poss, errors=(), close=True):
        self.name, self.poss, self.errors, self.close = name, list(poss), list(errors), close
        self.remaining = [] if close else list(poss)

    def flatten_all(self, dry_run=True, only=None):
        ps = [p for p in self.poss if only is None or only(p.symbol)]
        return FlattenResult(dry_run=False, planned=ps, closed=(ps if self.close else []), errors=list(self.errors))

    def list_open_positions(self):
        return list(self.remaining)


def close_stub(rows, brokers, open_assets, ctx_assets=()):
    """rows = {자산: [(브로커, 계좌 id)]}. 이 앱이 연 포지션 = open_assets(_open_assets), ctx_assets = 감시 맥락이 있는 자산."""
    me = Stub()
    me._accts_of = lambda a: [{"id": aid, "broker": bk} for bk, aid in rows.get(a, [])]
    me._acct_broker = lambda a, ac: ac["broker"]
    me._creds_of = lambda a, bk=None: {"f1": ("K-" + bk) if any(bk == x for x, _ in rows.get(a, [])) else "", "f3": ""}
    me._open_assets = set(open_assets)
    for a in ctx_assets:
        bk = rows[a][0][0]
        me._open_ctx[a] = {"b": brokers[bk], "sym": {"BTC": "BTCUSDT", "NQ": "CON.F.US.MNQ.Z26", "GC": "CON.F.US.MGC.Z26"}[a],
                           "stop": 1.0, "app_stop": 1.0, "dir": "LONG", "aid": rows[a][0][1] or None}
        me._persist_open_ctx(a)
    return me


def press(me, brokers, asset=None):
    _o = (E._build_broker, E.messagebox, threading.Thread)
    E._build_broker = lambda bk, *a, **k: brokers[bk]
    E.messagebox = SimpleNamespace(askyesno=lambda *a, **k: True, showwarning=lambda *a, **k: None)
    threading.Thread = _SyncThread
    try:
        me._close_all_positions(asset)
    finally:
        E._build_broker, E.messagebox, threading.Thread = _o


# C1 [자산별 청산] BTC: 이 앱이 연 BTC 포지션이 닫힘 → 바로 청산 보고 1회(why=manual), 손절 감시는 두 번째를 보내지 않는다
BR = {"okx": CloseBroker("okx", [Position("okx", "okx", "BTCUSDT", 1, {})])}
me = close_stub({"BTC": [("okx", "")]}, BR, {"BTC"}, ctx_assets=("BTC",))
ck("C0 준비: 감시 맥락이 디스크에도 있다", "BTC" in E._load_open_ctx(), E._load_open_ctx())
_ctx_before = me._open_ctx.get("BTC")
press(me, BR, "BTC")
ck("C1 청산 버튼 → 청산 보고 바로 1회, 사유 manual", me.fills == [("BTC", True, "manual")], me.fills)
ck("C1 _open_assets, 감시 맥락(메모리, 디스크)이 비었다", "BTC" not in me._open_assets and "BTC" not in me._open_ctx
   and "BTC" not in E._load_open_ctx(), (me._open_assets, me._open_ctx, E._load_open_ctx()))
ck("C1 로그: 서버에 바로 보고, 마지막 줄은 여전히 완료 줄", any("청산을 서버에 바로 보고" in m for m in me.logs)
   and "완료" in me.logs[-1] and "바로 보고" not in me.logs[-1], me.logs[-3:])
for _ in range(3):
    me._check_stop_closed()
ck("C1 그 뒤 손절 감시(3바퀴)는 두 번째 보고를 하지 않는다", me.fills == [("BTC", True, "manual")], me.fills)
# C1b 감시가 청산 버튼 전에 맥락을 잡아 둔 경우(두 스레드가 겹침): 보고 권한은 한 번뿐
ck("C1b 청산 버튼이 먼저 보고했으면 감시의 늦은 보고 시도는 거절", E.App._close_report_take(me, "BTC", _ctx_before) is False)

# C2 닫힌 포지션이 없는 자산(플랫) → 아무것도 보내지 않음
BR = {"okx": CloseBroker("okx", [])}
me = close_stub({"BTC": [("okx", "")]}, BR, {"BTC"}, ctx_assets=("BTC",))
press(me, BR, "BTC")
ck("C2 닫힌 것이 없으면 청산 보고 없음", me.fills == [] and "BTC" in me._open_assets, (me.fills, me._open_assets))

# C3 이 앱이 연 포지션이 아닌 자산(회원이 손으로 연 것)은 닫혀도 보고하지 않는다(다른 기기 버킷을 건드리지 않게)
BR = {"okx": CloseBroker("okx", [Position("okx", "okx", "BTCUSDT", 1, {})])}
me = close_stub({"BTC": [("okx", "")]}, BR, set())
press(me, BR, "BTC")
ck("C3 _open_assets에 없는 자산 → 보고 없음", me.fills == [], me.fills)

# C4 그 자산 계좌에 실패가 섞이면 바로 보고하지 않고 손절 감시가 종전대로(문구는 '손절 또는 직접 청산') 한 번 보고
BR = {"okx": CloseBroker("okx", [Position("okx", "okx", "BTCUSDT", 1, {})], errors=["STILL OPEN after flatten: BTCUSDT"])}
me = close_stub({"BTC": [("okx", "")]}, BR, {"BTC"}, ctx_assets=("BTC",))
press(me, BR, "BTC")
ck("C4 실패가 섞인 자산은 바로 보고하지 않음", me.fills == [] and "BTC" in me._open_assets, me.fills)
BR["okx"].remaining = []
me._check_stop_closed()
me._check_stop_closed()
me._check_stop_closed()
ck("C4 손절 감시가 사라진 포지션을 한 번 보고(사유 없음 = 종전 본문)", me.fills == [("BTC", True, None)], me.fills)
_ln = [m for m in me.logs if "사라졌습니다" in m]
ck("C5 감시 문구: 손절로 단정하지 않는다", len(_ln) == 1 and "(손절 또는 직접 청산)" in _ln[0] and "손절 추정" not in _ln[0], _ln)
me.lang = "en"
me._open_assets.add("BTC")
me._open_ctx["BTC"] = {"b": BR["okx"], "sym": "BTCUSDT", "stop": 1.0, "app_stop": 1.0, "dir": "LONG"}
me._check_stop_closed()
me._check_stop_closed()
ck("C5 영문 문구(stop or manual close)", any("(stop or manual close)" in m for m in me.logs), me.logs[-2:])
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("C5 앱 원문에 '손절 추정' 로그 문구가 남지 않음(이스케이프 표기 포함)",
   "\\uc190\\uc808 \\ucd94\\uc815" not in _src and 'f"\\u2139 {_a} \\ud3ec' not in _src)

# C6 [전체 청산]: NQ, GC가 같은 프롭 계좌 - 그 계좌가 실패하면 둘 다 바로 보고하지 않고, 따로 닫힌 BTC만 보고
BR = {"projectx": CloseBroker("projectx", [Position("1", "50KTC-V2-1", "CON.F.US.MNQ.Z26", 1, {}),
                                           Position("1", "50KTC-V2-1", "CON.F.US.MGC.Z26", 1, {})],
                              errors=["STILL OPEN after flatten: CON.F.US.MNQ.Z26"]),
      "okx": CloseBroker("okx", [Position("okx", "okx", "BTCUSDT", 1, {})])}
me = close_stub({"NQ": [("projectx", "50KTC-V2-1")], "GC": [("projectx", "50KTC-V2-1")], "BTC": [("okx", "")]},
                BR, {"NQ", "GC", "BTC"})
press(me, BR, None)
ck("C6 전체 청산: 실패한 공유 계좌의 NQ, GC는 바로 보고 안 함, BTC만 manual 1회", me.fills == [("BTC", True, "manual")], me.fills)
ck("C6 마지막 줄은 미완료", "미완료" in me.logs[-1], me.logs[-1])
# C6b 같은 구성에서 모두 성공 → 세 자산 각각 한 번
BR["projectx"].errors = []
me = close_stub({"NQ": [("projectx", "50KTC-V2-1")], "GC": [("projectx", "50KTC-V2-1")], "BTC": [("okx", "")]},
                BR, {"NQ", "GC", "BTC"})
press(me, BR, None)
ck("C6b 전부 성공 → 자산마다 한 번(manual)", sorted(me.fills) == [("BTC", True, "manual"), ("GC", True, "manual"), ("NQ", True, "manual")], me.fills)

# C7 확인하지 못했던 진입의 맥락(unconfirmed)은 열렸다고 보고한 적이 없다 - 청산 버튼도 보고하지 않는다(감시가 종전대로 놓는다)
BR = {"okx": CloseBroker("okx", [Position("okx", "okx", "BTCUSDT", 1, {})])}
me = close_stub({"BTC": [("okx", "")]}, BR, {"BTC"}, ctx_assets=("BTC",))
me._open_ctx["BTC"]["unconfirmed"] = True
press(me, BR, "BTC")
ck("C7 unconfirmed 맥락 → 청산 보고 없음", me.fills == [], me.fills)

# C8 서버 본문: 청산 사유 manual은 _send_fill 본문의 why(16자 안)로 나간다
_sf = inspect.getsource(E.App._send_fill)
ck("C8 _send_fill이 why를 청산 본문에 싣는다(16자 안)", 'body["why"] = str(why)[:16]' in _sf and len("manual") <= 16)

E._rq_mod.post = _real_post
print("FAIL", len(fails), fails) if fails else print("ALL PASS")
sys.exit(1 if fails else 0)
