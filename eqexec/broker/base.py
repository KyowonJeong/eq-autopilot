"""Broker adapter interface.

A broker adapter is the ONLY thing that talks to the member's account. Keep the surface tiny:
authenticate, read open positions, flatten everything. Every adapter must honor `dry_run`
(read-only, no orders) so the tool is safe to run before the member trusts it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class Position:
    account_id: str
    account_name: str
    symbol: str              # contract symbol, e.g. "NQH6"
    net_qty: int             # signed; >0 long, <0 short, 0 = flat
    raw: dict = field(default_factory=dict)


@dataclass
class FlattenResult:
    dry_run: bool
    planned: list[Position] = field(default_factory=list)   # positions we intend to close
    closed: list[Position] = field(default_factory=list)    # confirmed closed (live only)
    cancelled: list[str] = field(default_factory=list)      # working orders cancelled (live only)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def pos_px(v):
    """체결가 값 → 양수 유한 float, 아니면 None(없음, 0, 문자열, NaN, 무한대). 트랙레코드 영수증 체결가 재료(R57 #7, 2026-11-08)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if (f == f and 0 < f < float("inf")) else None


def entry_px_walk(rows) -> dict:
    """반턴 체결들에서 청산 반턴마다 그 포지션의 진입가를 정한다(트랙레코드 영수증, R57 #7, 2026-11-08).

    rows = [(정렬 키, 계약 키, 행 키, 부호 있는 수량(+ 매수, - 매도), 가격, 청산이냐)] - 청산이냐가 None이면 부호로 가른다(순포지션과
    반대 방향이면 청산, NT8 애드온 장부와 같은 규칙). 계약마다 시간순으로 보며, 포지션이 0에서 열린 뒤의 진입 반턴 가격을 모으고 0으로
    돌아오면 비운다. 반환 {행 키: 진입가 또는 None}(청산 반턴만).
    진입가 = 그 포지션의 진입 가격이 **하나뿐일 때만** 그 값. 여럿이면(시장가가 여러 호가에 걸림), 수량이나 가격을 모르는 반턴이 섞였으면,
    조회창 앞에서 열린 포지션이라 진입을 못 봤으면 None - 평균을 지어내지 않는다. 뒤집힘(남는 수량)은 그 가격의 새 진입으로 본다."""
    out, st = {}, {}
    rows = list(rows or ())
    dead = set()                                            # 수량을 모르는 반턴이 있는 계약 - 순포지션을 셀 수 없어 그 계약은 전부 None
    for r in rows:
        try:
            _q = float(r[3] or 0)
            if not _q or _q != _q or abs(_q) == float("inf"):
                dead.add(r[1])
        except (TypeError, ValueError):
            dead.add(r[1])
    for _sk, con, key, q, px, is_close in sorted(rows, key=lambda r: r[0]):
        try:
            q = float(q or 0)
        except (TypeError, ValueError):
            q = 0.0
        if con in dead:
            if is_close or is_close is None:
                out[key] = None
            continue
        px = pos_px(px)
        s = st.setdefault(con, [0.0, set(), False])         # [순포지션(부호), 진입 가격들, 모르는 반턴 섞임]
        net = s[0]
        if is_close is None:
            is_close = net != 0 and q != 0 and (net > 0) != (q > 0)
        if not is_close:
            if net == 0:
                s[1], s[2] = set(), False
            elif q != 0 and (net > 0) != (q > 0):
                s[2] = True                                 # 진입이라는데 열린 방향과 반대 - 모른다
            if px is None or q == 0:
                s[2] = True
            else:
                s[1].add(px)
            s[0] = net + q
            continue
        ok = net != 0 and q != 0 and (net > 0) != (q > 0) and len(s[1]) == 1 and not s[2]
        out[key] = next(iter(s[1])) if ok else None
        if q == 0 or net == 0 or (net > 0) == (q > 0):
            st[con] = [0.0, set(), False]                   # 모르는 상태 - 다음 진입부터 새로 센다
            continue
        rest = abs(net) - abs(q)
        if rest > 1e-9:
            s[0] = rest if net > 0 else -rest
        elif rest < -1e-9:                                  # 뒤집힘: 남는 만큼 이 가격의 반대 방향 새 진입
            st[con] = [(-rest if q > 0 else rest), ({px} if px else set()), px is None]
        else:
            st[con] = [0.0, set(), False]
    return out


def configured_accounts(adapter) -> list[str]:
    """이 인스턴스에 지정된 계좌 목록(공백 제외). cfg가 없거나 비면 []."""
    acc = getattr(adapter, "accounts", None)
    if acc is None:
        acc = getattr(getattr(adapter, "cfg", None), "accounts", None)
    return [str(a).strip() for a in (acc or []) if str(a).strip()]


def refuse_flatten_without_accounts(adapter, dry_run: bool):
    """선물 어댑터 flatten_all의 안전장치(최종 반박 리뷰 2026-10-08 P0).

    선물 로그인 하나에 계좌가 여럿이다(프롭 평가·펀디드·다른 회원 설정의 계좌). 계좌 목록이 빈 인스턴스에서
    flatten_all을 돌리면 _accounts()가 '전부'로 풀려 그 로그인의 모든 계좌를 닫는다. 청산은 계좌를 지정한
    인스턴스에서만 한다 - 비면 아무것도 하지 않고 오류를 돌려준다(dry_run이어도 같은 결과: 계획도 세우지 않는다).
    반환: 거부면 FlattenResult(errors=[...]), 진행해도 되면 None."""
    if configured_accounts(adapter):
        return None
    return FlattenResult(dry_run=dry_run, errors=[
        f"{getattr(adapter, 'name', 'broker')}: no account id set - refusing to flatten "
        f"(an empty account list would close every account on this login)"])


class BrokerAdapter(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def authenticate(self) -> None:
        """Acquire/refresh whatever token/session is needed. Raise on failure."""

    @abc.abstractmethod
    def list_open_positions(self) -> list[Position]:
        """Return open positions (net_qty != 0) across the configured accounts."""

    @abc.abstractmethod
    def flatten_all(self, dry_run: bool = True, only=None) -> FlattenResult:
        """Close every open position. dry_run=True must send NO orders — only build the plan."""

    def contract_for_month(self, root: str, year: int, month: int, max_age=None):
        """서버 신호의 월물(2026-11-02 월물 규칙 R)에 해당하는 이 브로커의 계약 id. 없으면 None(그 진입은 건너뛴다 -
        다른 월물로 바꿔 주문하지 않는다). 선물 브로커가 각자 덮어쓴다."""
        return None

    @abc.abstractmethod
    def healthcheck(self) -> bool:
        """Cheap auth + read check; True if the account is reachable."""
