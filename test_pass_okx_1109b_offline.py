# EQ Autopilot 2026.11.09b 오프라인 시험(2026-11-09 대표 실기기 시험, 오늘 저녁 시험판). 0 = 통과. 네트워크 없음(브로커, 서버 전부 가짜).
#   1 평가 통과: (a) 평가 계좌 청산(하루 상한, 통과 익절) 뒤 잔고를 다시 읽어 통과를 본다 - Lucid(두 push), Topstep(활성 목록에서 빠진 Combine은
#     닫힌 계좌까지 읽음). (b) 통과 금액 이상인데 앱이 스스로 표시하지 못하면 알림 창 [통과로 표시] [평가로 두기](Topstep, Lucid 같은 이름) + 회원 알림
#     eval_target_<해시>(통과 금액마다 한 번, 창은 거래일마다), 버튼은 자동 통과 표시와 같은 일. 자동 표시 안전 규칙(최고일을 다 알 때만)은 그대로.
#   2 OKX 50123(키의 거래 허용 코인 목록) 안내 - 진입 실패 로그, 회원 DM, 점검 DM, 일반 '권한' 안내보다 먼저.
#   3 선물 클레임 양보도 entry_skip claim_yield.
#   4 자산별 브로커 목록(bkl)은 이미 생존 핑에 있다(바꾸지 않음).
#   5 Lucid 이름으로 단계: LDI, LDE = 평가(묻지 않음), LDF = 펀디드(새 계좌, 펀디드 권장 1R), 그 밖(Flex, Pro)은 종전대로 묻는다. 서버 패턴으로 더할 수 있다.
# 사용: cd executor && python3.9 test_pass_okx_1109b_offline.py
import hashlib
import inspect
import os
import sys
import tempfile
import threading
from types import SimpleNamespace

os.environ["HOME"] = tempfile.mkdtemp()            # 앱 설정 폴더(APP_DIR)를 임시로
os.environ.setdefault("EQ_NO_ALERT", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui as E  # noqa: E402

fails = []


def ck(n, ok, got=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok else f"  got={got!r}"[:700]))
    if not ok:
        fails.append(n)


E._rq_mod.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call in offline test"))
E._SRV_HTTP.post = E._rq_mod.post
E._DC_PASS_RECHECK_S = (0,)
E._load_daycap_state = lambda: {}                  # 시험마다 새 상태(디스크를 건너 다른 시험의 통과 기록이 섞이지 않게)
E._save_daycap_state = lambda d: None
E._save_cfg = lambda *a, **k: None
E._DAYCAP_NT8_TWO_PUSH_S = 0.0


class _SyncThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **k):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        self._t(*self._a, **self._k)


class Stub:
    lang = "ko"
    _token = "tok"

    def __init__(self):
        self.logs, self.alerts, self.notices, self.evs, self.marked = [], [], [], [], []
        self._dc_st = None
        self._acfg = {}
        self._profile = {}

    def log(self, m):
        self.logs.append(str(m))

    def _member_alert(self, kind, ko, en):
        self.alerts.append((kind, ko, en))

    def _ui(self, fn):
        fn()

    def _notice(self, title, msg, buttons=None):
        self.notices.append((title, msg, buttons))

    def _send_ev(self, kind, asset, **kw):
        self.evs.append((kind, asset, kw))

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None


for _n in ("_daycap_key", "_dc_state", "_dc_save", "_dc_map", "_daycap_money", "_daycap_note_once", "_dc_exc_note",
           "_dc_note_hourly", "_daycap_stage_cfg", "_daycap_stage_flag", "_daycap_passed_rec", "_daycap_resolve_cfg", "_dc_best",
           "_dc_lucid_complete", "_dc_next_tday", "_dc_pass_markable", "_dc_target_stable", "_dc_tchg_update",
           "_daycap_pass_plausible", "_daycap_mark_passed", "_lucid_mark_funded", "_daycap_pass_prompt", "_dc_pass_hold",
           "_dc_pass_target_notice", "_dc_pass_confirm", "_dc_pass_recheck_soon", "_dc_pass_recheck_once", "_daycap_clear_passed",
           "_dc_f", "_daycap_mon_finish", "_dm_kind", "_lucid_stage_check", "_lucid_stage_note", "_prop_auto_type",
           "_prop_r_info", "_prop_reco_r", "_prop_reco_size", "_prop_stage_open", "_prop_legacy_locked", "_APP_VER"):
    setattr(Stub, _n, E.App.__dict__[_n])
