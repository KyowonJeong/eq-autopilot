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


def run(one_r, lim, prop_on=False, asset="NQ", broker="nt8",   # 개인 선물 계좌(NinjaTrader, 이름이 Lucid 형식 아님) - 2026-10-21부터 Topstep, Lucid는 대상 아님
        acct="123456", entry=20000.0, stop=19900.0, mult=1.0):
    me = MagicMock()
    me.lang = "ko"
    me._live_now.return_value = False
    me._jitter_stop.side_effect = lambda a, e, s, l: s
    me._prop_one_r.return_value = one_r
    cfg = {"broker": broker, "f1": "u", "f2": "k", "f3": "", "acct": acct, "one_r": one_r, "label": "t",
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
legs, skips, logs = run(100.0, 250.0, broker="projectx")
ck("Topstep account ignores limit even with prop off (2026-10-21)", legs is None and "size_cap" in skips, f"{legs} {skips}")
legs, skips, logs = run(100.0, 250.0, acct="LDI150A1234")
ck("Lucid-named NT8 account ignores limit even with prop off (2026-10-21)", legs is None and "size_cap" in skips, f"{legs} {skips}")
legs, skips, logs = run(300.0, 250.0)
ck("normal sizing untouched (1R $300 → 2 contracts)", legs is not None and sum(n for _, n in legs) == 2, f"{legs}")

# 21l(2026-10-27 실행 경로 리뷰 P1, 대표 "바로 다 고쳐"): 신호 배수가 1보다 작으면 한도에도 배수를 곱해 비교(정본은 배수만큼 위험을 줄인 거래).
# 금 마이크로: 진입 4136, 손절 4106 = 30pt x $10 = 한 계약 위험 $300. 배수 1 이상은 종전과 똑같다.
def gc(one_r, lim, mult):
    return run(one_r, lim, asset="GC", entry=4136.0, stop=4106.0, mult=mult)
def n_of(legs):
    return sum(n for _, n in legs) if legs else 0
# 리뷰 표: 1R $300, 한도 $400(배수 1.0, 0.78은 사이징이 바로 1계약, 0.5, 0.25는 종전 바닥이 한 계약 $300을 넣었다)
for _m, _want in ((1.0, 1), (0.78, 1), (0.5, 0), (0.25, 0)):
    legs, skips, logs = gc(300.0, 400.0, _m)
    ck(f"mult {_m}: 1R $300, 한도 $400, 한 계약 $300 → {_want}계약(배수 {_m} 위험 ${300 * _m:g})", n_of(legs) == _want
       and (bool(_want) or skips == ["size_cap"]), f"{legs} {skips} {logs[-2:]}")
legs, skips, logs = gc(300.0, 400.0, 0.5)
ck("mult 0.5 건너뜀 로그 = 배수로 줄인 한도($400 x 0.50 = $200)라는 실제 이유", any("배수(0.50)를 곱한 금액($200)보다 커서" in l for l in logs)
   and not any("한 계약 한도 $400를 넘어" in l for l in logs), logs[-3:])
# 바닥이 실제로 도는 1R $200(수량 0): 배수 1.0 → 한도 $400 안 → 1, 0.78 → $312 안 → 1, 0.5 → $200 < $300 → 0, 0.25 → 0
for _m, _want in ((1.0, 1), (0.78, 1), (0.5, 0), (0.25, 0)):
    legs, skips, logs = gc(200.0, 400.0, _m)
    ck(f"floor mult {_m}: 1R $200, 한도 $400 x min(1, {_m}) = ${400 * min(1.0, _m):g}, 한 계약 $300 → {_want}계약", n_of(legs) == _want
       and (bool(_want) or skips == ["size_cap"]), f"{legs} {skips} {logs[-2:]}")
legs, skips, logs = gc(200.0, 400.0, 1.0)
ck("floor mult 1.0 로그는 종전 문장 그대로", any("한 계약 위험 $300이 직접 정한 한 계약 한도 $400 안(이번 신호 리스크 1R $200 × 1.00 = $200)" in l
                                         for l in logs), logs[-3:])
legs, skips, logs = gc(200.0, 400.0, 0.78)
ck("floor mult 0.78 로그 = 한도 x 배수($400 × 0.78 = $312)", any("($400 × 0.78 = $312) 안" in l for l in logs), logs[-3:])
legs, skips, logs = gc(100.0, 400.0, 1.9)
ck("floor mult 1.9(1 이상) = 종전 그대로 한도 $400 비교 → 1계약", n_of(legs) == 1 and any("직접 정한 한 계약 한도 $400 안" in l for l in logs),
   f"{legs} {logs[-2:]}")
legs, skips, logs = gc(200.0, 250.0, 0.5)
ck("한 계약 $300이 한도 $250 자체를 넘으면 종전 문장('한도 $250를 넘어')", n_of(legs) == 0 and any("한 계약 한도 $250를 넘어 건너뜁니다" in l for l in logs)
   and not any("배수(" in l for l in logs), logs[-3:])
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
