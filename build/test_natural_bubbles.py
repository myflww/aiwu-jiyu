"""Natural QQ bubbles using synthetic events only: no adapter/model/network."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion/natural_bubbles.py"
spec = importlib.util.spec_from_file_location("_natural_bubbles_test", SOURCE)
bubbles = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bubbles
spec.loader.exec_module(bubbles)


class Plain:
    def __init__(self, text): self.text = text


class At: pass
class Reply: pass
class Face: pass
class Image: pass
class Record: pass


class Chain:
    def __init__(self, chain, *, kind=None):
        self.chain = chain
        self.type = kind
        self.use_t2i_ = None
        self.use_markdown_ = None
        self.async_stream = None
    def derive(self, chain):
        result = Chain(chain, kind=self.type)
        result.use_t2i_ = self.use_t2i_
        result.use_markdown_ = self.use_markdown_
        return result
    def is_llm_result(self): return True


TEXT = ("今天的小尾巴已经准备好陪你聊天啦，我们可以先慢慢把想做的事情整理清楚。"
        "不用一下子完成全部，先挑一件最想做的小事，认真开始就已经很棒啦。"
        "等这件事情做好以后，我们再一起吃口米饭休息一下，你有什么想法都可以告诉我。")


class Event:
    def __init__(self, message_id="turn-1", result=None, sender="123456789", origin=None):
        self.message_obj = SimpleNamespace(message_id=message_id)
        self.unified_msg_origin = origin or f"qq-test:FriendMessage:{sender}"
        self.sender = sender
        self.result = result or Chain([Plain(TEXT)])
        self.extra = {}
        self.sent = []
    def get_sender_id(self): return self.sender
    def get_self_id(self): return "987654321"
    def get_platform_id(self): return "qq-test"
    def get_platform_name(self): return "aiocqhttp"
    def get_message_type(self): return "FriendMessage"
    def get_result(self): return self.result
    def set_extra(self, key, value): self.extra[key] = value
    def get_extra(self, key, default=None): return self.extra.get(key, default)
    async def send(self, message): self.sent.append(message)


CONFIG = {"platform_settings": {"segmented_reply": {"enable": False}, "forward_threshold": 1500},
          "provider_settings": {"streaming_response": False, "display_reasoning_text": False},
          "provider_tts_settings": {"enable": False}, "t2i": False}
OPTIONS = {"natural_bubbles_enabled": True, "natural_bubbles_frequency": "often"}


class AlwaysBubbles(bubbles.BubbleReplies):
    _selected = staticmethod(lambda *args: True)
    async def _pause(self, signal, seconds): pass


class TextTests(unittest.TestCase):
    def test_sentence_grouping_has_two_to_four_and_preserves_all_text(self):
        for text, count in ((TEXT, 2), (TEXT * 6, 3), (TEXT * 10, 4)):
            parts = bubbles.split_text(text)
            self.assertEqual(len(parts), count)
            self.assertEqual("".join(parts), text)
            self.assertTrue(all(len(part) >= 30 for part in parts))

    def test_short_unbroken_and_oversized_replies_stay_whole(self):
        for text in ("好呀，先吃一口米饭。", "好" * 120, TEXT * 20):
            self.assertEqual(bubbles.split_text(text), [text])

    def test_code_links_markdown_and_tool_tokens_are_not_cut(self):
        for protected in ("```python\nprint('hello')\n```", "`foo.bar`", "https://example.com?a=1.2",
                          "www.example.com", "[资料](https://example.com)", "![图片](local.png)",
                          "\n1. 执行操作", "\n- 检查结果", "\n| 名称 | 状态 |", "\n## 说明",
                          "[CQ:face,id=123]", "<think>analysis</think>", "[大肥鱼表情:happy]"):
            text = TEXT + protected
            self.assertEqual(bubbles.split_text(text), [text], protected)

    def test_probability_is_stable_and_really_occasional(self):
        choose = bubbles.BubbleReplies._selected
        for frequency, low, high in (("occasional", 240, 360), ("often", 640, 760)):
            outcomes = [choose(("qq-test:FriendMessage:123456789", str(index)), TEXT, frequency)
                        for index in range(1000)]
            self.assertGreater(sum(outcomes), low)
            self.assertLess(sum(outcomes), high)
            self.assertEqual(outcomes, [choose(("qq-test:FriendMessage:123456789", str(index)), TEXT, frequency)
                                        for index in range(1000)])
        for index in range(30):
            self.assertTrue(0.8 <= bubbles.BubbleReplies._delay(("test", "1"), index) <= 1.8)

    def test_headers_and_one_trailing_sticker_stay_with_text(self):
        at, quote, image = At(), Reply(), Image()
        original = Chain([quote, at, Plain(TEXT), image])
        plan, text = bubbles._plan(original)
        self.assertEqual(text, TEXT)
        self.assertEqual(plan[0].chain[:2], [quote, at])
        self.assertIs(plan[-1].chain[-1], image)
        self.assertTrue(all(any(isinstance(part, Plain) for part in item.chain) for item in plan))
        self.assertEqual(original.chain, [quote, at, original.chain[2], image])
        self.assertEqual(original.chain[2].text, TEXT)

    def test_face_inline_image_record_and_two_images_stay_whole(self):
        for chain in ([Plain(TEXT), Face()], [Image(), Plain(TEXT)], [Plain(TEXT), Image(), Plain("end")],
                      [Plain(TEXT), Image(), Image()], [Plain(TEXT), Record()]):
            self.assertEqual(bubbles._plan(Chain(chain))[0], [])


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, service, event, guard=None, config=CONFIG):
        return await service.prepare(event, OPTIONS, guard or AsyncMock(return_value=True), config)

    async def test_decorator_does_not_send_and_host_receives_each_bubble_once(self):
        service, event = AlwaysBubbles(), Event()
        guard = AsyncMock(return_value=True)
        self.assertTrue(await self.prepare(service, event, guard))
        self.assertEqual(event.sent, [])
        self.assertTrue(await self.prepare(service, event, guard))
        await event.send(event.result)
        self.assertEqual(len(event.sent), 2)
        self.assertEqual(guard.await_count, 2)
        self.assertEqual("".join(part.text for item in event.sent for part in item.chain if isinstance(part, Plain)), TEXT)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "sent")
        self.assertEqual(event.get_extra(bubbles.COUNT_KEY), 2)
        self.assertEqual(event.get_extra(bubbles.TEXT_KEY), TEXT)
        await event.send(Chain([Plain(TEXT * 3)]))
        self.assertEqual(len(event.sent), 2)  # Different model retry cannot resend.

    async def test_technical_reply_or_native_features_keep_original_delivery(self):
        cases = [({}, Chain([Plain(TEXT)])),
                 ({**CONFIG, "platform_settings": {"segmented_reply": {"enable": True}}}, Chain([Plain(TEXT)])),
                 ({**CONFIG, "provider_settings": {"streaming_response": True}}, Chain([Plain(TEXT)])),
                 ({**CONFIG, "provider_settings": {"display_reasoning_text": True}}, Chain([Plain(TEXT)])),
                 ({**CONFIG, "provider_tts_settings": {"enable": True}}, Chain([Plain(TEXT)])),
                 ({**CONFIG, "t2i": True}, Chain([Plain(TEXT)])),
                 (CONFIG, Chain([Plain(TEXT + " https://example.com")]))]
        for config, result in cases:
            event, service = Event(result=result), AlwaysBubbles()
            await self.prepare(service, event, config=config)
            await event.send(result)
            self.assertEqual(event.sent, [result])
            self.assertIsNone(event.get_extra(bubbles.STATUS_KEY))

    async def test_tool_round_and_non_llm_result_never_intercepted(self):
        for mode in ("tool", "sticker-tool", "non-llm", "system"):
            event, service = Event(), AlwaysBubbles()
            if mode == "tool": service.mark_tool(event)
            elif mode == "sticker-tool": event.set_extra("dafeiyu_sticker_tool_round", True)
            elif mode == "non-llm": event.result.is_llm_result = lambda: False
            else: event.result.type = "system"
            self.assertFalse(await self.prepare(service, event))
            await event.send(event.result)
            self.assertEqual(event.sent, [event.result])

    async def test_guard_rechecked_before_every_segment_and_disable_stops_tail(self):
        event, service = Event(), AlwaysBubbles()
        guard = AsyncMock(side_effect=[True, False])
        await self.prepare(service, event, guard)
        await event.send(event.result)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "cancelled")
        await event.send(event.result)
        self.assertEqual(len(event.sent), 1)

    async def test_new_message_interrupts_wait_and_only_current_reply_continues(self):
        event, service = Event(), bubbles.BubbleReplies()
        service._selected = lambda *args: True
        await self.prepare(service, event)
        pending = asyncio.create_task(event.send(event.result))
        while not event.sent:
            await asyncio.sleep(0)
        new = Event(message_id="turn-2")
        service.observe(new)
        await asyncio.wait_for(pending, 0.3)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "cancelled")
        duplicate_old = Event(message_id="turn-1")
        service.observe(duplicate_old)
        self.assertEqual(service._sessions[new.unified_msg_origin].message_id, "turn-2")

    async def test_close_wakes_pending_delay_and_retains_native_cleanup_contract(self):
        event, service = Event(), bubbles.BubbleReplies()
        service._selected = lambda *args: True
        await self.prepare(service, event)
        completed = []
        async def native_stage():
            await event.send(event.result)
            completed.append("after_message_sent")
            event.result = None
        pending = asyncio.create_task(native_stage())
        while not event.sent:
            await asyncio.sleep(0)
        service.close()
        await asyncio.wait_for(pending, 0.3)
        self.assertEqual(completed, ["after_message_sent"])
        self.assertIsNone(event.result)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "cancelled")

    async def test_ambiguous_failure_is_not_retried_or_followed_by_more_segments(self):
        event, service = Event(result=Chain([Plain(TEXT * 6)])), AlwaysBubbles()
        attempts = []
        async def uncertain_send(message):
            attempts.append(message)
            if len(attempts) == 2:
                raise OSError("synthetic transport timeout")
        event.send = uncertain_send
        await self.prepare(service, event)
        with self.assertRaisesRegex(RuntimeError, "retry suppressed"):
            await event.send(event.result)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "failed")
        self.assertEqual(event.get_extra(bubbles.COUNT_KEY), 1)
        await event.send(Chain([Plain(TEXT)]))
        self.assertEqual(len(attempts), 2)

    async def test_duplicate_events_and_concurrent_reentry_cannot_repeat_chunks(self):
        event, duplicate, service = Event(), Event(), bubbles.BubbleReplies()
        service._selected = lambda *args: True
        paused, release = asyncio.Event(), asyncio.Event()
        async def pause(*args):
            paused.set()
            await release.wait()
        service._pause = pause
        await self.prepare(service, event)
        await self.prepare(service, duplicate)
        task = asyncio.create_task(event.send(event.result))
        await paused.wait()
        await duplicate.send(duplicate.result)
        self.assertEqual(duplicate.sent, [])
        self.assertEqual(duplicate.get_extra(bubbles.STATUS_KEY), "sending")
        release.set()
        await task
        self.assertEqual(len(event.sent), 2)
        await duplicate.send(duplicate.result)
        self.assertEqual(duplicate.sent, [])

    async def test_external_cancellation_leaves_attempt_marked_and_suppresses_retry(self):
        event, service = Event(), bubbles.BubbleReplies()
        service._selected = lambda *args: True
        entered = asyncio.Event()
        async def block(*args):
            entered.set()
            await asyncio.Event().wait()
        service._pause = block
        await self.prepare(service, event)
        pending = asyncio.create_task(event.send(event.result))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError): await pending
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "cancelled")
        await event.send(event.result)
        self.assertEqual(len(event.sent), 1)

    async def test_invalid_or_cross_conversation_events_do_not_wrap(self):
        for event in (Event(message_id=""), Event(sender="987654321"), Event(origin="qq-test:GroupMessage:1")):
            self.assertFalse(await self.prepare(AlwaysBubbles(), event))

    async def test_sticker_appended_after_prepare_is_attached_to_last_bubble(self):
        event, service = Event(), AlwaysBubbles()
        await self.prepare(service, event)
        image = Image()
        event.result.chain.append(image)
        await event.send(event.result)
        self.assertEqual(len(event.sent), 2)
        self.assertIs(event.sent[-1].chain[-1], image)
        self.assertTrue(all(any(isinstance(component, Plain) for component in message.chain) for message in event.sent))

    async def test_core_transformation_after_prepare_is_respected(self):
        event, service = Event(), AlwaysBubbles()
        await self.prepare(service, event)
        rendered = Chain([Image()])
        await event.send(rendered)
        self.assertEqual(event.sent, [rendered])
        self.assertIsNone(event.get_extra(bubbles.STATUS_KEY))

    async def test_pending_network_send_finishes_before_next_turn_bubble(self):
        old, new, service = Event(), Event(message_id="turn-2"), AlwaysBubbles()
        entered, release, order = asyncio.Event(), asyncio.Event(), []
        async def slow_send(message):
            entered.set()
            await release.wait()
            order.append("old")
        async def new_send(message): order.append("new")
        old.send, new.send = slow_send, new_send
        await self.prepare(service, old)
        pending_old = asyncio.create_task(old.send(old.result))
        await entered.wait()
        await self.prepare(service, new)
        pending_new = asyncio.create_task(new.send(new.result))
        await asyncio.sleep(0)
        self.assertEqual(order, [])
        release.set()
        await asyncio.gather(pending_old, pending_new)
        self.assertEqual(order, ["old", "new", "new"])
        self.assertEqual(old.get_extra(bubbles.STATUS_KEY), "cancelled")


if __name__ == "__main__":
    unittest.main()
