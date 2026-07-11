"""Bybit adapter (USDT perpetuals) — auto-close via the V5 REST API.

Flatten = read open positions, close each with a reduceOnly Market order on the opposite side.
Runs on the user's machine with their own Bybit API key/secret (trade permission; ideally
IP-restricted). ⚠ UNTESTED — verify on **testnet** (api-testnet.bybit.com) before live. dry_run
sends NO orders. V5 signing: HMAC_SHA256(timestamp + api_key + recv_window + body, secret).
"""
from __future__ import annotations

import hashlib
import hmac
import json as _json
import time

import requests

from .base import BrokerAdapter, FlattenResult, Position

_RECV = "5000"
_TIMEOUT = (10, 30)


class BybitBroker(BrokerAdapter):
    name = "bybit"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.BybitCfg
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self.base = ("https://api-testnet.bybit.com" if getattr(cfg, "testnet", False)
                     else "https://api.bybit.com")
        self.category = getattr(cfg, "category", "linear")     # linear = USDT perps
        self.settle = getattr(cfg, "settle_coin", "USDT")

    def _sign(self, ts: str, payload: str) -> str:
        return hmac.new(self.secret.encode(), f"{ts}{self.key}{_RECV}{payload}".encode(),
                        hashlib.sha256).hexdigest()

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        ts = str(int(time.time() * 1000))
        if method == "GET":
            qs = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
            sign = self._sign(ts, qs)
            url = f"{self.base}{path}" + (f"?{qs}" if qs else "")
            r = requests.get(url, headers=self._headers(ts, sign), timeout=_TIMEOUT)
        else:
            payload = _json.dumps(body or {}, separators=(",", ":"))
            sign = self._sign(ts, payload)
            r = requests.post(f"{self.base}{path}", data=payload,
                              headers={**self._headers(ts, sign), "Content-Type": "application/json"},
                              timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if d.get("retCode") not in (0, None):
            raise RuntimeError(f"Bybit {path} failed: retCode={d.get('retCode')} {d.get('retMsg')}")
        return d.get("result", d)

    def _headers(self, ts: str, sign: str) -> dict:
        return {"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": _RECV, "X-BAPI-SIGN": sign}

    def authenticate(self) -> None:
        # Cheap signed read to validate the key.
        self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})

    def list_open_positions(self) -> list[Position]:
        d = self._req("GET", "/v5/position/list",
                      {"category": self.category, "settleCoin": self.settle})
        out: list[Position] = []
        for p in d.get("list", []):
            size = float(p.get("size") or 0)
            if size == 0:
                continue
            net = size if str(p.get("side")).lower() == "buy" else -size
            out.append(Position(account_id="bybit", account_name="bybit",
                                symbol=str(p.get("symbol")), net_qty=net, raw=p))
        return out

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        for pos in plan:
            try:
                side = "Sell" if pos.net_qty > 0 else "Buy"        # opposite, reduceOnly
                body = {"category": self.category, "symbol": pos.symbol, "side": side,
                        "orderType": "Market",
                        # Bybit 원본 size 문자열을 그대로(코인 수량 형식 보존 — float 재포맷의 '2.0' 회피).
                        "qty": str(pos.raw.get("size") or abs(pos.net_qty)),
                        "reduceOnly": True}
                _pi = pos.raw.get("positionIdx")                   # 헤지 모드면 필수(one-way=0)
                if _pi is not None:
                    body["positionIdx"] = _pi
                self._req("POST", "/v5/order/create", body=body)
            except Exception as e:
                res.errors.append(f"{pos.symbol}: {e}")
        time.sleep(1)
        try:
            still = self.list_open_positions()
            res.closed = [p for p in plan if not any(s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.symbol} net={p.net_qty}")
        except Exception as e:
            res.errors.append(f"post-flatten re-check failed: {e}")
        return res

    @staticmethod
    def _symbol(sym: str) -> str:
        """신호 심볼('BTCUSDT.P')/기타 → Bybit perp 심볼('BTCUSDT')."""
        s = str(sym or "").upper().replace(".P", "").replace("-", "").replace("/", "")
        return s or "BTCUSDT"

    @staticmethod
    def _fmt_qty(size) -> str:
        """BTC 수량 → 문자열(랏 0.001, 소수 3자리). float '2.0' 재포맷 회피용 rstrip."""
        q = round(float(size), 3)
        return f"{q:.3f}".rstrip("0").rstrip(".") or "0"

    def place_entry(self, *, symbol, side, size, stop_loss_price=None,
                    dry_run: bool = True, **_ignored) -> dict:
        """USDT perp 마켓 진입 (+ 손절 첨부). 크립토엔 account_id/contract 개념 없음 —
        symbol·qty(BTC 수량)만. stopLoss는 주문에 붙여 포지션 손절로 건다(별도 주문 불필요).
        ProjectX place_entry와 반환 형태 맞춤(루프 재사용): would_place/entry/stop/stop_error."""
        sym = self._symbol(symbol)
        bside = "Buy" if str(side).upper() == "LONG" else "Sell"
        qty = self._fmt_qty(size)
        if float(qty) <= 0:
            return {"error": "qty<=0"}
        body = {"category": self.category, "symbol": sym, "side": bside,
                "orderType": "Market", "qty": qty, "reduceOnly": False}
        if stop_loss_price:
            body["stopLoss"] = str(stop_loss_price)
            body["slTriggerBy"] = "LastPrice"
        if dry_run:
            return {"would_place": body,
                    "would_place_stop": (str(stop_loss_price) if stop_loss_price else None)}
        try:
            res = self._req("POST", "/v5/order/create", body=body)
        except Exception as e:
            return {"error": str(e)}
        # stopLoss는 진입 주문에 첨부돼 함께 체결 → 진입 성공 = 손절도 설정됨.
        return {"entry": res, "stop": bool(stop_loss_price),
                "stop_error": None if stop_loss_price else "no stop provided"}

    def closed_fills(self, start_ms: int) -> list[dict]:
        """청산 완료 포지션의 실현손익(트랙레코드 푸시용, 파생값만).
        GET /v5/position/closed-pnl → [{tid, ts_ms, symbol, pnl, direction}].
        direction = 포지션 방향(청산 주문 side의 반대: Sell로 닫음 = LONG이었음).
        ⚠ UNTESTED(실키 검증 전). 실패 시 예외 — 호출측이 로그."""
        out, cursor = [], ""
        for _ in range(10):                                 # 최대 10페이지(안전 상한)
            params = {"category": self.category, "startTime": int(start_ms), "limit": 100}
            if cursor:
                params["cursor"] = cursor
            d = self._req("GET", "/v5/position/closed-pnl", params)
            for r in d.get("list", []):
                try:
                    out.append({
                        "tid": str(r.get("orderId") or r.get("execId") or ""),
                        "ts_ms": int(r.get("updatedTime") or r.get("createdTime") or 0),
                        "symbol": str(r.get("symbol") or ""),
                        "pnl": float(r.get("closedPnl") or 0),
                        "direction": "LONG" if str(r.get("side")).lower() == "sell" else "SHORT",
                    })
                except (TypeError, ValueError):
                    continue
            cursor = d.get("nextPageCursor") or ""
            if not cursor:
                break
        return out

    def healthcheck(self) -> bool:
        self.authenticate()
        return True
