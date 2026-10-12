#!/usr/bin/env python3
# EQ Autopilot - 공유 f2(Topstep API Key 등) 삭제 가드 오프라인 시험(대표 2026-10-07 "톱스텝 api key는 지워져버려"). 0=통과
# f2는 키체인에 f1 값(아이디)을 계정 키로 저장 → NQ·GC가 같은 아이디면 한 항목을 같이 쓴다.
# ①GC 칸 비움 → 자동 저장(transient) → NQ f2 유지 ②같은 상태 명시적 저장(탭 전환 등) → 다른 칸이 쓰므로 유지
# ③아무도 안 쓰는 아이디를 명시적 저장으로 비워도 지우지 않는다(2026-10-07 브로커 연결 공용: 빈 칸 저장 = 삭제 경로 폐지)
# ④같은 상태 자동 저장 → 안 지움 ⑤[연결 지우기] → 아무도 안 쓰는 아이디면 f2·키체인 삭제, 칸 비움(별칭 유지)
# ⑥[연결 지우기] 무장 중 거절 ⑦복사·가져오기 버튼 없음(대표 2026-10-07)
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
eqgui.messagebox.showinfo = lambda *a, **k: None      # 빈 칸 저장 안내 창(세션 1회) - 시험은 막히지 않게

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}"))
    if not c:
        FAIL.append(n)


class E:
    def __init__(self, v=""):
        self.v = v

    def get(self):
        return self.v


ID, KEY = "member_topstep", "TOPSTEP-API-KEY-123"
KC = {}


def mk(gc_f1_screen="", gc_prev=ID, nq_f1=ID, cur="GC"):
    me = MagicMock()
    me.lang = "ko"
    me._asset = cur
    me._broker_name = "projectx"
    me._live_include = {}
    me._armed_here = lambda a: False
    me._conn_by_broker = {}
    me._acfg = {
        "NQ": {"broker": "projectx", "creds": {"projectx": {"f1": nq_f1, "f3": ""}}, "accounts": []},
        "GC": {"broker": "projectx", "creds": {"projectx": {"f1": gc_prev, "f3": ""}}, "accounts": []},
        "BTC": {"broker": "bybit", "creds": {}, "accounts": []},
    }
    me._sig_accts, me._auto_accts = {}, {}
    me._creds_split = set()
    me._key_shown = ""
    for fn in ("_broker_of", "_creds_of", "_f1_shared", "_save_current_asset", "_save_creds_fields", "_sync_tab_broker",
               "_known_secrets", "_armed_on_conn", "_conn_users", "_conn_row_counts", "_fmt_counts", "_clear_broker_conn"):
        setattr(me, fn, (lambda f: (lambda *a, **k: getattr(eqgui.App, f)(me, *a, **k)))(fn))
    me.user = E(gc_f1_screen)
    me.key = E("")                          # 키 칸이 비어 있는 최악(비동기 로드 전 등)
    return me


def kc():
    return patch.multiple(eqgui,
                          _kc_save=lambda a, s, *x, **k: (KC.__setitem__(a, s) or True) if s else False,
                          _kc_load=lambda a, *x, **k: KC.get(a, ""),
                          _kc_del=lambda a, *x, **k: KC.pop(a, None))


def reset():
    KC.clear()
    KC[ID] = KEY


with kc():
    reset(); me = mk(); me._save_current_asset(transient=True)
    ck("① GC 칸 비움 → 자동 저장 → NQ f2 유지", KC.get(ID) == KEY, KC)
    reset(); me = mk(); me._save_current_asset()
    ck("② 명시적 저장이어도 NQ가 같은 아이디 → 유지", KC.get(ID) == KEY, KC)
    reset(); me = mk(nq_f1=""); me._save_current_asset()
    ck("③ 아무도 안 쓰는 아이디 + 명시적 저장으로 비움 → 지우지 않음(삭제는 [연결 지우기])", KC.get(ID) == KEY, KC)
    ck("③ 칸을 비워도 저장된 아이디 그대로", me._acfg["GC"]["creds"]["projectx"]["f1"] == ID)
    reset(); me = mk(nq_f1=""); me._save_current_asset(transient=True)
    ck("④ 같은 상태 자동 저장 → 안 지움", KC.get(ID) == KEY, KC)
    # ⑤ [연결 지우기]: GC만 이 아이디를 쓰는 상태(split처럼 NQ는 비었음)
    reset(); KC["eq:GC:projectx:f1"] = "x"
    me = mk(nq_f1="", gc_f1_screen=ID)
    _gc = me._acfg["GC"]["creds"]["projectx"]
    with patch.object(eqgui.messagebox, "askyesno", return_value=True) as _ask:
        ok = me._clear_broker_conn()
    ck("⑤ [연결 지우기] → f2 삭제", ok is True and ID not in KC, KC)
    ck("⑤ [연결 지우기] → 칸 비움(같은 dict 제자리)", me._acfg["GC"]["creds"]["projectx"] is _gc and _gc["f1"] == "")
    ck("⑤ 확인창에 키 값 없음(켜진 계좌 0이면 짧은 문장)", _ask.called and "Topstep" in str(_ask.call_args)
       and KEY not in str(_ask.call_args) and ID not in str(_ask.call_args))
    # ⑤' 다른 자산이 같은 아이디를 쓰면(split) f2는 남긴다
    reset(); me = mk(gc_f1_screen=ID)
    me._creds_split = {"projectx"}
    with patch.object(eqgui.messagebox, "askyesno", return_value=True):
        me._clear_broker_conn()
    ck("⑤' 다른 자산이 같은 아이디 → f2 남김", KC.get(ID) == KEY, KC)
    # ⑥ 무장 중 거절
    reset(); me = mk(gc_f1_screen=ID)
    me._sig_accts = {("GC", 0): {"broker": "projectx", "f1": ID}}
    with patch.object(eqgui.messagebox, "askyesno", return_value=True), \
         patch.object(eqgui.messagebox, "showwarning") as _w:
        ok = me._clear_broker_conn()
    ck("⑥ 무장 중 [연결 지우기] 거절·키 그대로", ok is False and KC.get(ID) == KEY and _w.called
       and me._acfg["GC"]["creds"]["projectx"]["f1"] == ID)
    ck("⑦ 복사·가져오기 함수 없음", not hasattr(eqgui.App, "_pull_broker_keys") and not hasattr(eqgui.App, "_copy_nq_settings"))

print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}"))
sys.exit(1 if FAIL else 0)
