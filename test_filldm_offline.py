#!/usr/bin/env python3
# 체결 DM용 계좌별 내역(2026-10-15, 대표 "앱에서 얼만큼 체결되었는지 오토파일럿은 dm 보내자. 체결순간.") 오프라인 시험. 0 = 통과.
#   1 _fill_accts: 계좌 끝 4자리(영숫자)만, 계좌 원문 없음, 정수는 정수로, 계좌 12개와 leg 4개 상한, 이상한 값은 건너뜀
#   2 _note_fill: 계좌마다 [심볼, 주문, 체결]을 모음(합계, 계좌 수, leg 수는 종전 그대로)
#   3 _send_fill: /eqfill 본문에 accts(가격 없음), 계좌 12 x leg 4도 4096바이트 안
#   4 _post_fill: 옛 서버(본문 상한 1024)가 size로 거절하면 accts를 빼고 한 번 더 보냄, accts 없는 본문은 종전 그대로
#   5 배선: 선물 확정 경로는 실제 체결(_seen_qty, 주문 수량까지만, 적게 보이면 _settle_fill_qty), BTC는 주문 수량, 신호 id와 방향
#   6 신호별 누적(검증 소견 1): 보고 뒤 늦게 체결된 계좌는 같은 신호 후속 보고(누적)로, 다음 신호 보고에는 얹히지 않음, 앞 보고 재시도 멈춤,
#     미전송 큐는 같은 신호, 같은 기기의 마지막 본문 하나, 본문에 sid, d(주문 방향), lg(앱 화면 언어)
#   7 _settle_fill_qty(검증 소견 4): 주문보다 적게 보였을 때만 3초 안에서 다시 읽어 가장 큰 값(주문 수량까지)
#   8 로그 문구 KO/EN 짝
#   9 후속 보고와 청산 보고, 다음 신호(2차 검증 소견 8): 첫 보고 뒤 청산 보고가 나갔으면 후속 보고는 늦은 계좌 몫만, 후속 보고를 기다리는 신호의
#     늦은 몫은 다음 신호 보고가 버리지 않음, 후속 보고는 진입 중인 다음 신호 몫을 버리지 않음
# HOME, APPDATA = 임시 폴더, 네트워크 차단, 요청은 가짜, EQ_NO_ALERT=1.
import json
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

HOME = tempfile.mkdtemp(prefix="eq_filldm_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.create_connection = _blk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eqgui as E  # noqa: E402

assert E.APP_DIR.startswith(HOME), E.APP_DIR
FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:500]}"))
    if not c:
        FAIL.append(n)


# ── 1 _fill_accts ──────────────────────────────────────────────────────────────
by = {"projectx:S1JUL2512341234": [["NQ", 2.0, 2.0], ["MNQ", 3.0, 3.0]],
      "nt8:LTT-50K-9999-5678": [["GC", 7.0, 5.0], ["MGC", 9.0, 9.0]],
      "bybit:88001234": [["BTC", 0.35, 0.35]]}
ac = E._fill_accts(by)
ck("1a 계좌 끝 4자리와 브로커 키, leg 그대로(정수는 정수)",
   ac == [{"b": "projectx", "k": "1234", "l": [["NQ", 2, 2], ["MNQ", 3, 3]]},
          {"b": "nt8", "k": "5678", "l": [["GC", 7, 5], ["MGC", 9, 9]]},
          {"b": "bybit", "k": "1234", "l": [["BTC", 0.35, 0.35]]}], ac)
