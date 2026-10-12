#!/usr/bin/env python3
# EQ Autopilot - 버그 조사 재검증(2026-10-06) 보강 오프라인 시험. 0=통과
# 1 가동 중 키(f1·f3)만 바꾼 계좌: 신호 때 건너뜀(무장 때 옛 키로 주문하지 않음) + 화면 '다시 무장해야 적용'과 같은 비교
#   + 재무장 로그 문구가 실제 동작(새 신호 진입 안 함, 마감 청산은 무장 때 키·계좌)과 맞음
# 2 미확인 진입 감시를 자산 ctx(_open_ctx)와 분리한 leg 단위 목록(_unconf_watch): 미니+마이크로 두 leg·다계좌에서
#   leg마다 감시·손절 재거치, 확정된 다른 leg·계좌의 감시를 건드리지 않음, 디스크 영속·재시작 복구
# 3 같은 신호 재처리: 처리 기록(_sig_seen·_sig_entered_day)을 새 세대 루프가 이어받음, 발주 전에 빠지는 낡은 루프는
#   기록을 되돌려 새 루프가 처리, 앱 재시작은 .entered.json(발행 뒤 진입 기록)으로 건너뜀
# 4 서버 불통 중 다른 자산을 무장해도 앞서 남긴 무장 사본을 덮지 않고 합침
# 5 마감 청산의 fired·retry를 새 세대가 이어받음(첫 실패 알림 1회, 재시도 성공 후속 보고 유지)
# 6 선물 진입 클레임 mid = 기기 id + 프로세스 난수 4자(같은 PC 다른 실행이 taken), 크립토 클레임은 그대로
# 브로커·네트워크·알림·실제 설정을 건드리지 않는다(전부 가짜). 사용: python executor/test_bughunt_fixup_offline.py
import ast
import datetime as _dt
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("EQ_NO_ALERT", "1")
os.environ.setdefault("EQ_ERR_REPORT", "0")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui  # noqa: E402
import requests  # noqa: E402
import autopilot_crypto  # noqa: E402
import tkinter.messagebox as _mb  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


# ── 격리: 파일 쓰기·알림·팝업 ──────────────────────────────────────────────────
_TMP = tempfile.mkdtemp()
eqgui._OPENCTX_PATH = os.path.join(_TMP, ".open_ctx.json")
eqgui._UNCONF_PATH = os.path.join(_TMP, ".unconf_watch.json")
LEDGER = []
eqgui._ledger_add = lambda *a, **k: LEDGER.append(a)
eqgui._mark_entered = lambda a: {}
eqgui._record_real_r = lambda *a, **k: None
subprocess.Popen = lambda *a, **k: None
eqgui._machine_id = lambda: "abcdef123456"
SHOWN = []
for _n in ("showinfo", "showwarning", "showerror"):
    setattr(_mb, _n, (lambda *a, **k: SHOWN.append(a)))
    setattr(eqgui.messagebox, _n, (lambda *a, **k: SHOWN.append(a)))
_real_time, _real_sleep = time.time, time.sleep


@contextmanager
def fake_clock(t0=1_000_000.0):
    clk = {"t": t0}
    time.time = lambda: clk["t"]
    time.sleep = lambda s: clk.__setitem__("t", clk["t"] + float(s))
    try:
        yield clk
    finally:
        time.time, time.sleep = _real_time, _real_sleep


# ── g1과 같은 꼴의 App 자리(MagicMock + 진짜 메서드 묶기) ──────────────────────
NORMAL = {"ok": True, "enabled": True, "force_dry_run": False, "tier": "royal",
          "caps": {"use": True, "autoentry": True, "connect": True}}
OFF = {"ok": False, "offline": True, "reason": "heartbeat error"}


def logs(me):
    return " ".join(str(c[0][0]) for c in me.log.call_args_list)


def acct(aid="111", one_r=600.0, on=True, label="A", broker="projectx"):
    return {"id": aid, "one_r": one_r, "on": on, "label": label, "broker": broker,
            "prop": {}, "pct": {}, "one_lim": 0.0, "manual": False}


def snap(aid="111", label="A", broker="projectx", f1="u", f3=""):
    return {"broker": broker, "f1": f1, "f2": "k", "f3": f3, "acct": aid, "one_r": 600.0,
            "prop": {}, "pct": {}, "one_lim": 0.0, "manual": False, "live": True, "label": label}


def bind(me, *names):
    for n in names:
        fn = getattr(eqgui.App, n)
        setattr(me, n, (lambda f: (lambda *a, **k: f(me, *a, **k)))(fn))


def app(accts_nq=None, creds=None):
    me = MagicMock()
    me.lang = "ko"
    me._acfg = {"NQ": {"broker": "projectx", "include": True,
                       "creds": creds or {"projectx": {"f1": "u", "f3": ""}},
                       "accounts": accts_nq if accts_nq is not None else [acct()]},
                "GC": {"broker": "projectx", "include": False, "creds": {"projectx": {"f1": "u", "f3": ""}},
                       "accounts": [acct(aid="g1", label="G")]},
                "BTC": {"broker": "bybit", "include": False, "creds": {}, "accounts": []}}
    me._sig_accts, me._auto_accts, me._sig_parked = {}, {}, {}
    me._sig_seen, me._sig_entered_day = None, {}
    me._feed_errs = 0
    me._watch_only = False
    me._sig_on, me._auto_on = True, True
    me._sig_gen, me._auto_gen = 1, 1
    me._killed = False
    me._fallback_mode = None
    me._kill_noted = False
    me._live_session = True
    me._live_ctx = {"live": True, "perm_auto": True, "perm_use": True}
    me._token = "tok"
    me._entered_at = {}
    me._clock_skew, me._clock_skew_ts = None, 0.0
    bind(me, "_live_acct_cfg", "_acct_broker", "_broker_of", "_accts_of", "_active_accts", "_creds_of",
         "_rearm_needs", "_fallback_kind", "_resume_parked_sig",
         "_note_clock", "_clock_skew_now", "_clock_skew_text", "_clock_warn_check")
    return me


class _R:
    ok = True
    text = "x"


