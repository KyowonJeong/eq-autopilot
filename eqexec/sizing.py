# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# =========================
# eqexec/sizing.py
# 자산·브로커·사용자 1R($) + 신호(진입참조/손절가)로 주문 수량을 계산한다.
# 서버는 더 이상 contracts를 보내지 않는다 — 각 사용자의 1R로 앱이 직접 계산(대표 2026-07-10).
#
# 브로커 → 심볼 매트릭스(대표 2026-07-10, MBTC 제거):
#   Topstep·IBKR : MNQ(NQ) · MGC(GC)               — 선물(정수 계약)
#   Bybit·Bitget : BTCUSDT.P (BTC만)               — 크립토(분수 수량, 랏 0.001)
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
#   발동 빈도는 1R·가격대에 좌우된다(1R=$600 기준 2026년 NQ 2.9%·GC 4.7%, 2022년 GC 84%).
_MINI_OF = {"MNQ": "NQ", "MGC": "GC"}     # 마이크로 → 같은 명목의 미니(비율 10:1)
MINI_RATIO = 10

# 크립토 최소 수량/랏 스텝(BTC). 거래소별 실제 최소치는 심볼 규격에서 확인해 조정.
CRYPTO_LOT_STEP = 0.001

# ── 안전 바닥·천장 (2026-10-12 버그 조사 D P2-3) ─────────────────────────────────
# 서버 손절이 진입가에 비정상적으로 붙으면(잘린 봉·틀린 전 4시간 저가 같은 데이터 사고) 계약수가 한없이 커졌다
# (예: 금 손절 1.5pt, 1R $600 × 3 → 마이크로 120 = 금 미니 12). 명목 R은 같아도 그 크기의 손절 슬리피지는 몇 R이고,
# 프롭 계좌는 계약 한도를 넘는다. 1R·사이징 공식은 그대로 두고(동결) 비정상 신호만 거르는 두 겹을 더한다.
#   ① 바닥: 손절거리 < 진입가 × MIN_STOP_FRAC 이면 진입 안 함(size 0, guard='stop_too_tight').
#      0.025% = 나스닥 약 7.8pt(31,000), 금 약 1.05pt(4,200). 2013~2026 4시간봉 공식(전 봉 고저 × 1.05)으로 잰
#      실제 진입 시각 최솟값은 나스닥 10:00 0.034%(2014-07-04 휴장일, 그다음 0.059%), 금 02:00 0.041%라 정상 신호엔 걸리지 않는다.
#   ② 천장: 한 계좌 한 주문의 마이크로 환산 계약수 상한 MAX_MICRO_EQ(넘으면 그 수로 줄여 진입, guard='size_cap').
#      400 = 미니 40계약. 가장 큰 프롭 1R $2,250에 배수 천장 3을 곱해도($6,750) 금은 손절 1.69pt(0.04%) 미만,
#      나스닥은 8.4pt(0.027%) 미만일 때만 닿는다 - 위 실측 최솟값보다 짧은 손절에서만 걸린다.
MIN_STOP_FRAC = {"MNQ": 0.00025, "MGC": 0.00025}
MAX_MICRO_EQ = {"MNQ": 400, "MGC": 400}

_FUTURES_BROKERS = ("projectx", "ibkr", "nt8")   # projectx == Topstep, nt8 == Lucid 브리지
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
    _min_pts = MIN_STOP_FRAC.get(sym, 0.0) * abs(entry_ref)
    if risk_pts < _min_pts:                        # ① 바닥: 비정상적으로 짧은 손절 = 데이터 사고로 보고 진입 안 함
        return {"size": 0, "symbol": sym, "kind": kind, "unit": "contracts",
                "risk_pts": round(risk_pts, 2), "legs": [], "guard": "stop_too_tight",
                "min_stop_pts": round(_min_pts, 2)}
    raw = one_r / (risk_pts * pv)                  # 이론 계약수(반올림 전)
    # 계약수 0.75 미만이면 0으로 떨궈 진입 금지(대표 2026-07-26): 선물(나스닥·금)은 1R 대비
    # 손절이 커서 1계약도 못 잡을 땐, 반올림으로 억지 1계약(=예정 리스크 초과)을 내지 않는다.
    # 크립토는 분수 수량(랏 0.001)이라 소액도 정상 사이징 → 이 컷오프 없음(위 crypto 분기).
    size = int(round(raw)) if raw >= 0.75 else 0
    size = max(0, size)
    out = {"size": size, "symbol": sym, "kind": kind, "unit": "contracts",
           "risk_pts": round(risk_pts, 2)}
    _cap = MAX_MICRO_EQ.get(sym)
    if _cap and size > _cap:                       # ② 천장: 줄여서 진입(리스크는 1R보다 작아지는 쪽만)
        out.update(guard="size_cap", size_uncapped=size, cap=_cap)
        size = out["size"] = int(_cap)
    # 마이크로 10계약 이상이면 미니로 묶어 커미션을 3배 아낀다(위 주석 참고).
    # legs = [(심볼, 계약수), ...] — 호출부는 legs가 있으면 그대로, 없으면 size/symbol 단일 주문.
    mini = _MINI_OF.get(sym)
    if mini and size >= MINI_RATIO:
        n_mini, n_micro = divmod(size, MINI_RATIO)
        out["legs"] = [(mini, n_mini)] + ([(sym, n_micro)] if n_micro else [])
    else:
        out["legs"] = [(sym, size)] if size > 0 else []
    return out
