"""First-run consent gate.

Bakes the positioning mandate into the tool: EdgeQuant does NOT manage your account, does NOT
hold your keys, does NOT trade for you. This is a convenience tool that runs YOUR rule, on YOUR
machine, with YOUR credentials — and YOU are solely responsible. Nothing runs (or simulates
running) until the member explicitly accepts, and the acceptance is recorded with a timestamp.
"""
from __future__ import annotations

import json
import os
import time

CONSENT_VERSION = "1"

CONSENT_TEXT = """
================ EQ-Exec — please read before using ================

WHAT THIS IS
  A convenience tool. It does, automatically and on a schedule YOU set, the same "close all /
  flatten" action you could click yourself. Nothing more.

WHO IS RESPONSIBLE
  YOU are — solely and entirely. You configure the rule, you supply your own broker credentials,
  you run it on your own machine. All trading outcomes are yours: missed closes, partial closes,
  wrong closes, bugs, outages, slippage — your risk, your responsibility.

WHAT EDGEQUANT IS NOT
  EdgeQuant does NOT manage your account, does NOT receive or store your API keys, and does NOT
  place trades for you. EdgeQuant only provides this software and (optionally) a signal. It is
  not your broker, not an investment adviser, and not an asset manager.

KEEP YOUR SAFETY NET ON
  This tool is best-effort, not a guarantee. You agree to keep your broker's own native flatten /
  auto-close protection ENABLED as the real backstop, in case this tool, your machine, or the
  API is unavailable.

NO WARRANTY
  Provided "as is", with no warranty of any kind. Test on a demo / evaluation account first.

By accepting you confirm you have read and agree to all of the above.
===================================================================
"""


def _path(record_dir: str = ".") -> str:
    return os.path.join(record_dir or ".", "consent.json")


def is_accepted(record_dir: str = ".") -> bool:
    try:
        with open(_path(record_dir), "r", encoding="utf-8") as f:
            rec = json.load(f)
        return rec.get("version") == CONSENT_VERSION and bool(rec.get("accepted_at"))
    except Exception:
        return False


def _record(record_dir: str = ".") -> None:
    with open(_path(record_dir), "w", encoding="utf-8") as f:
        json.dump({"version": CONSENT_VERSION, "accepted_at": int(time.time())}, f)


def ensure(record_dir: str = ".", assume_yes: bool = False) -> bool:
    """Return True only if consent (current version) is on record or just accepted."""
    if is_accepted(record_dir):
        return True
    print(CONSENT_TEXT)
    if assume_yes:
        _record(record_dir)
        print("Consent accepted via --accept.")
        return True
    try:
        ans = input('Type exactly "I AGREE" to accept (anything else cancels): ')
    except EOFError:
        ans = ""
    if ans.strip().upper() == "I AGREE":
        _record(record_dir)
        print("Consent recorded. Thank you.")
        return True
    print("Consent not given — not running.")
    return False
