"""NinjaTrader 8 adapter — auto-close via the Automated Trading Interface (ATI / OIF).

NT8 watches a folder for Order Instruction Files (OIF) and executes them on the LOCALLY running
NinjaTrader (connected to the user's broker — Lucid/MFF via Rithmic, etc.). So we can flatten WITHOUT
the broker exposing its own API — NinjaTrader does it. Flatten-only (OIF is one-way write; we can't
read positions back through it).

Enable in NT8: Tools → Options → Automated Trading Interface → "AT Interface" on. Files go to
  <Documents>/NinjaTrader 8/incoming/   (one command per .txt file)
Commands (semicolon-delimited): FLATTENEVERYTHING  /  CLOSEPOSITION;<account>;<instrument>;...
UNTESTED here — verify on a NinjaTrader Sim101 account first. dry_run writes NOTHING.
"""
from __future__ import annotations

import os
import time

from .base import BrokerAdapter, FlattenResult, Position


def _incoming_dir(custom: str | None) -> str:
    if custom:
        return os.path.expanduser(custom)
    return os.path.join(os.path.expanduser("~"), "Documents", "NinjaTrader 8", "incoming")


class NinjaTraderBroker(BrokerAdapter):
    name = "ninjatrader"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.NinjaTraderCfg
        self.dir = _incoming_dir(getattr(cfg, "incoming_dir", "") or None)
        # accounts: NT account names to flatten (e.g. ["Sim101"]). Empty → FLATTENEVERYTHING (all).
        self.accounts = list(getattr(cfg, "accounts", []) or [])

    def _write_oif(self, line: str) -> str:
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"eq_{int(time.time()*1000)}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(line + "\n")
        return path

    def authenticate(self) -> None:
        # No auth — NT8 must be running with AT Interface enabled. We can only check the folder.
        os.makedirs(self.dir, exist_ok=True)

    def list_open_positions(self) -> list[Position]:
        # OIF is write-only — we can't read positions back. Flatten is fire-and-forget.
        return []

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        # Plan = the OIF command(s) we'd drop. No accounts → FLATTENEVERYTHING (all NT accounts).
        cmds = ([f"CLOSEPOSITION;{a}" for a in self.accounts] if self.accounts
                else ["FLATTENEVERYTHING"])
        res = FlattenResult(dry_run=dry_run, planned=[
            Position(account_id=a, account_name=a, symbol="(all)", net_qty=0,
                     raw={"oif": c}) for a, c in zip(self.accounts or ["(all)"], cmds)])
        if dry_run:
            return res
        for c in cmds:
            try:
                self._write_oif(c)
            except Exception as e:
                res.errors.append(f"OIF write failed ({c}): {e}")
        # Can't confirm via OIF — surface a note so the user verifies in NT8.
        res.errors.append("NOTE: NinjaTrader OIF is fire-and-forget — verify the flat in NT8.")
        return res

    def healthcheck(self) -> bool:
        os.makedirs(self.dir, exist_ok=True)
        return os.path.isdir(self.dir)
