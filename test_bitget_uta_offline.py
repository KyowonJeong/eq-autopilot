#!/usr/bin/env python3
# EQ Autopilot 2026.11.11f - Bitget 통합 거래 계정(UTA, API v3) 오프라인 시험 (2026-11-11)
# =========================
# 왜: 회원이 Bitget 계정을 통합 거래 계정(UTA)으로 올리자 앱 연결이 끊겼다(Bitget: UTA 키는 Classic 끝점을 못 부른다).
#   어댑터가 키마다 계정 유형을 물어(GET /api/v3/account/settings, 25245 = 클래식) 클래식은 v2, UTA는 v3로 보낸다.
#   네트워크 없이 고정할 것:
#   D 판별 - 클래식(25245, HTTP 400이든 200이든), UTA(unified, hybrid), 바뀌는 중(upgrading/switching = 아무것도 안 보냄),
#     모름(종전 v2, 120초 뒤 다시), 거래 권한만 있는 키, 키마다 한 번(같은 키 새 인스턴스는 묻지 않음), UTA → 클래식 되돌림(25245),
#     클래식 → UTA(v2 확실한 거절 뒤 다시 묻기, 결과 모름이면 다시 보내지 않음), 연결 테스트는 늘 새로 묻기.
#   C 클래식 그대로 - 8c910ff8 원본 bitget.py로 뽑은 요청 순서(메서드, 주소, 본문, 서명 헤더)와 반환값이 글자까지 같다.
#     처음 한 번 판별 요청(GET /api/v3/account/settings)만 앞에 붙고, 판별이 끝난 뒤에는 그것도 없다.
#   U UTA 본문 - 포지션, 잔고, 레버리지, 진입 + 손절 첨부, 지정가, 청산(전체, 종목), 취소, 시세, 4H 블록, 손절 목록과 이동,
#     체결 이력(30일 창, cursor, 수수료, 펀딩 순손익), 연결 정보, v3 서명. UTA 계정에서는 v2 끝점을 한 번도 부르지 않는다.
#   R 2026-11-11 리뷰 수리 - P1-1 이중 주문(v2 쓰기가 들어갔거나 성공이면 v3로 다시 안 보냄, 반대쪽도), P1-2 헤지 모드 + 관리 권한 없는 키
#     (25236 뒤 posSide로 한 번, key_info 경고), P1-3 진입 손절 = 체결 뒤 TP/SL 전략 주문(목록에 나옴, 이동, 실패 → 바로 닫기, 체결 수량,
#     결과 모름 재시도), P2-4 판별 끊김, P2-5 처음 보는 accountMode, P2-6 'unknown: code=', P2-7 가용 마진과 모르는 레버리지, P2-8 새 clientOid.
#   R7, G3~G5 2026-11-11 재리뷰(2026.11.11g) - 비상 청산 전 이미 걸린 손절 찾기(결과 모름, 25595, 중복 하나 취소), 청산 뒤 포지션 확인(반만
#     체결, 결과 모름인데 닫힘, 못 읽음), 모든 UTA 진입 오류에 stop_attached False, 결과 모름 진입의 미확인 감시(늦은 체결에 손절), 단방향 손절
#     = side + reduceOnly, '닫음' / '체결 없음' 따로(closed_nostop / not_filled), 25580/25582 설명 줄.
#
# 사용: HOME=$(mktemp -d) EQ_NO_ALERT=1 python executor/test_bitget_uta_offline.py   (0=통과, 1=실패)
#      python executor/test_bitget_uta_offline.py --golden <bitget.py 경로>   클래식 기준 요청 순서를 그 파일로 다시 뽑아 출력
#      (기준값 GOLDEN은 `git show 8c910ff8:executor/eqexec/broker/bitget.py`로 뽑았다)
# =========================
import base64
import dataclasses
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqexec.broker.bitget as BG  # noqa: E402
import tempfile as _tf0  # noqa: E402
BG._TRIG_PATH = os.path.join(_tf0.mkdtemp(prefix="eq_bgtrig_"), "bitget_uta_triggers.json")   # 앱 트리거 손절 기록(시험용 자리)
from eqexec.config import BitgetCfg  # noqa: E402

fails = []


def ck(name, cond, info=None):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond or info is None else f"   -> {info!r}"[:600]))
    if not cond:
        fails.append(name)


# ── 가짜 네트워크와 시계 ──────────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, status, obj):
        self.status_code = status
        self._o = obj
        self.text = json.dumps(obj)

    def json(self):
        if isinstance(self._o, Exception):
            raise self._o
        return self._o


class Clock:
    def __init__(self, t=1793000000.0):
        self.t = t
        self.sleeps = []

    def time(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class Net:
    """경로별 응답 라우터. calls = [(method, path?query, body, headers)]. 라우트 함수 (query dict, body) → 데이터 | Resp | 예외."""

    def __init__(self, routes, default=None):
        self.calls = []
        self.routes = routes
        self.default = default

    def get(self, url, headers=None, timeout=None):
        return self._do("GET", url, headers, None)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._do("POST", url, headers, data)

    def _do(self, m, url, headers, data):
        sp = urlsplit(url)
        body = json.loads(data) if data else None
        self.calls.append((m, sp.path + ("?" + sp.query if sp.query else ""), body, dict(headers or {})))
        q = dict(kv.split("=", 1) for kv in sp.query.split("&")) if sp.query else {}
        fn = self.routes.get((m, sp.path)) or self.default
        if fn is None:
            return Resp(404, {"code": "40404", "msg": "Request URL NOT FOUND"})
        out = fn(q, body)
        if isinstance(out, BaseException):
            raise out
        if isinstance(out, Resp):
            return out
        return Resp(200, {"code": "00000", "msg": "success", "requestTime": 1, "data": out})


class TimeNS:
    def __init__(self, clock):
        self.time = clock.time
        self.sleep = clock.sleep


def bind(mod, net, clock):
    mod.requests = net
    mod.time = TimeNS(clock)


def fresh(mod):
    mod.BitgetBroker._PSTEP.clear()
    if hasattr(mod.BitgetBroker, "_QSTEP"):
        mod.BitgetBroker._QSTEP.clear()
    if hasattr(mod, "_MODES"):
        mod._MODES.clear()
    if hasattr(mod, "_trig_path"):
        try:
            os.remove(mod._trig_path())                                  # 트리거 손절 기록은 파일에도 남는다 - 시험 사이에 비운다
        except OSError:
            pass


def mk(mod, key="bg_key_test"):
    return mod.BitgetBroker(BitgetCfg(api_key=key, api_secret="bg_secret_test", passphrase="bg_pass_test"))


def norm(x):
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return norm(dataclasses.asdict(x))
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    return x


def paths(net):
    return [f"{c[0]} {c[1].split('?')[0]}" for c in net.calls]


NOT_UNIFIED = Resp(400, {"code": "25245", "msg": "The account is not the unified account mode", "requestTime": 1, "data": None})
PROBE = "/api/v3/account/settings"
END = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)


# ══ 클래식(v2) 세계: 8c910ff8 원본과 같은 순서인지 보는 시나리오 ════════════════════════════
def classic_routes(st):
    def all_pos(q, b):
        return [dict(p) for p in st["pos"]]

    def close(q, b):
        if "symbol" in b:
            st["pos"] = [p for p in st["pos"] if p["symbol"] != b["symbol"]]
        else:
            st["pos"] = []
        return {"successList": [], "failureList": []}

    def place(q, b):
        if st["place_fail"]:
            return st["place_fail"].pop(0)
        return {"orderId": "1001", "clientOid": b.get("clientOid")}

    def modify(q, b):
        if st["modify_fail"]:
            return Resp(400, {"code": "40017", "msg": "modify rejected"})
        return {"orderId": b["orderId"]}

    def candles(q, b):
        s = int(q["startTime"])
        return [[str(s + i * 3600000), str(90000 + i), "1", "1", str(90000.5 + i), "1", "1"] for i in (3, 1, 0, 2)]

    return {
        ("GET", PROBE): lambda q, b: st.get("probe", NOT_UNIFIED),
        ("GET", "/api/v2/mix/position/all-position"): all_pos,
        ("GET", "/api/v2/mix/account/account"): lambda q, b: {"crossedMaxAvailable": "1500.5", "available": "1400",
                                                               "crossedMarginLeverage": "5"},
        ("GET", "/api/v2/mix/market/contracts"): lambda q, b: [{"symbol": "BTCUSDT", "maxLever": "125",
                                                                "pricePlace": "1", "priceEndStep": "1"}],
        ("POST", "/api/v2/mix/account/set-leverage"): lambda q, b: {"crossMarginLeverage": b["leverage"]},
        ("POST", "/api/v2/mix/order/close-positions"): close,
        ("POST", "/api/v2/mix/order/place-order"): place,
        ("POST", "/api/v2/mix/order/cancel-order"): lambda q, b: (Resp(400, {"code": "40768", "msg": "Order does not exist"})
                                                                  if st["cancel_fail"] else {"orderId": b["orderId"]}),
        ("GET", "/api/v2/mix/market/ticker"): lambda q, b: [{"symbol": "BTCUSDT", "lastPr": "90010.5"}],
        ("GET", "/api/v2/mix/order/orders-plan-pending"): lambda q, b: {"entrustedList": [dict(r) for r in st["plans"]]},
        ("POST", "/api/v2/mix/order/modify-tpsl-order"): modify,
        ("POST", "/api/v2/mix/order/place-tpsl-order"): lambda q, b: {"orderId": "p9"},
        ("GET", "/api/v2/mix/market/candles"): candles,
        ("GET", "/api/v2/mix/position/history-position"): lambda q, b: {"list": [
            {"positionId": "h1", "utime": "1792900000000", "symbol": "BTCUSDT", "holdSide": "short", "openAvgPrice": "91000",
             "closeAvgPrice": "90500", "pnl": "5", "openFee": "0.4", "closeFee": "-0.4", "totalFunding": "-0.1"},
            {"positionId": "h2", "utime": "1792950000000", "symbol": "BTCUSDT", "holdSide": "long", "openAvgPrice": "90000",
             "closeAvgPrice": "90100", "netProfit": "0.7", "pnl": "1"}]},
    }


def classic_state():
    return {"pos": [{"symbol": "BTCUSDT", "holdSide": "long", "total": "0.01", "openPriceAvg": "90000.5", "marginCoin": "USDT"},
                    {"symbol": "ETHUSDT", "holdSide": "short", "total": "0.2", "openPriceAvg": "3000", "marginCoin": "USDT"}],
            "plans": [{"orderId": "A", "planType": "pos_loss", "symbol": "BTCUSDT", "triggerPrice": "85000.1",
                       "triggerType": "mark_price"},
                      {"orderId": "C", "planType": "profit_plan", "symbol": "BTCUSDT", "triggerPrice": "95000"}],
            "place_fail": [], "modify_fail": False, "cancel_fail": False}


def _side_err():
    return Resp(400, {"code": "40774", "msg": "The order type for unilateral position must also be the unilateral position type."})


def _only_btc(sym):
    return sym == "BTCUSDT"


SCEN = [
    ("authenticate", lambda b: b.authenticate(), None),
    ("healthcheck", lambda b: b.healthcheck(), None),
    ("available_usdt", lambda b: b.available_usdt("BTCUSDT.P"), None),
    ("current_leverage", lambda b: b.current_leverage("BTCUSDT"), None),
    ("max_leverage", lambda b: b.max_leverage("BTCUSDT"), None),
    ("set_leverage", lambda b: b.set_leverage("BTCUSDT.P", 20), None),
    ("ensure_leverage_up", lambda b: b.ensure_leverage("BTCUSDT", 0.5, 90000), None),
    ("ensure_leverage_ok", lambda b: b.ensure_leverage("BTCUSDT", 0.01, 90000), None),
    ("list_open_positions", lambda b: b.list_open_positions(), None),
    ("flatten_dry", lambda b: b.flatten_all(dry_run=True), None),
    ("flatten_live", lambda b: b.flatten_all(dry_run=False), None),
    ("flatten_only", lambda b: b.flatten_all(dry_run=False, only=_only_btc), None),
    ("close_symbol_dry", lambda b: b.close_symbol("BTCUSDT.P", dry_run=True), None),
    ("close_symbol_live", lambda b: b.close_symbol("BTCUSDT.P", dry_run=False), None),
    ("place_entry_dry", lambda b: b.place_entry(symbol="BTCUSDT.P", side="LONG", size=0.0123, stop_loss_price=85000.04,
                                                custom_tag="EQ-AP-1793000000000", dry_run=True), None),
    ("place_entry_live", lambda b: b.place_entry(symbol="BTCUSDT.P", side="SHORT", size=0.01, stop_loss_price=95000.06,
                                                 custom_tag="EQ-AP-1793000000000", dry_run=False), None),
    ("place_entry_nostop", lambda b: b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, dry_run=False), None),
    ("place_entry_hedge_retry", lambda b: b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000,
                                                        dry_run=False),
     lambda st: st["place_fail"].append(_side_err())),
    ("place_entry_reject", lambda b: b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000,
                                                   dry_run=False),
     lambda st: st["place_fail"].append(Resp(400, {"code": "40762", "msg": "The order amount exceeds the balance"}))),
    ("place_entry_bad_side", lambda b: b.place_entry(symbol="BTCUSDT", side="X", size=0.01, dry_run=False), None),
    ("place_limit_entry_dry", lambda b: b.place_limit_entry(symbol="BTCUSDT", side="LONG", size=0.01, price=89999.97,
                                                            stop_loss_price=85000, custom_tag="EQ-AP-1-0", dry_run=True), None),
    ("place_limit_entry_live", lambda b: b.place_limit_entry(symbol="BTCUSDT", side="SHORT", size=0.01, price=90001.04,
                                                             stop_loss_price=95000, custom_tag="EQ-AP-1-1", dry_run=False), None),
    ("place_limit_close_dry", lambda b: b.place_limit_close(symbol="BTCUSDT", price=90100.02, dry_run=True), None),
    ("place_limit_close_live", lambda b: b.place_limit_close(symbol="BTCUSDT", price=90100.02, dry_run=False), None),
    ("cancel_order", lambda b: b.cancel_order("BTCUSDT.P", 777), None),
    ("cancel_order_fail", lambda b: b.cancel_order("BTCUSDT.P", 777), lambda st: st.update(cancel_fail=True)),
    ("position_qty", lambda b: b.position_qty("BTCUSDT.P"), None),
    ("current_market_price", lambda b: b.current_market_price("BTCUSDT.P"), None),
    ("stop_plans", lambda b: b.stop_plans("BTCUSDT.P"), None),
    ("set_stop_modify", lambda b: b.set_stop("BTCUSDT.P", 86000.04), None),
    ("set_stop_modify_fail", lambda b: b.set_stop("BTCUSDT.P", 86000.04), lambda st: st.update(modify_fail=True)),
    ("set_stop_place", lambda b: b.set_stop("BTCUSDT.P", 86000.04), lambda st: st.update(plans=[])),
    ("block_4h", lambda b: b.block_4h("BTCUSDT.P", END), None),
    ("closed_fills", lambda b: b.closed_fills(1792000000000), None),
    ("entry_info", lambda b: b.entry_info(), None),
]


def run_classic(mod, warm=False, names=None):
    """시나리오마다 새 상태, 새 시계, 새 인스턴스. warm = 판별을 미리 끝낸 상태(새 모듈만)."""
    out = {}
    for name, fn, setup in SCEN:
        if names and name not in names:
            continue
        st = classic_state()
        if setup:
            setup(st)
        net, clock = Net(classic_routes(st)), Clock()
        bind(mod, net, clock)
        fresh(mod)
        b = mk(mod)
        if warm and hasattr(mod, "_MODES"):
            b._mstate()["mode"] = "classic"
            b._mstate()["probe_at"] = clock.time()
        try:
            res = norm(fn(b))
        except Exception as e:
            res = ["EXC", "RuntimeError" if isinstance(e, RuntimeError) else type(e).__name__, str(e)]
        out[name] = {"calls": [[c[0], c[1], c[2], c[3]] for c in net.calls], "result": res, "sleeps": clock.sleeps}
    return out


