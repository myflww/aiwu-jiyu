"""New social/persona routes over a real isolated HTTP handler, all I/O mocked.

Never starts the app runtime, reads private accounts, applies a persona, uses a
model, searches the web, modifies QQ or publishes to Qzone.
"""
from __future__ import annotations

from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from backend import server

REAL_TOOL_SELECTION = server.life.authorized_tools  # Pure bundled policy only.
POSTS = ("/api/personas/preview", "/api/personas/apply", "/api/personas/restore",
         "/api/life/settings", "/api/life/search", "/api/life/profile",
         "/api/life/prepare", "/api/life/confirm", "/api/life/cancel")


class SocialHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance = server.LocalServer(("127.0.0.1", 0), server.Handler)
        cls.instance.preview = False
        cls.instance.daemon_threads = True
        cls.thread = threading.Thread(target=cls.instance.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:" + str(cls.instance.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.instance.shutdown()
        cls.instance.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.instance.preview = False
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.mocks = {}
        functions = {
            "personas_status": (server.personas, "status"), "catalog": (server.personas, "catalog"),
            "preview": (server.personas, "preview"), "apply": (server.personas, "apply"),
            "restore": (server.personas, "restore"), "grant": (server.personas, "grant_bundled_tools"),
            "life_status": (server.life, "status"), "configure": (server.life, "configure"),
            "load": (server.life, "load"), "tools": (server.life, "authorized_tools"),
            "profile": (server.life, "profile"), "search": (server.life, "search"),
            "prepare": (server.life, "prepare"), "confirm": (server.life, "confirm"),
            "cancel": (server.life, "cancel"), "inventory": (server.plugins, "list_plugins"),
            "native_toggle": (server.plugins, "set_enabled"),
        }
        for name, (module, function) in functions.items():
            self.mocks[name] = stack.enter_context(patch.object(module, function, return_value={"ok": True, "service": name}))
        self.mocks["catalog"].return_value = {"cards": [{"id": "natural", "name": "公开随附人格卡"}]}
        self.mocks["load"].return_value = {"search_enabled": False, "profile_enabled": False, "qzone_enabled": False, "search_provider": "bing_rss", "searxng_url": ""}
        self.mocks["tools"].side_effect = REAL_TOOL_SELECTION
        self.mocks["inventory"].return_value = [{"id": server.life.PLUGIN_ID, "enabled": False, "installed": True}]
        # Preview builds all its fixture values eagerly. These are public
        # package-only interfaces, but mock them as well to keep HTTP isolated.
        stack.enter_context(patch.object(server.stickers, "status", return_value={"stickers": [], "settings": {}}))
        stack.enter_context(patch.object(server.updater, "current_version", return_value="1.5.0"))
        stack.enter_context(patch.object(server.config_store, "load", side_effect=AssertionError("No private config access in HTTP fixtures")))
        stack.enter_context(patch.object(server.napcat_api, "_call", side_effect=AssertionError("No QQ transport in HTTP fixtures")))
        stack.enter_context(patch.object(server.astrbot_api, "_request", side_effect=AssertionError("No native persona transport in HTTP fixtures")))

    def request(self, method, path, body=None, *, headers=None, missing_host=False, raw=None):
        encoded = raw if raw is not None else None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(*self.instance.server_address, timeout=4)
        try:
            if missing_host:
                connection.putrequest(method, path, skip_host=True)
                for key, value in (headers or {}).items(): connection.putheader(key, value)
                if encoded is not None: connection.putheader("Content-Length", str(len(encoded)))
                connection.endheaders(encoded)
            else:
                connection.request(method, path, body=encoded, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, path, body):
        return self.request("POST", path, body, headers={"Origin": self.origin, "Content-Type": "application/json"})

    def assert_no_actions(self, except_names=()):
        public = {"catalog", *except_names}
        self.assertFalse(any(mock.called for name, mock in self.mocks.items() if name not in public),
                         [name for name, mock in self.mocks.items() if name not in public and mock.called])

    def test_new_get_surfaces_select_only_their_read_service_and_uncached_json(self):
        for path, name in (("/api/personas", "personas_status"), ("/api/life", "life_status")):
            status, headers, raw = self.request("GET", path)
            self.assertEqual(status, 200, raw)
            self.assertEqual(json.loads(raw)["service"], name)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("application/json", headers["Content-Type"])
            self.mocks[name].assert_called_once_with()
        self.assert_no_actions({"personas_status", "life_status"})

    def test_every_new_write_requires_exact_origin_host_and_not_cross_site(self):
        invalid = ({}, {"Origin": "https://other.example"}, {"Origin": self.origin, "Host": "other.example"},
                   {"Origin": self.origin, "Host": "localhost:" + str(self.instance.server_address[1])},
                   {"Origin": self.origin, "Sec-Fetch-Site": "cross-site"})
        for path in POSTS:
            for headers in invalid:
                with self.subTest(path=path, headers=headers):
                    status, _, raw = self.request("POST", path, {}, headers=headers)
                    self.assertEqual(status, 403, raw)
            status, _, raw = self.request("POST", path, {}, headers={"Origin": self.origin}, missing_host=True)
            self.assertEqual(status, 403, raw)
        self.assert_no_actions()

    def test_nested_or_unknown_leaf_routes_cannot_dispatch_or_return_html(self):
        paths = ("/api/personas/nested/apply", "/api/personas/apply/", "/api/personas/nested/preview",
                 "/api/life/nested/settings", "/api/life/nested/confirm", "/api/life/nested/profile", "/api/life/confirm/",
                 "/api/life/not-real", "/api/personas/not-real")
        for path in paths:
            for method in ("GET", "POST"):
                with self.subTest(path=path, method=method):
                    status, headers, raw = self.post(path, {}) if method == "POST" else self.request(method, path)
                    self.assertEqual(status, 404, raw)
                    self.assertIn("application/json", headers["Content-Type"])
        self.assert_no_actions()

    def test_preview_reads_only_bundled_catalog_and_blocks_all_post_actions(self):
        self.instance.preview = True
        for path in ("/api/personas", "/api/life"):
            status, headers, raw = self.request("GET", path)
            self.assertEqual(status, 200, raw)
            value = json.loads(raw)
            self.assertTrue(value["preview"])
            if path == "/api/personas": self.assertFalse(value["can_apply"])
            else: self.assertFalse(value["plugin_enabled"])
        for path in POSTS:
            status, _, raw = self.post(path, {"confirmed": True, "confirmation_token": "must-not-use"})
            self.assertEqual(status, 403, raw)
        self.assert_no_actions()

    def test_persona_preview_never_applies_or_grants_tools_and_apply_preserves_revision_body(self):
        body = {"card_id": "natural"}
        status, _, raw = self.post("/api/personas/preview", body)
        self.assertEqual(status, 200, raw)
        self.mocks["preview"].assert_called_once_with(body)
        self.assert_no_actions({"preview"})
        chosen = {"card_id": "gentle", "expected_revision": "seen-original-revision"}
        status, _, raw = self.post("/api/personas/apply", chosen)
        self.assertEqual(status, 200, raw)
        self.mocks["apply"].assert_called_once_with(chosen)
        restore = {"backup_id": "20261104-120000-deadbeef.json", "expected_revision": "seen-current-revision", "confirmed": True}
        status, _, raw = self.post("/api/personas/restore", restore)
        self.assertEqual(status, 200, raw)
        self.mocks["restore"].assert_called_once_with(restore)
        self.mocks["grant"].assert_not_called()

    def test_life_settings_select_only_enabled_capability_tools_toggle_native_and_reread(self):
        cases = [({"search_enabled": True}, ["dafeiyu_web_search"], False),
                 ({"profile_enabled": True}, ["dafeiyu_read_qq_profile", "dafeiyu_prepare_nickname", "dafeiyu_prepare_signature", "dafeiyu_prepare_avatar", "dafeiyu_prepare_status"], False),
                 ({"qzone_enabled": True}, ["dafeiyu_prepare_qzone"], False),
                 ({"search_enabled": False, "profile_enabled": False, "qzone_enabled": False}, [], True)]
        for chosen, expected, native_before in cases:
            with self.subTest(chosen=chosen):
                for mock in self.mocks.values(): mock.reset_mock()
                options = {"search_enabled": False, "profile_enabled": False, "qzone_enabled": False,
                           "search_provider": "bing_rss", "searxng_url": "", **chosen}
                self.mocks["load"].return_value = options
                self.mocks["inventory"].return_value = [{"id": server.life.PLUGIN_ID, "enabled": native_before, "installed": True},
                                                        {"id": "unrelated_plugin", "enabled": True}]
                self.mocks["configure"].return_value = {"ok": True, "stale_from_before_toggle": True}
                self.mocks["life_status"].return_value = {"ok": True, "plugin_enabled": bool(expected), "read_after_toggle": True}
                timeline = Mock()
                for name in ("configure", "load", "tools", "native_toggle", "grant", "life_status"):
                    timeline.attach_mock(self.mocks[name], name)
                status, _, raw = self.post("/api/life/settings", chosen)
                self.assertEqual(status, 200, raw)
                value = json.loads(raw)
                self.assertTrue(value["read_after_toggle"])
                self.assertNotIn("stale_from_before_toggle", value)
                self.mocks["configure"].assert_called_once_with(chosen)
                self.mocks["native_toggle"].assert_called_once_with(server.life.PLUGIN_ID, bool(expected))
                self.mocks["grant"].assert_called_once_with(expected)
                self.mocks["life_status"].assert_called_once_with()
                names = [call[0] for call in timeline.mock_calls]
                self.assertLess(names.index("configure"), names.index("native_toggle"))
                self.assertLess(names.index("native_toggle"), names.index("life_status"))
                self.assertLess(names.index("grant"), names.index("life_status"))
                self.assert_no_actions({"configure", "load", "tools", "inventory", "native_toggle", "grant", "life_status"})

    def test_settings_validation_failure_cannot_enable_native_plugin_or_grant_tools(self):
        self.mocks["configure"].side_effect = ValueError("Unsupported setting")
        status, _, raw = self.post("/api/life/settings", {"profile_enabled": "true"})
        self.assertEqual(status, 400, raw)
        self.assert_no_actions({"configure"})

    def test_profile_is_post_only_and_rejects_arbitrary_target_accounts(self):
        status, _, raw = self.request("GET", "/api/life/profile")
        self.assertEqual(status, 404, raw)
        status, _, raw = self.post("/api/life/profile", {"user_id": "333333333"})
        self.assertEqual(status, 400, raw)
        self.mocks["profile"].assert_not_called()
        status, _, raw = self.post("/api/life/profile", {})
        self.assertEqual(status, 200, raw)
        self.mocks["profile"].assert_called_once_with()
        self.assert_no_actions({"profile"})

    def test_life_payloads_forward_once_without_implicit_confirmation_or_retry(self):
        requested = {"query": "Python 官方文档"}
        status, _, raw = self.post("/api/life/search", requested)
        self.assertEqual(status, 200, raw)
        self.mocks["search"].assert_called_once_with(requested)
        proposal = {"action": "qzone", "payload": {"content": "需核对的完整动态", "visibility": "private"}}
        status, _, raw = self.post("/api/life/prepare", proposal)
        self.assertEqual(status, 200, raw)
        self.mocks["prepare"].assert_called_once_with(proposal)
        self.mocks["confirm"].assert_not_called()
        confirmation = {"draft_id": "d" * 32, "confirmation_token": "a-real-one-use-token", "confirmed": True}
        self.mocks["confirm"].return_value = {"ok": True, "status": "uncertain", "message": "先在QQ核对，不重发"}
        status, _, raw = self.post("/api/life/confirm", confirmation)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["status"], "uncertain")
        self.mocks["confirm"].assert_called_once_with(confirmation)
        self.mocks["prepare"].assert_called_once_with(proposal)
        cancelled = {"draft_id": "d" * 32}
        status, _, raw = self.post("/api/life/cancel", cancelled)
        self.assertEqual(status, 200, raw)
        self.mocks["cancel"].assert_called_once_with(cancelled)
        self.assert_no_actions({"search", "prepare", "confirm", "cancel"})

    def test_confirmation_rejection_or_exception_is_returned_without_resending(self):
        body = {"draft_id": "d" * 32, "confirmation_token": "exact-token", "confirmed": False}
        self.mocks["confirm"].side_effect = ValueError("需要明确确认这一次操作")
        status, _, raw = self.post("/api/life/confirm", body)
        self.assertEqual(status, 400, raw)
        self.mocks["confirm"].assert_called_once_with(body)
        self.assert_no_actions({"confirm"})
        self.mocks["confirm"].reset_mock()
        self.mocks["confirm"].side_effect = RuntimeError("synthetic transport failure")
        status, _, raw = self.post("/api/life/confirm", {**body, "confirmed": True})
        self.assertEqual(status, 500, raw)
        self.mocks["confirm"].assert_called_once_with({**body, "confirmed": True})
        self.assert_no_actions({"confirm"})


if __name__ == "__main__":
    unittest.main()
