"""Installer regression tests: synthetic ZIPs and temporary homes only.

No real account configuration, network, user processes or installed runtime is
read or changed. The fixture pin is replaced only within each test.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import config_store, installer, napcat_runtime as runtime


def make_archive(*, extra=(), omit=()):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in runtime.REQUIRED_FILES:
            if name in omit:
                continue
            content = (b"MZ" if name in runtime._PE_FILES else b"//")
            content += b"0" * (runtime._MIN_FILE_BYTES[name] - len(content))
            archive.writestr(name, content)
        for name, content in extra:
            archive.writestr(name, content)
    return output.getvalue()


class NapCatRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-napcat-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.napcat = self.home / "runtime/napcat"
        self.cache = self.home / "downloads" / runtime.ARCHIVE_NAME
        self.payload = make_archive()
        self.operation_lock = threading.Lock()
        for p in (
            patch.object(runtime, "APP_HOME", self.home),
            patch.object(runtime, "NAPCAT", self.napcat),
            patch.object(runtime, "_operation_lock", self.operation_lock),
            patch.object(runtime, "ARCHIVE_BYTES", len(self.payload)),
            patch.object(runtime, "SHA256", hashlib.sha256(self.payload).hexdigest()),
            patch.object(runtime.processes, "port_up", return_value=False),
            patch.object(runtime.downloader, "download", side_effect=AssertionError("network not allowed")),
        ):
            mock = p.start()
            self.addCleanup(p.stop)
            if p.attribute == "download":
                self.download = mock

    def cache_payload(self, payload=None):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(self.payload if payload is None else payload)

    def populate_runtime(self):
        self.napcat.mkdir(parents=True)
        with zipfile.ZipFile(io.BytesIO(self.payload)) as archive:
            archive.extractall(self.napcat)

    def pin(self, payload):
        return patch.multiple(runtime, ARCHIVE_BYTES=len(payload), SHA256=hashlib.sha256(payload).hexdigest())

    def test_missing_inspection_is_read_only(self):
        result = runtime.inspect_napcat()
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["installed"])
        self.assertFalse(result["cache_valid"])
        self.assertFalse(self.home.exists())
        self.download.assert_not_called()

    def test_complete_runtime_is_preserved_without_download_or_replacement(self):
        self.populate_runtime()
        account = self.napcat / "napcat/config/private.json"
        account.parent.mkdir()
        account.write_bytes(b"private-fixture-do-not-replace")
        before = {str(p.relative_to(self.napcat)): p.read_bytes() for p in self.napcat.rglob("*") if p.is_file()}
        self.assertEqual(runtime.inspect_napcat()["status"], "installed")
        self.assertIsNone(runtime.prepare_napcat_archive())
        runtime.install_napcat()
        after = {str(p.relative_to(self.napcat)): p.read_bytes() for p in self.napcat.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.download.assert_not_called()

    def test_bat_only_partial_install_is_protected_even_with_good_cache(self):
        self.cache_payload()
        self.napcat.mkdir(parents=True)
        (self.napcat / "napcat.bat").write_bytes(b"old-launcher")
        (self.napcat / "existing-config.json").write_bytes(b"private-fixture")
        self.assertEqual(runtime.inspect_napcat()["status"], "partial")
        with self.assertRaisesRegex(ValueError, "备份"):
            runtime.install_napcat()
        self.assertEqual((self.napcat / "existing-config.json").read_bytes(), b"private-fixture")
        self.assertEqual((self.napcat / "napcat.bat").read_bytes(), b"old-launcher")

    def test_truncated_script_or_invalid_native_header_is_not_installed(self):
        self.populate_runtime()
        (self.napcat / "napcat/napcat.mjs").write_bytes(b"/")
        (self.napcat / "node.exe").write_bytes(b"XX" + b"0" * 1024**2)
        result = runtime.inspect_napcat()
        self.assertFalse(result["installed"])
        self.assertIn("node.exe", result["missing_files"])
        self.assertIn("napcat/napcat.mjs", result["missing_files"])

    def test_verified_cache_is_reused_without_network(self):
        self.cache_payload()
        self.assertEqual(runtime.inspect_napcat()["status"], "cached")
        self.assertEqual(runtime.prepare_napcat_archive(), self.cache)
        self.download.assert_not_called()

    def test_bad_download_does_not_replace_existing_cache_and_retry_can_recover(self):
        old = b"prior-cache-retained"
        self.cache_payload(old)
        self.download.side_effect = lambda _url, path, **_kw: path.write_bytes(b"invalid")
        with self.assertRaisesRegex(ValueError, "导入"):
            runtime.prepare_napcat_archive()
        self.assertEqual(self.cache.read_bytes(), old)
        self.assertFalse(list(self.cache.parent.glob(".napcat-*.part")))
        self.download.side_effect = lambda _url, path, **_kw: path.write_bytes(self.payload)
        self.assertEqual(runtime.prepare_napcat_archive(), self.cache)
        self.assertEqual(self.cache.read_bytes(), self.payload)

    def test_wrong_hash_transport_is_rejected_before_proxy_fallback(self):
        calls = []
        def deliver(url, path, **_kw):
            calls.append(url)
            payload = self.payload if len(calls) > 1 else self.payload[:-1] + bytes([self.payload[-1] ^ 1])
            path.write_bytes(payload)
        self.download.side_effect = deliver
        runtime.prepare_napcat_archive()
        self.assertEqual(calls, [runtime.ARCHIVE_URL, runtime.MIRRORS[0]])
        self.assertEqual(self.cache.read_bytes(), self.payload)

    def test_offline_import_only_commits_cache_and_invalid_import_preserves_it(self):
        source = Path(self.temp.name) / "import.zip"
        source.write_bytes(self.payload)
        result = runtime.import_napcat_archive(str(source))
        self.assertTrue(result["ok"])
        self.assertTrue(result["napcat"]["cache_valid"])
        self.assertFalse(self.napcat.exists())
        source.write_bytes(self.payload[:-1] + bytes([self.payload[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            runtime.import_napcat_archive(source)
        self.assertEqual(self.cache.read_bytes(), self.payload)
        self.assertFalse(list(self.cache.parent.glob(".napcat-*.part")))
        self.download.assert_not_called()

    def test_offline_import_refuses_running_service_and_busy_install(self):
        source = Path(self.temp.name) / "import.zip"
        source.write_bytes(self.payload)
        with patch.object(runtime.processes, "port_up", return_value=True):
            with self.assertRaisesRegex(ValueError, "正在运行"):
                runtime.import_napcat_archive(source)
        self.operation_lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "正在进行"):
                runtime.import_napcat_archive(source)
        finally:
            self.operation_lock.release()
        self.assertFalse(self.home.exists())

    def test_successful_staging_install_creates_all_entry_points_without_starting(self):
        self.cache_payload()
        runtime.install_napcat()
        self.assertTrue(runtime.runtime_complete())
        self.assertFalse(list(self.napcat.parent.glob(".napcat-install-*")))
        self.assertTrue(self.cache.is_file())
        self.download.assert_not_called()

    def test_extraction_error_cannot_leave_partial_runtime(self):
        self.cache_payload()
        with patch.object(runtime.shutil, "copyfileobj", side_effect=OSError("simulated full disk")):
            with self.assertRaisesRegex(OSError, "full disk"):
                runtime.install_napcat()
        self.assertFalse(self.napcat.exists())
        self.assertFalse(list(self.napcat.parent.glob(".napcat-install-*")))
        self.assertEqual(self.cache.read_bytes(), self.payload)

    def test_existing_empty_directory_is_restored_if_atomic_commit_fails(self):
        self.cache_payload()
        self.napcat.mkdir(parents=True)
        with patch.object(runtime.os, "replace", side_effect=OSError("simulated rename failure")):
            with self.assertRaisesRegex(OSError, "rename failure"):
                runtime.install_napcat()
        self.assertTrue(self.napcat.is_dir())
        self.assertEqual(list(self.napcat.iterdir()), [])
        self.assertFalse(list(self.napcat.parent.glob(".napcat-install-*")))

    def test_zip_path_traversal_ads_reserved_names_and_duplicates_are_rejected(self):
        for name in ("../escape", "/absolute", "C:/escape", "napcat/../../escape", "dir\\escape",
                     "napcat/file:stream", "AUX.txt", "trailing. /file", "NODE.EXE"):
            with self.subTest(name=name):
                payload = make_archive(extra=((name, b"untrusted"),))
                # Windows ZipInfo normalizes separators while writing. Replace
                # both ZIP headers to represent an actual hostile backslash.
                if "\\" in name:
                    payload = payload.replace(name.replace("\\", "/").encode(), name.encode())
                self.cache_payload(payload)
                with self.pin(payload):
                    with self.assertRaises(ValueError):
                        runtime._validate_archive(self.cache)
                self.assertFalse((self.home / "escape").exists())

    def test_symlink_and_missing_critical_file_archives_are_rejected(self):
        link = zipfile.ZipInfo("unsafe-link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for payload in (make_archive(extra=((link, b"../../escape"),)), make_archive(omit=("wrapper.node",))):
            with self.pin(payload):
                self.cache_payload(payload)
                with self.assertRaises(ValueError):
                    runtime._validate_archive(self.cache)

    def test_directory_populated_during_extraction_is_never_overwritten(self):
        self.cache_payload()
        original = runtime.shutil.copyfileobj
        added = False
        def copy_then_populate(source, destination):
            nonlocal added
            original(source, destination)
            if not added:
                self.napcat.mkdir(parents=True)
                (self.napcat / "keep.json").write_bytes(b"new-private-fixture")
                added = True
        with patch.object(runtime.shutil, "copyfileobj", side_effect=copy_then_populate):
            with self.assertRaisesRegex(ValueError, "保护原目录"):
                runtime.install_napcat()
        self.assertEqual((self.napcat / "keep.json").read_bytes(), b"new-private-fixture")
        self.assertFalse((self.napcat / "node.exe").exists())

    def test_installed_check_uses_lightweight_runtime_check_not_cache_hash(self):
        astrbot = self.home / "runtime/astrbot"
        (astrbot / "venv/Scripts").mkdir(parents=True)
        (astrbot / "venv/Scripts/python.exe").write_bytes(b"fixture")
        (astrbot / "run_astrbot.py").write_bytes(b"fixture")
        with patch.object(config_store, "ASTRBOT", astrbot), \
             patch.object(runtime, "inspect_napcat", side_effect=AssertionError("must not hash cache")):
            self.assertFalse(config_store.is_installed())
            self.populate_runtime()
            self.assertTrue(config_store.is_installed())


class InstallerLockTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.Lock()
        for p in (patch.object(runtime, "_operation_lock", self.lock),
                  patch.object(installer, "STATE", installer.InstallState())):
            p.start()
            self.addCleanup(p.stop)

    def test_double_start_is_rejected_before_worker_is_scheduled(self):
        with patch.object(installer.threading, "Thread") as thread:
            first = installer.start({})
            second = installer.start({})
        self.assertTrue(first["ok"])
        self.assertTrue(first["state"]["running"])
        self.assertFalse(second["ok"])
        self.assertEqual(thread.call_count, 1)
        self.assertTrue(self.lock.locked())
        self.lock.release()

    def test_thread_start_failure_releases_operation_and_running_state(self):
        with patch.object(installer.threading, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread unavailable")
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                installer.start({})
        self.assertFalse(self.lock.locked())
        self.assertFalse(installer.snapshot()["running"])

    def test_napcat_failure_happens_before_python_and_releases_shared_operation(self):
        self.assertTrue(runtime.claim_operation())
        installer.STATE.running = True
        with patch.object(installer, "ensure_dirs"), \
             patch.object(installer.shutil, "disk_usage", return_value=SimpleNamespace(free=10 * 1024**3)), \
             patch.object(installer.STATE, "update"), \
             patch.object(runtime, "prepare_napcat_archive", side_effect=ValueError("import original archive")), \
             patch.object(installer, "_ensure_uv") as uv, \
             patch.object(installer, "_install_python") as python:
            installer._bootstrap({})
        uv.assert_not_called()
        python.assert_not_called()
        self.assertFalse(self.lock.locked())
        self.assertFalse(installer.STATE.running)
        self.assertFalse(installer.STATE.done)
        self.assertIn("import original archive", installer.STATE.error)


if __name__ == "__main__":
    unittest.main()