def run_sig(me, gen, sig, on_cfg=None):
    """_sig_loop 한 바퀴. 반환 _enter_account에 간 cfg 목록."""
    entered = []
    me._enter_account = lambda cfg, *a: entered.append(cfg)
    orig = (requests.get, autopilot_crypto.decrypt, time.sleep)
    requests.get = lambda *a, **k: _R()
    autopilot_crypto.decrypt = lambda tok, txt: dict(sig)
    time.sleep = lambda _s: setattr(me, "_sig_on", False)
    if on_cfg is not None:
        _real = me._live_acct_cfg

        def _hook(k, c):
            on_cfg()
            return _real(k, c)
        me._live_acct_cfg = _hook
    try:
        eqgui.App._sig_loop(me, "http://feed", gen)
    finally:
        requests.get, autopilot_crypto.decrypt, time.sleep = orig
        if on_cfg is not None:
            me._live_acct_cfg = _real
    return entered


def sig_nq(sid="s1", pub=None):
    return {"id": sid, "tradeable": True, "direction": "long", "instrument": "NQ",
            "published_at": time.time() if pub is None else pub, "stop_price": 19900.0,
            "entry_ref": 20000.0, "size_mult": 1.0}


# ═════ 1: 가동 중 키만 바꾼 계좌 ═════════════════════════════════════════════════
me = app(creds={"projectx": {"f1": "새키", "f3": ""}})
c2, why = eqgui.App._live_acct_cfg(me, ("NQ", 0), snap(f1="u"))
ck("1 f1(키)이 무장 뒤 바뀌면 이번 신호에 진입하지 않음 + 사실 문장(옛 키로 주문 안 함)",
   c2 is None and "키가 무장 뒤 바뀌어" in why and "옛 키로" in why and "하세요" not in why
   and "하십시오" not in why, why)
me.lang = "en"
c2, why = eqgui.App._live_acct_cfg(me, ("NQ", 0), snap(f1="u"))
ck("1 EN 문장 한 쌍", c2 is None and "the key changed after arming" in why and "old key" in why, why)

me = app(creds={"projectx": {"f1": "u", "f3": "env-B"}})
ck("1 f3가 바뀌어도 건너뜀", eqgui.App._live_acct_cfg(me, ("NQ", 0), snap(f3="env-A"))[0] is None)
me = app(creds={"projectx": {"f1": " u ", "f3": ""}})
ck("1 앞뒤 공백만 다른 키는 같은 키(진입을 놓치지 않음)", eqgui.App._live_acct_cfg(me, ("NQ", 0), snap())[0] is not None)
me = app()
c2, _ = eqgui.App._live_acct_cfg(me, ("NQ", 0), snap())
ck("1 키가 같으면 종전처럼 지금 설정으로 만든 사본", c2 is not None and c2["f1"] == "u")

# 신호 루프에서: 키만 바꾼 계좌는 주문이 나가지 않는다(종전: 옛 키로 실주문)
me = app(creds={"projectx": {"f1": "새키", "f3": ""}})
me._sig_accts = {("NQ", 0): snap(f1="옛키")}
me._auto_accts = {("NQ", 0): [{"broker": "projectx", "f1": "옛키", "f3": "", "acct": "111"}]}
ent = run_sig(me, 1, sig_nq())
ck("1 신호 루프: 키를 바꾼 계좌는 진입 안 함 + 건너뜀 사실 한 줄", not ent and "키가 무장 뒤 바뀌어" in logs(me), logs(me))
ck("1 같은 계좌가 화면 '다시 무장해야 적용' 목록에도 있음", eqgui.App._rearm_needs(me, "NQ") == ["A"],
   eqgui.App._rearm_needs(me, "NQ"))

# 표시(_rearm_needs)와 동작(_live_acct_cfg)이 갈리지 않는다 - 같은 비교
_cases = [({"f1": "u", "f3": ""}, "같음"), ({"f1": "v", "f3": ""}, "f1"), ({"f1": "u", "f3": "x"}, "f3"),
          ({"f1": " u", "f3": " "}, "공백")]
_agree = []
for _cr, _nm in _cases:
    me = app(creds={"projectx": dict(_cr)})
    me._sig_accts = {("NQ", 0): snap()}
    me._auto_accts = {("NQ", 0): [{"broker": "projectx", "f1": "u", "f3": "", "acct": "111"}]}
    _skip = eqgui.App._live_acct_cfg(me, ("NQ", 0), snap())[0] is None
    _need = bool(eqgui.App._rearm_needs(me, "NQ"))
    _agree.append((_nm, _skip, _need))
ck("1 키 변경 판정: 신호 때 건너뜀 ⇔ 화면 '다시 무장해야 적용'(같음·f1·f3·공백)",
   all(s == n for _, s, n in _agree) and [s for _, s, _ in _agree] == [False, True, True, False], _agree)

# 크립토: 키가 곧 거래소 계정 - 다른 계정 키로 바꾸면 옛 계정으로 주문하지 않는다
me = app()
me._acfg["BTC"] = {"broker": "bybit", "include": True, "creds": {"bybit": {"f1": "NEWACCOUNTKEY", "f3": ""}},
                   "accounts": [acct(aid="", broker="bybit", label="BY")]}
ck("1 BTC(바이빗) 키를 다른 계정 것으로 바꾸면 건너뜀",
   eqgui.App._live_acct_cfg(me, ("BTC", 0), snap(aid="", broker="bybit", f1="OLDACCOUNTKEY", label="BY"))[0] is None)

# 재무장 로그 문구 = 실제 동작
me = app(accts_nq=[acct(), acct(aid="222", label="새행")], creds={"projectx": {"f1": "새키", "f3": ""}})
me._sig_accts = {("NQ", 0): snap(f1="옛키")}
me._auto_accts = {("NQ", 0): [{"broker": "projectx", "f1": "옛키", "f3": "", "acct": "111", "live": True}]}
eqgui.App._note_rearm_needs(me)
_l = logs(me)
ck("1 재무장 로그: '다시 무장하기 전까지 새 신호에 진입하지 않습니다' + 마감 청산은 무장 때 키·계좌(사실)",
   "다시 무장하기 전까지 새 신호에 진입하지 않습니다" in _l and "마감 자동 청산은 무장 때의 키·계좌로 돕니다" in _l
   and "아직 무장되지 않았습니다" not in _l and "하세요" not in _l and "하십시오" not in _l, _l)
