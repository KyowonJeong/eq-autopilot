#!/usr/bin/env python3
# Bybit 서버 시각 보정(2026-10-17, 회원 PC 시계 1.1초 빠름 → retCode 10002) 오프라인 시험. 0 = 통과.
#   1 PC 시계가 1.1초 빠르면: 종전 서명 시각은 거절, 새 서명 시각은 통과(서버보다 조금 늦게)
#   2 시각은 10분 캐시(요청마다 재지 않음), 같은 base는 공용, testnet은 따로
#   3 10002가 오면 다시 재고 한 번만 다시 보낸다(주문 POST도 두 번까지만), 계속 거절이면 그대로 올린다
#   4 시각을 못 재면 보정 0(종전과 같고 0.3초만 늦춤), 1분 뒤 다시 잰다 / PC가 3초 느려도 통과
# 가짜 requests(네트워크 없음), EQ_NO_ALERT=1.
import os
import sys
import time
from types import SimpleNamespace

os.environ["EQ_NO_ALERT"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqexec.broker.bybit as BB  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


class Resp:
    def __init__(self, d):
        self.d = d

    def raise_for_status(self):
        pass

    def json(self):
        return self.d


class FakeBybit:
    """서버 시각 = PC 시각 + skew_ms. 규칙: 서버 - 5000 <= ts < 서버 + 1000, 아니면 10002."""

    def __init__(self, skew_ms, time_ok=True):
        self.skew_ms, self.time_ok = skew_ms, time_ok
        self.time_calls, self.gets, self.posts, self.ts_seen = 0, [], [], []

    def _srv(self):
        return int(time.time() * 1000) + self.skew_ms

    def _check(self, headers):
        ts = int(headers["X-BAPI-TIMESTAMP"])
        self.ts_seen.append(ts - self._srv())
        srv = self._srv()
        if not (srv - 5000 <= ts < srv + 1000):
            return Resp({"retCode": 10002, "retMsg": f"invalid request, please check your server timestamp "
                                                      f"req_timestamp[{ts}],server_timestamp[{srv}]"})
        return Resp({"retCode": 0, "result": {"list": []}})

    def get(self, url, headers=None, timeout=None):
        if url.endswith("/v5/market/time"):
            self.time_calls += 1
            if not self.time_ok:
                raise OSError("down")
            srv = self._srv()
            return Resp({"retCode": 0, "result": {"timeSecond": str(srv // 1000), "timeNano": str(srv * 1_000_000)},
                         "time": srv})
        self.gets.append(url)
        return self._check(headers)

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append(url)
        return self._check(headers)


def broker(testnet=False):
    return BB.BybitBroker(SimpleNamespace(api_key="k", api_secret="s", testnet=testnet))


_orig = BB.requests
try:
    # ── 1 ──
    BB._CLOCK.clear()
    f = FakeBybit(-1100)                                # 서버가 1.1초 늦음 = PC가 1.1초 빠름(10/17 실제 값 1116ms)
    BB.requests = SimpleNamespace(get=f.get, post=f.post)
    _old_ts = int(time.time() * 1000)
    ck("1a 종전 서명 시각(PC 시각 그대로)은 거절되는 조건", not (f._srv() - 5000 <= _old_ts < f._srv() + 1000))
    try:
        broker().authenticate(); ok = True
    except Exception as e:
        ok = e
    ck("1b 새 서명 시각은 통과(잔고 읽기)", ok is True and f.gets, ok)
    ck("1c 서버보다 조금 늦게(0 ~ -1초 사이)", f.ts_seen and -1000 < f.ts_seen[-1] <= 0, f.ts_seen)

    # ── 2 ──
    n = f.time_calls
    broker().authenticate(); broker().list_positions() if hasattr(BB.BybitBroker, "list_positions") else None
    ck("2a 10분 안에는 시각을 다시 재지 않는다(다른 객체도 공용)", f.time_calls == n, (n, f.time_calls))
    broker(testnet=True).authenticate()
    ck("2b testnet은 따로 잰다", f.time_calls == n + 1 and "https://api-testnet.bybit.com" in BB._CLOCK)
    BB._CLOCK["https://api.bybit.com"] = (BB._CLOCK["https://api.bybit.com"][0], time.time() - 601)
    broker().authenticate()
    ck("2c 10분 지나면 다시 잰다", f.time_calls == n + 2, f.time_calls)

    # ── 3 ──
    BB._CLOCK.clear()
    BB._CLOCK["https://api.bybit.com"] = (0, time.time() - 10)   # 방금 0으로 잰 값(그 뒤 PC 시계가 1.5초 빨라짐)
    f = FakeBybit(-1500)
    BB.requests = SimpleNamespace(get=f.get, post=f.post)
    try:
        broker()._req("POST", "/v5/order/create", body={"symbol": "BTCUSDT"}); ok = True
    except Exception as e:
        ok = e
    ck("3a 10002 → 다시 재고 한 번 다시 보내 통과(주문 POST 두 번)", ok is True and len(f.posts) == 2 and f.time_calls == 1,
       (ok, f.posts, f.time_calls))
    BB._CLOCK.clear()
    f = FakeBybit(-3000, time_ok=False)                  # 시각을 못 재고 PC가 3초 빠름 → 계속 거절
    BB.requests = SimpleNamespace(get=f.get, post=f.post)
    try:
        broker()._req("POST", "/v5/order/create", body={"symbol": "BTCUSDT"}); r = None
    except RuntimeError as e:
        r = str(e)
    ck("3b 계속 거절이면 두 번에서 멈추고 그대로 올린다(retCode=10002)", r and "retCode=10002" in r and len(f.posts) == 2, (r, f.posts))

    # ── 4 ──
    BB._CLOCK.clear()
    f = FakeBybit(0, time_ok=False)
    BB.requests = SimpleNamespace(get=f.get, post=f.post)
    broker().authenticate()
    ck("4a 시각을 못 재면 보정 0, 0.3초만 늦춤(통과)", f.ts_seen and -400 < f.ts_seen[-1] <= -250, f.ts_seen)
    n = f.time_calls
    broker().authenticate()
    ck("4b 실패 뒤에는 요청마다 재지 않는다", f.time_calls == n, (n, f.time_calls))
    k = "https://api.bybit.com"
    BB._CLOCK[k] = (BB._CLOCK[k][0], BB._CLOCK[k][1] - 61)
    broker().authenticate()
    ck("4c 1분 뒤 다시 잰다", f.time_calls == n + 1, f.time_calls)
    BB._CLOCK.clear()
    f = FakeBybit(3000)                                  # PC가 3초 느림
    BB.requests = SimpleNamespace(get=f.get, post=f.post)
    try:
        broker().authenticate(); ok = True
    except Exception as e:
        ok = e
    ck("4d PC가 3초 느려도 통과", ok is True and -1000 < f.ts_seen[-1] <= 0, (ok, f.ts_seen))
finally:
    BB.requests = _orig
    BB._CLOCK.clear()

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
