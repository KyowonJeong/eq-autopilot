#!/usr/bin/env python3
# EQ Autopilot - OKX(USDT 스왑, BTC-USDT-SWAP) 어댑터 오프라인 테스트 (2026-11-03)
# =========================
# 왜: 대표가 본인 OKX 계좌로 시험할 어댑터를 새로 붙였다. 실키 전에 네트워크 없이 고정할 것 -
#   서명, BTC 수량 ↔ 계약 수 변환(양방향), 포지션 모드(net / long_short), 손절 첨부 진입 본문, 손절 이동,
#   청산, 포지션 목록, 체결 이력 모양(Bitget과 같게), 오류 코드 처리, 데모 헤더 스위치, clOrdId 정리,
#   지역 주소 자동 선택, 앱 배선(Bitget이 있는 자리에 OKX도).
#
# 사용: HOME=$(mktemp -d) EQ_NO_ALERT=1 python executor/test_okx_offline.py   (0=통과, 1=실패)
# =========================
import base64
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("EQ_OKX_DEMO", None)
os.environ.pop("EQ_OKX_HOST", None)
import eqexec.broker.okx as OK  # noqa: E402
from eqexec.config import OkxCfg  # noqa: E402

fails = []


def ck(name, cond, info=None):
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond or info is None else f"   -> {info!r}"[:400]))
    if not cond:
        fails.append(name)


class R:
    def __init__(self, status, obj):
        self.status_code = status
        self._o = obj
        self.text = json.dumps(obj)

    def json(self):
        return self._o


SPEC = {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctValCcy": "BTC", "lotSz": "0.01", "minSz": "0.01",
        "tickSz": "0.1", "lever": "100"}


class Fake:
    """경로별 응답 라우터. CALLS = [(method, host, path_with_query, headers, body_obj)]."""

    def __init__(self):
        self.calls = []
        self.routes = {}            # (method, path) → fn(host, query, body) -> R | obj
        self.cfg = {"posMode": "net_mode", "acctLv": "2", "perm": "read_only,trade"}
        self.positions = []
        self.algos = []
        self.set("GET", "/api/v5/public/instruments", lambda h, q, b: [SPEC])
        self.set("GET", "/api/v5/account/config", lambda h, q, b: [dict(self.cfg)])
        self.set("GET", "/api/v5/account/positions", lambda h, q, b: list(self.positions))
        self.set("GET", "/api/v5/trade/orders-algo-pending", lambda h, q, b: list(self.algos))
        self.acct_inst = {"SWAP": [{"instId": "BTC-USDT-SWAP", "state": "live", "ruleType": "normal"}], "FUTURES": []}
        self.set("GET", "/api/v5/account/instruments",
                 lambda h, q, b: list(self.acct_inst.get(dict(x.split("=", 1) for x in q.split("&")).get("instType"), [])))

    def set(self, m, path, fn):
        self.routes[(m, path)] = fn

    def _do(self, m, url, headers, data=None):
        u = urlsplit(url)
        body = json.loads(data) if data else None
        pq = u.path + (("?" + u.query) if u.query else "")
        self.calls.append((m, u.netloc, pq, dict(headers or {}), body))
        fn = self.routes.get((m, u.path))
        if fn is None:
            return R(200, {"code": "0", "msg": "", "data": [{"sCode": "0", "ordId": "1", "algoId": "9"}]})
        out = fn(u.netloc, u.query, body)
        if isinstance(out, R):
            return out
        return R(200, {"code": "0", "msg": "", "data": out})

    def get(self, url, headers=None, timeout=None, params=None):
        return self._do("GET", url, headers)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._do("POST", url, headers, data)

    def posts(self, path):
        return [c for c in self.calls if c[0] == "POST" and c[2].split("?")[0] == path]


def fresh(**cfg):
    """모듈 캐시를 비우고 새 가짜 HTTP와 어댑터."""
    for c in (OK._HOST_OF, OK._CONF_OF, OK._SPEC_OF, OK._CLOCK, OK._MKT_OF, OK._MKT_NA, OK._MKT_EXP):
        c.clear()
    f = Fake()
    OK.requests.get = f.get
    OK.requests.post = f.post
    OK.time.sleep = lambda s: None
    b = OK.OkxBroker(OkxCfg(api_key=cfg.get("key", "K"), api_secret="SECRET", passphrase="PP",
                            demo=cfg.get("demo", False), host=cfg.get("host", "")))
    return f, b