me.lang = "en"
me._rearm_noted = {}
eqgui.App._note_rearm_needs(me)
_l = logs(me)
ck("1 재무장 로그 EN 한 쌍", "do not enter new signals until they are armed again" in _l
   and "keeps using the key and account from arming" in _l, _l)


# ═════ 2: 미확인 진입 감시 = leg 단위 목록 ══════════════════════════════════════
class PXE:
    """ProjectX 모양 가짜. raise_cons = 진입 POST가 예외(접수 뒤 타임아웃)로 끝나는 계약(포지션은 안 보임)."""
    name = "projectx"

    def __init__(self, raise_cons=(), accounts=(("ACC1", 101),)):
        self.raise_cons = set(raise_cons)
        self.accts = [{"name": n, "id": i} for n, i in accounts]
        self.visible, self.orders, self.stops, self.entries = {}, [], [], []
        self.qty_none = False
        self.sign = 1                                # 보이는 포지션 방향(+1 롱, -1 숏)
        self.listed = True                           # 진입 경로의 잔여 조회에도 보이는가

    def _accounts(self):
        return list(self.accts)

    def list_open_positions(self):
        """ProjectX 꼴: account_id=str(id), symbol=contractId, 부호 있는 net_qty, raw에 _accountId·contractId."""
        if self.qty_none:
            raise RuntimeError("HTTP 502")
        aid = self.accts[0]["id"]
        return [SimpleNamespace(account_id=str(aid), symbol=c, net_qty=self.sign * q,
                                raw={"_accountId": aid, "contractId": c, "size": q})
                for c, q in self.visible.items() if q and self.listed]

    def current_market_price(self, con):
        return None

    def place_entry(self, account_id=None, contract_id=None, side=None, size=None, order_type=None,
                    stop_loss_price=None, custom_tag=None, dry_run=True):
        self.entries.append((contract_id, size))
        time.sleep(0.3)
        if contract_id in self.raise_cons:
            raise RuntimeError("HTTPSConnectionPool(host='api.topstepx.com', port=443): Read timed out.")
        self.visible[contract_id] = size
        self.orders.append({"id": 100 + len(self.orders), "type": 4, "contractId": contract_id, "side": 1,
                            "size": size, "stopPrice": stop_loss_price})
        return {"entry": {"orderId": 1}, "stop": {"id": 7}, "stop_price": stop_loss_price}

    def position_qty(self, aid, con):
        return None if self.qty_none else self.visible.get(con, 0)

    def _open_orders(self, aid):
        return list(self.orders)

    def _cancel_order(self, aid, oid):
        self.orders = [o for o in self.orders if o.get("id") != oid]

    def place_protective_stop(self, aid, con, side, size, px, *, custom_tag=None):
        self.stops.append((con, int(size), float(px)))
        self.orders.append({"id": 900 + len(self.stops), "type": 4, "contractId": con, "side": 1,
                            "size": int(size), "stopPrice": float(px)})
        return {"stop": {"id": 900 + len(self.stops)}, "stop_price": float(px)}

    def close_contract(self, aid, con):
        pass


class IBLike(PXE):
    """주문 목록을 못 보는 브로커(IBKR 모양) - _open_orders가 없다."""
    name = "ibkr"

    def __getattribute__(self, n):
        if n == "_open_orders":
            raise AttributeError(n)
        return super().__getattribute__(n)


class Stub:
    """진입 경로·하트비트 감시가 부르는 것만 기록, 나머지는 무해하게. 감시·ctx 메서드는 진짜를 쓴다."""
    lang = "ko"
    STOP_ORDER_CHECK_SEC = eqgui.App.STOP_ORDER_CHECK_SEC

    def __init__(self):
        self.logs, self.alerts, self.fills, self.sent, self.claims = [], [], [], [], []
        self._open_ctx, self._flat_seen, self._nostop_seen, self._stop_chk_at, self._size_seen = {}, {}, {}, {}, {}
        self._unconf_watch = {}
        self.claim_ok = True

    def log(self, m):
        self.logs.append(str(m))

    def _claim_entry(self, sig, aid, **k):
        self.claims.append((aid, k))
        return self.claim_ok

    def _resolve_contract(self, b, s):
        return f"CON.F.US.{s}.Z26"

    def _nt8_standby(self):
        return False

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def _note_fill(self, asset, **k):
        self.fills.append((asset, k))

    def _send_fill(self, asset, closed=False):
        self.sent.append((asset, closed))

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_unconf_watch_add", "_unconf_watch_drop", "_unconf_save", "_unconf_clear_state", "_unconf_restore_broker",
           "_check_unconf_watch", "_check_stop_alive", "_check_stop_size", "_learn_seen_stop",
           "_remember_open", "_persist_open_ctx", "_run_futures_entry", "_creds_of"):
    setattr(Stub, _n, eqgui.App.__dict__[_n])
Stub._unconf_key = eqgui.App.__dict__["_unconf_key"]

NQC, MNQC = "CON.F.US.NQ.Z26", "CON.F.US.MNQ.Z26"


def enter(st, b, legs, sc="ACC1"):
    eqgui.App._run_futures_entry(st, b, sc, legs, "LONG", 19900.0, {"id": "t-NQ-1", "entry_ref": 20000.0},
                                 True, _dt.datetime.now(), "NQ", None)


def disk_watch():
    try:
        return json.load(open(eqgui._UNCONF_PATH, encoding="utf-8"))
    except Exception:
        return {}


def disk_ctx():
    try:
        return json.load(open(eqgui._OPENCTX_PATH, encoding="utf-8"))
    except Exception:
        return {}


def _reset_disk():
    for p in (eqgui._UNCONF_PATH, eqgui._OPENCTX_PATH):
        try:
            os.remove(p)
        except OSError:
            pass


