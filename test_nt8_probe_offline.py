#!/usr/bin/env python3
# EQ Autopilot - NT8 브리지 상태 확인(생존 핑 n8) 오프라인 테스트(2026-09-28, 대표 "둘 다 넣어")
# 확인: NT8로 무장하면 1분 확인이 ok/down을 기록하고, down이 이어지면 since는 첫 실패 시각을 유지하고,
#       복구되면 ok, NT8 무장이 없으면 off. 상태 변화가 핑 서명에 들어가 스로틀을 통과한다.
# 사용: python executor/test_nt8_probe_offline.py   (0=통과, 1=실패)
import os, sys, time, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
fails = []


def chk(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{d}]"))
    if not c:
        fails.append(n)


state = {"ok": False}


class _B:
    def healthcheck(self):
        if not state["ok"]:
            raise RuntimeError("bridge heartbeat stale")


eqgui._build_broker = lambda *a, **k: _B()
eqgui.threading.Thread = lambda target=None, args=(), daemon=None: types.SimpleNamespace(start=lambda: target(*args))
app = types.SimpleNamespace(_sig_accts={("NQ", 0): {"broker": "nt8", "f1": "tok", "f2": "", "f3": "8377", "acct": "SIM1"}},
                            _auto_accts={}, root=types.SimpleNamespace(after=lambda *a, **k: None), _nt8_state=None)
app._nt8_probe_run = lambda cfg: eqgui.App._nt8_probe_run(app, cfg)
chk("첫 확인 전 = 미확인(None)", eqgui.App._nt8_ping_state(app) is None)
eqgui.App._nt8_probe_tick(app)
s1 = dict(app._nt8_state)
chk("끊김 → down + since", s1.get("st") == "down" and s1.get("since"), s1)
time.sleep(1.1)
eqgui.App._nt8_probe_tick(app)
s2 = dict(app._nt8_state)
chk("계속 끊김 → since는 첫 실패 시각 유지", s2.get("st") == "down" and s2.get("since") == s1.get("since"), (s1, s2))
state["ok"] = True
eqgui.App._nt8_probe_tick(app)
chk("복구 → ok(since 없음)", app._nt8_state.get("st") == "ok" and "since" not in app._nt8_state, app._nt8_state)
app._sig_accts = {("BTC", 0): {"broker": "bybit"}}
eqgui.App._nt8_probe_tick(app)
chk("NT8 무장 없음 → off", app._nt8_state == {"st": "off"}, app._nt8_state)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("생존 핑에 n8 필드", '"n8": self._nt8_ping_state()' in src)
chk("상태 변화가 핑 서명에 들어감(스로틀 통과)", '_nt8_state", None) or {}).get("st")' in src)
chk("시작 로그: 체크 꺼진 자산·사전점검 실패", "실행 자산 체크가 꺼져 있어 이번 시작에서 빠졌습니다" in src
    and "라이브 시작 중단 - 사전점검" in src)
# 건너뜀 보고(R47 P1-#6): entry_skip + 사유 코드, 같은 자산·사유 10분에 한 번
_ev = []
app2 = types.SimpleNamespace(_send_ev=lambda kind, asset, **k: _ev.append((kind, asset, k.get("why"))))
eqgui.App._entry_skip_ev(app2, "NQ", "size_cap")
eqgui.App._entry_skip_ev(app2, "NQ", "size_cap")
eqgui.App._entry_skip_ev(app2, "NQ", "adverse_move")
chk("건너뜀 보고: entry_skip + 사유, 같은 사유 중복 없음", _ev == [("entry_skip", "NQ", "size_cap"), ("entry_skip", "NQ", "adverse_move")], _ev)
chk("건너뜀 보고 호출 7곳(사이징·과대 사이징·불리 이동 2·프롭 완료·계좌 없음·늦은 신호)", src.count("_entry_skip_ev(") >= 8)
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