# ══ 1 서명·헤더 ═════════════════════════════════════════════════════════════
f, b = fresh()
ts = "2020-12-08T09:08:57.715Z"
exp = base64.b64encode(hmac.new(b"SECRET", (ts + "GET/api/v5/account/balance?ccy=BTC").encode(), hashlib.sha256).digest()).decode()
ck("1a 서명 = base64(HMAC_SHA256(ts+METHOD+path?query+body))", b._sign(ts, "get", "/api/v5/account/balance?ccy=BTC", "") == exp)
ck("1b 타임스탬프 = ISO8601 UTC 밀리초", re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", b._ts()) is not None, b._ts())
b.list_open_positions()
m, host, pq, hd, _ = [c for c in f.calls if c[2].startswith("/api/v5/account/positions")][0]
ck("1c 헤더 OK-ACCESS-KEY/SIGN/TIMESTAMP/PASSPHRASE", all(k in hd for k in ("OK-ACCESS-KEY", "OK-ACCESS-SIGN",
   "OK-ACCESS-TIMESTAMP", "OK-ACCESS-PASSPHRASE")) and hd["OK-ACCESS-KEY"] == "K" and hd["OK-ACCESS-PASSPHRASE"] == "PP", hd)
ck("1d 실제 보낸 서명이 그 요청(경로+쿼리)과 맞음",
   hd["OK-ACCESS-SIGN"] == b._sign(hd["OK-ACCESS-TIMESTAMP"], "GET", pq, ""), (pq, hd))
ck("1e 기본 = 실계좌(x-simulated-trading 없음), www.okx.com", "x-simulated-trading" not in hd and host == "www.okx.com", (host, hd))
b.place_entry(symbol="BTCUSDT.P", side="LONG", size=0.05, stop_loss_price=60000.04, custom_tag="EQ-AP-1", dry_run=False)
_p = f.posts("/api/v5/trade/order")[0]
ck("1f POST 서명 = ts+POST+path+본문(JSON 그대로)",
   _p[3]["OK-ACCESS-SIGN"] == b._sign(_p[3]["OK-ACCESS-TIMESTAMP"], "POST", "/api/v5/trade/order",
                                      json.dumps(_p[4], separators=(",", ":"))), _p)
pub = [c for c in f.calls if c[2].startswith("/api/v5/public/instruments")][0]
ck("1g 공개 종목 정보는 키를 안 보냄", "OK-ACCESS-KEY" not in pub[3] and "OK-ACCESS-SIGN" not in pub[3], pub[3])

# ══ 2 데모 스위치 ═══════════════════════════════════════════════════════════
f, b = fresh(demo=True)
b.list_open_positions()
ck("2a OkxCfg(demo=True) → 모든 요청 x-simulated-trading: 1", all(c[3].get("x-simulated-trading") == "1" for c in f.calls), f.calls)
os.environ["EQ_OKX_DEMO"] = "1"
f, b = fresh()
b.current_market_price("BTCUSDT")
ck("2b 환경 변수 EQ_OKX_DEMO=1 → 데모 헤더(공개 요청 포함)", b.demo and all(c[3].get("x-simulated-trading") == "1" for c in f.calls))
ck("2c 데모면 연결 정보 첫 줄에 '데모 트레이딩'", "데모 트레이딩" in (b.entry_info() or [""])[0])
os.environ["EQ_OKX_DEMO"] = "0"
f, b = fresh()
ck("2d EQ_OKX_DEMO=0 → 실계좌", not b.demo)
os.environ.pop("EQ_OKX_DEMO", None)

# ══ 3 clOrdId 정리 ══════════════════════════════════════════════════════════
ck("3a 'EQ-AP-1730000000000-2' → 영문·숫자만", OK.clord_id("EQ-AP-1730000000000-2") == "EQAP17300000000002")
ck("3b 32자 상한", len(OK.clord_id("EQ-" + "9" * 60)) == 32)
ck("3c 남는 게 없으면 None(안 붙임)", OK.clord_id("---") is None and OK.clord_id(None) is None)

# ══ 4 계약 수 변환 ══════════════════════════════════════════════════════════
f, b = fresh()
ck("4a 0.123 BTC → 12.30 계약(ctVal 0.01)", b._to_contracts("BTC-USDT-SWAP", 0.123) == "12.30", b._to_contracts("BTC-USDT-SWAP", 0.123))
ck("4b 0.12349 BTC → 12.34(lotSz 아래로 내림 - 1R 넘지 않게)", b._to_contracts("BTC-USDT-SWAP", 0.12349) == "12.34")
ck("4c 0.00005 BTC → '0'(minSz 미만)", b._to_contracts("BTC-USDT-SWAP", 0.00005) == "0")
ck("4d 12.3 계약 → 0.123 BTC", abs(b._to_coin("BTC-USDT-SWAP", "12.3") - 0.123) < 1e-12)
ck("4e 왕복 0.001 BTC 단위 보존", all(abs(b._to_coin("BTC-USDT-SWAP", b._to_contracts("BTC-USDT-SWAP", q / 1000)) - q / 1000) < 1e-12
                                    for q in range(1, 500, 7)))
ck("4f 가격 틱 스냅(tickSz 0.1)", b._snap_px("BTC-USDT-SWAP", 60000.04) == "60000.0" and b._snap_px("BTC-USDT-SWAP", 60000.06) == "60000.1")
n_spec = len([c for c in f.calls if "/public/instruments" in c[2]])
b._to_contracts("BTC-USDT-SWAP", 0.5)
ck("4g 종목 규격은 캐시(다시 읽지 않음)", len([c for c in f.calls if "/public/instruments" in c[2]]) == n_spec == 1)
f, b = fresh()
f.set("GET", "/api/v5/public/instruments", lambda h, q, bd: [{"instId": "BTC-USDT-SWAP"}])
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.1, stop_loss_price=60000, dry_run=False)
ck("4h 규격을 못 읽으면 주문 안 함(숫자 지어내지 않음)", "error" in r and not f.posts("/api/v5/trade/order"), r)
ck("4i 심볼 변환", OK.OkxBroker._inst("BTCUSDT.P") == "BTC-USDT-SWAP" and OK.OkxBroker._symbol("BTC-USDT-SWAP") == "BTCUSDT"
   and OK.OkxBroker._inst("ETHUSDT") == "ETH-USDT-SWAP")

# ══ 5 진입 본문 (net / long_short) ══════════════════════════════════════════
f, b = fresh()
r = b.place_entry(symbol="BTCUSDT.P", side="LONG", size=0.123, stop_loss_price=59876.54, custom_tag="EQ-AP-17", dry_run=False)
body = f.posts("/api/v5/trade/order")[0][4]
ck("5a net 모드: posSide 없음, cross, market, buy, sz=계약 수", body.get("posSide") is None and body["tdMode"] == "cross"
   and body["ordType"] == "market" and body["side"] == "buy" and body["sz"] == "12.30" and body["instId"] == "BTC-USDT-SWAP", body)
ck("5b 손절 첨부 attachAlgoOrds(slTriggerPx 틱 스냅, slOrdPx -1, last)",
   body.get("attachAlgoOrds") == [{"slTriggerPx": "59876.5", "slOrdPx": "-1", "slTriggerPxType": "last"}], body)
ck("5c clOrdId = 정리된 태그", body.get("clOrdId") == "EQAP17", body)
ck("5d 반환 형태 {entry, stop, stop_error}", r.get("stop") is True and r.get("stop_error") is None and "entry" in r, r)
f, b = fresh()
f.cfg["posMode"] = "long_short_mode"
b.list_open_positions()
b.place_entry(symbol="BTCUSDT", side="SHORT", size=0.05, stop_loss_price=61000, dry_run=False)
body = f.posts("/api/v5/trade/order")[0][4]
ck("5e long_short 모드: SHORT 진입 = sell + posSide short", body["side"] == "sell" and body.get("posSide") == "short", body)
r = b.place_entry(symbol="BTCUSDT", side="X", size=0.05, dry_run=False)
ck("5f 잘못된 방향은 거절(조용히 sell 아님)", str(r.get("error", "")).startswith("invalid side"), r)
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.00001, dry_run=False)
ck("5g 최소 수량 미만 = 'qty<=0'(거절 분류)", str(r.get("error", "")).startswith("qty<=0"), r)
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.05, stop_loss_price=59000, dry_run=True)
ck("5h dry_run = 주문 없음 + would_place", "would_place" in r and r.get("would_place_stop") == "59000.0"
   and len(f.posts("/api/v5/trade/order")) == 1, r)
