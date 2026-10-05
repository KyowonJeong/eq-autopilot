#!/usr/bin/env python3
# EQ Autopilot - 서버 지정 청산 시각은 앞당김만(2026-09-30 전송 감사) 오프라인 테스트. 0=통과
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


tz = dt.timezone(dt.timedelta(hours=-4))                      # ET(서머타임)
now = dt.datetime(2026, 10, 1, 9, 0, tzinfo=tz)
fixed = 14 * 60
ts = lambda h, m=0, day=1: dt.datetime(2026, 10, day, h, m, tzinfo=tz).timestamp()
ck("no override → fixed", eqgui._exit_due(fixed, None, now) == (fixed, ""))
ck("earlier (13:30) → used", eqgui._exit_due(fixed, (ts(13, 30), "FOMC"), now) == (13 * 60 + 30, "FOMC"))
ck("later (15:30) → ignored", eqgui._exit_due(fixed, (ts(15, 30), "x"), now) == (fixed, ""))
ck("equal → fixed", eqgui._exit_due(fixed, (ts(14, 0), "x"), now) == (fixed, ""))
ck("other day → ignored", eqgui._exit_due(fixed, (ts(13, 0, day=2), "x"), now) == (fixed, ""))
ck("garbage → fixed", eqgui._exit_due(fixed, ("abc", "x"), now) == (fixed, ""))
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
