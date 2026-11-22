"""ProjectX 실시간 허브(SignalR, 웹소켓) - 평가 계좌 감시가 시세를 밀어받고, 체결과 포지션 변경을 바로 알게 한다(2026-10-16).

왜: 감시는 1분봉 이력(History/retrieveBars, 30초에 50회 한도)을 1초마다 읽어 시세를 얻었다. 대표 2026-10-15 "1초 넘 느려", 10/16 "진짜 진입 시간
1초가 목표". 공식 문서(gateway.docs.projectx.com, Real Time Data Overview): 시세 허브 rtc.topstepx.com/hubs/market(SubscribeContractQuotes →
GatewayQuote(contractId, data)), 사용자 허브 /hubs/user(SubscribeAccounts, SubscribePositions/Orders/Trades(accountId) → GatewayUserAccount,
GatewayUserPosition, GatewayUserOrder, GatewayUserTrade). 인증 = 허브 주소의 access_token(REST와 같은 JWT). 협상 없이 웹소켓으로 바로 붙는다
(문서 예제의 skipNegotiation + WebSockets).

SignalR JSON 프로토콜만 쓴다(라이브러리 없이 websocket-client 하나): 붙자마자 {"protocol":"json","version":1}\\x1e, 서버 {}\\x1e, 그 뒤 메시지는
\\x1e로 끝나는 JSON - type 1 호출(target, arguments), 6 핑, 7 닫힘. 10초마다 핑을 보낸다.

안전 규약:
  · 이 모듈은 **읽기만** 한다 - 주문, 청산을 하지 않는다. 시세 값과 '이 계좌에 무엇이 바뀌었다'는 시각만 준다.
  · 감시는 포지션과 실현 손익을 여전히 REST로 읽는다(정본). 여기 이벤트는 그 캐시를 버리게 하는 신호일 뿐이다.
  · 끊기면 1, 2, 4 ... 30초 간격으로 다시 붙고, 붙을 때 토큰을 새로 받아 구독을 다시 건다. 실패해도 예외를 밖으로 내지 않는다(REST가 그대로 돈다).
  · 끄기: 환경변수 EQ_PX_RT_OFF=1.
"""
from __future__ import annotations

import json
import os
import threading
import time

RS = "\x1e"
_RTC = "wss://rtc.topstepx.com"
_PING_S = 10.0
_BACKOFF = (1, 2, 4, 8, 15, 30)


def rt_off() -> bool:
    return os.environ.get("EQ_PX_RT_OFF", "") == "1"


