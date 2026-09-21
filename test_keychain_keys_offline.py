#!/usr/bin/env python3
# EQ Autopilot - 키체인 키 문자열 고정 테스트 (2026-09-21)
# =========================
# 왜: 멀티 로그인(같은 브로커에 자격증명 두 벌)을 넣으면 _kc_key에 인자가 하나 늘어난다.
#   그때 **첫 로그인(c1)이 지금과 똑같은 키 문자열**을 내야 한다 - 어긋나면 업데이트하는
#   순간 모든 회원의 저장된 API 키가 **사라진 것처럼** 보인다(키는 남아 있는데 못 찾는다).
#   이 테스트가 오늘의 키 문자열을 골든으로 박아 그 사고를 구조적으로 막는다.
#
# ⛔골든 값을 '고쳐서' 통과시키지 말 것. 이 값이 바뀐다는 것은 회원 자격증명이 끊긴다는
#   뜻이다. 바꿔야 할 진짜 이유가 있다면 **마이그레이션 코드**를 먼저 쓰고, 이 파일에는
#   그 이유와 마이그레이션 경로를 주석으로 남긴 뒤에 갱신한다.
#
# 사용: python executor/test_keychain_keys_offline.py   (0=통과, 1=실패)
# =========================
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

# 2026-09-21 현재 값. 형식 = f"eq:{asset}:{broker}:{field}", 서비스명 = "EQAutopilot".
GOLDEN_SERVICE = "EQAutopilot"
GOLDEN = {
    ("NQ", "projectx", "f1"): "eq:NQ:projectx:f1",
    ("NQ", "nt8", "f1"): "eq:NQ:nt8:f1",
    ("NQ", "tradovate", "f1"): "eq:NQ:tradovate:f1",
    ("NQ", "ibkr", "f1"): "eq:NQ:ibkr:f1",
    ("GC", "projectx", "f1"): "eq:GC:projectx:f1",
    ("GC", "nt8", "f1"): "eq:GC:nt8:f1",
    ("BTC", "bybit", "f1"): "eq:BTC:bybit:f1",
    ("BTC", "bitget", "f1"): "eq:BTC:bitget:f1",
    ("BTC", "bitget", "f3"): "eq:BTC:bitget:f3",
}


def main():
    fails = []

    def ok(name, cond, got):
        print(f"{'PASS' if cond else 'FAIL'}  {name}  :: {got}")
        if not cond:
            fails.append(name)

    ok("서비스명 고정", eqgui.KC_SERVICE == GOLDEN_SERVICE, eqgui.KC_SERVICE)

    for (a, b, f), want in GOLDEN.items():
        try:
            got = eqgui._kc_key(a, b, f)                 # 현행 시그니처
        except TypeError:
            got = eqgui._kc_key(a, b, f, "c1")           # 멀티 로그인 뒤: c1이 같아야 한다
        ok(f"{a}/{b}/{f}", got == want, got)

    # 멀티 로그인이 들어온 뒤에는 둘째 로그인이 **다른** 키를 내야 한다(같으면 덮어쓴다).
    try:
        _c1 = eqgui._kc_key("NQ", "projectx", "f1", "c1")
        _c2 = eqgui._kc_key("NQ", "projectx", "f1", "c2")
        ok("c2는 c1과 달라야", _c1 != _c2, f"{_c1} vs {_c2}")
    except TypeError:
        print("SKIP  c2 분리 - 아직 멀티 로그인 미구현(현행 시그니처)")

    print(f"\n{'전부 통과' if not fails else 'FAIL ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
