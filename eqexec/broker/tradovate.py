"""Tradovate adapter (Phase 1 — covers Lucid, Apex, and other Tradovate-based prop firms).

Endpoints (from Tradovate API docs — see PLAN.md). The flatten path and contract resolution
are marked VERIFY: confirm them against a DEMO account before ever running live. This adapter
never sends an order when dry_run=True.
"""
from __future__ import annotations

import time

import requests

from .base import BrokerAdapter, FlattenResult, Position

_TIMEOUT = (10, 30)          # (connect, read) seconds — never hang forever
_TOKEN_TTL = 90 * 60         # Tradovate access token lifespan
_RENEW_MARGIN = 5 * 60       # re-auth this many seconds before expiry


class TradovateBroker(BrokerAdapter):
    name = "tradovate"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.TradovateCfg
        self.base = cfg.base_url
        self._token: str | None = None
        self._token_at: float = 0.0
        self._contract_cache: dict[int, str] = {}

    # ── auth ──────────────────────────────────────────────────────────────
    def authenticate(self) -> None:
        body = {
            "name": self.cfg.name,
            "password": self.cfg.password,
            "appId": self.cfg.app_id,
            "appVersion": self.cfg.app_version,
            "cid": self.cfg.cid,
            "sec": self.cfg.sec,
        }
        if self.cfg.device_id:
            body["deviceId"] = self.cfg.device_id
        r = requests.post(f"{self.base}/auth/accesstokenrequest", json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # A pending MFA / captcha response has no accessToken — surface it clearly.
        tok = data.get("accessToken")
        if not tok:
            raise RuntimeError(f"Tradovate auth returned no accessToken: {data}")
        self._token, self._token_at = tok, time.time()

    def _ensure_token(self) -> None:
        if self._token is None or (time.time() - self._token_at) > (_TOKEN_TTL - _RENEW_MARGIN):
            self.authenticate()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _get(self, path: str, **params):
        self._ensure_token()
        r = requests.get(f"{self.base}{path}", headers=self._headers(),
                         params=params or None, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        self._ensure_token()
        r = requests.post(f"{self.base}{path}", headers=self._headers(),
                          json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    # ── reads ─────────────────────────────────────────────────────────────
    def _accounts(self) -> list[dict]:
        accts = self._get("/account/list")
        want = {a.lower() for a in (self.cfg.accounts or [])}
        if want:
            accts = [a for a in accts
                     if str(a.get("name", "")).lower() in want or str(a.get("id")) in want]
        return accts

    def _contract_symbol(self, contract_id: int) -> str:
        if contract_id in self._contract_cache:
            return self._contract_cache[contract_id]
        try:  # VERIFY: /contract/item?id= shape
            item = self._get("/contract/item", id=contract_id)
            sym = item.get("name") or str(contract_id)
        except Exception:
            sym = str(contract_id)
        self._contract_cache[contract_id] = sym
        return sym

    def list_open_positions(self) -> list[Position]:
        accts = {a["id"]: a.get("name", str(a["id"])) for a in self._accounts()}
        positions = self._get("/position/list")          # VERIFY: returns all accessible positions
        out: list[Position] = []
        for p in positions:
            acct_id = p.get("accountId")
            if acct_id not in accts:
                continue
            net = int(p.get("netPos", 0) or 0)
            if net == 0:
                continue
            out.append(Position(
                account_id=str(acct_id),
                account_name=accts[acct_id],
                symbol=self._contract_symbol(p.get("contractId")),
                net_qty=net,
                raw=p,
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
                # VERIFY on demo: liquidatePositions body. Per Tradovate it market-closes the
                # position for {accountId, contractId}. admin=false for normal members.
                self._post("/order/liquidatePositions", {
                    "accountId": int(pos.account_id),
                    "contractId": int(pos.raw.get("contractId")),
                    "admin": False,
                })
            except Exception as e:
                res.errors.append(f"{pos.account_name}/{pos.symbol}: {e}")
        # Confirm-after-act: re-read; anything still open is an error to alert on.
        try:
            still = [p for p in self.list_open_positions()]
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
