"""EQ-Exec runner — wires config → broker → cutoff scheduler.

Usage:
  python -m eqexec.runner --config config.yaml                # run forever (fires at cutoffs)
  python -m eqexec.runner --config config.yaml --once         # flatten once now (test)
  python -m eqexec.runner --config config.yaml --healthcheck  # auth + show open positions

Safety: when config `live: false` (default) NO orders are sent — the flatten plan is only logged.
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import config as cfgmod
from . import consent
from .broker.base import FlattenResult
from .broker.ibkr import IBKRBroker
from .broker.projectx import ProjectXBroker
from .broker.tradovate import TradovateBroker
from .broker.nt8 import NT8Broker
from .scheduler import CutoffScheduler


def _log(cfg) -> logging.Logger:
    lg = logging.getLogger("eqexec")
    lg.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    if cfg.log_file:
        fh = logging.FileHandler(cfg.log_file)
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    return lg


def _build_broker(cfg):
    if cfg.broker == "projectx":
        return ProjectXBroker(cfg.projectx)
    if cfg.broker == "tradovate":
        return TradovateBroker(cfg.tradovate)
    if cfg.broker == "ibkr":
        return IBKRBroker(cfg.ibkr)
    if cfg.broker == "nt8":
        return NT8Broker(cfg.nt8)
    raise ValueError(f"unsupported broker: {cfg.broker}")


def _report(lg, cfg, res: FlattenResult, trigger: str) -> None:
    mode = "DRY-RUN" if res.dry_run else "LIVE"
    if not res.planned:
        lg.info(f"[{trigger}] {mode}: no open positions — nothing to flatten.")
        return
    plan = ", ".join(f"{p.account_name}/{p.symbol} net={p.net_qty}" for p in res.planned)
    lg.info(f"[{trigger}] {mode}: flatten plan → {plan}")
    if res.dry_run:
        lg.info(f"[{trigger}] DRY-RUN: no orders sent (set live: true after demo-testing).")
        return
    if res.closed:
        lg.info(f"[{trigger}] closed: " + ", ".join(f"{p.account_name}/{p.symbol}" for p in res.closed))
    if res.errors:
        # Loud — a failed close is the worst outcome. (Hook push/sound here later.)
        for e in res.errors:
            lg.error(f"[{trigger}] ⚠️ {e}")
        lg.error(f"[{trigger}] ⚠️⚠️ FLATTEN INCOMPLETE — check your broker manually NOW.")
    else:
        lg.info(f"[{trigger}] ✅ flat — all positions closed and confirmed.")


def _flatten(lg, broker, cfg, trigger: str) -> FlattenResult:
    broker.authenticate()
    res = broker.flatten_all(dry_run=not cfg.live)
    _report(lg, cfg, res, trigger)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eqexec")
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true", help="flatten once now and exit (test)")
    ap.add_argument("--healthcheck", action="store_true", help="auth + list positions and exit")
    ap.add_argument("--accept", action="store_true", help="accept the consent terms non-interactively")
    args = ap.parse_args(argv)

    cfg = cfgmod.load(args.config)
    lg = _log(cfg)
    broker = _build_broker(cfg)
    lg.info(f"EQ-Exec start — broker={cfg.broker} live={cfg.live} "
            f"cutoffs={cfg.schedule.cutoffs} tz={cfg.schedule.tz}")

    if args.healthcheck:
        try:
            broker.authenticate()
            pos = broker.list_open_positions()
        except Exception as e:
            lg.error(f"healthcheck FAILED — {e}")
            lg.error("→ check user_name / api_key (and that the ProjectX API subscription + 'Link' "
                     "are active). Regenerate the key if needed, then retry.")
            return 1
        lg.info(f"healthcheck OK — {len(pos)} open position(s): "
                + (", ".join(f"{p.account_name}/{p.symbol} net={p.net_qty}" for p in pos) or "(flat)"))
        return 0

    # Consent gate — anything that acts (or simulates acting) requires explicit, recorded consent.
    if not consent.ensure(assume_yes=args.accept):
        return 2

    if args.once:
        res = _flatten(lg, broker, cfg, "manual")
        return 0 if res.ok else 1

    sched = CutoffScheduler(cfg.schedule.tz, cfg.schedule.cutoffs, cfg.poll_seconds)
    lg.info("scheduler armed — waiting for cutoffs (Ctrl-C to stop).")
    sched.run(lambda c: _flatten(lg, broker, cfg, f"cutoff {c}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
