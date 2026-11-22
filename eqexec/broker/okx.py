"""OKX adapter (USDT 무기한 스왑, BTC-USDT-SWAP) — v5 REST API.

Bitget·Bybit 어댑터와 같은 공개 인터페이스(앱이 같은 경로로 부른다). 회원 PC에서 회원 본인의 OKX API
key/secret/passphrase로만 돈다. 키, 시크릿, 패스프레이즈는 OKX 서버 말고 어디에도 보내지 않는다.
출금·이체 호출은 없다(앞으로도 넣지 않는다). ⚠ 실키 검증 전 — 데모 트레이딩 키로 먼저 확인한다. dry_run은 주문을 내지 않는다.

서명: base64(HMAC_SHA256(timestamp + METHOD + requestPath(+?query) + body, secret)),
  timestamp = ISO8601 UTC 밀리초('2020-12-08T09:08:57.715Z'). 헤더 OK-ACCESS-KEY/SIGN/TIMESTAMP/PASSPHRASE.

데모 트레이딩(대표 2026-11-03 "내 OKX로 테스트"): 환경 변수 EQ_OKX_DEMO=1 이면 모든 요청에 x-simulated-trading: 1을
  붙인다(OKX 데모 키는 이 헤더가 있어야 받고, 실키는 이 헤더가 있으면 거절한다 - 섞이지 않는다).
  윈도: 시스템 설정 → 환경 변수에 EQ_OKX_DEMO=1 추가 → 앱 다시 시작. 지우면(또는 0) 실계좌.
서버 지역: OKX는 지역마다 API 주소가 다르다(전 세계 www.okx.com, 유럽 EEA eea.okx.com, 미국 us.okx.com). 다른 지역
  주소로 보내면 50119(API key doesn't exist)로 거절되므로, 그 프로세스의 첫 서명 요청(읽기)이 50119면 다른 지역 주소를
  차례로 읽어 보고 받아 주는 곳을 쓴다(읽기 전용 GET만으로 고른다 - 주문은 고른 뒤에만 나간다).
  EQ_OKX_HOST=www|eea|us(또는 https://… 전체 주소)로 고정할 수 있다.

수량: 앱은 BTC 수량을 준다. OKX 스왑 주문 수량은 **계약 수**(BTC-USDT-SWAP ctVal 0.01 BTC)라 공개 종목 정보
  (/api/v5/public/instruments의 ctVal·lotSz·minSz·tickSz·lever)를 읽어(프로세스 캐시) 양방향 변환한다. 숫자를 박아 두지 않는다.
  BTC → 계약은 lotSz 아래로 내림(리스크가 1R을 넘지 않게). 앱에 돌려주는 포지션·체결 수량은 다시 BTC.
심볼: 앱 쪽은 Bybit·Bitget과 같은 'BTCUSDT'로 보고 받는다(원장·손절 감시·자산별 청산이 그 이름으로 맞춘다).
  OKX instId('BTC-USDT-SWAP') 변환은 이 파일 안에서만 한다.
종목(2026-11-05 대표 독일 계정): OKX 유럽(EEA, MiFID) 계정은 BTC-USDT-SWAP(일반 무기한)을 거래하지 못하고 BTC 무기한 자리에
  X-Perp(instType FUTURES, ruleType xperp, 예 BTC-USD_UM_XPERP-310404 - 5년 만기 선물에 펀딩비가 붙어 현물을 따라가는 상품, USD 증거금)를 준다.
  그래서 키마다 한 번 '이 계정이 거래할 수 있는 종목'(GET /api/v5/account/instruments, 읽기)을 보고 고른다: BTC-USDT-SWAP이
  있으면 그것(종전 그대로), 없고 X-Perp가 있으면 X-Perp. 조회가 실패하면 고르지 않고 BTC-USDT-SWAP으로 둔 채 다음에 다시 본다.
  고른 결과는 프로세스 캐시(_MKT_OF) - 진입 경로에 조회를 더하지 않게 연결 테스트·포지션 조회 때 미리 정한다.
포지션 모드: /api/v5/account/config의 posMode - net_mode면 posSide를 안 보내고(청산 주문은 reduceOnly),
  long_short_mode면 posSide(long/short)를 보낸다. 마진은 교차(cross, Bitget·Bybit와 같은 취급).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json as _json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests

from .base import BrokerAdapter, FlattenResult, Position

_TIMEOUT = (10, 30)
_HOSTS = {"www": "https://www.okx.com", "eea": "https://eea.okx.com", "us": "https://us.okx.com"}
_HOST_ORDER = ("www", "eea", "us")
_INST_TYPE = "SWAP"

# 응답을 못 받은 것과 같은 코드(OKX 문서: 50004 = '요청 시간 초과 - 성공도 실패도 아님, 결과를 조회하라') -
# '거절'로 말하지 않는다(_entry_err_unknown이 'failed: code='를 거절로 읽는다 → 이 코드들은 'unknown: code='로 낸다).
_UNKNOWN_CODES = {"50001", "50004", "50013", "50026", "51149"}   # 51149 = 'Order timed out'(같은 단서, 2026-11-05 리뷰)

# 프로세스 공용 캐시(어댑터는 자주 새로 만들어진다 - _build_broker가 호출마다 만든다)
_LOCK = threading.Lock()
_HOST_OF: dict = {}       # 키 해시 → base URL(지역 판정 결과)
_CONF_OF: dict = {}       # (base, 키 해시) → (account/config 행, 읽은 시각)
_SPEC_OF: dict = {}       # (base, instId) → {ctVal, lotSz, minSz, tickSz, lever}
_CLOCK: dict = {}         # base → 보정 ms(서버 - 이 PC)
_MKT_OF: dict = {}        # (base, 키 해시) → (instType, instId) - 이 계정이 거래할 BTC 무기한 종목
_MKT_NA: dict = {}        # (base, 키 해시) → True: 이 계정은 BTC 무기한(SWAP도 X-Perp도)을 못 거래한다(거주 국가 제한 등, key_info 경고)
_MKT_EXP: dict = {}       # (base, 키 해시) → 이 시각까지만 캐시(못 거래하는 계정 판정은 1시간마다 다시 본다)
_MKT_NA_TTL_S = 3600
_SWAP_ID = "BTC-USDT-SWAP"
_XPERP_FAMILY = "BTC-USD_UM_XPERP"
_XPERP_CCY = ("USD", "USDC", "USDG")   # X-Perp 증거금 통화(OKX 유럽 안내: USD-margined, USD·USDC·USDG)
# X-Perp 레버리지 상한(2026-11-05 대표 확인 "독일에서 사용하는 계정이라면 X-Perps의 상한은 10배", OKX 안내 'up to 10x').
# 공개 종목 규격의 lever(50)는 상품 상한이라 유럽 리테일 계정에는 맞지 않는다 - 그 값으로 올리면 OKX가 거절하고 3배에 머문다.
_XPERP_MAX_LEV = 10
_CONF_TTL_S = 600


def _env_demo() -> bool:
    return str(os.environ.get("EQ_OKX_DEMO", "")).strip().lower() in ("1", "true", "yes", "on")


def _env_host() -> str:
    h = str(os.environ.get("EQ_OKX_HOST", "")).strip()
    if not h:
        return ""
    if h.lower() in _HOSTS:
        return _HOSTS[h.lower()]
    return h.rstrip("/") if h.startswith("https://") else ""


def clord_id(tag) -> str | None:
    """앱의 EQ 주문 표식('EQ-AP-1730…-2') → OKX clOrdId 규칙(영문·숫자만, 32자 이하). 남는 게 없으면 None(안 붙인다)."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(tag or ""))[:32]
    return s or None


