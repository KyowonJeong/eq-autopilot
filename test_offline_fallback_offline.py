#!/usr/bin/env python3
# EQ Autopilot - 서버 불통 폴백·킬스위치 오프라인 테스트(대표 2026-09-30 "서버 꺼지면 … 무조건 죽여" / "아 다 죽여"). 0=통과
# 킬스위치(서버 정상 응답 + force_dry_run) = 진입·청산 모두 dry-run + 사실만 알림.
# 서버 불통(유예 넘김)·명시 거부 = 새 진입 멈춤, 무장 계좌 마감 청산은 로컬 일정대로 LIVE, BTC는 조건부 출구 없이 그 블록에서.
# 서버가 킬스위치를 켠 뒤 불통이 되면 폴백하지 않는다(멈춘 걸 되살리지 않음).
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


def app(gate, killed=False):
    me = MagicMock()
    me.lang = "ko"
    me._gate = gate
    me._connected = True
    me._killed = killed
    me._auto_on = True
    me._auto_accts = {("NQ", 0): [{"asset": "NQ", "live": True}]}
    me._sig_on = False
    me._watch_only = False
    me._fallback_mode = None
    me._kill_noted = False
    me._live_ctx = {}
    me._fallback_kind = lambda g=None: eqgui.App._fallback_kind(me, g)
    me._live_now = lambda f: eqgui.App._live_now(me, f)
    me._close_mode = lambda j: eqgui.App._close_mode(me, j)
    return me


def logs(me):
    return " ".join(str(c[0][0]) for c in me.log.call_args_list)


job_nq = {"asset": "NQ", "live": True}
OFF = {"ok": False, "offline": True, "reason": "heartbeat error"}
REV = {"ok": False, "revoked": True, "reason": "expired"}
KILL = {"ok": True, "enabled": True, "force_dry_run": True, "caps": {"use": True, "autoentry": True}}
NORMAL = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True}}
NOTOK = {"ok": False, "reason": "no token"}

me = app(OFF); eqgui.App._apply_gating(me)
ck("불통: 자동 청산 계속(해제 안 함)", me._auto_on is True and me._auto_accts, me._auto_accts)
ck("불통: 폴백 모드 offline + 사실 문구", me._fallback_mode == "offline" and "예정 시각에 포지션을 닫습니다" in logs(me), logs(me))
ck("불통: 청산은 LIVE, BTC 조건부 출구 건너뜀", eqgui.App._close_mode(me, job_nq) == (True, True))
ck("불통: 회원에게 무엇을 하라는 말 없음", "닫아 주" not in logs(me) and "하세요" not in logs(me), logs(me))

me = app(REV); eqgui.App._apply_gating(me)
ck("명시 거부·만료: 앱이 연 포지션 마감 청산은 계속", me._auto_on is True and me._fallback_mode == "revoked")

me = app(KILL); eqgui.App._apply_gating(me)
ck("킬스위치: 청산 dry-run", eqgui.App._close_mode(me, job_nq) == (False, False))
ck("킬스위치: 사실 문구 한 번", "진입도 청산도 하지 않습니다" in logs(me) and me._kill_noted is True, logs(me))
ck("킬스위치: '직접 닫으라'는 말 없음", "닫아 주" not in logs(me), logs(me))

me = app(OFF, killed=True); eqgui.App._apply_gating(me)
ck("킬스위치 켠 뒤 불통: 폴백 안 함(멈춘 걸 되살리지 않음)", me._auto_on is False and eqgui.App._fallback_kind(me) is None)

me = app(NOTOK); eqgui.App._apply_gating(me)
ck("토큰 없음: 종전처럼 fail-closed", me._auto_on is False)

me = app(NORMAL); eqgui.App._apply_gating(me)
ck("평소: 청산 LIVE·BTC 조건부 출구 유지", eqgui.App._close_mode(me, job_nq) == (True, False) and me._fallback_mode is None)

me = app(OFF); eqgui.App._apply_gating(me); me._gate = NORMAL; eqgui.App._apply_gating(me)
ck("복구: 폴백 해제 + 한 줄", me._fallback_mode is None and "복구" in logs(me), logs(me))
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
