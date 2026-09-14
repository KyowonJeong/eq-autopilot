"""선물 활성월·롤 규칙 **단일 출처**(2026-09-14).

왜 생겼나: 어댑터 셋이 각자 달력을 들고 있었고 서로 달랐다.
  ibkr.py      금속 활성월 "GJMQVZ"  ← **10월(V) 포함**
  nt8.py       금속 활성월 (2,4,6,8,12)  ← 10월 제외("유동성 없어")
  tradovate.py 활성월 필터 **없음** - "가장 가까운 미래 월물"이라 시리얼물까지 집는다
546일(2026-01-01~2027-06-30) 실계산에서 ibkr↔nt8이 **62일** 다른 계약을 골랐다.

왜 그게 돈인가: 서버 신호는 **절대 손절가**를 보내고 그 값은 정본 가격 계열(12월물)에서
나온다. 어댑터가 다른 월물에 그 가격을 보정 없이 얹으면 월물 basis만큼 손절거리가
틀어진다. 실측 GC 4,389.8 / 4H 손절거리 중앙값 $29.4 vs 10↔12월 캐리 basis $22~37 =
손절거리의 75~125%. 롱은 스탑이 시장가 위로 가 브로커가 거부하고, 숏은 스탑이 두 배
멀어져 한 번 털릴 때 1R이 아니라 약 2R이 나간다.

⛔ProjectX는 이 모듈을 쓰지 않는다. 그쪽 activeContract는 **정본 가격의 출처**라
(signals/projectx_price.py) 하드코딩 달력으로 덮으면 그게 회귀다.
"""
from __future__ import annotations

import datetime as _dt

#: 월 코드. F=1월 … Z=12월.
MONTH_CODES = "FGHJKMNQUVXZ"

#: 루트별 활성월(거래되는 달이 아니라 **유동성이 있는 달**).
#: ⚠️금속에 V(10월)를 넣지 않는다 - 거래는 되지만 호가가 얇고, 무엇보다 정본 가격이
#: 12월물 계열이라 10월물에 발주하면 위 basis 문제가 그대로 난다(2026-09-14).
ACTIVE_MONTHS = {
    # 지수: CME 분기물
    "NQ": "HMUZ", "MNQ": "HMUZ", "ENQ": "HMUZ",
    "ES": "HMUZ", "MES": "HMUZ",
    "RTY": "HMUZ", "M2K": "HMUZ",
    "YM": "HMUZ", "MYM": "HMUZ",
    # 금속: 2·4·6·8·12월(10월 제외)
    "GC": "GJMQZ", "MGC": "GJMQZ", "GCE": "GJMQZ",
    # 은: 3·5·7·9·12월
    "SI": "HKNUZ", "SIL": "HKNUZ",
}

#: 지수 롤: 만기(계약월 3번째 금요일) 이 일수 전부터 다음 분기물.
ROLL_DAYS_INDEX = 8
#: 금속 롤: 계약월 시작 이 일수 전부터 다음 활성월.
#: FND(직전 월 마지막 영업일)보다 **먼저** 굴러야 한다 - 실물인수 계약이라 FND를 넘기면
#: 브로커가 강제 청산한다.
ROLL_DAYS_METAL = 5

_INDEX_ROOTS = {"NQ", "MNQ", "ENQ", "ES", "MES", "RTY", "M2K", "YM", "MYM"}


def is_index(root: str) -> bool:
    return str(root).upper() in _INDEX_ROOTS


def third_friday(y: int, m: int) -> _dt.date:
    first = _dt.date(y, m, 1)
    return first + _dt.timedelta(days=(4 - first.weekday()) % 7 + 14)


def active_months(root: str) -> str:
    """이 루트의 활성월 코드 문자열. 표에 없으면 "" - 호출부가 '모르면 거르지 않는다'를
    선택할지 '모르면 거부한다'를 선택할지 정한다(어댑터마다 안전한 쪽이 다르다)."""
    return ACTIVE_MONTHS.get(str(root).upper(), "")


def front_month(root: str, today: _dt.date | None = None) -> tuple[int, int] | None:
    """(연, 월) 프론트 계약월. 규칙을 모르는 루트면 None.

    지수: 활성월 중, 만기 ROLL_DAYS_INDEX일 전까지는 그 달. 지나면 다음 활성월.
    금속: 활성월 중, 계약월 시작 ROLL_DAYS_METAL일 전까지는 그 달. 지나면 다음 활성월.
    """
    root = str(root).upper()
    codes = active_months(root)
    if not codes:
        return None
    months = sorted(MONTH_CODES.index(c) + 1 for c in codes)
    d = today or _dt.date.today()
    _idx = is_index(root)
    y, m = d.year, d.month
    for _ in range(26):                       # 2년치면 어떤 달력에서도 답이 나온다
        if m in months:
            if _idx:
                _cut = third_friday(y, m) - _dt.timedelta(days=ROLL_DAYS_INDEX)
            else:
                _cut = _dt.date(y, m, 1) - _dt.timedelta(days=ROLL_DAYS_METAL)
            if d <= _cut:
                return (y, m)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return None


def month_code(m: int) -> str:
    return MONTH_CODES[int(m) - 1]
