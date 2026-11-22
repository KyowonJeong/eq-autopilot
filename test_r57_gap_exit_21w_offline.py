#!/usr/bin/env python3
# EQ Autopilot 2026.10.21w 오프라인 시험(R57 #18, #7 - 2026-11-08). 0 = 통과.
# A (R57 #18) BTC 체결 갭 보고: 진입 경로가 신호 표기('BTCUSDT.P')를 넘겨 Bybit, Bitget, OKX 포지션('BTCUSDT')과 종목이 영원히 안 맞았다
#   (exec_gap.jsonl BTC 0행). 진입 경로는 브로커 표기(_mysym)를 넘기고, _send_gap은 _gap_sym_hit로 양쪽을 정규화해 맞춘다. 주문 종목은 그대로.
#   Bitget 포지션 평단 키(openPriceAvg)도 읽는다. 선물 계약 ID 매칭은 종전 그대로.
# B (R57 #7) 공개 트랙레코드: 앱이 청산을 낸 사유(pass_tp, day_cap, time_exit, manual)를 청산 사유 원장(.eqexits.json)에 적고, 동기화가
#   그 거래의 청산 체결과 맞춰 exit_reasons로 싣는다(앱이 내지 않은 청산 = unknown, 기록 시작 전 진입 = 키 없음). 계좌 체결가는 그 거래의
#   모든 청산 체결이 같은 값일 때만(갈리면 싣지 않는다). 선물 진입가는 서버가 hb에 tr_fut_px를 광고할 때까지 싣지 않는다.
#   ProjectX, NT8 closed_fills가 청산 반턴마다 진입가(그 포지션 진입 가격이 하나뿐일 때만)와 ProjectX 청산가를 준다.
# HOME = 임시 폴더, 네트워크 차단, 알림 끔. 브로커와 서버는 전부 가짜.
import datetime as DT
import inspect
import json
import os
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_21w_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"
os.environ["EQ_STOP_JITTER"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402
import requests  # noqa: E402
import autopilot_crypto  # noqa: E402
from eqexec.broker.base import FlattenResult, Position, entry_px_walk, pos_px  # noqa: E402
from eqexec.broker import projectx as PXM  # noqa: E402
from eqexec.broker import nt8 as N8  # noqa: E402
from eqexec.broker import bitget as BGM  # noqa: E402

assert E.APP_DIR.startswith(HOME), E.APP_DIR
assert E._XLEDGER_PATH.startswith(HOME) and E._XLEDGER_SINCE_PATH.startswith(HOME)
FAIL, NPASS = [], [0]
_real_sleep, _real_time = time.sleep, time.time


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:700]}"))
    if c:
        NPASS[0] += 1
    else:
        FAIL.append(n)


def wait(pred, secs=5.0):
    t0 = _real_time()
    while _real_time() - t0 < secs:
        if pred():
            return True
        _real_sleep(0.02)
    return bool(pred())


ck("0 앱 버전 2026.10.21z 이상", E.App._APP_VER >= "2026.10.21z", E.App._APP_VER)

# ═════ A (R57 #18) BTC 체결 갭 보고 ═════
hit = E._gap_sym_hit
_cases = [("BTCUSDT.P", "BTCUSDT", True), ("BTCUSDT", "BTCUSDT", True), ("btcusdt.p", "BTCUSDT", True),
          ("BTCUSDT.P", "BTC-USDT", True), ("CON.F.US.MNQ.Z26", "CON.F.US.MNQ.Z26", True), ("MNQ 12-26", "MNQ 12-26", True),
          ("CON.F.US.MNQ.Z26", "CON.F.US.MGC.Z26", False), ("BTCUSDT.P", "ETHUSDT", False), ("BTCUSDT", "ETHUSDT", False),
          ("MNQ 12-26", "MGC 12-26", False)]
_bad = [(w, h, e, hit(w, h)) for w, h, e in _cases if hit(w, h) != e]
ck("A1 _gap_sym_hit: 'BTCUSDT.P' = 'BTCUSDT'(대소문자, 하이픈 무관), 선물 계약 ID는 종전 부분일치, 다른 종목은 아님", not _bad, _bad)
ck("A1 종전 매칭('BTCUSDT.P' in 'BTCUSDT')은 False였다 - 고친 이유 재현", ("BTCUSDT.P" in "BTCUSDT") is False)

_POSTS = []
_o_post = requests.post


def _fake_post(url, json=None, **k):
    _POSTS.append((url, dict(json or {})))
    return SimpleNamespace(ok=True, text="gap:ok")


class GapBroker:
    def __init__(self, name, positions):
        self.name, self.positions = name, positions
        self.cfg = SimpleNamespace(accounts=[])

    def list_open_positions(self):
        return list(self.positions)


def gap_me():
    return SimpleNamespace(_gate={"ok": True, "caps": {"autoentry": True}}, _token="tok-21w-gap-0123456789")


def run_gap(broker, sym_match, asset="BTC", sid="S-BTC-1", ref=90000.0):
    _POSTS.clear()
    E.App._send_gap(gap_me(), asset, {"id": sid, "entry_ref": ref, "direction": "LONG"}, broker, sym_match)
    wait(lambda: bool(_POSTS), 5.0)
    return list(_POSTS)


