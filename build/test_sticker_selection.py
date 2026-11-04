"""Automatic expressive replies: isolated images and fake adapter receipts only."""
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from .test_stickers import plugin_module, FakeEvent, Result, Plain, Image


class AutomaticSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-selection-")
        self.addCleanup(self.temp.cleanup)
        self.plugin = plugin_module.DafeiyuStickers(None, {"auto_probability": 100})
        self.plugin._custom_root = Path(self.temp.name) / "custom"
        self.plugin._library = plugin_module.library_state.LibraryState(Path(self.temp.name))

    async def prepare(self, message, reply):
        event = FakeEvent(message=message)
        req = SimpleNamespace(system_prompt="existing persona", func_tool=None)
        await self.plugin.match_before_request(event, req)
        event.result = Result([Plain(reply)])
        await self.plugin.attach_matching_sticker(event)
        return event, req

    async def test_ordinary_greeting_and_short_affection_attach_real_images_without_tool_or_extra_send(self):
        for message, reply in [("你好，小鱼", "你好呀，见到你真开心！"), ("好喜欢你，抱抱", "我也喜欢你，抱抱～")]:
            self.setUp()
            with patch.object(plugin_module.random, "random", return_value=0) as probability:
                event, req = await self.prepare(message, reply)
                self.assertIsNone(req.func_tool)
                self.assertIn("[大肥鱼表情:none]", req.system_prompt)
                self.assertEqual(event.sent, [])
                self.assertEqual(sum(isinstance(part, Image) for part in event.result.chain), 1)
                await event.send(event.result)
                await self.plugin.remember_sent_offer(event)
                probability.assert_called_once()
            self.assertEqual(event.get_extra(plugin_module.IMAGE_RECEIPT_KEY), "sent")
            self.assertEqual(sum(row["use_count"] for row in self.plugin._library.annotate(self.plugin.catalog["stickers"])), 1)

    async def test_explicit_none_always_prevents_fallback_and_is_hidden(self):
        event, _ = await self.prepare("今天好开心", "好耶[大肥鱼表情:none][sticker:happy]")
        self.assertEqual([part.text for part in event.result.chain if isinstance(part, Plain)], ["好耶"])
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))
        self.assertEqual(self.plugin._library.diagnostics()["last_reason"], "model_declined")

    async def test_response_capture_cleans_sdk_visible_history_without_losing_model_decision(self):
        event = FakeEvent(message="今天好开心")
        await self.plugin.match_before_request(event, SimpleNamespace(system_prompt="persona"))
        response = SimpleNamespace(completion_text="好耶！[sticker:happy]", role="assistant", is_chunk=False)
        await self.plugin.capture_model_decision(event, response)
        self.assertEqual(response.completion_text, "好耶！")
        event.result = Result([Plain(response.completion_text)])
        await self.plugin.attach_matching_sticker(event)
        self.assertTrue(any(isinstance(part, Image) for part in event.result.chain))
        self.assertIn("selected_model", self.plugin._library.diagnostics()["counts"])
        only = SimpleNamespace(completion_text="[sticker:none]", role="assistant", is_chunk=False)
        other = FakeEvent(message="今天好开心")
        await self.plugin.capture_model_decision(other, only)
        self.assertEqual(only.completion_text, "[sticker:none]")
        other.result = Result([Plain(only.completion_text)])
        await self.plugin.attach_matching_sticker(other)
        self.assertFalse(any(isinstance(part, Image) for part in other.result.chain))

    async def test_real_image_replaces_standalone_fish_tail_without_rewriting_semantic_fish(self):
        event, _ = await self.prepare("今天好开心", "好耶！ 🐟")
        self.assertTrue(any(isinstance(part, Image) for part in event.result.chain))
        self.assertEqual([part.text for part in event.result.chain if isinstance(part, Plain)], ["好耶！"])
        self.assertEqual(plugin_module.selection_policy.trim_mascot_tail("我想吃🐟"), "我想吃🐟")

    async def test_fish_emoji_neutral_or_low_mood_never_forces_image(self):
        for message, reply in [("在吗", "我在呀 🐟"), ("我今天很难过", "先抱抱你，慢慢说"),
                               ("我不开心，别庆祝", "我在这里"), ("请认真解释这个报错", "我认真看看"),
                               ("他说喜欢你，是什么意思", "喜欢有很多种含义"), ("分析‘好开心’的文案", "好开心表达喜悦")]:
            event, _ = await self.prepare(message, reply)
            self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))

    async def test_probability_zero_manual_and_cooldown_preserve_opt_out(self):
        for config in ({"auto_probability": 0}, {"scenario_policy": "manual"}, {"auto_match_enabled": False}):
            self.plugin.config = config
            event, _ = await self.prepare("你好，小鱼", "你好呀！")
            self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))
        self.plugin.config = {"auto_probability": 100}
        with patch.object(self.plugin, "_now", return_value=100):
            first, _ = await self.prepare("你好，小鱼", "你好呀！")
            await first.send(first.result)
        with patch.object(self.plugin, "_now", return_value=110):
            second, _ = await self.prepare("你好，小鱼", "你好呀！")
        self.assertFalse(any(isinstance(part, Image) for part in second.result.chain))

    async def test_rejected_scene_is_not_undone_by_favorite_or_common_emotion_words(self):
        for row in self.plugin.catalog["stickers"]:
            if row["emotion"] == "happy":
                self.plugin._library.reject(row["id"], scene="casual")
                self.plugin._library.set_favorite(row["id"], True)
        event, _ = await self.prepare("今天好开心", "好耶，太棒啦！")
        self.assertFalse(any(isinstance(part, Image) for part in event.result.chain))

    async def test_fallback_does_not_treat_emotion_or_description_alone_as_tag_match(self):
        row = {"id": "outfit", "title": "白衬衫", "emotion": "happy", "tags": ["职场"], "description": "一张开心的图片"}
        item, reason = plugin_module.selection_policy.decide([row], [], "今天好开心", "好耶，太棒啦！")
        self.assertIsNone(item)
        self.assertEqual(reason, "marker_missing_no_match")

    async def test_success_avoids_previous_asset_and_receipts_are_idempotent(self):
        with patch.object(self.plugin, "_now", return_value=100):
            first, _ = await self.prepare("今天好开心", "好耶，太棒啦！")
            await first.send(first.result)
            previous = self.plugin._previous_asset[first.unified_msg_origin]
            await self.plugin.remember_sent_offer(first)
            await self.plugin.remember_sent_offer(first)
        with patch.object(self.plugin, "_now", return_value=131):
            second, _ = await self.prepare("今天好开心", "好耶，太棒啦！")
            await second.send(second.result)
        self.assertNotEqual(self.plugin._previous_asset[second.unified_msg_origin], previous)
        self.assertEqual(sum(row["use_count"] for row in self.plugin._library.annotate(self.plugin.catalog["stickers"])), 2)

    async def test_final_packet_hot_disable_drops_image_without_false_usage(self):
        event, _ = await self.prepare("你好，小鱼", "你好呀！")
        self.plugin.config["auto_match_enabled"] = False
        await event.send(event.result)
        await self.plugin.remember_sent_offer(event)
        self.assertFalse(any(isinstance(part, Image) for part in event.sent[0].chain))
        self.assertFalse(self.plugin._last_sent)
        self.assertFalse(self.plugin._previous_asset)
        self.assertEqual(event.get_extra(plugin_module.IMAGE_RECEIPT_KEY), "not_sent")

    async def test_text_accepted_but_new_incoming_cancels_trailing_image_releases_slot(self):
        event, _ = await self.prepare("你好，小鱼", "你好呀！")
        event.set_extra(plugin_module.RECEIPT_KEY, "sent")
        event.set_extra("dafeiyu_bubbles_status", "interrupted")
        await self.plugin.remember_sent_offer(event)
        self.assertFalse(self.plugin._last_sent)
        self.assertEqual(sum(row["use_count"] for row in self.plugin._library.annotate(self.plugin.catalog["stickers"])), 0)

    async def test_unknown_image_delivery_keeps_cooldown_without_usage(self):
        event, _ = await self.prepare("你好，小鱼", "你好呀！")
        event.failure = True
        with self.assertRaises(OSError):
            await event.send(event.result)
        await self.plugin.remember_sent_offer(event)
        self.assertEqual(event.get_extra(plugin_module.IMAGE_RECEIPT_KEY), "failed_unknown")
        self.assertTrue(self.plugin._last_sent)
        self.assertEqual(sum(row["use_count"] for row in self.plugin._library.annotate(self.plugin.catalog["stickers"])), 0)
        diagnostic = self.plugin._library.diagnostics()
        self.assertEqual(diagnostic["last_reason"], "delivery_unknown")
        self.assertNotIn("sender-example", json.dumps(diagnostic))
        self.assertNotIn("你好", json.dumps(diagnostic))

    async def test_desktop_status_reads_private_reason_counts_only_for_installed_profile(self):
        from backend import stickers
        runtime = Path(self.temp.name) / "runtime/astrbot"
        state = plugin_module.library_state.LibraryState(runtime / "data/plugin_data" / stickers.PLUGIN_ID)
        state.record_decision("probability_skipped")
        with patch.object(stickers, "ASTRBOT", runtime):
            public = stickers.status()
            installed = stickers.status(custom_items=[])
        self.assertNotIn("runtime_diagnostics", public)
        self.assertEqual(installed["runtime_diagnostics"]["counts"], {"probability_skipped": 1})
        self.assertNotIn(str(runtime), json.dumps(installed["runtime_diagnostics"]))

    async def test_proactive_missing_marker_can_choose_clear_greeting_but_none_cannot(self):
        from unittest.mock import AsyncMock
        self.plugin._proactive_origin = AsyncMock(return_value=True)
        origin = "qq-test:FriendMessage:123456789"
        await self.plugin.proactive_context(origin, "轻松问候")
        prepared = await self.plugin.prepare_proactive(origin, "你好呀，见到你真开心！")
        self.assertIsInstance(prepared["component"], Image)
        await self.plugin.acknowledge_proactive(origin, prepared["reservation"], "not_sent")
        await self.plugin.proactive_context(origin, "轻松问候")
        declined = await self.plugin.prepare_proactive(origin, "你好呀[sticker:none]")
        self.assertIsNone(declined["component"])
