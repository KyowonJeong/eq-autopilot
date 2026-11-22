"""ProjectX Gateway adapter (Phase 1 — Topstep / TopstepX).

ProjectX is multi-tenant: each firm has its own base URL. TopstepX = https://api.topstepx.com.
Endpoints (ProjectX Gateway docs — see PLAN.md). VERIFY against a TopstepX demo/eval account
before live: account search shape, position `type` long/short mapping, and that closeContract
fully flattens. dry_run=True sends NO close calls.
"""
from __future__ import annotations

import threading as _thr
import time

import requests

from .base import BrokerAdapter, FlattenResult, Position, entry_px_walk, pos_px, refuse_flatten_without_accounts

_TIMEOUT = (10, 30)
_TOKEN_TTL = 24 * 60 * 60       # ProjectX session token ~24h
_RENEW_MARGIN = 60 * 60         # re-auth 1h before expiry

# ── 자격증명 단위 토큰 캐시(2026-09-18 대표 "일곱 개 돌리니까 좀 차이가 나더라고") ──────────
# 왜: 진입은 계좌마다 독립 스레드로 동시에 쏘는데(eqgui의 병렬 발주), 스레드마다 브로커 객체를
# 새로 만들어 **각자 로그인**했다. 계좌 7개면 /api/Auth/loginKey 요청 7개가 같은 순간에 날아가고
# 브로커가 그걸 줄 세우는 만큼 계좌 간 진입가가 벌어진다. 토큰은 자격증명 단위로 24시간 유효하니
# 객체가 달라도 나눠 쓰면 된다 - 진입 순간에는 주문 한 번만 남는다.
# 안전 규약 셋:
#   ① 키는 (base, 사용자, API키) **완전 일치**만. 조금이라도 다르면 새로 로그인한다(다른 계좌
#      토큰으로 주문이 나가는 것이 이 최적화의 유일한 치명적 실패라, 느슨하게 맞추지 않는다).
#   ② 캐시는 (토큰, 발급시각) 튜플뿐 - 브로커 객체나 requests.Session은 공유하지 않는다.
#   ③ 발급시각을 그대로 들고 오므로 기존 _ensure_token의 만료 가드(TTL-여유)가 그대로 작동한다.
#      가짜 시각을 심지 않는다.
_TOK_CACHE: dict = {}
_TOK_LOCK = _thr.Lock()

# 연결 재사용(대표 2026-10-20 진입 단계 시각: Topstep 주문 앞뒤가 3~4초): 종전엔 호출마다 requests.post로 새 연결을 열어 매번 TLS 악수를
# 했다(미국 서버라 왕복 몇 번이 곧 수백 ms). 한 프로세스가 이 세션 하나를 나눠 쓴다(연결 풀은 스레드 안전, 계좌 스레드 여럿이 동시에
# 써도 연결을 따로 잡는다). 진입 직전 예열(eqgui._entry_warm)이 연결을 미리 열어 둔다.
_HTTP = requests.Session()
_HTTP.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=32, max_retries=0))


# 다시 보내도 되는 조회 경로(앱 출시 전 검토 2026-10-20 P2): 재사용한 연결이 그새 끊겨 있으면(PC가 잠들었다 깸, 서버 쪽 유휴 종료) 종전처럼
# 매번 새 연결일 때는 없던 연결 오류가 난다. 조회는 한 번 더 보낸다. 주문, 취소, 청산은 다시 보내지 않는다 - 서버에 닿았는지 모르는 주문을
# 또 보내면 이중 주문이 된다(종전처럼 한 번, 실패는 호출부가 보고).
_RETRY_READS = ("/api/Auth/loginKey", "/api/Account/search", "/api/Contract/search", "/api/Position/searchOpen",
                "/api/Order/searchOpen", "/api/Trade/search", "/api/History/retrieveBars")


def _http_post(url, **kw):
    """세션으로 POST. 시험이 requests.post를 바꿔 끼웠으면 그것을 쓴다(종전 시험 그대로 - 네트워크 없이)."""
    if getattr(requests.post, "__module__", "") != "requests.api":
        return requests.post(url, **kw)
    try:
        return _HTTP.post(url, **kw)
    except requests.exceptions.ConnectionError:
        if not str(url).endswith(_RETRY_READS):
            raise
        return _HTTP.post(url, **kw)