requests.post = _fake_post
try:
    bb = GapBroker("bybit", [Position("bybit", "bybit", "BTCUSDT", 1, {"avgPrice": "90012.5", "symbol": "BTCUSDT"})])
    got = run_gap(bb, "BTCUSDT")
    ck("A2 Bybit: 진입 경로가 넘기는 브로커 표기('BTCUSDT')로 갭 보고가 나간다(inst BTC, 체결가 90012.5, 브로커 bybit)",
       len(got) == 1 and got[0][0] == E.PUSH_BASE + "eqgap" and got[0][1].get("inst") == "BTC"
       and got[0][1].get("fill_price") == 90012.5 and got[0][1].get("entry_ref") == 90000.0
       and got[0][1].get("broker") == "bybit", got)
    got = run_gap(bb, "BTCUSDT.P", sid="S-BTC-2")
    ck("A3 신호 표기('BTCUSDT.P')가 와도 _send_gap 안에서 맞춘다(다른 호출부 방어)",
       len(got) == 1 and got[0][1].get("fill_price") == 90012.5, got)
    bg = GapBroker("bitget", [Position("bitget", "bitget", "BTCUSDT", 1, {"openPriceAvg": "89990.1", "symbol": "BTCUSDT"})])
    got = run_gap(bg, "BTCUSDT", sid="S-BTC-3")
    ck("A4 Bitget: 평단 키 openPriceAvg를 읽어 갭 보고(89990.1)", len(got) == 1 and got[0][1].get("fill_price") == 89990.1
       and got[0][1].get("broker") == "bitget", got)
    ok = GapBroker("okx", [Position("okx", "okx", "BTCUSDT", 1, {"avgPrice": "90001", "avgPx": "90001"})])
    got = run_gap(ok, "BTCUSDT", sid="S-BTC-4")
    ck("A5 OKX(어댑터가 avgPrice로 옮겨 줌)도 갭 보고", len(got) == 1 and got[0][1].get("fill_price") == 90001.0, got)
    px = GapBroker("projectx", [Position("1", "A1", "CON.F.US.MGC.Z26", 2, {"averagePrice": 4000.0}),
                                Position("1", "A1", "CON.F.US.MNQ.Z26", 3, {"averagePrice": 20010.0})])
    got = run_gap(px, "CON.F.US.MNQ.Z26", asset="NQ", sid="S-NQ-1", ref=20000.0)
    ck("A6 선물(ProjectX 계약 ID)은 종전대로 그 계약만(MGC 평단이 아니라 MNQ 20010)",
       len(got) == 1 and got[0][1].get("inst") == "NQ" and got[0][1].get("fill_price") == 20010.0, got)
    _POSTS.clear()
    _o_sleep = time.sleep
    time.sleep = lambda s: None                     # 60초 폴링을 바로 돌린다
    try:
        E.App._send_gap(gap_me(), "BTC", {"id": "S-BTC-5", "entry_ref": 90000.0, "direction": "LONG"},
                        GapBroker("bybit", [Position("bybit", "bybit", "ETHUSDT", 1, {"avgPrice": "3000"})]), "BTCUSDT")
        _real_sleep(0.5)
    finally:
        time.sleep = _o_sleep
    ck("A7 다른 종목(ETHUSDT) 포지션만 있으면 BTC 갭 보고 없음", _POSTS == [], _POSTS)
finally:
    requests.post = _o_post


def run_btc_entry():
    """진짜 _enter_account(BTC, Bybit) 성공 경로 - 주문 종목과 _send_gap에 넘긴 종목을 본다."""
    me = MagicMock()
    me.lang = "ko"
    me._live_now.return_value = True
    me._claim_entry.return_value = True
    fake = MagicMock()
    fake._symbol.side_effect = lambda s: str(s).upper().replace(".P", "")
    fake.list_open_positions.return_value = []
    fake.current_market_price.return_value = None
    fake.place_entry.return_value = {"entry": {"orderId": "o1"}}
    _ob, _om = E._build_broker, E.messagebox
    E._build_broker = MagicMock(return_value=fake)
    E.messagebox = MagicMock()
    try:
        cfg = {"broker": "bybit", "f1": "k", "f2": "s", "f3": "", "acct": "", "one_r": 600.0, "label": "t",
               "live": True, "prop": {}, "pct": {}}
        E.App._enter_account(me, cfg, {"id": "S-BTC-E", "entry_ref": 60000.0, "published_at": None}, "BTC", "LONG", 59500.0, 1.0)
    finally:
        E._build_broker, E.messagebox = _ob, _om
    return me, fake


me, fake = run_btc_entry()
_pe = fake.place_entry.call_args
_sg = me._send_gap.call_args
ck("A8 BTC 진입 경로: 주문 종목은 그대로 'BTCUSDT.P', 갭 보고에는 브로커 표기 'BTCUSDT'(감시 기억, 원장과 같은 값)",
   _pe is not None and _pe.kwargs.get("symbol") == "BTCUSDT.P" and _sg is not None and _sg[0][3] == "BTCUSDT"
   and me._remember_open.call_args[0][2] == "BTCUSDT", (_pe, _sg))

# ═════ B (R57 #7) 청산 사유 원장 ═════
NOW = _real_time()


def _clean():
    for f in os.listdir(E.APP_DIR):
        if f.startswith((".eqexits", ".eqtrades", ".r_ledger", ".lastpush")):
            os.remove(os.path.join(E.APP_DIR, f))


def xl():
    try:
        return json.load(open(E._XLEDGER_PATH))
    except Exception:
        return None


_clean()
ck("B0 기록 시작 표식이 없으면 0, 만들지 않는다(동기화 쪽)", E._exit_since() == 0.0 and not os.path.exists(E._XLEDGER_SINCE_PATH))
_s0 = E._exit_since(create=True)
ck("B0 앱 기동(create=True) = 지금을 한 번 박제, 다시 불러도 그대로",
   abs(_s0 - NOW * 1000) < 120000 and E._exit_since(create=True) == _s0 and E._exit_since() == _s0, (_s0, NOW))
open(E._XLEDGER_SINCE_PATH, "wb").write(b"\x00" * 16)
ck("B0 깨진 표식은 0(동기화는 사유를 싣지 않는다), 기동이 다시 박제", E._exit_since() == 0.0
   and E._exit_since(create=True) > 0, open(E._XLEDGER_SINCE_PATH, "rb").read())

