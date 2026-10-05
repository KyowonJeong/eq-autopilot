#!/usr/bin/env python3
# EQ Autopilot - Operator 청산 범위 오프라인 테스트(대표 2026-09-30 "앱에 정해진 마지노 청산만 오퍼레이터"). 0=통과
# Operator(caps.fullexit False, 옛 서버면 autoentry False): BTC 4시간 봉 판정(반대봉 청산·손절 이동) 안 함, 24시간 만기(블록 5)에만 닫음.
# Autopilot: 종전 그대로 매 블록 판정. 서버 불통 폴백: 등급과 무관하게 그 블록에서 닫음. 선물 세션 마감 청산은 두 등급 모두.
import datetime as _dt
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


OP_NEW = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": False, "fullexit": False}}
OP_OLD = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": False}}
AP_NEW = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True, "fullexit": True}}
AP_OLD = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True}}
OFF = {"ok": False, "offline": True, "reason": "heartbeat error"}


def run(gate, asset, hour, fallback=False):
    """_auto_loop를 한 바퀴만 돌린다. 반환 (x2be_step 호출 수, flatten_all 호출 수, 로그)."""
    me = MagicMock()
    me.lang = "ko"
    me._gate = gate
    me._auto_on = True
    me._exit_override = {}
    me._entered_at = {}
    tz = "UTC" if asset == "BTC" else "America/New_York"
    me._auto_accts = {(asset, 0): [{"asset": asset, "hour": hour, "tz": tz, "broker": "bybit", "acct": "a1",
                                    "f1": "k", "f2": "s", "f3": "", "live": True}]}
    me._full_exit = lambda: eqgui.App._full_exit(me)
    me._close_mode = lambda j: (True, fallback)
    me._btc_x2be_step = MagicMock(return_value=False)
    b = MagicMock()
    b.position_qty.return_value = 0.01
    res = MagicMock(); res.planned = []; res.closed = []; res.errors = []
    b.flatten_all.return_value = res
    orig_b, orig_now, orig_sleep = eqgui._build_broker, eqgui._now_in, time.sleep
    eqgui._build_broker = lambda *a, **k: b
    eqgui._now_in = lambda _tz: _dt.datetime(2026, 10, 5, hour, 1)

    def _stop(_s):
        me._auto_on = False
    time.sleep = _stop
    try:
        eqgui.App._auto_loop(me)
    finally:
        eqgui._build_broker, eqgui._now_in, time.sleep = orig_b, orig_now, orig_sleep
    lg = " ".join(str(c[0][0]) for c in me.log.call_args_list)
    return me._btc_x2be_step.call_count, b.flatten_all.call_count, lg


for nm, g in (("새 서버", OP_NEW), ("옛 서버", OP_OLD)):
    x, f, lg = run(g, "BTC", 6)                        # 블록 1
    ck(f"Operator({nm}) BTC 블록 1: 판정·청산 안 함 + 사실 한 줄", x == 0 and f == 0 and "24시간 만기" in lg, (x, f, lg))
    x, f, lg = run(g, "BTC", 22)                       # 블록 5 = 24시간 만기
    ck(f"Operator({nm}) BTC 24시간 만기: 판정 없이 청산", x == 0 and f == 1, (x, f, lg))
    x, f, lg = run(g, "NQ", 14)
    ck(f"Operator({nm}) 선물 세션 마감 청산 그대로", f == 1, (x, f, lg))

for nm, g in (("새 서버", AP_NEW), ("옛 서버", AP_OLD)):
    x, f, lg = run(g, "BTC", 6)
    ck(f"Autopilot({nm}) BTC 블록 1: 봉 판정 그대로(보유면 청산 안 함)", x == 1 and f == 0, (x, f, lg))
    x, f, lg = run(g, "BTC", 22)
    ck(f"Autopilot({nm}) BTC 블록 5: 봉 판정 그대로", x == 1, (x, f, lg))

x, f, lg = run(OFF, "BTC", 6, fallback=True)
ck("서버 불통 폴백: Operator 여부와 무관하게 이 블록에서 청산", x == 0 and f == 1 and "4시간 봉 판정은 메시지로" not in lg, (x, f, lg))
x, f, lg = run(OP_NEW, "BTC", 6)
ck("Operator 문구에 회원 지시 금지어 없음", "닫아 주" not in lg, lg)
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