# 포지션 모드 캐시가 낡음: net으로 알고 보냈는데 계정은 long_short → 51000 거절 → 설정 다시 읽고 posSide 붙여 한 번 더
f, b = fresh()
b.list_open_positions()                                 # net으로 캐시
f.cfg["posMode"] = "long_short_mode"
_n = {"i": 0}


def _ord(h, q, bd):
    _n["i"] += 1
    if "posSide" not in bd:
        return R(200, {"code": "1", "msg": "All operations failed", "data": [{"sCode": "51000", "sMsg": "Parameter posSide error"}]})
    return [{"ordId": "77", "sCode": "0", "sMsg": ""}]


f.set("POST", "/api/v5/trade/order", _ord)
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.05, stop_loss_price=59000, dry_run=False)
ck("5i 모드 바뀜(51000) → 설정 재조회 후 posSide long으로 1회 재시도", _n["i"] == 2 and r.get("hedge_mode") is True
   and f.posts("/api/v5/trade/order")[-1][4].get("posSide") == "long" and r["entry"].get("ordId") == "77", (r, _n))

# ══ 6 오류 코드 ═════════════════════════════════════════════════════════════
f, b = fresh()
b.list_open_positions()
f.set("POST", "/api/v5/trade/order", lambda h, q, bd: R(200, {"code": "1", "msg": "All operations failed",
      "data": [{"sCode": "51008", "sMsg": "Order failed. Insufficient USDT margin in account"}]}))
r = b.place_entry(symbol="BTCUSDT", side="LONG", size=0.05, stop_loss_price=59000, dry_run=False)
_e = str(r.get("error"))
ck("6a 주문 실패: sCode·sMsg를 오류로 올림", "sCode=51008" in _e and "Insufficient" in _e and "failed: code=1" in _e, _e)
import eqgui  # noqa: E402
ck("6b 거절(51008)은 앱 분류 '거절'", eqgui._entry_err_unknown(_e) is False)
f.set("POST", "/api/v5/trade/order", lambda h, q, bd: R(200, {"code": "50004", "msg": "Endpoint request timeout", "data": []}))
_e = str(b.place_entry(symbol="BTCUSDT", side="LONG", size=0.05, dry_run=False).get("error"))
ck("6c 50004(시간 초과 - 결과 모름)은 '미확인'으로 분류", "unknown: code=50004" in _e and eqgui._entry_err_unknown(_e) is True, _e)
f.set("POST", "/api/v5/trade/order", lambda h, q, bd: R(401, {"code": "50113", "msg": "Invalid Sign"}))
_e = str(b.place_entry(symbol="BTCUSDT", side="LONG", size=0.05, dry_run=False).get("error"))
ck("6d HTTP 401 본문 코드 노출 + 힌트, 거절 분류", "HTTP 401: code=50113" in _e and "Secret" in _e and eqgui._entry_err_unknown(_e) is False, _e)
f.set("POST", "/api/v5/trade/order", lambda h, q, bd: R(502, {}))
_e = str(b.place_entry(symbol="BTCUSDT", side="LONG", size=0.05, dry_run=False).get("error"))
ck("6e HTTP 502 = 미확인", eqgui._entry_err_unknown(_e) is True, _e)
f.set("GET", "/api/v5/account/positions", lambda h, q, bd: R(200, {"msg": "weird"}))
try:
    b.list_open_positions(); ck("6f code 없는 응답은 성공으로 치지 않음", False)
except RuntimeError:
    ck("6f code 없는 응답은 성공으로 치지 않음", True)
# 50102 시각 거절 → 서버 시각 재고 한 번만 다시
f, b = fresh()
_t = {"n": 0}


def _pos(h, q, bd):
    _t["n"] += 1
    return R(401, {"code": "50102", "msg": "Timestamp request expired"}) if _t["n"] == 1 else []


f.set("GET", "/api/v5/account/positions", _pos)
f.set("GET", "/api/v5/public/time", lambda h, q, bd: [{"ts": str(int(datetime.now(timezone.utc).timestamp() * 1000) + 45000)}])
b.list_open_positions()
ck("6g 50102 → 시각 보정 후 1회 재시도", _t["n"] == 2 and OK._CLOCK.get(b.base, 0) > 40000, (_t, OK._CLOCK))

# ══ 7 지역 주소 자동 선택 ═══════════════════════════════════════════════════
f, b = fresh(key="EEAKEY")
f.set("GET", "/api/v5/account/config", lambda h, q, bd: (R(401, {"code": "50119", "msg": "API key doesn't exist"})
                                                         if h != "eea.okx.com" else [dict(f.cfg)]))
b.authenticate()
ck("7a www가 50119면 eea.okx.com으로(읽기만으로 판단)", b.base == "https://eea.okx.com"
   and all(c[0] == "GET" for c in f.calls), [c[:3] for c in f.calls])
b2 = OK.OkxBroker(OkxCfg(api_key="EEAKEY", api_secret="SECRET", passphrase="PP"))
ck("7b 같은 키 새 어댑터는 고른 지역을 그대로(다시 안 고름)", b2.base == "https://eea.okx.com" and b2._host_ok)
ck("7c 연결 정보에 API 서버 표시", any("eea.okx.com" in x for x in b.entry_info()))
f, b = fresh(key="BADPASS")
f.set("GET", "/api/v5/account/config", lambda h, q, bd: R(401, {"code": "50105", "msg": "Passphrase incorrect"}))
try:
    b.authenticate(); ck("7d 지역 문제가 아닌 오류(50105)는 바로 올림 + 힌트", False)
except RuntimeError as e:
    ck("7d 지역 문제가 아닌 오류(50105)는 바로 올림 + 힌트", "50105" in str(e) and "Passphrase" in str(e)
       and len([c for c in f.calls if "/account/config" in c[2]]) == 1, str(e))
f, b = fresh(host="us")
ck("7e host='us' 고정", b.base == "https://us.okx.com" and b._host_fixed)

# ══ 8 포지션 목록 ═══════════════════════════════════════════════════════════
f, b = fresh()
f.positions = [{"instId": "BTC-USDT-SWAP", "pos": "-12.3", "posSide": "net", "avgPx": "60000.5", "mgnMode": "cross"},
               {"instId": "ETH-USDT-SWAP", "pos": "0", "posSide": "net"}]
