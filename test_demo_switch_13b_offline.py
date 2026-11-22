#!/usr/bin/env python3
# EQ Autopilot 2026.11.13b 오프라인 시험 - 거래소 데모 스위치(대표 2026-11-13 "이번 빌드에 다 해"). 0 = 통과. 네트워크 차단.
# --demo 또는 EQ_DEMO=1: Bybit = api-demo.bybit.com, OKX = x-simulated-trading 헤더, Bitget = paptrading 헤더. 스위치가 없으면 종전 그대로.
import os
import socket
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HOME = tempfile.mkdtemp(prefix="eq_13b_")
os.environ["HOME"] = os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_DEMO", None)
os.environ.pop("EQ_OKX_DEMO", None)
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


import eqgui as E  # noqa: E402
import requests  # noqa: E402
from eqexec.broker import bitget as BG  # noqa: E402

ck("0 앱 버전 2026.11.13b 이상", E.App._APP_VER >= "2026.11.13b", E.App._APP_VER)
ck("1 스위치 없으면 DEMO False", E.DEMO is False)
b = E._build_broker("bybit", "k", "s", "", [])
ck("2 실계좌 앱 Bybit = api.bybit.com", b.base == "https://api.bybit.com", b.base)
b = E._build_broker("bybit", "k", "s", "1", [])
ck("3 테스트넷 칸 1 = api-testnet(종전)", b.base == "https://api-testnet.bybit.com", b.base)
o = E._build_broker("okx", "k", "s", "p", [])
ck("4 실계좌 앱 OKX demo False", o.demo is False)
g = E._build_broker("bitget", "k", "s", "p", [])
ck("5 실계좌 앱 Bitget demo False", g.demo is False)
# Bitget 요청 헤더
SEEN = []


class _R:
    status_code = 200

    def json(self):
        return {"code": "00000", "data": {}}


def _fake(url, headers=None, timeout=None, data=None):
    SEEN.append(dict(headers or {}))
    return _R()


requests.get = _fake
requests.post = _fake
try:
    g._req("GET", "/api/v2/mix/account/accounts", {"productType": "USDT-FUTURES"})
except Exception:
    pass
ck("6 실계좌 앱 Bitget 요청에 paptrading 없음", SEEN and "paptrading" not in SEEN[-1], SEEN[-1:] )
E.DEMO = True                       # 스위치를 켠 앱(--demo)
b = E._build_broker("bybit", "k", "s", "", [])
ck("7 데모 앱 Bybit = api-demo.bybit.com", b.base == "https://api-demo.bybit.com", b.base)
b = E._build_broker("bybit", "k", "s", "1", [])
ck("8 데모 앱은 테스트넷 칸보다 데모가 먼저", b.base == "https://api-demo.bybit.com", b.base)
o = E._build_broker("okx", "k", "s", "p", [])
ck("9 데모 앱 OKX demo True(x-simulated-trading)", o.demo is True)
g = E._build_broker("bitget", "k", "s", "p", [])
SEEN.clear()
try:
    g._req("GET", "/api/v2/mix/account/accounts", {"productType": "USDT-FUTURES"})
except Exception:
    pass
ck("10 데모 앱 Bitget 요청에 paptrading: 1", SEEN and SEEN[-1].get("paptrading") == "1", SEEN[-1:])
ck("11 창 제목에 [DEMO]", "[DEMO]" in E._window_title(), E._window_title())
E.DEMO = False
ck("12 스위치 끄면 제목에 DEMO 없음", "DEMO" not in E._window_title())
# 실행 인자와 환경 변수로 켜지는지(새 프로세스)
for args, env, want in ((["--demo"], {}, "True"), ([], {"EQ_DEMO": "1"}, "True"), ([], {}, "False")):
    _env = dict(os.environ, HOME=tempfile.mkdtemp(), APPDATA=tempfile.mkdtemp(), **env)
    _env.pop("EQ_DEMO", None) if not env else None
    r = subprocess.run([sys.executable, "-c", "import sys; sys.argv += %r; sys.path.insert(0, %r); import eqgui; print(eqgui.DEMO)"
                        % (args, HERE)], capture_output=True, text=True, env=_env, timeout=120)
    ck(f"13 {args or env or '없음'} → DEMO {want}", r.stdout.strip().splitlines()[-1:] == [want], (r.stdout[-200:], r.stderr[-300:]))
