"""Verify private local desktop-session integration without launching any services."""
from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import astrbot_api, processes, plugins


class DesktopSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aiwu-session-cache-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        patched = patch.object(astrbot_api, "RUN_DIR", self.home / "run")
        patched.start()
        self.addCleanup(patched.stop)
        astrbot_api._clear_session_token()
        self.addCleanup(astrbot_api._clear_session_token)

    def managed_marker(self, pid="12345"):
        astrbot_api.RUN_DIR.mkdir(parents=True, exist_ok=True)
        marker = astrbot_api.RUN_DIR / "astrbot.pid"
        marker.write_text(pid, encoding="ascii")
        return marker

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

    def test_burst_of_status_apply_and_grant_reuses_one_authentication_only_in_memory(self):
        marker = self.managed_marker()
        with patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-desktop-secret"), \
             patch.object(astrbot_api, "_request", return_value={"data": {"token": "synthetic-cached-token"}}) as request:
            tokens = [astrbot_api.dashboard_token() for _ in range(12)]
        self.assertEqual(tokens, ["synthetic-cached-token"] * 12)
        self.assertEqual(request.call_count, 1)
        self.assertEqual([path.name for path in astrbot_api.RUN_DIR.iterdir()], [marker.name])
        self.assertNotIn("synthetic-cached-token", marker.read_text("ascii"))

    def test_concurrent_authentication_is_single_flight(self):
        self.managed_marker()
        def authenticate(*args, **kwargs):
            time.sleep(.025)
            return {"data": {"token": "synthetic-concurrent-token"}}
        with patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-secret"), \
             patch.object(astrbot_api, "_request", side_effect=authenticate) as request, \
             ThreadPoolExecutor(max_workers=8) as workers:
            tokens = list(workers.map(lambda _: astrbot_api.dashboard_token(), range(16)))
        self.assertEqual(len(set(tokens)), 1)
        self.assertEqual(request.call_count, 1)

    def test_windows_handle_ctime_semantics_do_not_disable_managed_cache(self):
        self.managed_marker()
        original_fstat = os.fstat
        def handle_stat(fd):
            real = original_fstat(fd)
            return SimpleNamespace(st_dev=real.st_dev, st_ino=real.st_ino, st_size=real.st_size,
                                   st_mtime_ns=real.st_mtime_ns, st_ctime_ns=real.st_ctime_ns + 1000000)
        with patch.object(astrbot_api.os, "fstat", side_effect=handle_stat), \
             patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-secret"), \
             patch.object(astrbot_api, "_request", return_value={"data": {"token": "synthetic-token"}}) as request:
            self.assertEqual(astrbot_api.dashboard_token(), "synthetic-token")
            self.assertEqual(astrbot_api.dashboard_token(), "synthetic-token")
        self.assertEqual(request.call_count, 1)

    def test_expired_cache_authenticates_again_without_persisting_or_extending_by_reads(self):
        self.managed_marker()
        clock = [100.0]
        with patch.object(astrbot_api.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-secret"), \
             patch.object(astrbot_api, "_request", side_effect=[{"data": {"token": "first"}}, {"data": {"token": "second"}}]) as request:
            self.assertEqual(astrbot_api.dashboard_token(), "first")
            clock[0] += astrbot_api._SESSION_TOKEN_TTL - 1
            self.assertEqual(astrbot_api.dashboard_token(), "first")
            clock[0] += 2
            self.assertEqual(astrbot_api.dashboard_token(), "second")
        self.assertEqual(request.call_count, 2)

    def test_pid_change_and_same_pid_rewrite_each_invalidate_cached_session(self):
        marker = self.managed_marker()
        with patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-secret"), \
             patch.object(astrbot_api, "_request", side_effect=[{"data": {"token": "one"}}, {"data": {"token": "two"}}, {"data": {"token": "three"}}]) as request:
            self.assertEqual(astrbot_api.dashboard_token(), "one")
            marker.write_text("67890", encoding="ascii")
            self.assertEqual(astrbot_api.dashboard_token(), "two")
            old = marker.stat()
            os.utime(marker, ns=(old.st_atime_ns, old.st_mtime_ns + 1000000))
            self.assertEqual(astrbot_api.dashboard_token(), "three")
        self.assertEqual(request.call_count, 3)

    def test_secret_rotation_and_explicit_launch_signal_do_not_reuse_old_session(self):
        self.managed_marker()
        secret = ["synthetic-first-secret"]
        with patch.object(astrbot_api, "_desktop_secret", side_effect=lambda **kw: secret[0]), \
             patch.object(astrbot_api, "_request", side_effect=[{"data": {"token": "one"}}, {"data": {"token": "two"}}, {"data": {"token": "three"}}]) as request:
            self.assertEqual(astrbot_api.dashboard_token(), "one")
            secret[0] = "synthetic-rotated-secret"
            self.assertEqual(astrbot_api.dashboard_token(), "two")
            astrbot_api.desktop_environment()
            self.assertEqual(astrbot_api.dashboard_token(), "three")
        self.assertEqual(request.call_count, 3)

    def test_missing_or_invalid_pid_marker_cannot_prove_safe_cache_reuse(self):
        marker = self.managed_marker()
        with patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-secret"), \
             patch.object(astrbot_api, "_request", return_value={"data": {"token": "synthetic-token"}}) as request:
            astrbot_api.dashboard_token()
            marker.unlink()
            astrbot_api.dashboard_token()
            astrbot_api.dashboard_token()
            marker.write_text("not-a-process", encoding="ascii")
            astrbot_api.dashboard_token()
            astrbot_api.dashboard_token()
        self.assertEqual(request.call_count, 5)

    def test_rotation_while_authenticating_does_not_return_old_token_to_a_mutation(self):
        marker = self.managed_marker()
        def raced_authentication(*args, **kwargs):
            marker.write_text("67890", encoding="ascii")
            return {"data": {"token": "old-service-token"}}
        with patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-secret"), \
             patch.object(astrbot_api, "_request", side_effect=raced_authentication):
            with self.assertRaisesRegex(RuntimeError, "会话已变化"):
                astrbot_api.dashboard_token()
        self.assertFalse(astrbot_api._session_token_cache)

    def test_401_invalidates_cached_token_but_never_replays_the_mutation(self):
        self.managed_marker()
        with patch.object(astrbot_api, "_desktop_secret", return_value="synthetic-secret"):
            with patch.object(astrbot_api, "_request", return_value={"data": {"token": "old-token"}}):
                token = astrbot_api.dashboard_token()
            error = HTTPError(astrbot_api.BASE + "/api/v1/personas/by-id", 401, "Unauthorized", {}, None)
            with patch.object(astrbot_api.urllib.request, "urlopen", side_effect=error) as remote:
                with self.assertRaises(HTTPError):
                    astrbot_api._request("/api/v1/personas/by-id", {"tools": ["one-selected-tool"]}, token, method="PUT")
                self.assertEqual(remote.call_count, 1)
                self.assertEqual(remote.call_args.args[0].method, "PUT")
            self.assertFalse(astrbot_api._session_token_cache)
            with patch.object(astrbot_api, "_request", return_value={"data": {"token": "new-token"}}) as authenticate:
                self.assertEqual(astrbot_api.dashboard_token(), "new-token")
                authenticate.assert_called_once()

    def test_late_401_for_previous_token_cannot_evict_a_newer_session(self):
        self.managed_marker()
        secret = ["first-synthetic-secret"]
        with patch.object(astrbot_api, "_desktop_secret", side_effect=lambda **kw: secret[0]):
            with patch.object(astrbot_api, "_request", return_value={"data": {"token": "old-token"}}):
                astrbot_api.dashboard_token()
            secret[0] = "second-synthetic-secret"
            with patch.object(astrbot_api, "_request", return_value={"data": {"token": "new-token"}}):
                astrbot_api.dashboard_token()
            error = HTTPError(astrbot_api.BASE + "/api/v1/personas", 401, "Unauthorized", {}, None)
            with patch.object(astrbot_api.urllib.request, "urlopen", side_effect=error):
                with self.assertRaises(HTTPError):
                    astrbot_api._request("/api/v1/personas", token="old-token", method="GET")
            with patch.object(astrbot_api, "_request") as authenticate:
                self.assertEqual(astrbot_api.dashboard_token(), "new-token")
                authenticate.assert_not_called()

    def test_legacy_fallback_still_works_and_uses_same_short_managed_cache(self):
        self.managed_marker()
        with patch.object(astrbot_api, "_desktop_secret", return_value=""), \
             patch.object(astrbot_api, "read_initial_password", return_value="synthetic-legacy-password") as password, \
             patch.object(astrbot_api, "login", return_value="synthetic-legacy-token") as login:
            self.assertEqual(astrbot_api.dashboard_token(), "synthetic-legacy-token")
            self.assertEqual(astrbot_api.dashboard_token(), "synthetic-legacy-token")
        password.assert_called_once_with()
        login.assert_called_once_with("synthetic-legacy-password")


if __name__ == "__main__":
    unittest.main(verbosity=2)
