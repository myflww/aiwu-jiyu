"""Check bundled add-on integrity and upgrades without using a live profile."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import plugins


class BundledPluginsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aiwu-plugins-")
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "assets" / "plugins"
        self.runtime = self.root / "runtime" / "astrbot"
        self.installed = self.runtime / "data" / "plugins"
        self.plugin_id = "astrbot_plugin_example"
        self.source = self.bundle / self.plugin_id
        self.source.mkdir(parents=True)
        self.patchers = [patch.object(plugins, "BUNDLE_ROOT", self.bundle),
                         patch.object(plugins, "PLUGIN_ROOT", self.installed),
                         patch.object(plugins, "ASTRBOT", self.runtime)]
        for patcher in self.patchers:
            patcher.start()
        self.prepare()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def prepare(self, content: bytes = b"print('bundled')\n", relative: str = "main.py"):
        source = self.source / "main.py"
        source.write_bytes(content)
        (self.source / "metadata.yaml").write_text("name: Example\nversion: v1.0.0\n", encoding="utf-8")
        files = []
        for item in (source, self.source / "metadata.yaml"):
            data = item.read_bytes()
            files.append({"path": relative if item == source else item.name,
                          "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        manifest = {"schema_version": 1, "plugins": [{"id": self.plugin_id, "files": files}]}
        (self.bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_fresh_install_and_idempotence(self):
        first = plugins.install_bundled()
        target = self.installed / self.plugin_id
        self.assertEqual(first["installed"], [self.plugin_id])
        self.assertEqual((target / "main.py").read_bytes(), (self.source / "main.py").read_bytes())
        timestamp = (target / "main.py").stat().st_mtime_ns
        second = plugins.install_bundled()
        self.assertEqual(second["unchanged"], [self.plugin_id])
        self.assertEqual((target / "main.py").stat().st_mtime_ns, timestamp)
        self.assertFalse((self.runtime / "data" / "plugin_backups").exists())

    def test_upgrade_backs_up_source_and_preserves_user_files(self):
        target = self.installed / self.plugin_id
        target.mkdir(parents=True)
        (target / "main.py").write_text("# custom local source\n", encoding="utf-8")
        (target / "config.json").write_text('{"enabled": false}', encoding="utf-8")
        (target / "memory").mkdir()
        (target / "memory" / "story.txt").write_text("local-only story", encoding="utf-8")
        result = plugins.install_bundled()
        self.assertEqual(result["updated"], [self.plugin_id])
        self.assertEqual((Path(result["backup"]) / self.plugin_id / "main.py").read_text(), "# custom local source\n")
        self.assertEqual((target / "config.json").read_text(), '{"enabled": false}')
        self.assertEqual((target / "memory" / "story.txt").read_text(), "local-only story")
        self.assertFalse((Path(result["backup"]) / self.plugin_id / "config.json").exists())

    def test_disabled_plugin_stays_disabled(self):
        target = self.installed / (self.plugin_id + ".disabled")
        target.mkdir(parents=True)
        plugins.install_bundled()
        self.assertTrue((target / "main.py").is_file())
        self.assertFalse((self.installed / self.plugin_id).exists())
        row = plugins.list_plugins()[0]
        self.assertTrue(row["installed"] and row["bundled"])
        self.assertFalse(row["enabled"])

    def test_corrupt_bundle_is_rejected_before_install(self):
        (self.source / "main.py").write_text("corrupted", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            plugins.install_bundled()
        self.assertFalse(self.installed.exists())

    def test_path_escape_is_rejected(self):
        self.prepare(relative="../outside.py")
        with self.assertRaises(RuntimeError):
            plugins.install_bundled()
        self.assertFalse(self.installed.exists())

    def test_inventory_before_install_never_reads_profile(self):
        with patch.object(Path, "iterdir", side_effect=AssertionError("runtime inspected")):
            row = plugins.list_bundled_plugins()[0]
        self.assertEqual(row["id"], self.plugin_id)
        self.assertTrue(row["bundled"] and row["pending"])
        self.assertFalse(row["installed"] or row["enabled"])
        self.assertEqual(row["path"], "")

    def prepare_preferences(self):
        self.runtime.joinpath("data").mkdir(parents=True, exist_ok=True)
        database = self.runtime / "data" / "data_v4.db"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE preferences (id INTEGER PRIMARY KEY, scope TEXT, "
                               "scope_id TEXT, key TEXT, value TEXT, created_at TEXT, updated_at TEXT)")
            connection.execute("INSERT INTO preferences (scope,scope_id,key,value) VALUES (?,?,?,?)",
                               ("global", "global", "inactivated_plugins",
                                json.dumps({"val": ["data.plugins.some_other_plugin.main"]})))
            connection.execute("INSERT INTO preferences (scope,scope_id,key,value) VALUES (?,?,?,?)",
                               ("umo", "private-test", "unrelated", '{"val": "keep"}'))
        return database

    def test_offline_switch_persists_and_preserves_configuration_and_other_plugins(self):
        self.install_for_switch()
        database = self.prepare_preferences()
        config = self.installed / self.plugin_id / "config.json"
        config.write_text('{"private_setting": "keep"}', encoding="utf-8")
        with patch.object(plugins.processes, "port_up", return_value=False):
            disabled = plugins.set_enabled(self.plugin_id, False)
            self.assertFalse(disabled["enabled"])
            self.assertTrue(disabled["applied"])
            self.assertFalse(plugins.list_plugins()[0]["enabled"])
            enabled = plugins.set_enabled(self.plugin_id, True)
            self.assertTrue(enabled["enabled"])
        self.assertEqual(config.read_text("utf-8"), '{"private_setting": "keep"}')
        self.assertEqual(plugins._inactive_modules(), {"data.plugins.some_other_plugin.main"})
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM preferences WHERE key='unrelated'").fetchone()[0],
                             '{"val": "keep"}')

    def install_for_switch(self):
        plugins.install_bundled()

    def test_live_switch_uses_registered_name_without_restarting_services(self):
        self.install_for_switch()
        self.prepare_preferences()

        active = True

        def request(path, payload=None, token=None, method="POST"):
            nonlocal active
            self.assertEqual(token, "local-token")
            if path == "/api/plugin/get":
                return {"status": "ok", "data": [{"root_dir_name": self.plugin_id,
                        "name": "RegisteredName", "activated": active}]}
            self.assertEqual(path, "/api/plugin/off")
            self.assertEqual(payload, {"name": "RegisteredName"})
            plugins._set_offline(self.plugin_id, False)  # mimic native host persistence, in temp DB
            active = False
            return {"status": "ok"}

        with patch.object(plugins.processes, "port_up", return_value=True), \
             patch.object(plugins.astrbot_api, "dashboard_token", return_value="local-token"), \
             patch.object(plugins.astrbot_api, "_request", side_effect=request), \
             patch.object(plugins.processes, "stop_astrbot", side_effect=AssertionError("service restarted")):
            result = plugins.set_enabled(self.plugin_id, False)
        self.assertTrue(result["applied"])
        self.assertFalse(result["plugins"][0]["enabled"])

    def test_live_auth_failure_queues_switch_without_editing_cached_native_preferences(self):
        self.install_for_switch()
        self.prepare_preferences()
        before = plugins._inactive_modules()
        with patch.object(plugins.processes, "port_up", return_value=True), \
             patch.object(plugins.astrbot_api, "dashboard_token", side_effect=RuntimeError("auth failure")):
            result = plugins.set_enabled(self.plugin_id, False)
        self.assertTrue(result["ok"] and result["pending"] and result["restart_required"])
        self.assertFalse(result["applied"] or result["enabled"])
        self.assertTrue(result["runtime_enabled"])
        self.assertEqual(plugins._inactive_modules(), before)
        row = plugins.list_plugins()[0]
        self.assertTrue(row["pending_change"] and row["runtime_enabled"])
        self.assertFalse(row["enabled"])
        with patch.object(plugins.processes, "port_up", return_value=False):
            plugins.apply_pending()
        self.assertEqual(plugins._pending_switches(), {})
        self.assertFalse(plugins.list_plugins()[0]["enabled"])

    def test_extra_plugin_with_named_module_and_input_validation(self):
        target = self.installed / "custom_plugin"
        target.mkdir(parents=True)
        (target / "custom_plugin.py").write_text("# third party\n", encoding="utf-8")
        self.prepare_preferences()
        with patch.object(plugins.processes, "port_up", return_value=False):
            plugins.set_enabled("custom_plugin", False)
            self.assertIn("data.plugins.custom_plugin.custom_plugin", plugins._inactive_modules())
            row = next(row for row in plugins.list_plugins() if row["id"] == "custom_plugin")
            self.assertFalse(row["enabled"] or row["bundled"])
            with self.assertRaises(ValueError):
                plugins.set_enabled("../outside", False)
            with self.assertRaises(ValueError):
                plugins.set_enabled("custom_plugin", "false")

    def test_cancel_pending_switch_without_restart(self):
        self.install_for_switch()
        self.prepare_preferences()
        with patch.object(plugins.processes, "port_up", return_value=True), \
             patch.object(plugins.astrbot_api, "dashboard_token", side_effect=RuntimeError("old host")):
            plugins.set_enabled(self.plugin_id, False)
            result = plugins.set_enabled(self.plugin_id, True)
        self.assertTrue(result["ok"] and result["enabled"])
        self.assertFalse(result["applied"])
        self.assertFalse(result["pending"] or result["restart_required"])
        self.assertEqual(plugins._pending_switches(), {})

    def test_failed_hot_enable_cannot_be_reported_applied_from_changed_database(self):
        self.install_for_switch()
        self.prepare_preferences()
        plugins._set_offline(self.plugin_id, False)
        attempts = []

        def request(path, payload=None, token=None, method="POST"):
            if path == "/api/plugin/get":
                return {"status": "ok", "data": [{"root_dir_name": self.plugin_id,
                        "name": "RegisteredName", "activated": False}]}
            self.assertEqual(path, "/api/plugin/on")
            attempts.append(path)
            # AstrBot persists the enabled preference before attempting reload.
            plugins._set_offline(self.plugin_id, True)
            raise RuntimeError("native reload failed")

        with patch.object(plugins.processes, "port_up", return_value=True), \
             patch.object(plugins.astrbot_api, "dashboard_token", return_value="local-token"), \
             patch.object(plugins.astrbot_api, "_request", side_effect=request):
            first = plugins.set_enabled(self.plugin_id, True)
            row = plugins.list_plugins()[0]
            self.assertTrue(first["pending"] and row["pending_change"])
            self.assertFalse(first["applied"] or row["runtime_enabled"])
            self.assertNotIn(f"data.plugins.{self.plugin_id}.main", plugins._inactive_modules())
            second = plugins.set_enabled(self.plugin_id, True)
        self.assertEqual(len(attempts), 2, "a changed DB must not skip the real runtime retry")
        self.assertTrue(second["pending"] and second["restart_required"])
        self.assertFalse(second["applied"] or second["runtime_enabled"])

    def test_startup_pending_worker_cannot_overwrite_later_user_cancellation(self):
        self.install_for_switch()
        self.prepare_preferences()
        plugins._save_pending(self.plugin_id, False, runtime_enabled=True,
                              attempted=False, baseline_enabled=True)
        snapshot_ready = threading.Event()
        continue_worker = threading.Event()
        user_done = threading.Event()
        original = plugins._pending_switches
        errors = []

        def snapshot():
            result = original()
            if threading.current_thread().name == "pending-worker":
                snapshot_ready.set()
                if not continue_worker.wait(3):
                    raise AssertionError("worker test timed out")
            return result

        def run_worker():
            try:
                plugins.apply_pending_live()
            except Exception as exc:
                errors.append(exc)

        def run_user():
            try:
                plugins.set_enabled(self.plugin_id, True)
            except Exception as exc:
                errors.append(exc)
            finally:
                user_done.set()

        with patch.object(plugins.processes, "port_up", return_value=True), \
             patch.object(plugins.astrbot_api, "dashboard_token", side_effect=RuntimeError("old host")), \
             patch.object(plugins, "_pending_switches", side_effect=snapshot):
            worker = threading.Thread(target=run_worker, name="pending-worker")
            user = threading.Thread(target=run_user, name="user-change")
            worker.start()
            self.assertTrue(snapshot_ready.wait(3))
            user.start()
            completed_before_worker = user_done.wait(0.1)
            continue_worker.set()
            worker.join(3)
            user.join(3)
        self.assertFalse(worker.is_alive() or user.is_alive())
        self.assertFalse(completed_before_worker, "snapshot/application must share one lock")
        self.assertEqual(errors, [])
        self.assertEqual(plugins._pending_switches(), {})
        self.assertTrue(plugins.list_plugins()[0]["enabled"])

    def test_duplicate_legacy_disabled_directory_does_not_duplicate_plugin_row(self):
        self.install_for_switch()
        duplicate = self.installed / (self.plugin_id + ".disabled")
        duplicate.mkdir()
        (duplicate / "main.py").write_text("# stale legacy directory", encoding="utf-8")
        self.assertEqual(len(plugins.list_plugins()), 1)

    def test_shipped_manifest_and_safe_permission_defaults(self):
        actual = ROOT / "assets" / "plugins"
        with patch.object(plugins, "BUNDLE_ROOT", actual):
            result = plugins.install_bundled()
        self.assertEqual(len(result["installed"]), 3)
        pc = json.loads((actual / "astrbot_plugin_pc_operator" / "_conf_schema.json").read_text("utf-8"))
        memory = json.loads((actual / "astrbot_plugin_love_house" / "_conf_schema.json").read_text("utf-8"))
        self.assertFalse(pc["enabled"]["default"])
        self.assertEqual(pc["owner_qq"]["default"], "")
        self.assertFalse(memory["enabled"]["default"])
        self.assertEqual(memory["bindings"]["default"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
