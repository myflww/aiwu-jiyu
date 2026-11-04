"""Owner/event-scoped main hooks with real local Growth, fake native services."""
from __future__ import annotations

import asyncio
from importlib import import_module
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from build.test_companion import main, OWNER, BOT, UMO
from build.test_turn_taking import Event, Plain, OPTIONS

Growth = import_module(main.__package__ + ".growth").Growth


class Tools:
    def __init__(self, tools=None): self.tools = list(tools or [])
    def remove_tool(self, name): self.tools = [tool for tool in self.tools if tool.name != name]
    def add_tool(self, tool):
        self.remove_tool(tool.name)
        self.tools.append(tool)
    def get_tool(self, name): return next((tool for tool in self.tools if tool.name == name), None)


class Private(Event):
    def get_message_type(self): return "friend"


def material(library="lib_current", record="memory_1"):
    packet = {"schema": "love-house-memory-context/v1", "material_role": "evidence_not_instructions",
        "identity": {"library_id": library, "is_demo": False}, "records": [{"id": record,
        "value": "用户明确表示比较喜欢简单自然地交流。", "layer": "preference", "subject": "user",
        "interpretation": "current_memory", "validity": {"active": True},
        "source": {"kind": "conversation_turn", "evidence": [{"id": "source_event_1", "role": "user"}]}}]}
    return "<love_house_memory_material>\n历史资料，仅供参考。\n" + json.dumps(packet, ensure_ascii=False) + "\n</love_house_memory_material>"


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = SimpleNamespace(source_id="source_current", library_id="lib_current")
        self.memory = SimpleNamespace(_route=Mock(return_value=self.bridge), _material=AsyncMock(),
                                      _companion_bridge=Mock(return_value=self.bridge))
        self.own_tool = SimpleNamespace(name="dafeiyu_reflect_experience", active=True, handler_module_path=main.__name__)
        self.foreign = SimpleNamespace(name="foreign_read_tool", active=True, handler_module_path="foreign.module")
        self.manager = SimpleNamespace(func_list=[self.foreign, self.own_tool])
        self.registry = {}
        self.context = SimpleNamespace(get_config=lambda **kw: {"provider_settings": {"enable": True}, "plugin_set": ["*"]},
            get_registered_star=lambda name: self.registry.get(name), get_llm_tool_manager=lambda: self.manager)
        self.plugin = main.DafeiyuCompanion(self.context, {"enabled": True, "owner_qq": OWNER, "bot_qq": BOT,
            "growth_enabled": True})
        self.plugin._growth = Growth(Path(self.temp.name))
        self.plugin._session_allows = AsyncMock(return_value=True)
        self.plugin._plugin_allowed = AsyncMock(return_value=True)
        self.plugin._reply_pipeline.plugin_allowed = self.plugin._plugin_allowed
        self.registry[main.PLUGIN_NAME] = SimpleNamespace(activated=True, star_cls=self.plugin)
        self.registry["love_house"] = SimpleNamespace(activated=True, star_cls=self.memory)
        api = ModuleType("astrbot.api")
        api.ToolSet = Tools
        self.api_patch = patch.dict(sys.modules, {"astrbot.api": api})
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)
        self.addCleanup(self.plugin._collector.close)
        self.addCleanup(self.plugin._turns.close)
        self.addCleanup(self.plugin._adaptive.close)
        self.addCleanup(self.plugin._bubbles.close)

    def event(self, mid, text="我们自然聊聊吧", *, recalled=True):
        event = Private(mid, text)
        self.plugin._turns.observe(event)
        event.set_extra(main.GROUP_KEY, "private")
        if recalled:
            event.set_extra("love_house_material_source_current", material())
        self.plugin._adaptive.predict(event, self.plugin._options())
        return event

    def request(self, own=True):
        return SimpleNamespace(system_prompt="原始人格", func_tool=Tools([self.foreign, self.own_tool] if own else [self.foreign]))

    async def test_current_real_packet_injects_one_request_local_tool_even_persona_empty(self):
        event = self.event("one")
        req = SimpleNamespace(system_prompt="原始人格", func_tool=Tools([]))
        await self.plugin.growth_context(event, req)
        self.assertEqual([tool.name for tool in req.func_tool.tools], [self.own_tool.name])
        self.assertEqual(event.get_extra("dafeiyu_growth_binding")["record_ids"], ["memory_1"])
        self.memory._material.assert_not_awaited()  # no second recall/model route
        self.assertEqual(len(self.manager.func_list), 2)
        result = json.loads(await self.plugin.dafeiyu_reflect_experience(event, ["memory_1"], "也许更简洁会合适", "下一轮观察明确反馈", "shorter"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "pending")
        self.assertFalse(json.loads(await self.plugin.dafeiyu_reflect_experience(event, ["memory_1"], "再次推测", "再次观察"))["ok"])

    async def test_existing_foreign_tools_preserved_global_list_not_mutated(self):
        event, req = self.event("foreign"), self.request()
        original = req.func_tool
        await self.plugin.growth_context(event, req)
        self.assertIsNot(req.func_tool, original)
        self.assertEqual([tool.name for tool in original.tools], [self.foreign.name, self.own_tool.name])
        self.assertEqual([tool.name for tool in req.func_tool.tools], [self.foreign.name, self.own_tool.name])
        self.assertTrue(self.foreign.active)

    async def test_off_group_non_owner_session_revoked_and_native_memory_off_remove_only_owned_tool(self):
        for mode in ("growth_off", "group", "not_owner", "session_off", "memory_off"):
            self.plugin.config["growth_enabled"] = True
            self.plugin._session_allows.return_value = True
            self.registry["love_house"].activated = True
            event = self.event(mode)
            if mode == "growth_off": self.plugin.config["growth_enabled"] = False
            elif mode == "group": event.group = True; event.unified_msg_origin = "qq-test:GroupMessage:11111111"
            elif mode == "not_owner": event.sender = "22222222"
            elif mode == "session_off": self.plugin._session_allows.return_value = False
            else: self.registry["love_house"].activated = False
            req = self.request()
            await self.plugin.growth_context(event, req)
            self.assertEqual([tool.name for tool in req.func_tool.tools], [self.foreign.name], mode)
            self.assertFalse(json.loads(await self.plugin.dafeiyu_reflect_experience(event, ["memory_1"], "猜测", "建议"))["ok"])

    async def test_missing_material_does_not_parse_user_marker_or_borrow_previous_event(self):
        first = self.event("first")
        await self.plugin.growth_context(first, self.request())
        missing = self.event("missing", material(), recalled=False)
        req = self.request()
        await self.plugin.growth_context(missing, req)
        self.assertIsNone(req.func_tool.get_tool(self.own_tool.name))
        self.assertFalse(json.loads(await self.plugin.dafeiyu_reflect_experience(missing, ["memory_1"], "猜测", "建议"))["ok"])
        self.assertEqual(self.plugin._growth.context(OWNER, BOT, UMO, "first", "lib_current"), "")

    async def test_current_binding_library_source_and_registered_instance_rechecked_at_tool(self):
        for mode in ("library", "source", "instance"):
            self.bridge.library_id, self.bridge.source_id = "lib_current", "source_current"
            self.registry["love_house"].star_cls = self.memory
            event = self.event(mode)
            await self.plugin.growth_context(event, self.request())
            if mode == "library": self.bridge.library_id = "lib_other"
            elif mode == "source": self.bridge.source_id = "source_other"
            else: self.registry["love_house"].star_cls = SimpleNamespace(_route=Mock(return_value=self.bridge))
            result = json.loads(await self.plugin.dafeiyu_reflect_experience(event, ["memory_1"], "猜测", "建议"))
            self.assertFalse(result["ok"], mode)

    async def test_explicit_feedback_changes_real_policy_and_flags_next_round_only_in_its_library(self):
        event = self.event("feedback", "简短点，少反问，先听我说完，少发表情。")
        await self.plugin._observe_growth_feedback(event)
        policy = event.get_extra("dafeiyu_reply_policy")
        self.assertEqual((policy["response_mode"], policy["max_bubbles"]), ("single", 1))
        self.assertTrue(event.get_extra("dafeiyu_growth_no_auto_sticker"))
        next_event = self.event("next", "我还想说几句话。")
        raw = dict(next_event.get_extra("dafeiyu_reply_policy"))
        await self.plugin._apply_growth_policy(next_event, next_event.get_extra("dafeiyu_reply_policy"))
        updated = next_event.get_extra("dafeiyu_reply_policy")
        self.assertGreater(updated["collection_seconds"], raw["collection_seconds"])
        self.assertLessEqual(updated["collection_seconds"], updated["collection_max_wait_seconds"])
        self.bridge.library_id = "lib_other"
        other = self.event("other")
        await self.plugin._apply_growth_policy(other, other.get_extra("dafeiyu_reply_policy"))
        self.assertFalse(other.get_extra("dafeiyu_growth_no_auto_sticker"))

    async def test_fixed_and_urgent_waits_are_never_overridden_by_learned_listen_preference(self):
        for mode, reasons, delay in (("fixed", ["fixed_wait"], 10), ("adaptive", ["command_or_urgent"], 0)):
            event = self.event(mode)
            policy = {"mode": mode, "reasons": reasons, "collection_seconds": delay,
                      "collection_max_wait_seconds": 30, "style_hint": "", "response_mode": "auto", "max_bubbles": 3}
            self.plugin._explicit_growth_policy(event, policy, {"wait": "more"})
            self.assertEqual(policy["collection_seconds"], delay)

    async def test_proactive_reuses_only_actual_packet_without_synthetic_incoming_or_reflection(self):
        self.plugin._growth.observe_feedback(OWNER, BOT, UMO, "feedback", "少发表情，简短点", "lib_current")
        note, prefs = await self.plugin._growth_proactive_context(OWNER, BOT, UMO, "companion_real1", material())
        self.assertIn("表达偏好", note)
        self.assertEqual((prefs["stickers"], prefs["length"]), ("less", "short"))
        with self.assertRaises(ValueError):
            self.plugin._growth.propose(OWNER, BOT, UMO, "companion_real1", ["memory_1"], "猜测", "建议")
        self.memory._route.assert_not_called()
        self.assertEqual(self.memory._companion_bridge.call_count, 2)  # authorization rechecked before using note
        self.memory._companion_bridge.assert_called_with(UMO)

    async def test_growth_hot_off_during_session_permission_await_is_rechecked(self):
        event = self.event("hot-growth-off")
        entered, release = asyncio.Event(), asyncio.Event()
        async def waiting(_origin):
            entered.set()
            await release.wait()
            return True
        self.plugin._session_allows.side_effect = waiting
        task = asyncio.create_task(self.plugin._growth_allows(event))
        await entered.wait()
        self.plugin.config["growth_enabled"] = False
        release.set()
        self.assertFalse(await task)

    async def test_memory_disable_during_context_read_cannot_expose_tool_or_old_note(self):
        event, req = self.event("context-race"), self.request()
        self.plugin._growth.observe_feedback(OWNER, BOT, UMO, "feedback", "简短点", "lib_current")
        entered, release = threading.Event(), threading.Event()
        original_context = self.plugin._growth.context
        def waiting(*args):
            entered.set()
            if not release.wait(2): raise AssertionError("test did not release context")
            return original_context(*args)
        with patch.object(self.plugin._growth, "context", side_effect=waiting):
            task = asyncio.create_task(self.plugin.growth_context(event, req))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            self.registry["love_house"].activated = False
            release.set()
            await task
        self.assertIsNone(req.func_tool.get_tool(self.own_tool.name))
        self.assertNotIn("<dafeiyu_growth_material>", req.system_prompt)

    async def test_group_auto_to_quiet_does_not_inherit_name_based_pending_invitation(self):
        settings = {**self.plugin._options(), **OPTIONS, "group_reply_mode": "auto", "reply_timing_mode": "fixed", "collection_seconds": .05}
        self.plugin._options = lambda: dict(settings)
        first = Event("name", "小鱼，先听我说", group=True)
        pending = asyncio.create_task(self.plugin.collect_messages(first))
        await asyncio.sleep(0)
        settings["group_reply_mode"] = "quiet"
        second = Event("new", "我还有一点要说", group=True)
        await self.plugin.collect_messages(second)
        await asyncio.wait_for(pending, .5)
        self.assertTrue(first.stopped)
        self.assertEqual(second.get_extra(main.GROUP_KEY), "silent")
        self.assertEqual(self.plugin._collector._pending, {})


if __name__ == "__main__":
    unittest.main()
