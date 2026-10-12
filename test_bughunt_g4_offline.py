#!/usr/bin/env python3
# EQ Autopilot - 오픈 준비 버그 조사(2026-10-06) 1단계 g4(입력·설정·환경) 오프라인 시험. 0=통과
# D 크립토 행 = 등록, 같은 크립토 브로커 행 둘이면 무장 거절, [계좌 추가]는 안 쓰는 거래소로/둘 다 쓰면 거절
# Q 새 계좌 prop '미설정'(set=False), 창에서 저장한 값(set=True)은 기동 이행 안 함, 켜진 계좌 이행은 로그
# R 금액 칸 엄격 파서('600,00'·'62,5'·'1.000' 거절), 1R 칸 못 읽으면 저장 안 하고 칸을 저장값으로 되돌림
# V 설정 저장 원자화(임시 파일 + os.replace, 실패해도 원본 유지, 동시 저장에도 YAML 온전)
# O 설정 폴더별 단일 실행 잠금(같은 폴더 두 번째는 거절, 다른 폴더는 허용)
# Y BTC 사전점검 시각 = 일요일 22:00 UTC만
# W 신호 나이 = 서버 시각(응답 Date) 기준, 시계 30초 넘게 어긋나면 자동 진입 보류, 미래 발행 거부
# size_mult0 명시적 0은 진입 안 함, 필드 없을 때만 1.0
import datetime as _dt
import os
import subprocess
import sys
import tempfile
import threading
import time
from email.utils import formatdate
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


def bind(me, *names):
    for nm in names:
        setattr(me, nm, (lambda f: (lambda *a, **k: f(me, *a, **k)))(getattr(eqgui.App, nm)))


_orig_mb = eqgui.messagebox
eqgui.messagebox = MagicMock()

# ───────────────────────── D ─────────────────────────
def mk_btc(rows, broker="bybit", lang="ko"):
    me = MagicMock()
    me.lang = lang
    me._asset = "BTC"
    me._broker_name = broker
    me._acfg = {"BTC": {"broker": broker, "accounts": rows,
                        "creds": {"bybit": {"f1": "k1", "f3": ""}, "bitget": {"f1": "k2", "f3": "p"}}},
                "NQ": {"broker": "projectx", "accounts": [eqgui._new_acct(600, "", True, "")],
                       "creds": {"projectx": {"f1": "me@x", "f3": ""}}}}
    bind(me, "_accts_of", "_active_accts", "_acct_broker", "_broker_of", "_creds_of",
         "_dup_crypto_rows", "_dup_crypto_msg", "_pin_crypto_rows", "_crypto_unique_ok", "_crypto_keymiss_hint")
    me._enc_locked, me._kc_failed = False, []
    me._golive_refuse = lambda *a, **k: False            # 연결 테스트 중 편집 거절은 test_global_creds P0에서
    me._gate = {}                                         # 서버 잠금 아님(가동 중 체크 잠금 거절은 test_global_creds P2-4에서)
    me._bk_configured = lambda a, b: bool(str(((me._acfg.get(a) or {}).get("creds") or {}).get(b, {}).get("f1") or "").strip())
    return me


# D-1 안내 줄: 크립토 행(id 없음)은 등록으로 센다 → '[계좌 추가]'에서 멈추지 않음
me = mk_btc([eqgui._new_acct(600, "", True, "Bybit (USDT perp)")])
me._token = "tok"
me._sig_accts, me._auto_accts = {}, {}
me._test_ok = set()
me._gate = {"ok": True, "enabled": True, "force_dry_run": False, "caps": {"use": True, "autoentry": True}}
bind(me, "_next_not_ready", "_asset_not_ready", "_gate_line", "_creds_peek")
me._broker_allowed = lambda b: True
eqgui.App._refresh_next_action(me)
_txt = str(me._next_lbl.config.call_args)
ck("D-1 BTC 키만 있으면 안내가 '[계좌 추가]'로 가지 않음(다음 단계)", "계좌 추가" not in _txt and "연결 확인 전" in _txt, _txt)
me._asset = "NQ"
eqgui.App._refresh_next_action(me)
_txt = str(me._next_lbl.config.call_args)
ck("D-1 NQ(계좌 ID 필요)는 id 없으면 그대로 '[계좌 추가]'", "계좌 추가" in _txt, _txt)