ps = b.list_open_positions()
ck("8a net 숏 -12.3 계약 → BTCUSDT net_qty -0.123 BTC, 평단 avgPrice", len(ps) == 1 and ps[0].symbol == "BTCUSDT"
   and abs(ps[0].net_qty + 0.123) < 1e-12 and ps[0].raw.get("avgPrice") == "60000.5" and ps[0].raw.get("_contracts") == "12.3",
   [(p.symbol, p.net_qty, p.raw) for p in ps])
f.positions = [{"instId": "BTC-USDT-SWAP", "pos": "5", "posSide": "short", "avgPx": "1", "mgnMode": "cross"}]
ck("8b long_short: posSide short + pos 5 → -0.05", abs(b.list_open_positions()[0].net_qty + 0.05) < 1e-12)
ck("8c position_qty = BTC 절댓값", abs(b.position_qty("BTCUSDT.P") - 0.05) < 1e-12)
from eqexec import asset_scope  # noqa: E402
ck("8d 자산별 청산 판정: 'BTCUSDT'·'BTC-USDT-SWAP' 둘 다 BTC, ETH는 아님",
   asset_scope.asset_of("BTCUSDT") == "BTC" and asset_scope.asset_of("BTC-USDT-SWAP") == "BTC"
   and asset_scope.asset_of("ETH-USDT-SWAP") == "")

# ══ 9 청산 ══════════════════════════════════════════════════════════════════
f, b = fresh()
f.positions = [{"instId": "BTC-USDT-SWAP", "pos": "3", "posSide": "net", "avgPx": "1", "mgnMode": "cross"},
               {"instId": "ETH-USDT-SWAP", "pos": "1", "posSide": "net", "avgPx": "1", "mgnMode": "cross"}]


def _close(h, q, bd):
    f.positions = [p for p in f.positions if p["instId"] != bd["instId"]]
    return [{"instId": bd["instId"], "posSide": "net"}]


f.set("POST", "/api/v5/trade/close-position", _close)
f.algos = [{"algoId": "A1", "instId": "BTC-USDT-SWAP", "ordType": "conditional", "slTriggerPx": "59000"}]
r = b.close_symbol("BTCUSDT.P", dry_run=False)
cp = f.posts("/api/v5/trade/close-position")
ck("9a close_symbol: BTC만 close-position(cross, autoCxl, net은 posSide 없음)", r.get("closed") is True and len(cp) == 1
   and cp[0][4] == {"instId": "BTC-USDT-SWAP", "mgnMode": "cross", "autoCxl": True}, (r, cp))
ck("9b 플랫 확인 뒤 남은 손절 알고 정리(cancel-algos 배열)", [c[4] for c in f.posts("/api/v5/trade/cancel-algos")]
   == [[{"algoId": "A1", "instId": "BTC-USDT-SWAP"}]], f.posts("/api/v5/trade/cancel-algos"))
ck("9c ETH 그대로", any(p["instId"] == "ETH-USDT-SWAP" for p in f.positions))
r = b.flatten_all(dry_run=False, only=asset_scope.matcher("BTC"))
ck("9d flatten_all(only=BTC): BTC 없으면 아무것도 안 닫음", not r.planned and len(f.posts("/api/v5/trade/close-position")) == 1)
f.positions.append({"instId": "BTC-USDT-SWAP", "pos": "2", "posSide": "long", "avgPx": "1", "mgnMode": "cross"})
r = b.flatten_all(dry_run=False, only=asset_scope.matcher("BTC"))
cp = f.posts("/api/v5/trade/close-position")
ck("9e flatten_all(only=BTC) 양방향: posSide long 지정, ETH 그대로", r.ok and len(r.closed) == 1 and cp[-1][4].get("posSide") == "long"
   and any(p["instId"] == "ETH-USDT-SWAP" for p in f.positions), (r, cp[-1]))
r = b.flatten_all(dry_run=True)
ck("9f dry_run flatten = 주문 없음", len(f.posts("/api/v5/trade/close-position")) == 2 and r.planned)
f.positions = [{"instId": "BTC-USDT-SWAP", "pos": "4.5", "posSide": "net", "avgPx": "1", "mgnMode": "cross"}]
r = b.place_limit_close(symbol="BTCUSDT", price=61000.07, dry_run=True)
ck("9g 지정가 청산: sell, post_only, sz=계약 원문, reduceOnly(net)", r["would_place"] == {"instId": "BTC-USDT-SWAP",
   "tdMode": "cross", "side": "sell", "ordType": "post_only", "px": "61000.1", "sz": "4.5", "reduceOnly": True}, r)

# ══ 10 손절 감시·이동 ═══════════════════════════════════════════════════════
f, b = fresh()
f.positions = [{"instId": "BTC-USDT-SWAP", "pos": "3", "posSide": "net", "avgPx": "60000", "mgnMode": "cross"}]
f.algos = [{"algoId": "A1", "instId": "BTC-USDT-SWAP", "ordType": "conditional", "slTriggerPx": "59000", "slTriggerPxType": "last"},
           {"algoId": "A2", "instId": "BTC-USDT-SWAP", "ordType": "oco", "slTriggerPx": "58000", "slTriggerPxType": "mark"},
           {"algoId": "T1", "instId": "BTC-USDT-SWAP", "ordType": "conditional", "tpTriggerPx": "65000", "slTriggerPx": ""}]
pl = b.stop_plans("BTCUSDT.P")
ck("10a stop_plans: 손절만(익절 전용 제외), Bitget 모양", [(x["orderId"], x["price"]) for x in pl] == [("A1", 59000.0), ("A2", 58000.0)]
   and set(pl[0]) == {"orderId", "price", "triggerType", "planType"}, pl)
q = [c for c in f.calls if "/orders-algo-pending" in c[2]][-1][2]
ck("10b 알고 조회 = ordType conditional,oco + instId", "ordType=conditional,oco" in q and "instId=BTC-USDT-SWAP" in q, q)
r = b.set_stop("BTCUSDT", 60100.04)
am = [c[4] for c in f.posts("/api/v5/trade/amend-algos")]
ck("10c set_stop: 손절 둘 다 amend-algos 같은 값(틱 스냅), 트리거 기준 유지", r.get("error") is None and r.get("moved") == 2
   and am == [{"instId": "BTC-USDT-SWAP", "algoId": "A1", "newSlTriggerPx": "60100.0", "newSlOrdPx": "-1", "newSlTriggerPxType": "last"},
              {"instId": "BTC-USDT-SWAP", "algoId": "A2", "newSlTriggerPx": "60100.0", "newSlOrdPx": "-1", "newSlTriggerPxType": "mark"}], (r, am))
