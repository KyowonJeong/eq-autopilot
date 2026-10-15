#!/usr/bin/env python3
# 평가 계좌 감시 + 실제 NT8 브리지 끝에서 끝 재현(실기기 2026-10-15 11:04~11:12 CEST 실패). 0 = 통과.
#
# 실패한 장면: 시험판 2026.10.14a(_TEST_BUILD True, 그림자 모드 기본 켜짐), config.yaml daycap_test: true. 17개 계좌 라이브.
# NT8 Sim101(손으로 연 MNQ 12-26 롱 11, 미실현 +$204.50)이 +$150~+$204에서 10초 넘게 두 번 머물렀는데 앱은 정리하지 않았고,
# 감시 로그가 한 줄도 없었고(그림자, 건너뜀, 오류 모두 없음), 서버에 감시 사건(dce)도 오지 않았다. Lucid 세 계좌 상태 줄에는
# '(오늘 남은 $...)'가 끝내 나오지 않았다.
#
# 이 시험은 가짜 브로커를 쓰지 않는다:
#   - 진짜 NT8Broker 브리지 서버(127.0.0.1, 빈 포트)를 앱과 같은 길(_conn_check -> _build_broker -> healthcheck)로 띄운다.
#   - 가짜 애드온 하나가 EQAutopilotBridge.cs PushStateAsync와 같은 모양, 같은 숫자 표기(.NET Convert.ToString 불변 문화권,
#     double = G15, int, long ts)로 1초마다 /v1/state를 POST하고, /v1/pending을 가져가 close를 실행하고 /v1/ack을 보낸다.
#     계좌 집합 = 실기기: Sim101(MNQ 12-26 롱 11, 현금 100,000, NetLiq 100,204.50, 미실현 +204.50), LDI150...0001~3(플랫),
#     9990001(플랫).
#   - 진짜 App 메서드(object.__new__(App) + 속성만 채움 - 없는 속성은 AttributeError로 드러난다)로 [라이브 시작]과 같은 순서
#     (_dc_cache_clear, _daycap_latch_halt_all, _daycap_mode_notice, _conn_check, _arm_fps, _arm_tested_rows)를 밟아 _sig_accts를 채우고,
#     진짜 _daycap_tick을 1초마다 20초 돌린다. 스레드 시작(_start_sig_loop 등), 서버 핑, 화면(root.after)만 기록용으로 바꾼다.
#   - _daycap_mon_wrap은 예외를 삼키지 않게 바꿔 기록한다(드러내기). _daycap_mon_snap이 None을 돌려준 이유는 브로커 객체를
#     건드리지 않고(인증을 부르지 않고) 들여다본다.
# A단계(기대 동작): 20초 안에 Sim101을 정리하고, [시험 모드] 로그, 사건(test), Lucid 시작 잔고와 '오늘 남은' 상태가 있어야 한다.
#   원인(수리 전 실패): 감시 브로커 객체가 브리지에 안 붙은 채 snapshot_fresh()가 늘 False여서 _daycap_mon_snap이 말없이 None이었다.
# B단계(진단): 감시가 쓰는 같은 브로커 객체를 브리지 상태에 붙이면(authenticate) 나머지 길(시작 잔고, NetLiq 교차 확인, 두 push 규칙,
#   그림자 + 시험 + 잠금 판단, close 명령, 플랫 확인, 오늘 닿음)이 모두 도는지 본다 - 다른 막힘이 없는지 확인한다.
# C단계(회귀): 순서에 기대지 않는다 - T-10 증거금 사전 점검(_margin_warns)이 돌기 전에도 감시가 Sim101을 정리한다(수리 전에는 점검 뒤에만).
# D단계: 그림자 모드(시험판 기본)에서 Lucid 평가 계좌에 앱이 넣은 포지션이 하루 상한($4,320)을 넘으면 '정리했을 경우' 한 줄, 정리 주문 없음,
#   서버 보고 줄에 그림자 사건(mode shadow).
# E단계: 진짜 _daycap_mon_wrap, _daycap_tick이 예외를 말없이 삼키지 않는다 - (자리, 예외 종류)마다 한 시간에 한 줄, 루프는 계속.
# F단계: 감시가 계좌를 30초 넘게 못 읽으면 이유와 함께 한 줄(계좌가 push에 없음, 브리지 멈춤), 다시 읽히면 한 줄.
# HOME, APPDATA = 임시 폴더, 127.0.0.1 밖 연결 차단, 키체인 차단, EQ_NO_ALERT=1.
import datetime as _dt
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback

