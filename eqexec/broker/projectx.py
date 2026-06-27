"""ProjectX Gateway adapter (Phase 1 — Topstep / TopstepX).

ProjectX is multi-tenant: each firm has its own base URL. TopstepX = https://api.topstepx.com.
Endpoints (ProjectX Gateway docs — see PLAN.md). VERIFY against a TopstepX demo/eval account
before live: account search shape, position `type` long/short mapping, and that closeContract
fully flattens. dry_run=True sends NO close calls.
"""
from __future__ import annotations

import time

import requests

from .base import BrokerAdapter, FlattenResult, Position

_TIMEOUT = (10, 30)
_TOKEN_TTL = 24 * 60 * 60       # ProjectX session token ~24h
_RENEW_MARGIN = 60 * 60         # re-auth 1h before expiry
_LONG = 1                       # position.type: 1=long, 2=short (docs don't state it; common ProjectX
                                # convention). Only affects the displayed net sign — closeContract
                                # flattens the whole position regardless, so a wrong guess is cosmetic.


class ProjectXBroker(BrokerAdapter):
    name = "projectx"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.ProjectXCfg
        self.base = cfg.base_url.rstrip("/")
        self._token: str | None = None
        self._token_at: float = 0.0

    # ── auth ──────────────────────────────────────────────────────────────
    def authenticate(self) -> None:
        r = requests.post(f"{self.base}/api/Auth/loginKey",
                          json={"userName": self.cfg.user_name, "apiKey": self.cfg.api_key},
                          timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if not d.get("success") or not d.get("token"):
            raise RuntimeError(f"ProjectX auth failed (errorCode={d.get('errorCode')}): "
                               f"{d.get('errorMessage') or d}")
        self._token, self._token_at = d["token"], time.time()

    def _ensure_token(self) -> None:
        if self._token is None or (time.time() - self._token_at) > (_TOKEN_TTL - _RENEW_MARGIN):
            self.authenticate()

    def _post(self, path: str, body: dict):
        self._ensure_token()
        r = requests.post(f"{self.base}{path}",
                          headers={"Authorization": f"Bearer {self._token}",
                                   "Content-Type": "application/json"},
                          json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        # ProjectX returns HTTP 200 even on logical failures — the response's success/errorCode
        # is the real status (confirmed against gateway.docs.projectx.com). Treat success:false
        # as an error so a failed flatten/read never passes silently.
        if isinstance(d, dict) and d.get("success") is False:
            raise RuntimeError(f"ProjectX {path} failed (errorCode={d.get('errorCode')}): "
                               f"{d.get('errorMessage') or d}")
        return d

    # ── reads ─────────────────────────────────────────────────────────────
    def _accounts(self) -> list[dict]:
        # POST /api/Account/search {onlyActiveAccounts} -> {accounts:[{id,name,balance,canTrade,
        # isVisible}], success, errorCode, errorMessage}  (confirmed against the docs)
        d = self._post("/api/Account/search", {"onlyActiveAccounts": True})
        accts = d.get("accounts", d if isinstance(d, list) else [])
        want = {a.lower() for a in (self.cfg.accounts or [])}
        if want:
            accts = [a for a in accts
                     if str(a.get("name", "")).lower() in want or str(a.get("id")) in want]
        return accts

    def list_open_positions(self) -> list[Position]:
        out: list[Position] = []
        for a in self._accounts():
            aid = a.get("id")
            d = self._post("/api/Position/searchOpen", {"accountId": aid})
            for p in d.get("positions", []):
                size = int(p.get("size", 0) or 0)
                if size == 0:
                    continue
                net = size if p.get("type") == _LONG else -size
                out.append(Position(
                    account_id=str(aid),
                    account_name=a.get("name", str(aid)),
                    symbol=str(p.get("contractId")),
                    net_qty=net,
                    raw={**p, "_accountId": aid},
                ))
        return out

    # ── act ───────────────────────────────────────────────────────────────
    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        for pos in plan:
            try:
                # closeContract market-closes the entire position for {accountId, contractId}.
                self._post("/api/Position/closeContract", {
                    "accountId": int(pos.raw["_accountId"]),
                    "contractId": pos.raw.get("contractId"),
                })
            except Exception as e:
                res.errors.append(f"{pos.account_name}/{pos.symbol}: {e}")
        # Confirm-after-act: re-read; anything still open is an error to alert on loudly.
        try:
            still = self.list_open_positions()
            res.closed = [p for p in plan if not any(
                s.account_id == p.account_id and s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.account_name}/{p.symbol} "
                                  f"net={p.net_qty}")
        except Exception as e:
            res.errors.append(f"post-flatten position re-check failed: {e}")
        return res

    def healthcheck(self) -> bool:
        self.authenticate()
        self._accounts()
        return True

    # ── entry (order placement) — for the optional auto-ENTRY path. dry_run sends nothing. ──
    def place_entry(self, account_id, contract_id: str, side, size: int, *,
                    order_type: int = 2, limit_price=None, stop_price=None,
                    stop_loss_ticks=None, take_profit_ticks=None, custom_tag=None,
                    dry_run: bool = True):
        """Place an entry order via POST /api/Order/place (shapes confirmed against the docs).
          side: 'BUY'/'LONG'/0  or  'SELL'/'SHORT'/1   (0=Bid/buy, 1=Ask/sell)
          order_type: 2=Market (default), 1=Limit, 4=Stop
          stop_loss_ticks / take_profit_ticks: optional protective bracket (in ticks).
        Returns the dict {"orderId", ...} on live, or the request body (no order sent) on dry_run."""
        _SIDE = {"BUY": 0, "LONG": 0, "BID": 0, 0: 0, "SELL": 1, "SHORT": 1, "ASK": 1, 1: 1}
        s = side.upper() if isinstance(side, str) else side
        if s not in _SIDE:
            raise ValueError(f"bad side {side!r} (use BUY/LONG/0 or SELL/SHORT/1)")
        body = {"accountId": int(account_id), "contractId": contract_id,
                "type": int(order_type), "side": _SIDE[s], "size": int(size)}
        if limit_price is not None:
            body["limitPrice"] = limit_price
        if stop_price is not None:
            body["stopPrice"] = stop_price
        if custom_tag:
            body["customTag"] = custom_tag
        if stop_loss_ticks is not None:                 # bracket: type 4 = Stop
            body["stopLossBracket"] = {"ticks": int(stop_loss_ticks), "type": 4}
        if take_profit_ticks is not None:               # bracket: type 1 = Limit
            body["takeProfitBracket"] = {"ticks": int(take_profit_ticks), "type": 1}
        if dry_run:
            return {"dry_run": True, "would_place": body}
        return self._post("/api/Order/place", body)
