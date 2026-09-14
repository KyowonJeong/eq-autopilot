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
import re as _re
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone

from .base import BrokerAdapter, FlattenResult, Position

_ACK_WAIT_S = 2.5            # 주문 접수/거절 판정 대기(TWS는 거절을 비동기 에러로 준다)
_CONNECT_TIMEOUT_S = 15
# 활성월·롤 규칙은 **공용 달력 한 곳**에서 온다(2026-09-14). 종전엔 이 파일이 자기 표를
# 들고 있었고 nt8.py와 달랐다 - 금속에 V(10월)를 넣어, 546일 중 62일을 NT8과 **다른
# 계약월**로 골랐다. 서버 신호는 절대 손절가를 보내고 그 값은 정본 가격 계열(12월물)에서
# 나오므로, 다른 월물에 보정 없이 얹으면 월물 basis만큼 손절거리가 틀어진다(실측: basis가
# 손절거리의 75~125%). 롱은 스탑이 시장가 위로 가 거부되고, 숏은 한 번에 약 2R이 나간다.
from . import futures_cal as _cal

_ROLL_DAYS_INDEX = _cal.ROLL_DAYS_INDEX
_ROLL_DAYS_METAL = _cal.ROLL_DAYS_METAL
_MONTH_CODES = _cal.MONTH_CODES
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
    """'MNQZ5' → 'MNQ', 'MGCG6' → 'MGC', 'M2KZ6' → 'M2K'.

    ⚠️알파벳만 훑으면 안 된다(2026-09-14): 'M2K'처럼 **숫자가 든 루트**에서 첫 글자
    'M'에서 멈춰 루트를 'M'으로 읽었다. 그러면 활성월 표에도 틱 표에도 안 맞아
    Micro Russell은 계약 해석도 틱 정렬도 통째로 꺼진다. 뒤에서부터 '월코드+연도'
    꼬리를 떼는 쪽이 맞다 - 루트에 무엇이 들었든 꼬리 모양은 하나다."""
    s = str(sym).upper().strip()
    # 꼬리 = 월코드 1자 + 연도 1~2자리(Z5, Z25). 그 앞이 전부 루트다.
    _m = _re.match(r"^([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{1,2})$", s)
    if _m and len(_m.group(1)) >= 1:
        return _m.group(1)
    # 꼬리가 없으면 통째로 루트다('M2K'·'GC'). ⚠️여기서 알파벳만 훑어 자르면 'M2K'가
    # 'M'이 된다 - 위 정규식이 안 맞았다는 것은 애초에 월물 심볼이 아니라는 뜻이다.
    return s


