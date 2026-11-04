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
import importlib
import math
import os
import random
import re
import subprocess
import sys
import time
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit(
        f"파이썬 3.11 이상이 필요합니다 (지금: {sys.version.split()[0]}).\n"
        "맥이라면: brew install python  후  python3 --version 으로 확인하세요."
    )

def load_backend(demo: bool = False) -> None:
    """실제 라이브러리는 실행 시에만 불러온다. 데모는 네트워크를 사용하지 않는다."""
    module = importlib.import_module("demo_backend" if demo else "pykorail")
    names = (
        "AccessRestrictedError AdultPassenger Card ChildPassenger DeviceProfile Korail "
        "KorailError LoginFailedError NeedToLoginError NoResultsError PastDepartureError "
        "PykorailError ReserveOption SeniorPassenger SoldOutError StationNotFoundError TrainType"
    ).split()
    bindings = {name: getattr(module, name) for name in names}
    bindings["random_profile"] = (module.random_profile if demo else
                                  importlib.import_module("pykorail.device").random_profile)
    globals().update(bindings)
    for key, name in TRAIN_TYPE_NAMES.items():
        TRAIN_TYPES[key] = getattr(module.TrainType, name)
    for key, (name, label) in SEAT_PREF_NAMES.items():
        SEAT_PREFS[key] = (getattr(module.ReserveOption, name), label)


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