_clean()
E._exit_note("GC", "pass_tp", ["150KTC-V2-1", "12345", "", None], ts=NOW - 100)
E._exit_note("GC", "pass_tp", ["150ktc-v2-1"], ts=NOW - 70)           # 60초 안 같은 계좌, 같은 사유 = 다시 안 적음
E._exit_note("GC", "day_cap", ["150KTC-V2-1"], ts=NOW - 70)           # 사유가 다르면 적음
E._exit_note("GC", "stop", ["150KTC-V2-1"], ts=NOW - 60)              # 모르는 사유 = 안 적음
E._exit_note("ES", "time_exit", ["X"], ts=NOW - 60)                   # 모르는 자산 = 안 적음
E._exit_note("NQ", "manual", ["", None], ts=NOW - 60)                 # 계좌 키 없음 = 안 적음
E._exit_note("gc", "pass_tp", ["150KTC-V2-1"], ts=NOW + 5)            # 61초 뒤 = 새로 적음(자산 소문자 = GC)
_d = xl()
ck("B1 원장: 계좌 키 casefold, 빈 값 뺌, 60초 안 같은 사유 한 번, 모르는 사유, 자산, 빈 키는 안 적음",
   isinstance(_d, list) and [(r["asset"], r["why"], r["ids"]) for r in _d] == [
       ("GC", "pass_tp", ["150ktc-v2-1", "12345"]), ("GC", "day_cap", ["150ktc-v2-1"]), ("GC", "pass_tp", ["150ktc-v2-1"])]
   and _d[0]["ts_ms"] == int((NOW - 100) * 1000), _d)
_clean()                                                              # .bak 없이(있으면 그걸로 되살린다)
open(E._XLEDGER_PATH, "w").write("{not json")
E._exit_note("NQ", "time_exit", ["A1"], ts=NOW)
_d = xl()
ck("B1 깨진 원장 → 손상 사본으로 옮기고 새로 적음(청산 경로는 예외 없음)", isinstance(_d, list) and len(_d) == 1
   and any(f.startswith(".eqexits.json.corrupt-") for f in os.listdir(E.APP_DIR)), (_d, os.listdir(E.APP_DIR)))
_o_wr = E._write_json_atomic
E._write_json_atomic = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
try:
    E._exit_note("NQ", "manual", ["A1"], ts=NOW + 500)
    _raised = False
except Exception:
    _raised = True
finally:
    E._write_json_atomic = _o_wr
ck("B1 쓰기 실패해도 예외를 올리지 않는다(청산을 막지 않음)", not _raised)

T0 = 1793000000000                                                    # ms
_X = [{"ts_ms": T0, "asset": "GC", "why": "time_exit", "ids": ["a1"]},
      {"ts_ms": T0 + 600000, "asset": "GC", "why": "pass_tp", "ids": ["a1", "999"]},
      {"ts_ms": T0, "asset": "NQ", "why": "manual", "ids": ["a1"]},
      {"ts_ms": T0, "asset": "GC", "why": "bogus", "ids": ["b2"]}, "junk"]
ro = E._exit_reason_of
ck("B2 체결이 기록 뒤 몇 초 = 그 사유, 계좌 키는 원래 키(숫자 id)로도 맞는다",
   ro(_X, "GC", ("A1", None), T0 + 3000) == "time_exit" and ro(_X, "GC", (None, "999"), T0 + 600000 + 2000) == "pass_tp")
ck("B2 기록보다 60초 넘게 앞선 체결, 5분 넘게 뒤인 체결 = unknown(그 기록과 무관)",
   ro(_X, "GC", ("A1",), T0 - 61000) == "unknown" and ro(_X, "GC", ("a1",), T0 + 300001) == "unknown")
ck("B2 두 기록 창이 겹치면 체결 직전의 늦은 기록", ro(_X, "GC", ("a1",), T0 + 599000) == "pass_tp")
ck("B2 다른 계좌, 다른 자산, 모르는 사유 기록 = unknown",
   ro(_X, "GC", ("b9",), T0 + 1000) == "unknown" and ro(_X, "BTC", ("a1",), T0 + 1000) == "unknown"
   and ro(_X, "GC", ("b2",), T0 + 1000) == "unknown" and ro(None, "GC", ("a1",), T0) == "unknown")
rs = E._exit_reasons
_cl = [(T0 + 3000, ("a1", None)), (T0 + 7200000, ("b2", None))]
ck("B3 거래의 사유 = 청산 체결마다 사유를 모아 정렬(앱이 내지 않은 청산은 unknown)",
   rs(_X, "GC", T0 - 3600000, _cl, T0 - 7200000) == ["time_exit", "unknown"])
ck("B3 기록 시작 전 진입, 진입 모름, 기록 시작 표식 없음, 청산 체결 없음 = None(키를 싣지 않음)",
   rs(_X, "GC", T0 - 3600000, _cl, T0) is None and rs(_X, "GC", None, _cl, T0 - 7200000) is None
   and rs(_X, "GC", T0, _cl, 0) is None and rs(_X, "GC", T0, [], T0 - 1) is None)
op = E._one_px
ck("B4 _one_px: 모두 같은 값이면 그 값, 갈리거나 하나라도 빠지면 None",
   op([4012.3, 4012.3]) == 4012.3 and op([4012.3, 3990.1]) is None and op([4012.3, None]) is None and op([]) is None)

# ── B5 브로커 어댑터: 청산 반턴마다 진입가 ─────────────────────────────────────────────
W = entry_px_walk
_m = W([((1, 0), "C", "o1", 2, 100.0, False), ((2, 1), "C", "c1", -1, 101.0, True), ((3, 2), "C", "c2", -1, 102.0, True),
        ((4, 3), "C", "o2", 1, 103.0, False), ((5, 4), "C", "o3", 1, 104.0, False), ((6, 5), "C", "c3", -2, 105.0, True),
        ((0, 9), "C", "c0", -1, 99.0, True)])
