#!/usr/bin/env python3
# ProjectX 실시간 허브(2026-10-16) 오프라인 시험 - 가짜 웹소켓. 0 = 통과.
#   1 핸드셰이크, 구독 재등록(붙을 때마다), 시세 이벤트 → quote, 나이와 '첫 읽기 뒤' 조건, 0 이하와 숫자 아님은 버림
#   2 사용자 이벤트 → 계좌 이벤트 시각, 다른 대상 무시
#   3 끊기면 다시 붙고 구독을 다시 건다, 서버 닫힘(type 7)도 다시 붙는다, 토큰 없으면 붙지 않는다
#   4 get_rt: 꺼짐(EQ_PX_RT_OFF=1), ProjectX가 아니면 None, 같은 자격증명은 같은 연결
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

os.environ["EQ_NO_ALERT"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.modules.setdefault("websocket", SimpleNamespace(create_connection=None))   # 시험은 가짜 연결을 쓴다(get_rt의 라이브러리 확인만 통과)
import eqexec.broker.projectx_rt as RT  # noqa: E402

RT._BACKOFF = (0.05, 0.05, 0.05)
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


class FakeWS:
    """서버 쪽 프레임을 큐로 흉내. recv는 큐가 비면 timeout 예외."""

    def __init__(self, url, log):
        self.url, self.log, self.q, self.closed = url, log, [], False
        self.sent = []
        self.q.append("{}" + RT.RS)

    def send(self, s):
        self.sent.append(s)
        self.log.append(("send", self.url.split("?")[0], s))

    def settimeout(self, t):
        pass

    def recv(self):
        t0 = time.time()
        while not self.q:
            if self.closed:
                raise ConnectionError("closed")
            if time.time() - t0 > 0.05:
                raise TimeoutError("timeout")
            time.sleep(0.005)
        return self.q.pop(0)

    def push(self, obj):
        self.q.append(json.dumps(obj) + RT.RS)

    def close(self):
        self.closed = True


conns, log = [], []


def _connect(url):
    w = FakeWS(url, log)
    conns.append(w)
    return w


def wait(fn, t=2.0):
    t0 = time.time()
    while time.time() - t0 < t:
        if fn():
            return True
        time.sleep(0.01)
    return False


rt = RT.PxRealtime(lambda refresh=False: "TOK", base="wss://x.test", connect=_connect)
ck("1a 두 허브에 붙는다(access_token)", wait(lambda: rt.market.connected and rt.user.connected)
   and all("access_token=TOK" in w.url for w in conns), [w.url for w in conns])
mk = next(w for w in conns if "/hubs/market" in w.url)
us = next(w for w in conns if "/hubs/user" in w.url)
ck("1b 핸드셰이크 먼저", json.loads(mk.sent[0].rstrip(RT.RS)) == {"protocol": "json", "version": 1})
ck("1c 사용자 허브는 붙자마자 SubscribeAccounts", wait(lambda: any('"SubscribeAccounts"' in s for s in us.sent)), us.sent)
rt.watch_contract("CON.F.US.MGC.Z26")
ck("1d 계약 구독", wait(lambda: any('"SubscribeContractQuotes"' in s and "MGC.Z26" in s for s in mk.sent)), mk.sent)
mk.push({"type": 1, "target": "GatewayQuote", "arguments": ["CON.F.US.MGC.Z26", {"lastPrice": 4210.5}]})
ck("1e 시세 이벤트 → quote", wait(lambda: rt.quote("CON.F.US.MGC.Z26") is not None)
   and rt.quote("CON.F.US.MGC.Z26")[0] == 4210.5)
_t1 = rt.quote("CON.F.US.MGC.Z26")[1]
ck("1f 첫 읽기 뒤 받은 것만(같은 push로 두 번 확인 안 됨)", rt.quote("CON.F.US.MGC.Z26", after_ts=_t1) is None)
mk.push({"type": 1, "target": "GatewayQuote", "arguments": ["CON.F.US.MGC.Z26", {"lastPrice": 4211.0}]})
ck("1g 새 push면 통과", wait(lambda: rt.quote("CON.F.US.MGC.Z26", after_ts=_t1) is not None))
mk.push({"type": 1, "target": "GatewayQuote", "arguments": ["CON.F.US.MGC.Z26", {"lastPrice": 0}]})
mk.push({"type": 1, "target": "GatewayQuote", "arguments": ["CON.F.US.MGC.Z26", {"lastPrice": "x"}]})
time.sleep(0.1)
ck("1h 0 이하, 숫자 아님은 버림(마지막 정상 값 유지)", rt.quote("CON.F.US.MGC.Z26")[0] == 4211.0)
ck("1i 나이 넘으면 None", rt.quote("CON.F.US.MGC.Z26", max_age=0.0) is None)
ck("1j 허브가 살아 있음(방금 수신, 최근 시세)", rt.market_live() and not rt.market_live(within=0.0) and not rt.market_live(quote_within=0.0))

rt.watch_account(4321)
ck("2a 계좌 구독(포지션, 체결, 주문)", wait(lambda: sum(1 for s in us.sent if "4321" in s) >= 3), us.sent)
us.push({"type": 1, "target": "GatewayUserTrade", "arguments": [{"accountId": 4321, "profitAndLoss": 50}]})
ck("2b 체결 이벤트 → 계좌 이벤트 시각", wait(lambda: rt.acct_event_ts(4321) > 0))
us.push({"type": 1, "target": "Other", "arguments": [{"accountId": 999}]})
time.sleep(0.1)
ck("2c 다른 대상 무시", rt.acct_event_ts(999) == 0.0)

n0 = len(conns)
mk.close()
ck("3a 끊기면 다시 붙는다", wait(lambda: len(conns) > n0 and rt.market.connected, 3.0), len(conns))
mk2 = conns[-1]
ck("3b 다시 붙으면 계약 구독을 다시 건다", wait(lambda: any("MGC.Z26" in s for s in mk2.sent)), mk2.sent)
n1 = len(conns)
mk2.push({"type": 7, "error": "bye"})
ck("3c 서버 닫힘(type 7)도 다시 붙는다", wait(lambda: len(conns) > n1, 3.0))
rt.close()
c0 = []
rt2 = RT.PxRealtime(lambda refresh=False: None, base="wss://x.test", connect=lambda u: c0.append(u) or FakeWS(u, []))
time.sleep(0.3)
ck("3d 토큰 없으면 붙지 않는다", c0 == [] and not rt2.market.connected)
rt2.close()

os.environ["EQ_PX_RT_OFF"] = "1"
ck("4a 꺼짐 = None", RT.get_rt(SimpleNamespace(name="projectx", cfg=SimpleNamespace(base_url="b", user_name="u", api_key="k"))) is None)
os.environ.pop("EQ_PX_RT_OFF")
ck("4b ProjectX가 아니면 None", RT.get_rt(SimpleNamespace(name="nt8")) is None)
_orig = RT.PxRealtime
RT.PxRealtime = lambda tok, base=None, connect=None: SimpleNamespace(tok=tok)
try:
    b1 = SimpleNamespace(name="projectx", cfg=SimpleNamespace(base_url="b", user_name="u", api_key="k"))
    b2 = SimpleNamespace(name="projectx", cfg=SimpleNamespace(base_url="b", user_name="u", api_key="k"))
    b3 = SimpleNamespace(name="projectx", cfg=SimpleNamespace(base_url="b", user_name="v", api_key="k"))
    ck("4c 같은 자격증명은 같은 연결, 다르면 따로", RT.get_rt(b1) is RT.get_rt(b2) and RT.get_rt(b3) is not RT.get_rt(b1))
finally:
    RT.PxRealtime = _orig

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
