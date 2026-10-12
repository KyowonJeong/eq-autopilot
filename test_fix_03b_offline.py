#!/usr/bin/env python3
# EQ Autopilot - 03b 수리 오프라인 시험(2026-10-05). 0=통과
# ①일요일 22:00 UTC BTC는 '24h 만기' 청산 안 함(진입 시각 기록 전이어도) ②월요일 22:00 UTC는 그대로 만기 청산
# ③진입 '시작' 시각(_entering_at)이 방금이면 마감 청산 스킵 ④Bitget 손절 감시 = 플랜 조회(포지션 칸 비어도 '있음')
# ⑤Bitget set_stop = 손절 플랜 전부 같은 값으로 ⑥문구 '본절' → '진입 기준가'
import datetime as _dt
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
from eqexec.broker import bitget as BG  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


AP = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True, "fullexit": True}}


def run(day, hour, entering_ago=None, entered_ago=None):
    """BTC 22시 잡을 한 바퀴. day = 2026-10-04(일)/05(월). 반환 (flatten 호출 수, x2be 호출 수, 로그)."""
    me = MagicMock()
    me.lang = "ko"
    me._gate = AP
    me._auto_on = True
    me._exit_override = {}
    now_ts = time.time()
    me._entered_at = {"BTC": now_ts - entered_ago} if entered_ago is not None else {}
    me._entering_at = {"BTC": now_ts - entering_ago} if entering_ago is not None else {}
    me._auto_accts = {("BTC", 0): [{"asset": "BTC", "hour": 22, "tz": "UTC", "broker": "bybit", "acct": "a1",
                                     "f1": "k", "f2": "s", "f3": "", "live": True}]}
    me._full_exit = lambda: eqgui.App._full_exit(me)
    me._close_mode = lambda j: (True, False)
    me._btc_x2be_step = MagicMock(return_value=True)
    b = MagicMock()
    b.position_qty.return_value = 0.2
    res = MagicMock(); res.planned = [1]; res.closed = []; res.errors = []
    b.flatten_all.return_value = res
    orig_b, orig_now, orig_sleep = eqgui._build_broker, eqgui._now_in, time.sleep
    eqgui._build_broker = lambda *a, **k: b
    eqgui._now_in = lambda _tz: _dt.datetime(2026, 10, day, hour, 0, 11, tzinfo=_dt.timezone.utc)

    def _stop(_s):
        me._auto_on = False
    time.sleep = _stop
    try:
        eqgui.App._auto_loop(me)
    finally:
        eqgui._build_broker, eqgui._now_in, time.sleep = orig_b, orig_now, orig_sleep
    lg = " ".join(str(c[0][0]) for c in me.log.call_args_list)
    return b.flatten_all.call_count, me._btc_x2be_step.call_count, lg


f, x, lg = run(4, 22)
ck("① 일요일 22:00:11 UTC BTC(진입 시각 기록 없음): 만기 청산 안 함", f == 0 and x == 0, (f, x, lg))
f, x, lg = run(5, 22, entered_ago=86400)
ck("② 월요일 22:00 UTC BTC: 만기 판정·청산 그대로", x == 1 and f == 1, (f, x, lg))
f, x, lg = run(5, 22, entering_ago=3)
ck("③ 진입 시작 3초 전(_entering_at)이면 마감 청산 스킵", f == 0 and x == 0 and "스킵" in lg, (f, x, lg))

# ④ Bitget 손절 감시: 플랜이 있으면 포지션 stopLoss 칸이 비어도 '있음'
me = MagicMock()
me._nostop_seen = {}
b = MagicMock(spec=["stop_plans", "set_stop"])
b.stop_plans.return_value = [{"orderId": "1", "price": 85258.9, "triggerType": "mark_price", "planType": "loss_plan"}]
me._open_ctx = {"BTC": {"b": b, "sym": "BTCUSDT", "stop": 85258.9}}
pos = SimpleNamespace(raw={"stopLoss": ""}, symbol="BTCUSDT", net_qty=0.2)
for _ in range(3):
    eqgui.App._check_stop_alive(me, "BTC", pos)
