#!/usr/bin/env python3
# EQ Autopilot v2026.10.12e 버그 수리 오프라인 시험(2026-10-12 버그 조사 A·B·D 보고서). 0=통과
# 1 NT8 풀사이즈 표기('NQ 12-26', 'GC 12-26')를 NQ·GC로 본다: 트랙레코드 자산 판별, 통과 익절 포인트 가치,
#   진입 전 같은 자산 잔여 포지션 정리. 마이크로·ProjectX·IB 표기는 그대로.
# 2 1R 원장이 손상되고 백업이 없으면 초기화 시각을 한 번 영속 기록하고, 그 이전 진입 거래는 트랙레코드에 다시 올리지 않는다
#   (12d 이전 빌드가 남긴 .corrupt- 사본만 있는 경우 포함). 백업으로 되살린 경우는 종전대로 전부 올린다.
# 3 사이징 안전 바닥(손절거리 < 진입가 0.025%면 진입 안 함)과 천장(마이크로 환산 400): 정상 신호 결과는 그대로.
# 4 설정 저장 실패 표시, 신호 루프 주소를 폴마다 지금 토큰으로, 작은 상태 파일 원자 저장·설정 .bak·손상 사본,
#   ProjectX 확정 거절(success:false)은 '접수됐으나 미확인'이 아님, ENQ·GCE 틱 폴백, 거래 원장 기록 실패 로그.
# HOME·APPDATA = 임시 폴더. 브로커·네트워크·알림·실제 설정을 건드리지 않는다(전부 가짜).
import datetime as _dt
import glob
import inspect
import json
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

HOME = tempfile.mkdtemp(prefix="eq_12e_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
import requests  # noqa: E402
import autopilot_crypto  # noqa: E402
from eqexec import sizing  # noqa: E402
from eqexec.broker import projectx as PX  # noqa: E402
from eqexec.config import ProjectXCfg  # noqa: E402

assert eqgui.APP_DIR.startswith(HOME), eqgui.APP_DIR
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:300]}"))
    if not c:
        FAIL.append(n)


ck("0 버전 2026.10.12e 이상", eqgui.App._APP_VER >= "2026.10.12e", eqgui.App._APP_VER)

# ── 1 NT8 풀사이즈 표기 ───────────────────────────────────────────────────────
fa = eqgui.App._fill_asset
_cases = {"NQ 12-26": "NQ", "GC 12-26": "GC", "NQ 03-27": "NQ", "GC 02-27": "GC",
          "MNQ 12-26": "NQ", "MGC 12-26": "GC", "CON.F.US.ENQ.Z26": "NQ", "CON.F.US.GCE.Z26": "GC",
          "CON.F.US.MNQ.Z26": "NQ", "CON.F.US.MGC.Z26": "GC", "NQZ6": "NQ", "GCZ6": "GC", "MNQZ6": "NQ",
          "MGCZ6": "GC", "BTCUSDT": "BTC", "BTCUSDT.P": "BTC", "ES 12-26": None, "CL 12-26": None, "": None}
_bad = {k: (fa(k), v) for k, v in _cases.items() if fa(k) != v}
ck("1a _fill_asset: NT8 풀사이즈 'NQ 12-26'·'GC 12-26' → NQ·GC, 마이크로·ProjectX·IB·BTC 그대로, 모르는 것 None",
   not _bad, _bad)
pv = eqgui.App._passtp_pv
ck("1b _passtp_pv: 'NQ 12-26'=20, 'GC 12-26'=100, 'MNQ 12-26'=2, 'MGC 12-26'=10, ProjectX ENQ=20",
   (pv("NQ 12-26"), pv("GC 12-26"), pv("MNQ 12-26"), pv("MGC 12-26"), pv("CON.F.US.ENQ.Z26"))
   == (20.0, 100.0, 2.0, 10.0, 20.0))