# D-2 같은 크립토 브로커 행 중복 판정
me = mk_btc([eqgui._new_acct(600, "", True, "a", broker="bybit"), eqgui._new_acct(600, "", True, "b", broker="bybit")])
ck("D-2 bybit 행 둘 켜짐 → 중복", me._dup_crypto_rows("BTC") == [("bybit", 2)], me._dup_crypto_rows("BTC"))
me = mk_btc([eqgui._new_acct(600, "", True, "a", broker="bybit"), eqgui._new_acct(600, "", True, "b", broker="bitget")])
ck("D-2 bybit + bitget → 중복 아님(의도된 두 거래소)", me._dup_crypto_rows("BTC") == [])
me = mk_btc([eqgui._new_acct(600, "", True, "a", broker="bybit"), eqgui._new_acct(600, "", False, "b", broker="bybit")])
ck("D-2 같은 브로커라도 하나가 꺼져 있으면 중복 아님", me._dup_crypto_rows("BTC") == [])
me = mk_btc([eqgui._new_acct(600, "", True, "a"), eqgui._new_acct(600, "", True, "b", broker="bybit")])
ck("D-2 빈 브로커 칸(자산 기본=bybit)도 해석해서 셈", me._dup_crypto_rows("BTC") == [("bybit", 2)])

# D-3 가동 중 자산 체크(_live_arm_assets) 프리플라이트가 중복이면 무장하지 않음
eqgui.messagebox.reset_mock()
me = mk_btc([eqgui._new_acct(600, "", True, "a", broker="bybit"), eqgui._new_acct(600, "", True, "b", broker="bybit")])
me._live_ctx = {"live": True, "perm_auto": True, "perm_use": True}
me._broker_allowed = lambda b: True
me._live_include = {"BTC": MagicMock()}
me._acfg["BTC"]["include"] = True
eqgui.App._live_arm_assets(me, ["BTC"])
_w = str(eqgui.messagebox.showwarning.call_args)
ck("D-3 중복 행이면 연결 테스트·무장 전에 멈춤(이유 표시)", me._run.call_count == 0 and "같은 거래소 행 2개" in _w
   and me._acfg["BTC"]["include"] is False, (me._run.call_count, _w))
me = mk_btc([eqgui._new_acct(600, "", True, "a", broker="bybit"), eqgui._new_acct(600, "", True, "b", broker="bitget")])
me._live_ctx = {"live": True, "perm_auto": True, "perm_use": True}
me._broker_allowed = lambda b: True
me._acfg["BTC"]["include"] = True
eqgui.App._live_arm_assets(me, ["BTC"])
ck("D-3 bybit + bitget은 그대로 연결 테스트로 진행", me._run.call_count == 1)

# D-3b [라이브 시작](_master_start) 프리플라이트도 같은 이유로 멈춤
eqgui.messagebox.reset_mock()
me = mk_btc([eqgui._new_acct(600, "", True, "a", broker="bybit"), eqgui._new_acct(600, "", True, "b", broker="bybit")])
me._gate = {}
me._sig_accts, me._auto_accts = {}, {}
me._token = "tok"
me._broker_allowed = lambda b: True
me._live_include = {"NQ": SimpleNamespace(get=lambda: 0), "GC": SimpleNamespace(get=lambda: 0),
                    "BTC": SimpleNamespace(get=lambda: 1)}
me._live_session = False
eqgui.App._master_start(me)
_lg = " ".join(str(c[0][0]) for c in me.log.call_args_list)
ck("D-3b [라이브 시작]도 중복 크립토 행이면 사전점검에서 멈춤(로그·창)", "같은 거래소 행 2개" in _lg
   and "같은 거래소 행 2개" in str(eqgui.messagebox.showwarning.call_args) and me._live_session is False
   and me._run.call_count == 0, (_lg, me._run.call_count))