class _Hub:
    """허브 연결 하나(시세 또는 사용자). 데몬 스레드 하나가 연결, 수신, 핑, 재연결을 맡는다."""

    def __init__(self, base, path, token_fn, on_invoke, subs_fn, name, connect=None):
        self.base, self.path, self.name = base, path, name
        self.token_fn, self.on_invoke, self.subs_fn = token_fn, on_invoke, subs_fn
        self._connect = connect                      # 시험용 주입(websocket.create_connection 꼴)
        self.connected = False
        self.ws = None
        self.lock = threading.Lock()
        self.stop_ev = threading.Event()
        self.fails = 0
        self.last_rx = 0.0
        self.th = threading.Thread(target=self._run, name=f"px_rt_{name}", daemon=True)
        self.th.start()

    def _create(self, url):
        if self._connect is not None:
            return self._connect(url)
        import websocket                            # websocket-client
        # 인증서 묶음을 certifi로(앱 출시 전 검토 2026-10-20): websocket-client는 OpenSSL 기본 경로를 쓰는데, 맥 빌드(홈브루 파이썬)의 그 경로는
        # 회원 맥에 없다 - wss 악수가 실패하고 앱은 말없이 REST로만 돈다. requests가 쓰는 것과 같은 certifi 묶음을 넘긴다(없으면 종전대로).
        try:
            import certifi
            return websocket.create_connection(url, timeout=10, sslopt={"ca_certs": certifi.where()})
        except ImportError:
            return websocket.create_connection(url, timeout=10)

    def send(self, obj) -> bool:
        with self.lock:
            ws = self.ws
            if ws is None or not self.connected:
                return False
            try:
                ws.send(json.dumps(obj) + RS)
                return True
            except Exception:
                return False

    def invoke(self, target, *args) -> bool:
        return self.send({"type": 1, "target": target, "arguments": list(args)})

    def close(self):
        self.stop_ev.set()
        with self.lock:
            ws, self.ws, self.connected = self.ws, None, False
        try:
            if ws is not None:
                ws.close()
        except Exception:
            pass

    def _run(self):
        while not self.stop_ev.is_set():
            ws = None
            try:
                tok = self.token_fn(self.fails > 0)
                if not tok:
                    raise RuntimeError("no token")
                ws = self._create(f"{self.base}{self.path}?access_token={tok}")
                ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
                hs = ws.recv()
                if not isinstance(hs, str) or not hs.startswith("{"):
                    raise RuntimeError("handshake")
                _h = json.loads(hs.split(RS)[0] or "{}")
                if _h.get("error"):
                    raise RuntimeError(f"handshake error: {_h.get('error')}")
                try:
                    ws.settimeout(1.0)
                except Exception:
                    pass
                with self.lock:
                    self.ws, self.connected = ws, True
                self.fails = 0
                self.last_rx = time.time()
                for tgt, args in list(self.subs_fn() or []):   # 붙을 때마다 구독을 다시 건다
                    self.invoke(tgt, *args)
                last_ping = time.time()
                buf = ""
                while not self.stop_ev.is_set():
                    if time.time() - last_ping >= _PING_S:
                        self.send({"type": 6})
                        last_ping = time.time()
                    if time.time() - self.last_rx > 3 * _PING_S:
                        raise RuntimeError("silent")    # 30초 넘게 아무것도 안 오면(서버 핑도 없음) 다시 붙는다
                    try:
                        data = ws.recv()
                    except Exception as _e:
                        if type(_e).__name__ in ("WebSocketTimeoutException", "TimeoutError", "timeout"):
                            continue
                        raise
                    if data is None or data == "":
                        raise RuntimeError("closed")
                    self.last_rx = time.time()
                    buf += data if isinstance(data, str) else data.decode("utf-8", "replace")
                    while RS in buf:
                        frame, buf = buf.split(RS, 1)
                        if not frame:
                            continue
                        try:
                            m = json.loads(frame)
                        except ValueError:
                            continue
                        t = m.get("type")
                        if t == 1:
                            try:
                                self.on_invoke(str(m.get("target") or ""), list(m.get("arguments") or []))
                            except Exception:
                                pass
                        elif t == 7:
                            raise RuntimeError(f"server close: {m.get('error') or ''}")
            except Exception:
                self.fails += 1
            finally:
                with self.lock:
                    if self.ws is ws:
                        self.ws, self.connected = None, False
                try:
                    if ws is not None:
                        ws.close()
                except Exception:
                    pass
            if self.stop_ev.is_set():
                break
            self.stop_ev.wait(_BACKOFF[min(len(_BACKOFF) - 1, max(0, self.fails - 1))])


