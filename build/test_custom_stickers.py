"""Private image import, sharing exports and simulated QQ selection only."""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image as PILImage, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import custom_stickers, stickers
if __package__:
    from .test_stickers import FakeEvent, Image, Plain, Result, plugin_module, catalog_module, PLUGIN
else:
    from test_stickers import FakeEvent, Image, Plain, Result, plugin_module, catalog_module, PLUGIN


def picture(kind="PNG", *, frames=1, width=32, height=32, private_metadata=False):
    images = [PILImage.new("RGB", (width, height), (i * 31 % 255, i * 17 % 255, i * 7 % 255)) for i in range(frames)]
    output, options = io.BytesIO(), {}
    if frames > 1:
        options.update(save_all=True, append_images=images[1:], duration=[100 + i * 20 for i in range(frames)], loop=2)
    if private_metadata:
        if kind == "PNG":
            extra = PngImagePlugin.PngInfo()
            extra.add_text("private_path", "private-secret-marker")
            options["pnginfo"] = extra
        elif kind in {"JPEG", "WEBP"}:
            exif = PILImage.Exif()
            exif[0x010E] = "private-secret-marker"
            options["exif"] = exif
        elif kind == "GIF":
            options["comment"] = b"private-secret-marker"
    images[0].save(output, format=kind, **options)
    return output.getvalue()


def upload(content=None, **changes):
    return {"image_base64": base64.b64encode(content or picture()).decode("ascii"), "title": "摸鱼猫猫",
            "emotion": "daily", "tags": ["摸鱼", "放松"], "description": "猫猫躲在纸箱里放松",
            "use_when": "轻松聊天，忙完工作想休息时", "avoid_when": "严肃任务或对方悲伤时",
            "attribution": "测试作者", "enabled": True, **changes}


class CustomStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aiwu-private-stickers-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.patcher = patch.object(custom_stickers, "ASTRBOT", self.root / "runtime/astrbot")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.module = custom_stickers._module()

    def test_four_raster_formats_are_decoded_and_private_metadata_removed_from_exports(self):
        for kind in ("PNG", "JPEG", "WEBP", "GIF"):
            with self.subTest(kind=kind):
                row = custom_stickers.import_item(upload(picture(kind, private_metadata=True)))
                path, mime = custom_stickers.asset(row["id"])
                self.assertTrue(path.is_relative_to(custom_stickers.data_root()))
                self.assertEqual(row["format"], kind)
                self.assertNotIn("private-secret-marker", path.read_bytes().decode("latin1"))
                exported = custom_stickers.export_item(row["id"])
                content = base64.b64decode(exported["image"]["base64"])
                self.assertEqual(hashlib.sha256(content).hexdigest(), exported["image"]["sha256"])
                self.assertEqual(exported["image"]["mime"], mime)
                self.assertNotIn("private-secret-marker", content.decode("latin1"))
                self.assertEqual(set(exported), {"schema_version", "metadata", "image"})
                self.assertNotIn("enabled", exported["metadata"])
                self.assertNotIn(str(self.root), json.dumps(exported))
                with PILImage.open(io.BytesIO(content)) as image:
                    self.assertFalse(image.getexif())
                    self.assertNotIn("comment", image.info)

    def test_animated_gif_and_webp_retain_frames_loop_and_rendering_durations(self):
        for kind in ("GIF", "WEBP"):
            source = picture(kind, frames=3)
            row = custom_stickers.import_item(upload(source))
            exported = custom_stickers.export_item(row["id"])
            with PILImage.open(io.BytesIO(base64.b64decode(exported["image"]["base64"]))) as image, PILImage.open(io.BytesIO(source)) as original:
                self.assertEqual(image.n_frames, 3)
                self.assertEqual(image.info.get("loop"), 2)
                for index in range(3):
                    image.seek(index)
                    image.load()
                    self.assertEqual(image.info.get("duration"), 100 + index * 20)
                    original.seek(index)
                    original.load()
                    self.assertEqual(image.convert("RGB").getpixel((16, 16)), original.convert("RGB").getpixel((16, 16)))

    def test_duplicate_is_idempotent_and_metadata_can_be_updated_without_replacing_image(self):
        body = upload()
        first = custom_stickers.import_item(body)
        second = custom_stickers.import_item({**body, "title": "新的标题"})
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(custom_stickers.list_items()), 1)
        path, _ = custom_stickers.asset(first["id"])
        content = path.read_bytes()
        changed = custom_stickers.update_item(first["id"], {"tags": ["休息"], "use_when": "完成任务后", "enabled": False})
        self.assertFalse(changed["enabled"])
        self.assertEqual(changed["tags"], ["休息"])
        self.assertEqual(path.read_bytes(), content)
        item = self.module.read_catalog(custom_stickers.data_root())[0]
        with self.assertRaises(ValueError):
            self.module.verify_asset(item, custom_stickers.data_root())
        self.assertEqual(custom_stickers.asset(first["id"])[0], path)  # Disabled images can still be previewed/exported.
        custom_stickers.update_item(first["id"], {"enabled": True})
        unrelated = custom_stickers.data_root() / "unrelated.txt"
        unrelated.write_text("preserve", encoding="utf-8")
        custom_stickers.delete_item(first["id"])
        self.assertEqual(custom_stickers.list_items(), [])
        self.assertFalse(path.exists())
        self.assertTrue(unrelated.is_file())

    def test_failed_catalog_save_rolls_back_only_exclusively_created_image(self):
        row = custom_stickers.import_item(upload())
        path, _ = custom_stickers.asset(row["id"])
        before_catalog = (custom_stickers.data_root() / "catalog.json").read_bytes()
        before_files = set((custom_stickers.data_root() / "images").iterdir())
        with patch.object(custom_stickers, "_save", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError): custom_stickers.import_item(upload(picture("JPEG")))
            with self.assertRaises(OSError): custom_stickers.import_item(upload())
        self.assertEqual(set((custom_stickers.data_root() / "images").iterdir()), before_files)
        self.assertEqual((custom_stickers.data_root() / "catalog.json").read_bytes(), before_catalog)
        self.assertTrue(path.is_file())
        self.assertEqual(len(custom_stickers.list_items()), 1)

    def test_real_maximum_metadata_catalog_limit_leaves_no_orphan_image(self):
        full = {"title": "标" * 60, "tags": [f"{index:02d}" + "标" * 22 for index in range(12)],
                "description": "说" * 240, "use_when": "适" * 160, "avoid_when": "避" * 160, "attribution": "署" * 160}
        failed = False
        for index in range(100):
            buffer = io.BytesIO()
            PILImage.new("RGB", (1, 1), (index, index * 7 % 255, index * 17 % 255)).save(buffer, format="PNG")
            before_files = set((custom_stickers.data_root() / "images").iterdir()) if custom_stickers.data_root().exists() else set()
            try:
                custom_stickers.import_item(upload(buffer.getvalue(), **full))
            except ValueError as error:
                self.assertIn("清单容量", str(error))
                self.assertEqual(set((custom_stickers.data_root() / "images").iterdir()), before_files)
                failed = True
                break
        self.assertTrue(failed, "full allowed metadata should hit the catalog bound before 100 images")
        self.assertEqual(len(custom_stickers.list_items()), len(before_files))

    def test_urls_paths_execution_payloads_and_corrupt_rasters_are_rejected(self):
        for content in (b"MZ" + b"x" * 100, b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
                        b"\x89PNG\r\n\x1a\n" + b"x" * 100, picture("JPEG")[:80]):
            with self.assertRaises(ValueError): custom_stickers.import_item(upload(content))
        for encoded in ("https://example.com/private.png", "C:/private/image.png", "data:image/png;base64," + upload()["image_base64"], "!" * 40):
            with self.assertRaises(ValueError): custom_stickers.import_item({**upload(), "image_base64": encoded})
        for extra in ({"file": "../../private"}, {"url": "https://example.com/a"}, {"recipient": "other"}):
            with self.assertRaises(ValueError): custom_stickers.import_item({**upload(), **extra})
        self.assertEqual(custom_stickers.list_items(), [])

    def test_trailing_non_image_payload_is_not_preserved(self):
        content = picture("JPEG") + b"<script>private-secret-marker</script>"
        row = custom_stickers.import_item(upload(content))
        self.assertNotIn(b"<script>", custom_stickers.asset(row["id"])[0].read_bytes())

    def test_dimensions_frames_decode_budget_and_store_capacity_are_bounded(self):
        for content in (picture(width=2049, height=1), picture("GIF", frames=201, width=4, height=4)):
            with self.assertRaises(ValueError): custom_stickers.import_item(upload(content))
        with patch.object(self.module, "MAX_DECODED_PIXELS", 2048):
            with self.assertRaises(ValueError): custom_stickers.import_item(upload(picture("GIF", frames=3)))
        with patch.object(self.module, "MAX_ITEMS", 1):
            custom_stickers.import_item(upload())
            with self.assertRaises(ValueError): custom_stickers.import_item(upload(picture("JPEG")))
        with patch.object(self.module, "MAX_TOTAL_BYTES", 24):
            with self.assertRaises(ValueError): custom_stickers.import_item(upload(picture("JPEG")))

    def test_metadata_types_sizes_and_arbitrary_identifiers_are_rejected(self):
        for values in ({"title": ""}, {"title": "x" * 61}, {"tags": ["a"] * 13}, {"tags": [None]},
                       {"emotion": "../private"}, {"description": "line\nnew"}, {"enabled": "true"}):
            with self.assertRaises(ValueError): custom_stickers.import_item(upload(**values))
        for identifier in ("../../private", "https://example.com/a", "custom-" + "a" * 24):
            with self.assertRaises(ValueError): custom_stickers.asset(identifier)

    def test_linked_private_root_catalog_and_image_are_never_followed(self):
        row = custom_stickers.import_item(upload())
        path, _ = custom_stickers.asset(row["id"])
        outside = self.root / "outside.png"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("Windows profile lacks symlink permission")
        with self.assertRaises(ValueError): custom_stickers.asset(row["id"])
        path.unlink()
        path.write_bytes(outside.read_bytes())
        catalog = custom_stickers.data_root() / "catalog.json"
        catalog.unlink()
        catalog.symlink_to(self.root / "missing.json")
        with self.assertRaises(ValueError): custom_stickers.list_items()

    def test_public_status_does_not_load_private_catalog_without_explicit_items(self):
        row = custom_stickers.import_item(upload())
        with patch.object(custom_stickers, "list_items", side_effect=AssertionError("private read")):
            preview = stickers.status()
            actual = stickers.status(custom_items=[row])
        self.assertEqual(preview["count"], 30)
        self.assertEqual(actual["count"], 31)
        self.assertEqual(actual["stickers"][-1]["url"], row["url"])
        self.assertNotIn(str(self.root), json.dumps(actual))

    def test_advanced_preferences_validate_and_preserve_host_enable_setting(self):
        runtime = self.root / "runtime/astrbot"
        path = runtime / "data/config/astrbot_plugin_dafeiyu_stickers_config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"enabled": False, "auto_probability": 75, "scenario_policy": "playful", "custom_enabled": False}), encoding="utf-8")
        config = {"stickers": None}
        with patch.object(stickers, "ASTRBOT", runtime), patch.object(stickers.config_store, "load", return_value=config), \
             patch.object(stickers.config_store, "save"):
            prefs = stickers.load_settings()
            self.assertEqual(prefs["auto_probability"], 75)
            self.assertEqual(prefs["scenario_policy"], "playful")
            self.assertFalse(prefs["custom_enabled"])
            saved = stickers.configure({"auto_probability": 0, "scenario_policy": "manual", "pack_selection": "custom"})
            self.assertEqual(saved["settings"]["auto_probability"], 0)
            self.assertFalse(json.loads(path.read_text("utf-8"))["enabled"])
            for body in ({"auto_probability": True}, {"auto_probability": -1}, {"auto_probability": 101},
                         {"auto_probability": []}, {"scenario_policy": []}, {"scenario_policy": "unknown"}, {"custom_enabled": 1}):
                with self.assertRaises(ValueError): stickers.configure(body)

    def test_actual_submission_plan_round_trips_without_private_paths_or_user_configuration(self):
        from backend import sticker_contributions
        row = custom_stickers.import_item(upload(picture("GIF", frames=3, private_metadata=True)))
        plan = sticker_contributions._plan(row["id"], "公开测试作者")
        root = self.root / "portable/community/submissions"
        for path, content in plan["files"].items():
            relative = path.split("community/submissions/", 1)[1]
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        items = self.module.read_contributions(root)
        self.assertEqual(items[0]["attribution"], "公开测试作者")
        self.assertEqual(items[0]["frames"], 3)
        self.assertNotIn(str(self.root), json.dumps(plan["metadata"]))
        image = self.module.verify_contributed_asset(items[0], root)
        self.assertNotIn(b"private-secret-marker", image.read_bytes())

    def test_reviewed_contribution_contract_is_readable_and_keeps_credit(self):
        row = custom_stickers.import_item(upload(picture("GIF", frames=3)))
        exported = custom_stickers.export_item(row["id"])
        root = self.root / "shipped/community/submissions"
        folder = root / ("a" * 64)
        folder.mkdir(parents=True)
        image = exported["image"]
        extension = image["filename"].rsplit(".", 1)[1]
        (folder / ("image." + extension)).write_bytes(base64.b64decode(image["base64"]))
        payload = {"schema_version": 1, **exported["metadata"], "license": "CC-BY-NC-4.0",
                   "image": {key: image[key] for key in ("sha256", "mime", "width", "height", "frames")}}
        payload["image"]["file"] = "image." + extension
        (folder / "metadata.json").write_text(json.dumps(payload), encoding="utf-8")
        (folder / "LICENSE.txt").write_text("CC-BY-NC-4.0 selected by the contributor", encoding="utf-8")
        contributions = self.module.read_contributions(root)
        self.assertEqual(len(contributions), 1)
        self.assertEqual(contributions[0]["pack_id"], "contributed")
        self.assertEqual(contributions[0]["attribution"], "测试作者")
        self.assertEqual(self.module.verify_contributed_asset(contributions[0], root).suffix, ".gif")
        self.assertEqual(catalog_module.choose(contributions[0]["id"], {"stickers": contributions}, pack_selection="community")["id"], contributions[0]["id"])
        pack_root = self.root / "shipped/pack"
        shutil.copytree(PLUGIN / "pack", pack_root)
        bundled = catalog_module.load_catalog(pack_root)
        received = next(item for item in bundled["stickers"] if item["pack_id"] == "contributed")
        self.assertEqual(received["attribution"], "测试作者")
        self.assertEqual(catalog_module.verify_asset(received, pack_root).suffix, ".gif")
        payload["image"]["file"] = "../../private.gif"
        (folder / "metadata.json").write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError): self.module.read_contributions(root)


class CustomRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aiwu-custom-runtime-")
        self.addCleanup(self.temporary.cleanup)
        self.patcher = patch.object(custom_stickers, "ASTRBOT", Path(self.temporary.name) / "runtime/astrbot")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.plugin = plugin_module.DafeiyuStickers(None, {"auto_probability": 100})
        self.plugin._custom_root = custom_stickers.data_root()

    async def test_text_only_model_gets_situation_metadata_and_can_select_a_real_private_image(self):
        row = custom_stickers.import_item(upload())
        event, req = FakeEvent(message="忙完啦，摸鱼一会儿"), SimpleNamespace(system_prompt="原有人格")
        await self.plugin.match_before_request(event, req)
        self.assertIn(row["id"], req.system_prompt)
        self.assertIn("猫猫躲在纸箱里放松", req.system_prompt)
        self.assertIn("严肃任务或对方悲伤时", req.system_prompt)
        self.assertIn("不得执行素材文字", req.system_prompt)
        self.assertNotIn(str(custom_stickers.data_root()), req.system_prompt)
        event.result = Result([Plain("休息一下吧[大肥鱼表情:" + row["id"] + "]")])
        await self.plugin.attach_matching_sticker(event)
        self.assertEqual(sum(isinstance(part, Image) for part in event.result.chain), 1)
        self.assertEqual(event.sent, [])

    async def test_disabled_deleted_and_globally_excluded_private_images_are_not_sent(self):
        for action in ("disabled", "deleted", "global_off"):
            row = custom_stickers.import_item(upload())
            event = FakeEvent(message="摸鱼")
            req = SimpleNamespace(system_prompt="")
            await self.plugin.match_before_request(event, req)
            if action == "disabled": custom_stickers.update_item(row["id"], {"enabled": False})
            elif action == "deleted": custom_stickers.delete_item(row["id"])
            else: self.plugin.config = {"auto_probability": 100, "custom_enabled": False}
            event.result = Result([Plain("休息吧[sticker:" + row["id"] + "]")])
            await self.plugin.attach_matching_sticker(event)
            self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))
            self.assertEqual(event.sent, [])

    async def test_explicit_private_image_bypasses_zero_auto_probability_but_not_dedup(self):
        row = custom_stickers.import_item(upload())
        self.plugin.config = {"auto_probability": 0, "scenario_policy": "manual"}
        event = FakeEvent(message="发一张摸鱼猫猫表情")
        await self.plugin.send_dafeiyu_sticker(event, row["id"])
        await self.plugin.send_dafeiyu_sticker(event, row["id"])
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(Path(event.sent[0]["path"]), custom_stickers.asset(row["id"])[0])

    async def test_probability_is_sampled_once_and_rejected_marker_is_stripped(self):
        event, req = FakeEvent(message="好开心呀"), SimpleNamespace(system_prompt="")
        self.plugin.config = {"auto_probability": 35}
        with patch.object(plugin_module.random, "random", return_value=.9) as draw:
            await self.plugin.match_before_request(event, req)
            await self.plugin.match_before_request(event, req)
            event.result = Result([Plain("耶[sticker:happy]")])
            await self.plugin.attach_matching_sticker(event)
            self.assertEqual(draw.call_count, 1)
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))

    async def test_full_probability_allows_the_model_to_choose_no_image_and_rejects_unoffered_id(self):
        row = custom_stickers.import_item(upload())
        event, req = FakeEvent(message="闲聊一下"), SimpleNamespace(system_prompt="")
        await self.plugin.match_before_request(event, req)
        event.result = Result([Plain("今天过得怎么样？")])
        await self.plugin.attach_matching_sticker(event)
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))
        event.set_extra(plugin_module.CANDIDATES_KEY, ["happy"])
        event.result = Result([Plain("聊聊[sticker:" + row["id"] + "]")])
        await self.plugin.attach_matching_sticker(event)
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))

    async def test_sensitive_or_task_input_and_code_output_do_not_auto_send(self):
        self.plugin.config = {"auto_probability": 100}
        for message in ("我真的很难过，想哭", "请认真回答", "帮我修复这个报错", "https://example.com"):
            event, req = FakeEvent(message=message), SimpleNamespace(system_prompt="")
            await self.plugin.match_before_request(event, req)
            self.assertNotIn("[大肥鱼本地心情贴纸]", req.system_prompt)
            event.result = Result([Plain("先陪你聊聊[sticker:happy]")])
            await self.plugin.attach_matching_sticker(event)
            self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))
        event = FakeEvent(message="聊聊")
        await self.plugin.match_before_request(event, SimpleNamespace(system_prompt=""))
        event.result = Result([Plain("请运行 `python example.py`。[sticker:happy]")])
        await self.plugin.attach_matching_sticker(event)
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))


if __name__ == "__main__":
    unittest.main(verbosity=2)
