#!/usr/bin/env python3
# EQ Autopilot - NT8 진입 확인 개선 오프라인 테스트(2026-09-28 대표 "빠르게 좀 해 봐", 9/28 NQ 18초 간격 재진입)
# (a) 주문 상태 Filled면 포지션 스냅샷을 기다리지 않고 확정  (b) 주문이 살아 있으면(Working) 재진입 대신 대기
# (c) 확인까지 걸린 초를 로그에  + 종전 동작: 거절(Rejected)이면 재진입.
# 가짜 시계(time.time/sleep)와 가짜 NT8 브로커 - 실제 주문·네트워크 0.
# 사용: python executor/test_nt8_confirm_offline.py   (0=통과, 1=실패)
import os, sys, time, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
fails = []


def chk(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{d}]"))
    if not c:
        fails.append(n)


CLK = {"t": 1_000_000.0}
time.time = lambda: CLK["t"]
time.sleep = lambda s: CLK.__setitem__("t", CLK["t"] + float(s))


class NT8Broker:
    """시나리오: fill_at = 접수 뒤 몇 초에 주문이 Filled가 되나, pos_at = 포지션 스냅샷이 몇 초에 보이나(None=안 보임),
    state_before = 체결 전 주문 상태, reject = 거절."""
    def __init__(self, fill_at=None, pos_at=None, state_before="Working", reject=False):
        self.fill_at, self.pos_at, self.state_before, self.reject = fill_at, pos_at, state_before, reject
        self.placed, self.flattened, self.sub_t = [], 0, None

    def _accounts(self):
        return [{"name": "SIM1", "id": "SIM1"}]

    def place_entry(self, **k):
        self.placed.append(k.get("custom_tag")); self.sub_t = time.time()
        return {"order_id": "o" + str(len(self.placed))}

    def _age(self):
        return time.time() - (self.sub_t or time.time())

    def position_qty(self, aid, con):
        return 3 if (self.pos_at is not None and self._age() >= self.pos_at) else 0

    def order_status(self, aid, tag):
        if self.reject:
            return {"entry": {"state": "Rejected", "reason": "test reject"}}
        if self.fill_at is not None and self._age() >= self.fill_at and tag == self.placed[-1]:
            return {"entry": {"state": "Filled", "filled": 3}}
        return {"entry": {"state": self.state_before}}

    def list_open_positions(self):
        return []

    def __getattr__(self, n):                      # 확정 뒤 손절·보고 단계의 나머지 호출은 무해하게
        return lambda *a, **k: None

    def close_contract(self, aid, con):
        self.flattened += 1

    def current_market_price(self, con):
        return 0


class Stub:
    lang = "ko"

    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(str(m))

    def _claim_entry(self, *a, **k):
        return True

    def _resolve_contract(self, b, s):
        return "MNQ 12-26"

    def _nt8_standby(self):
        return False

    def _net_probe(self):
        return "ok"

    def __getattr__(self, n):
        return lambda *a, **k: None


def run(b):
    app = Stub()
    import datetime as _d
    try:
        eqgui.App._run_futures_entry(app, b, "SIM1", [("MNQ", 3)], "SHORT", 21000.0,
                                     {"id": "t-NQ-1", "entry_ref": 20900.0}, True,
                                     _d.datetime.now(), "NQ", None)
    except Exception as e:                       # 확정 뒤 손절·보고 단계의 스텁 한계는 판정 대상이 아니다
        app.logs.append(f"EXC {type(e).__name__}: {e}")
    return app


# (a) 3초에 Filled, 포지션 스냅샷은 끝까지 안 보임 → 재진입 없이 확정
b = NT8Broker(fill_at=3, pos_at=None)
a = run(b)
chk("(a) 주문 Filled로 확정·재진입 0", len(b.placed) == 1 and b.flattened == 0
    and any("체결 확인 ×3" in m and "주문 상태 Filled" in m for m in a.logs), (b.placed, b.flattened, a.logs[-6:]))
chk("(c) 확인까지 초가 로그에", any("접수 후 3." in m for m in a.logs), [m for m in a.logs if "체결 확인" in m])
# (b) 20초까지 Working, 20초에 Filled → 15초 창 뒤에도 재진입하지 않고 기다려 확정
b = NT8Broker(fill_at=20, pos_at=None, state_before="Working")
a = run(b)
chk("(b) Working이면 재진입 대신 대기 → 확정", len(b.placed) == 1 and b.flattened == 0
    and any("재진입하지 않고 계속 확인" in m for m in a.logs) and any("대기 뒤" in m for m in a.logs),
    (b.placed, b.flattened, a.logs[-6:]))
# 종전: 거절이면 재진입(Flatten 뒤 새 주문)
b = NT8Broker(reject=True)
a = run(b)
chk("거절 → 종전대로 재진입(Flatten 뒤 새 태그)", len(b.placed) >= 2 and b.flattened >= 1, (b.placed, b.flattened))
# 포지션이 2초에 보이면 종전대로 즉시 확정
b = NT8Broker(fill_at=None, pos_at=2, state_before="Working")
a = run(b)
chk("포지션 2초 → 즉시 확정(포지션)", len(b.placed) == 1 and any("체결 확인 ×3" in m and "포지션" in m for m in a.logs),
    a.logs[-4:])
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
