#!/usr/bin/env python3
# 트랙레코드 R 분모 - Topstep(ProjectX) 계좌 키 수리 오프라인 시험(홈피 리뷰 R55 P1, 2026-10-18). 0 = 통과.
# 수리 전: ProjectX 체결은 계좌를 숫자 id로 붙이는데 1R 맵과 1R 원장은 회원이 적은 값(보통 이름)이 키라 늘 못 찾아 기본 $600으로 나눴다.
# 수리 뒤: 계좌 목록(id, name)으로 체결 키를 설정 값으로 맞춘다. 이름으로 적어도, id로 적어도 같은 1R을 찾는다.
import os
import sys
from types import SimpleNamespace

os.environ["EQ_NO_ALERT"] = "1"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
src = open(os.path.join(HERE, "eqgui.py"), encoding="utf-8").read()
import socket, tempfile  # noqa: E401,E402
os.environ["HOME"] = os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eq_alias_")
os.environ["EQ_ERR_REPORT"] = "0"
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("network blocked by test"))
import eqgui as E  # noqa: E402

FAIL = []


def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:300]}"))
    if not c:
        FAIL.append(n)


class PX:
    def __init__(self, rows, boom=False):
        self.rows, self.boom, self.calls = rows, boom, []

    def _accounts(self, active_only=True, **k):
        self.calls.append(active_only)
        if self.boom:
            raise RuntimeError("503")
        return list(self.rows)


rows = [{"id": 12345, "name": "150KTC-V2-AB12"}, {"id": 777, "name": "EXPRESS-V2-CD34"}, {"id": 9, "name": "OTHER"}]
al = E._acct_alias(PX(rows), ["150KTC-V2-AB12", "777"])
ck("1a 이름으로 적은 계좌: 숫자 id와 이름 모두 → 적은 이름", al.get("12345") == "150KTC-V2-AB12" and al.get("150ktc-v2-ab12") == "150KTC-V2-AB12", al)
ck("1b id로 적은 계좌: 이름과 id 모두 → 적은 id", al.get("express-v2-cd34") == "777" and al.get("777") == "777", al)
ck("1c 설정에 없는 계좌는 바꾸지 않는다", "9" not in al and "other" not in al, al)
p = PX(rows)
E._acct_alias(p, ["150KTC-V2-AB12"])
ck("1d 닫힌 계좌까지 읽는다(active_only=False)", p.calls == [False], p.calls)
ck("1e 목록을 못 읽으면 {}(종전과 같음), 계좌 설정이 없으면 조회도 안 함",
   E._acct_alias(PX(rows, boom=True), ["150KTC-V2-AB12"]) == {} and E._acct_alias(PX(rows), []) == {}
   and E._acct_alias(SimpleNamespace(), ["x"]) == {})
# 규칙 재현: 1R $2,200 평가 계좌를 이름으로 설정, 체결은 숫자 id, 손절 -$3,498
credlist = [{"broker": "projectx", "f1": "u", "acct": "150KTC-V2-AB12", "one_r": 2200.0}]
_r_by_acct = {c["acct"]: c["one_r"] for c in credlist if c.get("acct")}
f = {"acct": "12345", "pnl": -3498.0}
_fa_old = f["acct"]
_fa_new = al.get(f["acct"].casefold(), f["acct"])
r_old = f["pnl"] / _r_by_acct.get(_fa_old, 600.0)
r_new = f["pnl"] / _r_by_acct.get(_fa_new, 600.0)
ck("2a 재현: 수리 전 -5.83R(기본 $600) → 수리 뒤 -1.59R(설정 $2,200)", round(r_old, 2) == -5.83 and round(r_new, 2) == -1.59, (r_old, r_new))
ck("3a 배선: closed_fills 뒤 _acct_alias로 계좌 키를 맞추고 그 값으로 1R 맵, 원장을 찾는다",
   "_alias = _acct_alias(b, [x[\"acct\"] for x in credlist" in src
   and "_fa = _alias.get(_fa.casefold(), _fa) if _fa else _fa" in src
   and src.index("_fa = _alias.get(") < src.index("f[\"_one_r\"] = _r_by_acct.get(f[\"_acct_id\"], _R_FALLBACK)"))
# ── 4. 설정에서 빠진 계좌(평가 → 펀디드 교체)의 지난 체결(2026-11-13 대표 "앱 고쳐. 지금") ──
# 회원 11/10 재전송: 평가 계좌(1R $2,200)를 펀디드로 바꾸자 평가 계좌가 설정에서 빠졌고, 그 계좌의 지난 6건이 기본 $600으로 다시
# 나뉘어 모두 정확히 3.67배가 됐다(10/23 NQ -1.387R → -5.087R, 서버는 맞던 값을 덮음). 1R 원장에는 그때 적은 계좌 키가 남아 있다.
ck("4a _rledger_accts: 원장 키의 계좌 값(순서, 중복 없음, 빈 값 뺌)",
   E._rledger_accts({"NQ|150KTC-V2-OLD1": [], "GC|150KTC-V2-OLD1": [], "BTC|bybit": [], "NQ|": [], "x": []})
   == ["150KTC-V2-OLD1", "bybit"], E._rledger_accts({"NQ|150KTC-V2-OLD1": [], "GC|150KTC-V2-OLD1": [], "BTC|bybit": []}))