# 2a: 미니+마이크로 둘 다 미확인 → leg마다 감시, 자산 ctx는 비어 있음
_reset_disk()
with fake_clock() as clk:
    st, b = Stub(), PXE(raise_cons={NQC, MNQC})
    enter(st, b, [("NQ", 1), ("MNQ", 2)])
    _keys = sorted(st._unconf_watch)
    ck("2a 두 leg 모두 미확인 → 감시 목록에 leg마다 한 칸(종전: 마지막 leg만)",
       _keys == sorted([f"NQ|101|{NQC}", f"NQ|101|{MNQC}"]), _keys)
    ck("2a 자산 ctx(_open_ctx)는 건드리지 않음(확정 포지션만 든다)", "NQ" not in st._open_ctx, st._open_ctx)
    _ua = [a for a in st.alerts if a[0] == "entry_unconfirmed"]
    ck("2a 회원 알림: leg마다 자기 심볼로 '계속 확인 … 없으면 겁니다'(사실·KO/EN)",
       len(_ua) == 2 and "이 계좌의 NQ 포지션을 계속 확인" in _ua[0][1] and "이 계좌의 MNQ 포지션을 계속 확인" in _ua[1][1]
       and all("없으면 겁니다" in k and "places one if there is none" in e and "하세요" not in k for _, k, e in _ua),
       [a[1] for a in _ua])
    _dw = disk_watch()
    ck("2a 디스크에도 두 칸 + 비밀·브로커 객체 없음",
       sorted(_dw) == _keys and all(not ({"f1", "f2", "f3", "b"} & set(v)) for v in _dw.values())
       and _dw[f"NQ|101|{NQC}"]["stop"] == 19900.0 and _dw[f"NQ|101|{MNQC}"]["size"] == 2, _dw)
    # 하트비트: 두 포지션이 뒤늦게 보이고 손절이 없다 → 각 leg를 자기 수량으로 재거치
    b.visible = {NQC: 1, MNQC: 2}
    st._check_unconf_watch()
    ck("2a 첫 발견: leg마다 '보입니다' 한 줄, 손절 미발견 1/2(leg 키로 따로 셈)",
       sum("확인하지 못했던 진입" in m and "보입니다" in m for m in st.logs) == 2
       and st._nostop_seen.get(f"NQ|101|{NQC}") == 1 and st._nostop_seen.get(f"NQ|101|{MNQC}") == 1, st._nostop_seen)
    clk["t"] += eqgui.App.STOP_ORDER_CHECK_SEC + 1
    st._check_unconf_watch()
    ck("2a 연속 2회 손절 없음 → 두 leg 모두 기억한 자리·실제 수량으로 손절(종전: 미니 leg는 손절 없이 남음)",
       sorted(b.stops) == sorted([(NQC, 1, 19900.0), (MNQC, 2, 19900.0)]), b.stops)
    _sr = [a for a in st.alerts if a[0] == "stop_restored"]
    ck("2a 재거치 알림은 미확인 문구(걸린 적 없을 수 있는 손절을 '사라졌다'고 하지 않음)·KO/EN",
       len(_sr) == 2 and all("진입 결과를 확인하지 못했던" in k and "사라" not in k and "could not be confirmed" in e
                             for _, k, e in _sr)
       and sorted("(계좌 ACC1 NQ)" in k or "(계좌 ACC1 MNQ)" in k for _, k, _e in _sr) == [True, True],
       [a[1] for a in _sr])
    clk["t"] += eqgui.App.STOP_ORDER_CHECK_SEC + 1
    st._check_unconf_watch()
    ck("2a 손절이 걸린 뒤엔 다시 걸지 않음", len(b.stops) == 2, b.stops)
    # 포지션이 사라지면(마감 청산 등) 연속 2회 뒤 놓는다 - 열렸다고 보고한 적이 없으니 청산 보고도 없다
    b.visible = {}
    st._check_unconf_watch()
    ck("2a 한 번 안 보인 건 안 믿음", len(st._unconf_watch) == 2)
    st._check_unconf_watch()
    ck("2a 연속 2회 안 보이면 감시를 놓음(청산 보고 없음)·디스크에서도 빠짐",
       not st._unconf_watch and not st.sent and disk_watch() == {}, (st._unconf_watch, st.sent, disk_watch()))

# 2b: 미니 미확인 + 마이크로 확정 → 미니는 감시 목록, 마이크로는 자산 ctx(서로 안 덮음)
_reset_disk()
with fake_clock() as clk:
    st, b = Stub(), PXE(raise_cons={NQC})
    enter(st, b, [("NQ", 1), ("MNQ", 3)])
    ck("2b 미니(미확인)는 감시 목록에 남음(종전: 마이크로 확정이 자산 ctx를 덮어 미니 감시가 사라짐)",
       list(st._unconf_watch) == [f"NQ|101|{NQC}"], list(st._unconf_watch))
    ck("2b 마이크로(확정)는 자산 ctx·체결 보고 - 미확인 표시 없음",
       st._open_ctx.get("NQ", {}).get("sym") == MNQC and not st._open_ctx["NQ"].get("unconfirmed")
       and len(st.fills) == 1, (st._open_ctx.get("NQ"), st.fills))
    b.visible[NQC] = 1
    st._check_unconf_watch()
    clk["t"] += eqgui.App.STOP_ORDER_CHECK_SEC + 1
    st._check_unconf_watch()
    ck("2b 미니 leg에만 손절 재거치, 마이크로(손절 있음)는 손대지 않음", b.stops == [(NQC, 1, 19900.0)], b.stops)
    ck("2b 자산 ctx는 마이크로 그대로", st._open_ctx["NQ"].get("sym") == MNQC and st._open_ctx["NQ"].get("aid") == 101)

# 2c: 같은 자산 다계좌 - A 확정, B 미확인 → A의 ctx를 B가 덮지 않음
_reset_disk()
with fake_clock():
    st = Stub()
    bA, bB = PXE(accounts=(("ACC1", 101),)), PXE(raise_cons={MNQC}, accounts=(("ACC2", 202),))
    enter(st, bA, [("MNQ", 2)], sc="ACC1")
    enter(st, bB, [("MNQ", 2)], sc="ACC2")
    ck("2c 다계좌: 확정 계좌 A의 자산 ctx 유지(종전: 미확인 B가 덮어 A 손절 청산 보고가 빠짐)",
       st._open_ctx.get("NQ", {}).get("aid") == 101 and list(st._unconf_watch) == [f"NQ|202|{MNQC}"],
       (st._open_ctx.get("NQ"), list(st._unconf_watch)))

