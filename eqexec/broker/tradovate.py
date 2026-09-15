"""Tradovate adapter — 주력 자기자본 브로커 (대표 2026-07-27 "Tradovate을 우리 주력으로").

Phase 2 (2026-07-28 심야): 인증·조회·청산에 더해 진입(placeoso 브래킷)·보호손절·잔고까지 —
ProjectX 어댑터와 동일한 호출 계약(place_entry/search_contracts/close_contract/account_balance)
이라 eqgui 선물 경로가 그대로 쓴다.

⚠️ VERIFY 표시 = Tradovate DEMO 계좌로 실검증 전. dry_run=True면 아무것도 안 보낸다.
   데모 검증 순서: healthcheck → search_contracts("MNQ") → account_balance →
   place_entry(dry_run=False, size=1) → flatten_all(dry_run=False).

Tradovate REST 요점:
  - base = https://{demo|live}.tradovateapi.com/v1  (config가 /v1 포함)
  - 주문은 contractId가 아니라 "symbol"(예: MNQZ6)로 넣는다. isAutomated=true 필수(CME 봇 규정).
  - 진입+손절은 POST /order/placeoso 한 방(브래킷) — 손절 없는 알몸 진입이 생기지 않는다
    (OSO가 거부되면 진입 자체가 없음 = ProjectX의 '진입 후 손절 거부' 케이스가 구조적으로 제거).
"""
from __future__ import annotations

import datetime as _dt
import time

import requests

from . import futures_cal as _cal
from .base import BrokerAdapter, FlattenResult, Position

_TIMEOUT = (10, 30)          # (connect, read) seconds — never hang forever
_TOKEN_TTL = 90 * 60         # Tradovate access token lifespan
_RENEW_MARGIN = 5 * 60       # re-auth this many seconds before expiry

# 심볼 루트별 틱 크기 폴백(계약 메타 조회 실패 시). VERIFY: /contract 메타로 대체 예정.
_TICK_FALLBACK = {"MNQ": 0.25, "NQ": 0.25, "MES": 0.25, "ES": 0.25,
                  "MGC": 0.1, "GC": 0.1, "MYM": 1.0, "M2K": 0.1}

# 월물 코드·활성월·롤 창은 futures_cal이 정본(2026-09-15: 종전 '가장 가까운 월물'이 금 시리얼월까지
# 집어 정본 손절가(12월물 계열)와 basis가 어긋나는 함정 - IBKR 9/14 감사와 같은 축).
_MONTH_CODES = _cal.MONTH_CODES


def _act(side) -> str:
    s = str(side).upper()
    if s in ("BUY", "LONG", "0"):
        return "Buy"
    if s in ("SELL", "SHORT", "1"):
        return "Sell"
    raise ValueError(f"unknown side {side!r}")


def _opp(action: str) -> str:
    return "Sell" if action == "Buy" else "Buy"


