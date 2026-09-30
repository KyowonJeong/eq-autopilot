#!/usr/bin/env python3
# EQ Autopilot - [로그 보내기] 오프라인 테스트(대표 2026-09-29 "로그 복사 말고 로그 리포트 버튼")
# ① 가림: key=/secret=/token=/pin=/password= 값, 이메일, 키처럼 긴 영숫자열 → 가림. 계좌 끝4·가격·시각은 남김
# ② 금액: 기본 가림($•••·bal=•••·잔고 •••), 포함 선택 시 남김 ③ 서버 스위치(log_upload) 없으면 게이트에 False
# ④ 로그 호출부에 자격 값(f2·키·PIN·토큰)을 끼워 넣는 f-string이 없다(원래 로그에 없어야 한다는 설계 고정)
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
fails = []


def chk(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{d}]"))
    if not c:
        fails.append(n)


S = eqgui.App._scrub_upload
raw = ("api_key=AbC123xyz secret: s3cr3tVal token=eyJhbGciOiJIUzI1NiJ9abcdef PIN = 1234 password=hunter2\n"
       "mail me@example.com  blob " + "sk_" + "live_" + "51HxYzABCDEFGHIJKLMNOPQRSTUV" + "\n"
       "[…5678] 진입 30651.75 잔고 $52,310 bal=51234.5 1R $210 10:00:02")
a = S(raw, False)
chk("① key/secret/token/pin/password 값 가림", all(x not in a for x in ("AbC123xyz", "s3cr3tVal", "eyJhbGci", "1234 ", "hunter2")), a)
chk("① 이메일·긴 키 모양 문자열 가림", "me@example.com" not in a and "sk_live_51Hx" not in a, a)
chk("① 계좌 끝4·가격·시각 남김", "…5678" in a and "30651.75" in a and "10:00:02" in a, a)
chk("② 금액 기본 가림", "$52,310" not in a and "51234.5" not in a and "$210" not in a and "잔고 $•••" in a, a)
b = S(raw, True)
chk("② 금액 포함 선택 시 남김(비밀은 여전히 가림)", "$52,310" in b and "51234.5" in b and "hunter2" not in b, b)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("③ 게이트 스위치 log_upload(서버가 켤 때만)", '"log_upload": bool(ap.get("log_upload"))' in src and "_sync_log_btn" in src)
bad = [ln for ln in src.splitlines() if "self.log(" in ln and re.search(r"\{[^}]*\b(f2|_f2|api_key|secret|_pin|pin_|self\._token)\b[^}]*\}", ln)]
chk("④ 로그에 자격 값을 끼워 넣는 줄 없음", not bad, bad[:3])
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
