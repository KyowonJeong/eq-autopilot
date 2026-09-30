#!/usr/bin/env python3
# EQ Autopilot - 나가는 문구의 계좌 식별자 마스킹(v2026.09.23d) 오프라인 테스트
# 왜: 진입 실패 DM에 계좌 ID 원문, /eqerr는 5자리+ 숫자만 가리던 것. 세 초크포인트가 한 헬퍼로 덮이는지 고정.
# 사용: python executor/test_mask_ids_offline.py   (0=통과, 1=실패)
import os, sys, types, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
fails = []
def chk(n, c):
    print(("PASS  " if c else "FAIL  ") + n)
    if not c: fails.append(n)
# 스레드는 즉시 실행, requests.post는 캡처
posts = []
class _T:
    def __init__(self, target=None, daemon=None, **k): self._t = target
    def start(self): self._t and self._t()
eqgui.threading.Thread = _T
sys.modules["requests"] = types.SimpleNamespace(post=lambda url, **k: posts.append((url, k.get("json"))) or types.SimpleNamespace(ok=True, status_code=200),
                                                get=lambda *a, **k: types.SimpleNamespace(ok=True, status_code=200, text=""))
app = types.SimpleNamespace(_acfg={"NQ": {"accounts": [{"id": "50KTC-V2-123456"}, {"id": "PRAC-ABC-98765"}]},
                                   "BTC": {"accounts": [{"id": ""}]}},
                            _token="tok", _APP_VER=eqgui.App._APP_VER, _err_sent={}, log=lambda *a, **k: None)
app._mask_ids = lambda t: eqgui.App._mask_ids(app, t)
m = app._mask_ids
chk("설정 계좌 ID → …끝4", m("계좌 50KTC-V2-123456 진입 실패") == "계좌 …3456 진입 실패")
chk("두 번째 계좌도·5자리+ 숫자열 #·짧은 이름은 유지", m("PRAC-ABC-98765 order 1234567 Sim101 U7") == "…8765 order # Sim101 U7")
chk("빈 문자열·None 안전", m("") == "" and m(None) == "")
chk("계좌 ID 없는 문구는 숫자 규칙만", m("NQ 진입 21500.25 손절 21400") == "NQ 진입 21500.25 손절 21400" or m("NQ 진입 2150025") == "NQ 진입 #")
# 초크포인트 1: _member_alert
posts.clear(); eqgui.App._member_alert(app, "entry_miss", "계좌 50KTC-V2-123456 실패 주문 9876543", "account 50KTC-V2-123456 failed")
chk("_member_alert 페이로드 ko/en 마스킹", posts and posts[-1][1]["ko"] == "계좌 …3456 실패 주문 #" and posts[-1][1]["en"] == "account …3456 failed")
# 초크포인트 2: _send_ev
posts.clear(); eqgui.App._send_ev(app, "preflight_fail", "NQ", broker="nt8", err="acct 50KTC-V2-123456: rejected 123456789", n=3)
ev = posts[-1][1]["ev"]
chk("_send_ev extra 문자열 마스킹·비문자열 유지", ev["err"] == "acct …3456: rejected #" and ev["n"] == 3 and ev["kind"] == "preflight_fail")
# 초크포인트 3: _report_error
posts.clear(); eqgui.App._report_error(app, "entry:NQ", "NQ 50KTC-V2-123456 timeout 987654 (3tries)")
sent = [p for p in posts if "eqerr" in p[0]]
chk("_report_error msg 마스킹", sent and "…3456" in json.dumps(sent[-1][1], ensure_ascii=False) and "50KTC-V2-123456" not in json.dumps(sent[-1][1]) and "987654" not in json.dumps(sent[-1][1]))
# 정적: 동의 문구·연결 감시 라벨
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
chk("동의 문구 범위 한정(KO/EN)", "이 요약에는 API 키, 계좌번호, 잔고가" in src and "This summary contains no API keys, account numbers" in src and "are never transmitted" not in src)
chk("연결 감시 라벨 끝 4자리", "[…{acct[-4:]}]" in src and "acct[-6:]" not in src)
chk("버전 23d 이상", eqgui.App._APP_VER >= "2026.09.23d")
# 앱 로그(화면·파일) 계좌 ID 끝 4자리(2026-09-28): 설정 계좌 + 설정 밖 계좌처럼 생긴 토큰, 가격·잔고·신호 ID는 그대로
L = lambda t: eqgui.App._mask_log(app, t)
chk("로그: 설정 계좌 → …끝4", L("[50KTC-V2-123456] 잔고 $152,340") == "[…3456] 잔고 $152,340")
chk("로그: 설정 밖 계좌(연결 테스트 나열)도 원문 없음", "26505604" not in L("계좌 PRAC-V2-26505604 / LFE0501726505604-01")
    and "…5604" in L("계좌 PRAC-V2-26505604"))
chk("로그: IBKR DU 계좌 → …끝4", "DU1234567" not in L("acct DU1234567 ok") and "…4567" in L("acct DU1234567 ok"))
chk("로그: 가격·잔고·수량·신호 ID·버전은 그대로", L("NQ 진입 21505.00 손절 21400 ×2 잔고 $2,009 id 2026-09-28-GC-1 v2026.09.23g")
    == "NQ 진입 21505.00 손절 21400 ×2 잔고 $2,009 id 2026-09-28-GC-1 v2026.09.23g")
chk("로그: 짧은 이름·None 안전", L("Sim101 U7") == "Sim101 U7" and L(None) == "")
_q = []
app.q = types.SimpleNamespace(put=_q.append)
_files = []
eqgui._log_to_file = lambda m: _files.append(m)
app._mask_log = lambda t: eqgui.App._mask_log(app, t)
eqgui.App.log(app, "계좌 50KTC-V2-123456 연결 OK")
chk("log(): 화면·파일 둘 다 마스킹", bool(_files) and _files[-1] == "계좌 …3456 연결 OK" and bool(_q)
    and "50KTC-V2-123456" not in _q[-1] and "…3456" in _q[-1])
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