HOME = tempfile.mkdtemp(prefix="eq_nt8e2e_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
for _v in ("EQ_PROFILE", "EQ_DAYCAP_OFF", "EQ_DAYCAP_SHADOW", "http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"

_LOOP = ("127.0.0.1", "localhost", "::1")
_orig_connect = socket.socket.connect


def _guard_connect(self, addr):
    host = addr[0] if isinstance(addr, tuple) else str(addr)
    if host not in _LOOP:
        raise OSError(f"network blocked by test: {host}")
    return _orig_connect(self, addr)


socket.socket.connect = _guard_connect

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui  # noqa: E402
from eqexec.broker.nt8 import NT8Broker  # noqa: E402

E = eqgui
assert E.APP_DIR.startswith(HOME), E.APP_DIR
E._TEST_BUILD = True                                     # 실기기 = 시험판(그림자 모드 기본 켜짐)
E._kc_load = lambda *a, **k: ""                          # 키체인 차단(맥 security 명령을 부르지 않는다)
E._kc_save = lambda *a, **k: False
E._kc_del = lambda *a, **k: None
with open(E.CFG_PATH, "w", encoding="utf-8") as _f:      # config.yaml의 숨은 설정
    _f.write("lang: ko\ndaycap_test: true\n")
E._DAYCAP_SRV = E._prop_reco_validate({"cap_pct": {"nt8": 0.48, "projectx": 0.54}})   # 시험 화면 하루 상한 $4,320 = 9,000 x 0.48(시험용 값)

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:900]}"), flush=True)
    if not c:
        FAIL.append(n)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = _free_port()
TOKEN = "eq-e2e-bridge-token"