TRAIN_TYPE_NAMES = {
    "ktx": "KTX", "all": "ALL", "saemaeul": "SAEMAEUL",
    "mugunghwa": "MUGUNGHWA", "itx-cheongchun": "ITX_CHEONGCHUN",
}
SEAT_PREF_NAMES = {
    "general-only": ("GENERAL_ONLY", "일반실만"),
    "general-first": ("GENERAL_FIRST", "일반실 우선"),
    "special-only": ("SPECIAL_ONLY", "특실만"),
    "special-first": ("SPECIAL_FIRST", "특실 우선"),
}
TRAIN_TYPES = dict(TRAIN_TYPE_NAMES)
SEAT_PREFS = dict(SEAT_PREF_NAMES)
VERSION_ERROR_MESSAGE = "코레일 앱 버전 거절(-8204). 중단합니다. 호환되는 라이브러리가 필요합니다."




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
    script = '''on run argv
    display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"
end run'''
    try:
        result = subprocess.run(["osascript", "-e", script, title, body],
                                check=False, timeout=5, capture_output=True)
        if result.returncode:
            log("맥 알림 표시 실패. 터미널의 예약 결과를 확인하세요.")
    except (OSError, subprocess.TimeoutExpired):
        log("맥 알림을 표시하지 못했습니다. 터미널의 예약 결과를 확인하세요.")


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
            if datetime.now(KST) >= self.window_end:
                raise Stop("원하는 시간대가 지나 로그인 재시도를 종료합니다.")
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
                if isinstance(exc, KorailError) and str(exc.code) in {"-8204", "-2000"}:
                    raise Stop(VERSION_ERROR_MESSAGE if str(exc.code) == "-8204" else "코레일 이용제한으로 종료합니다.") from exc
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
        if c.trains:
            log(f"열차번호 제한: {', '.join(sorted(c.trains))}번만 조회 결과에서 선택합니다")
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
                if str(exc.code) == "-8204":
                    raise Stop(VERSION_ERROR_MESSAGE) from exc
                if str(exc.code) == "-2000":
                    raise Stop("코레일 이용제한으로 종료합니다.") from exc
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
            in_window = [t for t in trains if t.departure is not None
                         and self.window_start <= t.departure <= self.window_end]
            if c.trains and in_window and not any(t.train_no.lstrip("0") in c.trains for t in in_window):
                self.idle_log(f"조회한 시간대에 지정 열차번호({', '.join(sorted(c.trains))})가 없습니다. "
                              "전체 열차를 보려면 --trains 제한을 제거하세요")
            else:
                self.idle_log(f"현재 예약 조건에 맞는 좌석 없음 ({len(trains)}편 확인), 계속 조회 중 · {self.attempts}회차")
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
        except (NeedToLoginError, AccessRestrictedError):
            raise
        except Exception as exc:
            raise Stop("예약 요청 결과를 확인할 수 없습니다. 중복 예약 방지를 위해 종료합니다. 코레일톡에서 예약 내역을 확인하세요.") from exc

        if rsv.is_waiting:  # 좌석이 아니라 예약대기가 걸렸으면 원치 않는 결과
            log(f"{label}: 예약대기로 잡혀 취소하고 계속합니다")
            self.safe_cancel(rsv)
            return False

        seats = [(s.car_no, s.seat_no) for s in rsv.seats]
        seat_text = ", ".join(f"{car}호차 {no}" for car, no in seats) or "좌석 정보 없음"
        if c.adjacent and self.total > 1:
            if len(seats) != self.total or not seats_are_adjacent(seats):
                log(f"{label}: 붙은 자리가 아님({seat_text}). 취소하고 계속 시도합니다")
                self.safe_cancel(rsv)
                self.skip_until[train.train_no] = time.monotonic() + NOT_ADJACENT_SKIP_SEC
                return False

        if c.demo:
            log(f"[데모] 모의 예약 완료 | {label} | 좌석 {seat_text} | 실제 승차권 없음")
            return True

        limit = rsv.buy_limit.strftime("%H:%M") if rsv.buy_limit else "확인 필요"
        log(f"예약 성공! {label} {train.dep_name}→{train.arr_name} | 좌석 {seat_text} | "
            f"{rsv.price:,}원 | 예약번호 {rsv.pnr_no} | 결제기한 {limit}")
        notify("KTX 예약 성공", f"{label} {seat_text}. " +
               ("자동 결제를 시도합니다." if self.card else f"결제기한 {limit}까지 결제하세요."))

        outcome_title = "KTX 예약 성공"
        outcome_body = f"결제기한 {limit}. 코레일톡에서 예약 내역을 확인하고 결제하세요."
        if self.card:
            try:
                self.korail.reservations.pay(rsv, self.card)
            except Exception:
                outcome_title = "KTX 결제 상태 확인 필요"
                outcome_body = f"자동 결제 결과를 확인하지 못했습니다. 코레일톡에서 결제·예약 상태를 확인하세요 (기한 {limit})."
                log(outcome_body + " 예약·결제를 자동으로 다시 시도하지 않습니다.")
                notify(outcome_title, outcome_body)
            else:
                outcome_title = "KTX 결제 완료"
                outcome_body = "결제 요청이 정상 처리됐습니다. 코레일톡 앱의 승차권 확인에서 표를 확인하세요."
                log(outcome_body)
                notify(outcome_title, f"{label}. {outcome_body}")
        else:
            log(f"{limit}까지 코레일톡 앱에서 결제하지 않으면 자동 취소됩니다.")

        # Notification failure must never restart the reservation loop.
        try:
            from discord_notify import send
            delivered = send(
                f"🚄 {outcome_title}! {c.date} {train.dep_name}→{train.arr_name}\n"
                f"{label} · {self.total}명 · {seat_text}\n"
                f"{outcome_body}", c.state_dir)
            if delivered is True:
                log("디스코드 결과 알림 전송 완료")
            elif delivered is False:
                log("디스코드 알림 전송 실패. 예약은 다시 시도하지 않습니다. 앱에서 확인하세요.")
        except Exception:
            log("디스코드 알림 처리 실패. 앱에서 예약 내역을 확인하세요.")

        return True

    def safe_cancel(self, rsv) -> None:
        try:
            self.korail.reservations.cancel(rsv)
        except Exception as exc:
            raise Stop(f"취소 실패 — 중복 예약을 막기 위해 종료합니다. 앱에서 예약번호 {rsv.pnr_no}를 확인하세요.") from exc


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
    p.add_argument("--check", action="store_true", help="로그인·예약 없이 실제 역 정보와 열차 조회를 한 번 검사")
    p.add_argument("--state-dir", type=Path, default=Path(os.environ.get("KTX_STATE_DIR", str(Path.home() / ".ktx_reserve"))), help="기기 ID 보관 폴더")
    p.add_argument("--demo", action="store_true", help="외부 연결 없이 모의 조회·예약으로 실행 (실제 승차권 없음)")
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
    if (not math.isfinite(args.interval_min) or not math.isfinite(args.interval_max)
            or args.interval_min < 0.5 or args.interval_max < args.interval_min):
        parser.error("조회 간격은 0.5초 이상, --interval-min ≤ --interval-max 여야 합니다")
    if args.check and (args.demo or args.pay):
        parser.error("--check는 --demo 또는 --pay와 함께 사용할 수 없습니다")
    if args.demo and args.pay:
        parser.error("--demo에서는 --pay를 사용할 수 없습니다")
    if args.dep == args.arr:
        parser.error("출발역과 도착역이 같습니다")
    train_numbers = [t.strip() for t in args.trains.split(",") if t.strip()]
    if any(not t.isascii() or not t.isdigit() or int(t) == 0 for t in train_numbers):
        parser.error("--trains에는 양의 열차번호를 쉼표로 구분해 입력하세요")
    args.trains = {t.lstrip("0") for t in train_numbers}


