# 월물 규칙 R(2026-11-02 대표 "다 적용해"): 신호에 실린 월물(contract_ym)로만 주문, 브로커에 없으면 건너뜀.
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eqgui
from eqexec.broker import nt8, tradovate, projectx, ibkr

FAIL = []
def ck(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (f" - {extra}" if not ok and extra != "" else ""))
    if not ok:
        FAIL.append(name)

ck("1 신호 월물 읽기", eqgui._sig_contract({"contract_ym": "2026-12", "contract_code": "Z26"}) == (2026, 12))
ck("2 월물 칸 없음/깨짐 = None", eqgui._sig_contract({}) is None and eqgui._sig_contract({"contract_ym": "202612"}) is None
   and eqgui._sig_contract({"contract_ym": "2026-13"}) is None and eqgui._sig_contract(None) is None)

n = nt8.NT8Broker.__new__(nt8.NT8Broker)
ck("3 NT8 표기", n.contract_for_month("MGC", 2026, 12) == "MGC 12-26" and n.contract_for_month("NQ", 2027, 3) == "NQ 03-27")

t = tradovate.TradovateBroker.__new__(tradovate.TradovateBroker)
t._contract_ids, t._contract_cache = {"MNQZ6": 111}, {}
t._get = lambda path, **kw: None
ck("4 Tradovate 있는 월물", t.contract_for_month("MNQ", 2026, 12) == "MNQZ6")
ck("5 Tradovate 없는 월물 = None", t.contract_for_month("MGC", 2027, 2) is None)

p = projectx.ProjectXBroker.__new__(projectx.ProjectXBroker) if hasattr(projectx, "ProjectXBroker") else None
if p is None:
    cls = [v for v in vars(projectx).values() if isinstance(v, type) and hasattr(v, "contract_for_month") and v.__module__ == projectx.__name__][0]
    p = cls.__new__(cls)
_calls = []
# TopstepX는 활성 월물만 거래(2026-11-05 확인). 검색이 활성 아닌 월물을 돌려줘도(activeContract False) 고르지 않는다.
p.search_contracts = lambda text, live=False, max_age=None: [{"id": "CON.F.US.MGC.G27", "activeContract": True},
                                                              {"id": "CON.F.US.MGC.Z26", "activeContract": False}]
def _post(path, body, **kw):
    _calls.append((path, body))
    if body.get("contractId") == "CON.F.US.ENQ.Z26":
        return {"contract": {"id": "CON.F.US.ENQ.Z26", "activeContract": True}}
    if body.get("contractId") == "CON.F.US.MGC.Z26":
        return {"contract": {"id": "CON.F.US.MGC.Z26", "activeContract": False}}
    if body.get("contractId") == "CON.F.US.MGC.H27":
        return {"contract": {"id": "CON.F.US.MGC.H27"}}             # activeContract 칸 없음 = 종전대로 쓴다
    return {"contract": None}
p._post = _post
ck("6 ProjectX 활성이 G27이고 신호가 Z26이면 None + inactive(주문 안 보냄)",
   p.contract_for_month("MGC", 2026, 12) is None and p.last_month_miss == "inactive", p.last_month_miss)
ck("6b 신호 월물이 활성이면 그 월물", p.contract_for_month("MGC", 2027, 2) == "CON.F.US.MGC.G27" and p.last_month_miss is None)
ck("7 ProjectX 검색에 없으면 id로 직접(NQ = ENQ, 활성)", p.contract_for_month("NQ", 2026, 12) == "CON.F.US.ENQ.Z26", _calls)
ck("7b id로 찾았는데 활성 칸이 없으면 종전대로 그 월물", p.contract_for_month("MGC", 2027, 3) == "CON.F.US.MGC.H27")
ck("8 ProjectX 어디에도 없으면 None + missing", p.contract_for_month("MGC", 2027, 4) is None and p.last_month_miss == "missing")
p.search_contracts = lambda text, live=False, max_age=None: [{"id": "CON.F.US.MGC.G27", "activeContract": True}]
ck("8b 검색엔 활성만, id 조회로 비활성 Z26 = None + inactive(11/25 실제 모양)",
   p.contract_for_month("MGC", 2026, 12) is None and p.last_month_miss == "inactive")

ib = ibkr.IBKRBroker.__new__(ibkr.IBKRBroker) if hasattr(ibkr, "IBKRBroker") else None
if ib is None:
    cls = [v for v in vars(ibkr).values() if isinstance(v, type) and hasattr(v, "contract_for_month") and v.__module__ == ibkr.__name__][0]
    ib = cls.__new__(cls)
C = lambda sym, ltd: types.SimpleNamespace(symbol=sym, tradingClass=sym, lastTradeDateOrContractMonth=ltd)
ib._contracts = {"MNQZ6": C("MNQ", "20261218"), "MNQH7": C("MNQ", "20270319"), "MGCZ6": C("MGC", "20261229")}
ib.search_contracts = lambda r, live=False: []
ck("9 IBKR 계약월로 localSymbol", ib.contract_for_month("MNQ", 2026, 12) == "MNQZ6" and ib.contract_for_month("MGC", 2026, 12) == "MGCZ6")
ck("10 IBKR 없으면 None", ib.contract_for_month("MGC", 2027, 2) is None)

class _B:
    def __init__(self): self.calls = []
    def search_contracts(self, s, max_age=None): return [{"id": "ACTIVE", "activeContract": True}]
    def contract_for_month(self, root, y, m, max_age=None): self.calls.append((root, y, m)); return None
fb = _B()
app = types.SimpleNamespace()
ck("11 월물 없으면 종전대로 활성", eqgui.App._resolve_contract(app, fb, "MNQ") == "ACTIVE")
ck("12 월물 있으면 그 월물만(없으면 None, 활성으로 바꾸지 않음)",
   eqgui.App._resolve_contract(app, fb, "MNQ", ym=(2026, 12)) is None and fb.calls == [("MNQ", 2026, 12)])
import inspect
_src = inspect.getsource(eqgui.App._run_futures_entry)
ck("13 진입 경로가 신호 월물을 넘김 + 못 찾으면 회원 DM(roll_miss)",
   "_ym = _sig_contract(sig)" in _src and '{"ym": _ym} if _ym else {}' in _src and '"roll_miss"' in _src)
ck("14 활성 아닌 월물 = 오류 보고 없이 건너뜀 사유 roll_inactive + 회원 DM roll_inactive",
   'getattr(b, "last_month_miss", None) == "inactive"' in _src and '_entry_skip_ev(asset, "roll_inactive")' in _src
   and '"roll_inactive",' in _src
   and _src.index('_entry_skip_ev(asset, "roll_inactive")') < _src.index('if not resolved:\n            self.log("   ❌ 유효 계약 없음'))
print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
