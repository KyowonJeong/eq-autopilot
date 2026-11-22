#!/usr/bin/env python3
# End-to-end offline harness for App._sig_loop: real loop + real _enter_account + real _run_futures_entry,
# fake feed (requests.Session.get / requests.get), fake futures broker, no network, no alerts.
# usage: python test_sig_loop_sim_offline.py [<executor_dir>] [--src <dir containing an alternative eqgui.py>] [--mode session|get]
# exit 0 = all PASS. 2026-10-21 08:00 금 사고(20d: 신호 하나 처리 뒤 피드 조회 멈춤) 뒤 정식 시험에 편입 - 신호를 여러 개 연달아,
# 피드 오류를 섞어 진짜 루프를 돌린다(옛 _sess 덮어쓰기를 되살리면 S1~S3이 실패하는 것 확인). 대표 '시스템으로 박아 놔'.
import os, sys, socket, tempfile, threading, time, json, email.utils
from types import SimpleNamespace

EXEC = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else os.path.dirname(os.path.abspath(__file__)))
SRC = sys.argv[sys.argv.index("--src") + 1] if "--src" in sys.argv else None
MODE = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "session"
HOME = tempfile.mkdtemp(prefix="eq_simloop_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"; os.environ["EQ_ERR_REPORT"] = "0"; os.environ["EQ_STOP_JITTER"] = "0"
def _blocked(*a, **k): raise OSError("network blocked in harness")
socket.socket.connect = _blocked; socket.create_connection = _blocked; socket.getaddrinfo = _blocked
sys.path.insert(0, EXEC)
if SRC:
    sys.path.insert(0, os.path.abspath(SRC))
import requests  # noqa: E402
import eqgui  # noqa: E402
import autopilot_crypto  # noqa: E402
from eqexec import sizing  # noqa: E402
print("eqgui from:", eqgui.__file__, "| python", sys.version.split()[0], "| mode", MODE)

FAIL = []
def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f"  -- {str(d)[:400]}"))
    if not c: FAIL.append(n)

TOKEN = "tok-sim-1234567890"
LOOP_DEATHS = []
_orig_hook = threading.excepthook
def _hook(a):
    LOOP_DEATHS.append(f"{a.thread.name}: {a.exc_type.__name__}: {a.exc_value}")
    _orig_hook(a)
threading.excepthook = _hook

# ---------------- fake feed ----------------
FEED = {"mode": "sig", "sig": None}
POLLS = [0]
POLL_LOCK = threading.Lock()
class Resp:
    def __init__(self, ok, text, code=200):
        self.ok, self.text, self.status_code = ok, text, code
        self.headers = {"Date": email.utils.formatdate(time.time(), usegmt=True)}
