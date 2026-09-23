#!/usr/bin/env python3
# EQ Autopilot - 백업 5분봉 전송(v2026.09.23e) 오프라인 테스트: 브리지 상태·닫힌 봉 선별·중복 방지·오너 게이트.
# 사용: python executor/test_bar_backup_offline.py   (0=통과, 1=실패)
import os, sys, time, types, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
from eqexec.broker.nt8 import NT8Broker, _BridgeState  # noqa: E402
fails = []
def chk(n, c):
    print(("PASS  " if c else "FAIL  ") + n)
    if not c: fails.append(n)
# .NET 시각 파싱
e = eqgui._bar_end_epoch({"t": "2026-09-23T16:05:00.0000000+02:00"})
chk(".NET 7자리 소수초+오프셋 파싱", abs(e - 1790172300.0) < 1)
chk("오프셋 없으면 off 분 사용", abs(eqgui._bar_end_epoch({"t": "2026-09-23T16:05:00", "off": 120}) - 1790172300.0) < 1)
chk("오프셋도 off도 없으면 None", eqgui._bar_end_epoch({"t": "2026-09-23T16:05:00"}) is None)
# 브리지 상태 헬퍼
st = _BridgeState(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tmp_nt8_journal_test.jsonl") if False else os.devnull)
NT8Broker._BRIDGES = {8377: (st, None, "tok")}
chk("bars_set_wanted → 브리지 1", NT8Broker.bars_set_wanted(["NQ 12-26", "GC 12-26"]) == 1 and st.bars_wanted == ["NQ 12-26", "GC 12-26"])
now = time.time()
def iso(ep):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ep, _dt.timezone(_dt.timedelta(hours=2))).isoformat()
st.bars["NQ 12-26"] = {"ts": now, "period": 5, "bars": [
    {"t": iso(now - 600), "off": 120, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3},
    {"t": iso(now - 300), "off": 120, "o": 1.5, "h": 2.5, "l": 1, "c": 2, "v": 4},
    {"t": iso(now + 200), "off": 120, "o": 2, "h": 2.2, "l": 1.9, "c": 2.1, "v": 1}]}   # 형성 중(끝 시각 미래)
st.bars["GC 12-26"] = {"ts": now - 500, "period": 5, "bars": [{"t": iso(now - 300), "off": 120, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}  # 푸시 멎음
chk("bars_take 복사본", set(NT8Broker.bars_take()) == {"NQ 12-26", "GC 12-26"})
# 앱 틱: 즉시 실행 스레드 + POST 캡처
posts = []
class _T:
    def __init__(self, target=None, daemon=None, **k): self._t = target
    def start(self): self._t and self._t()
eqgui.threading.Thread = _T
sys.modules["requests"] = types.SimpleNamespace(post=lambda url, **k: posts.append((url, k.get("json"))) or types.SimpleNamespace(ok=True, text="br:ok:2/2"))
app = types.SimpleNamespace(_gate={"tier": "admin"}, _token="tok", lang="ko", log=lambda *a, **k: None,
                            root=types.SimpleNamespace(after=lambda ms, fn=None, *a: None))
app._bar_backup_tick = lambda: None          # 틱 첫 줄이 자기 재예약에 이 속성을 쓴다(가짜 self)
eqgui.App._bar_backup_tick(app)
chk("오너: NQ 닫힌 봉 2개만 전송(형성 봉·푸시 멎은 GC 제외)", len(posts) == 1 and posts[0][1]["inst"] == "NQ" and len(posts[0][1]["bars"]) == 2 and posts[0][1]["src"] == "nt8" and posts[0][1]["t"] == "tok")
chk("전송 봉 필드", set(posts[0][1]["bars"][0]) == {"t_end", "off", "o", "h", "l", "c", "v"})
posts.clear(); eqgui.App._bar_backup_tick(app)
chk("두 번째 틱: 이미 보낸 봉은 재전송 안 함", posts == [])
st.bars["NQ 12-26"]["bars"].append({"t": iso(now - 60), "off": 120, "o": 2, "h": 3, "l": 2, "c": 2.5, "v": 2}); st.bars["NQ 12-26"]["ts"] = time.time()
posts.clear(); eqgui.App._bar_backup_tick(app)
chk("새로 닫힌 봉 1개만 추가 전송", len(posts) == 1 and len(posts[0][1]["bars"]) == 1)
posts.clear(); app._gate = {"tier": "royal"}; app._bar_backup_sent = {}
eqgui.App._bar_backup_tick(app)
chk("회원(royal) 기기는 아무것도 안 보냄", posts == [])
posts.clear(); app._gate = {"tier": "admin"}; NT8Broker._BRIDGES = {}
eqgui.App._bar_backup_tick(app)
chk("NT8 브리지 없으면 안 보냄", posts == [])
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("기동 배선·버전 23e 이상", "root.after(150 * 1000, self._bar_backup_tick)" in src and eqgui.App._APP_VER >= "2026.09.23e")
cs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nt8_addon", "EQAutopilotBridge.cs"), encoding="utf-8").read()
chk("애드온: bars_wanted 조회·/v1/bars 푸시·BarsRequest·버전", all(k in cs for k in ("/v1/bars_wanted", "PostAsync(\"/v1/bars\"", "new BarsRequest(instr, 8)", "BridgeVer = \"2026.09.23a\"", "using NinjaTrader.Data;")))
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
