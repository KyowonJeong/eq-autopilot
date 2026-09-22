#!/usr/bin/env python3
# EQ Autopilot - 포트폴리오 비중 오프라인 테스트 (2026-09-22)
# =========================
# 왜: 2026-09-22 대표 실측 로그에서 **설정이 완전히 같은 NQ와 GC가 25 : 28**로 나왔다
#   (내역 NQ 7계좌 $2,500 / GC 7계좌 $2,800). 원인은 비중 계산이 실제 1R 원장의 최신값을
#   쓰고 없으면 설정값으로 폴백한 것 - 프롭 계좌는 진입 경로가 **설정값이 아니라 방패값**을
#   쓰므로, 이미 거래한 자산은 $300(원장) 아직 안 거래한 자산은 $600(설정)으로 잡혔다.
#   같은 계좌가 자산마다 다른 무게를 가지면 회원이 보는 비중이 자기 계좌와 무관해진다.
#
# ⚠️하네스 함정: 메서드만 떼어 exec하면 모듈 전역(_as_float)을 못 봐 try/except에 삼켜지고
#   **거짓 통과**가 난다. ns = dict(eqgui.__dict__)로 전역을 넘긴다
#   (같은 함정이 test_stop_guard_offline.py 머리말에도 적혀 있다).
#
# 사용: python executor/test_portfolio_weights_offline.py   (0=통과, 1=실패)
# =========================
import ast
import io
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

NAMES = ("_planned_one_r", "_portfolio_risk_weights")
FAIL = []


def _build():
    src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"),
                  encoding="utf-8").read()
    segs = {n.name: ast.get_source_segment(src, n) for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name in NAMES}
    missing = [n for n in NAMES if n not in segs]
    if missing:
        raise AssertionError(f"메서드가 사라졌다: {missing}")
    ns = dict(eqgui.__dict__)
    exec("class T:\n    pass\n"
         + "\n".join(textwrap.indent(segs[n], "    ") for n in NAMES), ns)
    return ns["T"]


def _acct(one_r=600.0, prop=None):
    a = {"id": "", "one_r": float(one_r), "on": True, "prop": dict(prop or {"on": False}),
         "pct": {"on": False}, "manual": False}
    return a


def _app(cls, accts):
    t = cls()
    t._active_accts = lambda a: accts.get(a, [])
    t.log = lambda *a, **k: None
    return t


def ck(name, cond, info=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  :: " + info if info else ""))
    if not cond:
        FAIL.append(name)


def main():
    T = _build()
    funded = {"on": True, "type": "funded", "r_steady": 300.0, "r_test": 1200.0, "payouts": 1}

    # ① 대표가 본 그 장면: 같은 프롭 계좌가 NQ·GC 양쪽에 똑같이 설정돼 있다.
    #    명목 one_r($600)이 아니라 방패값($300)으로 잡혀야 하고, 두 자산이 같아야 한다.
    w = _app(T, {"NQ": [_acct(600, funded)] * 3,
                 "GC": [_acct(600, funded)] * 3,
                 "BTC": [_acct(50)] * 2})._portfolio_risk_weights()
    ck("설정 같으면 NQ=GC", w and w["NQ"] == w["GC"], f"{w}")
    ck("프롭은 방패값", w and abs(w["NQ"] / w["BTC"] - 900.0 / 100.0) < 1e-9, f"{w}")

    # ② 5발 완료 계좌는 진입을 안 한다(_prop_one_r이 None) - 비중에서도 빠져야 한다.
    done = dict(funded, payouts=5)
    w2 = _app(T, {"NQ": [_acct(600, funded), _acct(600, done)],
                  "GC": [_acct(600, funded)]})._portfolio_risk_weights()
    ck("5발완료 제외", w2 and w2["NQ"] == w2["GC"], f"{w2}")

    # ③ 프롭이 아닌 계좌는 회원이 확정한 설정값 그대로(2026-08-13 잔고 연동 폐지).
    w3 = _app(T, {"NQ": [_acct(800)], "GC": [_acct(400)]})._portfolio_risk_weights()
    ck("일반 계좌=설정값", w3 and abs(w3["NQ"] - 2.0) < 1e-9 and w3["GC"] == 1.0, f"{w3}")

    # ④ 테스트기·라이브 초기도 진입 경로와 같은 값을 써야 한다.
    t = _build()()
    t.log = lambda *a, **k: None
    ck("테스트기 r_test", t._planned_one_r(_acct(600, {"on": True, "type": "test",
                                                      "r_test": 1200.0})) == 1200.0)
    ck("라이브 초기 r_live", t._planned_one_r(_acct(600, {"on": True, "type": "live",
                                                         "r_live": 100.0})) == 100.0)

    # ⑤ 자산이 하나뿐이면 '비중'이 성립하지 않는다 → None(종전 규약 유지).
    ck("단일 자산=None", _app(T, {"NQ": [_acct(600)]})._portfolio_risk_weights() is None)

    print("\n" + ("전부 통과 - 7개 검사" if not FAIL else f"실패 {len(FAIL)}건: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