# D-4 [계좌 추가]: 안 쓰는 다른 거래소로 행 생성, 둘 다 쓰면 거절
eqgui.messagebox.reset_mock()
rows = [eqgui._new_acct(600, "", True, "Bybit (USDT perp)")]          # 브로커 칸 빈 행(자산 기본 bybit)
me = mk_btc(rows)
eqgui.App._add_acct(me)
ck("D-4 BTC [계좌 추가] → Bitget 행(같은 Bybit 행 아님)", len(rows) == 2 and rows[1]["broker"] == "bitget"
   and rows[0]["broker"] == "bybit", [(r["broker"], r["label"]) for r in rows])
eqgui.App._add_acct(me)
ck("D-4 두 거래소 다 있으면 추가 거절(행 수 그대로, 이유 표시)", len(rows) == 2
   and "거래소마다 하나" in str(eqgui.messagebox.showinfo.call_args), (len(rows), eqgui.messagebox.showinfo.call_args))
rows = [eqgui._new_acct(600, "", True, "b", broker="bitget")]
me = mk_btc(rows, broker="bitget")
eqgui.App._add_acct(me)
ck("D-4 Bitget만 있으면 Bybit 행 추가", [r["broker"] for r in rows] == ["bitget", "bybit"], [r["broker"] for r in rows])
nq_rows = [eqgui._new_acct(600, "ACC-1111", True, "", broker="projectx")]
me = mk_btc([])
me._asset, me._broker_name = "NQ", "projectx"
me._acfg["NQ"]["accounts"] = nq_rows
eqgui.App._add_acct(me)
ck("D-4 선물(NQ) [계좌 추가]는 종전대로 빈 행 추가", len(nq_rows) == 2 and nq_rows[1]["broker"] == "projectx")

# ───────────────────────── Q ─────────────────────────
eqgui._PROP_MIGRATED.clear()
a = eqgui._new_acct(600, "", True, "")
ck("Q-1 새 계좌 prop = 미설정(set=False), 숫자 기본값은 그대로(동결)", a["prop"].get("set") is False
   and a["prop"]["r_test"] == 1200.0 and a["prop"]["r_steady"] == 300.0, a["prop"])
ck("Q-1 새 계좌는 프롭 창이 1R 칸을 0으로 엶", eqgui._prop_is_unset(a["prop"]) is True)
old = {"on": True, "type": "test", "r_test": 1200.0, "r_buffer": 300.0, "r_steady": 300.0, "buffer": 6000.0}
a = eqgui._new_acct(600, "TS-12345678", True, "", dict(old, set=True), None, False, "projectx", topstep=True)
ck("Q-2 창에서 저장한 1200/300(set=True)은 재시작에도 그대로", (a["prop"]["r_test"], a["prop"]["r_steady"]) == (1200.0, 300.0)
   and a["prop"]["set"] is True, a["prop"])
ck("Q-2 set=True면 이행 기록 없음", not eqgui._PROP_MIGRATED)
a = eqgui._new_acct(600, "TS-12345678", True, "", dict(old), None, False, "projectx", topstep=True)
ck("Q-3 표식 전 옛 기본값(set 없음)은 종전대로 2200/450 이행", (a["prop"]["r_test"], a["prop"]["r_steady"]) == (2200.0, 450.0)
   and "set" not in a["prop"], a["prop"])
ck("Q-3 켜진 계좌 이행은 기록됨", len(eqgui._PROP_MIGRATED) == 1, eqgui._PROP_MIGRATED)
eqgui._new_acct(600, "TS-12345678", True, "", dict(old), None, False, "projectx", topstep=True)   # _build가 또 로드
lg = MagicMock(); lg.lang = "ko"
eqgui.App._log_prop_migrated(lg)
eqgui.App._log_prop_migrated(lg)
_l = " ".join(str(c[0][0]) for c in lg.log.call_args_list)
ck("Q-3 이행 로그 한 줄(같은 건 한 번만), 끝4자리·전후 값", lg.log.call_count == 1 and "…5678" in _l and "$1200 → $2200" in _l
   and "$300 → $450" in _l, _l)