Stub._acct_broker = lambda self, a, ac: ac.get("broker", "nt8")
Stub._entry_skip_ev = E.App.__dict__["_entry_skip_ev"]

LDI, LDI2, LDE, LDF, LFE = "LDI15086899310001", "LDI15086899310002", "LDE15012345670001", "LDF15086899310001", "LFE05012345670001"
TS = "150KTC-V2-26505604"
DAY = E._daycap_day_start()[1]

# ══ 0 버전 ═════════════════════════════════════════════════════════════════════════
ck("0 앱 버전 2026.11.09b 이상", E.App._APP_VER >= "2026.11.09b", E.App._APP_VER)

# ══ 5 Lucid 이름으로 단계 ═══════════════════════════════════════════════════════════
ck("5a LDF = 펀디드, 규모 150K(이름)", E._dc_name_info("nt8", LDF)[:2] == ("funded", 150000), E._dc_name_info("nt8", LDF))
ck("5a LDI, LDE = 평가(종전 패턴 그대로)", E._dc_name_info("nt8", LDI)[0] == "eval" and E._dc_name_info("nt8", LDE)[0] == "eval")
ck("5b 이름 단계: LDI, LDE = eval, LDF = funded, LFE(Flex) = 모름",
   [E._lucid_name_stage(x) for x in (LDI, LDE, LDF, LFE)] == ["eval", "eval", "funded", None],
   [E._lucid_name_stage(x) for x in (LDI, LDE, LDF, LFE)])
ck("5c0 옛 시험 표본 꼴(규모 뒤 글자, LDI150A1234)은 종전대로 답 기다림", E._daycap_stage("nt8", {}, "LDI150A1234")[0] == "unanswered"
   and E._lucid_name_stage("LDF150A1234") is None)
ck("5c 답 없이 LDI = 평가(묻지 않음), LDF = 펀디드, LFE = 답 기다림",
   E._daycap_stage("nt8", {}, LDI)[0] == "eval" and E._daycap_stage("nt8", {}, LDF) == ("funded", 150000)
   and E._daycap_stage("nt8", {}, LFE)[0] == "unanswered",
   (E._daycap_stage("nt8", {}, LDI), E._daycap_stage("nt8", {}, LDF), E._daycap_stage("nt8", {}, LFE)))
ck("5d 회원이 펀디드로 답한 LDI는 회원 답이 이긴다", E._daycap_stage("nt8", {"stage": "funded", "stage_for": LDI.casefold()}, LDI)[0] == "funded")
ck("5e LDF는 프롭 회사 계좌(한 계약 바닥 안 씀)", E._prop_firm_acct("nt8", LDF) is True and E._prop_firm_acct("tradovate", LDF) is True)
st5 = Stub()
ck("5f LDF 행 = 앱이 알아낸 단계 펀디드", E.App._prop_auto_type(st5, "nt8", LDF, {}) == "funded" and E.App._prop_auto_type(st5, "nt8", LDI, {}) == "test",
   (E.App._prop_auto_type(st5, "nt8", LDF, {}), E.App._prop_auto_type(st5, "nt8", LDI, {})))
_ri = E.App._prop_r_info(st5, {"on": True, "mode": "reco"}, "nt8", LDF)
ck("5g LDF 프롭(추천 금액) = 펀디드 권장 1R $450", _ri[0] == "funded" and abs(float(_ri[1]) - 450.0) < 1e-9 and _ri[2] is True, _ri)
st5._acfg = {"NQ": {"accounts": [{"id": LDI, "broker": "nt8"}, {"id": LFE, "broker": "nt8"}, {"id": LDF, "broker": "nt8"}]}}
E.App._lucid_stage_check(st5)
ck("5h 단계 질문(로그)은 이름으로 모르는 Lucid 계좌(LFE)에만", len([m for m in st5.logs if "평가인지 펀디드인지" in m]) == 1
   and any("…0001" in m and "LFE" in m for m in st5.logs if "평가인지 펀디드인지" in m), st5.logs)
