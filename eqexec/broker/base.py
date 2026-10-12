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


def configured_accounts(adapter) -> list[str]:
    """이 인스턴스에 지정된 계좌 목록(공백 제외). cfg가 없거나 비면 []."""
    acc = getattr(adapter, "accounts", None)
    if acc is None:
        acc = getattr(getattr(adapter, "cfg", None), "accounts", None)
    return [str(a).strip() for a in (acc or []) if str(a).strip()]


def refuse_flatten_without_accounts(adapter, dry_run: bool):
    """선물 어댑터 flatten_all의 안전장치(최종 반박 리뷰 2026-10-08 P0).

    선물 로그인 하나에 계좌가 여럿이다(프롭 평가·펀디드·다른 회원 설정의 계좌). 계좌 목록이 빈 인스턴스에서
    flatten_all을 돌리면 _accounts()가 '전부'로 풀려 그 로그인의 모든 계좌를 닫는다. 청산은 계좌를 지정한
    인스턴스에서만 한다 - 비면 아무것도 하지 않고 오류를 돌려준다(dry_run이어도 같은 결과: 계획도 세우지 않는다).
    반환: 거부면 FlattenResult(errors=[...]), 진행해도 되면 None."""
    if configured_accounts(adapter):
        return None
    return FlattenResult(dry_run=dry_run, errors=[
        f"{getattr(adapter, 'name', 'broker')}: no account id set - refusing to flatten "
        f"(an empty account list would close every account on this login)"])


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