ck("B5 walk(청산 표시): 한 가격 진입 → 부분 청산 둘 다 그 값, 두 가격 진입 → None, 조회창 앞에서 열린 포지션 → None",
   _m == {"c0": None, "c1": 100.0, "c2": 100.0, "c3": None}, _m)
_m = W([((1, 0), ("a", "MGC"), "e1", 3, 4000.0, None), ((2, 1), ("a", "MGC"), "x1", -3, 4012.3, None),
        ((3, 2), ("a", "MGC"), "e2", -2, 4010.0, None), ((4, 3), ("a", "MGC"), "x2", 4, 4005.0, None),
        ((5, 4), ("a", "MGC"), "x3", -2, 4008.0, None), ((6, 5), ("b", "MGC"), "x4", -1, 1.0, None)])
ck("B5 walk(부호로 가름, NT8): 롱 청산 = 4000, 숏 4010을 넘는 매수(뒤집힘) 청산 = 4010, 뒤집힌 롱 청산 = 4005, 다른 계좌 따로",
   _m.get("x1") == 4000.0 and _m.get("x2") == 4010.0 and _m.get("x3") == 4005.0 and _m.get("e2") is None, _m)
_m = W([((1, 0), "C", "o1", 0, 100.0, False), ((2, 1), "C", "c1", -1, 101.0, True)])
ck("B5 walk: 수량을 모르는 반턴이 있는 계약은 전부 None", _m == {"c1": None}, _m)
_m = W([((1, 0), "C", "o1", 1, None, False), ((2, 1), "C", "c1", -1, 101.0, True)])
ck("B5 walk: 가격을 모르는 진입이 섞이면 None", _m == {"c1": None}, _m)
ck("B5 pos_px: 양수 유한 값만", (pos_px("4012.3"), pos_px(0), pos_px(None), pos_px("x"), pos_px(float("nan")), pos_px(float("inf")))
   == (4012.3, None, None, None, None, None))

_PXT = [{"id": 11, "contractId": "CON.F.US.MGC.Z26", "creationTimestamp": "2026-10-29T06:00:01.52329+00:00", "price": 4000.0,
         "profitAndLoss": None, "fees": 0.74, "side": 0, "size": 3, "voided": False},
        {"id": 12, "contractId": "CON.F.US.MGC.Z26", "creationTimestamp": "2026-10-29T06:40:02+00:00", "price": 4012.3,
         "profitAndLoss": 369.0, "fees": 0.74, "side": 1, "size": 3, "voided": False},
        {"id": 13, "contractId": "CON.F.US.MNQ.Z26", "creationTimestamp": "2026-10-29T14:00:00+00:00", "price": 20000.0,
         "profitAndLoss": None, "fees": 0.5, "side": 1, "size": 1, "voided": False},
        {"id": 14, "contractId": "CON.F.US.MNQ.Z26", "creationTimestamp": "2026-10-29T14:00:00+00:00", "price": 20000.5,
         "profitAndLoss": None, "fees": 0.5, "side": 1, "size": 1, "voided": False},
        {"id": 15, "contractId": "CON.F.US.MNQ.Z26", "creationTimestamp": "2026-10-29T18:00:00+00:00", "price": 19990.0,
         "profitAndLoss": 41.0, "fees": 1.0, "side": 0, "size": 2, "voided": False},
        {"id": 16, "contractId": "CON.F.US.MGC.Z26", "creationTimestamp": "2026-10-29T07:00:00+00:00", "price": 4100.0,
         "profitAndLoss": 5.0, "fees": 0.0, "side": 1, "size": 1, "voided": True}]
px = PXM.ProjectXBroker.__new__(PXM.ProjectXBroker)
px._accounts = lambda active_only=True: [{"id": 777, "name": "150KTC-V2-1"}]
px._post = lambda path, body, **k: {"trades": [dict(t) for t in _PXT]}
_cf = px.closed_fills(0)
ck("B6 ProjectX closed_fills: 청산 반턴만(무효 제외), 청산가 = 그 반턴 price, 진입가 = 한 가격 진입(MGC 4000), 두 가격 진입(MNQ) = None",
   [(f["tid"], f["exit_px"], f["entry_px"], f["acct"]) for f in _cf] == [("12", 4012.3, 4000.0, "777"), ("15", 19990.0, None, "777")]
   and _cf[0]["pnl"] == 369.0 - 0.74 and _cf[0]["direction"] == "LONG" and _cf[1]["direction"] == "SHORT", _cf)
ck("B6 ProjectX: 거래 기록 시각 읽기는 그대로 _parse_iso(소수 초 5자리도 읽음)", "_parse_iso(ts)" in inspect.getsource(PXM.ProjectXBroker.closed_fills))
px._post = lambda path, body, **k: {"trades": [dict(_PXT[0], creationTimestamp="garbage"), dict(_PXT[1])]}
_cf = px.closed_fills(0)
ck("B6 ProjectX: 시각을 못 읽는 진입이 있는 계약은 진입가 None(청산 행과 청산가는 그대로)",
   [(f["tid"], f["exit_px"], f["entry_px"]) for f in _cf] == [("12", 4012.3, None)], _cf)

