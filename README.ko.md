# EQ Autopilot

EdgeQuant 신호를 회원 본인 브로커 계좌에서 실행하는 데스크톱 앱입니다.
**저희가 배포하는 앱의 전체 소스**이며, 화면에 적힌 대로 프로그램이 동작하는지
직접 확인하시라고 공개합니다.

---

## 왜 공개하는가

거래 앱은 요구하는 신뢰가 큽니다. 브로커 자격증명을 들고 실제 주문을 냅니다.
"저희를 믿으세요"는 답이 될 수 없어서, 대신 소스를 둡니다.

이 저장소로 하실 수 있는 것:

- 키와 주문에 닿는 **모든 줄을 읽기**
- 직접 **빌드해서** 저희 배포본 대신 그 빌드를 쓰기
- 받으신 실행파일이 이 소스에서 나왔는지 **검증하기**

## 이미 받으신 파일 검증하기

배포마다 SHA-256 해시를 다운로드 화면에 같이 싣습니다. 직접 비교해 보십시오.

```bash
# macOS
shasum -a 256 EQ-Autopilot-macOS.zip
```
```powershell
# Windows
Get-FileHash .\EQ-Autopilot-Windows.zip -Algorithm SHA256
```

버튼 옆 값과 같으면, 받으신 파일이 저희가 게시한 그 파일입니다.
앱 화면에서도 두 값을 나란히 보여 드립니다.

## 무엇이 있고, 무엇이 없는가

**있는 것** — 회원 기기에서 도는 전부:

| | |
|---|---|
| `eqgui.py` | 앱 본체: 화면, 신호 처리, 주문 발주, 손절 관리 |
| `eqexec/` | 사이징, 스케줄, 동의, 브로커 어댑터 |
| `eqexec/broker/` | Topstep(ProjectX), Tradovate, Interactive Brokers, Bybit, Bitget, NinjaTrader |
| `nt8_addon/` | API 없는 브로커용 NinjaTrader 8 애드온 |
| `test_*.py` | 오프라인 테스트(손절 보호 계층 포함) |

**없는 것** — 신호를 만드는 방법론. 앱은 방향과 손절가를 **받을 뿐** 계산하지 않습니다.
피처·모델·리서치는 공개하지 않습니다. 이 저장소가 다루는 것은 **실행**입니다 —
신호가 도착한 뒤 계좌에서 무슨 일이 일어나는가.

## 자격증명은 어떻게 다루는가

- 브로커 키는 운영체제의 보안 저장소에 둡니다(macOS 키체인, Windows 자격 증명 관리자).
  이 저장소에는 없습니다. 앱은 키를 보안 저장소에 쓰고, 되읽어 확인한 뒤에만 설정 파일에서
  지웁니다(`eqgui.py`의 `_save_full` / `_kc_save`). 보안 저장소를 쓸 수 없는 기기(잠긴 키체인,
  헤드리스 VM, keyring 백엔드 없음)에서도 키를 평문으로 쓰지 않습니다: PIN을 물어 PIN 파생 키
  (PBKDF2-HMAC-SHA256 120만 회, AES-256-GCM)로 암호화해 설정 파일에 두고, 매 기동 때 그 PIN을
  한 번 묻습니다(`_enc_ask_pin` / `_enc_restore`). PIN 입력을 취소하면 키를 아예 저장하지 않고
  다시 입력하라고 안내합니다.
- 신호와 하트비트 파일은 회원별로 AES-256-GCM으로 암호화합니다(키는 회원 토큰에서 파생,
  `autopilot_crypto.py`). 2026.09.23b 이전 빌드는 HMAC 인증 스트림 암호를 썼고, 서버는 그
  빌드에는 계속 그 형식으로 보내며 이 빌드는 둘 다 읽습니다.
- 키는 **브로커에게만** 갑니다. EdgeQuant로는 전송되지 않습니다.
  네트워크 호출을 직접 `grep`해서 확인해 보십시오.