ck("④ Bitget: 플랜 있으면 '미발견' 없음, 손절 추가 없음", b.set_stop.call_count == 0
   and not any("미발견" in str(c[0][0]) for c in me.log.call_args_list), me.log.call_args_list)
b.stop_plans.side_effect = RuntimeError("net")
eqgui.App._check_stop_alive(me, "BTC", pos)
ck("④ Bitget: 플랜 조회 실패 = 판단 안 함(손절 추가 없음)", b.set_stop.call_count == 0)

# ⑤ Bitget set_stop: 손절 플랜 둘 → 둘 다 같은 값으로
bg = BG.BitgetBroker.__new__(BG.BitgetBroker)
bg.product = "USDT-FUTURES"
CALLS = []


def fake_req(method, path, params=None, body=None):
    CALLS.append((method, path, dict(body or {})))
    if path.endswith("orders-plan-pending"):
        return {"entrustedList": [
            {"orderId": "A", "planType": "loss_plan", "symbol": "BTCUSDT", "triggerPrice": "85258.9", "triggerType": "mark_price"},
            {"orderId": "B", "planType": "pos_loss", "symbol": "BTCUSDT", "triggerPrice": "85258.9", "triggerType": "mark_price"},
            {"orderId": "C", "planType": "profit_plan", "symbol": "BTCUSDT", "triggerPrice": "90000"}]}
    return {}


bg._req = fake_req
bg._snap_px = lambda sym, px: str(px)
r = bg.set_stop("BTCUSDT.P", 85825.8)
mods = [c for c in CALLS if c[1].endswith("modify-tpsl-order")]
ck("⑤ 손절 플랜 둘 다 수정(익절 플랜은 무접촉), 신규 등록 없음",
   not r.get("error") and sorted(m[2]["orderId"] for m in mods) == ["A", "B"]
   and all(m[2]["triggerPrice"] == "85825.8" for m in mods)
   and not any(c[1].endswith("place-tpsl-order") for c in CALLS), (r, CALLS))
ck("⑤ stop_plans는 손절 플랜만(2건)", len(bg.stop_plans("BTCUSDT")) == 2)

# ⑦ 회원 DM 마스킹이 가격을 먹지 않음(2026-10-05 '#.9')
_m = MagicMock(); _m._acfg = {"BTC": {"accounts": [{"id": "UID12345678"}]}}
_mk = lambda t: eqgui.App._mask_ids(_m, t)
ck("⑦ 손절가 85258.9·85,826·21500.25 그대로", _mk("앱이 85258.9에 다시 걸었습니다 85,826 21500.25") == "앱이 85258.9에 다시 걸었습니다 85,826 21500.25", _mk("앱이 85258.9에 다시 걸었습니다 85,826 21500.25"))
ck("⑦ 계좌 ID는 끝4, 8자리+ 정수열은 #", _mk("UID12345678 주문 123456789") == "…5678 주문 #", _mk("UID12345678 주문 123456789"))
ck("⑦ 소수점 없는 6~7자리 가격(BTC 100250, 1234567)도 그대로", _mk("손절 100250 진입 101000 목표 1234567") == "손절 100250 진입 101000 목표 1234567", _mk("손절 100250 진입 101000 목표 1234567"))
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("⑥ 로그 문구 '본절(=블록0 시가)'·'(본절)' 없음, '진입 기준가' 있음",
   "본절(=블록0 시가)" not in src and '" (본절)"' not in src and "진입 기준가(=블록0 시가)" in src)
import re as _re
_v = _re.search(r'_APP_VER = "([^"]+)"', src).group(1)
ck("버전 03b 이상", _v >= "2026.10.03b", _v)
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