_EX = [{"account": "LT-B", "exec_id": "e1", "instrument": "MGC 12-26", "side": "BUY", "qty": 3, "price": 4000.0,
        "time": "2026-10-29T06:00:01Z", "commission": 1.0, "pnl": 0.0},
       {"account": "LT-B", "exec_id": "e2", "instrument": "MGC 12-26", "side": "SELL", "qty": 3, "price": 3990.1,
        "time": "2026-10-29T09:00:00Z", "commission": 1.0, "pnl": -297.0},
       {"account": "LT-C", "exec_id": "e3", "instrument": "MGC 12-26", "side": "SELL", "qty": 1, "price": 3995.0,
        "time": "2026-10-29T09:00:00Z", "commission": 1.0, "pnl": 50.0}]
nb = N8.NT8Broker.__new__(N8.NT8Broker)
nb.authenticate = lambda: None
nb._state = SimpleNamespace(last_state={"executions": [dict(e) for e in _EX]})
_cf = nb.closed_fills(0)
ck("B7 NT8 closed_fills: 청산가 그대로, 진입가 = 같은 (계좌, 종목) 진입 반턴 가격(4000), 진입을 못 본 계좌 = None, pnl은 종전대로",
   [(f["tid"], f["exit_px"], f["entry_px"], f["pnl"]) for f in _cf] == [("e2", 3990.1, 4000.0, -298.0), ("e3", 3995.0, None, 49.0)], _cf)

bgb = BGM.BitgetBroker.__new__(BGM.BitgetBroker)
bgb.product = "USDT-FUTURES"
bgb._req = lambda m, p, q: {"list": [{"positionId": "p1", "utime": "1793000000000", "symbol": "BTCUSDT", "holdSide": "long",
                                     "openAvgPrice": "90000.5", "closeAvgPrice": "91000", "netProfit": "10", "pnl": "11",
                                     "openFee": "-0.5", "closeFee": "-0.5", "totalFunding": "0"}]}
_cf = bgb.closed_fills(0)
ck("B8 Bitget closed_fills: 진입, 청산 평균가(openAvgPrice, closeAvgPrice) 실음", len(_cf) == 1 and _cf[0]["entry_px"] == 90000.5
   and _cf[0]["exit_px"] == 91000.0, _cf)


# ── B9 트랙레코드 동기화(진짜 push_profile) ─────────────────────────────────────────────
class SyncStub:
    lang = "ko"
    _srv_aead = False
    _srv_fut_px = E.App._srv_fut_px            # 앱 기본값(False) - 서버가 hb에 tr_fut_px를 광고하기 전

    def __init__(self, accts, fut_px=False):
        self.logs = []
        self.consent = SimpleNamespace(get=lambda: True)
        self.tr_public = SimpleNamespace(get=lambda: True)
        self._profile, self._gate, self._token = {}, {}, "tok-21w"
        self.root = SimpleNamespace(after=lambda *a, **k: None)
        self._accts = accts
        if fut_px:
            self._srv_fut_px = True

    def log(self, m):
        self.logs.append(str(m))

    def _accts_of(self, a):
        return self._accts.get(a, [])

    def _acct_broker(self, a, ac):
        return ac["broker"]

    def _creds_of(self, a, bk=None):
        return {"f1": "user-" + str(bk), "f3": ""}

    def _assets_with_open_position(self, assets):
        return set()

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("push_profile", "_fill_asset", "_fill_is_eq", "_fill_entry", "_ui", "_on_main"):
    setattr(SyncStub, _n, E.App.__dict__[_n])

FILLS = {}
PUSHED = []


class FakeFills:
    def __init__(self, bk):
        self.bk = bk

    def closed_fills(self, start_ms):
        return [dict(f) for f in FILLS.get(self.bk, [])]


E._build_broker = lambda bk, *a, **k: FakeFills(bk)
E._f2_load = lambda f1: "k"
requests.get = lambda url, params=None, timeout=None: (
    PUSHED.append(json.loads(params["profile_push"])) or SimpleNamespace(text="pp:ok:1"))
autopilot_crypto.encrypt = lambda tok, payload, v2=False: json.dumps(payload)
autopilot_crypto.path_id = lambda tok: "pid"


def sync(accts, fut_px=False):
    PUSHED.clear()
    s = SyncStub(accts, fut_px)
    SyncStub.push_profile(s, auto=True)
    wait(lambda: any(("체결 없음" in m) or ("동기화 완료" in m) or ("실패" in m) or ("건너뜁니다" in m) for m in s.logs), 20)
    return [t for p in PUSHED for t in p.get("trades", [])], s.logs


DAY = 86400.0
TE = NOW - 3 * DAY                                                   # 금 진입(초)
TB = NOW - 2 * DAY                                                   # BTC 진입(초)
TOLD = NOW - 6 * DAY                                                 # 기록 시작 전 진입(초)


def setup_ledgers(since_s):
    _clean()
    with open(E._LEDGER_SINCE_PATH, "w") as f:
        f.write(str((NOW - 30 * DAY) * 1000))
    json.dump([{"ts_ms": int(TOLD * 1000), "asset": "GC", "symbol": "CON.F.US.MGC.Z26", "direction": "LONG", "tag": "t0"},
               {"ts_ms": int(TE * 1000), "asset": "GC", "symbol": "CON.F.US.MGC.Z26", "direction": "LONG", "tag": "t1"},
               {"ts_ms": int(TB * 1000), "asset": "BTC", "symbol": "BTCUSDT", "direction": "LONG", "tag": "t2"}],
              open(E._LEDGER_PATH, "w"))
    json.dump({"GC|150KTC-V2-1": [[TOLD - 10, 1000.0]], "GC|LT-B": [[TOLD - 10, 1000.0]], "BTC|bybit": [[TB - 10, 500.0]]},
              open(E._RLEDGER_PATH, "w"))
    with open(E._XLEDGER_SINCE_PATH, "w") as f:
        f.write(str(since_s * 1000))