# 데모 모드 계좌 안전장치(_demo_acct_ok): 시뮬 계좌만
ck("14 데모 앱 주문 허용: 거래소 3곳(데모 주소 강제), Topstep PRAC, NT8 Sim101/Sim102",
   all(E._demo_acct_ok(b, n) for b, n in (("bybit", ""), ("bitget", ""), ("okx", ""), ("projectx", "PRAC-V2-12345-67890"),
                                          ("nt8", "Sim101"), ("nt8", "sim102"))))
ck("15 데모 앱 주문 거부: Topstep 평가, 펀디드, 숫자 id, Lucid 실계좌, IBKR, Tradovate, 빈 이름",
   not any(E._demo_acct_ok(b, n) for b, n in (("projectx", "150KTC-V2-123"), ("projectx", "EXPRESS-V2-1"), ("projectx", "12345"),
                                              ("nt8", "LDE1234-01"), ("nt8", "Playback101"), ("ibkr", "DU123"), ("tradovate", "x"),
                                              ("projectx", ""))))
_src = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
ck("16 배선: 진입 직전(계좌별 로그 앞)에 DEMO 안전장치, 건너뜀 사유 demo_live",
   "if DEMO and not _demo_acct_ok(_broker, sc):" in _src and '_entry_skip_ev(asset, "demo_live")' in _src
   and _src.index("if DEMO and not _demo_acct_ok(_broker, sc):") < _src.index("_record_real_r(asset, sc or _broker, one_r)"))
# 테스트용 앱 빌드(eq_flavor.txt = test): 늘 데모, 설정 폴더 EQAutopilot-test, 제목 EQ Autopilot Test
_fd = tempfile.mkdtemp()
open(os.path.join(_fd, "eq_flavor.txt"), "w").write("test\n")
_env = dict(os.environ, HOME=tempfile.mkdtemp(), APPDATA=tempfile.mkdtemp())
_env.pop("EQ_DEMO", None)
r = subprocess.run([sys.executable, "-c",
                    "import sys; sys._MEIPASS=%r; sys.path.insert(0, %r); import eqgui as E; "
                    "print(E.TEST_APP, E.DEMO, E.PROFILE, E.APP_DIR.endswith('EQAutopilot-test'), E._window_title())" % (_fd, HERE)],
                   capture_output=True, text=True, env=_env, timeout=120)
_o = (r.stdout.strip().splitlines() or [""])[-1]
ck("17 테스트용 빌드: TEST_APP, 늘 DEMO, 프로필 test, 설정 폴더 EQAutopilot-test, 제목 'EQ Autopilot Test  [DEMO]'",
   _o == "True True test True EQ Autopilot Test  [DEMO]", (_o, r.stderr[-300:]))
ck("18 거래용(파일 없음)은 TEST_APP False, 제목 EQ Autopilot", E.TEST_APP is False and E._window_title().startswith("EQ Autopilot")
   and "Test" not in E._window_title())
ck("19 번들 표식 파일과 테스트용 spec 두 개(맥, 윈도) - 거래용 spec에는 표식이 없다",
   open(os.path.join(HERE, "flavor_test", "eq_flavor.txt")).read().strip() == "test"
   and "flavor_test/eq_flavor.txt" in open(os.path.join(HERE, "EQ Autopilot Test.spec")).read()
   and "flavor_test/eq_flavor.txt" in open(os.path.join(HERE, "EQ Autopilot Test Windows.spec")).read()
   and "eq_flavor" not in open(os.path.join(HERE, "EQ Autopilot.spec")).read()
   and "eq_flavor" not in open(os.path.join(HERE, "EQ Autopilot Windows.spec")).read())
ck("20 데모 시험 신호: 하루 한 번 표시를 남기지 않고(그날 실제 신호도 받음), test_exit_ts에 그 자산을 확인 창 없이 닫음",
   'if not (DEMO and sig.get("owner_test")):' in _src and 'if DEMO and sig.get("owner_test") and sig.get("test_exit_ts"):' in _src
   and "App._close_all_positions(self, _a, confirm=False)" in _src and "if confirm and not messagebox.askyesno(" in _src)
ck("21 테스트용 앱 주황 띠와 창 테두리(TEST_APP일 때만)", "if TEST_APP:" in _src and "#F59E0B" in _src)
# ── BTC 레버리지 바꾸기, 손절 이동 시험(대표 2026-11-13 "비트는 손절 바꾸기도 실행", "레버리지 바꾸기도 실행해 조금씩") ──
from types import SimpleNamespace as _NS  # noqa: E402


