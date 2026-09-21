#!/usr/bin/env python3
# EQ Autopilot - 손절 보호 계층 오프라인 통합 테스트 (2026-09-21)
# =========================
# 왜: 2026-09-21 하루에 손절 관련 수리가 여섯 개 들어갔다(심볼·Bitget 폴백·이동 실패 경보·
#   생존 감시·자동 재거치·재시작 내성·수동 우선). 각각은 스텁으로 봤지만 **서로 물린 채로는**
#   한 번도 안 돌려봤고, 실제로 그 상태에서 호출부 시그니처와 영속 경로가 갈릴 수 있다.
#   브로커도 GUI도 없이 돌아가므로 빌드 전에 매번 돌린다.
#
# ⚠️하네스 함정(여기서 실제로 당했다): 메서드만 떼어내 exec하면 모듈 전역
#   (_load_open_ctx / _build_broker / _kc_load)을 못 봐서 try/except에 삼켜지고
#   **거짓 통과**가 난다. 반드시 ns = dict(eqgui.__dict__)로 전역을 넘긴다.
#   그리고 ctx dict는 **같은 객체**를 공유해야 한다 - 사본을 넘기면 갱신이 안 보인다.
#
# 사용: python executor/test_stop_guard_offline.py   (0=통과, 1=실패)
# =========================
import ast
import io
import json
import os
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

NAMES = ("_check_stop_closed", "_check_stop_alive", "_learn_seen_stop", "_restore_open_ctx",
         "_persist_open_ctx", "_forget_open_ctx", "_remember_open", "_remember_stop")


def _build():
    src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"),
                  encoding="utf-8").read()
    segs = {n.name: ast.get_source_segment(src, n) for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name in NAMES}
    missing = [n for n in NAMES if n not in segs]
    if missing:
        raise AssertionError(f"메서드가 사라졌다: {missing}")
    ns = dict(eqgui.__dict__)
    exec("class T:\n    STOP_ORDER_CHECK_SEC = 0\n"
         + "\n".join(textwrap.indent(segs[n], "    ") for n in NAMES), ns)
    return ns


class _Pos:
    def __init__(self, raw, sym, qty=1):
        self.raw, self.symbol, self.net_qty, self.account_id = raw, sym, qty, None


class _Crypto:
    name = "bybit"

    def __init__(self, poss):
        self.poss, self.stops = poss, []

    def list_open_positions(self):
        return self.poss

    def set_stop(self, sym, px):
        self.stops.append(px)
        return {}


def _app(T, b, ctx):
    t = T()
    t.logs, t.alerts, t.fills = [], [], []
    t.log = t.logs.append
    t._member_alert = lambda k, ko, en: t.alerts.append(k)
    t._send_fill = lambda a, closed=False: t.fills.append((a, closed))
    t._creds_of = lambda a, bk=None: {"f1": "K", "f3": "P"}
    t._open_assets, t._flat_seen, t._nostop_seen, t._stop_chk_at = {"BTC"}, {}, {}, {}
    ctx["b"] = b
    t._open_ctx = {"BTC": ctx}          # 같은 dict 공유(사본이면 갱신이 안 보인다)
    return t


def _disk():
    try:
        return json.load(open(eqgui._OPENCTX_PATH, encoding="utf-8"))
    except Exception:
        return {}


def main():
    eqgui._OPENCTX_PATH = os.path.join(tempfile.mkdtemp(), ".open_ctx.json")
    ns = _build()
    T = ns["T"]
    fails = []

    def ok(name, cond, got):
        print(f"{'PASS' if cond else 'FAIL'}  {name}  :: {got}")
        if not cond:
            fails.append(name)

    # ① 손절이 살아 있으면 아무 일도 없다
    b = _Crypto([_Pos({"stopLoss": "80964.2"}, "BTCUSDT")])
    c = {"sym": "BTCUSDT", "stop": 80964.2, "app_stop": 80964.2, "dir": "LONG"}
    t = _app(T, b, c)
    t._check_stop_closed()
    ok("정상=무음", not t.alerts and not b.stops and not t.fills and not c.get("manual"),
       f"경보{t.alerts} 재거치{b.stops}")

    # ② 손절이 사라지면 연속 2회 뒤 기억한 자리로 재거치 + 복구 알림
    b = _Crypto([_Pos({"stopLoss": "0"}, "BTCUSDT")])
    c = {"sym": "BTCUSDT", "stop": 80964.2, "app_stop": 80964.2, "dir": "LONG"}
    t = _app(T, b, c)
    for _ in range(2):
        t._stop_chk_at = {}
        t._check_stop_closed()
    ok("사라짐→재거치", b.stops == [80964.2] and t.alerts == ["stop_restored"],
       f"경보{t.alerts} 재거치{b.stops}")

    # ③ 회원이 옮기면 manual + 기억 갱신 + 디스크 반영, 앱은 재거치 안 함
    b = _Crypto([_Pos({"stopLoss": "81500"}, "BTCUSDT")])
    c = {"sym": "BTCUSDT", "stop": 80964.2, "app_stop": 80964.2, "dir": "LONG"}
    t = _app(T, b, c)
    t._check_stop_closed()
    _d = _disk().get("BTC", {})
    ok("수동이 우선", (t.alerts == ["stop_manual"] and c.get("manual") and c["stop"] == 81500.0
                       and _d.get("manual") and _d.get("stop") == 81500.0 and not b.stops),
       f"manual={c.get('manual')} 기억={c['stop']} 디스크={_d.get('stop')} 재거치{b.stops}")

    # ④ 포지션이 사라지면 청산 보고 + 디스크 기록 삭제
    b = _Crypto([])
    c = {"sym": "BTCUSDT", "stop": 80964.2, "dir": "LONG"}
    t = _app(T, b, c)
    t._persist_open_ctx("BTC")
    had = "BTC" in _disk()
    for _ in range(2):
        t._check_stop_closed()
    ok("청산→기록 삭제", had and t.fills == [("BTC", True)] and "BTC" not in _disk(),
       f"청산{t.fills} 남은기록={_disk()}")

    # ⑤ 재시작(맥락 없음) → 디스크에서 복구해 감시를 이어간다
    b = _Crypto([_Pos({"stopLoss": "0"}, "BTCUSDT")])
    ns["_build_broker"] = lambda bk, f1, f2, f3, acc: b
    ns["_kc_load"] = lambda k: "S"
    c = {"sym": "BTCUSDT", "stop": 80964.2, "app_stop": 80964.2, "dir": "LONG"}
    t = _app(T, b, c)
    t._persist_open_ctx("BTC")
    t._open_ctx = {}                       # 재시작 재현
    for _ in range(2):
        t._stop_chk_at = {}
        t._check_stop_closed()
    ok("재시작 복구", "BTC" in t._open_ctx and b.stops == [80964.2]
       and t.alerts == ["stop_restored"], f"복구={'BTC' in t._open_ctx} 재거치{b.stops}")

    print(f"\n{'전부 통과' if not fails else 'FAIL ' + str(fails)} - 5개 검사")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