ACCTS = {"GC": [{"id": "150KTC-V2-1", "broker": "projectx", "one_r": 1000.0, "label": "T"},
                {"id": "LT-B", "broker": "nt8", "one_r": 1000.0, "label": "L"}],
         "BTC": [{"id": "", "broker": "bybit", "one_r": 500.0, "label": "B"}]}


def f_(ts, pnl, entry, exit_, sym, acct=None, direction="LONG"):
    d = {"tid": f"{sym}-{ts}", "ts_ms": int(ts * 1000), "symbol": sym, "pnl": pnl, "direction": direction,
         "entry_px": entry, "exit_px": exit_}
    if acct is not None:
        d["acct"] = acct
    return d


setup_ledgers(TE - DAY)
# 10/29 모양: Topstep 계좌는 통과 익절로 먼저 닫힘(앱 기록 pass_tp), Lucid 계좌는 손절(앱 기록 없음) - 청산가가 갈린다
E._exit_note("GC", "pass_tp", ["150KTC-V2-1", "777"], ts=TE + 1800)
E._exit_note("BTC", "time_exit", ["bybit"], ts=TB + 4 * 3600)
FILLS.clear()
FILLS["projectx"] = [f_(TE + 1803, 1140.0, 4000.0, 4012.3, "CON.F.US.MGC.Z26", acct="150KTC-V2-1"),
                     f_(TOLD + 3600, 500.0, 3900.0, 3905.0, "CON.F.US.MGC.Z26", acct="150KTC-V2-1")]
FILLS["nt8"] = [f_(TE + 3 * 3600, -2580.0, 4000.0, 3990.1, "MGC 12-26", acct="LT-B")]
FILLS["bybit"] = [f_(TB + 4 * 3600 + 2, 250.0, 90000.0, 91000.0, "BTCUSDT")]
tr, lg = sync(ACCTS)
_by = {(t["instrument"], t["date"]): t for t in tr}
_gc = _by.get(("GC", DT.datetime.utcfromtimestamp(TE).date().isoformat()))
_go = _by.get(("GC", DT.datetime.utcfromtimestamp(TOLD).date().isoformat()))
_bt = _by.get(("BTC", DT.datetime.utcfromtimestamp(TB).date().isoformat()))
ck("B9 10/29 모양(두 계좌): exit_reasons = [pass_tp, unknown], R은 종전 식((1140 - 2580) / 2000 = -0.72), 브로커 둘",
   _gc is not None and _gc.get("exit_reasons") == ["pass_tp", "unknown"] and _gc.get("r") == -0.72
   and _gc.get("brokers") == ["nt8", "projectx"], (_gc, lg[-6:]))
ck("B9 두 계좌 청산가가 갈림(4012.3, 3990.1) → exit_px 없음, 선물 진입가는 서버 광고(tr_fut_px) 전이라 없음",
   _gc is not None and "exit_px" not in _gc and "entry_px" not in _gc, _gc)
ck("B9 BTC 한 계좌, 앱이 정해진 시각에 닫음 → [time_exit], 진입가, 청산가 둘 다(크립토는 종전대로 실림)",
   _bt is not None and _bt.get("exit_reasons") == ["time_exit"] and _bt.get("entry_px") == 90000.0
   and _bt.get("exit_px") == 91000.0, _bt)
ck("B9 기록 시작 전에 진입한 거래 = exit_reasons 키 없음(짐작으로 채우지 않음), 행은 종전대로",
   _go is not None and "exit_reasons" not in _go and _go.get("r") == 0.5, _go)
_pj = json.dumps(PUSHED).casefold()
ck("B9 계좌 키(설정 계좌 값 150KTC, LT-B)는 서버로 가지 않는다", "150ktc" not in _pj and "lt-b" not in _pj, _pj[:400])

tr, _ = sync(ACCTS, fut_px=True)
_gc = {(t["instrument"], t["date"]): t for t in tr}.get(("GC", DT.datetime.utcfromtimestamp(TE).date().isoformat()))
ck("B10 서버가 tr_fut_px를 광고하면 선물 진입가도 실음(두 계좌 모두 4000) - 청산가는 여전히 갈려서 없음",
   _gc is not None and _gc.get("entry_px") == 4000.0 and "exit_px" not in _gc, _gc)

FILLS["nt8"] = [f_(TE + 1801, 1140.0, 4000.0, 4012.3, "MGC 12-26", acct="LT-B")]
E._exit_note("GC", "pass_tp", ["LT-B"], ts=TE + 1799)
tr, _ = sync(ACCTS, fut_px=True)
_gc = {(t["instrument"], t["date"]): t for t in tr}.get(("GC", DT.datetime.utcfromtimestamp(TE).date().isoformat()))
ck("B11 두 계좌가 같은 가격에 같은 사유로 닫힘 → [pass_tp], 진입가 4000, 청산가 4012.3",
   _gc is not None and _gc.get("exit_reasons") == ["pass_tp"] and _gc.get("entry_px") == 4000.0 and _gc.get("exit_px") == 4012.3, _gc)

os.remove(E._XLEDGER_SINCE_PATH)
tr, _ = sync(ACCTS)
ck("B12 기록 시작 표식이 없으면(구버전에서 막 올라온 기기, 동기화가 먼저 돈 경우) 어떤 거래에도 exit_reasons 없음",
   tr and not any("exit_reasons" in t for t in tr), tr)
ck("B12 옛 서버 호환: 새 키는 exit_reasons 하나(리스트, 짧은 코드만) - 서버 _valid_trade가 보는 키(date, instrument, direction, r, "
   "entry_px, exit_px)는 형식 그대로", all(isinstance(t.get("r"), float) and t["direction"] in ("LONG", "SHORT") for t in tr), tr)