_txt = json.dumps(ac)
ck("1b 계좌 원문 없음", all(x not in _txt for x in ("S1JUL25", "LTT", "9999", "88001234")), _txt)
big = {f"projectx:XFA-{i:08d}": [["NQ", 1, 1]] * 6 for i in range(20)}
ab = E._fill_accts(big)
ck("1c 계좌 12개, 계좌마다 leg 4개까지", len(ab) == 12 and all(len(a["l"]) == 4 for a in ab), (len(ab), [len(a["l"]) for a in ab]))
ck("1d 이상한 값은 건너뜀(예외 없음)", E._fill_accts({"x:1": [["NQ", "abc", 1], ["MNQ", 1, 1]], "y:2": [None]}) ==
   [{"b": "x", "k": "1", "l": [["MNQ", 1, 1]]}] and E._fill_accts(None) == [] and E._fill_accts("bad") == [])

# ── 2 _note_fill ───────────────────────────────────────────────────────────────
class Stub:
    _profile = {}
    root = SimpleNamespace(after=lambda *a, **k: None)
    lang = "ko"
    LOG = []
    _fill_merge = staticmethod(E.App._fill_merge)
    _fill_take = E.App._fill_take
    _fill_superseded = E.App._fill_superseded
    _fill_followup = E.App._fill_followup
    _send_fill = E.App._send_fill
    _fill_pend_set = E.App._fill_pend_set
    _fill_closed_since = E.App._fill_closed_since
    _queue_unsent_fill = E.App._queue_unsent_fill
    _load_unsent = E.App._load_unsent
    _UNSENT_PATH = os.path.join(HOME, "unsent_fills.json")
    _FILL_FOLLOWUP_S = 5

    def _save_cfg(self, **k):
        pass

    def _refresh_live_panel(self):
        pass

    def log(self, m):
        self.LOG.append(str(m))


st = Stub()
E.App._note_fill(st, "NQ", micro=20.0, acct="projectx:S1JUL2512341234", leg=("NQ", 2, 2))
E.App._note_fill(st, "NQ", micro=3.0, acct="projectx:S1JUL2512341234", leg=("MNQ", 3, 3))
E.App._note_fill(st, "NQ", micro=15.0, acct="nt8:LTT-50K-99995678", leg=("MNQ", 15, 15))
_d = st._fill_acc[("NQ", "")]
ck("2a 합계, 계좌 수, leg 수는 종전 그대로", _d["micro"] == 38.0 and _d["n"] == 2 and _d["legs"] == 3, _d)
ck("2b 계좌별 leg 내역", _d["by"] == {"projectx:S1JUL2512341234": [["NQ", 2.0, 2.0], ["MNQ", 3.0, 3.0]],
                                     "nt8:LTT-50K-99995678": [["MNQ", 15.0, 15.0]]}, _d.get("by"))
E.App._note_fill(st, "GC", micro=10.0, acct="projectx:A1")
ck("2c leg 없이 부른 옛 호출은 내역 없이 종전 그대로", "by" not in st._fill_acc[("GC", "")] and st._fill_acc[("GC", "")]["n"] == 1)

# ── 3, 4 _send_fill과 _post_fill ───────────────────────────────────────────────
import requests  # noqa: E402
import autopilot_crypto  # noqa: E402

POSTS = []
MODE = {"old": False}


class _R:
    ok = True

    def __init__(self, text):
        self.text = text


def _post(url, timeout=None, json=None):
    POSTS.append(json)
    if MODE["old"] and len(__import__("json").dumps(json)) > 1024:
        return _R("fl:err:size")
    return _R("fl:ok")


requests.post = _post
autopilot_crypto.path_id = lambda tok: "TID1"


class FStub(Stub):
    _gate = {"ok": True, "caps": {"autoentry": True}}
    _token = "tok"
    _FILL_RETRY_WINDOW_S = 5


fs = FStub()
fs._fill_lock = st._fill_lock
fs._fill_acc = st._fill_acc
E.App._send_fill(fs, "NQ")
for _ in range(50):
    if POSTS:
        break
    time.sleep(0.05)