def _tok_key(cfg):
    return (str(getattr(cfg, "base_url", "")).rstrip("/"),
            str(getattr(cfg, "user_name", "")), str(getattr(cfg, "api_key", "")))


# ── 진입 경로 읽기 캐시(대표 2026-10-16 "아직도 들어가는데 십몇 초가 걸려"): 10/16 08:00 금 진입에서 Topstep 계좌는 크기 계산 뒤
# 주문까지 7초가 걸렸다(서버 클레임 기록: NT8 08:00:07.2, Topstep 08:00:09.8 - 그 뒤 주문 08:00:14). 계좌 스레드마다 계좌 목록
# (Account/search)을 세 번, 계약 조회(Contract/search)를 한두 번 미국 서버에 차례로 물었다. 아래 캐시는 **호출측이 max_age를 넘길
# 때만** 쓴다(진입 경로) - 감시 루프 등 나머지 호출은 종전대로 매번 새로 읽는다. 같은 키를 여러 스레드가 동시에 찾으면 한 스레드만
# 묻고 나머지는 그 답을 기다린다(동시 요청 폭주 없음). 계좌 목록 키 = 자격증명 완전 일치(_tok_key), 계약 키 = (base, 검색어, live).
# 캐시하는 것은 계좌 목록(이름, id, 잔고)과 계약 메타데이터뿐 - 포지션, 주문, 가격은 캐시하지 않는다.
_RD_CACHE: dict = {}
_RD_INFLIGHT: dict = {}
_RD_LOCK = _thr.Lock()
_RD_WAIT = 12.0                 # 같은 키를 묻는 다른 스레드의 답을 기다리는 최대 시간(초) - 넘으면 직접 묻는다
_TICKS: dict = {}               # contractId → tickSize(프로세스 공용, 계약 메타데이터는 바뀌지 않는다)


def _rd_empty(v) -> bool:
    """캐시에 남기면 안 되는 빈 조회 답(빈 목록, accounts·contracts가 빈 응답)."""
    if isinstance(v, (list, tuple)):
        return not v
    if isinstance(v, dict):
        return any(k in v and not v.get(k) for k in ("accounts", "contracts"))
    return v is None


def _rd_cached(key, max_age, fetch):
    """max_age 초 안의 값이 있으면 그 값, 없으면 fetch() 한 번(같은 키 동시 요청은 하나로 묶는다). fetch 예외는 그대로 올린다."""
    now = time.time()
    with _RD_LOCK:
        hit = _RD_CACHE.get(key)
        if hit is not None and (now - hit[0]) <= float(max_age):
            return hit[1]
        ev = _RD_INFLIGHT.get(key)
        owner = ev is None
        if owner:
            ev = _thr.Event()
            _RD_INFLIGHT[key] = ev
    if not owner:
        ev.wait(_RD_WAIT)
        with _RD_LOCK:
            hit = _RD_CACHE.get(key)
            if hit is not None and (time.time() - hit[0]) <= float(max_age) + _RD_WAIT:
                return hit[1]
        return fetch()                       # 앞선 스레드가 실패했거나 너무 늦음 - 직접 묻는다(조용한 빈 값 금지)
    try:
        v = fetch()
        # 빈 목록은 남기지 않는다(앱 출시 전 검토 2026-10-20 P1): 진입 90초 전 예열에서 한 번 빈 계좌, 계약 목록이 오면 그것이 150초 동안
        # 모든 계좌 진입에 쓰여 '계좌 없음', '활성 계약 없음'으로 다 건너뛰었다. 빈 답은 다음 호출이 새로 묻는다.
        if not _rd_empty(v):
            with _RD_LOCK:
                _RD_CACHE[key] = (time.time(), v)
        return v
    finally:
        with _RD_LOCK:
            _RD_INFLIGHT.pop(key, None)
        ev.set()


