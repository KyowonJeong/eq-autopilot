#!/usr/bin/env python3
# EQ Autopilot - 배포 빌드가 열리지 않게 만드는 문법 가드 (2026-09-22)
# =========================
# 왜: 앱 빌드 파이썬은 **3.9**다(Tk 8.6 제약 - 3.13은 Tk 9.0이라 못 쓴다). 그래서 3.10+
#   문법을 쓰면 py_compile은 통과해도(개발 파이썬은 3.13) **배포 빌드가 기동 즉시 죽는다**.
#   2026-08-31에 한 번 겪고 _idle_days 위에 경고를 적어 뒀는데, 2026-09-22에 또 밟았다
#   (`def _portfolio_risk_weights(self, rledger) -> dict | None:` → TypeError로 앱이 안 열림).
#   경고 주석은 두 번 다 막지 못했다 - 그래서 테스트로 박는다.
#
# 사용: python executor/test_build_guards_offline.py   (0=통과, 1=실패)
#   ⚠️**3.9 인터프리터로 돌려야** 의미가 있다(빌드 venv). 3.13으로 돌리면 통과해 버린다.
#   빌드 전 검증에 포함할 것: <buildvenv>/bin/python executor/test_build_guards_offline.py
# =========================
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = ["eqgui.py"]


def main():
    fails = []
    if sys.version_info[:2] != (3, 9):
        print(f"⚠️  지금 파이썬 {sys.version_info.major}.{sys.version_info.minor} - "
              f"빌드 파이썬(3.9)으로 돌려야 진짜 검사가 된다. 문자열 스캔만 수행한다.")

    for name in TARGETS:
        p = os.path.join(HERE, name)
        src = open(p, encoding="utf-8").read()

        # ① 실제 컴파일 - 3.9에서 돌면 PEP 604·match문 등을 전부 잡는다
        try:
            compile(src, name, "exec")
            print(f"PASS  {name} 컴파일")
        except SyntaxError as e:
            print(f"FAIL  {name} 컴파일 - {e}")
            fails.append(f"{name}:compile")

        # ② 어떤 파이썬으로 돌리든 걸리는 문자열 스캔(PEP 604 어노테이션)
        bad = []
        for i, line in enumerate(src.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"->\s*[A-Za-z_\[\]\.\"']+\s*\|\s*", line):
                bad.append(f"{i}: {line.strip()[:80]}")
            if re.search(r":\s*[A-Za-z_\[\]\.]+\s*\|\s*None\s*=", line):
                bad.append(f"{i}: {line.strip()[:80]}")
        if bad:
            print(f"FAIL  {name} PEP 604 어노테이션 {len(bad)}건")
            for b in bad[:5]:
                print("      ", b)
            fails.append(f"{name}:pep604")
        else:
            print(f"PASS  {name} PEP 604 없음")

    print(f"\n{'전부 통과' if not fails else 'FAIL ' + str(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
