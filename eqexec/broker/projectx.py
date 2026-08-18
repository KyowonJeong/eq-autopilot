"""ProjectX Gateway adapter (Phase 1 — Topstep / TopstepX).

ProjectX is multi-tenant: each firm has its own base URL. TopstepX = https://api.topstepx.com.
Endpoints (ProjectX Gateway docs — see PLAN.md). VERIFY against a TopstepX demo/eval account
before live: account search shape, position `type` long/short mapping, and that closeContract
fully flattens. dry_run=True sends NO close calls.
"""
from __future__ import annotations

import time

import requests

from .base import BrokerAdapter, FlattenResult, Position

_TIMEOUT = (10, 30)
_TOKEN_TTL = 24 * 60 * 60       # ProjectX session token ~24h
_RENEW_MARGIN = 60 * 60         # re-auth 1h before expiry
_LONG = 1                       # position.type: 1=long, 2=short (docs don't state it; common ProjectX
                                # convention). Only affects the displayed net sign — closeContract
                                # flattens the whole position regardless. ⚠ 2026-08-18: no longer
                                # cosmetic - eqgui pass-TP uses net_qty sign for open-PnL math.
                                # VERIFY type mapping (1=long?) live; pass-TP holds off on ∉(1,2).
_SIDE = {"BUY": 0, "LONG": 0, "BID": 0, 0: 0, "SELL": 1, "SHORT": 1, "ASK": 1, 1: 1}

# 계약 메타데이터 조회 실패 시 폴백 틱사이즈(우리가 다루는 심볼만).
_FALLBACK_TICK = {"MNQ": 0.25, "NQ": 0.25, "MGC": 0.1, "GC": 0.1}


def _side_code(side):
    s = side.upper() if isinstance(side, str) else side
    if s not in _SIDE:
        raise ValueError(f"bad side {side!r} (use BUY/LONG/0 or SELL/SHORT/1)")
    return _SIDE[s]


def _snap_to_tick(price: float, tick: float) -> float:
    """가격을 틱 그리드에 스냅(최근접 틱). ProjectX는 틱 미정렬 가격을 거부한다
    (errorCode=2 'Invalid stop price. Price is not aligned to tick size.' — 2026-07-14 GC 실사고).
    steps*tick은 이진 부동소수 잔여 오차가 남을 수 있어 틱 소수 자릿수로 재반올림."""
    steps = round(float(price) / tick)
    decimals = len(str(tick).split(".")[1]) if "." in str(tick) else 0
    return round(steps * tick, decimals)