# 2d: 주문 목록을 못 보는 브로커 → 감시에 넣지 않고 '계속 확인' 문장도 없음(사실)
_reset_disk()
with fake_clock():
    st = Stub()
    enter(st, IBLike(raise_cons={MNQC}), [("MNQ", 2)])
    _ua = [a for a in st.alerts if a[0] == "entry_unconfirmed"]
    ck("2d 재거치 못 하는 브로커: 감시 목록 없음 + 알림에 '계속 확인' 없음",
       not st._unconf_watch and len(_ua) == 1 and "계속 확인" not in _ua[0][1] and "keeps checking" not in _ua[0][2],
       (st._unconf_watch, _ua))

# 2e: 재시작 - 디스크 기록으로 브로커를 다시 지어 감시를 잇는다
_reset_disk()
with fake_clock() as clk:
    st, b = Stub(), PXE(raise_cons={NQC})
    enter(st, b, [("NQ", 1)])
    st2 = Stub()
    st2._unconf_watch = eqgui._load_unconf_watch()
    st2._acfg = {"NQ": {"broker": "projectx", "creds": {"projectx": {"f1": "u", "f3": ""}}}}
    b2 = PXE()
    b2.visible[NQC] = 1
    _built = []
    _orig = (eqgui._build_broker, eqgui._f2_load)
    eqgui._build_broker = lambda bk, f1, f2, f3, acc: (_built.append((bk, f1, acc)), b2)[1]
    eqgui._f2_load = lambda f1: "S"
    try:
        st2._check_unconf_watch()
        clk["t"] += eqgui.App.STOP_ORDER_CHECK_SEC + 1
        st2._check_unconf_watch()
    finally:
        eqgui._build_broker, eqgui._f2_load = _orig
    ck("2e 재시작 뒤 디스크 기록으로 복구(설정+키로 브로커 재구성) → 손절 재거치까지 이어짐",
       _built == [("projectx", "u", ["101"])] and b2.stops == [(NQC, 1, 19900.0)]
       and any("미확인 진입 감시 복구" in m for m in st2.logs), (_built, b2.stops, st2.logs[-4:]))

# 2f: 같은 (자산, 계좌, 계약)에 새 진입이 확정되면 남은 미확인 감시를 놓는다(옛 손절 자리로 새 포지션을 판단하지 않게)
_reset_disk()
with fake_clock():
    st = Stub()
    enter(st, PXE(raise_cons={MNQC}), [("MNQ", 2)])
    _had = list(st._unconf_watch)
    enter(st, PXE(), [("MNQ", 2)])
    ck("2f 같은 자리 새 진입 확정 → 미확인 감시 해제, 자산 ctx가 이어받음",
       _had == [f"NQ|101|{MNQC}"] and not st._unconf_watch and st._open_ctx.get("NQ", {}).get("sym") == MNQC,
       (_had, st._unconf_watch))

# 2g: 감시 leg의 손절 학습은 감시 기록에만 - 자산 ctx 파일(.open_ctx.json)을 만들거나 바꾸지 않는다
_reset_disk()
with fake_clock():
    st, b = Stub(), PXE(raise_cons={MNQC})
    enter(st, b, [("MNQ", 2)])
    b.visible[MNQC] = 2
    b.orders.append({"id": 55, "type": 4, "contractId": MNQC, "side": 1, "size": 2, "stopPrice": 19950.0})
    st._check_unconf_watch()
    ck("2g 감시 leg의 손절 학습 → 감시 기록에 저장, .open_ctx.json에는 NQ 기록 없음",
       disk_watch().get(f"NQ|101|{MNQC}", {}).get("stop") == 19950.0 and "NQ" not in disk_ctx() and not b.stops,
       (disk_watch(), disk_ctx(), b.stops))

# 2h: 조회 실패는 판단하지 않는다(놓지도, 걸지도 않음)
_reset_disk()
with fake_clock():
    st, b = Stub(), PXE(raise_cons={MNQC})
    enter(st, b, [("MNQ", 2)])
    b.qty_none = True
    for _ in range(3):
        st._check_unconf_watch()
    ck("2h 포지션 조회 실패가 이어져도 감시 유지·주문 없음", len(st._unconf_watch) == 1 and not b.stops)

# 2i: 같은 계좌·계약에 반대 방향 포지션(회원이 따로 연 것 등) → 이 주문의 포지션이 아님: 손절 안 걸고 감시를 놓는다
_reset_disk()
with fake_clock() as clk:
    st, b = Stub(), PXE(raise_cons={MNQC})
    enter(st, b, [("MNQ", 2)])                    # LONG 진입 미확인
    b.visible[MNQC], b.sign = 2, -1                # 숏 2계약이 보인다
    for _ in range(3):
        st._check_unconf_watch()
        clk["t"] += eqgui.App.STOP_ORDER_CHECK_SEC + 1
    ck("2i 방향이 반대인 포지션에는 손절을 걸지 않고(노출을 키우는 반대 손절 방지) 감시를 놓음 + 사실 한 줄",
       not b.stops and not st._unconf_watch and any("반대입니다" in m for m in st.logs), (b.stops, st.logs[-2:]))

# 2j: 수명(12시간)을 넘긴 감시 기록은 놓는다 - 그 주문의 포지션은 그날 세션 마감에 닫혔다
_reset_disk()
with fake_clock() as clk:
    st, b = Stub(), PXE(raise_cons={MNQC})
    enter(st, b, [("MNQ", 2)])
    clk["t"] += eqgui._UNCONF_MAX_AGE + 1
    b.visible[MNQC] = 2                            # 그 뒤 생긴 다른 포지션
    st._check_unconf_watch()
    ck("2j 12시간 넘은 감시는 손절 없이 놓음(나중 포지션을 이 주문 것으로 잡지 않게)",
       not st._unconf_watch and not b.stops and any("시간을 넘어" in m for m in st.logs), st.logs[-2:])
    _old = {"NQ|101|X": {"asset": "NQ", "broker": "projectx", "aid": 101, "con": "X", "ts": clk["t"] - 13 * 3600}}
    json.dump(_old, open(eqgui._UNCONF_PATH, "w", encoding="utf-8"))
    ck("2j 디스크 복구도 수명 넘은 기록은 버림", eqgui._load_unconf_watch() == {}, eqgui._load_unconf_watch())


