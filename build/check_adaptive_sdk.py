"""Actual AstrBot 4.29.x events/pipeline and dynamic collection; offline spies."""
from pathlib import Path
import asyncio
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="aiwu-adaptive-sdk-")
os.environ["ASTRBOT_ROOT"] = TEMP.name
os.chdir(TEMP.name)

import astrbot
from astrbot.core.message.components import Plain, Image, At
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.platform.astr_message_event import AstrMessageEvent, event_context
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember, Group
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.process_stage.method.star_request import StarRequestSubStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata

plugin = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion"
spec = importlib.util.spec_from_file_location("_adaptive_sdk_companion", plugin / "__init__.py", submodule_search_locations=[str(plugin)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
turns = importlib.import_module(spec.name + ".turn_taking")
adaptive = importlib.import_module(spec.name + ".adaptive")
OWNER, BOT = "123456789", "987654321"


class Event(AstrMessageEvent):
    def __init__(self, mid, text, *, group=False, sender=OWNER, components=None):
        obj = AstrBotMessage()
        obj.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        obj.self_id, obj.message_id = BOT, mid
        obj.sender = MessageMember(sender, "SDK tester")
        obj.message_str, obj.message, obj.raw_message = text, components or [Plain(text)], {}
        obj.group = Group("11111111") if group else None
        obj.session_id = "11111111" if group else sender
        self.sent = []
        super().__init__(text, obj, PlatformMetadata("aiocqhttp", "offline fake adapter", id="qq-sdk"), obj.session_id)
        self.is_at_or_wake_command = True
        self.is_wake = True

    async def send(self, message):
        self.sent.append(message)
        self._has_send_oper = True


async def run():
    now = [0.0]
    controller = adaptive.AdaptiveController(clock=lambda: now[0])
    collector = turns.MessageCollector(Plain, clock=lambda: now[0])
    options = {"message_collection_enabled": True, "collection_seconds": 10,
        "collection_max_wait_seconds": 30, "collection_max_messages": 12, "collection_max_chars": 6000,
        "reply_timing_mode": "adaptive", "persona_profile": "natural", "group_reply_mode": "auto",
        "group_wake_words": "大肥鱼,小鱼"}
    host = {"provider_settings": {"enable": True}, "provider_tts_settings": {"enable": False}, "t2i": False,
        "platform_settings": {"segmented_reply": {"enable": False, "only_llm_result": True,
                              "interval_method": "random", "log_base": 2.6, "interval": "0,0"}, "reply_with_mention": False,
                              "reply_with_quote": False, "forward_threshold": 1500}}

    async def allowed():
        return True

    async def collect(event):
        plan = controller.predict(event, options)
        admission = controller.admit(event, options, {})
        event.set_extra(turns.GROUP_KEY, admission)
        if admission == "silent":
            event.is_at_or_wake_command = False
            event.call_llm = True
            return
        settings = {**options, **{key: plan[key] for key in ("collection_seconds", "collection_max_wait_seconds")}}
        if not await collector.collect(event, settings, allowed):
            event.stop_event()

    module = "_adaptive_offline_handler"
    star_map[module] = StarMetadata(name="adaptive-offline-spy", activated=True)
    handler = StarHandlerMetadata(EventType.AdapterMessageEvent, "adaptive_collect", "collect", module, collect, [])
    process = ProcessStage()
    process.ctx = SimpleNamespace(astrbot_config=host)
    process.star_request_sub_stage = StarRequestSubStage()
    calls = []

    class AgentSpy:
        async def process(self, event):
            calls.append(event)
            event.set_result(MessageEventResult().message("这是离线适配器记录器中的唯一回复。"))
            yield

    process.agent_sub_stage = AgentSpy()
    respond = RespondStage()
    await respond.initialize(SimpleNamespace(astrbot_config=host))

    async def ordinary(event):
        event.set_extra("activated_handlers", [handler])
        event.set_extra("handlers_parsed_params", {})
        with event_context(event):
            async for _ in process.process(event):
                if event.get_result():
                    await respond.process(event)

    # Same genuine event chain: a bare image initially waits for its caption;
    # the complete caption shortens that already-live timer, no requeue needed.
    image = Image.fromURL("https://example.invalid/offline-never-downloaded.png")
    first = Event("image", "", components=[image])
    last = Event("question", "这张图片里是什么？")
    media = Path(TEMP.name) / "managed-existing-media.png"
    media.write_bytes(b"offline-test-owned-by-native-event")
    first.track_temporary_local_file(str(media))
    task1 = asyncio.create_task(ordinary(first))
    for _ in range(4):
        await asyncio.sleep(0)
    assert first.get_extra(adaptive.POLICY_KEY)["collection_seconds"] >= 4.5
    now[0] = .5
    task2 = asyncio.create_task(ordinary(last))
    for _ in range(4):
        await asyncio.sleep(0)
    assert last.get_extra(adaptive.POLICY_KEY)["collection_seconds"] < 2
    now[0] = 3.0  # earlier than the original bare-image deadline
    for batch in collector._pending.values():
        batch.changed.set()
    await asyncio.wait_for(asyncio.gather(task1, task2), .5)
    assert calls == [last] and not first.sent and len(last.sent) == 1
    assert last.message_str == "\n这张图片里是什么？"
    assert last.get_messages()[0] is image
    assert not media.exists(), "SDK final event must still own and clean the transferred media"
    assert last.get_result() is None

    # Actual SDK enum/At components, with no network sends/model requests.
    addressed = Event("addressed", "猫咪为什么喜欢纸箱？", group=True,
        components=[At(qq=BOT), Plain("猫咪为什么喜欢纸箱？")])
    controller.predict(addressed, options)
    addressed.set_extra(turns.GROUP_KEY, controller.admit(addressed, options, {}))
    assert addressed.get_extra(turns.GROUP_KEY) == "addressed"
    followup = Event("followup", "那猫咪为什么还会钻纸箱呢？", group=True)
    controller.predict(followup, options)
    assert controller.admit(followup, options, {}) == "silent"
    controller.mark_sent(addressed)
    assert controller.admit(followup, options, {}) == "auto_followup"
    other = Event("other", "那猫咪为什么还会钻纸箱呢？", group=True, sender="22222222")
    assert controller.admit(other, options, {}) == "silent"
    collector.close()
    controller.close()
    version = getattr(astrbot, "__version__", None)
    if not version:
        from importlib.metadata import version as distribution_version
        version = distribution_version("astrbot")
    print(json.dumps({"sdk_version": version, "real_ProcessStage": True, "real_RespondStage": True,
        "adaptive_timer_shortens_live_real_event_batch": True, "single_normal_Agent_entry": True,
        "exact_media_identity_and_native_cleanup": True, "native_enum_group_followup_scope": True,
        "real_QQ_or_model_calls": False}))


try:
    asyncio.run(run())
finally:
    os.chdir(ROOT)
    TEMP.cleanup()
