"""자산별 청산 범위(대표 2026-10-17 "자산별 청산 버튼", "지금 바로").

왜: 정해진 시각 청산이 브로커의 flatten_all(그 계좌 포지션 전부)을 불렀다. 비트코인 청산이 같은 Bybit, Bitget 계좌의 다른 코인
선물(ETH 등 USDT 무기한)까지, 나스닥 청산이 같은 프롭 계좌의 금이나 회원이 손으로 연 다른 종목까지 닫았다. 금(08~12시)과
나스닥(16~20시)은 시간이 안 겹쳐 드러나지 않았을 뿐이다. '손으로 만진 것은 앱이 건드리지 않는다'와도 어긋난다.

이 모듈은 종목 이름 → 자산을 판정한다. 진입과 같은 이름 규칙(대표 "진입도 너가 하니깐 그걸로 해"):
나스닥 = NQ, MNQ(ENQ), 금 = GC, MGC(GCE), 비트코인 = BTCUSDT(신호 표기 'BTCUSDT.P'). 브로커마다 종목 이름 꼴이 달라
뿌리(root)를 떼어 비교한다:
  ProjectX 'CON.F.US.MNQ.Z26' / NinjaTrader 'MNQ 12-26' / Tradovate, IBKR 'MNQZ6', 'MNQZ26' / Bybit, Bitget, OKX 'BTCUSDT'(OKX 원래 이름 'BTC-USDT-SWAP'도).
다른 종목(MES, ETHUSDT 등)은 어느 자산에도 속하지 않아 닫지 않는다. 다만 **이름을 해석할 수 없는** 포지션(빈 이름, 숫자 계약 ID -
Tradovate는 계약 이름 조회가 실패하면 ID를 그대로 준다)은 matcher가 '내 것일 수 있다'로 보고 닫는다: 청산을 놓쳐 우리 포지션이
알몸으로 남는 것이 남의 것을 닫는 것보다 나쁘고, 그 경우는 종전(계좌 전부)과 같다.
"""
from __future__ import annotations

import re

ROOTS = {
    "NQ": frozenset({"NQ", "MNQ", "ENQ"}),
    "GC": frozenset({"GC", "MGC", "GCE"}),
    "BTC": frozenset({"BTC"}),
}

_FUT_TAIL = re.compile(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{1,2})$")   # 월코드 1자 + 연도 1~2자리(Z6, Z26)


def sym_root(symbol) -> str:
    """종목 이름의 뿌리. 'CON.F.US.MNQ.Z26' → 'MNQ', 'MNQ 12-26' → 'MNQ', 'MNQZ6' → 'MNQ', 'BTCUSDT.P' → 'BTC'. 모르면 ''."""
    s = str(symbol or "").upper().strip()
    if not s:
        return ""
    parts = s.split(".")
    if len(parts) >= 5 and parts[0] == "CON":                 # ProjectX 계약 ID
        return parts[3]
    if " " in s:                                              # NinjaTrader 'MNQ 12-26'
        return s.split(" ")[0]
    # OKX 'BTC-USDT-SWAP'도 떼어 본다(어댑터는 'BTCUSDT'로 주지만 방어)
    s = s.replace(".P", "").replace("-PERP", "").replace("-SWAP", "").replace("/", "").replace("-", "").replace("_", "")
    for q in ("USDT", "USDC", "USD"):                          # 크립토 무기한('BTCUSDT') - 선물 뿌리는 이렇게 끝나지 않는다
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    m = _FUT_TAIL.match(s)
    if m:
        return m.group(1)
    return s


def asset_of(symbol) -> str:
    """종목 이름 → 자산('NQ', 'GC', 'BTC') 또는 ''(모름)."""
    r = sym_root(symbol)
    for a, roots in ROOTS.items():
        if r in roots:
            return a
    return ""


def is_opaque(symbol) -> bool:
    """이름을 해석할 수 없는 종목(빈 이름, 숫자뿐인 계약 ID). 이런 포지션은 자산별 청산이 닫는 쪽으로 기운다(모듈 설명)."""
    r = sym_root(symbol)
    return (not r) or r.isdigit()


def matcher(asset):
    """flatten_all(only=...)에 넘기는 판정 함수 - 그 자산 종목이거나 이름을 해석할 수 없으면 True. 모르는 자산이면 None(호출부가 거른다)."""
    a = str(asset or "").upper()
    if a not in ROOTS:
        return None

    def _only(symbol, _a=a) -> bool:
        return asset_of(symbol) == _a or is_opaque(symbol)
    _only.asset = a
    return _only