class FakeFut:
    """잔여 포지션이 있는 선물 브로커(이름은 NT8이 아님 - 대기조 경로를 피함). 심볼은 NT8 표기."""
    name = "nt8like"

    def __init__(self, positions):
        self.pos = dict(positions)
        self.closed, self.entries = [], []

    def _accounts(self):
        return [{"name": "A1", "id": "A1"}]

    def list_open_positions(self):
        return [SimpleNamespace(account_id="A1", symbol=s, net_qty=q, raw={"_accountId": "A1"})
                for s, q in self.pos.items() if q]

    def close_contract(self, aid, con):
        self.closed.append(con)
        self.pos.pop(con, None)

    def place_entry(self, **k):
        self.entries.append((k.get("contract_id"), k.get("size")))
        return {"would_place": k}

    def position_qty(self, aid, con):
        return self.pos.get(con, 0)

    def current_market_price(self, con):
        return None


class Stub:
    lang = "ko"
    STOP_ORDER_CHECK_SEC = eqgui.App.STOP_ORDER_CHECK_SEC

    def __init__(self):
        self.logs, self.alerts, self.evs = [], [], []
        self._open_ctx, self._unconf_watch = {}, {}

    def log(self, m):
        self.logs.append(str(m))

    def _claim_entry(self, sig, aid, **k):
        return True

    def _resolve_contract(self, b, s):
        return {"MNQ": "MNQ 12-26", "NQ": "NQ 12-26", "MGC": "MGC 12-26", "GC": "GC 12-26"}.get(s, s)

    def _nt8_standby(self):
        return False

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def _send_ev(self, kind, asset, **k):
        self.evs.append((kind, k))

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_unconf_watch_add", "_unconf_watch_drop", "_unconf_save", "_unconf_key", "_remember_open",
           "_persist_open_ctx", "_run_futures_entry", "_fill_asset"):
    setattr(Stub, _n, eqgui.App.__dict__[_n])

_real_sleep = time.sleep
time.sleep = lambda s: None
try:
    st, b = Stub(), FakeFut({"NQ 12-26": 2, "MNQ 12-26": 3, "ES 12-26": 1})
    eqgui.App._run_futures_entry(st, b, "A1", [("MNQ", 4)], "LONG", 30900.0,
                                 {"id": "t-NQ-12e", "entry_ref": 31000.0}, True, _dt.datetime.now(), "NQ", None)
    ck("1c 진입 전 잔여 정리: 남은 풀사이즈 'NQ 12-26'도 같은 자산으로 보고 닫음(종전: '다른 심볼'로 남겨 위에 쌓음)",
       "NQ 12-26" in b.closed and "MNQ 12-26" in b.closed, b.closed)
    ck("1c 다른 자산(ES)은 건드리지 않음", "ES 12-26" not in b.closed and any("다른 심볼 포지션 1개" in m for m in st.logs),
       (b.closed, [m for m in st.logs if "다른 심볼" in m]))
finally:
    time.sleep = _real_sleep

# ── 2 1R 원장 초기화 시각 ─────────────────────────────────────────────────────
P = eqgui._RLEDGER_PATH
RESET = eqgui._RLEDGER_RESET_PATH
DAY = 86400.0


def _clean_ledgers():
    for f in os.listdir(eqgui.APP_DIR):
        if f.startswith(".r_ledger") or f.startswith(".eqtrades") or f == ".lastpush":
            os.remove(os.path.join(eqgui.APP_DIR, f))
    # 거래 원장 시작 표식은 미래로(모든 체결 = 원장 시작 이전 = 포함) - 이 시험은 R 계산만 본다
    with open(eqgui._LEDGER_SINCE_PATH, "w") as f:
        f.write(str((time.time() + 10 * DAY) * 1000))


class SyncStub:
    """push_profile이 부르는 것만. 체결 판별·묶음은 진짜 메서드."""
    lang = "ko"
    _srv_aead = False

    def __init__(self):
        self.logs = []
        self.consent = SimpleNamespace(get=lambda: True)
        self.tr_public = SimpleNamespace(get=lambda: False)
        self._profile = {}
        self._gate = {}
        self._token = "tok-12e"
        self.root = SimpleNamespace(after=lambda *a, **k: None)

    def log(self, m):
        self.logs.append(str(m))

    def _accts_of(self, a):
        return [{"id": "", "one_r": 50.0, "on": True}] if a == "BTC" else []

    def _acct_broker(self, a, ac):
        return "bybit"

    def _creds_of(self, a, bk=None):
        return {"f1": "user", "f3": ""}

    def _assets_with_open_position(self, assets):
        return set()

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("push_profile", "_fill_asset", "_fill_is_eq", "_fill_entry"):
    setattr(SyncStub, _n, eqgui.App.__dict__[_n])

