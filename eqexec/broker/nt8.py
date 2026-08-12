# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# =========================
# broker/nt8.py — NinjaTrader 8 브리지 어댑터 (Lucid 경로, A안: 앱=두뇌 / NT8=씬 팔)
#
# Lucid는 API 크레덴셜을 주지 않는다(대표 2026-07-29 확정). 유일한 자동화 통로는
# NinjaTrader 플랫폼 자체이므로, 이 어댑터는 브로커 REST 대신 **localhost HTTP 브리지**를
# 연다. NT8 쪽에는 씬 애드온(EQAutopilotBridge.cs)이 1초 주기로 붙어서:
#   GET  /v1/pending  → 대기 명령(entry/stop/flatten)을 가져가 NT8 계정 API로 실행
#   POST /v1/ack      → 명령별 실행 결과(tid 멱등)
#   POST /v1/state    → 계정·포지션·잔고 스냅샷 + 하트비트
# 앱(두뇌)은 다른 어댑터와 동일한 place_entry/flatten_all 계약만 쓰면 된다.
#
# 안전 원칙:
#   - 바인드는 127.0.0.1 고정(외부 노출 없음), 토큰 헤더(X-EQ-Bridge-Token) 검증.
#   - dry_run=True는 절대 큐에 넣지 않는다(would_place만 반환).
#   - tid 멱등: 같은 tid는 두 번 실행되지 않는다(재시작 대비 저널 파일).
#   - 알몸 포지션 불가: entry는 stop_loss_price가 있으면 브래킷 명령 하나로 보내고,
#     애드온이 스탑 제출 실패 시 진입분을 즉시 플래튼한다(ProjectX placeoso 원칙과 동일).
#   - 하트비트 5초 초과 = unhealthy → 발주 거부(fail-closed).
#
# ⚠️ Windows 전용 경로(맥 미지원 확정, 대표 2026-07-29). 앱과 NT8은 같은 머신에서 돈다.
# =========================
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .base import BrokerAdapter, FlattenResult, Position

log = logging.getLogger("eqexec.nt8")

_ACK_TIMEOUT_S = 20          # 명령 → 애드온 ack 대기 상한(로컬이라 보통 <2s)
_HEARTBEAT_MAX_S = 5.0       # 이 이상 state 푸시가 없으면 unhealthy


class _BridgeState:
    """어댑터 ↔ HTTP 핸들러 공유 상태(락 보호)."""

    def __init__(self, journal_path: str):
        self.lock = threading.Lock()
        self.pending: list[dict] = []          # 아직 애드온이 안 가져간 명령
        self.acks: dict[str, dict] = {}        # tid → 결과
        self.seen_tids: set[str] = set()       # 멱등 저널(재시작 복원)
        self.last_state: dict = {}             # 애드온이 push한 최신 스냅샷
        self.last_state_ts: float = 0.0
        self.journal_path = journal_path
        self._load_journal()

    def _load_journal(self):
        try:
            with open(self.journal_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        self.seen_tids.add(json.loads(line)["tid"])
                    except Exception:
                        pass
        except FileNotFoundError:
            pass

    def journal(self, cmd: dict):
        self.seen_tids.add(cmd["tid"])
        try:
            os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"tid": cmd["tid"], "op": cmd.get("op"),
                                    "ts": time.time()}) + "\n")
        except OSError as ex:
            log.warning("nt8 journal write failed: %s", ex)


def _make_handler(state: _BridgeState, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):              # 기본 stderr 로그 침묵
            pass

        def _auth_ok(self) -> bool:
            return secrets.compare_digest(
                self.headers.get("X-EQ-Bridge-Token", ""), token)

        def _send(self, code: int, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad token"})
            if self.path == "/v1/ping":
                return self._send(200, {"ok": True, "ts": time.time()})
            if self.path == "/v1/pending":
                with state.lock:
                    cmds, state.pending = state.pending, []
                return self._send(200, {"commands": cmds})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth_ok():
                return self._send(401, {"error": "bad token"})
            try:
                body = self._read_json()
            except Exception as ex:
                return self._send(400, {"error": str(ex)})
            if self.path == "/v1/ack":
                tid = body.get("tid")
                if tid:
                    with state.lock:
                        state.acks[tid] = body
                return self._send(200, {"ok": True})
            if self.path == "/v1/state":
                with state.lock:
                    state.last_state = body
                    state.last_state_ts = time.time()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})

    return Handler


