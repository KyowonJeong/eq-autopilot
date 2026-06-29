"""Bitget adapter (USDT-FUTURES) — auto-close via the v2 mix REST API.

Flatten uses Bitget's flash close-all endpoint (POST /api/v2/mix/order/close-positions), which
market-closes every open position for the product type. Runs on the user's machine with their own
Bitget API key/secret/passphrase. ⚠ UNTESTED — verify on a small balance first. dry_run sends NO
orders. Signing: base64(HMAC_SHA256(timestamp + METHOD + requestPath + body, secret)).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json as _json
import time

import requests

from .base import BrokerAdapter, FlattenResult, Position

_BASE = "https://api.bitget.com"
_TIMEOUT = (10, 30)
_PRODUCT = "USDT-FUTURES"


class BitgetBroker(BrokerAdapter):
    name = "bitget"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.BitgetCfg
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self.passphrase = getattr(cfg, "passphrase", "")
        self.product = getattr(cfg, "product_type", _PRODUCT)

    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        msg = f"{ts}{method.upper()}{path}{body}"
        return base64.b64encode(hmac.new(self.secret.encode(), msg.encode(),
                                         hashlib.sha256).digest()).decode()

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        ts = str(int(time.time() * 1000))
        if method == "GET" and params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            path_q, payload = f"{path}?{qs}", ""
            url = f"{_BASE}{path_q}"
        else:
            payload = _json.dumps(body or {}, separators=(",", ":")) if body else ""
            path_q, url = path, f"{_BASE}{path}"
        headers = {"ACCESS-KEY": self.key, "ACCESS-SIGN": self._sign(ts, method, path_q, payload),
                   "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": self.passphrase,
                   "locale": "en-US", "Content-Type": "application/json"}
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=_TIMEOUT)
        else:
            r = requests.post(url, data=payload, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if str(d.get("code")) not in ("00000", "0", "None"):
            raise RuntimeError(f"Bitget {path} failed: code={d.get('code')} {d.get('msg')}")
        return d.get("data", d)

    def authenticate(self) -> None:
        self.list_open_positions()           # signed read validates key+passphrase

    def list_open_positions(self) -> list[Position]:
        d = self._req("GET", "/api/v2/mix/position/all-position",
                      {"productType": self.product, "marginCoin": "USDT"})
        out: list[Position] = []
        for p in (d or []):
            size = float(p.get("total") or 0)
            if size == 0:
                continue
            net = size if str(p.get("holdSide")).lower() == "long" else -size
            out.append(Position(account_id="bitget", account_name="bitget",
                                symbol=str(p.get("symbol")), net_qty=net, raw=p))
        return out

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        try:
            # Flash close ALL positions for the product type (market).
            self._req("POST", "/api/v2/mix/order/close-positions", body={"productType": self.product})
        except Exception as e:
            res.errors.append(f"close-positions failed: {e}")
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
