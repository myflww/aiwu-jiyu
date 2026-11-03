"""Bundled art integrity and isolated sends; never connects to QQ or user data."""
from __future__ import annotations

import asyncio
import copy
from enum import Enum
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_stickers"
sys.path.insert(0, str(ROOT / "app"))
from backend import stickers


class MessageType(Enum):
    FRIEND_MESSAGE = "FriendMessage"
    GROUP_MESSAGE = "GroupMessage"
    OTHER_MESSAGE = "OtherMessage"


def load_plugin():
    """Framework stubs are scoped to import, never installed into the process."""
    names = ["astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star",
             "astrbot.core", "astrbot.core.platform", "astrbot.core.platform.message_type",
             "astrbot.core.cron", "astrbot.core.cron.events", "astrbot.core.message", "astrbot.core.message.components"]
    modules = {name: ModuleType(name) for name in names}
    identity = lambda *args, **kwargs: lambda function: function
    class ToolSet:
        def __init__(self, tools=None): self.tools = list(tools or [])
        def add_tool(self, tool):
            self.tools = [item for item in self.tools if item.name != tool.name] + [tool]
    modules["astrbot.api"].ToolSet = ToolSet
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].filter = SimpleNamespace(llm_tool=identity, command=identity,
        on_llm_request=identity, on_llm_tool_respond=identity, on_decorating_result=identity)
    modules["astrbot.core.cron.events"].CronMessageEvent = type("CronMessageEvent", (), {})
    modules["astrbot.core.message.components"].Plain = Plain
    modules["astrbot.core.message.components"].Image = Image
    class Star:
        def __init__(self, context):
            self.context = context
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].register = identity
    modules["astrbot.core.platform.message_type"].MessageType = MessageType
    package = ModuleType("_aiwu_sticker_test_plugin")
    package.__path__ = [str(PLUGIN)]
    modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + ".main", PLUGIN / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
        catalog = sys.modules[package.__name__ + ".catalog"]
    return module, catalog


class Plain:
    def __init__(self, text): self.text = text


class Image:
    def __init__(self, path): self.path = path
    @classmethod
    def fromFileSystem(cls, path): return cls(path)


class Result:
    def __init__(self, chain=None, llm=True, kind=None):
        self.chain, self.llm, self.type = list(chain or []), llm, kind
    def is_llm_result(self): return self.llm


plugin_module, catalog_module = load_plugin()


class FakeEvent:
    def __init__(self, origin="qq-test:FriendMessage:example", message="发一张开心的大肥鱼表情",
                 platform="aiocqhttp", kind=MessageType.FRIEND_MESSAGE, failure=False):
        self.unified_msg_origin = origin
        self.message_obj = SimpleNamespace(message_id="example-message")
        self.message = message
        self.platform, self.kind = platform, kind
        self.extras, self.sent = {}, []
        self.failure = failure
        self.stopped = False
        self.result = None

    def get_platform_name(self):
        return self.platform

    def get_message_type(self):
        return self.kind

    def get_platform_id(self): return "qq-test"
    def get_sender_id(self): return "sender-example"
    def get_self_id(self): return "bot-example"
    def get_result(self): return self.result

    def get_message_str(self):
        return self.message

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def image_result(self, path):
        return {"kind": "image", "path": path}

    def plain_result(self, text):
        return {"kind": "plain", "text": text}

    def stop_event(self):
        self.stopped = True

    async def send(self, result):
        await asyncio.sleep(0)
        if self.failure:
            raise OSError("private path / secret must never reach the reply")
        self.sent.append(result)