rows2 = [{"id": 555, "name": "150KTC-V2-OLD1"}, {"id": 556, "name": "PA-150K-NEW2"}, {"id": 9, "name": "OTHER"}]
al2 = E._acct_alias(PX(rows2), ["PA-150K-NEW2"], ["150KTC-V2-OLD1", "bybit"])
ck("4b 설정에 없는 평가 계좌: 숫자 id와 이름 모두 → 원장에 적힌 그때 값", al2.get("555") == "150KTC-V2-OLD1"
   and al2.get("150ktc-v2-old1") == "150KTC-V2-OLD1" and al2.get("556") == "PA-150K-NEW2", al2)
ck("4c 원장에도 설정에도 없는 계좌는 그대로", "9" not in al2 and "other" not in al2, al2)
al3 = E._acct_alias(PX(rows), ["150KTC-V2-AB12"], ["12345"])
ck("4d 같은 계좌가 설정과 원장 둘 다에 맞으면 설정 값이 먼저", al3.get("12345") == "150KTC-V2-AB12"
   and al3.get("150ktc-v2-ab12") == "150KTC-V2-AB12", al3)
ck("4e 설정이 비어도 원장 값만으로 잇는다, 둘 다 비면 조회 안 함", E._acct_alias(PX(rows2), [], ["150KTC-V2-OLD1"]).get("555") == "150KTC-V2-OLD1"
   and E._acct_alias(PX(rows2), [], []) == {})
# 재현: 평가 1R $2,200으로 진입(원장 기록), 지금 설정은 펀디드 계좌 하나(1R $600), 체결은 평가 계좌 숫자 id, 손실 -$3,051.40
_t0 = 1792764000.0
_led = {"NQ|150KTC-V2-OLD1": [[_t0, 2200.0]]}
_rba = {"PA-150K-NEW2": 600.0}
_f = {"acct": "555", "pnl": -3051.40, "ts": _t0 + 1200}


def _r_of(alias):
    _fa = alias.get(_f["acct"].casefold(), _f["acct"])
    _one = _rba.get(_fa, 600.0)
    _fb = _fa not in _rba
    r = _f["pnl"] / E._lookup_real_r(_led, "NQ", _fa, _f["ts"], _one)
    est = bool(_fb and not _led.get(f"NQ|{_fa}"))
    return round(r, 3), est


_old = _r_of(E._acct_alias(PX(rows2), ["PA-150K-NEW2"]))
_new = _r_of(E._acct_alias(PX(rows2), ["PA-150K-NEW2"], E._rledger_accts(_led)))
ck("4f 재현: 수리 전 -5.086R(기본 $600, 추정) → 수리 뒤 -1.387R(진입 때 원장 $2,200, 추정 아님)",
   _old == (-5.086, True) and _new == (-1.387, False), (_old, _new))
ck("4g 배선: 동기화가 1R 원장 계좌 값을 읽어(_rledger_accts) _acct_alias extra로 넘긴다, 조회 루프 전",
   "_rl_accts = _rledger_accts(" in src and "_rl_accts, _alias_all)" in src
   and src.index("_rl_accts = _rledger_accts(") < src.index("_alias = _acct_alias(b, [x[\"acct\"] for x in credlist"))
# ── 5. 같은 계좌를 이름으로 적었다가 id로 바꿔 적은 경우(원장은 옛 표기) - 추정 표시도 없이 지금 1R로 나뉘던 길 ──
_all = {}
al5 = E._acct_alias(PX(rows), ["12345"], ["150KTC-V2-AB12"], _all)
ck("5a all_out: 그 계좌에 맞는 값 전부(설정 먼저, 원장 다음), 별칭은 설정 값", al5.get("12345") == "12345"
   and _all.get("12345") == ["12345", "150KTC-V2-AB12"] and _all.get("150ktc-v2-ab12") == ["12345", "150KTC-V2-AB12"], (al5, _all))
_led5 = {"NQ|150KTC-V2-AB12": [[_t0, 2200.0]]}
_rba5 = {"12345": 600.0}                                   # 지금 설정: 같은 계좌를 id로, 1R $600(펀디드로 낮춤)
_f5 = {"acct": "12345", "pnl": -3051.40, "ts": _t0 + 1200, "_acct_keys": _all.get("12345")}


def _r5(use_alt):
    _aid = al5.get(_f5["acct"].casefold(), _f5["acct"])
    _one = _rba5.get(_aid, 600.0)
    _lk = _aid
    if use_alt and not _led5.get(f"NQ|{_aid}"):
        for _alt in _f5.get("_acct_keys") or ():
            if _led5.get(f"NQ|{_alt}"):
                _lk = _alt
                break
    return round(_f5["pnl"] / E._lookup_real_r(_led5, "NQ", _lk, _f5["ts"], _one), 3)


ck("5b 재현: 다른 표기로 원장을 찾아 -1.387R(진입 1R $2,200), 종전은 -5.086R(지금 1R $600, 추정 표시도 없음)",
   _r5(True) == -1.387 and _r5(False) == -5.086, (_r5(True), _r5(False)))
ck("5c 배선: 체결마다 다른 표기(_acct_keys), 원장 못 찾으면 그 표기로(_lk), 추정 판정도 같은 키",
   "f[\"_acct_keys\"] = list(_alias_all.get(" in src and "_lk = _alt" in src
   and "_rledger, a, _lk, (f.get(\"ts_ms\") or 0) / 1000.0, _fb)" in src
   and "_lookup_baseline_r(_rledger, a, _lk, _fb)" in src and "(_rledger or {}).get(f\"{a}|{_lk or ''}\")" in src)
print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
