import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_backend  # noqa: E402
import ui_server as u  # noqa: E402


def payload(**extra):
    base = {"dep": "서울", "arr": "부산", "date": datetime.now().strftime("%Y-%m-%d"),
            "from": "00:00", "to": "23:59", "adult": 1, "mode": "demo"}
    base.update(extra)
    return base


class BuildArgsTest(unittest.TestCase):
    def test_rejects_invalid_input(self):
        cases = [
            payload(arr="서울"),
            payload(adult=0),
            payload(mode="reserve"),  # 로그인 정보 없음
            payload(mode="hack"),
            payload(mode="pay", loginId="a", loginPassword="b", card={"number": "12"}),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                u.build_args(case)

    def test_demo_and_flags(self):
        args, card, kind = u.build_args(payload(adult=2, adjacent=True))
        self.assertEqual(kind, "demo")
        self.assertTrue(args.demo and args.adjacent)
        self.assertIsNone(card)


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_search = demo_backend.Korail.search

    def tearDown(self):
        demo_backend.Korail.search = self.original_search
        u.RUNNER.stop()
        if u.RUNNER.thread:
            u.RUNNER.thread.join(5)
        self.tmp.cleanup()

    def start(self, **extra):
        args, card, kind = u.build_args(payload(**extra))
        args.state_dir = Path(self.tmp.name)
        u.RUNNER.start(args, card, kind)
        return args

    def wait_until(self, predicate, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_demo_run_succeeds(self):
        self.start()
        self.assertTrue(self.wait_until(lambda: not u.RUNNER.running()))
        snap = u.RUNNER.snapshot(0)
        self.assertEqual(snap["state"]["status"], "success")
        self.assertTrue(any("모의 예약 완료" in log["msg"] for log in snap["logs"]))

    def test_stop_interrupts_polling_and_clears_credentials(self):
        def sold_out(self, *args, **kwargs):
            raise demo_backend.NoResultsError("none")
        demo_backend.Korail.search = sold_out
        args = self.start(intervalMin="0.5", intervalMax="0.5")
        self.assertTrue(self.wait_until(lambda: u.RUNNER.snapshot(0)["state"]["status"] == "polling"))
        u.RUNNER.stop()
        self.assertTrue(self.wait_until(lambda: not u.RUNNER.running()))
        self.assertEqual(u.RUNNER.snapshot(0)["state"]["status"], "stopped")
        self.assertIsNone(args.korail_id)
        self.assertIsNone(args.korail_pw)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), u.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, body=None, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {},
                                     data=None if body is None else body.encode(), method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_serves_page_and_state(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("KTX 자동 예매".encode(), body)
        status, body = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("state", json.loads(body))

    def test_blocks_foreign_requests(self):
        self.assertEqual(self.request("/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/stop", "{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/stop", "{}", {"Content-Type": "application/json",
                                                          "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/../ktx_reserve.py")[0], 404)


if __name__ == "__main__":
    unittest.main()