class TradovateBroker(BrokerAdapter):
    name = "tradovate"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.TradovateCfg
        self.base = cfg.base_url
        self._token: str | None = None
        self._token_at: float = 0.0
        self._contract_cache: dict[int, str] = {}     # id -> name
        self._contract_ids: dict[str, int] = {}       # name -> id
        self._acct_cache: list[dict] | None = None
        self._last_renew_error: str = ""             # 갱신 실패 사유(진단용, 비밀 아님)

    # ── auth ──────────────────────────────────────────────────────────────
    def authenticate(self) -> None:
        body = {
            "name": self.cfg.name,
            "password": self.cfg.password,
            "appId": self.cfg.app_id,
            "appVersion": self.cfg.app_version,
            "cid": self.cfg.cid,
            "sec": self.cfg.sec,
        }
        if self.cfg.device_id:
            body["deviceId"] = self.cfg.device_id
        r = requests.post(f"{self.base}/auth/accesstokenrequest", json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        # A pending MFA / captcha response has no accessToken — surface it clearly.
        tok = data.get("accessToken")
        if not tok:
            raise RuntimeError(f"Tradovate auth returned no accessToken: {data}")
        self._token, self._token_at = tok, time.time()

    def _renew(self) -> bool:
        """기존 토큰 연장(GET /auth/renewAccessToken, Bearer). 성공 시 True.
        공식 문서(2026-08-17 대조): accesstokenrequest는 호출마다 **새 세션**을 열고
        동시 세션은 2개 제한 - 셋째가 생기면 가장 오래된 세션이 강제 종료된다. 대표가
        Tradovate Trader에 로그인한 채 앱이 재인증을 반복하면 서로를 걷어차는 구조라,
        문서 권고대로 만료 전에는 갱신을 먼저 시도하고 실패할 때만 재인증한다."""
        if not self._token:
            return False
        try:
            # 공식 레퍼런스 경로는 소문자(/auth/renewaccesstoken) - 다른 엔드포인트와 같은 표기.
            r = requests.get(f"{self.base}/auth/renewaccesstoken",
                             headers=self._headers(), timeout=_TIMEOUT)
            r.raise_for_status()
            tok = (r.json() or {}).get("accessToken")
            if tok:
                self._token, self._token_at = tok, time.time()
                return True
            self._last_renew_error = f"no accessToken in renew response: {str(r.text)[:120]}"
        except Exception as e:
            self._last_renew_error = str(e)[:200]      # 삼키되 사유는 남긴다(진단 출력)
        return False

    def _ensure_token(self) -> None:
        if self._token is None or (time.time() - self._token_at) > (_TOKEN_TTL - _RENEW_MARGIN):
            if not self._renew():
                self.authenticate()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _get(self, path: str, **params):
        self._ensure_token()
        r = requests.get(f"{self.base}{path}", headers=self._headers(),
                         params=params or None, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        self._ensure_token()
        r = requests.post(f"{self.base}{path}", headers=self._headers(),
                          json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        # Tradovate는 HTTP 200 + failureReason/failureText로 거부를 알린다 — 예외로 승격.
        if isinstance(d, dict) and (d.get("failureReason") or d.get("failureText")):
            raise RuntimeError(f"Tradovate reject: {d.get('failureReason')} "
                               f"{d.get('failureText') or ''}".strip())
        return d

    # ── reads ─────────────────────────────────────────────────────────────
    def _accounts(self) -> list[dict]:
        accts = self._get("/account/list")
        want = {a.lower() for a in (self.cfg.accounts or [])}
        if want:
            accts = [a for a in accts
                     if str(a.get("name", "")).lower() in want or str(a.get("id")) in want]
        self._acct_cache = accts
        return accts

    def _acct_by_id(self, account_id) -> dict | None:
        accts = self._acct_cache or self._accounts()
        for a in accts:
            if str(a.get("id")) == str(account_id) or str(a.get("name")) == str(account_id):
                return a
        return None

    def closed_fills(self, start_ms: int) -> list[dict]:
        """실현손익 체결 목록(트랙레코드 푸시용). ProjectX·NT8 어댑터와 같은 계약.

        Tradovate 엔티티(공식 REST 레퍼런스 기준, 2026-09-15 대조 - ⚠️데모 실검증 전):
          FillPair       {id, positionId, buyFillId, sellFillId, qty, buyPrice, sellPrice, active, archived}
          Fill           {id, orderId, contractId, timestamp, tradeDate, action, qty, price, active, finallyPaired}
          CashBalanceLog {id, accountId, timestamp, tradeDate, currencyId, amount, realizedPnL,
                          cashChangeType, fillPairId, fillId, ...}  - TradePaired 행이 짝별 실현손익
        종전 코드는 FillPair에 accountId·realizedPnl·timestamp가 있다고 가정해 존재하지 않는 필드에서
        continue → **항상 빈 목록**('체결 0건'으로 성공처럼 보임). 이제 셋을 조인한다:
        짝(FillPair) → 두 체결(Fill: 계약·시각) → 현금로그(CashBalanceLog: 계좌·손익).
        청산 시각 = 두 체결 중 나중 것, 방향 = 나중 체결이 매도면 롱. 수수료는 같은 체결의
        Commission/Fee 행(fillId 일치)을 빼서 ProjectX(순손익)와 같은 기준으로 맞춘다 - 행 유형명이
        문서와 다르면 0(총손익)으로 남고 data 필드 'fees'가 0이라 대조 시 드러난다.

        실패는 삼키지 않는다 - 예외가 호출측까지 올라가 '체결 조회 실패'로 찍힌다(빈 목록과 구분).
        필드명이 문서와 다르면 KeyError가 그대로 올라온다(조용한 0건 금지).
        """
        from datetime import datetime, timezone

        def _ms(ts) -> int:
            return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                       .astimezone(timezone.utc).timestamp() * 1000)

        want = {int(a["id"]): str(a.get("name") or "") for a in self._accounts()}
        fills = {int(f["id"]): f for f in (self._get("/fill/list") or [])}
        pairs = self._get("/fillPair/list") or []
        logs = self._get("/cashBalanceLog/list") or []
        pnl_by_pair: dict[int, dict] = {}
        fee_by_fill: dict[int, float] = {}
        for lg in logs:
            kind = str(lg.get("cashChangeType") or "")
            if lg.get("fillPairId") and kind == "TradePaired":
                pnl_by_pair[int(lg["fillPairId"])] = lg
            elif lg.get("fillId") and ("commission" in kind.lower() or "fee" in kind.lower()):
                fee_by_fill[int(lg["fillId"])] = fee_by_fill.get(int(lg["fillId"]), 0.0) \
                    + abs(float(lg.get("amount") or 0.0))
        out = []
        for fp in pairs:
            lg = pnl_by_pair.get(int(fp.get("id") or 0))
            bf = fills.get(int(fp.get("buyFillId") or 0))
            sf = fills.get(int(fp.get("sellFillId") or 0))
            if not (lg and bf and sf):
                continue                                  # 아직 안 닫힌 짝 / 현금로그 미도착
            acct = int(lg.get("accountId") or 0)
            if acct not in want:
                continue
            close_f = sf if _ms(sf["timestamp"]) >= _ms(bf["timestamp"]) else bf
            ts_ms = _ms(close_f["timestamp"])
            if ts_ms < int(start_ms):
                continue
            fees = fee_by_fill.get(int(bf["id"]), 0.0) + fee_by_fill.get(int(sf["id"]), 0.0)
            out.append({
                "tid": str(fp["id"]),
                "ts_ms": ts_ms,
                "symbol": self._contract_symbol(close_f.get("contractId")),
                "pnl": float(lg.get("realizedPnL") or 0.0) - fees,
                "fees": fees,
                "direction": "LONG" if close_f is sf else "SHORT",    # 매도로 닫음 = 롱
                "acct": str(acct),
                "acct_name": want[acct],
            })
        return out

    def account_balance(self, acct):
        """계좌 현금 잔고 — POST /cashBalance/getcashbalancesnapshot {accountId}.
        VERIFY: 응답 필드(totalCashValue) 데모 확인. 실패 시 None(호출측 폴백 규약)."""
        a = self._acct_by_id(acct) if acct else ((self._acct_cache or self._accounts()) or [None])[0]
        if not a:
            return None
        try:
            d = self._post("/cashBalance/getcashbalancesnapshot", {"accountId": int(a["id"])})
            for k in ("totalCashValue", "netLiq", "amount", "cashBalance"):
                if isinstance(d, dict) and d.get(k) is not None:
                    return float(d[k])
        except Exception:
            return None
        return None

    def _contract_symbol(self, contract_id: int) -> str:
        if contract_id in self._contract_cache:
            return self._contract_cache[contract_id]
        try:  # VERIFY: /contract/item?id= shape
            item = self._get("/contract/item", id=contract_id)
            sym = item.get("name") or str(contract_id)
        except Exception:
            sym = str(contract_id)
        self._contract_cache[contract_id] = sym
        if sym != str(contract_id):
            self._contract_ids[sym] = int(contract_id)
        return sym

    def search_contracts(self, text: str, live: bool = False) -> list[dict]:
        """심볼 루트(예: 'MNQ')로 활성(프론트월) 계약 후보를 찾는다 - ProjectX·IBKR과 동일 계약:
        [{id, name, contractId, activeContract}] 반환, eqgui._resolve_contract가 첫 활성을 쓴다.

        **id는 계약명 문자열**(ibkr.py와 동일, 2026-09-15). eqgui가 이 id를 place_entry/position_qty/
        close_contract에 그대로 넘기고 list_open_positions().symbol과 집합으로 대조하므로(_run_futures_entry)
        둘이 같은 문자열이어야 한다. 종전엔 id=int·symbol=str라 잔여 포지션을 '다른 심볼'로 오판해 그 위에
        새 진입을 얹고, 손절 청산 감지(_check_stop_closed)가 살아 있는 포지션을 '사라짐'으로 보고했다.
        Tradovate 숫자 contractId는 'contractId'로 같이 준다(캐시·flatten용).

        프론트월 = futures_cal(활성월 표 + 롤 창): 지수 분기물·만기 8일 전, 금속 짝수월(10월 제외)·
        계약월 시작 5일 전. 규칙 모르는 루트는 거르지 않는다(종전 '가장 가까운 월물').
        GET /contract/suggest?t=MNQ&l=50 → 이름 규칙(루트+월코드+연도)으로 만기 정렬.
        suggest가 프론트월을 안 주면(목록 상한) 달력이 말하는 이름을 /contract/find로 직접 조회.
        VERIFY: suggest·find 응답 데모 확인."""
        root = str(text).upper().strip()
        now = time.gmtime()
        today = _dt.date(now.tm_year, now.tm_mon, now.tm_mday)
        cur_key = (now.tm_year % 100) * 12 + (now.tm_mon - 1)
        active = _cal.active_months(root)                 # "" = 규칙 모르는 루트 → 거르지 않음
        fm = _cal.front_month(root, today)                # (연, 월) | None
        fm_key = ((fm[0] % 100) * 12 + (fm[1] - 1)) if fm else None
        try:
            items = self._get("/contract/suggest", t=root, l=50) or []
        except Exception:
            items = []
        cands = []
        for it in items:
            name = str(it.get("name", ""))
            # 루트 정확 일치 + 월코드+한두자리 연도 (예: MNQZ5, MNQZ25)
            if not name.startswith(root):
                continue
            tail = name[len(root):]
            if not tail or tail[0] not in _MONTH_CODES or not tail[1:].isdigit():
                continue
            if active and tail[0] not in active:
                continue                                   # 시리얼(비활성) 월물 제외
            mon = _MONTH_CODES.index(tail[0])              # 0~11
            yr = int(tail[1:]) % 100
            if len(tail[1:]) == 1:                         # 한 자리 연도 → 현재 십년대 해석
                dec = (now.tm_year % 100) // 10 * 10
                yr = dec + yr
                if (yr * 12 + mon) < cur_key - 1:          # 과거면 다음 십년대 후보로 승격하되
                    _bumped = (yr + 10) * 12 + mon         # 18개월 내 미래일 때만 인정
                    if _bumped - cur_key <= 18:            # (예: 2029년 말의 H0 → 2030 OK,
                        yr += 10                           #  2026년의 U4 → 2034는 기각)
                    else:
                        continue
            key = yr * 12 + mon
            if key < cur_key or key - cur_key > 18:        # 만기 지남·18개월 초과 원월물 제외
                continue
            if fm_key is not None and key < fm_key:
                continue                                   # 롤 창 안(만기 8일/계약월 5일 전) → 다음 활성월
            cands.append({"id": name, "name": name, "contractId": it.get("id"),
                          "activeContract": False, "_key": key})
        cands.sort(key=lambda c: c["_key"])
        if not cands and fm is not None:
            # suggest가 프론트월을 안 줬다(목록 상한·검색 누락) → 달력이 말하는 이름을 직접 조회
            exp = f"{root}{_cal.month_code(fm[1])}{fm[0] % 10}"
            cid = self._resolve_id(exp)
            if cid is not None:
                cands.append({"id": exp, "name": exp, "contractId": cid, "activeContract": False,
                              "_key": (fm[0] % 100) * 12 + (fm[1] - 1)})
        if cands:
            cands[0]["activeContract"] = True              # 달력 프론트월(규칙 없는 루트=가장 가까운 월물)
            for c in cands:
                if c.get("contractId") is not None:
                    self._contract_cache[int(c["contractId"])] = c["name"]
                    self._contract_ids[c["name"]] = int(c["contractId"])
        return cands

    def _resolve_id(self, contract) -> int | None:
        """계약 인자(계약ID int 또는 심볼명 str)를 Tradovate contractId로."""
        s = str(contract)
        if s.isdigit():
            return int(s)
        if s in self._contract_ids:
            return self._contract_ids[s]
        try:  # 정확한 월물명 조회 (예: MNQZ5)
            item = self._get("/contract/find", name=s)
            if isinstance(item, dict) and item.get("id"):
                self._contract_ids[s] = int(item["id"])
                self._contract_cache[int(item["id"])] = s
                return int(item["id"])
        except Exception:
            pass
        return None

    def _tick_size(self, contract) -> float | None:
        sym = self._contract_symbol(int(contract)) if str(contract).isdigit() else str(contract)
        for root, tick in _TICK_FALLBACK.items():
            if sym.upper().startswith(root):
                return tick
        return None

    def _align(self, contract, price) -> float:
        """가격을 그 계약 틱에 스냅 — ProjectX 틱정렬 사고(GC 손절 거부→진입취소)의 재발 방지."""
        t = self._tick_size(contract)
        if not t or price is None:
            return price
        return round(round(float(price) / t) * t, 10)

    def list_open_positions(self) -> list[Position]:
        accts = {a["id"]: a.get("name", str(a["id"])) for a in self._accounts()}
        positions = self._get("/position/list")          # VERIFY: returns all accessible positions
        out: list[Position] = []
        for p in positions:
            acct_id = p.get("accountId")
            if acct_id not in accts:
                continue
            net = int(p.get("netPos", 0) or 0)
            if net == 0:
                continue
            out.append(Position(
                account_id=str(acct_id),
                account_name=accts[acct_id],
                symbol=self._contract_symbol(p.get("contractId")),
                net_qty=net,
                raw={**p, "_accountId": acct_id},        # projectx/ibkr와 같은 키(eqgui 잔여 청산)
            ))
        return out

    def position_qty(self, account_id, contract):
        """이 {계좌, 계약}의 현재 포지션 수량(절대값). 미보유 0, 조회실패 None."""
        try:
            cid = self._resolve_id(contract)
            for p in self._get("/position/list"):
                if str(p.get("accountId")) == str(account_id) and \
                        (cid is None or int(p.get("contractId", -1)) == cid):
                    return abs(int(p.get("netPos", 0) or 0))
        except Exception:
            return None
        return 0

    # ── act: 진입 + 보호손절 (placeoso 브래킷 = 원자적) ──────────────────────
    def place_entry(self, account_id, contract_id, side, size: int, *,
                    order_type: int = 2, limit_price=None, stop_price=None,
                    stop_loss_ticks=None, stop_loss_price=None, take_profit_ticks=None,
                    custom_tag=None, dry_run: bool = True):
        """ProjectX와 동일 호출 계약. order_type: 2=Market(기본), 1=Limit.
        stop_loss_price가 있으면 POST /order/placeoso 브래킷으로 진입+손절을 한 방에 —
        브래킷이 거부되면 진입 자체가 없다(알몸 포지션 불가). VERIFY: placeoso 응답 shape.
        반환: dry_run={dry_run, would_place, would_place_stop} /
              live 성공={entry, stop, stop_price} / 실패는 예외."""
        a = self._acct_by_id(account_id)
        if not a:
            raise RuntimeError(f"Tradovate account {account_id!r} not found")
        sym = (self._contract_symbol(int(contract_id)) if str(contract_id).isdigit()
               else str(contract_id))
        action = _act(side)
        body = {
            "accountSpec": a.get("name"),
            "accountId": int(a["id"]),
            "action": action,
            "symbol": sym,
            "orderQty": int(size),
            "orderType": ("Limit" if int(order_type) == 1 else "Market"),
            "isAutomated": True,                      # CME 봇 규정 — 항상 명시
        }
        if limit_price is not None:
            body["price"] = self._align(sym, limit_price)
        sl_px = self._align(sym, stop_loss_price) if stop_loss_price is not None else None
        bracket = ({"action": _opp(action), "orderType": "Stop", "stopPrice": sl_px}
                   if sl_px is not None else None)
        if dry_run:
            return {"dry_run": True, "would_place": body, "would_place_stop": bracket}
        if bracket is not None:
            resp = self._post("/order/placeoso", {**body, "bracket1": bracket})
            oso = resp.get("oso1Id") if isinstance(resp, dict) else None
            if oso:
                return {"entry": resp, "stop": {"oso": oso}, "stop_price": sl_px}
            # 응답에 브래킷 id가 없다 = 미검증 모양. 종전엔 응답 전체를 stop에 넣어 항상 '🛡 거치'로
            # 찍혔다. placeoso는 원자적(브래킷 없이는 진입도 없음)이라 스탑이 있을 가능성이 높으니
            # 두 번째 스탑을 자동으로 얹지 않고(스탑 둘 = 첫 스탑 체결 후 남은 스탑이 역포지션을
            # 연다) '확인 안 됨' 경고 경로로 보낸다 - stop·stop_error 둘 다 없이 돌려준다.
            return {"entry": resp, "stop_price": sl_px,
                    "stop_unconfirmed": f"placeoso returned no oso1Id: {str(resp)[:160]}"}
        return {"entry": self._post("/order/placeorder", body)}

    def place_protective_stop(self, account_id, contract, entry_side, size: int,
                              stop_price, *, custom_tag=None) -> dict:
        """단독 보호 Stop(진입 반대 방향, 절대가) — 브래킷 밖 재거치/재시도용.
        반환 계약은 ProjectX와 동일: {stop, stop_price} | {stop_error, stop_rejected}."""
        a = self._acct_by_id(account_id)
        sym = (self._contract_symbol(int(contract)) if str(contract).isdigit()
               else str(contract))
        px = self._align(sym, stop_price)
        body = {"accountSpec": (a or {}).get("name"), "accountId": int((a or {}).get("id", 0)),
                "action": _opp(_act(entry_side)), "symbol": sym, "orderQty": int(size),
                "orderType": "Stop", "stopPrice": px, "isAutomated": True}
        try:
            return {"stop": self._post("/order/placeorder", body), "stop_price": px}
        except requests.RequestException as e:           # timeout / connection / 5xx → transient
            return {"stop_error": str(e), "stop_rejected": False}
        except Exception as e:                            # failureReason → broker reject
            return {"stop_error": str(e), "stop_rejected": True}

    def place_limit_entry(self, account_id, contract, side, size: int, price,
                          *, stop_loss_price=None, custom_tag=None, dry_run: bool = False):
        """지정가 진입(체결 확인은 호출측 position_qty 폴링) — 선물 지정가 정책용 인터페이스 패리티."""
        if dry_run:
            return {"dry_run": True, "would_place": {"orderType": "Limit",
                                                     "price": self._align(contract, price),
                                                     "action": _act(side), "orderQty": int(size)}}
        try:
            r = self.place_entry(account_id=account_id, contract_id=contract, side=side,
                                 size=size, order_type=1, limit_price=price,
                                 custom_tag=custom_tag, dry_run=False)
            oid = ((r or {}).get("entry") or {}).get("orderId")
            return {"order_id": oid, "error": None}
        except Exception as e:
            return {"order_id": None, "error": str(e)}

    def cancel_order(self, account_id, order_id) -> dict:
        return self._post("/order/cancelorder", {"orderId": int(order_id)})

    def current_market_price(self, contract):
        """시세는 별도 MD 웹소켓 구독이 필요해 REST로 없음 — None 반환(호출측 폴백 규약).
        선물 진입은 시장가 정책이라 실사용 없음."""
        return None

    def close_contract(self, account_id, contract) -> dict:
        """한 {계좌, 계약} 포지션 전체 시장가 청산 — POST /order/liquidateposition.
        contract는 계약ID(int) 또는 심볼명(str) 둘 다 받는다. VERIFY: 엔드포인트 단수형."""
        cid = self._resolve_id(contract)
        if cid is None:
            raise RuntimeError(f"cannot resolve Tradovate contract {contract!r}")
        return self._post("/order/liquidateposition",
                          {"accountId": int(account_id), "contractId": cid, "admin": False})

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        plan = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        for pos in plan:
            try:
                self._post("/order/liquidateposition", {
                    "accountId": int(pos.account_id),
                    "contractId": int(pos.raw.get("contractId")),
                    "admin": False,
                })
            except Exception as e:
                res.errors.append(f"{pos.account_name}/{pos.symbol}: {e}")
        # Confirm-after-act: re-read; anything still open is an error to alert on.
        try:
            still = [p for p in self.list_open_positions()]
            res.closed = [p for p in plan if not any(
                s.account_id == p.account_id and s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.account_name}/{p.symbol} "
                                  f"net={p.net_qty}")
        except Exception as e:
            res.errors.append(f"post-flatten position re-check failed: {e}")
        return res

    def healthcheck(self) -> bool:
        self.authenticate()
        self._accounts()
        return True

    def diagnostics(self) -> dict:
        """연결 테스트가 로그에 찍는 비밀 없는 사실(2026-09-15, 데모 검증용). 계좌 이름·프론트월
        후보·세 엔티티의 필드명만 - 값·키·토큰은 절대 안 담는다. 실패한 항목은 사유 문자열."""
        d: dict = {}
        try:
            d["accounts"] = [str(a.get("name") or a.get("id")) for a in self._accounts()]
        except Exception as e:
            d["accounts"] = f"ERR {str(e)[:80]}"
        for root in ("MGC", "MNQ"):
            try:
                cs = self.search_contracts(root)
                d[f"front_{root}"] = [c["name"] + ("*" if c.get("activeContract") else "")
                                      for c in cs[:4]]
            except Exception as e:
                d[f"front_{root}"] = f"ERR {str(e)[:80]}"
        for path in ("/fillPair/list", "/fill/list", "/cashBalanceLog/list", "/position/list"):
            try:
                rows = self._get(path) or []
                d[path] = {"n": len(rows),
                           "keys": sorted(rows[0].keys())[:20] if rows and isinstance(rows[0], dict) else []}
            except Exception as e:
                d[path] = f"ERR {str(e)[:80]}"
        if self._last_renew_error:
            d["renew_error"] = self._last_renew_error
        return d
