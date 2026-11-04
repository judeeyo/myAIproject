"""Optional Discord webhook notifications. URLs are credentials; never log them."""
import argparse
import getpass
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A webhook credential must never follow redirects to another destination.
        return None


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "discord.com"
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"/api/(?:v\d+/)?webhooks/[0-9]+/[A-Za-z0-9_-]+", parsed.path)):
        raise ValueError("discord.com의 웹훅 주소를 입력하세요")
    return url


def save_url(directory, url):
    validate_url(url)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix="discord-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"webhook_url": url}, stream)
        os.replace(temporary, directory / "discord.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def send(message, directory):
    """True=accepted, False=failed, None=not configured. Never exposes URL/errors."""
    try:
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        config = directory / "discord.json"
        if not url:
            if not config.exists():
                return None
            url = json.loads(config.read_text())["webhook_url"]
        validate_url(url)
        payload = json.dumps({"content": message[:1900], "allowed_mentions": {"parse": []}},
                             ensure_ascii=False).encode("utf-8")
        request = Request(url, data=payload, method="POST",
                          headers={"Content-Type": "application/json", "User-Agent": "KTXReserve/1.0"})
        with build_opener(NoRedirects()).open(request, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="디스코드 예약 성공 알림 설정")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--setup", action="store_true", help="웹훅 주소를 화면에 표시하지 않고 입력·저장")
    group.add_argument("--test", action="store_true", help="설정한 디스코드 채널에 시험 메시지 전송")
    parser.add_argument("--state-dir", type=Path,
                        default=Path(os.environ.get("KTX_STATE_DIR", str(Path.home() / ".ktx_reserve"))))
    args = parser.parse_args()
    if args.setup:
        try:
            url = getpass.getpass("디스코드 웹훅 URL (입력해도 화면에 안 보입니다): ").strip()
            save_url(args.state_dir, url)
        except (ValueError, OSError):
            print("저장 실패. 웹훅 URL과 저장 폴더 권한을 확인하세요.")
            return 1
        except (EOFError, KeyboardInterrupt):
            print("설정을 취소했습니다.")
            return 1
        print("웹훅 저장 완료. --test로 휴대폰 알림을 확인하세요.")
        return 0
    result = send("🔔 KTX 예약 알림 연결 테스트입니다. 실제 예약은 하지 않았습니다.", args.state_dir)
    if result:
        print("디스코드 전송 성공. 휴대폰에서 알림을 확인하세요.")
        return 0
    print("전송 실패. --setup으로 웹훅을 설정하고 네트워크·채널 설정을 확인하세요.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
