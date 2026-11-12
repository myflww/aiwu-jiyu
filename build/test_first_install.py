"""First-run installer regressions without running MSI, uv, or live services."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import installer


class FirstInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.python = self.root / "runtime" / "python"
        self.astrbot = self.root / "runtime" / "astrbot"
        for name, value in (("APP_HOME", self.root), ("PYTHON_DIR", self.python),
                            ("ASTRBOT", self.astrbot), ("STATE", Mock())):
            p = patch.object(installer, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_existing_valid_runtime_skips_download_and_msi(self):
        with patch.object(installer, "_python_usable", return_value=True), \
                patch.object(installer, "_download") as download, patch.object(installer, "_run") as run:
            installer._install_python()
        download.assert_not_called()
        run.assert_not_called()

    def test_compatible_local_python_is_copied_before_download(self):
        source = self.root / "system-python"
        source.mkdir()
        (source / "python.exe").write_bytes(b"fake interpreter")
        (source / "python313.dll").write_bytes(b"fake standard runtime")
        with patch.object(installer, "_python_candidates", return_value=[source / "python.exe"]), \
                patch.object(installer, "_python_usable", side_effect=lambda path: path.is_file()), \
                patch.object(installer, "_download") as download, patch.object(installer, "_run") as run:
            installer._install_python()
        self.assertEqual((self.python / "python313.dll").read_bytes(), b"fake standard runtime")
        download.assert_not_called()
        run.assert_not_called()

    def test_1603_still_attempts_validated_fallback(self):
        with patch.object(installer, "_python_usable", return_value=False), \
                patch.object(installer, "_reuse_existing_python", side_effect=[False, True]) as reuse, \
                patch.object(installer, "_download"), patch.object(installer, "_run", return_value=1603):
            installer._install_python()
        self.assertEqual(reuse.call_count, 2)

    def test_failed_staged_copy_preserves_existing_runtime(self):
        self.python.mkdir(parents=True)
        (self.python / "existing.txt").write_bytes(b"preserve")
        source = self.root / "system-python"
        source.mkdir()
        (source / "python.exe").touch()
        with patch.object(installer, "_python_candidates", return_value=[source / "python.exe"]), \
                patch.object(installer, "_python_usable", side_effect=[True, False]):
            self.assertFalse(installer._reuse_existing_python())
        self.assertEqual((self.python / "existing.txt").read_bytes(), b"preserve")
        self.assertEqual(list(self.python.parent.iterdir()), [self.python])

    def test_unreadable_first_candidate_does_not_block_second_candidate(self):
        first = self.root / "unreadable-python"
        second = self.root / "usable-python"
        for source in (first, second):
            source.mkdir()
            (source / "python.exe").write_bytes(b"fake interpreter")
        original_copy = installer.shutil.copytree
        def copy(source, destination):
            if source == first:
                raise PermissionError("unreadable third-party package")
            return original_copy(source, destination)
        with patch.object(installer, "_python_candidates", return_value=[first / "python.exe", second / "python.exe"]), \
                patch.object(installer, "_python_usable", side_effect=lambda path: path.is_file()), \
                patch.object(installer.shutil, "copytree", side_effect=copy):
            self.assertTrue(installer._reuse_existing_python())
        self.assertEqual((self.python / "python.exe").read_bytes(), b"fake interpreter")

    def test_1603_without_compatible_candidate_stays_a_failure(self):
        with patch.object(installer, "_python_usable", return_value=False), \
                patch.object(installer, "_reuse_existing_python", return_value=False), \
                patch.object(installer, "_download"), patch.object(installer, "_run", return_value=1603):
            with self.assertRaisesRegex(RuntimeError, "1603.*3.13 x64"):
                installer._install_python()

    def test_python_validation_rejects_wrong_version_bitness_and_broken_runtime(self):
        executable = self.root / "python.exe"
        executable.touch()
        for version, code, valid in (("3.13:64", 0, True), ("3.12:64", 0, False),
                                     ("3.13:32", 0, False), ("3.13:64", 1, False)):
            with self.subTest(version=version, code=code), patch.object(installer.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, stdout=version)):
                self.assertEqual(installer._python_usable(executable), valid)

    def _prepare_astrbot(self):
        self.python.mkdir(parents=True)
        (self.python / "python.exe").touch()
        scripts = self.astrbot / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").touch()

    def test_pypi_failure_switches_source_with_same_venv_and_pinned_package(self):
        self._prepare_astrbot()
        with patch.dict(os.environ, {"DAFEIYU_PYPI_INDEX": ""}), \
                patch.object(installer, "_run", side_effect=[1, 0]) as run:
            installer._install_astrbot(Path("uv.exe"), {})
        first, second = [call.args[0] for call in run.call_args_list]
        self.assertEqual(first[:-1], second[:-1])
        self.assertIn("AstrBot==4.29.4", second)
        self.assertEqual(first[-1], installer.PYPI_INDEX)
        self.assertEqual(second[-1], installer.PYPI_FALLBACKS[0])
        self.assertTrue((self.astrbot / "run_astrbot.py").exists())

    def test_explicit_pypi_override_is_the_only_source(self):
        self._prepare_astrbot()
        with patch.dict(os.environ, {"DAFEIYU_PYPI_INDEX": "https://example.invalid/simple"}), \
                patch.object(installer, "_run", return_value=1) as run:
            with self.assertRaisesRegex(RuntimeError, "所有软件源"):
                installer._install_astrbot(Path("uv.exe"), {})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-1], "https://example.invalid/simple")
        self.assertFalse((self.astrbot / "run_astrbot.py").exists())

    def test_pypi_override_rejects_credentials_before_command_logging(self):
        with patch.dict(os.environ, {"DAFEIYU_PYPI_INDEX": "https://user:secret@example.invalid/simple"}):
            with self.assertRaisesRegex(RuntimeError, "不含凭据"):
                installer._pypi_indexes()

    def test_bad_napcat_cache_is_redownloaded_with_expected_hash(self):
        cache = self.root / "downloads" / "NapCat.Shell.Windows.Node.zip"
        cache.parent.mkdir()
        cache.write_bytes(b"corrupt")
        good = self.root / "good.zip"
        with zipfile.ZipFile(good, "w") as archive:
            archive.writestr("napcat.bat", "@echo off")
        digest = hashlib.sha256(good.read_bytes()).hexdigest()
        def download(_url, dest, _mirrors, sha256):
            self.assertFalse(dest.exists())
            self.assertEqual(sha256, digest)
            dest.write_bytes(good.read_bytes())
        with patch.object(installer, "NAPCAT", self.root / "napcat"), \
                patch.object(installer, "NAPCAT_SHA256", digest), \
                patch.object(installer, "_download", side_effect=download):
            installer._install_napcat()
        self.assertTrue((self.root / "napcat" / "napcat.bat").exists())


if __name__ == "__main__":
    unittest.main()