ck("5i0 내장 패턴과 같은 꼴을 서버가 보내도 받는다", E._srv_pat_ok("nt8", "funded", E._LUCID_FUNDED_RE) and E._srv_pat_ok("nt8", "eval_known", E._LUCID_EVAL_KNOWN_RE))
ck("5i 서버 패턴: nt8 funded, eval_known 받음(묶음 하나 = 규모)",
   E._srv_pat_ok("nt8", "funded", r"^LFF(025|050|100|150)") and E._srv_pat_ok("nt8", "eval_known", r"^LF[IE](025|050|100|150)"))
ck("5i 서버 패턴: 평가 이름에 맞는 펀디드, LDF에 맞는 평가, 묶음 꼴이 틀린 것은 버림",
   not E._srv_pat_ok("nt8", "funded", r"^LD[IF](025|050|100|150)") and not E._srv_pat_ok("nt8", "eval_known", r"^LD[IEF](025|050|100|150)")
   and not E._srv_pat_ok("nt8", "funded", r"^LDF(150)(A)") and not E._srv_pat_ok("nt8", "funded", r"LDF(150)"))
_vr = E._srv_rules_validate({"names": {"nt8": {"funded": r"^LFF(025|050|100|150)"}}}) if hasattr(E, "_srv_rules_validate") else None
_src_names = inspect.getsource(E)
ck("5j 서버 규칙 읽기가 nt8 funded, eval_known 칸을 받는다", '("nt8", ("eval", "funded", "eval_known"))' in _src_names)
ck("5k 평가 이름 패턴은 LDF 표본에 맞지 않아야 한다(서버 평가 패턴이 펀디드를 평가로 만들지 못하게)",
   "LDF15012345670001" in E._PAT_NONEVAL_PROBES and not E._srv_pat_ok("nt8", "eval", r"^L([DFP])([IEF])(025|050|100|150)"))

# ══ 2 OKX 50123 ════════════════════════════════════════════════════════════════════
_m = "OKX /api/v5/trade/order HTTP 401: code=50123 This API Key does not have trading permission for the Crypto BTC"
_h_ko, _h_en = E._entry_fail_hint(_m, True), E._entry_fail_hint(_m, False)
ck("2a 진입 실패 로그 안내 = 코인 목록(BTC, USDT, USDC, USD) + 유럽 Expiry(KO/EN)",
   "BTC, USDT, USDC, USD" in _h_ko and "Expiry" in _h_ko and "X-Perp" in _h_ko and "BTC, USDT, USDC and USD" in _h_en and "Expiry" in _h_en,
   (_h_ko, _h_en))
ck("2b 회원 DM 안내(합니다체)도 같은 문장", E._entry_fail_hint_dm(_m) == (E._OKX_50123_KO, E._OKX_50123_EN) and E._OKX_50123_KO.endswith("분류합니다)."))
ck("2c 점검 DM 안내도(OKX)", E._preflight_hint_dm(_m, "okx") == (E._OKX_50123_KO, E._OKX_50123_EN))
ck("2d 50123이 아닌 '권한' 오류는 종전 안내", "파생상품(계약)" in E._entry_fail_hint("retCode=10005 permission denied", True)
   and "파생상품(계약)" in E._entry_fail_hint_dm("permission denied")[0])
ck("2e 51008(증거금 부족)은 종전 증거금 안내 그대로", "부족" in E._entry_fail_hint("code=51008 Insufficient margin", True))

# ══ 3 선물 클레임 양보 ══════════════════════════════════════════════════════════════
_fs = inspect.getsource(E.App._run_futures_entry)
_i = _fs.find("if live and not self._claim_entry(sig, _aid, per_process=True):")
_j = _fs.find("return", _i)
ck("3 선물 클레임 양보 → entry_skip claim_yield(되돌아가기 전에)",
   _i > 0 and 'self._entry_skip_ev(asset, "claim_yield")' in _fs[_i:_j], _fs[_i:_j + 10][-300:])

