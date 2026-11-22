#!/usr/bin/env python3
# 자산별 청산(대표 2026-10-17 "자산별 청산 버튼", "지금 바로", "비트코인은 바이빗 빗겟 다 봐") 오프라인 시험. 0 = 통과.
#   1 종목 이름 → 자산: ProjectX, NinjaTrader, Tradovate, IBKR, Bybit, Bitget 꼴, 'BTCUSDT.P', 모르는 종목은 어느 자산도 아님
#   2 브로커 여섯 곳 flatten_all(only=그 자산): 그 자산 종목만 닫고 주문도 그 종목만 걷는다. 다른 종목, 다른 코인은 그대로이고
#     오류로 세지 않는다. only 없음 = 종전대로 계좌 전부
#   3 앱 배선: 정해진 시각 청산은 _flatten_asset(그 자산), [모든 포지션 청산]은 종전대로 전부, [자산별 청산] 메뉴, 옛 어댑터 폴백
# 네트워크 없음(가짜 요청), EQ_NO_ALERT=1, time.sleep 없앰.
import inspect
import os
import sys
import threading
import types
from types import SimpleNamespace

os.environ["EQ_NO_ALERT"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from eqexec import asset_scope as AS  # noqa: E402
from eqexec.broker.base import Position  # noqa: E402
import eqexec.broker.bybit as BY  # noqa: E402
import eqexec.broker.bitget as BG  # noqa: E402
import eqexec.broker.projectx as PX  # noqa: E402
import eqexec.broker.tradovate as TV  # noqa: E402
import eqexec.broker.nt8 as N8  # noqa: E402
import eqexec.broker.ibkr as IB  # noqa: E402

for _m in (BY, BG, PX, TV, N8, IB):
    if hasattr(_m, "time"):
        _m.time = SimpleNamespace(sleep=lambda s: None, time=__import__("time").time)

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


# ── 1 종목 → 자산 ────────────────────────────────────────────────────────────
cases = {"CON.F.US.MNQ.Z26": "NQ", "CON.F.US.MGC.Z26": "GC", "CON.F.US.MES.Z26": "", "MNQ 12-26": "NQ", "NQ 12-26": "NQ",
         "MGC 12-26": "GC", "MNQZ6": "NQ", "NQZ26": "NQ", "MGCZ6": "GC", "GCZ26": "GC", "ENQZ5": "NQ", "MESZ6": "", "M2KZ6": "",
         "BTCUSDT": "BTC", "BTCUSDT.P": "BTC", "ETHUSDT": "", "SOLUSDT": "", "BTC-PERP": "BTC", "": "", None: "", "XYZ": ""}
bad = {k: (AS.asset_of(k), v) for k, v in cases.items() if AS.asset_of(k) != v}
ck("1a 브로커별 종목 이름 → 자산(모르는 종목은 '')", not bad, bad)
ck("1b matcher: 모르는 자산은 None, 아는 자산은 그 종목만 True", AS.matcher("ES") is None
   and AS.matcher("BTC")("BTCUSDT") and not AS.matcher("BTC")("ETHUSDT") and AS.matcher("nq")("MNQZ6"))
ck("1c 이름을 해석할 수 없는 포지션(빈 이름, 숫자 계약 ID)은 닫는 쪽(청산 누락 방지), 다른 종목 이름은 닫지 않음",
   AS.matcher("NQ")("") and AS.matcher("NQ")("4567890") and AS.matcher("BTC")(None)
   and not AS.matcher("NQ")("MESZ6") and not AS.matcher("NQ")("CON.F.US.MES.Z26") and not AS.matcher("BTC")("ETHUSDT"))


# ── 2 브로커 여섯 곳 ──────────────────────────────────────────────────────────
def P(acct, sym, q, **raw):
    return Position(account_id=str(acct), account_name=str(acct), symbol=sym, net_qty=q, raw=dict(raw))


# Bybit: BTC와 ETH 무기한이 같은 계좌에
class FBy(BY.BybitBroker):
    def __init__(self):
        self.category, self.settle, self.base = "linear", "USDT", "https://x.test"
        self.pos = {"BTCUSDT": 0.5, "ETHUSDT": 2.0}
        self.orders = []

    def list_open_positions(self):
        return [P("bybit", s, q, size=str(abs(q))) for s, q in self.pos.items() if q]

    def _req(self, method, path, params=None, body=None, _retry=True):
        if path == "/v5/order/create":
            self.orders.append(body["symbol"])
            self.pos[body["symbol"]] = 0
        return {}


b = FBy()
r = b.flatten_all(dry_run=False, only=AS.matcher("BTC"))
ck("2a Bybit: 비트코인 청산은 BTCUSDT만, ETH는 그대로이고 오류 아님", b.orders == ["BTCUSDT"] and b.pos["ETHUSDT"] == 2.0
   and not r.errors and [p.symbol for p in r.closed] == ["BTCUSDT"], (b.orders, b.pos, r.errors))
b = FBy()
r = b.flatten_all(dry_run=False)
ck("2b Bybit: only 없음 = 종전대로 전부", sorted(b.orders) == ["BTCUSDT", "ETHUSDT"] and not r.errors, b.orders)
b = FBy()
r = b.flatten_all(dry_run=True, only=AS.matcher("BTC"))
ck("2c Bybit dry-run: 계획에 BTC만", [p.symbol for p in r.planned] == ["BTCUSDT"] and not b.orders)


# Bitget: 상품 전체 flash close 대신 종목 지정
class FBg(BG.BitgetBroker):
    def __init__(self):
        self.product = "USDT-FUTURES"
        self.pos = {"BTCUSDT": 0.5, "ETHUSDT": 2.0}
        self.calls = []

    def list_open_positions(self):
        return [P("bitget", s, q) for s, q in self.pos.items() if q]

    def _req(self, method, path, params=None, body=None):
        if path == "/api/v2/mix/order/close-positions":
            self.calls.append(dict(body))
            if "symbol" in body:
                self.pos[body["symbol"]] = 0
            else:
                self.pos = {k: 0 for k in self.pos}
        return {}


b = FBg()
r = b.flatten_all(dry_run=False, only=AS.matcher("BTC"))
ck("2d Bitget: 비트코인 청산은 symbol=BTCUSDT 지정 요청 하나, ETH 그대로", b.calls == [{"symbol": "BTCUSDT", "productType": "USDT-FUTURES"}]
   and b.pos["ETHUSDT"] == 2.0 and not r.errors, (b.calls, b.pos, r.errors))
b = FBg()
r = b.flatten_all(dry_run=False)
ck("2e Bitget: only 없음 = 종전대로 상품 전체 요청", b.calls == [{"productType": "USDT-FUTURES"}] and not r.errors, b.calls)


# ProjectX: 같은 계좌에 MNQ(앱), MGC(앱), MES(회원 손매매) + 주문(MNQ 손절, MES 회원 지정가)
class FPx(PX.ProjectXBroker):
    def __init__(self):
        self.cfg = SimpleNamespace(accounts=["S1"], base_url="https://x.test", user_name="u", api_key="k")
        self.pos = {"CON.F.US.MNQ.Z26": 3, "CON.F.US.MGC.Z26": 2, "CON.F.US.MES.Z26": 1}
        self.closed, self.cancelled = [], []
        self.ords = [{"id": 1, "contractId": "CON.F.US.MNQ.Z26"}, {"id": 2, "contractId": "CON.F.US.MES.Z26"},
                     {"id": 3, "contractId": "CON.F.US.MGC.Z26"}]

    def list_open_positions(self, acct_max_age=None):
        return [P("S1", c, q, _accountId=11, contractId=c) for c, q in self.pos.items() if q]

    def _post(self, path, body, timeout=None):
        if path == "/api/Position/closeContract":
            self.closed.append(body["contractId"])
            self.pos[body["contractId"]] = 0
        return {}

    def _accounts(self, *a, **k):
        return [{"id": 11, "name": "S1"}]

    def _open_orders(self, aid):
        return list(self.ords)

    def _cancel_order(self, aid, oid):
        self.cancelled.append(oid)


b = FPx()
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2f ProjectX: 나스닥 청산은 MNQ만, 금과 회원 MES는 그대로", b.closed == ["CON.F.US.MNQ.Z26"] and b.pos["CON.F.US.MGC.Z26"] == 2
   and b.pos["CON.F.US.MES.Z26"] == 1 and not r.errors, (b.closed, b.pos, r.errors))
