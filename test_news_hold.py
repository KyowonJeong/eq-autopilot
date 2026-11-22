# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# 뉴스 창 계좌별 대기(대표 2026-11-04 "앱에서 프롭 거래 말고 실계좌는 뉴스 상관없이 진입하게 해") 오프라인 시험.
# 네트워크 차단, 임시 HOME. 브로커를 부르지 않는다(_enter_account는 기록만 하는 가짜).
import os
import socket
import sys
import tempfile
import time

HOME = tempfile.mkdtemp(prefix="eq_nhold_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui  # noqa: E402

assert eqgui.APP_DIR.startswith(HOME), eqgui.APP_DIR
E = eqgui
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:500]}"))
    if not c:
        FAIL.append(n)


class Stub:
    def __init__(self, lang="ko", skew=0.0):
        self.lang = lang
        self.logs = []
        self._sig_on = True
        self._skew = skew
        self.entered = []

    def log(self, m):
        self.logs.append(str(m))

    def _clock_skew_now(self):
        return self._skew

    def _enter_account(self, cfg, sig, asset, direction, stop, mult):
        self.entered.append((cfg.get("acct"), dict(sig), time.time()))


TOP = {"broker": "projectx", "acct": "150KTC-V2-26505604", "label": "Topstep", "prop": {"on": True}}
TOP_NOFLAG = {"broker": "projectx", "acct": "12345", "label": "TopstepX"}      # 프롭 표시가 없어도 ProjectX는 프롭 회사
OWN = {"broker": "tradovate", "acct": "195551", "label": "Tradovate", "prop": {}}
BYBIT = {"broker": "bybit", "acct": "", "label": "Bybit"}
SIG = {"id": "2026-12-01-NQ-1", "instrument": "NQ", "direction": "LONG", "entry_ref": 28813.75, "stop_price": 28257.98}

# 1 표시 없음 = 그대로(평소 날, 옛 서버)
s = Stub()
r = E.App._news_hold_wait(s, TOP, dict(SIG), "NQ")
ck("1 표시 없으면 신호 그대로, 기다리지 않음, _late 없음", r == SIG and not s.logs, (r, s.logs))

# 2 자기 자본 계좌 = 바로(기다리지 않음)
s = Stub()
sig = dict(SIG, news_hold_until=time.time() + 30, news_hold_why="ISM Manufacturing PMI (rule) 10:00 ET ±60s")
t0 = time.time()
r1 = E.App._news_hold_wait(s, OWN, sig, "NQ")
r2 = E.App._news_hold_wait(s, BYBIT, sig, "BTC")
ck("2 자기 자본 계좌(Tradovate 비 Lucid, Bybit)는 바로 진입, 신호 그대로(_late 없음)",
   time.time() - t0 < 0.5 and r1 is sig and r2 is sig and all("바로 진입" in x for x in s.logs), (s.logs,))

# 3 프롭 계좌 = 창 끝까지 기다렸다 _late로
s = Stub()
sig = dict(SIG, news_hold_until=time.time() + 1.2, news_hold_why="ISM")
t0 = time.time()
r = E.App._news_hold_wait(s, TOP, sig, "NQ")
dt = time.time() - t0
ck("3 프롭 계좌(prop on)는 창 끝까지 기다린 뒤 _late=True로 진입",
   1.0 <= dt <= 2.0 and r and r.get("_late") is True and r.get("_late_soft") is True and r.get("news_hold_until") == sig["news_hold_until"]
   and any("기다렸다가" in x for x in s.logs), (dt, r, s.logs))

# 4 프롭 표시 없어도 ProjectX = 프롭
s = Stub()
t0 = time.time()
r = E.App._news_hold_wait(s, TOP_NOFLAG, dict(SIG, news_hold_until=time.time() + 0.6), "NQ")
ck("4 ProjectX 계좌는 프롭 표시가 없어도 기다린다", time.time() - t0 >= 0.5 and r and r.get("_late") is True, s.logs)

