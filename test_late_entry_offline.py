#!/usr/bin/env python3
# [놓친 신호 진입] 늦은 진입 위험(대표 2026-10-21 '위험은 절대 늘리면 안된다'): 지금 가격으로 손절까지 거리를 다시 재서 계약 수를 줄이기만,
# 손절 지남, 불리 0.75R 초과, 가격 모름 = 진입 안 함. 정시 진입(_late 없음)은 종전 그대로. 0 = 통과.
# 21l(2026-10-27 실행 경로 리뷰 P1): 줄이기는 마이크로 환산 합에 한 번(leg마다 내림하면 미니 한 계약이 늘 0이 됐다), 사이징과 같은 규칙으로
# 다시 나눔. 종전 시험의 legs (MGC, 20)은 compute_size가 만들지 않는 값이라(마이크로 10 이상은 미니로 묶는다) GC 2로 바꿨다.
import datetime as _dt, os, socket, sys, tempfile, time
from types import SimpleNamespace
HOME = tempfile.mkdtemp(prefix="eq_late_"); os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"; os.environ["EQ_ERR_REPORT"] = "0"
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("blocked"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
FAIL = []
def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:300]}")); (None if c else FAIL.append(n))
class Fut:
    name = "nt8like"
    def __init__(self, px): self.px, self.entries = px, []
    def _accounts(self): return [{"name": "A1", "id": "A1"}]
    def list_open_positions(self): return []
    def close_contract(self, aid, con): pass
    def place_entry(self, **k): self.entries.append((k.get("contract_id"), k.get("size"))); return {"would_place": k}
    def position_qty(self, aid, con): return 0
    def current_market_price(self, con): return self.px
class Stub:
    lang = "ko"; STOP_ORDER_CHECK_SEC = eqgui.App.STOP_ORDER_CHECK_SEC
    def __init__(self): self.logs, self.skips = [], []; self._open_ctx, self._unconf_watch = {}, {}
    def log(self, m): self.logs.append(str(m))
    def _claim_entry(self, sig, aid, **k): return True
    def _resolve_contract(self, b, s): return {"MGC": "MGC 12-26", "GC": "GC 12-26"}.get(s, s)
    def _nt8_standby(self): return False
    def _entry_skip_ev(self, a, why): self.skips.append(why)
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return lambda *a, **k: None
for _n in ("_ui", "_on_main", "_dm_kind", "_unconf_watch_add", "_unconf_watch_drop", "_unconf_save", "_unconf_key",
           "_remember_open", "_persist_open_ctx", "_run_futures_entry", "_fill_asset"):
    setattr(Stub, _n, eqgui.App.__dict__[_n])
time.sleep = lambda s: None
REF, STOP = 4136.0, 4130.4          # 거리 5.6
def micro(entries):
    """주문들의 마이크로 환산 합(미니 = 마이크로 10)."""
    return sum(int(q) * (1 if str(c).startswith("M") else 10) for c, q in entries)
def run(px, late=True, legs=(("GC", 2),), d="LONG", stop=STOP):
    st, b = Stub(), Fut(px)
    sig = {"id": "t-GC", "entry_ref": REF}
    if late: sig["_late"] = True
    eqgui.App._run_futures_entry(st, b, "A1", list(legs), d, stop, sig, True, _dt.datetime.now(), "GC", None)
    return st, b
st, b = run(4139.2)                   # 롱이 3.2 올라감: 거리 8.8 → x0.606 → 마이크로 환산 20 → 12 = GC 1 + MGC 2
ck("1 롱 기준가보다 비쌈 = 계약 수를 줄임(GC 2 → GC 1 + MGC 2 = 마이크로 환산 12, 위험 1R 이하)",
   sorted(b.entries) == [("GC 12-26", 1), ("MGC 12-26", 2)], (b.entries, st.logs[-3:]))
