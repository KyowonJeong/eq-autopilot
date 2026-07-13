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

    def place_entry(self, *, symbol, side, size, stop_loss_price=None,
                    dry_run: bool = True, **_ignored) -> dict:
        """USDT perp 마켓 진입 (+ 손절 첨부). 크립토엔 account_id/contract 개념 없음 —
        symbol·qty(BTC 수량)만. stopLoss는 주문에 붙여 포지션 손절로 건다(별도 주문 불필요).
        ProjectX place_entry와 반환 형태 맞춤(루프 재사용): would_place/entry/stop/stop_error."""
        sym = self._symbol(symbol)
        bside = "Buy" if str(side).upper() == "LONG" else "Sell"
        qty = self._fmt_qty(size)
        if float(qty) <= 0:
            return {"error": "qty<=0"}
        body = {"category": self.category, "symbol": sym, "side": bside,
                "orderType": "Market", "qty": qty, "reduceOnly": False}
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

    def closed_fills(self, start_ms: int) -> list[dict]:
        """청산 완료 포지션의 실현손익(트랙레코드 푸시용, 파생값만).
        GET /v5/position/closed-pnl → [{tid, ts_ms, symbol, pnl, direction}].
        direction = 포지션 방향(청산 주문 side의 반대: Sell로 닫음 = LONG이었음).
        ⚠ UNTESTED(실키 검증 전). 실패 시 예외 — 호출측이 로그."""
        out, cursor = [], ""
        for _ in range(10):                                 # 최대 10페이지(안전 상한)
            params = {"category": self.category, "startTime": int(start_ms), "limit": 100}
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