eqgui._PROP_MIGRATED.clear()
eqgui._new_acct(600, "X", True, "", dict(old, on=False), None, False, "projectx", topstep=True)
ck("Q-3 꺼진 계좌 이행은 로그 대상 아님", not eqgui._PROP_MIGRATED)
ck("Q-4 옛 값(set 없음)·꺼짐·배포 기본값 묶음 → 미설정으로 봄", eqgui._prop_is_unset(dict(old, on=False))
   and eqgui._prop_is_unset({"on": False, "r_test": 2200.0, "r_buffer": 450.0, "r_steady": 450.0, "buffer": 6000.0}))
ck("Q-4 켜진 옛 값·손으로 고친 옛 값·set=True는 미설정 아님", not eqgui._prop_is_unset(old)
   and not eqgui._prop_is_unset(dict(old, on=False, r_test=1500.0)) and not eqgui._prop_is_unset(dict(old, on=False, set=True)))
# 저장 → 로드 왕복에 표식이 남는다
tmpd = tempfile.mkdtemp()
_orig_cfg = eqgui.CFG_PATH
eqgui.CFG_PATH = os.path.join(tmpd, "config.yaml")
import yaml  # noqa: E402
acfg = {"NQ": {"broker": "projectx", "creds": {}, "include": True,
               "accounts": [eqgui._new_acct(600, "", True, ""), eqgui._new_acct(600, "B", True, "", dict(old, set=True))]}}
eqgui._save_full("ko", "tok", acfg)
_d = yaml.safe_load(open(eqgui.CFG_PATH, encoding="utf-8").read())
_rows = _d["assets"]["NQ"]["accounts"]
_re = [eqgui._new_acct(x.get("one_r"), x.get("id"), x.get("on"), x.get("label"), x.get("prop"), x.get("pct"), False,
                       "projectx", topstep=True) for x in _rows]
ck("Q-5 저장·로드 왕복: 새 계좌 set=False 유지(창은 0), 저장값 set=True 유지(이행 없음)",
   _re[0]["prop"].get("set") is False and eqgui._prop_is_unset(_re[0]["prop"])
   and _re[1]["prop"].get("set") is True and _re[1]["prop"]["r_test"] == 1200.0, [r["prop"] for r in _re])

# ───────────────────────── R ─────────────────────────
P = eqgui._parse_amount
cases = {"600": 600.0, "62.5": 62.5, "1,800": 1800.0, "$600": 600.0, "1,800.5": 1800.5, "1,000,000": 1e6, "0": 0.0,
         "0.4": 0.4, " 450 ": 450.0,
         "600,00": None, "62,5": None, "450,00": None, "0,4": None, "1.000": None, "2.200": None, "2.250": None,
         "153.000": None, "1800,5": None, "-5": None, "abc": None, "": None, "1e3": None, "01,000": None}
_bad = {k: (P(k), v) for k, v in cases.items() if P(k) != v}
ck("R-1 엄격 파서: 숫자·점 소수·세 자리 쉼표만, 유럽식 소수 쉼표·점 천 단위·음수·글자 거절", not _bad, _bad)
ck("R-1 이유 문구(KO/EN)", "쉼표" in eqgui._amount_why("600,00", True) and "comma" in eqgui._amount_why("600,00", False)
   and "천 단위" in eqgui._amount_why("1.000", True) and "0보다" in eqgui._amount_why("-5", True)
   and "0보다" in eqgui._amount_why("0", True) and "빈 칸" in eqgui._amount_why("", True))
vals, bad = eqgui._prop_parse_fields({"r_test": "2.200", "r_steady": "450", "r_live": "", "payouts": "1", "pass_tp": "153,000"},
                                     {"r_test": "챌린지 1R $"}, True)
ck("R-2 프롭 창: '2.200'은 저장 안 함(칸 이름+이유), 빈 칸=0(기본값 1200 몰래 안 앉힘), '153,000'=153000",
   len(bad) == 1 and bad[0].startswith("챌린지 1R:") and vals.get("r_live") == 0.0 and vals.get("pass_tp") == 153000.0
   and "r_test" not in vals, (vals, bad))


