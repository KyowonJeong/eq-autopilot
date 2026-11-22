#!/usr/bin/env python3
# 시험판 표시(_TEST_BUILD) 오프라인 시험. 0 = 통과.
#   1 분기별 값: app-dailycap = False(회원판), app-dailycap-test = True(대표 시험판). git에 두 분기가 있으면 원문을 읽어 본다(없으면 건너뜀을
#     알린다). 지금 분기가 둘 중 하나면 이 작업트리의 값도 그 분기 값이어야 한다.
#   2 동작(값을 바꿔 가며): True = 그림자 모드 기본 켜짐(config.yaml daycap_shadow: false일 때만 꺼짐), 창 제목과 [라이브 시작] 로그에
#     시험판 KO/EN. False = 종전 그대로(그림자 모드는 설정, 환경 변수로만 켜짐, 제목과 로그에 시험판 없음).
#   3 버전 문자열은 꼬리 글자를 붙이지 않는다(서버의 앱 버전 비교 꼴 YYYY.MM.DD + 글자).
# HOME, APPDATA = 임시 폴더, 네트워크 차단, EQ_NO_ALERT=1.
import os
import re
import socket
import subprocess
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="eq_testbuild_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ.pop("EQ_PROFILE", None)
os.environ.pop("EQ_DAYCAP_SHADOW", None)
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
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {str(d)[:400]}"))
    if not c:
        FAIL.append(n)


_RX = re.compile(r"^_TEST_BUILD = (True|False)\s*$", re.M)


def _flag_of(src):
    m = _RX.findall(src or "")
    return (m[0] == "True") if len(m) == 1 else None


# ── 1 분기별 값 ─────────────────────────────────────────────────────────────────
with open(os.path.join(HERE, "eqgui.py"), encoding="utf-8") as f:
    _here = _flag_of(f.read())
ck("1a 이 작업트리의 eqgui.py에 _TEST_BUILD 줄이 꼭 하나(True/False)", _here is not None and _here is E._TEST_BUILD, _here)


def _git(*a):
    try:
        r = subprocess.run(["git", "-C", HERE] + list(a), capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


_vals = {}
for _br in ("app-dailycap", "app-dailycap-test"):
    _src = _git("show", f"{_br}:executor/eqgui.py")
    if _src is None:
        print(f"SKIP 1b 분기 {_br}를 git에서 읽지 못해 그 분기 값 확인을 건너뜁니다")
        continue
    _vals[_br] = _flag_of(_src)
if "app-dailycap" in _vals:
    ck("1b app-dailycap(회원판 분기) = _TEST_BUILD False", _vals["app-dailycap"] is False, _vals)
if "app-dailycap-test" in _vals:
    ck("1c app-dailycap-test(대표 시험판 분기) = _TEST_BUILD True", _vals["app-dailycap-test"] is True, _vals)
_head = (_git("rev-parse", "--abbrev-ref", "HEAD") or "").strip()
if _head in ("app-dailycap", "app-dailycap-test"):
    ck(f"1d 지금 분기({_head})의 값 = {_head == 'app-dailycap-test'}", E._TEST_BUILD is (_head == "app-dailycap-test"),
       (E._TEST_BUILD, _head))
else:
    print(f"SKIP 1d 지금 분기({_head or '모름'})는 두 분기가 아니라 값 대조를 건너뜁니다")


# ── 2 동작 ─────────────────────────────────────────────────────────────────────
def _cfg(text):
    os.makedirs(os.path.dirname(E.CFG_PATH), exist_ok=True)
    with open(E.CFG_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    E._SHADOW_CACHE["mt"] = None


def _rm_cfg():
    try:
        os.remove(E.CFG_PATH)
    except OSError:
        pass
    E._SHADOW_CACHE["mt"] = None


class Stub:
    lang = "ko"

    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(str(m))


_orig = E._TEST_BUILD
try:
    for _flag in (False, True):
        E._TEST_BUILD = _flag
        _rm_cfg()
        _s0 = E._daycap_shadow()
        _cfg("lang: ko\ndaycap_shadow: false\n")
        _s1 = E._daycap_shadow()
        _cfg("lang: ko\ndaycap_shadow: true\n")
        _s2 = E._daycap_shadow()
        _cfg("lang: ko\n")
        _s3 = E._daycap_shadow()
        _rm_cfg()
        os.environ["EQ_DAYCAP_SHADOW"] = "1"
        _s4 = E._daycap_shadow()
        os.environ.pop("EQ_DAYCAP_SHADOW", None)
        ck(f"2a _TEST_BUILD {_flag}: 그림자 모드 기본 = {_flag}(설정 없음, 다른 키만 있음), daycap_shadow: false = 꺼짐, true = 켜짐, "
           "환경 변수 = 켜짐", (_s0, _s1, _s2, _s3, _s4) == (_flag, False, True, _flag, True), (_s0, _s1, _s2, _s3, _s4))
        _t = E._window_title()
        ck(f"2b _TEST_BUILD {_flag}: 창 제목 " + ("'시험판 (Test build)'" if _flag else "종전 그대로('EQ Autopilot', 시험판 없음)"),
           (("시험판 (Test build)" in _t) is _flag) and _t.startswith("EQ Autopilot")
           and (_flag or _t == "EQ Autopilot" + (f"  [{E.PROFILE}]" if E.PROFILE else "") + ("  - share mode" if E.SHARE_MODE else "")),
           _t)
        _rm_cfg()
        _ko, _en = Stub(), Stub()
        _en.lang = "en"
        E.App._daycap_mode_notice(_ko)
        E.App._daycap_mode_notice(_en)
        _kt = [m for m in _ko.logs if "시험판" in m]
        _et = [m for m in _en.logs if "Test build" in m or "test build" in m]
        _ksh = [m for m in _ko.logs if "그림자 모드가 켜져 있습니다" in m]
        if _flag:
            ck("2c 시험판 [라이브 시작] 로그 KO/EN 한 줄씩(시험판, 회원 배포본 아님, 그림자 모드 기본 켜짐과 끄는 법) + 그림자 모드 안내",
               len(_kt) == 1 and "이 앱은 시험판입니다(회원 배포본이 아닙니다)" in _kt[0] and "daycap_shadow: false" in _kt[0]
               and len(_et) == 1 and "This is a test build (not the member release)" in _et[0] and "daycap_shadow: false" in _et[0]
               and len(_ksh) == 1, (_ko.logs, _en.logs))
            _all = _kt + _et
            ck("2d 새 문구에 가운뎃점, 이모지, em 대시 없음 + 합니다체",
               not any(("·" in m or "—" in m or "요." in m or re.search("[\U0001F300-\U0001FAFF☀-➿]", m)) for m in _all), _all)
        else:
            ck("2c 회원판 [라이브 시작] 로그 = 종전 그대로(시험판 줄, 그림자 모드 안내 없음)", not _kt and not _et and not _ksh,
               (_ko.logs, _en.logs))
finally:
    E._TEST_BUILD = _orig
    _rm_cfg()

# ── 3 버전 문자열 ──────────────────────────────────────────────────────────────
ck("3a 버전 문자열은 서버 비교 꼴 그대로(YYYY.MM.DD + 글자, 시험판 꼬리 없음)",
   re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})([a-z]*)$", E.App._APP_VER) is not None, E.App._APP_VER)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
