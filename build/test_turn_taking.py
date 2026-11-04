"""Race/media/group tests with genuine ordered component objects, no network."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("_turn_taking_test", ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion/turn_taking.py")
turns = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = turns
SPEC.loader.exec_module(turns)


class Plain:
    def __init__(self, text): self.text = text


class Image:
    def __init__(self, url): self.url = url


class At:
    def __init__(self, qq): self.qq = qq


class AtAll: pass


class Reply:
    def __init__(self, sender_id): self.sender_id = sender_id


class Event:
    def __init__(self, mid="1", text="你好", sender="123456789", group=False, chain=None):
        self.sender, self.group, self.message_str = sender, group, text
        self.message_obj = SimpleNamespace(message_id=mid, message_str=text, message=chain or [Plain(text)])
        self.unified_msg_origin = "qq-test:GroupMessage:11111111" if group else f"qq-test:FriendMessage:{sender}"
        self.extra = {}
        self._temporary_local_files = []
        self.stopped = False
        self.is_at_or_wake_command = True
        self.call_llm = False
    def get_sender_id(self): return self.sender
    def get_self_id(self): return "987654321"
    def get_platform_id(self): return "qq-test"
    def get_platform_name(self): return "aiocqhttp"
    def get_message_type(self): return "GroupMessage" if self.group else "FriendMessage"
    def get_group_id(self): return "11111111" if self.group else ""
    def get_message_str(self): return self.message_str
    def get_messages(self): return self.message_obj.message
    def get_extra(self, key, default=None): return self.extra.get(key, default)
    def set_extra(self, key, value): self.extra[key] = value
    def track_temporary_local_file(self, path): self._temporary_local_files.append(path)
    def untrack_temporary_local_file(self, path): self._temporary_local_files.remove(path)
    def stop_event(self): self.stopped = True


OPTIONS = {"message_collection_enabled": True, "collection_seconds": .035,
           "collection_max_wait_seconds": .13, "collection_max_messages": 12, "collection_max_chars": 6000,
           "group_reply_mode": "addressed", "group_cooldown_seconds": 90, "group_hourly_limit": 6,
           "group_participation_probability": 3, "group_wake_words": "大肥鱼,小鱼"}


class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.collector = turns.MessageCollector(Plain)
        self.guard = AsyncMock(return_value=True)
        self.addCleanup(self.collector.close)

    def offer(self, event, **options):
        return asyncio.create_task(self.collector.collect(event, {**OPTIONS, **options}, self.guard))

    async def test_trailing_reset_one_continuing_event_preserves_text_image_quote_order(self):
        image, quote = Image("file:///original.png"), Reply("987654321")
        first = Event("1", "先看这张图", chain=[Plain("先看这张图"), image])
        second = Event("2", "再看这个细节", chain=[quote, Plain("再看这个细节")])
        first._temporary_local_files = ["original-managed-image.png"]
        task1 = self.offer(first)
        await asyncio.sleep(.02)
        task2 = self.offer(second)
        await asyncio.sleep(.022)
        self.assertFalse(task1.done())  # Original deadline passed; new input reset it.
        self.assertEqual(await asyncio.wait_for(asyncio.gather(task1, task2), .5), [False, True])
        self.assertEqual(second.message_str, "先看这张图\n再看这个细节")
        self.assertEqual(second.get_messages()[1:4], [image, second.get_messages()[2], quote])
        self.assertIs(second.get_messages()[1], image)
        self.assertIs(second.get_messages()[3], quote)
        self.assertEqual(first._temporary_local_files, [])
        self.assertEqual(second._temporary_local_files, ["original-managed-image.png"])
        self.assertEqual(second.get_extra(turns.COLLECTED_KEY)["message_ids"], ["1", "2"])
        self.guard.assert_awaited_once()

    async def test_hard_max_wait_stops_continuous_typing_without_lost_messages(self):
        clock = [0.0]
        self.collector._clock = lambda: clock[0]
        tasks, events = [], []
        for index in range(6):
            event = Event(str(index), str(index))
            events.append(event)
            tasks.append(self.offer(event, collection_seconds=10, collection_max_wait_seconds=30))
            await asyncio.sleep(0)
            clock[0] += 4
        self.assertTrue(all(not task.done() for task in tasks))
        clock[0] = 31
        next(iter(self.collector._pending.values())).changed.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), .5)
        self.assertEqual(sum(results), 1)
        self.assertEqual(events[-1].message_str, "0\n1\n2\n3\n4\n5")

    async def test_count_and_char_threshold_flush_without_truncating_last_message(self):
        for options, first_text, second_text in (({"collection_max_messages": 2}, "one", "two"),
                ({"collection_max_chars": 5}, "123", "完整保留最后这段很长的文字")):
            first, second = Event("one", first_text), Event("two", second_text)
            t1 = self.offer(first, **options)
            await asyncio.sleep(0)
            t2 = self.offer(second, **options)
            self.assertEqual(await asyncio.wait_for(asyncio.gather(t1, t2), .03), [False, True])
            self.assertEqual(second.message_str, first_text + "\n" + second_text)

    async def test_group_members_are_never_globally_aggregated(self):
        a1, a2 = Event("a1", "甲一", group=True), Event("a2", "甲二", group=True)
        b = Event("b", "乙", sender="22222222", group=True)
        tasks = [self.offer(a1), self.offer(b)]
        await asyncio.sleep(0)
        tasks.append(self.offer(a2))
        self.assertEqual(await asyncio.gather(*tasks), [False, True, True])
        self.assertEqual(a2.message_str, "甲一\n甲二")
        self.assertEqual(b.message_str, "乙")

    async def test_command_urgent_stop_disable_and_restart_never_replay_pending_input(self):
        for immediate, enabled, expected in (("/help", True, True), ("救命，着火了", True, True),
                                             ("先别回了", True, True), ("新一条", False, True)):
            first, second = Event("old", "还没说完"), Event("new", immediate)
            pending = self.offer(first)
            await asyncio.sleep(0)
            self.assertEqual(await self.collector.collect(second, {**OPTIONS, "message_collection_enabled": enabled}, self.guard), expected)
            self.assertFalse(await pending)
            self.assertEqual(second.message_str, "还没说完\n" + immediate if immediate == "先别回了" else immediate)
        pending = self.offer(Event("restart"))
        await asyncio.sleep(0)
        self.collector.close()
        self.assertFalse(await pending)
        self.assertEqual(self.collector._pending, {})

    async def test_same_event_reentry_and_duplicate_ids_only_one_arrival_token(self):
        registry = turns.Turns()
        first, duplicate, new = Event("a"), Event("a"), Event("b")
        token = registry.observe(first)
        self.assertIs(registry.observe(first), token)
        self.assertIsNone(registry.observe(duplicate))
        self.assertTrue(registry.current_event(first))
        registry.observe(new)
        self.assertFalse(registry.current_event(first))
        self.assertTrue(registry.current_event(new))
        pending = self.offer(first)
        await asyncio.sleep(0)
        self.assertFalse(await self.collector.collect(first, OPTIONS, self.guard))
        self.assertTrue(await pending)

    async def test_permission_revocation_before_release_stops_normal_pipeline(self):
        self.guard.return_value = False
        first, last = Event("old"), Event("last")
        pending = self.offer(first)
        await asyncio.sleep(0)
        current = self.offer(last)
        self.assertEqual(await asyncio.gather(pending, current), [False, False])

    async def test_task_cancel_discards_batch_and_leaves_no_timer_or_retry(self):
        task1 = self.offer(Event("old"))
        await asyncio.sleep(0)
        task2 = self.offer(Event("last"))
        await asyncio.sleep(0)
        task2.cancel()
        with self.assertRaises(asyncio.CancelledError): await task2
        self.assertFalse(await task1)
        self.assertEqual(self.collector._pending, {})

    async def test_cancel_released_old_winner_does_not_cancel_new_batch_with_same_key(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_guard():
            entered.set()
            await release.wait()
            return True
        old = self.offer(Event("old"), collection_max_messages=2)
        await asyncio.sleep(0)
        released = asyncio.create_task(self.collector.collect(Event("winner"),
            {**OPTIONS, "collection_max_messages": 2}, slow_guard))
        await entered.wait()
        fresh = self.offer(Event("fresh"))
        await asyncio.sleep(0)
        released.cancel()
        with self.assertRaises(asyncio.CancelledError): await released
        self.assertFalse(await old)
        self.assertTrue(await fresh)


class GroupTests(unittest.TestCase):
    def test_explicit_plain_no_reply_is_literal_not_a_generic_assent_or_media_filter(self):
        for text in ("不用回", "不用回复", "我先忙了，不用回。", "先不聊了"):
            self.assertTrue(turns.no_reply_requested(Event(text=text)), text)
        for text in ("好", "可以", "收到", "可以呀", "不用回复吗？", "她说‘不用回复’", '"不用回"', "/不用回"):
            self.assertFalse(turns.no_reply_requested(Event(text=text)), text)
        self.assertFalse(turns.no_reply_requested(Event(text="不用回复", chain=[Plain("不用回复"), Image("photo")])))
        self.assertFalse(turns.no_reply_requested(Event(text="不用回复", chain=[Reply("987654321"), Plain("不用回复")])))
    def test_default_only_actual_at_quote_or_explicit_name_not_every_sentence_or_atall(self):
        group = turns.GroupAdmission()
        for event in (Event(text="大家晚上好", group=True), Event(text="大家晚上好", group=True, chain=[AtAll()])):
            self.assertEqual(group.admit(event, OPTIONS, {}), "silent")
        for event in (Event(text="大肥鱼，晚上好", group=True), Event(group=True, chain=[At("987654321")]),
                      Event(group=True, chain=[Reply("987654321")])):
            self.assertEqual(group.admit(event, OPTIONS, {}), "addressed")
        wrong = Event(group=True, chain=[At("11111111"), Reply("11111111")])
        self.assertEqual(group.admit(wrong, OPTIONS, {}), "silent")
        prefix = Event(text="已经去掉前缀", group=True)
        prefix.message_obj.message_str = "/fish 已经去掉前缀"
        self.assertEqual(group.admit(prefix, OPTIONS, {"wake_prefix": ["/fish"]}), "addressed")

    def test_light_probability_cooldown_sliding_hour_and_zero_budget_do_not_block_explicit(self):
        now = [10000.0]
        group = turns.GroupAdmission(clock=lambda: now[0])
        options = {**OPTIONS, "group_reply_mode": "light", "group_participation_probability": 20, "group_hourly_limit": 2}
        choices = [Event(str(i), "有人提到大肥鱼呢", group=True) for i in range(200)]
        first = next(event for event in choices if group.admit(event, options, {}) == "light")
        self.assertTrue(all(group.admit(event, options, {}) == "silent" for event in choices))
        now[0] += 91
        self.assertTrue(any(group.admit(event, options, {}) == "light" for event in choices))
        now[0] += 91
        self.assertTrue(all(group.admit(event, options, {}) == "silent" for event in choices))
        self.assertEqual(group.admit(Event(text="大肥鱼请回答", group=True), {**options, "group_hourly_limit": 0}, {}), "addressed")
        self.assertEqual(group.admit(first, {**options, "group_hourly_limit": 0}, {}), "silent")
        now[0] += 3600
        self.assertTrue(any(group.admit(event, options, {}) == "light" for event in choices))
        self.assertEqual(group.admit(Event(text="完全无关的话", group=True), options, {}), "silent")
        self.assertEqual(group.admit(first, {**options, "group_participation_probability": 0}, {}), "silent")

    def test_actual_group_budget_shared_when_native_unique_sessions_differ_by_member(self):
        group = turns.GroupAdmission(clock=lambda: 10000)
        options = {**OPTIONS, "group_reply_mode": "light", "group_participation_probability": 20, "group_hourly_limit": 1}
        first = next(event for index in range(1000)
            if group.admit(event := Event(str(index), "提起大肥鱼的事", group=True), options, {}) == "light")
        other = Event("other", "提起大肥鱼的事", sender="22222222", group=True)
        other.unified_msg_origin = "qq-test:GroupMessage:22222222_11111111"
        self.assertEqual(group.admit(other, options, {}), "silent")
        self.assertEqual(len(group._groups), 1)


class HookIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from build.test_companion import main
        self.plugin = main.DafeiyuCompanion(SimpleNamespace(get_config=lambda **kw: {"provider_settings": {"enable": True}}),
              {"enabled": True, "owner_qq": "123456789", "bot_qq": "987654321"})
        self.plugin._collector = turns.MessageCollector(Plain)
        self.plugin._session_allows = AsyncMock(return_value=True)
        base = self.plugin._options()
        self.settings = {**base, **OPTIONS}
        self.plugin._options = lambda: dict(self.settings)
        self.addCleanup(self.plugin._collector.close)
        self.addCleanup(self.plugin._turns.close)
        self.addCleanup(self.plugin._bubbles.close)

    async def execute(self, event):
        await self.plugin.collect_messages(event)
        if not event.stopped and event.is_at_or_wake_command and not event.call_llm:
            await self.plugin.guard_collected_turn(event)
            return not event.stopped
        return False

    async def test_collection_works_with_every_optional_companionship_feature_off(self):
        self.assertFalse(self.settings["enabled"])
        first, second = Event("a", "第一条"), Event("b", "第二条")
        pending = asyncio.create_task(self.execute(first))
        await asyncio.sleep(0)
        current = asyncio.create_task(self.execute(second))
        self.assertEqual(await asyncio.gather(pending, current), [False, True])
        self.assertEqual(second.message_str, "第一条\n第二条")

    async def test_hard_group_gate_and_same_sender_addressed_continuation(self):
        first = Event("a1", "先看这里", group=True, chain=[At("987654321"), Plain("先看这里")])
        other = Event("b1", "乙的无关消息", sender="22222222", group=True)
        continuation = Event("a2", "然后看这个", group=True)
        pending = asyncio.create_task(self.execute(first))
        await asyncio.sleep(0)
        self.assertFalse(await self.execute(other))
        current = asyncio.create_task(self.execute(continuation))
        self.assertEqual(await asyncio.gather(pending, current), [False, True])
        self.assertEqual(continuation.message_str, "先看这里\n然后看这个")
        self.assertNotIn("乙", continuation.message_str)
        self.assertIs(continuation.get_messages()[0], first.get_messages()[0])

    async def test_hot_collection_off_session_revocation_and_master_off_stop_waiting_turn(self):
        for change in ("collection", "session", "master"):
            self.settings["message_collection_enabled"] = True
            self.plugin.config["enabled"] = True
            self.plugin._session_allows.return_value = True
            event = Event(change)
            pending = asyncio.create_task(self.execute(event))
            await asyncio.sleep(0)
            if change == "collection": self.settings["message_collection_enabled"] = False
            elif change == "session": self.plugin._session_allows.return_value = False
            else: self.plugin.config["enabled"] = False
            self.assertFalse(await pending)
            self.assertTrue(event.stopped)

    async def test_duplicate_and_stop_do_not_enter_normal_agent(self):
        first, duplicate = Event("same"), Event("same")
        pending = asyncio.create_task(self.execute(first))
        await asyncio.sleep(0)
        self.assertFalse(await self.execute(duplicate))
        self.assertTrue(await pending)
        pending = asyncio.create_task(self.execute(Event("waiting")))
        await asyncio.sleep(0)
        stop = Event("stop", "先别回了")
        self.assertFalse(await self.execute(stop))
        self.assertFalse(await pending)

    async def test_collection_off_is_immediate_while_group_restraint_stays_on(self):
        self.settings["message_collection_enabled"] = False
        self.assertTrue(await self.execute(Event("now")))
        self.assertFalse(await self.execute(Event("silent", "无关消息", group=True)))

    async def test_master_revoked_during_awaited_session_check_blocks_final_packet(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def check(_origin):
            entered.set()
            await release.wait()
            return True
        self.plugin._session_allows.side_effect = check
        pending = asyncio.create_task(self.plugin._transport_allows(Event("permission-race")))
        await entered.wait()
        self.plugin.config["enabled"] = False
        release.set()
        self.assertFalse(await pending)

    async def test_explicit_plain_ending_keeps_real_input_in_native_history_without_llm_or_assistant(self):
        from types import SimpleNamespace
        history = [{"role": "user", "content": "之前的消息"}, {"role": "assistant", "content": "之前的回答"}]
        manager = SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value="existing-cid"),
            get_conversation=AsyncMock(return_value=SimpleNamespace(history=history)), update_conversation=AsyncMock())
        self.plugin.context.conversation_manager = manager
        first, ending = Event("first", "我还有件事想说"), Event("ending", "我先忙了，不用回。")
        pending = asyncio.create_task(self.execute(first))
        await asyncio.sleep(0)
        self.assertFalse(await self.execute(ending))
        self.assertFalse(await pending)
        manager.update_conversation.assert_awaited_once()
        stored = manager.update_conversation.call_args.kwargs["history"]
        self.assertEqual(stored[:-1], history)
        self.assertEqual(stored[-1], {"role": "user", "content": "我还有件事想说\n我先忙了，不用回。"})
        self.assertEqual(ending.get_extra("dafeiyu_no_reply_history"), "saved")
        self.assertTrue(ending.get_extra(turns.NO_REPLY_KEY))
        duplicate = Event("ending", "我先忙了，不用回。")
        self.assertFalse(await self.execute(duplicate))
        self.assertEqual(manager.update_conversation.await_count, 1)
        for index, text in enumerate(("好", "可以", "收到")):
            self.assertTrue(await self.execute(Event(f"confirmation-{index}", text)))
        media = Event("media", "不用回", chain=[Plain("不用回"), Image("photo")])
        self.assertTrue(await self.execute(media))

    async def test_no_reply_first_message_creates_only_native_user_turn_and_storage_error_is_honest(self):
        manager = SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value=None),
            new_conversation=AsyncMock(return_value="native-new-cid"))
        self.plugin.context.conversation_manager = manager
        event = Event("first-ending", "不用回复")
        self.assertFalse(await self.execute(event))
        manager.new_conversation.assert_awaited_once_with(event.unified_msg_origin,
            content=[{"role": "user", "content": "不用回复"}])
        self.assertEqual(event.get_extra("dafeiyu_no_reply_history"), "saved")
        manager.new_conversation.side_effect = OSError("synthetic-private-error")
        failed = Event("failed-ending", "先不聊了")
        self.assertFalse(await self.execute(failed))
        self.assertEqual(failed.get_extra("dafeiyu_no_reply_history"), "unavailable")


if __name__ == "__main__":
    unittest.main()