f.algos = []
r = b.set_stop("BTCUSDT", 60100)
oa = f.posts("/api/v5/trade/order-algo")
ck("10d 대기 손절 없음 → 전량 손절 신규(conditional, closeFraction 1, reduceOnly, sell)", r.get("error") is None and len(oa) == 1
   and oa[0][4] == {"instId": "BTC-USDT-SWAP", "tdMode": "cross", "side": "sell", "ordType": "conditional", "slTriggerPx": "60100.0",
                    "slOrdPx": "-1", "slTriggerPxType": "last", "closeFraction": "1", "reduceOnly": True}, (r, oa))
f.algos = [{"algoId": "A1", "instId": "BTC-USDT-SWAP", "ordType": "conditional", "slTriggerPx": "59000"}]
f.set("POST", "/api/v5/trade/amend-algos", lambda h, q, bd: R(200, {"code": "1", "msg": "", "data": [{"sCode": "51280", "sMsg": "x"}]}))
f.set("POST", "/api/v5/trade/order-algo", lambda h, q, bd: R(200, {"code": "1", "msg": "", "data": [{"sCode": "51088", "sMsg": "only one"}]}))
r = b.set_stop("BTCUSDT", 60100)
ck("10e amend·등록 다 실패 → 오류 반환, 취소는 안 함(기존 손절 유지)", "amend:" in str(r.get("error")) and "place:" in str(r.get("error"))
   and not f.posts("/api/v5/trade/cancel-algos"), r)
f.positions = [{"instId": "BTC-USDT-SWAP", "pos": "3", "posSide": "short", "avgPx": "60000", "mgnMode": "cross"}]
f.algos = []
f.set("POST", "/api/v5/trade/order-algo", lambda h, q, bd: [{"algoId": "N", "sCode": "0"}])
b.set_stop("BTCUSDT", 61000)
ck("10f 양방향 숏 신규 손절 = buy + posSide short(reduceOnly 없음)", f.posts("/api/v5/trade/order-algo")[-1][4].get("posSide") == "short"
   and f.posts("/api/v5/trade/order-algo")[-1][4]["side"] == "buy" and "reduceOnly" not in f.posts("/api/v5/trade/order-algo")[-1][4])
f.set("GET", "/api/v5/trade/orders-algo-pending", lambda h, q, bd: R(500, {}))
try:
    b.stop_plans("BTCUSDT"); ck("10g 조회 실패는 예외(앱이 '판단 안 함')", False)
except RuntimeError:
    ck("10g 조회 실패는 예외(앱이 '판단 안 함')", True)

# ══ 11 체결 이력 모양 ═══════════════════════════════════════════════════════
f, b = fresh()
now = int(datetime.now(timezone.utc).timestamp() * 1000)
f.set("GET", "/api/v5/account/positions-history", lambda h, q, bd: [
    {"instId": "BTC-USDT-SWAP", "posId": "P1", "cTime": str(now - 9000), "uTime": str(now - 1000), "direction": "long",
     "realizedPnl": "41.5", "pnl": "50", "fee": "-6", "fundingFee": "-2.5", "openAvgPx": "60000", "closeAvgPx": "60500", "type": "2"},
    {"instId": "BTC-USDT-SWAP", "posId": "P0", "cTime": str(now - 99999999), "uTime": str(now - 90000000), "direction": "short",
     "realizedPnl": "-10", "type": "2"}])
fl = b.closed_fills(now - 3600 * 1000)
ck("11a closed_fills: 시작 이후만, Bitget 모양 + 체결가", len(fl) == 1 and fl[0] == {
    "tid": f"P1-{now - 9000}", "ts_ms": now - 1000, "symbol": "BTCUSDT", "pnl": 41.5, "fees": -8.5, "direction": "LONG",
    "entry_px": 60000.0, "exit_px": 60500.0}, fl)
ck("11b 앱 자산 판정이 체결 심볼을 BTC로", eqgui.App._fill_asset(fl[0]["symbol"]) == "BTC")
ck("11c realizedPnl 없으면 pnl + 수수료·펀딩", OK._okx_net({"pnl": "10", "fee": "-1", "fundingFee": "0.5"}) == 9.5)

# ══ 12 레버리지 ═════════════════════════════════════════════════════════════
f, b = fresh()
f.set("GET", "/api/v5/account/balance", lambda h, q, bd: [{"details": [{"ccy": "USDT", "availEq": "", "availBal": "5000", "eq": "5100"}]}])
f.set("GET", "/api/v5/account/leverage-info", lambda h, q, bd: [{"instId": "BTC-USDT-SWAP", "mgnMode": "cross", "lever": "5"}])
r = b.ensure_leverage("BTCUSDT", 0.5, 60000)
sl = f.posts("/api/v5/account/set-leverage")
ck("12a 잔고(availEq 빈 칸 → availBal) · 레버리지 상향(cross, 상한 100)", r.get("new") == 10 and r.get("ab") == 5000.0
   and sl and sl[0][4] == {"instId": "BTC-USDT-SWAP", "lever": "10", "mgnMode": "cross"}, (r, sl))
ck("12b max_leverage = 종목 lever", b.max_leverage("BTCUSDT") == 100.0)

# ══ 13 키 점검 ═════════════════════════════════════════════════════════════
f, b = fresh()
f.cfg = {"posMode": "net_mode", "acctLv": "1", "perm": "read_only,withdraw"}
w = b.key_info()
ck("13a 거래 권한 없음·출금 권한 켜짐·현물 모드 경고", len(w) == 3 and any("거래" in x for x in w) and any("출금" in x for x in w)
   and any("현물" in x for x in w), w)

# ══ 14 4시간 블록 ═══════════════════════════════════════════════════════════
f, b = fresh()
end = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)
e_ms = int(end.timestamp() * 1000)
f.set("GET", "/api/v5/market/candles", lambda h, q, bd: [[str(e_ms - k * 3600000), str(100 + k), "0", "0", str(200 + k), "0", "0", "0", "1"]
                                                         for k in range(1, 6)])
ck("14a block_4h = 첫 봉 시가, 마지막 봉 종가(최신→과거 응답 재정렬)", b.block_4h("BTCUSDT", end) == (104.0, 201.0), b.block_4h("BTCUSDT", end))
q = [c for c in f.calls if "/market/candles" in c[2]][-1][2]
ck("14b bar=1H, after=블록 마감", "bar=1H" in q and f"after={e_ms}" in q, q)