st, b = run(4134.0)                   # 기준가보다 쌈: 거리 3.6 < 5.6 → 그대로(늘리지 않음)
ck("2 기준가보다 유리 = 늘리지 않고 그대로 GC 2", b.entries == [("GC 12-26", 2)], b.entries)
st, b = run(4141.0)                   # 불리 5.0 > 0.75 x 5.6 = 4.2 → 건너뜀
ck("3 불리하게 0.75R 넘게 벗어남 = 진입 안 함(adverse_move)", not b.entries and "adverse_move" in st.skips, (b.entries, st.skips))
st, b = run(4130.0)                   # 손절 지남
ck("4 손절을 이미 지남 = 진입 안 함", not b.entries and "adverse_move" in st.skips, (b.entries, st.skips))
st, b = run(None)
ck("5 지금 가격을 못 읽음 = 진입 안 함(late_no_price)", not b.entries and "late_no_price" in st.skips, (b.entries, st.skips))
st, b = run(4139.2, late=False)
ck("6 정시 진입(_late 없음)은 종전 그대로 GC 2", b.entries == [("GC 12-26", 2)], b.entries)
st, b = run(4132.8, d="SHORT", stop=4141.6)   # 숏: 기준 4136, 손절 4141.6(거리 5.6), 지금 4132.8 = 이미 아래로 감(거리 8.8) → 줄임
ck("7 숏이 이미 진입 방향으로 감 = 계약 수를 줄임(마이크로 환산 20 → 12)", micro(b.entries) == 12, b.entries)
st, b = run(4138.0, d="SHORT", stop=4141.6)   # 숏이 기준가보다 비싸게(거리 3.6 < 5.6) → 그대로(늘리지 않음, 위험 < 1R)
ck("8 숏 기준가보다 좋은 가격 = 늘리지 않고 그대로 GC 2", b.entries == [("GC 12-26", 2)], b.entries)
# 21d(2026-10-21 검토): 가격을 못 주는 브로커는 같은 자산 다른 계좌가 20초 안에 읽은 가격을 빌린다, 청산 40분 관문, 정리 전 판정
def run2(px, peer=None, pos=False):
    st, b = Stub(), Fut(px)
    if peer is not None:
        st._late_px = {"GC": peer}
    b.closed = []
    if pos:
        b.list_open_positions = lambda: [SimpleNamespace(symbol="MGC 12-26", net_qty=-3, raw={})]
        b.close_contract = lambda aid, con: b.closed.append(con)
    eqgui.App._run_futures_entry(st, b, "A1", [("GC", 2)], "LONG", STOP, {"id": "t-GC", "entry_ref": REF, "_late": True},
                                 True, _dt.datetime.now(), "GC", None)
    return st, b
st, b = run2(None, peer=(time.time(), 4139.2))
ck("9 가격 못 주는 브로커 = 다른 계좌가 방금 읽은 가격으로 줄여 진입(마이크로 환산 12)", micro(b.entries) == 12, (b.entries, st.logs[-3:]))
st, b = run2(None, peer=(time.time() - 60, 4139.2))
ck("10 빌린 가격이 20초보다 오래됨 = 진입 안 함", not b.entries and "late_no_price" in st.skips, (b.entries, st.skips))
st, b = run2(4139.2)
ck("11 가격을 읽은 계좌는 다른 계좌가 빌려 쓰게 남긴다", isinstance(st._late_px, dict) and st._late_px.get("GC", (0, 0))[1] == 4139.2, getattr(st, "_late_px", None))
st, b = run2(4141.0, pos=True)
ck("12 건너뛸 늦은 진입은 이전 세션 잔여를 정리하지 않는다(판정이 정리 앞)", not b.entries and not b.closed, (b.entries, b.closed))
_now = time.time()
ck("13 청산 41분 남음 = 들어감", eqgui._missed_exit_ok({"exit_ts": _now + 41 * 60}))
ck("14 청산 39분 남음 = 안 들어감", not eqgui._missed_exit_ok({"exit_ts": _now + 39 * 60}))
ck("15 청산 시각 모름 = 안 들어감", not eqgui._missed_exit_ok({}) and not eqgui._missed_exit_ok({"exit_ts": "x"}))
ck("16 서버 시각 기준(PC 5분 빠름 = 서버로 49분 남음 들어감, 5분 느림 = 39분 안 들어감)",
   eqgui._missed_exit_ok({"exit_ts": _now + 44 * 60}, skew=300) and not eqgui._missed_exit_ok({"exit_ts": _now + 44 * 60}, skew=-300))
