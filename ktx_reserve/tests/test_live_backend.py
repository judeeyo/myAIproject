"""Installed SDK integration tests; HTTP replies are fixtures, not live results."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ktx_reserve as app
from live_backend import create_client, device_state
from pykorail import TransportError, Train
from pykorail.device import random_profile


class LiveBackendTests(unittest.TestCase):
    def setUp(self):
        app.load_backend(False)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.day = (datetime.now(app.KST) + timedelta(days=1)).date()
        self.argv = ["--dep", "서울", "--arr", "동대구", "--date", str(self.day),
                     "--from", "13:00", "--to", "15:00", "--adult", "2",
                     "--state-dir", str(self.directory)]

    def test_android_identity_survives_restart(self):
        with create_client(self.directory) as first:
            identity = first.android_id
        with create_client(self.directory) as second:
            self.assertEqual(second.android_id, identity)
        self.assertEqual(stat.S_IMODE((self.directory / "device.json").stat().st_mode), 0o600)

    def test_old_profile_migrates_without_changing_device(self):
        profile = random_profile()
        (self.directory / "device.json").write_text(json.dumps(asdict(profile)))
        self.assertEqual(device_state(self.directory)[0], profile)
        self.assertEqual(device_state(self.directory), device_state(self.directory))

    def test_corrupt_profile_does_not_silently_rotate_identity(self):
        (self.directory / "device.json").write_text("not JSON")
        with self.assertRaises(ValueError): create_client(self.directory)

    def test_trust_and_timeout(self):
        ca = self.directory / "test-ca.pem"
        ca.write_text("placeholder; no request is made")
        with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": str(ca)}):
            with create_client(self.directory) as client:
                session = client._api._session._session
                self.assertEqual(session.verify, str(ca))
                self.assertEqual(session.timeout, 30)

    def test_missing_ca_fails_closed(self):
        with patch.dict(os.environ, {"REQUESTS_CA_BUNDLE": str(self.directory / "absent.pem")}):
            with self.assertRaises(ValueError): create_client(self.directory)

    def train_data(self):
        return {"h_trn_no": "00105", "h_trn_clsf_nm": "KTX", "h_trn_clsf_cd": "100",
                "h_dpt_rs_stn_nm": "서울", "h_arv_rs_stn_nm": "동대구",
                "h_dpt_dt": self.day.strftime("%Y%m%d"), "h_dpt_tm": "130000",
                "h_gen_rsv_cd": "11"}

    def test_check_uses_real_sdk_query_without_login_or_booking(self):
        client = create_client(self.directory)
        responses = [
            {"stns": {"stn": [{"stn_nm": "서울", "stn_cd": "0001"},
                               {"stn_nm": "동대구", "stn_cd": "0015"}]}},
            {"strResult": "SUCC", "trn_infos": {"trn_info": [self.train_data()]}},
        ]
        session = client._api._session._session
        session.post = Mock(side_effect=[SimpleNamespace(status_code=200, text=json.dumps(r)) for r in responses])
        with patch("live_backend.create_client", return_value=client), \
             patch.object(client, "login", side_effect=AssertionError("must not login")), \
             patch("builtins.input", side_effect=AssertionError("must not prompt")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(app.main(self.argv + ["--check"]), 0)
        self.assertIn("1편", output.getvalue())
        self.assertEqual(session.post.call_count, 2)
        call = session.post.call_args
        self.assertIn("ScheduleView", call.args[0])
        self.assertEqual(call.kwargs["params"]["txtPsgFlg_1"], 2)
        self.assertTrue(call.kwargs["headers"]["x-dynapath-m-token"].startswith("201"))

    def test_real_sdk_transport_failure_stops_reservation(self):
        parser = app.build_parser()
        cfg = parser.parse_args(self.argv)
        app.validate(cfg, parser)
        with create_client(self.directory) as client:
            client.reservations.create = Mock(side_effect=TransportError("response lost"))
            bot = app.Bot(cfg, client)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(app.Stop):
                bot.try_reserve(Train.from_response(self.train_data()))
            client.reservations.create.assert_called_once()

    def test_failed_check_has_nonzero_exit_without_credentials(self):
        client = create_client(self.directory)
        client._api._session._session.post = Mock(return_value=SimpleNamespace(status_code=403, text="denied"))
        with patch("live_backend.create_client", return_value=client), \
             patch("builtins.input", side_effect=AssertionError("must not prompt")), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(self.argv + ["--check"]), 1)


if __name__ == "__main__":
    unittest.main()
