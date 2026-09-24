#!/usr/bin/env python3
# EQ Autopilot - 월 1회 실행 확인(30일 재확인) 폐지 확인(대표 2026-09-24) 오프라인 테스트
# 남는 것: 첫 실행 동의(eqexec/consent.py). 걷은 것: _consent_ask/_consent_left/_consent_summary, [라이브 시작] 게이트,
# 신호 루프의 '30일 확인 만료 → 새 진입 보류'. 사용: python executor/test_consent_removed_offline.py (0=통과)
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
fails = []
def chk(n, c):
    print(("PASS  " if c else "FAIL  ") + n)
    if not c: fails.append(n)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("_consent_ask/_consent_left/_consent_summary 없음", not any(hasattr(eqgui.App, n) for n in ("_consent_ask", "_consent_left", "_consent_summary", "CONSENT_DAYS")))
i = src.find("    def _master_start(self):"); body = src[i:src.find("\n    def ", i + 10)]
chk("_master_start에 30일 재확인 게이트 없음(첫 실행 동의 _consent_ok는 그대로)", "_consent_ask" not in body and "_consent_left" not in body
    and "self._consent_ok()" in body and "live_dry.set(0)" in body)
chk("신호 루프에 '30일 실행 확인 만료' 보류 없음", "30일 실행 확인이 만료" not in src and "30-day confirmation expired" not in src)
chk("첫 실행 동의(eqexec/consent.py)는 남음", os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqexec", "consent.py")))
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