b0 = POSTS[0] if POSTS else {}
ck("3a /eqfill 본문에 accts(계좌 끝 4자리, 심볼, 주문, 체결)",
   b0.get("accts") == [{"b": "projectx", "k": "1234", "l": [["NQ", 2, 2], ["MNQ", 3, 3]]},
                       {"b": "nt8", "k": "5678", "l": [["MNQ", 15, 15]]}] and b0.get("qty_micro") == 38.0
   and b0.get("accounts") == 2 and b0.get("legs") == 3, b0)
ck("3b 가격 칸 없음", not any(k in json.dumps(b0).lower() for k in ("price", "px", "entry_ref", "stop")), b0)
_big = {"tok_id": "TID1", "inst": "NQ", "mid": "a" * 16, "accounts": 12, "legs": 48, "qty_micro": 9999.0, "armed": True,
        "accts": E._fill_accts({f"projectx:XFA-{i:08d}": [["MNQ", 9999, 9999]] * 4 for i in range(12)})}
ck("3c 계좌 12 x leg 4 본문도 4096바이트 안(서버 상한)", len(json.dumps(_big)) <= 4096, len(json.dumps(_big)))
POSTS.clear()
MODE["old"] = True
r = E._post_fill(requests, _big)
ck("4a 옛 서버가 size로 거절하면 accts 빼고 한 번 더(체결 보고 자체는 간다)", len(POSTS) == 2 and "accts" in POSTS[0]
   and "accts" not in POSTS[1] and POSTS[1]["qty_micro"] == 9999.0 and r.text == "fl:ok", [list(p) for p in POSTS])
POSTS.clear()
_small = {"tok_id": "TID1", "inst": "GC", "qty_micro": 0, "qty_btc": 0}
E._post_fill(requests, _small)
ck("4b accts 없는 본문(청산 등)은 한 번, 종전 그대로", POSTS == [_small], POSTS)
MODE["old"] = False

# ── 5 배선 ─────────────────────────────────────────────────────────────────────
src = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
ck("5a 선물 확정 경로: 주문 수량 대 실제 체결(주문 수량까지만, 적게 보이면 다시 읽음), 신호 id와 주문 방향",
   "_fill_n = min(int(_seen_qty or _qty), int(_qty))" in src and "_fill_n = self._settle_fill_qty(b, _aid, _con, _tag, int(_qty), _fill_n, _is_nt8)" in src
   and "leg=(_sym, int(_qty), _fill_n),\n                            sid=(sig or {}).get(\"id\"), side=direction)" in src)
ck("5b BTC 경로: 주문 수량, 신호 id와 주문 방향", 'leg=("BTC", float(size or 0), float(size or 0)),' in src
   and src.count('sid=(sig or {}).get("id"), side=direction)') == 2)
ck("5d 신호 루프: 이 신호 몫만 보고, 45초 안에 안 끝난 계좌는 후속 보고", "self._send_fill(_asset, sid=sid)" in src
   and "_late = [x for x in _ths if x.is_alive()]" in src and "self._fill_followup(_asset, sid, _late)" in src
   and src.count("self._send_fill(_asset") == 1)
_pf = src[src.index("def _post_fill"):]
_pf = _pf[:_pf.index("\ndef ", 1)]
ck("5c 재시도와 미전송 큐 둘 다 _post_fill, /eqfill 직접 호출은 _post_fill 안의 둘뿐", src.count("r = _post_fill(requests,") == 2
   and src.count('requests.post(PUSH_BASE + "eqfill"') == 2 == _pf.count('requests.post(PUSH_BASE + "eqfill"'),
   src.count('requests.post(PUSH_BASE + "eqfill"'))


# ── 6 신호별 누적, 후속 보고, 다음 신호에 안 얹힘(검증 소견 1) ─────────────────────────
POSTS.clear()
s6 = FStub()
s6.LOG = []
SA, SB = "2026-10-14-GC-1", "2026-10-15-GC-1"
E.App._note_fill(s6, "GC", micro=79.0, acct="nt8:LTT-50K-1111", leg=("GC", 7, 7), sid=SA, side="LONG")
E.App._note_fill(s6, "GC", micro=9.0, acct="nt8:LTT-50K-1111", leg=("MGC", 9, 9), sid=SA, side="LONG")