# ═════ 3: 같은 신호 재처리 - 처리 기록을 새 세대가 이어받음 ═════════════════════
me = app()
me._sig_accts = {("NQ", 0): snap()}
s1 = sig_nq("s1")
e1 = run_sig(me, 1, s1)
me._sig_on, me._sig_gen = True, 2                # 정지→시작: 새 세대 루프
e2 = run_sig(me, 2, s1)
ck("3 새 세대 루프는 발행 60초 안의 같은 신호를 다시 처리하지 않음(종전: 재진입)", len(e1) == 1 and not e2, (e1, e2))
ck("3 처리 기록·재진입 금지 표시가 앱에 남음", me._sig_seen == ("s1", 1) and me._sig_entered_day, me._sig_seen)

me._sig_on, me._sig_gen = True, 3
e3 = run_sig(me, 3, sig_nq("s2"))
ck("3 같은 날 같은 계좌의 다른 신호 id도 재진입 금지가 세대를 넘어 유지(설계 '하루 1회')",
   not e3 and "이미 진입" in logs(me), logs(me))

# 발주 직전 확인에서 빠지는 낡은 루프는 기록을 되돌린다 → 새 루프가 처리
me = app()
me._sig_accts = {("NQ", 0): snap()}
s1 = sig_nq("s1")
e1 = run_sig(me, 1, s1, on_cfg=lambda: setattr(me, "_sig_gen", 2))
ck("3 발주 직전에 빠진 낡은 루프: 진입 없음 + 맡은 표시·재진입 금지 표시를 되돌림",
   not e1 and me._sig_seen is None and not me._sig_entered_day, (me._sig_seen, me._sig_entered_day))
me._sig_on = True
e2 = run_sig(me, 2, s1)
ck("3 그 신호는 새 루프가 한 번 처리(놓치지 않음)", len(e2) == 1, e2)

# 계좌 선별 직전에서 빠지는 경우도 같다
me = app()
me._sig_accts = {("NQ", 0): snap()}
me._clock_skew_now = lambda: (setattr(me, "_sig_gen", 2), None)[1]
e1 = run_sig(me, 1, s1)
me._sig_on = True
e2 = run_sig(me, 2, s1)
ck("3 계좌 선별 직전에 빠진 낡은 루프도 되돌림 → 새 루프가 한 번 처리", not e1 and len(e2) == 1, (e1, e2))

# 되돌릴 때 새 루프가 이미 맡은 표시는 건드리지 않는다
me = app()
me._sig_accts = {("NQ", 0): snap()}


def _steal():
    me._sig_gen = 2
    me._sig_seen = ("s1", 2)


e1 = run_sig(me, 1, s1, on_cfg=_steal)
ck("3 되돌림은 자기 표시일 때만(새 루프가 맡은 표시 유지)", not e1 and me._sig_seen == ("s1", 2), me._sig_seen)

# 확인 신호(거래 없음)도 새 세대가 다시 '받았다'를 찍지 않는다
me = app()
me._sig_accts = {("NQ", 0): snap()}
_nt = {"id": "n1", "tradeable": False, "instrument": "NQ", "published_at": time.time()}
run_sig(me, 1, _nt)
me._sig_on, me._sig_gen = True, 2
run_sig(me, 2, _nt)
ck("3 거래 없음 신호 '받았다' 로그는 한 번", logs(me).count("NO-TRADE") == 1, logs(me))

# 앱 재시작(처리 기록 없음): .entered.json의 발행 뒤 진입 기록이면 건너뜀
me = app()
me._sig_accts = {("NQ", 0): snap()}
sg = sig_nq("s7")
me._entered_at = {"NQ": sg["published_at"] + 8}
ent = run_sig(me, 1, sg)
ck("3 재시작 뒤: 이 신호 발행 뒤 진입 기록(.entered.json)이 있으면 다시 진입하지 않음 + 사실 한 줄",
   not ent and "이 신호 발행 뒤에 이미 진입한 기록" in logs(me), logs(me))
ck("3 그 건너뜀은 서버에 '켜진 계좌 없음' 같은 건너뜀 보고를 보내지 않음",
   not any(c[0][1] == "no_account" for c in me._entry_skip_ev.call_args_list), me._entry_skip_ev.call_args_list)
me = app()
me._sig_accts = {("NQ", 0): snap()}
me._entered_at = {"NQ": sg["published_at"] - 4 * 3600}
ck("3 앞선 신호의 진입 기록(발행 전)이면 종전처럼 진입", len(run_sig(me, 1, sg)) == 1)
me = app()
me._sig_accts = {("NQ", 0): snap()}
me._clock_skew, me._clock_skew_ts = -30.0, time.time()          # PC 시계가 30초 느림
sk = sig_nq("s8", pub=time.time() + 25)                         # 발행 = 서버 시각
me._entered_at = {"NQ": sk["published_at"] + 5 - 30}            # 체결 기록 = PC 시각(서버보다 30초 늦게 찍힘)
run_sig(me, 1, sk)
ck("3 발행 뒤 판정은 잰 시계 차이만큼 맞춰 본다(PC 시계 느림)", "이 신호 발행 뒤에 이미 진입한 기록" in logs(me), logs(me))
me = app()
me._watch_only = True
me._sig_accts = {("NQ", 0): snap()}
me._entered_at = {"NQ": sg["published_at"] + 8}
run_sig(me, 1, sg)
ck("3 확인 모드(Operator)는 확인 카드 그대로(진입 기록 판정 안 함)",
   me._watch_ticket.call_count == 1 and "이미 진입한 기록" not in logs(me))

# 진짜 스레드: 진입 도중 정지→곧바로 시작(재검증 A 문제 1) - 같은 신호 진입은 한 번
me = app()
me._sig_accts = {("NQ", 0): snap()}
_ent, _go = [], threading.Event()


def _slow_enter(cfg, *a):
    _go.set()
    _real_sleep(0.5)
    _ent.append(cfg["acct"])


me._enter_account = _slow_enter
_sr = sig_nq("r1")
_orig = (requests.get, autopilot_crypto.decrypt, eqgui.SIG_POLL_SECS)
requests.get = lambda *a, **k: _R()
autopilot_crypto.decrypt = lambda tok, txt: dict(_sr)
eqgui.SIG_POLL_SECS = 0.05
try:
    me._sig_gen = 1
    _t1 = threading.Thread(target=eqgui.App._sig_loop, args=(me, "u", 1), daemon=True)
    _t1.start()
    _go.wait(5)
    me._sig_on = False                           # [전체 정지]
    me._sig_gen, me._sig_on = 2, True            # 곧바로 [라이브 시작] - 새 세대
    _t2 = threading.Thread(target=eqgui.App._sig_loop, args=(me, "u", 2), daemon=True)
    _t2.start()
    _real_sleep(1.5)
    me._sig_on = False
    _t1.join(5)
    _t2.join(5)