class PackTests(unittest.TestCase):
    def test_six_assets_have_valid_hashes_dimensions_and_aliases(self):
        catalog = catalog_module.load_catalog(include_community=False)
        self.assertEqual({item["id"] for item in catalog["stickers"]}, set(catalog_module.IDS))
        for item in catalog["stickers"]:
            path = catalog_module.verify_asset(item)
            self.assertEqual(path.parent, PLUGIN / "pack")
            self.assertEqual(path.name, item["id"] + ".png")
        self.assertEqual(catalog_module.choose("晚安", catalog)["id"], "sleepy")
        self.assertEqual(catalog_module.choose("SHY", catalog)["id"], "shy")
        for invalid in ("../config.json", "happy.png", "https://example.com/picture.png", "C:\\private.png"):
            self.assertIsNone(catalog_module.choose(invalid, catalog))

    def test_tampered_image_and_arbitrary_manifest_path_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-sticker-pack-") as temporary:
            folder = Path(temporary) / "pack"
            shutil.copytree(PLUGIN / "pack", folder)
            catalog = catalog_module.load_catalog(folder)
            entry = catalog["stickers"][0]
            (folder / entry["file"]).write_bytes(b"not an image")
            with self.assertRaises(ValueError):
                catalog_module.verify_asset(entry, folder)
            entry["file"] = "../private.png"
            (folder / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaises(ValueError):
                catalog_module.load_catalog(folder)

    def test_symlink_pack_asset_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-sticker-link-") as temporary:
            folder = Path(temporary) / "pack"
            shutil.copytree(PLUGIN / "pack", folder)
            entry = catalog_module.load_catalog(folder)["stickers"][0]
            asset = folder / entry["file"]
            outside = Path(temporary) / "external.png"
            asset.rename(outside)
            try:
                asset.symlink_to(outside)
            except OSError:
                self.skipTest("This Windows profile lacks symlink permission")
            with self.assertRaises(ValueError):
                catalog_module.verify_asset(entry, folder)

    def test_desktop_inventory_is_public_pack_only_and_detached(self):
        with patch.object(stickers.config_store, "load", side_effect=AssertionError("private profile read")):
            status = stickers.status()
        self.assertEqual(status["count"], 30)
        self.assertEqual(status["plugin_id"], "astrbot_plugin_dafeiyu_stickers")
        for entry in status["stickers"]:
            self.assertEqual(entry["path"], entry["url"])
            prefix = "vendor/nekowhale/pack/" if entry["pack_id"] == "community" else "pack/"
            self.assertTrue(entry["url"].startswith("/assets/plugins/astrbot_plugin_dafeiyu_stickers/" + prefix))
            self.assertNotIn("C:", entry["url"])
        status["stickers"].clear()
        self.assertEqual(len(stickers.status()["stickers"]), 30)
        self.assertEqual(status["settings"], stickers.DEFAULT_SETTINGS)

    def test_community_catalog_has_verified_gifs_and_preserved_source_credits(self):
        catalog = catalog_module.load_catalog()
        community = [item for item in catalog["stickers"] if item["pack_id"] == "community"]
        self.assertEqual(len(community), 24)
        for item in community:
            path = catalog_module.verify_asset(item)
            self.assertEqual(path.suffix, ".gif")
            self.assertTrue(path.read_bytes().startswith((b"GIF87a", b"GIF89a")))
        group = catalog["packs"][1]
        self.assertEqual(group["attribution"]["website"], "https://kiriyuki.com")
        self.assertIn("artwork", group["license"])
        first = catalog_module.choose("happy", catalog, pack_selection="community")
        second = catalog_module.choose("happy", catalog, pack_selection="community", avoid_id=first["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(catalog_module.choose("sad", catalog, pack_selection="original")["id"], "sad")

    def test_community_rejects_arbitrary_file_path_and_dimensions(self):
        item = copy.deepcopy(next(item for item in catalog_module.load_catalog()["stickers"] if item["pack_id"] == "community"))
        for path in ("../private.gif", "https://example.com/a.gif", "vendor/nekowhale/pack/x.png"):
            invalid = {**item, "file": path}
            with self.assertRaises(ValueError): catalog_module.verify_asset(invalid)
        with self.assertRaises(ValueError): catalog_module.verify_asset({**item, "width": 10000})

    def test_settings_sync_preserves_plugin_enable_and_unrelated_app_preferences(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-sticker-settings-") as temporary:
            root = Path(temporary)
            config = {"unrelated": {"keep": True}, "stickers": {"auto_match_enabled": True}}
            target = root / "data/config/astrbot_plugin_dafeiyu_stickers_config.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"enabled": False, "other": "keep"}), encoding="utf-8")
            with patch.object(stickers, "ASTRBOT", root), patch.object(stickers.config_store, "load", return_value=config), \
                 patch.object(stickers.config_store, "save") as save:
                result = stickers.configure({"auto_match_enabled": False, "cooldown_seconds": 45, "pack_selection": "community"})
                written = json.loads(target.read_text("utf-8"))
                self.assertFalse(written["enabled"] or written["auto_match_enabled"])
                self.assertEqual(written["other"], "keep")
                self.assertEqual(result["settings"]["pack_selection"], "community")
                self.assertTrue(save.call_args.args[0]["unrelated"]["keep"])
                for body in ({"auto_match_enabled": "true"}, {"cooldown_seconds": True}, {"cooldown_seconds": 9},
                             {"pack_selection": "https://example.com"}, {"pack_selection": []}, {"recipient": "other"}):
                    with self.assertRaises(ValueError): stickers.configure(body)


class StickerSendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = plugin_module.DafeiyuStickers(None)

    async def test_requested_sticker_is_sent_to_received_event_only(self):
        event = FakeEvent()
        result = await self.plugin.send_dafeiyu_sticker(event, "开心")
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(Path(event.sent[0]["path"]), PLUGIN / "pack/happy.png")
        self.assertIn("已向当前会话发送", result)
        self.assertNotIn(str(PLUGIN), result)

    async def test_unrequested_or_synthetic_events_never_send(self):
        for event in (FakeEvent(message="普通聊天"), FakeEvent(platform="cron"),
                      FakeEvent(platform="webchat"), FakeEvent(kind=MessageType.OTHER_MESSAGE)):
            await self.plugin.send_dafeiyu_sticker(event, "love")
            self.assertEqual(event.sent, [])
        event = FakeEvent()
        event.message_obj.message_id = ""
        await self.plugin.send_dafeiyu_sticker(event, "happy")
        self.assertEqual(event.sent, [])

    async def test_disabled_and_arbitrary_path_requests_never_send(self):
        event = FakeEvent()
        self.plugin.config = {"enabled": False}
        await self.plugin.send_dafeiyu_sticker(event, "happy")
        self.assertEqual(event.sent, [])
        self.plugin.config = {}
        for emotion in ("../../config.json", "https://example.com/a.png", "C:\\private.png"):
            await self.plugin.send_dafeiyu_sticker(event, emotion)
        self.assertEqual(event.sent, [])

    async def test_one_per_event_and_cooldown_are_separate_and_per_conversation(self):
        event = FakeEvent()
        with patch.object(self.plugin, "_now", return_value=100):
            await self.plugin.send_dafeiyu_sticker(event, "happy")
        with patch.object(self.plugin, "_now", return_value=140):
            await self.plugin.send_dafeiyu_sticker(event, "love")
        self.assertEqual(len(event.sent), 1)
        same = FakeEvent()
        with patch.object(self.plugin, "_now", return_value=110):
            result = await self.plugin.send_dafeiyu_sticker(same, "sad")
        self.assertIn("避免刷屏", result)
        self.assertEqual(same.sent, [])
        another = FakeEvent(origin="qq-test:GroupMessage:another", kind=MessageType.GROUP_MESSAGE)
        with patch.object(self.plugin, "_now", return_value=110):
            await self.plugin.send_dafeiyu_sticker(another, "love")
        self.assertEqual(len(another.sent), 1)

    async def test_parallel_calls_send_only_one_image(self):
        event = FakeEvent()
        await asyncio.gather(self.plugin.send_dafeiyu_sticker(event, "happy"),
                             self.plugin.send_dafeiyu_sticker(event, "sad"))
        self.assertEqual(len(event.sent), 1)

    async def test_uncertain_send_retains_attempt_without_leaking_exception(self):
        event = FakeEvent(failure=True)
        result = await self.plugin.send_dafeiyu_sticker(event, "happy")
        self.assertNotIn("secret", result)
        self.assertTrue(event.get_extra(plugin_module.SENT_KEY))
        self.assertIn("不要立即重试", result)
        event.failure = False
        result = await self.plugin.send_dafeiyu_sticker(event, "happy")
        self.assertEqual(len(event.sent), 0)
        self.assertIn("请勿重复发送", result)

    async def test_delivered_then_timeout_does_not_retry_or_append_auto_image(self):
        class UncertainEvent(FakeEvent):
            async def send(self, result):
                self.sent.append(result)
                raise TimeoutError("QQ may have accepted the image")
        event = UncertainEvent()
        with patch.object(self.plugin, "_now", return_value=100):
            first = await self.plugin.send_dafeiyu_sticker(event, "happy")
            await self.plugin.send_dafeiyu_sticker(event, "happy")
            another = FakeEvent()
            await self.plugin.send_dafeiyu_sticker(another, "happy")
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(another.sent, [])
        self.assertTrue(event.get_extra(plugin_module.SENT_KEY) and event.get_extra(plugin_module.UNCERTAIN_KEY))
        self.assertIn("没能确认", first)
        event.result = Result([Plain("[sticker:happy]")])
        await self.plugin.attach_matching_sticker(event)
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))
        self.assertIn("还没确认", event.result.chain[0].text)

    async def test_command_lists_or_sends_without_altering_other_messages(self):
        help_event = FakeEvent(message="/肥鱼表情")
        outputs = [item async for item in self.plugin.sticker_command(help_event)]
        self.assertEqual(help_event.sent, [])
        self.assertIn("开心、委屈、生气、害羞、晚安、喜欢", outputs[0]["text"])
        self.assertTrue(help_event.stopped)
        event = FakeEvent(message="/肥鱼表情 害羞")
        outputs = [item async for item in self.plugin.sticker_command(event, "害羞")]
        self.assertEqual(outputs, [])
        self.assertEqual(Path(event.sent[0]["path"]).name, "shy.png")
        self.assertTrue(event.stopped)


class AutoStickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.plugin = plugin_module.DafeiyuStickers(None)

    async def test_normal_chat_uses_bounded_prompt_then_appends_image_without_extra_send(self):
        event = FakeEvent(message="今天终于完成任务啦")
        request = SimpleNamespace(system_prompt="人格和记忆保持原样")
        await self.plugin.match_before_request(event, request)
        self.assertIn("无需对方明确索要", request.system_prompt)
        self.assertTrue(request.system_prompt.startswith("人格和记忆保持原样"))
        first = request.system_prompt
        await self.plugin.match_before_request(event, request)
        self.assertEqual(request.system_prompt, first)
        event.result = Result([Plain("太好啦！[大肥鱼表情:happy]")])
        await self.plugin.attach_matching_sticker(event)
        self.assertEqual(event.sent, [])
        self.assertEqual(sum(isinstance(part, Image) for part in event.result.chain), 1)
        self.assertEqual(event.result.chain[0].text, "太好啦！")

    async def test_auto_keeps_code_links_and_native_face_segments_unchanged(self):
        event = FakeEvent(message="好耶")
        protected = "```python\na='[sticker:happy]'\n``` `x[sticker:love]` [sticker:sad](https://example.com) https://example.com/[sticker:angry] "
        face = SimpleNamespace(kind="Face", id=277)
        event.result = Result([Plain(protected), face, Plain("一起庆祝[大肥鱼表情:喜欢]")])
        await self.plugin.attach_matching_sticker(event)
        self.assertEqual(event.result.chain[0].text, protected)
        self.assertIs(event.result.chain[1], face)
        self.assertEqual(event.result.chain[2].text, "一起庆祝")
        self.assertIsInstance(event.result.chain[-1], Image)

    async def test_maximum_one_image_multiple_markers_cooldown_and_conversation_isolation(self):
        event = FakeEvent(message="好消息")
        event.result = Result([Plain("开心[sticker:happy][sticker:love]")])
        with patch.object(self.plugin, "_now", return_value=100): await self.plugin.attach_matching_sticker(event)
        self.assertEqual(sum(isinstance(part, Image) for part in event.result.chain), 1)
        again = FakeEvent(message="然后呢")
        again.result = Result([Plain("继续[sticker:happy]")])
        with patch.object(self.plugin, "_now", return_value=110): await self.plugin.attach_matching_sticker(again)
        self.assertEqual([part.text for part in again.result.chain if isinstance(part, Plain)], ["继续"])
        self.assertFalse(any(isinstance(part, Image) for part in again.result.chain))
        other = FakeEvent(origin="qq-test:GroupMessage:other", kind=MessageType.GROUP_MESSAGE)
        other.result = Result([Plain("也开心[sticker:happy]")])
        with patch.object(self.plugin, "_now", return_value=110): await self.plugin.attach_matching_sticker(other)
        self.assertTrue(any(isinstance(part, Image) for part in other.result.chain))

    async def test_tool_round_and_explicit_tool_do_not_produce_an_additional_image(self):
        explicit = FakeEvent()
        await self.plugin.send_dafeiyu_sticker(explicit, "happy")
        explicit.result = Result([Plain("送给你[sticker:love]")])
        await self.plugin.attach_matching_sticker(explicit)
        self.assertEqual(len(explicit.sent), 1)
        self.assertFalse(any(isinstance(part, Image) for part in explicit.result.chain))
        event = FakeEvent(origin="qq-test:FriendMessage:other")
        event.result = Result([Plain("任务完成[sticker:happy]")])
        await self.plugin.after_tool(event, None, None, None)
        await self.plugin.attach_matching_sticker(event)
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))

    async def test_disabled_nonqq_synthetic_reasoning_and_nonllm_never_add_images(self):
        events = [FakeEvent(platform="webchat"), FakeEvent(platform="cron"), FakeEvent(), FakeEvent()]
        events[2].result = Result([Plain("tool[sticker:happy]")], llm=False)
        events[3].result = Result([Plain("<thought>[sticker:happy]</thought>")])
        for event in events:
            if event.result is None: event.result = Result([Plain("hello[sticker:happy]")])
            await self.plugin.attach_matching_sticker(event)
            self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))
            self.assertEqual(event.sent, [])
        self.plugin.config = {"auto_match_enabled": False}
        event = FakeEvent(message="普通聊天")
        event.result = Result([Plain("普通回复[sticker:happy]")])
        await self.plugin.attach_matching_sticker(event)
        self.assertEqual(event.result.chain[0].text, "普通回复")
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))

    async def test_marker_only_normal_reply_becomes_one_image_and_never_disappears_on_failure(self):
        event = FakeEvent(message="好开心")
        event.result = Result([Plain("[大肥鱼表情:happy]")])
        with patch.object(self.plugin, "_now", return_value=100):
            await self.plugin.attach_matching_sticker(event)
        self.assertEqual(len(event.result.chain), 1)
        self.assertIsInstance(event.result.chain[0], Image)
        self.assertEqual(event.sent, [])
        cooldown = FakeEvent(message="嗯嗯")
        cooldown.result = Result([Plain("[sticker:happy]")])
        with patch.object(self.plugin, "_now", return_value=110):
            await self.plugin.attach_matching_sticker(cooldown)
        self.assertTrue(any(isinstance(part, Plain) and part.text.strip() for part in cooldown.result.chain))
        self.assertFalse(any(isinstance(part, Image) for part in cooldown.result.chain))
        unknown = FakeEvent(origin="qq-test:FriendMessage:new")
        unknown.result = Result([Plain("[sticker:unknown-local-item]")])
        await self.plugin.attach_matching_sticker(unknown)
        self.assertTrue(any(isinstance(part, Plain) and part.text.strip() for part in unknown.result.chain))
        self.assertNotIn("[sticker:", unknown.result.chain[0].text)
        broken = FakeEvent(origin="qq-test:FriendMessage:broken")
        broken.result = Result([Plain("[sticker:happy]")])
        with patch.object(plugin_module, "verify_asset", side_effect=ValueError("private path/secret")):
            await self.plugin.attach_matching_sticker(broken)
        self.assertTrue(any(isinstance(part, Plain) and part.text.strip() for part in broken.result.chain))
        self.assertNotIn("secret", broken.result.chain[0].text)

    async def test_explicit_tool_stays_available_with_auto_off_and_empty_persona_without_global_changes(self):
        self.plugin.config = {"auto_match_enabled": False}
        tool = SimpleNamespace(name="send_dafeiyu_sticker", active=True,
                               handler_module_path=self.plugin.__class__.__module__)
        self.plugin.context = SimpleNamespace(get_llm_tool_manager=lambda: SimpleNamespace(func_list=[tool]))
        request = SimpleNamespace(system_prompt="保留人格", func_tool=SimpleNamespace(tools=[]))
        original = request.func_tool
        await self.plugin.match_before_request(FakeEvent(), request)
        self.assertIsNot(request.func_tool, original)
        self.assertEqual(original.tools, [])
        self.assertEqual(request.func_tool.tools, [tool])
        self.assertEqual(request.system_prompt, "保留人格")
        declined = FakeEvent(message="不要发表情包")
        result = await self.plugin.send_dafeiyu_sticker(declined, "happy")
        self.assertEqual(declined.sent, [])
        self.assertIn("明确要看", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
