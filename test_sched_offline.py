# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# 금 진입, 청산 시각 서버 지정(하트비트 autopilot.sched, 대표 2026-11-05 아시아 그리드 전환) 오프라인 시험. 네트워크 차단, 임시 HOME.
import os, socket, sys, tempfile, json
HOME = tempfile.mkdtemp(prefix="eq_sched_"); os.environ["HOME"] = HOME; os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None); os.environ["EQ_NO_ALERT"] = "1"; os.environ["EQ_ERR_REPORT"] = "0"
os.environ["TZ"] = "Europe/Berlin"
import time as _tm; _tm.tzset()
def _blk(*a, **k): raise OSError("network blocked by test")
socket.socket.connect = _blk; socket.create_connection = _blk
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui as E  # noqa: E402
assert E.APP_DIR.startswith(HOME)
FAIL = []
def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c: FAIL.append(n)
class Stub:
    lang = "ko"
    def __init__(self): self.logs = []; self._auto_accts = {}
    def log(self, m): self.logs.append(str(m))
ASIA = {"GC": {"entry": ["UTC", 8], "exit": ["UTC", 12]}}
DEF = {"default": True}                                    # 서버가 분명히 내장 시각으로 되돌림(2026-11-10 리뷰 P1)
ck("1 기본 = 뉴욕 2시 진입, 6시 청산", E._ASSET_ENTRIES["GC"] == [("America/New_York", 2)] and E._ASSET_EXITS["GC"] == [("America/New_York", 6)])
ck("2 검증: 정상 값", E._sched_validate(ASIA) == {"GC": ([("UTC", 8)], [("UTC", 12)], (0, 1, 2, 3, 4))}, E._sched_validate(ASIA))
for bad, why in (({"GC": {"entry": ["UTC", 8], "exit": ["UTC", 21]}}, "보유 13시간(1~12시간만)"),
                 ({"GC": {"entry": ["Asia/Tokyo", 17], "exit": ["Asia/Tokyo", 21]}}, "허용 안 된 tz"),
                 ({"GC": {"entry": ["UTC", 8], "exit": ["America/New_York", 12]}}, "tz 섞임"),
                 ({"GC": {"entry": ["UTC", 8.5], "exit": ["UTC", 12.5]}}, "정수 아님"),
                 ({"GC": {"entry": ["UTC", 24], "exit": ["UTC", 4]}}, "범위 밖"),
                 ({"GC": "x"}, "모양 틀림")):
    ck(f"3 검증 거부: {why}", E._sched_validate(bad) == {}, E._sched_validate(bad))
s = Stub()
s._auto_accts = {("GC", 0): [{"asset": "GC", "tz": "America/New_York", "hour": 6}], ("NQ", 0): [{"asset": "NQ", "tz": "America/New_York", "hour": 14}]}
E.App._sched_apply(s, ASIA)
ck("4 적용: 표 교체(진입 08 UTC, 청산 12 UTC), NQ는 그대로",
   E._ASSET_ENTRIES["GC"] == [("UTC", 8)] and E._ASSET_EXITS["GC"] == [("UTC", 12)] and E._ASSET_EXITS["NQ"] == [("America/New_York", 14)])
ck("5 무장된 금 자동 청산 잡도 12 UTC로, NQ 잡은 그대로",
   s._auto_accts[("GC", 0)][0]["tz"] == "UTC" and s._auto_accts[("GC", 0)][0]["hour"] == 12 and s._auto_accts[("NQ", 0)][0]["hour"] == 14)
ck("6 로그 한 줄(KO)", any("진입 08:00, 청산 12:00 UTC" in m for m in s.logs), s.logs)
ck("7 디스크 저장", json.load(open(E._SCHED_PATH)) == {"GC": {"entry": ["UTC", 8], "exit": ["UTC", 12], "days": [0, 1, 2, 3, 4]}})
n = len(s.logs); E.App._sched_apply(s, ASIA)
ck("8 같은 값 다시 = 아무것도 안 함(로그 없음)", len(s.logs) == n)
E.App._sched_apply(s, {"GC": {"entry": ["UTC", 8], "exit": ["UTC", 22]}})
ck("9 어긋난 값 = 지금 값 유지 + 알림 한 번", E._ASSET_EXITS["GC"] == [("UTC", 12)] and any("읽을 수 없어" in m for m in s.logs))
n = len(s.logs)
E.App._sched_apply(s, None)
E.App._sched_apply(s, {})
ck("10 서버가 안 보냄, 빈 값 = 마지막 정상 값 그대로(되돌리지 않음, 디스크, 잡, 로그 그대로 - 2026-11-10 리뷰 P1)",
   E._ASSET_EXITS["GC"] == [("UTC", 12)] and os.path.exists(E._SCHED_PATH) and s._auto_accts[("GC", 0)][0]["hour"] == 12
   and len(s.logs) == n, s.logs[n:])
