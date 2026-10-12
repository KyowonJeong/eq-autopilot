#!/usr/bin/env python3
# EQ Autopilot - 복사 버튼 부재 래칫(대표 2026-10-07 "복사 버튼을 다 없앱니다"). 0=통과
# 브로커 연결을 NQ·GC가 같이 쓰게 되면서(_creds_of 별칭) 세 복사 버튼을 없앴다:
#   [NQ 설정 복사하기](_copy_nq_settings), [다른 자산의 브로커 키 전부 가져오기](_pull_broker_keys), [계좌 설정 복사 ←](_copy_acct_setup).
# 10/7 회원 키 섞임 세 경로 가운데 둘이 이 버튼이었다. 되살리면 이 시험이 실패한다.
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
for fn in ("_copy_nq_settings", "_pull_broker_keys", "_copy_acct_setup"):
    ck(f"{fn} 함수 없음", not hasattr(eqgui.App, fn))
    ck(f"{fn} 참조 없음(버튼 command 포함)", not re.search(r"\b" + fn + r"\b", SRC), fn)
for lbl in ("NQ 설정 복사하기", "Copy NQ settings", "다른 자산의 브로커 키 전부 가져오기",
            "Pull all broker keys from other assets", "계좌 설정 복사 ←", "Copy account setup ←"):
    ck(f"버튼 문구 없음: {lbl}", f'"{lbl}"' not in SRC and f"'{lbl}'" not in SRC)

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
