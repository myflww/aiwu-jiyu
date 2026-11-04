"""Offline diagnostics/recovery contracts. No real services, keys or installers."""
from __future__ import annotations

import io
import json
from pathlib import Path
import hashlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from backend import config_store, health, recovery, tasks


class HealthTests(unittest.TestCase):
    def check(self, *, online=False, reason="all", proactive=None):
        inventory = [dict(id=x, installed=True, enabled=True) for x in
            (health.companion.PLUGIN_NAME, health.stickers.PLUGIN_ID, "astrbot_plugin_love_house", "astrbot_plugin_pc_operator")]
        with patch.object(config_store, "load", return_value={"owner_qq": "private-identifier", "model": {"model": "model", "api_key_enc": "secret-key"},
                "pc_access": {"enabled": True, "desktop_enabled": False}}), \
             patch.object(config_store, "api_key", return_value="secret-key"), \
             patch.object(health.processes, "status", return_value={k: {"up": online} for k in ("astrbot", "napcat", "onebot")}), \
             patch.object(health.napcat_api, "login_status", return_value={"isLogin": online}) as login, \
             patch.object(health.plugins, "list_plugins", return_value=inventory), \
             patch.object(health.companion, "status", return_value=proactive or {"proactive_enabled": True, "runtime": {"worker_running": online, "code": "waiting_interval", "message": "等待机会", "session_known": online}}), \
             patch.object(health.stickers, "load_settings", return_value={"auto_match_enabled": True, "scenario_policy": "balanced"}), \
             patch.object(health.lovehouse, "detect", return_value={"running": online}), \
             patch.object(health.lovehouse, "plugin_status", return_value={"enabled": True, "bindings": 1}):
            result = health.snapshot(reason)
            if not online:
                login.assert_not_called()
            return result

    def test_offline_switches_do_not_claim_end_to_end_readiness_or_leak_secrets(self):
        result = self.check()
        self.assertEqual(result["overall"], "blocked")
        encoded = json.dumps(result)
        self.assertNotIn("secret-key", encoded)
        self.assertNotIn("private-identifier", encoded)
        self.assertFalse(result["model_test_performed"] or result["qq_test_performed"])
        self.assertTrue(result["readonly"])
        self.assertTrue(any(x["id"] == "model" and x["evidence"]["model_request_tested"] is False for x in result["checks"]))

    def test_focuses_actual_reason_and_never_says_read_or_delivery_was_proven(self):
        result = self.check(online=True, reason="proactive")
        ids = {c["id"] for c in result["checks"]}
        self.assertIn("proactive_worker", ids)
        self.assertIn("memory_service", ids)
        self.assertNotIn("sticker_rule", ids)
        self.assertNotIn("computer_grants", ids)
        self.assertTrue(all(a["code"].startswith("open_") for c in result["checks"] for a in c["actions"]))

    def test_quiet_hours_is_explained_as_attention_not_a_broken_service(self):
        result = self.check(online=True, reason="proactive", proactive={"proactive_enabled": True, "runtime": {
            "worker_running": True, "code": "quiet_hours", "message": "当前免打扰", "last_receipt": "adapter_accepted"}})
        worker = next(c for c in result["checks"] if c["id"] == "proactive_worker")
        self.assertEqual(worker["status"], "attention")
        self.assertEqual(worker["evidence"]["last_receipt"], "adapter_accepted")
        with self.assertRaises(ValueError):
            health.snapshot("../../secret")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-recovery-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.assets = self.home / "assets"
        self.plugins = self.home / "runtime/astrbot/data/plugins"
        self.identifier = "astrbot_plugin_example"
        self.content = b"# reviewed fixture\n"
        target = self.plugins / self.identifier
        target.mkdir(parents=True)
        (target / "main.py").write_bytes(self.content)
        (self.assets / "plugins").mkdir(parents=True)
        manifest = {"plugins": [{"id": self.identifier, "files": [{"path": "main.py", "size": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest()}]}]}
        (self.assets / "plugins/manifest.json").write_text(json.dumps(manifest))
        self.config = self.home / "config.json"
        self.original = {"owner_qq": "private-account", "model": {"api_key_enc": "private-key"},
            "pc_access": {"enabled": True, "members": {"private-admin": {"run": True}}},
            "companion": {"daily_limit": 4, "quiet_start": 23}, "stickers": {"auto_probability": 35}, "ui": {"mascot_style": "classic"}}
        self.config.write_text(json.dumps(self.original))
        for module, field, value in ((recovery, "APP_HOME", self.home), (recovery, "ASSETS", self.assets), (config_store, "CONFIG_FILE", self.config)):
            context = patch.object(module, field, value)
            context.start(); self.addCleanup(context.stop)
        for obj, field, value in ((recovery.plugins, "plugin_path", lambda identifier: self.plugins / identifier),
                                 (recovery, "_current_version", lambda: "1.4.0"),
                                 (recovery.processes, "status", lambda: {k: {"up": False} for k in ("astrbot", "napcat", "onebot")})):
            context = patch.object(obj, field, value); context.start(); self.addCleanup(context.stop)

    def rewrite(self, backup_id, change):
        path = recovery._path(backup_id)
        with zipfile.ZipFile(path) as archive:
            values = {n: archive.read(n) for n in archive.namelist()}
        change(values)
        with zipfile.ZipFile(path, "w") as archive:
            for name, value in values.items(): archive.writestr(name, value)

    def test_restore_preserves_current_identity_credentials_grants_memory_and_private_library(self):
        backup = recovery.create_backup()["backup"]
        raw = recovery._path(backup["id"]).read_bytes()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertNotIn(b"private-key", archive.read("preferences.json"))
            self.assertNotIn(b"private-account", archive.read("preferences.json"))
            self.assertNotIn(b"private-admin", archive.read("preferences.json"))
            self.assertEqual(set(archive.namelist()), {"manifest.json", "preferences.json", "plugins/" + self.identifier + "/main.py"})
        changed = {**self.original, "owner_qq": "new-owner", "model": {"api_key_enc": "new-key"}, "companion": {"daily_limit": 1}}
        self.config.write_text(json.dumps(changed))
        database = self.home / "runtime/astrbot/data/data_v4.db"
        database.write_bytes(b"chat-sentinel")
        result = recovery.restore({"backup_id": backup["id"], "confirmed": True})
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved["owner_qq"], "new-owner")
        self.assertEqual(saved["model"]["api_key_enc"], "new-key")
        self.assertEqual(saved["pc_access"], changed["pc_access"])
        self.assertEqual(saved["companion"]["daily_limit"], 4)
        companion_cfg = json.loads((self.home / "runtime/astrbot/data/config/astrbot_plugin_dafeiyu_companion_config.json").read_text("utf-8"))
        sticker_cfg = json.loads((self.home / "runtime/astrbot/data/config/astrbot_plugin_dafeiyu_stickers_config.json").read_text("utf-8"))
        self.assertEqual(companion_cfg["daily_limit"], 4)
        self.assertEqual(companion_cfg["owner_qq"], "new-owner")
        self.assertEqual(sticker_cfg["auto_probability"], 35)
        self.assertEqual(database.read_bytes(), b"chat-sentinel")
        self.assertTrue(result["safety_backup"]["id"])

    def test_confirmation_running_services_and_cross_version_are_blocked_without_writes(self):
        backup = recovery.create_backup()["backup"]
        before = self.config.read_bytes()
        for body in ({"backup_id": backup["id"]}, {"backup_id": "../escape", "confirmed": True}, {"backup_id": backup["id"], "confirmed": "true"}):
            with self.assertRaises(ValueError): recovery.restore(body)
        with patch.object(recovery.processes, "status", return_value={"astrbot": {"up": True}}):
            with self.assertRaisesRegex(ValueError, "先在首页关闭"): recovery.restore({"backup_id": backup["id"], "confirmed": True})
        with patch.object(recovery, "_current_version", return_value="1.5.0"):
            self.assertFalse(recovery.list_backups()["backups"][0]["compatible"])
            self.assertEqual(recovery.rollback_version(backup["id"]), "1.4.0")
            with self.assertRaisesRegex(ValueError, "另一软件版本"): recovery.restore({"backup_id": backup["id"], "confirmed": True})
        self.assertEqual(before, self.config.read_bytes())

    def test_tampered_archive_and_cross_home_backup_never_restore(self):
        backup = recovery.create_backup()["backup"]
        self.rewrite(backup["id"], lambda values: values.update({"plugins/" + self.identifier + "/main.py": b"malicious"}))
        with self.assertRaisesRegex(ValueError, "匹配|校验"): recovery.restore({"backup_id": backup["id"], "confirmed": True})
        backup = recovery.create_backup()["backup"]
        other = self.home / "other-profile"; other.mkdir()
        with patch.object(recovery, "_identity", return_value="different-profile"):
            with self.assertRaisesRegex(ValueError, "不属于"): recovery.rollback_version(backup["id"])

    def test_unreviewed_installed_code_blocks_backup_instead_of_exporting_arbitrary_code(self):
        (self.plugins / self.identifier / "main.py").write_bytes(b"locally-modified")
        with self.assertRaisesRegex(ValueError, "本地修改"): recovery.create_backup()
        self.assertEqual(list(recovery._home().glob("*.zip")), [])

    def test_atomic_write_failure_rolls_back_created_plugin_and_does_not_touch_current_config(self):
        backup = recovery.create_backup()["backup"]
        target = self.plugins / self.identifier / "main.py"
        target.unlink()
        before = self.config.read_bytes()
        atomic = recovery._atomic
        count = 0
        def fail_second(path, raw):
            nonlocal count
            count += 1
            if count == 2: raise OSError("simulated disk error")
            return atomic(path, raw)
        with patch.object(recovery, "_atomic", side_effect=fail_second):
            with self.assertRaises(OSError): recovery.restore({"backup_id": backup["id"], "confirmed": True})
        self.assertFalse(target.exists())
        self.assertEqual(self.config.read_bytes(), before)

    def test_retention_is_bounded_and_unknown_files_are_never_deleted(self):
        first = recovery.create_backup()["backup"]["id"]
        unknown = recovery._home() / "unrelated.zip"; unknown.write_bytes(b"do-not-touch")
        for _ in range(5): recovery.create_backup()
        self.assertEqual(len(recovery.list_backups()["backups"]), 5)
        self.assertFalse(recovery._path(first).exists())
        self.assertEqual(unknown.read_bytes(), b"do-not-touch")

    def test_external_config_change_during_planning_is_preserved_and_cancels_restore(self):
        backup = recovery.create_backup()["backup"]
        preferences = recovery._plugin_preferences
        external = {**self.original, "owner_qq": "new-external-owner", "model": {"api_key_enc": "fresh-private-key"}}
        def change_after_config_read(config):
            result = preferences(config)
            self.config.write_text(json.dumps(external), "utf-8")
            return result
        with patch.object(recovery, "_plugin_preferences", side_effect=change_after_config_read):
            with self.assertRaisesRegex(ValueError, "文件发生变化"):
                recovery.restore({"backup_id": backup["id"], "confirmed": True})
        self.assertEqual(json.loads(self.config.read_text("utf-8")), external)


if __name__ == "__main__": unittest.main()