class FakeEntry:
    def __init__(self, text, state="normal"):
        self.text, self.state = text, state

    def get(self):
        return self.text

    def cget(self, k):
        return self.state

    def config(self, **kw):
        self.state = kw.get("state", self.state)

    def delete(self, a, b):
        if self.state == "normal":          # Tk readonly는 조용히 무시한다
            self.text = ""

    def insert(self, i, t):
        if self.state == "normal":
            self.text = t + self.text


def collect(raw, prev=600.0, state="normal"):
    eqgui.messagebox.reset_mock()
    accts = [eqgui._new_acct(prev, "", True, "A")]
    me = MagicMock()
    me.lang = "ko"
    me._asset = "NQ"
    me._bk_switch_warn = False
    me._accts_of = lambda a: accts
    e = FakeEntry(raw, state)
    me._acct_widgets = {0: {"on": SimpleNamespace(get=lambda: 1), "label": FakeEntry("A"), "one_r": e, "broker": None}}
    eqgui.App._collect_acct_widgets(me)
    return accts[0]["one_r"], e, eqgui.messagebox.showwarning.call_args


v, e, w = collect("600,00")
ck("R-3 1R '600,00' → 60000 아님: 저장 안 함, 칸은 저장값 600으로 되돌림, 사실대로 알림",
   v == 600.0 and e.text == "600" and w is not None and "저장하지 않았습니다" in str(w) and "쉼표" in str(w), (v, e.text, w))
v, e, w = collect("62,5", prev=750.0, state="readonly")
ck("R-3 잠긴(readonly) 칸도 되돌림", v == 750.0 and e.text == "750" and e.state == "readonly", (v, e.text, e.state))
v, e, w = collect("0")
ck("R-3 0은 저장 안 함 + 칸 되돌림(종전엔 칸에 0이 남아 저장된 것처럼 보임)", v == 600.0 and e.text == "600" and w is not None)
v, e, w = collect("1,800")
ck("R-3 '1,800' = 1800 저장, 경고 없음", v == 1800.0 and w is None, (v, w))
v, e, w = collect("62.5")
ck("R-3 '62.5' = 62.5 저장", v == 62.5 and w is None, (v, w))
v, e, w = collect("600")
ck("R-3 그대로인 칸은 아무것도 안 함", v == 600.0 and w is None)

# ───────────────────────── V ─────────────────────────
eqgui._write_cfg_atomic("a: 1\n")
ck("V-1 원자 저장 기본", open(eqgui.CFG_PATH).read() == "a: 1\n" and not os.path.exists(eqgui.CFG_PATH + ".tmp"))
_orig_replace = eqgui.os.replace
_orig_sleep = time.sleep
_real_open = open


def _full_open(path, *a, **k):              # 디스크 풀 흉내: 임시 파일 쓰기에서 실패
    if str(path).endswith(".tmp"):
        raise OSError(28, "No space left on device")
    return _real_open(path, *a, **k)


eqgui.open = _full_open
try:
    try:
        eqgui._write_cfg_atomic("b: 2\n")
        _raised = False
    except OSError:
        _raised = True
finally:
    del eqgui.open
ck("V-2 임시 파일 쓰기 실패(디스크 풀) → 원본 그대로(종전엔 파일이 먼저 비워짐)", _raised
   and open(eqgui.CFG_PATH).read() == "a: 1\n" and not os.path.exists(eqgui.CFG_PATH + ".tmp"))


def _boom(*a, **k):
    raise OSError(13, "locked")


eqgui.os.replace = _boom
time.sleep = lambda s: None
try:
    eqgui._write_cfg_atomic("c: 3\n")
finally:
    eqgui.os.replace = _orig_replace
    time.sleep = _orig_sleep
ck("V-2 바꿔 끼우기가 끝내 실패(윈도 파일 잡힘) → 종전 방식으로라도 저장, 임시 파일 정리",
   open(eqgui.CFG_PATH).read() == "c: 3\n" and not os.path.exists(eqgui.CFG_PATH + ".tmp"))


