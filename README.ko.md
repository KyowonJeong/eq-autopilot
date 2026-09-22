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
  평문 파일에 두지 않고, 이 저장소에도 없습니다.
- 키는 **브로커에게만** 갑니다. EdgeQuant로는 전송되지 않습니다.
  네트워크 호출을 직접 `grep`해서 확인해 보십시오.
- 앱이 저희에게 보내는 것은, 회원이 켰을 때만: 공개 기록 페이지용 체결 요약입니다.
  키도 잔고도 보내지 않습니다. `eqgui.py`의 `push_profile`을 보십시오.

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