def digest(rec):
    return hashlib.sha256(json.dumps(rec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def golden_of(run):
    return {k: {"calls": [f"{c[0]} {c[1]} {json.dumps(c[2], sort_keys=True, separators=(',', ':')) if c[2] else ''}".rstrip()
                          for c in v["calls"]],
                "sha": digest(v)} for k, v in run.items()}


def load_ref(path):
    spec = importlib.util.spec_from_file_location("eqexec.broker._bitget_ref", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


if len(sys.argv) >= 3 and sys.argv[1] == "--golden":
    print(json.dumps(golden_of(run_classic(load_ref(sys.argv[2]))), indent=1, ensure_ascii=False))
    sys.exit(0)

# 8c910ff8 원본 bitget.py로 뽑은 클래식 기준(python test_bitget_uta_offline.py --golden <원본>). sha = 요청(주소, 본문, 서명 헤더 전부),
# 반환값, sleep까지 묶은 값 - 하나라도 달라지면 바뀐다.
GOLDEN = json.loads(r"""{
 "authenticate": {"sha": "5295140cd054ab22", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "healthcheck": {"sha": "aad563f574a6760e", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "available_usdt": {"sha": "bbc847ff05c0e84f", "calls": [
   "GET /api/v2/mix/account/account?marginCoin=USDT&productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "current_leverage": {"sha": "b0b2cbf145caf400", "calls": [
   "GET /api/v2/mix/account/account?marginCoin=USDT&productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "max_leverage": {"sha": "52a3e6292347050c", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "set_leverage": {"sha": "6fd3accd46ea117b", "calls": [
   "POST /api/v2/mix/account/set-leverage {\"leverage\":\"20\",\"marginCoin\":\"USDT\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "ensure_leverage_up": {"sha": "12dc12243fff3bbd", "calls": [
   "GET /api/v2/mix/account/account?marginCoin=USDT&productType=USDT-FUTURES&symbol=BTCUSDT",
   "GET /api/v2/mix/account/account?marginCoin=USDT&productType=USDT-FUTURES&symbol=BTCUSDT",
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/account/set-leverage {\"leverage\":\"46\",\"marginCoin\":\"USDT\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "ensure_leverage_ok": {"sha": "839c1f4c0e2ad7bb", "calls": [
   "GET /api/v2/mix/account/account?marginCoin=USDT&productType=USDT-FUTURES&symbol=BTCUSDT",
   "GET /api/v2/mix/account/account?marginCoin=USDT&productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "list_open_positions": {"sha": "b862cb53fb976a66", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "flatten_dry": {"sha": "72863be6e75d0489", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "flatten_live": {"sha": "cf9eba7c69e049fe", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES",
   "POST /api/v2/mix/order/close-positions {\"productType\":\"USDT-FUTURES\"}",
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "flatten_only": {"sha": "0d0ecddb2d74d726", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES",
   "POST /api/v2/mix/order/close-positions {\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\"}",
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "close_symbol_dry": {"sha": "2036d8baf526cc2a", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "close_symbol_live": {"sha": "5f2a1d345d047383", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES",
   "POST /api/v2/mix/order/close-positions {\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\"}",
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "place_entry_dry": {"sha": "afe82f85fb864254", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "place_entry_live": {"sha": "d84cd8da23d039ef", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/order/place-order {\"clientOid\":\"EQ-AP-1793000000000\",\"marginCoin\":\"USDT\",\"marginMode\":\"crossed\",\"orderType\":\"market\",\"presetStopLossPrice\":\"95000.1\",\"productType\":\"USDT-FUTURES\",\"side\":\"sell\",\"size\":\"0.01\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "place_entry_nostop": {"sha": "c76635f42711d522", "calls": [
   "POST /api/v2/mix/order/place-order {\"marginCoin\":\"USDT\",\"marginMode\":\"crossed\",\"orderType\":\"market\",\"productType\":\"USDT-FUTURES\",\"side\":\"buy\",\"size\":\"0.01\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "place_entry_hedge_retry": {"sha": "fe2f8d8a1a8d1fa5", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/order/place-order {\"marginCoin\":\"USDT\",\"marginMode\":\"crossed\",\"orderType\":\"market\",\"presetStopLossPrice\":\"85000.0\",\"productType\":\"USDT-FUTURES\",\"side\":\"buy\",\"size\":\"0.01\",\"symbol\":\"BTCUSDT\"}",
   "POST /api/v2/mix/order/place-order {\"marginCoin\":\"USDT\",\"marginMode\":\"crossed\",\"orderType\":\"market\",\"presetStopLossPrice\":\"85000.0\",\"productType\":\"USDT-FUTURES\",\"side\":\"buy\",\"size\":\"0.01\",\"symbol\":\"BTCUSDT\",\"tradeSide\":\"open\"}"
  ]},
 "place_entry_reject": {"sha": "094750ea0415451a", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/order/place-order {\"marginCoin\":\"USDT\",\"marginMode\":\"crossed\",\"orderType\":\"market\",\"presetStopLossPrice\":\"85000.0\",\"productType\":\"USDT-FUTURES\",\"side\":\"buy\",\"size\":\"0.01\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "place_entry_bad_side": {"sha": "46a002b021b1cfd3", "calls": [
  ]},
 "place_limit_entry_dry": {"sha": "7dc050eed48d1e67", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "place_limit_entry_live": {"sha": "403b9a30f61f031b", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/order/place-order {\"clientOid\":\"EQ-AP-1-1\",\"force\":\"post_only\",\"marginCoin\":\"USDT\",\"marginMode\":\"crossed\",\"orderType\":\"limit\",\"presetStopLossPrice\":\"95000.0\",\"price\":\"90001.0\",\"productType\":\"USDT-FUTURES\",\"side\":\"sell\",\"size\":\"0.01\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "place_limit_close_dry": {"sha": "e0613c30df2db819", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES",
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "place_limit_close_live": {"sha": "72940b16faaaed3b", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES",
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/order/place-order {\"force\":\"post_only\",\"marginCoin\":\"USDT\",\"marginMode\":\"crossed\",\"orderType\":\"limit\",\"price\":\"90100.0\",\"productType\":\"USDT-FUTURES\",\"reduceOnly\":\"YES\",\"side\":\"sell\",\"size\":\"0.01\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "cancel_order": {"sha": "d3313fdee8136790", "calls": [
   "POST /api/v2/mix/order/cancel-order {\"marginCoin\":\"USDT\",\"orderId\":\"777\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "cancel_order_fail": {"sha": "ee1fbf4506bee1fa", "calls": [
   "POST /api/v2/mix/order/cancel-order {\"marginCoin\":\"USDT\",\"orderId\":\"777\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\"}"
  ]},
 "position_qty": {"sha": "421ee250570b4133", "calls": [
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES"
  ]},
 "current_market_price": {"sha": "82c1995d1585f04c", "calls": [
   "GET /api/v2/mix/market/ticker?productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "stop_plans": {"sha": "8febb38b9990c299", "calls": [
   "GET /api/v2/mix/order/orders-plan-pending?planType=profit_loss&productType=USDT-FUTURES&symbol=BTCUSDT"
  ]},
 "set_stop_modify": {"sha": "9ec26ea0c17a3fc6", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "GET /api/v2/mix/order/orders-plan-pending?planType=profit_loss&productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/order/modify-tpsl-order {\"executePrice\":\"0\",\"marginCoin\":\"USDT\",\"orderId\":\"A\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\",\"triggerPrice\":\"86000.0\",\"triggerType\":\"mark_price\"}"
  ]},
 "set_stop_modify_fail": {"sha": "78a788e992216195", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "GET /api/v2/mix/order/orders-plan-pending?planType=profit_loss&productType=USDT-FUTURES&symbol=BTCUSDT",
   "POST /api/v2/mix/order/modify-tpsl-order {\"executePrice\":\"0\",\"marginCoin\":\"USDT\",\"orderId\":\"A\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\",\"triggerPrice\":\"86000.0\",\"triggerType\":\"mark_price\"}",
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES",
   "POST /api/v2/mix/order/place-tpsl-order {\"executePrice\":\"0\",\"holdSide\":\"long\",\"marginCoin\":\"USDT\",\"planType\":\"pos_loss\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\",\"triggerPrice\":\"86000.0\",\"triggerType\":\"mark_price\"}"
  ]},
 "set_stop_place": {"sha": "d8ac8f4456e9ab4d", "calls": [
   "GET /api/v2/mix/market/contracts?productType=USDT-FUTURES&symbol=BTCUSDT",
   "GET /api/v2/mix/order/orders-plan-pending?planType=profit_loss&productType=USDT-FUTURES&symbol=BTCUSDT",
   "GET /api/v2/mix/position/all-position?marginCoin=USDT&productType=USDT-FUTURES",
   "POST /api/v2/mix/order/place-tpsl-order {\"executePrice\":\"0\",\"holdSide\":\"long\",\"marginCoin\":\"USDT\",\"planType\":\"pos_loss\",\"productType\":\"USDT-FUTURES\",\"symbol\":\"BTCUSDT\",\"triggerPrice\":\"86000.0\",\"triggerType\":\"mark_price\"}"
  ]},
 "block_4h": {"sha": "7832a87433bab681", "calls": [
   "GET /api/v2/mix/market/candles?endTime=1794347999999&granularity=1H&limit=10&productType=USDT-FUTURES&startTime=1794333600000&symbol=BTCUSDT"
  ]},
 "closed_fills": {"sha": "b310ba3dcc50f3cd", "calls": [
   "GET /api/v2/mix/position/history-position?limit=100&productType=USDT-FUTURES&startTime=1792000000000"
  ]},
 "entry_info": {"sha": "402040ce788cd30b", "calls": [
   "GET /api/v2/mix/account/account?marginCoin=USDT&productType=USDT-FUTURES&symbol=BTCUSDT"
  ]}
}""")

# ══ C: 클래식 그대로 ═════════════════════════════════════════════════════════════════════
cold = run_classic(BG)
warm = run_classic(BG, warm=True)
def _strip_probe(v):
    """맨 앞 판별 요청 하나를 떼어 낸다(없으면 None)."""
    c = v["calls"]
    if not c or c[0][0] != "GET" or c[0][1] != PROBE:
        return None
    return dict(v, calls=c[1:])


# healthcheck는 연결 테스트마다 계정 유형을 새로 묻는다(판별이 끝났어도 맨 앞 GET 하나) - 나머지는 같다
_bad_warm = [k for k in GOLDEN if k not in warm
             or digest(_strip_probe(warm[k]) if k == "healthcheck" else warm[k]) != GOLDEN[k]["sha"]]
ck(f"C1 판별이 끝난 클래식 키: {len(GOLDEN)}개 시나리오 요청(주소, 본문, 서명 헤더), 반환값, sleep이 8c910ff8 원본과 글자까지 같다"
   " (healthcheck만 맨 앞에 판별 GET)",
   not _bad_warm and len(warm) == len(GOLDEN),
   [(k, golden_of({k: warm[k]})[k]["calls"], GOLDEN[k]["calls"]) for k in _bad_warm[:2]])
_bad_cold = [k for k, v in cold.items() if _strip_probe(v) is None or digest(_strip_probe(v)) != GOLDEN[k]["sha"]]
ck("C2 처음 쓰는 클래식 키: 맨 앞 판별 요청(GET /api/v3/account/settings → 25245) 하나만 더하고 나머지는 원본과 같다",
   not _bad_cold, _bad_cold[:5])
ck("C3 판별 요청도 같은 서명(ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE)",
   set(cold["authenticate"]["calls"][0][3]) == set(cold["authenticate"]["calls"][1][3]), cold["authenticate"]["calls"][0][3])

# 같은 키 = 한 번만 묻는다(새 인스턴스도), 다른 키는 따로
st = classic_state()
net, clock = Net(classic_routes(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b1 = mk(BG)
b1.list_open_positions()
b1.available_usdt()
b2 = mk(BG)
b2.list_open_positions()
b3 = mk(BG, key="other_key")
b3.list_open_positions()
ck("C4 같은 키는 프로세스에서 한 번만 묻는다(새 인스턴스도 묻지 않음), 다른 키는 따로 묻는다",
   [c[1] for c in net.calls].count(PROBE) == 2 and b1.account_type() == "classic" and b3.account_type() == "classic",
   paths(net))
ck("C5 판별 상태에 API 키 원문을 두지 않는다(지문만)", "bg_key_test" not in json.dumps(list(BG._MODES.keys())), list(BG._MODES))

# 25245가 HTTP 200 + code로 와도 같다
st = classic_state()
st["probe"] = Resp(200, {"code": "25245", "msg": "The account is not the unified account mode", "data": None})
net, clock = Net(classic_routes(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
pos = b.list_open_positions()
ck("C6 25245가 HTTP 200 본문 code로 와도 클래식", b.account_type() == "classic" and len(pos) == 2
   and paths(net) == ["GET /api/v3/account/settings", "GET /api/v2/mix/position/all-position"], paths(net))

# 클래식 오류 경로: 마지막 판별 60초 뒤의 확실한 거절은 한 번 다시 묻는다 - 25245면 반환값은 원본과 같다(뒤에 판별 GET 하나)
_ref = run_classic(BG, warm=True, names={"place_entry_reject"})["place_entry_reject"]
st = classic_state()
st["place_fail"].append(Resp(400, {"code": "40762", "msg": "The order amount exceeds the balance"}))
net, clock = Net(classic_routes(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b._mstate().update(mode="classic", probe_at=clock.time() - 61)
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
ck("C7 클래식 거절(40762): 반환값과 요청이 원본과 같다 - 거래 길에서는 다시 묻지 않는다(최종 리뷰 E1 P2-10, 판별 GET 없음)",
   norm(r) == _ref["result"] and paths(net) == [f"{c[0]} {c[1].split('?')[0]}" for c in _ref["calls"]]
   and [c[1] for c in net.calls if c[0] == "POST"] == ["/api/v2/mix/order/place-order"] and b.account_type() == "classic",
   (r, paths(net)))
net.calls.clear()
st["place_fail"].append(Resp(400, {"code": "40762", "msg": "The order amount exceeds the balance"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
ck("C8 60초 안의 거절은 다시 묻지 않는다", paths(net) == ["POST /api/v2/mix/order/place-order"], paths(net))


# ══ UTA(v3) 세계 ══════════════════════════════════════════════════════════════════════════
def uta_state():
    return {"mode": "unified", "hold": "one_way_mode",
            "pos": [{"category": "USDT-FUTURES", "symbol": "BTCUSDT", "marginCoin": "USDT", "holdMode": "one_way_mode",
                     "posSide": "long", "marginMode": "crossed", "available": "0.01", "frozen": "0", "total": "0.01",
                     "leverage": "5", "avgPrice": "90000.5", "markPrice": "90100", "unrealisedPnl": "1"},
                    {"category": "USDT-FUTURES", "symbol": "ETHUSDT", "marginCoin": "USDT", "holdMode": "one_way_mode",
                     "posSide": "short", "total": "0.2", "avgPrice": "3000"},
                    {"category": "USDT-FUTURES", "symbol": "SOLUSDT", "posSide": "long", "total": "0"}],
            "strat": [{"orderId": "S1", "clientOid": "x", "category": "USDT-FUTURES", "symbol": "BTCUSDT", "qty": "0.01",
                       "side": "sell", "posSide": "long", "status": "pending", "slTriggerBy": "mark", "stopLoss": "85000.1",
                       "slOrderType": "market", "takeProfit": "", "type": "tpsl"},
                      {"orderId": "S2", "category": "USDT-FUTURES", "symbol": "BTCUSDT", "qty": "0.01", "status": "pending",
                       "takeProfit": "99000", "tpTriggerBy": "market", "tpOrderType": "market", "stopLoss": ""},
                      {"orderId": "S3", "category": "USDT-FUTURES", "symbol": "ETHUSDT", "qty": "0.2", "status": "pending",
                       "stopLoss": "3300"},
                      {"orderId": "S4", "category": "USDT-FUTURES", "symbol": "BTCUSDT", "qty": "0.01", "status": "cancelled",
                       "stopLoss": "80000"}],
            "place_fail": [], "modify_fail": [], "strat_place_fail": None, "cancel_fail": False, "settings_fail": None,
            "hist": None, "assets": None, "moa": None, "fill": None, "orders": {}, "info": None, "close_fill": 1.0}


def uta_routes(st):
    def settings(q, b):
        if st["settings_fail"] is not None:
            return st["settings_fail"]
        return {"uid": "1", "accountMode": st["mode"], "accountLevel": "basic", "holdMode": st["hold"],
                "symbolConfigList": [{"category": "USDT-FUTURES", "symbol": "ETHUSDT", "marginMode": "crossed", "leverage": "20"},
                                     {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "marginMode": "isolated", "leverage": "3"},
                                     {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "marginMode": "crossed", "leverage": "5"}]}

    def positions(q, b):
        return {"list": [dict(p) for p in st["pos"]]}

    def place(q, b):
        if st["place_fail"]:
            return st["place_fail"].pop(0)
        oid = str(2001 + len(st["orders"]))
        st["orders"][oid] = dict(b)
        if str(b.get("clientOid", "")).endswith(("-x", "-x2")):          # 비상 청산(reduce-only) - 포지션을 그만큼 줄인다
            _close(b)
        return {"orderId": oid, "clientOid": b.get("clientOid")}

    def _close(b):
        _ps = b.get("posSide") or ("long" if b.get("side") == "sell" else "short")
        for p in st["pos"]:
            if p.get("symbol") == b.get("symbol") and p.get("posSide") == _ps:
                _cf = st.get("close_fill", 1.0)
                _cf = (_cf.pop(0) if _cf else 1.0) if isinstance(_cf, list) else _cf
                _left = round(float(p.get("total") or 0) - float(_cf) * float(b.get("qty") or 0), 8)
                p["total"] = str(max(_left, 0.0))

    def order_info(q, b):
        if callable(st["fill"]):
            return st["fill"](q)
        o = st["orders"].get(q.get("orderId")) or {}
        return {"orderId": q.get("orderId"), "orderStatus": "filled", "cumExecQty": o.get("qty", "0"),
                "posSide": o.get("posSide", ""), "holdMode": st["hold"]}

    def moa(q, b):
        if st["moa"] is not None:
            return st["moa"]
        return {"available": "1500.25", "maxOpen": "", "buyOpenCost": "", "sellOpenCost": ""}

    def close(q, b):
        if "symbol" in b:
            st["pos"] = [p for p in st["pos"] if p["symbol"] != b["symbol"]]
        else:
            st["pos"] = []
        return {"list": [{"orderId": "c1", "clientOid": "", "code": "", "msg": ""}]}

    def _beyond(b):
        # UTA 오류 표 25580/25582: TP/SL 손절이 평단의 수익 쪽이면 거절(시험 세계에서 켤 때만)
        if not st.get("entry_rule") or not b.get("stopLoss"):
            return None
        for p in st["pos"]:
            if p.get("symbol") == (b.get("symbol") or "BTCUSDT") and float(p.get("total") or 0) > 0:
                _avg = float(p.get("avgPrice") or 0)
                if p.get("posSide") == "long" and float(b["stopLoss"]) >= _avg:
                    return Resp(400, {"code": "25580", "msg": "For long position TP/SL (close long), the stop-loss trigger price "
                                                              "must be less than the average entry price"})
                if p.get("posSide") == "short" and float(b["stopLoss"]) <= _avg:
                    return Resp(400, {"code": "25582", "msg": "For short position TP/SL (close short), the stop-loss trigger "
                                                              "price must be greater than the average entry price"})
        return None

    def modify(q, b):
        if b["orderId"] in st["modify_fail"]:
            return Resp(400, {"code": "25596", "msg": "Cannot modify strategy in triggered or error state"})
        _bz = _beyond(b)
        if _bz is not None:
            return _bz
        for r in st["strat"]:                                           # 바뀐 손절가가 목록에 보인다
            if r.get("orderId") == b["orderId"] and b.get("stopLoss"):
                r["stopLoss"] = b["stopLoss"]
            if r.get("orderId") == b["orderId"] and b.get("triggerPrice"):
                r["triggerPrice"], r["triggerOrderPrice"] = b["triggerPrice"], b.get("triggerOrderPrice")
        return {"orderId": b["orderId"], "clientOid": ""}

    def strat_place(q, b):
        if b.get("type") == "trigger" and st.get("trig_fail") is not None:
            return st["trig_fail"]
        _bz = _beyond(b)
        if _bz is not None:
            return _bz
        f = st["strat_place_fail"]
        if isinstance(f, list):
            if f:
                return f.pop(0)
        elif f is not None:
            return f
        st["strat"].append(dict(b, orderId="S9", status="pending"))       # 전략 주문 목록에 나온다(문서: unfilled-strategy-orders)
        return {"orderId": "S9", "clientOid": b.get("clientOid", "")}

    def candles(q, b):
        s = int(q["startTime"])
        return [[str(s + i * 3600000), str(90000 + i), "1", "1", str(90000.5 + i), "1", "1"] for i in (3, 1, 0, 2)]

    def hist(q, b):
        if st["hist"]:
            return st["hist"](q)
        return {"list": [{"positionId": "P1", "category": "USDT-FUTURES", "symbol": "EOSUSDT", "marginCoin": "USDT",
                          "holdMode": "one_way_mode", "posSide": "long", "marginMode": "crossed", "openPriceAvg": "1960.001",
                          "closePriceAvg": "1959.999", "openTotalPos": "58", "closeTotalPos": "58", "cumRealisedPnl": "-0.116",
                          "netProfit": "-45.588", "totalFunding": "0", "openFeeTotal": "-22.7360116",
                          "closeFeeTotal": "-22.7359884", "createdTime": "1792900000000", "updatedTime": "1792929656321"}],
                "cursor": ""}

    def assets(q, b):
        if st["assets"] is not None:
            return st["assets"]
        return {"accountEquity": "2000", "usdtEquity": "2001.5",
                "assets": [{"coin": "BGB", "available": "9"}, {"coin": "USDT", "equity": "1600", "available": "1500.25"}]}

    return {
        ("GET", PROBE): settings,
        ("GET", "/api/v3/position/current-position"): positions,
        ("GET", "/api/v3/account/assets"): assets,
        ("GET", "/api/v3/market/instruments"): lambda q, b: [{"symbol": "BTCUSDT", "category": "USDT-FUTURES",
                                                              "pricePrecision": "1", "priceMultiplier": "0.1",
                                                              "quantityPrecision": "4", "quantityMultiplier": "0.0001",
                                                              "maxLeverage": "150"}],
        ("POST", "/api/v3/account/set-leverage"): lambda q, b: "success",
        ("POST", "/api/v3/trade/place-order"): place,
        ("POST", "/api/v3/trade/cancel-order"): lambda q, b: (Resp(400, {"code": "25204", "msg": "Order does not exist"})
                                                             if st["cancel_fail"] else {"orderId": b["orderId"]}),
        ("POST", "/api/v3/trade/close-positions"): close,
        ("GET", "/api/v3/market/tickers"): lambda q, b: [{"category": "USDT-FUTURES", "symbol": "BTCUSDT", "lastPrice": "90020.5"}],
        ("GET", "/api/v3/market/candles"): candles,
        ("GET", "/api/v3/trade/unfilled-strategy-orders"): lambda q, b: [dict(r) for r in st["strat"]
                                                                          if (r.get("type") == "trigger") == (q.get("type") == "trigger")],
        ("POST", "/api/v3/trade/modify-strategy-order"): modify,
        ("POST", "/api/v3/trade/place-strategy-order"): strat_place,
        ("GET", "/api/v3/position/history-position"): hist,
        ("GET", "/api/v3/trade/order-info"): order_info,
        ("POST", "/api/v3/account/max-open-available"): moa,
        ("POST", "/api/v3/trade/cancel-strategy-order"): lambda q, b: (st.__setitem__("strat", [r for r in st["strat"]
                                                                                              if r.get("orderId") != b.get("orderId")])
                                                                       or None),
        ("GET", "/api/v3/account/info"): lambda q, b: (st["info"] if st["info"] is not None else
                                                       {"userId": "1", "permType": "read-and-write",
                                                        "permissions": ["uta_mgt", "uta_trade"]}),
    }


def uta_net(st):
    # UTA 키로 v2를 부르면 거절(어떤 코드인지는 문서에 없다 - 여기선 아무 4xx). 이 시험은 v2 호출이 0번인지 본다.
    return Net(uta_routes(st), default=lambda q, b: Resp(400, {"code": "40099", "msg": "classic endpoint (test stub)"}))


def uta_case(warm=True, **kw):
    st = uta_state()
    st.update(kw)
    net, clock = uta_net(st), Clock()
    bind(BG, net, clock)
    fresh(BG)
    b = mk(BG)
    if warm:
        b._mstate().update(mode="uta", hold=st["hold"], probe_at=clock.time())
    return st, net, clock, b


def v2_calls(net):
    return [c[1] for c in net.calls if c[1].startswith("/api/v2/")]


# ── D: 판별 ────────────────────────────────────────────────────────────────────────────────
st, net, clock, b = uta_case(warm=False)
pos = b.list_open_positions()
ck("D1 unified → UTA: 판별 GET 하나 뒤 v3 포지션, v2 없음",
   b.account_type() == "uta" and paths(net) == ["GET /api/v3/account/settings", "GET /api/v3/position/current-position"]
   and not v2_calls(net), paths(net))
b.available_usdt()
b2 = mk(BG)
b2.current_market_price("BTCUSDT")
ck("D2 판별은 키마다 한 번(같은 키 새 인스턴스도 다시 묻지 않음)", [c[1] for c in net.calls].count(PROBE) == 1, paths(net))

st, net, clock, b = uta_case(warm=False, mode="hybrid")
b.list_open_positions()
ck("D3 hybrid → UTA(v3)", b.account_type() == "uta" and not v2_calls(net), paths(net))

for _m in ("upgrading", "switching"):
    st, net, clock, b = uta_case(warm=False, mode=_m)
    r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
    _sent = [c for c in net.calls if c[0] == "POST"]
    ck(f"D4 {_m}: 진입은 아무것도 보내지 않고 'not sent:' 오류(유형 바뀌는 중, 1분 뒤)",
       not _sent and str(r.get("error", "")).startswith("not sent:") and _m in r["error"] and "minute" in r["error"],
       (r, paths(net)))
    try:
        b.list_open_positions()
        _ex = None
    except Exception as e:
        _ex = e
    ck(f"D5 {_m}: 포지션 조회(청산 길)는 '바뀌는 중'이어도 늘 다시 묻는다(검증 P2-2) - 여전히 바뀌는 중이면 BitgetModeError, 아무것도 보내지 않음",
       isinstance(_ex, BG.BitgetModeError) and [c[1] for c in net.calls].count(PROBE) == 2 and b.account_type() == "unknown"
       and not [c for c in net.calls if c[1] != PROBE], (_ex, paths(net)))
    try:
        b.healthcheck()
        _ex = None
    except Exception as e:
        _ex = e
    ck(f"D6 {_m}: 연결 테스트(healthcheck)도 같은 오류 문장", isinstance(_ex, BG.BitgetModeError) and _m in str(_ex), _ex)
    net.calls.clear()
    ck(f"D6b {_m}: 지정가 진입, 손절 이동은 60초 안이면 다시 묻지 않고 주문 없음",
       not b.place_limit_entry(symbol="BTCUSDT", side="LONG", size=0.01, price=90000, dry_run=False).get("order_id")
       and b.set_stop("BTCUSDT", 86000).get("error") and not net.calls, paths(net))
    try:
        b.close_symbol("BTCUSDT", dry_run=False)
        _ex = None
    except Exception as e:
        _ex = e
    ck(f"D6c {_m}: 청산(close_symbol)은 다시 묻는다 - 아직 바뀌는 중이면 주문 없이 BitgetModeError",
       isinstance(_ex, BG.BitgetModeError) and paths(net) == [f"GET {PROBE}"], (_ex, paths(net)))
    clock.t += 61
    net.calls.clear()
    st["mode"] = "unified"
    r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-5", dry_run=False)
    ck(f"D6d {_m}: 60초가 지나면 거래 길에서도 한 번 다시 묻는다 - 바꾸기가 끝났으면(unified) 그 호출이 v3로 진행",
       b.account_type() == "uta" and paths(net)[0] == f"GET {PROBE}" and "/api/v3/trade/place-order" in [c[1] for c in net.calls]
       and not v2_calls(net), (r, paths(net)))

# 모름(네트워크) → 종전 v2, 거래 길에서는 다시 묻지 않는다(최종 리뷰 E1 P2-10) - healthcheck만 다시 묻는다
st = classic_state()
_routes = classic_routes(st)
_routes[("GET", PROBE)] = lambda q, b: ConnectionError("network down")
net, clock = Net(_routes), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b.list_open_positions()
clock.t += 60
b.list_open_positions()
clock.t += 61
b.list_open_positions()
ck("D7 판별이 네트워크로 실패 → 종전 v2 경로, 121초 뒤에도 거래 길에서는 다시 묻지 않는다",
   paths(net) == ["GET /api/v3/account/settings", "GET /api/v2/mix/position/all-position", "GET /api/v2/mix/position/all-position",
                  "GET /api/v2/mix/position/all-position"] and b.account_type() == "unknown",
   paths(net))
net.calls.clear()
_hc = b.healthcheck()
ck("D7b 모름이면 healthcheck(거래 길 밖)가 다시 묻고, 또 네트워크면 한 번 더(1초 뒤) - 그 뒤 종전 v2 확인",
   _hc is True and [c[1] for c in net.calls].count(PROBE) == 2 and paths(net)[-1] == "GET /api/v2/mix/position/all-position",
   paths(net))

# 관리(읽기) 권한이 없는 키: settings 거절 → 거래(읽기) 권한의 포지션 조회로 판별
st, net, clock, b = uta_case(warm=False, settings_fail=Resp(400, {"code": "25620", "msg": "permission denied"}))
b.list_open_positions()
ck("D8 settings 권한 거절(25620) → current-position 성공 = UTA",
   b.account_type() == "uta" and paths(net)[:2] == ["GET /api/v3/account/settings", "GET /api/v3/position/current-position"]
   and not v2_calls(net), paths(net))
_st = classic_state()
_routes = classic_routes(_st)
_routes[("GET", PROBE)] = lambda q, b: Resp(400, {"code": "40014", "msg": "Incorrect permissions"})
_routes[("GET", "/api/v3/position/current-position")] = lambda q, b: NOT_UNIFIED
net, clock = Net(_routes), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b.list_open_positions()
ck("D9 settings 다른 거절 + current-position 25245 = 클래식", b.account_type() == "classic"
   and paths(net)[-1] == "GET /api/v2/mix/position/all-position", paths(net))

# UTA → 클래식으로 되돌린 키: v3가 25245 → 클래식으로 바꾸고 그 호출을 v2로 다시
st = classic_state()
_routes = classic_routes(st)
# 공개 시세(종목 스펙)는 계정 유형과 상관없이 답한다고 둔다 - 25245는 계정 끝점에서
_routes[("GET", "/api/v3/market/instruments")] = lambda q, bb: [{"symbol": "BTCUSDT", "pricePrecision": "1", "priceMultiplier": "0.1"}]
net, clock = Net(_routes, default=lambda q, b: NOT_UNIFIED), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b._mstate().update(mode="uta", probe_at=clock.time())
pos = b.list_open_positions()
ck("D10 기억은 UTA인데 v3가 25245 → 클래식으로 바꾸고 같은 호출을 v2로(결과 v2)",
   b.account_type() == "classic" and [p.symbol for p in pos] == ["BTCUSDT", "ETHUSDT"]
   and paths(net) == ["GET /api/v3/position/current-position", "GET /api/v2/mix/position/all-position"], paths(net))
net.calls.clear()
b.available_usdt()
ck("D11 그 뒤로는 v2만(다시 묻지 않음)", paths(net) == ["GET /api/v2/mix/account/account"], paths(net))
fresh(BG)
b._mstate().update(mode="uta", hold="one_way_mode", probe_at=clock.time())
net.calls.clear()
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000.04, custom_tag="EQ-AP-9", dry_run=False)
_po = [c for c in net.calls if c[0] == "POST"]
ck("D12 진입 중 25245(v3 거절 = 안 들어감) → v2로 한 번 진입, 주문은 v3 거절 1 + v2 1",
   r.get("entry", {}).get("orderId") == "1001" and [c[1] for c in _po] == ["/api/v3/trade/place-order", "/api/v2/mix/order/place-order"]
   and _po[1][2]["presetStopLossPrice"] == "85000.0", (r, paths(net)))
fresh(BG)
b._mstate().update(mode="uta", hold="one_way_mode", probe_at=clock.time())
net.calls.clear()
_ss = b.set_stop("BTCUSDT", 86000.04)
ck("D13 손절 이동 중 25245 → v2 손절 이동으로(삼키지 않음)",
   _ss.get("error") is None and "/api/v2/mix/order/modify-tpsl-order" in [c[1] for c in net.calls]
   and b.account_type() == "classic", (_ss, paths(net)))

# 클래식 → UTA로 올린 키(앱이 켜진 채): 거래 길에서는 다시 묻지 않는다(최종 리뷰 E1 P2-10) - 다음 healthcheck가 물어 v3로 옮긴다
st, net, clock, b = uta_case(warm=False)
b._mstate().update(mode="classic", probe_at=clock.time() - 61)
try:
    b.list_open_positions()
    _ex = None
except Exception as e:
    _ex = e
ck("D14 기억은 클래식인데 v2가 거절 → 거래 길에서는 다시 묻지 않고 종전 오류 그대로(v3 없음)",
   isinstance(_ex, RuntimeError) and PROBE not in [c[1] for c in net.calls]
   and not [c for c in net.calls if c[1].startswith("/api/v3/")], (_ex, paths(net)))
net.calls.clear()
b.healthcheck()
pos = b.list_open_positions()
ck("D14b 다음 healthcheck(사전점검, 연결 감시)가 다시 물어 UTA로 옮기고, 그 뒤 호출은 v3",
   b.account_type() == "uta" and [p.symbol for p in pos] == ["BTCUSDT", "ETHUSDT"]
   and paths(net)[0] == "GET /api/v3/account/settings" and paths(net)[-1] == "GET /api/v3/position/current-position"
   and not v2_calls(net), paths(net))
st, net, clock, b = uta_case(warm=False)
b._mstate().update(mode="classic", probe_at=clock.time() - 61)
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000.04, custom_tag="EQ-AP-7", dry_run=False)
ck("D15 진입: 기억은 클래식, v2 확실한 거절 → 그 오류 그대로(진입 길에서 다시 묻지도 v3로 보내지도 않는다)",
   r.get("error") and [c[1] for c in net.calls if c[0] == "POST"] == ["/api/v2/mix/order/place-order"]
   and PROBE not in [c[1] for c in net.calls], (r, paths(net)))
for _amb, _why in ((Resp(502, {"code": None, "msg": "bad gateway"}), "5xx"),
                   (Resp(200, {"code": "40010", "msg": "Request timed out"}), "40010"),
                   (ConnectionError("read timed out"), "network")):
    st, net, clock, b = uta_case(warm=False)
    b._mstate().update(mode="classic", probe_at=clock.time() - 61)
    net.routes[("POST", "/api/v2/mix/order/place-order")] = (lambda a: (lambda q, bb: a))(_amb)
    r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
    ck(f"D16 진입: v2 결과 모름({_why}) → 다시 묻지도 v3로 보내지도 않는다(오류는 종전 그대로)",
       r.get("error") and not [c for c in net.calls if c[1].startswith("/api/v3/")], (r, paths(net)))
st, net, clock, b = uta_case(warm=False)
b._mstate().update(mode="classic", probe_at=clock.time() - 10)
try:
    b.list_open_positions()
    _ex = None
except Exception as e:
    _ex = e
ck("D17 마지막 판별 60초 안이면 v2 거절에도 다시 묻지 않는다(종전 오류 그대로)",
   isinstance(_ex, RuntimeError) and "code=40099" in str(_ex) and PROBE not in [c[1] for c in net.calls], (_ex, paths(net)))
st = classic_state()
net, clock = Net(classic_routes(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b._mstate().update(mode=None, inconclusive_at=clock.time() - 119.5, probe_at=clock.time() - 119.5)
b.flatten_all(dry_run=False)
ck("D19 한 호출 안은 한 경로: 청산 중(1초 대기 사이) 판별 유효 기간이 끝나도 안쪽 포지션 재확인이 다시 묻지 않는다",
   paths(net) == ["GET /api/v2/mix/position/all-position", "POST /api/v2/mix/order/close-positions",
                  "GET /api/v2/mix/position/all-position"], paths(net))
st, net, clock, b = uta_case(warm=True)
b._mstate().update(mode="classic")
b.healthcheck()
ck("D18 연결 테스트(healthcheck)는 기억과 상관없이 새로 묻는다(클래식 기억 → UTA 확인 → v3 조회)",
   b.account_type() == "uta" and paths(net) == ["GET /api/v3/account/settings", "GET /api/v3/position/current-position"],
   paths(net))

# ── U: UTA 본문 ────────────────────────────────────────────────────────────────────────────
st, net, clock, b = uta_case()
pos = b.list_open_positions()
ck("U1 포지션: total·posSide → 부호 수량, 0 줄 제외, raw에 평단 avgPrice, 요청 category=USDT-FUTURES",
   [(p.symbol, p.net_qty) for p in pos] == [("BTCUSDT", 0.01), ("ETHUSDT", -0.2)] and pos[0].raw["avgPrice"] == "90000.5"
   and net.calls[0][1] == "/api/v3/position/current-position?category=USDT-FUTURES", (pos, net.calls[:1]))
st, net, clock, b = uta_case(pos=[{"symbol": "BTCUSDT", "posSide": "long", "total": "0.02", "holdMode": "hedge_mode"},
                                  {"symbol": "BTCUSDT", "posSide": "short", "total": "0.01", "holdMode": "hedge_mode"}])
ck("U1b 헤지 모드 두 줄(롱, 숏)은 따로", [p.net_qty for p in b.list_open_positions()] == [0.02, -0.01]
   and b._hold() == "hedge_mode")
ck("U1c position_qty = 그 종목 수량 합(절댓값)", b.position_qty("BTCUSDT.P") == 0.03)

st, net, clock, b = uta_case()
ck("U2 가용 USDT(리뷰 P2-7) = max-open-available의 available(문서: 선물이면 쓸 수 있는 USDT), 조회 POST 본문",
   b.available_usdt() == 1500.25 and paths(net) == ["POST /api/v3/account/max-open-available"]
   and net.calls[0][2] == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "orderType": "market", "side": "buy",
                           "marginMode": "crossed"}, net.calls)
st, net, clock, b = uta_case(moa=Resp(400, {"code": "25620", "msg": "permission denied"}),
                             assets={"effEquity": "1200.5", "imr": "200.25", "usdtEquity": "9999",
                                     "assets": [{"coin": "USDT", "available": "8888"}]})
ck("U2b max-open-available 실패 → assets effEquity - imr(교차 마진 가용, USD), 코인별 available은 안 씀",
   b.available_usdt() == 1000.25, paths(net))
st, net, clock, b = uta_case(moa=Resp(400, {"code": "25620", "msg": "x"}))
net.routes[("GET", "/api/v3/account/assets")] = lambda q, bb: Resp(400, {"code": "25620", "msg": "permission denied"})
ck("U2c 둘 다 실패 = None(v2와 같은 계약)", b.available_usdt() is None)

st, net, clock, b = uta_case()
ck("U3 현재 레버리지 = settings symbolConfigList의 BTCUSDT 교차 줄(격리 줄 아님)", b.current_leverage("BTCUSDT.P") == 5.0)
ck("U3b 최대 레버리지 = instruments maxLeverage", b.max_leverage("BTCUSDT") == 150.0
   and net.calls[-1][1] == "/api/v3/market/instruments?category=USDT-FUTURES&symbol=BTCUSDT", net.calls[-1][1])
net.calls.clear()
ck("U3c set_leverage 본문", b.set_leverage("BTCUSDT.P", 20) is True and net.calls[0][1] == "/api/v3/account/set-leverage"
   and net.calls[0][2] == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "leverage": "20", "marginMode": "crossed"},
   net.calls)
net.calls.clear()
lv = b.ensure_leverage("BTCUSDT", 0.1, 90000)
# 필요 = 9000/(1500.25×0.85) = 7.06 > 5 → ceil(7.06×1.3) = 10
ck("U3d ensure_leverage(같은 수학): 가용 1500.25, 현재 5x → 10x로 올림, v3만",
   lv.get("new") == 10 and lv.get("cur") == 5.0 and net.calls[-1][2]["leverage"] == "10" and not v2_calls(net), (lv, paths(net)))
# 리뷰 P2-7: 지금 레버리지를 모르면 바꾸지 않는다(종전 수학은 모름 = 0x로 보고 실제보다 낮게 '올릴' 수 있었다)
st, net, clock, b = uta_case(pos=[])
net.routes[("GET", PROBE)] = lambda q, bb: {"accountMode": "unified", "holdMode": "one_way_mode", "symbolConfigList": []}
net.calls.clear()
lv = b.ensure_leverage("BTCUSDT", 0.1, 90000)
ck("U3e 종목 줄도 포지션도 없어 레버리지 모름 → set-leverage 없음, 이유를 error로(앱 로그 '자동 조정 불가 … 그대로 진행')",
   lv.get("new") is None and lv.get("cur") is None and "레버리지" in str(lv.get("error"))
   and "/api/v3/account/set-leverage" not in [c[1] for c in net.calls], (lv, paths(net)))
st, net, clock, b = uta_case(pos=[{"symbol": "BTCUSDT", "posSide": "long", "total": "0.01", "leverage": "25"}])
net.routes[("GET", PROBE)] = lambda q, bb: {"accountMode": "unified", "holdMode": "one_way_mode", "symbolConfigList": []}
ck("U3f 종목 줄이 없으면 그 종목 포지션의 leverage", b.current_leverage("BTCUSDT") == 25.0)

# 진입 + 손절(리뷰 P1-3): 손절을 주문에 붙이지 않고, 체결 수량을 확인한 뒤 바로 TP/SL 전략 주문(partial, qty = 체결 수량)
st, net, clock, b = uta_case()
r = b.place_entry(symbol="BTCUSDT.P", side="LONG", size=0.0123, stop_loss_price=85000.04,
                  custom_tag="EQ-AP-1793000000000", dry_run=False)
_po = [c for c in net.calls if c[0] == "POST"]
ck("U4 시장가 진입 본문(category, qty, clientOid) - 손절 첨부(stopLoss), productType, marginCoin, marginMode, size 없음",
   _po[0][1] == "/api/v3/trade/place-order"
   and _po[0][2] == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "side": "buy", "orderType": "market", "qty": "0.012",
                     "clientOid": "EQ-AP-1793000000000"}, _po[:1])
ck("U4a 체결 확인 → 손절 전략 주문: order-info(orderId) 한 번 뒤 place-strategy-order(tpsl, partial, qty=체결 0.012, 단방향 닫기 = "
   "side sell + reduceOnly yes(문서 side 설명), stopLoss 0.1 틱, 최종가 트리거, 시장가, clientOid=표식-sl)",
   paths(net) == ["GET /api/v3/market/instruments", "POST /api/v3/trade/place-order", "GET /api/v3/trade/order-info",
                  "POST /api/v3/trade/place-strategy-order"]
   and net.calls[2][1] == "/api/v3/trade/order-info?orderId=2001"
   and _po[1][2] == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "type": "tpsl", "tpslMode": "partial", "qty": "0.012",
                     "side": "sell", "reduceOnly": "yes", "stopLoss": "85000.0", "slTriggerBy": "market",
                     "slOrderType": "market", "clientOid": "EQ-AP-1793000000000-sl"}
   and r == {"entry": {"orderId": "2001", "clientOid": "EQ-AP-1793000000000"}, "stop": True, "stop_error": None,
             "stop_order_id": "S9", "filled_qty": 0.012}, (r, paths(net), _po[1:]))
ck("U4a2 건 손절은 전략 주문 목록에 qty와 함께 나와 손절 감시(stop_plans)가 찾는다",
   any(x["orderId"] == "S9" and x["price"] == 85000.0 for x in b.stop_plans("BTCUSDT")), b.stop_plans("BTCUSDT"))
st, net, clock, b = uta_case(hold="hedge_mode")
r = b.place_entry(symbol="BTCUSDT", side="SHORT", size=0.01, stop_loss_price=95000.06, dry_run=False)
_po = [c for c in net.calls if c[0] == "POST"]
ck("U4b 헤지 모드: 진입 posSide=short(숏 열기 = sell + short), 손절 = buy + posSide short, 95000.1",
   _po[0][2].get("posSide") == "short" and _po[0][2]["side"] == "sell" and "stopLoss" not in _po[0][2]
   and _po[1][2]["side"] == "buy" and _po[1][2]["posSide"] == "short" and "reduceOnly" not in _po[1][2]
   and _po[1][2]["stopLoss"] == "95000.1"
   and r.get("stop") is True, _po)
st, net, clock, b = uta_case()
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="t", dry_run=True)
ck("U4c dry_run: 주문(POST) 없음, would_place(손절 없음), would_place_stop, would_place_sl(전략 주문 본문)",
   not [c for c in net.calls if c[0] == "POST"] and "stopLoss" not in r["would_place"]
   and r["would_place_stop"] == "85000.0" and r["would_place_sl"]["tpslMode"] == "partial"
   and r["would_place_sl"]["stopLoss"] == "85000.0", (r, paths(net)))
st, net, clock, b = uta_case()
ck("U4d 방향 오류, 수량 0은 요청 없이 거절(UTA 진입 오류에는 늘 stop_attached False)",
   b.place_entry(symbol="BTCUSDT", side="X", size=0.01, dry_run=False)["error"].startswith("invalid side")
   and b.place_entry(symbol="BTCUSDT", side="LONG", size=0.0001, dry_run=False) == {"error": "qty<=0", "stop_attached": False}
   and not net.calls, paths(net))
st, net, clock, b = uta_case()
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, dry_run=False)
ck("U4e 손절 없는 진입: stop False, stop_error 'no stop provided'", r.get("stop") is False
   and r.get("stop_error") == "no stop provided" and "stopLoss" not in net.calls[-1][2], r)
st, net, clock, b = uta_case()
b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, custom_tag="EQ AP#1793000000000-abcdefghijklmnopqrstuvwxyz", dry_run=False)
_co = net.calls[-1][2]["clientOid"]
ck("U4f clientOid = v3 규칙(허용 글자만, 32자 이하)", len(_co) <= 32 and all(c.isalnum() or c in ".:/_-" for c in _co), _co)

# 포지션 모드 재시도: 모드를 모르거나 틀렸으면 확실한 거절 뒤 settings를 다시 읽고 한 번만
st, net, clock, b = uta_case(hold="hedge_mode")
b._mstate()["hold"] = None
st["place_fail"].append(Resp(400, {"code": "40017", "msg": "Parameter verification failed posSide"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-5", dry_run=False)
_po = [c for c in net.calls if c[1] == "/api/v3/trade/place-order"]
ck("U5 모드 모름 → 단방향으로 보냄, 거절 → settings가 헤지 → posSide=long, 새 clientOid(표식-h, 리뷰 P2-8)로 한 번 더",
   r.get("entry") and len(_po) == 2 and "posSide" not in _po[0][2] and _po[1][2]["posSide"] == "long"
   and _po[0][2]["clientOid"] == "EQ-AP-5" and _po[1][2]["clientOid"] == "EQ-AP-5-h"
   and b._hold() == "hedge_mode", (r, _po))
st, net, clock, b = uta_case()
st["place_fail"].append(Resp(400, {"code": "25203", "msg": "Insufficient margin"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, dry_run=False)
ck("U5b 모드가 같으면(단방향) 다시 보내지 않고 거절 그대로(GUI는 '거절'로 분류)",
   len([c for c in net.calls if c[0] == "POST"]) == 1 and "failed" not in r["error"] and "HTTP 400: code=25203" in r["error"], r)
for _amb in (Resp(500, {"code": "45001", "msg": "Unknown error"}), Resp(200, {"code": "40010", "msg": "Request timed out"}),
             ConnectionError("read timeout")):
    st, net, clock, b = uta_case()
    b._mstate()["hold"] = None
    st["place_fail"].append(_amb)
    r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
    ck(f"U5c 결과 모름({str(_amb.text if isinstance(_amb, Resp) else _amb)[:30]}) → 다시 보내지 않는다, 앱은 '모름'으로 분류",
       r.get("error") and len([c for c in net.calls if c[0] == "POST"]) == 1 and PROBE not in [c[1] for c in net.calls]
       and ("HTTP 4" not in r["error"] and "failed: code=" not in r["error"]), (r, paths(net)))

# 지정가 진입, 지정가 청산
st, net, clock, b = uta_case()
r = b.place_limit_entry(symbol="BTCUSDT", side="SHORT", size=0.01, price=90001.04, stop_loss_price=95000,
                        custom_tag="EQ-AP-1-1", dry_run=False)
ck("U6 지정가 진입: post_only, 가격 0.1 틱, 손절 첨부, order_id",
   r == {"order_id": "2001"} and net.calls[-1][2] == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "side": "sell",
                                                      "orderType": "limit", "price": "90001.0", "qty": "0.01",
                                                      "timeInForce": "post_only", "clientOid": "EQ-AP-1-1",
                                                      "stopLoss": "95000.0", "slOrderType": "market"}, (r, net.calls[-1]))
st, net, clock, b = uta_case()
r = b.place_limit_close(symbol="BTCUSDT", price=90100.02, dry_run=False)
ck("U6b 지정가 청산(단방향): 반대 side + reduceOnly=yes, 전량",
   r == {"order_id": "2001"} and net.calls[-1][2] == {"category": "USDT-FUTURES", "symbol": "BTCUSDT", "side": "sell",
                                                      "orderType": "limit", "price": "90100.0", "qty": "0.01",
                                                      "timeInForce": "post_only", "reduceOnly": "yes"}, (r, net.calls[-1]))
st, net, clock, b = uta_case(hold="hedge_mode", pos=[{"symbol": "BTCUSDT", "posSide": "short", "total": "0.02",
                                                      "holdMode": "hedge_mode"}])
r = b.place_limit_close(symbol="BTCUSDT", price=90100.02, dry_run=True)
ck("U6c 지정가 청산(헤지, 숏): buy + posSide=short, reduceOnly 없음, dry_run은 POST 없음",
   r["would_place"]["side"] == "buy" and r["would_place"]["posSide"] == "short" and "reduceOnly" not in r["would_place"]
   and not [c for c in net.calls if c[0] == "POST"], r)
st, net, clock, b = uta_case(pos=[])
ck("U6d 포지션 없으면 flat", b.place_limit_close(symbol="BTCUSDT", price=1, dry_run=False) == {"flat": True})

# 청산
st, net, clock, b = uta_case()
r = b.flatten_all(dry_run=True)
ck("U7 flatten dry_run: 계획만(두 종목), POST 없음", len(r.planned) == 2 and not r.closed and not [c for c in net.calls if c[0] == "POST"])
st, net, clock, b = uta_case()
r = b.flatten_all(dry_run=False)
_po = [c for c in net.calls if c[0] == "POST"]
ck("U7b flatten live: close-positions {category} 한 번(flash close), 다시 읽어 확인, 1초 대기",
   [c[2] for c in _po if c[1] == "/api/v3/trade/close-positions"] == [{"category": "USDT-FUTURES"}] and len(r.closed) == 2
   and r.ok and clock.sleeps == [1], (r, _po))
st, net, clock, b = uta_case()
r = b.flatten_all(dry_run=False, only=_only_btc)
_po = [c for c in net.calls if c[0] == "POST"]
ck("U7c 자산별 청산: 종목 지정 {category, symbol} 하나, ETH 그대로",
   [c[2] for c in _po if c[1] == "/api/v3/trade/close-positions"] == [{"category": "USDT-FUTURES", "symbol": "BTCUSDT"}]
   and [p.symbol for p in r.closed] == ["BTCUSDT"] and r.ok, (r, _po))
st, net, clock, b = uta_case()
net.routes[("POST", "/api/v3/trade/close-positions")] = lambda q, bb: {"list": []}
r = b.flatten_all(dry_run=False)
ck("U7d 닫히지 않으면 STILL OPEN 오류(응답 줄 코드는 믿지 않고 다시 읽어 확인)",
   not r.ok and any("STILL OPEN" in e for e in r.errors) and not r.closed, r.errors)
st, net, clock, b = uta_case()
_r7e = b.close_symbol("BTCUSDT.P", dry_run=False)
ck("U7e close_symbol live: 종목 지정 flash close 뒤 확인", _r7e.get("closed") is True and _r7e.get("had") == 1
   and [c[2] for c in net.calls if c[1] == "/api/v3/trade/close-positions"] == [{"category": "USDT-FUTURES", "symbol": "BTCUSDT"}],
   _r7e)
st, net, clock, b = uta_case()
ck("U7f close_symbol dry_run: POST 없음", b.close_symbol("BTCUSDT", dry_run=True) == {"closed": False, "had": 1, "dry_run": True}
   and not [c for c in net.calls if c[0] == "POST"])
st, net, clock, b = uta_case()
net.routes[("POST", "/api/v3/trade/close-positions")] = lambda q, bb: Resp(400, {"code": "25227", "msg": "No position available to close"})
r = b.close_symbol("BTCUSDT", dry_run=False)
ck("U7g close 거절 = closed False + 오류 원문", r["closed"] is False and "25227" in r["error"], r)

# 취소, 시세, 4H 블록
st, net, clock, b = uta_case()
ck("U8 cancel-order {orderId, category}", b.cancel_order("BTCUSDT", 777) is True
   and net.calls[-1][2] == {"orderId": "777", "category": "USDT-FUTURES"}, net.calls[-1])
st, net, clock, b = uta_case(cancel_fail=True)
ck("U8b 취소 실패 = False", b.cancel_order("BTCUSDT", 777) is False)
st, net, clock, b = uta_case()
ck("U9 현재가 = tickers lastPrice", b.current_market_price("BTCUSDT.P") == 90020.5
   and net.calls[-1][1] == "/api/v3/market/tickers?category=USDT-FUTURES&symbol=BTCUSDT", net.calls[-1][1])
st, net, clock, b = uta_case()
r = b.block_4h("BTCUSDT.P", END)
_q = net.calls[-1][1]
ck("U10 4H 블록: candles interval=1H, 시작~끝-1ms, limit 10, (첫 시가, 끝 종가)",
   r == (90000.0, 90003.5) and "interval=1H" in _q and "granularity" not in _q
   and f"startTime={int(END.timestamp() * 1000) - 4 * 3600000}" in _q and f"endTime={int(END.timestamp() * 1000) - 1}" in _q
   and "limit=10" in _q, (r, _q))
net.routes[("GET", "/api/v3/market/candles")] = lambda q, bb: [["1", "1", "1", "1", "1", "1", "1"]]
ck("U10b 봉 모자람 = None(판정 불가)", b.block_4h("BTCUSDT", END) is None)

# 손절 목록, 손절 이동
st, net, clock, b = uta_case()
pl = b.stop_plans("BTCUSDT.P")
ck("U11 손절 목록: unfilled-strategy-orders type=tpsl, 이 종목, stopLoss 있는 줄만, 끝난 상태 제외",
   pl == [{"orderId": "S1", "price": 85000.1, "triggerType": "mark", "planType": "tpsl"}]
   and net.calls[-1][1] == "/api/v3/trade/unfilled-strategy-orders?category=USDT-FUTURES&type=tpsl", (pl, net.calls[-1][1]))
st, net, clock, b = uta_case()
st["strat"][1]["stopLoss"] = "84000"                    # S2 = 익절과 손절이 함께 있는 줄
r = b.set_stop("BTCUSDT.P", 86000.04)
_mods = [c[2] for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"]
ck("U11b 손절 이동: 손절 줄 둘 다 같은 값(qty 그대로, slTriggerBy 유지), 같은 줄의 익절은 그대로 다시 보냄, 새로 걸기 없음",
   r == {"error": None, "moved": 2, "partial_errors": []}
   and _mods == [{"orderId": "S1", "qty": "0.01", "stopLoss": "86000.0", "slOrderType": "market", "slTriggerBy": "mark"},
                 {"orderId": "S2", "qty": "0.01", "stopLoss": "86000.0", "slOrderType": "market", "takeProfit": "99000",
                  "tpTriggerBy": "market", "tpOrderType": "market"}]
   and "/api/v3/trade/place-strategy-order" not in [c[1] for c in net.calls], (r, _mods))
st, net, clock, b = uta_case(modify_fail=["S1"])
r = b.set_stop("BTCUSDT", 86000.04)
_pl = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("U11c 이동이 전부 실패 → 포지션 수량만큼 부분 손절 새로(tpsl, partial, qty, side, posSide, mark, market), 취소 먼저 하지 않음",
   r == {"error": None} and _pl == [{"category": "USDT-FUTURES", "symbol": "BTCUSDT", "type": "tpsl", "tpslMode": "partial",
                                     "qty": "0.01", "side": "sell", "reduceOnly": "yes", "stopLoss": "86000.0",
                                     "slTriggerBy": "mark", "slOrderType": "market"}]
   and not [c for c in net.calls if "cancel" in c[1]], (r, _pl))
st, net, clock, b = uta_case(modify_fail=["S1"], strat_place_fail=Resp(400, {"code": "25595",
                                                                           "msg": "The total TP/SL quantity for partial positions exceeds the position amount"}))
r = b.set_stop("BTCUSDT", 86000.04)
ck("U11d 이동 실패 + 새로 걸기 거절(25595 부분 손절 합 > 포지션) → 오류(modify / place 둘 다 원문) - 기존 손절은 그대로",
   "modify:" in r["error"] and "25596" in r["error"] and "place:" in r["error"] and "25595" in r["error"], r)
st, net, clock, b = uta_case(strat=[])
r = b.set_stop("BTCUSDT", 86000.04)
ck("U11e 손절 줄 없음 → 새로 걸기", r == {"error": None}
   and [c[1] for c in net.calls if c[0] == "POST"] == ["/api/v3/trade/place-strategy-order"], paths(net))
st, net, clock, b = uta_case(strat=[], pos=[])
ck("U11f 포지션도 없으면 'no open position'", b.set_stop("BTCUSDT", 86000) == {"error": "no open position"})
st, net, clock, b = uta_case()
st["strat"][0]["qty"] = ""
r = b.set_stop("BTCUSDT", 86000.04)
ck("U11g qty 없는 줄은 modify를 보내지 않고(문서상 필수) 새로 걸기로",
   not [c for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"]
   and [c[1] for c in net.calls if c[0] == "POST"] == ["/api/v3/trade/place-strategy-order"], paths(net))
st, net, clock, b = uta_case()
net.routes[("GET", "/api/v3/trade/unfilled-strategy-orders")] = lambda q, bb: Resp(400, {"code": "25620", "msg": "permission denied"})
r = b.set_stop("BTCUSDT", 86000)
_ex = None
try:
    b.stop_plans("BTCUSDT")
except Exception as e:
    _ex = e
ck("U11h 손절 조회 실패: set_stop은 'plan query' 오류(주문 없음), stop_plans는 예외(호출측 무판단)",
   r["error"].startswith("plan query") and not [c for c in net.calls if c[0] == "POST"] and isinstance(_ex, RuntimeError), (r, _ex))

# 체결 이력과 수수료
st, net, clock, b = uta_case()
f = b.closed_fills(1792000000000)
ck("U12 체결 이력 모양(v2와 같음): netProfit = 순손익, 수수료 = open+close(-|x|) + 펀딩, 진입/청산 평균가, 방향, 시각",
   f == [{"tid": "P1", "ts_ms": 1792929656321, "symbol": "EOSUSDT", "pnl": -45.588, "fees": -45.472, "direction": "LONG",
          "entry_px": 1960.001, "exit_px": 1959.999}], f)
ck("U12b 문서 예시 산수: cumRealisedPnl + 수수료 + 펀딩 = netProfit(-45.588)",
   abs(BG._bg3_net({"cumRealisedPnl": "-0.116", "openFeeTotal": "-22.7360116", "closeFeeTotal": "-22.7359884",
                    "totalFunding": "0"}) - (-45.588)) < 1e-9)
ck("U12c netProfit 없으면 cumRealisedPnl + 비용, 수수료 부호가 양수로 와도 비용, 펀딩은 부호 그대로(받은 펀딩 +)",
   abs(BG._bg3_net({"cumRealisedPnl": "10", "openFeeTotal": "0.5", "closeFeeTotal": "-0.5", "totalFunding": "0.2"}) - 9.2) < 1e-9
   and abs(BG._bg3_costs({"openFeeTotal": "-1", "totalFunding": "-0.3"}) - (-1.3)) < 1e-9
   and BG._bg3_costs({}) == 0.0)
_wins = []


def _hist(q):
    _wins.append((int(q["startTime"]), int(q["endTime"]), q.get("cursor")))
    if int(q["startTime"]) == _wins[0][0] and not q.get("cursor"):
        return {"list": [{"positionId": f"Q{i}", "posSide": "short", "symbol": "BTCUSDT", "updatedTime": "1", "netProfit": "1"}
                         for i in range(100)], "cursor": "CUR1"}
    if q.get("cursor") == "CUR1":
        return {"list": [{"positionId": "Q99", "posSide": "short", "symbol": "BTCUSDT", "netProfit": "1"},
                         {"positionId": "QX", "posSide": "short", "symbol": "BTCUSDT", "netProfit": "1"}], "cursor": "CUR2"}
    return {"list": [], "cursor": ""}


st, net, clock, b = uta_case(hist=_hist)
_now = int(clock.time() * 1000)
f = b.closed_fills(_now - 200 * 86400000)
_spans = [(e - s) for s, e, c in _wins]
ck("U12d 90일 창으로 자르고(문서 90일) 30일 이하 창으로 나눠 오래된 것부터, cursor로 다음 쪽, 같은 positionId는 한 번",
   _wins[0][0] >= _now - 90 * 86400000 and max(_spans) <= 30 * 86400000 and _wins[-1][1] == _now
   and ("CUR1" in [c for s, e, c in _wins]) and len(f) == 101 and len({x["tid"] for x in f}) == 101
   and all(x["direction"] == "SHORT" for x in f), (_wins[:5], len(f)))
st, net, clock, b = uta_case()
net.routes[("GET", "/api/v3/position/history-position")] = lambda q, bb: Resp(400, {"code": "25620", "msg": "permission denied"})
try:
    b.closed_fills(0)
    _ex = None
except Exception as e:
    _ex = e
ck("U12e 이력 조회 실패는 예외(v2와 같음 - 호출측이 처리)", isinstance(_ex, RuntimeError), _ex)

# 연결 정보, 연결 테스트
st, net, clock, b = uta_case()
info = b.entry_info()
ck("U13 연결 정보: 계정 유형 UTA, BTCUSDT 교차 5x, 단방향, 가용 USDT, 교차 자동",
   info == ["계정 유형 = 통합 거래 계정(Unified Trading Account, API v3)", "BTCUSDT 레버리지(교차) 5x", "포지션 모드 = 단방향",
            "가용 잔고(마진) ≈ 1,500 USDT", "주문 마진모드 = 교차(cross) 자동 지정"], info)
st, net, clock, b = uta_case(warm=False)
ck("U13b healthcheck: 판별 + v3 포지션 조회", b.healthcheck() is True and not v2_calls(net), paths(net))

# v3 서명: 문서의 prehash(timestamp + METHOD + requestPath + "?" + queryString + body), base64(HMAC-SHA256)
st, net, clock, b = uta_case()
b.current_market_price("BTCUSDT")
b.cancel_order("BTCUSDT", 5)
_ok_sig = True
for m, pq, body, h in net.calls:
    _payload = json.dumps(body, separators=(",", ":")) if body else ""
    _exp = base64.b64encode(hmac.new(b"bg_secret_test", (h["ACCESS-TIMESTAMP"] + m + pq + _payload).encode(),
                                     hashlib.sha256).digest()).decode()
    _ok_sig = _ok_sig and h["ACCESS-SIGN"] == _exp and h["ACCESS-KEY"] == "bg_key_test" and h["ACCESS-PASSPHRASE"] == "bg_pass_test" \
        and h["Content-Type"] == "application/json"
ck("U14 v3 서명 = 문서 방식(v2와 같은 헤더 넷 + Content-Type)", _ok_sig and len(net.calls) == 2, net.calls)

# UTA 계정에서는 v2 끝점을 한 번도 부르지 않는다(모든 메서드)
st, net, clock, b = uta_case(warm=False)
for name, fn, setup in SCEN:
    st2 = uta_state()
    st.clear()
    st.update(st2)
    try:
        fn(b)
    except Exception as e:
        print("   (U15 예외)", name, e)
ck(f"U15 UTA 계정: 시나리오 {len(SCEN)}개(진입, 청산, 손절, 이력 …) 전부 v2 호출 0번, 처음 한 번 판별 뒤 UTA 그대로",
   not v2_calls(net) and net.calls[0][1] == PROBE and b.account_type() == "uta" and len(net.calls) >= 40,
   (v2_calls(net)[:5], len(net.calls)))

# ══ R: 2026-11-11 리뷰 수리 ═══════════════════════════════════════════════════════════════
# R1(P1-1) 이중 주문: 클래식 본문에서 v2 쓰기가 하나라도 들어갔거나 결과가 성공이면 v3로 다시 보내지 않는다
def _r1_world(st):
    r = classic_routes(st)
    cnt = {"n": 0}

    def probe(q, b):
        cnt["n"] += 1
        if cnt["n"] == 1:
            return ConnectionError("blip")                         # 첫 판별은 네트워크로 모름
        return {"accountMode": "hybrid", "holdMode": "hedge_mode"}  # 다시 물으면 UTA라고 답한다(두 API 다 받는 최악)
    r[("GET", PROBE)] = probe
    r[("GET", "/api/v3/market/instruments")] = lambda q, b: [{"symbol": "BTCUSDT", "pricePrecision": "1", "priceMultiplier": "0.1"}]
    r[("POST", "/api/v3/trade/place-order")] = lambda q, b: {"orderId": "v3-1"}
    r[("POST", "/api/v3/trade/place-strategy-order")] = lambda q, b: {"orderId": "v3-s"}
    return r


st = classic_state()
st["place_fail"].append(_side_err())
net, clock = Net(_r1_world(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b.current_market_price("BTCUSDT")                                  # 첫 판별(모름)
clock.t += 90
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-9", dry_run=False)
_orders = [c[1] for c in net.calls if c[0] == "POST" and "place-order" in c[1]]
ck("R1a(P1-1) v2 진입 40774 → tradeSide 재시도 성공이면 다시 물어 UTA라 해도 v3 주문 없음(주문 = v2 두 번, 둘째만 들어감)",
   _orders == ["/api/v2/mix/order/place-order", "/api/v2/mix/order/place-order"] and r.get("entry", {}).get("orderId") == "1001",
   (r, _orders))
st = classic_state()
st["modify_fail"] = True
net, clock = Net(_r1_world(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b.current_market_price("BTCUSDT")
clock.t += 90
r = b.set_stop("BTCUSDT", 86000)
_w = [c[1] for c in net.calls if c[0] == "POST"]
ck("R1b(P1-1) v2 손절 이동: modify 거절 → place-tpsl 성공이면 v3 손절을 또 걸지 않는다",
   r == {"error": None} and _w == ["/api/v2/mix/order/modify-tpsl-order", "/api/v2/mix/order/place-tpsl-order"], (r, _w))
st = classic_state()
st["place_fail"].append(Resp(400, {"code": "40099", "msg": "classic api not available"}))
net, clock = Net(_r1_world(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b.current_market_price("BTCUSDT")
clock.t += 90
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-9", dry_run=False)
ck("R1c v2 진입이 확실히 거절돼도 진입 길에서는 다시 묻지 않는다(최종 리뷰 E1 P2-10) - v2 오류 그대로, v3 진입 없음",
   r.get("error") and [c[1] for c in net.calls if c[0] == "POST"] == ["/api/v2/mix/order/place-order"]
   and [c[1] for c in net.calls].count(PROBE) == 1, (r, paths(net)))
# 반대쪽: v3 쓰기가 들어간 뒤 25245면 클래식으로 처음부터 다시 돌리지 않는다
st, net, clock, b = uta_case()
net.routes[("GET", "/api/v3/trade/order-info")] = lambda q, bb: NOT_UNIFIED
net.routes[("GET", "/api/v3/position/current-position")] = lambda q, bb: NOT_UNIFIED
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = lambda q, bb: NOT_UNIFIED
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-8", dry_run=False)
ck("R1d v3 진입이 들어간 뒤 25245 → v2로 다시 진입하지 않는다(유형만 클래식으로, 이 호출은 '결과 모름' - 앱이 '거절'이라 말하지 않게)",
   not v2_calls(net) and str(r.get("error", "")).startswith("unknown:") and b.account_type() == "classic"
   and [c[1] for c in net.calls if c[0] == "POST"] == ["/api/v3/trade/place-order"], (r, paths(net)))

# R2(P1-2) 헤지 모드 + 'UTA 관리 읽기' 권한 없는 키
st, net, clock, b = uta_case(warm=False, hold="hedge_mode", settings_fail=Resp(400, {"code": "25620", "msg": "permission denied"}),
                             info={"userId": "1", "permType": "read-and-write", "permissions": ["uta_trade"]})
_pf = {"n": 0}


def _hedge_place(q, bb):
    if not bb.get("posSide") and "reduceOnly" not in bb:
        return Resp(400, {"code": "25236", "msg": "Incorrect position open type"})
    oid = str(3001 + _pf["n"]); _pf["n"] += 1
    st["orders"][oid] = dict(bb)
    return {"orderId": oid, "clientOid": bb.get("clientOid")}


net.routes[("POST", "/api/v3/trade/place-order")] = _hedge_place
b.healthcheck()
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-1", dry_run=False)
_po = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-order"]
ck("R2a(P1-2) 설정을 못 읽는 키 + 헤지 모드: 25236 거절 → posSide=long, 새 clientOid(EQ-AP-1-h)로 한 번 → 진입, 손절도 걸림",
   r.get("stop") is True and len(_po) == 2 and "posSide" not in _po[0] and _po[1]["posSide"] == "long"
   and _po[1]["clientOid"] == "EQ-AP-1-h" and b._hold() == "hedge_mode", (r, _po))
net.calls.clear()
r = b.place_entry(symbol="BTCUSDT", side="SHORT", size=0.01, stop_loss_price=95000, custom_tag="EQ-AP-2", dry_run=False)
_po = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-order"]
ck("R2b 한 번 알아낸 헤지 모드는 다음 진입부터 처음부터 posSide(거절 없이)", len(_po) == 1 and _po[0]["posSide"] == "short"
   and r.get("stop") is True, _po)
st, net, clock, b = uta_case(warm=False, settings_fail=Resp(400, {"code": "25620", "msg": "permission denied"}),
                             info={"userId": "1", "permType": "read-and-write", "permissions": ["uta_trade", "withdraw"]})
st["place_fail"].append(Resp(400, {"code": "25203", "msg": "Insufficient margin"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, dry_run=False)
ck("R2c 포지션 모드와 상관없는 거절(25203)은 posSide로 다시 보내지 않는다",
   len([c for c in net.calls if c[1] == "/api/v3/trade/place-order"]) == 1 and "25203" in r.get("error", ""), r)
ki = b.key_info()
ck("R2d 연결 테스트 경고(key_info): 'UTA 관리' 권한 없음 = KO / EN 한 줄(레버리지, 포지션 모드 못 읽음)",
   len(ki) == 1 and "UTA 관리" in ki[0] and "UTA management" in ki[0] and " / " in ki[0]
   and net.calls[-1][1] == "/api/v3/account/info", (ki, paths(net)[-2:]))
st, net, clock, b = uta_case(info={"userId": "1", "permType": "read-only", "permissions": ["uta_mgt", "uta_trade"]})
ki = b.key_info()
ck("R2e 읽기 전용 키 = 주문 불가 경고(KO / EN)", len(ki) == 1 and "UTA 거래" in ki[0] and "no orders" in ki[0], ki)
st, net, clock, b = uta_case()
ck("R2f 권한이 다 있으면 경고 없음", b.key_info() == [])
st = classic_state()
net, clock = Net(classic_routes(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b._mstate().update(mode="classic", probe_at=clock.time())
ck("R2g 클래식 키는 key_info가 묻지 않고 [](종전 요청 그대로)", b.key_info() == [] and not net.calls, paths(net))

# R3(P1-3) 손절: 목록에 나옴 / 안 나옴, 이동, 거는 데 실패 → 닫기
st, net, clock, b = uta_case(strat=[])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000.04, custom_tag="EQ-AP-3", dry_run=False)
pl = b.stop_plans("BTCUSDT")
ck("R3a 진입 손절은 앱이 건 전략 주문이라 목록에 qty와 함께 나온다(손절 감시 '있음')",
   r.get("stop_order_id") == "S9" and pl == [{"orderId": "S9", "price": 85000.0, "triggerType": "market", "planType": "tpsl"}], pl)
net.calls.clear()
r2 = b.set_stop("BTCUSDT", 86000.04)
_m = [c[2] for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"]
ck("R3b X2+TR 이동: 그 전략 주문을 qty 그대로(0.01) modify, 새로 걸기 없음",
   r2 == {"error": None, "moved": 1, "partial_errors": []}
   and _m == [{"orderId": "S9", "qty": "0.01", "stopLoss": "86000.0", "slOrderType": "market", "slTriggerBy": "market"}]
   and "/api/v3/trade/place-strategy-order" not in [c[1] for c in net.calls], (r2, _m))
st, net, clock, b = uta_case(strat=[])
r2 = b.set_stop("BTCUSDT", 86000.04)
_pl = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R3c 목록에 손절이 없으면(안 나옴, 사라짐) 포지션 수량만큼 부분 손절을 새로(qty 0.01, 단방향 닫기 side + reduceOnly, mark)",
   r2 == {"error": None} and _pl == [{"category": "USDT-FUTURES", "symbol": "BTCUSDT", "type": "tpsl", "tpslMode": "partial",
                                      "qty": "0.01", "side": "sell", "reduceOnly": "yes", "stopLoss": "86000.0",
                                      "slTriggerBy": "mark", "slOrderType": "market"}], (r2, _pl))
_fail = Resp(400, {"code": "25601", "msg": "Position does not exist; cannot set TP/SL"})
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_fail, _fail])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-4", dry_run=False)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
_cl = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-order"][1:]
ck("R3d 손절이 두 번(0.5초 간격) 다 거절 → 그 수량만 시장가 reduce-only로 바로 닫고 'entry closed:' 오류(남는 포지션 없음)",
   len(_sp) == 2 and _sp[0]["clientOid"] == "EQ-AP-4-sl" and _sp[1]["clientOid"] == "EQ-AP-4-sl2" and 0.5 in clock.sleeps
   and _cl == [{"category": "USDT-FUTURES", "symbol": "BTCUSDT", "side": "sell", "orderType": "market", "qty": "0.01",
                "reduceOnly": "yes", "clientOid": "EQ-AP-4-x"}]
   and str(r.get("error", "")).startswith("entry closed:") and r.get("closed") is True and "25601" in r["error"], (r, _sp, _cl))
st, net, clock, b = uta_case(strat=[], hold="hedge_mode", strat_place_fail=[_fail, _fail])
r = b.place_entry(symbol="BTCUSDT", side="SHORT", size=0.01, stop_loss_price=95000, custom_tag="EQ-AP-4", dry_run=False)
_cl = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-order"][1:]
ck("R3e 헤지 모드 비상 청산 = buy + posSide short(reduceOnly 없음)", _cl and _cl[0]["side"] == "buy"
   and _cl[0]["posSide"] == "short" and "reduceOnly" not in _cl[0] and str(r.get("error")).startswith("entry closed:"), _cl)
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_fail, _fail])
_n = {"n": 0}


def _place_then_fail(q, bb):
    _n["n"] += 1
    if _n["n"] == 1:
        st["orders"]["2001"] = dict(bb)
        return {"orderId": "2001"}
    return Resp(400, {"code": "25227", "msg": "No position available to close"})


net.routes[("POST", "/api/v3/trade/place-order")] = _place_then_fail
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-6", dry_run=False)
ck("R3f 손절도 닫기도 실패 → 진입은 됐다고 하고 stop False + stop_error(앱이 경고하고 손절 감시가 다시 건다)",
   r.get("entry") and r.get("stop") is False and "open without a stop" in r.get("stop_error", "") and not r.get("error"), r)
_amb = Resp(500, {"code": "45001", "msg": "Unknown error"})
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_amb, Resp(400, {"code": "25212", "msg": "Duplicate clientOid"})])
st["strat"].append({"orderId": "S7", "clientOid": "EQ-AP-7-sl", "symbol": "BTCUSDT", "qty": "0.01", "stopLoss": "85000",
                    "status": "pending"})
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-7", dry_run=False)
_sp = [c[2]["clientOid"] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R3g 손절 결과 모름(45001) → 같은 clientOid로 다시(문서: tpsl clientOid 6시간 멱등), 25212면 앞 것이 들어간 것 = 목록에서 찾아 성공",
   _sp == ["EQ-AP-7-sl", "EQ-AP-7-sl"] and r.get("stop") is True and r.get("stop_order_id") == "S7", (r, _sp))
st, net, clock, b = uta_case(fill=lambda q: {"orderId": q.get("orderId"), "orderStatus": "cancelled", "cumExecQty": "0"})
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
ck("R3h 시장가가 체결 없이 끝남(cancelled, 0) → 'not filled:', 손절 걸지 않음",
   str(r.get("error", "")).startswith("not filled:") and "/api/v3/trade/place-strategy-order" not in [c[1] for c in net.calls], r)
st, net, clock, b = uta_case(fill=lambda q: {"orderId": q.get("orderId"), "orderStatus": "filled", "cumExecQty": "0.004"})
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R3i 손절 수량 = 실제 체결 수량(cumExecQty 0.004, 주문 0.01이 아님)", _sp and _sp[0]["qty"] == "0.004"
   and r.get("filled_qty") == 0.004, (r, _sp))
st, net, clock, b = uta_case(fill=lambda q: Resp(500, {"code": "40725", "msg": "service return an error"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R3j 체결 조회가 끝내 안 되면(약 2초) 그 종목 그 방향 포지션 수량으로 손절", _sp and _sp[0]["qty"] == "0.01"
   and r.get("stop") is True and sum(x for x in clock.sleeps if x == 0.25) <= 2.0 + 1e-9, (r, clock.sleeps))

# 결과 모름 진입(문서: 40010, 40725, 45001이면 clientOid로 확인) - 손절이 주문에 붙어 있지 않으므로 확인해서 손절까지 건다
for _amb, _tag in ((Resp(500, {"code": "45001", "msg": "Unknown error"}), "45001"), (ConnectionError("read timeout"), "net")):
    st, net, clock, b = uta_case(strat=[], fill=lambda q: ({"orderId": "7001", "clientOid": q.get("clientOid"), "orderStatus": "filled",
                                                            "cumExecQty": "0.01"}))
    st["place_fail"].append(_amb)
    r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-11", dry_run=False)
    _oi = [c[1] for c in net.calls if c[1].startswith("/api/v3/trade/order-info")]
    ck(f"R3k 진입 결과 모름({_tag}) → order-info?clientOid로 체결 확인 → 손절 전략 주문까지(주문은 한 번만)",
       r.get("stop") is True and r.get("entry", {}).get("orderId") == "7001"
       and _oi[0] == "/api/v3/trade/order-info?clientOid=EQ-AP-11"
       and len([c for c in net.calls if c[1] == "/api/v3/trade/place-order"]) == 1, (r, paths(net)))
st, net, clock, b = uta_case(strat=[], fill=lambda q: {"orderId": "7002", "orderStatus": "cancelled", "cumExecQty": "0"})
st["place_fail"].append(Resp(500, {"code": "45001", "msg": "Unknown error"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-12", dry_run=False)
ck("R3l 결과 모름 → clientOid로 보니 체결 없이 끝남 → 'not filled:'(거절로 분류, 손절 없음)",
   str(r.get("error", "")).startswith("not filled:") and "/api/v3/trade/place-strategy-order" not in [c[1] for c in net.calls], r)
st, net, clock, b = uta_case(strat=[], pos=[], fill=lambda q: Resp(400, {"code": "25204", "msg": "Order does not exist"}))
st["place_fail"].append(Resp(500, {"code": "45001", "msg": "Unknown error"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-13", dry_run=False)
ck("R3m 주문 조회도 포지션도 없어 끝내 모름 → '모름' 오류 + stop_attached False(앱이 '손절이 없을 수 있다'고 알림)",
   r.get("stop_attached") is False and "unknown: code=45001" in r.get("error", "")
   and len([c for c in net.calls if c[1].startswith("/api/v3/trade/order-info")]) == 4
   and "/api/v3/trade/place-strategy-order" not in [c[1] for c in net.calls], (r, paths(net)))
st, net, clock, b = uta_case(strat=[], fill=lambda q: Resp(400, {"code": "25204", "msg": "Order does not exist"}))
st["place_fail"].append(Resp(500, {"code": "45001", "msg": "Unknown error"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-14", dry_run=False)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R3n 주문 조회는 모름인데 그 방향 포지션이 있으면 그 수량(0.01)에 손절을 건다(손절 없는 포지션을 남기지 않음)",
   r.get("stop") is True and _sp and _sp[0]["qty"] == "0.01" and _sp[0]["clientOid"] == "EQ-AP-14-sl", (r, _sp))

# ══ R7: 2026-11-11 재리뷰(진입 → 손절 → 비상 청산) ══════════════════════════════════════════
_TO = ConnectionError("read timed out")


def _sl_route(st, plan):
    """plan = 시도마다 ('placed'|'none', 응답) - placed면 목록에 그 줄을 남긴다(결과 모름인데 실제로 들어간 경우)."""
    seq = list(plan)

    def f(q, bb):
        kind, resp = seq.pop(0) if seq else ("placed", None)
        if kind == "placed":
            st["strat"].append(dict(bb, orderId=f"S{len(st['strat']) + 50}", status="pending",
                                    createdTime=str(1793000000000 + len(st["strat"]))))
        return resp if resp is not None else {"orderId": f"S{len(st['strat']) + 49}"}
    return f


st, net, clock, b = uta_case(strat=[])
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = _sl_route(st, [("placed", _TO), ("placed", _TO)])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-20", dry_run=False)
_x = [c for c in net.calls if c[1] == "/api/v3/trade/place-order" and str((c[2] or {}).get("clientOid", "")).endswith(("-x", "-x2"))]
_cx = [c[2] for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order"]
ck("R7a(P2-2, E5b) 손절 두 번 다 결과 모름인데 둘 다 들어감 → 닫기 전에 목록에서 우리 손절(표식-sl) 확인 = 성공, 닫지 않음, 둘이면 늦은 하나 취소",
   r.get("stop") is True and not _x and len(_cx) == 1
   and len([x for x in st["strat"] if str(x.get("clientOid", "")).startswith("EQ-AP-20-sl")]) == 1, (r, _cx, paths(net)))
st, net, clock, b = uta_case(strat=[])
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = _sl_route(
    st, [("placed", _TO), ("none", Resp(400, {"code": "25595", "msg": "The total TP/SL quantity for partial positions exceeds the position amount"}))])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-21", dry_run=False)
_x = [c for c in net.calls if str((c[2] or {}).get("clientOid", "")).endswith(("-x", "-x2"))]
ck("R7b(P2-2, E5c) 첫 손절 결과 모름(실제 들어감) + 둘째 25595 → 목록에서 찾아 성공, 비상 청산 없음",
   r.get("stop") is True and not _x and r.get("stop_order_id"), (r, paths(net)))
st, net, clock, b = uta_case(strat=[])
_p25595 = Resp(400, {"code": "25595", "msg": "exceeds the position amount"})
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = lambda q, bb: _p25595
net.routes[("GET", "/api/v3/trade/unfilled-strategy-orders")] = lambda q, bb: Resp(500, {"code": "45001", "msg": "Unknown error"})
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-22", dry_run=False)
_x = [c for c in net.calls if str((c[2] or {}).get("clientOid", "")).endswith(("-x", "-x2"))]
ck("R7c 25595(이미 덮는 손절이 있다는 뜻)인데 목록을 못 읽음 → 닫지 않고 stop False + '이미 있을 수 있다'(손절 감시가 확인)",
   not _x and r.get("entry") and r.get("stop") is False and "may already be in place" in r.get("stop_error", ""), (r, paths(net)))
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_fail, _fail])
_orig_place = net.routes[("POST", "/api/v3/trade/place-order")]


def _close_timeout(q, bb):
    out = _orig_place(q, bb)
    return _TO if str(bb.get("clientOid", "")).endswith("-x") else out


net.routes[("POST", "/api/v3/trade/place-order")] = _close_timeout
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-23", dry_run=False)
_x = [c[2]["clientOid"] for c in net.calls if str((c[2] or {}).get("clientOid", "")).endswith(("-x", "-x2"))]
ck("R7d(P2-3, E11) 비상 청산 결과 모름(실제 닫힘) → 포지션을 읽어 비었으니 '닫음', 둘째 청산은 보내지 않음",
   str(r.get("error", "")).startswith("entry closed:") and _x == ["EQ-AP-23-x"], (r, _x))
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_fail, _fail], close_fill=[0.5, 1.0])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-24", dry_run=False)
_x = [(c[2]["clientOid"], c[2]["qty"]) for c in net.calls if str((c[2] or {}).get("clientOid", "")).endswith(("-x", "-x2"))]
ck("R7e(P2-3a) 시장가 청산이 반만 체결(slippage) → 남은 0.005를 다시 닫고 비었을 때만 '닫음'",
   _x == [("EQ-AP-24-x", "0.01"), ("EQ-AP-24-x2", "0.005")] and str(r.get("error", "")).startswith("entry closed:"), (r, _x))
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_fail, _fail], close_fill=0.0)
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-25", dry_run=False)
ck("R7f(P2-3) 청산을 두 번 보내도 포지션이 남음 → '닫음'이라 하지 않고 stop False + 남은 수량(손절 감시가 건다)",
   not r.get("error") and r.get("stop") is False and "left 0.01 open" in r.get("stop_error", "") and r.get("filled_qty") == 0.01, r)
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_fail, _fail])
_pos_reads = {"n": 0}
_orig_pos = net.routes[("GET", "/api/v3/position/current-position")]


def _pos_then_fail(q, bb):
    _pos_reads["n"] += 1
    return Resp(500, {"code": "40725", "msg": "service return an error"})


net.routes[("GET", "/api/v3/trade/order-info")] = lambda q, bb: {"orderId": q.get("orderId"), "orderStatus": "filled", "cumExecQty": "0.01"}
net.routes[("GET", "/api/v3/position/current-position")] = _pos_then_fail
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-26", dry_run=False)
ck("R7g(P2-3) 청산 뒤 포지션을 못 읽음 → stop False + '확인하지 못함'(닫았다고 하지 않음)",
   r.get("stop") is False and "could not be confirmed" in r.get("stop_error", ""), r)
st, net, clock, b = uta_case(strat=[])
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = lambda q, bb: NOT_UNIFIED
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-27", dry_run=False)
ck("R7h(P2-5, E13) 진입이 들어간 뒤 25245 → 'unknown: …' + stop_attached False(앱이 '손절은 주문에 붙어 있다'고 말하지 않음)",
   str(r.get("error", "")).startswith("unknown:") and r.get("stop_attached") is False, r)
st, net, clock, b = uta_case(strat=[])
net.routes[("POST", "/api/v3/trade/place-order")] = lambda q, bb: _TO
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, dry_run=False)
ck("R7i 결과 모름(clientOid 없음)도 stop_attached False", r.get("stop_attached") is False and r.get("error"), r)

# ══ R8: 3차 리뷰(2026.11.11g 그대로) ═════════════════════════════════════════════════════════
# P2-4 수량 자리: v3 BTC 수량 스텝 0.0001(quantityPrecision 4) - 체결에서 나온 수량은 그 스텝으로 내림(포지션을 넘지 않음)
for _fq, _want in (("0.0065", "0.0065"), ("0.0063", "0.0063"), ("0.0025", "0.0025"), ("0.00657", "0.0065")):
    st, net, clock, b = uta_case(strat=[], fill=(lambda v: (lambda q: {"orderId": q.get("orderId"), "orderStatus": "filled",
                                                                      "cumExecQty": v}))(_fq))
    r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-30", dry_run=False)
    _sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
    ck(f"R8a(P2-4) 체결 {_fq} → 손절 qty {_want}(4자리 스텝 내림, v2 3자리 반올림이 아님)", _sp and _sp[0]["qty"] == _want, _sp[:1])
st, net, clock, b = uta_case(strat=[], strat_place_fail=[_fail, _fail], close_fill=[0.75, 0.0],
                             fill=lambda q: {"orderId": q.get("orderId"), "orderStatus": "filled", "cumExecQty": "0.01"})
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-31", dry_run=False)
_x = [c[2]["qty"] for c in net.calls if str((c[2] or {}).get("clientOid", "")).endswith(("-x", "-x2"))]
ck("R8b(P2-4) 청산 남은 수량 0.0025 = 둘째 청산 qty '0.0025', 남았다는 문장도 0.0025(0.003, 0.002가 아님)",
   _x == ["0.01", "0.0025"] and "left 0.0025 open" in r.get("stop_error", ""), (_x, r))
_cls = run_classic(BG, warm=True, names={"place_entry_live", "place_limit_close_live"})
ck("R8c(P2-4) 클래식 v2 수량 글자는 그대로(기준과 같음)",
   digest(_cls["place_entry_live"]) == GOLDEN["place_entry_live"]["sha"]
   and digest(_cls["place_limit_close_live"]) == GOLDEN["place_limit_close_live"]["sha"])
# P2-3 단방향 손절 꼴: 문서 꼴(side + reduceOnly)이 꼴 문제로 거절되면 posSide 꼴로 한 번, 먹힌 꼴은 키마다 기억
_formerr = Resp(400, {"code": "40017", "msg": "Parameter verification failed reduceOnly"})
st, net, clock, b = uta_case(strat=[])
_orig_sp = net.routes[("POST", "/api/v3/trade/place-strategy-order")]
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = lambda q, bb: _formerr if bb.get("reduceOnly") else _orig_sp(q, bb)
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-32", dry_run=False)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R8d(P2-3) 단방향: reduceOnly 꼴이 40017로 거절 → posSide 꼴(새 clientOid -sl2)로 → 손절 걸림, 비상 청산 없음",
   r.get("stop") is True and len(_sp) == 2 and _sp[0].get("reduceOnly") == "yes" and "posSide" not in _sp[0]
   and _sp[1].get("posSide") == "long" and "reduceOnly" not in _sp[1] and _sp[1]["clientOid"] == "EQ-AP-32-sl2"
   and not [c for c in net.calls if str((c[2] or {}).get("clientOid", "")).endswith("-x")], (r, _sp))
net.calls.clear()
st["strat"] = []
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-33", dry_run=False)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R8e(P2-3) 먹힌 꼴(posSide)을 이 키에 기억 → 다음 진입 손절은 처음부터 posSide 꼴 한 번", r.get("stop") is True and len(_sp) == 1
   and _sp[0].get("posSide") == "long" and "reduceOnly" not in _sp[0], _sp)
st["strat"] = []
net.calls.clear()
r2 = b.set_stop("BTCUSDT", 86000)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R8f(P2-3) set_stop 새로 걸기도 같은 꼴 차례(기억한 posSide 꼴)", r2 == {"error": None} and len(_sp) == 1
   and _sp[0].get("posSide") == "long", (r2, _sp))
st, net, clock, b = uta_case(strat=[])
_orig_sp = net.routes[("POST", "/api/v3/trade/place-strategy-order")]
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = lambda q, bb: _formerr if bb.get("reduceOnly") else _orig_sp(q, bb)
r2 = b.set_stop("BTCUSDT", 86000)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R8g(P2-3) set_stop 새로 걸기: reduceOnly 꼴 거절 → posSide 꼴로 한 번", r2 == {"error": None} and len(_sp) == 2
   and _sp[1].get("posSide") == "long", (r2, _sp))
st, net, clock, b = uta_case(strat=[], strat_place_fail=[Resp(400, {"code": "25601", "msg": "Position does not exist"})])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-34", dry_run=False)
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("R8h(P2-3) 25601(체결 직후 시차)은 꼴 문제가 아님 → 같은 꼴(reduceOnly)로 0.5초 뒤 다시", r.get("stop") is True and len(_sp) == 2
   and _sp[1].get("reduceOnly") == "yes" and "posSide" not in _sp[1], _sp)
# P2-2 중복 정리: 성공한 호출이 돌려준 줄을 남긴다, 살아 있는 줄을 모르면 취소하지 않는다
st, net, clock, b = uta_case(strat=[])
net.routes[("POST", "/api/v3/trade/place-strategy-order")] = _sl_route(st, [("placed", _TO), ("placed", None)])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="EQ-AP-35", dry_run=False)
_cx = [c[2]["orderId"] for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order"]
_live = [x["orderId"] for x in st["strat"]]
ck("R8i(P2-2, G5) 결과 모름 뒤 재시도 성공(둘 다 들어감) → 성공한 호출의 줄(stop_order_id)을 남기고 앞 줄을 취소",
   r.get("stop") is True and _live == [r.get("stop_order_id")] and len(_cx) == 1 and _cx[0] != r.get("stop_order_id"), (r, _cx, _live))
_two = [{"orderId": "A1", "clientOid": "EQ-AP-36-sl", "status": "submitting", "createdTime": "1"},
        {"orderId": "A2", "clientOid": "EQ-AP-36-sl", "status": "submitting", "createdTime": "2"}]
st, net, clock, b = uta_case(strat=[])
_keep, _nc = b._u_dedup_stops([dict(x) for x in _two])
ck("R8j(P2-2) 둘 다 submitting이고 성공한 orderId도 없으면(어느 것이 살아 있는지 모름) 아무것도 취소하지 않음",
   _keep is None and _nc == 0 and not net.calls, (_keep, _nc))
_mix = [{"orderId": "B1", "status": "submitting", "createdTime": "1"}, {"orderId": "B2", "status": "pending", "createdTime": "2"}]
_keep, _nc = b._u_dedup_stops([dict(x) for x in _mix])
ck("R8k(P2-2) 성공 orderId가 없으면 submitting보다 pending 줄을 남긴다", _keep and _keep["orderId"] == "B2" and _nc == 1
   and net.calls[-1][2] == {"orderId": "B1"}, (_keep, net.calls[-1:]))
st, net, clock, b = uta_case()
b.set_stop("BTCUSDT", 86000.04)
ck("R8l(P2-1) set_stop 성공 = 이 키로 앱이 건 마지막 손절 값을 기억(last_app_stop, 요청 없음)", b.last_app_stop("BTCUSDT.P") == 86000.04)

# R4(P2-4) 판별이 네트워크로 한 번 끊긴 UTA 회원
st, net, clock, b = uta_case(warm=False)
_p4 = {"n": 0}


def _probe4(q, bb):
    _p4["n"] += 1
    return ConnectionError("blip") if _p4["n"] == 1 else {"accountMode": "unified", "holdMode": "one_way_mode"}


net.routes[("GET", PROBE)] = _probe4
b.healthcheck()
ck("R4a 연결 테스트: 판별이 끊겨도 v2가 거절되면 바로 다시 묻고(60초 기다리지 않음) UTA로 통과",
   b.account_type() == "uta" and [c[1] for c in net.calls].count(PROBE) == 2, paths(net))
st, net, clock, b = uta_case(warm=False)
net.routes[("GET", PROBE)] = lambda q, bb: ConnectionError("blip")
try:
    b.healthcheck()
    _ex = None
except Exception as e:
    _ex = e
ck("R4b 판별이 계속 안 되고 v2도 실패 → 'could not determine the Bitget account type, try again'(KO / EN)",
   isinstance(_ex, RuntimeError) and "확인하지 못했습니다" in str(_ex) and "Could not determine" in str(_ex), _ex)
st = classic_state()
_routes = classic_routes(st)
_routes[("GET", PROBE)] = lambda q, bb: Resp(400, {"code": "40018", "msg": "Invalid IP"})
_routes[("GET", "/api/v3/position/current-position")] = lambda q, bb: Resp(400, {"code": "40018", "msg": "Invalid IP"})
_routes[("GET", "/api/v2/mix/position/all-position")] = lambda q, bb: Resp(400, {"code": "40018", "msg": "Invalid IP"})
net, clock = Net(_routes), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
try:
    b.healthcheck()
    _ex = None
except Exception as e:
    _ex = e
ck("R4c Bitget이 답한 오류(IP 차단 40018)면 감싸지 않고 종전 v2 오류 그대로", isinstance(_ex, RuntimeError)
   and str(_ex).startswith("Bitget /api/v2/mix/position/all-position HTTP 400: code=40018"), _ex)

# R5(P2-5) settings 성공 + 처음 보는 accountMode = UTA, 칸이 없으면 모름
st, net, clock, b = uta_case(warm=False, mode="advanced")
b.list_open_positions()
ck("R5a accountMode 'advanced'(문서 목록 밖) + 00000 = UTA", b.account_type() == "uta" and not v2_calls(net), paths(net))
st = classic_state()
_routes = classic_routes(st)
_routes[("GET", PROBE)] = lambda q, bb: {"uid": "1"}
net, clock = Net(_routes), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b.list_open_positions()
ck("R5b accountMode 칸이 없는 성공 응답 = 모름(종전 v2)", b.account_type() == "unknown"
   and paths(net)[-1] == "GET /api/v2/mix/position/all-position", paths(net))

# R6(P2-6) 결과 모름 코드: v3는 'unknown: code=', v2는 종전 글자 그대로
st, net, clock, b = uta_case()
st["place_fail"].append(Resp(400, {"code": "40725", "msg": "service return an error"}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, dry_run=False)
ck("R6a v3 40725 → 'Bitget /api/v3/trade/place-order unknown: code=40725 (status 400) …'",
   r.get("error", "").startswith("Bitget /api/v3/trade/place-order unknown: code=40725 (status 400)"), r)
_r6 = run_classic(BG, warm=True, names={"place_entry_reject"})
st = classic_state()
st["place_fail"].append(Resp(200, {"code": "40010", "msg": "Request timed out"}))
net, clock = Net(classic_routes(st)), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b._mstate().update(mode="classic", probe_at=clock.time())
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, dry_run=False)
ck("R6b v2 40010은 종전 메시지 그대로('failed: code=40010', 클래식 글자까지 같음 - 바꾸지 않음)",
   r == {"error": "Bitget /api/v2/mix/order/place-order failed: code=40010 Request timed out"}, r)

# ── G: 앱 쪽 분류(진입 오류 '거절' vs '모름') ────────────────────────────────────────────────
os.environ.setdefault("EQ_NO_ALERT", "1")
import eqgui  # noqa: E402

_cases = [
    ("not sent: Bitget account is changing between Classic and Unified Trading Account (accountMode=upgrading)", False),
    ("Bitget /api/v3/trade/place-order HTTP 400: code=25203 Insufficient margin", False),
    ("Bitget /api/v3/trade/place-order failed: code=25212 Duplicate clientOid", False),
    ("Bitget /api/v3/trade/place-order HTTP 502: code=None <html>", True),
    ("HTTPSConnectionPool(host='api.bitget.com', port=443): Read timed out.", True),
    ("invalid side: 'X' (expected LONG/SHORT)", False),
]
_cases += [("entry closed: stop could not be placed (x); the position was closed at market", False),
           ("not filled: market order 1 ended without a fill - no position, no stop needed", False),
           ("Bitget /api/v3/trade/place-order unknown: code=40725 (status 400) service return an error", True),
           ("Bitget /api/v3/trade/place-order unknown: code=40010 (status 200) Request timed out", True),
           ("unknown: Bitget answered code=25245 (account is not unified) after a write in this call was already accepted", True)]
_bad = [(m, w) for m, w in _cases if eqgui._entry_err_unknown(m) != w]
ck("G1 'not sent:', 'entry closed:', 'not filled:'는 거절(남은 주문, 포지션 없음), v3 거래소 거절도 거절, 5xx·타임아웃·'unknown: code='는 모름",
   not _bad, _bad)
_hk, _he = eqgui._entry_fail_hint_dm("entry closed: stop could not be placed (x); the position was closed at market")
ck("G1b 'entry closed:' 회원 DM 안내 = 손절을 못 걸어 앱이 바로 닫음, 열린 포지션 없음(KO / EN)",
   "바로 시장가로 닫았습니다" in _hk and "No position is open now" in _he, (_hk, _he))
import inspect as _insp  # noqa: E402
_src = _insp.getsource(eqgui.App._sig_entry) if hasattr(eqgui.App, "_sig_entry") else open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
ck("G1c 크립토 진입 성공인데 손절 미확인(stop False + stop_error) = 로그 🚨와 회원 알림(entry_nostop)",
   'self._dm_kind("entry_nostop", asset' in _src and 'res.get("stop") is False and res.get("stop_error")' in _src)
ck("G1d 진입 결과 모름 + stop_attached False = '손절이 없을 수 있다'(KO / EN), 종전 '손절은 주문에 붙어 있다' 문장은 그 밖에서만",
   '_nostop = res.get("stop_attached") is False' in _src and "손절이 없을 수 있으니" in _src and "it may have no stop" in _src)
ck("G2 앱 버전 2026.11.11h 이상", eqgui.App._APP_VER >= "2026.11.11h", eqgui.App._APP_VER)

# ── G3: 재리뷰 P1 - 결과 모름 + 아무것도 안 보인 진입을 미확인 진입 감시에 넣고, 늦게 보인 포지션에 손절(앱 하트비트 경로 그대로) ──
import tempfile  # noqa: E402
eqgui._UNCONF_PATH = os.path.join(tempfile.mkdtemp(prefix="eq_uw_"), ".unconf_watch.json")


class _Stub:
    lang = "ko"
    STOP_ORDER_CHECK_SEC = eqgui.App.STOP_ORDER_CHECK_SEC

    def __init__(self):
        self.logs, self.alerts = [], []
        self._open_ctx, self._flat_seen, self._nostop_seen, self._stop_chk_at, self._size_seen = {}, {}, {}, {}, {}
        self._unconf_watch = {}

    def log(self, m):
        self.logs.append(str(m))

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_unconf_watch_add", "_unconf_watch_drop", "_unconf_save", "_unconf_clear_state", "_unconf_restore_broker",
           "_check_unconf_watch", "_check_stop_alive", "_check_stop_size", "_learn_seen_stop", "_ui", "_on_main", "_dm_kind"):
    setattr(_Stub, _n, eqgui.App.__dict__[_n])
_Stub._unconf_key = eqgui.App.__dict__["_unconf_key"]
st, net, clock, b = uta_case(strat=[], pos=[])
app_ = _Stub()
_added = app_._unconf_watch_add("BTC", b, getattr(b, "name", "bitget"), "BTCUSDT", stop=85000.0, direction="LONG", size=0.01,
                                sc="", leg="BTCUSDT")
app_._check_unconf_watch()                                         # 아직 안 보임(1번 - 안 믿음)
st["pos"] = [{"symbol": "BTCUSDT", "posSide": "long", "total": "0.01", "holdMode": "one_way_mode", "avgPrice": "90000"}]
app_._check_unconf_watch()                                         # 늦게 체결 - 보임, 손절 없음 1/2
app_._check_unconf_watch()                                         # 손절 없음 2/2 → set_stop
_sp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("G3a 미확인 감시(크립토 소수 수량 0.01): 늦게 보인 포지션 → 손절 목록 두 번 비어 있으면 기억한 자리(85000)에 부분 손절(qty 0.01)",
   _added and len(_sp) == 1 and _sp[0]["stopLoss"] == "85000.0" and _sp[0]["qty"] == "0.01" and _sp[0]["tpslMode"] == "partial"
   and "BTC|bitget|BTCUSDT" in app_._unconf_watch, (_sp, app_.logs[-4:]))
n0 = len([c for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"])
app_._check_unconf_watch()
ck("G3b 다음 바퀴는 손절이 보여 다시 걸지 않음",
   len([c for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]) == n0, app_.logs[-2:])
st["pos"] = []
app_._check_unconf_watch(); app_._check_unconf_watch()
ck("G3c 포지션이 없어지면 연속 2번 뒤 감시를 놓음(청산 보고 없음)", not app_._unconf_watch, list(app_._unconf_watch))
st, net, clock, b = uta_case(strat=[], pos=[])
app_ = _Stub()
app_._unconf_watch_add("BTC", b, "bitget", "BTCUSDT", stop=85000.0, direction="LONG", size=0.01, sc="", leg="BTCUSDT")
app_._check_unconf_watch(); app_._check_unconf_watch()
ck("G3d 끝내 안 보이면 연속 2번 뒤 놓음, 손절 주문 없음", not app_._unconf_watch
   and not [c for c in net.calls if c[0] == "POST"], paths(net))
_esrc = _insp.getsource(eqgui.App._enter_account)
ck("G3e 진입 경로: 결과 모름 + stop_attached False면 _unconf_watch_add(계좌 = 어댑터 이름, 종목 = 어댑터 표기) - 그 밖은 종전 그대로",
   "if _nostop and stop is not None:" in _esrc and "self._unconf_watch_add(asset, b, getattr(b, \"name\", _broker), _mysym" in _esrc)
ck("G4(P2-4) '닫음', '체결 없음'은 거절 DM 대신 따로: 사유 closed_nostop / not_filled, 알림 entry_closed / entry_nofill",
   'why=("closed_nostop" if _closed else "not_filled")' in _esrc and '"entry_closed" if _closed else "entry_nofill"' in _esrc
   and _esrc.index('_em0.startswith(("entry closed:", "not filled:"))') < _esrc.index('self._send_ev("entry_fail", asset, why="rejected"'))
_msrc = _insp.getsource(eqgui.App._btc_move_stop_be)
ck("G5(P2-6b) 손절 이동이 25580/25582로 거절되면 원인 한 줄(KO / EN)", "code=25580" in _msrc and "code=25582" in _msrc
   and "평균 진입가" in _msrc and "average entry price" in _msrc)

# ── G6: 3차 리뷰 P2-1 - 앱이 옮긴 손절을 감시가 '회원이 직접 옮겼다'로 보지 않게 ─────────────────────
for _n in ("_remember_stop", "_persist_open_ctx"):
    setattr(_Stub, _n, eqgui.App.__dict__[_n])
st, net, clock, b = uta_case(strat=[], pos=[])
app_ = _Stub()
app_._unconf_watch_add("BTC", b, "bitget", "BTCUSDT", stop=85000.0, direction="LONG", size=0.01, sc="Bitget (USDT-F)",
                       leg="BTCUSDT")                                  # 진입 경로가 넘기는 그대로: sc = 행 이름(크립토는 계좌 ID 없음)
app_._check_unconf_watch()
st["pos"] = [{"symbol": "BTCUSDT", "posSide": "long", "total": "0.01", "holdMode": "one_way_mode", "avgPrice": "90000"}]
app_._check_unconf_watch(); app_._check_unconf_watch()                 # 늦게 보임 → 손절 85000 걸림(stop_restored)
_rest = [a for a in app_.alerts if a[0] == "stop_restored"]
ck("G6a stop_restored 문장: 크립토 감시는 계좌 칸에 행 이름 - '계좌 Bitget (USDT-F) BTCUSDT'(종목이 계좌처럼 '계좌 BTCUSDT'로 나오지 않음)",
   _rest and "(계좌 Bitget (USDT-F) BTCUSDT)" in _rest[0][1] and "(account Bitget (USDT-F) BTCUSDT)" in _rest[0][2]
   and "계좌 BTCUSDT" not in _rest[0][1], _rest)
ck("G6a2 진입 경로: 크립토 감시의 계좌 표기 = sc, 없으면 행 이름(_check_stop_alive는 12e 원문 그대로)",
   "sc=(sc or lbl or _broker_label(_broker))" in _esrc)
_ctx = {"b": b, "sym": "BTCUSDT", "stop": 85000.0, "app_stop": 85000.0, "dir": "LONG"}
app_._open_ctx["BTC"] = _ctx
b.set_stop("BTCUSDT", 90000.0)                                        # X2+TR 블록0 = 진입가로(앱의 이동)
eqgui.App._remember_stop(app_, "BTC", 90000.0)                        # _btc_move_stop_be가 성공 뒤 부르는 그대로
_rec = app_._unconf_watch.get("BTC|bitget|BTCUSDT") or {}
ck("G6b 앱이 손절을 옮기면(_remember_stop) 같은 브로커, 종목의 미확인 감시 기록 stop/app_stop도 새 값", _rec.get("stop") == 90000.0
   and _rec.get("app_stop") == 90000.0, _rec)
app_._check_unconf_watch()
ck("G6c 다음 감시 바퀴: 'stop_manual'(직접 옮겼다) 알림 없음, manual 아님",
   not [a for a in app_.alerts if a[0] == "stop_manual"] and not _rec.get("manual"), app_.alerts)
_rec["app_stop"] = 85000.0                                            # 기록이 옛 값이어도(재시작 등) 어댑터가 마지막으로 건 값과 같으면
app_._check_unconf_watch()
ck("G6d 감시 기록이 옛 값이어도 브로커의 손절 = 어댑터가 마지막으로 건 값이면 앱의 이동으로 보고 배운다(알림 없음)",
   not [a for a in app_.alerts if a[0] == "stop_manual"] and _rec.get("app_stop") == 90000.0, (_rec, app_.alerts[-1:]))
st["strat"][0]["stopLoss"] = "91000"                                  # 회원이 거래소 화면에서 옮김(어댑터를 안 거침)
app_._check_unconf_watch()
ck("G6e 회원이 거래소에서 옮긴 값(91000)은 종전대로 'stop_manual'", [a for a in app_.alerts if a[0] == "stop_manual"], app_.alerts[-1:])
ck("G6f 확정된 크립토 진입은 같은 (자산, 어댑터 계좌, 종목)의 미확인 감시를 놓는다(선물 확정 leg와 같은 규칙)",
   'self._unconf_watch_drop(asset, getattr(b, "name", _broker), _mysym)' in _esrc
   and _esrc.index("self._remember_open(asset, b, _mysym, stop=stop,") < _esrc.index('self._unconf_watch_drop(asset, getattr(b, "name", _broker), _mysym)'))

# ══ F: 최종 묶음(2026.11.11h) ════════════════════════════════════════════════════════════
# F1~F6 = 최종 리뷰 B P1-1(평단 넘는 손절 이동 = 트리거 손절, 새 손절 먼저, 옛 손절은 그 뒤에 취소, 실패면 옛 손절 그대로)
_TRIG_KEYS = {"category", "symbol", "type", "side", "qty", "triggerBy", "triggerPrice", "triggerOrderType", "triggerOrderPrice"}
st, net, clock, b = uta_case(entry_rule=True)
r = b.set_stop("BTCUSDT", 90010.04)
_seq = [(c[0], c[1].split("?")[0]) for c in net.calls]
_tp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
_ix_place = next((i for i, c in enumerate(net.calls) if c[1] == "/api/v3/trade/place-strategy-order"), -1)
_ix_cxl = next((i for i, c in enumerate(net.calls) if c[1] == "/api/v3/trade/cancel-strategy-order"), -1)
ck("F1 평단(90000.5) 넘는 손절 90010: TP/SL 이동이 25580 → 트리거 손절(type trigger, sell, reduceOnly yes, qty 0.01, mark, 지정가 0.98배)",
   r.get("error") is None and r.get("trigger") is True and len(_tp) == 1 and _TRIG_KEYS <= set(_tp[0])
   and _tp[0]["type"] == "trigger" and _tp[0]["side"] == "sell" and _tp[0]["reduceOnly"] == "yes" and "posSide" not in _tp[0]
   and _tp[0]["qty"] == "0.01" and _tp[0]["triggerPrice"] == "90010.0" and _tp[0]["triggerBy"] == "mark"
   and _tp[0]["triggerOrderType"] == "limit" and _tp[0]["triggerOrderPrice"] == "88209.8", (r, _tp, _seq))
ck("F1b 옛 TP/SL 손절(S1, 평단 아래)은 취소하지 않고 시장가 받침으로 남긴다(검증 P1-2) - 트리거 orderId는 기록(파일)",
   _ix_place >= 0 and _ix_cxl < 0 and any(x.get("orderId") == "S1" for x in st["strat"])
   and b.last_app_stop("BTCUSDT") == 90010.04 and b._trig_book().get("BTCUSDT") == {"S9"}
   and json.load(open(BG._trig_path(), encoding="utf-8")).get(b._kfp()) == {"BTCUSDT": ["S9"]}, _seq)
_pl = b.stop_plans("BTCUSDT")
ck("F2 stop_plans: 트리거 손절이 맨 앞(planType trigger, price = triggerPrice), 받침 TP/SL은 그 뒤 - 앱의 손절 감시는 첫 값(새 손절)을 본다",
   _pl and _pl[0]["planType"] == "trigger" and _pl[0]["price"] == 90010.0 and [x["orderId"] for x in _pl] == ["S9", "S1"], _pl)
net.calls.clear()
r = b.set_stop("BTCUSDT", 90015.0)
_md = [c[2] for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"]
ck("F3 다음 이동은 앱 트리거 손절을 먼저 찾아 그것만 옮긴다(modify: orderId, qty 그대로, triggerPrice, limit) - TP/SL 받침은 안 건드림",
   r.get("error") is None and len(_md) == 1 and _md[0]["orderId"] == "S9" and _md[0]["qty"] == "0.01"
   and _md[0]["triggerPrice"] == "90015.0" and _md[0]["triggerOrderType"] == "limit"
   and "type=tpsl" not in " ".join(c[1] for c in net.calls)
   and not [c for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"], (r, paths(net)))
# 재시작(메모리 없음): 파일 기록으로 앱 트리거를 다시 찾는다, 회원 트리거(같은 닫는 쪽)는 건드리지 않는다
st["strat"].append({"orderId": "M1", "symbol": "BTCUSDT", "qty": "0.01", "side": "sell", "posSide": "net", "status": "pending",
                    "type": "trigger", "triggerPrice": "89000"})
BG._MODES.clear()
b = mk(BG)
b._mstate().update(mode="uta", hold="one_way_mode", probe_at=clock.time())
net.calls.clear()
r = b.set_stop("BTCUSDT", 90016.0)
_md = [c[2]["orderId"] for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"]
ck("F3b 재시작 뒤에도(파일 기록) 앱 트리거(S9)만 옮기고 회원 트리거(M1, posSide 'net')는 그대로, 새 트리거를 더하지 않음(검증 P2-1)",
   r.get("error") is None and _md == ["S9"] and not [c for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"],
   (r, paths(net)))
_pl = b.stop_plans("BTCUSDT")
ck("F3c stop_plans도 회원 트리거는 손절로 세지 않는다", [x["orderId"] for x in _pl] == ["S9", "S1"], _pl)
net.calls.clear()
r = b.close_symbol("BTCUSDT", dry_run=False)
_cx = sorted(c[2]["orderId"] for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order")
ck("F3d 플랫 확인 뒤 정리: 앱 트리거(S9)와 받침 TP/SL(S1, 그 종목 TP/SL 전부)을 둘 다 취소, 회원 트리거(M1)는 그대로, 기록도 비움",
   r.get("closed") is True and "S9" in _cx and "S1" in _cx and "M1" not in _cx and any(x["orderId"] == "M1" for x in st["strat"])
   and not b._trig_book() and b._kfp() not in json.load(open(BG._trig_path(), encoding="utf-8")), (_cx, r))
st, net, clock, b = uta_case(entry_rule=True, trig_fail=Resp(400, {"code": "40017", "msg": "Parameter verification failed"}))
r = b.set_stop("BTCUSDT", 90010.0)
ck("F4 트리거 손절이 거절되면 옛 손절 그대로(취소 없음), 오류에 25580과 트리거 거절 둘 다(앱이 로그 + 알림)",
   r.get("error") and "code=25580" in r["error"] and "trigger" in r["error"] and "40017" in r["error"]
   and not [c for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order"]
   and any(x.get("orderId") == "S1" for x in st["strat"]), (r, paths(net)))
st, net, clock, b = uta_case(entry_rule=True)
r = b.set_stop("BTCUSDT", 90100.0)
ck("F5 새 손절이 지금 값(90020.5) 위(롱)면 트리거를 걸지 않는다(걸자마자 반대로 작동) - 옛 손절 그대로",
   r.get("error") and "not below the market" in r["error"]
   and not [c for c in net.calls if c[1] in ("/api/v3/trade/place-strategy-order", "/api/v3/trade/cancel-strategy-order")],
   (r, paths(net)))
st, net, clock, b = uta_case(entry_rule=True, hold="hedge_mode",
                             pos=[{"symbol": "BTCUSDT", "posSide": "long", "total": "0.01", "holdMode": "hedge_mode",
                                   "avgPrice": "90000.5"}])
r = b.set_stop("BTCUSDT", 90010.0)
_tp = [c[2] for c in net.calls if c[1] == "/api/v3/trade/place-strategy-order"]
ck("F6 헤지 모드: 트리거 손절은 side sell + posSide long(reduceOnly 없음, 문서의 헤지 닫기 규칙)",
   r.get("error") is None and _tp and _tp[0]["posSide"] == "long" and "reduceOnly" not in _tp[0], (r, _tp))
st, net, clock, b = uta_case()
r = b.set_stop("BTCUSDT", 86000.0)
ck("F6b 평단 아래 손절 이동은 종전 그대로(TP/SL modify 하나, 트리거 조회 없음)",
   r.get("error") is None and "type=trigger" not in " ".join(c[1] for c in net.calls)
   and [c[2]["orderId"] for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"] == ["S1"]
   and not [c for c in net.calls if c[1] in ("/api/v3/trade/place-strategy-order", "/api/v3/trade/cancel-strategy-order")],
   paths(net))

# F7 = 최종 리뷰 B P2-4: 청산(플랫 확인) 뒤 그 종목에 남은 손절 정리 - 최선 노력, 기록은 결과에
st, net, clock, b = uta_case()
st["strat"] += [{"orderId": "T1", "symbol": "BTCUSDT", "qty": "0.01", "side": "sell", "status": "pending", "type": "trigger",
                 "triggerPrice": "91000"},
                {"orderId": "T2", "symbol": "BTCUSDT", "qty": "0.01", "side": "buy", "status": "pending", "type": "trigger",
                 "triggerPrice": "95000"}]
b._mstate()["trig_ids"] = {"BTCUSDT": {"T1"}}
r = b.flatten_all(dry_run=False, only=_only_btc)
_cx = [c[2]["orderId"] for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order"]
ck("F7 자산별 청산 뒤: BTC의 TP/SL 줄(S1, S2)과 앱이 건 트리거(T1)만 취소 - 회원 트리거(T2), ETH 손절(S3)은 그대로, 기록은 cancelled",
   r.ok and sorted(_cx) == ["S1", "S2", "T1"] and any(x["orderId"] == "T2" for x in st["strat"])
   and any(x["orderId"] == "S3" for x in st["strat"]) and len(r.cancelled) == 3
   and not (b._mstate().get("trig_ids") or {}).get("BTCUSDT"), (_cx, r.cancelled))
st, net, clock, b = uta_case()
net.routes[("POST", "/api/v3/trade/cancel-strategy-order")] = lambda q, bb: Resp(400, {"code": "25204", "msg": "x"})
r = b.flatten_all(dry_run=False)
ck("F7b 정리 취소가 실패해도 청산 결과는 성공(오류 아님), 실패는 cancelled 기록에", r.ok and len(r.closed) == 2
   and any("cancel failed" in x for x in r.cancelled), r.cancelled)
st, net, clock, b = uta_case()
r = b.close_symbol("BTCUSDT", dry_run=False)
ck("F7c close_symbol(플랫 확인) 뒤 BTC 손절 정리 - stops_cancelled 기록", r.get("closed") is True
   and len(r.get("stops_cancelled") or []) == 2 and not [x for x in st["strat"] if x.get("symbol") == "BTCUSDT"
                                                        and x.get("status") == "pending"], r)
st, net, clock, b = uta_case()
net.routes[("POST", "/api/v3/trade/close-positions")] = lambda q, bb: {"list": []}
b._trig_book()["BTCUSDT"] = {"T1"}
r = b.flatten_all(dry_run=False)
ck("F7d 닫히지 않은 종목은 손절을 건드리지 않는다(무방비 방지)", not r.closed
   and not [c for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order"], paths(net))
st, net, clock, b = uta_case(pos=[])
st["strat"] += [{"orderId": "T1", "symbol": "BTCUSDT", "qty": "0.01", "side": "sell", "status": "pending", "type": "trigger",
                 "triggerPrice": "91000"}]
b._trig_book()["BTCUSDT"] = {"T1"}
r = b.flatten_all(dry_run=False, only=_only_btc)
_cx = sorted(c[2]["orderId"] for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order")
ck("F7e 이미 플랫(트리거가 체결돼 닫힘)인 청산 시각: 앱이 트리거를 건 BTC 종목의 남은 손절(받침 S1, S2, 트리거 T1) 정리, ETH(S3)는 그대로",
   r.ok and _cx == ["S1", "S2", "T1"] and any(x["orderId"] == "S3" for x in st["strat"]), (_cx, r.cancelled))
st, net, clock, b = uta_case(pos=[])
r = b.close_symbol("BTCUSDT", dry_run=False)
ck("F7f close_symbol: 포지션이 없어도 그 종목 남은 손절 정리(검증 P2-1)", r.get("closed") is True and r.get("had") == 0
   and sorted(c[2]["orderId"] for c in net.calls if c[1] == "/api/v3/trade/cancel-strategy-order") == ["S1", "S2"], r)
st, net, clock, b = uta_case(pos=[])
r = b.flatten_all(dry_run=False)
ck("F7g 앱 트리거 기록이 없으면 플랫 청산은 요청을 더하지 않는다(종전과 같음)", r.ok
   and paths(net) == ["GET /api/v3/position/current-position"], paths(net))

# F8 = 최종 리뷰 1: 거래소 계정 번호(userId) - UTA는 v3 account/info, 클래식은 v2 spot account/info, 실패 = None
st, net, clock, b = uta_case()
ck("F8 UTA account_uid = GET /api/v3/account/info의 userId", b.account_uid() == "1"
   and paths(net) == ["GET /api/v3/account/info"], paths(net))
_st = classic_state()
_routes = classic_routes(_st)
_routes[("GET", "/api/v2/spot/account/info")] = lambda q, bb: {"userId": "777001", "authorities": ["coor"]}
net, clock = Net(_routes), Clock()
bind(BG, net, clock)
fresh(BG)
b = mk(BG)
b._mstate().update(mode="classic", probe_at=clock.time())
ck("F8b 클래식 account_uid = GET /api/v2/spot/account/info의 userId(classic 문서)", b.account_uid() == "777001"
   and paths(net) == ["GET /api/v2/spot/account/info"], paths(net))
_routes[("GET", "/api/v2/spot/account/info")] = lambda q, bb: Resp(400, {"code": "40014", "msg": "Incorrect permissions"})
ck("F8c 읽지 못하면 None(앱은 키 지문으로)", b.account_uid() is None)

# ══ V: 재검증(2502371d) 세 건 - 검증 sim_backstop.py, sim_cleanup.py의 경우 그대로 ═══════════════════════════
from eqexec.config import BitgetCfg as _BCfg  # noqa: E402


def _vb(key):
    BG._MODES.clear()
    try:
        os.remove(BG._trig_path())
    except OSError:
        pass
    b = BG.BitgetBroker(_BCfg(api_key=key, api_secret="s", passphrase="p"))
    b._mstate()["mode"] = "uta"
    return b


# V1(P1-1): 앱 트리거 기록이 있는데 트리거 목록 읽기가 한 번 실패 → 받침 TP/SL 값을 손절로 내놓지 않는다(예외 = 감시 '무판단')
b = _vb("bg_key_sim")
b._trig_book()["BTCUSDT"] = {"T1"}
b._mstate().setdefault("app_sl", {})["BTCUSDT"] = 91000.0
_vc = {"n": 0}


def _vtr(sym):
    _vc["n"] += 1
    if _vc["n"] == 1:
        raise RuntimeError("Bitget GET unfilled-strategy-orders: read timed out")
    return [{"orderId": "T1", "symbol": "BTCUSDT", "status": "pending", "triggerPrice": "91000", "triggerBy": "mark",
             "side": "sell", "qty": "0.01"}]


b._u_trig_rows = _vtr
b._u_tpsl_rows = lambda sym: [{"orderId": "SL0", "stopLoss": "88000", "slTriggerBy": "mark", "qty": "0.01"}]
try:
    b.stop_plans("BTCUSDT")
    _ex = None
except Exception as e:
    _ex = e
ck("V1 트리거 목록 읽기 실패 + 앱 트리거 기록 있음 → stop_plans 예외(받침 SL0 88000을 손절로 내놓지 않음)", _ex is not None, _ex)
_vc["n"] = 0
_app = _Stub()
_ctx = {"b": b, "sym": "BTCUSDT", "app_stop": 91000.0, "stop": 91000.0, "dir": "LONG"}
_vp = BG.Position(account_id="bitget", account_name="bitget", symbol="BTCUSDT", net_qty=0.01, raw={"avgPrice": "90000"})
_app._check_stop_alive("BTC", _vp, ctx=_ctx)
ck("V1b 앱 손절 감시: 그 바퀴는 무판단 - 'stop_manual' 알림 없음, manual 아님, 기억한 손절(91000) 그대로",
   not [a for a in _app.alerts if a[0] == "stop_manual"] and not _ctx.get("manual") and _ctx["stop"] == 91000.0
   and not _app._nostop_seen.get("BTC"), (_app.alerts, _ctx))
_app._check_stop_alive("BTC", _vp, ctx=_ctx)
_pl = b.stop_plans("BTCUSDT")
ck("V1c 다음 바퀴(읽기 성공): 트리거 T1(91000)이 첫 값 - 알림 없음",
   [(x["orderId"], x["price"]) for x in _pl] == [("T1", 91000.0), ("SL0", 88000.0)]
   and not [a for a in _app.alerts if a[0] == "stop_manual"] and not _ctx.get("manual"), (_pl, _app.alerts))
b._u_trig_rows = lambda sym: []
_pl = b.stop_plans("BTCUSDT")
ck("V1d(재검증 P1-1) 목록을 읽었는데 기록한 앱 트리거가 없으면 대기 중이 아님이 확인된 것 - 기록에서 빼고 남은 줄(TP/SL)로 판단",
   [x["orderId"] for x in _pl] == ["SL0"] and not b._trig_book().get("BTCUSDT"), (_pl, b._trig_book()))
b = _vb("bg_key_sim2")
b._u_trig_rows = lambda sym: (_ for _ in ()).throw(RuntimeError("must not be read"))
b._u_tpsl_rows = lambda sym: [{"orderId": "SL0", "stopLoss": "88000", "slTriggerBy": "mark", "qty": "0.01"}]
ck("V1e 앱 트리거 기록이 없으면 종전대로 TP/SL 줄만(트리거 목록을 읽지 않음)",
   [x["orderId"] for x in b.stop_plans("BTCUSDT")] == ["SL0"])

# V2(P2-1): 정리가 취소하지 못한 트리거를 기록에서 지우지 않는다
_vsent = []


def _vreq(m, path, params=None, body=None):
    _vsent.append((m, path, body))
    return {}


b = _vb("k_cln")
b._trig_book()["BTCUSDT"] = {"T1"}
b._trig_save()
b._req = _vreq
b._u_trig_rows = lambda sym: (_ for _ in ()).throw(RuntimeError("timeout"))
b._u_tpsl_rows = lambda sym: [{"orderId": "SL0", "stopLoss": "88000", "qty": "0.01"}]
_out = b._u_cancel_left_stops("BTCUSDT")
ck("V2 트리거 목록을 못 읽으면 T1은 기록에 남는다(메모리, 파일) - 받침 SL0만 취소",
   [x[2] for x in _vsent] == [{"orderId": "SL0"}] and b._trig_book().get("BTCUSDT") == {"T1"}
   and json.load(open(BG._trig_path(), encoding="utf-8")).get(b._kfp()) == {"BTCUSDT": ["T1"]}, (_out, b._trig_book()))
_vsent.clear()


def _vreq_fail(m, path, params=None, body=None):
    _vsent.append((m, path, body))
    if (body or {}).get("orderId") == "T1":
        raise RuntimeError("Bitget cancel failed: code=40010")
    return {}


b._req = _vreq_fail
b._u_trig_rows = lambda sym: [{"orderId": "T1", "symbol": "BTCUSDT", "status": "pending", "triggerPrice": "91000"},
                              {"orderId": "T2", "symbol": "BTCUSDT", "status": "pending", "triggerPrice": "95000"}]
b._trig_book()["BTCUSDT"] = {"T1", "T7"}
b._u_cancel_left_stops("BTCUSDT")
ck("V2b 취소가 실패한 T1은 남고, 목록에 없어 대기 중이 아님을 확인한 T7은 지운다, 회원 트리거 T2는 건드리지 않음",
   b._trig_book().get("BTCUSDT") == {"T1"} and "T2" not in [str((x[2] or {}).get("orderId")) for x in _vsent], b._trig_book())
b._req = _vreq
_vsent.clear()
b._u_cancel_left_stops("BTCUSDT")
ck("V2c 다음 정리에서 취소되면 그때 기록을 비운다(파일도)", not b._trig_book().get("BTCUSDT")
   and b._kfp() not in json.load(open(BG._trig_path(), encoding="utf-8")), b._trig_book())

# V3(P2-2): 플랫 갈래의 정리는 두 번째 포지션 읽기로 플랫을 확인한 뒤에만
for _nm, _second in (("두 번째 읽기에 포지션", "pos"), ("두 번째 읽기 실패", "err"), ("두 번째도 플랫", "flat")):
    b2 = _vb("k_cln2")
    b2._trig_book()["BTCUSDT"] = {"T9"}
    _vsent.clear()
    b2._req = _vreq
    _rd = {"n": 0}

    def _vpos(_s=_second, _r=_rd):
        _r["n"] += 1
        if _r["n"] == 1 or _s == "flat":
            return []
        if _s == "err":
            raise RuntimeError("read timed out")
        return [BG.Position(account_id="bitget", account_name="bitget", symbol="BTCUSDT", net_qty=0.01, raw={})]
    b2._u_list_open_positions = _vpos
    b2._u_trig_rows = lambda sym: [{"orderId": "T9", "symbol": "BTCUSDT", "status": "pending", "triggerPrice": "91000"}]
    b2._u_tpsl_rows = lambda sym: [{"orderId": "SL9", "stopLoss": "88000", "qty": "0.01"}]
    r = b2.flatten_all(dry_run=False, only=lambda s_: "BTC" in s_)
    _cx = [x[2]["orderId"] for x in _vsent if x[1] == "/api/v3/trade/cancel-strategy-order"]
    if _second == "flat":
        ck(f"V3 flatten 플랫 갈래({_nm}): 두 번 다 플랫이면 받침(SL9)과 트리거(T9) 정리", sorted(_cx) == ["SL9", "T9"], _cx)
    else:
        ck(f"V3 flatten 플랫 갈래({_nm}): 아무것도 취소하지 않고 종전처럼 끝, 기록 그대로",
           not _cx and not r.cancelled and b2._trig_book().get("BTCUSDT") == {"T9"} and r.ok, (_cx, r.cancelled))
    b2._trig_book()["BTCUSDT"] = {"T9"}
    _vsent.clear()
    _rd["n"] = 0
    r = b2.close_symbol("BTCUSDT", dry_run=False)
    _cx = [x[2]["orderId"] for x in _vsent if x[1] == "/api/v3/trade/cancel-strategy-order"]
    if _second == "flat":
        ck(f"V3b close_symbol 포지션 없음({_nm}): 정리", sorted(_cx) == ["SL9", "T9"] and r.get("closed") is True, (_cx, r))
    else:
        ck(f"V3b close_symbol 포지션 없음({_nm}): 아무것도 취소하지 않음(종전과 같은 반환)",
           not _cx and r == {"closed": True, "had": 0}, (_cx, r))

# ══ W: 재검증(d260abf9) - 지난 거래의 트리거 기록(검증 sim_blind.py, sim_d3.py의 경우 그대로) ═══════════════════════
# W1(P1-1, sim_blind): 지난 거래 트리거(이미 체결, 대기 아님)의 기록 + 이번 거래 손절 없음 → 2바퀴 안에 손절을 다시 건다
b = _vb("k_blind")
b._trig_book()["BTCUSDT"] = {"T_PREV"}
b._trig_save()
b._u_trig_rows = lambda sym: []
b._u_tpsl_rows = lambda sym: []
_setc = []
b._u_set_stop = lambda sym, px: (_setc.append(px) or {"error": None})
_app = _Stub()
_ctx = {"b": b, "sym": "BTCUSDT", "stop": 85000.0, "app_stop": 85000.0, "dir": "LONG"}
_vp = BG.Position(account_id="bitget", account_name="bitget", symbol="BTCUSDT", net_qty=0.01, raw={})
for _i in range(2):
    _app._check_stop_alive("BTC", _vp, ctx=_ctx, skey="BTC|x")
ck("W1 지난 거래 기록(T_PREV, 대기 아님) + 이번 손절 없음: 2바퀴 안에 기억한 자리(85000)에 손절을 다시 건다, 기록은 지움(메모리, 파일)",
   _setc == [85000.0] and not b._trig_book().get("BTCUSDT")
   and b._kfp() not in json.load(open(BG._trig_path(), encoding="utf-8")), (_setc, _app.logs[-3:], b._trig_book()))

# W2(P2-1, sim_d3 2): 지난 거래의 트리거가 아직 대기 중 - 새 진입 체결 뒤 새 손절을 걸기 전에 취소하고 기록에서 뺀다
_TOLD = {"orderId": "T_OLD", "symbol": "BTCUSDT", "qty": "0.01", "side": "sell", "status": "pending", "type": "trigger",
         "triggerPrice": "91000", "triggerBy": "mark"}
st, net, clock, b = uta_case()
st["strat"].append(dict(_TOLD))
b._trig_book()["BTCUSDT"] = {"T_OLD"}
b._trig_save()
clock.t += 5
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000.0, custom_tag="EQ-AP-W", dry_run=False)
_po = [(c[1], (c[2] or {}).get("orderId") or (c[2] or {}).get("type")) for c in net.calls if c[0] == "POST"]
ck("W2 새 진입: 체결 뒤 지난 트리거(T_OLD) 취소 → 그다음 새 손절(tpsl) - 기록 비움",
   r.get("stop") is True and _po[0][0] == "/api/v3/trade/place-order"
   and _po.index(("/api/v3/trade/cancel-strategy-order", "T_OLD")) < _po.index(("/api/v3/trade/place-strategy-order", "tpsl"))
   and not b._trig_book().get("BTCUSDT"), (r, _po))
st, net, clock, b = uta_case()
st["strat"] = [dict(_TOLD)]
b._trig_book()["BTCUSDT"] = {"T_OLD"}
b._trig_save()
net.routes[("POST", "/api/v3/trade/cancel-strategy-order")] = lambda q, bb: Resp(400, {"code": "40010", "msg": "timeout"})
clock.t += 5
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000.0, custom_tag="EQ-AP-W2", dry_run=False)
_pl = b.stop_plans("BTCUSDT")
ck("W2b 취소가 실패하면 기록은 남지만 이 포지션의 손절로 보지 않는다(진입 체결 시각보다 먼저 건 트리거) - 첫 값 = 새 손절",
   r.get("stop") is True and b._trig_book().get("BTCUSDT") == {"T_OLD"} and _pl and _pl[0]["planType"] != "trigger"
   and "T_OLD" not in [x["orderId"] for x in _pl], (_pl, b._trig_book()))
BG._MODES.clear()                                                     # 재시작: 메모리 없음 - 파일의 기록과 진입 시각으로 거른다
b = mk(BG)
b._mstate().update(mode="uta", hold="one_way_mode", probe_at=clock.time())
_pl2 = b.stop_plans("BTCUSDT")
ck("W2b2 재시작 뒤에도(파일의 진입 시각) 지난 트리거 T_OLD를 손절로 보지 않는다", "T_OLD" not in [x["orderId"] for x in _pl2]
   and b._trig_book().get("BTCUSDT") == {"T_OLD"}, _pl2)
_app = _Stub()
_ctx = {"b": b, "sym": "BTCUSDT", "stop": float(_pl[0]["price"]), "app_stop": float(_pl[0]["price"]), "dir": "LONG"}
_app._learn_seen_stop("BTC", _ctx, next((x["price"] for x in _pl if x.get("price")), None))
ck("W2c 감시: 'stop_manual' 없음, manual 아님", not [a for a in _app.alerts if a[0] == "stop_manual"] and not _ctx.get("manual"),
   _app.alerts)
net.calls.clear()
r = b.set_stop("BTCUSDT", 86000.0)
ck("W2d 손절 이동도 지난 트리거(T_OLD)를 옮기지 않고 이 포지션의 TP/SL을 옮긴다",
   r.get("error") is None and "T_OLD" not in [c[2].get("orderId") for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"]
   and [c for c in net.calls if c[1] == "/api/v3/trade/modify-strategy-order"], paths(net))
st["strat"].append({"orderId": "T_NEW", "symbol": "BTCUSDT", "qty": "0.01", "side": "sell", "status": "pending", "type": "trigger",
                    "triggerPrice": "90010", "triggerBy": "mark"})
clock.t += 5
b._trig_book()["BTCUSDT"].add("T_NEW")
b._mstate().setdefault("trig_ts", {})["T_NEW"] = clock.time()
_pl = b.stop_plans("BTCUSDT")
ck("W2e 이 진입 뒤에 건 트리거(T_NEW)는 손절로 본다(맨 앞), 지난 것(T_OLD)은 아님",
   [x["orderId"] for x in _pl][:1] == ["T_NEW"] and "T_OLD" not in [x["orderId"] for x in _pl], _pl)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}")
sys.exit(1 if fails else 0)