# ── .NET MiniJson.Serialize와 같은 글자 ────────────────────────────────────────────────────
def _cs_num(v):
    """Convert.ToString(v, CultureInfo.InvariantCulture): int/long 그대로, double = .NET Framework 'G'(15자리)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    f = float(v)
    if f != f:
        return "NaN"
    if f in (float("inf"), float("-inf")):
        return "Infinity" if f > 0 else "-Infinity"
    s = format(f, ".15g")
    if "e" in s:
        m, ex = s.split("e")
        s = f"{m}E{'+' if int(ex) >= 0 else '-'}{abs(int(ex)):02d}"
    return s


def _cs_str(s):
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch < " ":
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def cs_json(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return _cs_str(v)
    if isinstance(v, dict):
        return "{" + ",".join(_cs_str(k) + ":" + cs_json(x) for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(cs_json(x) for x in v) + "]"
    return _cs_num(v)


# ── 가짜 애드온(EQAutopilotBridge.cs의 TickAsync: PushState → DrainCommands) ──────────────────────
class FakeAddon:
    PV = {"MNQ": 2.0, "NQ": 20.0, "MGC": 10.0, "GC": 100.0}

    def __init__(self, port, token):
        self.port, self.token = port, token
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.cmds, self.acks, self.push_n, self.push_err = [], [], 0, []
        self.paused = False                                  # True = NinjaTrader가 멈춘 것처럼 push를 보내지 않는다(F단계)
        self.reset()

    def reset(self):
        with self.lock:
            # Account.All 가운데 ConnectionStatus.Connected 계좌(시험 NT8: Tradovate 연결 아래 Lucid 세 계좌, 9990001, Sim101).
            # 값은 Account.Get(AccountItem.*, Currency.UsDollar) = double.
            self.accts = {
                "Sim101": {"cash": 100000.0, "real": 0.0},
                "LDI150A0001": {"cash": 150812.4, "real": 0.0},
                "LDI150A0002": {"cash": 149375.0, "real": 0.0},
                "LDI150A0003": {"cash": 151020.0, "real": 0.0},
                "9990001": {"cash": 50000.0, "real": 0.0},
            }
            # Position: Quantity int, AveragePrice double, Instrument.FullName "MNQ 12-26". 미실현 +204.50 = (mark - avg) x 11 x $2.
            self.pos = {("Sim101", "MNQ 12-26"): {"q": 11, "avg": 25010.0, "mark": 25010.0 + 204.5 / 22.0}}
            t0 = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.execs = [{"account": "Sim101", "exec_id": "sim-e1", "instrument": "MNQ 12-26", "side": "BUY", "qty": 11,
                           "price": 25010.0, "time": t0, "commission": 0.0, "pnl": 0.0}]

    def _upnl(self, acct):
        u = 0.0
        for (a, ins), p in self.pos.items():
            if a == acct and p["q"]:
                u += (p["mark"] - p["avg"]) * p["q"] * self.PV[ins.split(" ")[0]]
        return u

    def state_body(self):
        with self.lock:
            accounts, positions = [], []
            for name, a in self.accts.items():
                u = self._upnl(name)
                accounts.append({"name": name, "cash_value": float(a["cash"]), "realized_pnl": float(a["real"]),
                                 "net_liq": float(a["cash"] + u), "unrealized_pnl": float(u)})
                for (pa, ins), p in self.pos.items():
                    if pa == name and p["q"]:
                        positions.append({"account": name, "instrument": ins, "net_qty": int(p["q"]),
                                          "avg_price": float(p["avg"])})
            return {"accounts": accounts, "positions": positions, "executions": [dict(e) for e in self.execs],
                    "orders": [], "bridge_ver": "2026.09.23a", "ts": int(time.time())}

    def _req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            hd = {"X-EQ-Bridge-Token": self.token}
            data = None
            if body is not None:
                data = cs_json(body).encode("utf-8")
                hd["Content-Type"] = "application/json; charset=utf-8"
            c.request(method, path, body=data, headers=hd)
            r = c.getresponse()
            raw = r.read()
            if r.status >= 300:
                raise RuntimeError(f"{method} {path} -> {r.status}")
            return json.loads(raw or b"{}")
        finally:
            c.close()

    def _exec_close(self, c):
        acct, ins = str(c.get("account") or ""), str(c.get("instrument") or "")
        with self.lock:
            if acct not in self.accts:                       # FindAccount: a.Name == name(대소문자 그대로) && Connected
                raise RuntimeError("account/instrument missing")
            p = self.pos.get((acct, ins))
            if p and p["q"]:
                pnl = (p["mark"] - p["avg"]) * p["q"] * self.PV[ins.split(" ")[0]]
                self.accts[acct]["cash"] += pnl
                self.accts[acct]["real"] += pnl
                self.execs.append({"account": acct, "exec_id": f"x{len(self.execs)}", "instrument": ins,
                                   "side": "SELL" if p["q"] > 0 else "BUY", "qty": abs(p["q"]), "price": p["mark"],
                                   "time": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "commission": 0.0, "pnl": pnl})
                p["q"] = 0
        return {"ok": True}

    def tick(self):
        try:
            self._req("POST", "/v1/state", self.state_body())
            self.push_n += 1
        except Exception as ex:                              # 앱 꺼짐 = 조용히 대기(애드온과 같다)
            self.push_err.append(str(ex)[:80])
            return
        try:
            cmds = (self._req("GET", "/v1/pending") or {}).get("commands") or []
        except Exception:
            return
        for c in cmds:
            self.cmds.append(dict(c))
            op = c.get("op")
            try:
                if op == "close":
                    ack = self._exec_close(c)
                else:
                    ack = {"ok": False, "error": "unexpected op in this test: " + str(op)}
            except Exception as ex:
                ack = {"ok": False, "error": str(ex)}
            ack["tid"] = c.get("tid")
            self.acks.append(dict(ack))
            try:
                self._req("POST", "/v1/ack", ack)
            except Exception:
                pass

    def run(self):
        while not self.stop_ev.is_set():
            if not self.paused:
                self.tick()
            self.stop_ev.wait(1.0)

    def closes_for(self, acct):
        return [c for c in self.cmds if c.get("op") == "close" and c.get("account") == acct]


ADDON = FakeAddon(PORT, TOKEN)
threading.Thread(target=ADDON.run, daemon=True, name="fake-addon").start()


# ── 진짜 App(생성자 없이 - 속성만 채운다) ───────────────────────────────────────────────────
class FakeRoot:
    def __init__(self):
        self.calls = []

    def after(self, ms, fn=None, *a):
        self.calls.append((ms, getattr(fn, "__name__", str(fn))))
        return f"after#{len(self.calls)}"


def new_app():
    App = E.App
    app = object.__new__(App)
    app.lang = "ko"
    app.LOG = []

    def _log(m):
        app.LOG.append(str(m))
        print("    LOG " + str(m).replace("\n", " "), flush=True)
    app.log = _log
    app.root = FakeRoot()
    app._gate = {"ok": True, "enabled": True, "force_dry_run": False, "dce": True,
                 "caps": {"use": True, "autoentry": True, "connect": True, "fullexit": True}}
    app._token = "eq-test-member-token"
    app._sig_accts, app._auto_accts, app._sig_parked = {}, {}, {}
    app._sig_on = app._auto_on = False
    app._conn_by_broker = {}
    app._open_ctx = {}
    app._watch_only = False
    app._dc_mon = E._load_daycap_mon()
    app.PINGS, app.EVS, app.FILLS = [], [], []
    # 스레드 시작, 서버 통신, 화면만 기록용으로(감시 길과 무관)
    app._start_sig_loop = lambda: setattr(app, "_sig_on", True)
    app._start_auto_loop = lambda: setattr(app, "_auto_on", True)
    app._set_auto_ind = lambda *a, **k: None
    app._set_sig_ind = lambda *a, **k: None
    app._alive_ping = lambda *a, **k: app.PINGS.append(dict(k))
    app._send_ev = lambda kind, asset, **k: app.EVS.append((kind, asset, k))
    app._send_fill = lambda asset, closed=False, why=None: app.FILLS.append((asset, closed, why))
    nt8_cr = {"f1": TOKEN, "f3": str(PORT)}
    px_cr = {"f1": "owner@example.test", "f3": ""}

    def _lucid(i):
        _id = f"LDI150A000{i}"
        return E._new_acct(2200.0, _id, True, f"Lucid {i}",
                           prop={"on": True, "type": "test", "stage": "eval", "stage_for": _id.casefold()}, broker="nt8")

    def _xfa(tail):
        return E._new_acct(450.0, f"EXPRESS-V2-00000{tail}", True, f"XFA {tail}", prop={"on": True, "type": "funded"},
                           broker="projectx", topstep=True)

    def _rows(with_sim):
        rows = [_xfa("4101"), _xfa("4102"), _xfa("4103"), _lucid(1), _lucid(2), _lucid(3),
                E._new_acct(300.0, "9990001", True, "NT8 9990001", broker="nt8")]
        if with_sim:
            rows.append(E._new_acct(100.0, "Sim101", True, "Sim101", broker="nt8"))
        return rows
    app._acfg = {
        "NQ": {"broker": "projectx", "creds": {"projectx": px_cr, "nt8": nt8_cr}, "accounts": _rows(True)},
        "GC": {"broker": "projectx", "creds": {"projectx": px_cr, "nt8": nt8_cr}, "accounts": _rows(False)},
        "BTC": {"broker": "bybit", "creds": {"bybit": {"f1": "bybit-key", "f3": ""}, "bitget": {"f1": "bitget-key", "f3": "pp"}},
                "accounts": [E._new_acct(500.0, "", True, "Bybit", broker="bybit"),
                             E._new_acct(500.0, "", True, "Bitget", broker="bitget")]},
    }
    # 드러내기: 감시 스레드가 예외를 삼키지 않게(기록 후 그대로 둔다), snapshot None 이유를 객체를 건드리지 않고 기록
    app.EXC, app.SNAPNONE, app.TARGETS = [], [], []

    def _wrap_visible():
        try:
            App._daycap_mon_scan(app)
        except Exception:
            app.EXC.append(traceback.format_exc())
            print("    EXC " + app.EXC[-1].replace("\n", " | "), flush=True)
        finally:
            app._dc_busy = False
    app._daycap_mon_wrap = _wrap_visible

    def _targets_probe():
        tg = App._daycap_mon_targets(app)
        app.TARGETS.append(sorted(t["key"] for t in tg))
        return tg
    app._daycap_mon_targets = _targets_probe

    def _snap_probe(b, bk, sc, acache=None, fresh=False, fresh_accts=False):
        r = App._daycap_mon_snap(app, b, bk, sc, acache, fresh, fresh_accts)
        if r is None and bk == "nt8":
            st = getattr(b, "_state", None)
            ent = NT8Broker._BRIDGES.get(getattr(b, "port", None))
            shared = ent[0] if ent else None
            age = (time.time() - shared.last_state_ts) if (shared and shared.last_state_ts) else None
            names = [a.get("name") for a in ((shared.last_state if shared else {}) or {}).get("accounts", [])]
            app.SNAPNONE.append({"sc": sc, "fresh": fresh, "inst_state_attached": st is not None,
                                 "snapshot_fresh()": bool(st and st.last_state_ts and time.time() - st.last_state_ts <= 10.0),
                                 "shared_bridge_age_s": (round(age, 2) if age is not None else None),
                                 "acct_in_shared_push": str(sc).casefold() in {str(n).casefold() for n in names},
                                 "same_bridge_state": (st is shared) if st is not None else None})
        return r
    app._daycap_mon_snap = _snap_probe
    return app


def go_live(app):
    """[라이브 시작]의 감시 관련 순서 그대로: 캐시 비움, 래치 멈춤, 안내, 연결 테스트(NT8), 행 지문, 무장."""
    app._daycap_latch_halt_all()
    app._dc_cache_clear()
    app._daycap_mode_notice()
    err = app._conn_check("NQ", "nt8")
    fps = app._arm_fps(["NQ", "GC", "BTC"])
    app._watch_only = False
    n = app._arm_tested_rows(["NQ", "GC", "BTC"], True, True, fps)
    return err, n


def drive(app, seconds, stop_when=None):
    """root.after가 1초마다 _daycap_tick을 부르는 것과 같이 진짜 _daycap_tick을 부른다(감시는 그 안에서 스레드로 돈다)."""
    t_end = time.time() + seconds
    while time.time() < t_end:
        app._daycap_tick()
        time.sleep(1.0)
        if stop_when is not None and stop_when():
            break
    t_wait = time.time() + 25                                 # 마지막 회차 스레드(close ack 최대 20초 대기 포함)가 끝날 때까지
    while getattr(app, "_dc_busy", False) and time.time() < t_wait:
        time.sleep(0.2)


def _lucid_status(app):
    st = app._dc_state()["status"]
    return {k: (v or {}).get("room") for k, v in st.items() if k.startswith("nt8|ldi")}


def _expect(tag, app):
    day = E._daycap_day_start()[1]
    sim_closed = bool(ADDON.closes_for("Sim101"))
    test_log = [l for l in app.LOG if "[시험 모드]" in l and "Sim101" in l]
    ev = [(e.get("mode"), e.get("a4"), e.get("bk")) for e in E._dce_pending()]
    base_log = [l for l in app.LOG if "거래일 시작 잔고" in l]
    lst = _lucid_status(app)
    ck(f"{tag}1 Sim101(손으로 연 MNQ 11, +$204.50)이 20초 안에 [시험 모드]로 정리됨(애드온이 close 명령을 받음)",
       sim_closed and any("정리합니다" in l for l in test_log),
       {"closes": ADDON.cmds[-3:], "test_log": test_log[-2:], "snap_none": app.SNAPNONE[-2:], "exc": app.EXC[-1:]})
    ck(f"{tag}2 정리 뒤 오늘 닿음 표시 + 마침 로그",
       app._daycap_hit_today("nt8", "Sim101", day) and any("하루 이익 상한 익절을 마쳤습니다" in l for l in test_log),
       {"hit": app._dc_state()["hit"], "test_log": test_log[-2:]})
    ck(f"{tag}3 서버 보고 줄에 시험 정리 사건 하나(mode test, 끝 4자리 m101, bk nt8)",
       ("test", "m101", "nt8") in ev, ev)
    ck(f"{tag}4 Lucid 세 계좌 거래일 시작 잔고 로그(Sim101 포함 넷, Sim101은 'NinjaTrader', 나머지는 'Lucid')",
       len({l.split("]")[0] for l in base_log}) >= 4
       and all(("NinjaTrader 거래일" in l) == ("[Sim101]" in l) for l in base_log)
       and all(("Lucid 거래일" in l) == ("[LDI" in l) for l in base_log), base_log)
    ck(f"{tag}5 Lucid 세 계좌 상태 줄 '오늘 남은' 금액 있음(room)",
       len(lst) == 3 and all(v is not None for v in lst.values()), lst)
    ck(f"{tag}6 감시 스레드 예외 없음", not app.EXC, app.EXC[-1:])


def _reset_world():
    """감시 기록, 상태, 사건, 맥락 파일을 지우고 애드온을 처음 모습(Sim101 MNQ 11 열림)으로 되돌린다."""
    for _p in (E._DAYCAP_MON_PATH, E._DAYCAP_STATE_PATH, E._DCE_PATH, E._OPENCTX_PATH):
        try:
            os.remove(_p)
        except OSError:
            pass
    ADDON.reset()
    ADDON.cmds.clear()
    time.sleep(1.2)


def main():
    print(f"HOME={HOME} port={PORT}", flush=True)
    # ── A: 실기기 그대로(기대 동작) ──
    app = new_app()
    err, n = go_live(app)
    ck("0a NT8 연결 테스트 통과(진짜 브리지 서버 + 가짜 애드온 push)", err is None, err)
    ck("0b 무장 17개(NQ 8, GC 7, BTC 2) - 실기기 로그 '17개 계좌'와 같음", n == 17, n)
    _keys = sorted(app._sig_accts)
    ck("0c Sim101 행이 무장됨(live, nt8, 수동 아님)",
       any(app._sig_accts[k].get("acct") == "Sim101" and app._sig_accts[k].get("live") and not app._sig_accts[k].get("manual")
           for k in _keys), _keys)
    ck("0d 시험 모드 켜짐, 그림자 모드 켜짐(시험판 기본), Sim101 = 시험 대상",
       E._daycap_test_on() and E._daycap_shadow() and E._daycap_test_acct("nt8", "Sim101"))
    _nb = E._build_broker("nt8", TOKEN, "", str(PORT), [])    # 감시가 _passtp_broker로 만드는 것과 같은 새 인스턴스
    _was = _nb._state is None
    ck("0e 새로 만든 NT8Broker: authenticate 없이도 snapshot_fresh() True, state_ts() > 0(공유 브리지에 스스로 붙음)",
       _was and _nb.snapshot_fresh() and _nb.state_ts() > 0 and _nb._state is NT8Broker._BRIDGES[PORT][0],
       (_was, _nb._state is not None))
    _nb2 = E._build_broker("nt8", "other-token", "", str(PORT), [])
    ck("0f 토큰이 다른 인스턴스는 붙지 못해 snapshot_fresh() False, state_ts() 0(예외 없음)",
       _nb2.snapshot_fresh() is False and _nb2.state_ts() == 0.0)
    print("── A: 진짜 _daycap_tick 20초 ──", flush=True)
    drive(app, 20, stop_when=lambda: bool(ADDON.closes_for("Sim101")) and not getattr(app, "_dc_busy", False))
    print(f"    targets(last)={app.TARGETS[-1] if app.TARGETS else None} scans={len(app.TARGETS)} "
          f"snap_none={len(app.SNAPNONE)} exc={len(app.EXC)} pushes={ADDON.push_n}", flush=True)
    if app.SNAPNONE:
        print("    snap None 예:", app.SNAPNONE[0], flush=True)
    _pb = [b for b in (getattr(app, "_passtp_bk", {}) or {}).values() if isinstance(b, NT8Broker)]
    print(f"    감시 브로커 객체(_passtp_bk의 NT8Broker) {len(_pb)}개, _state 붙음 = {[b._state is not None for b in _pb]}",
          flush=True)
    _expect("A", app)

    # ── B: 진단 - 감시가 쓰는 같은 객체를 브리지 상태에 붙이면(authenticate) 나머지 길이 모두 도는가 ──
    print("── B: 진단(감시 브로커 객체에 authenticate) ──", flush=True)
    _reset_world()
    app2 = new_app()
    err2, n2 = go_live(app2)
    app2._daycap_tick()                                   # 첫 회차가 감시 브로커 객체를 만든다(_passtp_broker 캐시)
    t_w = time.time() + 10
    while getattr(app2, "_dc_busy", False) and time.time() < t_w:
        time.sleep(0.1)
    for b in list((getattr(app2, "_passtp_bk", {}) or {}).values()):
        if isinstance(b, NT8Broker):
            b.authenticate()
    drive(app2, 20, stop_when=lambda: bool(ADDON.closes_for("Sim101")) and not getattr(app2, "_dc_busy", False)
          and app2._daycap_hit_today("nt8", "Sim101", E._daycap_day_start()[1]))
    _expect("B", app2)
    _sh = [l for l in app2.LOG if "정리했을 경우: " in l]         # 그림자 줄('정리했을 경우: ...') - 시작 안내 문구는 빼고
    ck("B7 그림자 모드여도 Lucid, 9990001, XFA에는 정리 주문 없음(애드온 close는 Sim101 하나뿐)",
       [c.get("account") for c in ADDON.cmds if c.get("op") == "close"] == ["Sim101"] and not _sh,
       (ADDON.cmds, _sh))
    # ── C: 회귀 - 순서에 기대지 않는다. 수리 전에는 T-10 증거금 사전 점검(_margin_warns)이 같은 캐시 객체로 account_balance를 불러
    #    브리지에 붙인 뒤에야 감시가 돌았다(실기기 11:04~11:12처럼 앱을 켠 뒤 첫 T-10 점검 전에는 감시가 눈을 감았다).
    print("── C: 회귀(증거금 사전 점검 전) ──", flush=True)
    _reset_world()
    app3 = new_app()
    go_live(app3)
    drive(app3, 12, stop_when=lambda: bool(ADDON.closes_for("Sim101")) and not getattr(app3, "_dc_busy", False))
    _pre = bool(ADDON.closes_for("Sim101"))
    _att = [b._state is not None for b in (getattr(app3, "_passtp_bk", {}) or {}).values() if isinstance(b, NT8Broker)]
    ck("C1 사전 점검 전에도 감시 브로커 객체가 브리지에 붙고 Sim101을 정리함(close 계좌 이름 = NT8이 보낸 'Sim101')",
       _pre and _att == [True] and [c.get("account") for c in ADDON.closes_for("Sim101")][:1] == ["Sim101"],
       (_pre, _att, ADDON.cmds[-2:], app3.SNAPNONE[-2:]))

    # ── D: 그림자 모드(시험판 기본) + Lucid 평가 계좌에 앱이 넣은 포지션이 하루 상한을 넘음 ──
    print("── D: Lucid 평가 계좌 하루 상한 넘음(그림자 모드) ──", flush=True)
    _reset_world()
    app4 = new_app()
    go_live(app4)
    drive(app4, 4)                                        # 첫 회차들: Lucid 거래일 시작 잔고를 잡는다(플랫)
    LD = "LDI150A0001"
    _day = E._daycap_day_start()[1]
    _b4 = next(b for b in app4._passtp_bk.values() if isinstance(b, NT8Broker))
    with ADDON.lock:                                       # 앱이 진입한 MNQ 11 롱(미실현 +$5,000 > 상한 $4,320)
        ADDON.pos[(LD, "MNQ 12-26")] = {"q": 11, "avg": 25000.0, "mark": 25000.0 + 5000.0 / 22.0}
    app4._daycap_mon_note("NQ", _b4, {"broker": "nt8", "sc": LD}, LD, "MNQ 12-26", 24900.0, "LONG", 11,
                          {"lbl": "Lucid 1", "stage": "eval", "day": _day, "risk": 2200.0})
    _sh_ok = lambda: any("정리했을 경우: " in l and f"[Lucid 1]" in l for l in app4.LOG)
    drive(app4, 15, stop_when=lambda: _sh_ok() and not getattr(app4, "_dc_busy", False))
    _sh = [l for l in app4.LOG if "정리했을 경우: " in l]
    _ev4 = [(e.get("mode"), e.get("a4"), e.get("bk"), e.get("kind")) for e in E._dce_pending()]
    ck("D1 Lucid 1: '정리했을 경우' 한 줄(하루 이익 상한 $4,320.00을 넘어, 그림자 모드라 주문 없음)",
       len([l for l in _sh if "[Lucid 1]" in l]) == 1 and "$4,320.00" in _sh[0] and "그림자 모드라 주문은 내지 않았습니다" in _sh[0],
       (_sh, app4.LOG[-6:]))
    ck("D2 Lucid 1에는 정리 주문이 나가지 않음(애드온 close는 Sim101 시험 정리뿐)",
       not ADDON.closes_for(LD) and all(c.get("account") == "Sim101" for c in ADDON.cmds if c.get("op") == "close"),
       ADDON.cmds)
    ck("D3 서버 보고 줄에 그림자 사건(mode shadow, 끝 4자리 0001, nt8, cap)", ("shadow", "0001", "nt8", "cap") in _ev4, _ev4)
    _st4 = _lucid_status(app4)
    ck("D4 Lucid 세 계좌 상태 줄 '오늘 남은'(Lucid 1은 상한 넘어 음수)",
       len(_st4) == 3 and all(v is not None for v in _st4.values()) and (_st4.get("nt8|ldi150a0001") or 0) < 0, _st4)
    with ADDON.lock:
        ADDON.pos.pop((LD, "MNQ 12-26"), None)

    # ── E: 진짜 _daycap_mon_wrap, _daycap_tick - 예외를 말없이 삼키지 않는다 ──
    print("── E: 감시 예외 한 시간에 한 줄 ──", flush=True)
    app5 = new_app()
    del app5._daycap_mon_wrap                            # 진짜 _daycap_mon_wrap(클래스 메서드)
    _n0 = len(app5.LOG)
    _boom = {"exc": ValueError("boom-1")}

    def _raise_targets():
        raise _boom["exc"]
    app5._daycap_mon_targets = _raise_targets
    for _i in range(3):
        app5._dc_busy = True
        app5._daycap_mon_wrap()
    _l1 = [l for l in app5.LOG[_n0:] if "예상하지 못한 오류" in l]
    ck("E1 같은 예외 세 번 = 한 줄(자리 scan, ValueError), 루프 계속(_dc_busy 풀림)",
       len(_l1) == 1 and "scan" in _l1[0] and "ValueError: boom-1" in _l1[0] and "_raise_targets" in _l1[0]
       and app5._dc_busy is False, _l1)
    _boom["exc"] = KeyError("k")
    app5._daycap_mon_wrap()
    app5._daycap_mon_wrap()
    _l2 = [l for l in app5.LOG[_n0:] if "예상하지 못한 오류" in l]
    ck("E2 다른 예외 종류(KeyError)는 따로 한 줄", len(_l2) == 2 and "KeyError" in _l2[1], _l2)
    for _kk in list(app5._dc_hourly):
        app5._dc_hourly[_kk] -= E._DAYCAP_NOTE_EVERY_S + 1   # 한 시간이 지났다
    _boom["exc"] = ValueError("boom-2")
    app5._daycap_mon_wrap()
    _l3 = [l for l in app5.LOG[_n0:] if "예상하지 못한 오류" in l]
    ck("E3 한 시간 뒤 같은 종류는 다시 한 줄", len(_l3) == 3 and "boom-2" in _l3[2], _l3)
    app5.lang = "en"
    _boom["exc"] = TypeError("t")
    app5._daycap_mon_wrap()
    ck("E4 EN 문구", "The evaluation watch hit an unexpected error (scan, TypeError: t" in app5.LOG[-1]
       and "at most once an hour" in app5.LOG[-1], app5.LOG[-1:])
    app5.lang = "ko"
    _after0 = len(app5.root.calls)
    app5._dc_day_seen = "1999-01-01"

    def _rows_refresh_boom():
        raise RuntimeError("rows")
    app5._daycap_rows_refresh = _rows_refresh_boom
    app5._sig_accts = {}
    app5._dc_mon = {}
    app5._daycap_tick()
    app5._daycap_tick()
    _l4 = [l for l in app5.LOG if "예상하지 못한 오류가 났습니다(tick, RuntimeError: rows" in l]
    ck("E5 _daycap_tick 예외도 한 줄, 다음 tick 예약은 그대로", len(_l4) == 1 and len(app5.root.calls) == _after0 + 2,
       (_l4, app5.root.calls[-2:]))

    # ── F: 감시가 계좌를 30초 넘게 못 읽으면 이유와 함께 한 줄, 다시 읽히면 한 줄 ──
    print("── F: 못 읽음 안내 ──", flush=True)
    _reset_world()
    with ADDON.lock:
        ADDON.pos.clear()                                 # 이 단계는 포지션 없이(Sim101 시험 정리가 끼지 않게)
    app6 = new_app()
    go_live(app6)
    drive(app6, 3)
    L2 = "LDI150A0002"
    _k2 = "nt8|" + L2.casefold()
    with ADDON.lock:
        _acc2 = ADDON.accts.pop(L2)                       # NT8에서 이 계좌 연결이 끊김(push 계좌 목록에서 빠짐)
    time.sleep(1.5)
    app6._dc_map("_dc_due")[_k2] = 0
    drive(app6, 2)
    app6._dc_snapmiss[_k2] = time.time() - E._DAYCAP_SNAP_MISS_S - 1   # 30초가 지난 것처럼
    app6._dc_map("_dc_due")[_k2] = 0
    drive(app6, 2)
    _m1 = [l for l in app6.LOG if "평가 계좌 감시: NinjaTrader가 보내는 계좌 목록에 " + L2 in l]
    drive(app6, 6)
    _m1b = [l for l in app6.LOG if "평가 계좌 감시: NinjaTrader가 보내는 계좌 목록에 " + L2 in l]
    ck("F1 계좌가 push에 30초 넘게 없음 = 그 계좌 한 줄(이유, 확인할 것), 그 뒤 반복 없음",
       len(_m1) == 1 and len(_m1b) == 1 and "[Lucid 2]" in _m1[0], (_m1b, app6.LOG[-4:]))
    with ADDON.lock:
        ADDON.accts[L2] = _acc2
    time.sleep(1.5)
    app6._dc_map("_dc_due")[_k2] = 0
    drive(app6, 2)
    _b1 = [l for l in app6.LOG if "[Lucid 2] 평가 계좌 감시: 이 계좌를 다시 읽고 있습니다" in l]
    ck("F2 다시 보이면 한 줄", len(_b1) == 1, app6.LOG[-4:])
    ADDON.paused = True                                    # NinjaTrader 멈춤(push 없음)
    _st = NT8Broker._BRIDGES[PORT][0]
    with _st.lock:
        _st.last_state_ts = time.time() - 20
    for _kk in [k for k in list(app6._dc_map("_dc_due")) if k.startswith("nt8|")]:
        app6._dc_due[_kk] = 0
    drive(app6, 2)
    for _kk in [k for k in list(app6._dc_snapmiss) if k.startswith("nt8|")]:
        app6._dc_snapmiss[_kk] = time.time() - E._DAYCAP_SNAP_MISS_S - 1
    for _kk in [k for k in list(app6._dc_map("_dc_due")) if k.startswith("nt8|")]:
        app6._dc_due[_kk] = 0
    drive(app6, 2)
    _s1 = [l for l in app6.LOG if "NinjaTrader 브리지에서" in l and "새 값이 오지 않아" in l]
    ck("F3 브리지 멈춤 = 계좌마다가 아니라 한 줄(NinjaTrader, 애드온 확인)", len(_s1) == 1 and "EQAutopilotBridge" in _s1[0],
       (_s1, app6.LOG[-4:]))
    ADDON.paused = False
    time.sleep(1.5)
    for _kk in [k for k in list(app6._dc_map("_dc_due")) if k.startswith("nt8|")]:
        app6._dc_due[_kk] = 0
    drive(app6, 2)
    _s2 = [l for l in app6.LOG if "NinjaTrader 브리지에서 다시 값이 옵니다" in l]
    ck("F4 브리지가 다시 보내면 한 줄", len(_s2) == 1, app6.LOG[-4:])
    ck("F5 C~F 감시 스레드 예외 없음", not (app3.EXC or app4.EXC or app6.EXC), (app3.EXC + app4.EXC + app6.EXC)[-1:])
    print("결과: " + ("통과" if not FAIL else f"실패 {len(FAIL)}건: " + ", ".join(FAIL)), flush=True)
    ADDON.stop_ev.set()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
