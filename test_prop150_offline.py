#!/usr/bin/env python3
# EQ Autopilot - 프롭 150K 자동 할인 확인 보고(p150) 오프라인 테스트(대표 2026-09-28 "오토파일럿에 계좌 등록 확인되면임")
# ① Autopilot(royal)일 때만 보고, Operator(member)·미확인 게이트는 빈 목록 ② 기본 패턴은 Topstep 평가 150KTC 하나
# ③ 서버 패턴(p150)으로 XFA·Lucid(NT8) 확장 ④ 보내는 건 {브로커, 끝4}뿐 - 계좌 이름 전체·잔고 없음 ⑤ 꺼진 계좌·50K 제외, 중복 제거
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
fails = []


def chk(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{d}]"))
    if not c:
        fails.append(n)


class S:
    _P150_BROKERS = eqgui.App._P150_BROKERS

    def __init__(self, tier, p150=None):
        p150 = {"projectx": 내부 기록} if p150 is None else p150
        self._gate = {"ok": True, "tier": tier, "p150": p150}
        self._acc = {
            "NQ": [{"id": "150KTC-V2-11112222", "on": True, "broker": "projectx"},
                   {"id": "50KTC-V2-33334444", "on": True, "broker": "projectx"},
                   {"id": "150KTC-V2-55556666", "on": False, "broker": "projectx"}],
            "GC": [{"id": "150KTC-V2-11112222", "on": True, "broker": "projectx"},      # 같은 계좌 두 자산 → 한 번
                   {"id": "EXPRESS-150K-77778888", "on": True, "broker": "projectx"},
                   {"id": "LFE150K-99990000", "on": True, "broker": "nt8"}],
            "BTC": [{"id": "150KTC-V2-12121212", "on": True, "broker": "bybit"}],       # 크립토 브로커는 대상 아님
        }

    def _accts_of(self, a):
        return self._acc.get(a, [])

    def _acct_broker(self, a, ac):
        return ac.get("broker")


rep = lambda o: eqgui.App._prop150_report(o)  # noqa: E731
r = rep(S("royal"))
g0 = S("royal"); g0._gate["p150"] = None
chk("서버가 패턴을 안 주면(구 서버·끔) 보내지 않음(fail-closed)", rep(g0) == [], rep(g0))
chk("서버가 빈 패턴을 주면 보내지 않음", rep(S("royal", {})) == [], rep(S("royal", {})))
chk("패턴에 프롭사가 없으면 인정 안 함(프롭사 모르는 판정 금지)", rep(S("royal", {"projectx": [r"^150KTC"]})) == [], rep(S("royal", {"projectx": [r"^150KTC"]})))
chk("① Autopilot + 서버 패턴(Topstep 평가) = 150K 하나", r == [{"b": "projectx", "f": "topstep", "k": "2222"}], r)
chk("① Operator면 보고 없음", rep(S("member")) == [], rep(S("member")))
g = S("royal"); g._gate["ok"] = False
chk("① 게이트 ok=False면 없음", rep(g) == [], rep(g))
r3 = rep(S("royal", {"projectx": [[r"^150KTC", "topstep"], [r"^EXPRESS-150K", "topstep"]], "nt8": 내부 기록}))
chk("③ 서버 패턴으로 XFA·Lucid 확장", sorted((x["f"], x["k"]) for x in r3) == [("lucid", "0000"), ("topstep", "2222"), ("topstep", "8888")], r3)
chk("④ 필드는 b·f·k뿐, 끝4만", all(set(x) == {"b", "f", "k"} and len(x["k"]) == 4 for x in r3) and "150KTC" not in json.dumps(r3), r3)
chk("⑤ 50K·꺼진 계좌·크립토 브로커 제외", all(x["k"] not in ("4444", "6666", "1212") for x in r3), r3)
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
