# EQ Autopilot - 오프라인 시험: Bitget 통합 거래 계정(UTA) 결과 모름 진입과 그 전 플랫 구간의 트리거(2026-11-11 최종 확인 P2-1).
# 결과 모름으로 끝난 진입(뒤에 미확인 진입 감시가 늦은 체결을 찾음)은 _u_new_entry를 거치지 않는다. 보내기 직전에 진입 시각을 적어
# 그 전 거래에 남은 대기 중 트리거를 새 포지션의 손절로 보지 않아야 한다(_trig_current가 거름). 네트워크 없음.
import json
import os
import socket
import sys
import tempfile
import time

os.environ["HOME"] = tempfile.mkdtemp()
os.environ["EQ_NO_ALERT"] = "1"
os.environ["EQ_ERR_REPORT"] = "0"


def _blocked(*a, **k):
    raise OSError("network blocked")


socket.socket.connect = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eqexec.broker import bitget as BG  # noqa: E402
from eqexec.config import BitgetCfg  # noqa: E402

fails = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)


def _boom(body, long_side):
    raise TimeoutError("read timed out")


def _req_fail(m, p, params=None, body=None):
    raise TimeoutError("timeout")


# 1. 그 전 거래의 트리거가 남은 상태에서 결과 모름 진입 → 그 트리거는 이 포지션의 손절이 아니다
b = BG.BitgetBroker(BitgetCfg(api_key="k_unk1", api_secret="s", passphrase="p"))
b._mstate()["mode"] = "uta"
st = b._mstate()
b._trig_book()["BTCUSDT"] = {"T_OLD"}
st["trig_ts"] = {"T_OLD": time.time() - 7 * 86400}
st["entry_at"] = {"BTCUSDT": time.time() - 7 * 86400 - 3600}
check("1a stale trigger counts before the entry", b._trig_current("BTCUSDT", {"T_OLD"}) == {"T_OLD"})
b._u_send_open = _boom
b._req = _req_fail
out = b._u_place_entry_core(symbol="BTCUSDT", side="SHORT", size=0.01, stop_loss_price=95000, custom_tag="eqt1",
                            dry_run=False)
check("1b unknown entry reports stop_attached False", out.get("stop_attached") is False)
check("1c stale trigger ignored after the entry was sent", b._trig_current("BTCUSDT", {"T_OLD"}) == set())
d = json.load(open(BG._trig_path(), encoding="utf-8"))
check("1d entry time saved with the remaining ids (restart)", any(k.endswith("|entry") for k in d))
check("1e stale id stays saved (cleanup can still cancel it)", "T_OLD" in (b._trig_book().get("BTCUSDT") or set()))

# 2. 트리거 기록이 없으면 파일은 건드리지 않고 진입 시각만 적는다
b2 = BG.BitgetBroker(BitgetCfg(api_key="k_unk2", api_secret="s", passphrase="p"))
b2._mstate()["mode"] = "uta"
b2._u_send_open = _boom
b2._req = _req_fail
_before = os.path.exists(BG._trig_path()) and json.load(open(BG._trig_path(), encoding="utf-8"))
b2._u_place_entry_core(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="eqt2", dry_run=False)
check("2a entry time set with no saved ids", "BTCUSDT" in (b2._mstate().get("entry_at") or {}))
_after = os.path.exists(BG._trig_path()) and json.load(open(BG._trig_path(), encoding="utf-8"))
check("2b no file change for a key with no saved ids", _before == _after)

# 3. 진입 뒤에 건 트리거는 그대로 손절로 본다
b3 = BG.BitgetBroker(BitgetCfg(api_key="k_unk3", api_secret="s", passphrase="p"))
b3._mstate()["mode"] = "uta"
b3._u_send_open = _boom
b3._req = _req_fail
b3._u_place_entry_core(symbol="BTCUSDT", side="LONG", size=0.01, stop_loss_price=85000, custom_tag="eqt3", dry_run=False)
time.sleep(0.01)
b3._trig_book()["BTCUSDT"] = {"T_NEW"}
b3._mstate().setdefault("trig_ts", {})["T_NEW"] = time.time()
check("3a trigger placed after the entry still counts", b3._trig_current("BTCUSDT", {"T_NEW"}) == {"T_NEW"})

print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
