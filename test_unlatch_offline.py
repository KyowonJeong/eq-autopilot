#!/usr/bin/env python3
# EQ Autopilot - 가동 중 승급·체험으로 autoentry가 열리면 확인 모드 래치(_watch_only)를 푸는지(v2026.09.23f) 오프라인 테스트
# 왜(2026-09-23 R44 P0-#1): Operator로 무장한 앱은 _watch_only=True로 래치되고, royal 하트비트가 와도 풀리는 분기가
# 없어 등급 라벨만 Autopilot으로 바뀐 채 주문을 내지 않았다. 웹 성공 문구·DM은 '자동'이라 말했다.
# 사용: python executor/test_unlatch_offline.py   (0=통과, 1=실패)
import os, sys
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
import subprocess  # noqa: E402
import tkinter.messagebox as _mb  # noqa: E402

fails = []


def chk(n, c):
    print(("PASS  " if c else "FAIL  ") + n)
    if not c:
        fails.append(n)


subprocess.Popen = lambda *a, **k: None          # macOS 알림 억제
shown = []
_mb.showinfo = lambda t, m: shown.append(m)
logs = []


def _app(lang="ko"):
    app = MagicMock()
    app._gate = {"ok": True, "enabled": True, "tier": "royal", "force_dry_run": False,
                 "caps": {"use": True, "autoentry": True, "connect": True}}
    app._connected = True
    app._broker_name = "nt8"
    app.lang = lang
    app._token = "tok"
    app._sig_on = True
    app._auto_on = True
    app._watch_only = True                       # Operator로 무장한 상태의 래치
    app._live_ctx = {"live": True, "perm_auto": False, "perm_use": True}
    app._sig_accts = {("NQ", 0): {"one_r": 600.0, "label": "A", "prop": {}, "pct": {}, "manual": False},
                      ("GC", 0): {"one_r": 0.0, "label": "B", "prop": {}, "pct": {}, "manual": False},
                      ("GC", 1): {"one_r": 0.0, "label": "C", "prop": {"on": True}, "pct": {}, "manual": False},
                      ("BTC", 0): {"one_r": 0.0, "label": "D", "prop": {}, "pct": {}, "manual": True}}
    app._auto_accts = {}
    app.log = lambda m, *a, **k: logs.append(str(m))
    app.root.after = lambda ms, fn: fn()         # 즉시 실행
    app._notify_unlatched = lambda no_r=None: eqgui.App._notify_unlatched(app, no_r)
    return app


app = _app()
eqgui.App._apply_gating(app)
chk("승급 하트비트 → 래치 해제", app._watch_only is False)
chk("_live_ctx.perm_auto 갱신(가동 중 자산 추가 무장도 자동 진입)", app._live_ctx["perm_auto"] is True)
chk("로그 줄 '자동 진입 권한 반영'(DM·웹 문구가 이름으로 가리키는 줄)", any("자동 진입 권한 반영" in m for m in logs))
_w = [m for m in logs if "1R 미설정" in m]
chk("1R 미설정 계좌만 경고(prop·manual 계좌 제외)", _w and "GC B" in _w[0] and "GC C" not in _w[0] and "BTC D" not in _w[0])
chk("안내창 1회", len(shown) == 1 and "자동 진입 권한이 반영" in shown[0] and "GC B" in shown[0])
logs.clear(); shown.clear()
eqgui.App._apply_gating(app)
chk("같은 상태 재호출 = 반복 알림 없음", not shown and not any("권한 반영" in m for m in logs))
app._gate["caps"]["autoentry"] = False
eqgui.App._apply_gating(app)
chk("강등 하트비트 → 확인 모드 복귀(종전 분기 그대로)", app._watch_only is True and not shown)
app._gate["caps"]["autoentry"] = True
eqgui.App._apply_gating(app)
chk("재승급 → 다시 해제 + 알림 1회", app._watch_only is False and len(shown) == 1)
app2 = _app()
app2._sig_on = False
shown.clear(); logs.clear()
eqgui.App._apply_gating(app2)
chk("신호 대기 중이 아니면 래치를 건드리지 않음(무장 안 한 앱)", app2._watch_only is True and not shown)
app3 = _app()
app3._gate["enabled"] = False
shown.clear(); logs.clear()
eqgui.App._apply_gating(app3)
chk("마스터 OFF면 해제 아님(fail-closed 분기 우선)", app3._watch_only is True and not shown)
app4 = _app(lang="en")
shown.clear(); logs.clear()
eqgui.App._apply_gating(app4)
chk("EN 로그 줄 'Auto-entry permission applied'", any("Auto-entry permission applied" in m for m in logs)
    and shown and "Auto-entry permission applied" in shown[0])
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("정적: 해제 분기가 강등 분기 뒤 elif로 존재", "perm_auto and getattr(self, \"_watch_only\", False)" in src)
chk("버전 23f 이상", eqgui.App._APP_VER >= "2026.09.23f")
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