# 5 서버 시각 보정: PC 시계가 서버보다 10초 빠르면(skew=+10) 그만큼 덜 기다리지 않게 서버 시각으로 잰다
s = Stub(skew=10.0)
t0 = time.time()
r = E.App._news_hold_wait(s, TOP, dict(SIG, news_hold_until=time.time() - 10.0 + 0.8), "NQ")
ck("5 시계 차이를 빼고 서버 시각 기준으로 기다린다", 0.6 <= time.time() - t0 <= 1.5 and r.get("_late") is True,
   time.time() - t0)

# 6 이미 지난 시각 = 기다리지 않고 _late
s = Stub()
t0 = time.time()
r = E.App._news_hold_wait(s, TOP, dict(SIG, news_hold_until=time.time() - 5), "NQ")
ck("6 창이 이미 끝났으면 기다리지 않고 _late로", time.time() - t0 < 0.3 and r.get("_late") is True and not s.logs, s.logs)

# 7 기다리는 사이 자동 진입이 꺼지면 진입 안 함
s = Stub()
import threading  # noqa: E402
threading.Timer(0.3, lambda: setattr(s, "_sig_on", False)).start()
r = E.App._news_hold_wait(s, TOP, dict(SIG, news_hold_until=time.time() + 0.8), "NQ")
ck("7 기다리는 사이 꺼지면 None(진입 안 함)", r is None and any("꺼져" in x for x in s.logs), s.logs)

# 8 상한 180초(캘린더 오류로 무한 대기 금지) - 소스로 확인
import inspect  # noqa: E402
_src = inspect.getsource(E.App._news_hold_wait)
ck("8 대기 상한 180초", "min(180.0" in _src)

# 9 _pos_entry_run: 자기 자본은 바로, 프롭은 기다린 뒤 _enter_account로
s = Stub()
sig = dict(SIG, news_hold_until=time.time() + 0.8, news_hold_why="ISM")
t0 = time.time()
ths = [threading.Thread(target=E.App._pos_entry_run, args=(s, c, sig, "NQ", "LONG", 28257.98, 1.0)) for c in (OWN, TOP)]
[t.start() for t in ths]
[t.join(5) for t in ths]
_own = [e for e in s.entered if e[0] == "195551"]
_top = [e for e in s.entered if e[0] == TOP["acct"]]
ck("9 _pos_entry_run: 자기 자본 계좌는 즉시, 프롭 계좌는 창 끝 뒤(_late) 진입",
   _own and _top and _own[0][2] - t0 < 0.4 and _top[0][2] - t0 >= 0.7 and _top[0][1].get("_late") is True
   and not _own[0][1].get("_late"), s.entered)

# 10 EN 로그
s = Stub(lang="en")
E.App._news_hold_wait(s, OWN, dict(SIG, news_hold_until=time.time() + 30, news_hold_why="ISM"), "NQ")
ck("10 EN 화면은 영어 로그", s.logs and "not a prop account" in s.logs[0] and not any("가" <= ch <= "힣" for ch in s.logs[0]),
   s.logs)

# 11 heartbeat가 nh를 광고
ck("11 생존 핑 본문에 'nh': True", '"nh": True' in inspect.getsource(E.App))

# 12 가격을 못 주는 브로커(NT8) 계좌만 있는 기기: 대기 뒤 가격을 못 읽어도 건너뛰지 않고 기준가 사이징 그대로(종전 발표 날과 같게)
_ent = inspect.getsource(E.App._run_futures_entry)
ck("12 _late_soft: 가격 못 읽음 = 기준가로 진입(건너뜀 아님), 5% 축소 안 함, NT8 대기조 건너뜀에서 제외",
   'sig.get("_late_soft") and _ref is not None' in _ent and "and not _soft_np:" in _ent
   and 'sig.get("_late") and not sig.get("_late_soft") and stop is not None and type(b).__name__ == "NT8Broker"' in _ent)

print("\n" + ("FAILED: " + "; ".join(FAIL) if FAIL else "전부 통과"))
sys.exit(1 if FAIL else 0)