class NT8Broker(Fut):
    pass
_st, _b = Stub(), NT8Broker(None)
_st._late_px = {"GC": (time.time(), 4136.0)}
_st._nt8_standby = lambda: True
eqgui.App._run_futures_entry(_st, _b, "A1", [("GC", 2)], "LONG", STOP, {"id": "t-GC", "entry_ref": REF, "_late": True},
                             True, _dt.datetime.now(), "GC", None)
ck("17 NT8 대기조 기기 = 늦은 진입 안 함(주문 전 200초 대기)", not _b.entries and "late_no_price" in _st.skips, (_b.entries, _st.skips))
_msgs = []
eqgui.messagebox.showinfo = lambda t, m: _msgs.append(m)
eqgui.messagebox.askyesno = lambda t, m: (_msgs.append("ASK " + m), False)[1]
_ms = Stub(); _ms._sig_on = True; _ms._watch_only = False; _ms._entered_at = {}; _ms._sig_entered_day = {}
_ms._clock_skew_now = lambda: 0.0
_ms._last_feed_sig = {"id": "S", "instrument": "GC", "tradeable": True, "direction": "LONG", "published_at": time.time() - 600,
                      "exit_ts": time.time() + 3 * 3600, "entry_ref": REF, "stop_price": STOP}
_ms._sig_accts = {("GC", 0): {"broker": "nt8", "label": "L"}}
eqgui.App._missed_entry(_ms)
ck("18 켜진 GC 계좌가 NinjaTrader뿐 = 확인 창 없이 처음부터 안 된다고 알림", _msgs and not _msgs[-1].startswith("ASK") and "NinjaTrader" in _msgs[-1], _msgs)
_ms._sig_accts = {("GC", 0): {"broker": "nt8", "label": "L"}, ("GC", 1): {"broker": "projectx", "label": "T"}}
eqgui.App._missed_entry(_ms)
ck("19 Topstep 계좌가 같이 켜져 있으면 확인 창(두 계좌)", _msgs[-1].startswith("ASK") and "계좌 2개" in _msgs[-1], _msgs[-1:])
_ms._entered_at = {"GC": time.time() - 60}
eqgui.App._missed_entry(_ms)
ck("20 이 신호로 이미 진입한 계좌가 있음 = 안 들어감", "이미 진입한" in _msgs[-1], _msgs[-1:])
# ── 21l(2026-10-27 실행 경로 리뷰 P1): 미니 한 계약이 늘 0이 되던 표 - 사이징(compute_size)이 실제로 내는 legs로 ──
from eqexec import sizing as _szg
class FutQ(Fut):
    """자산을 받는 브로커(나스닥, 금), 없는 계약을 흉내 낼 수 있게. 주문한 수량을 포지션으로 돌려준다(체결 확인이 바로 끝나게)."""
    def __init__(self, px, missing=()):
        super().__init__(px); self.missing = set(missing); self.pos = {}
    def place_entry(self, **k):
        self.pos[k.get("contract_id")] = self.pos.get(k.get("contract_id"), 0) + int(k.get("size") or 0)
        return super().place_entry(**k)
    def position_qty(self, aid, con): return self.pos.get(con, 0)
class StubQ(Stub):
    def _resolve_contract(self, b, s, **k):
        return None if s in getattr(b, "missing", ()) else f"{s} 12-26"
def run3(asset, one_r, ref, stop, px, d="LONG", missing=()):
    sz = _szg.compute_size(asset, "projectx", one_r, ref, stop, d)
    st, b = StubQ(), FutQ(px, missing)
    eqgui.App._run_futures_entry(st, b, "A1", list(sz["legs"]), d, stop, {"id": f"t-{asset}", "entry_ref": ref, "_late": True},
                                 True, _dt.datetime.now(), asset, None)
    return sz["legs"], st, b
