# KTX 자동 예매 (v1.7)

- 실행: Mac `start.command`, Windows `start.bat` 더블클릭. 처음엔 자동 설치 후 설정 화면이 열립니다.
- 사용법: [사용설명서.txt](사용설명서.txt)
- 터미널 실행: `bash run.sh --help` (Windows `run.bat --help`)
- 테스트: `.venv/bin/python -B -m unittest discover -s tests`

| 파일 | 역할 |
|---|---|
| `ui_server.py`, `ui/` | 로컬 설정 화면 (http://127.0.0.1:3939) |
| `ktx_reserve.py` | 예매 루프 (조회 → 예약 → 알림 → 결제) |
| `live_backend.py` | pykorail 클라이언트 생성, 기기 ID 고정 |
| `demo_backend.py` | 데모 모드용 모의 데이터 |
| `discord_notify.py` | Discord 알림 |
| `launch.sh`, `launch.bat` | 첫 실행 자동 설치 + 화면 실행 |
| `setup.*`, `run.*`, `ui.*` | 설치, 터미널 실행, 화면 실행 |
| `THIRD_PARTY.md` | pykorail · 갈무리 폰트 출처와 라이선스 |