# ══ 4 bkl ═════════════════════════════════════════════════════════════════════════
_ap = inspect.getsource(E.App._alive_ping)
ck("4 생존 핑에 자산별 브로커 목록(bkl) 이미 있음, brokers(bk)도 그대로", '"bkl": self._asset_brokers_multi()' in _ap and '"bk": self._asset_brokers()' in _ap)
_mb = SimpleNamespace(_conn_qual=lambda a: {"BTC": ["okx", "bybit"], "NQ": ["projectx", "nt8"]}.get(a, []))
ck("4b _asset_brokers_multi = {자산: [브로커, …]}", E.App._asset_brokers_multi(_mb) == {"NQ": ["projectx", "nt8"], "BTC": ["okx", "bybit"]},
   E.App._asset_brokers_multi(_mb))

# ══ 1 평가 통과 ════════════════════════════════════════════════════════════════════


def nt8_target(acct=LDI):
    return {"key": E.App._daycap_key("nt8", acct), "broker": "nt8", "sc": acct, "pr": {}, "lbl": "Lucid " + acct[-4:],
            "cfg": {"broker": "nt8"}}


def px_target(acct=TS):
    return {"key": E.App._daycap_key("projectx", acct), "broker": "projectx", "sc": acct, "pr": {}, "lbl": "TS " + acct[-4:],
            "cfg": {"broker": "projectx"}}


def run_recheck(st, b, t, ticks=12):
    """다시 보기 예약 + 감시 회차 흉내(기한을 바로 지난 것으로). 남은 예약 수를 돌려준다."""
    if t is not None:
        E.App._dc_pass_recheck_soon(st, b, t, {"lbl": t["lbl"]})
    for _ in range(ticks):
        prk = st._dc_map("_dc_prk")
        if not prk:
            break
        for r in prk.values():
            r["due"] = 0
        E.App._dc_prk_tick(st)
    return len(st._dc_map("_dc_prk"))


def snaps(seq):
    """_daycap_mon_snap 가짜: seq 차례로(마지막 값 반복). 값 = 잔고 또는 None(못 읽음). ts는 부를 때마다 새로."""
    it = {"i": 0}

    def f(b, bk, sc, acache=None, fresh=False, fresh_accts=False):   # 인스턴스 속성이라 self 없이 불린다
        v = seq[min(it["i"], len(seq) - 1)]
        it["i"] += 1
        if v is None:
            return None
        return {"name": sc, "bal": float(v), "aid": sc, "poss": [], "nl": float(v), "ts": float(it["i"])}
    return f


# 1a Lucid 하루 상한 청산 뒤: 잔고가 통과 금액(150,000 + 9,000) 이상 → 최고일을 다 모름(원장 없음) → 표시하지 않고 알림 창 + 회원 알림
st = Stub()
st._daycap_mon_snap = snaps([159120.0])
t = nt8_target()
info = {"mode": "cap", "day": DAY, "lbl": t["lbl"], "sc": LDI, "broker": "nt8", "firm": "Lucid", "assets": ["NQ"], "dp": 4400.0,
        "cap": 4410.0, "pass_tp": 159050.0, "size": 150000}
st._dc_map("_dc_fired")[t["key"]] = info
E.App._daycap_mon_finish(st, object(), t, {"aid": LDI, "poss": []})
ck("1a0 마무리가 통과 다시 보기를 예약(감시 회차가 한다, 잠들지 않음)", t["key"] in st._dc_map("_dc_prk"), st._dc_map("_dc_prk"))
ck("1a1 다시 보기는 NinjaTrader 두 push 뒤에 끝난다", run_recheck(st, None, None) == 0, st._dc_map("_dc_prk"))
ck("1a 하루 상한 청산 뒤에도 통과를 다시 본다 → 통과 금액 이상, 최고일 모름 → 통과 표시 안 함(안전 규칙)",
   t["key"] not in st._dc_state()["passed"], st._dc_state()["passed"])
