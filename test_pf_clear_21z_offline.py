# EQ Autopilot 2026.10.21z 오프라인 시험(2026-11-08, 대표 "정상화 되면 바로 없어져이지"). 0 = 통과.
# 연결 테스트나 라이브 켜기에서 연결이 확인되면 preflight_ok(win=0)를 보내 서버 사전점검 실패 배너를 바로 지운다. 같은 (자산, 브로커)는 10분에 한 번.
import os, sys, types, inspect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EQ_NO_ALERT", "1")
import eqgui as E
fails = []
def ck(n, ok, got=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"  got={got!r}"))
    if not ok: fails.append(n)
ck("0 앱 버전 2026.10.21z 이상", E.App._APP_VER >= "2026.10.21z", E.App._APP_VER)
sent = []
me = types.SimpleNamespace(_send_ev=lambda kind, asset, **kw: sent.append((kind, asset, kw)))
ck("1 첫 확인 = 보냄", E.App._pf_clear(me, "BTC", "okx") is True and sent[-1] == ("preflight_ok", "BTC", {"broker": "okx", "win": 0, "warns": 0}), sent)
ck("2 10분 안 같은 자산, 브로커 = 안 보냄", E.App._pf_clear(me, "BTC", "okx") is False and len(sent) == 1, sent)
ck("3 다른 브로커는 따로", E.App._pf_clear(me, "BTC", "bybit") is True and sent[-1][2]["broker"] == "bybit", sent)
me._pf_clear_at[("BTC", "okx")] -= 601
ck("4 10분 지나면 다시 보냄", E.App._pf_clear(me, "BTC", "okx") is True and len(sent) == 3, sent)
ck("5 빈 자산이나 브로커 = 안 보냄", E.App._pf_clear(me, "", "okx") is False and E.App._pf_clear(me, "BTC", "") is False and len(sent) == 3)
bad = types.SimpleNamespace(_send_ev=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net")))
ck("6 보내기 실패해도 예외 안 냄", E.App._pf_clear(bad, "GC", "projectx") is False)
src_cc = inspect.getsource(E.App._conn_check)
ck("7 무음 연결 확인 성공 뒤에 부름", "self._conn_by_broker[bk] = True\n            self._pf_clear(asset, bk)" in src_cc)
src_hc = inspect.getsource(E.App.healthcheck)
ck("8 연결 테스트 버튼 성공 뒤에 부름", "self._pf_clear(_ha, _hb)" in src_hc
   and src_hc.index("self._pf_clear(_ha, _hb)") > src_hc.index("self._connected = True"))
ck("9 실패 경로에서는 안 부름", src_hc.index("self._pf_clear") > src_hc.index("return\n"))
print("FAIL", len(fails)) if fails else print("ALL PASS")
sys.exit(1 if fails else 0)
