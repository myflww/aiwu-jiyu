"""Offline stable-release plugin checks; no profile, bot, or network writes."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from backend import plugin_updates, plugins


class PluginUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aiwu-plugin-updates-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "assets/plugins"
        self.installed = self.root / "runtime/data/plugins"
        self.bundle.mkdir(parents=True)
        self.patchers = [patch.object(plugins, "BUNDLE_ROOT", self.bundle), patch.object(plugins, "PLUGIN_ROOT", self.installed)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manifest = {"schema_version": 1, "plugins": []}
        for i, plugin_id in enumerate(plugin_updates.PLUGIN_IDS):
            self.manifest["plugins"].append({"id": plugin_id, "name": f"Plugin {i + 1}", "version": "v1.0.0", "files": []})
            self.metadata(self.bundle / plugin_id, "v1.0.0")
            target = self.installed / plugin_id if i < 3 else self.installed / ".aiwu-disabled" / (plugin_id + ".disabled")
            self.metadata(target, "v1.0.0")
        self.write_bundle_manifest()
        self.remote = json.dumps(self.manifest).encode()
        self.requests = []
        self.now = 1000

    @staticmethod
    def metadata(directory, version):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "main.py").write_text("# never imported\n", encoding="utf-8")
        (directory / "metadata.yaml").write_text(f"name: Example\nversion: '{version}'\n", encoding="utf-8")

    def write_bundle_manifest(self):
        (self.bundle / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def opener(self, url, timeout=8):
        self.requests.append((url, timeout))
        if isinstance(self.remote, Exception):
            raise self.remote
        return io.BytesIO(self.remote)

    def service(self):
        return plugin_updates.PluginUpdates(opener=self.opener, clock=lambda: self.now)

    def checked(self, service=None, tag="v1.2.0", force=False):
        service = service or self.service()
        service.start_check(tag, force=force)
        if service.worker:
            service.worker.join(2)
            self.assertFalse(service.worker.is_alive())
        return service

    def test_inventory_is_local_detached_and_reads_disabled_preinstalled_metadata(self):
        with patch.object(plugin_updates, "_open", side_effect=AssertionError("network forbidden")), \
             patch.object(plugins, "_inactive_modules", side_effect=AssertionError("native DB forbidden")):
            result = plugin_updates.inventory()
        self.assertEqual(len(result["plugins"]), 5)
        self.assertTrue(all(row["is_installed"] for row in result["plugins"]))
        self.assertTrue(all(row["installed"] == row["bundled"] == "1.0.0" for row in result["plugins"]))
        self.assertTrue(all(row["latest"] is None for row in result["plugins"]))
        self.assertEqual(result["state"], "unchecked")
        self.assertEqual(self.requests, [])
        result["plugins"].clear()
        self.assertEqual(len(plugin_updates.inventory()["plugins"]), 5)

    def test_stable_tag_manifest_compares_installed_and_bundled_versions(self):
        remote = json.loads(self.remote)
        remote["plugins"][0]["version"] = "v1.1.0"
        self.remote = json.dumps(remote).encode()
        with patch.object(plugins, "install_bundled", side_effect=AssertionError("must not install")), \
             patch.object(plugins, "set_enabled", side_effect=AssertionError("must not toggle")):
            status = self.checked().snapshot()
        self.assertEqual(self.requests, [("https://raw.githubusercontent.com/myflww/aiwu-jiyu/v1.2.0/assets/plugins/manifest.json", 8)])
        self.assertEqual(status["state"], "checked")
        self.assertTrue(status["available"] and status["update_available"])
        self.assertEqual(status["plugins"][0]["status"], "update_available")
        self.assertEqual(status["plugins"][0]["latest"], "1.1.0")
        self.assertEqual(status["plugins"][0]["update_via"], "app")
        self.assertEqual(status["plugins"][1]["status"], "current")

    def test_installed_old_bundle_current_is_still_outdated_and_local_ahead_is_current(self):
        first, second = plugin_updates.PLUGIN_IDS[:2]
        self.metadata(self.bundle / first, "v1.2.0")
        self.metadata(self.bundle / second, "v2.0.0")
        self.metadata(self.installed / second, "v2.0.0")
        data = json.loads(self.remote)
        data["plugins"][0]["version"] = "1.2.0"
        data["plugins"][1]["version"] = "v1.9.0"
        self.remote = json.dumps(data).encode()
        rows = self.checked().snapshot()["plugins"]
        self.assertEqual(rows[0]["bundled"], "1.2.0")
        self.assertEqual(rows[0]["installed"], "1.0.0")
        self.assertTrue(rows[0]["available"])
        self.assertEqual(rows[1]["status"], "current")
        self.assertFalse(rows[1]["available"])

    def test_missing_or_unknown_metadata_never_claims_latest(self):
        first = plugin_updates.PLUGIN_IDS[0]
        (self.installed / first / "metadata.yaml").write_text("version: unknown\n", encoding="utf-8")
        missing = plugin_updates.PLUGIN_IDS[-1]
        folder = self.installed / ".aiwu-disabled" / (missing + ".disabled")
        (folder / "main.py").unlink()
        (folder / "metadata.yaml").unlink()
        folder.rmdir()
        result = self.checked().snapshot()
        self.assertIsNone(result["plugins"][0]["installed"])
        self.assertEqual(result["plugins"][0]["status"], "unknown")
        self.assertEqual(result["plugins"][-1]["status"], "not_installed")
        self.assertFalse(result["available"])

    def test_older_release_missing_new_plugins_is_unknown_not_outdated(self):
        data = json.loads(self.remote)
        data["plugins"] = data["plugins"][:3]
        self.remote = json.dumps(data).encode()
        result = self.checked().snapshot()
        self.assertFalse(result["available"])
        self.assertEqual([row["status"] for row in result["plugins"][-2:]], ["unknown", "unknown"])

    def test_nonblocking_duplicate_checks_and_snapshot_do_not_start_network(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(url, timeout=8):
            self.requests.append((url, timeout))
            entered.set()
            release.wait(2)
            return io.BytesIO(self.remote)
        service = plugin_updates.PluginUpdates(opener=blocked)
        try:
            began = time.monotonic()
            result = service.start_check("v1.2.0")
            self.assertLess(time.monotonic() - began, .5)
            self.assertTrue(entered.wait(1))
            self.assertTrue(result["checking"])
            service.start_check("v1.2.0", force=True)
            service.snapshot()
            self.assertEqual(len(self.requests), 1)
        finally:
            release.set()
            service.worker.join(2)

    def test_successful_six_hour_cache_refreshes_inventory_and_force_bypasses(self):
        service = self.checked()
        self.now += 60
        service.start_check("v1.2.0")
        self.assertEqual(len(self.requests), 1)
        self.metadata(self.installed / plugin_updates.PLUGIN_IDS[0], "v0.9.0")
        self.assertTrue(service.snapshot()["plugins"][0]["available"])
        self.checked(service, force=True)
        self.assertEqual(len(self.requests), 2)
        self.now += plugin_updates.CACHE_SECONDS + 1
        self.checked(service)
        self.assertEqual(len(self.requests), 3)

    def test_failure_is_unknown_backed_off_and_manual_check_can_retry(self):
        self.remote = URLError("private details must not reach UI")
        service = self.checked()
        result = service.snapshot()
        self.assertEqual(result["state"], "unavailable")
        self.assertFalse(result["available"])
        self.assertNotIn("private", result["error"])
        self.assertTrue(all(row["status"] == "unknown" for row in result["plugins"]))
        self.now += 1
        service.start_check("v1.2.0")
        self.assertEqual(len(self.requests), 1)
        self.remote = json.dumps(self.manifest).encode()
        self.checked(service, force=True)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(service.snapshot()["state"], "checked")

    def test_bad_manifests_do_not_claim_current(self):
        duplicate = json.loads(self.remote)
        duplicate["plugins"].append(duplicate["plugins"][0])
        invalid = json.loads(self.remote)
        invalid["plugins"][0]["version"] = "https://example.com/code"
        for body in (b"[]", b"{bad json", json.dumps(duplicate).encode(), json.dumps(invalid).encode(),
                     b" " * (plugin_updates.MAX_MANIFEST_BYTES + 1)):
            self.remote = body
            result = self.checked().snapshot()
            self.assertEqual(result["state"], "unavailable")
            self.assertFalse(result["available"])

    def test_main_prerelease_path_injection_and_foreign_sources_are_rejected(self):
        service = self.service()
        for value in ("main", "HEAD", "v1.2.0rc1", "1.2.0-beta", "../../main", "v1.2.0?token=x", "https://evil.example"):
            with self.assertRaises(ValueError):
                service.start_check(value)
        self.assertEqual(self.requests, [])
        safe = plugin_updates._manifest_url("v1.2.0")
        self.assertTrue(plugin_updates._trusted_url(safe))
        for url in (safe.replace("https:", "http:"), safe.replace("raw.githubusercontent.com", "raw.githubusercontent.com.evil.example"),
                    safe.replace("myflww/aiwu-jiyu", "evil/repo"), safe.replace("v1.2.0", "main"), safe + "?token=x"):
            self.assertFalse(plugin_updates._trusted_url(url))
        with self.assertRaises(ValueError):
            plugin_updates._SafeRedirect(safe).redirect_request(None, None, 302, "", {}, "https://evil.example/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
