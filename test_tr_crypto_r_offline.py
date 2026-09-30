#!/usr/bin/env python3
# 트랙레코드 R 분모 - 크립토 계좌 1R 폴백 수리 오프라인 테스트(2026-09-29, 9/20 BTC 3.19R 사고)
# 수리 전: 크립토(계좌 ID 없음)는 설정 1R 맵에 안 들어가 R 원장이 비면 기본 $600으로 나눴다.
# 수리 뒤: 브로커 이름으로 그 계좌 설정 1R을 찾고, 그래도 없으면 행에 r_est 표시 + 로그 경고.
# 그 자리 코드는 수집 스레드 안의 인라인이라, 같은 규칙을 소스에서 확인하고 규칙을 재현해 값을 본다.
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
fails = []


def chk(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{d}]"))
    if not c:
        fails.append(n)


chk("크립토: 브로커 이름으로 설정 1R 등록", 'if not c.get("acct") and c.get("broker") and c["broker"] not in _r_by_acct:' in src)
chk("조회는 _acct_id(= 계좌 ID 또는 브로커)로", 'f["_one_r"] = _r_by_acct.get(f["_acct_id"], _R_FALLBACK)' in src)
chk("기본값 폴백 행에 r_est", '"r_est": True' in src and 'e["r_est"] = True' in src)
chk("폴백 경고 로그", "R 추정" in src)
# 규칙 재현: 1R $50 두 크립토 계좌, R 원장 비어 있음, 손익 $4,400
credlist = [{"broker": "bybit", "acct": "", "one_r": 50.0}, {"broker": "bitget", "acct": "", "one_r": 50.0}]
_r_by_acct = {c["acct"]: c["one_r"] for c in credlist if c.get("acct")}
for c in credlist:
    if not c.get("acct") and c.get("broker") and c["broker"] not in _r_by_acct:
        _r_by_acct[c["broker"]] = c["one_r"]
fills = [{"acct": "", "broker": "bybit", "pnl": 2200.0}, {"acct": "", "broker": "bitget", "pnl": 2200.0}]
den = sum(_r_by_acct.get(f["acct"] or f["broker"], 600.0) for f in fills)
r = sum(f["pnl"] for f in fills) / den
chk("재현: 분모 $100 → r 44(수리 전 규칙이면 $1,200 → 3.67)", abs(r - 44.0) < 1e-9, (den, r))
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
