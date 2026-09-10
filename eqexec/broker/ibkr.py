"""Interactive Brokers adapter - 자기자본 계좌(프롭 제약 없음). 대표 2026-09-10 "IBKR 다 해".

회원 기기의 **로컬** TWS 또는 IB Gateway에 ib_insync로 붙는다(원격 API 아님 - 약관 §14.5 "같은
컴퓨터"). ProjectX·Tradovate와 **같은 호출 계약**이라 eqgui의 선물 경로(_run_futures_entry,
_exec_entry_limit_fut, _handle_stop_failure, 체결 보고)가 그대로 쓴다:
  _accounts / search_contracts / account_balance / list_open_positions / position_qty /
  place_entry / place_protective_stop / place_limit_entry / cancel_order / current_market_price /
  close_contract / flatten_all / closed_fills / entry_info / healthcheck

설계 결정
  - **계약 ID = IB localSymbol**(예: MNQZ5). list_open_positions의 symbol도 같은 문자열이라 eqgui가
    '내 계약' 잔여를 대조하는 `p.symbol in {계약ID}`가 맞아 떨어진다(Tradovate는 id=int·symbol=str로
    어긋나 있음 - 주말 수리 항목).
  - **진입+보호손절 = IB 부모/자식 주문**(parentId, 부모 transmit=False → 자식 transmit=True).
    부모가 체결돼야 자식 스탑이 활성화되고, 부모가 거절·취소되면 자식도 소멸 → 알몸 진입이 구조적으로
    없다(Tradovate OSO와 같은 성질, ProjectX의 '진입 후 스탑 거부' 케이스 제거). 자식만 거절되면
    {stop_error, stop_rejected=True}를 돌려 호출측이 즉시 청산한다.
  - **프론트월 선택**: 지수(MNQ·NQ·MES·ES…)는 분기물만, 만기 8일 전부터 다음 분기물.
    금속(MGC·GC)은 짝수월만, **계약월 시작 5일 전부터 다음 활성월**(선물 실물인수 계약은 FND 전에
    IB가 강제 청산한다 - 12월에 Z를 잡으면 인도 기간이라 위험. Tradovate 어댑터의 '가장 가까운 월물'
    규칙은 이 함정이 있음).
  - **스레드**: ib_insync는 asyncio 루프가 필요한데 앱은 브로커 작업을 워커 스레드에서 돌린다.
    연결 때 만든 루프를 기억해 매 호출 스레드에 set_event_loop하고, 전 호출을 RLock으로 직렬화한다.
  - **시세**: 구독이 없으면 지연 시세(reqMarketDataType 3)로 폴백. 주문 자체는 시세 구독 없이 나간다.
    ⚠️ 페이퍼 계좌는 시세 없이는 시장가가 안 체결될 수 있다(TWS 설정에서 지연 시세 허용).
  - **체결 기록(closed_fills)**: TWS 현재 세션의 executions + CommissionReport.realizedPNL. TWS를
    껐다 켰으면 그 전 체결은 안 나온다(Flex 조회는 범위 밖) - 같은 날 세션 안에서의 푸시용.

UNTESTED 표시(2026-09-10): ib_insync API 문서 기준 작성, 페이퍼 계좌 실검증은 대표 오늘 밤.
dry_run=True면 아무 주문도 보내지 않는다.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .base import BrokerAdapter, FlattenResult, Position

_ACK_WAIT_S = 2.5            # 주문 접수/거절 판정 대기(TWS는 거절을 비동기 에러로 준다)
_CONNECT_TIMEOUT_S = 15
_ROLL_DAYS_INDEX = 8         # 지수 분기물: 만기(3번째 금요일) 8일 전부터 다음 분기물(거래량 이전 시점)
_ROLL_DAYS_METAL = 5         # 금속: 계약월 시작 5일 전부터 다음 활성월(FND = 직전 월 마지막 영업일)
_MONTH_CODES = "FGHJKMNQUVXZ"
_ACTIVE_MONTHS = {"MNQ": "HMUZ", "NQ": "HMUZ", "MES": "HMUZ", "ES": "HMUZ",
                  "M2K": "HMUZ", "RTY": "HMUZ", "MYM": "HMUZ", "YM": "HMUZ",
                  "MGC": "GJMQVZ", "GC": "GJMQVZ"}
_EXCHANGE = {"MNQ": "CME", "NQ": "CME", "MES": "CME", "ES": "CME", "M2K": "CME", "RTY": "CME",
             "MGC": "COMEX", "GC": "COMEX", "SIL": "COMEX", "SI": "COMEX",
             "MYM": "CBOT", "YM": "CBOT"}
_TICK_FALLBACK = {"MNQ": 0.25, "NQ": 0.25, "MES": 0.25, "ES": 0.25,
                  "MGC": 0.1, "GC": 0.1, "MYM": 1.0, "M2K": 0.1}
_REJECT_STATES = ("Cancelled", "ApiCancelled", "Inactive")
_PNL_UNSET = 1e300           # IB CommissionReport.realizedPNL 미설정 센티널(1.7976931348623157e+308)


def _px(v):
    """IB UNSET_DOUBLE(1.797e308) → None - 시장가 주문의 lmtPrice처럼 '없는 값'을 사람이 읽게."""
    try:
        return None if v is None or abs(float(v)) >= _PNL_UNSET else float(v)
    except (TypeError, ValueError):
        return None


def _ib_classes():
    """Lazy import so the dependency is only needed when IBKR is actually used."""
    try:
        import ib_insync as ibi
    except ImportError:  # ib_async = maintained drop-in fork
        import ib_async as ibi
    return ibi


def _act(side) -> str:
    s = str(side).upper()
    if s in ("BUY", "LONG", "0", "BID"):
        return "BUY"
    if s in ("SELL", "SHORT", "1", "ASK"):
        return "SELL"
    raise ValueError(f"bad side {side!r} (use BUY/LONG or SELL/SHORT)")


def _opp(action: str) -> str:
    return "SELL" if action == "BUY" else "BUY"


def _root(sym: str) -> str:
    """'MNQZ5' → 'MNQ', 'MGCG6' → 'MGC' (문자 접두 = 루트)."""
    s = str(sym).upper().strip()
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    # 마지막 글자가 월코드이고 뒤가 숫자면 월코드는 루트가 아니다(MNQZ5 → MNQ).
    if i >= 2 and i < len(s) and s[i:].isdigit() and s[i - 1] in _MONTH_CODES:
        return s[:i - 1]
    return s[:i] if i else s


class IBKRBroker(BrokerAdapter):
    name = "ibkr"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.IBKRCfg
        self._ib = None
        self._loop = None
        self._lock = threading.RLock()
        self._contracts: dict[str, object] = {}     # localSymbol -> qualified Contract
        self._ticks: dict[str, float] = {}          # localSymbol -> minTick
        self._mults: dict[str, float] = {}          # localSymbol -> multiplier
        self._acct_cache: list[dict] | None = None

    # ── connection ─────────────────────────────────────────────────────────────
    def _bind_loop(self) -> None:
        """이 스레드에 연결 때 쓴 루프를 묶는다(ib_insync 동기 호출은 현재 스레드 루프를 돈다)."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        try:
            cur = asyncio.get_event_loop()
        except RuntimeError:
            cur = None
        if cur is not self._loop:
            asyncio.set_event_loop(self._loop)

    def _connect(self):
        with self._lock:
            self._bind_loop()
            if self._ib is not None and self._ib.isConnected():
                return self._ib
            ibi = _ib_classes()
            # TWS/Gateway must already be running on the user's machine (API enabled in settings).
            # 로컬호스트 강제(2026-08-27 일치성 P2-39): 약관 §14.5가 "같은 컴퓨터의 TWS/Gateway"를
            # 전제하는데 host 칸이 임의 원격 주소를 받았다. NT8의 127.0.0.1 고정과 동일 원칙.
            host = str(getattr(self.cfg, "host", "") or "127.0.0.1").strip()
            if host not in ("127.0.0.1", "localhost", "::1"):
                raise RuntimeError(
                    "IBKR host must be local (127.0.0.1/localhost/::1) - TWS or IB Gateway "
                    "runs on this computer per the supported setup.")
            ib = ibi.IB()
            ib.connect(host, int(self.cfg.port), clientId=int(getattr(self.cfg, "client_id", 11) or 11),
                       timeout=_CONNECT_TIMEOUT_S, readonly=False)
            try:
                ib.reqMarketDataType(3)      # 구독 없으면 지연 시세(주문과 무관, 현재가 폴백용)
            except Exception:
                pass
            self._ib = ib
            self._acct_cache = None
            return ib

    def authenticate(self) -> None:
        self._connect()

    def healthcheck(self) -> bool:
        with self._lock:
            ib = self._connect()
            return bool(ib.isConnected() and self._accounts())

    def entry_info(self) -> list:
        """연결 테스트 로그에 찍는 정보 줄(eqgui가 AttributeError 허용 - 있으면 찍는다)."""
        with self._lock:
            ib = self._connect()
            try:
                ver = ib.client.serverVersion()
            except Exception:
                ver = "?"
            accts = ", ".join(a["id"] for a in self._accounts()) or "(none)"
            return [f"IBKR {self.cfg.host}:{self.cfg.port} clientId {getattr(self.cfg, 'client_id', 11)} "
                    f"· TWS/Gateway server v{ver} · accounts: {accts}",
                    "paper = DU*, live = U* · market data falls back to delayed when unsubscribed"]

    # ── accounts ───────────────────────────────────────────────────────────────
    def _accounts(self) -> list[dict]:
        """[{id, name}] - ProjectX/Tradovate와 같은 키. cfg.accounts가 있으면 그 계좌만."""
        with self._lock:
            ib = self._connect()
            ids = [str(a) for a in (ib.managedAccounts() or []) if a]
            want = {str(a).strip().upper() for a in (self.cfg.accounts or []) if str(a).strip()}
            if want:
                ids = [a for a in ids if a.upper() in want]
            out = [{"id": a, "name": a} for a in ids]
            self._acct_cache = out
            return out

    def _acct_id(self, account_id) -> str:
        accts = self._acct_cache or self._accounts()
        for a in accts:
            if str(a["id"]).upper() == str(account_id).upper():
                return a["id"]
        if account_id:
            return str(account_id)
        return accts[0]["id"] if accts else ""

    def account_balance(self, acct):
        """순자산(NetLiquidation) - 없으면 TotalCashValue. 실패 시 None(호출측 폴백 규약)."""
        with self._lock:
            try:
                ib = self._connect()
                aid = self._acct_id(acct)
                vals = ib.accountSummary(aid) if aid else ib.accountSummary()
                best = {}
                for v in vals or []:
                    if aid and str(v.account) != aid:
                        continue
                    if v.tag in ("NetLiquidation", "TotalCashValue") and v.currency in ("USD", "BASE", ""):
                        try:
                            best[v.tag] = float(v.value)
                        except (TypeError, ValueError):
                            pass
                for k in ("NetLiquidation", "TotalCashValue"):
                    if k in best:
                        return best[k]
            except Exception:
                return None
            return None

    # ── contracts ──────────────────────────────────────────────────────────────
    @staticmethod
    def _front_ok(root: str, month_yyyymm: str, last_trade: str, today: date) -> bool:
        """이 월물이 '지금 거래할 프론트월 후보'인가(활성월 + 롤 규칙)."""
        try:
            y, m = int(month_yyyymm[:4]), int(month_yyyymm[4:6])
        except (TypeError, ValueError):
            return False
        code = _MONTH_CODES[m - 1]
        active = _ACTIVE_MONTHS.get(root)
        if active and code not in active:
            return False                                    # 시리얼(비활성) 월물 제외
        if root in ("MGC", "GC", "SIL", "SI"):
            month_start = date(y, m, 1)
            return (month_start - today).days >= _ROLL_DAYS_METAL
        try:
            lt = datetime.strptime(str(last_trade)[:8], "%Y%m%d").date()
        except (TypeError, ValueError):
            lt = date(y, m, 28)
        return (lt - today).days >= _ROLL_DAYS_INDEX

    def search_contracts(self, text: str, live: bool = False) -> list[dict]:
        """심볼 루트(예: 'MNQ')로 월물 후보를 찾는다 - ProjectX/Tradovate와 같은 반환 계약
        [{id, name, activeContract}], eqgui._resolve_contract가 activeContract 첫 항목의 id를 쓴다.
        id = name = IB localSymbol(예: MNQZ5). 만기·활성월·롤 규칙은 _front_ok."""
        root = _root(text)
        ibi = _ib_classes()
        with self._lock:
            ib = self._connect()
            exch = _EXCHANGE.get(root, "CME")
            try:
                cds = ib.reqContractDetails(ibi.Future(symbol=root, exchange=exch, currency="USD")) or []
            except Exception:
                cds = []
            today = datetime.now(timezone.utc).date()
            cands = []
            for cd in cds:
                c = cd.contract
                if str(getattr(c, "tradingClass", "") or c.symbol).upper() not in (root,):
                    if str(c.symbol).upper() != root:
                        continue
                mon = str(getattr(cd, "contractMonth", "") or "")[:6]
                lt = str(c.lastTradeDateOrContractMonth or "")
                if len(mon) < 6 and len(lt) >= 6:
                    mon = lt[:6]
                lsym = str(c.localSymbol or "")
                if not lsym or not mon:
                    continue
                self._contracts[lsym] = c
                try:
                    if cd.minTick:
                        self._ticks[lsym] = float(cd.minTick)
                except (TypeError, ValueError):
                    pass
                try:
                    self._mults[lsym] = float(c.multiplier or 0) or self._mults.get(lsym, 0.0)
                except (TypeError, ValueError):
                    pass
                if not self._front_ok(root, mon, lt, today):
                    continue
                cands.append({"id": lsym, "name": lsym, "activeContract": False,
                              "conId": int(c.conId or 0), "expiry": lt, "month": mon, "_key": mon})
            cands.sort(key=lambda d: d["_key"])
            for d in cands:
                d.pop("_key", None)
            if cands:
                cands[0]["activeContract"] = True
            return cands

    def _contract(self, contract):
        """계약 인자(localSymbol str 또는 conId int)를 qualified Contract로."""
        s = str(contract).strip()
        if s in self._contracts:
            return self._contracts[s]
        ibi = _ib_classes()
        with self._lock:
            ib = self._connect()
            if s.isdigit():
                c = ibi.Contract(conId=int(s))
                ib.qualifyContracts(c)
                if c.localSymbol:
                    self._contracts[str(c.localSymbol)] = c
                    self._contracts[s] = c
                    return c
                raise RuntimeError(f"IBKR: cannot qualify conId {s}")
            root = _root(s)
            self.search_contracts(root)          # 루트 월물 전부 캐시(프론트 여부 무관)
            if s in self._contracts:
                return self._contracts[s]
            raise RuntimeError(f"IBKR: unknown contract {contract!r} (root {root})")

    def _tick(self, contract) -> float | None:
        s = str(contract)
        if s in self._ticks:
            return self._ticks[s]
        for r, t in _TICK_FALLBACK.items():
            if _root(s) == r:
                return t
        return None

    def _align(self, contract, price):
        """가격을 그 계약 틱에 스냅 - ProjectX 틱정렬 사고(GC 손절 거부→진입취소) 재발 방지."""
        t = self._tick(contract)
        if not t or price is None:
            return price
        return round(round(float(price) / t) * t, 10)

    # ── positions ──────────────────────────────────────────────────────────────
    def _positions(self, account_id=None):
        """포지션을 **서버에서 다시 받아**(reqPositions는 동기 - 루프를 돌려 positionEnd까지) 돌려준다.
        ib.positions()만 읽으면 루프가 안 돌아 stale할 수 있다(앱은 호출 사이에 루프를 돌리지 않음)."""
        ib = self._connect()
        try:
            ib.reqPositions()
        except Exception:
            pass
        poss = ib.positions(str(account_id)) if account_id else ib.positions()
        return [p for p in poss if int(p.position or 0) != 0]

    def list_open_positions(self) -> list[Position]:
        with self._lock:
            want = {a["id"].upper() for a in self._accounts()}
            out: list[Position] = []
            for p in self._positions():
                if want and str(p.account).upper() not in want:
                    continue
                c = p.contract
                lsym = str(getattr(c, "localSymbol", "") or getattr(c, "symbol", ""))
                qty = int(p.position)
                try:
                    mult = float(c.multiplier or 0) or self._mults.get(lsym, 0.0)
                except (TypeError, ValueError):
                    mult = 0.0
                avg = None
                try:                                   # IB avgCost = 계약당 비용(승수 포함) → 가격으로
                    avg = float(p.avgCost) / mult if (p.avgCost and mult) else None
                except (TypeError, ValueError, ZeroDivisionError):
                    avg = None
                if lsym and lsym not in self._contracts:
                    self._contracts[lsym] = c
                out.append(Position(
                    account_id=str(p.account), account_name=str(p.account), symbol=lsym, net_qty=qty,
                    raw={"contract": c, "account": p.account, "_accountId": p.account,
                         "conId": int(getattr(c, "conId", 0) or 0), "avgPrice": avg,
                         "avgCost": p.avgCost, "multiplier": mult},
                ))
            return out

    def position_qty(self, account_id, contract):
        """이 {계좌, 계약}의 현재 포지션 수량(절대값). 미보유 0, 조회 실패 None."""
        with self._lock:
            try:
                aid = self._acct_id(account_id)
                c = self._contract(contract)
                for p in self._positions(aid):
                    pc = p.contract
                    if int(getattr(pc, "conId", 0) or 0) == int(c.conId or -1) or \
                            str(getattr(pc, "localSymbol", "")) == str(c.localSymbol):
                        return abs(int(p.position or 0))
                return 0
            except Exception:
                return None

    # ── orders ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _odict(trade) -> dict:
        o, st = trade.order, trade.orderStatus
        return {"orderId": int(o.orderId or 0), "permId": int(o.permId or 0), "status": str(st.status),
                "orderRef": str(o.orderRef or ""), "action": o.action, "qty": o.totalQuantity,
                "type": o.orderType, "lmtPrice": _px(getattr(o, "lmtPrice", None)),
                "auxPrice": _px(getattr(o, "auxPrice", None))}

    def _ack(self, ib, trade, wait_s: float = _ACK_WAIT_S) -> str | None:
        """주문 접수 판정. 거절·취소·Inactive면 사유 문자열, 정상(PreSubmitted/Submitted/Filled/
        아직 PendingSubmit)이면 None. TWS는 거절을 비동기 error로 주므로 잠깐 루프를 돌려 기다린다."""
        t0 = time.time()
        while time.time() - t0 < wait_s:
            st = str(trade.orderStatus.status)
            if st in _REJECT_STATES:
                break
            if st in ("PreSubmitted", "Submitted", "Filled"):
                return None
            ib.sleep(0.25)
        st = str(trade.orderStatus.status)
        if st in _REJECT_STATES:
            msg = ""
            try:
                for e in reversed(trade.log or []):
                    if getattr(e, "message", ""):
                        msg = str(e.message)
                        break
            except Exception:
                pass
            return f"{st}: {msg}" if msg else st
        return None

    def _mk(self, ibi, kind: str, action: str, size: int, *, account, ref, tif, price=None):
        if kind == "MKT":
            o = ibi.MarketOrder(action, int(size))
        elif kind == "LMT":
            o = ibi.LimitOrder(action, int(size), float(price))
        else:
            o = ibi.StopOrder(action, int(size), float(price))
        o.account = str(account or "")
        o.orderRef = str(ref or "")[:64]
        o.tif = tif
        o.outsideRth = True
        return o

    def place_entry(self, account_id, contract_id, side, size: int, *,
                    order_type: int = 2, limit_price=None, stop_price=None,
                    stop_loss_ticks=None, stop_loss_price=None, take_profit_ticks=None,
                    custom_tag=None, dry_run: bool = True):
        """ProjectX와 동일 호출 계약. order_type: 2=Market(기본), 1=Limit.
        stop_loss_price가 있으면 부모(진입)+자식(보호 Stop, parentId) 묶음 - 부모 체결 시에만
        자식이 살고 부모 거절 시 자식도 없다(알몸 진입 불가). 자식만 거절되면
        {entry, stop_error, stop_rejected=True}를 돌려 호출측(_handle_stop_failure)이 즉시 청산.
        반환: dry_run={dry_run, would_place, would_place_stop} /
              live={entry, order_id[, stop, stop_price | stop_error, stop_rejected]} / 거절은 예외."""
        ibi = _ib_classes()
        with self._lock:
            ib = self._connect()
            aid = self._acct_id(account_id)
            c = self._contract(contract_id)
            lsym = str(c.localSymbol or contract_id)
            action = _act(side)
            tag = custom_tag or f"EQ-AP-{int(time.time() * 1000)}"
            if int(order_type) == 1:
                if limit_price is None:
                    raise ValueError("limit entry needs limit_price")
                parent = self._mk(ibi, "LMT", action, size, account=aid, ref=tag, tif="DAY",
                                  price=self._align(lsym, limit_price))
            else:
                parent = self._mk(ibi, "MKT", action, size, account=aid, ref=tag, tif="DAY")
            sl_px = self._align(lsym, stop_loss_price) if stop_loss_price is not None else None
            child = None
            if sl_px is not None:
                child = self._mk(ibi, "STP", _opp(action), size, account=aid, ref=f"{tag}-SL",
                                 tif="GTC", price=sl_px)
            if dry_run:
                def _d(o):
                    return {"action": o.action, "qty": o.totalQuantity, "type": o.orderType,
                            "lmtPrice": _px(getattr(o, "lmtPrice", None)), "auxPrice": _px(getattr(o, "auxPrice", None)),
                            "tif": o.tif, "account": o.account, "orderRef": o.orderRef, "symbol": lsym}
                return {"dry_run": True, "would_place": _d(parent),
                        "would_place_stop": (_d(child) if child is not None else None)}
            if child is None:
                t1 = ib.placeOrder(c, parent)
                rej = self._ack(ib, t1)
                if rej:
                    raise RuntimeError(f"IBKR entry rejected ({lsym}): {rej}")
                return {"entry": self._odict(t1), "order_id": int(t1.order.orderId or 0)}
            # 부모/자식 묶음: 부모는 transmit=False로 대기, 자식(transmit=True)이 붙으면 함께 전송.
            parent.orderId = ib.client.getReqId()
            parent.transmit = False
            child.orderId = ib.client.getReqId()
            child.parentId = parent.orderId
            child.transmit = True
            t1 = ib.placeOrder(c, parent)
            t2 = ib.placeOrder(c, child)
            rej = self._ack(ib, t1)
            if rej:
                # 부모 거절 = 진입 없음(자식도 소멸). 혹시 남은 자식은 정리.
                try:
                    if str(t2.orderStatus.status) not in _REJECT_STATES:
                        ib.cancelOrder(child)
                except Exception:
                    pass
                raise RuntimeError(f"IBKR entry rejected ({lsym}): {rej}")
            rej2 = self._ack(ib, t2, wait_s=1.5)
            if rej2:
                return {"entry": self._odict(t1), "order_id": int(t1.order.orderId or 0),
                        "stop_error": f"protective stop rejected: {rej2}", "stop_rejected": True}
            return {"entry": self._odict(t1), "order_id": int(t1.order.orderId or 0),
                    "stop": self._odict(t2), "stop_price": sl_px}

    def place_protective_stop(self, account_id, contract, entry_side, size: int,
                              stop_price, *, custom_tag=None) -> dict:
        """단독 보호 Stop(진입 반대 방향, 절대가) - 지정가 체결 확인 뒤·재시도용(포지션이 있을 때만 호출됨).
        반환 계약은 ProjectX와 동일: {stop, stop_price} | {stop_error, stop_rejected}."""
        ibi = _ib_classes()
        with self._lock:
            try:
                ib = self._connect()
                aid = self._acct_id(account_id)
                c = self._contract(contract)
                lsym = str(c.localSymbol or contract)
                px = self._align(lsym, stop_price)
                o = self._mk(ibi, "STP", _opp(_act(entry_side)), size, account=aid,
                             ref=f"{custom_tag or 'EQ-AP'}-SL", tif="GTC", price=px)
                t = ib.placeOrder(c, o)
                rej = self._ack(ib, t)
                if rej:
                    return {"stop_error": rej, "stop_rejected": True}
                return {"stop": self._odict(t), "stop_price": px}
            except (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError) as e:
                return {"stop_error": str(e), "stop_rejected": False}        # transient → retry
            except Exception as e:
                return {"stop_error": str(e), "stop_rejected": True}

    def place_limit_entry(self, account_id, contract, side, size: int, price,
                          *, stop_loss_price=None, custom_tag=None, dry_run: bool = False):
        """지정가 진입(체결 확인은 호출측 position_qty 폴링, 손절은 체결 뒤 place_protective_stop)."""
        if dry_run:
            return {"dry_run": True, "would_place": {"orderType": "LMT", "lmtPrice": self._align(contract, price),
                                                     "action": _act(side), "qty": int(size)}}
        try:
            r = self.place_entry(account_id=account_id, contract_id=contract, side=side, size=size,
                                 order_type=1, limit_price=price, custom_tag=custom_tag, dry_run=False)
            return {"order_id": (r or {}).get("order_id"), "error": None}
        except Exception as e:
            return {"order_id": None, "error": str(e)}

    def cancel_order(self, account_id, order_id) -> dict:
        with self._lock:
            ib = self._connect()
            oid = int(order_id)
            for t in ib.openTrades():
                if int(t.order.orderId or 0) == oid:
                    ib.cancelOrder(t.order)
                    ib.sleep(0.3)
                    return {"cancelled": oid, "status": str(t.orderStatus.status)}
            return {"cancelled": oid, "status": "not_open"}

    def current_market_price(self, contract):
        """현재가(스냅샷) - last → close → mid. 구독 없으면 지연 시세. 실패 시 None(호출측 폴백 규약)."""
        with self._lock:
            try:
                ib = self._connect()
                c = self._contract(contract)
                tk = ib.reqMktData(c, "", True, False)
                ib.sleep(1.5)
                for v in (getattr(tk, "last", None), getattr(tk, "close", None)):
                    if v is not None and not (isinstance(v, float) and math.isnan(v)) and float(v) > 0:
                        return float(v)
                bid, ask = getattr(tk, "bid", None), getattr(tk, "ask", None)
                if bid and ask and not math.isnan(bid) and not math.isnan(ask) and bid > 0 and ask > 0:
                    return (float(bid) + float(ask)) / 2.0
                return None
            except Exception:
                return None

    def _cancel_for(self, ib, aid: str, c) -> int:
        """이 {계좌, 계약}의 미체결 주문만 취소(스탑 포함). 취소 수 반환."""
        n = 0
        for t in list(ib.openTrades()):
            try:
                same_c = int(getattr(t.contract, "conId", 0) or 0) == int(c.conId or -1) or \
                    str(getattr(t.contract, "localSymbol", "")) == str(c.localSymbol)
                if same_c and (not aid or str(t.order.account or "") in ("", aid)):
                    ib.cancelOrder(t.order)
                    n += 1
            except Exception:
                pass
        if n:
            ib.sleep(0.4)
        return n

    def close_contract(self, account_id, contract) -> dict:
        """한 {계좌, 계약} 포지션 전체 시장가 청산 + 그 계약의 미체결 주문(스탑 등) 취소.
        contract는 localSymbol(str) 또는 conId 둘 다 받는다. 플랫이면 주문 없이 no-op."""
        ibi = _ib_classes()
        with self._lock:
            ib = self._connect()
            aid = self._acct_id(account_id)
            c = self._contract(contract)
            lsym = str(c.localSymbol or contract)
            cancelled = self._cancel_for(ib, aid, c)
            qty = 0
            for p in self._positions(aid):
                pc = p.contract
                if int(getattr(pc, "conId", 0) or 0) == int(c.conId or -1) or \
                        str(getattr(pc, "localSymbol", "")) == lsym:
                    qty = int(p.position or 0)
                    break
            if qty == 0:
                return {"closed": 0, "cancelled": cancelled, "symbol": lsym}
            o = self._mk(ibi, "MKT", ("SELL" if qty > 0 else "BUY"), abs(qty), account=aid,
                         ref=f"EQ-AP-CLOSE-{int(time.time() * 1000)}", tif="DAY")
            t = ib.placeOrder(c, o)
            rej = self._ack(ib, t)
            if rej:
                raise RuntimeError(f"IBKR close rejected ({lsym}): {rej}")
            ib.sleep(1.0)
            return {"closed": qty, "cancelled": cancelled, "symbol": lsym, "order": self._odict(t)}

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        """계좌(들) 전체 청산: 미체결 전부 취소 → 포지션마다 시장가 반대 주문 → 재확인."""
        ibi = _ib_classes()
        with self._lock:
            ib = self._connect()
            plan = self.list_open_positions()
            res = FlattenResult(dry_run=dry_run, planned=list(plan))
            if dry_run or not plan:
                return res
            want = {a["id"] for a in (self._acct_cache or self._accounts())}
            try:
                if want and len(want) < len(ib.managedAccounts() or []):
                    for t in list(ib.openTrades()):          # 지정 계좌의 주문만 취소
                        if str(t.order.account or "") in want:
                            ib.cancelOrder(t.order)
                else:
                    ib.reqGlobalCancel()
            except Exception as e:
                res.errors.append(f"cancel open orders failed: {e}")
            ib.sleep(0.5)
            for pos in plan:
                try:
                    o = self._mk(ibi, "MKT", ("SELL" if pos.net_qty > 0 else "BUY"), abs(int(pos.net_qty)),
                                 account=pos.account_id, ref=f"EQ-AP-FLAT-{int(time.time() * 1000)}", tif="DAY")
                    t = ib.placeOrder(pos.raw["contract"], o)
                    rej = self._ack(ib, t, wait_s=1.5)
                    if rej:
                        res.errors.append(f"{pos.account_name}/{pos.symbol}: {rej}")
                except Exception as e:
                    res.errors.append(f"{pos.account_name}/{pos.symbol}: {e}")
            ib.sleep(2)                           # let the market orders fill / positions update
            # Confirm-after-act: re-read; anything still open is a loud error.
            try:
                still = self.list_open_positions()
                res.closed = [p for p in plan if not any(
                    s.account_id == p.account_id and s.symbol == p.symbol for s in still)]
                for p in still:
                    res.errors.append(f"STILL OPEN after flatten: {p.account_name}/{p.symbol} net={p.net_qty}")
            except Exception as e:
                res.errors.append(f"post-flatten position re-check failed: {e}")
            return res

    # ── fills (트랙레코드 푸시) ──────────────────────────────────────────────────
    def closed_fills(self, start_ms: int) -> list[dict]:
        """실현손익 체결 목록 - ProjectX/Tradovate/NT8과 같은 계약
        [{tid, ts_ms, symbol, pnl, direction, acct, acct_name}]. 청산 체결만(진입 체결은
        CommissionReport.realizedPNL이 미설정 센티널). direction = 그 포지션의 **진입** 방향
        (SLD로 닫았으면 LONG). TWS 현재 세션 범위."""
        with self._lock:
            ib = self._connect()
            want = {a["id"].upper() for a in self._accounts()}
            try:
                ib.reqExecutions()
                ib.sleep(0.5)                     # CommissionReport는 execDetails 뒤에 온다
            except Exception:
                pass
            out = []
            for f in ib.fills() or []:
                try:
                    ex, cr = f.execution, f.commissionReport
                    if cr is None:
                        continue
                    pnl = getattr(cr, "realizedPNL", None)
                    if pnl is None or (isinstance(pnl, float) and (math.isnan(pnl) or abs(pnl) >= _PNL_UNSET)):
                        continue                  # 진입 체결(실현손익 없음)
                    acct = str(ex.acctNumber or "")
                    if want and acct.upper() not in want:
                        continue
                    t = ex.time
                    if isinstance(t, datetime):
                        ts_ms = int((t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp() * 1000)
                    else:
                        ts_ms = int(datetime.fromisoformat(str(t)).replace(tzinfo=timezone.utc).timestamp() * 1000)
                    if ts_ms < int(start_ms):
                        continue
                    out.append({
                        "tid": str(ex.execId or ""),
                        "ts_ms": ts_ms,
                        "symbol": str(getattr(f.contract, "localSymbol", "") or getattr(f.contract, "symbol", "")),
                        "pnl": float(pnl),
                        "direction": "LONG" if str(ex.side).upper().startswith("S") else "SHORT",
                        "acct": acct, "acct_name": acct,
                        "qty": int(ex.shares or 0), "price": float(ex.price or 0),
                        "commission": float(getattr(cr, "commission", 0) or 0),
                    })
                except Exception:
                    continue
            return out
