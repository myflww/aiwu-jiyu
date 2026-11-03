"""Verify private local desktop-session integration without launching any services."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import astrbot_api, processes, plugins


class DesktopSessionTests(unittest.TestCase):
    def test_legacy_login_survives_diagnostic_log_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-login-log-") as temporary, \
             patch.object(astrbot_api, "RUN_DIR", Path(temporary) / "run"), \
             patch.object(astrbot_api, "LOGS", Path(temporary) / "logs"):
            astrbot_api.LOGS.mkdir()
            log = astrbot_api.LOGS / "astrbot.log"
            log.write_text("Initial password: example-legacy-secret\n", encoding="utf-8")
            self.assertTrue(astrbot_api.preserve_log_credentials("astrbot.log"))
            encrypted = (astrbot_api.RUN_DIR / "astrbot-initial-login.enc").read_text("utf-8")
            self.assertNotIn("example-legacy-secret", encrypted)
            log.write_text("", encoding="utf-8")
            self.assertEqual(astrbot_api.read_initial_password(), "example-legacy-secret")

    def test_log_cleanup_waits_when_legacy_login_cannot_be_preserved(self):
        with patch.object(astrbot_api, "read_initial_password", return_value="example-secret"), \
             patch.object(astrbot_api, "_cache_initial_password", return_value=False):
            self.assertFalse(astrbot_api.preserve_log_credentials("astrbot.log"))
            self.assertTrue(astrbot_api.preserve_log_credentials("napcat.log"))

    def test_private_secret_is_reused_and_plaintext_is_not_written(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-session-") as temporary, \
             patch.object(astrbot_api, "RUN_DIR", Path(temporary)):
            environment = astrbot_api.desktop_environment()
            secret = environment["ASTRBOT_DESKTOP_SESSION_SECRET"]
            self.assertGreaterEqual(len(secret), 32)
            self.assertEqual(environment["ASTRBOT_DESKTOP_MANAGED"], "1")
            self.assertEqual(astrbot_api._desktop_secret(), secret)
            self.assertNotIn(secret, Path(temporary, "astrbot-desktop-session.enc").read_text("utf-8"))
            self.assertEqual(astrbot_api.desktop_environment(), environment)

    def test_official_session_endpoint_receives_secret_in_header(self):
        with patch.object(astrbot_api, "_desktop_secret", return_value="example-local-secret"), \
             patch.object(astrbot_api, "_request", return_value={"data": {"token": "example-token"}}) as request:
            self.assertEqual(astrbot_api.dashboard_token(), "example-token")
        request.assert_called_once_with("/api/v1/auth/desktop-session", payload={},
                                        extra_headers={"X-AstrBot-Desktop-Session": "example-local-secret"})

    def test_managed_launch_applies_pending_switches_and_passes_session_environment(self):
        with patch.object(processes.Path, "exists", return_value=True), \
             patch.object(processes, "port_up", return_value=False), \
             patch.object(processes, "_spawn") as spawn, \
             patch.object(processes, "_write_pid"), \
             patch.object(plugins, "apply_pending") as apply, \
             patch.object(plugins, "_pending_switches", return_value={}), \
             patch.object(astrbot_api, "desktop_environment", return_value={"LOCAL_TEST": "value"}):
            processes.start_astrbot()
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(spawn.call_args.kwargs["environment"], {"LOCAL_TEST": "value"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