class ProjectXBroker(BrokerAdapter):
    name = "projectx"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.ProjectXCfg
        self.base = cfg.base_url.rstrip("/")
        self._token: str | None = None
        self._token_at: float = 0.0
        self._ticks: dict[str, float] = {}   # contractId → tickSize 캐시

    # ── auth ──────────────────────────────────────────────────────────────
    def authenticate(self) -> None:
        r = requests.post(f"{self.base}/api/Auth/loginKey",
                          json={"userName": self.cfg.user_name, "apiKey": self.cfg.api_key},
                          timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if not d.get("success") or not d.get("token"):
            raise RuntimeError(f"ProjectX auth failed (errorCode={d.get('errorCode')}): "
                               f"{d.get('errorMessage') or d}")
        self._token, self._token_at = d["token"], time.time()

    def _ensure_token(self) -> None:
        if self._token is None or (time.time() - self._token_at) > (_TOKEN_TTL - _RENEW_MARGIN):
            self.authenticate()

    def _post(self, path: str, body: dict):
        self._ensure_token()
        r = requests.post(f"{self.base}{path}",
                          headers={"Authorization": f"Bearer {self._token}",
                                   "Content-Type": "application/json"},
                          json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        # ProjectX returns HTTP 200 even on logical failures — the response's success/errorCode
        # is the real status (confirmed against gateway.docs.projectx.com). Treat success:false
        # as an error so a failed flatten/read never passes silently.
        if isinstance(d, dict) and d.get("success") is False:
            raise RuntimeError(f"ProjectX {path} failed (errorCode={d.get('errorCode')}): "
                               f"{d.get('errorMessage') or d}")
        return d

    # ── reads ─────────────────────────────────────────────────────────────
    def _accounts(self, active_only: bool = True) -> list[dict]:
        # POST /api/Account/search {onlyActiveAccounts} -> {accounts:[{id,name,balance,canTrade,
        # isVisible}], ...}. active_only=False면 로테이션으로 닫힌 계좌까지 포함(트랙레코드용,
        # 대표 2026-07-29: 프롭 로테이션으로 빠진 계좌의 과거 체결이 트랙레코드에서 누락되던 버그).
        d = self._post("/api/Account/search", {"onlyActiveAccounts": bool(active_only)})
        accts = d.get("accounts", d if isinstance(d, list) else [])
        want = {a.lower() for a in (self.cfg.accounts or [])}
        if want:
            accts = [a for a in accts
                     if str(a.get("name", "")).lower() in want or str(a.get("id")) in want]
        return accts

    def account_balance(self, acct):
        """계좌(이름 또는 id) 현재 잔고($) — 프롭 페이즈 자동 사이징용(대표 2026-07-26).
        못 찾거나 API 실패 시 None(호출부가 버퍼기 1R로 폴백)."""
        want = str(acct or "").strip().lower()
        if not want:
            return None
        for a in self._accounts():
            if str(a.get("name", "")).lower() == want or str(a.get("id")) == want:
                bal = a.get("balance")
                return float(bal) if bal is not None else None
        return None

    def search_contracts(self, text: str, live: bool = False) -> list[dict]:
        """POST /api/Contract/search {searchText, live} -> {contracts:[{id, name, description,
        tickSize, tickValue, activeContract, ...}]}. Used to resolve the current front-month
        contractId for a symbol (e.g. 'MNQ') so the user doesn't hand-type an expired code."""
        d = self._post("/api/Contract/search", {"searchText": text, "live": bool(live)})
        return d.get("contracts", d if isinstance(d, list) else [])

    def _tick_size(self, contract_id: str):
        """contractId(예: CON.F.US.MGC.Q26)의 tickSize — Contract/search 메타데이터에서 조회(캐시),
        실패 시 심볼 루트로 폴백 테이블. 못 찾으면 None(정렬 없이 원가격 사용)."""
        if contract_id in self._ticks:
            return self._ticks[contract_id]
        parts = str(contract_id).split(".")
        sym = parts[3] if len(parts) >= 4 else str(contract_id)
        tick = None
        try:
            for c in self.search_contracts(sym):
                if str(c.get("id")) == str(contract_id) and c.get("tickSize"):
                    tick = float(c["tickSize"])
                    break
        except Exception:
            pass
        if not tick:
            tick = _FALLBACK_TICK.get(sym.upper())
        if tick:
            self._ticks[contract_id] = tick
        return tick

    def _align(self, contract_id: str, price) -> float:
        """주문 가격을 계약 틱 그리드에 정렬. 틱을 못 알아내면 원가격 그대로(서버 판정에 맡김)."""
        tick = self._tick_size(contract_id)
        return _snap_to_tick(price, tick) if tick else float(price)

    def _open_orders(self, account_id) -> list[dict]:
        """POST /api/Order/searchOpen {accountId} -> {orders:[{id, ...}]}. Working (resting) orders
        — e.g. a protective stop left behind after a position closes."""
        d = self._post("/api/Order/searchOpen", {"accountId": account_id})
        return d.get("orders", d if isinstance(d, list) else [])

    def _cancel_order(self, account_id, order_id) -> None:
        """POST /api/Order/cancel {accountId, orderId}. Cancel one working order."""
        self._post("/api/Order/cancel", {"accountId": int(account_id), "orderId": order_id})

    def list_open_positions(self) -> list[Position]:
        out: list[Position] = []
        for a in self._accounts():
            aid = a.get("id")
            d = self._post("/api/Position/searchOpen", {"accountId": aid})
            for p in d.get("positions", []):
                size = int(p.get("size", 0) or 0)
                if size == 0:
                    continue
                net = size if p.get("type") == _LONG else -size
                out.append(Position(
                    account_id=str(aid),
                    account_name=a.get("name", str(aid)),
                    symbol=str(p.get("contractId")),
                    net_qty=net,
                    raw={**p, "_accountId": aid},
                ))
        return out

    # ── act ───────────────────────────────────────────────────────────────
    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run:
            return res
        for pos in plan:
            try:
                # closeContract market-closes the entire position for {accountId, contractId}.
                self._post("/api/Position/closeContract", {
                    "accountId": int(pos.raw["_accountId"]),
                    "contractId": pos.raw.get("contractId"),
                })
            except Exception as e:
                res.errors.append(f"{pos.account_name}/{pos.symbol}: {e}")
        # "Flat" = no positions AND no working orders. Cancel any resting orders (e.g. a protective
        # stop left behind after the position closed) so a leftover stop can't re-open a position
        # next session. Best-effort: failures are logged, never block the position close above.
        for a in self._accounts():
            aid = a.get("id")
            try:
                for o in self._open_orders(aid):
                    oid = o.get("id")
                    try:
                        self._cancel_order(aid, oid)
                        res.cancelled.append(f"{a.get('name', aid)}:{oid}")
                    except Exception as e:
                        res.errors.append(f"cancel order {oid} failed: {e}")
            except Exception as e:
                res.errors.append(f"{a.get('name', aid)}: open-order read failed: {e}")
        # Confirm-after-act: re-read; anything still open is an error to alert on loudly.
        try:
            still = self.list_open_positions()
            res.closed = [p for p in plan if not any(
                s.account_id == p.account_id and s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.account_name}/{p.symbol} "
                                  f"net={p.net_qty}")
        except Exception as e:
            res.errors.append(f"post-flatten position re-check failed: {e}")
        return res

    def entry_info(self) -> list:
        """진입 관련 계정 정보(연결 테스트 로그용): 계좌별 잔고(마이크로 선물 마진 여력 가늠)."""
        out = []
        try:
            for a in self._accounts():
                bal = a.get("balance")
                if bal is not None:
                    out.append(f"{a.get('name')}: 잔고 ${float(bal):,.0f}")
        except Exception:
            pass
        return out

    def closed_fills(self, start_ms: int) -> list[dict]:
        """실현손익 체결 목록(트랙레코드 푸시용). POST /api/Trade/search {accountId,
        startTimestamp} → trades[{id, contractId, creationTimestamp, profitAndLoss, side, ...}].
        profitAndLoss=null은 진입 반턴(half-turn) → 제외, 청산 반턴만 수집.
        direction = 포지션 방향(청산 체결 side의 반대: 1(sell)로 닫음 = LONG이었음).
        ⚠ UNTESTED(실키 검증 전). 실패 시 예외 — 호출측이 로그."""
        from datetime import datetime, timezone
        start_iso = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
        out = []
        # 트랙레코드 = 비활성(로테이션으로 닫힌) 계좌까지 전부 조회(대표 2026-07-29 실사고:
        # 프롭 계좌 로테이션 후 그 계좌의 과거 익절이 통째 누락). fill마다 계좌 id 부착 →
        # 호출측이 계좌별 1R로 정규화(닫힌 계좌는 1R을 몰라 폴백).
        for a in self._accounts(active_only=False):
            try:
                d = self._post("/api/Trade/search",
                               {"accountId": a["id"], "startTimestamp": start_iso})
            except Exception:
                continue
            for t in d.get("trades", []) or []:
                pnl = t.get("profitAndLoss")
                if pnl is None or t.get("voided"):
                    continue
                try:
                    ts = t.get("creationTimestamp") or ""
                    ts_ms = int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
                    out.append({
                        "tid": str(t.get("id") or ""),
                        "ts_ms": ts_ms,
                        "symbol": str(t.get("contractId") or ""),     # 예: CON.F.US.GCE.Q26
                        "pnl": float(pnl) - float(t.get("fees") or 0),
                        "direction": "LONG" if int(t.get("side") or 0) == 1 else "SHORT",
                        "acct": str(a.get("id") or ""),               # 계좌 id(정규화·닫힌계좌 판별)
                        "acct_name": str(a.get("name") or ""),
                    })
                except (TypeError, ValueError):
                    continue
        return out

    def healthcheck(self) -> bool:
        self.authenticate()
        self._accounts()
        return True

    # ── entry (order placement) — for the optional auto-ENTRY path. dry_run sends nothing. ──
    def place_entry(self, account_id, contract_id: str, side, size: int, *,
                    order_type: int = 2, limit_price=None, stop_price=None,
                    stop_loss_ticks=None, stop_loss_price=None, take_profit_ticks=None,
                    custom_tag=None, dry_run: bool = True):
        """Place an entry order via POST /api/Order/place (shapes confirmed against the docs).
          side: 'BUY'/'LONG'/0  or  'SELL'/'SHORT'/1   (0=Bid/buy, 1=Ask/sell)
          order_type: 2=Market (default), 1=Limit, 4=Stop
          stop_loss_ticks  — protective bracket, distance in TICKS from fill (broker-managed OCO).
          stop_loss_price  — protective stop at an ABSOLUTE price. Placed as a SEPARATE Stop order
                             on the opposite side after the entry (the daily flatten cancels any
                             leftover working order). Prefer this when the signal gives a price.
        Returns the order dict(s) on live, or the request body/bodies (nothing sent) on dry_run."""
        side_code = _side_code(side)
        body = {"accountId": int(account_id), "contractId": contract_id,
                "type": int(order_type), "side": side_code, "size": int(size)}
        if limit_price is not None:
            body["limitPrice"] = self._align(contract_id, limit_price)
        if stop_price is not None:
            body["stopPrice"] = self._align(contract_id, stop_price)
        if custom_tag:
            body["customTag"] = custom_tag
        if stop_loss_ticks is not None:                 # bracket: type 4 = Stop
            body["stopLossBracket"] = {"ticks": int(stop_loss_ticks), "type": 4}
        if take_profit_ticks is not None:               # bracket: type 1 = Limit
            body["takeProfitBracket"] = {"ticks": int(take_profit_ticks), "type": 1}

        # Absolute-price protective stop = a standalone Stop (type 4) on the OPPOSITE side.
        stop_body = None
        if stop_loss_price is not None:
            stop_body = {"accountId": int(account_id), "contractId": contract_id,
                         "type": 4, "side": 1 - side_code, "size": int(size),
                         "stopPrice": self._align(contract_id, stop_loss_price)}
            if custom_tag:
                stop_body["customTag"] = f"{custom_tag}-SL"

        if dry_run:
            return {"dry_run": True, "would_place": body, "would_place_stop": stop_body}
        entry = self._post("/api/Order/place", body)
        if stop_loss_price is None:
            return entry
        # Place the protective stop as a separate order; classify a failure so the caller can
        # decide: a broker rejection (stop_rejected) is permanent → flatten; transient → retry.
        return {"entry": entry,
                **self.place_protective_stop(account_id, contract_id, side, size,
                                             stop_loss_price, custom_tag=custom_tag)}

    def place_protective_stop(self, account_id, contract_id, entry_side, size: int,
                              stop_price, *, custom_tag=None) -> dict:
        """(Re)place a standalone protective Stop (type 4) on the side OPPOSITE the entry, at an
        absolute price — used after a market entry, and retried by the caller on a transient miss.
        Returns {"stop": <order>, "stop_price": <tick-aligned px>} on success,
        else {"stop_error": str, "stop_rejected": bool}:
          stop_rejected=True  → broker said no (HTTP 200 success:false) — won't change on retry.
          stop_rejected=False → transient network/HTTP error — safe to retry."""
        px = self._align(contract_id, stop_price)
        body = {"accountId": int(account_id), "contractId": contract_id, "type": 4,
                "side": 1 - _side_code(entry_side), "size": int(size),
                "stopPrice": px}
        if custom_tag:
            body["customTag"] = f"{custom_tag}-SL"
        try:
            return {"stop": self._post("/api/Order/place", body), "stop_price": px}
        except requests.RequestException as e:           # timeout / connection / 5xx → transient
            return {"stop_error": str(e), "stop_rejected": False}
        except Exception as e:                            # RuntimeError(success:false) → broker reject
            return {"stop_error": str(e), "stop_rejected": True}

    # ── 지정가 체결 정책 지원 (대표 2026-07-15: GC 시장가 슬리피지 = 건당 $64) ─────────
    # 크립토(bybit)에만 있던 4종을 선물에도 제공 → eqgui의 지정가 정책을 선물에 그대로 이식.
    # 근거: MGC는 손절이 타이트($26/계약)해 1R=$600이면 23계약 → 1틱($1) 밀리면 왕복 $46.
    #       커미션($18)보다 슬리피지가 크다. NQ는 11계약뿐이라 영향 작음(건당 $19).
    def place_limit_entry(self, account_id, contract_id, side, size: int, price,
                          *, stop_loss_price=None, custom_tag=None, dry_run: bool = False):
        """지정가(type 1) 진입. 체결 확인은 호출측이 position_qty로 폴링(크립토와 동일 계약).
        보호 손절은 체결 확인 후 호출측이 place_protective_stop으로 건다 —
        미체결 상태에서 손절부터 걸면 무포지션 스탑이 남는다."""
        if dry_run:
            return {"dry_run": True, "would_place": {"type": 1, "limitPrice": self._align(contract_id, price),
                                                     "side": _side_code(side), "size": int(size)}}
        try:
            r = self.place_entry(account_id=account_id, contract_id=contract_id, side=side,
                                 size=size, order_type=1, limit_price=price,
                                 custom_tag=custom_tag, dry_run=False)
            return {"order_id": (r or {}).get("orderId"), "error": None}
        except Exception as e:
            return {"order_id": None, "error": str(e)}

    def position_qty(self, account_id, contract_id):
        """이 {계좌, 계약}의 현재 포지션 수량(절대값). 미보유 0. 지정가 체결 확인용."""
        try:
            d = self._post("/api/Position/searchOpen", {"accountId": int(account_id)})
            for p in d.get("positions", []) or []:
                if str(p.get("contractId")) == str(contract_id):
                    return abs(int(p.get("size", 0) or 0))
        except Exception:
            return None          # 조회 실패는 '미체결'과 구분 — 호출측이 판단
        return 0

    def cancel_order(self, account_id, order_id) -> dict:
        """미체결 지정가 주문 취소(공개 래퍼)."""
        return self._post("/api/Order/cancel", {"accountId": int(account_id), "orderId": order_id})

    def current_market_price(self, contract_id):
        """현재가 = 진행 중 1분봉 종가(POST /api/History/retrieveBars). 실패 시 None.
        지정가 미체결 후 '불리 이동' 판정용이라 정밀도보다 가용성이 중요."""
        from datetime import datetime, timedelta, timezone
        try:
            now = datetime.now(timezone.utc)
            d = self._post("/api/History/retrieveBars", {
                "contractId": contract_id, "live": False,
                "startTime": (now - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "unit": 2, "unitNumber": 1, "limit": 3, "includePartialBar": True,
            })
            bars = d.get("bars", d if isinstance(d, list) else [])
            bars = sorted(bars, key=lambda b: (b.get("t") or b.get("timestamp") or ""))
            return float(bars[-1].get("c")) if bars else None
        except Exception:
            return None

    def close_contract(self, account_id, contract_id) -> dict:
        """Market-close the whole position for one {account, contract} (POST /api/Position/
        closeContract). Used to flatten a just-entered position when its protective stop was
        rejected (so we never sit unprotected)."""
        return self._post("/api/Position/closeContract",
                          {"accountId": int(account_id), "contractId": contract_id})