class NT8Broker(BrokerAdapter):
    """NT8 브리지 — 다른 어댑터와 동일 계약, 실행만 NinjaTrader 애드온에 위임."""

    name = "nt8"

    # 포트별 공유 브리지(2026-08-10): GUI는 연결 테스트·무장·청산마다 어댑터 인스턴스를
    # 새로 만든다 - 인스턴스마다 서버를 열면 두 번째부터 'Address already in use'로 죽는다.
    # 서버·상태를 포트 단위 싱글턴으로 공유하고, 인스턴스는 핸들만 잡는다.
    _BRIDGES: dict = {}                      # {port: (_BridgeState, server, token)}
    _BRIDGES_LOCK = threading.Lock()

    def __init__(self, cfg):
        # cfg: NT8Cfg(port, token, accounts, symbol_map, journal_path)
        self.cfg = cfg
        self.port = int(getattr(cfg, "port", 8377) or 8377)
        self.token = getattr(cfg, "token", "") or ""
        self.accounts = list(getattr(cfg, "accounts", []) or [])
        self.symbol_map = dict(getattr(cfg, "symbol_map", {}) or {})
        self._journal = (getattr(cfg, "journal_path", "") or
                         os.path.join(os.path.expanduser("~"), ".eqexec", "nt8_journal.jsonl"))
        self._state: _BridgeState | None = None

    # ── 수명 ──
    def authenticate(self) -> None:
        """브리지 서버 확보(포트 싱글턴 - 이미 떠 있으면 공유). NT8 접속 판정은 healthcheck."""
        if self._state is not None:
            return
        if not self.token:
            raise RuntimeError("nt8.token required — 앱과 애드온이 공유하는 브리지 토큰")
        with NT8Broker._BRIDGES_LOCK:
            ent = NT8Broker._BRIDGES.get(self.port)
            if ent is not None:
                state, _srv, tok = ent
                if tok != self.token:
                    raise RuntimeError(f"nt8 bridge token mismatch on port {self.port}")
                self._state = state
                return
            state = _BridgeState(self._journal)
            handler = _make_handler(state, self.token)
            server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
            t = threading.Thread(target=server.serve_forever,
                                 name="eq-nt8-bridge", daemon=True)
            t.start()
            NT8Broker._BRIDGES[self.port] = (state, server, self.token)
            self._state = state
        log.info("nt8 bridge listening on 127.0.0.1:%d", self.port)

    def shutdown(self):
        pass                                  # 공유 서버는 앱 수명과 함께 감(개별 종료 없음)

    def healthcheck(self) -> bool:
        """애드온 하트비트 판정. 미접속이면 **예외**(조용한 False = GUI 가짜 초록불 방지).
        브리지 서버가 방금 떴을 수 있어 애드온 첫 push(1초 주기)를 최대 4초 기다린다."""
        self.authenticate()
        deadline = time.time() + 4.0
        while time.time() < deadline:
            age = time.time() - self._state.last_state_ts
            if self._state.last_state_ts > 0 and age <= _HEARTBEAT_MAX_S:
                return True
            time.sleep(0.2)
        raise RuntimeError(
            "NT8 애드온 미접속 - NinjaTrader가 켜져 있고 EQAutopilotBridge 애드온이 컴파일"
            "(F5)돼 있는지, 애드온의 Token·Port가 앱 설정과 같은지 확인하세요")

    # ── 조회(애드온이 push한 스냅샷 기반) ──
    def _snapshot(self) -> dict:
        self.authenticate()
        with self._state.lock:
            return dict(self._state.last_state)

    def list_open_positions(self) -> list[Position]:
        out = []
        for p in self._snapshot().get("positions", []):
            q = int(p.get("net_qty") or 0)
            if q != 0:
                out.append(Position(account_id=str(p.get("account", "")),
                                    account_name=str(p.get("account", "")),
                                    symbol=str(p.get("instrument", "")),
                                    net_qty=q, raw=p))
        return out

    def position_qty(self, account_id, contract) -> int:
        sym = self._nt_symbol(contract)
        for p in self._snapshot().get("positions", []):
            if (str(p.get("account")) == str(account_id)
                    and str(p.get("instrument")) == sym):
                return int(p.get("net_qty") or 0)
        return 0

    def _accounts(self):
        """가용 계좌 자동 로드(2026-08-12 대표 "자동 읽기 안 되는 게 별로") - 브리지 tick의
        Account.All 열거를 ProjectX 인터페이스 모양으로. NT8이 켜져 있어야 응답한다."""
        return [{"name": a.get("name"), "id": a.get("name")}
                for a in self._snapshot().get("accounts", []) if a.get("name")]

    def account_balance(self, acct):
        for a in self._snapshot().get("accounts", []):
            if str(a.get("name")) == str(acct):
                return a.get("cash_value")
        return None

    # ── 명령 ──
    @staticmethod
    def _front_month(sym: str, today=None):
        """NT8 인스트루먼트 만기 자동 계산 - symbol_map 미설정 시 폴백(2026-08-12 신설).
        지수(NQ/MNQ/ES/MES): 분기물(3/6/9/12), 만기월 3째 금요일 8일 전 롤.
        금(GC/MGC): 유동월(2/4/6/8/12 - 10월은 유동성 없어 제외), 인도 전월 27일 롤.
        규칙이 어긋나는 예외 상황은 앱 설정 symbol_map 한 줄로 덮는다(맵이 항상 우선)."""
        import datetime as _dt
        d = today or _dt.date.today()
        base = str(sym).upper()

        def _third_friday(y, m):
            first = _dt.date(y, m, 1)
            return first + _dt.timedelta(days=(4 - first.weekday()) % 7 + 14)

        if base in ("NQ", "MNQ", "ENQ", "ES", "MES"):
            y, m = d.year, d.month
            for _ in range(8):
                if m in (3, 6, 9, 12):
                    if d <= _third_friday(y, m) - _dt.timedelta(days=8):
                        return f"{sym} {m:02d}-{y % 100:02d}"
                m += 1
                if m > 12:
                    m, y = 1, y + 1
            return None
        if base in ("GC", "MGC", "GCE"):
            y, m = d.year, d.month
            for _ in range(14):
                if m in (2, 4, 6, 8, 12):
                    py_, pm = (y, m - 1) if m > 1 else (y - 1, 12)
                    if d <= _dt.date(py_, pm, 27):
                        return f"{sym} {m:02d}-{y % 100:02d}"
                m += 1
                if m > 12:
                    m, y = 1, y + 1
            return None
        return None

    def _nt_symbol(self, contract) -> str:
        """EQ 계약 표기 → NT8 인스트루먼트 문자열(예: 'MNQ' → 'MNQ 09-26').
        우선순위: ①symbol_map(설정 오버라이드) ②이미 완전 표기면 그대로 ③만기 자동 계산."""
        c = str(contract)
        if c in self.symbol_map:
            return self.symbol_map[c]
        if " " in c:
            return c                                  # 이미 "MNQ 09-26" 완전 표기
        return self._front_month(c) or c

    def search_contracts(self, text: str, live: bool = False) -> list[dict]:
        """ProjectX/Tradovate와 동일 호출 계약. NT8은 계약 조회 API가 없어 정적 해석
        (symbol_map → 만기 자동 계산)이 곧 '활성 계약'이다. id에 NT 완전 표기("MNQ 09-26")를
        넣어야 place_entry(_nt_symbol 항등 통과)·잔여 포지션 대조(p.symbol == id)가 맞물린다.
        ☠️2026-08-12 첫 라이브 NQ 진입이 이 메서드 부재로 실패(대표 실전 발각) - 연결
        테스트는 인터페이스 구멍을 못 잡는다. 신규 선물 브로커는 place_entry/search_contracts/
        place_protective_stop/close_contract/account_balance 전부 있어야 발주가 돈다."""
        nt = self._nt_symbol(str(text))
        return [{"id": nt, "name": nt, "activeContract": True}]

    def _enqueue_and_wait(self, cmd: dict) -> dict:
        self.authenticate()
        tid = cmd["tid"]
        with self._state.lock:
            if tid in self._state.seen_tids:
                return {"duplicate": True, "tid": tid}   # 멱등: 이미 나간 명령
            self._state.journal(cmd)
            self._state.pending.append(cmd)
        deadline = time.time() + _ACK_TIMEOUT_S
        while time.time() < deadline:
            with self._state.lock:
                ack = self._state.acks.pop(tid, None)
            if ack is not None:
                if not ack.get("ok"):
                    raise RuntimeError(f"nt8 command failed: {ack.get('error')}")
                return ack
            time.sleep(0.1)
        raise TimeoutError(f"nt8 ack timeout ({cmd.get('op')}, tid={tid}) — "
                           "NT8 애드온 연결 상태를 확인하세요")

    def place_entry(self, account_id, contract_id, side, size: int, *,
                    order_type: int = 2, limit_price=None, stop_price=None,
                    stop_loss_ticks=None, stop_loss_price=None, take_profit_ticks=None,
                    custom_tag=None, dry_run: bool = True):
        """ProjectX/Tradovate와 동일 호출 계약. stop_loss_price가 있으면 브래킷 —
        애드온이 스탑 제출 실패 시 진입분 즉시 플래튼(알몸 포지션 불가)."""
        if not self.healthcheck() and not dry_run:
            raise RuntimeError("nt8 bridge unhealthy (하트비트 없음) — 발주 거부")
        sym = self._nt_symbol(contract_id)
        cmd = {"op": "entry", "tid": custom_tag or f"eq-{uuid.uuid4().hex[:12]}",
               "account": str(account_id), "instrument": sym,
               "side": ("buy" if str(side).lower() in ("buy", "long", "0") else "sell"),
               "qty": int(size),
               "order_type": ("limit" if int(order_type) == 1 else "market"),
               "limit_price": limit_price,
               "stop_loss_price": stop_loss_price}
        if dry_run:
            return {"dry_run": True, "would_place": cmd}
        ack = self._enqueue_and_wait(cmd)
        return {"entry": ack, "stop": ack.get("stop_order_id"),
                "stop_price": stop_loss_price}

    def place_protective_stop(self, account_id, contract, entry_side, size: int,
                              stop_price, dry_run: bool = True):
        sym = self._nt_symbol(contract)
        cmd = {"op": "stop", "tid": f"eqs-{uuid.uuid4().hex[:12]}",
               "account": str(account_id), "instrument": sym,
               "side": ("sell" if str(entry_side).lower() in ("buy", "long", "0")
                        else "buy"),
               "qty": int(size), "stop_price": stop_price}
        if dry_run:
            return {"dry_run": True, "would_place": cmd}
        return self._enqueue_and_wait(cmd)

    def close_contract(self, account_id, contract) -> dict:
        sym = self._nt_symbol(contract)
        cmd = {"op": "close", "tid": f"eqc-{uuid.uuid4().hex[:12]}",
               "account": str(account_id), "instrument": sym}
        return self._enqueue_and_wait(cmd)

    def flatten_all(self, dry_run: bool = True) -> FlattenResult:
        planned = self.list_open_positions()
        res = FlattenResult(dry_run=dry_run, planned=planned)
        if dry_run:
            return res
        cmd = {"op": "flatten", "tid": f"eqf-{uuid.uuid4().hex[:12]}",
               "accounts": self.accounts or None}
        try:
            self._enqueue_and_wait(cmd)
            res.closed = planned
        except Exception as ex:                          # noqa: BLE001 — 전부 보고
            res.errors.append(str(ex))
        return res
