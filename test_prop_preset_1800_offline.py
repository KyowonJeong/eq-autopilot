#!/usr/bin/env python3
# EQ Autopilot - Topstep 평가 1R 1800·펀디드 1R 450 이행 오프라인 테스트 (2026-09-29, 대표 "가보자"·"가즈아")
# Topstep(projectx) 계좌만 배포된 직전 기본값 1200/300/300·6000 그대로면 1800/450/450·6000으로(1800/300은 수동 값으로 불변). Lucid(nt8)·커스텀 값은 불변.
# 사용: python executor/test_prop_preset_1800_offline.py   (0=통과, 1=실패)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


old = {"on": True, "type": "test", "r_test": 1200.0, "r_buffer": 300.0, "r_steady": 300.0, "buffer": 6000.0}
ck("projectx preset r_test 1800", eqgui._PROP_PRESETS["projectx"]["r_test"] == 1800.0)
ck("projectx preset funded 450", eqgui._PROP_PRESETS["projectx"]["r_steady"] == 450.0
   and eqgui._PROP_PRESETS["projectx"]["r_buffer"] == 450.0 and eqgui._PROP_PRESETS["projectx"]["buffer"] == 6000.0)
ck("nt8 preset unchanged", eqgui._PROP_PRESETS["nt8"]["r_test"] == 300.0 and eqgui._PROP_PRESETS["nt8"]["r_steady"] == 150.0)
a = eqgui._new_acct(600, "x", True, "", dict(old), None, False, "projectx", topstep=True)
ck("topstep old default -> 1800/450", a["prop"]["r_test"] == 1800.0 and a["prop"]["r_steady"] == 450.0
   and a["prop"]["r_buffer"] == 450.0 and a["prop"]["buffer"] == 6000.0, str(a["prop"]))
a2 = eqgui._new_acct(600, "x", True, "", dict(old, r_test=1800.0), None, False, "projectx", topstep=True)
ck("topstep 1800/300 (never shipped = manual) untouched", (a2["prop"]["r_test"], a2["prop"]["r_steady"]) == (1800.0, 300.0),
   str(a2["prop"]))
a3 = eqgui._new_acct(600, "x", True, "", dict(old, r_test=1800.0, r_buffer=450.0, r_steady=450.0), None, False,
                     "projectx", topstep=True)
ck("already 1800/450 stays", (a3["prop"]["r_test"], a3["prop"]["r_steady"]) == (1800.0, 450.0), str(a3["prop"]))
b2 = eqgui._new_acct(600, "x", True, "", dict(old, r_test=1800.0), None, False, "nt8", topstep=False)
ck("lucid 1800/300 untouched", (b2["prop"]["r_test"], b2["prop"]["r_steady"]) == (1800.0, 300.0))
b = eqgui._new_acct(600, "x", True, "", dict(old), None, False, "nt8", topstep=False)
ck("lucid untouched", b["prop"]["r_test"] == 1200.0 and b["prop"]["r_steady"] == 300.0)
c = eqgui._new_acct(600, "x", True, "", dict(old, r_test=1500.0), None, False, "projectx", topstep=True)
ck("custom untouched", c["prop"]["r_test"] == 1500.0)
d = eqgui._new_acct(600, "x", True, "", dict(old, r_steady=250.0), None, False, "projectx", topstep=True)
ck("custom funded untouched", d["prop"]["r_test"] == 1200.0 and d["prop"]["r_steady"] == 250.0)
e = eqgui._new_acct(600, "x", True, "", dict(old, r_test=900.0, r_steady=600.0, buffer=9000.0), None, False,
                    "projectx", topstep=True)
ck("older champion chain ends at 1800/450", e["prop"]["r_test"] == 1800.0 and e["prop"]["r_steady"] == 450.0
   and e["prop"]["buffer"] == 6000.0, str(e["prop"]))
f = eqgui._new_acct(600, "x", True, "", None, None, False, "projectx")
ck("no prop -> default kept (no flag)", f["prop"]["r_test"] == 1200.0)
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