FILLS = []
PUSHED = []


class FakeBybit:
    def closed_fills(self, start_ms):
        return [dict(f) for f in FILLS]


eqgui._build_broker = lambda *a, **k: FakeBybit()
eqgui._f2_load = lambda f1: "k"
requests.get = lambda url, params=None, timeout=None: (
    PUSHED.append(json.loads(params["profile_push"])) or SimpleNamespace(text="pp:ok:1"))
autopilot_crypto.encrypt = lambda tok, payload, v2=False: json.dumps(payload)
autopilot_crypto.path_id = lambda tok: "pid"


def run_sync():
    PUSHED.clear()
    s = SyncStub()
    SyncStub.push_profile(s, auto=True)
    t_end = time.time() + 20
    while time.time() < t_end:
        if any(("체결 없음" in m) or ("동기화 완료" in m) or ("실패" in m) for m in s.logs):
            break
        _real_sleep(0.05)
    trades = [t for p in PUSHED for t in p.get("trades", [])]
    return trades, s.logs


def fill(ts, pnl):
    return {"symbol": "BTCUSDT", "ts_ms": int(ts * 1000), "pnl": pnl, "direction": "LONG", "acct": "",
            "entry_px": 100.0, "exit_px": 99.0}


now = time.time()
# 2a: 대표 기기 모양 - 246바이트 NUL, 백업 없음
_clean_ledgers()
open(P, "wb").write(b"\x00" * 246)
FILLS[:] = [fill(now - 5 * DAY, -32.25), fill(now - 3 * DAY, 40.0)]
tr, lg = run_sync()
ck("2a NUL 원장·백업 없음 → 지난 거래를 지금 1R로 다시 올리지 않음(푸시 0건)", tr == [], tr)
ck("2a 초기화 시각이 파일로 남음(지금 근처)", os.path.exists(RESET) and abs(float(open(RESET).read()) - now) < 120,
   os.listdir(eqgui.APP_DIR))
ck("2a 로그: 다시 올리지 않는다는 한 줄(건수)", any("이전 체결 2건은" in m and "다시 올리지 않습니다" in m for m in lg), lg)
_r1 = float(open(RESET).read())
tr2, _ = run_sync()
ck("2a 다음 동기화(원장 = 없음 상태)도 지난 거래를 올리지 않고, 초기화 시각은 그대로(한 번만 기록)",
   tr2 == [] and float(open(RESET).read()) == _r1, (tr2, open(RESET).read(), _r1))
# 초기화 뒤의 새 진입은 정상으로 올라간다(1R 기록 시각이 체결보다 앞)
time.sleep(0.01)
_t_after = time.time()
eqgui._record_real_r("BTC", "bybit", 50.0)
FILLS[:] = [fill(now - 5 * DAY, -32.25), fill(_t_after + 1, -25.0)]
tr3, _ = run_sync()
ck("2a 초기화 뒤 체결은 그대로 올라가고 R은 그 진입의 1R로(−25/50 = −0.5)",
   len(tr3) == 1 and tr3[0]["r"] == -0.5, tr3)

# 2b: 12d가 이미 손상 파일을 .corrupt-로 옮기고 새 줄만 쌓은 경우(초기화 표식 없음)
_clean_ledgers()
T = now - 4 * DAY
json.dump({"BTC|bybit": 내부 기록}, open(P, "w"))
open(P + f".corrupt-{int(T)}", "wb").write(b"\x00" * 246)
FILLS[:] = [fill(now - 6 * DAY, -64.5), fill(now - 2 * DAY, -25.0)]
tr, lg = run_sync()
ck("2b 남은 .corrupt- 사본만으로 초기화 시각을 알아내 그 이전 거래는 빼고, 이후 거래는 올림(r −0.5)",
   len(tr) == 1 and tr[0]["r"] == -0.5 and tr[0]["date"] == _dt.datetime.utcfromtimestamp(now - 2 * DAY).date().isoformat(),
   tr)
ck("2b 그 시각을 표식 파일로 한 번 기록", os.path.exists(RESET) and float(open(RESET).read()) == float(int(T)),
   open(RESET).read() if os.path.exists(RESET) else None)