def _wait_posts(n, t=3.0):
    _e = time.time() + t
    while len(POSTS) < n and time.time() < _e:
        time.sleep(0.02)


E.App._send_fill(s6, "GC", sid=SA)
_wait_posts(1)
p1 = POSTS[0] if POSTS else {}
ck("6a 첫 보고: 그 신호 몫, 본문에 sid, d(주문 방향), lg(앱 화면 언어)", p1.get("sid") == SA and p1.get("d") == "LONG" and p1.get("lg") == "ko"
   and p1.get("qty_micro") == 88.0 and p1.get("accts") == [{"b": "nt8", "k": "1111", "l": [["GC", 7, 7], ["MGC", 9, 9]]}], p1)
# 늦게 끝난 계좌(…2222)가 보고 뒤에 체결 - 예전엔 다음 신호 보고에 얹혔다
E.App._note_fill(s6, "GC", micro=9.0, acct="nt8:LTT-50K-2222", leg=("MGC", 9, 9), sid=SA, side="LONG")
E.App._send_fill(s6, "GC", sid=SA)                 # 후속 보고(_fill_followup이 부르는 것과 같음)
_wait_posts(2)
p2 = POSTS[1] if len(POSTS) > 1 else {}
ck("6b 후속 보고 = 같은 신호의 누적(수량 97, 계좌 2, 두 계좌 내역) - 서버 대시보드 칸을 전체로 덮고, 체결 DM은 새 계좌만",
   p2.get("sid") == SA and p2.get("qty_micro") == 97.0 and p2.get("accounts") == 2 and p2.get("legs") == 3
   and p2.get("accts") == [{"b": "nt8", "k": "1111", "l": [["GC", 7, 7], ["MGC", 9, 9]]}, {"b": "nt8", "k": "2222", "l": [["MGC", 9, 9]]}], p2)
# 다음 신호: 그 사이 앞 신호 몫이 또 늦게 남아 있어도 이번 보고에 안 얹힘
E.App._note_fill(s6, "GC", micro=9.0, acct="nt8:LTT-50K-3333", leg=("MGC", 9, 9), sid=SA, side="LONG")
E.App._note_fill(s6, "GC", micro=79.0, acct="nt8:LTT-50K-1111", leg=("GC", 7, 7), sid=SB, side="SHORT")
E.App._send_fill(s6, "GC", sid=SB)
_wait_posts(3)
p3 = POSTS[2] if len(POSTS) > 2 else {}
ck("6c 다음 신호 보고: 앞 신호의 늦은 몫(…3333) 없음, 수량 79(88로 부풀지 않음), 같은 계좌 줄 한 번",
   p3.get("sid") == SB and p3.get("qty_micro") == 79.0 and p3.get("d") == "SHORT"
   and p3.get("accts") == [{"b": "nt8", "k": "1111", "l": [["GC", 7, 7]]}], p3)
ck("6d 버린 앞 신호 몫은 로그에 남김", any(SA in m for m in s6.LOG), s6.LOG)
ck("6e 누적 칸에 남은 것 없음, 보낸 기록은 이번 신호 것만", s6._fill_acc == {} and list(s6._fill_sent) == [("GC", SB)], (s6._fill_acc, s6._fill_sent))
_c, _g1 = E.App._fill_take(s6, "GC", SB)
ck("6f 꺼낼 것이 없으면 (None, None) - 후속 보고 예산 안에 늦은 계좌가 체결되지 않았으면 아무것도 안 보냄", _c is None and _g1 is None)
# 세대: 앞 보고 재시도는 더 새로운 누적 보고가 생기면 멈춤
s7 = FStub()
E.App._note_fill(s7, "NQ", micro=20.0, acct="px:A1111", leg=("NQ", 2, 2), sid="S7")
_c1, g1 = E.App._fill_take(s7, "NQ", "S7")
E.App._note_fill(s7, "NQ", micro=3.0, acct="px:A2222", leg=("MNQ", 3, 3), sid="S7")
_c2, g2 = E.App._fill_take(s7, "NQ", "S7")
ck("6g 앞 보고 세대는 낡음(재시도, 미전송 큐 멈춤), 새 보고는 아님", E.App._fill_superseded(s7, g1) and not E.App._fill_superseded(s7, g2)
   and not E.App._fill_superseded(s7, None) and _c2["micro"] == 23.0 and _c2["n"] == 2, (g1, g2, _c2))