E.App._sched_apply(s, {"BTC": "x", "foo": 1})
ck("10b 읽을 수 있는 자산이 없는 값 = 마지막 정상 값 그대로(다른 자산도 되돌리지 않음) + 알림",
   E._ASSET_EXITS["GC"] == [("UTC", 12)] and E._ASSET_EXITS["BTC"] == [("UTC", h) for h in (2, 6, 10, 14, 18, 22)]
   and any("BTC 진입, 청산 시각을 읽을 수 없어" in m for m in s.logs), s.logs[-1:])
E.App._sched_apply(s, {"default": False})
ck("10c 'default'가 참이 아니면 되돌리지 않음", E._ASSET_EXITS["GC"] == [("UTC", 12)])
E.App._sched_apply(s, DEF)
ck("10d 서버가 {\"default\": true} = 기본으로 되돌림 + 디스크 지움 + 잡도 되돌림",
   E._ASSET_EXITS["GC"] == [("America/New_York", 6)] and not os.path.exists(E._SCHED_PATH)
   and s._auto_accts[("GC", 0)][0]["hour"] == 6 and s._auto_accts[("GC", 0)][0]["tz"] == "America/New_York")
E.App._sched_apply(s, ASIA)
E._ASSET_ENTRIES["GC"] = [("America/New_York", 2)]; E._ASSET_EXITS["GC"] = [("America/New_York", 6)]
E._sched_load()
ck("11 기동 때 디스크 값 = 첫 하트비트 전에도 아시아 시각", E._ASSET_EXITS["GC"] == [("UTC", 12)] and E._ASSET_ENTRIES["GC"] == [("UTC", 8)])
import datetime as _dt
d = E._next_entry_dt("GC")
ck("12 다음 금 진입 시각 = 08:00 UTC, 평일", d.astimezone(_dt.timezone.utc).hour == 8 and d.weekday() < 5, d)
s2 = Stub(); s2.lang = "en"; E.App._sched_apply(s2, DEF); E.App._sched_apply(s2, ASIA)
ck("13 EN 로그는 영어", s2.logs and "entry 08:00, exit 12:00 UTC" in s2.logs[-1] and not any("가" <= ch <= "힣" for ch in s2.logs[-1]), s2.logs)
# ── 세 자산 전부(대표 2026-11-05 "서버에서 다 바꿀수 있게 하자 하드코딩은 싫어") ──
E.App._sched_apply(s, DEF)
ck("15 기본 표: NQ 평일 10→14 뉴욕, BTC 일요일 22 UTC + 판정 2,6,10,14,18,22",
   E._ENTRY_DAYS == {"NQ": (0, 1, 2, 3, 4), "GC": (0, 1, 2, 3, 4), "BTC": (6,)}
   and E._ASSET_EXITS["BTC"] == [("UTC", h) for h in (2, 6, 10, 14, 18, 22)] and E._BTC_BLOCK_K == {2: 0, 6: 1, 10: 2, 14: 3, 18: 4, 22: 5})
s3 = Stub()
s3._auto_accts = {("BTC", 0): [{"asset": "BTC", "tz": "UTC", "hour": h, "broker": "bybit"} for h in (2, 6, 10, 14, 18, 22)],
                  ("NQ", 0): [{"asset": "NQ", "tz": "America/New_York", "hour": 14, "broker": "projectx"}]}
ALL = {"NQ": {"entry": ["America/New_York", 9], "exit": ["America/New_York", 13], "days": [0, 1, 2, 3, 4]},
       "GC": {"entry": ["UTC", 8], "exit": ["UTC", 12]},
       "BTC": {"entry": ["UTC", 20], "days": [5, 6]}}
E.App._sched_apply(s3, ALL)
ck("16 NQ 9→13 뉴욕, 잡도 13시(브로커 칸 유지)",
   E._ASSET_ENTRIES["NQ"] == [("America/New_York", 9)] and s3._auto_accts[("NQ", 0)] == [{"asset": "NQ", "tz": "America/New_York", "hour": 13, "broker": "projectx"}])