ck("2g ProjectX: 주문도 MNQ 것만 걷고, 회원 MES 주문과 아직 열린 금 손절은 그대로", b.cancelled == [1], b.cancelled)
b = FPx()
r = b.flatten_all(dry_run=False)
ck("2h ProjectX: only 없음 = 종전대로 전부, 주문 전부", sorted(b.closed) == sorted(["CON.F.US.MNQ.Z26", "CON.F.US.MGC.Z26", "CON.F.US.MES.Z26"])
   and sorted(b.cancelled) == [1, 2, 3], (b.closed, b.cancelled))
b = FPx()
b.cfg.accounts = []
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2i ProjectX: 계좌 지정 없으면 only가 있어도 거부(종전 안전장치 그대로)", r.errors and not b.closed, r.errors)


# Tradovate
class FTv(TV.TradovateBroker):
    def __init__(self):
        self.cfg = SimpleNamespace(accounts=["A1"])
        self.pos = {"MNQZ6": (3, 101), "MESZ6": (1, 102)}
        self.liq = []

    def list_open_positions(self):
        return [P(1, s, q, contractId=cid) for s, (q, cid) in self.pos.items() if q]

    def _post(self, path, body):
        if path == "/order/liquidateposition":
            self.liq.append(body["contractId"])
            for s, (q, cid) in list(self.pos.items()):
                if cid == body["contractId"]:
                    self.pos[s] = (0, cid)
        return {}