class FB:                                           # Bybit 모양: 손절은 포지션 stopLoss 칸
    def __init__(s, lev=10.0, pos=None, px=90000.0, lev_fail=False, stop_err=None, stop_sticks=True):
        s.lev, s.pos, s.px, s.lev_fail, s.stop_err, s.stop_sticks, s.stops, s.levs = lev, pos, px, lev_fail, stop_err, stop_sticks, [], []

    def _symbol(s, sym):
        return "BTCUSDT"

    def current_leverage(s, sym):
        return s.lev

    def set_leverage(s, sym, lev):
        if s.lev_fail:
            raise RuntimeError("HTTP 400: code=40797 leverage refused")
        s.levs.append(float(lev))
        s.lev = float(lev)
        return True

    def list_open_positions(s):
        return [s.pos] if s.pos else []

    def current_market_price(s, sym):
        return s.px

    def set_stop(s, sym, px):
        s.stops.append(px)
        if s.stop_err:
            return {"error": s.stop_err}
        if s.stop_sticks and s.pos:
            s.pos.raw["stopLoss"] = str(px)
        return {"error": None}


class FBG(FB):                                      # Bitget/OKX 모양: 손절은 플랜 주문(stop_plans)
    def __init__(s, plan=88000.0, **k):
        super().__init__(**k)
        s.plan = plan

    def stop_plans(s, sym):
        return [{"orderId": "1", "price": s.plan}]

    def set_stop(s, sym, px):
        s.stops.append(px)
        s.plan = float(px)
        return {"error": None}


def _app():
    a = object.__new__(E.App)
    a.lang = "ko"
    a.logs, a.EV = [], []
    a.log = lambda m: a.logs.append(m)
    a._send_ev = lambda kind, asset, **kw: a.EV.append((kind, asset, kw))
    a._open_ctx = {}
    a._remember_stop = lambda *x, **k: None
    a._stop_move_alert = lambda *x, **k: a.EV.append(("alert", None, {}))
    return a


def _pos(q=0.001, entry=90000.0, stop=88000.0):
    raw = {"avgPrice": str(entry)}
    if stop is not None:
        raw["stopLoss"] = str(stop)
    return _NS(symbol="BTCUSDT", net_qty=q, raw=raw)


a = _app()
fb = FB(lev=10.0)
r = E.App._test_leverage(a, fb, "BTCUSDT", "bybit", 3, "bybit", "OT-BTC-1")
ck("22 레버리지 시험: 10x → 3x 바꾸고 다시 읽어 확인, 이벤트 test_lev ok(바꾸기 전, 원한 값, 읽은 값, sid)",
   r is True and fb.levs == [3.0] and a.EV == [("test_lev", "BTC", {"ok": True, "want": 3.0, "got": 3.0, "before": 10.0,
                                                                   "broker": "bybit", "sid": "OT-BTC-1", "err": ""})]
   and any("10x → 3x 바꿈" in x for x in a.logs), (a.EV, a.logs))
a = _app()
r = E.App._test_leverage(a, FB(lev=10.0, lev_fail=True), "BTCUSDT", "bitget", 4, "bitget", "OT-BTC-2")
ck("23 레버리지 시험 실패: False, 이벤트 ok False + 거래소 원인, 로그 '진입은 계속'(진입은 막지 않음)",
   r is False and a.EV[0][2]["ok"] is False and "40797" in a.EV[0][2]["err"] and any("진입은 계속" in x for x in a.logs), (a.EV, a.logs))
a = _app()
r = E.App._test_leverage(a, FB(lev=10.0), "BTCUSDT", "bybit", "x", "bybit", "OT-BTC-3")
ck("24 레버리지 값이 숫자가 아니면 바꾸지 않고 실패로(예외 없음)", r is False and a.EV[0][2]["ok"] is False, (a.EV, a.logs))


def _sm(b, sig_stop=88000.0, sid="OT-BTC-9", reg=True):
    a = _app()
    if reg:
        a._test_brk = {sid: [(b, "bybit", "bybit", sig_stop)]}
    E.App._test_stop_move(a, sid)
    return a


fb = FB(pos=_pos(entry=90000.0, stop=88000.0), px=90200.0)
a = _sm(fb)
ck("25 손절 이동(롱, 진입가보다 0.1% 넘게 유리): 본절 = 진입가 90000으로, 다시 읽어 확인, test_stop ok breakeven",
   fb.stops == [90000.0] and a.EV[-1][0] == "test_stop" and a.EV[-1][2]["ok"] is True and a.EV[-1][2]["why"] == "breakeven"
   and a.EV[-1][2]["got"] == 90000.0, (fb.stops, a.EV))
