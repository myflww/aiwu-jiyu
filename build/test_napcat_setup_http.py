"""QQ recovery routes stay local, bounded and isolated from preview mode."""
from __future__ import annotations

import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from backend import server


class NapcatSetupRoutes(unittest.TestCase):
    def setUp(self):
        self.httpd = server.LocalServer(("127.0.0.1", 0), server.Handler)
        self.httpd.preview = False
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, path, body=None if payload is None else json.dumps(payload), headers=headers or {})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_status_reports_actual_inspection(self):
        actual = {"installed": True, "cache_valid": False, "status": "installed", "version": "4.18.42"}
        with patch.object(server.napcat_runtime, "inspect_napcat", return_value=actual) as inspect:
            code, value = self.request("GET", "/api/setup/napcat")
        self.assertEqual(code, 200)
        self.assertEqual(value, {"ok": True, **actual})
        inspect.assert_called_once_with()

    def test_import_requires_local_origin_and_host_before_any_file_access(self):
        with patch.object(server.napcat_runtime, "import_napcat_archive") as importer:
            for headers in ({}, {"Origin": "https://untrusted.example"},
                            {"Origin": self.origin, "Host": "untrusted.example"},
                            {"Origin": self.origin, "Sec-Fetch-Site": "cross-site"}):
                code, value = self.request("POST", "/api/setup/napcat/import", {"path": "C:/archive.zip"}, headers)
                self.assertEqual(code, 403)
                self.assertFalse(value["ok"])
            importer.assert_not_called()

    def test_import_dispatches_selected_path_only(self):
        result = {"ok": True, "message": "已校验", "napcat": {"cache_valid": True}}
        with patch.object(server.napcat_runtime, "import_napcat_archive", return_value=result) as importer:
            code, value = self.request("POST", "/api/setup/napcat/import", {"path": "E:/原包.zip"}, {"Origin": self.origin})
        self.assertEqual((code, value), (200, result))
        importer.assert_called_once_with("E:/原包.zip")

    def test_invalid_paths_and_validation_failures_are_not_success(self):
        with patch.object(server.napcat_runtime, "import_napcat_archive", side_effect=ValueError("SHA256 校验失败")) as importer:
            for value in (None, "", "  ", [], {}, 1):
                code, payload = self.request("POST", "/api/setup/napcat/import", {"path": value}, {"Origin": self.origin})
                self.assertEqual(code, 400)
                self.assertFalse(payload["ok"])
            importer.assert_not_called()
            code, value = self.request("POST", "/api/setup/napcat/import", {"path": "E:/wrong.zip"}, {"Origin": self.origin})
            self.assertEqual(code, 400)
            self.assertFalse(value["ok"])
            self.assertIn("SHA256", value["message"])

    def test_oversized_import_does_not_reach_filesystem(self):
        with patch.object(server.napcat_runtime, "import_napcat_archive") as importer:
            code, value = self.request("POST", "/api/setup/napcat/import", {},
                                       {"Origin": self.origin, "Content-Length": str(1024 * 1024 + 1)})
            self.assertEqual(code, 413)
            self.assertFalse(value["ok"])
            importer.assert_not_called()

    def test_preview_reads_constants_only_and_rejects_import(self):
        self.httpd.preview = True
        with patch.object(server.napcat_runtime, "inspect_napcat", side_effect=AssertionError("private filesystem")), \
             patch.object(server.napcat_runtime, "import_napcat_archive", side_effect=AssertionError("private filesystem")):
            code, value = self.request("GET", "/api/setup/napcat")
            self.assertEqual(code, 200)
            self.assertTrue(value["preview"])
            self.assertFalse(value["cache_valid"])
            code, value = self.request("POST", "/api/setup/napcat/import", {"path": "E:/archive.zip"}, {"Origin": self.origin})
            self.assertEqual(code, 403)
            self.assertFalse(value["ok"])


if __name__ == "__main__":
    unittest.main()