- 앱이 EdgeQuant(`app.edgequant.app`)로 보내는 것은 아래가 전부입니다. 무엇이 기기 밖으로
  나가는지 직접 확인하실 수 있게 적습니다(전부 `eqgui.py`).
  - 암호화된 신호·상태 파일을 받아 올 때: 파일 이름에 들어가는 토큰 해시(`_heartbeat`,
    `_sig_loop`)
  - 앱이 켜져 있는 동안의 작동 신호: 멤버십 토큰, 앱 버전, 무작위 기기 ID, 무장한 자산,
    자산별 브로커 이름, 동의한 버전과 시각, 그리고 진입 전 점검 실패 같은 상태 알림과 그 오류
    문구(`_alive_ping`, `_send_ev`)
  - 자동 진입마다: 자산, 총 수량, 참여 계좌 수, 기준가 대비 체결가(`_send_fill`, `_send_gap`),
    그리고 두 기기가 같은 계좌로 두 번 진입하지 않도록 계좌 이름을 되돌릴 수 없게 바꾼 값
    (해시, `_claim_entry`)
  - Autopilot 회원: 하루 한 번, 결과를 R 단위로 요약한 암호화 기록(금액 없음, `push_profile`)
  - 오류 보고: 앱 버전, 운영체제 이름, 긴 숫자를 가린 오류 문구(`_report_error`,
    `EQ_ERR_REPORT=0`으로 끌 수 있음)
  - 회원님께 가는 알림(진입 누락, 브로커 연결 끊김 등): 텔레그램이나 디스코드로 보내려고
    저희 서버를 거칩니다(`_member_alert`)
  - PIN 재설정을 요청하실 때만: 토큰과 네 자리 코드(`/eqpin`)
  - 시세 백업용 5분봉: 운영자 본인 기기에서만 서버로 보냅니다. 회원 설치본은 이 봉 데이터를
    보내지 않습니다(`_bar_backup_tick`은 등급이 `admin`이 아니면 바로 돌아가고, 서버도 오너
    토큰의 봉만 저장합니다)

  오류 문구, 알림, 상태 알림에서 앱은 설정한 계좌 ID를 끝 4자리만 남기고 긴 숫자를
  가립니다. 브로커가 돌려준 메시지에는 다른 식별자가 섞일 수 있습니다. API 키, 비밀번호,
  PIN은 어디에도 들어가지 않습니다.

## 직접 빌드하기

Python **3.9**가 필요합니다(GUI가 Tk 8.6을 씁니다. 최신 파이썬은 Tk 9라 창이 빈 채로 뜹니다).

```bash
python3.9 -m venv venv
./venv/bin/pip install -r requirements.txt pyinstaller
./venv/bin/python -m PyInstaller --clean --noconfirm "EQ Autopilot.spec"     # macOS
./venv/bin/python -m PyInstaller --clean --noconfirm "EQ Autopilot Windows.spec"   # Windows
```

오프라인 테스트는 브로커 연결도 키도 없이 돕니다:

```bash
./venv/bin/python test_stop_guard_offline.py
./venv/bin/python test_build_guards_offline.py
./venv/bin/python test_portfolio_weights_offline.py
```

## 안전 설계

- **손절은 브로커에 걸립니다.** 앱이 들고 있지 않습니다. 앱이 꺼져도 손절은 남습니다.
- **수동이 우선입니다.** 손으로 손절을 옮기시면 앱은 그 손절을 더 건드리지 않습니다.
  조이지도, 되돌리지도 않습니다.
- **보호 손절이 없으면 포지션도 없습니다.** 진입 후 손절 주문이 거부되면 무방비로 두지 않고
  즉시 청산합니다.
- **청산은 계좌 전체를 정리합니다.** 전용 계좌를 쓰시고 다른 거래를 섞지 마십시오.

## 보안 문제 제보

공개 이슈 대신 사이트에 적힌 주소로 메일 주시고, 공개 전에 수정을 배포할 시간을 주십시오.

## 라이선스

`LICENSE` 파일을 보십시오.