def _saver(n):
    for i in range(40):
        acfg2 = {"NQ": {"broker": "projectx", "creds": {}, "include": True,
                        "accounts": [dict(eqgui._new_acct(600, "", True, ""), one_r=float(10 ** (i % 7 + n)))]}}
        eqgui._save_full("ko", "tok-keep", acfg2)


_ths = [threading.Thread(target=_saver, args=(k,)) for k in range(4)]
[t.start() for t in _ths]
[t.join() for t in _ths]
try:
    _d = yaml.safe_load(open(eqgui.CFG_PATH, encoding="utf-8").read()) or {}
    _okv = _d.get("token") == "tok-keep"
except Exception as _e:
    _okv = False
ck("V-3 동시 저장 4스레드×40회 뒤에도 YAML 온전·토큰 유지", _okv)
eqgui.CFG_PATH = _orig_cfg

# ───────────────────────── O ─────────────────────────
_orig_dir = eqgui.APP_DIR
lk1 = tempfile.mkdtemp()
eqgui.APP_DIR = lk1
ck("O-1 첫 실행은 잠금 잡음", eqgui._acquire_instance_lock() is True)
_held = eqgui._INSTANCE_LOCK_FH
ck("O-1 같은 폴더 두 번째(같은 프로세스 다른 핸들)는 거절", eqgui._acquire_instance_lock() is False)
_code = ("import sys; sys.path.insert(0, %r); import eqgui; eqgui.APP_DIR = %r; "
         "print('LOCK', eqgui._acquire_instance_lock())") % (os.path.dirname(os.path.abspath(__file__)), lk1)
_out = subprocess.run([sys.executable, "-c", _code], capture_output=True, text=True, timeout=120,
                      env=dict(os.environ, EQ_NO_ALERT="1"))
ck("O-2 같은 폴더를 쓰는 다른 프로세스는 거절", "LOCK False" in _out.stdout, (_out.stdout[-300:], _out.stderr[-300:]))
lk2 = tempfile.mkdtemp()
_code2 = _code.replace(repr(lk1), repr(lk2))
_out2 = subprocess.run([sys.executable, "-c", _code2], capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, EQ_NO_ALERT="1"))
ck("O-3 다른 폴더(--profile)는 허용", "LOCK True" in _out2.stdout, (_out2.stdout[-300:], _out2.stderr[-300:]))
_held.close()
eqgui._INSTANCE_LOCK_FH = None
_out3 = subprocess.run([sys.executable, "-c", _code], capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, EQ_NO_ALERT="1"))
ck("O-4 먼저 잡은 쪽이 끝나면(핸들 닫힘) 다시 잡힘 - 남은 잠금 없음", "LOCK True" in _out3.stdout, _out3.stdout[-300:])
# O-5 이미 실행 중 안내: Tk 창 하나로 사실만 알리고 끝(설정 언어 따름)
_orig_tk = eqgui.tk.Tk
eqgui.tk.Tk = MagicMock()
eqgui.messagebox.reset_mock()
_oc = eqgui.CFG_PATH
eqgui.CFG_PATH = os.path.join(lk1, "none.yaml")
try:
    eqgui._already_running_notice()
finally:
    eqgui.tk.Tk = _orig_tk
    eqgui.CFG_PATH = _oc
ck("O-5 이미 실행 중 안내(KO 기본, 두 번 주문 이유)", "이미 실행 중" in str(eqgui.messagebox.showwarning.call_args)
   and "두 번" in str(eqgui.messagebox.showwarning.call_args))
eqgui.APP_DIR = _orig_dir

# ───────────────────────── Y ─────────────────────────
_orig_now = eqgui._now_in


def at(y, m, d, h, mi):
    eqgui._now_in = lambda tz: _dt.datetime(y, m, d, h, mi, tzinfo=_dt.timezone.utc)


