import contextlib
import io
import subprocess
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ktx_reserve as app
import demo_backend as demo


class ReservationTests(unittest.TestCase):
    def setUp(self):
        app.load_backend(True)
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)
        notify = patch.object(app, "notify")
        notify.start()
        self.addCleanup(notify.stop)
        self.date = (datetime.now(app.KST) + timedelta(days=1)).date().isoformat()
        self.argv = ["--dep", "서울", "--arr", "동대구", "--date", self.date,
                     "--from", "13:00", "--to", "15:00", "--adult", "2", "--demo"]
        parser = app.build_parser()
        self.cfg = parser.parse_args(self.argv)
        app.validate(self.cfg, parser)
        self.cfg.korail_id = self.cfg.korail_pw = "demo"
        self.client = demo.Korail(self.cfg)
        self.bot = app.Bot(self.cfg, self.client, sleep=Mock())
        self.train = self.client.search("서울", "동대구", depart_after=self.bot.window_start,
                                        train_type=demo.TrainType.KTX)[0]

    def test_demo_cli(self):
        result = subprocess.run([sys.executable, "-B", str(Path(app.__file__)), *self.argv],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("실제 승차권 없음", result.stdout)

    def test_dry_run_never_reserves(self):
        self.cfg.dry_run = True
        self.client.reservations.create = Mock(side_effect=AssertionError("must not reserve"))
        self.assertTrue(self.bot.poll_once())
        self.client.reservations.create.assert_not_called()

    def test_adjacent_rules(self):
        for seats, expected in [([("1", "15A"), ("1", "15B")], True),
                                ([("1", "15A"), ("2", "15B")], False),
                                ([("1", "15"), ("1", "16")], True),
                                ([("1", "15A"), ("1", "15A")], False),
                                ([("1", "15A"), ("1", "16B")], False)]:
            with self.subTest(seats=seats):
                self.assertEqual(app.seats_are_adjacent(seats), expected)

    def test_missing_seats_cancel_and_skip(self):
        self.cfg.adjacent = True
        rsv = self.client.create(self.train, self.bot.passengers, None)
        rsv.seats = []
        self.client.reservations.create = Mock(return_value=rsv)
        self.client.reservations.cancel = Mock()
        self.assertFalse(self.bot.try_reserve(self.train))
        self.client.reservations.cancel.assert_called_once_with(rsv)
        self.assertIn(self.train.train_no, self.bot.skip_until)

    def test_cancellation_failure_stops(self):
        self.cfg.adjacent = True
        rsv = self.client.create(self.train, self.bot.passengers, None)
        rsv.seats = []
        self.client.reservations.create = Mock(return_value=rsv)
        self.client.reservations.cancel = Mock(side_effect=OSError("offline"))
        with self.assertRaises(app.Stop):
            self.bot.try_reserve(self.train)

    def test_waitlist_cancelled(self):
        rsv = self.client.create(self.train, self.bot.passengers, None)
        rsv.is_waiting = True
        self.client.reservations.create = Mock(return_value=rsv)
        self.client.reservations.cancel = Mock()
        self.assertFalse(self.bot.try_reserve(self.train))
        self.client.reservations.cancel.assert_called_once_with(rsv)

    def test_login_failure_not_retried(self):
        self.client.login = Mock(side_effect=demo.LoginFailedError())
        with self.assertRaises(app.Stop): self.bot.login_with_retry()
        self.assertEqual(self.client.login.call_count, 1)
        self.bot.sleep.assert_not_called()

    def test_login_version_rejection_stops(self):
        self.client.login = Mock(side_effect=demo.KorailError(code="-8204"))
        with self.assertRaises(app.Stop): self.bot.login_with_retry()
        self.bot.sleep.assert_not_called()

    def test_login_transient_backoff(self):
        self.client.login = Mock(side_effect=[OSError()] * 5 + [None])
        self.bot.login_with_retry()
        self.assertEqual([c.args[0] for c in self.bot.sleep.call_args_list], [30, 60, 90, 120, 600])

    def test_restricted_and_version_errors_stop(self):
        for code in ("-8204", "-2000"):
            with self.subTest(code=code), patch.object(self.bot, "poll_once", side_effect=demo.KorailError(code=code)):
                with self.assertRaises(app.Stop): self.bot.run()

    def test_session_expiry_logs_in_again(self):
        self.client.login = Mock()
        with patch.object(self.bot, "poll_once", side_effect=[demo.NeedToLoginError(), True]):
            self.bot.run()
        self.assertEqual(self.client.login.call_count, 2)

    def test_macro_cooldown(self):
        with patch.object(self.bot, "poll_once", side_effect=[demo.KorailError(code="MACRO ERROR"), True]):
            self.bot.run()
        self.bot.sleep.assert_called_once_with(60)

    def test_sold_out_retries(self):
        self.client.reservations.create = Mock(side_effect=demo.SoldOutError())
        self.assertFalse(self.bot.try_reserve(self.train))

    def test_unknown_reservation_result_stops(self):
        self.client.reservations.create = Mock(side_effect=TimeoutError())
        with self.assertRaises(app.Stop): self.bot.try_reserve(self.train)
        self.client.reservations.create.assert_called_once()

    def test_payment_timeout_does_not_reserve_again(self):
        self.cfg.demo = False
        self.bot.card = object()
        self.client.reservations.create = Mock(wraps=self.client.create)
        self.client.reservations.pay = Mock(side_effect=TimeoutError())
        self.assertTrue(self.bot.try_reserve(self.train))
        self.client.reservations.create.assert_called_once()
        self.client.reservations.pay.assert_called_once()

    def test_train_filter(self):
        self.cfg.trains = {"999"}
        self.client.trains.search = Mock(return_value=[self.train])
        with patch.object(self.bot, "idle_log") as idle:
            self.assertFalse(self.bot.poll_once())
        self.assertIn("지정 열차번호(999)가 없습니다", idle.call_args.args[0])
        self.assertIn("--trains", idle.call_args.args[0])

    def test_elapsed_window_stops(self):
        self.bot.window_end = datetime.now(app.KST) - timedelta(seconds=1)
        with self.assertRaises(app.Stop): self.bot.poll_once()
        with self.assertRaises(app.Stop): self.bot.login_with_retry()

    def test_invalid_options(self):
        for extra in (["--interval-min", "nan"], ["--interval-max", "inf"], ["--adult", "0"],
                      ["--trains", "abc"], ["--pay"], ["--from", "16:00"]):
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    app.main(self.argv + extra)
                self.assertEqual(exc.exception.code, 2)

    def test_missing_backend_does_not_prompt(self):
        with patch.object(app, "load_backend", side_effect=ModuleNotFoundError()), \
             patch("builtins.input", side_effect=AssertionError("must not prompt")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(self.argv[:-1]), 2)


if __name__ == "__main__":
    unittest.main()