# 2c: 백업으로 되살린 손상(원장에 손상 시각 이전 줄이 있음) → 표식 없음, 전부 올림
_clean_ledgers()
T = now - 4 * DAY
json.dump({"BTC|bybit": [[now - 9 * DAY, 40.0], [T + 100, 50.0]]}, open(P, "w"))
open(P + f".corrupt-{int(T)}", "wb").write(b"\x00" * 10)
FILLS[:] = [fill(now - 6 * DAY, -20.0), fill(now - 2 * DAY, -25.0)]
tr, lg = run_sync()
ck("2c 백업으로 되살린 원장 → 초기화 표식 없음, 두 거래 모두 그 시각 1R로(−0.5, −0.5)",
   not os.path.exists(RESET) and sorted(t["r"] for t in tr) == [-0.5, -0.5], (tr, os.path.exists(RESET)))

# 2d: 진입 경로(_record_real_r)가 먼저 손상을 만나도 표식이 남는다
_clean_ledgers()
open(P, "wb").write(b"\x00" * 246)
eqgui._record_real_r("NQ", "A1", 600.0)
ck("2d 진입 때 손상 발견(백업 없음) → 초기화 표식 + 새 줄 기록",
   os.path.exists(RESET) and json.load(open(P)).get("NQ|A1", 내부 기록)[0][1] == 600.0, os.listdir(eqgui.APP_DIR))
_r = float(open(RESET).read())
eqgui._rledger_mark_reset(_r - 1000)
ck("2d 더 이른 시각으로는 표식이 되돌아가지 않음", float(open(RESET).read()) == _r)

# ── 3 사이징 바닥·천장 ────────────────────────────────────────────────────────


def ref_size(one_r, entry, stop, pv):
    raw = one_r / ((entry - stop) * pv)
    return int(round(raw)) if raw >= 0.75 else 0


_diff = []
_n = 0
for sym, asset, entry, pvv in (("MNQ", "NQ", 31000.0, 2.0), ("MNQ", "NQ", 18000.0, 2.0),
                               ("MGC", "GC", 4200.0, 10.0), ("MGC", "GC", 2000.0, 10.0)):
    for frac in (0.0006, 0.0008, 0.001, 0.002, 0.0035, 0.006, 0.01, 0.02):
        stop = round(entry * (1 - frac), 2)
        for one_r in (50, 100, 300, 450, 600, 900, 1200, 1800, 2250, 2250 * 1.75, 2250 * 2):
            _n += 1
            got = sizing.compute_size(asset, "projectx", one_r, entry, stop, "LONG")
            want = ref_size(one_r, entry, stop, pvv)
            if got["size"] != want or got.get("guard"):
                _diff.append((asset, entry, frac, one_r, got["size"], want, got.get("guard")))
ck(f"3a 정상 손절(진입가 0.06% 이상)·1R $50~$4,500 {_n}가지: 수량이 종전 공식과 같고 guard 없음", not _diff, _diff[:5])
g = sizing.compute_size("GC", "projectx", 1800, 4200.0, 4199.5, "LONG")
ck("3b 금 손절 0.5pt(0.012%) → 진입 안 함(size 0, legs 없음, guard stop_too_tight, 바닥 1.05pt)",
   g["size"] == 0 and g["legs"] == [] and g["guard"] == "stop_too_tight" and g["min_stop_pts"] == 1.05, g)
g = sizing.compute_size("NQ", "nt8", 1800, 31000.0, 31005.0, "SHORT")
ck("3b 나스닥 숏 손절 5pt(0.016%) → 진입 안 함", g["size"] == 0 and g["guard"] == "stop_too_tight", g)
g = sizing.compute_size("GC", "projectx", 6750, 4200.0, 4198.9, "LONG")
ck("3c 금 1.1pt·$6,750 → 마이크로 614가 천장 400으로(금 미니 40), guard size_cap",
   g["size"] == 400 and g["legs"] == [("GC", 40)] and g["guard"] == "size_cap" and g["size_uncapped"] == 614, g)
g = sizing.compute_size("GC", "projectx", 1800, 4200.0, 4198.5, "LONG")
ck("3c 보고서 예(금 1.5pt, $600×3 = 마이크로 120)는 바닥·천장 아래라 종전과 같음(정상 범위의 꼬리)",
   g["size"] == 120 and g["legs"] == [("GC", 12)] and not g.get("guard"), g)