def ask_card() -> Card:
    print("\n자동 결제: 개인카드 정보를 입력합니다. 화면에 표시하거나 파일에 저장하지 않습니다.")
    number = re.sub(r"[ -]", "", getpass.getpass("카드번호: "))
    expire = getpass.getpass("유효기간 YYMM (예: 2812 = 2028년 12월): ").strip()
    password = getpass.getpass("카드 비밀번호 앞 2자리: ").strip()
    verify = getpass.getpass("카드 소유자 생년월일 YYMMDD: ").strip()
    if not re.fullmatch(r"[0-9]{13,19}", number):
        raise Stop("카드번호 형식을 확인하세요. 개인카드 번호는 숫자 13~19자리여야 합니다.")
    if not re.fullmatch(r"[0-9]{4}", expire) or not 1 <= int(expire[2:]) <= 12:
        raise Stop("유효기간은 YYMM 네 자리로 입력하세요. 월은 01~12여야 합니다.")
    if not re.fullmatch(r"[0-9]{2}", password):
        raise Stop("카드 비밀번호 앞 두 자리는 숫자 두 자리로 입력하세요.")
    if not re.fullmatch(r"[0-9]{6}", verify):
        raise Stop("카드 소유자 생년월일은 YYMMDD 여섯 자리로 입력하세요.")
    return Card(number=number, password=password, verify_number=verify, expire=expire)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate(args, parser)

    try:
        load_backend(args.demo)
    except (ImportError, AttributeError):
        print("실제 실행에 필요한 호환 pykorail을 불러올 수 없습니다. "
              "가상환경에서 python -m pip install -r requirements.txt 로 고정 배포본을 설치하세요.", file=sys.stderr)
        return 2

    try:
        if args.demo:
            log("[데모] 모의 데이터로 실행합니다. 서버 접속·실제 예약·결제는 없습니다.")
            args.korail_id, args.korail_pw = "demo", "demo"
            card = None
            client = Korail(config=args)
        else:
            from live_backend import create_client
            if args.check:
                with create_client(args.state_dir) as korail:
                    korail.stations.ensure_exist(args.dep, args.arr)
                    log("실제 서버 역 정보 조회 성공")
                    start = datetime.combine(args.date, datetime.min.time(), tzinfo=KST).replace(
                        hour=args.time_from[0], minute=args.time_from[1])
                    try:
                        trains = korail.trains.search(args.dep, args.arr, depart_after=start,
                                                     train_type=TRAIN_TYPES[args.train],
                                                     passengers=([AdultPassenger(args.adult)] if args.adult else [])
                                                     + ([ChildPassenger(args.child)] if args.child else [])
                                                     + ([SeniorPassenger(args.senior)] if args.senior else []),
                                                     include_no_seats=True, max_pages=1)
                    except NoResultsError:
                        trains = []
                    log(f"실제 서버 열차 조회 성공: {len(trains)}편 (첫 페이지, 예약·결제 없음)")
                    for train in trains:
                        log(f"{train_label(train)} {train.dep_name}→{train.arr_name}")
                return 0
            args.korail_id = os.environ.get("KORAIL_ID") or input("코레일 아이디 (회원번호·이메일·010-0000-0000): ").strip()
            args.korail_pw = os.environ.get("KORAIL_PASSWORD") or getpass.getpass("코레일 비밀번호: ")
            card = ask_card() if args.pay and not args.dry_run else None
            client = create_client(args.state_dir)
        with client as korail:
            korail.stations.ensure_exist(args.dep, args.arr)
            Bot(args, korail, card).run()
        return 0
    except StationNotFoundError as exc:
        log(f"역 이름 오류: {exc}")
    except Stop as exc:
        log(str(exc))
    except KorailError as exc:
        log(f"코레일 요청 거절 (코드 {exc.code}). 서버 연결·호환성 또는 계정 상태를 확인하세요.")
    except Exception as exc:
        # Transport errors may contain proxy URLs or request details; do not dump them.
        if isinstance(exc, EOFError):
            log("입력을 읽을 수 없습니다. 대화형 터미널 또는 환경변수로 인증 정보를 제공하세요.")
        else:
            log(f"실행 실패 ({type(exc).__name__}). 네트워크 허용 도메인, CA 설정, 기기 파일을 확인하세요.")
        return 1
    except KeyboardInterrupt:
        log("사용자 요청으로 중지했습니다")
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
