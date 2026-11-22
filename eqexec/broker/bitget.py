"""Bitget adapter (USDT-FUTURES) — classic accounts via the v2 mix REST API, Unified Trading
Accounts (UTA) via the v3 REST API.

Flatten uses Bitget's flash close-all endpoint (classic POST /api/v2/mix/order/close-positions,
UTA POST /api/v3/trade/close-positions), which market-closes every open position for the product
type. Runs on the user's machine with their own Bitget API key/secret/passphrase. dry_run sends NO
orders. Signing (same for v2 and v3): base64(HMAC_SHA256(timestamp + METHOD + requestPath + body, secret)).

Account type (2026-11-11, a member upgraded to UTA and the v2 connection stopped working; Bitget:
UTA keys cannot call Classic endpoints). The adapter asks Bitget once which account type the key
belongs to and keeps the answer (per API key, for the life of the app process):
  GET /api/v3/account/settings answered 00000 with any accountMode (unified, hybrid, …) = UTA (v3 paths);
  error 25245 "The account is not the unified account mode" = classic (v2 paths, unchanged).
  Other answers (network, other error codes) = not known → classic path as before, asked again later.
  accountMode upgrading|switching = the account is changing type → nothing is sent, clear error.
Re-detection: a v3 call answering 25245 switches the key back to classic and the call is re-run
on v2 (25245 = rejected, nothing was placed) - unless a v3 write in that call was already accepted.
A classic key whose v2 call is rejected by Bitget, with no v2 write accepted in that call and a failed
result, is asked again (at most once a minute; at once while the type is unknown); if it is now UTA
the call is re-run on v3.
UTA entries carry no attached stop: after the fill the stop is placed as a TP/SL strategy order
(partial, qty = filled qty); if that fails twice the position is closed at market (see _u_place_entry).
Docs: https://www.bitget.com/api-doc/classic/uta-api-upgrade-guide (v2→v3 map),
      https://www.bitget.com/docs/catalog/account/account-settings (settings, set-leverage),
      https://www.bitget.com/docs/uta/error-code/restapi (25245).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json as _json
import math
import os
import re as _re
import threading
import time

import requests

from .base import BrokerAdapter, FlattenResult, Position, pos_px

_BASE = "https://api.bitget.com"
_TIMEOUT = (10, 30)
_PRODUCT = "USDT-FUTURES"

# ── 데모 트레이딩 이름(2026-11-13 대표 "빗겟은 왜 데모 계좌 잔고가 0이냐"): Bitget 데모 선물은 상품, 종목, 증거금 코인 이름이 다르다
# (공개 /api/v2/mix/market/contracts 실측: SUSDT-FUTURES = SBTCSUSDT, 증거금 SUSDT). 앱 --demo일 때만 _req 한 곳에서 보내는 값을
# 데모 이름으로, 받은 값을 원래 이름으로 바꾼다 - 위쪽 코드(심볼 비교, 손절, 청산)는 실계좌 이름 그대로 돈다. 실계좌 경로는 안 거친다.
_DEMO_PT = {"USDT-FUTURES": "SUSDT-FUTURES", "USDC-FUTURES": "SUSDC-FUTURES", "COIN-FUTURES": "SCOIN-FUTURES"}
_DEMO_PT_IN = {v: k for k, v in _DEMO_PT.items()}
_DEMO_COIN = {"USDT": "SUSDT", "USDC": "SUSDC", "BTC": "SBTC", "ETH": "SETH"}
_DEMO_COIN_IN = {v: k for k, v in _DEMO_COIN.items()}
_DEMO_COIN_KEYS = ("marginCoin", "coin", "quoteCoin", "baseCoin")


def _demo_sym_out(v):
    s = str(v or "")
    u = s.upper()
    if u.endswith("USDT") and not (u.startswith("S") and u.endswith("SUSDT")) and len(u) > 4:
        return "S" + s[:-4] + "SUSDT"
    return v


def _demo_sym_in(v):
    s = str(v or "")
    u = s.upper()
    if u.startswith("S") and u.endswith("SUSDT") and len(u) > 6:
        return s[1:-5] + "USDT"
    return v


def _demo_out(d):
    """보낼 params/body(dict) - 데모 이름으로. 다른 모양은 그대로."""
    if not isinstance(d, dict):
        return d
    o = {}
    for k, v in d.items():
        if k in ("productType", "category") and isinstance(v, str):
            v = _DEMO_PT.get(v, v)
        elif k == "symbol" and isinstance(v, str):
            v = _demo_sym_out(v)
        elif k in _DEMO_COIN_KEYS and isinstance(v, str):
            v = _DEMO_COIN.get(v, v)
        o[k] = v
    return o


def _demo_in(x):
    """받은 data - 원래 이름으로(목록, 사전 안쪽까지)."""
    if isinstance(x, list):
        return [_demo_in(i) for i in x]
    if isinstance(x, dict):
        o = {}
        for k, v in x.items():
            if k in ("productType", "category") and isinstance(v, str):
                v = _DEMO_PT_IN.get(v, v)
            elif k == "symbol" and isinstance(v, str):
                v = _demo_sym_in(v)
            elif k in _DEMO_COIN_KEYS and isinstance(v, str):
                v = _DEMO_COIN_IN.get(v, v)
            elif isinstance(v, (list, dict)):
                v = _demo_in(v)
            o[k] = v
        return o
    return x

# ── 계정 유형(클래식 v2 / 통합 거래 계정 UTA v3) 판별 상태 ─────────────────────────────
_NOT_UNIFIED = "25245"                 # UTA error table: "The account is not the unified account mode"
# 주문 결과를 모르는 코드(place-order 문서: "use clientOid to query the order details to confirm") -
# 이 코드나 네트워크 끊김, 5xx가 섞인 클래식 호출은 v3로 다시 보내지 않는다(이중 진입 방지).
_AMBIGUOUS = frozenset({"40010", "40725", "45001"})
_CHANGING_MODES = ("upgrading", "switching")
_HIST_MAX_MS = 90 * 86400 * 1000       # v3 history-position: 90일 창
_HIST_WIN_MS = 30 * 86400 * 1000 - 1000  # v3: startTime~endTime 30일 이하
_MODES: dict = {}                      # API 키 지문 → {"mode", "hold", "probe_at", "inconclusive_at"}
_MODES_LOCK = threading.Lock()
_SWITCH_REPROBE_S = 60                 # '바뀌는 중'이면 거래 길에서도 이만큼마다 한 번 다시 묻는다(검증 P2-2). 청산 길은 늘 묻는다.
# 앱이 건 트리거 손절(수익 쪽 손절)의 orderId 기록(검증 P2-1): 문서상 트리거 주문은 clientOid를 받지 않는다('Only valid when type=tpsl;
# not supported for other strategy types') - 표식 대신 orderId를 키 지문별로 파일에 둔다(재시작 뒤에도 앱 것만 옮기고 정리, 회원 트리거는 그대로).
_TRIG_PATH = None                      # 시험이 바꾼다. 없으면 ~/.eqexec/bitget_uta_triggers.json
_TRIG_LOCK = threading.Lock()


def _trig_path() -> str:
    return _TRIG_PATH or os.path.join(os.path.expanduser("~"), ".eqexec", "bitget_uta_triggers.json")
_TL = threading.local()                # 스레드별 호출 깊이와 클래식 거절 기록
_COID_BAD = _re.compile(r"[^.A-Z:/a-z0-9_-]")   # v3 clientOid: ^[\.A-Z\:/a-z0-9_-]{1,32}$


class BitgetApiError(RuntimeError):
    """Bitget이 답한 오류(HTTP 4xx/5xx 또는 code != 00000). 메시지는 종전 RuntimeError와 글자까지 같다."""

    def __init__(self, msg, code=None, http=None, path=""):
        super().__init__(msg)
        self.code = None if code is None else str(code)
        self.http = http
        self.path = path


class BitgetModeError(RuntimeError):
    """계정이 클래식 ↔ 통합 거래 계정으로 바뀌는 중(accountMode upgrading/switching) - 아무것도 보내지 않았다."""


_RAISE = object()                      # _dispatch on_error: 예외를 그대로 올린다
_FLIPPED = object()                    # _run_uta: 25245 → 클래식으로 다시


def _nu(e):
    """v3 본문의 except 안에서: 25245('통합 계정 아님')는 삼키지 않고 올려 _dispatch가 클래식으로 다시 돌게 한다."""
    if isinstance(e, BitgetApiError) and e.code == _NOT_UNIFIED:
        raise e


def _definitive(e) -> bool:
    """Bitget이 처리하고 거절했다고 확실한 오류인가(주문이 안 들어갔다). 네트워크, 5xx, 결과 모름 코드는 아니다."""
    return (isinstance(e, BitgetApiError) and e.http is not None and e.http < 500
            and e.code not in _AMBIGUOUS and e.code not in (None, "None", ""))


def _holdmode_reject(e) -> bool:
    """거절이 포지션 모드(posSide) 문제로 보이나(리뷰 P1-2). 업그레이드 안내 'Note 1': 헤지 모드에서 posSide를 빼면 거절된다 - 코드는
    적혀 있지 않다. UTA 오류 표에서 그 뜻에 맞는 25236 'Incorrect position open type', 또는 오류 글에 posSide가 나오는 경우(40017
    'Parameter verification failed {0}', 25200 '{0} validation error' 등)만 본다."""
    return isinstance(e, BitgetApiError) and (e.code == "25236" or "posside" in str(e).lower())


def _sl_form_reject(e) -> bool:
    """손절 본문 꼴(인자, 포지션 지정) 문제로 보이는 확실한 거절(3차 리뷰 P2-3): 40017 'Parameter verification failed', 25200 '{0}
    validation error', 40034 'Parameter {0} does not exist', 25236 'Incorrect position open type', 25238 '{0} do not assign values at the
    same', 또는 오류 글에 posSide / reduceOnly. 25601 'Position does not exist'(체결 직후 시차)는 꼴 문제가 아니라 같은 꼴로 다시 보낸다."""
    if not isinstance(e, BitgetApiError):
        return False
    m = str(e).lower()
    return e.code in ("40017", "25200", "40034", "25236", "25238") or "posside" in m or "reduceonly" in m or "reduce only" in m


_TRIG_SLIP = 0.02                      # 수익 쪽 손절(트리거 주문)의 지정가 여유: 롱 손절 = 트리거 × 0.98, 숏 = × 1.02


def _beyond_entry_reject(e) -> bool:
    """UTA 오류 표 25580/25582: 'For long(short) position TP/SL, the stop-loss trigger price must be less(greater) than the average entry
    price' - TP/SL 손절은 평단을 넘어 수익 쪽으로 갈 수 없다(최종 리뷰 B P1-1). 그때는 트리거 주문으로 옮긴다(_u_trig_move)."""
    return isinstance(e, BitgetApiError) and e.code in ("25580", "25582")


def _v3_unknown(path, code) -> bool:
    """v3 끝점의 '결과 모름' 코드(place-order 문서: 40010, 40725, 45001이면 clientOid로 결과를 확인하라)인가.
    v2(클래식)는 종전 메시지 그대로 둔다(클래식 동작 글자까지 같음 - 2026-11-11 리뷰 P2-6)."""
    return str(path or "").startswith("/api/v3/") and str(code) in _AMBIGUOUS


def _v3_unknown_msg(path, code, status, msg) -> str:
    """okx.py와 같은 모양('unknown: code=') - 앱의 _entry_err_unknown이 'HTTP 4xx'·'failed: code='를 확정 거절로 읽으므로 그 글자를 쓰지 않는다."""
    return f"Bitget {path} unknown: code={code} (status {status}) {msg or ''}".strip()[:300]


# 성공해도 상태를 바꾸지 않는 POST(조회) - '이번 호출에서 주문, 설정이 이미 들어갔나' 셈에서 뺀다
_READ_POSTS = frozenset({"/api/v3/account/max-open-available"})


def _order_err(e) -> dict:
    if isinstance(e, BitgetModeError):
        return {"error": f"not sent: {e}"}
    return {"error": str(e)}


def _entry_err(e) -> dict:
    """시장가 진입(place_entry)의 UTA 오류 모양: _order_err + stop_attached False(리뷰 P2-5 - 25245 뒤 'unknown: …' 등 어떤 오류도)."""
    out = _order_err(e)
    out["stop_attached"] = False
    return out