finally:
    requests.get, autopilot_crypto.decrypt, eqgui.SIG_POLL_SECS = _orig
ck("3 실제 스레드: 진입 도중 정지→시작해도 같은 신호 진입은 한 번(종전 2번)", _ent == ["111"], _ent)

# 주석·문구의 '이중 진입 없음' 과장 정정
_src = io.open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
_seg = {n.name: ast.get_source_segment(_src, n) for n in ast.walk(ast.parse(_src))
        if isinstance(n, ast.FunctionDef) and n.name in ("_sig_loop", "_start_sig_loop")}
ck("3 신호 루프 주석이 세대 확인의 한계를 사실대로(공유 처리 기록이 재처리를 막음)",
   "과장" in _seg["_sig_loop"] and "루프는 늘 하나다" not in _seg["_start_sig_loop"]
   and "_sig_seen" in _seg["_start_sig_loop"])


# ═════ 4: 불통 중 다른 자산 무장 → 사본 합치기 ═══════════════════════════════════
def gated(gate):
    me = app()
    me._gate = dict(gate)
    me._connected = True
    me._sig_accts = {("NQ", 0): snap()}
    me._auto_accts = {("NQ", 0): [{"asset": "NQ", "live": True, "broker": "projectx", "acct": "111",
                                   "f1": "u", "f3": ""}]}
    me._start_sig_loop = MagicMock(side_effect=lambda: setattr(me, "_sig_on", True))
    me.root.after = lambda ms, fn: fn() if ms < 1000 else None
    return me


me = gated(OFF)
eqgui.App._apply_gating(me)
_p1 = sorted(me._sig_parked)
# 불통 중 회원이 GC를 체크해 무장(_master_arm이 하는 일): 신호 대기 무장 + 마감 청산 잡 + 루프 켜짐
me._acfg["GC"]["include"] = True
me._sig_accts[("GC", 0)] = snap(aid="g1", label="G")
me._auto_accts[("GC", 0)] = [{"asset": "GC", "live": True, "broker": "projectx", "acct": "g1", "f1": "u", "f3": ""}]
me._sig_on = True
eqgui.App._apply_gating(me)                       # 아직 불통 - 다시 끈다
ck("4 불통 중 다른 자산을 무장해도 앞서 남긴 사본을 덮지 않고 합침(종전: GC만 남음)",
   _p1 == [("NQ", 0)] and sorted(me._sig_parked) == [("GC", 0), ("NQ", 0)], sorted(me._sig_parked))
me._gate = dict(NORMAL)
eqgui.App._apply_gating(me)
ck("4 복구: 두 자산 모두 신호 대기를 되살림(루프 하나)",
   sorted(me._sig_accts) == [("GC", 0), ("NQ", 0)] and me._start_sig_loop.call_count == 1
   and not me._sig_parked, (sorted(me._sig_accts), me._start_sig_loop.call_count))
ck("4 복구 안내에 두 자산", "GC, NQ" in logs(me), logs(me))

me = gated(OFF)
eqgui.App._apply_gating(me)
_old = me._sig_parked[("NQ", 0)]
me._sig_accts[("NQ", 0)] = dict(snap(), label="새무장")
me._sig_on = True
eqgui.App._apply_gating(me)
ck("4 같은 키는 지금 무장이 이김", me._sig_parked[("NQ", 0)]["label"] == "새무장" and _old["label"] == "A")


# ═════ 5: 마감 청산 재시도 기록을 새 세대가 이어받음 ════════════════════════════
AP = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True, "fullexit": True}}
ET = _dt.timezone(_dt.timedelta(hours=-4))


def _res(errors=(), planned=(1,), closed=()):
    r = MagicMock()
    r.planned = list(planned); r.closed = list(closed); r.errors = list(errors)
    return r


def _me5():
    me = MagicMock()
    me.lang = "ko"
    me._gate = AP
    me._auto_on = True
    me._exit_override = {}
    me._entered_at, me._entering_at = {}, {}
    me._auto_accts = {("NQ", 0): [{"asset": "NQ", "hour": 14, "tz": "America/New_York", "broker": "projectx",
                                   "acct": "a1", "f1": "k", "f2": "s", "f3": "", "live": True}]}
    me._close_mode = lambda j: (True, False)
    me._auto_gen = 1
    return me


def run_loop(me, gen, times, broker):
    seq = list(times)
    clock = {"now": seq.pop(0)}
    orig = (eqgui._build_broker, eqgui._now_in, time.sleep, time.time)
    eqgui._build_broker = lambda *a, **k: broker
    eqgui._now_in = lambda _tz: clock["now"]
    time.time = lambda: clock["now"].timestamp()

    def _sleep(_s):
        if seq:
            clock["now"] = seq.pop(0)
        else:
            me._auto_on = False
    time.sleep = _sleep
    try:
        eqgui.App._auto_loop(me, gen)
    finally:
        eqgui._build_broker, eqgui._now_in, time.sleep, time.time = orig
    return " ".join(str(c[0][0]) for c in me.log.call_args_list)


def T(h, m, s=0, day=6):
    return _dt.datetime(2026, 10, day, h, m, s, tzinfo=ET)


def _ctxs(mock):
    return [c[0][0] for c in mock.call_args_list]


me = _me5()
b = MagicMock()
b.flatten_all.side_effect = [RuntimeError("HTTP 502"), _res()]
run_loop(me, 1, [T(14, 0, 5)], b)                 # 세대 1: 첫 시도 실패
me._auto_on, me._auto_gen = True, 2               # 정지→다시 무장: 세대 2
run_loop(me, 2, [T(14, 1, 10), T(14, 5)], b)
ck("5 새 세대가 실패 기록을 이어받아 재시도 → 성공", b.flatten_all.call_count == 2, b.flatten_all.call_count)
ck("5 첫 실패 서버 보고·팝업은 한 번(새 세대가 '첫 실패'로 다시 보내지 않음)",
   _ctxs(me._report_error) == ["auto_close"] and me.root.after.call_count == 1,
   (me._report_error.call_args_list, me.root.after.call_count))
