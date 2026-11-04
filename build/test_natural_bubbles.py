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
SHORT_TEXT = "今天的小尾巴已经准备好陪你一起聊天啦。刚才看到一朵像鲸鱼的小云，忍不住想分享给你。"


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
    def get_message_str(self): return "一起随便聊聊吧"
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
            self.assertTrue(all(len(part.strip()) >= 12 for part in parts))

    def test_short_casual_sentences_can_be_two_complete_different_bubbles(self):
        self.assertTrue(40 <= len(SHORT_TEXT) <= 70)
        parts = bubbles.split_text(SHORT_TEXT)
        self.assertEqual(len(parts), 2)
        self.assertNotEqual(parts[0], parts[1])
        self.assertEqual("".join(parts), SHORT_TEXT)
        self.assertTrue(all(len(part.strip()) >= 12 and part.rstrip().endswith("。") for part in parts))

    def test_threshold_uses_complete_sentences_without_cutting_tiny_acknowledgments(self):
        for text in ("在", "嗯", "好呀。", "第一句话还没说完" * 6,
                     "短句。" + "这是一句足够长但前面短句不应变成单独一包的普通文字。"):
            self.assertEqual(bubbles.split_text(text), [text])
        lower = "甲" * 17 + "。" + "乙" * 17 + "。"
        self.assertEqual(len(lower), 36)
        self.assertEqual(len(bubbles.split_text(lower)), 2)
        self.assertEqual(bubbles.split_text(lower[:-1]), [lower[:-1]])
        padded = "  " + SHORT_TEXT.replace("。", "。\n\n", 1) + "  "
        self.assertEqual("".join(bubbles.split_text(padded)), padded)

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
    async def test_unsplit_plain_response_also_claims_once_and_never_falls_through_to_host_again(self):
        event, service = Event(result=Chain([Plain("好呀，晚安。")])) , AlwaysBubbles()
        await service.prepare(event, {"natural_bubbles_enabled": False}, AsyncMock(return_value=True), CONFIG)
        await asyncio.gather(event.send(event.result), event.send(event.result))
        await event.send(Chain([Plain("模型重试返回不同文本也不应重复回复")]))
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.sent[0].chain[0].text, "好呀，晚安。")

    async def test_formal_task_keeps_complete_response_even_with_natural_bubbles_enabled(self):
        event, service = Event(), AlwaysBubbles()
        event.get_message_str = lambda: "请帮我写一份详细报告"
        await self.prepare(service, event)
        await event.send(event.result)
        await event.send(event.result)
        self.assertEqual(event.sent, [event.result])

    async def test_explicit_formal_requests_keep_the_same_short_answer_in_one_packet(self):
        for request in ("这是正式内容", "这是重要的事", "请严肃一点", "不要分条", "别分条", "一条发完",
                        "合同内容", "法律问题", "财务问题", "完整文稿"):
            event, service = Event(result=Chain([Plain(SHORT_TEXT)])), AlwaysBubbles()
            event.get_message_str = lambda value=request: value
            await self.prepare(service, event)
            await event.send(event.result)
            self.assertEqual(len(event.sent), 1, request)
            self.assertEqual(event.sent[0].chain[0].text, SHORT_TEXT, request)

    async def test_two_group_senders_do_not_interrupt_or_mix_each_others_packets(self):
        a, b, service = Event(origin="qq-test:GroupMessage:11111111"), Event(sender="22222222", origin="qq-test:GroupMessage:11111111"), AlwaysBubbles()
        a.get_message_type = b.get_message_type = lambda: "GroupMessage"
        await self.prepare(service, a)
        await self.prepare(service, b)
        await asyncio.gather(a.send(a.result), b.send(b.result))
        self.assertEqual(len(a.sent), 2)
        self.assertEqual(len(b.sent), 2)

    async def test_sticker_callbacks_receive_only_actual_packets_and_no_false_image_receipt(self):
        event, service, image = Event(), AlwaysBubbles(), Image()
        event.result.chain.append(image)
        before, after = AsyncMock(side_effect=lambda packet: packet), AsyncMock()
        event._dafeiyu_sticker_before_packet = before
        event._dafeiyu_sticker_after_packet = after
        await self.prepare(service, event)
        await event.send(event.result)
        self.assertTrue(event._dafeiyu_sticker_bubble_packets)
        self.assertEqual(before.await_count, 2)
        self.assertEqual(after.await_count, 2)
        self.assertNotIn(image, after.await_args_list[0].args[0].chain)
        self.assertIs(after.await_args_list[-1].args[0].chain[-1], image)
        self.assertTrue(all(call.args[1] == "sent" for call in after.await_args_list))

    async def test_new_input_during_async_image_revalidation_sends_no_obsolete_packet(self):
        event, service = Event(), AlwaysBubbles()
        async def before(packet):
            service.observe(Event(message_id="new-input"))
            return packet
        event._dafeiyu_sticker_before_packet = before
        event._dafeiyu_sticker_after_packet = AsyncMock()
        await self.prepare(service, event)
        await event.send(event.result)
        self.assertEqual(event.sent, [])
        self.assertEqual(event.get_extra(bubbles.ATTEMPT_KEY), 0)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "not_sent")
        event._dafeiyu_sticker_after_packet.assert_not_awaited()

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
            self.assertEqual(event.get_extra(bubbles.STATUS_KEY), None if not config else "sent")

    async def test_tool_finals_are_unsplit_but_claimed_and_non_llm_status_remains_independent(self):
        for mode in ("tool", "sticker-tool", "non-llm", "system"):
            event, service = Event(), AlwaysBubbles()
            if mode == "tool": service.mark_tool(event)
            elif mode == "sticker-tool": event.set_extra("dafeiyu_sticker_tool_round", True)
            elif mode == "non-llm": event.result.is_llm_result = lambda: False
            else: event.result.type = "system"
            self.assertEqual(await self.prepare(service, event), mode in {"tool", "sticker-tool"})
            await event.send(event.result)
            self.assertEqual(event.sent, [event.result])
            if mode in {"tool", "sticker-tool"}:
                await event.send(event.result)
                self.assertEqual(event.sent, [event.result])

    async def test_native_components_each_have_one_slot_and_cloned_final_cannot_start_again(self):
        first, second = Plain("第一条"), Plain("第二条")
        event, service = Event(result=Chain([first, second])), AlwaysBubbles()
        config = {**CONFIG, "platform_settings": {"segmented_reply": {"enable": True}}}
        await self.prepare(service, event, config=config)
        await event.send(event.result.derive([first]))
        await event.send(event.result.derive([second]))
        await event.send(event.result)
        self.assertEqual([packet.chain[0].text for packet in event.sent], ["第一条", "第二条"])
        service.finish(event)
        event.result = None
        await event.send(Chain([Plain("第一条")]))
        self.assertEqual(len(event.sent), 2)

    async def test_queued_native_tail_cannot_continue_after_first_packet_becomes_uncertain(self):
        first, second = Plain("第一条"), Plain("第二条")
        event, service = Event(result=Chain([first, second])), AlwaysBubbles()
        entered, release, attempts = asyncio.Event(), asyncio.Event(), []
        async def uncertain(packet):
            attempts.append(packet)
            entered.set()
            await release.wait()
            raise TimeoutError("accepted but receipt lost")
        event.send = uncertain
        await self.prepare(service, event, config={**CONFIG, "platform_settings": {"segmented_reply": {"enable": True}}})
        a = asyncio.create_task(event.send(event.result.derive([first])))
        await entered.wait()
        b = asyncio.create_task(event.send(event.result.derive([second])))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(a, b, return_exceptions=True)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(event.get_extra(bubbles.COUNT_KEY), 0)

    async def test_cleared_result_alias_and_cloned_final_are_scoped_to_turn_not_global_words(self):
        service, event = AlwaysBubbles(), Event(result=Chain([Plain("你好")]))
        await self.prepare(service, event)
        final = event.result
        await event.send(final)
        event.result = None
        await event.send(final.derive([Plain("你好")]))
        self.assertEqual(len(event.sent), 1)
        new = Event(message_id="new-request", result=Chain([Plain("你好")]))
        await self.prepare(service, new)
        await new.send(new.result)
        self.assertEqual(len(new.sent), 1)

    async def test_policy_single_and_bounded_bubbles_override_probability_without_rewriting_text(self):
        for mode, maximum, count in (("single", 4, 1), ("bubbles", 2, 2)):
            event, service = Event(result=Chain([Plain(TEXT * 10)])), AlwaysBubbles()
            service._selected = lambda *args: False
            event.set_extra(bubbles.POLICY_KEY, {"response_mode": mode, "max_bubbles": maximum, "profile": "quiet"})
            await self.prepare(service, event)
            await event.send(event.result)
            self.assertEqual(len(event.sent), count)
            self.assertEqual("".join(packet.chain[0].text for packet in event.sent), TEXT * 10)

    async def test_real_receipt_callback_failure_cannot_reclassify_or_repeat_an_accepted_packet(self):
        event, service = Event(result=Chain([Plain("收到啦。")])) , AlwaysBubbles()
        event._dafeiyu_reply_packet_accepted = AsyncMock(side_effect=RuntimeError("synthetic analytics failure"))
        await self.prepare(service, event)
        await event.send(event.result)
        await event.send(event.result)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "sent")
        self.assertEqual(event.get_extra(bubbles.COUNT_KEY), 1)
        event._dafeiyu_reply_packet_accepted.assert_awaited_once_with(event)

    async def test_invitation_flag_comes_only_from_accepted_packet_not_unsent_question(self):
        event, service = Event(result=Chain([Plain(TEXT + "今晚要不要一起再聊一会儿？")])) , AlwaysBubbles()
        snapshots = []
        async def accepted(incoming):
            snapshots.append(incoming.get_extra("dafeiyu_last_packet_invites_reply"))
        event._dafeiyu_reply_packet_accepted = accepted
        await self.prepare(service, event, AsyncMock(side_effect=[True, False]))
        await event.send(event.result)
        self.assertEqual(snapshots, [False])
        self.assertNotIn("？", event.get_extra(bubbles.TEXT_KEY))
        question = Event(message_id="question", result=Chain([Plain("那要不要再来一张？")]))
        await self.prepare(service, question)
        await question.send(question.result)
        self.assertTrue(question.get_extra("dafeiyu_last_packet_invites_reply"))
        self.assertFalse(bubbles.packet_invites_reply(Chain([Image()])))
        self.assertFalse(bubbles.packet_invites_reply(Chain([Plain('他说“要不要再来一张？”')])) )

    async def test_unknown_transport_attempt_sets_host_operation_flag_but_no_accepted_callback(self):
        event, service = Event(), AlwaysBubbles()
        event._has_send_oper = False
        event._dafeiyu_reply_packet_accepted = AsyncMock()
        async def failure(message):
            raise TimeoutError("adapter may have accepted already")
        event.send = failure
        await self.prepare(service, event)
        with self.assertRaises(RuntimeError):
            await event.send(event.result)
        self.assertTrue(event._has_send_oper)
        self.assertEqual(event.get_extra(bubbles.COUNT_KEY), 0)
        self.assertEqual(event.get_extra(bubbles.TEXT_KEY), "")
        event._dafeiyu_reply_packet_accepted.assert_not_awaited()

    async def test_streaming_is_armed_once_and_never_custom_split_or_resend_as_full_final(self):
        event, service = Event(), AlwaysBubbles()
        async def stream_native(generator, *_args):
            async for packet in generator:
                await event.send(packet)
        event.send_streaming = stream_native
        await service.arm(event, OPTIONS, AsyncMock(return_value=True), CONFIG)
        async def chunks():
            yield Chain([Plain("第一句。")])
            yield Chain([Plain("第二句。")])
        await event.send_streaming(chunks())
        await event.send_streaming(chunks())
        await event.send(event.result)
        self.assertEqual([packet.chain[0].text for packet in event.sent], ["第一句。", "第二句。"])

    async def test_guard_rechecked_before_every_segment_and_disable_stops_tail(self):
        event, service = Event(), AlwaysBubbles()
        guard = AsyncMock(side_effect=[True, False])
        await self.prepare(service, event, guard)
        await event.send(event.result)
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "interrupted")
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
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "interrupted")
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
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "interrupted")

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
        self.assertEqual(event.get_extra(bubbles.STATUS_KEY), "sent")

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
        self.assertEqual(old.get_extra(bubbles.STATUS_KEY), "interrupted")


if __name__ == "__main__":
    unittest.main()
