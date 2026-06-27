"""Interactive Brokers adapter (own-capital accounts — no prop restrictions).

Connects to the user's LOCAL Trader Workstation (TWS) or IB Gateway running on their personal
device — not a remote API — via ib_insync (or its maintained fork ib_async; same API). IBKR has
no single "close-all" call, so we cancel all working orders then market-close each open position.

UNTESTED against a live IBKR account (needs a funded IBKR Pro account). The flow follows the
ib_insync API; verify on a paper account before trusting it with real money. dry_run sends no orders.
"""
from __future__ import annotations

from .base import BrokerAdapter, FlattenResult, Position


def _ib_classes():
    """Lazy import so the dependency is only needed when IBKR is actually used."""
    try:
        from ib_insync import IB, MarketOrder
    except ImportError:  # ib_async = maintained drop-in fork
        from ib_async import IB, MarketOrder
    return IB, MarketOrder


class IBKRBroker(BrokerAdapter):
    name = "ibkr"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.IBKRCfg
        self._ib = None

    def _connect(self):
        if self._ib is not None and self._ib.isConnected():
            return self._ib
        IB, _ = _ib_classes()
        ib = IB()
        # TWS/Gateway must already be running on the user's machine (API enabled in settings).
        ib.connect(self.cfg.host, int(self.cfg.port), clientId=int(self.cfg.client_id), timeout=15)
        self._ib = ib
        return ib

    def authenticate(self) -> None:
        self._connect()

    def list_open_positions(self) -> list[Position]:
        ib = self._connect()
        want = {a for a in (self.cfg.accounts or [])}
        out: list[Position] = []
        for p in ib.positions():
            qty = int(p.position)
            if qty == 0:
                continue
            if want and p.account not in want:
                continue
            sym = getattr(p.contract, "localSymbol", "") or getattr(p.contract, "symbol", "")
            out.append(Position(
                account_id=p.account,
                account_name=p.account,
                symbol=str(sym),
                net_qty=qty,
                raw={"contract": p.contract, "account": p.account},
            ))
        return out

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        ib = self._connect()
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        _, MarketOrder = _ib_classes()
        try:
            ib.reqGlobalCancel()             # cancel all working orders first
        except Exception as e:
            res.errors.append(f"reqGlobalCancel failed: {e}")
        for pos in plan:
            try:
                action = "SELL" if pos.net_qty > 0 else "BUY"
                order = MarketOrder(action, abs(int(pos.net_qty)))
                order.account = pos.account_id
                ib.placeOrder(pos.raw["contract"], order)
            except Exception as e:
                res.errors.append(f"{pos.account_name}/{pos.symbol}: {e}")
        ib.sleep(2)                          # let the market orders fill / positions update
        # Confirm-after-act: re-read; anything still open is a loud error.
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
        return self._connect().isConnected()