try:
    at(2026, 10, 6, 21, 45)                     # 화요일
    ck("Y-1 화 21:45 UTC → 다음 BTC 진입은 일 10/11 22:00(평일 사전점검 없음)",
       eqgui._next_entry_dt("BTC") == _dt.datetime(2026, 10, 11, 22, 0, tzinfo=_dt.timezone.utc), eqgui._next_entry_dt("BTC"))
    at(2026, 10, 10, 21, 50)                    # 토요일
    ck("Y-1 토 21:50 UTC → 일 10/11 22:00", eqgui._next_entry_dt("BTC").date() == _dt.date(2026, 10, 11))
    at(2026, 10, 11, 21, 50)                    # 일요일
    ck("Y-2 일 21:50 UTC → 그날 22:00(T-10 점검 그대로)",
       eqgui._next_entry_dt("BTC") == _dt.datetime(2026, 10, 11, 22, 0, tzinfo=_dt.timezone.utc))
    at(2026, 10, 11, 22, 30)
    ck("Y-2 일 22:30 UTC → 다음 일 10/18", eqgui._next_entry_dt("BTC").date() == _dt.date(2026, 10, 18))
    at(2026, 10, 9, 15, 0)                      # 금요일 NQ 마감 뒤
    ck("Y-3 NQ 주말 건너뜀은 그대로(금 → 월)", eqgui._next_entry_dt("NQ").date() == _dt.date(2026, 10, 12))
finally:
    eqgui._now_in = _orig_now

# ───────────────────────── W + size_mult0 ─────────────────────────
ck("W-1 Date 헤더 → 서버 시각", abs(eqgui._server_epoch_from_headers({"Date": formatdate(1_800_000_000, usegmt=True)})
                                   - 1_800_000_000.5) < 1e-6)
ck("W-1 헤더 없음·깨짐·캐시 응답(Age>0, HIT) → None",
   eqgui._server_epoch_from_headers({}) is None and eqgui._server_epoch_from_headers({"Date": "x"}) is None
   and eqgui._server_epoch_from_headers({"Date": formatdate(1_800_000_000, usegmt=True), "Age": "40"}) is None
   and eqgui._server_epoch_from_headers({"Date": formatdate(1_800_000_000, usegmt=True), "cf-cache-status": "HIT"}) is None)

import requests  # noqa: E402
import autopilot_crypto  # noqa: E402


def loop_once(pc_fast=0.0, pub_ago=5.0, with_date=True, size_mult="absent", pub_abs=None, age_hdr=None):
    """_sig_loop 한 바퀴. pc_fast = PC 시계가 서버보다 빠른 초. 반환 (진입 호출들, 스킵 코드들, 로그)."""
    me = MagicMock()
    me.lang = "ko"
    me._sig_on = True
    me._token = "tok"
    me._watch_only = False
    me._feed_errs = 0
    me._clock_skew, me._clock_skew_ts = None, 0.0
    me._clock_bad, me._clock_popped, me._clock_warn_at = False, False, 0.0
    me._sig_accts = {("NQ", 0): {"label": "A", "manual": False}}
    bind(me, "_note_clock", "_clock_skew_now", "_clock_skew_text", "_clock_warn_check")
    srv_now = time.time() - pc_fast
    sig = {"id": f"s{time.time()}", "instrument": "NQ", "tradeable": True, "direction": "LONG", "stop_price": 100.0,
           "published_at": (pub_abs if pub_abs is not None else srv_now - pub_ago)}
    if size_mult != "absent":
        sig["size_mult"] = size_mult
    hdr = {"Date": formatdate(srv_now, usegmt=True)} if with_date else {}
    if age_hdr is not None:
        hdr["Age"] = str(age_hdr)
    o_get, o_dec, o_sleep = requests.get, autopilot_crypto.decrypt, time.sleep
    requests.get = lambda *a, **k: SimpleNamespace(ok=True, text="x", headers=hdr)
    autopilot_crypto.decrypt = lambda tok, txt: dict(sig)

    def _stop(_s):
        me._sig_on = False
    time.sleep = _stop
    try:
        eqgui.App._sig_loop(me, "u")
    finally:
        requests.get, autopilot_crypto.decrypt, time.sleep = o_get, o_dec, o_sleep
    lg = " ".join(str(c[0][0]) for c in me.log.call_args_list)
    return me._enter_account.call_args_list, [c[0][1] for c in me._entry_skip_ev.call_args_list], lg, me