def _feed_get(url, params=None, timeout=None, **k):
    with POLL_LOCK:
        POLLS[0] += 1
    assert url.startswith(eqgui.APP_BASE) and autopilot_crypto.path_id(TOKEN) in url, url
    m = FEED["mode"]
    if m == "exc":
        raise requests.ConnectionError("simulated connection reset")
    if m == "500":
        return Resp(False, "Internal Server Error", 500)
    if m == "garbage":
        return Resp(True, "v2.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    if m == "wrongkey":
        return Resp(True, autopilot_crypto.encrypt("other-token", FEED["sig"] or {}))
    if m == "nonDict":
        return Resp(True, autopilot_crypto.encrypt(TOKEN, None))
    return Resp(True, autopilot_crypto.encrypt(TOKEN, FEED["sig"] or {}))
if MODE == "session":
    requests.Session.get = lambda self, url, **k: _feed_get(url, **k)   # requests.get untouched -> loop uses its Session
    def _no_get(*a, **k): raise AssertionError("requests.get must not be used in session mode")
else:
    _fg = lambda url, **k: _feed_get(url, **k)
    _fg.__module__ = "harness"
    requests.get = _fg
    def _sess_get(self, *a, **k): raise AssertionError("Session.get must not be used in get mode")
    requests.Session.get = _sess_get

# ---------------- fake futures broker ----------------
PRICE = {"NQ": 20000.0, "GC": 4136.0}
ALL_PLACED = []
PLACED = []             # (acct, asset, contract, size, sid_hint, t)
PLACED_LOCK = threading.Lock()
CUR_SID = [None]
class FakeFut:
    name = "ibkr"
    def __init__(self, acct): self.acct, self.pos = acct, {}
    def _accounts(self): return [{"name": self.acct, "id": self.acct}]
    def list_open_positions(self): return []
    def close_contract(self, aid, con): self.pos.pop(con, None)
    def position_qty(self, aid, con): return self.pos.get(con, 0)
    def current_market_price(self, con):
        return PRICE["NQ" if "NQ" in str(con) else "GC"]
    def place_entry(self, account_id=None, contract_id=None, side=None, size=None, stop_loss_price=None,
                    custom_tag=None, dry_run=True, **k):
        assert not dry_run, "expected live path"
        with PLACED_LOCK:
            PLACED.append((self.acct, contract_id, int(size), CUR_SID[0], stop_loss_price, time.time()))
            ALL_PLACED.append(PLACED[-1])
        self.pos[contract_id] = self.pos.get(contract_id, 0) + int(size)
        return {"order_id": custom_tag, "stop": True, "stop_price": stop_loss_price}
BROKERS = {}
def _fake_build(broker, f1, f2, f3, accounts):
    a = accounts[0]
    BROKERS.setdefault(a, FakeFut(a))
    return BROKERS[a]
eqgui._build_broker = _fake_build

INFO = []
eqgui.messagebox = SimpleNamespace(askyesno=lambda *a, **k: True,
                                   showinfo=lambda t, m, **k: INFO.append(m),
                                   showwarning=lambda *a, **k: None)

# ---------------- stub App ----------------
_BIND = ("_sig_loop", "_sig_loop_body", "_enter_account", "_run_futures_entry", "_note_clock", "_clock_skew_now", "_fill_asset",
         "_jitter_stop", "_missed_entry", "_clock_skew_text")
STUB_CALLS = {}
class Stub:
    lang = "ko"
    STOP_ORDER_CHECK_SEC = eqgui.App.STOP_ORDER_CHECK_SEC
    _JITTER_TICK = getattr(eqgui.App, "_JITTER_TICK", {})
    def __init__(self, accts):
        self.logs, self.skips, self.evs, self.alerts = [], [], [], []
        self._sig_accts = accts
        self._token = TOKEN
        self._sig_on = False
        self._sig_gen = 0
        self._entered_at = {}
        self._gate = {"force_dry_run": False}
        self._open_ctx, self._unconf_watch = {}, {}
    def log(self, m): self.logs.append(str(m))
    def _sig_poll_secs(self): return 0.01
    def _live_now(self, f): return bool(f)
    def _live_acct_cfg(self, key, cfg): return dict(cfg), ""
    def _claim_entry(self, sig, aid, **k): return True
    def _nt8_standby(self): return False
    def _resolve_contract(self, b, s, **k): return f"{s} 12-26"
    def _entry_skip_ev(self, a, why): self.skips.append((a, why))
    def _send_ev(self, kind, asset, **k): self.evs.append((kind, asset, k))
    def _member_alert(self, *a, **k): self.alerts.append(a[:2])
    def __getattr__(self, n):
        if n.startswith("__") or not hasattr(eqgui.App, n) or not callable(getattr(eqgui.App, n)):
            raise AttributeError(n)
        def _noop(*a, **k):
            STUB_CALLS[n] = STUB_CALLS.get(n, 0) + 1
            return None
        return _noop
for _n in _BIND:
    setattr(Stub, _n, eqgui.App.__dict__[_n])

def cfg(acct, one_r):
    return {"broker": "ibkr", "f1": "127.0.0.1", "f2": "", "f3": "7497", "acct": acct, "one_r": one_r,
            "live": True, "label": acct, "prop": {}, "pct": {}, "one_lim": 0.0}

THREADS = []
def start(st):
    st._sig_gen += 1; st._sig_on = True
    th = threading.Thread(target=st._sig_loop, args=(eqgui._feed_url(st._token), st._sig_gen),
                          name=f"sig_loop_g{st._sig_gen}", daemon=True)
    th.start(); THREADS.append(th); return th
def stop(st, th, wait=True):
    st._sig_on = False
    if wait: th.join(5)
def wait_polls(n=6, timeout=15):
    t0, p0 = time.time(), POLLS[0]
    while POLLS[0] < p0 + n:
        if time.time() - t0 > timeout:
            return False
        time.sleep(0.005)
    return True
def sig(sid, asset, age, direction="LONG", tradeable=True, ref=None, stop=None, mult=1.0):
    ref = ref if ref is not None else {"NQ": 20000.0, "GC": 4136.0}[asset]
    stop = stop if stop is not None else {"NQ": 19950.0, "GC": 4130.4}[asset]
    pub = time.time() - age
    d = {"id": sid, "instrument": asset, "published_at": pub, "exit_ts": pub + 4 * 3600, "barclose_ts": pub, "tradeable": tradeable,
         "direction": direction if tradeable else None, "stop_price": stop if tradeable else None,
         "entry_ref": ref, "size_mult": mult}
    return d
def feed(s, mode="sig"):
    FEED["sig"], FEED["mode"] = s, mode
    CUR_SID[0] = (s or {}).get("id")
def placed_for(sid, acct=None):
    return [p for p in PLACED if p[3] == sid and (acct is None or p[0] == acct)]
def micro(rows):
    return sum(p[2] * (1 if p[1].startswith("M") else sizing.MINI_RATIO) for p in rows)
def reset_world():
    PLACED.clear(); BROKERS.clear(); LOOP_DEATHS.clear(); INFO.clear(); feed(None)
    try: os.remove(eqgui._ENTERED_PATH)
    except OSError: pass

NQ_R, GC_R = 1000.0, 1120.0
def accts():
    return {("NQ", 0): cfg("NQ-A", NQ_R), ("NQ", 1): cfg("NQ-B", NQ_R),
            ("GC", 0): cfg("GC-A", GC_R), ("GC", 1): cfg("GC-B", GC_R)}

# =============== Scenario 1: stale -> fresh GC -> no-trade -> fresh NQ -> same id ===============
print("\n== S1 multi-signal sequence ==")
reset_world(); st = Stub(accts()); th = start(st)
feed(sig("S1-NQ-old", "NQ", age=900)); ck("S1.0 loop polls", wait_polls())
ck("S1.1 stale NQ not entered", not placed_for("S1-NQ-old"), PLACED)
ck("S1.1b stale skip event", ("NQ", "stale_signal") in st.skips, st.skips)
feed(sig("S1-GC-1", "GC", age=1)); ck("S1.2 loop still polling after stale", wait_polls())
for a in ("GC-A", "GC-B"):
    ck(f"S1.2 fresh GC entered once on {a}", len({p[1] for p in placed_for('S1-GC-1', a)}) >= 1 and
       micro(placed_for("S1-GC-1", a)) == 20, placed_for("S1-GC-1", a))
feed(sig("S1-NT", "NQ", age=1, tradeable=False)); ck("S1.3 loop polls after entry (session bug)", wait_polls(), LOOP_DEATHS)
ck("S1.3 no-trade -> no entry", not placed_for("S1-NT"))
ck("S1.3 no-trade logged", any("S1-NT" in l and "NO-TRADE" in l for l in st.logs))
feed(sig("S1-NQ-2", "NQ", age=1)); ck("S1.4 polls", wait_polls())
for a in ("NQ-A", "NQ-B"):
    ck(f"S1.4 fresh NQ entered on {a} (10 MNQ eq)", micro(placed_for("S1-NQ-2", a)) == 10, placed_for("S1-NQ-2", a))
n_before = len(PLACED)
ck("S1.5 same id repeated many polls", wait_polls(40))
ck("S1.5 no double entry", len(PLACED) == n_before, PLACED[n_before:])
feed(sig("S1-NQ-3", "NQ", age=1)); wait_polls()
ck("S1.6 2nd fresh NQ same day blocked by once-per-day rule", not placed_for("S1-NQ-3"), placed_for("S1-NQ-3"))
ck("S1.7 loop thread alive", th.is_alive() and not LOOP_DEATHS, LOOP_DEATHS)
ck("S1.8 no entry-thread exception/report_error", STUB_CALLS.get("_report_error", 0) == 0, STUB_CALLS)
stop(st, th); ck("S1.9 loop exits on stop", not th.is_alive())

# =============== Scenario 2: feed errors then recovery ===============
print("\n== S2 feed errors ==")
reset_world(); st = Stub(accts()); th = start(st)
for m in ("exc", "500", "garbage", "wrongkey"):
    feed(sig("S2-x", "GC", age=1), mode=m); ck(f"S2.{m} loop survives", wait_polls(25), LOOP_DEATHS)
ck("S2.1 no entry during errors", not PLACED, PLACED)
print("INFO S2 _feed_errs after exc/500/garbage/wrongkey =", getattr(st, "_feed_errs", None),
      "| 1-min warnings:", sum("1분 이상" in l for l in st.logs), "(HTTP 500 resets the error counter: r.ok False -> {} is 'no signal')")
ck("S2.1c 1-minute warning logged on persistent exceptions", sum("1분 이상" in l for l in st.logs) >= 1)
feed(sig("S2-GC", "GC", age=1)); ck("S2.2 recovery polls", wait_polls())
ck("S2.2 recovered -> GC entered both accts", micro(placed_for("S2-GC", "GC-A")) == 20 and micro(placed_for("S2-GC", "GC-B")) == 20, PLACED)
ck("S2.2b recovery logged", any("정상 복구" in l for l in st.logs))
# errors in the middle after an entry, then a new asset signal
for m in ("exc", "garbage"):
    feed(None, mode=m); wait_polls(5)
feed(sig("S2-NQ", "NQ", age=1)); wait_polls()
ck("S2.3 entry after 2nd error burst", micro(placed_for("S2-NQ", "NQ-A")) == 10 and micro(placed_for("S2-NQ", "NQ-B")) == 10, PLACED)
ck("S2.4 loop alive", th.is_alive() and not LOOP_DEATHS, LOOP_DEATHS)
stop(st, th)

# =============== Scenario 3: [놓친 신호 진입] ===============
print("\n== S3 missed entry ==")
reset_world(); st = Stub(accts()); th = start(st)
PRICE["GC"] = 4139.2      # long moved +3.2 vs ref 4136, stop 4130.4: dist 5.6 -> 8.8
s3 = sig("S3-GC", "GC", age=20 * 60); feed(s3); wait_polls()
ck("S3.1 stale GC skipped by loop", not placed_for("S3-GC"))
ck("S3.1b _last_feed_sig is the stale signal", (getattr(st, "_last_feed_sig", None) or {}).get("id") == "S3-GC")
st._missed_entry(); wait_polls(10)
rows_a, rows_b = placed_for("S3-GC", "GC-A"), placed_for("S3-GC", "GC-B")
ck("S3.2 forced entry placed on both GC accounts", rows_a and rows_b, PLACED)
for a, rows in (("GC-A", rows_a), ("GC-B", rows_b)):
    risk = micro(rows) * (PRICE["GC"] - 4130.4) * sizing.FUTURES_POINT_VALUE["MGC"]
    ck(f"S3.3 _late sizing shrank {a} (<=1R)", 0 < risk <= GC_R + 1e-6 and micro(rows) < 20, (rows, risk))
ck("S3.3b late-entry log line", any("늦은 진입" in l for l in st.logs))
ck("S3.3c NQ untouched", not [p for p in PLACED if p[0].startswith("NQ")])
n0 = len(PLACED); INFO.clear()
st._missed_entry(); wait_polls(10)
ck("S3.4 second press: no double entry", len(PLACED) == n0, PLACED[n0:])
ck("S3.4b second press refused with message", any("이미 진입" in m for m in INFO), INFO)
# bypass the button guard: force id + seen reset directly -> loop rules must still block
st._sig_force_id = "S3-GC"; st._sig_seen = None; wait_polls(10)
ck("S3.5 forced reprocess after entry -> loop blocks (entered today)", len(PLACED) == n0, PLACED[n0:])
stop(st, th)

print("\n== S3b forced after auto-entry ==")
reset_world(); PRICE["GC"] = 4136.0
_orig_age = eqgui.MAX_SIGNAL_AGE_SEC
eqgui.MAX_SIGNAL_AGE_SEC = 2           # let a fresh signal turn stale in seconds
st = Stub(accts()); th = start(st)
feed(sig("S3b-GC", "GC", age=0.5)); wait_polls()
n_auto = len(placed_for("S3b-GC"))
ck("S3b.1 auto entry happened", micro(placed_for("S3b-GC", "GC-A")) == 20 and micro(placed_for("S3b-GC", "GC-B")) == 20, PLACED)
time.sleep(2.2); INFO.clear()
st._missed_entry(); wait_polls(10)
ck("S3b.2 missed-entry after auto-entry -> refused, no entry", len(placed_for("S3b-GC")) == n_auto and INFO, (PLACED, INFO))
stop(st, th)

print("\n== S3c account armed after auto-entry (one entered, one not) ==")
reset_world(); PRICE["GC"] = 4136.0
a3 = accts(); gcb = a3.pop(("GC", 1))
st = Stub(a3); th = start(st)
feed(sig("S3c-GC", "GC", age=0.5)); wait_polls()
ck("S3c.1 GC-A auto-entered", micro(placed_for("S3c-GC", "GC-A")) == 20, PLACED)
time.sleep(2.2)
st._sig_accts[("GC", 1)] = gcb                   # member arms GC-B later
INFO.clear(); st._missed_entry(); wait_polls(10)
# 21d: 같은 자산 계좌가 이 신호로 이미 진입했으면 루프가 자산 전체를 건너뛰므로(재시작 뒤 이중 진입 방지), 버튼이 처음부터 거절한다.
ck("S3c.2 button refuses honestly (an account of this asset already entered)", INFO and "이미 진입한" in INFO[-1], INFO)
ck("S3c.3 GC-A not entered again", micro(placed_for("S3c-GC", "GC-A")) == 20, placed_for("S3c-GC", "GC-A"))
ck("S3c.4 GC-B not entered by the refused button", not placed_for("S3c-GC", "GC-B"), placed_for("S3c-GC", "GC-B"))
stop(st, th)

print("\n== S3d app restart after auto-entry (memory lost, .entered.json kept) ==")
reset_world(); PRICE["GC"] = 4136.0
st = Stub(accts()); th = start(st)
feed(sig("S3d-GC", "GC", age=0.5)); wait_polls()
ck("S3d.1 auto-entered both", micro(placed_for("S3d-GC", "GC-A")) == 20 and micro(placed_for("S3d-GC", "GC-B")) == 20, PLACED)
stop(st, th); time.sleep(2.2); n0 = len(PLACED)
st2 = Stub(accts()); st2._entered_at = eqgui._load_entered(); th = start(st2); wait_polls(10)
INFO.clear(); st2._missed_entry(); wait_polls(10)
ck("S3d.2 after restart, missed-entry press does not double-enter", len(PLACED) == n0, (PLACED[n0:], INFO))
stop(st2, th)
eqgui.MAX_SIGNAL_AGE_SEC = _orig_age

# =============== Scenario 4: stop/restart (new gen) ===============
print("\n== S4 stop/restart ==")
reset_world(); PRICE["GC"] = 4136.0
st = Stub(accts()); th = start(st)
feed(sig("S4-NQ", "NQ", age=1)); wait_polls()
ck("S4.1 NQ entered", micro(placed_for("S4-NQ", "NQ-A")) == 10 and micro(placed_for("S4-NQ", "NQ-B")) == 10, PLACED)
stop(st, th); n0 = len(PLACED)
th2 = start(st); wait_polls(20)
ck("S4.2 restart, same signal still in feed -> no double", len(PLACED) == n0, PLACED[n0:])
feed(sig("S4-GC", "GC", age=1)); wait_polls()
ck("S4.3 new signal processed by new gen", micro(placed_for("S4-GC", "GC-A")) == 20 and micro(placed_for("S4-GC", "GC-B")) == 20, PLACED)
# overlap: start a new gen without waiting for the old loop to exit, then a new signal
n1 = len(PLACED)
th3 = start(st)            # bumps _sig_gen; th2 must quit on its own
feed(sig("S4-GC2", "GC", age=1, direction="SHORT", stop=4141.6)); wait_polls(20)
ck("S4.4 old gen loop exited", not th2.is_alive(), "th2 alive")
ck("S4.5 overlapping gens: 2nd GC same day blocked (once/day), no dup", len(PLACED) == n1, PLACED[n1:])
# new day-key free asset via a fresh Stub-day: clear entered_day to emulate next day, overlap again
st._sig_entered_day.clear()
th4 = start(st)
time.sleep(0.3); feed(sig("S4-NQ-next", "NQ", age=0)); wait_polls(20)
ck("S4.6 overlap restart then new signal: exactly one entry per account",
   micro(placed_for("S4-NQ-next", "NQ-A")) == 10 and micro(placed_for("S4-NQ-next", "NQ-B")) == 10,
   (placed_for("S4-NQ-next"), [l for l in st.logs if "S4-NQ-next" in l or "⏹" in l][-6:], st._sig_seen, st._sig_gen))
ck("S4.7 only newest loop alive", th4.is_alive() and not th3.is_alive(), (th3.is_alive(), th4.is_alive()))
stop(st, th4)
ck("S4.8 no loop deaths", not LOOP_DEATHS, LOOP_DEATHS)

print("\n== S5 hardening: unexpected exception in loop body ==")
reset_world(); st = Stub(accts()); th = start(st); wait_polls(3)
feed(None, mode="nonDict"); alive_nd = wait_polls(5, timeout=3)
ck("S5.1 [hardening] valid-encrypted non-dict payload does not kill loop", alive_nd and th.is_alive(), LOOP_DEATHS)
print("INFO after loop death: st._sig_on =", st._sig_on, "(stays True -> arming another account will not restart the loop)")
stop(st, th)
reset_world(); st = Stub(accts())
_boom = [1]
def _skip_raise(a, why):
    if _boom[0]:
        _boom[0] = 0; raise RuntimeError("simulated failure inside _entry_skip_ev")
st._entry_skip_ev = _skip_raise
th = start(st); feed(sig("S5-NQ-old", "NQ", age=900)); wait_polls(5, timeout=3)
feed(sig("S5-GC", "GC", age=1)); alive2 = wait_polls(5, timeout=3)
ck("S5.2 [hardening] one exception in a helper does not stop all later signals", alive2 and placed_for("S5-GC"), LOOP_DEATHS)
stop(st, th)

print("\nplace_entry calls per (signal, account): [contract x size]")
_agg = {}
for p_ in ALL_PLACED:
    _agg.setdefault((p_[3], p_[0]), []).append(f"{p_[1].split()[0]}x{p_[2]}")
for k_, v_ in sorted(_agg.items()):
    print(f"  {k_[0]:<12} {k_[1]:<5} calls={len(v_)} {v_}")
print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
