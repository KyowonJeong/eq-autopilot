"""Broker adapter interface.

A broker adapter is the ONLY thing that talks to the member's account. Keep the surface tiny:
authenticate, read open positions, flatten everything. Every adapter must honor `dry_run`
(read-only, no orders) so the tool is safe to run before the member trusts it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class Position:
    account_id: str
    account_name: str
    symbol: str              # contract symbol, e.g. "NQH6"
    net_qty: int             # signed; >0 long, <0 short, 0 = flat
    raw: dict = field(default_factory=dict)


@dataclass
class FlattenResult:
    dry_run: bool
    planned: list[Position] = field(default_factory=list)   # positions we intend to close
    closed: list[Position] = field(default_factory=list)    # confirmed closed (live only)
    cancelled: list[str] = field(default_factory=list)      # working orders cancelled (live only)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class BrokerAdapter(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def authenticate(self) -> None:
        """Acquire/refresh whatever token/session is needed. Raise on failure."""

    @abc.abstractmethod
    def list_open_positions(self) -> list[Position]:
        """Return open positions (net_qty != 0) across the configured accounts."""

    @abc.abstractmethod
    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        """Close every open position. dry_run=True must send NO orders — only build the plan."""

    @abc.abstractmethod
    def healthcheck(self) -> bool:
        """Cheap auth + read check; True if the account is reachable."""
