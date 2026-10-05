#!/usr/bin/env python3
# EQ Autopilot - IBKR 9/14 감사 '다음 차' 수리 오프라인 테스트(2026-09-30, TWS 없이 가짜 IB). 0=통과
# ① close_contract: 청산 먼저 → 플랫 확인 → 그다음 우리 스탑 취소. 안 채워지면 예외 + 스탑 유지(알몸 '완료' 금지)
# ② 포지션 조회 실패는 예외(플랫 둔갑 금지)  ③ IB 미니 localSymbol(NQZ6·GCZ6) 자산 인식  ④ 롤 당일 같은 자산 옛 월물 = 잔여
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
from eqexec.broker import ibkr as IB  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


class FakeIB:
    def __init__(self, qty, fills_after=1, fail_req=False):
        self.qty, self.fills_after, self.fail_req, self.slept, self.events = qty, fills_after, fail_req, 0, []

    def reqPositions(self):
        if self.fail_req:
            raise ConnectionError("socket lost")

    def positions(self, acct=None):
        if self.qty == 0:
            return []
        c = types.SimpleNamespace(conId=7, localSymbol="MNQZ6")
        return [types.SimpleNamespace(contract=c, position=self.qty)]

    def placeOrder(self, c, o):
        self.events.append("close")
        return types.SimpleNamespace(order=o)

    def sleep(self, s):
        self.slept += 1
        if self.fills_after is not None and self.slept >= self.fills_after:
            self.qty = 0


def broker(fake):
    b = IB.IBKRBroker.__new__(IB.IBKRBroker)
    import threading
    b._lock = threading.RLock()
    b._connect = lambda: fake
    b._acct_id = lambda a: "DU1"
    b._contract = lambda c: types.SimpleNamespace(conId=7, localSymbol="MNQZ6")
    b._mk = lambda ibi, *a, **k: types.SimpleNamespace(kind=a[0])
    b._ack = lambda ib, t, wait_s=None: None
    b._odict = lambda t: {}
    b._cancel_for = lambda ib, aid, c: (fake.events.append("cancel_stop") or 1)
    return b


IB._ib_classes = lambda: None
f = FakeIB(qty=2, fills_after=2)
r = broker(f).close_contract("DU1", "MNQZ6")
ck("① 체결되면 완료 + 청산 뒤에 스탑 취소", r["closed"] == 2 and f.events == ["close", "cancel_stop"], (r, f.events))
f = FakeIB(qty=2, fills_after=None)
try:
    broker(f).close_contract("DU1", "MNQZ6"); ok = False
except RuntimeError as e:
    ok = "unconfirmed" in str(e)
ck("① 안 채워지면 예외 + 스탑 안 지움", ok and "cancel_stop" not in f.events, f.events)
f = FakeIB(qty=0)
r = broker(f).close_contract("DU1", "MNQZ6")
ck("① 플랫이면 주문 없이 고아 스탑만 걷음", r["closed"] == 0 and f.events == ["cancel_stop"], f.events)
f = FakeIB(qty=2, fail_req=True)
try:
    broker(f)._positions("DU1"); ok = False
except ConnectionError:
    ok = True
ck("② 조회 실패 = 예외(플랫 둔갑 아님)", ok)
fa = eqgui.App._fill_asset
ck("③ IB 미니·마이크로 인식", [fa(x) for x in ("NQZ6", "GCZ6", "MNQZ6", "MGCZ6", "NQH27")] == ["NQ", "GC", "NQ", "GC", "NQ"])
ck("③ 다른 상품은 None", [fa(x) for x in ("ESZ6", "SIZ6", "CLZ6")] == [None, None, None])
ck("③ 기존 형식 그대로", fa("CON.F.US.MNQ.Z26") == "NQ" and fa("BTCUSDT") == "BTC")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("④ 롤 당일: 같은 자산 옛 월물을 잔여로(분류·확인 두 곳)", src.count("_same(p.symbol)") == 2 and "_same(q.symbol)" in src)
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
