"""Offline release/update checks; no installer is executed and no real network is used."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from backend import updater


class Response(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aiwu-update-test-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "updates"
        self.release = updater._release("v1.2.0")
        self.binary = b"MZ" + b"test-setup-not-executable" * 100
        assets = [{"name": self.release["name"], "browser_download_url": self.release["installer_url"]},
                  {"name": "SHA256SUMS.txt", "browser_download_url": self.release["checksums_url"]}]
        self.responses = {
            updater.API_URL: json.dumps({"tag_name": "v1.2.0", "assets": assets}).encode(),
            self.release["installer_url"]: self.binary,
            self.release["checksums_url"]: (hashlib.sha256(self.binary).hexdigest() + "  " + self.release["name"] + "\n").encode(),
        }
        self.requests = []

    def opener(self, url, timeout=8):
        self.requests.append(url)
        if url not in self.responses:
            raise URLError("offline fixture")
        return Response(self.responses[url])

    def service(self, version="1.1.0"):
        return updater.Updater(self.home, version=version, opener=self.opener)

    def checked(self, version="1.1.0"):
        service = self.service(version)
        service.check()
        service.worker.join(3)
        self.assertFalse(service.snapshot()["checking"])
        return service

    def test_available_current_and_versions(self):
        self.assertTrue(self.checked().snapshot()["available"])
        self.assertFalse(self.checked("1.2.0").snapshot()["available"])
        self.assertGreater(updater.version_key("v1.10.0"), updater.version_key("1.9.0"))
        self.assertGreater(updater.version_key("1.2.0"), updater.version_key("1.2.0rc3"))

    def test_offline_reports_error(self):
        self.responses.clear()
        status = self.checked().snapshot()
        self.assertTrue(status["error"])
        self.assertFalse(status["available"])
        self.assertFalse(status["download_verified"])

    def test_rate_limit_uses_official_atom_and_skips_prerelease(self):
        self.responses.pop(updater.API_URL)
        self.responses[updater.FEED_URL] = b'''<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><link href="https://github.com/myflww/aiwu-jiyu/releases/tag/v1.3.0rc1"/></entry>
          <entry><link href="https://github.com/myflww/aiwu-jiyu/releases/tag/v1.2.0"/></entry>
          <entry><link href="https://evil.example/releases/tag/v99.0.0"/></entry>
        </feed>'''
        status = self.checked().snapshot()
        self.assertEqual(status["latest_version"], "1.2.0")
        self.assertTrue(status["available"])

    def test_untrusted_asset_and_url_are_rejected(self):
        with self.assertRaises(ValueError):
            updater._release("v1.2.0", {self.release["name"]: "https://evil.example/setup.exe"})
        for value in ("http://github.com/a", "https://github.com.evil.example/a", "file:///setup.exe"):
            self.assertFalse(updater._trusted_url(value))
        with self.assertRaises(ValueError):
            updater._release("../../setup.exe")

    def test_download_verified_and_install_only_explicit(self):
        service = self.checked()
        with patch.object(updater.subprocess, "Popen") as execute:
            service.download()
            service.worker.join(3)
            status = service.snapshot()
            self.assertTrue(status["downloaded"])
            self.assertTrue(status["download_verified"])
            self.assertEqual(status["progress"], 100)
            execute.assert_not_called()
            service.install()
            execute.assert_called_once()
            arguments, keywords = execute.call_args
            self.assertEqual(arguments, ([str((self.home / self.release["name"]).resolve())],))
            self.assertIs(keywords["shell"], False)
            self.assertIs(keywords["close_fds"], True)
            self.assertEqual(keywords["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_bad_hash_never_leaves_installable_file(self):
        self.responses[self.release["checksums_url"]] = ("0" * 64 + "  " + self.release["name"]).encode()
        service = self.checked()
        service.download()
        service.worker.join(3)
        self.assertFalse(service.snapshot()["download_verified"])
        self.assertIn("SHA-256", service.snapshot()["error"])
        self.assertFalse(list(self.home.glob("*.part")))
        self.assertFalse(list(self.home.glob("*.exe")))
        with patch.object(updater.subprocess, "Popen") as execute:
            with self.assertRaises(ValueError):
                service.install()
            execute.assert_not_called()

    def test_installer_tampering_and_path_change_block_execution(self):
        service = self.checked()
        service.download()
        service.worker.join(3)
        original = service.installer_path
        service.installer_path = self.home.parent / self.release["name"]
        with patch.object(updater.subprocess, "Popen") as execute:
            with self.assertRaises(ValueError):
                service.install()
            service.installer_path = original
            original.write_bytes(b"MZtampered")
            with self.assertRaises(ValueError):
                service.install()
            execute.assert_not_called()

    def test_preferences_persist_and_suppress_only_notification(self):
        service = self.checked()
        self.assertTrue(service.snapshot()["notify"])
        service.preferences({"auto_check": False, "ignored_version": "1.2.0"})
        self.assertTrue(service.snapshot()["available"])
        self.assertFalse(service.snapshot()["notify"])
        reloaded = self.service()
        self.assertFalse(reloaded.snapshot()["auto_check"])
        self.assertEqual(reloaded.snapshot()["ignored_version"], "1.2.0")


if __name__ == "__main__":
    unittest.main()
