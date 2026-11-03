"""Diagnostic-only cleanup, legacy writer deferral and native stdout continuity."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import log_retention as logs
from backend import processes


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aiwu-log-retention-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.folder = self.home / "logs"
        self.folder.mkdir()
        self.run_dir = self.home / "run"
        self.active = {"astrbot": None, "napcat": None}
        self.cleaner = logs.LogRetention(log_dir=self.folder, run_dir=self.run_dir,
                                         active_provider=lambda: self.active)

    def write_log(self, name, payload, age=20):
        path = self.folder / name
        path.write_bytes(payload)
        when = time.time() - age
        os.utime(path, (when, when))
        return path

    def test_trim_keeps_recent_complete_utf8_lines_and_same_file(self):
        payload = b"old record\n" * (logs.MAX_BYTES // 10) + ("最新诊断记录\n" * 100).encode()
        path = self.write_log("astrbot.log", payload)
        identity = path.stat().st_ino
        result = self.cleaner.cleanup()
        self.assertEqual(result["trimmed_files"], 1)
        self.assertEqual(path.stat().st_ino, identity)
        kept = path.read_bytes()
        self.assertLessEqual(len(kept), logs.KEEP_BYTES)
        self.assertTrue(kept.endswith(("最新诊断记录\n" * 100).encode()))
        self.assertFalse(kept.decode("utf-8").startswith("ld record"))
        self.assertEqual(result["reclaimed_bytes"], len(payload) - len(kept))

    def test_expiry_only_clears_allowlisted_inactive_logs(self):
        expired = self.write_log("astrbot.log", b"expired\n", age=8 * 86400)
        fresh = self.write_log("napcat.log", b"recent\n", age=1 * 86400)
        unrelated = self.write_log("chat-history.log", b"private history", age=30 * 86400)
        archive = self.write_log("astrbot.log.1", b"not managed", age=30 * 86400)
        nested = self.folder / "runtime"
        nested.mkdir()
        (nested / "astrbot.log").write_bytes(b"runtime own log")
        result = self.cleaner.cleanup()
        self.assertEqual(result["cleared_files"], 1)
        self.assertEqual(expired.read_bytes(), b"")
        self.assertEqual(fresh.read_bytes(), b"recent\n")
        self.assertEqual(unrelated.read_bytes(), b"private history")
        self.assertEqual(archive.read_bytes(), b"not managed")
        self.assertEqual((nested / "astrbot.log").read_bytes(), b"runtime own log")

    def test_busy_logs_are_deferred(self):
        path = self.write_log("napcat.log", b"x" * (logs.MAX_BYTES + 1), age=0)
        result = self.cleaner.cleanup()
        self.assertEqual(result["deferred"], [{"name": "napcat.log", "reason": "busy"}])
        self.assertGreater(path.stat().st_size, logs.MAX_BYTES)

    def test_legacy_active_writer_is_not_truncated_and_next_stop_allows_cleanup(self):
        path = self.write_log("napcat.log", b"x" * (logs.MAX_BYTES + 1))
        self.active["napcat"] = 12345
        result = self.cleaner.cleanup()
        self.assertEqual(result["deferred"], [{"name": "napcat.log", "reason": "legacy_writer"}])
        self.assertGreater(path.stat().st_size, logs.MAX_BYTES)
        self.active["napcat"] = None
        self.assertEqual(self.cleaner.cleanup()["trimmed_files"], 1)

    def test_recycled_writer_pid_is_not_accepted(self):
        self.write_log("astrbot.log", b"x" * (logs.MAX_BYTES + 1))
        self.run_dir.mkdir()
        (self.run_dir / "astrbot.log.append-writer.json").write_text(
            json.dumps({"schema": 1, "pid": 12345, "created": 111}), encoding="utf-8")
        self.active["astrbot"] = 12345
        with patch.object(logs, "_process_token", return_value=222):
            self.assertEqual(self.cleaner.cleanup()["deferred"][0]["reason"], "legacy_writer")

    def test_links_and_unrelated_paths_are_never_truncated(self):
        target = self.home / "important.txt"
        target.write_bytes(b"important" * logs.MAX_BYTES)
        hardlink = self.folder / "astrbot.log"
        os.link(target, hardlink)
        result = self.cleaner.cleanup(now=time.time() + 10)
        self.assertIn("astrbot.log", result["errors"])
        self.assertEqual(target.stat().st_size, len(b"important") * logs.MAX_BYTES)
        hardlink.unlink()
        try:
            hardlink.symlink_to(target)
        except OSError:
            return  # Hardlink protection is still verified without symlink privilege.
        result = self.cleaner.cleanup(now=time.time() + 10)
        self.assertIn("astrbot.log", result["errors"])
        self.assertEqual(target.stat().st_size, len(b"important") * logs.MAX_BYTES)

    def test_tail_is_bounded_and_ansi_is_removed(self):
        self.write_log("astrbot.log", b"a" * (logs.MAX_BYTES + 1) + b"\n\x1b[31mrecent\x1b[0m\n")
        with patch.object(processes, "LOGS", self.folder), \
             patch.object(Path, "read_text", side_effect=AssertionError("unbounded read")):
            self.assertTrue(processes.tail_log("astrbot", 200).endswith("\nrecent\n"))
            self.assertEqual(processes.tail_log("other"), "")
            self.assertEqual(processes.tail_log("astrbot", 0), "")
        self.assertLessEqual(len(logs.read_tail("astrbot.log", log_dir=self.folder)), 12000)

    def test_snapshot_is_read_only_and_background_thread_stops(self):
        cleaner = logs.LogRetention(log_dir=self.home / "absent", run_dir=self.run_dir,
                                     active_provider=lambda: self.active)
        state = cleaner.snapshot()
        state["files"].clear()
        self.assertEqual(cleaner.snapshot()["files"], list(logs.LOG_NAMES))
        self.assertFalse((self.home / "absent").exists())
        cleaner.start()
        cleaner.stop()
        self.assertFalse(cleaner._thread.is_alive())

    def test_app_text_stdout_still_appends_after_compaction(self):
        path = self.write_log("app.log", b"line\n" * (logs.MAX_BYTES // 5 + 1))
        logs.register_writer("app.log", os.getpid(), run_dir=self.run_dir)
        with logs.open_text_log(path) as stream:
            self.assertEqual(self.cleaner.cleanup()["trimmed_files"], 1)
            length = path.stat().st_size
            stream.write("新的启动记录\n")
        expected = ("新的启动记录" + os.linesep).encode()
        self.assertEqual(path.stat().st_size, length + len(expected))
        self.assertTrue(path.read_bytes().endswith(expected))

    @unittest.skipUnless(os.name == "nt", "Windows native WriteFile inherited stdout")
    def test_native_child_writefile_appends_after_live_compaction_without_sparse_hole(self):
        path = self.write_log("astrbot.log", b"line\n" * (logs.MAX_BYTES // 5 + 1))
        code = r'''
import ctypes, sys
from ctypes import wintypes
k=ctypes.WinDLL('kernel32', use_last_error=True)
k.GetStdHandle.argtypes=[wintypes.DWORD]
k.GetStdHandle.restype=wintypes.HANDLE
k.WriteFile.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
h=k.GetStdHandle(-11 & 0xffffffff)
def write(message):
    n=wintypes.DWORD()
    if not k.WriteFile(h,message,len(message),ctypes.byref(n),None): raise ctypes.WinError(ctypes.get_last_error())
write(b'READY\n')
sys.stdin.buffer.readline()
write(b'NATIVE_APPEND_AFTER_TRIM\n')
'''
        with logs.open_append_log(path) as output:
            child = subprocess.Popen([sys.executable, "-c", code], stdout=output,
                                     stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                                     creationflags=0x08000000)
        try:
            for _ in range(200):
                if logs.read_tail("astrbot.log", 100, log_dir=self.folder).endswith("READY\n"):
                    break
                if child.poll() is not None:
                    self.fail("Native writer exited before readiness")
                time.sleep(.01)
            else:
                self.fail("Native writer did not become ready")
            # A native listener can be a descendant of the managed cmd/python
            # launcher (NapCat). Verify the process-tree proof as well.
            logs.register_writer("astrbot.log", os.getpid(), run_dir=self.run_dir)
            self.active["astrbot"] = child.pid
            when = time.time() - logs.QUIET_SECONDS - 1
            os.utime(path, (when, when))
            result = self.cleaner.cleanup()
            self.assertEqual(result["trimmed_files"], 1, result)
            compacted = path.stat().st_size
            _, error = child.communicate(b"go\n", timeout=10)
            self.assertEqual(child.returncode, 0, error)
            self.assertEqual(path.stat().st_size, compacted + len(b"NATIVE_APPEND_AFTER_TRIM\n"))
            self.assertTrue(path.read_bytes().endswith(b"NATIVE_APPEND_AFTER_TRIM\n"))
            self.assertNotIn(b"\0", path.read_bytes())
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