# ══ 15 출금·이체 호출 없음(소스 정적) ═══════════════════════════════════════
src_okx = open(OK.__file__, encoding="utf-8").read()
ck("15a 어댑터에 출금·이체 엔드포인트 없음", not re.search(r"/asset/withdrawal|/asset/transfer|/asset/", src_okx))

# ══ 16 앱 배선 - Bitget이 있는 자리에 OKX도 ════════════════════════════════
from eqexec import sizing  # noqa: E402
ck("16a _BROKERS·BTC 브로커 목록에 okx", "okx" in eqgui._BROKERS and "okx" in eqgui._ASSET_BROKERS["BTC"]
   and "okx" not in eqgui._ASSET_BROKERS["NQ"] and "okx" not in eqgui._ASSET_BROKERS["GC"])
sp = eqgui._BROKER_SPEC.get("okx") or {}
ck("16b 화면 칸: 'OKX (USDT/USDC perp)', API Key/Secret/Passphrase, 크립토(계좌 ID 없음), 키·패스프레이즈 비밀",
   sp.get("label") == "OKX (USDT/USDC perp)" and sp.get("f1") == "API Key" and sp.get("f2") == "API Secret" and sp.get("f3") == "Passphrase"
   and sp.get("acct") is False and sp.get("futures") is False and eqgui._secret_fields("okx") == ("f1", "f3"), sp)
ck("16c _secret_fields가 Bitget과 같음", eqgui._secret_fields("okx") == eqgui._secret_fields("bitget"))
# 16b2 옛 기본 라벨 이행(2026-11-06): 설정 파일의 행 이름이 정확히 'OKX (USDT-SWAP)'면 새 라벨로, 회원이 바꾼 이름은 그대로
import tempfile as _tf16  # noqa: E402
import yaml as _y16  # noqa: E402
_cfg_bak = eqgui.CFG_PATH
eqgui.CFG_PATH = os.path.join(_tf16.mkdtemp(prefix="okxlbl_"), "config.yaml")
open(eqgui.CFG_PATH, "w", encoding="utf-8").write(_y16.safe_dump({"lang": "ko", "token": "", "assets": {"BTC": {
    "broker": "okx", "include": True, "creds": {"okx": {"f1": "", "f3": ""}},
    "accounts": [{"id": "", "one_r": 20.0, "on": True, "label": "OKX (USDT-SWAP)", "broker": "okx"},
                 {"id": "", "one_r": 20.0, "on": True, "label": "OKX KR", "broker": "bybit"}]}}}, allow_unicode=True))
try:
    _lbls = [x.get("label") for x in (eqgui._load().get("assets", {}).get("BTC", {}).get("accounts") or [])]
finally:
    eqgui.CFG_PATH = _cfg_bak
ck("16b2 옛 기본 라벨 'OKX (USDT-SWAP)' → 새 라벨, 'OKX KR'은 그대로", _lbls == ["OKX (USDT/USDC perp)", "OKX KR"], _lbls)
ok_b = eqgui._build_broker("okx", "K1", "S1", "P1", [])
ck("16d _build_broker('okx') → OkxBroker(키·시크릿·패스프레이즈)", isinstance(ok_b, OK.OkxBroker) and ok_b.key == "K1"
   and ok_b.secret == "S1" and ok_b.passphrase == "P1")
ck("16e sizing: okx = 크립토, BTC만", sizing.symbol_for("BTC", "okx") == ("BTCUSDT.P", "crypto")
   and sizing.symbol_for("NQ", "okx") == (None, None) and "okx" in sizing._CRYPTO_BROKERS)
cs = sizing.compute_size("BTC", "okx", 100.0, 60000.0, 59700.0, "LONG")
cs_bg = sizing.compute_size("BTC", "bitget", 100.0, 60000.0, 59700.0, "LONG")
ck("16f 수량 계산 = Bitget과 같음(BTC)", cs and cs_bg and cs["size"] == cs_bg["size"] and cs["unit"] == "BTC", (cs, cs_bg))
ck("16g 하트비트 브로커 키 okx → 'okx'", eqgui.App._HB_BROKER_KEY.get("okx") == "okx")
_me = type("M", (), {"_gate": {"brokers": {"bybit": True, "bitget": True}}, "_HB_BROKER_KEY": eqgui.App._HB_BROKER_KEY})()
ck("16h 서버 brokers에 okx가 없으면 허용 안 됨(서버가 켜야 함)", eqgui.App._broker_allowed(_me, "okx") is False
   and eqgui.App._broker_allowed(_me, "bitget") is True)
_me._gate = {"brokers": {"okx": True}}
ck("16i 서버가 okx: True면 허용", eqgui.App._broker_allowed(_me, "okx") is True)
ck("16j 검증 전 표시(unverified)", sp.get("status") == "unverified")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("16k 손절 캘리브레이션 두 자리(진입·지정가) 모두 okx 포함", src.count('_cross_basis_for(_broker, b)') == 1
   and src.count('_cross_basis_for(broker, b)') == 1 and 'if broker in ("bitget", "okx"):' in src
   and 'if _broker in ("bitget", "okx") and stop is not None:' in src)
_seen = []
eqgui._cross_basis_bitget = lambda: (_seen.append("bitget"), 1.0)[1]
_real_okx = eqgui._cross_basis_okx
eqgui._cross_basis_okx = lambda base="", inst="": (_seen.append((base, inst)), 2.0)[1]
ck("16l _cross_basis_for: bitget/okx는 각자(okx = 그 계정 종목), bybit는 0", eqgui._cross_basis_for("bitget") == 1.0
   and eqgui._cross_basis_for("okx", ok_b) == 2.0 and eqgui._cross_basis_for("bybit") == 0.0
   and _seen == ["bitget", ("https://www.okx.com", "BTC-USDT-SWAP")], _seen)
eqgui._cross_basis_okx = _real_okx


class _RQ:
    def get(self, url, params=None, timeout=None):
        if "bybit" in url:
            return R(200, {"result": {"list": [{"lastPrice": "60000"}]}})
        assert params == {"instId": "BTC-USDT-SWAP"}, params
        return R(200, {"code": "0", "data": [{"last": "60012.5"}]})


_rq = sys.modules["requests"]
sys.modules["requests"] = _RQ()
try:
    ck("16m OKX 베이시스 = OKX 최종가 - 바이빗가(공개 티커)", eqgui._cross_basis_okx("https://eea.okx.com") == 12.5)
