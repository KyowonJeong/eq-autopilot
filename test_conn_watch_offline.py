#!/usr/bin/env python3
# EQ Autopilot - 브로커 연결 상시 감시 오프라인 테스트 (2026-09-23)
# =========================
# 왜: 사전점검은 진입 직전 두 번(T-70·T-10)만 돈다. 그 사이 - 특히 포지션을 들고 있는 동안 -
#   브로커가 죽으면 자동 청산도 손절 감시도 안 도는데 화면은 조용하다. NinjaTrader는 가끔
#   조용히 죽고, Topstep은 2026-09-22에 48분 전면 장애가 있었다.
#   대표 지시: "연결 실패면 바로 디엠. 특히 앱이 켜져있는데 그 상태면" / "한 시간 마다 디엠"
#
# 이 테스트가 고정하는 것:
#   ① 한 번 깜빡이는 걸로는 DM하지 않는다(연속 2회부터)
#   ② 실패가 이어져도 DM은 한 시간에 한 번
#   ③ 복구되면 한 번 알리고 조용해진다
#   ④ 포지션을 들고 있으면 문구가 격상된다
#   ⑤ 라이브가 꺼져 있으면 아무것도 안 한다
#
# ⚠️하네스 함정: 메서드만 떼어 exec하면 모듈 전역(_build_broker·_broker_label)을 못 봐
#   try/except에 삼켜지고 **거짓 통과**가 난다. ns = dict(eqgui.__dict__)로 전역을 넘긴다.
#
# 사용: python executor/test_conn_watch_offline.py   (0=통과, 1=실패)
# =========================
import ast
import io
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

NAMES = ("_conn_watch_run", "_assets_with_open_position", "_member_alert")
FAIL = []


def ck(name, cond, info=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  :: " + str(info)) if info else ""))
    if not cond:
        FAIL.append(name)


def _build(broker_ok, held=()):
    src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"),
                  encoding="utf-8").read()
    segs = {n.name: ast.get_source_segment(src, n) for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name in NAMES}
    missing = [n for n in NAMES if n not in segs]
    if missing:
        raise AssertionError(f"메서드가 사라졌다: {missing}")
    ns = dict(eqgui.__dict__)
    sent = []

    class _B:
        def healthcheck(self):
            if not broker_ok["ok"]:
                raise RuntimeError("bridge down")
            return True

    ns["_build_broker"] = lambda *a, **k: _B()
    ns["_broker_label"] = lambda b: str(b).upper()
    exec("class T:\n" + "\n".join(textwrap.indent(segs[n], "    ") for n in NAMES), ns)
    t = ns["T"]()
    t.lang = "ko"
    t.log = lambda *a, **k: None
    t._conn_state = {}
    t._member_alert = lambda kind, ko, en: sent.append((kind, ko))
    t._assets_with_open_position = lambda assets: set(held)
    return t, sent


SEEN = {("nt8", "ACC1"): ("NQ", {"f1": "", "f2": "", "f3": ""})}


def main():
    print("① 깜빡임 1회는 무음 / 연속 2회부터 DM")
    ok = {"ok": False}
    t, sent = _build(ok)
    t._conn_watch_run(dict(SEEN))
    ck("1회 실패는 무음", not sent, f"{len(sent)}건")
    t._conn_watch_run(dict(SEEN))
    ck("2회째 DM 1건", len(sent) == 1, sent[-1][0] if sent else "-")
    ck("문구가 '연결이 끊겼습니다'", sent and "연결이 끊겼습니다" in sent[0][1])

    print("② 실패가 이어져도 한 시간에 한 번")
    for _ in range(5):
        t._conn_watch_run(dict(SEEN))
    ck("추가 DM 없음", len(sent) == 1, f"{len(sent)}건")

    print("③ 복구는 1회 알림 후 침묵")
    ok["ok"] = True
    t._conn_watch_run(dict(SEEN))
    ck("복구 DM 1건", len(sent) == 2 and sent[-1][0] == "conn_ok", sent[-1][0])
    t._conn_watch_run(dict(SEEN))
    ck("이후 조용", len(sent) == 2, f"{len(sent)}건")

    print("④ 포지션 보유 중이면 문구 격상")
    ok2 = {"ok": False}
    t2, sent2 = _build(ok2, held=("NQ",))
    t2._conn_watch_run(dict(SEEN))
    t2._conn_watch_run(dict(SEEN))
    ck("격상 문구", sent2 and "포지션이 열려 있습니다" in sent2[0][1], sent2[0][1][:48] if sent2 else "-")
    ck("자동청산·손절 감시를 언급", sent2 and "손절 감시" in sent2[0][1])

    print("⑤ 라이브가 꺼져 있으면 점검 자체를 안 한다")
    src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"),
                  encoding="utf-8").read()
    seg = next(ast.get_source_segment(src, n) for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "_conn_watch_tick")
    ck("_live_session 가드가 맨 앞에",
       seg.index("_live_session") < seg.index("_sig_accts"), "가드가 뒤면 꺼진 앱도 왕복한다")
    ck("꺼지면 상태를 비운다", "_conn_state = {}" in seg)

    print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
