#!/usr/bin/env python3
# EQ Autopilot - 페이로드 암호화 v2(AES-GCM) + 구 v1 호환 오프라인 테스트 (2026-09-23)
# =========================
# 왜: 2026-09-23 외부 감사(공개 저장소)가 "직접 조립한 SHA256-CTR+HMAC 대신 검증된 AEAD를
#   쓰라"고 했다. 옮기면서 지켜야 할 것은 하나 - **구 앱·구 파일이 계속 열려야 한다**.
#   서버는 앱이 alive 핑에 aead=True를 광고한 토큰에만 v2를 쓰고, 앱은 hb가 aead=True를
#   광고할 때만 서버로 v2를 보낸다. 이 테스트는 그 양방향 호환과 설정 내보내기(EQSET1/2)를
#   네트워크 없이 고정한다.
#
# 사용: python executor/test_crypto_offline.py   (0=통과, 1=실패)
# =========================
import base64
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import autopilot_crypto as C  # noqa: E402
import eqgui  # noqa: E402

fails = []


def chk(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        fails.append(name)


tok = "abcDEF123_-xyz"
obj = {"id": "NQ-2026-09-23", "direction": "LONG", "stop": 1.25, "한글": "값"}
have = C.aead_available()
chk("빌드 환경에 cryptography가 있다(없으면 회원 앱이 v1만 받는다)", have)

# ── v1(구 포맷): 늘 되고, 구 모듈이 만든 골든 블롭이 열린다 ──
b1 = C.encrypt(tok, obj, v2=False)
chk("v1 왕복", C.decrypt(tok, b1) == obj and C.blob_version(b1) == 1)
_, ek, mk = C._keys(tok)
nonce = b"\x01" * 16
pt = json.dumps(obj, ensure_ascii=False).encode()
ct = bytes(a ^ b for a, b in zip(pt, C._keystream(ek, nonce, len(pt))))
gold = base64.b64encode(nonce + ct + hmac.new(mk, nonce + ct, hashlib.sha256).digest()).decode()
chk("v1 골든(구 모듈 산출물) 복호화", C.decrypt(tok, gold) == obj)
try:
    C.decrypt("wrong", b1); chk("v1 토큰 불일치=ValueError", False)
except ValueError:
    chk("v1 토큰 불일치=ValueError", True)

# ── v2 ──
if have:
    b2 = C.encrypt(tok, obj, v2=True)
    chk("v2 접두사·왕복", b2.startswith("v2.") and C.decrypt(tok, b2) == obj)
    chk("v2 bytes 입력도 연다(requests .content 경로)", C.decrypt(tok, b2.encode()) == obj)
    try:
        C.decrypt("wrong", b2); chk("v2 토큰 불일치=ValueError", False)
    except ValueError:
        chk("v2 토큰 불일치=ValueError", True)
    raw = bytearray(base64.b64decode(b2[3:])); raw[15] ^= 1
    try:
        C.decrypt(tok, "v2." + base64.b64encode(bytes(raw)).decode()); chk("v2 변조=ValueError", False)
    except ValueError:
        chk("v2 변조=ValueError", True)
    chk("v2 호출마다 nonce 다름", C.encrypt(tok, obj, v2=True) != b2)
    chk("기본(v2=None)은 v2", C.blob_version(C.encrypt(tok, obj)) == 2)
    # 패키지 없는 척 - 구 빌드 동작
    _real = C._aead
    C._aead = lambda: None
    try:
        C.encrypt(tok, obj, v2=True); chk("패키지 없이 v2 강제=RuntimeError", False)
    except RuntimeError:
        chk("패키지 없이 v2 강제=RuntimeError", True)
    chk("패키지 없으면 기본은 v1", C.blob_version(C.encrypt(tok, obj)) == 1)
    try:
        C.decrypt(tok, b2); chk("패키지 없이 v2 블롭=ValueError(조용히 {} 아님)", False)
    except ValueError:
        chk("패키지 없이 v2 블롭=ValueError(조용히 {} 아님)", True)
    chk("aead_available()가 False로 정직", C.aead_available() is False)
    C._aead = _real

# ── 설정 내보내기: EQSET2(GCM) 만들고 열기, EQSET1(구) 열기, 잘못된 PIN ──
cfg = {"kind": "eq-autopilot-settings", "v": 1, "assets": {"NQ": {"creds": {"projectx": {"f1": "u", "f2": "s3cr3t"}}}}}
blob = eqgui._settings_export_blob("1234", cfg)
chk("내보내기 포맷 = " + ("EQSET2" if have else "EQSET1"), blob[:6] == (b"EQSET2" if have else b"EQSET1"))
chk("내보내기 왕복", eqgui._settings_import_blob("1234", blob) == cfg)
try:
    eqgui._settings_import_blob("9999", blob); chk("PIN 틀림=ValueError", False)
except ValueError:
    chk("PIN 틀림=ValueError", True)
# 구 빌드가 만든 EQSET1 파일을 새 빌드가 연다(구 산출 경로를 그대로 재현)
salt, nonce = os.urandom(16), os.urandom(16)
ek, mk = eqgui._exp_keys("1234", salt)
pt = json.dumps(cfg, ensure_ascii=False).encode()
ct = bytes(a ^ b for a, b in zip(pt, eqgui._exp_stream(ek, nonce, len(pt))))
old = eqgui._EXP_MAGIC + salt + nonce + ct + hmac.new(mk, eqgui._EXP_MAGIC + salt + nonce + ct, hashlib.sha256).digest()
chk("EQSET1(구 빌드 파일) 가져오기", eqgui._settings_import_blob("1234", old) == cfg)
try:
    eqgui._settings_import_blob("1234", b"EQSETX" + b"\x00" * 60); chk("정체불명 파일=ValueError", False)
except ValueError:
    chk("정체불명 파일=ValueError", True)

# ── 배선 고정(정적): 핑이 aead를 광고하고, 프로필 푸시는 서버 광고를 따른다 ──
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("alive 핑에 aead 광고 배선", '"aead": _ac.aead_available()' in src)
chk("프로필 푸시 v2 = 서버 광고 AND 로컬 가능", "v2=bool(self._srv_aead) and autopilot_crypto.aead_available()" in src)
chk("hb에서 서버 광고 저장", 'self._srv_aead = bool(hb.get("aead"))' in src)
chk("클래스 기본값 _srv_aead=False(광고 전엔 v1)", getattr(eqgui.App, "_srv_aead", None) is False)

print()
print(("실패 %d건: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