ck("1a 대신 알림 창 하나: [통과로 표시] [평가로 두기](Lucid도 같은 이름), 문구에 통과 금액과 Lucid 화면 확인",
   len(st.notices) == 1 and [b[0] for b in st.notices[0][2]] == ["통과로 표시", "평가로 두기"]
   and "평가 통과 목표 잔고에 닿았습니다" in st.notices[0][1] and "$159,000" in st.notices[0][1] and "Lucid 화면에서" in st.notices[0][1],
   st.notices)
_ka = [a for a in st.alerts if a[0].startswith("eval_target_")]
_want_kind = E.App._dm_kind("eval_target", "nt8", LDI)
ck("1b 회원 알림 한 번: 종류 eval_target_<해시 6자>(통과 알림 eval_passed_와 다른 이름, 24자 안)",
   len(_ka) == 1 and _ka[0][0] == _want_kind and len(_want_kind) <= 24 and _want_kind != E.App._dm_kind("eval_passed", "nt8", LDI)
   and "[통과로 표시]" in _ka[0][1] and "앱 알림 창에서" in _ka[0][1] and "펀디드로 바꾸기" not in _ka[0][1], st.alerts)
ck("1b 거래 기록(exit_ok cap_tp)은 그대로", ("exit_ok", "NQ", {"why": "cap_tp"}) in st.evs, st.evs)
# 같은 날 같은 금액이면 다시 묻지 않는다(감시가 1초마다 불러도)
for _ in range(3):
    E.App._dc_pass_hold(st, "nt8", LDI, t["lbl"], 159120.0, 159000.0, "best", "Lucid", size=150000, target=9000.0)
ck("1c 같은 거래일, 같은 통과 금액 = 창, 알림 다시 없음", len(st.notices) == 1 and len([a for a in st.alerts if a[0].startswith("eval_target_")]) == 1,
   (len(st.notices), st.alerts))
# [평가로 두기] → 통과 표시 없음, 다음 거래일에 다시 묻는다(회원 알림은 같은 금액이면 다시 안 보냄)
st.notices[0][2][1][1]()
ck("1d [평가로 두기] = 표시하지 않음, 답 기록", t["key"] not in st._dc_state()["passed"]
   and (st._dc_state()["pass_hold"][t["key"]].get("ans") or {}).get("v") == "eval", st._dc_state()["pass_hold"])
st._dc_state()["pass_hold"][t["key"]]["tn"]["day"] = "2000-01-03"
E.App._dc_pass_hold(st, "nt8", LDI, t["lbl"], 159120.0, 159000.0, "best", "Lucid", size=150000, target=9000.0)
ck("1d 다음 거래일 = 창 다시, 회원 알림은 같은 금액이라 한 번뿐", len(st.notices) == 2
   and len([a for a in st.alerts if a[0].startswith("eval_target_")]) == 1, (len(st.notices), st.alerts))
# [통과로 표시] = 자동 통과와 같은 일(통과 기록 src=member = 이 행 멈춤, eval_passed 알림, 1R 창). Lucid 행을 펀디드로 저장하지 않는다(새 LDF 계좌)
st._acfg = {"NQ": {"accounts": [{"id": LDI, "broker": "nt8", "prop": {}}]}}
st.notices[1][2][0][1]()
_pr = st._dc_state()["passed"].get(t["key"]) or {}
ck("1e [통과로 표시] → 통과 기록(src member, via member), 확인 대기 기록 지움",
   _pr.get("src") == "member" and _pr.get("via") == "member" and t["key"] not in st._dc_state()["pass_hold"], (_pr, st._dc_state()["pass_hold"]))
ck("1e Lucid 평가 행은 펀디드로 저장하지 않는다(Lucid는 통과 뒤 새 계좌를 준다), 통과 알림 eval_passed_ 한 번",
   st._acfg["NQ"]["accounts"][0]["prop"] == {}
   and [a[0] for a in st.alerts].count(E.App._dm_kind("eval_passed", "nt8", LDI)) == 1, (st._acfg, [a[0] for a in st.alerts]))