fb = FB(pos=_pos(entry=90000.0, stop=88000.0), px=89500.0)
a = _sm(fb)
ck("26 손절 이동(롱, 불리): 지금 손절 88000과 현재가 89500의 가운데 88750(늘 조이는 쪽)", fb.stops == [88750.0]
   and a.EV[-1][2]["ok"] is True and a.EV[-1][2]["why"] == "halfway", (fb.stops, a.EV))
fb = FB(pos=_pos(q=-0.002, entry=90000.0, stop=92000.0), px=90300.0)
a = _sm(fb, sig_stop=92000.0)
ck("27 손절 이동(숏, 불리): 92000과 90300의 가운데 91150", fb.stops == [91150.0] and a.EV[-1][2]["ok"] is True, (fb.stops, a.EV))
fb = FB(pos=_pos(q=-0.002, entry=90000.0, stop=92000.0), px=89800.0)
a = _sm(fb, sig_stop=92000.0)
ck("28 손절 이동(숏, 유리): 본절 90000", fb.stops == [90000.0] and a.EV[-1][2]["why"] == "breakeven", (fb.stops, a.EV))
fb = FB(pos=None)
a = _sm(fb)
ck("29 포지션 없음: 옮기지 않고 test_stop ok None why no_position", fb.stops == [] and a.EV[-1][2]["ok"] is None
   and a.EV[-1][2]["why"] == "no_position", a.EV)
a = _sm(FB(pos=_pos()), reg=False)
ck("30 등록된 계좌 없음: test_stop why no_account", a.EV == [("test_stop", "BTC", {"ok": None, "why": "no_account", "sid": "OT-BTC-9"})], a.EV)
fb = FB(pos=_pos(entry=90000.0, stop=88000.0), px=89500.0, stop_sticks=False)
a = _sm(fb)
ck("31 거래소가 받았다는데 다시 읽은 손절이 다르면 실패(halfway:readback)", a.EV[-1][2]["ok"] is False
   and a.EV[-1][2]["why"] == "halfway:readback", a.EV)
fb = FB(pos=_pos(entry=90000.0, stop=88000.0), px=89500.0, stop_err="HTTP 400: code=25582 x")
a = _sm(fb)
ck("32 거래소 거절: ok False, 회원 알림 길(_stop_move_alert)은 종전대로 탐", a.EV[-1][2]["ok"] is False
   and any(e[0] == "alert" for e in a.EV), a.EV)
fb = FBG(plan=88000.0, pos=_pos(entry=90000.0, stop=None), px=89500.0)
a = _sm(fb)
ck("33 Bitget/OKX 모양(손절 = 플랜 주문): 지금 손절을 플랜에서 읽고 옮긴 뒤 플랜으로 확인", fb.stops == [88750.0]
   and a.EV[-1][2]["ok"] is True and a.EV[-1][2]["got"] == 88750.0, (fb.stops, a.EV))
a = _app()
ck("34 _btc_move_stop_be 반환: 옮기면 True, 느슨해지는 목표는 False(블록 판정은 반환을 안 씀)",
   E.App._btc_move_stop_be(a, FB(pos=_pos()), _pos(), True, 89000.0) is True
   and E.App._btc_move_stop_be(a, FB(pos=_pos()), _pos(), True, 87000.0) is False)
_g1 = _src.index('if live and DEMO and sig.get("owner_test") and asset == "BTC":')
ck("35 배선: 레버리지 시험은 데모 + 시험 신호 + BTC + LIVE에서만, 잔여 청산 뒤, 진입 주문(_pol) 앞, 손절 이동 브로커 등록",
   _src.index("✅ [{lbl}] 잔여 청산 확인") < _g1 < _src.index('_pol = ((sig.get("exec_policy") or {}).get("entry")')
   and 'App._test_leverage(self, b, sym, lbl, sig.get("test_leverage"), _broker, sig.get("id"))' in _src
   and '.setdefault(sig.get("id"), []).append((b, lbl, _broker, stop))' in _src)
ck("36 배선: 손절 이동 타이머는 데모 + 시험 신호 + BTC, 청산 시각 전일 때만",
   'if (DEMO and sig.get("owner_test") and _asset == "BTC" and sig.get("test_stop_move_ts")' in _src
   and "float(sig[\"test_stop_move_ts\"]) < float(sig[\"test_exit_ts\"])" in _src
   and "threading.Timer(_sm_wait, lambda _s=sid: App._test_stop_move(self, _s)).start()" in _src)
