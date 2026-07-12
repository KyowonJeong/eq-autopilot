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

    def close_symbol(self, symbol, dry_run: bool = True) -> dict:
        """이 심볼 포지션만 청산(다른 심볼 무접촉) — 연속 세션 '청산 확인 후 진입'용.
        flash close(symbol 지정) + 최대 ~6초 재확인 폴링. 반환 {closed: bool, had: n, error}."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"closed": True, "had": 0}
        if dry_run:
            return {"closed": False, "had": len(mine), "dry_run": True}
        try:
            self._req("POST", "/api/v2/mix/order/close-positions",
                      body={"symbol": sym, "productType": self.product})
        except Exception as e:
            return {"closed": False, "had": len(mine), "error": str(e)}
        for _ in range(6):                                   # 죽은 것 '확인' 후에만 True
            time.sleep(1)
            try:
                if not any(p.symbol == sym for p in self.list_open_positions()):
                    return {"closed": True, "had": len(mine)}
            except Exception:
                pass
        return {"closed": False, "had": len(mine), "error": "still open after close"}

    @staticmethod
    def _symbol(sym: str) -> str:
        """신호 심볼('BTCUSDT.P') → Bitget perp 심볼('BTCUSDT')."""
        s = str(sym or "").upper().replace(".P", "").replace("-", "").replace("/", "")
        return s or "BTCUSDT"

    @staticmethod
    def _fmt_qty(size) -> str:
        q = round(float(size), 3)
        return f"{q:.3f}".rstrip("0").rstrip(".") or "0"

    def place_entry(self, *, symbol, side, size, stop_loss_price=None,
                    dry_run: bool = True, **_ignored) -> dict:
        """USDT-FUTURES 마켓 진입 (+ presetStopLossPrice 손절 첨부). 크립토엔 account/contract 없음 —
        symbol·size(BTC 수량)만. ProjectX place_entry와 반환 형태 맞춤(루프 재사용)."""
        sym = self._symbol(symbol)
        bside = "buy" if str(side).upper() == "LONG" else "sell"
        qty = self._fmt_qty(size)
        if float(qty) <= 0:
            return {"error": "qty<=0"}
        margin_mode = getattr(self.cfg, "margin_mode", "crossed")
        body = {"symbol": sym, "productType": self.product, "marginMode": margin_mode,
                "marginCoin": "USDT", "side": bside, "orderType": "market", "size": qty}
        if stop_loss_price:
            body["presetStopLossPrice"] = str(stop_loss_price)
        if dry_run:
            return {"would_place": body,
                    "would_place_stop": (str(stop_loss_price) if stop_loss_price else None)}
        try:
            res = self._req("POST", "/api/v2/mix/order/place-order", body=body)
        except Exception as e:
            # 헤지(양방향) 모드 계정: tradeSide(open/close) 필수 — 거절 시 open 지정 후 1회 재시도
            # (원웨이/헤지 자동 호환, 2026-07-12). 주문 미체결 거절이라 이중 진입 위험 없음.
            if "side" in str(e).lower() or "40774" in str(e):
                body["tradeSide"] = "open"
                try:
                    res = self._req("POST", "/api/v2/mix/order/place-order", body=body)
                    return {"entry": res, "stop": bool(stop_loss_price), "hedge_mode": True,
                            "stop_error": None if stop_loss_price else "no stop provided"}
                except Exception as e2:
                    return {"error": f"{e2} (hedge retry)"}
            return {"error": str(e)}
        return {"entry": res, "stop": bool(stop_loss_price),
                "stop_error": None if stop_loss_price else "no stop provided"}

    def closed_fills(self, start_ms: int) -> list[dict]:
        """청산 완료 포지션의 실현손익(트랙레코드 푸시용, 파생값만).
        GET /api/v2/mix/position/history-position → [{tid, ts_ms, symbol, pnl, direction}].
        pnl = netProfit 우선(수수료 반영), 없으면 pnl. ⚠ UNTESTED(실키 검증 전)."""
        d = self._req("GET", "/api/v2/mix/position/history-position",
                      {"productType": self.product, "startTime": int(start_ms), "limit": 100})
        rows = d.get("list", d) if isinstance(d, dict) else d
        out = []
        for r in (rows or []):
            try:
                out.append({
                    "tid": str(r.get("positionId") or r.get("orderId") or ""),
                    "ts_ms": int(r.get("utime") or r.get("ctime") or 0),
                    "symbol": str(r.get("symbol") or ""),
                    "pnl": float(r.get("netProfit") if r.get("netProfit") is not None else (r.get("pnl") or 0)),
                    "direction": "LONG" if str(r.get("holdSide")).lower() == "long" else "SHORT",
                })
            except (TypeError, ValueError):
                continue
        return out

    def healthcheck(self) -> bool:
        self.authenticate()
        return True
