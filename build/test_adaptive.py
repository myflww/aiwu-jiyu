"""Local adaptive policy and real-event timing races. No network or model."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock

from build.test_turn_taking import Event, Plain, Image, At, Reply, OPTIONS, turns

PACKAGE = "_adaptive_policy_test"
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / "assets/plugins/astrbot_plugin_dafeiyu_companion")]
sys.modules[PACKAGE] = package
sys.modules[PACKAGE + ".turn_taking"] = turns
spec = importlib.util.spec_from_file_location(PACKAGE + ".adaptive", Path(package.__path__[0]) / "adaptive.py")
adaptive = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adaptive
spec.loader.exec_module(adaptive)

BASE = {**OPTIONS, "collection_seconds": 10, "collection_max_wait_seconds": 30,
        "reply_timing_mode": "adaptive", "persona_profile": "natural", "group_reply_mode": "auto"}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.controller = adaptive.AdaptiveController(clock=lambda: self.now)

    def plan(self, event, **options):
        return self.controller.predict(event, {**BASE, **options})

    def admitted(self, event, **options):
        self.plan(event, **options)
        result = self.controller.admit(event, {**BASE, **options}, {})
        event.set_extra(turns.GROUP_KEY, result)
        return result

    def test_complete_question_short_fragment_continuation_correction_media(self):
        expected = [("你知道附近哪里好玩吗？", "complete_question"), ("我想", "short_fragment"),
                    ("还有一点，", "explicit_continuation"), ("不对，我想重新说", "correction")]
        plans = [self.plan(Event(str(i), text)) for i, (text, _) in enumerate(expected)]
        self.assertEqual([plan["reasons"][0] for plan in plans], [reason for _, reason in expected])
        self.assertLess(plans[0]["collection_seconds"], plans[1]["collection_seconds"])
        self.assertLessEqual(plans[1]["collection_seconds"], plans[2]["collection_seconds"])
        image = Image("native-existing-media")
        event = Event("photo", "", chain=[image])
        self.assertIn("media_caption_window", self.plan(event)["reasons"])
        self.assertIs(event.get_messages()[0], image)

    def test_fixed_instant_and_always_immediate_emergency_end_commands(self):
        self.assertEqual(self.plan(Event("fixed"), reply_timing_mode="fixed")["collection_seconds"], 10)
        self.assertEqual(self.plan(Event("instant"), reply_timing_mode="instant")["collection_seconds"], 0)
        for mode in ("adaptive", "fixed", "instant"):
            for index, text in enumerate(("救命，着火了", "不用回复", "/help")):
                plan = self.plan(Event(mode + str(index), text), reply_timing_mode=mode)
                self.assertEqual(plan["collection_seconds"], 0)
                self.assertEqual(plan["response_mode"], "single")

    def test_persona_changes_actual_timing_and_output_bounded_not_just_prompt(self):
        plans = {name: self.plan(Event(name, "今天见到一只很好看的猫。"), persona_profile=name)
                 for name in ("natural", "classic", "gentle", "playful", "study")}
        self.assertLess(plans["playful"]["collection_seconds"], plans["gentle"]["collection_seconds"])
        self.assertEqual(plans["gentle"]["max_bubbles"], 2)
        self.assertEqual(plans["playful"]["max_bubbles"], 4)
        self.assertEqual(plans["study"]["response_mode"], "auto")
        self.assertEqual(self.plan(Event("invalid"), persona_profile=[])["profile"], "natural")
        for name in plans:
            plan = self.plan(Event("serious" + name, "别闹，认真一点，我有急事。"), persona_profile=name)
            self.assertEqual((plan["response_mode"], plan["max_bubbles"]), ("single", 1))

    def test_cadence_ema_is_member_and_session_isolated_bounded_reset_and_idempotent(self):
        self.plan(Event("a", "碎片"))
        self.now += .3
        second = Event("b", "碎片")
        self.plan(second)
        self.now += .3
        fast = self.plan(Event("c", "碎片"))
        self.assertEqual(fast["cadence_samples"], 2)
        self.assertEqual(self.plan(second)["cadence_samples"], 1)  # no re-observation
        other = self.plan(Event("c", "碎片", sender="22222222"))
        group = self.plan(Event("c", "碎片", group=True))
        self.assertEqual((other["cadence_samples"], group["cadence_samples"]), (0, 0))
        self.now += 20
        slow = self.plan(Event("d", "碎片"))
        self.assertGreater(slow["collection_seconds"], fast["collection_seconds"])
        self.now += 121
        self.assertEqual(self.plan(Event("e"))["cadence_samples"], 0)

    def test_recent_scene_decays_and_changes_real_output_without_punitive_delay(self):
        support = self.plan(Event("support", "我今天心情不好。"))
        self.assertEqual((support["scene"], support["max_bubbles"]), ("support", 2))
        self.assertEqual(support["response_mode"], "auto")
        self.now += 10
        ongoing = self.plan(Event("neutral", "刚刚看见了一只很漂亮的猫。"))
        self.assertEqual(ongoing["scene"], "support")
        self.assertLessEqual(ongoing["collection_seconds"], 2.4)
        self.now += 80
        self.assertEqual(self.plan(Event("decayed", "刚刚看见了一只很漂亮的猫。"))["scene"], "calm")
        excited = self.plan(Event("excited", "哈哈哈，刚刚看见了一只很漂亮的猫。"))
        self.assertEqual(excited["scene"], "playful")
        self.assertNotIn("拒绝", excited["style_hint"])
        serious = self.plan(Event("serious", "这件事情很重要，请认真回答。"))
        self.assertEqual((serious["scene"], serious["response_mode"]), ("focused", "single"))
        self.now += 2
        follow = self.plan(Event("formal-follows", "还有一些细节。"))
        self.assertEqual(follow["response_mode"], "single")

    def test_formal_to_explicit_casual_switch_releases_focused_but_vague_or_quoted_does_not(self):
        formal = self.plan(Event("formal", "请写一份完整报告"))
        self.assertEqual((formal["scene"], formal["response_mode"]), ("focused", "single"))
        for index, text in enumerate(("嗯", "还有一些细节", "然后呢", "闲聊记录需要整理", "她说‘一起聊聊今天的小云朵’")):
            plan = self.plan(Event("supplement" + str(index), text))
            self.assertEqual((plan["scene"], plan["response_mode"]), ("focused", "single"), text)
        for index, text in enumerate(("一起聊聊今天的小云朵", "我们换个轻松的话题吧", "先聊点轻松的吧")):
            self.plan(Event("new-formal" + str(index), "这是重要的正式内容，请一条发完"))
            casual = self.plan(Event("explicit-casual" + str(index), text))
            self.assertEqual((casual["scene"], casual["response_mode"]), ("calm", "bubbles"), text)
            self.assertIn("explicit_casual_switch", casual["reasons"])
        # The request itself still contains a formal instruction: a casual
        # phrase inside it cannot lift explicit one-packet protection.
        retained = self.plan(Event("mixed-intent", "请写一份完整报告，然后一起聊聊今天的小云朵"))
        self.assertEqual((retained["scene"], retained["response_mode"]), ("focused", "single"))

    def test_group_auto_requires_actual_accepted_reply_and_same_member_relevance(self):
        first = Event("addressed", "大肥鱼，猫咪为什么喜欢纸箱？", group=True)
        self.assertEqual(self.admitted(first), "addressed")
        follow = Event("follow", "那它为什么还会钻进去？", group=True)
        self.assertEqual(self.admitted(follow), "silent")
        self.controller.mark_sent(first, accepted=False)
        self.assertEqual(self.admitted(follow), "silent")
        self.controller.mark_sent(first)
        self.now += 2
        self.assertEqual(self.admitted(follow), "auto_followup")
        other = Event("other", "那它为什么还会钻进去？", sender="22222222", group=True)
        self.assertEqual(self.admitted(other), "silent")
        self.assertEqual(self.admitted(Event("chat", "大家今天晚上吃什么？", group=True)), "silent")
        self.assertEqual(self.admitted(Event("unrelated-exit", "那我先去吃饭了。", group=True)), "silent")
        self.assertEqual(self.admitted(Event("generic-start", "这个人真的很有意思。", group=True)), "silent")
        elsewhere = Event("elsewhere", "为什么呢？", group=True, chain=[At("22222222"), Plain("为什么呢？")])
        self.assertEqual(self.admitted(elsewhere), "silent")

    def test_sent_dedup_does_not_extend_window_and_group_identity_isolated(self):
        first = Event("first", "大肥鱼，猫咪为什么喜欢纸箱？", group=True)
        self.admitted(first)
        self.controller.mark_sent(first)
        self.now += 80
        self.controller.mark_sent(first)  # same multi-packet reply cannot reopen
        self.now += 11
        self.assertEqual(self.admitted(Event("late", "为什么呢？", group=True)), "silent")
        new = Event("new", "大肥鱼，猫咪为什么喜欢纸箱？", group=True)
        self.admitted(new)
        self.controller.mark_sent(new)
        other_group = Event("new-group", "为什么呢？", group=True)
        other_group.unified_msg_origin = "qq-test:GroupMessage:33333333"
        other_group.get_group_id = lambda: "33333333"
        self.assertEqual(self.admitted(other_group), "silent")

    def test_explicit_predict_profile_controls_followup_without_public_options_field(self):
        first = Event("first", "小鱼，猫咪为什么喜欢纸箱？", group=True)
        self.admitted(first)
        self.controller.mark_sent(first)
        self.now += 100
        follow = Event("gentle-follow", "那猫咪为什么会钻纸箱？", group=True)
        self.controller.predict(follow, BASE, profile="gentle")
        self.assertEqual(self.controller.admit(follow, BASE, {}), "auto_followup")

    def test_one_confirmation_only_after_actual_accepted_inviting_packet(self):
        first = Event("proposal", "小鱼，今天聊聊猫咪", group=True)
        self.admitted(first)
        self.controller.mark_sent(first)
        self.assertEqual(self.admitted(Event("ordinary-yes", "可以呀", group=True)), "silent")
        first.set_extra("dafeiyu_bubbles_sent_count", 2)
        first.set_extra("dafeiyu_last_packet_invites_reply", True)
        self.controller.mark_sent(first)
        first.set_extra("dafeiyu_bubbles_sent_count", 3)
        first.set_extra("dafeiyu_last_packet_invites_reply", False)  # later image-only
        self.controller.mark_sent(first)
        confirmation = Event("confirmation", "可以呀", group=True)
        self.assertEqual(self.admitted(confirmation), "auto_followup")
        self.assertEqual(self.admitted(confirmation), "auto_followup")
        self.assertEqual(self.admitted(Event("second-confirmation", "收到", group=True)), "silent")
        self.controller.mark_sent(first)  # same count cannot reopen
        self.assertEqual(self.admitted(Event("third-confirmation", "好", group=True)), "silent")

    def test_confirmation_window_is_member_scoped_short_and_not_media(self):
        first = Event("proposal", "小鱼，今天聊聊猫咪", group=True)
        self.admitted(first)
        first.set_extra("dafeiyu_last_packet_invites_reply", True)
        self.controller.mark_sent(first)
        self.assertEqual(self.admitted(Event("other", "可以呀", sender="22222222", group=True)), "silent")
        self.assertEqual(self.admitted(Event("media", "可以呀", group=True, chain=[Image("photo"), Plain("可以呀")])), "silent")
        self.now += 46
        self.assertEqual(self.admitted(Event("late", "可以呀", group=True)), "silent")

    def test_late_accepted_question_same_response_cannot_reopen_consumed_confirmation(self):
        first = Event("proposal", "小鱼，今天聊聊猫咪", group=True)
        self.admitted(first)
        first.set_extra("dafeiyu_last_packet_invites_reply", True)
        first.set_extra("dafeiyu_bubbles_sent_count", 1)
        self.controller.mark_sent(first)
        self.assertEqual(self.admitted(Event("confirmed", "可以呀", group=True)), "auto_followup")
        # A genuinely later accepted packet has a new count and another
        # question, so receipt dedup alone cannot protect this boundary.
        first.set_extra("dafeiyu_bubbles_sent_count", 2)
        self.controller.mark_sent(first)
        self.assertEqual(self.admitted(Event("second-yes", "好", group=True)), "silent")
        # A different response token may offer one new real invitation.
        next_reply = Event("new-proposal", "小鱼，再聊聊猫咪吧", group=True)
        self.admitted(next_reply)
        next_reply.set_extra("dafeiyu_last_packet_invites_reply", True)
        self.controller.mark_sent(next_reply)
        self.assertEqual(self.admitted(Event("new-confirmation", "收到", group=True)), "auto_followup")

    def test_quiet_only_actual_at_or_quote_auto_name_and_legacy_modes(self):
        self.assertEqual(self.admitted(Event("name", "小鱼，你在吗？", group=True)), "addressed")
        self.assertEqual(self.admitted(Event("quiet-name", "小鱼，你在吗？", group=True), group_reply_mode="quiet"), "silent")
        for component in (At("987654321"), Reply("987654321")):
            self.assertEqual(self.admitted(Event(str(type(component)), group=True, chain=[component]), group_reply_mode="quiet"), "addressed")
        self.assertEqual(self.admitted(Event("all", group=True, chain=[At("all")]), group_reply_mode="quiet"), "silent")
        self.assertEqual(self.admitted(Event("legacy", "大肥鱼，你在吗", group=True), group_reply_mode="addressed"), "addressed")

    def test_busy_group_restricts_followup_and_uninvited_semantics_not_retained(self):
        first = Event("first", "大肥鱼，猫咪为什么喜欢纸箱？", group=True)
        self.admitted(first)
        self.controller.mark_sent(first)
        for index in range(4):
            self.now += .5
            self.admitted(Event("noise" + str(index), "我今天心情不好。", sender=str(22000000 + index), group=True))
        generic = Event("generic", "为什么呢？", group=True)
        plan = self.plan(generic)
        self.assertTrue(plan["group_busy"])
        self.assertEqual(self.admitted(generic), "silent")
        relevant = Event("relevant", "那猫咪为什么喜欢那个纸箱呢？", group=True)
        self.assertEqual(self.admitted(relevant), "auto_followup")
        self.now += 26
        self.assertEqual(self.admitted(Event("stale-busy", "那猫咪为什么喜欢那个纸箱呢？", group=True)), "silent")
        for state in self.controller._cadence.values():
            self.assertEqual(state.signals, {})

    def test_followup_budget_real_groups_even_unique_session_and_explicit_invitation_survives(self):
        first = Event("first", "小鱼，猫咪为什么喜欢纸箱？", group=True)
        self.admitted(first)
        self.controller.mark_sent(first)
        admitted = []
        for index in range(4):
            event = Event("follow" + str(index), "那猫咪为什么喜欢纸箱？", group=True)
            event.unified_msg_origin = "qq-test:GroupMessage:123456789_11111111"
            self.assertEqual(self.admitted(event), "auto_followup")
            self.assertEqual(self.admitted(event), "auto_followup")
            admitted.append(event)
        self.assertEqual(self.admitted(admitted[0]), "auto_followup")
        self.assertEqual(self.admitted(Event("fifth", "那猫咪为什么喜欢纸箱？", group=True)), "silent")
        self.assertEqual(self.admitted(Event("at", group=True, chain=[At("987654321")])), "addressed")

    def test_state_is_bounded_and_contains_no_ids_or_plaintext_and_close_is_terminal(self):
        marker = "uniquesensitivechatcanary"
        for index in range(300):
            self.plan(Event(str(index), marker, sender=str(10000000 + index), group=True))
        self.assertLessEqual(len(self.controller._cadence), 256)
        contents = repr((self.controller._groups, self.controller._cadence, self.controller._seen))
        self.assertNotIn(marker, contents)
        self.assertNotIn("123456789", contents)
        self.controller.close()
        self.assertEqual(self.plan(Event("closed"))["response_mode"], "single")
        self.assertEqual(self.admitted(Event("closed-group", group=True, chain=[At("987654321")])), "silent")


class DynamicCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 0.0
        self.collector = turns.MessageCollector(Plain, clock=lambda: self.now)
        self.guard = AsyncMock(return_value=True)
        self.addCleanup(self.collector.close)

    def offer(self, event, **options):
        return asyncio.create_task(self.collector.collect(event, {**BASE, **options}, self.guard))

    async def tick(self, now):
        self.now = now
        for batch in list(self.collector._pending.values()):
            batch.changed.set()
        for _ in range(4):
            await asyncio.sleep(0)

    async def test_new_complete_question_shortens_live_wait_preserving_media_exactly_once(self):
        image = Image("original-photo")
        first = Event("fragment", "先看看", chain=[Plain("先看看"), image])
        first._temporary_local_files = ["managed-real-photo"]
        second = Event("question", "这张图里是什么？")
        a = self.offer(first, collection_seconds=10)
        await asyncio.sleep(0)
        self.now = 1
        b = self.offer(second, collection_seconds=1)
        await asyncio.sleep(0)
        await self.tick(2.1)
        self.assertEqual(await asyncio.wait_for(asyncio.gather(a, b), .2), [False, True])
        self.assertEqual(second.message_str, "先看看\n这张图里是什么？")
        self.assertIs(second.get_messages()[1], image)
        self.assertEqual(first._temporary_local_files, [])
        self.assertEqual(second._temporary_local_files, ["managed-real-photo"])

    async def test_new_fragment_can_extend_pause_but_never_the_first_arrival_cap(self):
        first, second = Event("first", "你好呀。"), Event("second", "还有一点，")
        a = self.offer(first, collection_seconds=2, collection_max_wait_seconds=5)
        await asyncio.sleep(0)
        self.now = 1
        b = self.offer(second, collection_seconds=10, collection_max_wait_seconds=30)
        await asyncio.sleep(0)
        await self.tick(3)
        self.assertFalse(a.done())
        self.assertFalse(b.done())
        await self.tick(5.1)
        self.assertEqual(await asyncio.wait_for(asyncio.gather(a, b), .2), [False, True])

    async def test_strictest_formal_policy_survives_late_fragment_and_no_extra_event(self):
        first, second = Event("formal", "给我一份正式合同"), Event("late", "就是这个意思")
        first.set_extra(adaptive.POLICY_KEY, {"response_mode": "single", "max_bubbles": 1, "style_hint": "认真完整"})
        second.set_extra(adaptive.POLICY_KEY, {"response_mode": "bubbles", "max_bubbles": 4, "style_hint": "轻快"})
        a = self.offer(first)
        await asyncio.sleep(0)
        b = self.offer(second, collection_max_messages=2)
        self.assertEqual(await asyncio.gather(a, b), [False, True])
        policy = second.get_extra(adaptive.POLICY_KEY)
        self.assertEqual((policy["response_mode"], policy["max_bubbles"]), ("single", 1))
        self.assertEqual(policy["style_hint"], "认真完整")
        self.assertEqual(self.collector._pending, {})

    async def test_expired_window_cannot_be_reopened_by_event_loop_busy_new_input(self):
        first, second = Event("old", "第一条"), Event("new", "还有一点，")
        a = self.offer(first, collection_seconds=2)
        for _ in range(4):
            await asyncio.sleep(0)
        # Simulate the event loop doing other work beyond the deadline before
        # its timer callback runs. Keep both genuine inputs, release new once.
        self.now = 3
        b = self.offer(second, collection_seconds=12)
        self.assertEqual(await asyncio.wait_for(asyncio.gather(a, b), .2), [False, True])
        self.assertEqual(second.message_str, "第一条\n还有一点，")


if __name__ == "__main__":
    unittest.main()
