"""Check telemetry uses real OS readings and avoids counting child processes twice."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import metrics


class MetricsTests(unittest.TestCase):
    def test_descendants_and_components_do_not_count_managed_children_twice(self):
        parents = {1: 0, 2: 1, 3: 2, 4: 1, 5: 4, 6: 0}
        self.assertEqual(metrics._descendants(2, parents), {2, 3})
        roots = {"app": 1, "astrbot": 2, "napcat": 4, "lovehouse": 6}
        with patch.object(metrics, "_process_mb", return_value=10) as read:
            result = metrics._component_memory(roots, parents)
        self.assertEqual(result, {"app_mb": 10, "astrbot_mb": 20, "napcat_mb": 20, "lovehouse_mb": 10})
        self.assertEqual(read.call_count, 6)

    def test_failed_system_read_is_explicitly_unavailable(self):
        with patch.object(metrics, "_system_memory", side_effect=OSError("unavailable")), \
             patch.object(metrics, "_port_pids", side_effect=AssertionError("should not inspect processes")):
            result = metrics.snapshot()
        self.assertFalse(result["available"])
        self.assertFalse(result["processes_available"])

    def test_verified_managed_launchers_belong_to_their_service_component(self):
        parents = {1: 0, 10: 1, 11: 10, 12: 11, 20: 1, 21: 20, 22: 21, 99: 0}
        with patch.object(metrics.processes, "_read_pid", side_effect=lambda name: 10 if name == "astrbot" else 20):
            roots = {"app": 1, "astrbot": metrics._managed_root("astrbot", 12, parents),
                     "napcat": metrics._managed_root("napcat", 22, parents), "lovehouse": None}
        with patch.object(metrics, "_process_mb", return_value=10):
            result = metrics._component_memory(roots, parents)
        self.assertEqual(result, {"app_mb": 10, "astrbot_mb": 30, "napcat_mb": 30, "lovehouse_mb": 0})
        with patch.object(metrics.processes, "_read_pid", return_value=99):
            self.assertEqual(metrics._managed_root("astrbot", 12, parents), 12)
        self.assertIsNone(metrics._managed_root("napcat", None, parents))

    def test_process_failure_preserves_system_sample(self):
        sample = {"available": True, "total_mb": 16000, "used_mb": 8000, "available_mb": 8000, "percent": 50}
        with patch.object(metrics, "_system_memory", return_value=sample), \
             patch.object(metrics, "_port_pids", side_effect=OSError("netstat unavailable")):
            result = metrics.snapshot()
        self.assertEqual(result["percent"], 50)
        self.assertTrue(result["available"])
        self.assertFalse(result["processes_available"])

    @unittest.skipUnless(os.name == "nt", "requires Windows memory APIs")
    def test_windows_memory_and_current_64_bit_process_are_readable(self):
        system = metrics._system_memory()
        self.assertGreater(system["total_mb"], 0)
        self.assertGreaterEqual(system["total_mb"], system["used_mb"])
        self.assertGreaterEqual(system["percent"], 0)
        self.assertLessEqual(system["percent"], 100)
        self.assertGreater(metrics._process_mb(os.getpid()), 0)
        self.assertIn(os.getpid(), metrics._process_parents())


if __name__ == "__main__":
    unittest.main(verbosity=2)
