"""Config loading + validation for EQ-Exec.

The config holds the member's OWN broker credentials and runs entirely on the member's
machine — it is never transmitted to EdgeQuant.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class TradovateCfg:
    env: str = "demo"            # demo | live
    name: str = ""
    password: str = ""
    app_id: str = "EQ-Exec"
    app_version: str = "0.1.0"
    cid: str = ""
    sec: str = ""
    device_id: str = ""
    accounts: list[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        # Tradovate REST base. demo for eval/testing, live for funded.
        return ("https://live.tradovateapi.com" if self.env == "live"
                else "https://demo.tradovateapi.com")


@dataclass
class ProjectXCfg:
    # ProjectX is multi-tenant; each firm has its own base URL. TopstepX shown.
    base_url: str = "https://api.topstepx.com"
    user_name: str = ""
    api_key: str = ""
    accounts: list[str] = field(default_factory=list)


@dataclass
class ScheduleCfg:
    tz: str = "America/New_York"
    cutoffs: list[str] = field(default_factory=list)   # ["HH:MM", ...]


@dataclass
class Config:
    live: bool = False           # False = dry-run (no orders sent)
    broker: str = "projectx"     # Phase 1 default: projectx (Topstep). also: tradovate
    projectx: ProjectXCfg = field(default_factory=ProjectXCfg)
    tradovate: TradovateCfg = field(default_factory=TradovateCfg)
    schedule: ScheduleCfg = field(default_factory=ScheduleCfg)
    poll_seconds: int = 20
    log_file: str = "eqexec.log"


def _coerce(d: dict[str, Any]) -> Config:
    px = ProjectXCfg(**{**ProjectXCfg().__dict__, **(d.get("projectx") or {})})
    tv = TradovateCfg(**{**TradovateCfg().__dict__, **(d.get("tradovate") or {})})
    sc = ScheduleCfg(**{**ScheduleCfg().__dict__, **(d.get("schedule") or {})})
    return Config(
        live=bool(d.get("live", False)),
        broker=d.get("broker", "projectx"),
        projectx=px,
        tradovate=tv,
        schedule=sc,
        poll_seconds=int(d.get("poll_seconds", 20)),
        log_file=d.get("log_file", "eqexec.log"),
    )


def load(path: str) -> Config:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"config not found: {path}. Copy config.example.yaml to config.yaml and fill it in.")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _coerce(raw)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.broker not in ("projectx", "tradovate"):
        raise ValueError(f"unsupported broker '{cfg.broker}' (supported: projectx, tradovate)")
    if cfg.broker == "projectx":
        if not cfg.projectx.base_url:
            raise ValueError("projectx.base_url required (e.g. https://api.topstepx.com)")
    elif cfg.broker == "tradovate":
        if cfg.tradovate.env not in ("demo", "live"):
            raise ValueError("tradovate.env must be 'demo' or 'live'")
    for hhmm in cfg.schedule.cutoffs:
        h, _, m = str(hhmm).partition(":")
        if not (h.isdigit() and m.isdigit() and 0 <= int(h) < 24 and 0 <= int(m) < 60):
            raise ValueError(f"bad cutoff time '{hhmm}' (want 'HH:MM' 24h)")
    if not cfg.schedule.cutoffs:
        raise ValueError("schedule.cutoffs is empty — give at least one 'HH:MM' flatten time")