class IBKRBroker(BrokerAdapter):
    name = "ibkr"

    _CID_LOCK = threading.Lock()
    _CID_SEQ = 0

    @classmethod
    def _next_cid(cls) -> int:
        """인스턴스별 clientId 오프셋(0,1,2…). TWS는 32비트 양수면 되고 충돌만 피하면 된다."""
        with cls._CID_LOCK:
            cls._CID_SEQ = (cls._CID_SEQ + 1) % 900
            return cls._CID_SEQ

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
            # clientId는 **인스턴스마다 달라야 한다**(2026-09-14). TWS는 같은 clientId로
            # 두 번째 접속이 오면 첫 번째를 끊거나 새 접속을 거부한다. 종전엔 cfg 기본값
            # 11이 전 인스턴스에 고정이라, 다계좌 병렬 발주에서 **둘째 계좌가 조용히
            # 빠졌다**(같은 브로커에 계좌를 둘 켜는 것이 우리 표준 구성인데도).
            # cfg에 명시값이 있으면 그것을 쓰고(회원이 TWS의 다른 도구와 충돌을 피하려
            # 정한 값), 없으면 기본값에 인스턴스 일련번호를 더해 서로 겹치지 않게 한다.
            _cid = getattr(self.cfg, "client_id", None)
            _cid = int(_cid) if _cid else (11 + IBKRBroker._next_cid())
            ib.connect(host, int(self.cfg.port), clientId=_cid,
                       timeout=_CONNECT_TIMEOUT_S, readonly=False)
            self._client_id = _cid
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
            return [f"IBKR {self.cfg.host}:{self.cfg.port} clientId {getattr(self, '_client_id', None) or getattr(self.cfg, 'client_id', 11)} "
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
        active = _cal.active_months(root)
        if active and code not in active:
            return False                                    # 시리얼(비활성) 월물 제외
        if not _cal.is_index(root):
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
        # ⚠️'좋은 상태를 보자마자 OK'로 돌아가지 않는다(2026-09-14). TWS는 PreSubmitted를
        # 먼저 주고 **그 뒤에** 비동기 error로 거절하는 일이 흔하다. 종전에는 첫 좋은
        # 상태에서 즉시 None을 돌려줘, 1.5초 뒤 거절되는 보호 스탑을 '스탑 걸림'으로
        # 보고했다 - 회원 화면에 방패가 있다고 적히는데 실제로는 알몸인 상태(거짓 방패).
        # 창을 끝까지 지켜보되, Filled는 되돌릴 수 없으므로 그때만 즉시 확정한다.
        t0 = time.time()
        while time.time() - t0 < wait_s:
            st = str(trade.orderStatus.status)
            if st in _REJECT_STATES:
                break
            if st == "Filled":
                return None                       # 체결은 되돌아가지 않는다
            ib.sleep(0.25)
        st = str(trade.orderStatus.status)
        # 창이 끝났는데 아직 접수 신호조차 없으면 **모른다**고 말한다 - 종전엔 None(정상)으로
        # 돌려줘 타임아웃이 곧 '접수됨'이었다. 호출부가 방패로 삼는 자리라 침묵이 제일 나쁘다.
        if st not in _REJECT_STATES and st not in ("PreSubmitted", "Submitted", "Filled"):
            return f"PENDING: {st or 'no status'} after {wait_s:.1f}s"
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
            # 자식 스탑은 **두 번** 본다(2026-09-14): 첫 창을 통과해도 TWS가 뒤늦게
            # 거절하는 일이 있어, 한 박자 쉬고 상태를 다시 읽는다. 여기가 방패의 유무를
            # 판정하는 유일한 자리라 낙관이 제일 비싸다.
            rej2 = self._ack(ib, t2, wait_s=2.0)
            if not rej2:
                try:
                    ib.sleep(1.0)
                    _st2 = str(t2.orderStatus.status)
                    if _st2 in _REJECT_STATES:
                        rej2 = f"{_st2} (late reject)"
                except Exception:
                    pass
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
            want = {a["id"] for a in (self._acct_cache or self._accounts())}
            if dry_run:
                return res
            if not plan:
                # ⚠️포지션이 없어도 **우리 미체결 주문은 걷어낸다**(2026-09-14). 종전엔
                # 여기서 그냥 돌아가, 청산이 다른 경로로 끝난 뒤 남은 GTC 보호 스탑이
                # 살아 있었다 - 그 스탑이 나중에 혼자 체결되면 **아무도 모르는 반대
                # 포지션**이 열린다(보호 스탑이 진입 주문이 되는 것). 자식 스탑은 tif=GTC라
                # 장을 넘겨도 안 죽는다.
                try:
                    for t in list(ib.openTrades()):
                        _o = t.order
                        if want and str(_o.account or "") not in want:
                            continue
                        if str(getattr(_o, "orderRef", "") or "").startswith("EQ-AP-"):
                            ib.cancelOrder(_o)
                except Exception as e:
                    res.errors.append(f"cancel orphan orders failed: {e}")
                return res
            # ⛔reqGlobalCancel을 쓰지 않는다(2026-09-14). 종전엔 '지정 계좌 수 == 관리 계좌
            # 수'이면 전역 취소로 떨어졌는데, **단일 계좌 로그인에서는 그게 항상 참**이라
            # 실제로는 거의 매번 전역 취소가 나갔다. 그 계좌에서 회원이 직접 낸 주문
            # (다른 상품 지정가, 개인 스탑)까지 우리가 지운다 - 우리 것이 아닌 것을 건드리는
            # 유일한 자리였다. 항상 **우리가 붙인 orderRef가 있는 주문만** 취소한다.
            try:
                for t in list(ib.openTrades()):
                    _o = t.order
                    if want and str(_o.account or "") not in want:
                        continue
                    if not str(getattr(_o, "orderRef", "") or "").startswith("EQ-AP-"):
                        continue                             # 회원 본인 주문 - 건드리지 않는다
                    ib.cancelOrder(_o)
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
            # ⚠️realizedPNL로 진입/청산을 가르지 않는다(2026-09-14). ib_insync 0.9.86이
            # wrapper.py에서 UNSET_DOUBLE을 **0.0으로 먼저 뭉개고**(objects.py 기본값도 0.0)
            # commissionReport도 None이 아니므로, 종전의 센티널 검사(_PNL_UNSET)와 None 검사는
            # 둘 다 한 번도 발동하지 않는 죽은 코드였다. 그 결과 **진입 체결이 pnl 0.0짜리
            # 청산 거래로 새어나가고**, 방향도 청산 측(side)에서 역산하던 탓에 반대로 붙었다 -
            # 트랙레코드에 '그 날 0R 숏 거래'라는 없는 행이 생겨 승률·거래수·연속 줄이 오염된다.
            #
            # 대신 **포지션을 걸어서** 가른다: 체결을 시각순으로 훑으며 (계좌, conId)별 부호
            # 포지션을 누적하고, |포지션|을 **줄이는** 체결만 청산으로 본다. 회원이 TWS에서
            # 손으로 닫은 것도 잡히고, 손익이 진짜 0.00인 청산도 살아남는다.
            # 진입 방향은 닫기 직전 포지션의 부호에서 나온다(양수면 LONG 진입).
            _fills = []
            for f in ib.fills() or []:
                try:
                    _t = f.execution.time
                    _k = (_t.timestamp() if isinstance(_t, datetime)
                          else datetime.fromisoformat(str(_t)).timestamp())
                except Exception:
                    _k = 0.0
                _fills.append((_k, str(getattr(f.execution, "execId", "")), f))
            _fills.sort(key=lambda x: (x[0], x[1]))
            _book = {}                            # (계좌, conId) -> 부호 포지션
            out = []
            for _, _eid, f in _fills:
                try:
                    ex, cr = f.execution, f.commissionReport
                    acct = str(ex.acctNumber or "")
                    if want and acct.upper() not in want:
                        continue
                    _cid = int(getattr(f.contract, "conId", 0) or 0)
                    _key = (acct, _cid)
                    _prev = _book.get(_key, 0)
                    _shares = int(ex.shares or 0)
                    _delta = _shares if str(ex.side).upper().startswith("B") else -_shares
                    _book[_key] = _prev + _delta
                    # 청산 = 직전 포지션이 있고 그 반대 방향으로 체결된 것
                    if _prev == 0 or (_prev > 0) == (_delta > 0):
                        continue                  # 진입 또는 증량
                    pnl = getattr(cr, "realizedPNL", 0.0) if cr is not None else 0.0
                    if pnl is None or (isinstance(pnl, float)
                                       and (math.isnan(pnl) or abs(pnl) >= _PNL_UNSET)):
                        pnl = 0.0                 # 값이 아직 안 왔을 뿐 - 청산 사실은 유효
                    _entry_dir = "LONG" if _prev > 0 else "SHORT"
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
                        "direction": _entry_dir,   # 닫기 직전 포지션 부호 = 진입 방향
                        "acct": acct, "acct_name": acct,
                        "qty": int(ex.shares or 0), "price": float(ex.price or 0),
                        "commission": float(getattr(cr, "commission", 0) or 0),
                    })
                except Exception:
                    continue
            return out
