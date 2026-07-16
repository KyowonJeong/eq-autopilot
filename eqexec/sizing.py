# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# =========================
# eqexec/sizing.py
# 자산·브로커·사용자 1R($) + 신호(진입참조/손절가)로 주문 수량을 계산한다.
# 서버는 더 이상 contracts를 보내지 않는다 — 각 사용자의 1R로 앱이 직접 계산(대표 2026-07-10).
#
# 브로커 → 심볼 매트릭스(대표 2026-07-10, MBTC 제거):
#   Topstep·IBKR : MNQ(NQ) · MGC(GC)               — 선물(정수 계약)
#   Bybit·Bitget : BTCUSDT.P (BTC만)               — 크립토(분수 수량, 랏 0.001)
#   ⚠️ BTC를 CME MBTC 선물로 안 함 — MBTC 주말 휴장인데 BTC 엣지가 주말(일요일)에 몰려 있어
#      MBTC로 돌리면 실행 성과가 크게 훼손됨. BTC는 크립토 전용.
#
# 수량 공식:
#   손절거리(pts) = |entry_ref − stop|  (LONG: entry−stop, SHORT: stop−entry)
#   선물  : 계약수 = round( 1R / (손절거리 × 포인트값) )        → 정수
#   크립토: 수량   = round( (1R / 손절거리$) / 랏 ) × 랏         → 분수(BTC)
# =========================

# 선물 포인트값 = 지수 가격이 1pt 움직일 때 계약당 손익($). 마이크로 계약 기준.
#   MNQ = $2/pt · MGC = $10/pt
FUTURES_POINT_VALUE = {"MNQ": 2.0, "MGC": 10.0}

# ── 미니/마이크로 분할 (대표 2026-07-16) ──────────────────────────────────────
# 마이크로 10계약 = 미니 1계약(같은 명목)인데 **수수료는 미니가 3배 이상 싸다**(TopstepX):
#   MNQ $1.24 RT × 10 = $12.40  vs  NQ $3.80 RT      → 커미션 −69%
#   MGC $1.74 RT × 10 = $17.40  vs  GC $4.24 RT      → 커미션 −76%
# 그래서 마이크로 23계약을 낼 사람은 없다 — 미니 2 + 마이크로 3으로 낸다. 앱도 그렇게 낸다.
# ⚠️슬리피지는 안 준다(틱 수 동일, 계약당 명목만 10배) — 아끼는 건 커미션뿐.
# ⚠️백테스트(costs._blended_commission)와 **같은 규칙**이어야 트랙레코드가 진실이다.
#   발동 빈도는 1R·가격대에 좌우된다(1R=$600 기준 2026년 NQ 2.9%·GC 4.7%, 2022년 GC 84%).
_MINI_OF = {"MNQ": "NQ", "MGC": "GC"}     # 마이크로 → 같은 명목의 미니(비율 10:1)
MINI_RATIO = 10

# 크립토 최소 수량/랏 스텝(BTC). 거래소별 실제 최소치는 심볼 규격에서 확인해 조정.
CRYPTO_LOT_STEP = 0.001

_FUTURES_BROKERS = ("projectx", "ibkr")   # projectx == Topstep
_CRYPTO_BROKERS = ("bybit", "bitget")
_FUT_SYMBOL = {"NQ": "MNQ", "GC": "MGC"}   # BTC 선물 없음(MBTC 제거) — BTC는 크립토 전용


def symbol_for(asset: str, broker: str):
    """(심볼, kind) 반환. kind ∈ {'futures','crypto'}. 매핑 불가 시 (None, None).
    크립토 브로커는 BTC만(BTCUSDT.P), 선물 브로커는 NQ·GC만(BTC는 선물 불가)."""
    a = str(asset).upper()
    b = str(broker).lower()
    if b in _CRYPTO_BROKERS:
        return ("BTCUSDT.P", "crypto") if a == "BTC" else (None, None)
    if b in _FUTURES_BROKERS:
        sym = _FUT_SYMBOL.get(a)
        return (sym, "futures") if sym else (None, None)
    return (None, None)


def compute_size(asset, broker, one_r, entry_ref, stop, direction):
    """사용자 1R($) + 신호 진입참조/손절가로 주문 수량 계산.
    반환 dict {size, symbol, kind, unit, risk_pts} — 계산 불가 시 None.
      · 선물 : size = 정수 계약수, unit='contracts'
      · 크립토: size = 분수 BTC 수량(랏 반올림), unit='BTC'
    size==0(1R가 최소 손절거리 대비 너무 작음)도 유효 결과(진입 안 함은 호출부가 판단)."""
    sym, kind = symbol_for(asset, broker)
    if not sym:
        return None
    try:
        entry_ref = float(entry_ref)
        stop = float(stop)
        one_r = float(one_r)
    except (TypeError, ValueError):
        return None
    if one_r <= 0:
        return None
    long = str(direction).upper() == "LONG"
    risk_pts = (entry_ref - stop) if long else (stop - entry_ref)
    if risk_pts <= 0:
        return None
    if kind == "crypto":
        raw = one_r / risk_pts                       # 1 BTC = $1 P&L per $1 move
        size = round(round(raw / CRYPTO_LOT_STEP) * CRYPTO_LOT_STEP, 3)
        return {"size": size, "symbol": sym, "kind": kind, "unit": "BTC",
                "risk_pts": round(risk_pts, 2)}
    pv = FUTURES_POINT_VALUE[sym]
    size = max(0, int(round(one_r / (risk_pts * pv))))
    out = {"size": size, "symbol": sym, "kind": kind, "unit": "contracts",
           "risk_pts": round(risk_pts, 2)}
    # 마이크로 10계약 이상이면 미니로 묶어 커미션을 3배 아낀다(위 주석 참고).
    # legs = [(심볼, 계약수), ...] — 호출부는 legs가 있으면 그대로, 없으면 size/symbol 단일 주문.
    mini = _MINI_OF.get(sym)
    if mini and size >= MINI_RATIO:
        n_mini, n_micro = divmod(size, MINI_RATIO)
        out["legs"] = [(mini, n_mini)] + ([(sym, n_micro)] if n_micro else [])
    else:
        out["legs"] = [(sym, size)] if size > 0 else []
    return out