finally:
    sys.modules["requests"] = _rq
ck("16n 패널 드롭다운에 OKX", "okx" in eqgui._panel_brokers())
ck("16o 연결 실패 힌트에 OKX 줄(데모 환경 변수 안내 KO/EN)", 'if self._broker_name == "okx":' in src
   and "EQ_OKX_DEMO=1 뒤 앱 다시 시작" in src and "set the environment variable EQ_OKX_DEMO=1" in src)

# ══ 17 유럽(EEA) 계정 = X-Perp (대표 2026-11-05 독일 계정) ═══════════════════
XP = "BTC-USD_UM_XPERP-310404"
XSPEC = {"instId": XP, "ctVal": "0.0001", "ctValCcy": "BTC", "lotSz": "1", "minSz": "1", "tickSz": "0.1", "lever": "50"}


def eea(swap_resp=None):
    f, b = fresh()
    f.acct_inst["SWAP"] = [] if swap_resp is None else swap_resp
    f.acct_inst["FUTURES"] = [
        {"instId": "BTC-USD_UM-261127", "state": "live", "ruleType": "normal", "expTime": "1795766400000"},
        {"instId": "BTC-USD_UM_XPERP-300101", "state": "suspend", "ruleType": "xperp", "expTime": "1893456000000"},
        {"instId": XP, "state": "live", "ruleType": "xperp", "expTime": "1933056000000"}]
    f.set("GET", "/api/v5/public/instruments",
          lambda h, q, bd: [XSPEC] if XP in q else [SPEC])
    return f, b


f, b = eea()
ck("17a SWAP이 계정에 없으면 살아 있는 X-Perp를 고른다(일반 만기 선물·정지 종목 제외)", b._market() == ("FUTURES", XP), b._market())
b.place_entry(symbol="BTCUSDT.P", side="LONG", size=0.0123, stop_loss_price=59876.54, custom_tag="EQ-AP-9", dry_run=False)
body = f.posts("/api/v5/trade/order")[0][4]
ck("17b 진입 = X-Perp instId, 계약 수 = BTC/0.0001(lotSz 1 아래로 내림), 손절 첨부",
   body["instId"] == XP and body["sz"] == "123" and body["tdMode"] == "cross"
   and body.get("attachAlgoOrds") == [{"slTriggerPx": "59876.5", "slOrdPx": "-1", "slTriggerPxType": "last"}], body)
f.positions = [{"instId": XP, "pos": "-150", "posSide": "net", "avgPx": "60010.2", "mgnMode": "cross"}]
ps = b.list_open_positions()
q = [c for c in f.calls if "/account/positions" in c[2]][-1][2]
ck("17c 포지션 = instType FUTURES로 읽고, 앱 쪽 이름 BTCUSDT·BTC 수량으로 돌려준다",
   "instType=FUTURES" in q and len(ps) == 1 and ps[0].symbol == "BTCUSDT" and abs(ps[0].net_qty + 0.015) < 1e-12, (q, ps))
b.close_symbol("BTCUSDT", dry_run=False)
cp = f.posts("/api/v5/trade/close-position")
ck("17d 청산 = X-Perp instId", cp and cp[0][4]["instId"] == XP, cp)
f.algos = [{"algoId": "77", "instId": XP, "slTriggerPx": "59000", "slTriggerPxType": "last", "ordType": "conditional"}]
b.set_stop("BTCUSDT", 59500.04)
q = [c for c in f.calls if "/orders-algo-pending" in c[2]][-1][2]
am = f.posts("/api/v5/trade/amend-algos")
ck("17e 손절 조회 instType FUTURES, 이동 = X-Perp instId",
   "instType=FUTURES" in q and XP in q and am and am[0][4]["instId"] == XP and am[0][4]["newSlTriggerPx"] == "59500.0", (q, am))
f.set("GET", "/api/v5/account/balance", lambda h, q, bd: [{"details": [
    {"ccy": "USDT", "availEq": "999"}, {"ccy": "USD", "availEq": "100.5"}, {"ccy": "USDC", "availBal": "50"}]}])
ab = b.available_usdt()
q = [c for c in f.calls if "/account/balance" in c[2]][-1][2]
ck("17f 증거금 = USD+USDC+USDG 합(USDT 제외)", ab == 150.5 and "USD%2CUSDC%2CUSDG" not in q and "USD,USDC,USDG" in q, (ab, q))
f.set("GET", "/api/v5/account/positions-history", lambda h, q, bd: [
    {"instId": XP, "posId": "p1", "cTime": "1793800000000", "uTime": "1793900000000", "realizedPnl": "12.5",
     "fee": "-0.4", "fundingFee": "0.1", "direction": "long", "openAvgPx": "60000", "closeAvgPx": "60100"}])
cf = b.closed_fills(1793000000000)
q = [c for c in f.calls if "/positions-history" in c[2]][-1][2]
ck("17g 체결 이력 instType FUTURES, symbol BTCUSDT", "instType=FUTURES" in q and cf and cf[0]["symbol"] == "BTCUSDT", (q, cf))
f.set("GET", "/api/v5/market/ticker", lambda h, q, bd: [{"last": "60111.1"}] if XP in q else [{"last": "1"}])
ck("17h 시세 = X-Perp 티커", b.current_market_price("BTCUSDT") == 60111.1)
ck("17i 연결 정보에 거래 종목 X-Perp 한 줄", any(XP in ln and "X-Perp" in ln for ln in b.entry_info()), b.entry_info())
_n = len([c for c in f.calls if "/account/instruments" in c[2]])
b.place_entry(symbol="BTCUSDT", side="SHORT", size=0.01, stop_loss_price=61000, dry_run=False)
ck("17j 고른 종목은 캐시 - 진입 앞에 종목 조회를 더하지 않음", len([c for c in f.calls if "/account/instruments" in c[2]]) == _n == 2)

# 조회 오류: 51001(그 종목 없음)은 X-Perp로, 그 밖 오류는 판단 보류(종전 종목, 캐시 안 함)
f, b = eea()
f.set("GET", "/api/v5/account/instruments", lambda h, q, bd: (
    R(200, {"code": "51001", "msg": "Instrument ID doesn't exist", "data": []}) if "instType=SWAP" in q else
    [{"instId": XP, "state": "live", "ruleType": "xperp", "expTime": "1933056000000"}]))
