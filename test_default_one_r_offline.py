#!/usr/bin/env python3
# EQ Autopilot - 새 계좌 기본 1R 오프라인 테스트(2026-10-11b, 대표 10/9 "1R을 500으로"): 새로 만드는 계좌만 500,
# 회원이 정한 1R·기존 계좌·옛 설정 이행은 그대로(빠진 칸은 옛 기본 600으로 읽음). 사용: python executor/test_default_one_r_offline.py
import inspect
import os
import sys
import tempfile

_H = tempfile.mkdtemp(prefix="eq_1r_")          # 실제 설정 폴더를 건드리지 않게(리뷰 10/12 운영 13)
os.environ["HOME"] = _H; os.environ["APPDATA"] = _H; os.environ.pop("EQ_PROFILE", None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


ck("기본 상수 500", eqgui.DEFAULT_ONE_R == 500.0)
ck("옛 기본 600 보존", eqgui._LEGACY_ONE_R == 600.0)
ck("새 계좌 기본 = 500", eqgui._new_acct()["one_r"] == 500.0)
ck("명시한 1R은 그대로", eqgui._new_acct(750.0)["one_r"] == 750.0)
ck("빈 값 → 500(새 계좌)", eqgui._new_acct(0)["one_r"] == 500.0)
src = inspect.getsource(eqgui)
ck("새 계좌 만들 때 600 리터럴 없음", "_new_acct(600" not in src and "one_r or 600" not in src)
assert eqgui.CFG_PATH.startswith(_H), eqgui.CFG_PATH
_d0 = eqgui._load()
_r0 = {a: [x["one_r"] for x in ((_d0.get("assets") or {}).get(a) or {}).get("accounts", [])] for a in ("NQ", "GC", "BTC")}
ck("새 설치 _load() 첫 계좌 1R = 500(리뷰 P1-1)", all(v and all(r == 500.0 for r in v) for v in _r0.values()), _r0)
ck("버전 2026.10.12d", eqgui.App._APP_VER == "2026.10.12d")
print("FAIL %d" % len(FAIL) if FAIL else "ALL PASS")
sys.exit(1 if FAIL else 0)