# ── B13 기록 자리: 평가 계좌 감시 정리 주문 ─────────────────────────────────────────────
class DcStub:
    lang = "ko"

    def __init__(self, mode):
        self.fired = {"projectx:150ktc-v2-1": {"mode": mode, "lbl": "T", "pos": {}, "tgt": {}, "psent": {}, "ts": NOW}}
        self.saved, self.notes = [], []

    def _dc_map(self, name):
        return self.fired

    def _dc_orders_ok(self, test):
        return True

    def _dc_remaining(self, info, con, qty):
        return qty

    def _daycap_fired_save(self, k):
        self.saved.append(k)

    def _daycap_note_once(self, key, ko, en):
        self.notes.append(ko)


DcStub._fill_asset = E.App.__dict__["_fill_asset"]
DcStub._daycap_mon_send = E.App.__dict__["_daycap_mon_send"]


class DcBroker:
    def __init__(self, fail=()):
        self.closed, self.fail = [], set(fail)

    def close_contract(self, acct, con):
        if con in self.fail:
            raise RuntimeError("rejected")
        self.closed.append((acct, con))


_clean()
_snap = {"aid": 777, "poss": [{"con": "CON.F.US.MGC.Z26", "qty": 3, "acct": 777}, {"con": "CON.F.US.MNQ.Z26", "qty": 1, "acct": 777}]}
_t = {"sc": "150KTC-V2-1", "key": "projectx:150ktc-v2-1", "lbl": "T"}
st = DcStub("pass")
b = DcBroker()
n = st._daycap_mon_send(b, _t, _snap)
_d = xl() or []
ck("B13 통과 목표 정리: 주문은 종전대로 두 계약, 원장에 자산마다 pass_tp(설정 계좌 값 + 숫자 id)",
   n == 2 and b.closed == [(777, "CON.F.US.MGC.Z26"), (777, "CON.F.US.MNQ.Z26")]
   and sorted((r["asset"], r["why"], tuple(r["ids"])) for r in _d) == [("GC", "pass_tp", ("150ktc-v2-1", "777")),
                                                                      ("NQ", "pass_tp", ("150ktc-v2-1", "777"))], (n, b.closed, _d))
_clean()
st = DcStub("cap")
b = DcBroker(fail={"CON.F.US.MNQ.Z26"})
n = st._daycap_mon_send(b, _t, _snap)
_d = xl() or []
ck("B13 하루 상한 정리: 주문이 나간 자산만 day_cap(실패한 MNQ는 적지 않음), 실패 로그는 종전대로",
   n == 1 and [(r["asset"], r["why"]) for r in _d] == [("GC", "day_cap")] and any("청산 발주 실패" in m for m in st.notes), (n, _d, st.notes))
_clean()
_o_xn = E._exit_note
E._exit_note = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    st = DcStub("pass")
    b = DcBroker()
    n = st._daycap_mon_send(b, _t, _snap)
    _ok = True
except Exception as _e:
    _ok, n = False, _e
finally:
    E._exit_note = _o_xn
ck("B13 기록이 터져도 정리 주문 수는 그대로 돌아온다", _ok and n == 2, n)


# ── B14 기록 자리: 앱의 청산 버튼(_close_all_positions) ─────────────────────────────────
class CloseStub:
    lang = "ko"

    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(str(m))

    def _accts_of(self, a):
        return {"GC": [{"id": "150KTC-V2-1", "broker": "projectx"}], "BTC": [{"id": "", "broker": "bybit"}]}.get(a, [])

    def _acct_broker(self, a, ac):
        return ac["broker"]

    def _creds_of(self, a, bk=None):
        return {"f1": "u" if bk in ("projectx", "bybit") else "", "f3": ""}   # 키가 있는 브로커는 이 둘뿐

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


CloseStub._close_all_positions = E.App.__dict__["_close_all_positions"]
CloseStub._fill_asset = E.App.__dict__["_fill_asset"]


class CloseBroker:
    def __init__(self, bk):
        self.bk = bk

    def flatten_all(self, dry_run=True, only=None):
        if self.bk == "projectx":
            ps = [Position("777", "150KTC-V2-1", "CON.F.US.MGC.Z26", 3, {"_accountId": 777}),
                  Position("777", "150KTC-V2-1", "CON.F.US.ESZ6", 1, {"_accountId": 777})]
            ps = [p for p in ps if only is None or only(p.symbol)]
            return FlattenResult(dry_run=False, planned=ps, closed=[p for p in ps if "MGC" in p.symbol],
                                 errors=[f"STILL OPEN after flatten: {p.symbol}" for p in ps if "MGC" not in p.symbol])
        ps = [Position("bybit", "bybit", "BTCUSDT", 1, {})]
        return FlattenResult(dry_run=False, planned=ps, closed=ps)


_clean()
E._build_broker = lambda bk, *a, **k: CloseBroker(bk)
_o_mb = E.messagebox
E.messagebox = SimpleNamespace(askyesno=lambda *a, **k: True, showwarning=lambda *a, **k: None, showinfo=lambda *a, **k: None)
try:
    cs = CloseStub()
    cs._close_all_positions()
    wait(lambda: any("전체 청산" in m and ("완료" in m or "미완료" in m) for m in cs.logs), 10)
finally:
    E.messagebox = _o_mb
_d = xl() or []
ck("B14 청산 버튼: 닫힌 것이 확인된 포지션의 자산, 계좌만 manual(금 = 설정 계좌 값 + 숫자 id, BTC = 거래소 이름), 남은 ES는 안 적음",
   sorted((r["asset"], r["why"], tuple(r["ids"])) for r in _d) == [("BTC", "manual", ("bybit",)),
                                                                  ("GC", "manual", ("150ktc-v2-1", "777"))], (_d, cs.logs[-4:]))


