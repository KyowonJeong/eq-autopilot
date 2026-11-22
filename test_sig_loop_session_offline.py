#!/usr/bin/env python3
# 2026-10-21 08:00 금 사고 재발 방지: _sig_loop 안에서 피드 조회 세션 이름(_sess)을 다른 값으로 다시 묶지 않는다.
# 종전 '_sess = sig.get("session")'이 세션을 None으로 덮어 신호 하나를 처리한 뒤 모든 폴이 'NoneType … get'으로 실패했다. 0 = 통과.
import ast, inspect, os, sys, tempfile
os.environ["HOME"] = os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eq_sess_")
os.environ["EQ_NO_ALERT"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui as E  # noqa: E402
FAIL = []
def ck(n, c, d=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else f" - {d}")); (None if c else FAIL.append(n))
src = inspect.getsource(E.App._sig_loop_body)
tree = ast.parse("class _X:\n" + "\n".join("    " + l for l in src.splitlines()))
names = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.setdefault(t.id, []).append(ast.unparse(node.value)[:60])
ck("1 _sig_loop에서 _sess는 한 번만 묶인다(requests.Session())", names.get("_sess") == ["requests.Session()"], names.get("_sess"))
ck("2 신호의 세션 값은 _sig_sess", "_sig_sess" in names, list(names)[:40])
# 다른 긴 루프도 세션 변수를 한 번만 묶는지(같은 실수 방지)
for fn in ("_sig_loop",) + tuple(n for n in dir(E.App) if n.endswith("_loop") and n != "_sig_loop"):
    try:
        s2 = inspect.getsource(getattr(E.App, fn))
    except Exception:
        continue
    t2 = ast.parse("class _X:\n" + "\n".join("    " + l for l in s2.splitlines()))
    sess_like = {}
    for node in ast.walk(t2):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and "Session()" in ast.unparse(node.value):
                    sess_like[t.id] = 0
    for node in ast.walk(t2):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in sess_like:
                    sess_like[t.id] += 1
    ck(f"3 {fn}: 세션 변수 재대입 없음", all(v == 1 for v in sess_like.values()), sess_like)
print("FAIL:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
