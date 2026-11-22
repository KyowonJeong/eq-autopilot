# EQ Autopilot 2026.10.21x/21y 오프라인 시험(2026-11-08, 대표 "옆에 진입시각 줄 수가 넘 많아", "NQ GC BTC로 짧게", "최대 24시간 보유 라고 써"). 0 = 통과.
# 왼쪽 메뉴 진입 시각 칸을 실제 Tk로 그려 본다: 머리 한 줄 + 자산마다 한 줄(이름 칸 NQ/GC/BTC, 시각 칸), '기준 …' 줄 없음, 말풍선에 기준 시각.
# 21y: 실제 칸 폭으로 재서 한 줄에 다 들어가면 이름 | 시각, 아니면 이름 아래 줄로(잘리지 않게) - 넓은 칸과 좁은 칸 둘 다 그려 본다.
# 2026.11.09a(대표 "진입 시각 부분 사각형 테두리 해 줘"): 칸 전체에 1px 사각 테두리(_SIDE_LINE), 안쪽 여백 6px - 폭 계산은 양쪽 (1 + 6)을 뺀다.
#   테두리 없이 쟀다면 한 줄로 판정했을 '경계 폭'도 그려서, 뺀 폭으로 판정하는지 본다.
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("EQ_NO_ALERT", "1")
import tkinter as tk
import tkinter.font as tkf
import eqgui as E
fails = []
def ck(name, ok, got=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  got={got!r}"))
    if not ok: fails.append(name)
ck("0 앱 버전 2026.10.21z 이상", E.App._APP_VER >= "2026.10.21z", E.App._APP_VER)
root = tk.Tk(); root.withdraw()
for ko in (True, False):
    lg = "ko" if ko else "en"
    lines = E._sched_lines(ko)
    ck(f"1 이름 = NQ, GC, BTC ({lg})", [l[0] for l in lines] == ["NQ", "GC", "BTC"], lines)
    btc = lines[2][1].split("\n")
    ck(f"2 BTC = 시각 한 줄 + 아래 줄 보유 ({lg})",
       len(btc) == 2 and btc[1] == ("최대 24시간 보유" if ko else "held up to 24 hours") and ":" in btc[0], btc)
    f = tkf.Font(family=E._UF, size=E._FS)
    need = max(f.measure(l[0]) for l in lines) + 8 + max(f.measure(s) for l in lines for s in l[1].split("\n"))
    INSET = 2 * (1 + 6)                                # 양쪽 (테두리 1 + 안쪽 여백 6)
    # 경계 폭: 칸 바깥 폭 = need + 11 → 테두리를 빼지 않으면 한 줄(need <= W - 4), 빼면 이름 아래 줄(need > W - 14 - 4)
    edge_w = need + 11 + 24
    for side_w, tag in ((200, "메뉴 폭 200"), (110, "좁은 칸 110"), (edge_w, f"경계 폭 {edge_w}")):
        side = tk.Frame(root, width=side_w, height=400); side.pack_propagate(False); side.pack(side="left", fill="y")
        box = tk.Frame(side); box.pack(fill="x", padx=12)
        tips = []
        me = types.SimpleNamespace(_sched_box=box, lang=lg, root=root, _daycap_tip=lambda w, g: tips.append((w, g())))
        E.App._sched_paint(me)
        outer = box.winfo_width() if box.winfo_width() > 60 else 176
        avail = outer - INSET                           # 글이 들어가는 폭
        want_fit = need <= avail - 4
        ck(f"10 [{tag}] 사각 테두리 1px, 색 _SIDE_LINE(바깥, 초점 같게), 안쪽 여백 6 ({lg})",
           int(str(box.cget("highlightthickness"))) == 1 and box.cget("highlightbackground") == E._SIDE_LINE == "#d1d5db"
           and box.cget("highlightcolor") == E._SIDE_LINE and int(str(box.cget("padx"))) == 6 and int(str(box.cget("pady"))) == 6
           and int(str(box.cget("bd"))) == 0, [box.cget(k) for k in ("highlightthickness", "highlightbackground", "highlightcolor", "padx", "pady", "bd")])
        ck(f"3 [{tag}] 맞춤 판정 = 실제 폭으로 잰 값 ({lg}, 필요 {need}px, 칸 {avail}px)", me._sched_fit == want_fit, (me._sched_fit, need, avail))
        if side_w == 110:
            ck(f"3b [{tag}] 좁으면 이름 아래 줄로 ({lg})", me._sched_fit is False, me._sched_fit)
        if side_w == edge_w:
            ck(f"3c [{tag}] 테두리, 여백을 뺀 폭으로 판정 - 빼지 않았다면 한 줄이었을 폭에서 이름 아래 줄 ({lg}, 칸 {outer}px)",
               outer == edge_w - 24 and need <= outer - 4 and me._sched_fit is False, (outer, need, me._sched_fit))
        kids = box.winfo_children()
        head = [w for w in kids if isinstance(w, tk.Label)]
        grids = [w for w in kids if isinstance(w, tk.Frame)]
        ck(f"4 [{tag}] 머리 한 줄 + 격자 하나 ({lg})", len(head) == 1 and len(grids) == 1
           and ("진입 시각" if ko else "Entry times") in head[0].cget("text"), [h.cget("text") for h in head])
        cells = grids[0].winfo_children() if grids else []
        got = {}
        for c in cells:
            gi = c.grid_info(); got[(int(gi["row"]), int(gi["column"]))] = c.cget("text")
        want = {}
        for i, (n, l, r) in enumerate(lines):
            if me._sched_fit:
                want[(i, 0)] = n; want[(i, 1)] = l
            else:
                want[(2 * i, 0)] = n; want[(2 * i + 1, 0)] = l
        ck(f"5 [{tag}] 칸 배치 ({lg}, {'한 줄' if me._sched_fit else '이름 아래 줄'})", got == want, got)
        alltxt = " ".join(c.cget("text") for c in cells)
        ck(f"6 [{tag}] 화면에 '기준' 글자 없음 ({lg})", ("기준 " not in alltxt) and ("ref. " not in alltxt), alltxt)
        tt = sorted(set(t for _, t in tips))
        want_t = sorted(set(("기준 " if ko else "ref. ") + r for _, _, r in lines))
        ck(f"7 [{tag}] 말풍선 = 기준 시각, 칸마다 ({lg})", tt == want_t and len(tips) == 2 * len(lines), (tt, len(tips)))
        if me._sched_fit:
            root.update_idletasks()
            over = [c.cget("text") for c in cells if c.winfo_x() + c.winfo_reqwidth() > avail + 1]
            ck(f"8 [{tag}] 한 줄 배치에서 넘치는 칸 없음 ({lg})", not over, over)
        root.update_idletasks()
        _in = all(w.winfo_x() >= 7 and w.winfo_x() + w.winfo_width() <= outer - 7 and w.winfo_y() >= 7 for w in kids)
        ck(f"11 [{tag}] 머리 줄, 격자가 테두리 안쪽 여백 안 ({lg})", _in, [(w.winfo_x(), w.winfo_width(), w.winfo_y()) for w in kids])
        E.App._sched_paint(me)
        ck(f"9 [{tag}] 다시 그려도 머리 1 + 격자 1 ({lg})", len(box.winfo_children()) == 2, len(box.winfo_children()))
        side.destroy()
root.destroy()
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "eqgui.py"), encoding="utf-8").read()
ck("12 앱이 만드는 진입 시각 칸도 같은 테두리 모양", "self._sched_box = tk.Frame(_side, bg=_SIDE_BG, **_SCHED_BOX_STYLE)" in src
   and E._SCHED_BOX_STYLE == {"highlightthickness": 1, "highlightbackground": "#d1d5db", "highlightcolor": "#d1d5db",
                              "bd": 0, "padx": 6, "pady": 6}, E._SCHED_BOX_STYLE)
print("FAIL", len(fails)) if fails else print("ALL PASS")
sys.exit(1 if fails else 0)