def _rd_forget(prefix) -> None:
    """캐시에서 prefix로 시작하는 키를 지운다(시험, 무효화용)."""
    with _RD_LOCK:
        for k in [k for k in _RD_CACHE if k[:len(prefix)] == prefix]:
            _RD_CACHE.pop(k, None)
_LONG = 1                       # position.type: 1=long, 2=short (docs don't state it; common ProjectX
                                # convention). Only affects the displayed net sign — closeContract
                                # flattens the whole position regardless. ⚠ 2026-08-18: no longer
                                # cosmetic - eqgui pass-TP uses net_qty sign for open-PnL math.
                                # VERIFY type mapping (1=long?) live; pass-TP holds off on ∉(1,2).
_SIDE = {"BUY": 0, "LONG": 0, "BID": 0, 0: 0, "SELL": 1, "SHORT": 1, "ASK": 1, 1: 1}

# 계약 메타데이터 조회 실패 시 폴백 틱사이즈(우리가 다루는 심볼만).
_FALLBACK_TICK = {"MNQ": 0.25, "NQ": 0.25, "ENQ": 0.25, "MGC": 0.1, "GC": 0.1, "GCE": 0.1}   # ENQ, GCE = ProjectX 풀사이즈 루트(버그 조사 P2-2)
# flatten_all 대기(초, 오픈 준비 버그 조사 2026-10-06 G): closeContract 1회 재시도 전 / 청산 반영 재조회 전.
_CLOSE_RETRY_WAIT = 1.0
_RECHECK_WAIT = 1.5


def _parse_iso(s):
    """ISO 시각 → aware datetime. 소수 초를 6자리로 맞춘 뒤 읽는다 - .NET이 끝의 0을 지운 '.52329'를 파이썬 3.9의 fromisoformat이
    거절해 거래 행이 조용히 빠지던 것(평가 계좌 익절 3차 리뷰 P1, 맥 빌드는 3.9). 3.11 이상에서는 종전과 같은 값. 못 읽으면 ValueError."""
    import re as _re
    from datetime import datetime, timezone
    t = str(s or "").strip().replace("Z", "+00:00")
    t = _re.sub(r"(\.\d+)", lambda m: (m.group(1) + "000000")[:7], t, count=1)
    d = datetime.fromisoformat(t)
    return d if d.tzinfo is not None else d.replace(tzinfo=timezone.utc)