# ── B15 기록 자리: 정해진 시각 청산(진짜 _auto_loop_body 한 바퀴, 가짜 시계) ──────────────────
_RDT = DT.datetime
CLK = {"t": _RDT(2026, 11, 10, 19, 1, tzinfo=DT.timezone.utc).timestamp()}   # 화 14:01 뉴욕(EST) = NQ 마감 청산 창


class FakeDT(_RDT):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(CLK["t"], tz)

    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(CLK["t"], DT.timezone.utc).replace(tzinfo=None)


class AutoStub:
    lang = "ko"

    def __init__(self, jobs):
        self.logs, self.sent = [], []
        self._auto_accts = {("NQ", 0): jobs}
        self._auto_on, self._entered_at, self._exit_override = True, {}, {}

    def log(self, m):
        self.logs.append(str(m))

    def _live_now(self, f):
        return bool(f)

    def _fallback_kind(self):
        return None

    def _send_fill(self, a, **k):
        self.sent.append((a, k))

    def __getattr__(self, n):
        if n.startswith("__") or not callable(getattr(E.App, n, None)):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_auto_loop_body", "_close_mode", "_fill_asset"):
    setattr(AutoStub, _n, E.App.__dict__[_n])
FLAT = {}


class AutoBroker:
    def __init__(self, acct):
        self.acct = acct

    def flatten_all(self, dry_run=True, only=None):
        ps = FLAT.get(self.acct, [])
        if dry_run:
            return FlattenResult(dry_run=True, planned=list(ps))
        return FlattenResult(dry_run=False, planned=list(ps), closed=[p for p in ps if p.net_qty != 99],
                             errors=[f"STILL OPEN {p.symbol}" for p in ps if p.net_qty == 99])


def run_auto(jobs):
    st = AutoStub(jobs)
    _o = (DT.datetime, time.time, time.sleep)
    DT.datetime = FakeDT
    time.time = lambda: CLK["t"]

    def _sleep(s):
        st._auto_on = False
    time.sleep = _sleep
    try:
        E.App._auto_loop_body(st, None)
    finally:
        DT.datetime, time.time, time.sleep = _o
    return st


E._build_broker = lambda bk, f1, f2, f3, accounts: AutoBroker((accounts or [bk])[0])
_tz, _hr = E._ASSET_EXITS["NQ"][0]
_job = {"asset": "NQ", "tz": _tz, "hour": _hr, "live": True, "broker": "projectx", "f1": "u", "f2": "k", "f3": "", "acct": "150KTC-V2-1"}
_clean()
FLAT.clear()
FLAT["150KTC-V2-1"] = [Position("777", "150KTC-V2-1", "CON.F.US.MNQ.Z26", 2, {"_accountId": 777})]
st = run_auto([dict(_job), dict(_job, live=False, acct="DRY-1")])
_d = xl() or []
ck("B15 정해진 시각 청산(라이브)이 닫은 계좌 = time_exit(시각은 청산을 내기 직전), 청산 보고는 종전대로",
   [(r["asset"], r["why"], tuple(r["ids"])) for r in _d] == [("NQ", "time_exit", ("150ktc-v2-1", "777"))]
   and abs(_d[0]["ts_ms"] - CLK["t"] * 1000) < 2000 and st.sent == [("NQ", {"closed": True})], (_d, st.sent, st.logs[-6:]))
_clean()
FLAT["150KTC-V2-1"] = []
st = run_auto([dict(_job)])
ck("B15 포지션이 없으면(이미 손절 등) 기록 없음 - 손절 체결이 time_exit로 읽히지 않게", xl() is None, xl())
_clean()
FLAT["150KTC-V2-1"] = [Position("777", "150KTC-V2-1", "CON.F.US.MNQ.Z26", 99, {"_accountId": 777})]
st = run_auto([dict(_job)])
ck("B15 청산을 냈지만 아직 열려 있으면 기록 없음(재시도가 닫을 때 적는다)", xl() is None, (xl(), st.logs[-4:]))

# ── C 기존 경로 그대로인 것(소스) ─────────────────────────────────────────────────────
_src_ea = inspect.getsource(E.App._enter_account)
ck("C1 BTC 진입 경로: 주문(place_entry)은 원본 sym, 갭 보고와 감시 기억, 원장은 _mysym",
   "self._send_gap(asset, sig, b, _mysym)" in _src_ea and "self._send_gap(asset, sig, b, sym)" not in _src_ea
   and "place_entry(symbol=sym," in _src_ea and "_ledger_add(asset, _mysym" in _src_ea)
_src_sg = inspect.getsource(E.App._send_gap)
ck("C2 _send_gap: 종목 맞추기는 _gap_sym_hit 하나, 평단 키에 openPriceAvg", "_gap_sym_hit(sym_match, _p.symbol)" in _src_sg
   and "str(sym_match) not in str(_p.symbol)" not in _src_sg and '"openPriceAvg"' in _src_sg)
_src_rt = inspect.getsource(E.App._run_futures_entry) + inspect.getsource(E.App._flatten_unprotected)
ck("C3 진입 직전 잔여 정리, 손절 거절 긴급 청산은 사유를 적지 않는다(새 포지션의 손절 체결과 섞이지 않게)", "_exit_note(" not in _src_rt)
_pp = inspect.getsource(E.App.push_profile)
ck("C4 동기화: 계좌 키는 맞추기에만 쓰고 행에는 exit_reasons(코드 목록)만", '"exit_reasons": _xr' in _pp and "_acct_id\")" in _pp
   and '"ids"' not in _pp)

ck("C5 서버 광고(hb tr_fut_px)만 선물 진입가를 연다 - 앱 기본값 False, 하트비트가 매번 그 값으로 갱신",
   E.App._srv_fut_px is False and 'self._srv_fut_px = bool(hb.get("tr_fut_px"))' in inspect.getsource(E.App))

print(f"\n{NPASS[0]} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
