"""Explicit credential reveal using synthetic local fixtures, never real secrets."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import dashboard_credentials as credentials

INITIAL = "SyntheticInitial42"
CHANGED = "SyntheticChanged43"


def md5(password):
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def pbkdf2(password, iterations=1000):
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-dashboard-reveal-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("ASTRBOT", "NAPCAT", "LOGS", "RUN_DIR"):
            path = root / name.lower()
            path.mkdir()
            self.addCleanup(patch.stopall)
            patch.object(credentials, name, path).start()
        self.astrbot = credentials.ASTRBOT / "data/cmd_config.json"
        self.napcat = credentials.NAPCAT / "napcat/config/webui.json"
        self.cache = credentials.RUN_DIR / "astrbot-initial-login.enc"
        self.log = credentials.LOGS / "astrbot.log"

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def configure_astrbot(self, **fields):
        dashboard = {"enable": True, "username": "synthetic-owner", "password": md5(INITIAL),
                     "pbkdf2_password": "", "service_account_secret": "never-show-service-secret",
                     "jwt_secret": "never-show-jwt-secret", "totp": {"enable": False, "secret": "never-show-totp"}}
        dashboard.update(fields)
        self.write(self.astrbot, {"dashboard": dashboard, "provider": [{"api_key": "never-show-api-key"}]})

    def initial_log(self, value=INITIAL):
        self.log.write_text(f"Synthetic startup\nInitial password: {value}\n", encoding="utf-8")

    @staticmethod
    def fields(result):
        return {row["id"]: row for row in result["fields"]}

    def test_import_has_no_credential_reads(self):
        with patch.object(Path, "open", side_effect=AssertionError("Import must not open credentials")), \
             patch.object(Path, "lstat", side_effect=AssertionError("Import must not inspect credentials")):
            runpy.run_path(str(ROOT / "app/backend/dashboard_credentials.py"), run_name="_credential_import_only")

    def test_invalid_target_never_reads_or_guesses_path(self):
        with patch.object(credentials, "_json") as read:
            for value in ("ASTRBOT", "../astrbot", "all", "", None, [], {}):
                with self.assertRaises(ValueError): credentials.reveal(value)
            read.assert_not_called()

    def test_md5_initial_password_is_labeled_and_current_username_is_real(self):
        self.configure_astrbot()
        self.initial_log()
        result = credentials.reveal("astrbot")
        fields = self.fields(result)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["verification"], "matches_saved_configuration")
        self.assertEqual(fields["username"]["value"], "synthetic-owner")
        self.assertEqual(fields["password"]["value"], INITIAL)
        self.assertIn("初始密码", fields["password"]["label"])
        self.assertTrue(fields["password"]["sensitive"])
        self.assertNotIn(INITIAL, result["login_url"])

    def test_pbkdf2_is_authoritative_over_old_md5_even_if_flag_is_false(self):
        self.configure_astrbot(pbkdf2_password=pbkdf2(CHANGED), password_storage_upgraded=False)
        self.initial_log()
        result = credentials.reveal("astrbot")
        self.assertEqual(result["status"], "initial_expired")
        self.assertEqual(result["verification"], "expired")
        self.assertNotIn("password", self.fields(result))
        self.assertNotIn(INITIAL, json.dumps(result))
        self.assertNotIn(CHANGED, json.dumps(result))

    def test_pbkdf2_matching_initial_is_verified_without_login(self):
        self.configure_astrbot(pbkdf2_password=pbkdf2(INITIAL), password=md5(CHANGED))
        self.initial_log()
        with patch("urllib.request.urlopen", side_effect=AssertionError("Must not log in")):
            result = credentials.reveal("astrbot")
        self.assertEqual(result["status"], "available")
        self.assertEqual(self.fields(result)["password"]["value"], INITIAL)

    def test_unknown_or_excessive_hash_does_not_fall_back_to_stale_md5(self):
        for modern in (f"pbkdf2_sha256$999999999$aa${'00' * 32}", f"pbkdf2_sha256$1000$$"):
            self.configure_astrbot(pbkdf2_password=modern)
            self.initial_log()
            with patch.object(hashlib, "pbkdf2_hmac", side_effect=AssertionError("Unsafe cost must not run")):
                result = credentials.reveal("astrbot")
            self.assertNotIn("password", self.fields(result))
            self.assertEqual(result["status"], "unavailable")

    def test_malformed_pbkdf2_syntax_uses_sdk_legacy_selection(self):
        self.configure_astrbot(pbkdf2_password="pbkdf2_sha256$not-an-integer$aa$aa")
        self.initial_log()
        self.assertEqual(credentials.reveal("astrbot")["status"], "available")

    def test_missing_initial_does_not_offer_hash_or_internal_service_secrets(self):
        self.configure_astrbot()
        result = credentials.reveal("astrbot")
        serialized = json.dumps(result)
        self.assertEqual(result["status"], "initial_missing")
        self.assertEqual(set(self.fields(result)), {"username"})
        for value in (md5(INITIAL), "never-show-service-secret", "never-show-jwt-secret", "never-show-totp", "never-show-api-key"):
            self.assertNotIn(value, serialized)

    def test_missing_or_malformed_configuration_never_invents_default_account(self):
        for data in (None, [], {}, {"dashboard": {"password": md5(INITIAL)}}):
            if data is not None: self.write(self.astrbot, data)
            result = credentials.reveal("astrbot")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["fields"], [])

    def test_existing_dpapi_cache_is_read_only_and_plain_cache_not_accepted(self):
        self.configure_astrbot()
        self.cache.write_text("dpapi:synthetic-opaque-ciphertext", encoding="utf-8")
        before = {path: path.read_bytes() for path in Path(self.temp.name).rglob("*") if path.is_file()}
        with patch.object(credentials.dpapi, "unprotect", return_value=INITIAL) as decrypt, \
             patch.object(credentials.dpapi, "protect", side_effect=AssertionError("Reveal must not create secrets")):
            result = credentials.reveal("astrbot")
        self.assertEqual(result["status"], "available")
        decrypt.assert_called_once_with("dpapi:synthetic-opaque-ciphertext")
        after = {path: path.read_bytes() for path in Path(self.temp.name).rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.cache.write_text("plain:synthetic-legacy-cache", encoding="utf-8")
        with patch.object(credentials.dpapi, "unprotect") as decrypt:
            self.assertEqual(credentials.reveal("astrbot")["status"], "initial_missing")
            decrypt.assert_not_called()

    def test_latest_log_candidate_can_match_when_encrypted_cache_is_stale(self):
        self.configure_astrbot()
        self.cache.write_text("dpapi:synthetic-opaque-ciphertext", encoding="utf-8")
        self.initial_log()
        with patch.object(credentials.dpapi, "unprotect", return_value=CHANGED):
            self.assertEqual(credentials.reveal("astrbot")["status"], "available")

    def test_log_read_is_bounded_and_accepts_terminal_color(self):
        self.configure_astrbot()
        self.log.write_bytes((f"Initial password: {INITIAL}\n".encode("utf-8") + b"x" * (credentials._LOG_LIMIT + 20) + b"\n"))
        self.assertEqual(credentials.reveal("astrbot")["status"], "initial_missing")
        with self.log.open("ab") as stream:
            stream.write(f"\x1b[33mInitial password: {INITIAL}\x1b[0m\n".encode("utf-8"))
        self.assertEqual(credentials.reveal("astrbot")["status"], "available")

    def test_totp_and_disabled_state_are_reported_without_exposing_recovery_material(self):
        self.configure_astrbot(enable=False, totp={"enable": True, "secret": "never-show-totp", "recovery_code_hash": "never-show-recovery"})
        self.initial_log()
        result = credentials.reveal("astrbot")
        self.assertEqual(result["status"], "disabled")
        self.assertTrue(any("二步验证" in warning for warning in result["warnings"]))
        self.assertNotIn("never-show-recovery", json.dumps(result))

    def test_napcat_displays_actual_token_and_no_fictional_username_or_qq_password(self):
        self.write(self.napcat, {"token": "synthetic-webui-token", "password": "never-show-qq-password", "host": "0.0.0.0"})
        result = credentials.reveal("napcat")
        self.assertEqual(result["auth_type"], "token")
        self.assertEqual(result["status"], "available")
        self.assertEqual(set(self.fields(result)), {"token"})
        self.assertEqual(self.fields(result)["token"]["value"], "synthetic-webui-token")
        self.assertNotIn("synthetic-webui-token", result["login_url"])
        self.assertNotIn("never-show-qq-password", json.dumps(result))

    def test_napcat_uninitialized_disabled_or_invalid_token_is_not_presented_as_usable(self):
        for config, expected in (({}, "initial_missing"), ({"token": "napcat"}, "initial_missing"),
                                 ({"token": ["bad"]}, "initial_missing"),
                                 ({"token": "valid-test-token", "disableWebUI": True}, "disabled")):
            self.write(self.napcat, config)
            result = credentials.reveal("napcat")
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["fields"], [])

    def test_each_reveal_reads_only_requested_dashboard_and_does_not_log(self):
        self.configure_astrbot()
        self.initial_log()
        with patch.object(credentials, "_napcat", side_effect=AssertionError("Must not read other dashboard")), \
             patch("builtins.print") as output, patch("logging.Logger._log") as logs:
            credentials.reveal("astrbot")
            output.assert_not_called()
            logs.assert_not_called()
        self.write(self.napcat, {"token": "synthetic-webui-token"})
        with patch.object(credentials, "_astrbot", side_effect=AssertionError("Must not read other dashboard")):
            credentials.reveal("napcat")

    def test_linked_and_oversized_files_are_rejected(self):
        self.configure_astrbot()
        self.initial_log()
        backup = self.astrbot.with_name("synthetic-copy.json")
        os.link(self.astrbot, backup)
        self.assertEqual(credentials.reveal("astrbot")["status"], "unavailable")
        backup.unlink()
        self.astrbot.write_bytes(b" " * (credentials._CONFIG_LIMIT + 1))
        self.assertEqual(credentials.reveal("astrbot")["status"], "unavailable")
        self.write(self.napcat, {"token": "synthetic-webui-token"})
        outside = self.napcat.with_name("target.json")
        self.napcat.rename(outside)
        try:
            self.napcat.symlink_to(outside)
        except OSError:
            return
        self.assertEqual(credentials.reveal("napcat")["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