# 가격 그대로(x0.952): 계획 마이크로 환산 N → floor(N x 0.952)
for _nm, _a, _r, _ref, _stp, _want in (
        ("21 NQ 1(미니 하나) → MNQ 9(종전 0, 건너뜀)", "NQ", 1000.0, 20000.0, 19950.0, [("MNQ 12-26", 9)]),
        ("22 NQ 1 + MNQ 3 → NQ 1 + MNQ 2(종전 MNQ 2)", "NQ", 1300.0, 20000.0, 19950.0, [("MNQ 12-26", 2), ("NQ 12-26", 1)]),
        ("23 GC 2 → GC 1 + MGC 9(종전 GC 1)", "GC", 1120.0, 4136.0, 4130.4, [("GC 12-26", 1), ("MGC 12-26", 9)]),
        ("24 GC 1 + MGC 1 → GC 1(종전 0, 건너뜀)", "GC", 600.0, 4136.0, 4130.4, [("GC 12-26", 1)]),
        ("25 MNQ 9 → MNQ 8(종전과 같음)", "NQ", 900.0, 20000.0, 19950.0, [("MNQ 12-26", 8)]),
        ("26 MNQ 1 → 0 = 진입 안 함(size_cap)", "NQ", 100.0, 20000.0, 19950.0, [])):
    _legs, st, b = run3(_a, _r, _ref, _stp, _ref)
    ck(_nm, sorted(b.entries) == _want and (bool(_want) or "size_cap" in st.skips), (_legs, b.entries, st.skips, st.logs[-2:]))
_legs, st, b = run3("NQ", 1000.0, 20000.0, 19950.0, 20000.0)
ck("27 로그: 마이크로 환산 합과 나눈 결과(10 → 9, NQ 1 → MNQ 9)", any("마이크로 환산 10 → 9" in l and "NQ 1 → MNQ 9" in l for l in st.logs), st.logs[-4:])
_legs, st, b = run3("NQ", 1000.0, 20000.0, 19950.0, 20000.0, missing=("MNQ",))
ck("28 다시 나눠 생긴 종목의 계약을 못 찾으면 그 leg만 건너뜀(진입 없음, 늘리지 않음)", not b.entries and "size_cap" in st.skips, (b.entries, st.skips))
# 늘리지 않음: 여러 1R, 가격(유리, 그대로, 불리 0.7R까지), 방향에서 늦은 진입 마이크로 환산 합 ≤ 정시 진입 합, 위험 ≤ 계획 위험(1R x 배수)
_bad = []
for _a, _ref, _stp, _pv in (("NQ", 20000.0, 19950.0, 2.0), ("GC", 4136.0, 4130.4, 10.0)):
    _dist = abs(_ref - _stp)
    for _r in (100.0, 300.0, 600.0, 900.0, 1000.0, 1120.0, 1300.0, 2250.0, 4000.0):
        for _k in (-0.5, -0.1, 0.0, 0.05, 0.3, 0.7):
            for _d in ("LONG", "SHORT"):
                _sgn = 1 if _d == "LONG" else -1
                _st0 = _ref - _sgn * _dist
                _px = _ref + _sgn * _k * _dist
                _sz = _szg.compute_size(_a, "projectx", _r, _ref, _st0, _d)
                if not _sz["legs"]:
                    continue
                _plan = micro([(f"{s} 12-26", q) for s, q in _sz["legs"]])
                _lg, st, b = run3(_a, _r, _ref, _st0, _px, d=_d)
                _got = micro(b.entries)
                _risk = _got * abs(_px - _st0) * _pv
                _prisk = _plan * _dist * _pv
                if _got > _plan or _risk > _prisk + 1e-6 or (_k > 0 and _got >= _plan):
                    _bad.append((_a, _r, _k, _d, _plan, _got, round(_risk, 1), round(_prisk, 1)))
ck("29 늦은 진입은 정시 진입보다 크지 않고(마이크로 환산), 지금 가격의 위험이 계획 위험 이하", not _bad, _bad[:6])
print("FAIL:", FAIL if FAIL else "none"); sys.exit(1 if FAIL else 0)