# 실제 재시도 경로: 서버가 첫 보고를 계속 거절하는 동안 후속 보고가 나가면 첫 보고 스레드는 멈추고 미전송 큐에도 안 남음
POSTS.clear()
_orig_post = requests.post


def _post_fail_first(url, timeout=None, json=None):
    POSTS.append(json)
    if json.get("accounts") == 1 and json.get("sid") == "S8":
        return _R("fl:err:busy")
    return _R("fl:ok")


requests.post = _post_fail_first
s8 = FStub()
s8._FILL_RETRY_WINDOW_S = 40
import time as _tm  # noqa: E402
_real_sleep = _tm.sleep
_tm.sleep = lambda x: _real_sleep(min(x, 0.05))     # 재시도 간격 30초를 짧게
try:
    E.App._note_fill(s8, "NQ", micro=20.0, acct="px:A1111", leg=("NQ", 2, 2), sid="S8")
    E.App._send_fill(s8, "NQ", sid="S8")
    _wait_posts(2)
    E.App._note_fill(s8, "NQ", micro=3.0, acct="px:A2222", leg=("MNQ", 3, 3), sid="S8")
    E.App._send_fill(s8, "NQ", sid="S8")
    _real_sleep(0.6)
    _n_first = sum(1 for p in POSTS if p.get("accounts") == 1)
    _real_sleep(0.3)
    ck("6h 후속 보고가 나가면 앞 보고 재시도는 멈춤(늦게 도착해 수량을 되돌리지 않음), 미전송 큐에도 안 남김",
       sum(1 for p in POSTS if p.get("accounts") == 1) == _n_first and any(p.get("accounts") == 2 for p in POSTS)
       and not s8._load_unsent(), (len(POSTS), _n_first, s8._load_unsent()))
finally:
    _tm.sleep = _real_sleep
    requests.post = _orig_post
# 미전송 큐: 같은 신호, 같은 기기의 진입 본문은 마지막 하나
s9 = FStub()
try:
    os.remove(s9._UNSENT_PATH)
except FileNotFoundError:
    pass
E.App._queue_unsent_fill(s9, {"tok_id": "T", "inst": "GC", "mid": "m1", "sid": "S9", "qty_micro": 79.0, "accounts": 1})
E.App._queue_unsent_fill(s9, {"tok_id": "T", "inst": "GC", "mid": "m1", "qty_micro": 0, "qty_btc": 0})          # 청산은 그대로
E.App._queue_unsent_fill(s9, {"tok_id": "T", "inst": "GC", "mid": "m1", "sid": "S9", "qty_micro": 88.0, "accounts": 2})
_q = [it["body"] for it in s9._load_unsent()]
ck("6i 미전송 큐: 같은 신호, 같은 기기의 진입 본문은 마지막(누적) 하나, 청산 본문은 그대로",
   [(b.get("sid"), b.get("qty_micro")) for b in _q] == [(None, 0), ("S9", 88.0)], _q)
