"""Bitget adapter (USDT-FUTURES) — auto-close via the v2 mix REST API.

Flatten uses Bitget's flash close-all endpoint (POST /api/v2/mix/order/close-positions), which
market-closes every open position for the product type. Runs on the user's machine with their own
Bitget API key/secret/passphrase. ⚠ UNTESTED — verify on a small balance first. dry_run sends NO
orders. Signing: base64(HMAC_SHA256(timestamp + METHOD + requestPath + body, secret)).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json as _json
import time

import requests

from .base import BrokerAdapter, FlattenResult, Position

_BASE = "https://api.bitget.com"
_TIMEOUT = (10, 30)
_PRODUCT = "USDT-FUTURES"


class BitgetBroker(BrokerAdapter):
    name = "bitget"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.BitgetCfg
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self.passphrase = getattr(cfg, "passphrase", "")
        self.product = getattr(cfg, "product_type", _PRODUCT)

    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        msg = f"{ts}{method.upper()}{path}{body}"
        return base64.b64encode(hmac.new(self.secret.encode(), msg.encode(),
                                         hashlib.sha256).digest()).decode()

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        ts = str(int(time.time() * 1000))
        if method == "GET" and params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            path_q, payload = f"{path}?{qs}", ""
            url = f"{_BASE}{path_q}"
        else:
            payload = _json.dumps(body or {}, separators=(",", ":")) if body else ""
            path_q, url = path, f"{_BASE}{path}"
        headers = {"ACCESS-KEY": self.key, "ACCESS-SIGN": self._sign(ts, method, path_q, payload),
                   "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": self.passphrase,
                   "locale": "en-US", "Content-Type": "application/json"}
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=_TIMEOUT)
        else:
            r = requests.post(url, data=payload, headers=headers, timeout=_TIMEOUT)
        if r.status_code >= 400:
            # HTTP 에러에도 Bitget은 본문에 원인 코드를 준다(예: 40012 passphrase 불일치,
            # 40018 IP 차단) — raise_for_status가 이걸 숨겨 진단 불가였음(대표 2026-08-09 400).
            try:
                _e = r.json() or {}
            except Exception:
                _e = {}
            raise RuntimeError(
                f"Bitget {path} HTTP {r.status_code}: code={_e.get('code')} "
                f"{_e.get('msg') or (r.text or '')[:160]}")
        d = r.json()
        # Bitget은 성공 시 항상 code="00000". str(None)=="None"을 허용 목록에 두면 code 없는
        # 변형 응답을 성공으로 오판(주문 미발주인데 '완료') → "None" 제거, 명시적 성공코드만 통과.
        if str(d.get("code")) not in ("00000", "0"):
            raise RuntimeError(f"Bitget {path} failed: code={d.get('code')} {d.get('msg')}")
        return d.get("data", d)

    def authenticate(self) -> None:
        self.list_open_positions()           # signed read validates key+passphrase

    # ── 잔고-맞춤 자동 레버리지 (대표 2026-07-20, Bybit와 동일 계약) ────────────
    # 진입은 crossed 마진(place_entry 기본)이라 cross 레버리지를 조정한다. 상향만.
    def _account(self, symbol):
        return self._req("GET", "/api/v2/mix/account/account",
                         {"symbol": self._symbol(symbol), "productType": self.product,
                          "marginCoin": "USDT"})

    def available_usdt(self, symbol="BTCUSDT"):
        """새 주문에 쓸 수 있는 USDT. 가용마진 우선, 없으면 자산/에쿼티로 폴백(필드 공백 대응,
        대표 2026-07-26). 실패 시 None."""
        try:
            a = self._account(symbol) or {}
            for k in ("crossedMaxAvailable", "available", "maxTransferOut",
                      "usdtEquity", "accountEquity"):
                v = a.get(k)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            return None
        except Exception:
            return None

    def current_leverage(self, symbol):
        """cross 레버리지 설정(account.crossedMarginLeverage)."""
        try:
            a = self._account(symbol) or {}
            v = a.get("crossedMarginLeverage")
            return float(v) if v not in (None, "") else None
        except Exception:
            return None

    def max_leverage(self, symbol):
        """심볼 최대 레버리지(contracts.maxLever)."""
        try:
            d = self._req("GET", "/api/v2/mix/market/contracts",
                          {"productType": self.product, "symbol": self._symbol(symbol)})
            lst = d if isinstance(d, list) else []
            v = (lst[0] or {}).get("maxLever") if lst else None
            return float(v) if v else None
        except Exception:
            return None

    def set_leverage(self, symbol, lev) -> bool:
        """cross 레버리지 설정(홀드사이드 불필요 — crossed는 롱숏 공통)."""
        self._req("POST", "/api/v2/mix/account/set-leverage",
                  body={"symbol": self._symbol(symbol), "productType": self.product,
                        "marginCoin": "USDT", "leverage": f"{float(lev):g}"})
        return True

    def ensure_leverage(self, symbol, size, price) -> dict:
        """Bybit ensure_leverage와 동일 수학: 필요=명목/(가용×0.85), ×1.3 여유, 상한 캡, 상향만.
        반환 {ab, notional, cur, new|None, error|None} — 호출측 로그 전용, 예외 없음."""
        import math
        try:
            ab = self.available_usdt(symbol)
            if not ab or not price:
                return {"error": "잔고 조회 실패", "ab": ab, "cur": None, "new": None}
            notional = float(size) * float(price)
            need = notional / (ab * 0.85)
            cur = self.current_leverage(symbol) or 0.0
            if cur >= need:
                return {"ab": ab, "notional": notional, "cur": cur, "new": None, "error": None}
            target = math.ceil(need * 1.3)
            mx = self.max_leverage(symbol)
            if mx:
                target = min(target, int(mx))
            if target <= cur:
                return {"ab": ab, "notional": notional, "cur": cur, "new": None,
                        "error": f"심볼 상한 {mx:g}x로도 부족(필요 {need:.1f}x) — 잔고를 늘려야 합니다"}
            self.set_leverage(symbol, target)
            return {"ab": ab, "notional": notional, "cur": cur, "new": target, "error": None}
        except Exception as e:
            return {"error": str(e)[:140], "ab": None, "cur": None, "new": None}

    def list_open_positions(self) -> list[Position]:
        d = self._req("GET", "/api/v2/mix/position/all-position",
                      {"productType": self.product, "marginCoin": "USDT"})
        out: list[Position] = []
        for p in (d or []):
            size = float(p.get("total") or 0)
            if size == 0:
                continue
            net = size if str(p.get("holdSide")).lower() == "long" else -size
            out.append(Position(account_id="bitget", account_name="bitget",
                                symbol=str(p.get("symbol")), net_qty=net, raw=p))
        return out

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        try:
            # Flash close ALL positions for the product type (market).
            self._req("POST", "/api/v2/mix/order/close-positions", body={"productType": self.product})
        except Exception as e:
            res.errors.append(f"close-positions failed: {e}")
        time.sleep(1)
        try:
            still = self.list_open_positions()
            res.closed = [p for p in plan if not any(s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.symbol} net={p.net_qty}")
        except Exception as e:
            res.errors.append(f"post-flatten re-check failed: {e}")
        return res

    def close_symbol(self, symbol, dry_run: bool = True) -> dict:
        """이 심볼 포지션만 청산(다른 심볼 무접촉) — 연속 세션 '청산 확인 후 진입'용.
        flash close(symbol 지정) + 최대 ~6초 재확인 폴링. 반환 {closed: bool, had: n, error}."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"closed": True, "had": 0}
        if dry_run:
            return {"closed": False, "had": len(mine), "dry_run": True}
        try:
            self._req("POST", "/api/v2/mix/order/close-positions",
                      body={"symbol": sym, "productType": self.product})
        except Exception as e:
            return {"closed": False, "had": len(mine), "error": str(e)}
        for _ in range(6):                                   # 죽은 것 '확인' 후에만 True
            time.sleep(1)
            try:
                if not any(p.symbol == sym for p in self.list_open_positions()):
                    return {"closed": True, "had": len(mine)}
            except Exception:
                pass
        return {"closed": False, "had": len(mine), "error": "still open after close"}

    @staticmethod
    def _symbol(sym: str) -> str:
        """신호 심볼('BTCUSDT.P') → Bitget perp 심볼('BTCUSDT')."""
        s = str(sym or "").upper().replace(".P", "").replace("-", "").replace("/", "")
        return s or "BTCUSDT"

    @staticmethod
    def _fmt_qty(size) -> str:
        q = round(float(size), 3)
        return f"{q:.3f}".rstrip("0").rstrip(".") or "0"

    _PSTEP: dict = {}                     # 심볼 → (step, decimals) 캐시 (프로세스 수명)

    def _pstep(self, sym: str) -> tuple:
        """심볼의 가격 스텝. Bitget 계약 스펙(pricePlace·priceEndStep)에서 읽는다.
        BTCUSDT = place 1, endStep 1 → 0.1. 조회 실패 시 BTC 0.1 / 그 외 0.01 폴백."""
        v = BitgetBroker._PSTEP.get(sym)
        if v:
            return v
        try:
            d = self._req("GET", "/api/v2/mix/market/contracts",
                          {"productType": self.product, "symbol": sym})
            row = (d[0] if isinstance(d, list) and d else d) or {}
            place = int(row.get("pricePlace") or 1)
            end = float(row.get("priceEndStep") or 1)
            v = (end / (10 ** place), place)
        except Exception:
            v = (0.1, 1) if sym.startswith("BTC") else (0.01, 2)
        BitgetBroker._PSTEP[sym] = v
        return v

    def _snap_px(self, sym: str, px) -> str:
        """가격을 계약 틱에 스냅해 문자열로. 2026-08-17 첫 실측 사고(45115 'multiple of
        0.1'): 신호 손절은 Bybit 좌표 + 크로스 캘리브레이션(소수 2자리)이라 Bitget 틱
        (0.1)에 안 맞아 진입 자체가 거절됐다. ProjectX 금 틱스냅(1a51d42)과 같은 원칙 —
        정렬은 브로커층 책임. 반올림 오차는 최대 반 틱(BTC $0.05)로 무시 가능."""
        step, place = self._pstep(sym)
        q = round(round(float(px) / step) * step, place)
        return f"{q:.{place}f}"

    def place_entry(self, *, symbol, side, size, stop_loss_price=None, custom_tag=None,
                    dry_run: bool = True, **_ignored) -> dict:
        """USDT-FUTURES 마켓 진입 (+ presetStopLossPrice 손절 첨부). 크립토엔 account/contract 없음 —
        symbol·size(BTC 수량)만. ProjectX place_entry와 반환 형태 맞춤(루프 재사용).
        custom_tag → clientOid(EQ 주문 표식). ⚠ history-position 응답에 clientOid가 없어
        트랙레코드 필터는 앱의 EQ 원장이 담당 — 태그는 사후 대조용(2026-07-17)."""
        sym = self._symbol(symbol)
        _s = str(side).upper()
        if _s not in ("LONG", "SHORT"):               # LONG/SHORT 외 값이 조용히 sell 되면 방향 반전
            return {"error": f"invalid side: {side!r} (expected LONG/SHORT)"}
        bside = "buy" if _s == "LONG" else "sell"
        qty = self._fmt_qty(size)
        if float(qty) <= 0:
            return {"error": "qty<=0"}
        margin_mode = getattr(self.cfg, "margin_mode", "crossed")
        body = {"symbol": sym, "productType": self.product, "marginMode": margin_mode,
                "marginCoin": "USDT", "side": bside, "orderType": "market", "size": qty}
        if custom_tag:
            body["clientOid"] = str(custom_tag)[:64]
        if stop_loss_price:
            body["presetStopLossPrice"] = self._snap_px(sym, stop_loss_price)   # 틱스냅(45115)
        if dry_run:
            return {"would_place": body,
                    "would_place_stop": body.get("presetStopLossPrice")}
        try:
            res = self._req("POST", "/api/v2/mix/order/place-order", body=body)
        except Exception as e:
            # 헤지(양방향) 모드 계정: tradeSide(open/close) 필수 — 거절 시 open 지정 후 1회 재시도
            # (원웨이/헤지 자동 호환, 2026-07-12). 주문 미체결 거절이라 이중 진입 위험 없음.
            if "side" in str(e).lower() or "40774" in str(e):
                body["tradeSide"] = "open"
                try:
                    res = self._req("POST", "/api/v2/mix/order/place-order", body=body)
                    return {"entry": res, "stop": bool(stop_loss_price), "hedge_mode": True,
                            "stop_error": None if stop_loss_price else "no stop provided"}
                except Exception as e2:
                    return {"error": f"{e2} (hedge retry)"}
            return {"error": str(e)}
        return {"entry": res, "stop": bool(stop_loss_price),
                "stop_error": None if stop_loss_price else "no stop provided"}

    # ── 지정가 체결 정책(대표 2026-07-15) — 진입/청산 지정가 도전용 프리미티브 ──
    def place_limit_entry(self, *, symbol, side, size, price, stop_loss_price=None,
                          custom_tag=None, dry_run: bool = True) -> dict:
        """post_only 지정가 진입(+손절 첨부). 반환 {order_id}|{error}. dry_run: would_place.
        custom_tag → clientOid. 호출측이 재시도마다 유니크하게 만들어 넘긴다(중복 = 거절)."""
        sym = self._symbol(symbol)
        _s = str(side).upper()
        if _s not in ("LONG", "SHORT"):
            return {"error": f"invalid side: {side!r} (expected LONG/SHORT)"}
        bside = "buy" if _s == "LONG" else "sell"
        qty = self._fmt_qty(size)
        body = {"symbol": sym, "productType": self.product, "marginMode": "crossed",
                "marginCoin": "USDT", "side": bside, "orderType": "limit",
                "price": self._snap_px(sym, price), "size": qty, "force": "post_only"}
        if custom_tag:
            body["clientOid"] = str(custom_tag)[:64]
        if stop_loss_price:
            body["presetStopLossPrice"] = self._snap_px(sym, stop_loss_price)
        if dry_run:
            return {"would_place": body}
        try:
            r = self._req("POST", "/api/v2/mix/order/place-order", body=body)
            return {"order_id": (r or {}).get("orderId")}
        except Exception as e:
            if "side" in str(e).lower() or "40774" in str(e):    # 헤지 모드 호환
                body["tradeSide"] = "open"
                try:
                    r = self._req("POST", "/api/v2/mix/order/place-order", body=body)
                    return {"order_id": (r or {}).get("orderId")}
                except Exception as e2:
                    return {"error": f"{e2} (hedge retry)"}
            return {"error": str(e)}

    def place_limit_close(self, *, symbol, price, dry_run: bool = True) -> dict:
        """reduceOnly post_only 지정가 청산(전량). 반환 {order_id}|{error}|{flat}."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"flat": True}
        pos = mine[0]
        body = {"symbol": sym, "productType": self.product, "marginMode": "crossed",
                "marginCoin": "USDT",
                "side": "sell" if pos.net_qty > 0 else "buy",
                "orderType": "limit", "price": self._snap_px(sym, price),
                "size": self._fmt_qty(abs(pos.net_qty)), "force": "post_only",
                "reduceOnly": "YES"}
        if dry_run:
            return {"would_place": body}
        try:
            r = self._req("POST", "/api/v2/mix/order/place-order", body=body)
            return {"order_id": (r or {}).get("orderId")}
        except Exception as e:
            if "side" in str(e).lower() or "40774" in str(e):    # 헤지 모드: tradeSide=close
                body.pop("reduceOnly", None)
                body["tradeSide"] = "close"
                try:
                    r = self._req("POST", "/api/v2/mix/order/place-order", body=body)
                    return {"order_id": (r or {}).get("orderId")}
                except Exception as e2:
                    return {"error": f"{e2} (hedge retry)"}
            return {"error": str(e)}

    def cancel_order(self, symbol, order_id) -> bool:
        try:
            self._req("POST", "/api/v2/mix/order/cancel-order",
                      body={"symbol": self._symbol(symbol), "productType": self.product,
                            "marginCoin": "USDT", "orderId": str(order_id)})
            return True
        except Exception:
            return False   # 이미 체결/취소된 주문의 취소 실패는 무해

    def position_qty(self, symbol) -> float:
        """이 심볼 순포지션 수량(체결 확인용) — 0.0=플랫, -1.0=조회 실패."""
        sym = self._symbol(symbol)
        try:
            return sum(abs(p.net_qty) for p in self.list_open_positions() if p.symbol == sym)
        except Exception:
            return -1.0

    def current_market_price(self, symbol):
        try:
            d = self._req("GET", "/api/v2/mix/market/ticker",
                          {"symbol": self._symbol(symbol), "productType": self.product})
            row = d[0] if isinstance(d, list) else d
            return float(row.get("lastPr"))
        except Exception:
            return None

    def set_stop(self, symbol, stop_price):
        """포지션 보호 손절가 이동(X2+TR 트레일용, 대표 2026-08-09 Bitget 병렬 발주).
        Bitget은 Bybit(trading-stop 덮어쓰기)와 달리 포지션 손절이 플랜 주문(pos_loss)이라
        ①대기 중 손절 플랜을 찾아 modify ②없으면 pos_loss 신규 등록. 실패 시 {"error":...}
        반환 — 호출측(_btc_move_stop_be)이 경고만 남기고 기존 손절 유지(무방비 구간 없음)."""
        sym = self._symbol(symbol)
        _px = self._snap_px(sym, stop_price)   # 틱스냅 — round(,2)는 45115 거절(2026-08-17)
        try:
            pend = self._req("GET", "/api/v2/mix/order/orders-plan-pending",
                             {"productType": self.product, "symbol": sym,
                              "planType": "profit_loss"}) or {}
            rows = pend.get("entrustedList") or pend.get("list") or \
                (pend if isinstance(pend, list) else [])
            _sl = [r for r in rows
                   if str(r.get("planType", "")).lower() in ("pos_loss", "loss_plan")
                   and str(r.get("symbol", "")).upper() == sym]
        except Exception as e:
            return {"error": f"plan query: {e}"}
        if _sl:
            try:
                self._req("POST", "/api/v2/mix/order/modify-tpsl-order",
                          body={"orderId": str(_sl[0].get("orderId")), "marginCoin": "USDT",
                                "productType": self.product, "symbol": sym,
                                "triggerPrice": _px,
                                "triggerType": _sl[0].get("triggerType") or "mark_price",
                                "executePrice": "0"})          # 0 = 트리거 시 시장가
                return {"error": None}
            except Exception as e:
                return {"error": f"modify: {e}"}
        # 대기 손절 플랜 없음(진입 preset이 소진됐거나 미첨부) → 포지션 손절 신규 등록
        try:
            _side = None
            for p in self.list_open_positions():
                if p.symbol == sym:
                    _side = "long" if p.net_qty > 0 else "short"
                    break
            if _side is None:
                return {"error": "no open position"}
            self._req("POST", "/api/v2/mix/order/place-tpsl-order",
                      body={"marginCoin": "USDT", "productType": self.product, "symbol": sym,
                            "planType": "pos_loss", "triggerPrice": _px,
                            "triggerType": "mark_price", "executePrice": "0",
                            "holdSide": _side})
            return {"error": None}
        except Exception as e:
            return {"error": f"place: {e}"}

    def block_4h(self, symbol, end_utc):
        """우리 4H 블록(22-02, 02-06, …)의 (시가, 종가). end_utc = 블록 마감 UTC datetime.
        Bybit와 동일 규약: 거래소 네이티브 4H 그리드(00-04)가 우리 그리드(+2h)와 어긋나므로
        1H 4개를 직접 조립한다. 반환 (open, close) | None(판정 불가 → 호출측 보수적 홀드)."""
        from datetime import timedelta
        start = end_utc - timedelta(hours=4)
        try:
            d = self._req("GET", "/api/v2/mix/market/candles",
                          {"symbol": self._symbol(symbol), "productType": self.product,
                           "granularity": "1H",
                           "startTime": int(start.timestamp() * 1000),
                           "endTime": int(end_utc.timestamp() * 1000) - 1,
                           "limit": 10})
            lst = d if isinstance(d, list) else (d.get("list") or [])
            # Bitget candle: [ts, open, high, low, close, baseVol, usdtVol] — 정렬 방향은
            # 방어적으로 ts 기준 재정렬(오름차순) 후 블록 범위 필터.
            rows = sorted(lst, key=lambda r: int(r[0]))
            rows = [r for r in rows
                    if start.timestamp() * 1000 <= int(r[0]) < end_utc.timestamp() * 1000]
            if len(rows) < 4:              # 봉 누락 = 판정 불가 → 호출측이 홀드(보수적)
                return None
            return float(rows[0][1]), float(rows[-1][4])
        except Exception:
            return None

    def closed_fills(self, start_ms: int) -> list[dict]:
        """청산 완료 포지션의 실현손익(트랙레코드 푸시용, 파생값만).
        GET /api/v2/mix/position/history-position → [{tid, ts_ms, symbol, pnl, direction}].
        pnl = netProfit 우선(수수료 반영), 없으면 pnl. ⚠ UNTESTED(실키 검증 전)."""
        d = self._req("GET", "/api/v2/mix/position/history-position",
                      {"productType": self.product, "startTime": int(start_ms), "limit": 100})
        rows = d.get("list", d) if isinstance(d, dict) else d
        out = []
        for r in (rows or []):
            try:
                out.append({
                    "tid": str(r.get("positionId") or r.get("orderId") or ""),
                    "ts_ms": int(r.get("utime") or r.get("ctime") or 0),
                    "symbol": str(r.get("symbol") or ""),
                    "pnl": float(r.get("netProfit") if r.get("netProfit") is not None else (r.get("pnl") or 0)),
                    "direction": "LONG" if str(r.get("holdSide")).lower() == "long" else "SHORT",
                })
            except (TypeError, ValueError):
                continue
        return out

    def entry_info(self) -> list:
        """진입 관련 계정 정보(연결 테스트 로그용, 읽기전용). 주문은 교차(cross) 자동 지정."""
        out = []
        try:
            d = self._req("GET", "/api/v2/mix/account/account",
                          {"symbol": "BTCUSDT", "productType": self.product, "marginCoin": "USDT"})
            row = d[0] if isinstance(d, list) else d
            lev = row.get("crossedMarginLeverage") or row.get("leverage")
            avail = row.get("available") or row.get("crossedMaxAvailable")
            if lev:
                out.append(f"BTCUSDT 레버리지(교차) {float(lev):g}x")
            if avail:
                out.append(f"가용 잔고(마진) ≈ {float(avail):,.0f} USDT")
        except Exception:
            pass
        out.append("주문 마진모드 = 교차(cross) 자동 지정")
        return out

    def healthcheck(self) -> bool:
        self.authenticate()
        return True
