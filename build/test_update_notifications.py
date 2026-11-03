"""Version notifications use verified release tags and preserve opt-out choices."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from backend import server, updater


class UpdateNotificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aiwu-notification-")
        self.addCleanup(temporary.cleanup)
        self.service = updater.Updater(Path(temporary.name), version="1.3.1")
        self.service.release = updater._release("v1.3.1")
        self.service.state.update(latest_version="1.3.1", checking=False)
        self.server = SimpleNamespace(plugin_update_requested=False)
        self.plugins = dict(state="checked", tag="v1.3.1", update_available=True, plugins=[])

    def snapshot(self):
        with patch.object(server.updater, "get_updater", return_value=self.service), \
             patch.object(server.plugin_updates, "snapshot", return_value=dict(self.plugins)), \
             patch.object(server.plugin_updates, "start_check", return_value=dict(self.plugins)) as check:
            result = server._updates_snapshot(self.server)
        return result, check

    def test_automatic_checks_use_verified_tag(self):
        result, check = self.snapshot()
        check.assert_called_once_with("v1.3.1", force=False)
        self.assertTrue(result["plugin_updates"]["can_repair"])

    def test_auto_opt_out_skips_plugin_network(self):
        self.service.settings["auto_check"] = False
        _, check = self.snapshot()
        check.assert_not_called()

    def test_manual_check_waits_for_new_release_then_checks_plugins(self):
        self.service.settings["auto_check"] = False
        self.service.state["checking"] = True
        self.server.plugin_update_requested = True
        _, check = self.snapshot()
        check.assert_not_called()
        self.assertTrue(self.server.plugin_update_requested)
        self.service.state["checking"] = False
        _, check = self.snapshot()
        check.assert_called_once_with("v1.3.1", force=True)
        self.assertFalse(self.server.plugin_update_requested)

    def test_unknown_release_or_mismatched_snapshot_never_supplies_tag(self):
        self.service.release = None
        _, check = self.snapshot()
        check.assert_not_called()
        self.service.release = updater._release("v1.3.0")
        _, check = self.snapshot()
        check.assert_not_called()

    def test_manual_request_survives_busy_worker_for_previous_release(self):
        self.service.settings["auto_check"] = False
        self.server.plugin_update_requested = True
        self.plugins.update(tag="v1.3.0", state="checking")
        _, check = self.snapshot()
        check.assert_called_once_with("v1.3.1", force=True)
        self.assertTrue(self.server.plugin_update_requested)
        self.plugins.update(tag="v1.3.1", state="checking")
        self.snapshot()
        self.assertFalse(self.server.plugin_update_requested)

    def test_repair_requires_checked_same_release_and_same_app_version(self):
        for change in ({"state": "unavailable"}, {"tag": "v1.3.0"}, {"update_available": False}):
            self.assertFalse(server._plugin_repair_allowed(self.service, {**self.plugins, **change}))
        self.service.release = updater._release("v1.3.0")
        self.assertFalse(server._plugin_repair_allowed(self.service, {**self.plugins, "tag": "v1.3.0"}))


if __name__ == "__main__":
    unittest.main()
