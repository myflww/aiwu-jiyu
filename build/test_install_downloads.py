"""Exercise first-run download failures against a local HTTP server only."""
from __future__ import annotations

import functools
import hashlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import downloader

DATA = bytes(range(251)) * 17


class DownloadHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        if self.path == "/short":
            self.send_error(405)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(DATA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        if self.path == "/short":
            self.send_response(200)
            self.send_header("Content-Length", str(len(DATA)))
            self.end_headers()
            self.wfile.write(DATA[:10])
            self.close_connection = True
            return
        data = b"x" * len(DATA) if self.path == "/bad-hash" else DATA
        value = self.headers.get("Range")
        if value and self.path != "/ignore-range":
            start, end = map(int, value.removeprefix("bytes=").split("-"))
            self.send_response(206)
            actual_start = start + 1 if self.path == "/wrong-range" else start
            self.send_header("Content-Range", f"bytes {actual_start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            self.wfile.write(data[start:end + 1])
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


class InstallDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DownloadHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dest = Path(self.temp.name) / "archive.zip"
        self.digest = hashlib.sha256(DATA).hexdigest()
        self.logs = []
        patcher = patch.object(downloader, "CHUNK", 1024)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_correct_parallel_download_and_no_partial_files(self):
        downloader.download(self.url + "/good", self.dest, sha256=self.digest, log=self.logs.append)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_equal_length_wrong_range_is_rejected_then_next_mirror_succeeds(self):
        fetch = functools.partial(downloader._fetch, retries=1)
        with patch.object(downloader, "_fetch", fetch):
            downloader.download(self.url + "/wrong-range", self.dest,
                                mirrors=[self.url + "/good"], sha256=self.digest, log=self.logs.append)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertTrue(any("范围不匹配" in line for line in self.logs))

    def test_equal_length_wrong_hash_retries_mirror(self):
        downloader.download(self.url + "/bad-hash", self.dest,
                            mirrors=[self.url + "/good"], sha256=self.digest, log=self.logs.append)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertTrue(any("SHA256" in line for line in self.logs))

    def test_ignored_range_falls_back_to_single(self):
        downloader.download(self.url + "/ignore-range", self.dest, sha256=self.digest, log=self.logs.append)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertTrue(any("单线程" in line for line in self.logs))

    def test_get_short_read_is_rejected_even_when_head_fails(self):
        self.dest.write_bytes(b"previous verified file")
        with self.assertRaisesRegex(RuntimeError, "长度不匹配"):
            downloader.download(self.url + "/short", self.dest)
        self.assertEqual(self.dest.read_bytes(), b"previous verified file")
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_timed_out_workers_cannot_overwrite_successful_retry(self):
        def fetch(url, start, end, _size, _deadline, _cancelled):
            if url.endswith("/slow"):
                time.sleep(0.15)
                return b"x" * (end - start + 1)
            return DATA[start:end + 1]

        with patch.object(downloader, "TOTAL_TIMEOUT", 0.04), \
                patch.object(downloader, "_probe", return_value=(len(DATA), True)), \
                patch.object(downloader, "_fetch", side_effect=fetch):
            downloader.download(self.url + "/slow", self.dest,
                                mirrors=[self.url + "/good"], sha256=self.digest, log=self.logs.append)
        time.sleep(0.2)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertTrue(any("总时限" in line for line in self.logs))
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_single_stream_checks_total_budget_even_while_bytes_keep_arriving(self):
        clock = [0.0]
        response = Mock(status=200, headers={})
        def read(_size):
            clock[0] += 0.6
            return b"x"
        response.read1.side_effect = read
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(downloader.urllib.request, "urlopen", return_value=response), \
                patch.object(downloader.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(TimeoutError, "总时限"):
                downloader._single(self.url + "/drip", self.dest, self.logs.append, 1.0)
        self.assertEqual(response.read1.call_count, 2)


if __name__ == "__main__":
    unittest.main()
