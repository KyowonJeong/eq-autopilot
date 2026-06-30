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

    def healthcheck(self) -> bool:
        self.authenticate()
        return True
