import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import discord_notify as discord


URL = "https://discord.com/api/webhooks/123456789/TEST_FAKE_WEBHOOK_TOKEN"


class DiscordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        env = patch.dict(discord.os.environ, {"DISCORD_WEBHOOK_URL": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_unconfigured_does_not_send(self):
        with patch.object(discord, "build_opener") as opener:
            self.assertIsNone(discord.send("test", self.directory))
        opener.assert_not_called()

    def test_setup_file_private(self):
        discord.save_url(self.directory, URL)
        path = self.directory / "discord.json"
        if discord.os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text())["webhook_url"], URL)

    def test_reject_wrong_destinations(self):
        for url in ("http://discord.com/api/webhooks/1/token", URL + "?token=secret",
                    URL.replace("discord.com", "discord.com.evil.test"),
                    URL.replace("discord.com", "example.com"), "https://discord.com/users/@me"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                discord.validate_url(url)

    def test_payload_and_status(self):
        discord.save_url(self.directory, URL)
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 204
        with patch.object(discord, "build_opener", return_value=opener):
            self.assertTrue(discord.send("예약 성공 @everyone", self.directory))
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["content"], "예약 성공 @everyone")
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)

    def test_failure_does_not_raise_or_retry(self):
        discord.save_url(self.directory, URL)
        opener = MagicMock()
        opener.open.side_effect = TimeoutError(URL)
        with patch.object(discord, "build_opener", return_value=opener):
            self.assertFalse(discord.send("예약 성공", self.directory))
        opener.open.assert_called_once()

    def test_redirects_not_followed(self):
        self.assertIsNone(discord.NoRedirects().redirect_request(None, None, 302, "", {}, URL))
