"""Detection metadata only. No live profile, service, registry or process calls."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import lovehouse


def healthy(version="0.10.4rc1", **changes):
    value = {"ok": True, "name": "爱之屋记忆服务", "version": version,
             "protocol_version": "0.4.0", "mode": "storage"}
    value.update(changes)
    return value


class LoveHouseDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-lh-detection-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "memory-metadata"
        self.data.mkdir()
        self.patchers = [patch.object(lovehouse, "DATA_DIR", self.data),
                         patch.object(lovehouse, "ASTRBOT", self.root / "unused-astrbot"),
                         patch.object(lovehouse, "_find_exe", return_value=None),
                         patch.object(lovehouse, "_read_version", return_value=""),
                         patch.object(lovehouse, "_port", return_value=8768),
                         patch.object(lovehouse, "_health", return_value=None)]
        self.mocks = [p.start() for p in self.patchers]
        for p in self.patchers:
            self.addCleanup(p.stop)
        self.exe, self.readme, self.port, self.health = self.mocks[2:]

    def test_verified_online_service_is_installed_even_when_the_executable_moved(self):
        self.health.return_value = healthy()
        result = lovehouse.detect()
        self.assertTrue(result["installed"])
        self.assertTrue(result["running"])
        self.assertEqual(result["exe"], "")
        self.assertFalse(result["has_exe"])
        self.assertEqual(result["version"], "0.10.4rc1")
        self.assertFalse(result["update_available"])
        self.assertEqual(result["port"], 8768)

    def test_newer_stable_and_release_candidates_are_not_downgrade_prompts(self):
        for version in ("0.10.4rc1", "0.10.2rc2", "0.10.2", "0.11.0a1", "1.0.0", "v0.10.2rc1"):
            with self.subTest(version=version):
                self.health.return_value = healthy(version)
                self.assertFalse(lovehouse.detect()["update_available"])

    def test_older_prereleases_and_older_stable_versions_offer_an_update(self):
        for version in ("0.10.1", "0.10.1rc8", "0.10.2a1", "0.10.2b3", "0.10.2rc0", "0.9.99"):
            with self.subTest(version=version):
                self.health.return_value = healthy(version)
                self.assertTrue(lovehouse.detect()["update_available"])

    def test_stage_order_is_numeric_and_stable_follows_prereleases(self):
        cases = [("0.10.2a9", "0.10.2b1", True), ("0.10.2b10", "0.10.2rc1", True),
                 ("0.10.2rc2", "0.10.2rc10", True), ("0.10.2rc10", "0.10.2rc2", False),
                 ("0.10.2rc9", "0.10.2", True), ("0.10.2", "0.10.2rc9", False),
                 ("0.10.2", "0.10.2", False)]
        for installed, latest, expected in cases:
            with self.subTest(installed=installed, latest=latest):
                self.assertEqual(lovehouse._older_version(installed, latest), expected)

    def test_unknown_versions_never_guess_an_update_or_raise_a_type_error(self):
        for value in ("", "unknown", "development-build", "0.10", "v", None, 123, True, [], {}, "1" * 1000):
            with self.subTest(version_type=type(value).__name__):
                self.assertFalse(lovehouse._older_version(value, lovehouse.KNOWN_LATEST))
                self.assertFalse(lovehouse._older_version(lovehouse.KNOWN_LATEST, value))
        self.health.return_value = healthy("unknown")
        result = lovehouse.detect()
        self.assertTrue(result["running"])
        self.assertFalse(result["update_available"])

    def test_invalid_health_does_not_prove_a_moved_installation_or_crash_detection(self):
        invalid = (None, {}, [], [{"version": "0.10.4rc1"}], "ok", {"version": "0.10.4rc1"},
                   healthy(ok=False), healthy(ok=1), healthy(name="another-service"), healthy(protocol_version="other"),
                   healthy(version=None), healthy(version=123), healthy(version=""), healthy(version=" "),
                   healthy(version="0.10.4rc1\n"), healthy(version="x" * 81))
        for response in invalid:
            with self.subTest(response_type=type(response).__name__):
                self.health.return_value = response
                result = lovehouse.detect()
                self.assertFalse(result["installed"])
                self.assertFalse(result["running"])
                self.assertEqual(result["port"], 0)
                self.assertEqual(result["health"], {})

    def test_an_older_identified_service_without_optional_protocol_metadata_remains_compatible(self):
        value = healthy("0.10.1")
        value.pop("protocol_version")
        self.health.return_value = value
        self.assertTrue(lovehouse.detect()["running"])

    def test_offline_known_executable_remains_installed_and_uses_readme_version(self):
        path = self.root / "known-program" / "LoveHouse.exe"
        self.exe.return_value = path
        self.readme.return_value = "0.10.2"
        result = lovehouse.detect()
        self.assertTrue(result["installed"])
        self.assertTrue(result["has_exe"])
        self.assertFalse(result["running"])
        self.assertEqual(result["exe"], str(path))
        self.assertEqual(result["version"], "0.10.2")
        self.assertFalse(result["update_available"])

    def test_stale_data_without_a_service_is_not_an_installed_executable(self):
        result = lovehouse.detect()
        self.assertFalse(result["installed"])
        self.assertFalse(result["running"])
        self.assertEqual(result["exe"], "")

    def test_fresh_profile_without_installation_does_not_probe_an_unrelated_loopback_service(self):
        self.data.rmdir()
        self.health.return_value = healthy()
        result = lovehouse.detect()
        self.health.assert_not_called()
        self.assertFalse(result["installed"])
        self.assertFalse(result["running"])

    def test_valid_running_metadata_takes_precedence_over_the_older_readme(self):
        self.exe.return_value = self.root / "program" / "LoveHouse.exe"
        self.readme.return_value = "0.9.0"
        self.health.return_value = healthy("0.10.4rc1")
        self.assertEqual(lovehouse.detect()["version"], "0.10.4rc1")


class ConfiguredExecutableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-lh-executable-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.configured = self.root / "moved-program" / "LoveHouse.exe"
        self.configured.parent.mkdir()
        self.configured.write_bytes(b"synthetic file, never executable in tests")
        self.fallback = self.root / "fallback" / "LoveHouse.exe"
        self.fallback.parent.mkdir()
        self.fallback.write_bytes(b"synthetic fallback")
        self.values = {"lovehouse_executable": str(self.configured)}
        self.patches = [patch.object(lovehouse.config_store, "load", side_effect=lambda: self.values),
                        patch.object(lovehouse, "EXE_CANDIDATES", [self.fallback]),
                        patch.object(lovehouse.subprocess, "Popen", side_effect=AssertionError("metadata must not launch"))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_valid_absolute_config_is_preferred_and_nothing_is_launched(self):
        self.assertEqual(lovehouse._find_exe(), self.configured)

    def test_invalid_nonstring_relative_wrong_name_missing_or_directory_falls_back(self):
        directory = self.root / "directory" / "LoveHouse.exe"
        directory.mkdir(parents=True)
        wrong = self.root / "Other.exe"
        wrong.write_bytes(b"synthetic wrong name")
        for value in (None, True, 12, {}, [], "", "LoveHouse.exe", "relative/LoveHouse.exe", str(wrong),
                      str(self.root / "missing" / "LoveHouse.exe"), str(directory), "x" * 5000, "\0LoveHouse.exe"):
            with self.subTest(value_type=type(value).__name__):
                self.values = {"lovehouse_executable": value}
                self.assertEqual(lovehouse._find_exe(), self.fallback)

    def test_invalid_config_with_no_fallback_stays_unlocated(self):
        self.values = {"lovehouse_executable": "relative/LoveHouse.exe"}
        with patch.object(lovehouse, "EXE_CANDIDATES", []):
            self.assertIsNone(lovehouse._find_exe())

    def test_reparse_metadata_on_file_or_parent_is_refused(self):
        original = Path.lstat
        for redirected in (self.configured, self.configured.parent):
            def metadata(path):
                info = original(path)
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if path == redirected else info
            with self.subTest(redirected=redirected.name), patch.object(Path, "lstat", metadata):
                self.assertEqual(lovehouse._find_exe(), self.fallback)

    def test_symlink_metadata_is_refused_even_if_is_file_would_follow_it(self):
        original = Path.lstat
        def metadata(path):
            info = original(path)
            return SimpleNamespace(st_mode=0o120777, st_file_attributes=0) if path == self.configured else info
        with patch.object(Path, "lstat", metadata):
            self.assertEqual(lovehouse._find_exe(), self.fallback)


if __name__ == "__main__":
    unittest.main()
