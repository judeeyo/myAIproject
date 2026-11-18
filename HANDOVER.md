# KTX 자동 예매 인수인계

> 마지막 정리: 2026-11-18 · 프로젝트 개요는 [README.md](README.md) 참고

## 1. 바로 쓰려면

| 받을 파일 | 내용 |
|---|---|
| [`downloads/ktx-reserve-final.zip`](downloads/ktx-reserve-final.zip) | 맥·윈도우 겸용 프로그램 + `사용설명서.txt` |
| [`downloads/KTX_WINDOWS_FIXED.zip`](downloads/KTX_WINDOWS_FIXED.zip) | 윈도우용 (영문 파일명, 설명서 `MANUAL.txt`) |
| [`ktx_reserve/사용설명서.txt`](ktx_reserve/사용설명서.txt) | 설치·실행 설명서 (v1.7) |
| [`old/`](old/README.md) | 이전 설명서·배포본 (기록용) |

- **가장 쉬운 방법**: `git clone https://github.com/judeeyo/myAIproject.git` 후 맥은 `start.command`, 윈도우는 `start.bat` 더블클릭
  (처음엔 자동 설치 → 설정 화면이 브라우저에 바로 열림)
- ZIP으로 받았다면 압축을 풀고 같은 파일(`start.command` / `start.bat`)을 더블클릭
- 화면 사용법은 [사용설명서](ktx_reserve/사용설명서.txt) 참고
- 필요한 것: Python 3.11 이상, Git

### `start_booking` 파일로 실행할 경우 (화면 없이)

`start_booking.sh` / `start_booking.bat` 에 **2026-11-06 일정과 자동 결제**가 들어 있습니다.

```
--dep 서울 --arr 동대구 --date 2026-11-06 --from 13:49 --to 13:58 --adult 2 --seat general-only --pay
```

- 메모장으로 열어 `--date`, `--from`, `--to`를 새 일정으로 바꾸세요.
- 자동 결제를 원하지 않으면 `--pay`를 지우세요. 그러면 예약만 하고, 결제는 코레일톡 앱에서 직접 합니다.
- 먼저 `--check`로 실제 서버 조회가 되는지 시험할 수 있습니다. 로그인·예약은 하지 않습니다.
  ```
  bash run.sh --check --dep 서울 --arr 동대구 --date 2026-12-01 --from 13:00 --to 15:00 --adult 2
  ```

## 2. 지금까지의 경과

1. **기존 ktx-auto (ShinbaramEbaksa/ktx-auto) — 사용 불가**
   - 2026-10-28 코레일톡 앱이 7.1.0으로 업데이트된 뒤, 조회 시 `-8204`("최신 버전으로 업데이트해 주세요") 오류로 거절됨.
   - 버전 숫자만 바꿔서는 안 됨. 보안 토큰(DynaPath) 방식까지 바뀌어서임.
2. **pykorail 기반 스크립트로 교체 (2026-11-02)**
   - 7.1.0에 대응한 비공식 파이썬 라이브러리 pykorail(devgyurak)로 같은 예매 루프를 구현함 → `ktx_reserve/`
3. **pykorail 원본 삭제 (2026-11-03 확인)**
   - GitHub(devgyurak/pykorail)와 PyPI에서 모두 사라짐 → `pip install pykorail` 불가.
4. **공개 보존 사본으로 고정 + 배포 패키지 (2026-11-04~05, Codex 작업)**
   - pykorail 사본 `ArchsouL/pykorail`의 커밋 `030576a6`을 Git으로 설치하도록 고정 (2026-11-18 기준 GitHub에 존재).
   - 맥/윈도우 설치·실행 스크립트, 디스코드 알림, 자동 결제 입력 검증, 설명서(txt/PDF/HTML), ZIP 배포본 추가.
   - 실서버에서 조회·로그인·예약·결제 전 과정 검증 완료.

## 3. 동작 방식

1. 로그인. 아이디/비밀번호 오류면 즉시 멈춤 (재시도하면 5회째 계정 잠김).
2. 지정한 시간대의 열차를 1~2초 간격으로 조회 → 원하는 좌석이 보이면 바로 예약.
3. `--adjacent`면 붙은 자리인지 확인, 아니면 취소 후 계속.
4. 예약 성공 → 알림 (맥 알림, 선택적으로 디스코드).
5. `--pay`면 카드 결제 1회 시도. 실패해도 예약은 유지되니 결제기한 안에 앱에서 결제.

| 상황 | 대응 |
|---|---|
| 자리 없음 | 계속 조회 |
| 세션 만료 | 다시 로그인 |
| MACRO ERROR | 60초 쉬고 계속 |
| 통신 오류 | 5초 쉬고 계속 |
| 비밀번호 오류 / 이용제한(-2000) | 즉시 멈춤 |
| 버전 거절(-8204) | 멈춤 (아래 위험 요소 참고) |

## 4. 검증 상태

조회, 로그인, 예약, 결제 전 과정을 실서버에서 검증 완료 (macOS · Windows).

## 5. 위험 요소

- **앱 버전 고정 문제**: 고정한 pykorail 사본은 앱 버전 7.0.10을 보냄. 코레일 최신은 7.1.0.
  코레일이 옛 버전을 막으면 다시 `-8204`가 날 수 있고, 원본 라이브러리가 없어 고치기 어려움.
- **사본 삭제 가능성**: `ArchsouL/pykorail`가 사라지면 새 PC에서 설치가 안 됨. 이미 설치한 PC(`.venv` 폴더)는 그대로 동작.
- **매크로 제재**: 코레일은 매크로 적발 시 1회 30분, 2회 1개월 예매 정지, 3회 강제 탈퇴를 안내함. 조회 간격을 줄이지 말 것.
- **용도 제한**: 본인 탑승용만. 되팔기 목적 매크로 예매는 철도사업법 위반. pykorail 라이선스도 상업적 이용 금지.

## 6. 남은 일

- [ ] 다음 일정으로 `start_booking` 설정 변경

## 7. 파일 구조

```
HANDOVER.md                 이 문서
ktx_reserve/
  ktx_reserve.py            예매 프로그램 본체
  live_backend.py           pykorail 클라이언트 생성 (기기 ID 고정)
  discord_notify.py         디스코드 알림
  setup.sh / setup.bat      설치 (가상환경 + 의존성 + 테스트)
  run.sh / run.bat          실행 (옵션 직접 지정)
  start_booking.sh / .bat   정해 둔 일정으로 실행
  사용설명서.txt             설명서 (v1.7)
  ui_server.py, ui/          설정 화면
  launch.sh / launch.bat     첫 실행 자동 설치 + 화면 실행
  THIRD_PARTY.md            pykorail 사본 출처·라이선스
  tests/                    자동 테스트
downloads/                  배포용 ZIP
old/                        이전 설명서·배포본
start.command / start.bat   원클릭 실행
```