ck("17 BTC 20 UTC 토·일 진입, 판정 여섯 = 0,4,8,12,16,20 + 블록 표 다시 만듦 + 잡 여섯 다시 만듦",
   E._ASSET_ENTRIES["BTC"] == [("UTC", 20)] and E._ENTRY_DAYS["BTC"] == (5, 6)
   and [h for _t, h in E._ASSET_EXITS["BTC"]] == [0, 4, 8, 12, 16, 20] and E._BTC_BLOCK_K == {0: 0, 4: 1, 8: 2, 12: 3, 16: 4, 20: 5}
   and [j["hour"] for j in s3._auto_accts[("BTC", 0)]] == [0, 4, 8, 12, 16, 20] and all(j["broker"] == "bybit" for j in s3._auto_accts[("BTC", 0)]))
d = E._next_entry_dt("BTC")
ck("18 다음 BTC 진입 = 토 또는 일 20:00 UTC", d.astimezone(_dt.timezone.utc).hour == 20 and d.astimezone(_dt.timezone.utc).weekday() in (5, 6), d)
ck("19 로그: 자산마다 한 줄(KO, 요일 포함)", sum(1 for m in s3.logs if m.startswith("⏰")) == 3 and any("진입 요일 토일" in m for m in s3.logs), s3.logs)
E.App._sched_apply(s3, {"NQ": {"entry": ["America/New_York", 9], "exit": ["America/New_York", 23]}, "GC": {"entry": ["UTC", 8], "exit": ["UTC", 12]},
                        "BTC": {"entry": ["UTC", 20], "days": [5, 6]}})
ck("20 NQ만 어긋남(보유 14시간) = NQ는 지금 값 유지, 나머지 그대로, 알림 한 번",
   E._ASSET_EXITS["NQ"] == [("America/New_York", 13)] and E._ASSET_EXITS["GC"] == [("UTC", 12)]
   and any("NQ 진입, 청산 시각을 읽을 수 없어" in m for m in s3.logs), s3.logs[-2:])
for bad, why in (({"BTC": {"entry": ["UTC", 22], "days": []}}, "요일 없음"), ({"BTC": {"entry": ["UTC", 22], "days": [7]}}, "요일 범위 밖"),
                 ({"BTC": {"entry": ["UTC", 22], "days": [True]}}, "요일 bool"), ({"NQ": {"entry": ["America/New_York", True], "exit": ["America/New_York", 14]}}, "시 bool")):
    ck(f"21 검증 거부: {why}", E._sched_validate(bad) == {}, E._sched_validate(bad))
E.App._sched_apply(s3, {"GC": {"entry": ["UTC", 8], "exit": ["UTC", 12]}})
ck("22 서버가 금만 보냄 = NQ, BTC는 기본으로 되돌림(보낸 자산만 바뀐 값)",
   E._ASSET_ENTRIES["NQ"] == [("America/New_York", 10)] and E._ASSET_ENTRIES["BTC"] == [("UTC", 22)] and E._ENTRY_DAYS["BTC"] == (6,)
   and E._BTC_BLOCK_K == {2: 0, 6: 1, 10: 2, 14: 3, 18: 4, 22: 5} and E._ASSET_EXITS["GC"] == [("UTC", 12)])
ck("23 디스크에는 기본과 다른 자산만(금)", json.load(open(E._SCHED_PATH)) == {"GC": {"entry": ["UTC", 8], "exit": ["UTC", 12], "days": [0, 1, 2, 3, 4]}},
   json.load(open(E._SCHED_PATH)))
_src_all = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("24 요일 규칙 하드코딩 없음(진입 계산 두 곳이 _ENTRY_DAYS, BTC 24h 방어도 진입 표 기준)",
   _src_all.count("_ENTRY_DAYS.get(") >= 3 and 'j["hour"] == 22 and now.weekday() == 6' not in _src_all
   and "if asset in _ENTRY_SUNDAY_ONLY" not in _src_all)
