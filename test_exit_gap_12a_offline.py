#!/usr/bin/env python3
# EQ Autopilot 2026.11.12a 오프라인 시험 - 청산 체결 보고(/eqgap side=exit, 대표 2026-11-12 "청산 슬리피지도 재자, 기준은 늘 봉 마감가,
# 뉴스 날은 빼고"). 0 = 통과.
# A  _send_exit_gap 본문: 서버 계약 키 그대로(tok_id, side, id, inst, dir, fill_price, fill_ts, sent_ts, why, ref_price, broker, ver), 신호 id와
#    방향은 최근 진입 기록에서, 청산가는 브로커 청산 기록(closed_fills)의 exit_px(IBKR는 price, 수량 가중).
# B  (자산, 신호, exit)마다 기기에서 한 번 - 진입 갭 보고 키와 겹치지 않는다. 키는 청산가를 처음 읽은 계좌의 스레드가 잠금 안에서 잡는다(첫 계좌
#    조회가 실패하거나 청산가 없는 브로커여도 다음 계좌가 보낸다). 앱이 진입하지 않은 계좌, 반대 방향, 모르는 사유는 키를 쓰지 않는다.
# C  조용한 실패: 청산 기록 조회 실패, 토큰 풀이 실패, closed_fills 없는 어댑터, 청산가 없는 기록(Tradovate), 서버 전송 실패 - 예외 없음, 보고 없음.
#    호출은 기다리지 않는다(조회가 느려도 바로 돌아온다). 등급(Autopilot만)도 _send_gap과 같다.
# D  청산 경로 배선(_auto_loop 한 바퀴): 선물 정해진 시각 = time_exit, BTC 24시간 만기 = btc_expiry(기준가 없음), BTC 반대봉 = btc_block(기준가 =
#    판정에 쓴 봉 종가). 뉴스 선행 청산, 서버 불통 폴백의 BTC 블록 청산, dry-run, 닫힌 포지션 없음 = 보내지 않음.
# E  보고가 터져도(동기 예외, 스레드 안 예외, 스레드 시작 실패) 청산 경로는 끝까지 간다(flat 확인, 청산 통보, 오늘 끝남 기록, 재시도 없음).
# HOME = 임시 폴더, 네트워크 차단, 알림 끔. 브로커와 서버는 전부 가짜.
import datetime as DT
import os
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_12a_")
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
from eqexec.broker.base import FlattenResult, Position  # noqa: E402

assert E.APP_DIR.startswith(HOME), E.APP_DIR
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


ck("0 앱 버전 2026.11.12a 이상", E.App._APP_VER >= "2026.11.12a", E.App._APP_VER)

# ── 가짜 서버, 스레드 기록 ─────────────────────────────────────────────────────
_POSTS = []
_POST_MODE = {"m": "ok"}


def _fake_post(url, json=None, **k):
    _POSTS.append((url, dict(json or {})))
    if _POST_MODE["m"] == "raise":
        raise OSError("server down")
    return SimpleNamespace(ok=True, text="gap:ok")


_THREADS = []
_o_start = threading.Thread.start


def _rec_start(self):
    _THREADS.append(self)
    return _o_start(self)


def join_last(n0, secs=10.0):
    """이번 호출이 띄운 스레드(n0 뒤)가 모두 끝날 때까지."""
    for th in list(_THREADS[n0:]):
        th.join(secs)
    return all(not th.is_alive() for th in _THREADS[n0:])


class XBroker:
    """closed_fills만 있는 가짜 브로커. rows = 돌려줄 청산 기록, exc = 매번 던질 예외, delay = 실제로 기다릴 초."""

    def __init__(self, name, rows=None, exc=None, delay=0.0):
        self.name, self.rows, self.exc, self.delay = name, list(rows or []), exc, delay
        self.calls = []
        self.cfg = SimpleNamespace(accounts=[])

    def closed_fills(self, start_ms):
        self.calls.append(int(start_ms))
        if self.delay:
            _real_sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return [dict(r) for r in self.rows]


AP = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True, "fullexit": True}}
OP = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": False, "fullexit": False}}
TOK = "tok-12a-exitgap-0123456789"
TID = autopilot_crypto.path_id(TOK)
CONTRACT = {"tok_id", "side", "id", "inst", "dir", "fill_price", "fill_ts", "sent_ts", "why", "ref_price", "broker", "ver"}


def rec_of(sid, side, acc_key, root="MNQ", ts0=None, now=None):
    now = _real_time() if now is None else float(now)
    t0 = float(ts0 if ts0 is not None else now - 4 * 3600)
    return {"ts0": t0, "ts": t0, "sid": sid, "side": side, "acc": {acc_key: {root: 2.0}}, "ref": None, "avg": {}, "avg_by": {},
            "closed": None, "until": now + 3 * 86400}


def xme(asset="NQ", sid="S-NQ-1", side="LONG", acc_key="projectx:150KTC-A", gate=AP, root="MNQ", ts0=None):
    return SimpleNamespace(_gate=gate, _token=TOK, _pos_today={asset: rec_of(sid, side, acc_key, root, ts0)})


