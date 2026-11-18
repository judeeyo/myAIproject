#!/usr/bin/env python3
"""KTX 자동 예매 로컬 웹 UI.

브라우저 화면(http://127.0.0.1:3939)에서 설정하고, 예매 루프는 ktx_reserve.Bot 을
그대로 백그라운드 스레드에서 실행한다. 외부 의존성 없이 표준 라이브러리만 사용한다.

    python ui_server.py            # 화면 자동으로 열림
    NO_OPEN=1 PORT=4000 python ui_server.py
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import ktx_reserve as kr

UI_DIR = Path(__file__).resolve().parent / "ui"
HOST = "127.0.0.1"
MAX_LOGS = 500
MAX_BODY = 16 * 1024

MAJOR_STATIONS = [
    "서울", "용산", "광명", "수서", "영등포", "수원", "평택", "천안아산", "천안", "오송", "조치원",
    "대전", "서대전", "김천구미", "구미", "동대구", "대구", "경주", "울산(통도사)", "포항", "경산",
    "밀양", "부산", "구포", "창원중앙", "마산", "평창", "진부(오대산)", "강릉", "익산", "전주",
    "광주송정", "목포", "순천", "청량리", "여수EXPO", "동해", "정동진", "안동", "서원주", "원주",
    "행신", "나주", "정읍", "남원",
]


class UserStopped(BaseException):
    """중지 버튼. BaseException 이라 Bot 의 `except Exception` 재시도에 걸리지 않는다."""


# --------------------------------------------------------------------------- 실행 상태


class Runner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.logs: list[dict] = []
        self.next_id = 1
        self.state = self._idle_state()

    @staticmethod
    def _idle_state() -> dict:
        return {"status": "idle", "message": "대기 중", "attempts": 0, "lastCheck": None,
                "trains": [], "result": None, "mode": None, "summary": None}

    # ---- 로그·상태

    def log(self, msg: str, level: str | None = None) -> None:
        msg = str(msg)
        print(f"{datetime.now(kr.KST):%H:%M:%S}  {msg}", flush=True)
        with self.lock:
            self.logs.append({"id": self.next_id, "time": f"{datetime.now(kr.KST):%H:%M:%S}",
                              "level": level or classify(msg), "msg": msg})
            self.next_id += 1
            del self.logs[:-MAX_LOGS]

    def notify(self, title: str, body: str) -> None:
        with self.lock:
            self.state["toast"] = {"id": time.time(), "title": title, "body": body}
        try:
            ORIGINAL_NOTIFY(title, body)
        except Exception:
            pass

    def update(self, **fields) -> None:
        with self.lock:
            self.state.update(fields)

    def snapshot(self, after: int) -> dict:
        with self.lock:
            return {"state": dict(self.state), "running": self.running(),
                    "logs": [entry for entry in self.logs if entry["id"] > after]}

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def sleep(self, seconds: float) -> None:
        if self.stop_event.wait(max(0.0, seconds)):
            raise UserStopped()

    # ---- 시작·중지

    def start(self, args: argparse.Namespace, card, kind: str) -> None:
        with self.lock:
            if self.running():
                raise ValueError("이미 실행 중입니다")
            self.stop_event.clear()
            toast = self.state.get("toast")
            self.state = self._idle_state()
            self.state.update(status="starting", message="준비 중", mode=kind, toast=toast,
                              summary=summary_text(args, kind))
            target = self._check if kind == "check" else self._run
            self.thread = threading.Thread(target=target, args=(args, card), daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    # ---- 실행 본체

    def _client(self, args):
        if args.demo:
            args.korail_id = args.korail_pw = "demo"
            return kr.Korail(config=args)
        from live_backend import create_client
        return create_client(args.state_dir)

    def _run(self, args, card) -> None:
        try:
            if args.demo:
                self.log("[데모] 모의 데이터로 실행합니다. 서버 접속·실제 예약·결제는 없습니다.")
            with self._client(args) as korail:
                korail.stations.ensure_exist(args.dep, args.arr)
                bot = UIBot(args, TrackingKorail(korail, self), card, sleep=self.sleep)
                bot.runner = self
                bot.run()
            result = self.state.get("result")
            self.update(status="success", message=result or "완료")
        except UserStopped:
            self.log("사용자 요청으로 중지했습니다", "warn")
            self.update(status="stopped", message="중지됨")
        except kr.Stop as exc:
            self.log(str(exc), "error")
            self.update(status="error", message=str(exc))
        except Exception as exc:
            msg = describe_error(exc)
            self.log(msg, "error")
            self.update(status="error", message=msg)
        finally:
            args.korail_id = args.korail_pw = None  # 자격 증명을 메모리에 남기지 않는다

    def _check(self, args, card) -> None:
        try:
            self.update(status="checking", message="조회 테스트 중")
            with self._client(args) as korail:
                korail.stations.ensure_exist(args.dep, args.arr)
                start = datetime(args.date.year, args.date.month, args.date.day,
                                 args.time_from[0], args.time_from[1], tzinfo=kr.KST)
                start = max(start, datetime.now(kr.KST))
                try:
                    trains = korail.trains.search(args.dep, args.arr, depart_after=start,
                                                  train_type=kr.TRAIN_TYPES[args.train],
                                                  passengers=passengers(args),
                                                  include_no_seats=True, max_pages=1)
                except kr.NoResultsError:
                    trains = []
            chips = [train_chip(t) for t in trains]
            self.update(trains=chips, lastCheck=f"{datetime.now(kr.KST):%H:%M:%S}", attempts=1,
                        status="checked", message=f"조회 성공 · {len(chips)}편")
            self.log(f"조회 테스트 성공: {len(chips)}편 (로그인·예약 없음)", "success")
        except UserStopped:
            self.update(status="stopped", message="중지됨")
        except Exception as exc:
            msg = describe_error(exc)
            self.log(msg, "error")
            self.update(status="error", message=msg)


RUNNER = Runner()
ORIGINAL_NOTIFY = kr.notify
kr.log = RUNNER.log          # Bot 내부의 log()/notify() 를 화면으로 연결
kr.notify = RUNNER.notify


class UIBot(kr.Bot):
    runner: Runner

    def login_with_retry(self) -> None:
        self.runner.update(status="login", message="로그인 중")
        super().login_with_retry()
        self.runner.update(status="polling", message="빈자리 조회 중")

    def poll_once(self) -> bool:
        if self.runner.stop_event.is_set():
            raise UserStopped()
        self.runner.update(attempts=self.attempts)
        return super().poll_once()

    def try_reserve(self, train) -> bool:
        self.runner.update(status="reserving", message=f"{kr.train_label(train)} 예약 시도")
        done = super().try_reserve(train)
        if done:
            self.runner.update(result=f"{kr.train_label(train)} {train.dep_name}→{train.arr_name}")
        else:
            self.runner.update(status="polling", message="빈자리 조회 중")
        return done


class TrackingKorail:
    """조회 결과를 화면의 열차 칩으로 보여주기 위해 trains.search 만 가로챈다."""

    def __init__(self, inner, runner: Runner) -> None:
        self._inner = inner
        self._runner = runner
        self.trains = _TrainsProxy(inner.trains, runner)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _TrainsProxy:
    def __init__(self, inner, runner: Runner) -> None:
        self._inner = inner
        self._runner = runner

    def search(self, *args, **kwargs):
        stamp = f"{datetime.now(kr.KST):%H:%M:%S}"
        try:
            trains = self._inner.search(*args, **kwargs)
        except kr.NoResultsError:
            self._runner.update(lastCheck=stamp, trains=[])
            raise
        self._runner.update(lastCheck=stamp, trains=[train_chip(t) for t in trains][:12])
        return trains

    def __getattr__(self, name):
        return getattr(self._inner, name)


# --------------------------------------------------------------------------- 변환·검증


def classify(msg: str) -> str:
    if re.search(r"예약 성공|결제 완료|조회 성공|로그인 완료|모의 예약 완료|테스트 완료", msg):
        return "success"
    if re.search(r"오류|실패|종료|거절|이용제한|중단", msg):
        return "error"
    if re.search(r"쉬었다|다시 로그인|취소|그새|아님|확인 필요|중지", msg):
        return "warn"
    return "info"


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, kr.StationNotFoundError):
        return f"역 이름 오류: {exc}"
    if isinstance(exc, kr.KorailError):
        return f"코레일 요청 거절 (코드 {exc.code}). 서버 연결·호환성 또는 계정 상태를 확인하세요."
    # 통신 오류 메시지에는 프록시 주소 등이 섞일 수 있어 종류만 보여준다
    return f"실행 실패 ({type(exc).__name__}). 네트워크와 설치 상태를 확인하세요."


def passengers(args):
    return (([kr.AdultPassenger(args.adult)] if args.adult else [])
            + ([kr.ChildPassenger(args.child)] if args.child else [])
            + ([kr.SeniorPassenger(args.senior)] if args.senior else []))


def train_chip(train) -> dict:
    t = train.dep_time or ""
    def has(method):
        try:
            return bool(getattr(train, method)())
        except Exception:
            return False
    return {"no": (train.train_no or "").lstrip("0") or "0", "type": train.train_type_name,
            "time": f"{t[:2]}:{t[2:4]}" if len(t) >= 4 else "",
            "general": has("has_general_seat"), "special": has("has_special_seat")}


def summary_text(args, kind: str) -> str:
    mode = {"reserve": "예약까지", "pay": "결제까지", "dryrun": "테스트", "demo": "데모",
            "check": "조회 테스트"}[kind]
    return f"{args.dep}→{args.arr} · {args.date:%m/%d} · {mode}"


def build_args(payload: dict) -> tuple[argparse.Namespace, object, str]:
    """화면 입력을 CLI 와 똑같은 argparse 검증에 통과시킨다."""
    kind = str(payload.get("mode", "reserve"))
    if kind not in {"reserve", "pay", "dryrun", "demo", "check"}:
        raise ValueError("처리 방식이 올바르지 않습니다")

    def text(key: str, default: str = "") -> str:
        value = payload.get(key, default)
        return str(value).strip() if value is not None else default

    argv = [
        "--dep", text("dep"), "--arr", text("arr"), "--date", text("date"),
        "--from", text("from"), "--to", text("to"),
        "--adult", text("adult", "1") or "0", "--child", text("child", "0") or "0",
        "--senior", text("senior", "0") or "0",
        "--train", text("train", "ktx"), "--seat", text("seat", "general-first"),
        "--trains", text("trains"),
        "--interval-min", text("intervalMin", "1") or "1", "--interval-max", text("intervalMax", "2") or "2",
    ]
    if payload.get("adjacent"):
        argv.append("--adjacent")
    argv += {"pay": ["--pay"], "dryrun": ["--dry-run"], "demo": ["--demo"], "check": ["--check"]}.get(kind, [])

    parser = kr.build_parser()

    def fail(message: str):
        raise ValueError(message)
    parser.error = fail  # 종료 대신 화면에 오류 표시
    args = parser.parse_args(argv)
    kr.validate(args, parser)

    if kind in {"reserve", "pay", "dryrun"}:
        args.korail_id = text("loginId") or os.environ.get("KORAIL_ID", "")
        args.korail_pw = str(payload.get("loginPassword") or "") or os.environ.get("KORAIL_PASSWORD", "")
        if not args.korail_id or not args.korail_pw:
            raise ValueError("코레일 아이디와 비밀번호를 입력하세요")

    kr.load_backend(args.demo)

    card = None
    if kind == "pay":
        card = build_card(payload.get("card") or {})
    return args, card, kind


def build_card(data: dict):
    number = re.sub(r"[ -]", "", str(data.get("number", "")))
    expire = str(data.get("expire", "")).strip()
    password = str(data.get("password", "")).strip()
    verify = str(data.get("birth", "")).strip()
    if not re.fullmatch(r"[0-9]{13,19}", number):
        raise ValueError("카드번호는 숫자 13~19자리여야 합니다")
    if not re.fullmatch(r"[0-9]{4}", expire) or not 1 <= int(expire[2:]) <= 12:
        raise ValueError("유효기간은 YYMM 네 자리로 입력하세요")
    if not re.fullmatch(r"[0-9]{2}", password):
        raise ValueError("카드 비밀번호 앞 두 자리를 입력하세요")
    if not re.fullmatch(r"[0-9]{6}", verify):
        raise ValueError("생년월일은 YYMMDD 여섯 자리로 입력하세요")
    return kr.Card(number=number, password=password, verify_number=verify, expire=expire)


# --------------------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "ktx-ui"

    def log_message(self, fmt, *args):  # 요청 로그로 터미널을 어지럽히지 않는다
        pass

    # ---- 보안: 로컬 화면에서 온 요청만 받는다 (DNS rebinding·다른 사이트의 요청 차단)

    def _allowed_host(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        return origin is None or origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._allowed_host():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
        url = urlsplit(self.path)
        if url.path == "/api/state":
            try:
                after = int(parse_qs(url.query).get("after", ["0"])[0])
            except ValueError:
                after = 0
            return self._json(HTTPStatus.OK, RUNNER.snapshot(after))
        if url.path == "/api/config":
            return self._json(HTTPStatus.OK, {
                "stations": MAJOR_STATIONS,
                "envLogin": bool(os.environ.get("KORAIL_ID") and os.environ.get("KORAIL_PASSWORD")),
            })
        name = "index.html" if url.path in {"/", "/index.html"} else url.path.lstrip("/")
        path = (UI_DIR / name).resolve()
        if UI_DIR not in path.parents or not path.is_file():
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        extra = {}
        if name == "index.html":
            extra["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                "font-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self._send(HTTPStatus.OK, path.read_bytes(), ctype, extra)

    def do_POST(self) -> None:
        if not (self._allowed_host() and self._same_origin()):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "forbidden"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "JSON 요청만 받습니다"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "요청이 너무 큽니다"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "요청 형식 오류"})

        path = urlsplit(self.path).path
        if path == "/api/start":
            try:
                if RUNNER.running():
                    raise ValueError("이미 실행 중입니다")
                args, card, kind = build_args(payload)
                RUNNER.start(args, card, kind)
            except ValueError as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except (ImportError, AttributeError):
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                    "error": "pykorail을 불러올 수 없습니다. setup 을 먼저 실행하세요."})
            return self._json(HTTPStatus.OK, {"ok": True})
        if path == "/api/stop":
            RUNNER.stop()
            return self._json(HTTPStatus.OK, {"ok": True})
        if path == "/api/clear":
            with RUNNER.lock:
                RUNNER.logs.clear()
            return self._json(HTTPStatus.OK, {"ok": True})
        return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def already_running(url: str) -> bool:
    """같은 포트에 이 화면이 이미 떠 있는지 확인한다."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 로컬 주소는 프록시를 타지 않게
    try:
        with opener.open(url + "/api/config", timeout=2) as res:
            return "stations" in json.loads(res.read())
    except Exception:
        return False


def main() -> int:
    port = int(os.environ.get("PORT", "3939"))
    url = f"http://{HOST}:{port}"
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError:
        if already_running(url):
            print(f"이미 실행 중입니다. 화면을 다시 엽니다: {url}")
            if os.environ.get("NO_OPEN") != "1":
                webbrowser.open(url)
            return 0
        print(f"포트 {port}를 다른 프로그램이 쓰고 있습니다. PORT=4000 처럼 바꿔 실행하세요.", file=sys.stderr)
        return 2
    print(f"KTX 자동 예매 화면: {url}\n종료하려면 Ctrl+C", flush=True)
    if os.environ.get("NO_OPEN") != "1":
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        RUNNER.stop()
        print("\n종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
