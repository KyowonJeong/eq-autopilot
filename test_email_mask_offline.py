#!/usr/bin/env python3
# 브로커 연결 이메일 칸 가림 시험(대표 2026-10-12 "첫 글자랑 @닷컴 빼고 별표"): 실제 Tk 위젯. 네트워크·알림 없음.
import os, sys, tempfile
HOME = tempfile.mkdtemp(prefix="eq_em_")
os.environ["HOME"] = HOME; os.environ["APPDATA"] = HOME; os.environ.pop("EQ_PROFILE", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui as G  # noqa: E402
import tkinter as tk  # noqa: E402
fails = []


def ck(n, c, d=""):
    print(("PASS  " if c else "FAIL  ") + n + ("" if c else f"  [{str(d)[:200]}]"))
    if not c:
        fails.append(n)


ck("가림 모양", G._mask_email("kyowon@gmail.com") == "k*****@gmail.com", G._mask_email("kyowon@gmail.com"))
ck("짧은 이름도 별표 3개 이상", G._mask_email("ab@x.io") == "a***@x.io", G._mask_email("ab@x.io"))
root = tk.Tk(); root.withdraw()
other = tk.Entry(root); other.pack()
e = G._MaskEmailEntry(root); e.pack()
e.insert(0, "kyowon@gmail.com")
e._apply_mask(); root.update()
shown = G.ttk.Entry.get(e)
ck("칸 밖이면 가려 보임", shown == "k*****@gmail.com", shown)
ck("get()은 원문", e.get() == "kyowon@gmail.com", e.get())
e._unmask()
ck("누르면(포커스) 원문 표시", G.ttk.Entry.get(e) == "kyowon@gmail.com")
e._apply_mask()
e.delete(0, "end"); e.insert(0, "new@topstep.com"); e._apply_mask(); root.update()
ck("다시 넣은 값도 원문 저장·가림 표시", e.get() == "new@topstep.com" and G.ttk.Entry.get(e) == "n***@topstep.com",
   (e.get(), G.ttk.Entry.get(e)))
e.delete(0, "end"); e.insert(0, "trader_12345"); e._apply_mask(); root.update()
ck("이메일 아닌 아이디는 그대로", G.ttk.Entry.get(e) == "trader_12345" and e.get() == "trader_12345")
e.delete(0, "end"); e.insert(0, "a@b.com"); root.update()
G._entry_set_text(e, "x@y.com"); root.update()
ck("_entry_set_text 경로도 원문 유지", e.get() == "x@y.com", e.get())
e.delete(0, "end"); e.insert(0, "kyowon@gmail.com"); e._apply_mask(); root.update()
e.tk.call(e._w, "delete", 0, "end"); e.tk.call(e._w, "insert", 0, "typed@new.com")   # Tk 쪽 직접 편집(클래스 바인딩 경로)
ck("가린 칸을 Tk가 직접 바꾸면 화면 값이 진짜 값", e.get() == "typed@new.com" and e._real is None, e.get())
e.delete(0, "end"); e.insert(0, "kyowon@gmail.com"); e.selection_range(0, "end"); e._apply_mask(); root.update()
ck("가린 뒤에도(전체 선택이던 칸)", G.ttk.Entry.get(e) == "k*****@gmail.com")
e._unmask()
ck("되돌리면 전체 선택 복원(리뷰 P2-A: 창 다녀온 뒤 붙여넣기)", e.selection_present() and e.index("sel.first") == 0
   and e.index("sel.last") == e.index("end"))
e.delete("sel.first", "sel.last"); e.insert("insert", "new@topstep.com")
ck("선택 복원 뒤 붙여넣기 = 통째로 바뀜", e.get() == "new@topstep.com", e.get())
ck("<<TraverseIn>> 바인딩 있음", "_unmask" in str(e.bind("<<TraverseIn>>")) or bool(e.bind("<<TraverseIn>>")))
print(("실패 %d: %s" % (len(fails), fails)) if fails else "전부 통과")
sys.exit(1 if fails else 0)