def send(me, asset, b, closed, why, sent, ref=None, acct="150KTC-A"):
    """_send_exit_gap 호출 → (걸린 시간, 띄운 스레드 시작 위치)."""
    n0 = len(_THREADS)
    t0 = _real_time()
    E.App._send_exit_gap(me, asset, b, closed, why, sent, ref_price=ref, acct=acct)
    return _real_time() - t0, n0


MNQ = "CON.F.US.MNQ.Z26"
MGC = "CON.F.US.MGC.Z26"
P_MNQ_L = Position("1001", "150KTC-A", MNQ, 2, {"contractId": MNQ})
P_BTC_S = Position("bybit", "bybit", "BTCUSDT", -0.01, {"symbol": "BTCUSDT"})

_o_post = requests.post
requests.post = _fake_post
threading.Thread.start = _rec_start
try:
    # ═════ A 본문 ═════
    sent = _real_time() - 2.0
    rows = [
        {"tid": "t1", "ts_ms": int((sent + 0.8) * 1000), "symbol": MNQ, "pnl": 40.0, "direction": "LONG", "acct": "1001", "exit_px": 21000.25},
        {"tid": "t0", "ts_ms": int((sent - 900) * 1000), "symbol": MNQ, "pnl": -5.0, "direction": "LONG", "acct": "1001", "exit_px": 20900.0},
        {"tid": "t2", "ts_ms": int((sent + 0.5) * 1000), "symbol": MGC, "pnl": 3.0, "direction": "LONG", "acct": "1001", "exit_px": 4000.0},
        {"tid": "t3", "ts_ms": int((sent + 0.5) * 1000), "symbol": MNQ, "pnl": 3.0, "direction": "SHORT", "acct": "1001", "exit_px": 20999.0},
    ]
    me = xme()
    bx = XBroker("projectx", rows)
    _POSTS.clear()
    dt, n0 = send(me, "NQ", bx, [P_MNQ_L], "time_exit", sent)
    join_last(n0)
    b1 = _POSTS[0][1] if _POSTS else {}
    ck("A1 정해진 시각 청산(NQ, ProjectX) 보고 하나, 주소 PUSH_BASE + eqgap", len(_POSTS) == 1 and _POSTS[0][0] == E.PUSH_BASE + "eqgap", _POSTS)
    ck("A2 본문 키가 서버 계약 그대로(더도 덜도 없음)", set(b1) == CONTRACT, sorted(set(b1) ^ CONTRACT))
    ck("A3 값: tok_id(진입과 같은 path_id), side exit, 신호 id = 진입 기록의 sid, inst NQ, dir LONG, why time_exit, ref_price None, broker, ver",
       b1.get("tok_id") == TID and b1.get("side") == "exit" and b1.get("id") == "S-NQ-1" and b1.get("inst") == "NQ"
       and b1.get("dir") == "LONG" and b1.get("why") == "time_exit" and b1.get("ref_price", "x") is None
       and b1.get("broker") == "projectx" and b1.get("ver") == E.App._APP_VER, b1)
    ck("A4 청산가 = 이번 청산 기록의 exit_px(다른 종목 MGC, 반대 방향, 2분보다 오래된 기록은 뺌)", b1.get("fill_price") == 21000.25, b1)
    ck("A5 fill_ts = 브로커 청산 시각, sent_ts = 청산을 낸 시각", abs(b1.get("fill_ts", 0) - (sent + 0.8)) < 0.01
       and b1.get("sent_ts") == sent, (b1.get("fill_ts"), b1.get("sent_ts"), sent))
    ck("A6 조회 시작 = 진입 앞(진입 시각 - 10분) - 시작 시각을 연 시각으로 거르는 끝점에서도 하루 전 BTC가 보인다",
       bx.calls and bx.calls[0] <= int((me._pos_today["NQ"]["ts0"] - 600) * 1000) + 1, (bx.calls, me._pos_today["NQ"]["ts0"]))
    ck("A7 키(자산, 신호, exit)가 기기 기록에 남음", ("NQ", "S-NQ-1", "exit") in getattr(me, "_gap_sent", set()), getattr(me, "_gap_sent", None))

    # 같은 계약 기록 여러 줄: 수량을 모르면 단순 평균, IBKR(price + qty)는 수량 가중
    rows2 = [{"ts_ms": int((sent + 0.4) * 1000), "symbol": MNQ, "direction": "LONG", "exit_px": 21000.0},
             {"ts_ms": int((sent + 0.6) * 1000), "symbol": MNQ, "direction": "LONG", "exit_px": 21000.5}]
    _POSTS.clear()
    dt, n0 = send(xme(sid="S-NQ-2"), "NQ", XBroker("projectx", rows2), [P_MNQ_L], "time_exit", sent)
    join_last(n0)
    ck("A8 같은 청산의 여러 체결(수량 모름) = 단순 평균 21000.25, fill_ts = 마지막 체결", len(_POSTS) == 1
       and _POSTS[0][1].get("fill_price") == 21000.25 and abs(_POSTS[0][1].get("fill_ts") - (sent + 0.6)) < 0.01, _POSTS)
    P_IB = Position("U123", "U123", "MNQZ6", 4, {})
    rows3 = [{"ts_ms": int((sent + 0.3) * 1000), "symbol": "MNQZ6", "direction": "LONG", "price": 100.0, "qty": 1},
             {"ts_ms": int((sent + 0.5) * 1000), "symbol": "MNQZ6", "direction": "LONG", "price": 101.0, "qty": 3}]
    _POSTS.clear()
    dt, n0 = send(xme(sid="S-NQ-3", acc_key="ibkr:U123"), "NQ", XBroker("ibkr", rows3), [P_IB], "time_exit", sent, acct="U123")
    join_last(n0)
    ck("A9 IBKR(exit_px 없음, price + qty) = 수량 가중 100.75", len(_POSTS) == 1 and _POSTS[0][1].get("fill_price") == 100.75
       and _POSTS[0][1].get("broker") == "ibkr", _POSTS)

    # BTC 반대봉 마감(Bybit): 기준가 = 앱이 판정에 쓴 봉 종가
    rows4 = [{"tid": "o9", "ts_ms": int((sent + 1.2) * 1000), "symbol": "BTCUSDT", "pnl": -12.0, "direction": "SHORT",
              "entry_px": 90000.0, "exit_px": 90230.5}]
    meb = xme("BTC", "S-BTC-1", "SHORT", "bybit:", root="BTC", ts0=_real_time() - 8 * 3600)
    _POSTS.clear()
    dt, n0 = send(meb, "BTC", XBroker("bybit", rows4), [P_BTC_S], "btc_block", sent, ref=90200.0, acct="bybit")
    join_last(n0)
    bb = _POSTS[0][1] if _POSTS else {}
    ck("A10 BTC 반대봉 마감(Bybit): why btc_block, ref_price 90200(판정 봉 종가), dir SHORT, 청산가 90230.5, 계좌 키 'bybit:'와 맞춤",
       len(_POSTS) == 1 and bb.get("why") == "btc_block" and bb.get("ref_price") == 90200.0 and bb.get("dir") == "SHORT"
       and bb.get("inst") == "BTC" and bb.get("fill_price") == 90230.5 and bb.get("broker") == "bybit" and bb.get("id") == "S-BTC-1", _POSTS)
    # Bitget(UTA)과 OKX도 같은 모양(exit_px)
    for _bn in ("bitget", "okx"):
        _POSTS.clear()
        dt, n0 = send(xme("BTC", f"S-BTC-{_bn}", "SHORT", f"{_bn}:", root="BTC"), "BTC", XBroker(_bn, rows4), [P_BTC_S],
                      "btc_expiry", sent, acct=_bn)
        join_last(n0)
        ck(f"A11 {_bn}: btc_expiry 보고(ref_price None)", len(_POSTS) == 1 and _POSTS[0][1].get("why") == "btc_expiry"
           and _POSTS[0][1].get("ref_price", "x") is None and _POSTS[0][1].get("broker") == _bn, _POSTS)

    # ═════ B 한 번만 ═════
    _POSTS.clear()
    dt, n0 = send(me, "NQ", XBroker("projectx", rows), [P_MNQ_L], "time_exit", sent)
    join_last(n0)
    ck("B1 같은 (NQ, S-NQ-1) 청산 두 번째 계좌는 보내지 않음(스레드도 안 띄움)", _POSTS == [] and len(_THREADS) == n0, (_POSTS, len(_THREADS), n0))
    me2 = xme(sid="S-NQ-9")
    me2._gap_sent = {("NQ", "S-NQ-9")}                   # 진입 갭 보고가 먼저 쓴 키
    _POSTS.clear()
    dt, n0 = send(me2, "NQ", XBroker("projectx", rows), [P_MNQ_L], "time_exit", sent)
    join_last(n0)
    ck("B2 진입 갭 보고 키(자산, 신호)가 있어도 청산 보고는 나간다", len(_POSTS) == 1 and _POSTS[0][1].get("id") == "S-NQ-9", _POSTS)
    _POSTS.clear()
    E.App._send_gap(me2, "NQ", {"id": "S-NQ-9", "entry_ref": 21000.0, "direction": "LONG"},
                    SimpleNamespace(name="projectx", cfg=SimpleNamespace(accounts=[]),
                                    list_open_positions=lambda: [Position("1", "A", MNQ, 2, {"averagePrice": 21001.0})]), MNQ)
    ck("B3 청산 키가 진입 키를 막지 않음(진입 키가 이미 있으면 종전처럼 진입 보고는 건너뜀)", _POSTS == [], _POSTS)
    me3 = xme(sid="S-NQ-10")
    E.App._send_gap(me3, "NQ", {"id": "S-NQ-10", "entry_ref": 21000.0, "direction": "LONG"},
                    SimpleNamespace(name="projectx", cfg=SimpleNamespace(accounts=[]),
                                    list_open_positions=lambda: [Position("1", "A", MNQ, 2, {"averagePrice": 21001.0})]), MNQ)
    wait(lambda: len(_POSTS) >= 1)
    dt, n0 = send(me3, "NQ", XBroker("projectx", rows), [P_MNQ_L], "time_exit", sent)
    join_last(n0)
    ck("B4 진입 보고 뒤 같은 신호의 청산 보고도 나간다(진입 1 + 청산 1)", len(_POSTS) == 2
       and {p[1].get("side") for p in _POSTS} == {None, "exit"}, _POSTS)

    # 앱이 진입하지 않은 계좌, 반대 방향, 모르는 사유, 진입 기록 없음 → 보내지 않고 키도 쓰지 않는다
    for nm, kw, args in (
            ("다른 계좌(그 신호로 앱이 진입한 계좌 아님)", {}, {"acct": "50KTC-B"}),
            ("반대 방향(진입 LONG, 닫힌 포지션 SHORT)", {}, {"closed": [Position("1001", "150KTC-A", MNQ, -2, {})]}),
            ("모르는 사유", {}, {"why": "cap_tp"}),
            ("닫힌 포지션 없음", {}, {"closed": []}),
            ("종목 없는 포지션", {}, {"closed": [Position("1001", "150KTC-A", "", 2, {})]})):
        mx = xme(sid="S-NQ-20")
        _POSTS.clear()
        n0 = len(_THREADS)
        E.App._send_exit_gap(mx, "NQ", XBroker("projectx", rows), args.get("closed", [P_MNQ_L]), args.get("why", "time_exit"), sent,
                             ref_price=None, acct=args.get("acct", "150KTC-A"))
        ck(f"B5 {nm} → 보고 없음, 스레드 없음, 키 안 씀", _POSTS == [] and len(_THREADS) == n0
           and ("NQ", "S-NQ-20", "exit") not in (getattr(mx, "_gap_sent", None) or set()), (_POSTS, getattr(mx, "_gap_sent", None)))
        dt, n0 = send(mx, "NQ", XBroker("projectx", rows), [P_MNQ_L], "time_exit", sent)
        join_last(n0)
        ck(f"B6 {nm} 뒤 진입한 계좌의 청산은 그대로 보고", len(_POSTS) == 1 and _POSTS[0][1].get("id") == "S-NQ-20", _POSTS)
    mn = SimpleNamespace(_gate=AP, _token=TOK, _pos_today={})
    _POSTS.clear()
    n0 = len(_THREADS)
    E.App._send_exit_gap(mn, "NQ", XBroker("projectx", rows), [P_MNQ_L], "time_exit", sent, acct="150KTC-A")
    ck("B7 최근 진입 기록 없음(손으로 연 포지션, 기록 만료) → 보고 없음", _POSTS == [] and len(_THREADS) == n0, _POSTS)
    mold = xme(sid="S-NQ-21")
    mold._pos_today["NQ"]["until"] = _real_time() - 1
    E.App._send_exit_gap(mold, "NQ", XBroker("projectx", rows), [P_MNQ_L], "time_exit", sent, acct="150KTC-A")
    ck("B8 다음 진입 시각이 지난 기록(지난 신호) → 보고 없음", _POSTS == [] and len(_THREADS) == n0, _POSTS)
    mc = xme(sid="S-NQ-22")
    mc._pos_today["NQ"]["acc"]["projectx:50KTC-B"] = {"MNQ": 1.0}
    _POSTS.clear()
    n0 = len(_THREADS)
    E.App._send_exit_gap(mc, "NQ", XBroker("projectx", rows, delay=0.3), [P_MNQ_L], "time_exit", sent, acct="150KTC-A")
    E.App._send_exit_gap(mc, "NQ", XBroker("projectx", rows, delay=0.3), [Position("1002", "50KTC-B", MNQ, 1, {})], "time_exit", sent,
                         acct="50KTC-B")
    ck("B9 두 계좌 스레드가 같은 순간 청산가를 읽음 → 보고 하나(키는 잠금 안에서 한 스레드만)", join_last(n0) and len(_THREADS) - n0 == 2
       and len(_POSTS) == 1 and ("NQ", "S-NQ-22", "exit") in mc._gap_sent, (len(_THREADS) - n0, _POSTS))

    # ═════ C 조용한 실패, 기다리지 않음, 등급 ═════
    for nm, g in (("Operator(autoentry 끔)", OP), ("게이트 없음", None), ("게이트 ok False", {"ok": False, "caps": {"autoentry": True}})):
        bq = XBroker("projectx", rows)
        mg = xme(sid="S-NQ-30", gate=g)
        _POSTS.clear()
        n0 = len(_THREADS)
        E.App._send_exit_gap(mg, "NQ", bq, [P_MNQ_L], "time_exit", sent, acct="150KTC-A")
        _real_sleep(0.1)
        ck(f"C1 {nm} → 보고 없음, 브로커 조회 없음(_send_gap과 같은 등급 문)", _POSTS == [] and bq.calls == [] and len(_THREADS) == n0,
           (_POSTS, bq.calls))

    bslow = XBroker("projectx", rows, delay=1.0)
    _POSTS.clear()
    dt, n0 = send(xme(sid="S-NQ-31"), "NQ", bslow, [P_MNQ_L], "time_exit", sent)
    ck("C2 청산 기록 조회가 1초 걸려도 호출은 바로 돌아온다(청산 경로를 기다리게 하지 않음)", dt < 0.3, dt)
    join_last(n0)
    ck("C2 느린 조회도 끝나면 보고", len(_POSTS) == 1, _POSTS)

    # 시계를 빨리 돌려 60초 폴링을 바로 끝낸다(스레드의 time.sleep, time.time)
    CL = {"t": _real_time()}
    _o_time, _o_sleep = time.time, time.sleep
    time.time = lambda: CL["t"]
    time.sleep = lambda s: CL.__setitem__("t", CL["t"] + float(s))
    try:
        bexc = XBroker("projectx", exc=RuntimeError("HTTP 500 Trade/search"))
        _POSTS.clear()
        sent2 = CL["t"]
        n0 = len(_THREADS)
        E.App._send_exit_gap(xme(sid="S-NQ-40"), "NQ", bexc, [P_MNQ_L], "time_exit", sent2, acct="150KTC-A")
        ok_j = join_last(n0)
        ck("C3 청산 기록 조회가 매번 실패 → 60초 동안 5초마다 다시 읽고 조용히 끝(예외 없음, 보고 없음)",
           ok_j and _POSTS == [] and 10 <= len(bexc.calls) <= 13, (ok_j, _POSTS, len(bexc.calls)))

        bnone = XBroker("projectx", rows=[])
        _POSTS.clear()
        n0 = len(_THREADS)
        E.App._send_exit_gap(xme(sid="S-NQ-41"), "NQ", bnone, [P_MNQ_L], "time_exit", sent2, acct="150KTC-A")
        ok_j = join_last(n0)
        ck("C4 60초 안에 이번 청산 기록이 안 보임 → 조용히 끝", ok_j and _POSTS == [] and len(bnone.calls) >= 10, (_POSTS, len(bnone.calls)))

        btv = XBroker("tradovate", rows=[{"tid": "fp1", "ts_ms": int((sent2 + 1) * 1000), "symbol": "MNQZ6", "pnl": 5.0,
                                          "direction": "LONG", "acct": "77", "acct_name": "TV1"}])
        _POSTS.clear()
        n0 = len(_THREADS)
        E.App._send_exit_gap(xme(sid="S-NQ-42", acc_key="tradovate:TV1"), "NQ", btv, [Position("77", "TV1", "MNQZ6", 1, {})],
                             "time_exit", sent2, acct="TV1")
        ok_j = join_last(n0)
        ck("C5 Tradovate(청산 기록에 청산가 없음) → 한 번 읽고 바로 끝, 보고 없음", ok_j and _POSTS == [] and len(btv.calls) == 1,
           (_POSTS, btv.calls))

        nob = SimpleNamespace(name="tradovate-old")
        _POSTS.clear()
        n0 = len(_THREADS)
        E.App._send_exit_gap(xme(sid="S-NQ-43"), "NQ", nob, [P_MNQ_L], "time_exit", sent2, acct="150KTC-A")
        ok_j = join_last(n0)
        ck("C6 closed_fills 없는 어댑터 → 조용히 끝", ok_j and _POSTS == [], _POSTS)

        _o_pid = autopilot_crypto.path_id
        autopilot_crypto.path_id = MagicMock(side_effect=ValueError("bad token"))
        try:
            bpid = XBroker("projectx", rows)
            _POSTS.clear()
            n0 = len(_THREADS)
            E.App._send_exit_gap(xme(sid="S-NQ-44"), "NQ", bpid, [P_MNQ_L], "time_exit", sent2, acct="150KTC-A")
            ok_j = join_last(n0)
            ck("C7 토큰 풀이 실패(스레드 안 예외) → 조용히 끝, 브로커 조회도 없음", ok_j and _POSTS == [] and bpid.calls == [], (_POSTS, bpid.calls))
        finally:
            autopilot_crypto.path_id = _o_pid

        rows_now = [dict(rows[0], ts_ms=int((sent2 + 0.8) * 1000))]
        _POST_MODE["m"] = "raise"
        _POSTS.clear()
        n0 = len(_THREADS)
        E.App._send_exit_gap(xme(sid="S-NQ-45"), "NQ", XBroker("projectx", rows_now), [P_MNQ_L], "time_exit", sent2, acct="150KTC-A")
        ok_j = join_last(n0)
        ck("C8 서버 전송 실패 → 세 번 시도 뒤 조용히 끝(예외 없음)", ok_j and len(_POSTS) == 3, (ok_j, len(_POSTS)))
        _POST_MODE["m"] = "ok"

        # 키는 청산가를 읽은 계좌의 스레드가 잡는다 - 첫 계좌 조회가 실패하거나 청산가를 주지 않는 브로커여도 다음 계좌가 보낸다
        P_N8 = Position("LT-A", "LT-A", "MNQ 12-26", 2, {})
        rows_n8 = [{"ts_ms": int((sent2 + 0.9) * 1000), "symbol": "MNQ 12-26", "direction": "LONG", "exit_px": 21002.5, "acct": "LT-A"}]
        for nm, first, facct, fpos in (
                ("첫 계좌(ProjectX) 조회가 계속 실패", XBroker("projectx", exc=RuntimeError("HTTP 500")), "150KTC-A", P_MNQ_L),
                ("첫 계좌가 청산가 없는 Tradovate", XBroker("tradovate", rows=[{"ts_ms": int((sent2 + 1) * 1000), "symbol": "MNQZ6",
                                                                              "direction": "LONG", "pnl": 1.0}]),
                 "TV1", Position("77", "TV1", "MNQZ6", 1, {}))):
            mf = xme(sid="S-NQ-47" + facct)
            mf._pos_today["NQ"]["acc"].update({"tradovate:TV1": {"MNQ": 1.0}, "nt8:LT-A": {"MNQ": 2.0}})
            _POSTS.clear()
            n0 = len(_THREADS)
            E.App._send_exit_gap(mf, "NQ", first, [fpos], "time_exit", sent2, acct=facct)
            E.App._send_exit_gap(mf, "NQ", XBroker("nt8", rows_n8), [P_N8], "time_exit", sent2, acct="LT-A")
            ok_j = join_last(n0)
            ck(f"C9 {nm} → 다음 계좌(NT8)가 보낸다(보고 하나, broker nt8, 청산가 21002.5)", ok_j and len(_POSTS) == 1
               and _POSTS[0][1].get("broker") == "nt8" and _POSTS[0][1].get("fill_price") == 21002.5, (ok_j, _POSTS))
    finally:
        _POST_MODE["m"] = "ok"
        time.time, time.sleep = _o_time, _o_sleep

    # ═════ D 청산 경로 배선(_auto_loop 한 바퀴) ═════
    ET = DT.timezone(DT.timedelta(hours=-5))          # 11월 = EST
    UTC = DT.timezone.utc

    def _me(jobs, gate=AP, fallback=False, live=True):
        m = MagicMock()
        m.lang = "ko"
        m._gate = gate
        m._auto_on = True
        m._exit_override = {}
        m._entered_at = {}
        m._entering_at = {}
        m._auto_accts = {(j["asset"], i): [j] for i, j in enumerate(jobs)}
        m._full_exit = lambda: E.App._full_exit(m)
        m._close_mode = lambda j: (live, fallback)
        m.x2be_decide = E.App.x2be_decide
        m._btc_x2be_step = lambda b, j, lv, tz: E.App._btc_x2be_step(m, b, j, lv, tz)
        m._daycap_mon_targets.return_value = []
        return m

    def run_loop(me, at, closed, qty=None, blk=None, errors=None):
        """_auto_loop 한 바퀴(시각 at). 반환 (로그, 만든 브로커들)."""
        clock = {"now": at}
        builds = []
        orig = (E._build_broker, E._now_in, time.sleep, time.time)

        def _bb(broker, f1, f2, f3, accounts):
            bm = MagicMock()
            bm.name = broker
            bm.flatten_all.return_value = FlattenResult(dry_run=False, planned=list(closed), closed=list(closed),
                                                        errors=list(errors or []))
            bm.position_qty.return_value = qty if qty is not None else 0.01
            bm.list_open_positions.return_value = list(closed)
            bm.block_4h.return_value = blk
            builds.append(bm)
            return bm
        E._build_broker = _bb
        E._now_in = lambda _tz: clock["now"]
        time.time = lambda: clock["now"].timestamp()

        def _sleep(_s):
            me._auto_on = False
        time.sleep = _sleep
        try:
            E.App._auto_loop(me)
        finally:
            E._build_broker, E._now_in, time.sleep, time.time = orig
        return " ".join(str(c[0][0]) for c in me.log.call_args_list if c[0]), builds

    JNQ = {"asset": "NQ", "hour": 14, "tz": "America/New_York", "broker": "projectx", "acct": "150KTC-A", "f1": "k", "f2": "s", "f3": "",
           "live": True}
    at = DT.datetime(2026, 11, 16, 14, 0, 5, tzinfo=ET)
    m = _me([JNQ])
    lg, bs = run_loop(m, at, [P_MNQ_L])
    ca = m._send_exit_gap.call_args
    ck("D1 NQ 정해진 시각 청산 → _send_exit_gap(NQ, 그 브로커, 닫힌 포지션, time_exit, 청산 낸 시각, 기준가 None, 계좌)",
       m._send_exit_gap.call_count == 1 and ca[0][0] == "NQ" and ca[0][1] is bs[0] and ca[0][2] == [P_MNQ_L]
       and ca[0][3] == "time_exit" and ca[0][4] == at.timestamp() and ca[1].get("ref_price", "x") is None
       and ca[1].get("acct") == "150KTC-A", (m._send_exit_gap.call_args_list, lg[-300:]))
    ck("D1 청산 경로는 종전 그대로(flat, 청산 통보)", "✅ flat" in lg and m._send_fill.call_args_list[-1][1].get("closed") is True, lg[-300:])

    m = _me([JNQ])
    m._exit_override = {"NQ": (DT.datetime(2026, 11, 16, 13, 0, tzinfo=ET).timestamp(), "CPI")}
    lg, bs = run_loop(m, DT.datetime(2026, 11, 16, 13, 0, 5, tzinfo=ET), [P_MNQ_L])
    ck("D2 뉴스 선행 청산 → 청산은 하되 청산 체결 보고는 하지 않음", bs and bs[0].flatten_all.call_count == 1
       and m._send_exit_gap.call_count == 0 and "선행 청산 발화" in lg, (m._send_exit_gap.call_args_list, lg[-300:]))

    m = _me([JNQ], live=False)
    lg, bs = run_loop(m, at, [P_MNQ_L])
    ck("D3 dry-run(킬스위치 등) → 보고 없음", m._send_exit_gap.call_count == 0, m._send_exit_gap.call_args_list)
    m = _me([JNQ])
    lg, bs = run_loop(m, at, [])
    ck("D4 닫힌 포지션 없음(이미 플랫) → 보고 없음", m._send_exit_gap.call_count == 0, m._send_exit_gap.call_args_list)

    JBT = {"asset": "BTC", "hour": 22, "tz": "UTC", "broker": "bybit", "acct": "", "f1": "k", "f2": "s", "f3": "", "live": True}
    m = _me([JBT])
    lg, bs = run_loop(m, DT.datetime(2026, 11, 16, 22, 0, 5, tzinfo=UTC), [P_BTC_S], qty=-0.01, blk=(90000.0, 89900.0))
    ca = m._send_exit_gap.call_args
    ck("D5 BTC 24시간 만기(월 22 UTC, 진짜 X2+TR 판정) → btc_expiry, 기준가 None, 계좌 = 브로커 이름",
       m._send_exit_gap.call_count == 1 and ca[0][3] == "btc_expiry" and ca[1].get("ref_price", "x") is None
       and ca[1].get("acct") == "bybit", (m._send_exit_gap.call_args_list, lg[-300:]))

    JB6 = dict(JBT, hour=6)
    m = _me([JB6])
    lg, bs = run_loop(m, DT.datetime(2026, 11, 16, 6, 0, 5, tzinfo=UTC), [P_BTC_S], qty=-0.01, blk=(90000.0, 90200.0))
    ca = m._send_exit_gap.call_args
    ck("D6 BTC 블록 1 숏인데 양봉 마감(진짜 판정 = 청산) → btc_block, 기준가 = 판정에 쓴 봉 종가 90200",
       m._send_exit_gap.call_count == 1 and ca[0][3] == "btc_block" and ca[1].get("ref_price") == 90200.0
       and "청산" in lg, (m._send_exit_gap.call_args_list, lg[-300:]))
    ck("D6 판정이 남긴 봉 기록(같은 잡, k, 방향)", isinstance(m._x2_close, dict) and m._x2_close.get("k") == 1
       and m._x2_close.get("dir") == "SHORT" and m._x2_close.get("c") == 90200.0, m._x2_close)

    m = _me([JB6])
    lg, bs = run_loop(m, DT.datetime(2026, 11, 16, 6, 0, 5, tzinfo=UTC), [P_BTC_S], qty=-0.01, blk=(90200.0, 90000.0))
    ck("D7 BTC 블록 1 순항(숏, 음봉) → 청산 안 함, 보고 없음", m._send_exit_gap.call_count == 0
       and all(b_.flatten_all.call_count == 0 for b_ in bs), (m._send_exit_gap.call_args_list, lg[-300:]))

    m = _me([JB6])
    m._btc_x2be_step = MagicMock(return_value=True)
    m._x2_close = {"j": 12345, "k": 0, "c": 1.0, "dir": "LONG"}          # 다른 잡이 남긴 봉
    lg, bs = run_loop(m, DT.datetime(2026, 11, 16, 6, 0, 5, tzinfo=UTC), [P_BTC_S], qty=-0.01)
    ca = m._send_exit_gap.call_args
    ck("D8 다른 잡의 봉 기록은 기준가로 쓰지 않음(btc_block, 기준가 None)", m._send_exit_gap.call_count == 1 and ca[0][3] == "btc_block"
       and ca[1].get("ref_price", "x") is None, m._send_exit_gap.call_args_list)

    m = _me([JB6], gate={"ok": False, "offline": True}, fallback=True)
    lg, bs = run_loop(m, DT.datetime(2026, 11, 16, 6, 0, 5, tzinfo=UTC), [P_BTC_S], qty=-0.01)
    ck("D9 서버 불통 폴백의 BTC 블록 청산 → 청산은 하되 보고 없음", bs and bs[0].flatten_all.call_count == 1
       and m._send_exit_gap.call_count == 0, (m._send_exit_gap.call_args_list, lg[-300:]))
    m = _me([JBT], fallback=True)
    lg, bs = run_loop(m, DT.datetime(2026, 11, 16, 22, 0, 5, tzinfo=UTC), [P_BTC_S], qty=-0.01)
    ck("D10 폴백이어도 24시간 만기는 btc_expiry", m._send_exit_gap.call_count == 1 and m._send_exit_gap.call_args[0][3] == "btc_expiry",
       m._send_exit_gap.call_args_list)

    # 진짜 _send_exit_gap를 붙여 두 계좌 청산 → 보고 하나(청산이 확인된 첫 계좌)
    JNQ2 = dict(JNQ, acct="50KTC-B")
    m = _me([JNQ, JNQ2])
    m._token = TOK
    m._gap_sent = set()
    _r = rec_of("S-NQ-50", "LONG", "projectx:150KTC-A", now=at.timestamp())     # 기록 시각은 판의 시계(at) 기준
    _r["acc"]["projectx:50KTC-B"] = {"MNQ": 1.0}
    m._pos_today = {"NQ": _r}
    m._send_exit_gap = lambda *a, **k: E.App._send_exit_gap(m, *a, **k)
    _xrows = [{"ts_ms": int((at.timestamp() + 0.7) * 1000), "symbol": MNQ, "direction": "LONG", "exit_px": 21010.0}]
    _POSTS.clear()
    n0 = len(_THREADS)
    # 계좌마다 다른 포지션과 closed_fills를 단 브로커로 한 바퀴(run_loop는 계좌 하나의 포지션만 준다)
    clock = {"now": at}
    builds = []
    orig = (E._build_broker, E._now_in, time.sleep, time.time)

    def _bb2(broker, f1, f2, f3, accounts):
        bm = MagicMock()
        bm.name = broker
        _p = P_MNQ_L if accounts == ["150KTC-A"] else Position("1002", "50KTC-B", MNQ, 1, {"contractId": MNQ})
        bm.flatten_all.return_value = FlattenResult(dry_run=False, planned=[_p], closed=[_p], errors=[])
        bm.closed_fills = lambda start_ms: [dict(r) for r in _xrows]
        builds.append(bm)
        return bm
    E._build_broker = _bb2
    E._now_in = lambda _tz: clock["now"]
    time.time = lambda: clock["now"].timestamp()
    time.sleep = lambda _s: setattr(m, "_auto_on", False)
    try:
        E.App._auto_loop(m)
    finally:
        E._build_broker, E._now_in, time.sleep, time.time = orig
    join_last(n0)
    ck("D11 두 계좌 정해진 시각 청산(진짜 _send_exit_gap) → 두 계좌 모두 닫고 청산 보고는 하나(첫 계좌)",
       len(builds) == 2 and all(b_.flatten_all.call_count == 1 for b_ in builds) and len(_POSTS) == 1
       and _POSTS[0][1].get("id") == "S-NQ-50" and _POSTS[0][1].get("fill_price") == 21010.0
       and _POSTS[0][1].get("sent_ts") == at.timestamp(), (len(builds), _POSTS))

    # ═════ E 보고가 터져도 청산 경로는 끝까지 ═════
    m = _me([JNQ])
    m._send_exit_gap = MagicMock(side_effect=RuntimeError("boom in report"))
    lg, bs = run_loop(m, at, [P_MNQ_L])
    _key = ("NQ", 14, "150KTC-A")
    ck("E1 _send_exit_gap가 예외를 던져도 flat 확인, 청산 통보, 오늘 끝남 기록, 재시도 없음, 오류 보고 없음",
       m._send_exit_gap.call_count == 1 and "✅ flat" in lg and m._send_fill.call_args_list[-1][1].get("closed") is True
       and m._auto_fired.get(_key) == "2026-11-16" and _key not in (m._auto_retry or {})
       and not any(c[0] and c[0][0] == "auto_close" for c in m._report_error.call_args_list)
       and "auto-close failed" not in lg, (lg[-400:], m._auto_fired, m._auto_retry, m._report_error.call_args_list))

    for nm, patch in (("스레드 안 예외(토큰 풀이)", "pid"), ("스레드 시작 실패", "start")):
        m = _me([JNQ])
        m._token = TOK
        m._gap_sent = set()
        m._pos_today = {"NQ": rec_of("S-NQ-60", "LONG", "projectx:150KTC-A", now=at.timestamp())}
        m._send_exit_gap = lambda *a, _m=m, **k: E.App._send_exit_gap(_m, *a, **k)
        _o_pid, _o_st = autopilot_crypto.path_id, threading.Thread.start
        if patch == "pid":
            autopilot_crypto.path_id = MagicMock(side_effect=RuntimeError("thread boom"))
        else:
            def _bad_start(self):
                raise RuntimeError("can't start new thread")
            threading.Thread.start = _bad_start
        _POSTS.clear()
        n0 = len(_THREADS)
        try:
            lg, bs = run_loop(m, at, [P_MNQ_L])
        finally:
            threading.Thread.start = _o_st
            join_last(n0)
            autopilot_crypto.path_id = _o_pid
        _nth = len(_THREADS) - n0
        ck(f"E2 {nm} → 청산 경로 그대로(flat, 청산 통보, 오늘 끝남), 보고 없음",
           "✅ flat" in lg and m._send_fill.call_args_list[-1][1].get("closed") is True
           and m._auto_fired.get(_key) == "2026-11-16" and _POSTS == [] and "auto-close failed" not in lg
           and ("NQ", "S-NQ-60", "exit") not in m._gap_sent and _nth == (1 if patch == "pid" else 0), (lg[-400:], _POSTS, _nth, m._gap_sent))
finally:
    requests.post = _o_post
    threading.Thread.start = _o_start

# 소스 점검: 청산 경로에서 부르는 자리는 하나, try로 감쌈. 3.9 문법(같은 따옴표 중첩 f-string 없음)은 별도 ast 점검.
import inspect  # noqa: E402
_src = inspect.getsource(E.App._auto_loop_body)
ck("F1 _auto_loop_body가 _send_exit_gap을 한 곳에서, try 안에서 부른다", _src.count("self._send_exit_gap(") == 1
   and "try:\n                                    self._send_exit_gap(" in _src)
ck("F2 손절(stop)은 보내지 않는다 - 앱 어디에서도 why='stop'으로 부르지 않음",
   '"stop", _xts' not in _src and "_send_exit_gap(" not in inspect.getsource(E.App._check_stop_closed))

print(f"\n통과 {NPASS[0]}, 실패 {len(FAIL)}" + ("" if not FAIL else ": " + ", ".join(FAIL)))
sys.exit(1 if FAIL else 0)