g = sizing.compute_size("BTC", "bybit", 600, 110000.0, 109990.0, "LONG")
ck("3d 크립토는 대상 아님(종전과 같음)", g["size"] == 60.0 and not g.get("guard"), g)


def enter(one_r, stop, lim=0.0, entry=4200.0, asset="GC", mult=1.0):
    me = MagicMock()
    me.lang = "ko"
    me._live_now.return_value = False
    me._jitter_stop.side_effect = lambda a, e, s, l: s
    cfg = {"broker": "projectx", "f1": "u", "f2": "k", "f3": "", "acct": "123456", "one_r": one_r, "label": "t",
           "live": False, "prop": {}, "pct": {}, "one_lim": lim}
    _bb = eqgui._build_broker
    eqgui._build_broker = MagicMock()
    try:
        eqgui.App._enter_account(me, cfg, {"entry_ref": entry, "published_at": None}, asset, "LONG", stop, mult)
    finally:
        eqgui._build_broker = _bb
    legs = me._run_futures_entry.call_args[0][2] if me._run_futures_entry.called else None
    return legs, [c[0][1] for c in me._entry_skip_ev.call_args_list], [str(c[0][0]) for c in me.log.call_args_list]


legs, skips, lg = enter(600, 4199.5, lim=100000.0)
ck("3e 앱: 바닥에 걸리면 주문 안 함(한 계약 한도가 넉넉해도), 사유 sizing, 회원 로그 한 줄",
   legs is None and skips == ["sizing"] and any("비정상적으로 짧습니다" in m and "1.05pt" in m for m in lg), (legs, skips, lg))
legs, skips, lg = enter(2250, 4198.9, mult=3.0)
ck("3e 앱: 천장에 걸리면 줄여서 진입(GC 40), '수량 상한' 로그", legs == [("GC", 40)] and not skips
   and any("수량 상한" in m and "614" in m and "400" in m for m in lg), (legs, skips, lg))
legs, skips, lg = enter(600, 4180.0)
ck("3e 앱: 정상 신호(20pt)는 종전 그대로(MGC 3)", legs == [("MGC", 3)] and not any("상한" in m or "짧습니다" in m for m in lg),
   (legs, lg))

# ── 4 ───────────────────────────────────────────────────────────────────────
# 4a 설정 저장 실패 표시
acfg = {"NQ": {"broker": "projectx", "creds": {}, "include": True, "accounts": [eqgui._new_acct(600, "", True, "")]}}
eqgui._save_full("ko", "tok", acfg)
ck("4a 정상 저장 → 실패 표식 없음", eqgui._CFG_SAVE_FAILED is False and os.path.exists(eqgui.CFG_PATH))
_wca = eqgui._write_cfg_atomic


def _boom(text):
    raise OSError(28, "No space left on device")


eqgui._write_cfg_atomic = _boom
eqgui._save_full("ko", "tok", acfg)
eqgui._write_cfg_atomic = _wca
ck("4a 디스크 쓰기 실패 → 실패 표식", eqgui._CFG_SAVE_FAILED is True)


class Lbl:
    def __init__(self):
        self.kw = {}

    def config(self, **k):
        self.kw.update(k)

    def winfo_exists(self):
        return True


me = MagicMock()
me.lang = "ko"
me._saved_lbl = Lbl()
me._cfg_fail_logged = False
eqgui.App._flash_saved(me)
ck("4a 실패면 초록 '저장됨' 대신 주황 '저장 안 됨' + 로그 한 줄",
   "저장 안 됨" in me._saved_lbl.kw.get("text", "") and "저장됨" not in me._saved_lbl.kw.get("text", "").replace("저장 안 됨", "")
   and me._saved_lbl.kw.get("foreground") == "#b45309" and me.log.call_count == 1, (me._saved_lbl.kw, me.log.call_args_list))
