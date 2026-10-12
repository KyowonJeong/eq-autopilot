#!/usr/bin/env python3
# 원장 JSON 원자 저장·손상 복구 시험(대표 2026-10-12: .r_ledger.json이 246바이트 NUL로 깨져 지난 거래 R이 지금 1R로 재계산).
# HOME·APPDATA = 임시 폴더, 네트워크 0, 알림 0.
import os, sys, tempfile, json
HOME = tempfile.mkdtemp(prefix="eq_led_")
os.environ["HOME"] = HOME; os.environ["APPDATA"] = HOME; os.environ.pop("EQ_PROFILE", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui as G  # noqa: E402
assert G.APP_DIR.startswith(HOME), G.APP_DIR
fails = []


def ck(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:200]}]"))
    if not c:
        fails.append(n)


P = G._RLEDGER_PATH
G._record_real_r("BTC", "bybit", 50.0)
G._record_real_r("BTC", "bybit", 75.0)
d, st = G._read_json_guarded(P, dict)
ck("정상 기록 두 줄", st == "ok" and [r[1] for r in d["BTC|bybit"]] == [50.0, 75.0], (st, d))
ck(".bak = 직전 정상본(한 줄)", [r[1] for r in json.load(open(P + ".bak"))["BTC|bybit"]] == [50.0])
open(P, "wb").write(b"\x00" * 246)                      # 대표 기기 실제 손상 모양
d2, st2 = G._read_json_guarded(P, dict)
ck("NUL 손상 → 백업본으로 복구", st2 == "restored" and [r[1] for r in d2["BTC|bybit"]] == [50.0], (st2, d2))
ck("손상 파일은 .corrupt-로 보존", any(f.startswith(".r_ledger.json.corrupt-") for f in os.listdir(G.APP_DIR)), os.listdir(G.APP_DIR))
ck("복구본이 본 파일로 다시 써짐", json.load(open(P))["BTC|bybit"][0][1] == 50.0)
G._record_real_r("BTC", "bitget", 60.0)
d3, st3 = G._read_json_guarded(P, dict)
ck("복구 뒤 기록 이어짐", st3 == "ok" and "BTC|bitget" in d3 and "BTC|bybit" in d3, d3)
for f in [x for x in os.listdir(G.APP_DIR) if x.startswith(".r_ledger.json")]:
    os.remove(os.path.join(G.APP_DIR, f))
open(P, "wb").write(b"\x00" * 10)
d4, st4 = G._read_json_guarded(P, dict)
ck("백업도 없으면 빈 원장 + corrupt 상태", d4 == {} and st4 == "corrupt", (d4, st4))
G._ledger_add("BTC", "BTCUSDT", "LONG", "t")
ck("거래 원장도 원자 저장·읽기", len(G._ledger_load()) == 1 and not os.path.exists(G._LEDGER_PATH + ".tmp"))
import threading as _th  # noqa: E402
for f in [x for x in os.listdir(G.APP_DIR) if x.startswith(".r_ledger.json") or x.startswith(".eqtrades.json")]:
    os.remove(os.path.join(G.APP_DIR, f))
_lost = 0
for _round in range(100):                                # 계좌별 진입 스레드 3개 동시 출발(리뷰 P1-2 재현 모양)
    _go = _th.Barrier(3)

    def _w(i, r=_round):
        _go.wait()
        G._record_real_r("BTC", f"acct{i}", 100.0 + r)
        G._ledger_add("BTC", "BTCUSDT", "LONG", f"t{r}-{i}")
    _ts = [_th.Thread(target=_w, args=(i,)) for i in range(3)]
    [x.start() for x in _ts]; [x.join() for x in _ts]
    _d = G._read_json_guarded(P, dict)[0]
    if any(len(_d.get(f"BTC|acct{i}", [])) != _round + 1 for i in range(3)):
        _lost += 1
ck("동시 3스레드 100회: 1R 원장 새 항목 손실 0", _lost == 0, _lost)
ck("동시 3스레드 100회: 거래 원장 300건 그대로", len(G._ledger_load()) == 300, len(G._ledger_load()))
ck("임시 파일 찌꺼기 없음", not [x for x in os.listdir(G.APP_DIR) if x.endswith(".tmp")], os.listdir(G.APP_DIR))
# 리뷰 P2-1: 읽기 OSError(잠금·권한)는 손상이 아니다 - 옮기지도 되돌리지도 않고, 1R 기록은 덮어쓰지 않고 건너뛴다
G._record_real_r("NQ", "acctX", 2200.0)
_before = open(P, "rb").read()
os.chmod(P, 0)
try:
    _raised = False
    try:
        G._read_json_guarded(P, dict)
    except OSError:
        _raised = True
    G._record_real_r("NQ", "acctX", 450.0)
finally:
    os.chmod(P, 0o644)
ck("읽기 OSError → 예외(손상 처리 안 함)", _raised or os.geteuid() == 0)
ck("OSError 때 .corrupt로 옮기지 않음", not [x for x in os.listdir(G.APP_DIR) if x.startswith(".r_ledger.json.corrupt-")])
ck("OSError 때 원장 그대로(덮어쓰지 않음)", open(P, "rb").read() == _before)
# 리뷰 P2-2: 바꿔 끼우기가 끝내 실패하면 직접 저장 + 임시 파일 정리
_orig_replace = G.os.replace
G.os.replace = lambda a, b: (_ for _ in ()).throw(PermissionError("locked"))
try:
    G._write_json_atomic(P, {"X|y": 내부 기록})
finally:
    G.os.replace = _orig_replace
ck("바꿔 끼우기 실패 → 직접 저장으로 기록 남음", json.load(open(P)) == {"X|y": 내부 기록})
ck("바꿔 끼우기 실패 → .tmp 남지 않음", not [x for x in os.listdir(G.APP_DIR) if x.endswith(".tmp")])
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
