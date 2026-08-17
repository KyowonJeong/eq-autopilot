# EdgeQuant — Author: Kyowon Jeong — Started: 2026-04-13
# =========================
# tradovate_probe.py — Tradovate 어댑터 실사 프로브 (대표 수동 실행 전용, 2026-08-17)
#
# $1,000 입금 + API Access 애드온 + 키 발급 후, 어댑터를 **체결 위험 0부터** 단계로 실증한다.
# 앱과 무관한 단독 스크립트라 여기서 낸 주문·체결은 EQ 원장에 안 적혀 공개 트랙레코드에
# 집계되지 않는다(트랙레코드 필터 = 앱 원장).
#
#   A) 인증 + 계좌·잔고 조회 (읽기 검증 — 항상 실행)
#   B) 시장가에서 5% 떨어진 지정가 1계약 → 즉시 취소 (체결 위험 0, 주문 권한 실증)
#   C) --fill : MGC 1계약 시장가 진입(+보호손절 브래킷) → 3초 뒤 시장가 청산
#      (실체결 왕복, 비용 ≈ 스프레드+수수료 몇 달러. 명시적으로 켤 때만)
#
# 사용 (executor 디렉토리에서):
#   .buildvenv/bin/python tradovate_probe.py                → A+B
#   .buildvenv/bin/python tradovate_probe.py --fill        → A+B+C
#   .buildvenv/bin/python tradovate_probe.py --demo        → 데모 서버 대상
#
# 자격 증명은 프롬프트로만 받는다(비밀번호·sec는 에코 없음). 어디에도 저장·출력하지 않는다.
# =========================
import argparse
import getpass
import sys
import time

from eqexec.broker.tradovate import TradovateBroker
from eqexec.config import TradovateCfg

PROBE_CONTRACT = "MGC"       # 젤 작은 단위: 마이크로 골드 1계약 (MNQ보다 1계약 리스크 작음)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill", action="store_true", help="C단계(실체결 왕복)까지 실행")
    ap.add_argument("--demo", action="store_true", help="데모 서버 대상")
    args = ap.parse_args()

    print("Tradovate 실사 프로브 — 자격 증명은 저장되지 않습니다.")
    name = input("  Username: ").strip()
    password = getpass.getpass("  API dedicated password (에코 없음): ")
    cid = input("  API cid: ").strip()
    sec = getpass.getpass("  API sec (에코 없음): ")
    cfg = TradovateCfg(env=("demo" if args.demo else "live"),
                       name=name, password=password, cid=cid, sec=sec)
    b = TradovateBroker(cfg)

    # ── A) 읽기 검증 ──────────────────────────────────────────────────────
    print("\n[A] 인증...")
    b.authenticate()
    print("    ✅ 토큰 발급 OK")
    accts = b._accounts()
    if not accts:
        print("    🛑 계좌가 0개 — Account Information 권한이 Denied인지 확인"); sys.exit(1)
    for a in accts:
        bal = b.account_balance(a.get("id"))
        print(f"    계좌 {a.get('name')} (id {a.get('id')}) · 잔고 {bal}")
    acct = accts[0]
    aid = acct.get("id")

    px = b.current_market_price(PROBE_CONTRACT)
    print(f"    {PROBE_CONTRACT} 현재가 {px}")
    if px is None:
        print("    ⚠ 시세 조회 실패 — Contract Library/Market Data 권한 또는 시세 구독 확인.")
        print("      (B단계는 가격 없이 진행 불가, 여기서 중단)"); sys.exit(1)

    # ── B) 지정가 → 즉시 취소 (체결 위험 0) ──────────────────────────────
    far = round(px * 0.95 / 0.1) * 0.1          # 5% 아래, MGC 틱(0.1) 정렬
    print(f"\n[B] 체결 위험 0 테스트 — {PROBE_CONTRACT} 1계약 지정가 매수 @{far:.1f} (시장가 -5%)")
    r = b.place_limit_entry(aid, PROBE_CONTRACT, "LONG", 1, far, dry_run=False)
    oid = (r or {}).get("orderId") or (r or {}).get("order_id")
    if not oid:
        print(f"    🛑 주문 거부: {r} — Orders 권한이 Full Access인지 확인"); sys.exit(1)
    print(f"    ✅ 접수 orderId={oid} → 즉시 취소...")
    ok = b.cancel_order(aid, oid)
    print("    ✅ 취소 확인" if ok else "    🛑 취소 실패 — Tradovate 화면에서 미체결 주문을 직접 취소하세요!")
    if not ok:
        sys.exit(1)
    print("    → 인증·주문·취소 권한 전부 실증 완료 (체결 0, 비용 $0)")

    # ── C) 실체결 왕복 (명시적으로 켤 때만) ──────────────────────────────
    if not args.fill:
        print("\n[C] 건너뜀 (--fill 플래그로 실행). 여기까지로 API 권한 실사는 끝났습니다.")
        return
    stop = round((px - px * 0.005) / 0.1) * 0.1   # 0.5% 아래 보호손절 (틱 정렬)
    print(f"\n[C] 실체결 왕복 — {PROBE_CONTRACT} 1계약 시장가 매수 + 보호손절 @{stop:.1f}")
    print("    5초 안에 Ctrl-C로 중단할 수 있습니다...")
    time.sleep(5)
    r = b.place_entry(aid, PROBE_CONTRACT, "LONG", 1,
                      stop_loss_price=stop, custom_tag=None, dry_run=False)
    print(f"    진입 응답: {r}")
    time.sleep(3)
    r2 = b.close_contract(aid, PROBE_CONTRACT)
    print(f"    청산 응답: {r2}")
    q = b.position_qty(aid, PROBE_CONTRACT)
    print(f"    잔여 포지션: {q}")
    if q not in (0, None):
        print("    🛑 포지션이 남았습니다 — Tradovate 화면에서 직접 청산하세요!")
        sys.exit(1)
    print("    ✅ 왕복 완료 — 진입·브래킷 손절·청산 전 경로 실증")


if __name__ == "__main__":
    main()