class PxRealtime:
    """자격증명 하나의 시세, 사용자 허브. quote(con) = (가격, 받은 시각, 순번), acct_event_ts(aid) = 그 계좌의 마지막 이벤트 시각."""

    def __init__(self, token_fn, base=_RTC, connect=None):
        self.token_fn = token_fn
        self.lock = threading.Lock()
        self.cons: set = set()
        self.accts: set = set()
        self.quotes: dict = {}
        self.acct_evt: dict = {}
        self.seq = 0
        self.market = _Hub(base, "/hubs/market", token_fn, self._on_market, self._market_subs, "market", connect)
        self.user = _Hub(base, "/hubs/user", token_fn, self._on_user, self._user_subs, "user", connect)

    # ── 구독 ──
    def _market_subs(self):
        with self.lock:
            return [("SubscribeContractQuotes", (c,)) for c in sorted(self.cons)]

    def _user_subs(self):
        with self.lock:
            out = [("SubscribeAccounts", ())]
            for a in sorted(self.accts):
                out += [("SubscribePositions", (a,)), ("SubscribeTrades", (a,)), ("SubscribeOrders", (a,))]
            return out

    def watch_contract(self, con) -> None:
        con = str(con or "")
        if not con:
            return
        with self.lock:
            new = con not in self.cons
            self.cons.add(con)
        if new:
            self.market.invoke("SubscribeContractQuotes", con)

    def watch_account(self, aid) -> None:
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            return
        with self.lock:
            new = aid not in self.accts
            self.accts.add(aid)
        if new:
            for tgt in ("SubscribePositions", "SubscribeTrades", "SubscribeOrders"):
                self.user.invoke(tgt, aid)

    # ── 이벤트 ──
    def _on_market(self, target, args):
        if target != "GatewayQuote" or len(args) < 2 or not isinstance(args[1], dict):
            return
        con, d = str(args[0]), args[1]
        px = d.get("lastPrice")
        try:
            px = float(px)
        except (TypeError, ValueError):
            return
        if not (px > 0) or px != px:
            return
        with self.lock:
            self.seq += 1
            self.quotes[con] = (px, time.time(), self.seq)

    def _on_user(self, target, args):
        if not target.startswith("GatewayUser"):
            return
        d = args[-1] if args else None
        if not isinstance(d, dict):
            return
        aid = d.get("accountId") if target != "GatewayUserAccount" else d.get("id")
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            return
        with self.lock:
            self.acct_evt[aid] = time.time()
        try:                                             # 진입 경로 계좌 목록 캐시(잔고)도 버린다(검토 P2-7)
            from .projectx import _rd_forget
            _rd_forget(("acct",))
        except Exception:
            pass

    # ── 읽기 ──
    def quote(self, con, max_age=2.0, after_ts=0.0):
        """(가격, 받은 시각, 순번) - max_age초 안에 받은 것만, after_ts 뒤에 받은 것만(정리 직전 두 번째 확인 = 첫 읽기 뒤의 다른 push). 없으면 None."""
        with self.lock:
            q = self.quotes.get(str(con or ""))
        if not q or (time.time() - q[1]) > float(max_age) or q[1] <= float(after_ts or 0.0):
            return None
        return q

    def acct_event_ts(self, aid) -> float:
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            return 0.0
        with self.lock:
            return float(self.acct_evt.get(aid, 0.0))

    def connected(self) -> bool:
        return bool(self.market.connected)

    def market_live(self, within=20.0, quote_within=5.0) -> bool:
        """시세 허브가 붙어 있고 within초 안에 무엇이든 받았고, quote_within초 안에 어떤 계약이든 시세를 받았다(핑만으로는 아니다 - 검토 P2-5).
        이때만 시세가 없는 계약은 가격이 그대로라는 뜻으로 본다."""
        now = time.time()
        with self.lock:
            lq = max((q[1] for q in self.quotes.values()), default=0.0)
        return bool(self.market.connected and (now - float(self.market.last_rx or 0)) < float(within)
                    and (now - lq) < float(quote_within))

    def close(self):
        self.market.close()
        self.user.close()


_REG: dict = {}
_REG_LOCK = threading.Lock()


def get_rt(broker):
    """ProjectX 브로커 객체(같은 자격증명이면 같은 실시간 연결) → PxRealtime 또는 None(꺼짐, 다른 브로커, 실패)."""
    if rt_off() or str(getattr(broker, "name", "") or "") != "projectx":
        return None
    try:                                                 # 라이브러리가 없으면 허브를 만들지 않는다(검토 P2-6 - 재시도, 토큰 재발급 폭주 없음)
        import websocket  # noqa: F401
    except Exception:
        return None
    try:
        from .projectx import _tok_key
        key = _tok_key(broker.cfg)
    except Exception:
        return None
    with _REG_LOCK:
        rt = _REG.get(key)
        if rt is not None:
            return rt

        _last_refresh = [0.0]

        def _tok(refresh=False, b=broker):
            # 다시 붙을 때 토큰을 새로 받는 것은 10분에 한 번까지 - 연결이 계속 실패해도 REST가 쓰는 공용 토큰을 30초마다 버리지 않게.
            try:
                if refresh and time.time() - _last_refresh[0] > 600:
                    _last_refresh[0] = time.time()
                    b._drop_token()
                b._ensure_token()
                return b._token
            except Exception:
                return None
        try:
            rt = PxRealtime(_tok, base=os.environ.get("EQ_PX_RTC", _RTC))
        except Exception:
            return None
        _REG[key] = rt
        return rt