# ── 13c: Bitget 데모 이름(대표 2026-11-13 "빗겟은 왜 데모 계좌 잔고가 0이냐") ──
import json as _js  # noqa: E402
_RQ = []


class _R2:
    status_code = 200

    def __init__(s, data):
        s._d = data

    def json(s):
        return {"code": "00000", "data": s._d}


_RESP = {"data": None}


def _fake2(url, headers=None, timeout=None, data=None):
    _RQ.append({"url": url, "headers": dict(headers or {}), "data": data})
    return _R2(_RESP["data"])


requests.get = _fake2
requests.post = _fake2
gd = BG.BitgetBroker(E.BitgetCfg(api_key="k", api_secret="s", passphrase="p", demo=True)) if hasattr(E, "BitgetCfg") else None
if gd is None:
    from eqexec.config import BitgetCfg as _BC
    gd = BG.BitgetBroker(_BC(api_key="k", api_secret="s", passphrase="p", demo=True))
_RESP["data"] = [{"marginCoin": "SUSDT", "available": "50000"}]
_r = gd._req("GET", "/api/v2/mix/account/accounts", {"productType": "USDT-FUTURES", "marginCoin": "USDT"})
ck("37 데모 Bitget 잔고 조회: 보내는 값 = SUSDT-FUTURES, SUSDT(paptrading 1), 받은 marginCoin은 USDT로 되돌림",
   "productType=SUSDT-FUTURES" in _RQ[-1]["url"] and "marginCoin=SUSDT" in _RQ[-1]["url"] and _RQ[-1]["headers"].get("paptrading") == "1"
   and _r == [{"marginCoin": "USDT", "available": "50000"}], (_RQ[-1], _r))
_RESP["data"] = {"orderId": "1"}
gd._req("POST", "/api/v2/mix/order/place-order", body={"symbol": "BTCUSDT", "productType": "USDT-FUTURES", "marginCoin": "USDT",
                                                         "side": "buy", "size": "0.001"})
_b = _js.loads(_RQ[-1]["data"])
ck("38 데모 Bitget 주문 본문: SBTCSUSDT, SUSDT-FUTURES, SUSDT(서명 전에 바꿈), 수량 등 나머지는 그대로",
   _b == {"symbol": "SBTCSUSDT", "productType": "SUSDT-FUTURES", "marginCoin": "SUSDT", "side": "buy", "size": "0.001"}, _b)
_RESP["data"] = [{"symbol": "SBTCSUSDT", "holdSide": "long", "total": "0.001", "marginCoin": "SUSDT",
                  "inner": [{"symbol": "SBTCSUSDT"}]}]
_r = gd._req("GET", "/api/v2/mix/position/all-position", {"productType": "USDT-FUTURES", "marginCoin": "USDT"})
ck("39 데모 포지션 응답: 종목 SBTCSUSDT → BTCUSDT(안쪽 목록까지) - 앱의 심볼 비교, 손절, 청산이 실계좌 이름 그대로 맞음",
   _r[0]["symbol"] == "BTCUSDT" and _r[0]["inner"][0]["symbol"] == "BTCUSDT" and _r[0]["marginCoin"] == "USDT", _r)
gl = BG.BitgetBroker(type(gd.cfg)(api_key="k", api_secret="s", passphrase="p", demo=False))
_RESP["data"] = [{"symbol": "BTCUSDT", "marginCoin": "USDT"}]
_r = gl._req("GET", "/api/v2/mix/position/all-position", {"productType": "USDT-FUTURES", "marginCoin": "USDT"})
ck("40 실계좌 Bitget(데모 아님): 이름 그대로, paptrading 없음", "productType=USDT-FUTURES" in _RQ[-1]["url"]
   and "paptrading" not in _RQ[-1]["headers"] and _r == [{"symbol": "BTCUSDT", "marginCoin": "USDT"}], _RQ[-1])
_RESP["data"] = [{"symbol": "BTCUSDT", "category": "USDT-FUTURES"}]
_r = gd._req("GET", "/api/v3/position/current-position", {"category": "USDT-FUTURES"})
ck("40b 데모 Bitget v3(통합 거래 계정, 계정 유형 판별): 원래 이름 그대로 + paptrading - v3엔 데모 이름이 없다(40034)",
   "category=USDT-FUTURES" in _RQ[-1]["url"] and _RQ[-1]["headers"].get("paptrading") == "1"
   and _r == [{"symbol": "BTCUSDT", "category": "USDT-FUTURES"}], (_RQ[-1], _r))
