# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# =========================
# autopilot_crypto.py
# 멤버별 피드/하트비트 페이로드 암호화(서버·앱 공용, stdlib only — 네이티브 의존성 없음).
#   파일 경로 = path_id(token) = SHA256("eqpath:"+token)  → URL이 토큰을 노출하지 않음
#   암호화    = SHA256-CTR 스트림 + HMAC-SHA256 (encrypt-then-MAC)
#   키        = 토큰에서 파생(enc/mac 분리). 토큰 없으면 경로도 못 찾고 내용도 못 푼다.
# 토큰을 가진 본인(멤버 앱)과 서버만 복호화 가능. 파일/URL이 새도 평문 노출 X.
# ⚠ 서버(autopilot_feed/hb)와 앱(eqgui/eqgui_close)이 같은 파일을 써야 한다(동일 복사본 유지).
# =========================
import base64
import hashlib
import hmac
import json
import os


def _keys(token: str):
    t = (token or "").encode()
    path_id = hashlib.sha256(b"eqpath:" + t).hexdigest()[:40]
    enc_key = hashlib.sha256(b"eqenc:" + t).digest()
    mac_key = hashlib.sha256(b"eqmac:" + t).digest()
    return path_id, enc_key, mac_key


def path_id(token: str) -> str:
    """토큰 → 파일 경로 식별자(단방향). URL에 토큰 대신 이 값을 쓴다."""
    return _keys(token)[0]


def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(enc_key + nonce + i.to_bytes(8, "big")).digest()
        i += 1
    return bytes(out[:n])


def encrypt(token: str, obj) -> str:
    """obj(dict) → base64 문자열(nonce|ciphertext|tag). 토큰별 키로 암호화."""
    _, enc_key, mac_key = _keys(token)
    pt = json.dumps(obj, ensure_ascii=False).encode()
    nonce = os.urandom(16)
    ks = _keystream(enc_key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(nonce + ct + tag).decode()


def decrypt(token: str, blob) -> dict:
    """base64 문자열 → obj(dict). 태그 검증 실패/손상 시 ValueError."""
    raw = base64.b64decode(blob)
    if len(raw) < 16 + 32:
        raise ValueError("blob too short")
    _, enc_key, mac_key = _keys(token)
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    expect = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expect):
        raise ValueError("bad tag (wrong token or tampered)")
    ks = _keystream(enc_key, nonce, len(ct))
    pt = bytes(a ^ b for a, b in zip(ct, ks))
    return json.loads(pt)
