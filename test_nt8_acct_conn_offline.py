# NT8 healthcheck가 계좌 연결 끊김도 잡는지(대표 2026-11-02 "닌자 트레이더 끊겨도 워닝 안 와").
# 애드온은 접속이 끊긴 계좌를 accounts에서 뺀다 - 하트비트가 살아 있어도 정해진 계좌가 없으면 예외.
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eqexec.broker import nt8

FAIL = []
def ck(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f" - {extra}" if not ok and extra else ""))
    if not ok:
        FAIL.append(name)

class _St:
    def __init__(self, names):
        self.lock = threading.Lock()
        self.last_state_ts = time.time()
        self.last_state = {"accounts": [{"name": n} for n in names], "positions": []}

def mk(accounts, names):
    b = nt8.NT8Broker.__new__(nt8.NT8Broker)
    b.accounts = accounts
    b._state = _St(names)
    b.authenticate = lambda: None
    return b

def hc(b):
    try:
        b.healthcheck(); return None
    except Exception as e:
        return str(e)

ck("계좌 연결됨 = 정상", hc(mk(["LFE-150-01"], ["LFE-150-01", "Sim101"])) is None)
ck("대소문자, 공백 무시", hc(mk([" lfe-150-01 "], ["LFE-150-01"])) is None)
e = hc(mk(["LFE-150-01", "LFE-150-02"], ["LFE-150-01", "Sim101"]))
ck("한 계좌 끊김 = 예외, 그 계좌 이름", e is not None and "LFE-150-02" in e and "LFE-150-01" not in e, e)
e = hc(mk(["LFE-150-01"], []))
ck("모든 접속 끊김(하트비트는 삶) = 예외", e is not None and "끊김" in (e or ""), e)
ck("계좌를 안 정한 인스턴스(연결 테스트) = 종전대로 하트비트만", hc(mk([], [])) is None)
b = mk(["A"], ["A"]); b._state.last_state_ts = time.time() - 60
nt8._HEARTBEAT_MAX_S, _keep = 5.0, nt8._HEARTBEAT_MAX_S
_t0 = time.time(); e = hc(b)
ck("하트비트 낡음 = 종전 애드온 미접속 예외", e is not None and "애드온 미접속" in e, e)

# ── 1분 확인의 회원 DM(대표 2026-11-02 "닌자 끊기면 DM 줘야 함") ──
import types
import eqgui
_clock = [1.8e9]
eqgui_time = eqgui.time if hasattr(eqgui, "time") else None
import time as _tm
_real_time = _tm.time
_tm.time = lambda: _clock[0]
_down = [None]
class _FB:
    def __init__(self, accts): self.accts = accts
    def healthcheck(self):
        if _down[0]:
            raise RuntimeError(_down[0])
        return True
_built = []
def _bb(bk, f1, f2, f3, accts):
    _built.append(list(accts)); return _FB(accts)
eqgui._build_broker = _bb
DMS, LOGS = [], []
fake = types.SimpleNamespace(log=LOGS.append, _member_alert=lambda k, ko, en: DMS.append((k, ko, en)),
                             _assets_with_open_position=lambda a: set(), _nt8_state=None)
cfg = {"broker": "nt8", "acct": "LFE-01", "_accts": ["LFE-01", "LFE-02"]}
def run(): eqgui.App._nt8_probe_run(fake, dict(cfg))
run()
ck("D1 정상: DM 없음, 무장 계좌 둘 다 짚음", not DMS and _built[-1] == ["LFE-01", "LFE-02"], _built)
_down[0] = "NT8 계좌 연결 끊김: LFE-02 - NinjaTrader 연결(Connections) 상태를 확인하세요"
run(); _clock[0] += 60; run()
ck("D2 끊긴 지 60초: 아직 DM 없음", not DMS, DMS)
_clock[0] += 60; run()
ck("D3 120초: nt8_down DM 한 통, 계좌 이름, KO/EN", len(DMS) == 1 and DMS[0][0] == "nt8_down"
   and "LFE-02" in DMS[0][1] and "LFE-02" in DMS[0][2] and "Connections" in DMS[0][1], DMS)
for _ in range(10):
    _clock[0] += 60; run()
ck("D4 이어져도 한 시간 안에는 더 안 보냄", len(DMS) == 1, len(DMS))
_clock[0] += 3600; run()
ck("D5 한 시간 넘게 이어지면 한 통 더", len(DMS) == 2, len(DMS))
_down[0] = None; _clock[0] += 60; run()
ck("D6 복구: nt8_ok 한 통", len(DMS) == 3 and DMS[-1][0] == "nt8_ok", DMS[-1:])
_clock[0] += 60; run()
ck("D7 복구 뒤 조용", len(DMS) == 3)
_down[0] = "NT8 애드온 미접속 - x"; fake._assets_with_open_position = lambda a: {"NQ"}
for _ in range(3):
    _clock[0] += 60; run()
ck("D8 애드온 미접속 + 보유 중: 포지션 문구로", len(DMS) == 4 and "포지션이 열려" in DMS[-1][1] and "position is open" in DMS[-1][2], DMS[-1:])
import inspect
ck("D9 10분 연결 감시는 NT8을 건너뜀(두 번 DM 안 함)", 'if bk == "nt8":' in inspect.getsource(eqgui.App._conn_watch_run))
_tm.time = _real_time
print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
