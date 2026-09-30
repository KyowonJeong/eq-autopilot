#!/usr/bin/env python3
# EQ Autopilot - 시연 프로필·화면 공유 모드 오프라인 테스트(대표 2026-09-28 설명회 화면 공유)
# ① 기본 실행: 설정 폴더 EQAutopilot·보안 저장소 서비스 EQAutopilot 그대로(기존 회원 설정·키 불변)
# ② --profile demo: 폴더 EQAutopilot-demo, 서비스 EQAutopilot-demo(실계좌 키를 못 읽음), 공유 모드 기본 켬
# ③ EQ_PROFILE 환경변수도 같은 효과, 이상한 문자는 걸러짐 ④ 공유 모드 로그: 금액·bal·잔고 가림, 가격·계좌 끝4 규칙 유지
# HOME을 임시 폴더로 돌려 실제 설정 폴더를 만들지 않는다. 사용: python executor/test_profile_share_offline.py
import os, subprocess, sys, tempfile, json
HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def chk(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{d}]"))
    if not c:
        fails.append(n)


PROBE = r'''
import sys, json, os
sys.path.insert(0, %r)
import eqgui
out = {"dir": os.path.basename(eqgui.APP_DIR), "kc": eqgui.KC_SERVICE, "share": eqgui.SHARE_MODE, "prof": eqgui.PROFILE}
class _S: pass
app = _S(); app._acfg = {"NQ": {"accounts": [{"id": "PRAC12345678"}]}}
out["mask"] = eqgui.App._mask_log(app, "   • name=x  id=PRAC12345678  bal=51234.5  잔고 $52,310 1R $210 entry 30651.75")
print("JSON" + json.dumps(out, ensure_ascii=False))
'''


def run(args=(), env_extra=None):
    home = tempfile.mkdtemp(prefix="eqprof_")
    env = {**os.environ, "HOME": home, "APPDATA": home, "XDG_CONFIG_HOME": home}
    env.pop("EQ_PROFILE", None); env.pop("EQ_SHARE", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "-c", PROBE % HERE, *args], capture_output=True, text=True, env=env, timeout=120)
    line = next((l for l in r.stdout.splitlines() if l.startswith("JSON")), None)
    return json.loads(line[4:]) if line else {"err": r.stderr[-400:]}


a = run()
chk("① 기본: 폴더 EQAutopilot", a.get("dir") == "EQAutopilot", a)
chk("① 기본: 서비스 EQAutopilot(기존 키 그대로)", a.get("kc") == "EQAutopilot", a)
chk("① 기본: 공유 모드 꺼짐·금액 보임", a.get("share") is False and "$52,310" in a.get("mask", ""), a.get("mask"))
b = run(["--profile", "demo"])
chk("② --profile demo: 폴더 분리", b.get("dir") == "EQAutopilot-demo", b)
chk("② --profile demo: 보안 저장소 서비스 분리", b.get("kc") == "EQAutopilot-demo", b)
m = b.get("mask", "")
chk("② 공유 모드 기본 켬: 금액·bal·잔고 가림", b.get("share") is True and "$52,310" not in m and "$210" not in m
    and "51234" not in m and "잔고 $•••" in m, m)
chk("② 가격은 그대로, 계좌는 끝4", "30651.75" in m and "…5678" in m and "PRAC12345678" not in m, m)
c = run(env_extra={"EQ_PROFILE": "de mo!/.."})
chk("③ EQ_PROFILE + 문자 거름", c.get("dir") == "EQAutopilot-demo" and c.get("kc") == "EQAutopilot-demo", c)
d = run(["--share"])
chk("④ --share만: 폴더·서비스 그대로, 금액 가림", d.get("dir") == "EQAutopilot" and d.get("kc") == "EQAutopilot"
    and "$52,310" not in d.get("mask", ""), d)
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