# 2026-11-10 리뷰 P0: 아직 통과 전일 때 푸는 길 = [프롭: …]의 '통과 표시 풀기'(단계만 다시 고르면 풀리지 않는다)
ck("1e 통과 로그: 이 계좌로는 더 진입하지 않음 + 새 펀디드 계좌(LDF)로 바꾸라 + 아직이면 '통과 표시 풀기'",
   any("이 계좌로는 더 진입하지 않습니다" in m and "새 펀디드 계좌" in m and "LDF" in m and "'통과 표시 풀기'" in m
       and "펀디드로 저장" not in m for m in st.logs), st.logs[-3:])
ck("1e2 기동 때 통과 기록이 있는 Lucid 계좌를 펀디드로 저장하지 않는다(_lucid_passed_persist 아무것도 안 함)",
   E.App._lucid_passed_persist(st) is None and st._acfg["NQ"]["accounts"][0]["prop"] == {})
ck("1e3 새 LDF 계좌 행 = 이름으로 펀디드(통과 기록과 무관)", E.App._daycap_stage_cfg(st, "nt8", LDF, {})[0] == "funded")
ck("1e 표시 뒤에는 단계 'passed'(이 계좌 진입 멈춤)", E.App._daycap_stage_cfg(st, "nt8", LDI, {})[0] == "passed")

# 1f Lucid 통과 익절 뒤 첫 읽기가 실현 전 잔고(NinjaTrader 늦은 반영) → 아직 아래 → 다음 간격에 다시 읽어 통과 금액 이상이면 묻는다
st = Stub()
st._daycap_mon_snap = snaps([156857.9, 156857.9, 159031.4, 159031.4])
t = nt8_target(LDI2)
E._DC_PASS_RECHECK_S = (0, 0)
run_recheck(st, object(), t)
ck("1f 첫 읽기 실현 전(아래) → 다음 간격에 다시 읽어 통과 금액 이상 → 알림 창(두 push 모두 이상일 때)",
   len(st.notices) == 1 and "$159,031" in st.notices[0][1], st.notices)
# 1g NinjaTrader 두 push: 같은 push(ts 같음)를 두 번 읽으면 판단하지 않는다
st = Stub()
st._daycap_mon_snap = lambda b, bk, sc, acache=None, fresh=False, fresh_accts=False: {"name": sc, "bal": 159100.0, "poss": [], "ts": 5.0}
_rec = {"t": nt8_target(LDI2), "b": object(), "info": {}, "s1": None}
ck("1g NT8 같은 push 두 번 = 판단 안 함(기다림)", E.App._dc_pass_recheck_once(st, _rec) == "wait" and E.App._dc_pass_recheck_once(st, _rec) == "wait"
   and not st.notices)
ck("1g 같은 push만 오면 기다림 횟수를 다 쓰고 놓는다", run_recheck(st, object(), nt8_target(LDI2), ticks=40) == 0 and not st.notices)
# 1h 다시 열린 포지션이면 감시에 맡긴다
st = Stub()
st._daycap_mon_snap = lambda b, bk, sc, acache=None, fresh=False, fresh_accts=False: {"name": sc, "bal": 159100.0, "poss": [{"con": "MNQ", "qty": 1}], "ts": 1.0}
ck("1h 다시 열린 포지션 = 끝(감시가 본다), 알림 없음",
   E.App._dc_pass_recheck_once(st, {"t": nt8_target(LDI2), "b": object(), "info": {}, "s1": None}) is True and not st.notices)
# 1i 통과 금액 아래면 아무것도 하지 않는다(하루 상한 청산의 보통 경우)
st = Stub()
st._daycap_mon_snap = snaps([154400.0])
ck("1i 통과 금액 아래 = 아무것도 안 함, 간격을 다 쓰면 놓음", run_recheck(st, object(), nt8_target(LDI2)) == 0 and not st.notices and not st.alerts)

# 1j Topstep: 거래 기록을 오늘 다 읽었다(최고일을 앎) → 자동 통과 표시 + eval_passed 알림(종전 규약)
st = Stub()
st._daycap_mon_snap = snaps([159300.0])
tp = px_target()
st._dc_state()["hist"][tp["key"]] = {"day": DAY, "ts": 0, "days": {"2000-01-03": 2500.0}, "bad": 0}
ck("1j Topstep 최고일을 앎 → 다시 보기가 통과로 표시", E.App._dc_pass_recheck_once(st, {"t": tp, "b": object(), "info": {}}) is True
   and (st._dc_state()["passed"].get(tp["key"]) or {}).get("via") == "recheck", st._dc_state()["passed"])