ck("17k SWAP 조회가 51001이면 X-Perp", b._market() == ("FUTURES", XP), b._market())
f, b = eea()
f.set("GET", "/api/v5/account/instruments", lambda h, q, bd: (
    R(200, {"code": "51155", "msg": "compliance restrictions", "data": []}) if "instType=SWAP" in q else
    [{"instId": XP, "state": "live", "ruleType": "xperp", "expTime": "1933056000000"}]))
ck("17k2 지역·규정 제한 코드(예 51155)도 X-Perp", b._market() == ("FUTURES", XP), b._market())
f, b = eea()
f.set("GET", "/api/v5/account/instruments", lambda h, q, bd: R(401, {"code": "50113", "msg": "Invalid Sign"}))
try:
    _r3 = b._market()
except Exception as _e:
    _r3 = str(_e)
ck("17k3 인증 오류는 판단 보류(예외, 캐시 안 함)", isinstance(_r3, str) and "code=50113" in _r3 and not OK._MKT_OF, _r3)
f, b = eea()
_st = {"n": 0}


def _flaky(h, q, bd):
    _st["n"] += 1
    if _st["n"] == 1:
        return R(200, {"code": "50001", "msg": "Service temporarily unavailable", "data": []})
    return [] if "instType=SWAP" in q else [{"instId": XP, "state": "live", "ruleType": "xperp", "expTime": "1933056000000"}]


f.set("GET", "/api/v5/account/instruments", _flaky)
try:
    m1 = b._market()
except Exception as _e:
    m1 = str(_e)
m2 = b._market()
ck("17l 일시 오류 = 예외(정하지 못함, 'market lookup failed: code='), 캐시 안 함 → 다음 호출이 다시 봐서 X-Perp",
   isinstance(m1, str) and "market lookup failed: code=50001" in m1 and m2 == ("FUTURES", XP), (m1, m2))
f, b = fresh()
b._market()
ck("17m 전 세계 계정(SWAP 있음) = 종전 그대로, FUTURES는 묻지 않음",
   b._market() == ("SWAP", "BTC-USDT-SWAP") and not [c for c in f.calls if "instType=FUTURES" in c[2]])
ck("17n X-Perp instId → 앱 쪽 이름 BTCUSDT", OK.OkxBroker._symbol(XP) == "BTCUSDT" and OK.OkxBroker._itype(XP) == "FUTURES"
   and OK.OkxBroker._itype("BTC-USDT-SWAP") == "SWAP")
f, b = eea()
b._market()
_seen = []
eqgui._cross_basis_okx = lambda base="", inst="": (_seen.append(inst), 3.0)[1]
ck("17o 손절 캘리브레이션 = 그 계정 종목(X-Perp) 티커로", eqgui._cross_basis_for("okx", b) == 3.0 and _seen == [XP], _seen)
eqgui._cross_basis_okx = _real_okx

# ══ 17p 유럽 X-Perp 레버리지 상한 10배(공개 규격 50배 아님) ══════════════
f, b = eea()
f.set("GET", "/api/v5/account/balance", lambda h, q, bd: [{"details": [{"ccy": "USDC", "availEq": "500"}]}])
f.set("GET", "/api/v5/account/leverage-info", lambda h, q, bd: [{"lever": "3"}])
ck("17p max_leverage = 10(X-Perp 리테일 상한)", b.max_leverage("BTCUSDT") == 10.0, b.max_leverage("BTCUSDT"))
r = b.ensure_leverage("BTCUSDT", 0.06, 60000)            # 명목 3,600 / (500×0.85) = 8.5x → ×1.3 = 12 → 상한 10
sl = f.posts("/api/v5/account/set-leverage")
ck("17q 올릴 때도 10배를 넘기지 않는다", r.get("new") == 10 and sl and sl[-1][4]["lever"] == "10", (r, sl[-1:] if sl else None))
f.set("GET", "/api/v5/account/leverage-info", lambda h, q, bd: [{"lever": "10"}])   # 대표가 OKX에서 10배로 맞춘 상태
r2 = b.ensure_leverage("BTCUSDT", 0.2, 60000)            # 명목 12,000 → 28x 필요 → 10배로도 부족
ck("17r 이미 10배인데 모자라면 올리지 않고 오류 문장(잔고를 늘려야)", r2.get("error") and "10x" in r2["error"] and not r2.get("new"), r2)
f, b = fresh()
ck("17s 전 세계 계정(SWAP)은 공개 규격 상한 그대로", b.max_leverage("BTCUSDT") == 100.0, b.max_leverage("BTCUSDT"))

# ══ 17t X-Perp 잔고: settleCcy 줄이 없으면(USD 정산 + USDC만) 더해서 읽는다, 있으면 그것만 ══
f, b = eea()
f.cfg["settleCcy"] = "USD"
f.set("GET", "/api/v5/account/balance", lambda h, q, bd: [{"details": [{"ccy": "USDC", "availEq": "500"}]}])
ck("17t settleCcy USD + USDC만 있음 = 500(없음으로 떨어지지 않음 - 대표 계정 경로)", b.available_usdt() == 500.0, b.available_usdt())
f, b = eea()
f.cfg["settleCcy"] = "USDC"
f.set("GET", "/api/v5/account/balance", lambda h, q, bd: [{"details": [{"ccy": "USDC", "availEq": "500"}, {"ccy": "USDG", "availEq": "2000"}]}])
ck("17u settleCcy USDC 줄이 있으면 그것만(USDG 안 더함)", b.available_usdt() == 500.0, b.available_usdt())

# ══ 18 BTC 무기한을 못 거래하는 계정(한국 거주 인증 등) = 연결 때 경고 ════════
f, b = fresh()
f.acct_inst = {"SWAP": [], "FUTURES": []}
ck("18a SWAP도 X-Perp도 없으면 종전 종목(주문은 OKX가 거절)", b._market() == ("SWAP", "BTC-USDT-SWAP"))
ck("18b key_info가 '거래할 수 없습니다' 경고(거주 국가 안내)", any("거래할 수 없습니다" in w and "거주 국가" in w and "한국" not in w for w in b.key_info()), b.key_info())
f, b = fresh()
ck("18c 전 세계 계정은 그 경고 없음", not any("거래할 수 없습니다" in w for w in b.key_info()), b.key_info())
f, b = eea()
ck("18d 유럽 계정(X-Perp 있음)도 경고 없음", not any("거래할 수 없습니다" in w for w in b.key_info()), b.key_info())

print()
print(("실패 %d건: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
