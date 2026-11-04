"""Complete local persona cards and native partial writes; no live service."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import personas


class NativeAPI:
    def __init__(self):
        self.rows = [{"persona_id": personas.config_store.PERSONA_ID,
            "system_prompt": "这是用户以前自己写的大肥鱼人格，请保留。", "tools": ["original_tool"],
            "skills": ["original_skill"], "begin_dialogs": [{"role": "assistant", "content": "以前的开场"}],
            "custom_error_message": "原有错误提示"},
            {"persona_id": "另一个人格", "system_prompt": "不能碰它", "tools": None, "skills": ["unrelated"]}]
        self.calls = []
        self.reads = 0
        self.before_read = None
        self.confirm_status = "ok"
    def list_personas(self, token):
        self.reads += 1
        if self.before_read:
            self.before_read(self.reads)
        return deepcopy(self.rows)
    def request(self, path, payload, token, method="POST"):
        self.calls.append((path, deepcopy(payload), method))
        if self.confirm_status != "ok":
            return {"status": self.confirm_status}
        if path == "/api/v1/personas/by-id" and method == "PUT":
            row = next(row for row in self.rows if row["persona_id"] == payload["persona_id"])
            row.update(deepcopy(payload))
        elif path == "/api/v1/personas" and method == "POST":
            self.rows.append(deepcopy(payload))
        else:
            raise AssertionError("Unexpected native endpoint or method")
        return {"status": "ok"}


class PersonaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-personas-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.native = NativeAPI()
        self.config = {"owner_qq": "123456789", "bot_qq": "987654321", "unrelated": {"keep": True}}
        self.patchers = [patch.object(personas, "RUN_DIR", self.directory),
            patch.object(personas.astrbot_api, "dashboard_token", return_value="synthetic-test-token"),
            patch.object(personas.astrbot_api, "list_personas", side_effect=self.native.list_personas),
            patch.object(personas.astrbot_api, "_request", side_effect=self.native.request),
            patch.object(personas.processes, "port_up", return_value=True),
            patch.object(personas.config_store, "is_installed", return_value=True),
            patch.object(personas.config_store, "load", side_effect=lambda: deepcopy(self.config)),
            patch.object(personas.config_store, "save", side_effect=lambda value: self.config.update(deepcopy(value)))]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)

    def revision(self):
        return personas._revision(self.native.rows[0]["system_prompt"])

    def apply(self, card="natural"):
        return personas.apply({"card_id": card, "expected_revision": self.revision()})

    def test_five_complete_distinct_cards_with_attribution_sources_and_full_common_rules(self):
        catalog = personas.catalog()
        self.assertEqual({row["id"] for row in catalog["cards"]}, {"natural", "classic", "gentle", "playful", "study"})
        prompts = []
        for row in catalog["cards"]:
            value = personas.preview({"card_id": row["id"]})
            text = value["prompt"]
            self.assertGreater(len(text), 1800)
            self.assertIn("先听完整，再接话", text)
            self.assertIn("群里的分寸", text)
            self.assertIn("不展示隐藏推理", text)
            self.assertTrue(row["author"] and row["license"])
            self.assertTrue(all(url.startswith("https://") for url in row["source_urls"]))
            if row["source_kind"] == "社区参考改写":
                self.assertTrue(row["source_blobs"])
                self.assertTrue(all(len(digest) == 40 and all(char in "0123456789abcdef" for char in digest) for digest in row["source_blobs"]))
            prompts.append(value["sha256"])
        self.assertEqual(len(set(prompts)), 5)
        self.assertEqual(self.native.calls, [])

    def test_preview_is_strictly_local_no_dashboard_port_probe_or_model_api(self):
        with patch.object(personas.astrbot_api, "dashboard_token", side_effect=AssertionError("No local API")), \
             patch.object(personas.astrbot_api, "list_personas", side_effect=AssertionError("No persona API")), \
             patch.object(personas.astrbot_api, "_request", side_effect=AssertionError("No model or local API")), \
             patch.object(personas.processes, "port_up", side_effect=AssertionError("No port probe")):
            self.assertTrue(personas.preview({"card_id": "gentle"})["ok"])
            for body in ({"card_id": "../secret"}, {"card_id": "natural", "path": "C:/secret"}, {"card_id": []}):
                with self.assertRaises(ValueError): personas.preview(body)

    def test_apply_updates_only_fixed_persona_prompt_preserving_tools_skills_and_dialogs(self):
        before, unrelated = deepcopy(self.native.rows[0]), deepcopy(self.native.rows[1])
        config_before = deepcopy(self.config)
        result = self.apply()
        self.assertTrue(result["changed"])
        self.assertEqual(self.native.calls[0][0], "/api/v1/personas/by-id")
        self.assertEqual(set(self.native.calls[0][1]), {"persona_id", "system_prompt"})
        for key in ("tools", "skills", "begin_dialogs", "custom_error_message"):
            self.assertEqual(self.native.rows[0][key], before[key])
        self.assertEqual(self.native.rows[1], unrelated)
        for key in config_before:
            self.assertEqual(self.config[key], config_before[key])
        backup = json.loads((self.directory / "persona-backups" / result["backup_id"]).read_text("utf-8"))
        self.assertEqual(backup["system_prompt"], before["system_prompt"])
        self.assertNotIn("tools", backup)
        self.assertNotIn("password", backup)
        self.assertEqual(backup["sha256"], hashlib.sha256(before["system_prompt"].encode()).hexdigest())

    def test_restore_requires_confirmation_and_restores_prompt_not_other_fields_or_password(self):
        original = deepcopy(self.native.rows[0])
        result = self.apply("playful")
        backup_path = self.directory / "persona-backups" / result["backup_id"]
        backup = json.loads(backup_path.read_text("utf-8"))
        backup.update(password="synthetic-field-must-not-be-restored", tools=["forged_admin_tool"], persona_role="admin")
        backup_path.write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")
        self.native.rows[0]["tools"] = ["new_explicit_tool"]
        body = {"backup_id": result["backup_id"], "expected_revision": self.revision(), "confirmed": True}
        with self.assertRaises(ValueError): personas.restore({**body, "confirmed": False})
        restored = personas.restore(body)
        self.assertTrue(restored["changed"])
        self.assertEqual(self.native.rows[0]["system_prompt"], original["system_prompt"])
        self.assertEqual(self.native.rows[0]["tools"], ["new_explicit_tool"])
        self.assertNotIn("password", self.native.rows[0])
        self.assertNotIn("persona_role", self.native.rows[0])
        self.assertTrue(all(call[1]["persona_id"] == personas.config_store.PERSONA_ID for call in self.native.calls))

    def test_stale_revision_and_external_prompt_change_between_backup_and_write_stop_apply(self):
        with self.assertRaisesRegex(ValueError, "已变化"):
            personas.apply({"card_id": "natural", "expected_revision": "old-preview"})
        self.assertFalse(self.native.calls)
        def external_edit(number):
            if number == 3: self.native.rows[0]["system_prompt"] = "另一窗口刚刚编辑了人格"
        # Previous failed attempt consumed read1. The next apply reads 2,
        # backs it up, then read3 catches the external edit before write.
        self.native.before_read = external_edit
        with self.assertRaisesRegex(ValueError, "其他窗口修改"):
            self.apply()
        self.assertFalse(self.native.calls)
        self.assertEqual(self.native.rows[0]["system_prompt"], "另一窗口刚刚编辑了人格")
        self.assertEqual(len(personas.backups()), 1)

    def test_idempotent_same_card_does_not_create_backup_or_extra_write(self):
        self.apply()
        count = len(personas.backups())
        calls = len(self.native.calls)
        result = self.apply()
        self.assertFalse(result["changed"])
        self.assertEqual(len(personas.backups()), count)
        self.assertEqual(len(self.native.calls), calls)

    def test_missing_fixed_persona_creates_only_it_with_no_implicit_grants(self):
        unrelated = deepcopy(self.native.rows[1])
        self.native.rows = [unrelated]
        result = personas.apply({"card_id": "natural", "expected_revision": None})
        self.assertTrue(result["changed"])
        created = next(row for row in self.native.rows if row["persona_id"] == personas.config_store.PERSONA_ID)
        self.assertEqual(created["tools"], [])
        self.assertEqual(created["skills"], [])
        self.assertEqual(self.native.rows[0], unrelated)
        self.assertIsNone(result["backup_id"])

    def test_tool_grants_preserve_none_merge_only_supported_names_and_never_change_prompt(self):
        original = self.native.rows[0]["system_prompt"]
        self.native.rows[0]["tools"] = None
        self.assertFalse(personas.grant_bundled_tools(["send_dafeiyu_sticker"])["changed"])
        self.assertFalse(self.native.calls)
        self.native.rows[0]["tools"] = ["original_tool"]
        grants = ["send_dafeiyu_sticker", "dafeiyu_web_search", "dafeiyu_read_qq_profile",
                  "dafeiyu_prepare_nickname", "dafeiyu_prepare_signature", "dafeiyu_prepare_avatar", "dafeiyu_prepare_qzone"]
        self.assertTrue(personas.grant_bundled_tools(grants + grants)["changed"])
        self.assertEqual(self.native.rows[0]["tools"], ["original_tool", *grants])
        self.assertEqual(set(self.native.calls[0][1]), {"persona_id", "tools"})
        self.assertEqual(self.native.rows[0]["system_prompt"], original)
        self.assertFalse(personas.grant_bundled_tools(grants)["changed"])
        for bad in (["execute_shell"], ["dafeiyu_web_search", "arbitrary_tool"], "send_dafeiyu_sticker", [None]):
            with self.assertRaises(ValueError): personas.grant_bundled_tools(bad)

    def test_offline_status_can_preview_and_tool_grants_stay_pending(self):
        with patch.object(personas.processes, "port_up", return_value=False), \
             patch.object(personas.astrbot_api, "dashboard_token", side_effect=AssertionError("No login offline")):
            status = personas.status()
            self.assertFalse(status["can_apply"])
            self.assertEqual(len(status["cards"]), 5)
            self.assertTrue(personas.grant_bundled_tools(["send_dafeiyu_sticker"])["pending"])
            with self.assertRaises(ValueError): self.apply()
        self.assertFalse(self.native.calls)

    def test_backup_id_traversal_wrong_persona_tampering_and_unbounded_backups_rejected(self):
        result = self.apply()
        common = {"expected_revision": self.revision(), "confirmed": True}
        for name in ("../secret.json", "C:/outside.json", result["backup_id"] + "/../else.json"):
            with self.assertRaises(ValueError): personas.restore({**common, "backup_id": name})
        path = self.directory / "persona-backups" / result["backup_id"]
        record = json.loads(path.read_text("utf-8"))
        record["persona_id"] = "另一个人格"
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ValueError): personas.restore({**common, "backup_id": result["backup_id"]})
        record["persona_id"] = personas.config_store.PERSONA_ID
        record["system_prompt"] += "tampered"
        path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(ValueError): personas.restore({**common, "backup_id": result["backup_id"]})
        for index in range(19):
            copy = path.parent / f"20261104-120000-{index:08x}.json"
            copy.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "20份"):
            self.apply("gentle")

    def test_manifest_card_and_common_paths_cannot_escape_asset_root(self):
        assets = self.directory / "assets"
        shutil.copytree(ROOT / "assets/persona", assets / "persona")
        manifest = assets / "persona/cards.json"
        original = json.loads(manifest.read_text("utf-8"))
        with patch.object(personas, "ASSETS", assets):
            for field, value in (("file", "../private.txt"), ("file", "C:/outside.txt")):
                changed = deepcopy(original)
                changed["cards"][0][field] = value
                manifest.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaises(ValueError): personas.prompt("natural")
            changed = deepcopy(original)
            changed["common_file"] = "../private.txt"
            manifest.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError): personas.prompt("natural")

    def test_linked_parent_directory_cannot_redirect_card_or_backup_reads(self):
        assets = self.directory / "assets"
        assets.mkdir()
        source = ROOT / "assets/persona"
        try:
            (assets / "persona").symlink_to(source, target_is_directory=True)
        except OSError:
            self.skipTest("This Windows account cannot create test symlinks")
        with patch.object(personas, "ASSETS", assets):
            with self.assertRaises(ValueError):
                personas.prompt("natural")

    def test_fresh_home_does_not_authenticate_another_runtime_on_the_shared_port(self):
        with patch.object(personas.config_store, "is_installed", return_value=False):
            self.assertFalse(personas.status()["runtime_online"])
            self.assertTrue(personas.grant_bundled_tools(["dafeiyu_web_search"])["pending"])
            with self.assertRaises(ValueError): self.apply()
        self.assertEqual(self.native.calls, [])

    def test_repeated_card_switching_reuses_identical_backups(self):
        for _ in range(5):
            for card in ("natural", "gentle", "classic", "playful", "study"):
                self.apply(card)
        self.assertLessEqual(len(personas.backups()), 6)


if __name__ == "__main__":
    unittest.main()