b = FTv()
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2j Tradovate: 나스닥 청산은 MNQ만, 회원 MES 그대로", b.liq == [101] and b.pos["MESZ6"][0] == 1 and not r.errors, (b.liq, r.errors))
b = FTv()
b.pos = {"101": (3, 101), "MESZ6": (1, 102)}             # 계약 이름 조회 실패 = 숫자 ID
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2j2 Tradovate: 계약 이름을 못 읽어 숫자 ID로 온 포지션은 닫는다(우리 것일 수 있다), MES는 그대로", b.liq == [101] and b.pos["MESZ6"][0] == 1, b.liq)


# NinjaTrader: 종목을 주면 계좌 전체 flatten 대신 그 종목만 'close'
class FN8(N8.NT8Broker):
    def __init__(self):
        self.accounts = ["LDI150"]
        self.symbol_map = {}                       # 실제 생성자가 만드는 속성
        self.snapshot_fresh = lambda *x, **k: True    # 브리지가 살아 있는 상태(끊김은 2l2에서 따로)
        self.cmds = []
        self.pos = [P("LDI150", "MNQ 12-26", 3, _accountId="LDI150"), P("LDI150", "MGC 12-26", 2, _accountId="LDI150")]
        self.connected = {"LDI150"}

    def account_connected(self, a):
        return a in self.connected

    def list_open_positions(self):
        return list(self.pos)

    def _enqueue_and_wait(self, cmd, *a, **k):
        self.cmds.append(dict(cmd))
        return {"ok": True}


b = FN8()
r = b.flatten_all(dry_run=False, only=AS.matcher("GC"))
ck("2k NinjaTrader: 금 청산은 'close'(MGC)만, 계좌 전체 flatten 명령 없음", [c["op"] for c in b.cmds] == ["close"]
   and b.cmds[0]["account"] == "LDI150" and "MGC" in b.cmds[0]["instrument"] and not r.errors, b.cmds)
b = FN8()
r = b.flatten_all(dry_run=False)
ck("2l NinjaTrader: only 없음 = 종전대로 flatten 한 번", [c["op"] for c in b.cmds] == ["flatten"], b.cmds)
b = FN8()
b.pos = []
b.snapshot_fresh = lambda *a, **k: False
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2l2 NinjaTrader: 브리지가 끊겨 스냅샷이 비면 '포지션 없음'이 아니라 오류(적대 검토 P1)", r.errors and not b.cmds and not r.planned, (r.errors, b.cmds))
b = FN8()
b.pos = []
b.connected = set()
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2l4 NinjaTrader: 브리지는 살아 있는데 그 계좌만 끊겨 push에서 빠지면 '포지션 없음'이 아니라 오류(앱 출시 전 검토 2026-10-20 P1)",
   r.errors and "not connected" in r.errors[0] and not b.cmds, (r.errors, b.cmds))
b = FN8()
b.pos.append(P("LDI150", "MNQ 03-27", 1, _accountId="LDI150"))
b._enqueue_and_wait = lambda cmd, *a, **k: (b.cmds.append(dict(cmd)), (_ for _ in ()).throw(TimeoutError("nt8 ack timeout")))[1]
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2l3 NinjaTrader: 첫 시간 초과 뒤 남은 종목은 보내지 않고 오류로(종목마다 20초씩 막히지 않게)", len(b.cmds) == 1 and len(r.errors) == 2, (b.cmds, r.errors))