E.App._sched_apply(s3, DEF)
# ── 왼쪽 메뉴 '진입 시각' 칸(대표 2026-11-05 "자산별 진입시간 요일이 어딘가에 나오게 하자") - 이 PC(베를린) 현지 시각 ──
_u = _dt.datetime(2026, 11, 9, 12, 0, tzinfo=_dt.timezone.utc)          # 월요일, 겨울 시간
L = E._sched_lines(True, _u)
ck("25 기본(21y 이름 = 종목 기호): NQ 월~금 16:00~20:00(뉴욕 10:00), GC 월~금 08:00~12:00(뉴욕 02:00), BTC 일 23:00 + 아래 줄 최대 24시간 보유(UTC 22:00)",
   L == [("NQ", "월~금 16:00~20:00", "뉴욕 10:00"), ("GC", "월~금 08:00~12:00", "뉴욕 02:00"), ("BTC", "일 23:00\n최대 24시간 보유", "UTC 22:00")], L)
E._sched_set(E._sched_validate(ASIA), raw_keys={"GC"})
ck("26 서버가 금을 아시아로 = 칸도 월~금 09:00~13:00(UTC 08:00)", E._sched_lines(True, _u)[1] == ("GC", "월~금 09:00~13:00", "UTC 08:00"), E._sched_lines(True, _u))
ck("27 EN 칸", E._sched_lines(False, _u)[1] == ("GC", "Mon~Fri 09:00~13:00", "UTC 08:00"), E._sched_lines(False, _u))
E._sched_set({}, raw_keys=None)
_srcp = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("28 메뉴에 칸을 만들고, 시각이 바뀌면 다시 그린다(화면 줄)", "self._sched_box = tk.Frame(_side" in _srcp and "self._ui(self._sched_paint)" in _srcp)
# ── 열린 포지션이 있을 수 있는 자산의 청산 잡은 오늘 이미 지난 시각으로 옮기지 않는다(2026-11-10 리뷰 P1) ──
import time as _time
E.App._sched_apply(Stub(), DEF)
_hn = _dt.datetime.now(_dt.timezone.utc).hour
_new_x, _old_x = _hn, (_hn + 2) % 24                      # 새 청산 = 이번 시(오늘 이미 지남), 지금 잡 = 두 시간 뒤(아직)
NEWS = {"GC": {"entry": ["UTC", (_hn - 4) % 24], "exit": ["UTC", _new_x]}}
s4 = Stub()
s4._auto_accts = {("GC", 0): [{"asset": "GC", "tz": "UTC", "hour": _old_x, "acct": "A1", "broker": "projectx"}]}
s4._entered_at = {"GC": _time.time() - 3600}               # 한 시간 전 진입 = 포지션이 열려 있을 수 있다
s4._auto_fired = {}
E.App._sched_apply(s4, NEWS)
ck("29 열린 포지션 가능 + 새 청산 시각이 오늘 지남 = 잡은 지금 시각 그대로(옮기지 않음) + 로그, 표는 새 값",
   s4._auto_accts[("GC", 0)][0]["hour"] == _old_x and E._ASSET_EXITS["GC"] == [("UTC", _new_x)]
   and any("이른 쪽인 지금 청산 시각" in m for m in s4.logs) and E.App._sched_exits_now(s4, "GC") == [("UTC", _old_x)], s4.logs)
E.App._sched_apply(s4, NEWS)
E.App._sched_apply(s4, None)
ck("30 그 뒤 하트비트(같은 값, sched 없음) = 지금 잡 그대로, 로그 한 번", s4._auto_accts[("GC", 0)][0]["hour"] == _old_x
   and sum(1 for m in s4.logs if "이른 쪽인 지금 청산 시각" in m) == 1, s4.logs)
s4._auto_fired[("GC", _old_x, "A1")] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")   # 지금 잡이 오늘 청산을 끝냄
E.App._sched_apply(s4, None)
ck("31 지금 잡이 오늘 끝난 뒤 하트비트 = 새 시각으로 옮김 + 로그, 미룸 기록 지움",
   s4._auto_accts[("GC", 0)][0]["hour"] == _new_x and s4._auto_accts[("GC", 0)][0]["acct"] == "A1"
   and any("미뤄 둔 청산 시각을 새 시각" in m for m in s4.logs) and E.App._sched_exits_now(s4, "GC") == [("UTC", _new_x)], s4.logs)
