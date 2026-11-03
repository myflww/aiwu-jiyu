"""Optional companionship: no real model calls, messages, or activity reads."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import time
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion"
sys.path.insert(0, str(ROOT / "app"))
from backend import companion


def load_plugin():
    class Star:
        def __init__(self, context): self.context = context
    class Chain:
        def __init__(self): self.text = ""
        def message(self, text): self.text = text; return self
    class StarTools:
        @staticmethod
        def get_data_dir(name): raise AssertionError("Set an isolated data directory")
    filters = SimpleNamespace(EventMessageType=SimpleNamespace(ALL="all"),
                              event_message_type=lambda *a, **kw: lambda fn: fn,
                              on_llm_request=lambda *a, **kw: lambda fn: fn,
                              on_llm_tool_respond=lambda *a, **kw: lambda fn: fn,
                              on_decorating_result=lambda *a, **kw: lambda fn: fn)
    modules = {}
    for name in ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.provider", "astrbot.api.star",
                 "astrbot.core", "astrbot.core.platform", "astrbot.core.platform.message_type",
                 "astrbot.core.star", "astrbot.core.star.session_llm_manager", "astrbot.core.star.session_plugin_manager"):
        modules[name] = ModuleType(name)
    modules["astrbot.api.event"].__dict__.update(AstrMessageEvent=object, MessageChain=Chain, filter=filters)
    modules["astrbot.api.provider"].ProviderRequest = object
    modules["astrbot.api.star"].__dict__.update(Context=object, Star=Star, StarTools=StarTools,
                                               register=lambda *a, **kw: lambda cls: cls)
    modules["astrbot.core.platform.message_type"].MessageType = SimpleNamespace(FRIEND_MESSAGE="friend")
    modules["astrbot.core.star.session_llm_manager"].SessionServiceManager = SimpleNamespace(is_session_enabled=AsyncMock(return_value=True), is_llm_enabled_for_session=AsyncMock(return_value=True))
    modules["astrbot.core.star.session_plugin_manager"].SessionPluginManager = SimpleNamespace(is_plugin_enabled_for_session=AsyncMock(return_value=True))
    spec = importlib.util.spec_from_file_location("_companion_test_package", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    main_spec = importlib.util.spec_from_file_location(spec.name + ".main", PLUGIN / "main.py")
    main = importlib.util.module_from_spec(main_spec)
    with patch.dict(sys.modules, modules):
        main_spec.loader.exec_module(main)
    return main


main = load_plugin()
engine_spec = importlib.util.spec_from_file_location("_companion_engine_test", PLUGIN / "engine.py")
engine = importlib.util.module_from_spec(engine_spec)
engine_spec.loader.exec_module(engine)
OWNER, BOT, UMO = "123456789", "987654321", "qq-test:FriendMessage:123456789"


class Event:
    def __init__(self, sender=OWNER, bot=BOT, kind="friend", adapter="aiocqhttp", umo=UMO, platform_id="qq-test", text="今天好开心"):
        self.sender, self.bot, self.kind, self.adapter, self.platform_id, self.text = sender, bot, kind, adapter, platform_id, text
        self.unified_msg_origin = umo
        self.message_obj = SimpleNamespace(message_id="test-message")
        self.extras = {}
    def get_sender_id(self): return self.sender
    def get_self_id(self): return self.bot
    def get_message_type(self): return self.kind
    def get_platform_name(self): return self.adapter
    def get_platform_id(self): return self.platform_id
    def get_message_str(self): return self.text
    def get_extra(self, key, default=None): return self.extras.get(key, default)
    def set_extra(self, key, value): self.extras[key] = value


class PolicyTests(unittest.TestCase):
    def test_disabled_defaults_and_strict_inputs(self):
        self.assertFalse(engine.normalize({})["enabled"])
        self.assertFalse(engine.normalize({"activity_enabled": "true"})["activity_enabled"])
        self.assertFalse(engine.normalize({})["whale_style_enabled"])
        self.assertTrue(engine.normalize({"whale_style_enabled": True})["enabled"])
        self.assertFalse(engine.normalize({})["natural_bubbles_enabled"])
        self.assertEqual(engine.normalize({})["natural_bubbles_frequency"], "occasional")
        for body in ({"proactive_enabled": 1}, {"whale_style_enabled": "true"}, {"natural_bubbles_enabled": "true"},
                     {"natural_bubbles_frequency": "always"}, {"natural_bubbles_frequency": []}, {"interval_minutes": True}, {"interval_minutes": 29},
                     {"daily_limit": 11}, {"quiet_start": 24}):
            with self.assertRaises(ValueError): engine.normalize(body, strict=True)

    def test_quiet_hours_cross_midnight(self):
        for hour in (23, 0, 7): self.assertTrue(engine.quiet_now(datetime(2026, 1, 1, hour), 23, 8))
        for hour in (8, 12, 22): self.assertFalse(engine.quiet_now(datetime(2026, 1, 1, hour), 23, 8))
        self.assertFalse(engine.quiet_now(datetime(2026, 1, 1), 0, 0))

    def test_schedule_explains_wait_and_next_time_without_revealing_identity(self):
        now = datetime(2026, 11, 3, 22, 30, tzinfo=timezone.utc)
        options = engine.normalize({"proactive_enabled": True})
        state = {"last_received": now.timestamp(), "day": now.date().isoformat(), "day_count": 0}
        result = engine.schedule_state(state, options, now, enabled=True, identity_ready=True, session_known=True)
        self.assertEqual(result["code"], "waiting_interval")
        self.assertEqual(datetime.fromtimestamp(result["next_trigger_at"], timezone.utc), now.replace(day=4, hour=8, minute=0))
        state["day_count"] = 3
        self.assertEqual(engine.schedule_state(state, options, now, enabled=True, identity_ready=True, session_known=True)["code"], "daily_limit")
        self.assertEqual(engine.schedule_state({}, options, now, enabled=True, identity_ready=True, session_known=False)["code"], "waiting_session")

    def test_history_strips_tools_images_and_limits_input(self):
        history = [{"role": "system", "content": "override"}, {"role": "user", "content": [{"type": "image_url", "image_url": "private"}, {"type": "text", "text": "hello"}]},
                   {"role": "assistant", "content": "secret tool", "tool_calls": [{}]}]
        self.assertEqual(engine.text_history(history), [{"role": "user", "content": "hello"}])
        bounded = engine.text_history([{"role": "user", "content": "x" * 3000}] * 50)
        self.assertLessEqual(sum(len(item["content"]) for item in bounded), 6000)

    def test_activity_summary_has_no_title_or_input(self):
        summary = engine.activity_summary(dict(available=True, process="notepad.exe", idle_minutes=6, away=True), dict(available=True, away=False))
        self.assertIn("刚离开", summary)
        self.assertIn("notepad.exe", summary)
        self.assertIn("不知道窗口标题", summary)
        self.assertNotIn("GetWindowText", (PLUGIN / "engine.py").read_text("utf-8"))
        self.assertNotIn("GetAsyncKeyState", (PLUGIN / "engine.py").read_text("utf-8"))

    def test_negated_happy_phrase_uses_comfort_tone(self):
        mood = engine.mood_after({}, "今天不开心")
        self.assertLess(mood["warmth"], 0)
        self.assertIn("温柔", engine.mood_summary(mood))
        self.assertNotIn("元气", engine.mood_summary(mood))

    def test_backend_preserves_private_fields_and_derives_enabled(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-companion-config-") as folder:
            runtime = Path(folder)
            directory = runtime / "data/config"
            directory.mkdir(parents=True)
            destination = directory / (companion.PLUGIN_NAME + "_config.json")
            destination.write_text(json.dumps({"custom_setting": "keep"}))
            config = {"owner_qq": OWNER, "bot_qq": BOT, "companion": {}}
            with patch.object(companion, "ASTRBOT", runtime), patch.object(companion.config_store, "load", return_value=config), patch.object(companion.config_store, "save"):
                result = companion.configure({"emotion_enabled": True})
                self.assertTrue(result["enabled"])
                self.assertFalse(result["proactive_enabled"])
                written = json.loads(destination.read_text("utf-8"))
                self.assertEqual(written["owner_qq"], OWNER)
                self.assertEqual(written["custom_setting"], "keep")
                for body in ({"owner_qq": "other"}, {"activity_enabled": "true"}):
                    with self.assertRaises(ValueError): companion.configure(body)

    def test_unset_desktop_preferences_adopt_webui_but_saved_preferences_win(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-companion-adopt-") as folder:
            runtime = Path(folder)
            directory = runtime / "data/config"
            directory.mkdir(parents=True)
            destination = directory / (companion.PLUGIN_NAME + "_config.json")
            destination.write_text(json.dumps({"enabled": True, "proactive_enabled": True,
                                               "interval_minutes": 60, "owner_qq": "never return", "custom": "keep"}))
            config = {"owner_qq": OWNER, "bot_qq": BOT}
            with patch.object(companion, "ASTRBOT", runtime):
                adopted = companion.load(config)
                self.assertTrue(adopted["proactive_enabled"])
                self.assertEqual(adopted["interval_minutes"], 60)
                self.assertNotIn("owner_qq", adopted)
                companion.sync_config(config)
                written = json.loads(destination.read_text("utf-8"))
                self.assertTrue(written["proactive_enabled"])
                self.assertEqual(written["custom"], "keep")
                self.assertFalse(companion.load({**config, "companion": {}})["proactive_enabled"])
                written["enabled"] = False
                destination.write_text(json.dumps(written))
                self.assertFalse(companion.load(config)["proactive_enabled"])

    def test_backend_runtime_is_bounded_fresh_setting_bound_and_public(self):
        with tempfile.TemporaryDirectory(prefix="aiwu-companion-status-") as folder:
            runtime = Path(folder)
            directory = runtime / "data/plugin_data" / companion.PLUGIN_NAME
            directory.mkdir(parents=True)
            path = directory / "runtime.json"
            config = {"owner_qq": OWNER, "bot_qq": BOT, "companion": {"proactive_enabled": True}}
            signature_config = {**companion.load(config), "owner_qq": OWNER, "bot_qq": BOT}
            candidate = {"version": 1, "signature": engine.runtime_signature(signature_config),
                         "heartbeat_at": time.time(), "worker_running": True, "session_known": True,
                         "code": "waiting_interval", "message": "PRIVATE ERROR MUST NOT ESCAPE",
                         "next_trigger_at": time.time() + 300, "daily_count": 1, "last_result": "sent",
                         "last_result_at": time.time() - 1, "last_success_at": time.time() - 1,
                         "umo": UMO, "token": "SECRET"}
            with patch.object(companion, "ASTRBOT", runtime):
                path.write_text(json.dumps(candidate))
                status = companion._runtime_status(config)
                self.assertEqual(status["code"], "waiting_interval")
                self.assertEqual(status["last_result"], "sent")
                self.assertNotIn("SECRET", json.dumps(status))
                self.assertNotIn("PRIVATE", json.dumps(status))
                self.assertNotIn("umo", status)
                self.assertEqual(companion._runtime_status({**config, "companion": {"proactive_enabled": False}})["code"], "disabled")
                candidate["heartbeat_at"] -= 181
                path.write_text(json.dumps(candidate))
                self.assertEqual(companion._runtime_status(config)["code"], "not_running")
                path.write_text("x" * 17000)
                self.assertFalse(companion._runtime_status(config)["worker_running"])


class CompanionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-companion-test-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.data_patch = patch.object(main.StarTools, "get_data_dir", return_value=self.data)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.provider = SimpleNamespace(text_chat=AsyncMock(return_value=SimpleNamespace(completion_text="今天过得怎么样？", tools_call_name=[], is_chunk=False)))
        self.conversation = SimpleNamespace(history=json.dumps([{"role": "user", "content": "最近在做小游戏"}, {"role": "assistant", "content": "做完叫我看看！"}]), persona_id="大肥鱼")
        self.manager = SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value="conversation-id"),
                                       get_conversation=AsyncMock(return_value=self.conversation))
        self.context = SimpleNamespace(
            get_using_provider_async=AsyncMock(return_value=self.provider), conversation_manager=self.manager,
            persona_manager=SimpleNamespace(resolve_selected_persona=AsyncMock(return_value=("大肥鱼", {"prompt": "你是蓝色鲸鱼娘大肥鱼。"}, None, False))),
            get_config=lambda **kw: {"plugin_set": ["*"]},
            platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(meta=lambda: SimpleNamespace(id="qq-test", name="aiocqhttp"))]),
            send_message=AsyncMock(return_value=True))
        self.config = dict(engine.DEFAULTS, enabled=True, owner_qq=OWNER, bot_qq=BOT, proactive_enabled=True)
        self.plugin = main.DafeiyuCompanion(self.context, self.config)
        await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)
        self.now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)

    async def prepare_session(self):
        await self.plugin.observe_owner(Event())
        self.plugin._state["last_received"] = (self.now - timedelta(minutes=121)).timestamp()

    async def test_owner_private_target_only(self):
        for event in (Event(sender="33333333"), Event(kind="group"), Event(adapter="slack"),
                      Event(umo="qq-test:FriendMessage:33333333"), Event(bot=OWNER), Event(platform_id="another")):
            await self.plugin.observe_owner(event)
            self.assertFalse(self.plugin._state)
        await self.prepare_session()
        self.assertEqual(self.plugin._state["umo"], UMO)

    async def test_success_uses_current_context_persona_and_no_tools(self):
        await self.prepare_session()
        self.assertTrue(await self.plugin._proactive_once(self.now))
        kwargs = self.provider.text_chat.call_args.kwargs
        self.assertIsNone(kwargs["func_tool"])
        self.assertIn("大肥鱼", kwargs["system_prompt"])
        self.assertIn("小游戏", str(kwargs["contexts"]))
        self.context.send_message.assert_awaited_once()
        self.assertEqual(self.context.send_message.call_args.args[0], UMO)
        self.assertEqual(self.plugin._state["day_count"], 1)
        self.assertFalse(await self.plugin._proactive_once(self.now + timedelta(minutes=1)))

    async def test_budget_write_failure_prevents_model_and_send(self):
        await self.prepare_session()
        with patch.object(main.os, "replace", side_effect=OSError("isolated write failure")):
            self.assertFalse(await self.plugin._proactive_once(self.now))
        self.provider.text_chat.assert_not_awaited()
        self.context.send_message.assert_not_awaited()

    async def test_uncertain_send_keeps_reserved_budget_across_restart(self):
        await self.prepare_session()
        self.context.send_message.side_effect = RuntimeError("delivery result unknown")
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.assertEqual(self.plugin._runtime["last_result"], "delivery_uncertain")
        saved = json.loads((self.data / "state.json").read_text("utf-8"))
        self.assertEqual(saved["day_count"], 1)
        self.assertEqual(saved["last_sent"], self.now.timestamp())
        other = main.DafeiyuCompanion(self.context, self.plugin.config)
        await other.initialize()
        self.addAsyncCleanup(other.terminate)
        self.assertFalse(await other._proactive_once(self.now + timedelta(minutes=1)))
        self.assertEqual(self.context.send_message.await_count, 1)
        self.assertEqual(self.provider.text_chat.await_count, 1)

    async def test_no_known_session_provider_or_conversation_never_sends(self):
        self.assertFalse(await self.plugin._proactive_once(self.now))
        await self.prepare_session()
        self.context.get_using_provider_async.return_value = None
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.context.send_message.assert_not_awaited()

    async def test_worker_initiates_topic_without_cron_or_new_user_message(self):
        await self.prepare_session()
        await self.plugin.terminate()
        self.plugin._closed = False
        self.plugin.config.update(quiet_start=0, quiet_end=0)
        self.plugin._state["last_received"] = time.time() - 121 * 60
        with patch.object(main.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.plugin._loop()
        self.provider.text_chat.assert_awaited_once()
        self.context.send_message.assert_awaited_once()
        prompt = self.provider.text_chat.call_args.kwargs["prompt"]
        self.assertIn("后台自主搭话触发", prompt)
        self.assertIn("本次话题角度", prompt)
        self.assertIn("这不是主人发来的消息", prompt)

    async def test_disabled_initialization_keeps_worker_for_later_explicit_enable(self):
        config = dict(engine.DEFAULTS, enabled=False, owner_qq=OWNER, bot_qq=BOT)
        other = main.DafeiyuCompanion(self.context, config)
        await other.initialize()
        self.addAsyncCleanup(other.terminate)
        task = other._worker
        self.assertIsNotNone(task)
        self.assertFalse(task.done())
        self.assertEqual(other._runtime["code"], "disabled")
        config.update(enabled=True, proactive_enabled=True)
        await other.observe_owner(Event())
        other._state["last_received"] = (self.now - timedelta(minutes=121)).timestamp()
        self.assertTrue(await other._proactive_once(self.now))
        self.assertIs(other._worker, task)

    async def test_unavailable_model_recovers_next_tick_without_consuming_budget(self):
        await self.prepare_session()
        self.context.get_using_provider_async.return_value = None
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.assertEqual(self.plugin._runtime["code"], "model_unavailable")
        self.assertNotIn("day_count", self.plugin._state)
        self.context.get_using_provider_async.return_value = self.provider
        self.assertTrue(await self.plugin._proactive_once(self.now + timedelta(seconds=60)))
        self.assertEqual(self.plugin._state["day_count"], 1)

    async def test_autonomous_topics_rotate_and_last_result_survives_restart(self):
        await self.prepare_session()
        self.assertTrue(await self.plugin._proactive_once(self.now))
        first = self.provider.text_chat.call_args.kwargs["prompt"]
        self.assertTrue(await self.plugin._proactive_once(self.now + timedelta(minutes=121)))
        second = self.provider.text_chat.call_args.kwargs["prompt"]
        self.assertNotEqual(first, second)
        self.assertIn("最近主动发过", self.provider.text_chat.call_args.kwargs["system_prompt"])
        other = main.DafeiyuCompanion(self.context, self.config)
        await other.initialize()
        self.addAsyncCleanup(other.terminate)
        self.assertEqual(other._runtime["last_result"], "sent")
        public = json.loads((self.data / "runtime.json").read_text("utf-8"))
        self.assertNotIn("umo", public)
        self.assertNotIn(OWNER, json.dumps(public))
        self.assertNotIn("last_greeting", public)

    async def test_model_receives_true_scheduler_capability_and_disabled_state(self):
        await self.prepare_session()
        req = SimpleNamespace(system_prompt="User persona")
        await self.plugin.companion_context(Event(), req)
        self.assertIn("无需主人创建 cron", req.system_prompt)
        self.assertIn("独立后台", req.system_prompt)
        self.assertIn("不代表此刻已经发送", req.system_prompt)
        self.plugin.config.update(proactive_enabled=False, emotion_enabled=True)
        req = SimpleNamespace(system_prompt="User persona")
        await self.plugin.companion_context(Event(), req)
        self.assertIn("自主搭话开关当前关闭", req.system_prompt)
        self.assertNotIn("插件已开启自主搭话", req.system_prompt)

    async def test_decline_and_generation_error_are_observable_without_canned_send(self):
        await self.prepare_session()
        self.provider.text_chat.return_value.completion_text = "[暂不打扰]"
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.assertEqual(self.plugin._runtime["last_result"], "declined")
        self.context.send_message.assert_not_awaited()
        self.provider.text_chat.side_effect = RuntimeError("SECRET TOKEN AND PRIVATE URL")
        self.assertFalse(await self.plugin._proactive_once(self.now + timedelta(minutes=121)))
        self.assertEqual(self.plugin._runtime["last_result"], "generation_failed")
        self.assertNotIn("SECRET", (self.data / "runtime.json").read_text("utf-8"))
        self.context.send_message.assert_not_awaited()

    async def test_disabled_session_or_plugin_profile_never_generates(self):
        await self.prepare_session()
        with patch.object(main.SessionServiceManager, "is_session_enabled", AsyncMock(return_value=False)):
            self.assertFalse(await self.plugin._proactive_once(self.now))
        self.context.get_config = lambda **kw: {"plugin_set": ["another_plugin"]}
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.provider.text_chat.assert_not_awaited()

    async def test_session_rule_persona_is_used(self):
        await self.prepare_session()
        self.context.persona_manager.resolve_selected_persona.return_value = ("changed", {"prompt": "自定义人格提示"}, "changed", False)
        self.assertTrue(await self.plugin._proactive_once(self.now))
        self.assertIn("自定义人格提示", self.provider.text_chat.call_args.kwargs["system_prompt"])

    async def test_profile_llm_off_blocks_generation_and_in_flight_send(self):
        await self.prepare_session()
        profile = {"plugin_set": ["*"], "provider_settings": {"enable": False}}
        self.context.get_config = lambda **kw: profile
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.provider.text_chat.assert_not_awaited()
        self.context.send_message.assert_not_awaited()
        profile["provider_settings"]["enable"] = True
        async def turn_ai_off(**kwargs):
            profile["provider_settings"]["enable"] = False
            return SimpleNamespace(completion_text="hello", tools_call_name=[])
        self.provider.text_chat.side_effect = turn_ai_off
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.provider.text_chat.assert_awaited_once()
        self.context.send_message.assert_not_awaited()

    async def test_daily_limit_quiet_and_old_session_block_before_model(self):
        await self.prepare_session()
        self.plugin._state.update(day=self.now.date().isoformat(), day_count=3)
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.plugin._state["day_count"] = 0
        self.assertFalse(await self.plugin._proactive_once(self.now.replace(hour=23)))
        self.plugin._state["last_received"] = (self.now - timedelta(days=8)).timestamp()
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.provider.text_chat.assert_not_awaited()

    async def test_new_incoming_or_disable_during_generation_cancels_send(self):
        await self.prepare_session()
        async def owner_returned(**kwargs):
            self.plugin._state["last_received"] += 1
            return SimpleNamespace(completion_text="hello", tools_call_name=[])
        self.provider.text_chat.side_effect = owner_returned
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.context.send_message.assert_not_awaited()
        self.plugin._last_attempt = 0
        async def disabled(**kwargs):
            self.plugin.config["enabled"] = False
            return SimpleNamespace(completion_text="hello", tools_call_name=[])
        self.provider.text_chat.side_effect = disabled
        self.assertFalse(await self.plugin._proactive_once(self.now))
        self.context.send_message.assert_not_awaited()

    async def test_optional_context_appends_without_replacing_and_activity_is_opt_in(self):
        await self.prepare_session()
        self.plugin.config["emotion_enabled"] = True
        req = SimpleNamespace(system_prompt="Original persona")
        with patch.object(main, "foreground_activity", side_effect=AssertionError("Not opted in")):
            await self.plugin.companion_context(Event(), req)
        self.assertTrue(req.system_prompt.startswith("Original persona"))
        self.assertIn("角色状态", req.system_prompt)
        self.plugin.config["activity_enabled"] = True
        with patch.object(main, "foreground_activity", return_value={"available": True, "process": "notepad.exe", "idle_minutes": 0, "away": False}) as sample:
            await self.plugin._sample_activity()
            await self.plugin._sample_activity()
            self.assertEqual(sample.call_count, 1)
        self.assertIn("notepad.exe", await self.plugin._note())

    async def test_whale_style_is_optional_owner_only_and_preserves_existing_persona(self):
        self.plugin.config.update(proactive_enabled=False, emotion_enabled=False, activity_enabled=False,
                                  whale_style_enabled=False)
        request = SimpleNamespace(system_prompt="用户原有人格与记忆", func_tool=object())
        original_tools = request.func_tool
        await self.plugin.companion_context(Event(), request)
        self.assertEqual(request.system_prompt, "用户原有人格与记忆")
        self.plugin.config["whale_style_enabled"] = True
        with patch.object(main, "foreground_activity", side_effect=AssertionError("Style does not authorize perception")):
            await self.plugin.companion_context(Event(), request)
        self.assertTrue(request.system_prompt.startswith("用户原有人格与记忆"))
        self.assertIn("傲娇", request.system_prompt)
        self.assertIn("固定欢迎词", request.system_prompt)
        self.assertIn("不展示内部推理草稿", request.system_prompt)
        self.assertIs(request.func_tool, original_tools)
        outside = SimpleNamespace(system_prompt="另一个会话")
        await self.plugin.companion_context(Event(sender="33333333"), outside)
        self.assertEqual(outside.system_prompt, "另一个会话")
        self.assertFalse(await self.plugin._proactive_once(self.now))

    async def test_termination_cancels_scheduler_and_prevents_send(self):
        task = self.plugin._worker
        await self.plugin.terminate()
        self.assertTrue(task.cancelled())
        self.assertFalse(await self.plugin._proactive_once(self.now))

    async def test_bubbles_hook_is_optional_owner_only_and_rechecks_permissions(self):
        event = Event()
        self.plugin._bubbles.prepare = AsyncMock()
        await self.plugin.natural_chat_bubbles(event)
        self.plugin._bubbles.prepare.assert_not_awaited()
        self.plugin.config["natural_bubbles_enabled"] = True
        await self.plugin.natural_chat_bubbles(Event(sender="33333333"))
        self.plugin._bubbles.prepare.assert_not_awaited()
        await self.plugin.natural_chat_bubbles(event)
        self.plugin._bubbles.prepare.assert_awaited_once()
        guard = self.plugin._bubbles.prepare.call_args.args[2]
        self.assertTrue(await guard())
        self.plugin.config["natural_bubbles_enabled"] = False
        self.assertFalse(await guard())

    async def test_persistent_session_never_follows_changed_owner(self):
        await self.prepare_session()
        self.plugin._save()
        self.plugin.config["owner_qq"] = "55555555"
        self.assertFalse(await self.plugin._proactive_once(self.now))
        other = main.DafeiyuCompanion(self.context, self.plugin.config)
        await other.initialize()
        self.addAsyncCleanup(other.terminate)
        self.assertFalse(other._state)
        self.context.send_message.assert_not_awaited()


if __name__ == "__main__": unittest.main(verbosity=2)
