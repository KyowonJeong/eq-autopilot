"""Bybit adapter (USDT perpetuals) — auto-close via the V5 REST API.

Flatten = read open positions, close each with a reduceOnly Market order on the opposite side.
Runs on the user's machine with their own Bybit API key/secret (trade permission; ideally
IP-restricted). ⚠ UNTESTED — verify on **testnet** (api-testnet.bybit.com) before live. dry_run
sends NO orders. V5 signing: HMAC_SHA256(timestamp + api_key + recv_window + body, secret).
"""
from __future__ import annotations

import hashlib
import hmac
import json as _json
import time

import requests

from .base import BrokerAdapter, FlattenResult, Position

_RECV = "5000"
_TIMEOUT = (10, 30)


class BybitBroker(BrokerAdapter):
    name = "bybit"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.BybitCfg
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self.base = ("https://api-testnet.bybit.com" if getattr(cfg, "testnet", False)
                     else "https://api.bybit.com")
        self.category = getattr(cfg, "category", "linear")     # linear = USDT perps
        self.settle = getattr(cfg, "settle_coin", "USDT")

    def _sign(self, ts: str, payload: str) -> str:
        return hmac.new(self.secret.encode(), f"{ts}{self.key}{_RECV}{payload}".encode(),
                        hashlib.sha256).hexdigest()

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        ts = str(int(time.time() * 1000))
        if method == "GET":
            qs = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
            sign = self._sign(ts, qs)
            url = f"{self.base}{path}" + (f"?{qs}" if qs else "")
            r = requests.get(url, headers=self._headers(ts, sign), timeout=_TIMEOUT)
        else:
            payload = _json.dumps(body or {}, separators=(",", ":"))
            sign = self._sign(ts, payload)
            r = requests.post(f"{self.base}{path}", data=payload,
                              headers={**self._headers(ts, sign), "Content-Type": "application/json"},
                              timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if d.get("retCode") not in (0, None):
            raise RuntimeError(f"Bybit {path} failed: retCode={d.get('retCode')} {d.get('retMsg')}")
        return d.get("result", d)

    def _headers(self, ts: str, sign: str) -> dict:
        return {"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": _RECV, "X-BAPI-SIGN": sign}

    def authenticate(self) -> None:
        # Cheap signed read to validate the key.
        self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})

    # ── 잔고-맞춤 자동 레버리지 (대표 2026-07-20) ────────────────────────────
    # 2026-07-19 BTC 첫 라이브 진입이 110007(ab not enough)로 실패 — 잔고 $5,590인데
    # 레버리지 10x라 명목 $70.7k에 증거금 $7,070이 필요했던 것. BTC는 손절이 가격의 ~0.2%라
    # '리스크의 ~500배 명목'이 구조적이라, 앱이 진입 전에 잔고를 보고 레버리지를 맞춰 준다.
    # (상향만 한다 — 유저가 높여둔 값은 존중. 교차 마진에서 실위험은 손절=1R로 고정이고
    #  레버리지는 증거금 효율만 결정하므로 상향은 위험 증가가 아니다.)
    def available_usdt(self):
        """UNIFIED 가용 잔고(USDT, totalAvailableBalance). 실패 시 None."""
        try:
            d = self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            lst = (d or {}).get("list") or []
            v = (lst[0] or {}).get("totalAvailableBalance") if lst else None
            return float(v) if v not in (None, "") else None
        except Exception:
            return None

    def current_leverage(self, symbol):
        """심볼의 현재 레버리지 설정(포지션 유무 무관 — position/list가 설정값을 준다)."""
        try:
            d = self._req("GET", "/v5/position/list",
                          {"category": self.category, "symbol": self._symbol(symbol)})
            lst = (d or {}).get("list") or []
            v = (lst[0] or {}).get("leverage") if lst else None
            return float(v) if v not in (None, "") else None
        except Exception:
            return None

    def max_leverage(self, symbol):
        """심볼 최대 허용 레버리지(instruments-info leverageFilter)."""
        try:
            d = self._req("GET", "/v5/market/instruments-info",
                          {"category": self.category, "symbol": self._symbol(symbol)})
            lst = (d or {}).get("list") or []
            v = ((lst[0] or {}).get("leverageFilter") or {}).get("maxLeverage") if lst else None
            return float(v) if v else None
        except Exception:
            return None

    def set_leverage(self, symbol, lev) -> bool:
        """buy/sell 동시 설정. 110043(leverage not modified)=동일값이므로 성공 취급."""
        body = {"category": self.category, "symbol": self._symbol(symbol),
                "buyLeverage": f"{float(lev):g}", "sellLeverage": f"{float(lev):g}"}
        try:
            self._req("POST", "/v5/position/set-leverage", body=body)
            return True
        except Exception as e:
            if "110043" in str(e):
                return True
            raise

    def ensure_leverage(self, symbol, size, price) -> dict:
        """이 주문(size×price)이 현재 잔고로 들어가도록 레버리지를 자동 상향.
        필요 = 명목/(가용×0.85 수수료·변동 버퍼), 30% 여유를 얹고 심볼 상한에서 캡.
        반환 {ab, notional, cur, new|None, error|None} — 호출측 로그 전용, 예외 없음."""
        import math
        try:
            ab = self.available_usdt()
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
        d = self._req("GET", "/v5/position/list",
                      {"category": self.category, "settleCoin": self.settle})
        out: list[Position] = []
        for p in d.get("list", []):
            size = float(p.get("size") or 0)
            if size == 0:
                continue
            net = size if str(p.get("side")).lower() == "buy" else -size
            out.append(Position(account_id="bybit", account_name="bybit",
                                symbol=str(p.get("symbol")), net_qty=net, raw=p))
        return out

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        for pos in plan:
            try:
                side = "Sell" if pos.net_qty > 0 else "Buy"        # opposite, reduceOnly
                body = {"category": self.category, "symbol": pos.symbol, "side": side,
                        "orderType": "Market",
                        # Bybit 원본 size 문자열을 그대로(코인 수량 형식 보존 — float 재포맷의 '2.0' 회피).
                        "qty": str(pos.raw.get("size") or abs(pos.net_qty)),
                        "reduceOnly": True}
                _pi = pos.raw.get("positionIdx")                   # 헤지 모드면 필수(one-way=0)
                if _pi is not None:
                    body["positionIdx"] = _pi
                self._req("POST", "/v5/order/create", body=body)
            except Exception as e:
                res.errors.append(f"{pos.symbol}: {e}")
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
        """이 심볼 포지션만 청산(다른 심볼 무접촉) — 연속 세션(BTC 02↔22) '청산 확인 후 진입'용.
        reduceOnly 반대 마켓 + 최대 ~6초 재확인 폴링. 반환 {closed: bool, had: n, error}."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"closed": True, "had": 0}
        if dry_run:
            return {"closed": False, "had": len(mine), "dry_run": True}
        for pos in mine:
            try:
                body = {"category": self.category, "symbol": pos.symbol,
                        "side": "Sell" if pos.net_qty > 0 else "Buy",
                        "orderType": "Market",
                        "qty": str(pos.raw.get("size") or abs(pos.net_qty)),
                        "reduceOnly": True}
                _pi = pos.raw.get("positionIdx")
                if _pi is not None:
                    body["positionIdx"] = _pi
                self._req("POST", "/v5/order/create", body=body)
            except Exception as e:
                return {"closed": False, "had": len(mine), "error": f"{pos.symbol}: {e}"}
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
        """신호 심볼('BTCUSDT.P')/기타 → Bybit perp 심볼('BTCUSDT')."""
        s = str(sym or "").upper().replace(".P", "").replace("-", "").replace("/", "")
        return s or "BTCUSDT"

    @staticmethod
    def _fmt_qty(size) -> str:
        """BTC 수량 → 문자열(랏 0.001, 소수 3자리). float '2.0' 재포맷 회피용 rstrip."""
        q = round(float(size), 3)
        return f"{q:.3f}".rstrip("0").rstrip(".") or "0"

    def place_entry(self, *, symbol, side, size, stop_loss_price=None, custom_tag=None,
                    dry_run: bool = True, **_ignored) -> dict:
        """USDT perp 마켓 진입 (+ 손절 첨부). 크립토엔 account_id/contract 개념 없음 —
        symbol·qty(BTC 수량)만. stopLoss는 주문에 붙여 포지션 손절로 건다(별도 주문 불필요).
        ProjectX place_entry와 반환 형태 맞춤(루프 재사용): would_place/entry/stop/stop_error.
        custom_tag → orderLinkId(EQ 주문 표식). ⚠ 트랙레코드 필터는 이걸 못 쓴다 —
        closed-pnl 응답에 orderLinkId가 없어서(2026-07-17). 필터는 앱의 EQ 원장이 담당.
        태그는 사후 대조·지원 문의용으로 심어둔다."""
        sym = self._symbol(symbol)
        bside = "Buy" if str(side).upper() == "LONG" else "Sell"
        qty = self._fmt_qty(size)
        if float(qty) <= 0:
            return {"error": "qty<=0"}
        body = {"category": self.category, "symbol": sym, "side": bside,
                "orderType": "Market", "qty": qty, "reduceOnly": False}
        if custom_tag:
            body["orderLinkId"] = str(custom_tag)[:36]     # Bybit 상한 36자
        if stop_loss_price:
            body["stopLoss"] = str(stop_loss_price)
            body["slTriggerBy"] = "LastPrice"
        if dry_run:
            return {"would_place": body,
                    "would_place_stop": (str(stop_loss_price) if stop_loss_price else None)}
        try:
            res = self._req("POST", "/v5/order/create", body=body)
        except Exception as e:
            # 헤지(양방향) 모드 계정: positionIdx 필수(10001 position idx not match) —
            # 주문이 안 나간 거절이므로 사이드 지정 후 1회 재시도(원웨이/헤지 자동 호환, 2026-07-12).
            if "10001" in str(e) or "position idx" in str(e).lower():
                body["positionIdx"] = 1 if bside == "Buy" else 2
                try:
                    res = self._req("POST", "/v5/order/create", body=body)
                    return {"entry": res, "stop": bool(stop_loss_price), "hedge_mode": True,
                            "stop_error": None if stop_loss_price else "no stop provided"}
                except Exception as e2:
                    return {"error": f"{e2} (hedge retry)"}
            return {"error": str(e)}
        # stopLoss는 진입 주문에 첨부돼 함께 체결 → 진입 성공 = 손절도 설정됨.
        return {"entry": res, "stop": bool(stop_loss_price),
                "stop_error": None if stop_loss_price else "no stop provided"}

    # ── 지정가 체결 정책(대표 2026-07-15) — 진입/청산 지정가 도전용 프리미티브 ──
    def place_limit_entry(self, *, symbol, side, size, price, stop_loss_price=None,
                          custom_tag=None, dry_run: bool = True) -> dict:
        """PostOnly 지정가 진입(+손절 첨부). 반환 {order_id}|{error}. dry_run: would_place.
        custom_tag → orderLinkId. 호출측이 재시도마다 유니크하게 만들어 넘긴다(중복 = 거절)."""
        sym = self._symbol(symbol)
        bside = "Buy" if str(side).upper() == "LONG" else "Sell"
        qty = self._fmt_qty(size)
        body = {"category": self.category, "symbol": sym, "side": bside,
                "orderType": "Limit", "price": f"{float(price):g}", "qty": qty,
                "timeInForce": "PostOnly", "reduceOnly": False}
        if custom_tag:
            body["orderLinkId"] = str(custom_tag)[:36]
        if stop_loss_price:
            body["stopLoss"] = str(stop_loss_price)
            body["slTriggerBy"] = "LastPrice"
        if dry_run:
            return {"would_place": body}
        try:
            r = self._req("POST", "/v5/order/create", body=body)
            return {"order_id": (r or {}).get("orderId")}
        except Exception as e:
            if "10001" in str(e) or "position idx" in str(e).lower():   # 헤지 모드 호환
                body["positionIdx"] = 1 if bside == "Buy" else 2
                try:
                    r = self._req("POST", "/v5/order/create", body=body)
                    return {"order_id": (r or {}).get("orderId")}
                except Exception as e2:
                    return {"error": f"{e2} (hedge retry)"}
            return {"error": str(e)}

    def place_limit_close(self, *, symbol, price, dry_run: bool = True) -> dict:
        """reduceOnly PostOnly 지정가 청산(현 포지션 반대방향 전량). 반환 {order_id}|{error}|{flat}."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"flat": True}
        pos = mine[0]
        body = {"category": self.category, "symbol": sym,
                "side": "Sell" if pos.net_qty > 0 else "Buy",
                "orderType": "Limit", "price": f"{float(price):g}",
                "qty": str(pos.raw.get("size") or abs(pos.net_qty)),
                "timeInForce": "PostOnly", "reduceOnly": True}
        _pi = pos.raw.get("positionIdx")
        if _pi is not None:
            body["positionIdx"] = _pi
        if dry_run:
            return {"would_place": body}
        try:
            r = self._req("POST", "/v5/order/create", body=body)
            return {"order_id": (r or {}).get("orderId")}
        except Exception as e:
            return {"error": str(e)}

    def cancel_order(self, symbol, order_id) -> bool:
        try:
            self._req("POST", "/v5/order/cancel",
                      body={"category": self.category, "symbol": self._symbol(symbol),
                            "orderId": str(order_id)})
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
            d = self._req("GET", "/v5/market/tickers",
                          {"category": self.category, "symbol": self._symbol(symbol)})
            return float((d.get("list") or [{}])[0].get("lastPrice"))
        except Exception:
            return None

    def set_stop(self, symbol, stop_price):
        """포지션의 보호 손절가를 변경(X2+BE 본절 이동용). POST /v5/position/trading-stop.
        Bybit는 stopLoss를 포지션 속성으로 관리 → 값만 덮어쓰면 기존 스탑이 갱신된다
        (취소→재등록 사이 무방비 구간이 없다). 반환 {"error": None|str}."""
        try:
            self._req("POST", "/v5/position/trading-stop", body={
                "category": self.category, "symbol": self._symbol(symbol),
                "stopLoss": str(round(float(stop_price), 2)),
                "positionIdx": 0,          # one-way 모드
            })
            return {"error": None}
        except Exception as e:
            return {"error": str(e)}

    def block_4h(self, symbol, end_utc):
        """우리 4H 블록(22-02, 02-06, …)의 (시가, 종가). end_utc = 블록 마감 UTC datetime.
        ⚠거래소 네이티브 4H 그리드는 00-04라 우리 그리드(+2h 오프셋)와 어긋난다 → **1H 4개를
        직접 조립**한다(백테스트 build_sheet의 +2h 오프셋 재조립과 동일 규약).
        반환 (open, close) | None. X2 출구('반대 봉 마감 시 청산') 판정용."""
        from datetime import timedelta
        start = end_utc - timedelta(hours=4)
        try:
            d = self._req("GET", "/v5/market/kline", {
                "category": self.category, "symbol": self._symbol(symbol), "interval": "60",
                "start": int(start.timestamp() * 1000),
                "end": int((end_utc - timedelta(seconds=1)).timestamp() * 1000),
                "limit": 10,
            })
            lst = d.get("list") or []
            # Bybit kline: [startMs, open, high, low, close, vol, turnover], 최신→과거 정렬
            rows = sorted(lst, key=lambda r: int(r[0]))
            rows = [r for r in rows
                    if start.timestamp() * 1000 <= int(r[0]) < end_utc.timestamp() * 1000]
            if len(rows) < 4:            # 봉 누락 = 판정 불가 → 호출측이 홀드(보수적)
                return None
            return float(rows[0][1]), float(rows[-1][4])
        except Exception:
            return None

    def closed_fills(self, start_ms: int) -> list[dict]:
        """청산 완료 포지션의 실현손익(트랙레코드 푸시용, 파생값만).
        GET /v5/position/closed-pnl → [{tid, ts_ms, symbol, pnl, direction}].
        direction = 포지션 방향(청산 주문 side의 반대: Sell로 닫음 = LONG이었음).
        ⚠ UNTESTED(실키 검증 전). 실패 시 예외 — 호출측이 로그."""
        # ⚠ Bybit 제약: closed-pnl은 조회창 7일 초과 시 startTime 주변 7일만 반환
        # (2026-07-15 실사고: 90일 요청 → 90일 전 그 주만 오고 최근 BTCUSDT 누락).
        # → 7일 창으로 분할해 전 구간 수집.
        out = []
        WEEK_MS = 7 * 86400 * 1000 - 1000
        t0 = int(start_ms)
        now_ms = int(time.time() * 1000)
        while t0 < now_ms:
            t1 = min(t0 + WEEK_MS, now_ms)
            cursor = ""
            for _ in range(10):                             # 창당 최대 10페이지(안전 상한)
                params = {"category": self.category, "startTime": t0, "endTime": t1, "limit": 100}
                if cursor:
                    params["cursor"] = cursor
                d = self._req("GET", "/v5/position/closed-pnl", params)
                for r in d.get("list", []):
                    try:
                        out.append({
                            "tid": str(r.get("orderId") or r.get("execId") or ""),
                            "ts_ms": int(r.get("updatedTime") or r.get("createdTime") or 0),
                            "symbol": str(r.get("symbol") or ""),
                            "pnl": float(r.get("closedPnl") or 0),
                            "direction": "LONG" if str(r.get("side")).lower() == "sell" else "SHORT",
                        })
                    except (TypeError, ValueError):
                        continue
                cursor = d.get("nextPageCursor") or ""
                if not cursor:
                    break
            t0 = t1 + 1000
        return out

    def entry_info(self) -> list:
        """진입 관련 계정 정보(연결 테스트 로그용, 읽기전용): BTCUSDT 레버리지·마진모드·가용잔고.
        변경은 안 함 — 정보 제공만(대표 2026-07-12). 실패 필드는 조용히 생략."""
        out = []
        try:
            d = self._req("GET", "/v5/position/list", {"category": self.category, "symbol": "BTCUSDT"})
            row = (d.get("list") or [{}])[0]
            lev = row.get("leverage")
            tm = row.get("tradeMode")
            mode = {0: "교차(cross)", 1: "격리(isolated)"}.get(int(tm) if tm is not None else -1, "")
            if lev:
                out.append(f"BTCUSDT 레버리지 {float(lev):g}x" + (f" · {mode}" if mode else ""))
        except Exception:
            pass
        try:
            w = self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            acct = (w.get("list") or [{}])[0]
            avail = acct.get("totalAvailableBalance") or acct.get("totalEquity")
            if avail:
                out.append(f"가용 잔고(마진) ≈ {float(avail):,.0f} USDT")
        except Exception:
            pass
        return out

    def key_info(self) -> list:
        """API 키 사전 점검 경고 목록(빈 리스트 = 이상 없음). GET /v5/user/query-api:
        읽기전용 여부·계약 주문 권한·만료 잔여일(deadlineDay, IP 미등록 키의 3개월 만료)."""
        out = []
        d = self._req("GET", "/v5/user/query-api")
        if int(d.get("readOnly") or 0) == 1:
            out.append("키가 읽기 전용입니다 — 주문 불가")
        ct = (d.get("permissions") or {}).get("ContractTrade") or []
        if "Order" not in ct:
            out.append("계약(Contract) 주문 권한이 없습니다")
        try:
            dd = int(d.get("deadlineDay") if d.get("deadlineDay") is not None else -1)
        except (TypeError, ValueError):
            dd = -1
        if 0 <= dd <= 7:
            out.append(f"키 만료 D-{dd} — IP 미등록 키는 3개월 만료. 재발급 또는 IP 등록 필요")
        return out

    def healthcheck(self) -> bool:
        self.authenticate()
        return True