ck("1j 통과 알림 eval_passed_ 한 번, 알림 창 없이(통과 표시는 1R 제안 창)", [a[0] for a in st.alerts] == [E.App._dm_kind("eval_passed", "projectx", TS)],
   [a[0] for a in st.alerts])
# 1k Topstep: 통과한 Combine이 활성 목록에서 빠짐 → 닫힌 계좌까지 읽어 잔고를 본다, 거래 기록 모름 → [통과로 표시] [평가로 두기]
st = Stub()
st._daycap_mon_snap = snaps([None])
_acc_calls = []


class PXB:
    def _accounts(self, active_only=True, timeout=None, max_age=None):
        _acc_calls.append(active_only)
        return [{"id": 77, "name": TS, "balance": 159210.0, "canTrade": False}]


ck("1k 활성 목록에 없는 Combine = 닫힌 계좌까지 읽음", E.App._dc_pass_recheck_once(st, {"t": px_target(), "b": PXB(), "info": {}}) is True
   and _acc_calls == [False],
   _acc_calls)
ck("1k Topstep 버튼 = [통과로 표시] [평가로 두기], 회원 알림 eval_target_",
   len(st.notices) == 1 and [b[0] for b in st.notices[0][2]] == ["통과로 표시", "평가로 두기"] and "Topstep 화면에서" in st.notices[0][1]
   and [a[0] for a in st.alerts] == [E.App._dm_kind("eval_target", "projectx", TS)], (st.notices, st.alerts))
st.notices[0][2][0][1]()
ck("1k [통과로 표시] → 통과 기록(member) + eval_passed 알림", (st._dc_state()["passed"].get(px_target()["key"]) or {}).get("src") == "member"
   and E.App._dm_kind("eval_passed", "projectx", TS) in [a[0] for a in st.alerts], (st._dc_state()["passed"], st.alerts))
# 1l 영문
st = Stub()
st.lang = "en"
E.App._dc_pass_hold(st, "nt8", LDI2, "Lucid 0002", 159120.0, 159000.0, "best", "Lucid", size=150000, target=9000.0)
ck("1l EN: [Mark as passed] [Keep as evaluation], DM EN",
   [b[0] for b in st.notices[0][2]] == ["Mark as passed", "Keep as evaluation"] and "pass target balance was reached" in st.notices[0][1]
   and "notice window" in st.alerts[0][2], (st.notices, st.alerts))
# 1m 정해진 시각 청산 뒤에도 다시 보기를 건다(평가 계좌, 라이브)
_al = inspect.getsource(E.App._auto_loop_body)
ck("1m 정해진 시각 청산 보고 뒤 평가 계좌면 통과 다시 보기", "App._dc_pass_recheck_soon(self, None, _tg" in _al
   and _al.index("App._dc_pass_recheck_soon(self, None, _tg") > _al.index('self._send_fill(j["asset"], closed=True)'))
_sc = inspect.getsource(E.App._daycap_mon_scan)
ck("1o 감시 회차가 다시 보기를 돌린다(감시 대상이 없어도, 정리를 낸 회차는 건너뜀), 감시 타이머도 예약이 있으면 돈다",
   _sc.count("App._dc_prk_run(self)") == 2 and _sc.index("App._dc_prk_run(self)") < _sc.index("return", _sc.index("if not tg:"))
   and _sc.rindex("App._dc_prk_run(self)") > _sc.index("self._dc_fire_and_watch(")
   and 'self._dc_map("_dc_prk")' in inspect.getsource(E.App._daycap_tick))
_fin = inspect.getsource(E.App._daycap_mon_finish)
ck("1n 평가 계좌 익절 마무리(하루 상한, 통과 익절 모두)에서 다시 보기", "App._dc_pass_recheck_soon(self, b, t, info)" in _fin
   and _fin.index("App._dc_pass_recheck_soon") > _fin.index('if info.get("mode") == "pass":'))

print("FAIL", len(fails), fails) if fails else print("ALL PASS")
sys.exit(1 if fails else 0)
