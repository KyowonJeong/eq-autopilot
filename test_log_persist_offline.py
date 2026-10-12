#!/usr/bin/env python3
# 화면 로그 보존 시험(대표 2026-10-12 "로그 창 지속되게"): 실제 eqgui.App을 실제 Tk로 띄우고, 로그를 찍은 뒤
# _build(자산 탭·언어·브로커 전환 때 부르는 화면 재생성)를 불러도 지난 줄이 새 로그 창에 그대로 있는지.
# HOME = 임시 폴더, 네트워크 차단, 스레드 실행 안 함, 지연 타이머 버림. 알림 0.
import os, sys, tempfile
HOME = tempfile.mkdtemp(prefix="eq_logp_")
os.environ["HOME"] = HOME
os.environ["APPDATA"] = HOME
os.environ["EQ_NO_ALERT"] = "1"
os.environ.pop("EQ_PROFILE", None)
import socket  # noqa: E402


def _blk(*a, **k):
    raise OSError("network blocked by test")


socket.socket.connect = _blk
socket.socket.connect_ex = _blk
socket.create_connection = _blk
socket.getaddrinfo = _blk
import threading  # noqa: E402


class FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None, **k):
        self.name = name or "fake"

    def start(self):
        pass

    def join(self, *a, **k):
        pass

    def is_alive(self):
        return False


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui  # noqa: E402
threading.Thread = FakeThread
import tkinter as tk  # noqa: E402
assert eqgui.CFG_PATH.startswith(HOME), eqgui.CFG_PATH
for _f in ("askyesno", "showinfo", "showwarning", "showerror"):
    setattr(eqgui.messagebox, _f, lambda *a, **k: True)
ROOT = tk.Tk()
ROOT.withdraw()
_orig_after = ROOT.after
ROOT.after = lambda ms, func=None, *a: (_orig_after(ms) if func is None else
                                        (_orig_after(0, func, *a) if ms in (0, "idle") else "dropped"))
_oc = ROOT.after_cancel
ROOT.after_cancel = lambda i: None if i == "dropped" else _oc(i)

fails = []


def ck(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:300]}]"))
    if not c:
        fails.append(n)


app = eqgui.App(ROOT)
for _ in range(3):
    ROOT.update()
app.log("시험 줄 A - 22:00 진입")
app.log("시험 줄 B - 손절 → 83487.1 이동 완료")
app._drain()
txt0 = app.out.get("1.0", "end")
ck("찍은 줄이 화면에", "시험 줄 A" in txt0 and "시험 줄 B" in txt0, txt0[-300:])
old = app.out
app._build()
for _ in range(3):
    ROOT.update()
ck("재빌드로 로그 창이 새로 생김(시험 전제)", app.out is not old)
txt1 = app.out.get("1.0", "end")
ck("재빌드 뒤에도 지난 줄 그대로", "시험 줄 A" in txt1 and "시험 줄 B" in txt1, txt1[-300:])
ck("순서 유지", txt1.find("시험 줄 A") < txt1.find("시험 줄 B"))
ck("중복 없음", txt1.count("시험 줄 B") == 1, txt1.count("시험 줄 B"))
app.log("시험 줄 C - 재빌드 뒤")
app._drain()
app._build()
for _ in range(3):
    ROOT.update()
txt2 = app.out.get("1.0", "end")
ck("두 번째 재빌드 뒤 A·B·C 모두", all(x in txt2 for x in ("시험 줄 A", "시험 줄 B", "시험 줄 C")) and txt2.count("시험 줄 C") == 1, txt2[-300:])
logs = os.listdir(eqgui.LOG_DIR)
ck("파일 로그는 그대로 기록", any("시험 줄 B" in open(os.path.join(eqgui.LOG_DIR, f), encoding="utf-8").read() for f in logs), logs)
ck("보존 줄 상한 5000", app._log_ring.maxlen == 5000)
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
