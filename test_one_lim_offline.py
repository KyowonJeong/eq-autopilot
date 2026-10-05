#!/usr/bin/env python3
# EQ Autopilot - 한 계약 허용 한도(one_lim) 오프라인 테스트 (2026-09-30, 대표 "어 그 방식 좋아")
# 자기 자본 선물 계좌만: 수량 0일 때 한 계약 위험이 회원이 정한 한도 안이면 한 계약, 넘거나 비어 있으면 종전처럼 건너뜀.
# 프롭 계좌·크립토는 대상 아님. 잔고는 읽지 않는다(2026-08-13 설계).
# 사용: python executor/test_one_lim_offline.py   (0=통과, 1=실패)
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


def run(one_r, lim, prop_on=False, asset="NQ", broker="projectx", entry=20000.0, stop=19900.0, mult=1.0):
    me = MagicMock()
    me.lang = "ko"
    me._live_now.return_value = False
    me._jitter_stop.side_effect = lambda a, e, s, l: s
    me._prop_one_r.return_value = one_r
    cfg = {"broker": broker, "f1": "u", "f2": "k", "f3": "", "acct": "123456", "one_r": one_r, "label": "t",
           "live": False, "prop": {"on": prop_on, "type": "test"}, "pct": {}, "one_lim": lim}
    eqgui._build_broker = MagicMock()
    sig = {"entry_ref": entry, "published_at": None}
    eqgui.App._enter_account(me, cfg, sig, asset, "LONG", stop, mult)
    legs = me._run_futures_entry.call_args[0][2] if me._run_futures_entry.called else None
    skips = [c[0][1] for c in me._entry_skip_ev.call_args_list]
    return legs, skips, [str(c[0][0]) for c in me.log.call_args_list]


# 기본값: 새 계좌·옛 설정 모두 0(꺼짐)
ck("new acct default off", eqgui._new_acct(600)["one_lim"] == 0.0)
ck("old config (no key) off", eqgui._new_acct(600, "x", True, "", None, None, False, "projectx")["one_lim"] == 0.0)
ck("load keeps value", eqgui._new_acct(600, one_lim="1,500")["one_lim"] == 1500.0)
ck("negative clamps to 0", eqgui._new_acct(600, one_lim=-5)["one_lim"] == 0.0)

# MNQ: 100pt × $2 = 한 계약 위험 $200. 1R $100 → 0.5계약 → 수량 0
legs, skips, logs = run(100.0, 0.0)
ck("limit off → skip as before", legs is None and skips == ["size_cap"], f"{legs} {skips}")
legs, skips, logs = run(100.0, 250.0)
ck("risk $200 ≤ limit $250 → 1 contract", legs is not None and sum(n for _, n in legs) == 1 and not skips, f"{legs} {skips}")
ck("log says within limit", any("한 계약 한도" in l for l in logs))
legs, skips, logs = run(100.0, 150.0)
ck("risk $200 > limit $150 → skip", legs is None and skips == ["size_cap"], f"{legs} {skips}")
ck("log says over limit", any("넘어 건너뜁니다" in l for l in logs))
legs, skips, logs = run(100.0, 250.0, prop_on=True)
ck("prop account ignores limit", legs is None and "size_cap" in skips, f"{legs} {skips}")
legs, skips, logs = run(300.0, 250.0)
ck("normal sizing untouched (1R $300 → 2 contracts)", legs is not None and sum(n for _, n in legs) == 2, f"{legs}")
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