# _fill_followup: 늦은 스레드가 끝난 뒤 같은 신호로 후속 보고
POSTS.clear()
s10 = FStub()
E.App._note_fill(s10, "GC", micro=79.0, acct="nt8:L1111", leg=("GC", 7, 7), sid="S10", side="LONG")
E.App._send_fill(s10, "GC", sid="S10")
_wait_posts(1)
import threading as _thr  # noqa: E402
_late_t = _thr.Thread(target=lambda: (time.sleep(0.3), E.App._note_fill(s10, "GC", micro=9.0, acct="nt8:L2222", leg=("MGC", 9, 9),
                                                                         sid="S10", side="LONG")), daemon=True)
_late_t.start()
E.App._fill_followup(s10, "GC", "S10", [_late_t], budget=3)
_wait_posts(2)
ck("6j _fill_followup: 늦은 계좌 스레드가 끝나면 같은 신호로 후속 보고(누적)", len(POSTS) == 2 and POSTS[1].get("sid") == "S10"
   and [a["k"] for a in POSTS[1].get("accts") or []] == ["1111", "2222"] and POSTS[1].get("qty_micro") == 88.0, POSTS)
POSTS.clear()
s11 = FStub()
E.App._note_fill(s11, "GC", micro=79.0, acct="nt8:L1111", leg=("GC", 7, 7), sid="S11")
E.App._send_fill(s11, "GC", sid="S11")
_wait_posts(1)
_fail_t = _thr.Thread(target=lambda: time.sleep(0.2), daemon=True)   # 늦은 계좌가 체결 없이 끝남
_fail_t.start()
E.App._fill_followup(s11, "GC", "S11", [_fail_t], budget=3)
time.sleep(0.8)
ck("6k 늦은 계좌가 체결 없이 끝나면 후속 보고 없음", len(POSTS) == 1, POSTS)
s12 = FStub()
s12.lang = "en"
E.App._note_fill(s12, "NQ", micro=20.0, acct="px:A1111", leg=("NQ", 2, 2), sid="S12", side="SHORT")
E.App._note_fill(s12, "NQ", micro=20.0, acct="px:A2222", leg=("NQ", 2, 2), sid="S12", side="LONG")
POSTS.clear()
E.App._send_fill(s12, "NQ", sid="S12")
_wait_posts(1)
ck("6l 앱 화면 언어 en이면 lg=en, 계좌마다 방향이 다르면 d=MIXED(서버가 보내지 않음)", POSTS and POSTS[0].get("lg") == "en"
   and POSTS[0].get("d") == "MIXED", POSTS)

# ── 7 _settle_fill_qty(검증 소견 4) ───────────────────────────────────────────
class FB:
    def __init__(self, seq, filled=None):
        self.seq, self.filled, self.n = list(seq), filled, 0

    def position_qty(self, aid, con):
        self.n += 1
        return self.seq.pop(0) if self.seq else (self.seq_last if hasattr(self, "seq_last") else 0)

    def order_status(self, aid, tag):
        return {"entry": {"state": "PartFilled", "filled": self.filled}} if self.filled is not None else {}


_nos = lambda x: None   # noqa: E731
fb = FB([5, 7])
ck("7a 3/7로 잡혔다가 곧 7이 되면 7(일부로 단정하지 않음)", E.App._settle_fill_qty(fb, "a", "c", "t", 7, 3, False, wait_s=3, sleep=_nos) == 7)
fb = FB([])
fb.seq_last = 3
ck("7b 3초 동안 3에 머물면 3(진짜 일부)", E.App._settle_fill_qty(fb, "a", "c", "t", 7, 3, False, wait_s=0.2, sleep=lambda x: time.sleep(0.01)) == 3)
fb = FB([9])
ck("7c 포지션이 주문보다 커도(같은 계약 기존 포지션) 주문 수량까지만", E.App._settle_fill_qty(fb, "a", "c", "t", 7, 3, False, wait_s=3, sleep=_nos) == 7)
fb = FB([])
ck("7d 처음부터 전부면 다시 읽지 않음", E.App._settle_fill_qty(fb, "a", "c", "t", 7, 7, True, wait_s=3, sleep=_nos) == 7 and fb.n == 0)
fb = FB([], filled=6)
fb.seq_last = 4
ck("7e NT8은 주문 상태의 filled도 봄(가장 큰 값)", E.App._settle_fill_qty(fb, "a", "c", "t", 7, 4, True, wait_s=0.2, sleep=lambda x: time.sleep(0.01)) == 6)

