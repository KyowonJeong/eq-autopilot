# EQ Autopilot ↔ NinjaTrader 8 브리지 (Lucid 자동 실행 경로)

Lucid Trading은 API 크레덴셜을 제공하지 않으므로(2026-07-29 확정), 자동 실행은
NinjaTrader 8 플랫폼을 통해서만 가능하다. 이 브리지는 A안 아키텍처를 구현한다:

```
[EQ Autopilot 앱 = 두뇌]  ←127.0.0.1:8377→  [NT8 애드온 = 씬 팔]  →  [Lucid 계좌]
 신호·사이징·손절 계산        localhost HTTP        주문 제출만            (NT 브로커 연결)
```

- 앱이 브리지 서버를 열고(`broker/nt8.py`), 애드온이 1초마다 붙는다.
- 판단은 전부 앱에 있고, 애드온은 명령 실행 + 상태 보고만 한다.
- **Windows 전용** (맥 미지원 확정). 앱과 NT8은 같은 머신에서 실행.

## 설치 (회원 안내용 초안)

1. NinjaTrader 8 설치 후 Lucid 계정 연결(Lucid가 안내하는 NT 브로커 커넥션).
2. `EQAutopilotBridge.cs`를 `문서\NinjaTrader 8\bin\Custom\AddOns\`에 복사.
3. NT8 → New → NinjaScript Editor → F5(컴파일). 오류 없이 컴파일되면 끝 —
   애드온은 NT8 시작 시 자동 기동된다(Output 창에 `[EQBridge] started` 확인).
4. `EQAutopilotBridge.cs` 상단 `Token` 값을 앱 설정의 `nt8.token`과 동일하게 맞춘다.
5. EQ Autopilot 앱에서 브로커 = NinjaTrader(Lucid) 선택 → 연결 테스트.

## 설정 (앱 쪽 config)

```yaml
broker: nt8
nt8:
  port: 8377
  token: "긴-랜덤-문자열"        # 애드온 Token과 동일
  accounts: ["Lucid150K-1", "Lucid150K-2"]   # NT8 계정 이름 그대로
  symbol_map:                    # EQ 계약 → NT 인스트루먼트(롤오버 시 여기만 갱신)
    MNQ: "MNQ 09-26"
    MGC: "MGC 10-26"
```

## 프로토콜 (버전 v1)

| 방향 | 엔드포인트 | 내용 |
|---|---|---|
| 애드온 → 앱 | `GET /v1/pending` | 대기 명령 목록 수령(수령 즉시 큐에서 제거) |
| 애드온 → 앱 | `POST /v1/ack` | `{tid, ok, order_id?, stop_order_id?, error?}` |
| 애드온 → 앱 | `POST /v1/state` | 계정·포지션·잔고 스냅샷(1초 주기 = 하트비트) |
| 앱 내부 | `GET /v1/ping` | 브리지 자체 생존 확인 |

명령 op: `entry`(시장가/지정가 + 손절 브래킷) · `stop` · `close`(종목 플래튼) ·
`flatten`(전 계좌 정리). 모든 요청은 `X-EQ-Bridge-Token` 헤더 필수.

## 안전 장치

- **fail-closed**: state 푸시가 5초 끊기면 앱이 신규 발주를 거부한다.
- **tid 멱등**: 같은 명령이 두 번 실행되지 않는다(앱 저널 + 애드온 tid 레지스트리).
- **알몸 포지션 불가**: entry 브래킷에서 스탑 제출이 거부되면 애드온이 진입분을
  즉시 플래튼한다(ProjectX placeoso와 동일 원칙).
- **로컬 전용**: 서버는 127.0.0.1 바인드 — 네트워크 노출 없음.
- dry_run 모드에서는 명령이 큐에 들어가지 않는다(would_place만 반환).

## 남은 일 (계정 개설 후, 2026-08-03 주)

- [ ] NT8 데모 계정에서 컴파일 + `Account.CreateOrder/Submit` 시그니처 검증 (VERIFY 주석 2곳)
- [ ] 시장가 체결가·시각이 앱 트랙레코드 파이프라인(가격 노출 금지 원칙)과 정합한지 확인
- [ ] Lucid 실계정 다계좌(락스텝 5계좌) 동시 발주 테스트 — 계좌별 1R 동일
- [ ] 앱 config UI에 nt8 브로커 항목 추가 + 연결 테스트 버튼 배선
- [ ] 토큰 자동 생성(수기 입력 제거) + 애드온 설정 파일 분리(cs 하드코딩 제거)