eqgui._save_full("ko", "tok", acfg)
me2 = MagicMock()
me2.lang = "ko"
me2._saved_lbl = Lbl()
me2._kc_warned_save = None
me2._kc_enc_logged_save = False
eqgui._KC_LAST_FAILED[:] = []
eqgui.App._flash_saved(me2)
ck("4a 다시 성공하면 초록 '✓ 저장됨'", me2._saved_lbl.kw.get("text", "").startswith("✓ 저장됨")
   and me2._saved_lbl.kw.get("foreground") == "#15803d", me2._saved_lbl.kw)

# 4b 신호 루프: 주소를 폴마다 지금 토큰으로
_src = inspect.getsource(eqgui.App._sig_loop)
ck("4b 신호 루프가 폴마다 지금 토큰으로 주소를 만들고 같은 토큰으로 복호화",
   "_url_now = _feed_url(_tok_now)" in _src and "requests.get(_url_now" in _src
   and "decrypt(_tok_now, r.text)" in _src and "requests.get(url," not in _src)

# 4c 원자 저장·설정 백업·손상 사본
eqgui._save_open_ctx({"NQ": {"ts": time.time(), "sym": "MNQ 12-26"}})
eqgui._mark_entered("NQ")
for p in (eqgui._LEDGER_SINCE_PATH,):
    try:
        os.remove(p)
    except OSError:
        pass
_since = eqgui._ledger_since()
eqgui._mark_pushed()
_tmps = glob.glob(os.path.join(eqgui.APP_DIR, "*.tmp")) + glob.glob(os.path.join(eqgui.APP_DIR, ".*.tmp"))
ck("4c .open_ctx·.entered·.eqtrades_since·.lastpush 원자 저장(읽힘, 임시 파일 안 남음)",
   eqgui._load_open_ctx().get("NQ", {}).get("sym") == "MNQ 12-26" and "NQ" in eqgui._load_entered()
   and eqgui._ledger_since() == _since and eqgui._last_pushed() > 0 and not _tmps, _tmps)
_src_all = inspect.getsource(eqgui._save_open_ctx) + inspect.getsource(eqgui._mark_entered) + \
    inspect.getsource(eqgui._ledger_since) + inspect.getsource(eqgui._mark_pushed)
ck("4c 네 곳 모두 _write_text_atomic 경유(파일을 비우고 쓰는 open(..., 'w') 없음)",
   _src_all.count("_write_text_atomic(") == 4 and '"w"' not in _src_all)
eqgui._save_full("ko", "tok-A", acfg)
eqgui._save_full("ko", "tok-B", acfg)
import yaml  # noqa: E402
ck("4c config.yaml 덮기 전 직전 정상본을 .bak로", yaml.safe_load(open(eqgui.CFG_PATH + ".bak").read()).get("token") == "tok-A"
   and yaml.safe_load(open(eqgui.CFG_PATH).read()).get("token") == "tok-B")
open(eqgui.CFG_PATH, "wb").write(b"\x00" * 300)
_d = eqgui._load()
_cc = glob.glob(eqgui.CFG_PATH + ".corrupt-*")
ck("4c 읽히지 않는 config.yaml → 손상 사본을 남기고 기본값으로 연다(.bak는 그대로)",
   _d.get("token") == "" and len(_cc) == 1 and open(_cc[0], "rb").read() == b"\x00" * 300
   and yaml.safe_load(open(eqgui.CFG_PATH + ".bak").read()).get("token") == "tok-A", (_d.get("token"), _cc))
for _f in _cc:
    os.remove(_f)
os.remove(eqgui.CFG_PATH)
_d = eqgui._load()
ck("4c 설정 파일이 없을 때(새 설치)는 손상 사본을 만들지 않음", not glob.glob(eqgui.CFG_PATH + ".corrupt-*"))

# 4d ProjectX 확정 거절
ck("4d ProjectX 틱 폴백에 풀사이즈 루트 ENQ·GCE", PX._FALLBACK_TICK.get("ENQ") == 0.25 and PX._FALLBACK_TICK.get("GCE") == 0.1)
_pxb = PX.ProjectXBroker(ProjectXCfg(user_name="m@x.com", api_key="K", accounts=["A"]))
_pxb.search_contracts = MagicMock(side_effect=RuntimeError("Contract/search down"))
ck("4d 계약 조회가 실패해도 ENQ·GCE 손절 정렬 틱을 앎",
   _pxb._tick_size("CON.F.US.ENQ.Z26") == 0.25 and _pxb._tick_size("CON.F.US.GCE.Z26") == 0.1)