ent, sk, lg, _m = loop_once(pc_fast=0.0)
ck("W-2 시계 정상·5초 전 발행 → 진입, size_mult 없으면 1.0", len(ent) == 1 and ent[0][0][5] == 1.0 and not sk, (ent, sk, lg))
ent, sk, lg, _m = loop_once(pc_fast=90.0)
ck("W-3 PC 시계 90초 빠름: '오래됨'으로 버리지 않고(서버 시각 나이 5초) 자동 진입 보류 + clock_skew 보고",
   not ent and sk == ["clock_skew"] and "오래됨" not in lg and "자동 진입 보류" in lg and "90초 빠름" in lg, (sk, lg))
ck("W-3 무장 중이면 시계 경고 창 한 번", _m.root.after.call_count >= 1)
ent, sk, lg, _m = loop_once(pc_fast=-90.0)
ck("W-4 PC 시계 90초 느림 → 자동 진입 보류", not ent and sk == ["clock_skew"] and "90초 느림" in lg, (sk, lg))
ent, sk, lg, _m = loop_once(pc_fast=0.0, pub_ago=120.0)
ck("W-5 서버 시각 기준 2분 전 발행 → 오래됨 스킵(가드 유지)", not ent and sk == ["stale_signal"] and "서버 시각 기준" in lg, (sk, lg))
ent, sk, lg, _m = loop_once(with_date=False, pub_abs=time.time() + 40)
ck("W-6 Date 없음 + 발행 시각이 PC 시계보다 40초 뒤(PC 느림) → 진입 안 함(clock_skew)",
   not ent and sk == ["clock_skew"] and "PC 시계가 느림" in lg, (sk, lg))
ent, sk, lg, _m = loop_once(with_date=False, pub_ago=3.0)
ck("W-7 Date 없음 + 정상 나이 → 종전처럼 진입(PC 시계 폴백)", len(ent) == 1 and not sk, (sk, lg))
ent, sk, lg, _m = loop_once(pc_fast=90.0, age_hdr=60)
ck("W-8 캐시 응답(Age)으로는 시계를 재지 않음 → 종전처럼 PC 시계(이 경우 오래됨)", not ent and sk == ["stale_signal"], (sk, lg))
ent, sk, lg, _m = loop_once(size_mult=0)
ck("size_mult0-1 명시적 0 → 진입 안 함(로그·보고)", not ent and sk == ["size_mult0"] and "size_mult" in lg, (sk, lg))
ent, sk, lg, _m = loop_once(size_mult=-1)
ck("size_mult0-2 음수 → 진입 안 함", not ent and sk == ["size_mult0"])
ent, sk, lg, _m = loop_once(size_mult="abc")
ck("size_mult0-3 못 읽는 값 → 진입 안 함", not ent and sk == ["size_mult0"])
ent, sk, lg, _m = loop_once(size_mult=2.5)
ck("size_mult0-4 2.5 → 2.5배 진입", len(ent) == 1 and ent[0][0][5] == 2.5, ent)
ent, sk, lg, _m = loop_once(size_mult=None)
ck("size_mult0-5 None(필드 값 없음) → 1.0 진입", len(ent) == 1 and ent[0][0][5] == 1.0, ent)
ent, sk, lg, _m = loop_once(size_mult=9)
ck("size_mult0-6 3 초과는 3으로 클램프(종전 그대로)", len(ent) == 1 and ent[0][0][5] == 3.0, ent)

# 하트비트 만료 판정이 서버 시각 기준
me = MagicMock()
me._clock_skew, me._clock_skew_ts = 1200.0, time.time()
ck("W-9 시계 차이 값은 잰 지 10분 안만 믿음", eqgui.App._clock_skew_now(me) == 1200.0)
me._clock_skew_ts = time.time() - 700
ck("W-9 10분 지난 값은 None(종전처럼 PC 시계)", eqgui.App._clock_skew_now(me) is None)

eqgui.messagebox = _orig_mb
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}: {FAIL}"))
sys.exit(1 if FAIL else 0)