class BitgetBroker(BrokerAdapter):
    name = "bitget"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.BitgetCfg
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self.passphrase = getattr(cfg, "passphrase", "")
        self.product = getattr(cfg, "product_type", _PRODUCT)
        self.demo = bool(getattr(cfg, "demo", False))       # 데모 트레이딩(2026-11-13, 앱 --demo): 모든 요청에 paptrading: 1

    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        msg = f"{ts}{method.upper()}{path}{body}"
        return base64.b64encode(hmac.new(self.secret.encode(), msg.encode(),
                                         hashlib.sha256).digest()).decode()

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        ts = str(int(time.time() * 1000))
        # 데모 이름은 클래식 v2에만 있다(공개 조회 실측 2026-11-13: v3 category=SUSDT-FUTURES = 40034 '없는 값', v3는 데모에서도 USDT-FUTURES,
        # BTCUSDT). v3(통합 거래 계정, 계정 유형 판별 포함)는 원래 이름 + paptrading 헤더만.
        _demo = bool(getattr(self, "demo", False)) and str(path).startswith("/api/v2/")
        if _demo:                            # 데모 이름으로(서명 전에) - 위 _demo_out
            params, body = _demo_out(params), _demo_out(body)
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
        if getattr(self, "demo", False):
            headers["paptrading"] = "1"
        try:
            if method == "GET":
                r = requests.get(url, headers=headers, timeout=_TIMEOUT)
            else:
                r = requests.post(url, data=payload, headers=headers, timeout=_TIMEOUT)
        except Exception:
            self._note_v2(path, None)        # 결과 모름(타임아웃, 끊김) - 기록만, 예외는 종전 그대로
            raise
        if r.status_code >= 400:
            # HTTP 에러에도 Bitget은 본문에 원인 코드를 준다(예: 40012 passphrase 불일치,
            # 40018 IP 차단) — raise_for_status가 이걸 숨겨 진단 불가였음(대표 2026-08-09 400).
            try:
                _e = r.json() or {}
            except Exception:
                _e = {}
            if _v3_unknown(path, _e.get("code")):
                _err = BitgetApiError(_v3_unknown_msg(path, _e.get("code"), r.status_code, _e.get("msg")),
                                      _e.get("code"), r.status_code, path)
            else:
                _err = BitgetApiError(
                    f"Bitget {path} HTTP {r.status_code}: code={_e.get('code')} "
                    f"{_e.get('msg') or (r.text or '')[:160]}", _e.get("code"), r.status_code, path)
            self._note_v2(path, _err)
            raise _err
        try:
            d = r.json()
        except Exception:
            self._note_v2(path, None)
            raise
        # Bitget은 성공 시 항상 code="00000". str(None)=="None"을 허용 목록에 두면 code 없는
        # 변형 응답을 성공으로 오판(주문 미발주인데 '완료') → "None" 제거, 명시적 성공코드만 통과.
        if str(d.get("code")) not in ("00000", "0"):
            if _v3_unknown(path, d.get("code")):
                _err = BitgetApiError(_v3_unknown_msg(path, d.get("code"), r.status_code, d.get("msg")),
                                      d.get("code"), r.status_code, path)
            else:
                _err = BitgetApiError(f"Bitget {path} failed: code={d.get('code')} {d.get('msg')}",
                                      d.get("code"), r.status_code, path)
            self._note_v2(path, _err)
            raise _err
        self._note_ok(method, path)
        return _demo_in(d.get("data", d)) if _demo else d.get("data", d)

    def authenticate(self) -> None:
        self.list_open_positions()           # signed read validates key+passphrase

    # ══ 계정 유형 판별과 경로 고르기(2026-11-11 통합 거래 계정) ══════════════════════════════
    def _mstate(self) -> dict:
        """이 API 키의 판별 상태(프로세스 공유, 키 원문 대신 지문). 키가 없는 인스턴스(시험의 __new__)는 인스턴스 것."""
        k = str(getattr(self, "key", "") or "")
        if k:
            fp = hashlib.sha256(k.encode()).hexdigest()[:20]
            with _MODES_LOCK:
                return _MODES.setdefault(fp, {})
        st = self.__dict__.get("_mode_local")
        if st is None:
            st = self.__dict__["_mode_local"] = {}
        return st

    def _tl(self, create=True):
        d = getattr(_TL, "d", None)
        if d is None:
            d = _TL.d = {}
        s = d.get(id(self))
        if s is None and create:
            s = d[id(self)] = {"depth": 0, "fault": None, "ambig": False, "v2_post_ok": False, "v3_post_ok": False}
        return s

    def _note_v2(self, path, err) -> None:
        """클래식(v2) 호출이 거절되면 기록한다(바깥 _dispatch가 '혹시 UTA로 바뀌었나' 다시 묻는 재료). 동작은 바꾸지 않는다."""
        if not str(path or "").startswith("/api/v2/"):
            return
        s = self._tl(create=False)
        if s is None:
            return
        if err is None or not _definitive(err):
            s["ambig"] = True
        elif s.get("fault") is None:
            s["fault"] = err

    def _note_ok(self, method, path) -> None:
        """이번 호출에서 성공한 쓰기(POST)를 기록한다(리뷰 P1-1): 쓰기가 하나라도 들어간 호출은 다른 API로 다시 보내지 않는다(이중 주문 방지)."""
        if str(method).upper() != "POST" or path in _READ_POSTS:
            return
        s = self._tl(create=False)
        if s is None:
            return
        if str(path).startswith("/api/v2/"):
            s["v2_post_ok"] = True
        elif str(path).startswith("/api/v3/"):
            s["v3_post_ok"] = True

    def _cat(self) -> str:
        """v3 category(= v2 productType, 대문자)."""
        return str(getattr(self, "product", "") or _PRODUCT).upper()

    def _probe(self):
        """Bitget에 계정 유형을 묻는다 → ("uta"|"classic"|None, holdMode|None). None = 모름.
        accountMode upgrading/switching이면 BitgetModeError(아무것도 보내지 않는다)."""
        st = self._mstate()
        st["probe_fail"] = None              # 모름일 때 까닭: "net"(답을 못 받음) | "answered"(Bitget이 다른 코드로 답함)
        try:
            d = self._req("GET", "/api/v3/account/settings")
        except BitgetApiError as e:
            if e.code == _NOT_UNIFIED:
                return "classic", None
            if not _definitive(e):
                st["probe_fail"] = "net"
                return None, None
            # settings는 'UTA 관리(읽기)' 권한 - 거래 권한만 있는 키면 거래(읽기) 권한의 포지션 조회로 한 번 더 본다
            try:
                self._req("GET", "/api/v3/position/current-position", {"category": self._cat()})
                return "uta", None
            except BitgetApiError as e2:
                if e2.code == _NOT_UNIFIED:
                    return "classic", None
                st["probe_fail"] = "answered" if _definitive(e2) else "net"
                return None, None
            except Exception:
                st["probe_fail"] = "net"
                return None, None
        except Exception:
            st["probe_fail"] = "net"
            return None, None
        if not isinstance(d, dict):
            return None, None
        am = str(d.get("accountMode") or "").strip().lower()
        if am in _CHANGING_MODES:
            raise BitgetModeError(
                f"Bitget account is changing between Classic and Unified Trading Account (accountMode={am}) "
                f"- nothing was sent; try again in about a minute")
        if am:
            # unified, hybrid, 그 밖의 새 값도 UTA(리뷰 P2-5): v3 settings가 성공(00000)으로 답했다는 것 자체가 v3 계정이다 - 클래식은 25245로 답한다.
            # accountMode 칸이 아예 없는 성공 응답만 '모름'(문서의 응답에는 늘 있는 칸이다).
            return "uta", (str(d.get("holdMode") or "") or None)
        return None, None

    def _detect(self, force=False, exit_path=False) -> str:
        """판별해서 상태에 적고 쓸 경로를 돌려준다("uta"|"classic"). 모르면 "classic"(종전 경로)."""
        st = self._mstate()
        if not force:
            m = st.get("mode")
            if m in ("uta", "classic"):
                return m
            if st.get("switching") and not exit_path and time.time() - float(st.get("probe_at") or 0) < _SWITCH_REPROBE_S:
                # 마지막 판별이 '바뀌는 중'(upgrading, switching) - 60초 안에는 다시 묻지 않고 아무것도 보내지 않는다(검증 P2-2: 그 뒤에는 거래 길에서도
                # 한 번 다시 묻는다, 청산 길(포지션 조회, 청산)은 늘 다시 묻는다).
                raise BitgetModeError(
                    "Bitget account is changing between Classic and Unified Trading Account - nothing was sent; "
                    "the app checks again within a minute")
            if st.get("probe_at") and not st.get("switching"):
                # 한 번 물었는데 모름(네트워크) - 진입 같은 거래 길에서는 다시 묻지 않는다(최종 리뷰 E1 P2-10, 요청이 진입 앞에 끼지 않게).
                # 다시 묻는 곳은 healthcheck(연결 테스트, 사전점검, 라이브 시작, 연결 감시)뿐이다. 그때까지는 종전 경로(v2).
                return "classic"
        st["probe_at"] = time.time()
        try:
            mode, hold = self._probe()
        except BitgetModeError:
            st["mode"] = None                # 바뀌는 중 - 유형은 기억하지 않고 '바뀌는 중'만 기억한다(거래 길은 보내지 않는다)
            st["inconclusive_at"] = None
            st["switching"] = True
            raise
        if mode is not None:
            st["switching"] = False
        if mode is None:
            if not force or st.get("mode") not in ("uta", "classic"):
                st["mode"] = None
                st["inconclusive_at"] = time.time()
            return st.get("mode") or "classic"
        st["mode"] = mode
        st["inconclusive_at"] = None
        if mode == "uta" and hold:
            st["hold"] = hold
        return mode

    def account_type(self) -> str:
        """판별된 계정 유형: "uta" | "classic" | "unknown"(묻지 못함). 묻지 않는다 - 읽기만."""
        return self._mstate().get("mode") or "unknown"

    def _flip_classic(self):
        st = self._mstate()
        st["mode"] = "classic"
        st["inconclusive_at"] = None
        st["probe_at"] = time.time()

    def _run_uta(self, uta, a, kw, on_error, depth):
        try:
            return uta(*a, **kw)
        except BitgetApiError as e:
            if e.code != _NOT_UNIFIED:
                return self._fail(on_error, e)
            if depth:
                raise                        # 안쪽 호출 - 바깥 _dispatch가 클래식으로 처음부터 다시 돈다
            self._flip_classic()
            if (self._tl(create=False) or {}).get("v3_post_ok"):
                # 이 호출에서 v3 쓰기(주문, 손절, 레버리지)가 이미 들어갔다 - 클래식으로 처음부터 다시 돌리면 두 번이 된다(리뷰 P1-1).
                # 유형만 클래식으로 바꾸고 이 호출은 '결과 모름'으로 끝낸다(다음 호출부터 v2). 'failed: code=', 'HTTP 4xx'를 쓰지 않는다 -
                # 앱이 확정 거절로 읽으면 들어간 주문을 '거절'이라 말하게 된다.
                return self._fail(on_error, BitgetApiError(
                    f"unknown: Bitget answered code={e.code} (account is not unified) after a write in this call was "
                    f"already accepted - check the position and its stop at Bitget", e.code, e.http, e.path))
            return _FLIPPED
        except Exception as e:
            return self._fail(on_error, e)

    @staticmethod
    def _fail(on_error, e):
        if on_error is _RAISE:
            raise e
        return on_error(e) if callable(on_error) else on_error

    def _dispatch(self, classic, uta, a=(), kw=None, on_error=_RAISE, exit_path=False):
        """공개 메서드 하나를 계정 유형에 맞는 본문(_c_* = v2 종전 그대로, _u_* = v3)으로 보낸다.
        on_error = UTA 본문의 예외를 v2 본문과 같은 모양으로 바꾸는 값(None, False, dict 함수) - 클래식 본문은 손대지 않는다."""
        kw = kw or {}
        s = self._tl()
        depth = s["depth"]
        if depth == 0:
            s.update(fault=None, ambig=False, mode=None, v2_post_ok=False, v3_post_ok=False)
        s["depth"] = depth + 1
        try:
            mode = s.get("mode")
            if depth == 0 or mode is None:
                try:
                    mode = self._detect(exit_path=exit_path)
                except BitgetModeError as e:
                    return self._fail(on_error, e)
                s["mode"] = mode             # 한 호출 안(안쪽 호출 포함)은 한 경로만 쓴다 - v2와 v3를 섞지 않는다
            if mode == "uta":
                r = self._run_uta(uta, a, kw, on_error, depth)
                if r is not _FLIPPED:
                    return r
                s["fault"], s["ambig"], s["mode"] = None, False, "classic"   # 25245 → 클래식으로 처음부터(아무것도 들어가지 않았다)
            # 클래식 본문의 실패를 보고 다시 묻고 v3로 다시 보내던 길은 뺐다(최종 리뷰 E1 P2-10: 진입 앞에 판별 요청이 끼었다).
            # 클래식에서 UTA로 바꾼 키는 다음 healthcheck(사전점검, 연결 테스트, 라이브 시작)가 새로 물어 v3로 옮긴다.
            return classic(*a, **kw)
        finally:
            s["depth"] = depth
            if depth == 0:
                (getattr(_TL, "d", None) or {}).pop(id(self), None)

    # ── 공개 메서드: 경로만 고른다(본문 = _c_* v2 종전 그대로 / _u_* v3) ────────────────────
    def available_usdt(self, symbol="BTCUSDT"):
        return self._dispatch(self._c_available_usdt, self._u_available_usdt, (symbol,), on_error=None)

    def current_leverage(self, symbol):
        return self._dispatch(self._c_current_leverage, self._u_current_leverage, (symbol,), on_error=None)

    def max_leverage(self, symbol):
        return self._dispatch(self._c_max_leverage, self._u_max_leverage, (symbol,), on_error=None)

    def set_leverage(self, symbol, lev) -> bool:
        return self._dispatch(self._c_set_leverage, self._u_set_leverage, (symbol, lev))

    def list_open_positions(self) -> list[Position]:
        return self._dispatch(self._c_list_open_positions, self._u_list_open_positions, exit_path=True)

    def flatten_all(self, dry_run: bool = True, only=None) -> FlattenResult:
        return self._dispatch(self._c_flatten_all, self._u_flatten_all, (),
                              {"dry_run": dry_run, "only": only}, exit_path=True)

    def close_symbol(self, symbol, dry_run: bool = True) -> dict:
        return self._dispatch(self._c_close_symbol, self._u_close_symbol, (symbol,), {"dry_run": dry_run}, exit_path=True)

    def place_entry(self, *, symbol, side, size, stop_loss_price=None, custom_tag=None,
                    dry_run: bool = True, **_ignored) -> dict:
        return self._dispatch(self._c_place_entry, self._u_place_entry, (),
                              dict(_ignored, symbol=symbol, side=side, size=size,
                                   stop_loss_price=stop_loss_price, custom_tag=custom_tag,
                                   dry_run=dry_run), on_error=_entry_err)

    def place_limit_entry(self, *, symbol, side, size, price, stop_loss_price=None,
                          custom_tag=None, dry_run: bool = True) -> dict:
        return self._dispatch(self._c_place_limit_entry, self._u_place_limit_entry, (),
                              {"symbol": symbol, "side": side, "size": size, "price": price,
                               "stop_loss_price": stop_loss_price, "custom_tag": custom_tag,
                               "dry_run": dry_run}, on_error=_order_err)

    def place_limit_close(self, *, symbol, price, dry_run: bool = True) -> dict:
        return self._dispatch(self._c_place_limit_close, self._u_place_limit_close, (),
                              {"symbol": symbol, "price": price, "dry_run": dry_run})

    def cancel_order(self, symbol, order_id) -> bool:
        return self._dispatch(self._c_cancel_order, self._u_cancel_order, (symbol, order_id),
                              on_error=False)

    def current_market_price(self, symbol):
        return self._dispatch(self._c_current_market_price, self._u_current_market_price, (symbol,),
                              on_error=None)

    def stop_plans(self, symbol) -> list:
        return self._dispatch(self._c_stop_plans, self._u_stop_plans, (symbol,))

    def set_stop(self, symbol, stop_price):
        r = self._dispatch(self._c_set_stop, self._u_set_stop, (symbol, stop_price),
                           on_error=lambda e: {"error": str(e)})
        try:
            if isinstance(r, dict) and not r.get("error"):
                self._note_app_stop(symbol, stop_price)
        except Exception:
            pass
        return r

    def _note_app_stop(self, symbol, px) -> None:
        """앱(이 어댑터)이 마지막으로 건 손절 값을 키, 종목마다 기억한다(메모리만, 요청 없음 - 클래식 요청 그대로).
        회원이 거래소 화면에서 옮긴 손절은 이 어댑터를 거치지 않는다 - 앱이 '직접 옮겼다'고 잘못 보지 않게 쓴다(2026-11-11 3차 리뷰 P2-1)."""
        try:
            self._mstate().setdefault("app_sl", {})[self._symbol(symbol)] = float(px)
        except (TypeError, ValueError):
            pass

    def last_app_stop(self, symbol):
        """이 키로 앱이 마지막으로 건 손절 값(없으면 None). 앱의 손절 감시(_learn_seen_stop)가 읽는다."""
        v = (self._mstate().get("app_sl") or {}).get(self._symbol(symbol))
        return float(v) if v is not None else None

    def block_4h(self, symbol, end_utc):
        return self._dispatch(self._c_block_4h, self._u_block_4h, (symbol, end_utc), on_error=None)

    def closed_fills(self, start_ms: int) -> list[dict]:
        return self._dispatch(self._c_closed_fills, self._u_closed_fills, (start_ms,))

    def key_info(self) -> list:
        """API 키 사전 점검 경고(빈 리스트 = 이상 없음) - 연결 테스트와 진입 전 점검이 로그(⚠)와 회원 알림에 쓴다.
        클래식은 묻지 않고 [](종전과 같은 요청). UTA는 GET /api/v3/account/info(권한 없이 읽힘)의 permType, permissions로
        'UTA 관리'(잔고, 레버리지, 포지션 모드 읽기와 레버리지 설정), 'UTA 거래'(주문) 권한을 본다(리뷰 P1-2). KO / EN 한 줄씩."""
        if self.account_type() != "uta":
            return []
        try:
            return self._u_key_info()
        except Exception:
            return []

    def account_uid(self):
        """거래소 계정 번호(userId, 모르면 None) - 같은 거래소 계정을 쓰는 여러 PC가 진입 하나만 내게 하는 열쇠(최종 리뷰 1).
        UTA = GET /api/v3/account/info의 userId, 클래식 = GET /api/v2/spot/account/info의 userId(공식 문서의 응답 칸).
        앱은 연결 테스트, 사전점검, 라이브 시작의 작업 스레드에서만 부른다 - 진입 길에서는 부르지 않는다."""
        try:
            path = "/api/v3/account/info" if self.account_type() == "uta" else "/api/v2/spot/account/info"
            d = self._req("GET", path) or {}
            return str((d if isinstance(d, dict) else {}).get("userId") or "").strip() or None
        except Exception:
            return None

    def entry_info(self) -> list:
        return self._dispatch(self._c_entry_info, self._u_entry_info,
                              on_error=lambda e: [f"계정 유형 확인 실패: {e}"[:200],
                                                  "주문 마진모드 = 교차(cross) 자동 지정"])

    # ── 잔고-맞춤 자동 레버리지 (대표 2026-07-20, Bybit와 동일 계약) ────────────
    # 진입은 crossed 마진(place_entry 기본)이라 cross 레버리지를 조정한다. 상향만.
    def _account(self, symbol):
        return self._req("GET", "/api/v2/mix/account/account",
                         {"symbol": self._symbol(symbol), "productType": self.product,
                          "marginCoin": "USDT"})

    def _c_available_usdt(self, symbol="BTCUSDT"):
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

    def _c_current_leverage(self, symbol):
        """cross 레버리지 설정(account.crossedMarginLeverage)."""
        try:
            a = self._account(symbol) or {}
            v = a.get("crossedMarginLeverage")
            return float(v) if v not in (None, "") else None
        except Exception:
            return None

    def _c_max_leverage(self, symbol):
        """심볼 최대 레버리지(contracts.maxLever)."""
        try:
            d = self._req("GET", "/api/v2/mix/market/contracts",
                          {"productType": self.product, "symbol": self._symbol(symbol)})
            lst = d if isinstance(d, list) else []
            v = (lst[0] or {}).get("maxLever") if lst else None
            return float(v) if v else None
        except Exception:
            return None

    def _c_set_leverage(self, symbol, lev) -> bool:
        """cross 레버리지 설정(홀드사이드 불필요 — crossed는 롱숏 공통)."""
        self._req("POST", "/api/v2/mix/account/set-leverage",
                  body={"symbol": self._symbol(symbol), "productType": self.product,
                        "marginCoin": "USDT", "leverage": f"{float(lev):g}"})
        return True

    def ensure_leverage(self, symbol, size, price) -> dict:
        """클래식 = 종전 본문(_c_ensure_leverage) 그대로. UTA = 같은 수학이지만 지금 레버리지를 모르면 바꾸지 않는다(리뷰 P2-7)."""
        return self._dispatch(self._c_ensure_leverage, self._u_ensure_leverage, (symbol, size, price),
                              on_error=lambda e: {"error": str(e)[:140], "ab": None, "cur": None, "new": None})

    def _c_ensure_leverage(self, symbol, size, price) -> dict:
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

    def _c_list_open_positions(self) -> list[Position]:
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

    def _c_flatten_all(self, dry_run: bool = True, only=None) -> FlattenResult:
        # only = 그 자산 종목만(2026-10-17 자산별 청산): 상품 전체 flash close 대신 그 종목만 flash close(close_symbol과 같은 요청).
        _mine = (lambda sym: True) if only is None else (lambda sym: bool(only(str(sym or ""))))
        plan = [p for p in self.list_open_positions() if _mine(p.symbol)]
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        if only is None:
            try:
                # Flash close ALL positions for the product type (market).
                self._req("POST", "/api/v2/mix/order/close-positions", body={"productType": self.product})
            except Exception as e:
                res.errors.append(f"close-positions failed: {e}")
        else:
            for sym in sorted({p.symbol for p in plan}):
                try:
                    self._req("POST", "/api/v2/mix/order/close-positions", body={"symbol": sym, "productType": self.product})
                except Exception as e:
                    res.errors.append(f"close-positions {sym} failed: {e}")
        time.sleep(1)
        try:
            still = [s for s in self.list_open_positions() if _mine(s.symbol)]
            res.closed = [p for p in plan if not any(s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.symbol} net={p.net_qty}")
        except Exception as e:
            res.errors.append(f"post-flatten re-check failed: {e}")
        return res

    def _c_close_symbol(self, symbol, dry_run: bool = True) -> dict:
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

    def _c_place_entry(self, *, symbol, side, size, stop_loss_price=None, custom_tag=None,
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
    def _c_place_limit_entry(self, *, symbol, side, size, price, stop_loss_price=None,
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

    def _c_place_limit_close(self, *, symbol, price, dry_run: bool = True) -> dict:
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

    def _c_cancel_order(self, symbol, order_id) -> bool:
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

    def _c_current_market_price(self, symbol):
        try:
            d = self._req("GET", "/api/v2/mix/market/ticker",
                          {"symbol": self._symbol(symbol), "productType": self.product})
            row = d[0] if isinstance(d, list) else d
            return float(row.get("lastPr"))
        except Exception:
            return None

    def _c_stop_plans(self, symbol) -> list:
        """이 심볼에 걸린 손절 플랜 목록 [{orderId, price, triggerType, planType}]. 조회 실패는 예외로 올린다
        (호출측이 '판단 안 함'으로 처리). 2026-10-05: 앱 손절 감시가 포지션 데이터의 stopLoss 칸만 봐서, 진입 때
        첨부한 손절(플랜 주문)이 있어도 '미발견'으로 보고 손절을 하나 더 걸었다 - 플랜을 직접 본다."""
        sym = self._symbol(symbol)
        pend = self._req("GET", "/api/v2/mix/order/orders-plan-pending",
                         {"productType": self.product, "symbol": sym, "planType": "profit_loss"}) or {}
        rows = pend.get("entrustedList") or pend.get("list") or (pend if isinstance(pend, list) else [])
        out = []
        for r in rows:
            if (str(r.get("planType", "")).lower() in ("pos_loss", "loss_plan")
                    and str(r.get("symbol", "")).upper() == sym):
                try:
                    _p = float(r.get("triggerPrice") or 0) or None
                except (TypeError, ValueError):
                    _p = None
                out.append({"orderId": str(r.get("orderId")), "price": _p,
                            "triggerType": r.get("triggerType"), "planType": r.get("planType")})
        return out

    def _c_set_stop(self, symbol, stop_price):
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
        _mod_err = None
        if _sl:
            # 손절 플랜이 둘 이상이면 **전부** 같은 값으로(2026-10-05): 첫 번째만 옮기면 나머지가 옛 자리에 남아
            # '이동 완료' 로그와 거래소 화면이 갈린다. 하나라도 옮겼으면 성공, 실패분은 오류 문자열로 남긴다.
            _ok, _errs = 0, []
            for _row in _sl:
                try:
                    self._req("POST", "/api/v2/mix/order/modify-tpsl-order",
                              body={"orderId": str(_row.get("orderId")), "marginCoin": "USDT",
                                    "productType": self.product, "symbol": sym,
                                    "triggerPrice": _px,
                                    "triggerType": _row.get("triggerType") or "mark_price",
                                    "executePrice": "0"})          # 0 = 트리거 시 시장가
                    _ok += 1
                except Exception as _e1:
                    _errs.append(str(_e1))
            if _ok:
                return {"error": None, "moved": _ok, "partial_errors": _errs[:3]}
            try:
                raise RuntimeError("; ".join(_errs)[:300] or "modify failed")
            except Exception as e:
                # 🚨modify 실패로 끝내지 않는다(2026-09-21 실사고: 대표 BTC 보유 중 4시간 경계마다
                # "modify-tpsl-order HTTP 400"으로 트레일이 두 번 연속 멈췄고, 대표가 수동으로
                # 손절을 옮겨야 했다. 같은 시각 Bybit은 정상). Bitget은 pos_loss를 place로 다시
                # 올리면 기존 것을 대체하므로, 아래 등록 경로로 한 번 더 간다.
                # ⚠️취소를 먼저 하지 않는다 - 실패하면 무방비 구간이 생긴다. place가 실패해도
                # 기존 손절은 그대로 살아 있다(호출측이 '기존 손절 유지'로 로그).
                _mod_err = e
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
            return {"error": (f"modify: {_mod_err} / place: {e}" if _mod_err is not None
                              else f"place: {e}")}

    def _c_block_4h(self, symbol, end_utc):
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

    def _c_closed_fills(self, start_ms: int) -> list[dict]:
        """청산 완료 포지션의 실현손익(트랙레코드 푸시용, 파생값만).
        GET /api/v2/mix/position/history-position → [{tid, ts_ms, symbol, pnl, direction}].
        pnl = 순손익(_bg_net: netProfit, 없으면 pnl + 수수료·펀딩), fees = 수수료·펀딩 합. ⚠ 실키 대조 전."""
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
                    "pnl": _bg_net(r),
                    "fees": _bg_costs(r),
                    "direction": "LONG" if str(r.get("holdSide")).lower() == "long" else "SHORT",
                    # 진입, 청산 평균가(history-position이 함께 준다 - Bybit closed-pnl, OKX와 같은 재료, R57 #7 2026-11-08). 없으면 None
                    "entry_px": pos_px(r.get("openAvgPrice")),
                    "exit_px": pos_px(r.get("closeAvgPrice")),
                })
            except (TypeError, ValueError):
                continue
        return out

    def _c_entry_info(self) -> list:
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
        # 연결 테스트·라이브 시작마다 계정 유형을 새로 묻는다(회원이 앱을 켠 채 UTA로 바꾸거나 되돌린 경우).
        # 모르면(네트워크, 다른 코드) 아는 값 그대로 - 클래식이면 아래 v2 조회가 종전 오류를 그대로 보여 준다.
        self._detect(force=True)
        if self.account_type() == "unknown" and self._mstate().get("probe_fail") == "net":
            time.sleep(1.0)                  # 묻지 못함(네트워크) - 거래 길 밖인 여기서 한 번만 더 묻는다(최종 리뷰 E1 P2-10)
            self._detect(force=True)
        try:
            self.authenticate()
        except BitgetModeError:
            raise
        except Exception as e:
            if self.account_type() == "unknown" and self._mstate().get("probe_fail") == "net":
                # 계정 유형을 묻지 못했고(네트워크 등) 종전 v2 조회도 실패 - v2 오류 원문만 보이면 UTA 회원에게 엉뚱한 안내가 된다(리뷰 P2-4)
                raise RuntimeError(
                    "Bitget 계정 유형(클래식 / 통합 거래 계정)을 확인하지 못했습니다 - 잠시 뒤 연결 테스트를 다시 하십시오. "
                    "/ Could not determine the Bitget account type (classic or Unified Trading Account) - "
                    f"please run the connection test again in a moment. ({str(e)[:160]})") from e
            raise
        return True

    # ══ 통합 거래 계정(UTA, API v3) 본문 ═══════════════════════════════════════════════════
    # 모든 끝점, 인자, 응답 칸은 공식 문서에서만 가져왔다(2026-11-11 확인):
    #   주문 https://www.bitget.com/docs/catalog/trading/order-management
    #   포지션 https://www.bitget.com/docs/catalog/trading/position-management
    #   손절(전략 주문) https://www.bitget.com/docs/catalog/trading/strategy-trading
    #   시세 https://www.bitget.com/docs/catalog/market/market-data
    #   자산 https://www.bitget.com/docs/catalog/account/assets-balance
    #   설정, 레버리지 https://www.bitget.com/docs/catalog/account/account-settings
    # v3 본문의 except는 첫 줄에서 _nu(e)를 부른다 - 25245는 삼키지 않고 _dispatch로 올려 클래식으로 다시 돈다.

    def _hold(self):
        """마지막으로 읽은 포지션 모드(one_way_mode | hedge_mode | None=모름). 묻지 않는다."""
        return self._mstate().get("hold")

    def _u_settings(self) -> dict:
        """GET /api/v3/account/settings (UTA 관리 읽기). 읽으면 포지션 모드를 상태에 적는다."""
        d = self._req("GET", "/api/v3/account/settings") or {}
        d = d if isinstance(d, dict) else {}
        if d.get("holdMode"):
            self._mstate()["hold"] = str(d.get("holdMode"))
        return d

    @staticmethod
    def _coid(tag):
        """v3 clientOid 규칙(1~32자, 영문, 숫자, . : / _ -). 앱 표식 'EQ-AP-<ms>'는 그대로 통과. 비면 None(안 보냄)."""
        if not tag:
            return None
        s = _COID_BAD.sub("", str(tag))[:32]
        return s or None

    @classmethod
    def _coid_sfx(cls, base, sfx):
        """같은 앱 표식에 꼬리를 단 새 clientOid(32자 이하, 꼬리는 지키고 앞을 줄인다). base가 없으면 None.
        포지션 모드 재전송(-h), 손절 전략 주문(-sl, -sl2), 비상 청산(-x)마다 다른 값(리뷰 P2-8: 재전송이 같은 clientOid를 썼다)."""
        b = cls._coid(base)
        if not b:
            return None
        sfx = _COID_BAD.sub("", str(sfx))
        return (b[:32 - len(sfx)] + sfx) if sfx else b

    def _pstep3(self, sym: str) -> tuple:
        """v3 종목 가격 스텝: instruments의 priceMultiplier(스텝)와 pricePrecision(소수 자리).
        BTCUSDT = 0.1 / 1(2026-11-11 공개 시세로 확인). 조회 실패 시 v2와 같은 폴백."""
        _k = "v3:" + sym
        v = BitgetBroker._PSTEP.get(_k)
        if v:
            return v
        try:
            d = self._req("GET", "/api/v3/market/instruments", {"category": self._cat(), "symbol": sym})
            row = (d[0] if isinstance(d, list) and d else d) or {}
            place = int(row.get("pricePrecision") or 1)
            step = float(row.get("priceMultiplier") or 0) or 10.0 ** -place
            v = (step, place)
            BitgetBroker._QSTEP.setdefault(sym, self._qstep_of(row))     # 같은 줄로 수량 스텝도(조회 한 번)
        except Exception as e:
            _nu(e)
            v = (0.1, 1) if sym.startswith("BTC") else (0.01, 2)
        BitgetBroker._PSTEP[_k] = v
        return v

    def _snap_px3(self, sym: str, px) -> str:
        step, place = self._pstep3(sym)
        q = round(round(float(px) / step) * step, place)
        return f"{q:.{place}f}"

    _QSTEP: dict = {}                    # v3 종목 → (수량 스텝 또는 None, 소수 자리) 캐시(프로세스 수명)

    def _qstep3(self, sym: str) -> tuple:
        """v3 수량 스텝: instruments의 quantityMultiplier(스텝)와 quantityPrecision(소수 자리)(문서: 'qty must be a multiple of
        quantityMultiplier'). BTCUSDT = 0.0001 / 4(2026-11-11 공개 시세로 확인). 칸이 없거나 조회가 실패하면 (None, 8) = 스텝 모름 -
        그때는 거래소가 준 수량(체결 수량, 포지션 수량)을 반올림 없이 그대로 쓴다(그 값은 이미 거래소 스텝의 배수다)."""
        v = BitgetBroker._QSTEP.get(sym)
        if v:
            return v
        try:
            d = self._req("GET", "/api/v3/market/instruments", {"category": self._cat(), "symbol": sym})
            row = (d[0] if isinstance(d, list) and d else d) or {}
            v = self._qstep_of(row)
        except Exception as e:
            _nu(e)
            v = (None, 8)
        BitgetBroker._QSTEP[sym] = v
        return v

    @staticmethod
    def _qstep_of(row) -> tuple:
        if row.get("quantityPrecision") in (None, "") and row.get("quantityMultiplier") in (None, ""):
            return (None, 8)
        place = int(row.get("quantityPrecision") or 8)
        step = float(row.get("quantityMultiplier") or 0) or 10.0 ** -place
        return (step, place)

    def _fmt_qty3(self, sym: str, q) -> str:
        """체결에서 나온 수량(손절 수량, 청산 수량, 남은 수량)을 종목 수량 스텝으로 **내림**해 문자열로(3차 리뷰 P2-4: v2의 3자리
        _fmt_qty는 0.0065를 0.007(포지션보다 큼)로 만들었다). 내림이라 포지션을 넘지 않는다. 스텝을 모르면 거래소 값 그대로(8자리까지).
        진입 수량은 앱 사이징 그대로 _fmt_qty(종전과 같음)."""
        step, place = self._qstep3(sym)
        if not step:
            return (f"{float(q):.{place}f}".rstrip("0").rstrip(".")) or "0"
        n = int(math.floor(float(q) / step + 1e-9))
        v = round(n * step, place)
        return (f"{v:.{place}f}".rstrip("0").rstrip(".")) or "0"

    def _u_available_usdt(self, symbol="BTCUSDT"):
        """선물 교차 마진으로 새로 쓸 수 있는 USDT(리뷰 P2-7 - 자산 목록의 코인별 available은 선물 가용 마진이 아니다).
        ① POST /api/v3/account/max-open-available(UTA 거래 읽기 권한, 조회) {category, symbol, orderType=market, side=buy,
           marginMode=crossed} → available: 문서 'The futures represents the quantity of the quote coins'(이 종목 선물에 쓸 수 있는 USDT).
        ② 실패하면 GET /api/v3/account/assets의 effEquity(문서: 'The net value available for margin in spot and perpetual trades
           under cross-margin mode', USD) - imr(쓰고 있는 개시 증거금, USD). 둘 다 없으면 None(v2와 같은 계약: 실패 = None)."""
        try:
            d = self._req("POST", "/api/v3/account/max-open-available",
                          body={"category": self._cat(), "symbol": self._symbol(symbol), "orderType": "market",
                                "side": "buy", "marginMode": "crossed"}) or {}
            v = _f((d if isinstance(d, dict) else {}).get("available"))
            if v is not None:
                return v
        except Exception as e:
            _nu(e)
        d = self._req("GET", "/api/v3/account/assets") or {}
        d = d if isinstance(d, dict) else {}
        eff = _f(d.get("effEquity"))
        if eff is None:
            return None
        return max(eff - (_f(d.get("imr")) or 0.0), 0.0)

    def _u_lev_row(self, s: dict, sym: str):
        rows = [r for r in (s.get("symbolConfigList") or [])
                if str((r or {}).get("symbol") or "").upper() == sym
                and str(r.get("category") or self._cat()).upper() == self._cat()]
        rows.sort(key=lambda r: str(r.get("marginMode") or "") != "crossed")    # 교차 먼저
        return rows[0] if rows else None

    def _u_current_leverage(self, symbol):
        """settings.symbolConfigList의 이 종목 leverage(교차 우선). 줄이 없으면 그 종목 포지션의 leverage(current-position),
        그것도 없으면 None = 모름(_u_ensure_leverage는 모르면 바꾸지 않는다)."""
        sym = self._symbol(symbol)
        try:
            row = self._u_lev_row(self._u_settings(), sym)
        except Exception as e:
            _nu(e)
            row = None
        v = _f((row or {}).get("leverage"))
        if v:
            return v
        for p in self._u_list_open_positions():
            if p.symbol == sym and _f((p.raw or {}).get("leverage")):
                return _f(p.raw.get("leverage"))
        return None

    def _u_ensure_leverage(self, symbol, size, price) -> dict:
        """_c_ensure_leverage와 같은 수학(필요=명목/(가용×0.85), ×1.3, 상한 캡, 올리기만). 다른 점(리뷰 P2-7): 지금 레버리지를 모르면
        바꾸지 않는다 - 종전 수학은 모름을 0x로 보고 '올리기'로 레버리지를 실제보다 낮출 수 있었다. 반환 모양은 같다(로그 전용, 예외 없음)."""
        import math
        try:
            ab = self._u_available_usdt(symbol)
            if not ab or not price:
                return {"error": "잔고 조회 실패", "ab": ab, "cur": None, "new": None}
            notional = float(size) * float(price)
            need = notional / (ab * 0.85)
            cur = self._u_current_leverage(symbol)
            if cur is None:
                return {"ab": ab, "notional": notional, "cur": None, "new": None,
                        "error": f"지금 레버리지를 읽지 못해 바꾸지 않습니다(필요 {need:.1f}x) - Bitget에서 BTCUSDT 레버리지를 확인하십시오"}
            if cur >= need:
                return {"ab": ab, "notional": notional, "cur": cur, "new": None, "error": None}
            target = math.ceil(need * 1.3)
            mx = self._u_max_leverage(symbol)
            if mx:
                target = min(target, int(mx))
            if target <= cur:
                return {"ab": ab, "notional": notional, "cur": cur, "new": None,
                        "error": f"심볼 상한 {mx:g}x로도 부족(필요 {need:.1f}x) — 잔고를 늘려야 합니다"}
            self._u_set_leverage(symbol, target)
            return {"ab": ab, "notional": notional, "cur": cur, "new": target, "error": None}
        except Exception as e:
            _nu(e)
            return {"error": str(e)[:140], "ab": None, "cur": None, "new": None}

    def _u_max_leverage(self, symbol):
        d = self._req("GET", "/api/v3/market/instruments", {"category": self._cat(), "symbol": self._symbol(symbol)})
        lst = d if isinstance(d, list) else []
        v = (lst[0] or {}).get("maxLeverage") if lst else None
        return float(v) if v else None

    def _u_set_leverage(self, symbol, lev) -> bool:
        """POST /api/v3/account/set-leverage - 교차(crossed)는 posSide 불필요(isolated만 필수)."""
        self._req("POST", "/api/v3/account/set-leverage",
                  body={"category": self._cat(), "symbol": self._symbol(symbol),
                        "leverage": f"{float(lev):g}", "marginMode": "crossed"})
        return True

    def _u_list_open_positions(self) -> list[Position]:
        """GET /api/v3/position/current-position: total(수량), posSide(long/short, 단방향 모드에서도 온다).
        raw = 응답 줄 그대로(avgPrice = 평단, holdMode = 포지션 모드)."""
        d = self._req("GET", "/api/v3/position/current-position", {"category": self._cat()})
        rows = d.get("list") if isinstance(d, dict) else d
        out: list[Position] = []
        for p in (rows or []):
            size = float(p.get("total") or 0)
            if size == 0:
                continue
            net = size if str(p.get("posSide")).lower() == "long" else -size
            out.append(Position(account_id="bitget", account_name="bitget",
                                symbol=str(p.get("symbol")), net_qty=net, raw=p))
        if out and out[0].raw.get("holdMode"):
            self._mstate()["hold"] = str(out[0].raw.get("holdMode"))
        return out

    def _u_close_req(self, sym=None):
        """POST /api/v3/trade/close-positions: 시장가로 그 category(또는 종목)의 포지션 전부를 닫는다(flash close).
        응답의 줄별 code/msg 뜻은 문서에 없다 - 닫혔는지는 호출측이 포지션을 다시 읽어 확인한다(v2와 같은 원칙)."""
        body = {"category": self._cat()}
        if sym:
            body["symbol"] = sym
        return self._req("POST", "/api/v3/trade/close-positions", body=body)

    def _u_flatten_all(self, dry_run: bool = True, only=None) -> FlattenResult:
        _mine = (lambda sym: True) if only is None else (lambda sym: bool(only(str(sym or ""))))
        plan = [p for p in self.list_open_positions() if _mine(p.symbol)]
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if not dry_run and not plan:
            # 이미 플랫(앱 트리거 손절이 체결돼 닫힘 등) - 앱이 트리거를 건 종목에 남은 손절(트리거, 받침 TP/SL)을 정리한다(검증 P1-2, P2-1).
            # 두 번째 읽기로 플랫을 확인한 뒤에만(재검증 P2-2) - 아니면 아무것도 취소하지 않는다.
            _syms = sorted(s_ for s_ in self._trig_book() if _mine(s_))
            if _syms and self._u_flat_again(_syms):
                for sym in _syms:
                    res.cancelled.extend(self._u_cancel_left_stops(sym))
        if dry_run or not plan:
            return res
        if only is None:
            try:
                self._u_close_req()
            except Exception as e:
                _nu(e)
                res.errors.append(f"close-positions failed: {e}")
        else:
            for sym in sorted({p.symbol for p in plan}):
                try:
                    self._u_close_req(sym)
                except Exception as e:
                    _nu(e)
                    res.errors.append(f"close-positions {sym} failed: {e}")
        time.sleep(1)
        try:
            still = [s for s in self.list_open_positions() if _mine(s.symbol)]
            res.closed = [p for p in plan if not any(s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.symbol} net={p.net_qty}")
        except Exception as e:
            _nu(e)
            res.errors.append(f"post-flatten re-check failed: {e}")
        for sym in sorted({p.symbol for p in res.closed}):
            res.cancelled.extend(self._u_cancel_left_stops(sym))      # 플랫 확인된 종목만(최종 리뷰 B P2-4) - 기록은 cancelled에
        return res

    def _u_cancel_left_stops(self, sym) -> list:
        """플랫이 확인된 종목에 남은 손절 정리(최종 리뷰 B P2-4, OKX _cancel_left_stops와 같은 원칙 - 남은 손절이 다음 진입의 손절로
        잘못 읽히거나 트리거되지 않게): ①대기 중 TP/SL 줄 전부(포지션에 묶인 주문이라 플랫이면 쓸 데가 없다) ②이 어댑터가 수익 쪽 손절로
        건 트리거 주문(기억해 둔 orderId만 - 회원이 직접 건 트리거 주문은 건드리지 않는다). 최선 노력: 조회 한 번씩, 취소 최대 10건,
        실패는 기록만(예외를 올리지 않는다 - 청산 결과에 영향 없음). 반환 = 기록 줄(청산 결과의 cancelled, close_symbol의 stops_cancelled)."""
        out, rows = [], []
        try:
            rows = list(self._u_tpsl_rows(sym))
        except Exception as e:
            out.append(f"{sym} leftover stop check failed: {e}"[:200])
        _book = self._trig_book()
        _tids = set(_book.get(sym) or ())
        _gone = set()                        # 기록에서 지울 트리거: 취소했거나, 목록을 읽어 더는 대기 중이 아님을 확인한 것(재검증 P2-1)
        if _tids:
            try:
                _tr = [r for r in self._u_trig_rows(sym) if str(r.get("orderId")) in _tids]
                _gone = _tids - {str(r.get("orderId")) for r in _tr}
                rows += _tr
            except Exception as e:
                out.append(f"{sym} leftover trigger check failed: {e}"[:200])
        for r in rows[:10]:
            _oid = str(r.get("orderId") or "")
            if not _oid:
                continue
            try:
                self._req("POST", "/api/v3/trade/cancel-strategy-order", body={"orderId": _oid})
                out.append(f"{sym} leftover stop order {_oid} cancelled")
                if _oid in _tids:
                    _gone.add(_oid)
            except Exception as e:
                out.append(f"{sym} leftover stop order {_oid} cancel failed: {e}"[:200])
        if _gone:
            _left = _tids - _gone                # 못 읽었거나 취소가 실패한 트리거는 기록에 남긴다 - 다음 정리가 다시 본다
            if _left:
                _book[sym] = _left
            else:
                _book.pop(sym, None)
            self._trig_save()
        return out

    def _u_flat_again(self, syms) -> bool:
        """플랫을 두 번째 읽기로 확인(두 번 읽기 규칙, 재검증 P2-2): 1초 뒤 포지션을 다시 읽어 그 종목들이 없을 때만 True. 읽기 실패 = False
        (손절을 건드리지 않는다). 첫 읽기 한 번이 비어 보였다고 열린 포지션의 받침 손절과 트리거를 걷지 않게."""
        time.sleep(1)
        try:
            _s = set(syms)
            return not any(p.symbol in _s and p.net_qty for p in self._u_list_open_positions())
        except Exception:
            return False

    def _u_close_symbol(self, symbol, dry_run: bool = True) -> dict:
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            if dry_run or not self._u_flat_again([sym]):
                return {"closed": True, "had": 0}                  # 두 번째 읽기로 플랫을 확인하지 못하면 손절을 건드리지 않는다(재검증 P2-2)
            return {"closed": True, "had": 0, "stops_cancelled": self._u_cancel_left_stops(sym)}   # 플랫이어도 남은 손절 정리(검증 P2-1)
        if dry_run:
            return {"closed": False, "had": len(mine), "dry_run": True}
        try:
            self._u_close_req(sym)
        except Exception as e:
            _nu(e)
            return {"closed": False, "had": len(mine), "error": str(e)}
        for _ in range(6):
            time.sleep(1)
            try:
                if not any(p.symbol == sym for p in self.list_open_positions()):
                    return {"closed": True, "had": len(mine), "stops_cancelled": self._u_cancel_left_stops(sym)}
            except Exception as e:
                _nu(e)
        return {"closed": False, "had": len(mine), "error": "still open after close"}

    def _u_send_open(self, body: dict, long_side: bool):
        """여는 주문을 보낸다. 포지션 모드: 헤지면 posSide 필수, 단방향이면 뺀다(place-order 문서 'Open Position Logic').
        모드를 모르면(설정 읽기 권한 없음) 단방향으로 보낸다. Bitget이 확실히 거절하면(주문 안 들어감) 한 번만 고쳐 다시 보낸다:
          - 설정(holdMode)을 읽을 수 있으면 모드가 달랐을 때만.
          - 설정을 못 읽는 키(UTA 관리 읽기 권한 없음, 리뷰 P1-2)면 거절이 포지션 모드 문제로 보일 때만(_holdmode_reject) posSide를 붙여서.
        다시 보낼 때는 새 clientOid(앱 표식 + '-h', 리뷰 P2-8). 실제로 보낸 본문으로 body를 바꿔 둔다. 반환 (응답, 다시 보냈나)."""
        hold = self._hold()
        if hold == "hedge_mode":
            body["posSide"] = "long" if long_side else "short"
        try:
            return self._req("POST", "/api/v3/trade/place-order", body=body), False
        except Exception as e:
            _nu(e)
            if not _definitive(e):
                raise e
            try:
                fresh = str(self._u_settings().get("holdMode") or "") or None
                readable = True
            except Exception as e2:
                _nu(e2)
                fresh, readable = None, False
            if readable:
                if not fresh or fresh == (hold or "one_way_mode"):
                    raise e
                want_hedge = fresh == "hedge_mode"
            else:
                if "posSide" in body or not _holdmode_reject(e):
                    raise e
                want_hedge = True
            body2 = dict(body)
            if want_hedge:
                body2["posSide"] = "long" if long_side else "short"
            else:
                body2.pop("posSide", None)
            _nc = self._coid_sfx(body.get("clientOid"), "-h")
            if _nc:
                body2["clientOid"] = _nc
            body.clear()
            body.update(body2)                # 호출측이 마지막으로 보낸 본문(clientOid 포함)을 보게 - 결과 모름이면 이 clientOid로 확인한다
            res = self._req("POST", "/api/v3/trade/place-order", body=body)
            self._mstate()["hold"] = "hedge_mode" if want_hedge else "one_way_mode"
            return res, True

    # ── 진입 손절(리뷰 P1-3): 첨부 손절 대신 체결 뒤 바로 TP/SL 전략 주문 ─────────────────────────────
    # 왜: place-order의 stopLoss(첨부 손절)가 unfilled-strategy-orders(type=tpsl)에 수량과 함께 나오는지 공식 문서, 변경 이력,
    # 웹소켓 strategy-order 채널 문서, ccxt(bitget.ts의 UTA 경로)에서 확인하지 못했다(2026-11-11). 나오지 않으면 앱의 손절 감시가
    # '손절이 사라졌다'고 회원에게 잘못 알리고, X2+TR 트레일이 손절을 옮기지 못한다. 그래서 진입은 손절 없이 내고, 체결 수량을
    # 확인한 즉시 POST /api/v3/trade/place-strategy-order(type=tpsl, tpslMode=partial, qty=체결 수량, stopLoss, 시장가)로 손절을
    # 건다 - 전략 주문은 같은 문서 묶음의 unfilled-strategy-orders에 orderId, qty와 함께 나오고 modify-strategy-order로 옮긴다.
    # 손절이 한 번 다시 해도 안 걸리면 그 포지션을 바로 시장가로 닫는다(손절 없는 포지션을 남기지 않는다).
    # 노출 구간 = 체결 확인(order-info 1회, 보통) + 전략 주문 1회의 왕복(수백 ms). 재시도와 비상 청산까지 가도 몇 초.
    # 문서: https://www.bitget.com/docs/catalog/trading/strategy-trading#place-strategy-order
    #       https://www.bitget.com/docs/catalog/trading/order-management#get-order-details (cumExecQty, orderStatus)
    #       https://www.bitget.com/docs/catalog/trading/order-management#place-order (닫기: 헤지 = 반대 side + posSide, 단방향 = 반대 side + reduceOnly)
    def _u_fill_qty(self, oid, coid, sym, long_side):
        """방금 낸 시장가 주문의 체결 수량. GET /api/v3/trade/order-info(orderId 또는 clientOid)를 짧게(최대 약 2초) 본다 →
        (수량, 확정). filled/cancelled면 확정. 끝내 모르면 그 종목, 그 방향 포지션 수량(current-position) → (수량, False). 모두 실패 = (None, False)."""
        q = {"orderId": str(oid)} if oid else ({"clientOid": str(coid)} if coid else None)
        for _i in range(8):
            if q is None:
                break
            try:
                d = self._req("GET", "/api/v3/trade/order-info", q) or {}
                d = d if isinstance(d, dict) else {}
                st = str(d.get("orderStatus") or "").lower()
                if st in ("filled", "cancelled"):
                    return (_f(d.get("cumExecQty")) or 0.0), True
            except Exception as e:
                _nu(e)
            time.sleep(0.25)
        try:
            for p in self._u_list_open_positions():
                if p.symbol == sym and (p.net_qty > 0) == bool(long_side) and p.net_qty:
                    return abs(float(p.net_qty)), False
        except Exception as e:
            _nu(e)
        return None, False

    def _u_order_by_coid(self, coid):
        """결과를 모르는 주문을 clientOid로 확인(place-order 문서: 40010, 40725, 45001이면 clientOid로 주문 상세를 조회하라).
        GET /api/v3/trade/order-info?clientOid=… 를 0.5초 간격으로 최대 4번. → ("filled", 행) 체결분 있음 | ("none", 행) 체결 없이 끝남 |
        ("unknown", None) 끝내 모름(주문이 없다는 답도 '모름'으로 둔다 - 늦게 보일 수 있다)."""
        for _i in range(4):
            try:
                d = self._req("GET", "/api/v3/trade/order-info", {"clientOid": str(coid)}) or {}
                d = d if isinstance(d, dict) else {}
                st = str(d.get("orderStatus") or "").lower()
                q = _f(d.get("cumExecQty")) or 0.0
                if q > 0 and st in ("filled", "partially_filled", "cancelled"):
                    return "filled", d
                if st == "cancelled" and q == 0:
                    return "none", d
            except Exception as e:
                _nu(e)
            time.sleep(0.5)
        return "unknown", None

    def _u_sl_body(self, sym, qty, long_side, px, coid, hedge=False, form=None):
        """포지션 손절 전략 주문 본문(문서 place-strategy-order, type=tpsl의 partial): qty = 보호할 수량(BTC), side = 닫는 쪽,
        트리거 = 최종가(market, 문서 기본값과 같음), 시장가 실행. 닫는 쪽 지정은 문서의 side 설명(리뷰 P2-6a)대로:
        헤지 모드 = 'Close long: side=sell & posSide=long', 단방향 = 'Close long: side=sell & reduceOnly=yes'(reduceOnly는 tpsl 속성 목록에도 있다).
        ⚠ 실계좌 확인 전: ccxt의 UTA 손절은 단방향에서도 posSide를 보내고 reduceOnly를 보내지 않는다 - 문서의 단방향 규칙을 따랐다."""
        b = {"category": self._cat(), "symbol": sym, "type": "tpsl", "tpslMode": "partial",
             "qty": self._fmt_qty3(sym, qty), "side": "sell" if long_side else "buy"}
        if hedge or form == "posSide":
            b["posSide"] = "long" if long_side else "short"     # 단방향의 posSide 꼴 = 공식 tpsl 예시, ccxt와 같은 꼴(되받기용)
        else:
            b["reduceOnly"] = "yes"
        b.update({"stopLoss": px, "slTriggerBy": "market", "slOrderType": "market"})
        if coid:
            b["clientOid"] = coid
        return b

    def _u_side_qty(self, sym, long_side):
        """그 종목, 그 방향의 지금 포지션 수량(0 = 없음). 읽기 실패 = None(모름)."""
        try:
            return sum(abs(float(p.net_qty)) for p in self._u_list_open_positions()
                       if p.symbol == sym and p.net_qty and (p.net_qty > 0) == bool(long_side))
        except Exception as e:
            _nu(e)
            return None

    def _u_our_stops(self, sym, coid, qty, long_side):
        """지금 걸려 있는 이 진입의 손절 줄들(리뷰 P2-2): clientOid가 '표식-sl'로 시작하는 줄(-sl, -sl2), 없으면 이 포지션을 다 덮는
        손절(stopLoss 있음, qty ≥ 체결 수량, 방향이 맞거나 적혀 있지 않음). 반환 (우리 줄 목록, 덮는 줄 목록). 읽기 실패 = 예외."""
        pre = self._coid_sfx(coid, "-sl") if coid else None
        ours, cover = [], []
        for r in self._u_tpsl_rows(sym):
            if (_f(r.get("stopLoss")) or 0) <= 0:
                continue
            if pre and str(r.get("clientOid") or "").startswith(pre):
                ours.append(r)
                continue
            _ps = str(r.get("posSide") or "").lower()
            if (_f(r.get("qty")) or 0) >= float(qty) - 1e-12 and (not _ps or _ps == ("long" if long_side else "short")):
                cover.append(r)
        return ours, cover

    def _u_dedup_keep(self, ours, keep_id=None):
        """우리 손절 둘 이상에서 남길 줄(3차 리뷰 P2-2): ①성공한 호출이 돌려준 orderId ②없으면 대기(pending) 상태 줄 중 먼저 만든 것
        (submitting보다 pending - 문서 상태: pending = 'Waiting to be executed', submitting = 'Submitting'). ③어느 것이 살아 있는지 모르면 None."""
        if keep_id:
            for r in ours:
                if str(r.get("orderId")) == str(keep_id):
                    return r
        live = [r for r in ours if str(r.get("status") or "").lower() == "pending"]
        if live:
            return sorted(live, key=lambda r: (_f(r.get("createdTime")) or 0, str(r.get("orderId") or "")))[0]
        return None

    def _u_dedup_stops(self, ours, keep_id=None):
        """우리 손절이 둘 이상이면(재시도가 멱등하지 않았을 때 - 문서: 'not fully guaranteed') 남길 줄(_u_dedup_keep) 하나만 두고 나머지를
        취소한다. POST /api/v3/trade/cancel-strategy-order {orderId}(문서 strategy-trading#cancel-strategy-order). 남길 줄을 모르면 아무것도
        취소하지 않는다(무방비가 되지 않게). 우리 clientOid가 아닌 줄은 건드리지 않는다. 반환 (남긴 줄 또는 None, 취소한 수)."""
        keep = self._u_dedup_keep(ours, keep_id)
        if keep is None:
            return None, 0
        n = 0
        for r in ours:
            if r is keep:
                continue
            try:
                self._req("POST", "/api/v3/trade/cancel-strategy-order", body={"orderId": str(r.get("orderId"))})
                n += 1
            except Exception as e:
                _nu(e)
        return keep, n

    def _u_sl_forms(self, hedge):
        """손절 본문 꼴 차례. 헤지 = posSide 하나(문서). 단방향 = 이 키에서 먹힌 꼴 먼저, 처음이면 문서의 side + reduceOnly, 되받기는
        posSide 꼴(공식 tpsl 예시와 ccxt) - 3차 리뷰 P2-3: 문서 꼴이 실제로 안 먹히면 단방향 진입이 모두 비상 청산으로 끝난다."""
        if hedge:
            return ["hedge"]
        first = self._mstate().get("sl_form_1w") or "reduceOnly"
        return [first, "posSide" if first == "reduceOnly" else "reduceOnly"]

    def _u_form_ok(self, form):
        if form in ("reduceOnly", "posSide"):
            self._mstate()["sl_form_1w"] = form

    def _u_place_sl(self, sym, qty, long_side, px, coid, hedge=False):
        """손절 전략 주문을 건다. 실패하면 0.5초 뒤 한 번 더: 결과 모름이면 같은 본문, 같은 clientOid(문서: tpsl clientOid 6시간 멱등),
        확실한 거절이면 새 clientOid(-sl2), 그 거절이 본문 꼴(인자, 포지션 지정) 문제로 보이면 단방향은 다른 꼴로(_u_sl_forms, 먹힌 꼴은 키마다 기억).
        결과 모름이 섞였거나 25212(같은 clientOid), 25595(부분 손절 합 > 포지션 = 이미 덮고 있다)면 목록에서 이 진입의 손절을 찾아 있으면
        성공으로 본다(리뷰 P2-2), 둘 이상이면 하나만 남긴다(남길 줄을 모르면 그대로).
        반환 (orderId 또는 None, 마지막 오류 문자열 또는 None, '있을 수 있음' - 목록을 못 읽어 확인 못 했는데 25595/결과 모름이었다)."""
        _c1 = self._coid_sfx(coid, "-sl")
        forms = self._u_sl_forms(hedge)
        fi = 0
        last, amb, saw = None, False, set()
        for attempt in range(2):
            form = forms[min(fi, len(forms) - 1)]
            body = self._u_sl_body(sym, qty, long_side, px, _c1, hedge, None if form == "hedge" else form)
            try:
                r = self._req("POST", "/api/v3/trade/place-strategy-order", body=body) or {}
                oid = str((r if isinstance(r, dict) else {}).get("orderId") or "") or "?"
                self._u_form_ok(form)
                if amb:                                   # 앞 시도가 결과 모름이었다 - 둘 다 들어갔을 수 있다
                    try:
                        ours, _cv = self._u_our_stops(sym, coid, qty, long_side)
                        if len(ours) > 1:
                            self._u_dedup_stops(ours, keep_id=oid)      # 이 호출이 돌려준 줄을 남긴다
                    except Exception as e4:
                        _nu(e4)
                return oid, None, False
            except Exception as e:
                _nu(e)
                last = e
                if not _definitive(e):
                    amb = True
                elif isinstance(e, BitgetApiError):
                    saw.add(str(e.code))
                    _c1 = self._coid_sfx(coid, "-sl2")
                    if _sl_form_reject(e) and len(forms) > 1:
                        fi += 1                           # 다른 꼴로 한 번
            if attempt == 0:
                time.sleep(0.5)
        # 두 번 다 안 됐다 - 닫기 전에 이미 걸린 손절이 있는지 본다(결과 모름으로 들어갔거나, 25212/25595가 '이미 있다'는 뜻일 때)
        maybe = amb or bool(saw & {"25212", "25595"})
        if maybe:
            for _i in range(2):
                try:
                    ours, cover = self._u_our_stops(sym, coid, qty, long_side)
                except Exception as e5:
                    _nu(e5)
                    return None, str(last)[:300], True    # 목록을 못 읽었다 - 손절이 있을 수 있어 닫지 않는다
                if ours:
                    keep = ours[0] if len(ours) == 1 else self._u_dedup_stops(ours)[0]
                    if keep is None:                     # 둘 이상인데 어느 것이 살아 있는지 모른다 - 취소하지 않고 첫 줄을 알린다
                        keep = ours[0]
                    return str(keep.get("orderId") or "?"), None, False
                if cover:
                    return str(cover[0].get("orderId") or "?"), None, False
                time.sleep(0.5)
        return None, str(last)[:300], False

    def _u_close_fill(self, sym, qty, long_side, hedge, coid):
        """비상 청산: 방금 연 수량만 시장가 reduce-only로 닫는다(place-order 문서의 닫기 규칙). 보낸 뒤마다 포지션을 다시 읽어
        그 방향이 비었는지 본다(리뷰 P2-3: 시장가는 기본 2% slippageTolerance라 일부만 닫힐 수 있고, 결과 모름으로 실제 닫혔는데
        다음 시도가 25231로 거절될 수도 있다). 남았으면 남은 수량으로 한 번 더. reduce-only라 포지션보다 크게 닫지 않는다.
        반환 (남은 수량: 0 = 비었음, None = 확인 못 함, 마지막 오류 문자열 또는 None)."""
        last, q, rem = None, float(qty), None
        for attempt in range(2):
            b = {"category": self._cat(), "symbol": sym, "side": "sell" if long_side else "buy",
                 "orderType": "market", "qty": self._fmt_qty3(sym, q)}
            if hedge:
                b["posSide"] = "long" if long_side else "short"
            else:
                b["reduceOnly"] = "yes"
            _c = self._coid_sfx(coid, "-x" if attempt == 0 else "-x2")
            if _c:
                b["clientOid"] = _c
            try:
                self._req("POST", "/api/v3/trade/place-order", body=b)
            except Exception as e:
                _nu(e)
                last = e
            for _i in range(3):                           # 체결이 포지션에 비치기까지 잠깐 - 0.5초 간격 세 번
                time.sleep(0.5)
                rem = self._u_side_qty(sym, long_side)
                if rem == 0:
                    return 0.0, (str(last)[:300] if last else None)
            if rem:
                q = rem
        return rem, (str(last)[:300] if last else None)

    def _u_place_entry(self, **kw) -> dict:
        """UTA 진입의 모든 오류 결과에 stop_attached: False(리뷰 P2-5) - 손절을 주문에 붙이지 않는 경로라, 앱이 어떤 '모름' 오류에서도
        '손절은 주문에 붙어 있다'고 말하지 않게. 본문은 _u_place_entry_core."""
        out = self._u_place_entry_core(**kw)
        if isinstance(out, dict) and out.get("error"):
            out["stop_attached"] = False
        return out

    def _u_place_entry_core(self, *, symbol, side, size, stop_loss_price=None, custom_tag=None,
                            dry_run: bool = True, **_ignored) -> dict:
        """POST /api/v3/trade/place-order 시장가 진입(손절을 주문에 붙이지 않는다) → 체결 수량 확인 → 손절 전략 주문(위 설명, 리뷰 P1-3).
        마진 모드는 보내지 않는다(문서: 안 보내면 교차). 수량 단위 = BTC(USDT-FUTURES).
        반환(v2와 같은 열쇠): 성공 {entry, stop: True, stop_error: None, stop_order_id, filled_qty}.
        손절을 못 걸어 닫았으면 {error: 'entry closed: …', entry, closed: True}(앱은 '주문 안 남음'으로 본다).
        닫기까지 실패하면 {entry, stop: False, stop_error: '… open without a stop'}(앱의 손절 감시가 다시 건다).
        체결 없이 끝난 시장가 = {error: 'not filled: …'}."""
        sym = self._symbol(symbol)
        _s = str(side).upper()
        if _s not in ("LONG", "SHORT"):
            return {"error": f"invalid side: {side!r} (expected LONG/SHORT)"}
        _long = _s == "LONG"
        bside = "buy" if _long else "sell"
        qty = self._fmt_qty(size)
        if float(qty) <= 0:
            return {"error": "qty<=0"}
        body = {"category": self._cat(), "symbol": sym, "side": bside, "orderType": "market", "qty": qty}
        _coid = self._coid(custom_tag)
        if _coid:
            body["clientOid"] = _coid
        _sl = self._snap_px3(sym, stop_loss_price) if stop_loss_price else None
        if dry_run:
            if self._hold() == "hedge_mode":
                body["posSide"] = "long" if _long else "short"
            out = {"would_place": body, "would_place_stop": _sl}
            if _sl:
                out["would_place_sl"] = self._u_sl_body(sym, qty, _long, _sl, self._coid_sfx(_coid, "-sl"),
                                                        "posSide" in body)
            return out
        # 최종 확인 P2-1(2026-11-11): 결과 모름으로 끝나 늦게 체결된 진입(미확인 진입 감시가 찾음)은 _u_new_entry를 거치지 않는다 - 보내기 직전에
        # 진입 시각을 적어 둬야 그 전 플랫 구간에 남은 트리거를 이 포지션의 손절로 보지 않는다(_trig_current). 네트워크 호출 없음.
        self._mstate().setdefault("entry_at", {})[sym] = time.time()
        if self._trig_book().get(sym):
            self._trig_save()
        try:
            res, again = self._u_send_open(body, _long)
        except Exception as e:
            _nu(e)
            if _definitive(e) or not body.get("clientOid"):
                out = {"error": str(e)}
                if _sl and not _definitive(e):
                    out["stop_attached"] = False      # 결과 모름 + 손절은 체결 뒤에 건다 - 앱이 '손절은 주문에 붙어 있다'고 말하지 않게
                return out
            # 결과 모름(타임아웃, 5xx, 40010/40725/45001): place-order 문서대로 clientOid로 주문을 확인한다(리뷰 P1-3 뒤처리).
            got, d = self._u_order_by_coid(body["clientOid"])
            if got == "none":
                return {"error": f"not filled: market order {body['clientOid']} ended without a fill (checked after: {str(e)[:120]})"}
            if got != "filled":
                # 주문 조회로도 모르면 포지션을 본다: 진입 전에 그 종목 잔여를 닫았으니(앱 진입 경로) 그 방향 포지션이 있으면 이 주문이다 -
                # 손절 없는 포지션을 남기지 않게 그 수량에 손절을 건다. 포지션도 없으면 '모름' 그대로(들어가지 않았을 가능성이 크다).
                try:
                    _pq = next((abs(float(p.net_qty)) for p in self._u_list_open_positions()
                                if p.symbol == sym and p.net_qty and (p.net_qty > 0) == _long), None)
                except Exception as e2:
                    _nu(e2)
                    _pq = None
                if not _pq:
                    out = {"error": str(e)}
                    if _sl:
                        out["stop_attached"] = False
                    return out
                d = {"orderId": None}
            res, again = {"orderId": d.get("orderId"), "clientOid": body["clientOid"]}, False
        _hedge = "posSide" in body
        if not _sl:
            out = {"entry": res, "stop": False, "stop_error": "no stop provided"}
            if again:
                out["hedge_mode"] = _hedge
            return out
        oid = (res or {}).get("orderId") if isinstance(res, dict) else None
        fq, final = self._u_fill_qty(oid, body.get("clientOid"), sym, _long)
        if final and not fq:
            return {"error": f"not filled: market order {oid} ended without a fill - no position, no stop needed",
                    "entry": res}
        _q = fq if fq else float(qty)
        self._u_new_entry(sym)               # 그 전 트리거 손절은 이 포지션 것이 아니다 - 새 손절을 걸기 전에 정리(재검증 P2-1)
        _base = _coid or body.get("clientOid")
        sl_id, sl_err, maybe = self._u_place_sl(sym, _q, _long, _sl, _base, _hedge)
        if sl_id:
            out = {"entry": res, "stop": True, "stop_error": None, "stop_order_id": sl_id, "filled_qty": _q}
            if again:
                out["hedge_mode"] = _hedge
            return out
        if maybe:
            # 손절이 걸렸을 수 있는데(결과 모름, 25595) 목록을 못 읽었다 - 닫지 않는다(리뷰 P2-2). 앱의 손절 감시가 확인하고 없으면 건다.
            return {"entry": res, "stop": False, "filled_qty": _q,
                    "stop_error": (f"stop not confirmed ({sl_err}) - a stop may already be in place, so the position was "
                                   f"not closed; the stop watch will check and place one if it is missing")}
        rem, cl_err = self._u_close_fill(sym, _q, _long, _hedge, _base)
        if rem == 0:
            return {"error": f"entry closed: stop could not be placed ({sl_err}); the position was closed at market",
                    "entry": res, "closed": True}
        if rem is None:
            return {"entry": res, "stop": False, "filled_qty": _q,
                    "stop_error": (f"stop could not be placed ({sl_err}) and the market close could not be confirmed "
                                   f"({cl_err or 'position read failed'}) - the position may be open without a stop")}
        return {"entry": res, "stop": False, "filled_qty": rem,
                "stop_error": (f"stop could not be placed ({sl_err}) and the market close left {self._fmt_qty3(sym, rem)} open "
                               f"({cl_err or 'partly filled'}) - the position is open without a stop; the stop watch will place one")}

    def _u_place_limit_entry(self, *, symbol, side, size, price, stop_loss_price=None,
                             custom_tag=None, dry_run: bool = True) -> dict:
        """post_only 지정가 진입(+손절 첨부). timeInForce=post_only(v2 force=post_only).
        ⚠ 이 경로만 첨부 손절(stopLoss)을 그대로 쓴다: 2026-08-03 전면 시장가 전환 뒤 옛 신호(exec_policy limit_then_market) 호환으로만 남은
        경로이고, 체결 확인이 앱(_exec_entry_limit의 포지션 수량 확인)에 있어 여기서 체결 뒤 손절을 걸 자리가 깔끔하지 않다(리뷰 P1-3).
        첨부 손절이 전략 주문 목록에 안 나오면 앱의 손절 감시가 '없음'으로 보고 set_stop으로 부분(partial) 손절을 새로 건다."""
        sym = self._symbol(symbol)
        _s = str(side).upper()
        if _s not in ("LONG", "SHORT"):
            return {"error": f"invalid side: {side!r} (expected LONG/SHORT)"}
        body = {"category": self._cat(), "symbol": sym, "side": "buy" if _s == "LONG" else "sell",
                "orderType": "limit", "price": self._snap_px3(sym, price), "qty": self._fmt_qty(size),
                "timeInForce": "post_only"}
        _coid = self._coid(custom_tag)
        if _coid:
            body["clientOid"] = _coid
        if stop_loss_price:
            body["stopLoss"] = self._snap_px3(sym, stop_loss_price)
            body["slOrderType"] = "market"
        if dry_run:
            if self._hold() == "hedge_mode":
                body["posSide"] = "long" if _s == "LONG" else "short"
            return {"would_place": body}
        try:
            r, _again = self._u_send_open(body, _s == "LONG")
            return {"order_id": (r or {}).get("orderId")}
        except Exception as e:
            _nu(e)
            return {"error": str(e)}

    def _u_place_limit_close(self, *, symbol, price, dry_run: bool = True) -> dict:
        """reduce-only post_only 지정가 청산(전량). 단방향 = 반대 side + reduceOnly=yes, 헤지 = 반대 side + posSide
        (place-order 문서 'Open Position Logic'). 모드는 포지션 줄의 holdMode."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"flat": True}
        pos = mine[0]
        body = {"category": self._cat(), "symbol": sym,
                "side": "sell" if pos.net_qty > 0 else "buy",
                "orderType": "limit", "price": self._snap_px3(sym, price),
                "qty": self._fmt_qty(abs(pos.net_qty)), "timeInForce": "post_only"}
        hold = str((pos.raw or {}).get("holdMode") or self._hold() or "one_way_mode")
        if hold == "hedge_mode":
            body["posSide"] = "long" if pos.net_qty > 0 else "short"
        else:
            body["reduceOnly"] = "yes"
        if dry_run:
            return {"would_place": body}
        try:
            r = self._req("POST", "/api/v3/trade/place-order", body=body)
            return {"order_id": (r or {}).get("orderId")}
        except Exception as e:
            _nu(e)
            return {"error": str(e)}

    def _u_cancel_order(self, symbol, order_id) -> bool:
        try:
            self._req("POST", "/api/v3/trade/cancel-order",
                      body={"orderId": str(order_id), "category": self._cat()})
            return True
        except Exception as e:
            _nu(e)
            return False

    def _u_current_market_price(self, symbol):
        d = self._req("GET", "/api/v3/market/tickers", {"category": self._cat(), "symbol": self._symbol(symbol)})
        row = d[0] if isinstance(d, list) else d
        return float(row.get("lastPrice"))

    def _u_tpsl_rows(self, sym: str) -> list:
        """GET /api/v3/trade/unfilled-strategy-orders?category=…&type=tpsl → 이 종목의 대기 중 TP/SL 줄.
        (문서: 요청 인자는 category, type뿐 - 종목은 응답에서 거른다. 끝난 상태 success/failed/cancelled는 뺀다.)"""
        d = self._req("GET", "/api/v3/trade/unfilled-strategy-orders", {"category": self._cat(), "type": "tpsl"})
        rows = d if isinstance(d, list) else ((d or {}).get("list") or [] if isinstance(d, dict) else [])
        return [r for r in rows
                if str((r or {}).get("symbol") or "").upper() == sym
                and str(r.get("status") or "").lower() not in ("success", "failed", "cancelled")]

    def _u_stop_plans(self, symbol) -> list:
        """손절 = TP/SL 전략 주문 중 stopLoss 값이 있는 줄. 반환 모양은 v2와 같다 [{orderId, price, triggerType, planType}].
        ⚠ 진입 때 붙인 손절(place-order stopLoss)이 이 목록에 나오는지는 문서에 적혀 있지 않다(실계좌 확인 전)."""
        sym = self._symbol(symbol)
        out = []
        # 수익 쪽으로 옮긴 손절(트리거 주문, 최종 리뷰 B P1-1)을 맨 앞에 - 앱의 손절 감시가 첫 값을 '걸린 손절'로 읽는다.
        # 앱 트리거 기록이 있는데 트리거 목록을 못 읽었거나 그 트리거가 목록에 없으면 예외(감시는 '무판단'): 남겨 둔 받침 TP/SL 값이 첫 값이
        # 되면 '회원이 손절을 옮겼다'로 잘못 보고 트레일을 멈췄다(재검증 P1-1). 기록이 없으면 종전대로 TP/SL 줄만.
        # 목록을 읽었는데 기록한 트리거가 없으면 대기 중이 아님이 확인된 것 - 기록에서 빼고 남은 줄로 판단한다(재검증 P1-1: 지난 거래의 기록 하나가
        # 감시를 거래 내내 막았다). 읽기 자체가 실패하면 예외(감시 '무판단').
        _ids = self._trig_current(sym, self._trig_book().get(sym) or ())
        _tg = []
        if _ids:
            _rows = self._u_trig_rows(sym)
            _gone = _ids - {str(r.get("orderId")) for r in _rows}
            if _gone:
                self._trig_forget(sym, _gone)
            _tg = [r for r in _rows if str(r.get("orderId")) in (_ids - _gone)]
        for r in _tg:
            out.append({"orderId": str(r.get("orderId")), "price": _f(r.get("triggerPrice")),
                        "triggerType": r.get("triggerBy"), "planType": "trigger"})
        for r in self._u_tpsl_rows(sym):
            _p = _f(r.get("stopLoss"))
            if not _p or _p <= 0:
                continue
            out.append({"orderId": str(r.get("orderId")), "price": _p,
                        "triggerType": r.get("slTriggerBy"), "planType": r.get("type") or "tpsl"})
        return out

    def _u_trig_rows(self, sym: str) -> list:
        """GET /api/v3/trade/unfilled-strategy-orders?category=…&type=trigger → 이 종목의 대기 중 트리거 주문 줄(triggerPrice 있음)."""
        d = self._req("GET", "/api/v3/trade/unfilled-strategy-orders", {"category": self._cat(), "type": "trigger"})
        rows = d if isinstance(d, list) else ((d or {}).get("list") or [] if isinstance(d, dict) else [])
        return [r for r in rows
                if str((r or {}).get("symbol") or "").upper() == sym
                and str(r.get("status") or "").lower() not in ("success", "failed", "cancelled")
                and (_f(r.get("triggerPrice")) or 0) > 0]

    def _u_trig_stops(self, sym: str) -> list:
        """앱이 건 트리거 손절(수익 쪽 손절) 가운데 아직 대기 중인 줄 - 기록한 orderId로만 고른다(검증 P2-1: 회원이 직접 건 트리거는 건드리지
        않는다, 재시작 뒤에도 같다). 기록이 없으면 요청하지 않는다. 읽기 실패 = 예외."""
        ids = self._trig_current(sym, self._trig_book().get(sym) or ())
        if not ids:
            return []
        return [r for r in self._u_trig_rows(sym) if str(r.get("orderId")) in ids]

    def _trig_book(self) -> dict:
        """이 키가 건 트리거 손절 {종목: {orderId…}} - 메모리, 처음엔 파일(_trig_path)에서 읽는다(키 지문별, 키 원문 없음)."""
        st = self._mstate()
        bk = st.get("trig_ids")
        if isinstance(bk, dict):
            return bk
        bk = {}
        try:
            with open(_trig_path(), encoding="utf-8") as f:
                d = _json.load(f)
            raw = (d if isinstance(d, dict) else {}).get(self._kfp()) or {}
            bk = {str(k_): set(str(x) for x in (v or [])) for k_, v in raw.items() if v}
            st["trig_ts"] = {str(k_): float(v) for k_, v in (((d if isinstance(d, dict) else {}).get(self._kfp() + "|ts")) or {}).items()}
            for k_, v in (((d if isinstance(d, dict) else {}).get(self._kfp() + "|entry")) or {}).items():
                st.setdefault("entry_at", {}).setdefault(str(k_), float(v))      # 재시작 뒤에도 진입 시각과 비교(지금 값이 앞선다)
        except Exception:
            bk = {}
        st["trig_ids"] = bk
        return bk

    def _trig_save(self) -> None:
        """트리거 손절 기록을 파일에(임시 파일 + 바꿔 끼우기). 실패는 조용히(메모리 기록은 그대로)."""
        try:
            with _TRIG_LOCK:
                p = _trig_path()
                try:
                    with open(p, encoding="utf-8") as f:
                        d = _json.load(f)
                    d = d if isinstance(d, dict) else {}
                except Exception:
                    d = {}
                v = {k_: sorted(ids) for k_, ids in (self._mstate().get("trig_ids") or {}).items() if ids}
                _all = {i for ids in v.values() for i in ids}
                _ts = {i: t for i, t in (self._mstate().get("trig_ts") or {}).items() if i in _all}
                if v:
                    d[self._kfp()] = v
                    d[self._kfp() + "|ts"] = _ts
                    d[self._kfp() + "|entry"] = {k_: t for k_, t in (self._mstate().get("entry_at") or {}).items() if k_ in v}
                else:
                    d.pop(self._kfp(), None)
                    d.pop(self._kfp() + "|ts", None)
                    d.pop(self._kfp() + "|entry", None)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                tmp = f"{p}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    _json.dump(d, f)
                os.replace(tmp, p)
        except Exception:
            pass

    def _kfp(self) -> str:
        return hashlib.sha256(str(getattr(self, "key", "") or "").encode()).hexdigest()[:20]

    def _trig_current(self, sym, ids) -> set:
        """지금 포지션의 트리거만(재검증 P2-1): 이 종목의 마지막 진입 체결 시각(entry_at)보다 먼저 건 트리거는 그 전 플랫 구간의 것이라 손절로 보지도,
        옮기지도 않는다. 진입 시각을 모르면(재시작 뒤) 거르지 않는다. 건 시각을 모르는 기록은 진입보다 먼저 것으로 본다."""
        _ea = (self._mstate().get("entry_at") or {}).get(sym)
        if not _ea:
            return set(ids)
        _ts = self._mstate().get("trig_ts") or {}
        return {i for i in ids if float(_ts.get(i) or 0) >= float(_ea)}

    def _trig_forget(self, sym, ids) -> None:
        """기록에서 지운다(메모리, 파일) - 취소했거나 대기 중이 아님을 확인한 트리거만 넘긴다."""
        _book = self._trig_book()
        _left = set(_book.get(sym) or ()) - set(ids)
        if _left:
            _book[sym] = _left
        else:
            _book.pop(sym, None)
        for i in ids:
            (self._mstate().get("trig_ts") or {}).pop(i, None)
        self._trig_save()

    def _u_new_entry(self, sym) -> None:
        """진입 체결 직후, 새 손절을 걸기 전(재검증 P2-1): 진입 시각을 적고, 그 전에 건 앱 트리거 가운데 아직 대기 중인 것은 취소하고 기록에서 뺀다
        (그 전 플랫 구간의 것 - 새 포지션의 손절로 쓰이지 않게). 대기 중이 아님을 확인한 것도 뺀다. 목록을 못 읽거나 취소가 실패하면 기록은 남고
        _trig_current가 이 포지션에서는 거른다. 예외를 올리지 않는다(진입 경로)."""
        _now = time.time()
        ids = set(self._trig_book().get(sym) or ())
        self._mstate().setdefault("entry_at", {})[sym] = _now
        if not ids:
            return
        self._trig_save()                    # 남은 기록이 있으면 진입 시각도 파일에(재시작 뒤에도 거른다)
        try:
            _pend = {str(r.get("orderId")) for r in self._u_trig_rows(sym)}
        except Exception:
            return
        _gone = ids - _pend
        for _oid in sorted(ids & _pend):
            try:
                self._req("POST", "/api/v3/trade/cancel-strategy-order", body={"orderId": _oid})
                _gone.add(_oid)
            except Exception:
                pass
        if _gone:
            self._trig_forget(sym, _gone)

    def _u_trig_guard(self, sym, px, long_side):
        """트리거 손절은 지금 값의 손절 쪽이어야 한다(롱 = 최종가, 표시가보다 아래). 아니면 걸자마자 반대로 작동한다 - 보내지 않고 사유를
        돌려준다(TP/SL의 25586/25590과 같은 규칙). 시세를 못 읽으면 사유(보내지 않는다). 문제없으면 None."""
        try:
            d = self._req("GET", "/api/v3/market/tickers", {"category": self._cat(), "symbol": sym})
            row = (d[0] if isinstance(d, list) and d else d) or {}
            _ref = [v for v in (_f(row.get("lastPrice")), _f(row.get("markPrice"))) if v and v > 0]
        except Exception as e:
            _nu(e)
            return f"price read failed: {e}"
        if not _ref:
            return "price read failed: no lastPrice/markPrice"
        _p = float(px)
        if (long_side and _p >= min(_ref)) or (not long_side and _p <= max(_ref)):
            return (f"stop {px} is not below the market ({min(_ref):g})" if long_side
                    else f"stop {px} is not above the market ({max(_ref):g})")
        return None

    def _u_trig_lim(self, sym, px, long_side) -> str:
        """트리거 뒤 나가는 지정가(문서: triggerOrderType=limit일 때 triggerOrderPrice). 시장가 꼴은 문서가 'triggerPrice는 limit일 때만
        유효, market이면 무시'라고 적어 쓰지 않는다 - 지정가에 여유(_TRIG_SLIP)를 두어 거의 시장가처럼 체결되게."""
        return self._snap_px3(sym, float(px) * ((1 - _TRIG_SLIP) if long_side else (1 + _TRIG_SLIP)))

    def _u_trig_modify(self, sym, px, rows) -> dict:
        """이미 건 트리거 손절을 새 값으로(modify-strategy-order: orderId, qty 필수, triggerPrice, triggerOrderType, triggerOrderPrice).
        하나라도 받아들여지면 성공. 모두 실패면 옛 트리거 그대로 두고 오류."""
        _long = str(rows[0].get("side") or "").lower() == "sell"
        _g = self._u_trig_guard(sym, px, _long)
        if _g:
            return {"error": f"trigger stop not moved: {_g}"}
        ok, errs = 0, []
        for r in rows:
            _q = str(r.get("qty") or "").strip()
            if not _q:
                errs.append(f"trigger order {r.get('orderId')}: no qty in the order list - modify not sent")
                continue
            _lg = str(r.get("side") or "").lower() == "sell"
            body = {"orderId": str(r.get("orderId")), "qty": _q, "triggerPrice": px, "triggerOrderType": "limit",
                    "triggerOrderPrice": self._u_trig_lim(sym, px, _lg), "triggerBy": str(r.get("triggerBy") or "mark")}
            try:
                self._req("POST", "/api/v3/trade/modify-strategy-order", body=body)
                ok += 1
            except Exception as e:
                _nu(e)
                errs.append(str(e))
        if ok:
            return {"error": None, "moved": ok, "trigger": True, "partial_errors": errs[:3]}
        return {"error": ("trigger modify: " + "; ".join(errs))[:400] or "trigger modify failed"}

    def _u_trig_move(self, sym, px, why) -> dict:
        """수익 쪽 손절 이동(최종 리뷰 B P1-1). TP/SL 손절은 평단을 넘어 갈 수 없어(25580/25582) 포지션을 닫는 쪽 트리거 주문을 건다:
        문서 place-strategy-order type=trigger - 단방향 'Close long: side=sell & reduceOnly=yes', 헤지 'side=sell & posSide=long',
        qty = 포지션 수량, triggerBy = mark(손절 새 등록과 같음), 지정가 + 여유(_u_trig_lim). clientOid는 보내지 않는다(문서: tpsl만) -
        받은 orderId를 기록해 둔다(_trig_book, 파일). 옛 TP/SL 손절은 취소하지 않는다(검증 P1-2: 시장가 받침).
        걸기가 실패하면 아무것도 바꾸지 않는다(옛 손절 그대로).
        ⚠ 실계좌 확인 항목: 트리거 주문이 TP/SL 손절과 함께 받아들여지는지, 지정가 여유로 빠른 장에서 채워지는지."""
        _side, _qty, _hm = None, 0.0, None
        for p in self._u_list_open_positions():
            if p.symbol == sym:
                _side, _qty = p.net_qty > 0, abs(float(p.net_qty))
                _hm = str((p.raw or {}).get("holdMode") or "") or self._hold()
                break
        if _side is None:
            return {"error": f"{why} / no open position"[:400]}
        _g = self._u_trig_guard(sym, px, _side)
        if _g:
            return {"error": f"{why} / trigger stop not placed: {_g}"[:400]}
        body = {"category": self._cat(), "symbol": sym, "type": "trigger", "side": "sell" if _side else "buy",
                "qty": self._fmt_qty3(sym, _qty), "triggerBy": "mark", "triggerPrice": px,
                "triggerOrderType": "limit", "triggerOrderPrice": self._u_trig_lim(sym, px, _side)}
        if _hm == "hedge_mode":
            body["posSide"] = "long" if _side else "short"
        else:
            body["reduceOnly"] = "yes"
        try:
            d = self._req("POST", "/api/v3/trade/place-strategy-order", body=body) or {}
        except Exception as e:
            _nu(e)
            return {"error": f"{why} / trigger: {e}"[:400]}
        _oid = str((d if isinstance(d, dict) else {}).get("orderId") or "")
        if _oid:
            self._trig_book().setdefault(sym, set()).add(_oid)
            self._mstate().setdefault("trig_ts", {})[_oid] = time.time()
            self._trig_save()
        return {"error": None, "moved": 1, "trigger": True}

    def _u_set_stop(self, symbol, stop_price):
        """손절 이동(v2 set_stop과 같은 원칙): ①대기 중 손절(TP/SL 전략 주문)을 전부 modify-strategy-order로 같은 값에
        ②없거나 전부 실패면 포지션 수량만큼 부분(partial) 손절을 새로 건다(진입 손절과 같은 모양). 취소를 먼저 하지 않는다(무방비 구간 없음).
        부분 손절 수량의 합이 포지션을 넘으면 Bitget이 거절한다(25595) - 이미 걸린 손절이 있으면 새로 걸기는 거절되고 기존 손절이 그대로 산다.
        modify-strategy-order는 qty가 필수(문서)라 그 주문의 qty를 그대로 보낸다. 앱이 건 손절(진입 손절, 여기서 새로 건 손절)은 늘 qty가 있다.
        qty가 없는 줄(회원이 직접 건 전체 TP/SL 등)은 보내지 않는다. 같은 주문의 익절(takeProfit 등)은 읽은 값 그대로 다시 보낸다.
        평단을 넘는 자리(본절, 수익 쪽 트레일)는 Bitget이 TP/SL로 받지 않는다(25580/25582) - 그때는 트리거 손절을 건다(_u_trig_move, 최종 리뷰
        B P1-1). 옛 TP/SL 손절은 시장가 받침으로 남긴다(검증 P1-2: 트리거는 지정가라 가격이 건너뛰면 안 채워질 수 있다 - 최악은 옛 손절).
        그 뒤의 이동은 트리거 손절만 옮긴다. 둘 다 플랫 확인 뒤 정리한다(_u_cancel_left_stops).
        반환 {"error": None|str, ...}."""
        sym = self._symbol(symbol)
        _px = self._snap_px3(sym, stop_price)
        # 앱이 건 트리거 손절(수익 쪽)이 있으면 그것만 옮긴다 - TP/SL 줄(평단 아래 받침 손절)은 건드리지 않는다(검증 P1-2).
        # 조회가 안 되면 아무것도 새로 걸지 않는다(트리거 손절 위에 하나 더 거는 일 방지 - 기존 손절 그대로).
        try:
            _tg = self._u_trig_stops(sym)
        except Exception as e:
            _nu(e)
            return {"error": f"trigger query: {e}"}
        if _tg:
            return self._u_trig_modify(sym, _px, _tg)
        try:
            _sl = [r for r in self._u_tpsl_rows(sym) if (_f(r.get("stopLoss")) or 0) > 0]
        except Exception as e:
            _nu(e)
            return {"error": f"plan query: {e}"}
        _mod_err = None
        if _sl:
            _ok, _errs, _beyond = 0, [], False
            for _row in _sl:
                _q = str(_row.get("qty") or "").strip()
                if not _q:
                    _errs.append(f"strategy order {_row.get('orderId')}: no qty in the order list - modify not sent")
                    continue
                body = {"orderId": str(_row.get("orderId")), "qty": _q, "stopLoss": _px, "slOrderType": "market"}
                if _row.get("slTriggerBy"):
                    body["slTriggerBy"] = str(_row.get("slTriggerBy"))
                if (_f(_row.get("takeProfit")) or 0) > 0:
                    body["takeProfit"] = str(_row.get("takeProfit"))
                    for _k in ("tpTriggerBy", "tpOrderType"):
                        if _row.get(_k):
                            body[_k] = str(_row.get(_k))
                    if str(_row.get("tpOrderType") or "") == "limit" and _row.get("tpLimitPrice"):
                        body["tpLimitPrice"] = str(_row.get("tpLimitPrice"))
                try:
                    self._req("POST", "/api/v3/trade/modify-strategy-order", body=body)
                    _ok += 1
                except Exception as _e1:
                    _nu(_e1)
                    _errs.append(str(_e1))
                    _beyond = _beyond or _beyond_entry_reject(_e1)
            if _ok:
                return {"error": None, "moved": _ok, "partial_errors": _errs[:3]}
            _mod_err = "; ".join(_errs)[:300] or "modify failed"
            if _beyond:
                # 평단을 넘는 손절(본절, 수익 쪽 트레일)은 TP/SL로 갈 수 없다 - 트리거 손절을 먼저 걸고 옛 손절을 취소한다
                return self._u_trig_move(sym, _px, f"modify: {_mod_err}")
        try:
            _side, _qty, _hm = None, 0.0, None
            for p in self.list_open_positions():
                if p.symbol == sym:
                    _side = "long" if p.net_qty > 0 else "short"
                    _qty = abs(float(p.net_qty))
                    _hm = str((p.raw or {}).get("holdMode") or "") or self._hold()
                    break
            if _side is None:
                return {"error": "no open position"}
            # 포지션 수량만큼 부분(partial) 손절(진입 손절과 같은 모양, 리뷰 P1-3). 트리거는 v2 새 등록(mark_price)과 같게 mark.
            # 본문 꼴은 진입 손절과 같은 차례(_u_sl_forms) - 단방향에서 꼴 문제로 거절되면 다른 꼴로 한 번(3차 리뷰 P2-3).
            _hedge = _hm == "hedge_mode"
            _forms = self._u_sl_forms(_hedge)
            _e_last = None
            for _fi, _form in enumerate(_forms):
                _b = self._u_sl_body(sym, _qty, _side == "long", _px, None, _hedge, None if _form == "hedge" else _form)
                _b["slTriggerBy"] = "mark"
                try:
                    self._req("POST", "/api/v3/trade/place-strategy-order", body=_b)
                    self._u_form_ok(_form)
                    return {"error": None}
                except Exception as _e2:
                    _nu(_e2)
                    _e_last = _e2
                    if _beyond_entry_reject(_e2) and not _sl:
                        return self._u_trig_move(sym, _px, f"place: {_e2}")
                    if not (_sl_form_reject(_e2) and _fi + 1 < len(_forms)):
                        break
            raise _e_last
        except Exception as e:
            _nu(e)
            return {"error": (f"modify: {_mod_err} / place: {e}" if _mod_err is not None
                              else f"place: {e}")}

    def _u_block_4h(self, symbol, end_utc):
        """v2와 같은 규약(1H 4개 조립). GET /api/v3/market/candles - interval(=v2 granularity), 응답 줄
        [ts, open, high, low, close, base vol, quote turnover]."""
        from datetime import timedelta
        start = end_utc - timedelta(hours=4)
        d = self._req("GET", "/api/v3/market/candles",
                      {"category": self._cat(), "symbol": self._symbol(symbol), "interval": "1H",
                       "startTime": int(start.timestamp() * 1000),
                       "endTime": int(end_utc.timestamp() * 1000) - 1,
                       "limit": 10})
        lst = d if isinstance(d, list) else ((d or {}).get("list") or [])
        rows = sorted(lst, key=lambda r: int(r[0]))
        rows = [r for r in rows
                if start.timestamp() * 1000 <= int(r[0]) < end_utc.timestamp() * 1000]
        if len(rows) < 4:
            return None
        return float(rows[0][1]), float(rows[-1][4])

    def _u_closed_fills(self, start_ms: int) -> list[dict]:
        """GET /api/v3/position/history-position(청산된 포지션). 문서: 90일 창, startTime~endTime 30일 이하,
        limit 최대 100, cursor로 다음 쪽 - 30일 창으로 나눠 오래된 것부터 읽는다(같은 positionId는 한 번).
        pnl = netProfit(수수료, 펀딩 포함 순손익), 없으면 cumRealisedPnl(수수료, 펀딩 제외) + 수수료·펀딩(_bg3_net)."""
        now = int(time.time() * 1000)
        lo = max(int(start_ms or 0), now - _HIST_MAX_MS + 60000)
        seen, out = set(), []
        s = lo
        while s < now:
            e = min(s + _HIST_WIN_MS, now)
            cursor = None
            for _pg in range(20):
                q = {"category": self._cat(), "startTime": s, "endTime": e, "limit": 100}
                if cursor:
                    q["cursor"] = cursor
                d = self._req("GET", "/api/v3/position/history-position", q)
                lst = (d.get("list") if isinstance(d, dict) else d) or []
                for r in lst:
                    try:
                        _tid = str(r.get("positionId") or "")
                        if _tid and _tid in seen:
                            continue
                        out.append({
                            "tid": _tid,
                            "ts_ms": int(r.get("updatedTime") or r.get("createdTime") or 0),
                            "symbol": str(r.get("symbol") or ""),
                            "pnl": _bg3_net(r),
                            "fees": _bg3_costs(r),
                            "direction": "LONG" if str(r.get("posSide")).lower() == "long" else "SHORT",
                            "entry_px": pos_px(r.get("openPriceAvg")),
                            "exit_px": pos_px(r.get("closePriceAvg")),
                        })
                        if _tid:
                            seen.add(_tid)
                    except (TypeError, ValueError, AttributeError):
                        continue
                cursor = d.get("cursor") if isinstance(d, dict) else None
                if not cursor or len(lst) < 100:
                    break
            s = e
        return out

    def _u_key_info(self) -> list:
        """GET /api/v3/account/info(문서: 'Permission: No permission required') → permType(read-only | read-and-write),
        permissions(uta_mgt = UTA 관리, uta_trade = UTA 거래, …). 앱이 쓰는 권한이 빠졌으면 KO / EN 한 줄씩(리뷰 P1-2)."""
        d = self._req("GET", "/api/v3/account/info") or {}
        d = d if isinstance(d, dict) else {}
        perms = {str(x).strip().lower() for x in (d.get("permissions") or []) if str(x).strip()}
        ro = str(d.get("permType") or "").strip().lower() == "read-only"
        out = []
        if (perms and "uta_trade" not in perms) or ro:
            out.append("Bitget 키에 'UTA 거래(읽기와 쓰기)' 권한이 없어 주문을 낼 수 없습니다 - Bitget API 키 설정에서 켜십시오. "
                       "/ The Bitget key lacks 'UTA trade (read & write)' permission, so no orders can be placed - "
                       "enable it in Bitget's API key settings.")
        if perms and "uta_mgt" not in perms:
            out.append("Bitget 키에 'UTA 관리' 권한이 없어 레버리지와 포지션 모드(단방향 / 양방향)를 읽거나 바꾸지 못합니다 - 레버리지 자동 조정이 "
                       "꺼지고, 양방향 모드면 진입 주문이 한 번 거절된 뒤 다시 나갑니다. Bitget API 키 설정에서 'UTA 관리(읽기와 쓰기)'를 켜십시오. "
                       "/ The Bitget key lacks 'UTA management' permission, so the app cannot read or change leverage and the "
                       "position mode (one-way / hedge) - automatic leverage adjustment is off, and in hedge mode each entry is "
                       "rejected once and then resent. Enable 'UTA management (read & write)' in Bitget's API key settings.")
        return out

    def _u_entry_info(self) -> list:
        """연결 테스트 로그(읽기 전용): 계정 유형, BTCUSDT 교차 레버리지, 포지션 모드, 가용 USDT."""
        out = ["계정 유형 = 통합 거래 계정(Unified Trading Account, API v3)"]
        try:
            s = self._u_settings()
            row = self._u_lev_row(s, "BTCUSDT")
            if row and _f(row.get("leverage")):
                out.append(f"BTCUSDT 레버리지(교차) {float(row.get('leverage')):g}x")
            hm = str(s.get("holdMode") or "")
            if hm:
                out.append("포지션 모드 = " + ("양방향(헤지)" if hm == "hedge_mode" else "단방향"))
        except Exception as e:
            _nu(e)
        try:
            avail = self._u_available_usdt()
            if avail:
                out.append(f"가용 잔고(마진) ≈ {float(avail):,.0f} USDT")
        except Exception as e:
            _nu(e)
        out.append("주문 마진모드 = 교차(cross) 자동 지정")
        return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bg_costs(r: dict) -> float:
    """수수료+펀딩 합(낸 돈 = 음수). history-position의 openFee·closeFee·totalFunding.
    수수료는 부호와 상관없이 비용으로 뺀다(-|fee|): 응답의 부호 규약을 실키로 대조하기 전이라, 양수로 오면 더해져
    손익이 부풀던 위험을 막는다(이 앱은 시장가라 리베이트가 없다). 펀딩은 받는 날도 있어 부호 그대로."""
    fees = sum(-abs(x) for x in (_f(r.get("openFee")), _f(r.get("closeFee"))) if x is not None)
    fund = _f(r.get("totalFunding"))
    return fees + (fund or 0.0)


def _bg_net(r: dict) -> float:
    """회원 실제 손익 = 수수료와 펀딩까지 뺀 순손익(대표 2026-10-12 '수수료 넣어').
    netProfit이 오면 그것(거래소 순손익), 없으면 총손익 pnl + 수수료·펀딩(_bg_costs). 종전엔 netProfit이 없을 때
    총손익을 그대로 써서 '내 성과'에 수수료가 빠질 수 있었다."""
    n = _f(r.get("netProfit"))
    if n is not None:
        return n
    return (_f(r.get("pnl")) or 0.0) + _bg_costs(r)


def _bg3_costs(r: dict) -> float:
    """UTA(v3) history-position의 수수료+펀딩 합(낸 돈 = 음수): openFeeTotal, closeFeeTotal, totalFunding.
    문서 예시의 수수료는 음수(차감)지만 _bg_costs와 같은 이유로 부호와 상관없이 -|fee|. 펀딩은 부호 그대로."""
    fees = sum(-abs(x) for x in (_f(r.get("openFeeTotal")), _f(r.get("closeFeeTotal"))) if x is not None)
    fund = _f(r.get("totalFunding"))
    return fees + (fund or 0.0)


def _bg3_net(r: dict) -> float:
    """UTA(v3) 순손익: netProfit(문서: 'Including fees and funding costs'), 없으면 cumRealisedPnl(문서: 'Excluding fees
    and funding costs') + _bg3_costs. 문서 예시: -0.116 + (-22.7360116) + (-22.7359884) + 0 = -45.588 = netProfit."""
    n = _f(r.get("netProfit"))
    if n is not None:
        return n
    return (_f(r.get("cumRealisedPnl")) or 0.0) + _bg3_costs(r)