def _f(v):
    try:
        if v in (None, ""):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _dec(step: float) -> int:
    """스텝(0.01, 0.1, 1)의 소수 자릿수."""
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


class OkxBroker(BrokerAdapter):
    name = "okx"

    def __init__(self, cfg):
        self.cfg = cfg                       # eqexec.config.OkxCfg
        self.key = cfg.api_key
        self.secret = cfg.api_secret
        self.passphrase = getattr(cfg, "passphrase", "")
        self.demo = bool(getattr(cfg, "demo", False)) or _env_demo()
        _h = str(getattr(cfg, "host", "") or "").strip()
        _h = _HOSTS.get(_h.lower(), _h.rstrip("/")) if _h else _env_host()
        self._host_fixed = bool(_h)
        self._kid = hashlib.sha256(f"{self.key}|{int(self.demo)}".encode()).hexdigest()[:16]
        with _LOCK:
            self.base = _h or _HOST_OF.get(self._kid) or _HOSTS["www"]
        self._host_ok = self._host_fixed or (self._kid in _HOST_OF)

    # ── 서명·요청 ─────────────────────────────────────────────────────────────
    def _sign(self, ts: str, method: str, path: str, body: str) -> str:
        msg = f"{ts}{method.upper()}{path}{body}"
        return base64.b64encode(hmac.new(self.secret.encode(), msg.encode(),
                                         hashlib.sha256).digest()).decode()

    def _ts(self) -> str:
        """ISO8601 UTC 밀리초. 서버와 30초 넘게 어긋나면 50102로 거절되므로 그때 잰 보정을 더한다."""
        ms = int(time.time() * 1000) + _CLOCK.get(self.base, 0)
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"

    def _sync_clock(self):
        """서버 시각(/api/v5/public/time)과 이 PC 시각의 차이. 실패면 그대로(0)."""
        try:
            t0 = time.time()
            r = requests.get(f"{self.base}/api/v5/public/time", timeout=(5, 5))
            t1 = time.time()
            srv = int(((r.json() or {}).get("data") or [{}])[0].get("ts"))
            _CLOCK[self.base] = srv - int((t0 + t1) / 2 * 1000)
        except Exception:
            pass

    def _req(self, method: str, path: str, params: dict | None = None, body=None,
             auth: bool = True, _retry: bool = True):
        if auth and not self._host_ok:
            self._pick_host()
        method = method.upper()
        if method == "GET" and params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            path_q, payload = f"{path}?{qs}", ""
        else:
            path_q = path
            payload = _json.dumps(body, separators=(",", ":")) if body is not None else ""
        url = f"{self.base}{path_q}"
        headers = {"Content-Type": "application/json"}
        if self.demo:
            headers["x-simulated-trading"] = "1"
        if auth:
            ts = self._ts()
            headers.update({"OK-ACCESS-KEY": self.key,
                            "OK-ACCESS-SIGN": self._sign(ts, method, path_q, payload),
                            "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": self.passphrase})
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=_TIMEOUT)
        else:
            r = requests.post(url, data=payload, headers=headers, timeout=_TIMEOUT)
        try:
            d = r.json() or {}
        except Exception:
            d = {}
        code = str(d.get("code")) if isinstance(d, dict) and d.get("code") is not None else None
        if code == "50102" and auth and _retry:            # 시각 거절 = 처리 전 거절 → 시각 재고 한 번만 다시
            self._sync_clock()
            return self._req(method, path, params, body, auth=auth, _retry=False)
        if r.status_code >= 400 and code in _UNKNOWN_CODES:
            # 결과를 모르는 코드는 HTTP 상태와 상관없이 '모름'(2026-11-05 리뷰 P1: OKX는 50004를 HTTP 400, 50013을 429로 준다 -
            # 'HTTP 4xx'라고 적으면 _entry_err_unknown이 확정 거절로 읽어, 손절 붙은 포지션이 살아 있는데 회원에게 '거절'이라 했다).
            raise RuntimeError(f"OKX {path} unknown: code={code} (status {r.status_code}) "
                               f"{(d.get('msg') if isinstance(d, dict) else '') or ''}"[:300])
        if r.status_code >= 400:
            # HTTP 에러에도 OKX는 본문에 원인 코드를 준다(예: 50113 서명 불일치, 50110 IP 차단, 50119 키 없음).
            raise RuntimeError(f"OKX {path} HTTP {r.status_code}: code={code} "
                               f"{(d.get('msg') if isinstance(d, dict) else '') or (r.text or '')[:160]}"
                               f"{self._hint(code)}")
        if code != "0":
            # 성공은 code="0"만. 주문 실패는 code="1"(전부)·"2"(일부)이고 진짜 이유는 data[i].sCode/sMsg에 있다.
            det = []
            for it in (d.get("data") or []) if isinstance(d, dict) else []:
                if isinstance(it, dict) and str(it.get("sCode", "0")) != "0":
                    det.append(f"sCode={it.get('sCode')} {it.get('sMsg') or ''}".strip())
            _sc = {str(x.split()[0][6:]) for x in det}
            _kind = "unknown" if (code in _UNKNOWN_CODES or (_sc and _sc <= _UNKNOWN_CODES)) else "failed"
            raise RuntimeError(f"OKX {path} {_kind}: code={code} {d.get('msg') or ''}"
                               + (f" [{'; '.join(det)[:240]}]" if det else "") + self._hint(code))
        return d.get("data")

    def _hint(self, code) -> str:
        """자주 나는 인증 오류 → 회원이 고칠 곳 한 줄(설명용, 판단에는 안 쓴다)."""
        c = str(code or "")
        if c == "50101":
            return (" (데모 키와 실계좌 설정이 섞였습니다 - 데모 키면 EQ_OKX_DEMO=1, 실키면 EQ_OKX_DEMO를 지우세요)")
        if c == "50105":
            return " (Passphrase가 다릅니다 - 키를 만들 때 정한 Passphrase)"
        if c == "50110":
            return " (키의 IP 허용 목록에 이 PC IP가 없습니다)"
        if c == "50113":
            return " (서명 불일치 - API Secret을 다시 붙여 넣으세요)"
        if c == "50119":
            return " (이 서버 지역에 없는 키 - EQ_OKX_HOST=eea 또는 us를 확인하세요)"
        return ""

    def _pick_host(self):
        """첫 서명 요청 전에 키가 사는 지역 주소를 고른다(모듈 설명). 읽기(GET account/config)로만 판단하고,
        고른 결과·계정 설정을 프로세스 캐시에 둔다. 모든 지역이 50119면 마지막 오류를 올린다."""
        self._host_ok = True            # 재귀 방지(아래 _req가 다시 들어오지 않게)
        cands = [self.base] + [_HOSTS[k] for k in _HOST_ORDER if _HOSTS[k] != self.base]
        last = None
        for base in cands:
            self.base = base
            try:
                rows = self._req("GET", "/api/v5/account/config")
            except Exception as e:
                last = e
                if "code=50119" in str(e):
                    continue
                self.base = cands[0]          # 지역 문제가 아닌 오류(패스프레이즈, 네트워크 등)는 그대로 올린다
                self._host_ok = False
                raise
            with _LOCK:
                _HOST_OF[self._kid] = base
                _CONF_OF[(base, self._kid)] = ((rows or [{}])[0] or {}, time.time())
            return
        self.base = cands[0]
        self._host_ok = False
        raise last or RuntimeError("OKX: no region accepted this key")

    # ── 계정 설정·종목 정보 ───────────────────────────────────────────────────
    def _config(self, fresh: bool = False) -> dict:
        """account/config 행(posMode, acctLv, perm, …). 10분 캐시."""
        if not self._host_ok:
            self._pick_host()
        k = (self.base, self._kid)
        with _LOCK:
            c = _CONF_OF.get(k)
        if c is not None and not fresh and time.time() - c[1] < _CONF_TTL_S:
            return c[0]
        rows = self._req("GET", "/api/v5/account/config")
        row = (rows or [{}])[0] or {}
        with _LOCK:
            _CONF_OF[k] = (row, time.time())
        return row

    def _hedge(self, allow_stale: bool = False) -> bool:
        """long_short_mode(양방향)인가. 설정을 못 읽으면 net(OKX 기본)으로 본다 - 틀리면 주문이 51000류로
        거절되고(처리 전 거절) place_entry가 설정을 다시 읽어 한 번 더 보낸다."""
        try:
            if allow_stale:
                with _LOCK:
                    c = _CONF_OF.get((self.base, self._kid))
                if c is not None:
                    return str(c[0].get("posMode") or "") == "long_short_mode"
            return str(self._config().get("posMode") or "") == "long_short_mode"
        except Exception:
            return False

    def _spec(self, inst: str) -> dict:
        """종목 규격(공개, 프로세스 캐시). 못 읽으면 예외 - 수량 변환을 지어내지 않는다."""
        k = (self.base, inst)
        with _LOCK:
            s = _SPEC_OF.get(k)
        if s:
            return s
        rows = self._req("GET", "/api/v5/public/instruments",
                         {"instType": self._itype(inst), "instId": inst}, auth=False)
        row = (rows or [{}])[0] or {}
        s = {"ctVal": _f(row.get("ctVal")), "lotSz": _f(row.get("lotSz")), "minSz": _f(row.get("minSz")),
             "tickSz": _f(row.get("tickSz")), "lever": _f(row.get("lever")),
             "maxMktSz": _f(row.get("maxMktSz")),            # 시장가 1회 최대 계약 수(X-Perp 20000 = 2 BTC)
             "ctValCcy": str(row.get("ctValCcy") or "")}
        if not s["ctVal"] or not s["lotSz"] or not s["tickSz"]:
            raise RuntimeError(f"OKX instrument spec missing for {inst}: {row!r}"[:200])
        with _LOCK:
            _SPEC_OF[k] = s
        return s

    @staticmethod
    def _symbol(sym: str) -> str:
        """신호 심볼('BTCUSDT.P')·OKX instId('BTC-USDT-SWAP') → 앱 쪽 이름('BTCUSDT', Bybit·Bitget과 같다)."""
        s = str(sym or "").upper()
        if "_XPERP" in s:                                 # X-Perp instId('BTC-USD_UM_XPERP-310404') → 'BTCUSDT'(앱 쪽 이름은 하나)
            # 일반 만기 선물(BTC-USD_UM-261127)은 여기 안 온다 - 회원이 따로 든 선물을 앱의 BTC 포지션으로 보지 않게(2026-11-05 리뷰)
            return (s.split("-")[0] or "BTC") + "USDT"
        s = s.replace(".P", "").replace("-SWAP", "").replace("-", "").replace("/", "")
        return s or "BTCUSDT"

    @staticmethod
    def _inst(sym: str) -> str:
        """앱 쪽 이름('BTCUSDT', 'BTCUSDT.P') → OKX instId('BTC-USDT-SWAP')."""
        s = OkxBroker._symbol(sym)
        for q in ("USDT", "USDC"):
            if s.endswith(q) and len(s) > len(q):
                return f"{s[:-len(q)]}-{q}-SWAP"
        return "BTC-USDT-SWAP"

    @staticmethod
    def _itype(inst: str) -> str:
        """instId → instType. '-SWAP'으로 끝나면 SWAP, 그 밖(X-Perp)은 FUTURES."""
        return "SWAP" if str(inst or "").upper().endswith("-SWAP") else "FUTURES"

    def _market(self) -> tuple:
        """이 계정이 거래할 BTC 무기한 종목 (instType, instId) - 모듈 설명 '종목'. 키마다 읽기로 정하고 캐시한다.
        ⚠️정하지 못하면(일시 오류, 인증 오류) **예외**를 올린다(2026-11-05 리뷰 P0): 종전엔 BTC-USDT-SWAP을 돌려줘, 유럽 계정의
        X-Perp 포지션을 SWAP으로 찾아 '없음'으로 읽고 position_qty 0, close_symbol closed=True, flatten_all '정리 끝'이 됐다
        (손절 체결로 오판, 잔여 위에 새 진입 겹침). 예외면 호출측이 '조회 실패'로 다룬다. 확정된 답만 캐시하고,
        '둘 다 없음'(파생 막힌 계정)은 1시간만 캐시해 다시 본다."""
        if not self._host_ok:
            self._pick_host()
        k = (self.base, self._kid)
        with _LOCK:
            m = _MKT_OF.get(k)
            exp = _MKT_EXP.get(k)
        if m and (exp is None or time.time() < exp):
            return m
        dflt = ("SWAP", _SWAP_ID)

        def _soft(e) -> bool:
            # 판단 보류할 오류: 네트워크(코드 없음), 응답 없음/시간 초과, 요청 한도, 인증(501xx)
            c = re.search(r"code=(\d+)", str(e))
            c = c.group(1) if c else ""
            return (not c) or c in _UNKNOWN_CODES or c in ("50011", "50061") or c.startswith("501")

        def _fail(e):
            c = re.search(r"code=(\d+)", str(e))
            return RuntimeError(f"OKX market lookup failed: code={c.group(1) if c else 'net'} {str(e)[:160]}")

        try:
            rows = self._req("GET", "/api/v5/account/instruments", {"instType": "SWAP", "instId": _SWAP_ID}) or []
        except Exception as e:
            if _soft(e):
                raise _fail(e)
            rows = []                                      # 51001 '그 종목 없음', 지역·규정 제한 코드 = 이 계정엔 없다
        if any(str(r.get("instId") or "") == _SWAP_ID and str(r.get("state") or "live") == "live" for r in rows):
            m, ttl = dflt, None
        else:
            try:
                xr = self._req("GET", "/api/v5/account/instruments",
                               {"instType": "FUTURES", "instFamily": _XPERP_FAMILY}) or []
            except Exception as e:
                if _soft(e):
                    raise _fail(e)
                xr = []
            xs = [r for r in xr if str(r.get("ruleType") or "") == "xperp"
                  and str(r.get("instId") or "").startswith(_XPERP_FAMILY + "-")]
            # 살아 있는 것 먼저, 없으면 일시 정지 중인 것이라도(그 계정의 종목이다 - 포지션 조회는 그 종목으로 해야 한다)
            xs.sort(key=lambda r: (str(r.get("state") or "") == "live", int(_f(r.get("expTime")) or 0)), reverse=True)
            if xs:
                m, ttl = ("FUTURES", str(xs[0].get("instId"))), None
            else:
                m, ttl = dflt, _MKT_NA_TTL_S
        with _LOCK:
            _MKT_OF[k] = m
            if ttl:
                _MKT_EXP[k] = time.time() + ttl
                if not any(str(r.get("instId") or "") == _SWAP_ID for r in rows):
                    _MKT_NA[k] = True
            else:
                _MKT_EXP.pop(k, None)
                _MKT_NA.pop(k, None)
        return m

    def _xperp(self) -> bool:
        try:
            return self._market()[0] == "FUTURES"
        except Exception:
            return False

    def _inst_of(self, sym: str) -> str:
        """앱 쪽 이름 → 이 계정에서 주문할 instId. BTC는 _market이 고른 종목, 그 밖은 _inst 규칙 그대로."""
        if self._symbol(sym) == "BTCUSDT":
            return self._market()[1]
        return self._inst(sym)

    def _to_contracts(self, inst: str, coin) -> str:
        """BTC 수량 → 계약 수 문자열(lotSz 아래로 내림, minSz 미만이면 '0')."""
        sp = self._spec(inst)
        c = float(coin) / sp["ctVal"]
        lots = math.floor(c / sp["lotSz"] + 1e-9)
        n = round(lots * sp["lotSz"], _dec(sp["lotSz"]))
        if n <= 0 or (sp.get("minSz") and n < sp["minSz"] - 1e-12):
            return "0"
        return f"{n:.{_dec(sp['lotSz'])}f}"

    def _to_coin(self, inst: str, contracts) -> float:
        """계약 수 → BTC 수량. 규격을 못 읽으면(다른 종목) 계약 수 그대로."""
        try:
            sp = self._spec(inst)
            return round(float(contracts) * sp["ctVal"], 8)
        except Exception:
            return float(contracts)

    def _snap_px(self, inst: str, px) -> str:
        """가격을 종목 틱(tickSz)에 스냅. 신호 손절은 Bybit 좌표 + 캘리브레이션(소수 2자리)이라 틱이 안 맞으면 거절된다
        (Bitget 2026-08-17 45115와 같은 원칙 - 정렬은 브로커층 책임)."""
        step = self._spec(inst)["tickSz"]
        d = _dec(step)
        return f"{round(round(float(px) / step) * step, d):.{d}f}"

    # ── 잔고·레버리지 (Bybit·Bitget과 같은 계약) ─────────────────────────────
    def available_usdt(self, symbol="BTCUSDT"):
        """새 주문에 쓸 수 있는 증거금(달러). availEq → availBal → cashBal → eq 순(계정 모드마다 빈 칸이 다르다). 실패 시 None.
        BTC-USDT-SWAP = USDT 한 통화.
        X-Perp(유럽 계정) = USD 정산. ① account/config settleCcy가 USD, USDC, USDG 중 하나이고 그 통화 잔고가 있으면 그것만(리뷰 P1:
        셋을 더하면 부풀린다) ② 그 통화 잔고 줄이 없으면(예: settleCcy가 USD인데 USDC만 넣은 계정 - OKX BD 2026-11-05 '증거금은
        BTC, ETH, SOL, USDC, USDG, DOGE, 정산은 USD') USD, USDC, USDG를 더해 읽는다(대표 계정에서 실측된 경로, 없음으로 떨어뜨리지 않게)."""
        def _pick(d):
            for k in ("availEq", "availBal", "cashBal", "eq"):
                v = _f(d.get(k))
                if v is not None:
                    return v
            return None
        try:
            xp = self._market()[0] == "FUTURES"
            ccys = _XPERP_CCY if xp else ("USDT",)
            rows = self._req("GET", "/api/v5/account/balance", {"ccy": ",".join(ccys)})
            det = {}
            for acct in rows or []:
                for d in (acct or {}).get("details") or []:
                    c = str(d.get("ccy") or "").upper()
                    if c in ccys:
                        v = _pick(d)
                        if v is not None:
                            det[c] = det.get(c, 0.0) + v
            if not det:
                return None
            if xp:
                try:
                    _sc = str(self._config().get("settleCcy") or "").upper()
                except Exception:
                    _sc = ""
                if _sc in det:
                    return det[_sc]
                return sum(det.values())
            return det.get("USDT")
        except Exception:
            return None

    def current_leverage(self, symbol):
        """교차 레버리지 설정(account/leverage-info, mgnMode=cross)."""
        try:
            rows = self._req("GET", "/api/v5/account/leverage-info",
                             {"instId": self._inst_of(symbol), "mgnMode": "cross"})
            vals = [_f(r.get("lever")) for r in (rows or []) if _f(r.get("lever"))]
            return min(vals) if vals else None
        except Exception:
            return None

    def max_leverage(self, symbol):
        """종목 최대 레버리지(public/instruments의 lever). X-Perp(유럽 계정)는 리테일 상한 _XPERP_MAX_LEV로 자른다."""
        try:
            inst = self._inst_of(symbol)
            lv = self._spec(inst).get("lever")
            if self._itype(inst) == "FUTURES" and lv:
                lv = min(float(lv), float(_XPERP_MAX_LEV))
            return lv
        except Exception:
            return None

    def set_leverage(self, symbol, lev) -> bool:
        """교차 레버리지 설정(교차는 posSide 불필요 - 롱숏 공통)."""
        self._req("POST", "/api/v5/account/set-leverage",
                  body={"instId": self._inst_of(symbol), "lever": f"{float(lev):g}", "mgnMode": "cross"})
        return True

    def ensure_leverage(self, symbol, size, price) -> dict:
        """Bybit·Bitget ensure_leverage와 같은 수학: 필요=명목/(가용×0.85), ×1.3 여유, 상한 캡, 상향만.
        반환 {ab, notional, cur, new|None, error|None} — 호출측 로그 전용, 예외 없음."""
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

    # ── 포지션 ───────────────────────────────────────────────────────────────
    def list_open_positions(self) -> list[Position]:
        rows = self._req("GET", "/api/v5/account/positions", {"instType": self._market()[0]})
        out: list[Position] = []
        for p in rows or []:
            n = _f(p.get("pos")) or 0.0
            if n == 0:
                continue
            inst = str(p.get("instId") or "")
            ps = str(p.get("posSide") or "net").lower()
            # net 모드: pos 부호 = 방향(+롱, -숏). 양방향 모드: pos는 양수, posSide가 방향.
            sign = (1 if n > 0 else -1) if ps == "net" else (1 if ps == "long" else -1)
            coin = self._to_coin(inst, abs(n))
            raw = dict(p)
            raw["_contracts"] = str(p.get("pos")).strip().lstrip("-")   # 계약 수 원문(지정가 청산이 그대로 쓴다)
            raw["size"] = f"{coin:g}"                       # BTC 수량(앱 표시·로그용)
            if p.get("avgPx") not in (None, ""):
                raw["avgPrice"] = p.get("avgPx")             # 앱이 찾는 평단 키(_btc_move_stop_be, 체결 갭)
            # 앱 이름 'BTCUSDT'는 이 계정에서 앱이 거래하는 종목(_market) 하나에만 준다(2026-11-11 11h 감사 D P2-3): 회원이 따로 든 BTC-USDC-SWAP,
            # BTC-USD-SWAP까지 'BTCUSDC', 'BTCUSD'로 바꾸면 자산별 BTC 청산(X2+TR, 24시간 청산)이 그 포지션도 닫았다. 다른 종목은 'OKX:원래 이름'
            # (자산 판정이 BTC로 보지 않는다 - [모든 포지션 청산]만 닫는다).
            try:
                _own = self._market()[1]
            except Exception:
                _own = None
            _sym = self._symbol(inst) if (not _own or inst == _own) else f"OKX:{inst}"
            out.append(Position(account_id="okx", account_name="okx",
                                symbol=_sym, net_qty=sign * coin, raw=raw))
        self._warm()
        return out

    def _warm(self):
        """이 계정 BTC 종목(_market)과 그 규격을 미리 캐시(진입 경로는 진입 직전에 포지션을 읽는다 - 진입 주문 앞에 조회를 더하지 않게 - 진입 1초 목표).
        실패는 조용히 - 진입 때 다시 읽고, 그때도 못 읽으면 주문하지 않는다."""
        try:
            self._spec(self._market()[1])
        except Exception:
            pass

    def _close_one(self, pos: Position):
        """포지션 하나 시장가 전량 청산(close-position). autoCxl=True - 그 포지션의 대기 청산 주문(손절)도 OKX가 같이 취소."""
        inst = str(pos.raw.get("instId") or "") or self._inst_of(pos.symbol)   # 그 포지션의 실제 종목(파생 이름으로 다시 만들지 않는다)
        body = {"instId": inst, "mgnMode": str(pos.raw.get("mgnMode") or "cross"), "autoCxl": True}
        ps = str(pos.raw.get("posSide") or "net").lower()
        if ps in ("long", "short"):
            body["posSide"] = ps
        self._req("POST", "/api/v5/trade/close-position", body=body)

    def _cancel_left_stops(self, inst: str):
        """플랫 확인 뒤 그 종목에 남은 손절 알고 주문 정리(빈 종목의 손절이 트리거돼 반대 포지션을 여는 일 방지).
        OKX가 autoCxl로 이미 지웠으면 빈 목록이라 아무것도 안 한다. 실패는 조용히(청산 결과에 영향 없음)."""
        try:
            left = self._sl_algos(inst)
            if left:
                self._req("POST", "/api/v5/trade/cancel-algos",
                          body=[{"algoId": str(r.get("algoId")), "instId": inst} for r in left])
        except Exception:
            pass

    def flatten_all(self, dry_run: bool = True, only=None) -> FlattenResult:
        # only = 그 자산 종목만(2026-10-17 자산별 청산 - 같은 계좌의 다른 코인 스왑은 건드리지 않는다)
        _mine = (lambda sym: True) if only is None else (lambda sym: bool(only(str(sym or ""))))
        plan = [p for p in self.list_open_positions() if _mine(p.symbol)]
        res = FlattenResult(dry_run=dry_run, planned=list(plan))
        if dry_run or not plan:
            return res
        for pos in plan:
            try:
                self._close_one(pos)
            except Exception as e:
                res.errors.append(f"{pos.symbol}: {e}")
        time.sleep(1)
        try:
            still = [s for s in self.list_open_positions() if _mine(s.symbol)]
            res.closed = [p for p in plan if not any(s.symbol == p.symbol for s in still)]
            for p in still:
                res.errors.append(f"STILL OPEN after flatten: {p.symbol} net={p.net_qty}")
            for sym in sorted({p.symbol for p in res.closed}):
                self._cancel_left_stops(self._inst_of(sym))
        except Exception as e:
            res.errors.append(f"post-flatten re-check failed: {e}")
        return res

    def close_symbol(self, symbol, dry_run: bool = True) -> dict:
        """이 심볼 포지션만 청산(다른 심볼 무접촉) — 연속 세션 '청산 확인 후 진입'용.
        close-position + 최대 ~6초 재확인 폴링. 반환 {closed: bool, had: n, error}."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"closed": True, "had": 0}
        if dry_run:
            return {"closed": False, "had": len(mine), "dry_run": True}
        for pos in mine:
            try:
                self._close_one(pos)
            except Exception as e:
                return {"closed": False, "had": len(mine), "error": f"{pos.symbol}: {e}"}
        for _ in range(6):                                   # 죽은 것 '확인' 후에만 True
            time.sleep(1)
            try:
                if not any(p.symbol == sym for p in self.list_open_positions()):
                    self._cancel_left_stops(self._inst_of(sym))
                    return {"closed": True, "had": len(mine)}
            except Exception:
                pass
        return {"closed": False, "had": len(mine), "error": "still open after close"}

    # ── 주문 ─────────────────────────────────────────────────────────────────
    def _order_body(self, inst, bside, sz, *, ord_type, px=None, tag=None, stop_px=None,
                    entry_side=None, hedge=False):
        body = {"instId": inst, "tdMode": "cross", "side": bside, "ordType": ord_type, "sz": sz}
        if px is not None:
            body["px"] = px
        if hedge and entry_side:
            body["posSide"] = "long" if entry_side == "LONG" else "short"
        c = clord_id(tag)
        if c:
            body["clOrdId"] = c
        if stop_px is not None:
            # 진입에 손절 첨부: 트리거=최종 체결가(Bybit LastPrice와 같은 기준), slOrdPx -1 = 트리거 시 시장가
            body["attachAlgoOrds"] = [{"slTriggerPx": stop_px, "slOrdPx": "-1", "slTriggerPxType": "last"}]
        return body

    @staticmethod
    def _posmode_err(e) -> bool:
        """포지션 모드가 안 맞아 처리 전에 거절된 오류인가(posSide 누락·불일치). 51000 = Parameter {posSide} error 꼴."""
        m = str(e)
        return "posSide" in m or "51000" in m or "posside" in m.lower()

    def place_entry(self, *, symbol, side, size, stop_loss_price=None, custom_tag=None,
                    dry_run: bool = True, **_ignored) -> dict:
        """스왑 시장가 진입(+손절 첨부 attachAlgoOrds). size = BTC 수량 → 계약 수로 변환.
        반환은 Bybit·Bitget place_entry와 같은 형태: would_place/entry/stop/stop_error/error.
        custom_tag → clOrdId(영문·숫자 32자로 정리)."""
        _s = str(side).upper()
        if _s not in ("LONG", "SHORT"):               # LONG/SHORT 외 값이 조용히 sell 되면 방향 반전
            return {"error": f"invalid side: {side!r} (expected LONG/SHORT)"}
        try:
            inst = self._inst_of(symbol)
        except Exception as e:
            return {"error": str(e)[:240]}               # 'market lookup failed: code=' = 주문을 안 냈다(확정)
        bside = "buy" if _s == "LONG" else "sell"
        _capped = None
        try:
            sz = self._to_contracts(inst, size)
            spx = self._snap_px(inst, stop_loss_price) if stop_loss_price else None
            _mx = self._spec(inst).get("maxMktSz")
            if _mx and float(sz) > _mx:
                # 시장가 1회 상한(2026-11-05 리뷰 P1: X-Perp 2 BTC 넘으면 51202 거절 = 놓친 거래). 나눠 내지 않고 상한까지만 -
                # 위험은 1R보다 작아진다(넘지 않는다).
                _lot = self._spec(inst)["lotSz"]
                _n = math.floor(_mx / _lot + 1e-9) * _lot
                _capped = {"from": sz, "to": f"{_n:.{_dec(_lot)}f}"}
                sz = _capped["to"]
        except Exception as e:
            return {"error": f"not sent: OKX instrument spec unavailable: {e}"[:200]}
        if float(sz) <= 0:
            return {"error": f"qty<=0 (OKX 최소 수량 미만: {size} BTC)"}
        hedge = self._hedge(allow_stale=True)            # 진입 앞에 설정을 다시 읽지 않는다(낡았으면 51000 재시도가 바로잡는다)
        body = self._order_body(inst, bside, sz, ord_type="market", tag=custom_tag, stop_px=spx,
                                entry_side=_s, hedge=hedge)
        if dry_run:
            return {"would_place": body, "would_place_stop": spx}
        try:
            res = self._req("POST", "/api/v5/trade/order", body=body)
        except Exception as e:
            if self._posmode_err(e):
                # 포지션 모드 캐시가 낡았다(회원이 OKX에서 바꿈) - 설정을 다시 읽어 모드가 달라졌으면 한 번만 다시.
                # 처리 전 거절이라 이중 진입 위험 없음(Bitget 40774·Bybit 10001 재시도와 같은 원칙).
                try:
                    hedge2 = str(self._config(fresh=True).get("posMode") or "") == "long_short_mode"
                except Exception:
                    hedge2 = hedge
                if hedge2 != hedge:
                    body = self._order_body(inst, bside, sz, ord_type="market", tag=custom_tag, stop_px=spx,
                                            entry_side=_s, hedge=hedge2)
                    try:
                        res = self._req("POST", "/api/v5/trade/order", body=body)
                        return {"entry": (res or [{}])[0], "stop": bool(spx), "hedge_mode": hedge2,
                                "stop_error": None if spx else "no stop provided"}
                    except Exception as e2:
                        return {"error": f"{e2} (posMode retry)"}
            return {"error": str(e)}
        out = {"entry": (res or [{}])[0], "stop": bool(spx), "hedge_mode": hedge,
               "stop_error": None if spx else "no stop provided"}
        if _capped:
            out["size_capped"] = _capped
        return out

    # ── 지정가 체결 정책(대표 2026-07-15) — 진입/청산 지정가 도전용 프리미티브 ──
    def place_limit_entry(self, *, symbol, side, size, price, stop_loss_price=None,
                          custom_tag=None, dry_run: bool = True) -> dict:
        """post_only 지정가 진입(+손절 첨부). 반환 {order_id}|{error}. dry_run: would_place."""
        _s = str(side).upper()
        if _s not in ("LONG", "SHORT"):
            return {"error": f"invalid side: {side!r} (expected LONG/SHORT)"}
        try:
            inst = self._inst_of(symbol)
        except Exception as e:
            return {"error": str(e)[:240]}
        try:
            sz = self._to_contracts(inst, size)
            px = self._snap_px(inst, price)
            spx = self._snap_px(inst, stop_loss_price) if stop_loss_price else None
        except Exception as e:
            return {"error": f"not sent: OKX instrument spec unavailable: {e}"[:200]}
        if float(sz) <= 0:
            return {"error": f"qty<=0 (OKX 최소 수량 미만: {size} BTC)"}
        body = self._order_body(inst, "buy" if _s == "LONG" else "sell", sz, ord_type="post_only", px=px,
                                tag=custom_tag, stop_px=spx, entry_side=_s, hedge=self._hedge())
        if dry_run:
            return {"would_place": body}
        try:
            r = self._req("POST", "/api/v5/trade/order", body=body)
            return {"order_id": ((r or [{}])[0] or {}).get("ordId")}
        except Exception as e:
            return {"error": str(e)}

    def place_limit_close(self, *, symbol, price, dry_run: bool = True) -> dict:
        """reduce-only post_only 지정가 청산(전량). 반환 {order_id}|{error}|{flat}."""
        sym = self._symbol(symbol)
        mine = [p for p in self.list_open_positions() if p.symbol == sym]
        if not mine:
            return {"flat": True}
        pos = mine[0]
        inst = self._inst_of(sym)
        body = {"instId": inst, "tdMode": str(pos.raw.get("mgnMode") or "cross"),
                "side": "sell" if pos.net_qty > 0 else "buy", "ordType": "post_only",
                "px": self._snap_px(inst, price), "sz": str(pos.raw.get("_contracts"))}
        ps = str(pos.raw.get("posSide") or "net").lower()
        if ps in ("long", "short"):
            body["posSide"] = ps                    # 양방향: posSide가 '닫는 쪽'을 정한다(reduceOnly는 net 전용)
        else:
            body["reduceOnly"] = True
        if dry_run:
            return {"would_place": body}
        try:
            r = self._req("POST", "/api/v5/trade/order", body=body)
            return {"order_id": ((r or [{}])[0] or {}).get("ordId")}
        except Exception as e:
            return {"error": str(e)}

    def cancel_order(self, symbol, order_id) -> bool:
        try:
            self._req("POST", "/api/v5/trade/cancel-order",
                      body={"instId": self._inst_of(symbol), "ordId": str(order_id)})
            return True
        except Exception:
            return False   # 이미 체결/취소된 주문의 취소 실패는 무해

    def position_qty(self, symbol) -> float:
        """이 심볼 순포지션 수량(BTC, 체결 확인용) — 0.0=플랫, -1.0=조회 실패."""
        sym = self._symbol(symbol)
        try:
            return sum(abs(p.net_qty) for p in self.list_open_positions() if p.symbol == sym)
        except Exception:
            return -1.0

    def current_market_price(self, symbol):
        try:
            rows = self._req("GET", "/api/v5/market/ticker", {"instId": self._inst_of(symbol)}, auth=False)
            return float((rows or [{}])[0].get("last"))
        except Exception:
            return None

    # ── 손절 ─────────────────────────────────────────────────────────────────
    def _sl_algos(self, inst: str) -> list:
        """그 종목의 대기 중 손절 알고 주문(conditional·oco 중 slTriggerPx가 있는 것). 실패는 예외."""
        rows = self._req("GET", "/api/v5/trade/orders-algo-pending",
                         {"ordType": "conditional,oco", "instType": self._itype(inst), "instId": inst}) or []
        return [r for r in rows if str(r.get("instId") or "") == inst and _f(r.get("slTriggerPx"))]

    def stop_plans(self, symbol) -> list:
        """이 심볼에 걸린 손절 목록 [{orderId, price, triggerType, planType}] (Bitget stop_plans와 같은 모양).
        조회 실패는 예외로 올린다(호출측이 '판단 안 함'). 진입에 첨부한 손절은 체결 뒤 알고 주문이 되어 여기서 보인다."""
        inst = self._inst_of(symbol)
        return [{"orderId": str(r.get("algoId")), "price": _f(r.get("slTriggerPx")),
                 "triggerType": r.get("slTriggerPxType"), "planType": r.get("ordType")}
                for r in self._sl_algos(inst)]

    def set_stop(self, symbol, stop_price):
        """보호 손절가 이동(X2+TR 트레일). ①대기 손절 알고를 전부 amend-algos로 같은 값으로 ②없거나 전부 실패면
        포지션 전량 손절(closeFraction=1) 새로 등록. ⚠️취소를 먼저 하지 않는다 - 등록이 실패해도 기존 손절은 살아 있다
        (Bitget 2026-09-21 사고에서 정한 원칙). 반환 {"error": None|str, ...}."""
        sym = self._symbol(symbol)
        try:
            inst = self._inst_of(sym)
        except Exception as e:
            return {"error": f"market: {e}"[:240]}
        try:
            _px = self._snap_px(inst, stop_price)
        except Exception as e:
            return {"error": f"spec: {e}"}
        try:
            _sl = self._sl_algos(inst)
        except Exception as e:
            return {"error": f"algo query: {e}"}
        _mod_err = None
        if _sl:
            _ok, _errs = 0, []
            for _row in _sl:
                try:
                    self._req("POST", "/api/v5/trade/amend-algos",
                              body={"instId": inst, "algoId": str(_row.get("algoId")),
                                    "newSlTriggerPx": _px, "newSlOrdPx": "-1",
                                    "newSlTriggerPxType": _row.get("slTriggerPxType") or "last"})
                    _ok += 1
                except Exception as _e1:
                    _errs.append(str(_e1))
            if _ok:
                return {"error": None, "moved": _ok, "partial_errors": _errs[:3]}
            _mod_err = "; ".join(_errs)[:300] or "amend failed"
        try:
            pos = next((p for p in self.list_open_positions() if p.symbol == sym), None)
            if pos is None:
                return {"error": "no open position"}
            body = {"instId": inst, "tdMode": str(pos.raw.get("mgnMode") or "cross"),
                    "side": "sell" if pos.net_qty > 0 else "buy", "ordType": "conditional",
                    "slTriggerPx": _px, "slOrdPx": "-1", "slTriggerPxType": "last", "closeFraction": "1"}
            ps = str(pos.raw.get("posSide") or "net").lower()
            if ps in ("long", "short"):
                body["posSide"] = ps
            else:
                body["reduceOnly"] = True               # net 모드 전량 손절은 reduceOnly 필수
            self._req("POST", "/api/v5/trade/order-algo", body=body)
            return {"error": None}
        except Exception as e:
            return {"error": (f"amend: {_mod_err} / place: {e}" if _mod_err is not None else f"place: {e}")}

    # ── 봉·체결 이력 ─────────────────────────────────────────────────────────
    def block_4h(self, symbol, end_utc):
        """우리 4H 블록(22-02, 02-06, …)의 (시가, 종가). end_utc = 블록 마감 UTC datetime.
        Bybit·Bitget과 같은 규약: 1H 4개를 직접 조립(거래소 4H 그리드와 어긋남). 반환 (open, close) | None."""
        from datetime import timedelta
        start = end_utc - timedelta(hours=4)
        try:
            rows = self._req("GET", "/api/v5/market/candles",
                             {"instId": self._inst_of(symbol), "bar": "1H",
                              "after": int(end_utc.timestamp() * 1000), "limit": 10}, auth=False)
            # OKX candle: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], 최신→과거. after=그 시각보다 이른 봉.
            lst = sorted(rows or [], key=lambda r: int(r[0]))
            lst = [r for r in lst if start.timestamp() * 1000 <= int(r[0]) < end_utc.timestamp() * 1000]
            if len(lst) < 4:              # 봉 누락 = 판정 불가 → 호출측이 홀드(보수적)
                return None
            return float(lst[0][1]), float(lst[-1][4])
        except Exception:
            return None

    def closed_fills(self, start_ms: int) -> list[dict]:
        """청산 완료 포지션의 실현손익(트랙레코드 푸시용, 파생값만) — Bitget closed_fills와 같은 모양
        [{tid, ts_ms, symbol, pnl, fees, direction, entry_px, exit_px}].
        GET /api/v5/account/positions-history(포지션 단위, 최근 3개월): pnl = realizedPnl(수수료·펀딩까지 뺀 순손익),
        fees = 수수료(-|fee|)+펀딩. fills-history(체결 단위)는 한 포지션이 여러 줄로 쪼개지고 진입 수수료가 따로 와서 쓰지 않는다.
        tid = posId-cTime(포지션 수명 하나 = 한 값, 부분 청산 뒤 갱신돼도 같은 tid). ⚠ 실키 대조 전."""
        out, seen = [], set()
        after = None
        for _ in range(10):                                 # 최대 10페이지(1000포지션) 안전 상한
            params = {"instType": self._market()[0], "limit": 100}
            if after:
                params["after"] = after
            rows = self._req("GET", "/api/v5/account/positions-history", params) or []
            if not rows:
                break
            oldest = None
            for r in rows:
                try:
                    ut = int(r.get("uTime") or r.get("cTime") or 0)
                    oldest = ut if oldest is None else min(oldest, ut)
                    if ut < int(start_ms):
                        continue
                    tid = f"{r.get('posId') or ''}-{r.get('cTime') or ''}"
                    if tid in seen:
                        continue
                    seen.add(tid)
                    out.append({
                        "tid": tid,
                        "ts_ms": ut,
                        "symbol": self._symbol(r.get("instId")),
                        "pnl": _okx_net(r),
                        "fees": _okx_costs(r),
                        "direction": "LONG" if str(r.get("direction")).lower() == "long" else "SHORT",
                        "entry_px": _f(r.get("openAvgPx")) or None,
                        "exit_px": _f(r.get("closeAvgPx")) or None,
                    })
                except (TypeError, ValueError):
                    continue
            if oldest is None or oldest < int(start_ms) or len(rows) < 100:
                break
            after = oldest
        return out

    # ── 연결 테스트 정보 ─────────────────────────────────────────────────────
    def entry_info(self) -> list:
        """진입 관련 계정 정보(연결 테스트 로그용, 읽기전용). 변경은 안 함."""
        out = []
        if self.demo:
            out.append("데모 트레이딩(EQ_OKX_DEMO=1) - 실제 돈이 아닙니다")
        if self.base != _HOSTS["www"]:
            out.append(f"API 서버 = {self.base.replace('https://', '')}")
        try:
            c = self._config()
            pm = str(c.get("posMode") or "")
            if pm:
                out.append("포지션 모드 = " + ("양방향(long/short)" if pm == "long_short_mode" else "단방향(net)"))
        except Exception:
            pass
        try:
            _it, _id = self._market()
            out.append(f"거래 종목 = {_id}" + (" (X-Perp, OKX 유럽 계정의 BTC 무기한)" if _it == "FUTURES" else ""))
        except Exception:
            _id = _SWAP_ID
        lev = self.current_leverage("BTCUSDT")
        if lev:
            out.append(f"{_id} 레버리지(교차) {lev:g}x")
        ab = self.available_usdt()
        if ab is not None:
            out.append(f"가용 잔고(마진) ≈ {ab:,.0f} " + ("USD" if self._xperp() else "USDT"))
        out.append("주문 마진모드 = 교차(cross) 자동 지정")
        return out

    def account_uid(self):
        """이 키의 거래소 계정 ID(account/config의 uid, 문서 'Account ID of current request'). 진입 클레임을 계정 단위로 묶는 재료(11h 감사 A/D P1-1) -
        연결 테스트, 사전 점검에서만 부른다(진입 경로 아님). 10분 캐시. 못 읽으면 None."""
        try:
            return str((self._config() or {}).get("uid") or "").strip() or None
        except Exception:
            return None

    def key_info(self) -> list:
        """API 키 사전 점검 경고(빈 리스트 = 이상 없음). account/config의 perm·acctLv."""
        out = []
        c = self._config(fresh=True)
        perm = {p.strip().lower() for p in str(c.get("perm") or "").split(",") if p.strip()}
        if perm and "trade" not in perm:
            out.append("키에 거래(Trade) 권한이 없습니다 - 주문 불가")
        if "withdraw" in perm:
            out.append("키에 출금(Withdraw) 권한이 켜져 있습니다 - 앱에는 필요 없으니 끄는 것을 권장합니다")
        try:
            self._market()
            if _MKT_NA.get((self.base, self._kid)):
                # OKX 공개 고지(Risk & Compliance Disclosure 2026-07-08): 한국, 호주, 브라질, 영국 등은 파생상품을 제한할 수 있다.
                out.append("이 계정은 OKX에서 BTC 무기한(BTC-USDT-SWAP, X-Perp)을 거래할 수 없습니다 - 주문 불가. "
                           "OKX에서 선물(무기한) 거래를 켰는지, 계정을 인증한 거주 국가에서 파생상품이 열려 있는지 확인하세요")
        except Exception:
            pass
        if str(c.get("acctLv") or "") == "1":
            out.append("계정 모드가 현물(Spot) 모드라 무기한 주문이 막힙니다 - OKX에서 '현물과 선물' 이상 모드로 바꾸세요")
        return out

    def authenticate(self) -> None:
        if not self._host_ok:
            self._pick_host()                 # 서명 읽기로 키·패스프레이즈·지역을 함께 확인
        else:
            self._config(fresh=True)
        self._warm()

    def healthcheck(self) -> bool:
        self.authenticate()
        return True


def _okx_costs(r: dict) -> float:
    """수수료+펀딩 합(낸 돈 = 음수). 수수료는 부호와 상관없이 비용(-|fee|) - Bitget _bg_costs와 같은 이유(시장가라 리베이트 없음).
    펀딩은 받는 날도 있어 부호 그대로."""
    fee = _f(r.get("fee"))
    fund = _f(r.get("fundingFee"))
    return (-abs(fee) if fee is not None else 0.0) + (fund or 0.0)


def _okx_net(r: dict) -> float:
    """회원 실제 손익 = realizedPnl(OKX 문서: pnl + fee + fundingFee + liqPenalty + settledPnl). 없으면 pnl + 수수료·펀딩."""
    n = _f(r.get("realizedPnl"))
    if n is not None:
        return n
    return (_f(r.get("pnl")) or 0.0) + _okx_costs(r)