E.App._sched_apply(Stub(), DEF)
s5 = Stub()
s5._auto_accts = {("GC", 0): [{"asset": "GC", "tz": "America/New_York", "hour": 6, "acct": "A1", "broker": "projectx"}]}
s5._entered_at = {"GC": _time.time() - 30 * 3600}           # 30시간 전 진입 = 열린 포지션 없음
E.App._sched_apply(s5, NEWS)
ck("32 열린 포지션이 없으면(마지막 진입 26시간 넘음) 바로 새 시각", s5._auto_accts[("GC", 0)][0]["hour"] == _new_x, s5._auto_accts)
E.App._sched_apply(Stub(), DEF)
s6 = Stub()
s6._auto_accts = {("GC", 0): [{"asset": "GC", "tz": "America/New_York", "hour": 6, "acct": "A1", "broker": "projectx"}]}
s6._entered_at = {"GC": _time.time() - 600}
_fut = (_hn + 1) % 24
if _fut > _hn:                                             # 자정 직전이면 '오늘 아직'인 시각이 없다 - 건너뜀
    E.App._sched_apply(s6, {"GC": {"entry": ["UTC", (_fut - 4) % 24], "exit": ["UTC", _fut]}})
    ck("33 열린 포지션이 있어도 새 청산 시각이 오늘 아직이면 바로 옮김", s6._auto_accts[("GC", 0)][0]["hour"] == _fut, s6._auto_accts)
E.App._sched_apply(Stub(), DEF)
# ── 재검토 P2(2026-11-10): 늦어지는 청산 시각도 열린 포지션이면 지금 시각(이른 쪽)에, 미룸은 디스크에 남아 재시작해도 그 전 시각으로 무장 ──
if _hn + 3 <= 23:
    _o2, _n2 = _hn + 1, _hn + 3
    s7 = Stub()
    s7._auto_accts = {("GC", 0): [{"asset": "GC", "tz": "UTC", "hour": _o2, "acct": "A1", "broker": "projectx"}]}
    s7._entered_at = {"GC": _time.time() - 1800}
    E.App._sched_apply(s7, {"GC": {"entry": ["UTC", _n2 - 4], "exit": ["UTC", _n2]}})
    ck("35 새 청산 시각이 늦어져도(지금 +1시 → +3시) 열린 포지션 가능이면 지금 시각(이른 쪽) 그대로 + 로그",
       s7._auto_accts[("GC", 0)][0]["hour"] == _o2 and any("이른 쪽인 지금 청산 시각" in m for m in s7.logs), s7.logs)
    ck("36 미룸은 디스크(.sched_pend.json)에 남는다", os.path.exists(E._SCHED_PEND_PATH)
       and json.load(open(E._SCHED_PEND_PATH))["GC"]["exits"] == [["UTC", _o2]], open(E._SCHED_PEND_PATH).read())
    s8 = Stub()                                            # 재시작 = 메모리 새로
    ck("37 재시작 뒤 무장(_sched_exits_now)은 그 전 시각(+1시)", E.App._sched_exits_now(s8, "GC") == [("UTC", _o2)],
       E.App._sched_exits_now(s8, "GC"))
    E.App._sched_apply(s8, None)
    ck("38 재시작 뒤 무장 전 하트비트(잡 없음)는 미룸 기록을 지우지 않는다", E.App._sched_exits_now(Stub(), "GC") == [("UTC", _o2)])
    s8._auto_accts = {("GC", 0): [{"asset": "GC", "tz": "UTC", "hour": _o2, "acct": "A1", "broker": "projectx"}]}
    s8._entered_at = {"GC": _time.time() - 1800}
    s8._auto_fired = {("GC", _o2, "A1"): _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")}
    E.App._sched_apply(s8, None)
    ck("39 지금 잡이 끝난 뒤 = 새 시각으로 + 디스크 기록 지움", s8._auto_accts[("GC", 0)][0]["hour"] == _n2
       and not os.path.exists(E._SCHED_PEND_PATH), s8._auto_accts)
    E.App._sched_apply(Stub(), DEF)
with open(E._SCHED_PEND_PATH, "w") as _f:
    json.dump({"GC": {"exits": [["UTC", 3]], "ts": _time.time() - 27 * 3600}}, _f)
ck("40 26시간 넘은 미룸 기록은 버린다", E.App._sched_exits_now(Stub(), "GC") == E._ASSET_EXITS["GC"])
os.remove(E._SCHED_PEND_PATH)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("34 무장(_master_arm)도 미뤄 둔 자산은 그 전 시각으로", "for tzname, hour in App._sched_exits_now(self, asset)]" in src)
ck("14 하트비트에서 sched를 읽고, 받은 하트비트에서만 반영", '"sched": ap.get("sched")' in src and 'self._sched_apply(gate.get("sched"))' in src)
print("\n" + ("FAILED: " + "; ".join(FAIL) if FAIL else "전부 통과"))
sys.exit(1 if FAIL else 0)
