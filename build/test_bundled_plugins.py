"""Check bundled add-on integrity and upgrades without using a live profile."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
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