# ── 8 로그 문구 KO/EN 짝(합친 뒤 정리: 가운뎃점 bullet 대신, 영문 화면에는 영문) ─────────────────────────
for _lg, _want_drop, _want_late in (("ko", "앞 신호(S8A)의 늦은 체결 기록은 이번 신호 보고에 넣지 않습니다.",
                                     "진입이 0초 넘게 끝나지 않은 계좌가 있어 지금까지 체결된 것만 보고합니다."),
                                    ("en", "late fills from the earlier signal (S8A) are not included in this signal's report.",
                                     "an account's entry has not finished after 0 seconds, so only the fills so far are reported.")):
    s8 = FStub()
    s8.lang = _lg
    s8.LOG = []
    E.App._note_fill(s8, "GC", micro=9.0, acct="nt8:L8888", leg=("MGC", 9, 9), sid="S8A", side="LONG")
    E.App._note_fill(s8, "GC", micro=7.0, acct="nt8:L9999", leg=("MGC", 7, 7), sid="S8B", side="LONG")
    E.App._fill_take(s8, "GC", "S8B")
    _slow = _thr.Thread(target=lambda: time.sleep(1.5), daemon=True)
    _slow.start()
    E.App._fill_followup(s8, "GC", "S8B", [_slow], budget=0.3)
    time.sleep(0.9)
    ck(f"8a 로그 문구({_lg}): 앞 신호 늦은 체결 버림, 후속 보고 시간 넘음 - 가운뎃점 없음",
       any(_want_drop in m for m in s8.LOG) and any(_want_late in m for m in s8.LOG)
       and not any("\u00b7" in m for m in s8.LOG), s8.LOG)

# ── 9 후속 보고와 청산 보고, 다음 신호(2차 검증 소견 8) ─────────────────────────────
for _lg, _want in (("ko", "GC 첫 체결 보고 뒤 청산 보고가 나가, 늦게 체결된 계좌만 따로 보고합니다."),
                   ("en", "GC: a close was reported after the first fill report, so only the accounts that filled late are reported.")):
    POSTS.clear()
    s13 = FStub()
    s13.lang = _lg
    s13.LOG = []
    E.App._note_fill(s13, "GC", micro=79.0, acct="nt8:L1111", leg=("GC", 7, 7), sid="S13", side="LONG")
    E.App._send_fill(s13, "GC", sid="S13")
    _wait_posts(1)
    E.App._send_fill(s13, "GC", closed=True, why="cap_tp")        # 첫 보고 뒤 그 자산 청산 보고(손절, 익절, 시간 청산)
    _wait_posts(2)
    _lt13 = _thr.Thread(target=lambda: (time.sleep(0.2), E.App._note_fill(s13, "GC", micro=9.0, acct="nt8:L2222", leg=("MGC", 9, 9),
                                                                           sid="S13", side="LONG")), daemon=True)
    _lt13.start()
    E.App._fill_followup(s13, "GC", "S13", [_lt13], budget=3)
    _wait_posts(3)
    _p3 = POSTS[2] if len(POSTS) > 2 else {}
    ck(f"9a({_lg}) 첫 보고 뒤 청산 보고가 나갔으면 후속 보고는 늦은 계좌 몫만(수량 9, 계좌 1, …2222) - 닫힌 칸을 부풀린 수량으로 다시 열지 않음, 로그 한 줄",
       _p3.get("sid") == "S13" and _p3.get("qty_micro") == 9.0 and _p3.get("accounts") == 1
       and [a["k"] for a in _p3.get("accts") or []] == ["2222"] and any(_want in m for m in s13.LOG)
       and not any("\u00b7" in m for m in s13.LOG), (POSTS, s13.LOG))
