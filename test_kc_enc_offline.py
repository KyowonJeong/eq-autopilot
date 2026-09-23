#!/usr/bin/env python3
# EQ Autopilot - 키체인 대체 보관(PIN 암호화, 대표 2026-09-23 C안) 오프라인 테스트
# =========================
# 왜: 외부 감사가 "키체인 실패 시 평문 YAML"을 지적했다. A안(fail-closed)은 키링이 안 되는 기기에서
#   매 기동 재입력이 된다. C안 = 실패분을 PIN 파생 키로 암호화한 블롭(kc_enc)에 두고 기동 때 PIN 한 번.
#   이 테스트는 키체인을 '항상 실패'로 스텁해 ①디스크에 평문 0 ②블롭 왕복 ③틀린 PIN ④취소=fail-closed
#   ⑤키체인 되는 기기는 블롭 없음 ⑥f2 사용처가 폴백을 경유함을 고정한다. 부수효과 0(임시 설정 파일).
#
# 사용: python executor/test_kc_enc_offline.py   (0=통과, 1=실패)
# =========================
import os
import re
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
import yaml  # noqa: E402

fails = []


def chk(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        fails.append(name)


# ── 스텁: 키체인 항상 실패, PIN 대화상자는 큐에서 꺼냄, 팝업 무시 ──
eqgui._kc_save = lambda *a, **k: False
eqgui._kc_load = lambda *a, **k: ""
eqgui._kc_del = lambda *a, **k: None
PINS = []
PROMPTS = []
eqgui.simpledialog.askstring = lambda *a, **k: (PROMPTS.append(a[1] if len(a) > 1 else ""), (PINS.pop(0) if PINS else None))[1]
eqgui.messagebox.showwarning = lambda *a, **k: None
tmp = tempfile.mkdtemp()
eqgui.CFG_PATH = os.path.join(tmp, "config.yaml")

spec = eqgui._BROKER_SPEC
b3 = next(b for b, v in spec.items() if v.get("f3_secret"))
a3 = next(a for a, bs in eqgui._ASSET_BROKERS.items() if b3 in bs)
F3, F2 = "PASS-f3-secret-xyz", "API-SECRET-f2-abc"
NSEC = len(eqgui._secret_fields(b3)) + 1   # 이 브로커의 비밀 필드 수(f1이 비밀인 브로커는 2) + f2
acfg = {a3: {"broker": b3, "creds": {b3: {"f1": "user@x", "f3": F3, "avail": []}},
             "include": True, "accounts": []}}
eqgui._ENC_SECRETS.clear(); eqgui._ENC_PIN = None

# ① f2: 키체인 실패 → 세션 메모리 폴백
chk("f2 키체인 실패 → _f2_save False·메모리 보관", eqgui._f2_save("user@x", F2) is False and eqgui._f2_load("user@x") == F2)

# ② 저장: PIN 새로 정함(두 번 입력) → 디스크 평문 0 + kc_enc 블롭
PINS[:] = ["2468", "2468"]
eqgui._save_full("ko", "tok", acfg)
raw = open(eqgui.CFG_PATH, encoding="utf-8").read()
d = yaml.safe_load(raw) or {}
chk("YAML에 평문 비밀 0(f3·f2)", F3 not in raw and F2 not in raw)
chk("kc_enc 블롭 있음 · kc_failed 없음", bool((d.get("kc_enc") or {}).get("blob")) and not d.get("kc_failed"))
chk("kc_enc.fields = 비밀 필드 전부 + f2", sorted(d["kc_enc"]["fields"]) == sorted([f"{a3}|{b3}|{f}" for f in eqgui._secret_fields(b3)] + ["f2|user@x"]))
chk("PIN 두 번 물음(설정+확인)", len(PROMPTS) == 2 and not PINS)
chk("메모리 acfg는 그대로(세션 동작)", acfg[a3]["creds"][b3]["f3"] == F3)
chk("표시 상태 _KC_LAST_ENC N개·_KC_LAST_FAILED 0", len(eqgui._KC_LAST_ENC) == NSEC and not eqgui._KC_LAST_FAILED)
chk("세션 PIN 캐시", eqgui._ENC_PIN == "2468")

# ③ 로드 → 필드 비어 있음 + kc_enc 전달 → 올바른 PIN으로 복원
L = eqgui._load()
chk("로드 시 비밀 필드 비어 있음", not L["assets"][a3]["creds"][b3]["f3"])
chk("로드가 kc_enc를 넘김", isinstance(L.get("kc_enc"), dict) and L["kc_enc"].get("blob"))
eqgui._ENC_SECRETS.clear()
got = eqgui._enc_restore(L["assets"], L["kc_enc"], "2468")
chk("복원 N개: f3 되채움 + f2 폴백 경유", len(got) == NSEC and L["assets"][a3]["creds"][b3]["f3"] == F3
    and eqgui._f2_load("user@x") == F2)
try:
    eqgui._enc_restore(L["assets"], L["kc_enc"], "0000"); chk("틀린 PIN = ValueError", False)
except ValueError:
    chk("틀린 PIN = ValueError", True)

# ④ 기동 되채움 메서드(App 인스턴스 없이 함수로): 성공 / 틀림→재시도 / 취소→kc_failed
def _fake(pending):
    f = types.SimpleNamespace(lang="ko", root=None, _kc_enc_pending=pending, _kc_failed=[], _acfg=L["assets"])
    return f
eqgui._ENC_SECRETS.clear(); eqgui._ENC_PIN = None
L["assets"][a3]["creds"][b3]["f3"] = ""
PINS[:] = ["9999", "2468"]; PROMPTS.clear()
fk = _fake(L["kc_enc"]); eqgui.App._enc_unlock_startup(fk)
chk("기동 되채움: 틀린 PIN 뒤 맞는 PIN → 복원·캐시", L["assets"][a3]["creds"][b3]["f3"] == F3
    and eqgui._ENC_PIN == "2468" and len(fk._kc_enc_restored) == NSEC and len(PROMPTS) == 2 and not fk._kc_failed)
eqgui._ENC_SECRETS.clear(); eqgui._ENC_PIN = None
L["assets"][a3]["creds"][b3]["f3"] = ""
PINS[:] = []; PROMPTS.clear()
fk = _fake(L["kc_enc"]); eqgui.App._enc_unlock_startup(fk)
chk("기동 되채움 취소 → 재입력 경고 목록(kc_failed) N개·값 없음", len(fk._kc_failed) == NSEC
    and not L["assets"][a3]["creds"][b3]["f3"] and eqgui._ENC_PIN is None)
chk("취소 경고 라벨에 내부 필드 키 원문(a|b|f) 안 나감", all("|" not in x for x in fk._kc_failed))

# ⑤ 재저장: 캐시된 PIN이면 프롬프트 0, 캐시 없으면 지난 블롭을 여는 PIN이어야 함
eqgui._ENC_SECRETS["f2|user@x"] = F2
eqgui._ENC_PIN = "2468"; PINS[:] = []; PROMPTS.clear()
eqgui._save_full("ko", "tok", acfg)
d = yaml.safe_load(open(eqgui.CFG_PATH, encoding="utf-8").read()) or {}
chk("캐시된 PIN 재저장: 프롬프트 0·블롭 갱신", not PROMPTS and bool((d.get("kc_enc") or {}).get("blob")))
eqgui._ENC_PIN = None; PINS[:] = ["1111", "2468"]; PROMPTS.clear()
eqgui._save_full("ko", "tok", acfg)
d = yaml.safe_load(open(eqgui.CFG_PATH, encoding="utf-8").read()) or {}
chk("캐시 없음: 지난 블롭 못 여는 PIN 거부 → 맞는 PIN으로 저장(프롬프트 2)", len(PROMPTS) == 2
    and eqgui._ENC_PIN == "2468" and bool((d.get("kc_enc") or {}).get("blob")))

# ⑥ PIN 취소 = fail-closed(저장 안 함 + kc_failed) - 평문 0
eqgui._ENC_PIN = None; PINS[:] = []; PROMPTS.clear()
eqgui._save_full("ko", "tok", acfg)
raw = open(eqgui.CFG_PATH, encoding="utf-8").read(); d = yaml.safe_load(raw) or {}
chk("PIN 취소: kc_enc 없음·kc_failed N개·평문 0", not d.get("kc_enc") and len(d.get("kc_failed") or []) == NSEC
    and F3 not in raw and F2 not in raw)
chk("취소 시 표시 상태 = 저장 안 됨(_KC_LAST_FAILED)·암호화 0", eqgui._KC_LAST_FAILED and not eqgui._KC_LAST_ENC)

# ⑦ 키체인이 되는 기기: 종전 그대로(블롭도 경고도 없음)
eqgui._kc_save = lambda *a, **k: True
eqgui._ENC_SECRETS.clear(); PINS[:] = []; PROMPTS.clear()
eqgui._save_full("ko", "tok", acfg)
raw = open(eqgui.CFG_PATH, encoding="utf-8").read(); d = yaml.safe_load(raw) or {}
chk("키체인 정상: kc_enc·kc_failed 없음·프롬프트 0·평문 0", not d.get("kc_enc") and not d.get("kc_failed")
    and not PROMPTS and F3 not in raw)
chk("f2 키체인 성공 시 메모리 폴백 비움", eqgui._f2_save("user@x", F2) is True and "f2|user@x" not in eqgui._ENC_SECRETS)

# ⑧ 정적 배선: f2 사용처가 키체인을 직접 읽지 않는다
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("f2 읽기 사이트 전부 _f2_load 경유(직접 _kc_load(f1)은 헬퍼 안 1곳뿐)", len(re.findall(r"_kc_load\((f1|_f1|user)\)", src)) == 1)
chk("f2 저장 사이트 전부 _f2_save 경유", not re.search(r"_kc_save\((_f1_now|_f1, _f2|user or f1)", src))
chk("기동 되채움이 __init__에 배선", "self._enc_unlock_startup()" in src)

print()
print(("실패 %d건: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
