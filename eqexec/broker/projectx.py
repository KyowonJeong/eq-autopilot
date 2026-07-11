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
_SIDE = {"BUY": 0, "LONG": 0, "BID": 0, 0: 0, "SELL": 1, "SHORT": 1, "ASK": 1, 1: 1}


def _side_code(side):
    s = side.upper() if isinstance(side, str) else side
    if s not in _SIDE:
        raise ValueError(f"bad side {side!r} (use BUY/LONG/0 or SELL/SHORT/1)")
    return _SIDE[s]


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

    def search_contracts(self, text: str, live: bool = False) -> list[dict]:
        """POST /api/Contract/search {searchText, live} -> {contracts:[{id, name, description,
        tickSize, tickValue, activeContract, ...}]}. Used to resolve the current front-month
        contractId for a symbol (e.g. 'MNQ') so the user doesn't hand-type an expired code."""
        d = self._post("/api/Contract/search", {"searchText": text, "live": bool(live)})
        return d.get("contracts", d if isinstance(d, list) else [])

    def _open_orders(self, account_id) -> list[dict]:
        """POST /api/Order/searchOpen {accountId} -> {orders:[{id, ...}]}. Working (resting) orders
        — e.g. a protective stop left behind after a position closes."""
        d = self._post("/api/Order/searchOpen", {"accountId": account_id})
        return d.get("orders", d if isinstance(d, list) else [])

    def _cancel_order(self, account_id, order_id) -> None:
        """POST /api/Order/cancel {accountId, orderId}. Cancel one working order."""
        self._post("/api/Order/cancel", {"accountId": int(account_id), "orderId": order_id})

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
        if dry_run:
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
        # "Flat" = no positions AND no working orders. Cancel any resting orders (e.g. a protective
        # stop left behind after the position closed) so a leftover stop can't re-open a position
        # next session. Best-effort: failures are logged, never block the position close above.
        for a in self._accounts():
            aid = a.get("id")
            try:
                for o in self._open_orders(aid):
                    oid = o.get("id")
                    try:
                        self._cancel_order(aid, oid)
                        res.cancelled.append(f"{a.get('name', aid)}:{oid}")
                    except Exception as e:
                        res.errors.append(f"cancel order {oid} failed: {e}")
            except Exception as e:
                res.errors.append(f"{a.get('name', aid)}: open-order read failed: {e}")
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

    def closed_fills(self, start_ms: int) -> list[dict]:
        """실현손익 체결 목록(트랙레코드 푸시용). POST /api/Trade/search {accountId,
        startTimestamp} → trades[{id, contractId, creationTimestamp, profitAndLoss, side, ...}].
        profitAndLoss=null은 진입 반턴(half-turn) → 제외, 청산 반턴만 수집.
        direction = 포지션 방향(청산 체결 side의 반대: 1(sell)로 닫음 = LONG이었음).
        ⚠ UNTESTED(실키 검증 전). 실패 시 예외 — 호출측이 로그."""
        from datetime import datetime, timezone
        start_iso = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
        out = []
        for a in self._accounts():
            try:
                d = self._post("/api/Trade/search",
                               {"accountId": a["id"], "startTimestamp": start_iso})
            except Exception:
                continue
            for t in d.get("trades", []) or []:
                pnl = t.get("profitAndLoss")
                if pnl is None or t.get("voided"):
                    continue
                try:
                    ts = t.get("creationTimestamp") or ""
                    ts_ms = int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
                    out.append({
                        "tid": str(t.get("id") or ""),
                        "ts_ms": ts_ms,
                        "symbol": str(t.get("contractId") or ""),     # 예: CON.F.US.MNQ.U25
                        "pnl": float(pnl) - float(t.get("fees") or 0),
                        "direction": "LONG" if int(t.get("side") or 0) == 1 else "SHORT",
                    })
                except (TypeError, ValueError):
                    continue
        return out

    def healthcheck(self) -> bool:
        self.authenticate()
        self._accounts()
        return True

    # ── entry (order placement) — for the optional auto-ENTRY path. dry_run sends nothing. ──
    def place_entry(self, account_id, contract_id: str, side, size: int, *,
                    order_type: int = 2, limit_price=None, stop_price=None,
                    stop_loss_ticks=None, stop_loss_price=None, take_profit_ticks=None,
                    custom_tag=None, dry_run: bool = True):
        """Place an entry order via POST /api/Order/place (shapes confirmed against the docs).
          side: 'BUY'/'LONG'/0  or  'SELL'/'SHORT'/1   (0=Bid/buy, 1=Ask/sell)
          order_type: 2=Market (default), 1=Limit, 4=Stop
          stop_loss_ticks  — protective bracket, distance in TICKS from fill (broker-managed OCO).
          stop_loss_price  — protective stop at an ABSOLUTE price. Placed as a SEPARATE Stop order
                             on the opposite side after the entry (the daily flatten cancels any
                             leftover working order). Prefer this when the signal gives a price.
        Returns the order dict(s) on live, or the request body/bodies (nothing sent) on dry_run."""
        side_code = _side_code(side)
        body = {"accountId": int(account_id), "contractId": contract_id,
                "type": int(order_type), "side": side_code, "size": int(size)}
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

        # Absolute-price protective stop = a standalone Stop (type 4) on the OPPOSITE side.
        stop_body = None
        if stop_loss_price is not None:
            stop_body = {"accountId": int(account_id), "contractId": contract_id,
                         "type": 4, "side": 1 - side_code, "size": int(size),
                         "stopPrice": float(stop_loss_price)}
            if custom_tag:
                stop_body["customTag"] = f"{custom_tag}-SL"

        if dry_run:
            return {"dry_run": True, "would_place": body, "would_place_stop": stop_body}
        entry = self._post("/api/Order/place", body)
        if stop_loss_price is None:
            return entry
        # Place the protective stop as a separate order; classify a failure so the caller can
        # decide: a broker rejection (stop_rejected) is permanent → flatten; transient → retry.
        return {"entry": entry,
                **self.place_protective_stop(account_id, contract_id, side, size,
                                             stop_loss_price, custom_tag=custom_tag)}

    def place_protective_stop(self, account_id, contract_id, entry_side, size: int,
                              stop_price, *, custom_tag=None) -> dict:
        """(Re)place a standalone protective Stop (type 4) on the side OPPOSITE the entry, at an
        absolute price — used after a market entry, and retried by the caller on a transient miss.
        Returns {"stop": <order>} on success, else {"stop_error": str, "stop_rejected": bool}:
          stop_rejected=True  → broker said no (HTTP 200 success:false) — won't change on retry.
          stop_rejected=False → transient network/HTTP error — safe to retry."""
        body = {"accountId": int(account_id), "contractId": contract_id, "type": 4,
                "side": 1 - _side_code(entry_side), "size": int(size),
                "stopPrice": float(stop_price)}
        if custom_tag:
            body["customTag"] = f"{custom_tag}-SL"
        try:
            return {"stop": self._post("/api/Order/place", body)}
        except requests.RequestException as e:           # timeout / connection / 5xx → transient
            return {"stop_error": str(e), "stop_rejected": False}
        except Exception as e:                            # RuntimeError(success:false) → broker reject
            return {"stop_error": str(e), "stop_rejected": True}

    def close_contract(self, account_id, contract_id) -> dict:
        """Market-close the whole position for one {account, contract} (POST /api/Position/
        closeContract). Used to flatten a just-entered position when its protective stop was
        rejected (so we never sit unprotected)."""
        return self._post("/api/Position/closeContract",
                          {"accountId": int(account_id), "contractId": contract_id})