class PXRej:
    """ProjectX 모양 가짜. mode='reject' = success:false 거절 문구, 'timeout' = 망 예외(주문은 들어갔을 수 있음)."""
    name = "projectx"

    def __init__(self, mode):
        self.mode = mode
        self.entries = []

    def _accounts(self):
        return [{"name": "ACC1", "id": 101}]

    def list_open_positions(self):
        return []

    def place_entry(self, **k):
        self.entries.append(k.get("contract_id"))
        if self.mode == "reject":
            raise RuntimeError("ProjectX /api/Order/place failed (errorCode=2): Account is locked (daily loss limit)")
        raise requests.exceptions.ReadTimeout("Read timed out.")

    def position_qty(self, aid, con):
        return 0

    def current_market_price(self, con):
        return None

    def _open_orders(self, aid):
        return []

    def close_contract(self, aid, con):
        pass


class Stub2(Stub):
    def _resolve_contract(self, b, s):
        return f"CON.F.US.{s}.Z26"


_rf_sleep = time.sleep
_rt = time.time
_clk = {"t": time.time()}
time.time = lambda: _clk["t"]
time.sleep = lambda s: _clk.__setitem__("t", _clk["t"] + float(s))
try:
    for f in glob.glob(eqgui._LEDGER_PATH + "*"):
        os.remove(f)
    st, b = Stub2(), PXRej("reject")
    eqgui.App._run_futures_entry(st, b, "ACC1", [("MNQ", 2)], "LONG", 30900.0,
                                 {"id": "t-NQ-rej", "entry_ref": 31000.0}, True, _dt.datetime.now(), "NQ", None)
    _kinds = [a[0] for a in st.alerts]
    ck("4d 확정 거절: '접수됐으나' 로그 없음, '진입 거절' 로그와 사유",
       not any("접수됐으나" in m for m in st.logs)
       and any("진입 거절" in m and "daily loss limit" in m for m in st.logs), st.logs)
    ck("4d 확정 거절: 미확인 DM 대신 진입 실패 DM(사유 포함, KO/EN), 미확인 감시 없음",
       _kinds == ["entry_miss"] and "거절" in st.alerts[0][1] and "rejected" in st.alerts[0][2]
       and not st._unconf_watch, (st.alerts, st._unconf_watch))
    ck("4d 확정 거절: 거래 원장에 유령 진입 없음, entry_fail(rejected) 보고",
       eqgui._ledger_load() == [] and ("entry_fail", {"why": "rejected", "err": b and st.evs[-1][1]["err"]}) in st.evs
       and len(b.entries) == 1, (eqgui._ledger_load(), st.evs))
    st, b = Stub2(), PXRej("timeout")
    eqgui.App._run_futures_entry(st, b, "ACC1", [("MNQ", 2)], "LONG", 30900.0,
                                 {"id": "t-NQ-to", "entry_ref": 31000.0}, True, _dt.datetime.now(), "NQ", None)
    ck("4d 망 예외(타임아웃)는 종전대로 '접수됐으나 미확인' + 원장 + 미확인 DM(주문이 들어갔을 수 있음)",
       any("접수됐으나" in m for m in st.logs) and [a[0] for a in st.alerts] == ["entry_unconfirmed"]
       and len(eqgui._ledger_load()) == 1, (st.alerts, eqgui._ledger_load()))
finally:
    time.sleep, time.time = _rf_sleep, _rt

# 4e 거래 원장 기록 실패는 로그에 남는다
_ll = eqgui._ledger_load


def _locked():
    raise PermissionError(13, "locked")


eqgui._ledger_load = _locked
eqgui._ledger_add("GC", "MGC 12-26", "LONG", "EQ-AP-x")
eqgui._ledger_load = _ll
_logtxt = ""
for f in glob.glob(os.path.join(eqgui.LOG_DIR, "eq-*.log")):
    _logtxt += open(f, encoding="utf-8").read()
ck("4e 원장이 잠겨 진입 기록을 버리면 로그 파일에 한 줄(자산·심볼)",
   "거래 원장 기록 실패" in _logtxt and "GC MGC 12-26" in _logtxt, _logtxt[-400:])

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: {FAIL}"))
sys.exit(1 if FAIL else 0)
