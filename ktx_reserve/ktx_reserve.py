#!/usr/bin/env python3
"""KTX 빈자리 자동 예매 스크립트 (pykorail 기반).

ktx-auto(ShinbaramEbaksa/ktx-auto)의 예매 루프를 코레일톡 7.1.0 에 대응한
pykorail 라이브러리로 옮긴 것입니다. 화면 없이 터미널에서 실행합니다.

동작 순서 (ktx-auto 와 같음)
  1. 로그인 — 아이디/비밀번호 오류면 즉시 멈춤 (재시도하면 5회에서 계정 잠김)
  2. 시간대 안 열차를 조회 → 원하는 좌석 등급이 있으면 바로 예약
  3. 2명 이상 + --adjacent 이면 배정 좌석이 붙어 있는지 확인, 아니면 취소 후 계속
  4. 예약 성공 → macOS 알림 (결제기한 안내)
  5. --pay 이면 카드 결제까지 진행 (실패해도 예약은 유지)

상황별 대응
  자리 없음            → 계속 조회
  세션 만료            → 다시 로그인
  MACRO ERROR         → 60초 쉬고 계속
  이용제한(-2000)      → 즉시 멈춤 (반복하면 안 됨)
  통신 오류            → 5초 쉬고 계속
  로그인 일시 오류     → 30초 → 최대 2분 간격, 5회 연속이면 10분 간격

사용 예
  python3 ktx_reserve.py --dep 서울 --arr 동대구 --date 2026-11-06 \
      --from 13:00 --to 15:00 --adult 2 --adjacent
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit(
        f"파이썬 3.11 이상이 필요합니다 (지금: {sys.version.split()[0]}).\n"
        "맥이라면: brew install python  후  python3 --version 으로 확인하세요."
    )

try:
    from pykorail import (
        AccessRestrictedError,
        AdultPassenger,
        Card,
        ChildPassenger,
        DeviceProfile,
        Korail,
        KorailError,
        LoginFailedError,
        NeedToLoginError,
        NoResultsError,
        PastDepartureError,
        PykorailError,
        ReserveOption,
        SeniorPassenger,
        SoldOutError,
        StationNotFoundError,
        TrainType,
    )
    from pykorail.device import random_profile
except ImportError:  # pragma: no cover - 설치 안내
    sys.exit("pykorail 이 설치되어 있지 않습니다.\n  python3 -m pip install -U pykorail")


KST = timezone(timedelta(hours=9))

# ktx-auto(src/bot/runner.ts) 와 같은 값
LOGIN_RETRY_BASE_SEC = 30
LOGIN_RETRY_MAX_SEC = 120
LOGIN_RETRY_ALERT_AFTER = 5
LOGIN_RETRY_SLOW_SEC = 600
MACRO_COOLDOWN_SEC = 60
TRANSIENT_ERROR_SEC = 5
IDLE_LOG_EVERY_SEC = 10
# 붙은 자리가 아니라 취소한 열차는 잠시 건너뛴다 (예약→취소 반복 방지)
NOT_ADJACENT_SKIP_SEC = 60

TRAIN_TYPES = {
    "ktx": TrainType.KTX,
    "all": TrainType.ALL,
    "saemaeul": TrainType.SAEMAEUL,
    "mugunghwa": TrainType.MUGUNGHWA,
    "itx-cheongchun": TrainType.ITX_CHEONGCHUN,
}

SEAT_PREFS = {
    "general-only": (ReserveOption.GENERAL_ONLY, "일반실만"),
    "general-first": (ReserveOption.GENERAL_FIRST, "일반실 우선"),
    "special-only": (ReserveOption.SPECIAL_ONLY, "특실만"),
    "special-first": (ReserveOption.SPECIAL_FIRST, "특실 우선"),
}

DEVICE_FILE = Path.home() / ".ktx_reserve" / "device.json"


class Stop(Exception):
    """루프를 끝내야 하는 상황 (메시지는 사용자에게 보여 줌)."""


# --------------------------------------------------------------------------- 출력·알림


def log(msg: str) -> None:
    print(f"{datetime.now(KST):%H:%M:%S}  {msg}", flush=True)


def notify(title: str, body: str) -> None:
    """macOS 알림 센터 + 소리. 맥이 아니면 터미널 벨만 울린다."""
    print("\a", end="", flush=True)
    if sys.platform != "darwin":
        return
    script = f"display notification {json.dumps(body)} with title {json.dumps(title)} sound name \"Glass\""
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        pass


# --------------------------------------------------------------------------- 좌석 판정


def seats_are_adjacent(seats: list[tuple[str, str]]) -> bool:
    """(호차, 좌석표시) 목록이 붙은 자리인지. ktx-auto 의 seatsAreAdjacent 와 같은 규칙.

    같은 호차여야 하고, '15A','15B' 처럼 같은 열에서 알파벳이 연속이거나
    숫자만 있는 좌석이면 번호가 연속이어야 한다.
    """
    if len(seats) < 2:
        return True
    if len({car for car, _ in seats}) != 1:
        return False
    parsed = []
    for _, label in seats:
        m = re.fullmatch(r"\s*(\d+)\s*([A-Za-z])?\s*", label or "")
        if not m:
            return False
        parsed.append((int(m.group(1)), m.group(2).upper() if m.group(2) else None))

    def consecutive(xs: list[int]) -> bool:
        xs = sorted(xs)
        return len(set(xs)) == len(xs) and xs[-1] - xs[0] == len(xs) - 1

    if all(col is not None for _, col in parsed):
        if len({row for row, _ in parsed}) != 1:
            return False
        return consecutive([ord(col) for _, col in parsed])
    if any(col is not None for _, col in parsed):
        return False
    return consecutive([row for row, _ in parsed])


def has_wanted_seat(train, seat_pref: str) -> bool:
    if seat_pref == "general-only":
        return train.has_general_seat()
    if seat_pref == "special-only":
        return train.has_special_seat()
    return train.has_seat()


def train_label(train) -> str:
    t = train.dep_time
    return f"{train.train_type_name} {train.train_no.lstrip('0') or '0'}번 {t[:2]}:{t[2:4]}"


# --------------------------------------------------------------------------- 기기 프로파일


def load_device_profile() -> DeviceProfile:
    """실행할 때마다 다른 폰인 척하지 않도록 같은 가상 기기를 저장해 재사용한다."""
    try:
        return DeviceProfile(**json.loads(DEVICE_FILE.read_text()))
    except Exception:
        pass
    profile = random_profile()
    try:
        DEVICE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_FILE.write_text(json.dumps(asdict(profile)))
        DEVICE_FILE.chmod(0o600)
    except OSError as exc:
        log(f"기기 정보 저장 실패(계속 진행): {exc}")
    return profile


# --------------------------------------------------------------------------- 봇


class Bot:
    def __init__(self, cfg: argparse.Namespace, korail, card: Card | None = None, sleep=time.sleep):
        self.cfg = cfg
        self.korail = korail
        self.card = card
        self.sleep = sleep
        self.attempts = 0
        self.last_idle_log = 0.0
        self.skip_until: dict[str, float] = {}

        d = cfg.date
        self.window_start = datetime(d.year, d.month, d.day, cfg.time_from[0], cfg.time_from[1], tzinfo=KST)
        self.window_end = datetime(d.year, d.month, d.day, cfg.time_to[0], cfg.time_to[1], tzinfo=KST)
        self.passengers = (
            ([AdultPassenger(cfg.adult)] if cfg.adult else [])
            + ([ChildPassenger(cfg.child)] if cfg.child else [])
            + ([SeniorPassenger(cfg.senior)] if cfg.senior else [])
        )
        self.total = cfg.adult + cfg.child + cfg.senior
        # 한 번 조회에 약 10편이 온다. 시간대가 길면 2쪽까지 받는다.
        self.max_pages = 1 if self.window_end - self.window_start <= timedelta(hours=2) else 2

    # ---- 로그

    def idle_log(self, msg: str) -> None:
        now = time.monotonic()
        if now - self.last_idle_log >= IDLE_LOG_EVERY_SEC:
            self.last_idle_log = now
            log(msg)

    # ---- 로그인

    def login_with_retry(self) -> None:
        transient = 0
        while True:
            try:
                self.korail.login(self.cfg.korail_id, self.cfg.korail_pw)
                log(f"로그인 완료 ({self.korail.name or self.korail.membership_number})")
                return
            except LoginFailedError as exc:
                notify("KTX 예매: 로그인 실패", "계정 잠금을 막기 위해 멈췄습니다.")
                raise Stop(f"로그인 실패로 종료 (계정 잠금 방지): {exc}") from exc
            except AccessRestrictedError as exc:
                raise Stop(f"코레일이 이용을 제한했습니다: {exc}") from exc
            except Exception as exc:  # 통신·일시 오류 → 백오프 재시도
                transient += 1
                if transient == LOGIN_RETRY_ALERT_AFTER:
                    notify("KTX 예매: 로그인이 계속 실패", f"{transient}회 연속 로그인 오류입니다.")
                rest = (
                    LOGIN_RETRY_SLOW_SEC
                    if transient >= LOGIN_RETRY_ALERT_AFTER
                    else min(LOGIN_RETRY_BASE_SEC * transient, LOGIN_RETRY_MAX_SEC)
                )
                log(f"로그인 일시 오류 ({transient}회): {first_line(exc)}. {rest}초 뒤 다시 시도합니다")
                self.sleep(rest)

    # ---- 메인 루프

    def run(self) -> None:
        c = self.cfg
        log(
            f"조회기반 | {c.date} {c.dep}→{c.arr} {c.time_from[0]:02d}:{c.time_from[1]:02d}~"
            f"{c.time_to[0]:02d}:{c.time_to[1]:02d}, 총 {self.total}명, 등급 {SEAT_PREFS[c.seat][1]}"
            f"{', 붙은 자리만' if c.adjacent and self.total > 1 else ''}, "
            f"{'결제까지' if self.card else '예약까지만'}{' · 테스트 모드' if c.dry_run else ''}"
        )
        self.login_with_retry()

        while True:
            self.attempts += 1
            try:
                if self.poll_once():
                    return
            except Stop:
                raise
            except NeedToLoginError:
                log("세션이 만료되어 다시 로그인합니다")
                self.login_with_retry()
                continue
            except AccessRestrictedError as exc:
                notify("KTX 예매: 이용제한", "코레일이 요청을 막았습니다. 멈춥니다.")
                raise Stop(f"코레일 이용제한으로 종료 (반복하면 안 됩니다): {exc}") from exc
            except KorailError as exc:
                if exc.code == "MACRO ERROR":
                    log(f"안티매크로 감지. {MACRO_COOLDOWN_SEC}초 쉬었다가 계속합니다")
                    self.sleep(MACRO_COOLDOWN_SEC)
                    continue
                if exc.code == "-8204":
                    raise Stop(
                        "코레일이 앱 버전이 낮다고 거절했습니다. "
                        "python3 -m pip install -U pykorail 로 업데이트하세요."
                    ) from exc
                log(f"오류: {first_line(exc)}. 잠시 뒤 계속합니다")
                self.sleep(TRANSIENT_ERROR_SEC)
            except Exception as exc:  # 통신 오류 등
                log(f"오류: {first_line(exc)}. 잠시 뒤 계속합니다")
                self.sleep(TRANSIENT_ERROR_SEC)
            self.sleep(random.uniform(c.interval_min, c.interval_max))

    def poll_once(self) -> bool:
        """조회 1회 + 예약 시도. 예매가 끝나면 True."""
        c = self.cfg
        now = datetime.now(KST)
        if now >= self.window_end:
            raise Stop("원하는 시간대의 열차가 모두 출발했습니다. 종료합니다.")
        depart_after = max(self.window_start, now)

        try:
            trains = self.korail.trains.search(
                c.dep,
                c.arr,
                depart_after=depart_after,
                train_type=TRAIN_TYPES[c.train],
                passengers=self.passengers,
                max_pages=self.max_pages,
            )
        except NoResultsError:
            self.idle_log(f"빈자리 없음, 계속 조회 중 · {self.attempts}회차")
            return False
        except PastDepartureError:
            return False

        mono = time.monotonic()
        candidates = [
            t
            for t in trains
            if t.departure is not None
            and self.window_start <= t.departure <= self.window_end
            and (not c.trains or t.train_no.lstrip("0") in c.trains)
            and has_wanted_seat(t, c.seat)
            and self.skip_until.get(t.train_no, 0) <= mono
        ]
        if not candidates:
            self.idle_log(f"빈자리 없음 ({len(trains)}편 확인), 계속 조회 중 · {self.attempts}회차")
            return False

        for train in candidates:
            if self.try_reserve(train):
                return True
        return False

    def try_reserve(self, train) -> bool:
        c = self.cfg
        label = train_label(train)
        log(f"{label}: 빈자리 발견 → 예약 시도")

        if c.dry_run:
            log(f"테스트 모드: {label} 예약 직전까지 확인했습니다 (실제 예약 안 함)")
            notify("KTX 예매 테스트 완료", f"{label} 예약 직전까지 정상 동작했습니다.")
            return True

        option = SEAT_PREFS[c.seat][0]
        try:
            rsv = self.korail.reservations.create(train, self.passengers, option)
        except SoldOutError:
            log(f"{label}: 그새 자리가 나갔습니다. 계속 시도합니다")
            return False

        if rsv.is_waiting:  # 좌석이 아니라 예약대기가 걸렸으면 원치 않는 결과
            log(f"{label}: 예약대기로 잡혀 취소하고 계속합니다")
            self.safe_cancel(rsv)
            return False

        seats = [(s.car_no, s.seat_no) for s in rsv.seats]
        seat_text = ", ".join(f"{car}호차 {no}" for car, no in seats) or "좌석 정보 없음"
        if c.adjacent and self.total > 1:
            if not seats:
                log(f"{label}: 좌석 정보를 읽지 못해 붙은 자리 확인을 건너뜁니다. 앱에서 확인하세요")
            elif not seats_are_adjacent(seats):
                log(f"{label}: 붙은 자리가 아님({seat_text}). 취소하고 계속 시도합니다")
                self.safe_cancel(rsv)
                self.skip_until[train.train_no] = time.monotonic() + NOT_ADJACENT_SKIP_SEC
                return False

        limit = rsv.buy_limit.strftime("%H:%M") if rsv.buy_limit else "확인 필요"
        log(f"예약 성공! {label} {train.dep_name}→{train.arr_name} | 좌석 {seat_text} | "
            f"{rsv.price:,}원 | 예약번호 {rsv.pnr_no} | 결제기한 {limit}")
        notify("KTX 예약 성공", f"{label} {seat_text}. 결제기한 {limit}까지 결제하세요.")

        if self.card:
            try:
                self.korail.reservations.pay(rsv, self.card)
                log("결제 완료! 코레일톡 앱의 승차권 확인에서 표를 확인하세요.")
                notify("KTX 결제 완료", f"{label} 발권되었습니다.")
            except PykorailError as exc:
                log(f"자동 결제 실패: {first_line(exc)} — 예약은 유지됩니다. {limit}까지 앱에서 직접 결제하세요")
                notify("KTX 결제 실패", f"예약은 유지됩니다. {limit}까지 앱에서 결제하세요.")
        else:
            log(f"{limit}까지 코레일톡 앱에서 결제하지 않으면 자동 취소됩니다.")
        return True

    def safe_cancel(self, rsv) -> None:
        try:
            self.korail.reservations.cancel(rsv)
        except Exception as exc:
            log(f"취소 실패: {first_line(exc)} — 앱에서 예약번호 {rsv.pnr_no} 를 직접 취소하세요")


def first_line(exc: BaseException) -> str:
    return (str(exc) or type(exc).__name__).splitlines()[0]


# --------------------------------------------------------------------------- 입력


def parse_hhmm(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})", text.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise argparse.ArgumentTypeError(f"시각 형식이 잘못됐습니다: {text} (예: 13:30)")
    return int(m.group(1)), int(m.group(2))


def parse_date(text: str) -> Date:
    digits = re.sub(r"\D", "", text)
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜 형식이 잘못됐습니다: {text} (예: 2026-11-06)") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="KTX 빈자리 자동 예매 (pykorail 기반, ktx-auto 동작 재현)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--dep", required=True, help="출발역 이름 (예: 서울)")
    p.add_argument("--arr", required=True, help="도착역 이름 (예: 동대구)")
    p.add_argument("--date", required=True, type=parse_date, help="출발일 (예: 2026-11-06)")
    p.add_argument("--from", dest="time_from", required=True, type=parse_hhmm, help="이 시각부터 출발 (예: 13:00)")
    p.add_argument("--to", dest="time_to", required=True, type=parse_hhmm, help="이 시각까지 출발 (예: 15:00)")
    p.add_argument("--adult", type=int, default=1, help="어른 인원")
    p.add_argument("--child", type=int, default=0, help="어린이 인원")
    p.add_argument("--senior", type=int, default=0, help="경로 인원")
    p.add_argument("--train", choices=TRAIN_TYPES, default="ktx", help="열차 종류")
    p.add_argument("--seat", choices=SEAT_PREFS, default="general-first", help="좌석 등급")
    p.add_argument("--adjacent", action="store_true", help="2명 이상일 때 붙은 자리만")
    p.add_argument("--trains", default="", help="이 열차번호만 (쉼표로 구분, 예: 105,107)")
    p.add_argument("--interval-min", type=float, default=1.0, help="조회 간격 최소(초)")
    p.add_argument("--interval-max", type=float, default=2.0, help="조회 간격 최대(초)")
    p.add_argument("--pay", action="store_true", help="예약 후 카드 결제까지 자동 진행")
    p.add_argument("--dry-run", action="store_true", help="테스트: 빈자리를 찾으면 예약하지 않고 멈춤")
    return p


def validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if min(args.adult, args.child, args.senior) < 0:
        parser.error("인원은 0 이상이어야 합니다")
    total = args.adult + args.child + args.senior
    if not 1 <= total <= 9:
        parser.error("총 인원은 1~9명이어야 합니다")
    if args.time_from > args.time_to:
        parser.error("--from 이 --to 보다 늦습니다")
    if args.interval_min < 0.5 or args.interval_max < args.interval_min:
        parser.error("조회 간격은 0.5초 이상, --interval-min ≤ --interval-max 여야 합니다")
    args.trains = {t.strip().lstrip("0") for t in args.trains.split(",") if t.strip()}


def ask_card() -> Card:
    print("\n결제 카드 정보 (화면에 표시되지 않고 파일로 저장하지 않습니다)")
    number = re.sub(r"\D", "", getpass.getpass("카드번호 16자리: "))
    expire = re.sub(r"\D", "", input("유효기간 YYMM (예: 2812 = 2028년 12월): "))
    password = getpass.getpass("카드 비밀번호 앞 2자리: ").strip()
    verify = re.sub(r"\D", "", getpass.getpass("생년월일 YYMMDD: "))
    return Card(number=number, password=password, verify_number=verify, expire=expire)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate(args, parser)

    args.korail_id = os.environ.get("KORAIL_ID") or input("코레일 아이디 (회원번호·이메일·010-0000-0000): ").strip()
    args.korail_pw = os.environ.get("KORAIL_PASSWORD") or getpass.getpass("코레일 비밀번호: ")
    card = ask_card() if args.pay and not args.dry_run else None

    try:
        with Korail(device_profile=load_device_profile()) as korail:
            korail.stations.ensure_exist(args.dep, args.arr)
            Bot(args, korail, card).run()
        return 0
    except StationNotFoundError as exc:
        log(f"역 이름 오류: {exc}")
    except Stop as exc:
        log(str(exc))
    except KeyboardInterrupt:
        log("사용자 요청으로 중지했습니다")
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
