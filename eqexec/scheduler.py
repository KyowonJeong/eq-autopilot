"""Local hard-cutoff scheduler.

Fires a callback once per (date, cutoff) — independent of any EdgeQuant signal, so the flatten
happens even if the network/EQ feed is down. Pair with the broker's own native flatten as a
second line of defense.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo


class CutoffScheduler:
    def __init__(self, tz: str, cutoffs: list[str], poll_seconds: int = 20):
        self.tz = ZoneInfo(tz)
        self.cutoffs = sorted(set(cutoffs))            # ["HH:MM", ...]
        self.poll = max(5, int(poll_seconds))
        self._fired: set[tuple[str, str]] = set()      # (YYYY-MM-DD, "HH:MM")

    def _due(self, now: datetime) -> list[str]:
        """Cutoffs whose time has passed today and not yet fired. We fire if now >= cutoff
        and within a sane catch-up window so a late start still flattens the same day."""
        today = now.strftime("%Y-%m-%d")
        hhmm_now = now.strftime("%H:%M")
        due = []
        for c in self.cutoffs:
            if (today, c) in self._fired:
                continue
            if hhmm_now >= c:                          # string compare ok for zero-padded HH:MM
                due.append(c)
        return due

    def run(self, on_cutoff: Callable[[str], None]) -> None:
        """Block forever, calling on_cutoff(cutoff_str) once per due cutoff."""
        while True:
            now = datetime.now(self.tz)
            for c in self._due(now):
                self._fired.add((now.strftime("%Y-%m-%d"), c))
                try:
                    on_cutoff(c)
                except Exception as e:                 # never let a callback kill the loop
                    print(f"[scheduler] on_cutoff({c}) raised: {e!r}", flush=True)
            # forget yesterday's fired keys so memory doesn't grow
            today = now.strftime("%Y-%m-%d")
            self._fired = {k for k in self._fired if k[0] == today}
            time.sleep(self.poll)
