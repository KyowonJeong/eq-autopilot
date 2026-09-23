# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# =========================
# autopilot_crypto.py
# 멤버별 피드/하트비트 페이로드 암호화(서버·앱 공용).
#   파일 경로 = path_id(token) = SHA256("eqpath:"+token)  → URL이 토큰을 노출하지 않음
#   포맷 v2   = "v2." + base64(nonce12 | AES-256-GCM 암호문+태그16)   (2026-09-23 표준 AEAD)
#              키 = HKDF-SHA256(token, info="eq-autopilot v2 aes-gcm"), AAD = "eq:v2"
#              `cryptography` 패키지 필요(앱 번들·서버 venv에 포함). 없으면 v1로 낸다.
#   포맷 v1   = base64(nonce16 | SHA256-CTR 암호문 | HMAC-SHA256 태그32)  (encrypt-then-MAC)
#              stdlib만으로 동작. **복호화는 계속 지원**(구 앱·구 파일), 발행은 v2를 못
#              받는 상대(구 앱)에게만 - 서버가 앱의 aead 광고(app_alive)를 보고 고른다.
#   왜 바꿨나: 2026-09-23 외부 감사(공개 저장소)가 "직접 조립한 스트림 암호+MAC보다
#   검증된 AEAD를 쓰라"고 지적했다. 구조상 결함이 발견된 것은 아니지만 표준 구성으로
#   옮기는 비용이 작고, 감사자가 매번 같은 질문을 하지 않게 된다.
# 토큰을 가진 본인(멤버 앱)과 서버만 복호화 가능. 파일/URL이 새도 평문 노출 X.
# ⚠ 서버(autopilot_feed/hb)와 앱(eqgui)이 같은 파일을 써야 한다(동일 복사본 유지).
# =========================
import base64
import hashlib
import hmac
import json
import os

_V2_PREFIX = "v2."
_V2_AAD = b"eq:v2"
_V2_INFO = b"eq-autopilot v2 aes-gcm"


def _keys(token: str):
    t = (token or "").encode()
    path_id = hashlib.sha256(b"eqpath:" + t).hexdigest()[:40]
    enc_key = hashlib.sha256(b"eqenc:" + t).digest()
    mac_key = hashlib.sha256(b"eqmac:" + t).digest()
    return path_id, enc_key, mac_key


def path_id(token: str) -> str:
    """토큰 → 파일 경로 식별자(단방향). URL에 토큰 대신 이 값을 쓴다."""
    return _keys(token)[0]


# ── v2: AES-256-GCM (cryptography) ─────────────────────────────────────────
def _aead():
    """AESGCM 클래스 또는 None(패키지 없음). 지연 import - path_id만 쓰는 곳은 부담 0."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except Exception:
        return None


def aead_available() -> bool:
    """이 프로세스가 v2를 만들고 풀 수 있는가. 앱은 이 값을 서버에 광고(alive 핑 'aead')하고,
    서버는 그 광고를 본 토큰에만 v2를 발행한다 - 앱이 못 푸는 형식을 보내는 일이 없게."""
    return _aead() is not None


def _v2_key(token: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=_V2_INFO).derive((token or "").encode())


def _encrypt_v2(token: str, pt: bytes) -> str:
    AESGCM = _aead()
    if AESGCM is None:
        raise RuntimeError("cryptography not available - v2 unsupported here")
    nonce = os.urandom(12)
    ct = AESGCM(_v2_key(token)).encrypt(nonce, pt, _V2_AAD)
    return _V2_PREFIX + base64.b64encode(nonce + ct).decode()


def _decrypt_v2(token: str, blob: str) -> bytes:
    AESGCM = _aead()
    if AESGCM is None:
        raise ValueError("v2 blob but cryptography not available")
    raw = base64.b64decode(blob[len(_V2_PREFIX):])
    if len(raw) < 12 + 16:
        raise ValueError("blob too short")
    try:
        return AESGCM(_v2_key(token)).decrypt(raw[:12], raw[12:], _V2_AAD)
    except Exception:
        raise ValueError("bad tag (wrong token or tampered)")


# ── v1: SHA256-CTR + HMAC (stdlib) - 구 앱·구 파일 호환 ────────────────────
def _keystream(enc_key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(enc_key + nonce + i.to_bytes(8, "big")).digest()
        i += 1
    return bytes(out[:n])


def _encrypt_v1(token: str, pt: bytes) -> str:
    _, enc_key, mac_key = _keys(token)
    nonce = os.urandom(16)
    ks = _keystream(enc_key, nonce, len(pt))
    ct = bytes(a ^ b for a, b in zip(pt, ks))
    tag = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(nonce + ct + tag).decode()


def _decrypt_v1(token: str, blob) -> bytes:
    raw = base64.b64decode(blob)
    if len(raw) < 16 + 32:
        raise ValueError("blob too short")
    _, enc_key, mac_key = _keys(token)
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    expect = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expect):
        raise ValueError("bad tag (wrong token or tampered)")
    ks = _keystream(enc_key, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks))


# ── 공개 API ────────────────────────────────────────────────────────────────
def encrypt(token: str, obj, v2=None) -> str:
    """obj(dict) → 문자열. 토큰별 키로 암호화.
    v2=None: 패키지 있으면 v2, 없으면 v1.  v2=False: v1 강제(구 앱 수신자).
    v2=True: v2 강제 - 패키지 없으면 RuntimeError(호출부가 v1로 내리고 운영자에게 알릴 것)."""
    pt = json.dumps(obj, ensure_ascii=False).encode()
    if v2 is None:
        v2 = aead_available()
    return _encrypt_v2(token, pt) if v2 else _encrypt_v1(token, pt)


def blob_version(blob) -> int:
    """이 문자열이 어느 포맷인가(2 또는 1). 판정만, 복호화 안 함."""
    return 2 if isinstance(blob, str) and blob.startswith(_V2_PREFIX) else 1


def decrypt(token: str, blob) -> dict:
    """문자열 → obj(dict). 태그 검증 실패/손상 시 ValueError. v1·v2 모두 푼다."""
    if isinstance(blob, bytes):
        blob = blob.decode()
    if blob_version(blob) == 2:
        pt = _decrypt_v2(token, blob)
    else:
        pt = _decrypt_v1(token, blob)
    return json.loads(pt)
