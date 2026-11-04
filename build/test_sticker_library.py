"""Bounded local imports and explicit preferences; all remote transport is fake."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from backend import custom_stickers, sticker_library, sticker_materials, stickers
if __package__:
    from .test_custom_stickers import picture, upload
    from .test_stickers import plugin_module, Image
else:
    from test_custom_stickers import picture, upload
    from test_stickers import plugin_module, Image


def encoded(raw):return base64.b64encode(raw).decode("ascii")


def zipped(entries):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries:archive.writestr(name, raw)
    return content.getvalue()


class LibraryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-library-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        p = patch.object(custom_stickers, "ASTRBOT", self.root / "runtime/astrbot")
        p.start();self.addCleanup(p.stop)
        p = patch.object(stickers, "load_settings", return_value=dict(stickers.DEFAULT_SETTINGS))
        p.start();self.addCleanup(p.stop)


class LocalLibraryTests(LibraryFixture, unittest.TestCase):
    def test_batch_reports_each_result_and_reuses_disabled_image_without_changing_metadata(self):
        first = custom_stickers.import_item(upload())
        custom_stickers.update_item(first["id"], {"enabled": False})
        result = custom_stickers.import_batch({"files": [
            {"name": "重新导入.png", "image_base64": encoded(picture()), "metadata": {"title": "不应覆盖", "enabled": True}},
            {"name": "开心.jpg", "image_base64": encoded(picture("JPEG"))},
            {"name": "坏图.png", "image_base64": "invalid"}]})
        self.assertEqual([row["status"] for row in result["results"]], ["reused", "imported", "invalid"])
        self.assertEqual(result["results"][0]["item"]["title"], first["title"])
        self.assertFalse(result["results"][0]["item"]["enabled"])
        self.assertEqual(len(custom_stickers.list_items()), 2)

    def test_batch_count_capacity_and_pathnames_reject_before_dangerous_writes(self):
        with self.assertRaises(ValueError):custom_stickers.import_batch({"files": [{}] * 21})
        result = custom_stickers.import_batch({"files": [{"name": "C:\\private\\a.png", "image_base64": encoded(picture())}]})
        self.assertEqual(result["invalid"], 1)
        self.assertNotIn("private", json.dumps(result))
        self.assertFalse(custom_stickers.data_root().exists())

    def test_zip_nested_images_and_unsupported_files_have_explicit_results(self):
        result = custom_stickers.import_zip({"archive_base64": encoded(zipped([
            ("pack/happy.png", picture()), ("pack/hello.gif", picture("GIF", frames=3)), ("README.md", b"metadata only")]))})
        self.assertEqual((result["imported"], result["invalid"]), (2, 1))
        self.assertEqual(len(custom_stickers.list_items()), 2)

    def test_zip_path_symlink_duplicate_and_bomb_are_all_rejected_before_import(self):
        link = zipfile.ZipInfo("linked.png");link.create_system = 3;link.external_attr = (stat.S_IFLNK | 0o777) << 16
        cases = [[("first.png", picture()), ("../escape.png", picture())],
                 [("x.png", picture()), ("X.png", picture())], [(link, b"/outside")],
                 [("bomb.txt", b"x" * (2 * 1024 * 1024))], [("x.py", b"print('never run')")]]
        for entries in cases:
            with self.subTest(entries=str(entries[0][0])):
                with self.assertRaises(ValueError):custom_stickers.import_zip({"archive_base64": encoded(zipped(entries))})
                self.assertFalse(custom_stickers.list_items())

    def test_exact_duplicate_stays_read_only_when_catalog_save_unavailable(self):
        row = custom_stickers.import_item(upload())
        with patch.object(custom_stickers, "_save", side_effect=OSError("disk")):
            result = custom_stickers.import_batch({"files": [{"name": "same.png", "image_base64": encoded(picture())}]})
        self.assertEqual(result["reused"], 1)
        self.assertEqual(result["results"][0]["item"]["id"], row["id"])

    def test_favorites_usage_scope_and_explicit_feedback_are_separate_and_clearable(self):
        row = custom_stickers.import_item(upload())
        store = sticker_library.store()
        origin = "qq:FriendMessage:123456789"
        store.set_favorite(row["id"], True)
        store.record_used(row["id"], origin=origin, now=1000)
        store.reject(row["id"], scene="greeting", origin=origin)
        self.assertEqual(store.annotate([row], origin=origin, scene="greeting")[0]["scene_rejections"], 1)
        self.assertEqual(store.annotate([row], origin="qq:FriendMessage:987654321", scene="greeting")[0]["scene_rejections"], 0)
        self.assertEqual(store.annotate([row], origin=origin, scene="casual")[0]["scene_rejections"], 0)
        store.clear_feedback(row["id"], origin=origin, scene="greeting")
        annotated = store.annotate([row], origin=origin, scene="greeting")[0]
        self.assertEqual((annotated["favorite"], annotated["use_count"], annotated["last_used"], annotated["scene_rejections"]), (True, 1, 1000, 0))
        self.assertNotIn(b"123456789", (custom_stickers.data_root().parent / "library.sqlite3").read_bytes())

    def test_concurrent_receipt_writes_keep_every_counter(self):
        store = sticker_library.store()
        def write(_):store.record_used("happy", origin="qq:FriendMessage:123456789")
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(write, range(24)))
        self.assertEqual(store.annotate([{"id": "happy"}])[0]["use_count"], 24)

    def test_search_favorite_recent_and_local_preview_never_send_or_call_models(self):
        row = custom_stickers.import_item(upload())
        sticker_library.set_favorite({"id": row["id"], "favorite": True})
        self.assertEqual(sticker_library.browse({"query": "摸鱼", "favorites_only": True})["count"], 1)
        result = sticker_library.preview({"text": "忙完啦，摸鱼放松一下", "scene": "casual"})
        self.assertFalse(result["model_called"])
        self.assertFalse(result["message_sent"])
        self.assertEqual(result["candidates"][0]["id"], row["id"])
        sticker_library.feedback({"id": row["id"], "scene": "casual", "reason": "not_suitable"})
        self.assertEqual(sticker_library.preview({"text": "摸鱼"})["mode"], "local_candidates")
        self.assertFalse(sticker_library.preview({"text": "我很难过", "scene": "support"})["candidates"])
        with self.assertRaises(ValueError):sticker_library.feedback({"id": "../private", "reason": "not_suitable"})

    def test_local_preview_requires_label_hits_and_never_fills_with_unmatched_images(self):
        rows = [
            {"id": "sad", "title": "哭泣", "tags": ["伤心"], "pack_id": "original"},
            {"id": "angry", "title": "生气", "tags": ["气鼓鼓"], "pack_id": "original"},
            {"id": "happy", "title": "开心", "tags": ["好消息"], "pack_id": "original"},
        ]
        sticker_library.store().set_favorite("sad", True)
        with patch.object(sticker_library, "_all", return_value={"settings": dict(stickers.DEFAULT_SETTINGS), "stickers": rows}):
            matched = sticker_library.preview({"text": "今天有好消息，好开心", "scene": "casual"})
            self.assertEqual([row["id"] for row in matched["candidates"]], ["happy"])
            unmatched = sticker_library.preview({"text": "今天晴空万里", "scene": "casual"})
            self.assertEqual(unmatched["candidates"], [])
            self.assertIn("没有文字标签命中", unmatched["message"])
            self.assertFalse(unmatched["model_called"])
            self.assertFalse(unmatched["message_sent"])
        # The runtime shortlist keeps its existing model-selection behavior.
        self.assertEqual(len(sticker_library._policy().lexical_rank(rows, "晴空万里")), 3)


def material_fixture():
    raw = picture()
    module = custom_stickers._module()
    clean, facts = module.sanitize_image(raw)
    sha = facts["sha256"]
    row = {"id": "new-material", "title": "新社区表情", "emotion": "happy", "tags": ["开心"],
           "description": "测试图", "use_when": "开心时", "avoid_when": "严肃时", "attribution": "测试作者",
           **facts, "file": f"images/{sha}.png", "license_file": f"licenses/{sha}.txt", "license_id": "CC-BY-NC-4.0",
           "source_pack": "contributed", "source_url": "https://github.com/myflww/aiwu-jiyu"}
    manifest = {"schema_version": 1, "version": "1.0.0", "source": "https://github.com/myflww/aiwu-jiyu", "stickers": [row]}
    encoded_manifest = json.dumps(manifest).encode()
    package = zipped([("manifest.json", encoded_manifest), (row["file"], clean), (row["license_file"], b"CC BY-NC 4.0 test attribution")])
    tag = "v1.4.0"
    assets = []
    mapping = {}
    for name, content in [("community-stickers-manifest.json", encoded_manifest), ("community-stickers-1.0.0.zip", package)]:
        url = f"https://github.com/myflww/aiwu-jiyu/releases/download/{tag}/{name}"
        assets.append({"name": name, "size": len(content), "digest": "sha256:" + hashlib.sha256(content).hexdigest(), "browser_download_url": url})
        mapping[url] = content
    mapping[sticker_materials.RELEASES] = json.dumps({"tag_name": tag, "draft": False, "prerelease": False, "assets": assets}).encode()
    return manifest, mapping, package


class MaterialTests(LibraryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        sticker_materials._pending = None
        sticker_materials._busy = False
        self.manifest, self.mapping, self.package = material_fixture()
        p = patch.object(sticker_materials, "_download", side_effect=lambda url, limit: self.mapping[url])
        p.start();self.addCleanup(p.stop)

    def test_official_release_preview_and_explicit_apply_switch_only_data(self):
        result = sticker_materials.check()
        self.assertTrue(result["available"])
        self.assertFalse(sticker_materials.current())
        self.assertIn("新社区表情", result["preview"][0]["title"])
        installed = sticker_materials.apply({"fingerprint": result["fingerprint"]})
        self.assertEqual(installed["version"], "1.0.0")
        row = sticker_materials.rows()[0]
        path, mime = sticker_materials.asset(row["id"])
        self.assertEqual(mime, "image/png")
        self.assertTrue(path.is_relative_to(custom_stickers.data_root().parent))
        self.assertTrue(any(row["id"] == "new-material" for row in stickers.status(custom_items=[])["stickers"]))
        with self.assertRaises(ValueError):sticker_materials.apply({"fingerprint": result["fingerprint"]})

    def test_bad_hash_extra_code_file_and_unpreviewed_fingerprint_leave_old_profile(self):
        pending = sticker_materials.check()
        with self.assertRaises(ValueError):sticker_materials.apply({"fingerprint": "0" * 64})
        zip_url = next(url for url in self.mapping if url.endswith(".zip"))
        self.mapping[zip_url] = b"tampered"
        with self.assertRaises(ValueError):sticker_materials.apply({"fingerprint": pending["fingerprint"]})
        self.assertFalse(sticker_materials.current())
        self.mapping[zip_url] = self.package
        with zipfile.ZipFile(io.BytesIO(self.package)) as archive:
            content = zipped([(name, archive.read(name)) for name in archive.namelist()] + [("main.py", b"never execute")])
        release = json.loads(self.mapping[sticker_materials.RELEASES])
        release["assets"][1].update(size=len(content), digest="sha256:" + hashlib.sha256(content).hexdigest())
        self.mapping[zip_url] = content
        self.mapping[sticker_materials.RELEASES] = json.dumps(release).encode()
        pending = sticker_materials.check()
        with self.assertRaises(ValueError):sticker_materials.apply({"fingerprint": pending["fingerprint"]})
        self.assertFalse(sticker_materials.current())
        self.assertFalse(list(sticker_materials.root().rglob("main.py")))

    def test_corrupt_active_image_can_be_repaired_atomically_without_losing_personal_images(self):
        personal = custom_stickers.import_item(upload(picture("JPEG")))
        preview = sticker_materials.check()
        sticker_materials.apply({"fingerprint": preview["fingerprint"]})
        path, _ = sticker_materials.asset("new-material")
        old_generation_folder = path.parent.parent
        path.write_bytes(b"damaged")
        preview = sticker_materials.check()
        self.assertTrue(preview["available"])
        self.assertTrue(preview["repair_needed"])
        sticker_materials.apply({"fingerprint": preview["fingerprint"]})
        repaired, _ = sticker_materials.asset("new-material")
        self.assertNotEqual(repaired.parent.parent, old_generation_folder)
        self.assertTrue(old_generation_folder.is_dir())
        self.assertEqual(custom_stickers.list_items()[0]["id"], personal["id"])

    def test_checksum_valid_archive_with_false_image_facts_or_missing_license_is_rejected(self):
        for failure in ("facts", "license"):
            manifest, mapping, package = material_fixture()
            if failure == "facts":manifest["stickers"][0]["width"] += 1
            manifest_bytes = json.dumps(manifest).encode()
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                entries = [(name, manifest_bytes if name == "manifest.json" else archive.read(name)) for name in archive.namelist()
                           if failure != "license" or not name.startswith("licenses/")]
            package = zipped(entries)
            release = json.loads(mapping[sticker_materials.RELEASES])
            release["assets"][0].update(size=len(manifest_bytes), digest="sha256:" + hashlib.sha256(manifest_bytes).hexdigest())
            release["assets"][1].update(size=len(package), digest="sha256:" + hashlib.sha256(package).hexdigest())
            self.mapping = {release["assets"][0]["browser_download_url"]: manifest_bytes,
                            release["assets"][1]["browser_download_url"]: package,
                            sticker_materials.RELEASES: json.dumps(release).encode()}
            preview = sticker_materials.check()
            with self.assertRaises(ValueError):sticker_materials.apply({"fingerprint": preview["fingerprint"]})
            self.assertFalse(sticker_materials.current())

    def test_same_shipped_contents_do_not_prompt_and_metadata_only_update_is_detected(self):
        with patch.object(stickers._catalog_module(), "load_catalog", return_value={"stickers": self.manifest["stickers"]}):
            self.assertFalse(sticker_materials.check()["available"])
        preview = sticker_materials.check()
        sticker_materials.apply({"fingerprint": preview["fingerprint"]})
        self.manifest["version"] = "1.0.1"
        self.manifest["stickers"][0]["description"] = "修正后的场景说明"
        raw = json.dumps(self.manifest).encode()
        release = json.loads(self.mapping[sticker_materials.RELEASES])
        old_url = release["assets"][1]["browser_download_url"]
        new_url = old_url.replace("1.0.0.zip", "1.0.1.zip")
        with zipfile.ZipFile(io.BytesIO(self.package)) as archive:
            data = zipped([(name, raw if name == "manifest.json" else archive.read(name)) for name in archive.namelist()])
        release["assets"][0].update(size=len(raw), digest="sha256:" + hashlib.sha256(raw).hexdigest())
        release["assets"][1].update(name="community-stickers-1.0.1.zip", size=len(data),
                                     digest="sha256:" + hashlib.sha256(data).hexdigest(), browser_download_url=new_url)
        self.mapping.update({release["assets"][0]["browser_download_url"]: raw, new_url: data,
                             sticker_materials.RELEASES: json.dumps(release).encode()})
        preview = sticker_materials.check()
        self.assertTrue(preview["available"])
        self.assertEqual(preview["changes"]["changed"], 1)
        self.assertEqual(sticker_materials.apply({"fingerprint": preview["fingerprint"]})["version"], "1.0.1")


class ProactiveStickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-proactive-stickers-")
        self.addCleanup(self.temp.cleanup)
        self.plugin = plugin_module.DafeiyuStickers(None, {"auto_probability": 100})
        self.plugin._custom_root = Path(self.temp.name) / "custom"
        self.plugin._library = plugin_module.library_state.LibraryState(self.plugin._custom_root.parent)
        self.plugin._proactive_origin = AsyncMock(return_value=True)
        self.origin = "qq:FriendMessage:123456789"

    async def test_context_only_offers_ids_and_successful_receipt_records_usage_once(self):
        note = await self.plugin.proactive_context(self.origin, "开心地打个招呼")
        self.assertIn("候选", note)
        result = await self.plugin.prepare_proactive(self.origin, "见到你很开心[大肥鱼表情:happy]")
        self.assertEqual(result["text"], "见到你很开心")
        self.assertIsInstance(result["component"], Image)
        self.assertIsNotNone(result["reservation"])
        await self.plugin.acknowledge_proactive(self.origin, result["reservation"], "sent")
        await self.plugin.acknowledge_proactive(self.origin, result["reservation"], "sent")
        self.assertEqual(self.plugin._library.annotate([{"id": "happy"}])[0]["use_count"], 1)
        self.assertEqual(await self.plugin.proactive_context(self.origin, "开心"), "")

    async def test_manual_zero_probability_task_and_forged_marker_keep_text_without_image(self):
        self.plugin.config.update(auto_probability=0)
        self.assertEqual(await self.plugin.proactive_context(self.origin, "开心"), "")
        self.plugin.config.update(auto_probability=100, scenario_policy="manual")
        self.assertEqual(await self.plugin.proactive_context(self.origin, "开心"), "")
        self.plugin.config.update(scenario_policy="balanced")
        self.assertEqual(await self.plugin.proactive_context(self.origin, "我很难过"), "")
        await self.plugin.proactive_context(self.origin, "开心")
        result = await self.plugin.prepare_proactive(self.origin, "hi[sticker:../../secret]")
        self.assertEqual(result["text"], "hi")
        self.assertIsNone(result["component"])

    async def test_cancel_releases_reservation_and_uncertain_delivery_retains_throttle_without_usage(self):
        await self.plugin.proactive_context(self.origin, "开心")
        result = await self.plugin.prepare_proactive(self.origin, "hello[sticker:happy]")
        await self.plugin.acknowledge_proactive(self.origin, result["reservation"], "not_sent")
        self.assertFalse(self.plugin._last_sent)
        await self.plugin.proactive_context(self.origin, "开心")
        result = await self.plugin.prepare_proactive(self.origin, "hello[sticker:happy]")
        await self.plugin.acknowledge_proactive(self.origin, result["reservation"], "failed_unknown")
        self.assertEqual(self.plugin._library.annotate([{"id": "happy"}])[0]["use_count"], 0)
        self.assertEqual(await self.plugin.proactive_context(self.origin, "开心"), "")

    async def test_disabled_private_asset_after_model_generation_never_becomes_component(self):
        with patch.object(custom_stickers, "ASTRBOT", Path(self.temp.name) / "runtime/astrbot"):
            self.plugin._custom_root = custom_stickers.data_root()
            row = custom_stickers.import_item(upload())
            note = await self.plugin.proactive_context(self.origin, "摸鱼放松")
            self.assertIn(row["id"], note)
            custom_stickers.update_item(row["id"], {"enabled": False})
            result = await self.plugin.prepare_proactive(self.origin, "摸鱼[sticker:" + row["id"] + "]")
            self.assertEqual(result["text"], "摸鱼")
            self.assertIsNone(result["component"])

    async def test_final_packet_rechecks_existing_reservation_and_live_plugin_preferences(self):
        for changes in ({"enabled": False}, {"auto_match_enabled": False}, {"auto_probability": 0}, {"scenario_policy": "manual"}):
            self.plugin.config = {"auto_probability": 100}
            await self.plugin.proactive_context(self.origin, "开心")
            result = await self.plugin.prepare_proactive(self.origin, "hello[sticker:happy]")
            self.assertIsInstance(await self.plugin.revalidate_proactive(self.origin, result["reservation"]), Image)
            self.assertIsNone(await self.plugin.revalidate_proactive(self.origin, object()))
            self.assertIsNone(await self.plugin.revalidate_proactive("qq:FriendMessage:987654321", result["reservation"]))
            self.plugin.config.update(changes)
            self.assertIsNone(await self.plugin.revalidate_proactive(self.origin, result["reservation"]))
            await self.plugin.acknowledge_proactive(self.origin, result["reservation"], "not_sent")
            self.assertFalse(self.plugin._proactive_assets)

    async def test_final_packet_rechecks_private_disable_deletion_and_content_hash(self):
        with patch.object(custom_stickers, "ASTRBOT", Path(self.temp.name) / "runtime/astrbot"):
            self.plugin._custom_root = custom_stickers.data_root()
            for action in ("disable", "delete", "pack_change", "tamper"):
                row = custom_stickers.import_item(upload())
                custom_stickers.update_item(row["id"], {"enabled": True})
                self.plugin.config = {"auto_probability": 100}
                await self.plugin.proactive_context(self.origin, "摸鱼放松")
                result = await self.plugin.prepare_proactive(self.origin, "休息吧[sticker:" + row["id"] + "]")
                self.assertIsNotNone(result["reservation"])
                if action == "disable":custom_stickers.update_item(row["id"], {"enabled": False})
                if action == "delete":custom_stickers.delete_item(row["id"])
                if action == "pack_change":self.plugin.config["pack_selection"] = "original"
                if action == "tamper":custom_stickers.asset(row["id"])[0].write_bytes(b"damaged")
                self.assertIsNone(await self.plugin.revalidate_proactive(self.origin, result["reservation"]))
                await self.plugin.acknowledge_proactive(self.origin, result["reservation"], "not_sent")
                self.assertFalse(self.plugin._proactive_assets)


if __name__ == "__main__":unittest.main()