ck("5 성공 후속 보고(autoclose_ok '2번째 시도') 유지(종전: 새 세대에선 빠짐)",
   _ctxs(me._member_alert) == ["autoclose_ok"] and "2번째 시도" in me._member_alert.call_args[0][1],
   me._member_alert.call_args_list)

me = _me5()
b = MagicMock()
b.flatten_all.side_effect = [RuntimeError("HTTP 502"), RuntimeError("HTTP 502")]
run_loop(me, 1, [T(14, 0, 5)], b)
me._auto_on, me._auto_gen = True, 2
run_loop(me, 2, [T(14, 1, 10)], b)
ck("5 새 세대에서 또 실패해도 실패 팝업·회원 DM이 반복되지 않음",
   _ctxs(me._report_error) == ["auto_close"] and me.root.after.call_count == 1
   and me._auto_retry[("NQ", 14, "a1")]["n"] == 2, (me._report_error.call_args_list, me._auto_retry))

me = _me5()
b = MagicMock()
b.flatten_all.return_value = _res()
run_loop(me, 1, [T(14, 0, 5)], b)
me._auto_on, me._auto_gen = True, 2
run_loop(me, 2, [T(14, 2)], b)
ck("5 끝난 청산은 새 세대가 같은 창에서 다시 쏘지 않음(하루 1회)", b.flatten_all.call_count == 1,
   b.flatten_all.call_count)

me = _me5()
me._auto_gen = 2
me._auto_retry = {("NQ", 14, "a1"): {"day": "2026-10-05", "n": 3, "next": 0, "end": 14 * 60 + 30,
                                     "lab": "NQ 14:00 ET", "err": "x", "live": True, "gen": 1}}
b = MagicMock()
b.flatten_all.return_value = _res()
lg = run_loop(me, 2, [T(14, 0, 5)], b)
ck("5 정지 전 옛 세대의 다른 날 기록: 늦은 포기 DM 없이 사실 한 줄로 닫고, 오늘 청산은 정상",
   "autoclose_gaveup" not in _ctxs(me._member_alert) and "auto_close_gaveup" not in _ctxs(me._report_error)
   and "자동 청산이 꺼져 있는 사이" in lg and b.flatten_all.call_count == 1 and not me._auto_retry, lg)

me = _me5()
me._auto_gen = 2
me._auto_retry = {("NQ", 14, "a1"): {"day": "2026-10-06", "n": 2, "next": 0, "end": 14 * 60 + 30,
                                     "lab": "NQ 14:00 ET", "err": "x", "live": True, "gen": 1}}
run_loop(me, 2, [T(14, 40)], MagicMock())
ck("5 같은 날 창이 닫힌 기록은 새 세대가 한 번 알림(종전: 기록째 사라져 알림 없음)",
   _ctxs(me._member_alert) == ["autoclose_gaveup"] and not me._auto_retry, me._member_alert.call_args_list)


# ═════ 6: 선물 진입 클레임 mid + 프로세스 난수 ═══════════════════════════════════
_posted = []


class _Resp:
    ok = True
    text = "claim:granted"


_orig_post = requests.post
requests.post = lambda url, timeout=None, json=None: (_posted.append(json), _Resp())[1]
_orig_pid = autopilot_crypto.path_id
autopilot_crypto.path_id = lambda tok: "tid123"
try:
    me = MagicMock()
    me._token = "tok"
    eqgui.App._claim_entry(me, {"id": "s1"}, 101, per_process=True)
    eqgui.App._claim_entry(me, {"id": "s1"}, 101, per_process=True)
    eqgui.App._claim_entry(me, {"id": "s1"}, "BY")
finally:
    requests.post = _orig_post
    autopilot_crypto.path_id = _orig_pid
_m = [p["mid"] for p in _posted]
ck("6 선물 클레임 mid = 기기 id 12자 + 프로세스 난수 4자(16자, 서버 상한 안)",
   _m[0] == "abcdef123456" + eqgui._CLAIM_NONCE and len(_m[0]) == 16 and _m[0].isalnum(), _m)
ck("6 같은 프로세스의 재요청은 같은 mid(멱등 그대로)", _m[0] == _m[1], _m)
ck("6 per_process 없으면 종전 mid(기기 id)", _m[2] == "abcdef123456", _m)


def _server(db, key, mid):                          # web/entry_claims.handle_claim의 판정 규칙
    cur = db.get(key)
    if cur and cur != mid:
        return "taken"
    db[key] = mid
    return "granted"


_db = {}
ck("6 같은 PC 옛 03b 창(기기 id) 뒤 새 빌드(기기 id+난수)는 taken - 늦은 쪽이 양보",
   _server(_db, "k", "abcdef123456") == "granted" and _server(_db, "k", _m[0]) == "taken")

with fake_clock():
    st = Stub()
    enter(st, PXE(), [("MNQ", 2)])
    ck("6 선물 진입 경로는 per_process=True로 클레임", st.claims == [(101, {"per_process": True})], st.claims)
    st = Stub()
    st.claim_ok = False
    b = PXE()
    enter(st, b, [("MNQ", 2)])
    ck("6 taken이면 포지션을 건드리지 않고 양보 + 사실 문장(같은 PC 다른 실행 포함)",
       not b.entries and any("이 PC의 다른 EQ Autopilot 실행" in m for m in st.logs), st.logs[-2:])
    st = Stub()
    st.lang = "en"
    st.claim_ok = False
    enter(st, PXE(), [("MNQ", 2)])
    ck("6 양보 문장 EN 한 쌍", any("another EQ Autopilot run on this PC" in m for m in st.logs), st.logs[-2:])

_ea = next(n for n in ast.walk(ast.parse(_src)) if isinstance(n, ast.FunctionDef) and n.name == "_enter_account")
_calls = [c for c in ast.walk(_ea) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "_claim_entry"]
ck("6 크립토 경로 클레임은 종전 mid(잔여 청산 뒤 클레임이라 난수를 붙이면 늦은 쪽이 포지션을 닫고 양보)",
   len(_calls) == 1 and not any(k.arg == "per_process" for k in _calls[0].keywords), [ast.dump(c) for c in _calls])

print()
print("실패", len(FAIL), FAIL if FAIL else "")
sys.exit(1 if FAIL else 0)