def _px_entry_map(trades) -> dict:
    """한 계좌의 거래 기록(Trade/search 반턴들) → {id(청산 반턴 행): 진입가 또는 None}(트랙레코드 영수증, R57 #7, 2026-11-08).
    진입 반턴 = profitAndLoss null(ProjectX 규약), side 0 = 매수, 1 = 매도, 계약마다 따로 센다(base.entry_px_walk). 시각을 못 읽는 행이
    있는 계약은 전부 None(순서를 모르면 짐작이다). 어떤 실패든 {} - 체결가 없이 종전대로 올라간다."""
    try:
        rows, bad = [], set()
        for i, t in enumerate(trades or []):
            if not isinstance(t, dict) or t.get("voided"):
                continue
            con = str(t.get("contractId") or "")
            try:
                ts = _parse_iso(t.get("creationTimestamp") or "").timestamp()
            except Exception:
                bad.add(con)
                continue
            try:
                q = abs(float(t.get("size") or 0)) * (1 if int(t.get("side")) == 0 else -1)
            except (TypeError, ValueError):
                q = 0.0
            rows.append(((ts, i), con, id(t), q, t.get("price"), t.get("profitAndLoss") is not None))
        m = entry_px_walk(rows)
        for r in rows:
            if r[1] in bad:
                m[r[2]] = None
        return m
    except Exception:
        return {}


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
        self._ticks = _TICKS                 # contractId → tickSize 캐시(프로세스 공용, 2026-10-16 - 계좌 스레드마다 다시 묻지 않게)

    # ── auth ──────────────────────────────────────────────────────────────
    def authenticate(self) -> None:
        r = _http_post(f"{self.base}/api/Auth/loginKey",
                       json={"userName": self.cfg.user_name, "apiKey": self.cfg.api_key},
                       timeout=_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if not d.get("success") or not d.get("token"):
            raise RuntimeError(f"ProjectX auth failed (errorCode={d.get('errorCode')}): "
                               f"{d.get('errorMessage') or d}")
        self._token, self._token_at = d["token"], time.time()
        with _TOK_LOCK:                      # 같은 자격증명의 다른 스레드가 재로그인하지 않게
            _TOK_CACHE[_tok_key(self.cfg)] = (self._token, self._token_at)

    def _ensure_token(self) -> None:
        if self._token is None:
            with _TOK_LOCK:                  # 다른 스레드/객체가 이미 받아 둔 토큰이 있으면 쓴다
                _c = _TOK_CACHE.get(_tok_key(self.cfg))
            if _c and (time.time() - _c[1]) <= (_TOKEN_TTL - _RENEW_MARGIN):
                self._token, self._token_at = _c
                return
        if self._token is None or (time.time() - self._token_at) > (_TOKEN_TTL - _RENEW_MARGIN):
            self.authenticate()

    def _drop_token(self) -> None:
        """무효 판정된 토큰을 나와 캐시에서 함께 버린다(다음 호출이 새로 받는다)."""
        self._token = None
        with _TOK_LOCK:
            _TOK_CACHE.pop(_tok_key(self.cfg), None)

    def _post(self, path: str, body: dict, timeout=None):
        # timeout: 기본은 종전 _TIMEOUT. 진입 직전의 하루 실현 손익 조회(day_realized)만 짧은 값을 넘긴다 -
        # 느린 조회가 진입을 붙잡지 않게(평가 계좌 익절, 2026-10-14).
        _to = timeout or _TIMEOUT
        self._ensure_token()
        r = _http_post(f"{self.base}{path}",
                       headers={"Authorization": f"Bearer {self._token}",
                                "Content-Type": "application/json"},
                       json=body, timeout=_to)
        # 토큰이 무효면 한 번 다시 받아서 재시도한다(2026-09-18): 종전엔 주문 직전에 새로 로그인해
        # 이 창이 거의 없었지만, 토큰을 나눠 쓰면 그 사이에 서버가 세션을 끊는 창이 생긴다. 그때
        # 401을 그대로 raise하면 그 계좌는 **진입을 놓친다**. 재시도는 1회뿐이고, 실패하면 종전대로
        # 예외를 올린다(조용한 성공 위장 금지).
        if r.status_code in (401, 403):
            self._drop_token()
            self._ensure_token()
            r = _http_post(f"{self.base}{path}",
                           headers={"Authorization": f"Bearer {self._token}",
                                    "Content-Type": "application/json"},
                           json=body, timeout=_to)
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
    def _accounts(self, active_only: bool = True, timeout=None, max_age=None) -> list[dict]:
        # POST /api/Account/search {onlyActiveAccounts} -> {accounts:[{id,name,balance,canTrade,
        # isVisible}], ...}. active_only=False면 로테이션으로 닫힌 계좌까지 포함(트랙레코드용,
        # 대표 2026-07-29: 프롭 로테이션으로 빠진 계좌의 과거 체결이 트랙레코드에서 누락되던 버그).
        # timeout: 기본(None)은 종전 _TIMEOUT 그대로. 평가 계좌 감시만 짧은 값을 넘긴다(2026-10-14 2차 리뷰 P2 - 느린 한 번이 감시를 붙잡지 않게).
        # max_age: 진입 경로만 넘긴다(위 _rd_cached) - 그 초 안에 같은 자격증명으로 읽은 목록을 다시 쓴다. None = 종전대로 매번 읽기.
        _body = {"onlyActiveAccounts": bool(active_only)}

        def _fetch():
            return (self._post("/api/Account/search", _body, timeout=timeout) if timeout is not None
                    else self._post("/api/Account/search", _body))
        d = (_rd_cached(("acct",) + _tok_key(self.cfg) + (bool(active_only),), max_age, _fetch) if max_age is not None
             else _fetch())
        accts = d.get("accounts", d if isinstance(d, list) else [])
        want = {a.lower() for a in (self.cfg.accounts or [])}
        if want:
            accts = [a for a in accts
                     if str(a.get("name", "")).lower() in want or str(a.get("id")) in want]
        return accts

    def account_balance(self, acct, max_age=None):
        """계좌(이름 또는 id) 현재 잔고($) — 프롭 페이즈 자동 사이징용(대표 2026-07-26).
        못 찾거나 API 실패 시 None(호출부가 버퍼기 1R로 폴백)."""
        want = str(acct or "").strip().lower()
        if not want:
            return None
        for a in self._accounts(max_age=max_age):
            if str(a.get("name", "")).lower() == want or str(a.get("id")) == want:
                bal = a.get("balance")
                return float(bal) if bal is not None else None
        return None

    def search_contracts(self, text: str, live: bool = False, max_age=None) -> list[dict]:
        """POST /api/Contract/search {searchText, live} -> {contracts:[{id, name, description,
        tickSize, tickValue, activeContract, ...}]}. Used to resolve the current front-month
        contractId for a symbol (e.g. 'MNQ') so the user doesn't hand-type an expired code."""
        def _fetch():
            return self._post("/api/Contract/search", {"searchText": text, "live": bool(live)})
        d = (_rd_cached(("con", self.base, str(text), bool(live)), max_age, _fetch) if max_age is not None else _fetch())
        return d.get("contracts", d if isinstance(d, list) else [])

    _ROOT_ALT = {"NQ": ("NQ", "ENQ"), "GC": ("GC", "GCE")}

    def contract_for_month(self, root: str, year: int, month: int, max_age=None):
        """월물 규칙 R(2026-11-02): id 'CON.F.US.<루트>.<월코드><연 두 자리>'. 검색 결과에서 찾고, 없으면 id로 직접 묻는다
        (/api/Contract/searchById). 둘 다 없으면 None - activeContract로 바꿔 주문하지 않는다.
        TopstepX(ProjectX)는 활성 월물만 거래한다(2026-11-05 확인: 연습 계좌 화면에서 MGCG7을 고를 수 없음, /api/Contract/available에
        금, 나스닥은 활성 Z26만). 그 월물이 있어도 activeContract가 False면 주문해 봐야 거절이라 None을 돌려주고
        last_month_miss = 'inactive'로 남긴다(대표 2026-11-05 A안: 주문 전에 건너뛰고 맞는 사유로 알림). 못 찾으면 'missing'."""
        self.last_month_miss = None
        code = "FGHJKMNQUVXZ"[int(month) - 1] + f"{int(year) % 100:02d}"
        roots = self._ROOT_ALT.get(str(root).upper(), (str(root).upper(),))
        want = {f"CON.F.US.{r}.{code}" for r in roots}
        _inactive = False
        _known_off = set()                                 # 검색이 activeContract False라고 분명히 말한 id(2026-11-05 리뷰 P2)
        try:
            for c in self.search_contracts(root, max_age=max_age):
                if str(c.get("id") or "").upper() in want:
                    if c.get("activeContract") is False:
                        _inactive = True
                        _known_off.add(str(c.get("id") or "").upper())
                        continue
                    return c.get("id")
        except Exception:
            pass
        for cid in sorted(want):
            if cid in _known_off:
                continue                                   # 검색의 분명한 False를 searchById의 '필드 없음'이 뒤집지 않게
            try:
                d = self._post("/api/Contract/searchById", {"contractId": cid})
                c = (d or {}).get("contract") if isinstance(d, dict) else None
                if isinstance(c, dict) and str(c.get("id") or "").upper() == cid:
                    if c.get("activeContract") is False:
                        _inactive = True
                        continue
                    return c.get("id")
            except Exception:
                pass
        self.last_month_miss = "inactive" if _inactive else "missing"
        return None

    def _tick_size(self, contract_id: str):
        """contractId(예: CON.F.US.MGC.Q26)의 tickSize — Contract/search 메타데이터에서 조회(캐시),
        실패 시 심볼 루트로 폴백 테이블. 못 찾으면 None(정렬 없이 원가격 사용)."""
        if contract_id in self._ticks:
            return self._ticks[contract_id]
        parts = str(contract_id).split(".")
        sym = parts[3] if len(parts) >= 4 else str(contract_id)
        tick = None
        try:
            for c in self.search_contracts(sym, max_age=1800):
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

    def list_open_positions(self, acct_max_age=None) -> list[Position]:
        # acct_max_age: 진입 경로만 넘긴다 - 계좌 **목록**만 그 초 안의 값을 다시 쓰고, 포지션은 언제나 새로 읽는다.
        out: list[Position] = []
        for a in self._accounts(max_age=acct_max_age):
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
    def flatten_all(self, dry_run: bool = True, only=None) -> FlattenResult:
        """계좌 포지션 전부(only가 있으면 그 종목만) 시장가 청산 → 재조회 → **플랫으로 확인된 계약의** 잔여 주문만 취소.

        순서(오픈 준비 버그 조사 2026-10-06 G): 종전엔 청산 → 계좌 주문 **전부** 취소 → 재조회였다.
        closeContract가 실패해도(5xx·시간 초과·거절) 그 계약의 보호 손절까지 걷어 손절 없는 포지션이
        남았다(IBKR close_contract는 2026-09-30에 '청산 → 플랫 확인 → 취소'로 이미 고쳤다).
        이제 재조회에서 아직 열려 있는 계약과, 재조회 실패로 확인 못 한 청산 대상 계약의 주문은 손대지
        않는다. closeContract가 실패했어도 재조회가 플랫이면(그 사이 손절 체결 등) 남은 주문은 고아라 걷는다 -
        남기면 그 스탑이 나중에 혼자 체결돼 새 포지션을 연다. 포지션이 없던 계약의 고아 주문 정리는 종전 그대로다."""
        _refuse = refuse_flatten_without_accounts(self, dry_run)   # 빈 계좌 목록 = 로그인 전 계좌 청산 금지(2026-10-08 P0)
        if _refuse is not None:
            return _refuse
        def _mine(sym) -> bool:                       # only = 그 자산 종목만(2026-10-17 자산별 청산)
            return only is None or bool(only(str(sym or "")))
        plan = [p for p in self.list_open_positions() if _mine((p.raw or {}).get("contractId") or p.symbol)]
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run:
            return res

        def _k(p):                                   # (계좌, 계약) - 주문의 accountId/contractId와 같은 꼴
            return (str((p.raw or {}).get("_accountId", p.account_id)),
                    str((p.raw or {}).get("contractId") or p.symbol))
        failed = set()                               # closeContract가 재시도까지 실패한 (계좌, 계약)
        for pos in plan:
            err = None
            for _try in range(2):                    # 일시 오류 대비 1회 재시도(G)
                try:
                    # closeContract market-closes the entire position for {accountId, contractId}.
                    self._post("/api/Position/closeContract", {
                        "accountId": int(pos.raw["_accountId"]),
                        "contractId": pos.raw.get("contractId"),
                    })
                    err = None
                    break
                except Exception as e:
                    err = e
                    if _try == 0:
                        time.sleep(_CLOSE_RETRY_WAIT)
            if err is not None:
                failed.add(_k(pos))
                res.errors.append(f"{pos.account_name}/{pos.symbol}: {err}")
        # Confirm-after-act **먼저**: 재조회로 플랫을 확인한 뒤에만 그 계약의 주문을 걷는다.
        planned_keys = {_k(p) for p in plan}
        still = None
        try:
            still = [s for s in self.list_open_positions() if _mine((s.raw or {}).get("contractId") or s.symbol)]
            # 시장가 청산이 조회에 반영되기 전일 수 있다 - 청산 요청이 성공한 계약이 남아 보이면 한 번만 다시 본다.
            if any(_k(s) in planned_keys and _k(s) not in failed for s in still):
                time.sleep(_RECHECK_WAIT)
                still = [s for s in self.list_open_positions() if _mine((s.raw or {}).get("contractId") or s.symbol)]
            res.closed = [p for p in plan if not any(
                s.account_id == p.account_id and s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.account_name}/{p.symbol} "
                                  f"net={p.net_qty} (its working orders, incl. the stop, left in place)")
        except Exception as e:
            res.errors.append(f"post-flatten position re-check failed: {e} "
                              f"(working orders on planned contracts left in place)")
        # 주문을 남길 계약: 재조회에서 아직 열린 것. 재조회가 실패했으면 청산하려던 계약 전부.
        keep = {_k(s) for s in still} if still is not None else set(planned_keys)
        # "Flat" = no positions AND no working orders. Cancel resting orders on contracts confirmed flat
        # (e.g. a protective stop left behind after the position closed) so a leftover stop can't re-open
        # a position next session. Best-effort: failures are logged, never block the position close above.
        try:
            accts = self._accounts()
        except Exception as e:
            accts = []
            res.errors.append(f"open-order cleanup skipped: account read failed: {e}")
        for a in accts:
            aid = a.get("id")
            try:
                for o in self._open_orders(aid):
                    oid = o.get("id")
                    if not _mine(o.get("contractId")):
                        continue                     # 다른 종목의 주문(회원이 손으로 낸 것 포함)은 건드리지 않는다
                    if (str(aid), str(o.get("contractId"))) in keep:
                        continue                     # 열린(또는 확인 못 한) 계약의 보호 손절은 남긴다(G)
                    try:
                        self._cancel_order(aid, oid)
                        res.cancelled.append(f"{a.get('name', aid)}:{oid}")
                    except Exception as e:
                        res.errors.append(f"cancel order {oid} failed: {e}")
            except Exception as e:
                res.errors.append(f"{a.get('name', aid)}: open-order read failed: {e}")
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
            _epx = _px_entry_map(d.get("trades", []) or [])     # 청산 반턴마다 그 포지션의 진입가(하나뿐일 때만, R57 #7)
            for t in d.get("trades", []) or []:
                pnl = t.get("profitAndLoss")
                if pnl is None or t.get("voided"):
                    continue
                try:
                    ts = t.get("creationTimestamp") or ""
                    ts_ms = int(_parse_iso(ts).timestamp() * 1000)
                    out.append({
                        "tid": str(t.get("id") or ""),
                        "ts_ms": ts_ms,
                        "symbol": str(t.get("contractId") or ""),     # 예: CON.F.US.GCE.Q26
                        "pnl": float(pnl) - float(t.get("fees") or 0),
                        "direction": "LONG" if int(t.get("side") or 0) == 1 else "SHORT",
                        "acct": str(a.get("id") or ""),               # 계좌 id(정규화·닫힌계좌 판별)
                        "acct_name": str(a.get("name") or ""),
                        # 이 계좌의 체결가(트랙레코드 영수증, R57 #7, 2026-11-08): 청산가 = 이 청산 반턴의 체결가 그대로, 진입가 = 이 포지션의
                        # 진입 반턴 가격이 하나뿐일 때만(_px_entry_map). 모르면 None - 종전엔 둘 다 없어 금, 나스닥 영수증이 늘 신호 기준가였다.
                        "exit_px": pos_px(t.get("price")),
                        "entry_px": _epx.get(id(t)),
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

    # ── 평가 계좌 익절(하루 이익 상한 + 통과 목표, 대표 2026-10-14 "앱이 보다가 정리하는 방식 고") ─────────────────
    # 브로커에 익절 주문을 걸지 않는다(손절과 OCO가 아닌 독립 지정가는 한쪽 체결 뒤 남은 주문이 반대 포지션을 열 수 있다).
    # 앱(eqgui 평가 계좌 감시)이 오늘 실현 손익 + 열린 포지션 미실현으로 계좌의 오늘 손익을 보고, 닿으면 closeContract로 그 계좌만 닫는다.
    # 아래 두 메서드는 읽기 전용이고 새로 추가된 것이다 - 기존 진입·손절·청산 경로는 이것들을 부르지 않는다.
    def day_realized(self, account_id, start_iso: str, timeout=None) -> float:
        """start_iso 이후 이 계좌의 실현 순손익 = Σ profitAndLoss(반턴은 null→0) − Σ fees(진입 반턴 수수료 포함),
        voided 제외(POST /api/Trade/search). 실패하면 예외 - 호출측이 '모름'으로 다룬다(조용한 0 금지)."""
        d = self._post("/api/Trade/search", {"accountId": int(account_id), "startTimestamp": start_iso},
                       timeout=timeout)
        tot = 0.0
        for t in d.get("trades", []) or []:
            if t.get("voided"):
                continue
            tot += float(t.get("profitAndLoss") or 0.0) - float(t.get("fees") or 0.0)
        return tot

    def trades(self, account_id, start_iso: str, timeout=None) -> list:
        """이 계좌의 start_iso 이후 거래 기록 원본(POST /api/Trade/search) - [{id, creationTimestamp, profitAndLoss, fees, voided, ...}].
        평가 계좌 통과 익절의 '지금까지 최고일'(거래일별 손익의 최대) 재료다(3차 결정 B, 2026-10-14). 읽기 전용. 실패하면 예외 - 호출측이 '모름'으로
        다룬다."""
        d = self._post("/api/Trade/search", {"accountId": int(account_id), "startTimestamp": start_iso}, timeout=timeout)
        return [t for t in (d.get("trades", []) or []) if isinstance(t, dict)]

    def mark_price(self, contract_id, max_age_s: float = 120.0, timeout=None):
        """평가 계좌 감시용 시세(3차 리뷰 P0) = (가장 최근 1분봉 종가, 그 봉 시각 UTC 초) 또는 None. current_market_price(지정가 경로가 쓰는
        종전 함수, 그대로 둔다)와 같은 봉을 읽되 0 이하, 숫자 아님, max_age_s보다 오래된 봉(시장이 쉬는 동안 남은 몇 시간 전 봉)은 버린다 -
        잘못된 값 한 번으로 계좌를 닫지 않게. 실패하면 None(그 회차는 판단하지 않는다)."""
        import math as _m
        from datetime import datetime, timedelta, timezone
        try:
            now = datetime.now(timezone.utc)
            body = {"contractId": contract_id, "live": False,
                    "startTime": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "unit": 2, "unitNumber": 1, "limit": 3, "includePartialBar": True}
            d = (self._post("/api/History/retrieveBars", body, timeout=timeout) if timeout is not None
                 else self._post("/api/History/retrieveBars", body))
            bars = d.get("bars", d if isinstance(d, list) else []) or []
            best = None
            for b in bars:
                try:
                    ts = _parse_iso(b.get("t") or b.get("timestamp")).timestamp()
                    px = float(b.get("c"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if best is None or ts > best[1]:
                    best = (px, ts)
            if best is None:
                return None
            px, ts = best
            if not _m.isfinite(px) or px <= 0 or (now.timestamp() - ts) > float(max_age_s):
                return None
            return px, ts
        except Exception:
            return None

    def partial_close_contract(self, account_id, contract_id, size: int) -> dict:
        """그 계좌, 그 계약 포지션 가운데 size만 시장가로 닫는다(POST /api/Position/partialCloseContract {accountId, contractId,
        size} - gateway.docs.projectx.com 'Partially Close Positions'). 평가 계좌 감시가 회원이 손으로 더한 수량은 두고 앱 몫만 정리할 때
        쓴다(3차 리뷰 P0 '앱이 넣은 포지션만'). 실패하면 예외."""
        return self._post("/api/Position/partialCloseContract",
                          {"accountId": int(account_id), "contractId": contract_id, "size": int(size)})

    def open_positions(self, account_id, timeout=None) -> list:
        """이 계좌 하나의 열린 포지션 원본(POST /api/Position/searchOpen) - [{contractId, type, size, averagePrice, ...}],
        수량 0은 뺀다. list_open_positions와 달리 계좌 목록(Account/search)을 다시 읽지 않는다(5초 감시의 호출 수를 줄인다).
        실패하면 예외 - 호출측이 '모름'으로 다룬다."""
        d = self._post("/api/Position/searchOpen", {"accountId": int(account_id)}, timeout=timeout)
        out = []
        for p in d.get("positions", []) or []:
            try:
                if isinstance(p, dict) and int(p.get("size", 0) or 0) != 0:
                    out.append(p)
            except (TypeError, ValueError):
                continue
        return out
