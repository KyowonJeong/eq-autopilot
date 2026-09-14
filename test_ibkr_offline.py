"""IBKR 어댑터 **오프라인 불변식** - TWS 없이 도는 검사.

왜 기계가 지키는가: 이 어댑터는 실주문 경로인데 페이퍼 검증조차 없이 5일을 보냈고,
2026-09-14 오프라인 감사에서 고유 13건(그중 돈이 걸린 것 5건)이 나왔다. 그중 절반은
접속 없이 잡히는 것들이었다 - 달력·문자열 파싱·죽은 분기. 그것들을 여기서 붙잡는다.

 ① 계약월이 형제 어댑터와 같다 - 다르면 절대 손절가가 다른 월물에 얹혀
    손절거리가 basis만큼 틀어진다(실측: basis가 손절거리의 75~125%).
 ② 금속 활성월에 10월(V)이 없다 - 유동성이 얇고 정본 가격 계열(12월물)과 어긋난다.
 ③ 루트 파싱이 숫자 든 루트(M2K)를 살린다.
 ④ closed_fills가 진입 체결을 청산으로 세지 않는다 - realizedPNL은 판별에 못 쓴다
    (ib_insync 0.9.86이 UNSET을 0.0으로 먼저 뭉갠다).
 ⑤ flatten_all이 전역 취소를 쓰지 않는다 - 회원 본인 주문을 지우면 안 된다.

실행: /Users/edgequant/.buildvenv86/bin/python executor/test_ibkr_offline.py
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eqexec.broker import futures_cal as fc          # noqa: E402
from eqexec.broker.ibkr import IBKRBroker, _root     # noqa: E402
from eqexec.broker.nt8 import NT8Broker             # noqa: E402

fails = 0


def _ok(cond, name, extra=""):
    global fails
    print(("  OK   " if cond else "  FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails += 1


def _ibkr_pick(root, d):
    y, m = d.year, d.month
    for _ in range(26):
        code = fc.MONTH_CODES[m - 1]
        act = fc.active_months(root)
        if not act or code in act:
            lt = fc.third_friday(y, m).strftime("%Y%m%d")
            if IBKRBroker._front_ok(root, f"{y}{m:02d}", lt, d):
                return (y, m)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return None


def _nt8_pick(root, d):
    r = NT8Broker._front_month(root, d)
    if not r:
        return None
    mm = re.search(r"(\d{2})-(\d{2})$", r)
    return (2000 + int(mm.group(2)), int(mm.group(1))) if mm else None


print("① 계약월이 NT8과 같다 (546일 전수)")
_d0, _d1 = dt.date(2026, 1, 1), dt.date(2027, 6, 30)
for _root_s in ("NQ", "MNQ", "GC", "MGC"):
    _bad, _ex = [], None
    _d = _d0
    while _d <= _d1:
        _a, _b = _ibkr_pick(_root_s, _d), _nt8_pick(_root_s, _d)
        if _b and _a != _b:
            _bad.append(_d)
            _ex = _ex or f"{_d} ibkr={_a} nt8={_b}"
        _d += dt.timedelta(days=1)
    # 2026-11-27 한 날은 롤 타이밍 1일 차(IBKR=월초-5일 / NT8=전월 27일)이고
    # 둘 다 FND(11월 마지막 영업일) 전이라 무해하다. 그 외에는 0이어야 한다.
    _allowed = {dt.date(2026, 11, 27)}
    _ok(set(_bad) <= _allowed, f"{_root_s} 불일치 {len(_bad)}일 (허용 1일 외 0)", _ex or "")

print("② 금속 활성월에 10월(V)이 없다")
for _r in ("GC", "MGC", "GCE"):
    _ok("V" not in fc.active_months(_r), f"{_r} 활성월 {fc.active_months(_r)!r}")
_ok(fc.active_months("NQ") == "HMUZ", "지수는 분기물 그대로")
_ok(fc.front_month("GC", dt.date(2026, 9, 14)) == (2026, 12),
    "오늘 GC 프론트 = 12월물", str(fc.front_month("GC", dt.date(2026, 9, 14))))

print("③ 루트 파싱")
for _s, _want in (("MNQZ5", "MNQ"), ("M2KZ6", "M2K"), ("M2K", "M2K"), ("GCZ6", "GC"),
                  ("MNQZ25", "MNQ"), ("SILZ6", "SIL"), ("MES", "MES")):
    _ok(_root(_s) == _want, f"_root({_s!r}) = {_root(_s)!r}", f"기대 {_want!r}")

print("④ closed_fills가 realizedPNL로 진입/청산을 가르지 않는다")
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "eqexec", "broker", "ibkr.py"), encoding="utf-8").read()
_cf = _src[_src.index("def closed_fills"):]
_cf = _cf[:_cf.index("\n    def ")] if "\n    def " in _cf else _cf
_ok("_book" in _cf and "_prev" in _cf, "포지션 걷기로 가른다(_book/_prev)")
_ok("continue                  # 진입 체결(실현손익 없음)" not in _cf,
    "옛 센티널 조기 continue가 사라졌다")
_ok('"direction": _entry_dir' in _cf, "방향을 닫기 직전 포지션 부호에서 뽑는다")

print("⑤ flatten_all이 전역 취소를 쓰지 않는다")
# ⚠️주석까지 세면 '왜 안 쓰는지' 적어 둔 줄이 걸려 규칙이 거꾸로 깐깐해진다 -
# 코드 줄만 본다(2026-09-14, 이 검사 자체가 처음에 그렇게 틀렸다).
_code = "\n".join(ln for ln in _src.split("\n") if not ln.lstrip().startswith("#"))
_ok("reqGlobalCancel" not in _code, "reqGlobalCancel 호출 0건(코드 줄 기준)")
_fa = _src[_src.index("def flatten_all"):]
_fa = _fa[:_fa.index("\n    # ")] if "\n    # " in _fa else _fa
_ok('startswith("EQ-AP-")' in _fa, "우리 orderRef 주문만 취소한다")
_ok("cancel orphan orders failed" in _fa, "플랫이어도 고아 스탑을 회수한다")

print("⑥ clientId가 인스턴스마다 다르다")
_ok("_next_cid" in _src and "_CID_SEQ" in _src, "인스턴스 일련번호 존재")
_a, _b = IBKRBroker._next_cid(), IBKRBroker._next_cid()
_ok(_a != _b, f"연속 호출이 다른 값 ({_a} vs {_b})")

print("⑦ 주문 접수 판정이 타임아웃을 '접수됨'으로 읽지 않는다")
_ack = _src[_src.index("def _ack"):]
_ack = _ack[:_ack.index("\n    def ")]
_ok('return f"PENDING:' in _ack, "타임아웃이면 PENDING을 돌려준다")
_ok('if st in ("PreSubmitted", "Submitted", "Filled"):\n                return None' not in _ack,
    "좋은 상태를 보자마자 OK로 돌아가지 않는다")
_ok("late reject" in _src, "자식 스탑을 한 번 더 확인한다")

print(("\nFAIL %d건" % fails) if fails else "\n전부 통과")
sys.exit(1 if fails else 0)