# IBKR: 우리 주문(EQ-AP-)만, 그 종목만 걷는다
class FakeIB:
    def __init__(self):
        self.cancelled, self.placed = [], []
        mk = lambda sym, ref: SimpleNamespace(contract=SimpleNamespace(localSymbol=sym, symbol=sym[:3]),
                                              order=SimpleNamespace(account="DU1", orderRef=ref))
        self.trades = [mk("MNQZ6", "EQ-AP-STOP-1"), mk("MGCZ6", "EQ-AP-STOP-2"), mk("MESZ6", "manual")]

    def openTrades(self):
        return list(self.trades)

    def cancelOrder(self, o):
        self.cancelled.append(o.orderRef)

    def placeOrder(self, c, o):
        self.placed.append(c)
        return SimpleNamespace(order=o)

    def sleep(self, s):
        pass


fib = FakeIB()
b = IB.IBKRBroker.__new__(IB.IBKRBroker)
b._lock = threading.RLock()
b._connect = lambda: fib
b._acct_cache = [{"id": "DU1"}]
b.cfg = SimpleNamespace(accounts=["DU1"])
_ipos = [P("DU1", "MNQZ6", 2, contract="C-MNQ"), P("DU1", "MGCZ6", 1, contract="C-MGC")]
b.list_open_positions = lambda: list(_ipos) if not fib.placed else [p for p in _ipos if p.raw["contract"] not in fib.placed]
b._mk = lambda ibi, *a, **k: SimpleNamespace(kind=a[0])
b._ack = lambda ib, t, wait_s=None: None
IB._ib_classes = lambda: None
r = b.flatten_all(dry_run=False, only=AS.matcher("NQ"))
ck("2m IBKR: 나스닥 청산은 MNQ 주문만 내고 MNQ의 우리 손절만 취소(금 손절, 회원 MES 주문은 그대로)",
   fib.placed == ["C-MNQ"] and fib.cancelled == ["EQ-AP-STOP-1"] and not r.errors, (fib.placed, fib.cancelled, r.errors))


# ── 3 앱 배선 ────────────────────────────────────────────────────────────────
import socket  # noqa: E402
import tempfile  # noqa: E402
os.environ["HOME"] = os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eq_ascope_")
os.environ["EQ_ERR_REPORT"] = "0"
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("network blocked by test"))
import eqgui as E  # noqa: E402

seen = {}


class NewB:
    def flatten_all(self, dry_run=True, only=None):
        seen["only"] = only
        return SimpleNamespace(planned=[], closed=[], errors=[], cancelled=[])


class OldB:
    def flatten_all(self, dry_run=True):
        seen["old"] = True
        return SimpleNamespace(planned=[], closed=[], errors=[], cancelled=[])


E._flatten_asset(NewB(), "BTC", True)
ck("3a _flatten_asset: 그 자산 판정 함수를 넘긴다", callable(seen.get("only")) and seen["only"]("BTCUSDT") and not seen["only"]("ETHUSDT"))
E._flatten_asset(OldB(), "BTC", True)
ck("3b only를 모르는 옛 어댑터, 시험 객체 = 종전대로 계좌 전부", seen.get("old") is True)


class BoomB:
    calls = 0

    def flatten_all(self, dry_run=True, only=None):
        BoomB.calls += 1
        raise TypeError("can only concatenate str (not \"NoneType\") to str")


try:
    E._flatten_asset(BoomB(), "NQ", False); _boom = False
except TypeError:
    _boom = True
ck("3b2 only를 받는 어댑터 안의 다른 TypeError는 그대로 올린다(계좌 전부 청산으로 되돌아가지 않음)", _boom and BoomB.calls == 1, BoomB.calls)
src = inspect.getsource(E)
ck("3c 정해진 시각 청산 = _flatten_asset(b, j[\"asset\"], not live)", 'res = _flatten_asset(b, j["asset"], not live)' in src
   and "res = b.flatten_all(dry_run=not live)   # 잔여 확인" not in src)
_ca = inspect.getsource(E.App._close_all_positions)
ck("3d [모든 포지션 청산]은 종전대로 전부, 자산을 주면 그 자산 계좌, 그 종목만",
   "(_flatten_asset(b, asset, False) if asset else b.flatten_all(dry_run=False))" in _ca and "([asset] if asset else _ASSETS)" in _ca)
ck("3e [자산별 청산] 메뉴(나스닥, 금, 비트코인)", "self._close_all_positions(asset=_a)" in src
   and 'self._flat_asset_mb = ttk.Menubutton(self._dock, text=("자산별 청산"' in src and "for _fa in _ASSETS:" in src)
_al = inspect.getsource(getattr(E.App, "_auto_loop_body", E.App._auto_loop))   # 21l: 본문은 _auto_loop_body(_auto_loop는 재시작 보호막)
ck("3f 정해진 시각 청산: 계획이 비었어도 오류가 있으면(라이브) 실패로 - 재시도와 알림", "if not res.planned and res.errors and live:" in _al)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