POSTS.clear()
s14 = FStub()
s14.LOG = []
E.App._note_fill(s14, "GC", micro=79.0, acct="nt8:L1111", leg=("GC", 7, 7), sid="S14A", side="LONG")
E.App._send_fill(s14, "GC", sid="S14A")
_wait_posts(1)
_lt14 = _thr.Thread(target=lambda: time.sleep(0.8), daemon=True)      # 앞 신호의 늦은 계좌 스레드(아직 안 끝남)
_lt14.start()
E.App._note_fill(s14, "GC", micro=9.0, acct="nt8:L2222", leg=("MGC", 9, 9), sid="S14A", side="LONG")   # 그 계좌는 이미 체결
E.App._fill_followup(s14, "GC", "S14A", [_lt14], budget=3)
E.App._note_fill(s14, "GC", micro=70.0, acct="nt8:L3333", leg=("GC", 7, 7), sid="S14B", side="SHORT")
E.App._send_fill(s14, "GC", sid="S14B")                               # 다음 신호 보고(앞 신호 후속 보고 대기 중)
_wait_posts(2)
_wait_posts(3, t=4.0)
_pb = next((p for p in POSTS if p.get("sid") == "S14B"), {})
_pa = [p for p in POSTS if p.get("sid") == "S14A"]
ck("9b 다음 신호 보고는 후속 보고를 기다리는 앞 신호의 늦은 몫을 버리지 않고(로그 없음), 앞 신호 후속 보고가 그 몫을 누적으로 보냄(88, …1111, …2222)",
   _pb.get("qty_micro") == 70.0 and [a["k"] for a in _pb.get("accts") or []] == ["3333"] and len(_pa) == 2
   and _pa[1].get("qty_micro") == 88.0 and [a["k"] for a in _pa[1].get("accts") or []] == ["1111", "2222"]
   and not any("S14A" in m for m in s14.LOG), (POSTS, s14.LOG))
POSTS.clear()
s15 = FStub()
s15.LOG = []
E.App._note_fill(s15, "NQ", micro=20.0, acct="px:A1111", leg=("NQ", 2, 2), sid="S15A", side="LONG")
E.App._send_fill(s15, "NQ", sid="S15A")
_wait_posts(1)
_lt15 = _thr.Thread(target=lambda: time.sleep(0.3), daemon=True)      # 앞 신호의 늦은 계좌가 체결 없이 끝남
_lt15.start()
E.App._fill_followup(s15, "NQ", "S15A", [_lt15], budget=3)
E.App._note_fill(s15, "NQ", micro=30.0, acct="px:A3333", leg=("MNQ", 30, 30), sid="S15B", side="LONG")   # 다음 신호 진입 중
time.sleep(0.8)                                                       # 앞 신호 후속 보고가 돈 뒤
E.App._send_fill(s15, "NQ", sid="S15B")
_wait_posts(2)
_p15 = next((p for p in POSTS if p.get("sid") == "S15B"), {})
ck("9c 앞 신호 후속 보고는 진입 중인 다음 신호 몫을 버리지 않음(다음 신호 보고에 30, …3333), 앞 신호 후속 보고 자체는 보낼 것 없음",
   _p15.get("qty_micro") == 30.0 and [a["k"] for a in _p15.get("accts") or []] == ["3333"]
   and sum(1 for p in POSTS if p.get("sid") == "S15A") == 1 and not s15._fill_pend_set(), (POSTS, s15.LOG))

print(("FAIL %d: %s" % (len(FAIL), FAIL)) if FAIL else "OK - 0 fail(s)")
sys.exit(1 if FAIL else 0)