ck("41 이름 바꾸기 함수: 이미 데모 이름은 두 번 안 바꿈, ETH도", BG._demo_sym_out("SBTCSUSDT") == "SBTCSUSDT"
   and BG._demo_sym_out("ETHUSDT") == "SETHSUSDT" and BG._demo_sym_in("SETHSUSDT") == "ETHUSDT" and BG._demo_sym_in("BTCUSDT") == "BTCUSDT")
# ── 13d: 테스트용 앱 NT8 애드온 따로(대표 2026-11-13 "테스트용 앱이 자기 애드온을 따로 쓰게 ... 어 해 봐") ──
_cs = open(os.path.join(HERE, "nt8_addon", "EQAutopilotBridge.cs"), encoding="utf-8").read()
ck("42 실전 앱: 애드온 파일 EQAutopilotBridge.cs, 포트 8377, 원문 그대로", E._BRIDGE_CS == "EQAutopilotBridge.cs" and E._NT8_PORT == 8377
   and E._bridge_flavor(_cs) == (_cs, True))
_r = subprocess.run([sys.executable, "-c",
                     "import sys, json; sys._MEIPASS=%r; sys.path.insert(0, %r); import eqgui as E; "
                     "t, ok = E._bridge_flavor(open(%r, encoding='utf-8').read()); "
                     "print(json.dumps([E._BRIDGE_CS, E._NT8_PORT, ok, t]))" % (_fd, HERE, os.path.join(HERE, "nt8_addon", "EQAutopilotBridge.cs"))],
                    capture_output=True, text=True, env=dict(os.environ, HOME=tempfile.mkdtemp(), APPDATA=tempfile.mkdtemp()), timeout=120)
import json as _js2  # noqa: E402
_bcs, _bport, _bok, _bt = _js2.loads((_r.stdout.strip().splitlines() or ["[null,null,null,\"\"]"])[-1])
ck("43 테스트용 앱: 애드온 파일 EQAutopilotBridgeTest.cs, 포트 8378, 변환 성공", _bcs == "EQAutopilotBridgeTest.cs" and _bport == 8378 and _bok is True,
   (_bcs, _bport, _bok, _r.stderr[-300:]))
ck("44 테스트용 사본: 하위 이름공간 ...AddOns.EQTest(보조 클래스 DictExt, MiniJson이 실전 애드온과 안 겹침), 클래스, 애드온 이름 EQAutopilotBridgeTest",
   "namespace NinjaTrader.NinjaScript.AddOns.EQTest" in _bt and "namespace NinjaTrader.NinjaScript.AddOns\n" not in _bt
   and "public class EQAutopilotBridgeTest : AddOnBase" in _bt and 'Name = "EQAutopilotBridgeTest"' in _bt
   and "[EQBridgeTest]" in _bt and "EQAutopilotBridge " not in _bt.replace("EQAutopilotBridgeTest", ""))
_n_conn = _cs.count("if (a.ConnectionStatus != ConnectionStatus.Connected) continue;")
ck("45 테스트용 사본: 계좌를 도는 곳마다 Sim 계좌만(구독, 보고, 주문 거절 정리), 명령의 계좌 찾기도 Sim만",
   _n_conn >= 5 and _bt.count('continue; if (!a.Name.StartsWith("Sim")) continue;') == _n_conn
   and 'a.Name == name && a.Name.StartsWith("Sim")' in _bt, _n_conn)
_bad, _bok2 = E.TEST_APP, None
E.TEST_APP = True
try:
    _bok2 = E._bridge_flavor(_cs.replace("namespace NinjaTrader.NinjaScript.AddOns", "namespace X"))[1]
finally:
    E.TEST_APP = _bad
ck("46 바꿀 자리를 못 찾으면 실패(설치 안 함) - 거르지 않은 사본이 실계좌를 보지 않게", _bok2 is False)
ck("47 배선: 설치와 자동 업데이트가 변환을 거치고, 설치 파일 이름은 _BRIDGE_CS",
   "txt, _fok = _bridge_flavor(open(src, encoding=\"utf-8\").read())" in _src
   and "new, _fok = _bridge_flavor(open(src_path, encoding=\"utf-8\").read())" in _src
   and "_dst = os.path.join(_addons, _BRIDGE_CS)" in _src)
print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
