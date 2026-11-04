"""AstrBot real ProcessStage/RespondStage spies; no QQ/network/model calls."""
from pathlib import Path
import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="aiwu-turn-sdk-")
os.environ["ASTRBOT_ROOT"] = TEMP.name
os.chdir(TEMP.name)
import astrbot
from astrbot.api.event import MessageChain
from astrbot.core.message.components import Plain, Image, At, AtAll, Reply
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.platform.astr_message_event import AstrMessageEvent, event_context
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember, Group
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.process_stage.method.star_request import StarRequestSubStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.star.star import star_map, star_registry, StarMetadata
from astrbot.core.star.context import Context
from astrbot.core.conversation_mgr import ConversationManager
from astrbot.core.db.po import ConversationV2
from astrbot.core.agent.message import UserMessageSegment
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry

plugin = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion"
spec = importlib.util.spec_from_file_location("_turn_sdk_companion", plugin / "__init__.py", submodule_search_locations=[str(plugin)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from importlib import import_module
main = import_module(spec.name + ".main")
natural = import_module(spec.name + ".natural_bubbles")

OWNER, BOT = "123456789", "987654321"
TEXT = ("今天的小尾巴已经准备好陪你聊天啦，我们可以先慢慢把想做的事情整理清楚。"
        "不用一下子完成全部，先挑一件最想做的小事，认真开始就已经很棒啦。"
        "等这件事情做好以后，我们再一起吃口米饭休息一下，你有什么想法都可以告诉我。")
SHORT_TEXT = "今天的小尾巴已经准备好陪你一起聊天啦。刚才看到一朵像鲸鱼的小云，忍不住想分享给你。"


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
        super().__init__(text, obj, PlatformMetadata("aiocqhttp", "SDK fake adapter", id="qq-sdk"), obj.session_id)
        self.is_at_or_wake_command = True
        self.is_wake = True
    async def send(self, message):
        self.sent.append(message)
        self._has_send_oper = True


async def run():
    host = {"wake_prefix": ["/fish"], "provider_settings": {"enable": True, "streaming_response": False, "display_reasoning_text": False},
            "provider_tts_settings": {"enable": False}, "t2i": False,
            "platform_settings": {"segmented_reply": {"enable": False, "only_llm_result": True, "interval_method": "random", "log_base": 2.6, "interval": "0,0"},
                                  "reply_with_mention": False, "reply_with_quote": False, "forward_threshold": 1500}}
    context = SimpleNamespace(get_config=lambda **kw: host)
    instance = main.DafeiyuCompanion(context, {"enabled": True, "owner_qq": OWNER, "bot_qq": BOT,
              "natural_bubbles_enabled": True, "collection_seconds": 1})
    instance._session_allows = AsyncMock(return_value=True)
    instance._bubbles._selected = lambda *args: True
    instance._bubbles._delay = lambda *args: 0
    options = instance._options
    instance._options = lambda: {**options(), "collection_seconds": .025, "collection_max_wait_seconds": .1}
    # Real SDK Plain is pydantic.v1: a shallow Python copy aliases __dict__.
    # Regression asserts source and all planned packet objects are independent.
    raw = MessageChain([Plain(TEXT)])
    packets, _ = natural._plan(raw)
    assert len(packets) == 2 and raw.chain[0].text == TEXT
    assert packets[0].chain[0].text != packets[1].chain[0].text
    assert "".join(packet.chain[0].text for packet in packets) == TEXT
    md = star_map.get(main.__name__)
    if md is None:
        md = StarMetadata(name=main.PLUGIN_NAME)
        star_map[main.__name__] = md
    md.activated = True
    md.star_cls = instance
    if not any(item is md for item in star_registry):
        star_registry.append(md)
    context.get_registered_star = lambda name: Context.get_registered_star(context, name)
    handler = StarHandlerMetadata(EventType.AdapterMessageEvent, "sdk_collection", "collect_messages", main.__name__, instance.collect_messages, [])
    process = ProcessStage()
    process.ctx = SimpleNamespace(astrbot_config=host)
    process.star_request_sub_stage = StarRequestSubStage()
    calls = []
    class AgentSpy:
        async def process(self, event):
            calls.append(event)
            result = MessageEventResult().message(event.get_extra("sdk_response_text", TEXT))
            result.result_content_type = ResultContentType.LLM_RESULT
            event.set_result(result)
            yield
    process.agent_sub_stage = AgentSpy()
    respond = RespondStage()
    await respond.initialize(SimpleNamespace(astrbot_config=host))
    # Registered after-send hooks are not needed for fake bridge spies here.
    for metadata in list(star_handlers_registry._handlers):
        if metadata.handler_module_path == main.__name__:
            metadata.enabled = False
    async def ordinary(event):
        event.set_extra("activated_handlers", [handler])
        event.set_extra("handlers_parsed_params", {})
        with event_context(event):
            async for _ in process.process(event):
                if event.get_result():
                    await instance.natural_chat_bubbles(event)
                    await respond.process(event)
    image = Image.fromURL("https://example.invalid/test.png")
    old = Event("old", "看图", components=[Plain("看图"), image])
    newest = Event("new", "这个好可爱")
    media = Path(TEMP.name) / "owned-media.png"
    media.write_bytes(b"isolated-test")
    old.track_temporary_local_file(str(media))
    first = asyncio.create_task(ordinary(old))
    await asyncio.sleep(.005)
    second = asyncio.create_task(ordinary(newest))
    await asyncio.gather(first, second)
    assert calls == [newest], [event.message_obj.message_id for event in calls]
    assert newest.message_str == "看图\n这个好可爱"
    assert newest.get_messages()[1] is image
    assert not media.exists(), "Transferred media must be cleaned once continuing SDK event finishes"
    assert len(old.sent) == 0 and len(newest.sent) == 2
    received_text = "".join(part.text for packet in newest.sent for part in packet.chain if isinstance(part, Plain))
    assert received_text == TEXT, {"expected": TEXT, "actual": received_text, "packets": [packet.get_plain_text() for packet in newest.sent]}
    assert newest.get_result() is None, "Native RespondStage must own result cleanup"
    # Repeated decorate/respond on the same final event cannot append the
    # entire original reply after the two already accepted QQ packets.
    repeat = MessageEventResult().message(TEXT)
    repeat.result_content_type = ResultContentType.LLM_RESULT
    newest.set_result(repeat)
    await instance.natural_chat_bubbles(newest)
    await respond.process(newest)
    assert len(newest.sent) == 2
    # At-all and unrelated group traffic never call the normal Agent.
    before = len(calls)
    silent = Event("group-silent", "大家好", group=True, components=[AtAll(), Plain("大家好")])
    await ordinary(silent)
    assert len(calls) == before and not silent.sent
    explicit = Event("group-direct", "大肥鱼一起聊聊", group=True, components=[At(qq=BOT), Plain("一起聊聊")])
    await ordinary(explicit)
    assert calls[-1] is explicit and len(explicit.sent) == 2
    formal = Event("formal", "请写一份完整报告")
    await ordinary(formal)
    assert len(formal.sent) == 1 and formal.sent[0].get_plain_text() == TEXT
    assert 40 <= len(SHORT_TEXT) <= 70
    short_chat = Event("short-casual", "一起聊聊今天的小云朵")
    short_chat.set_extra("sdk_response_text", SHORT_TEXT)
    await ordinary(short_chat)
    short_packets = [packet.get_plain_text() for packet in short_chat.sent]
    assert len(short_packets) == 2 and short_packets[0] != short_packets[1], short_packets
    assert "".join(short_packets) == SHORT_TEXT
    assert all(len(part.strip()) >= 12 for part in short_packets)
    short_formal = Event("short-formal", "这是重要的正式内容，请一条发完，不要分条")
    short_formal.set_extra("sdk_response_text", SHORT_TEXT)
    await ordinary(short_formal)
    assert len(short_formal.sent) == 1 and short_formal.sent[0].get_plain_text() == SHORT_TEXT
    quiet = Event("quiet", "我先忙了，不用回。")
    conv = ConversationV2(conversation_id="quiet-cid", platform_id="qq-sdk", user_id=quiet.unified_msg_origin,
        content=[{"role": "user", "content": "真实的上一句"}], created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    class DB:
        async def get_conversation_by_id(self, *, cid): return conv
        async def update_conversation(self, *, cid, content, **kwargs): conv.content = content
    manager = ConversationManager(DB())
    manager.session_conversations[quiet.unified_msg_origin] = "quiet-cid"
    context.conversation_manager = manager
    count = len(calls)
    await ordinary(quiet)
    assert len(calls) == count and not quiet.sent and quiet.get_extra("dafeiyu_no_reply_history") == "saved"
    assert conv.content[-1] == {"role": "user", "content": "我先忙了，不用回。"}
    UserMessageSegment.model_validate(conv.content[-1])
    # If a user explicitly chooses native segmentation, exactly one engine
    # owns it; our optional splitter must not wrap/deliver a second copy.
    native_host = {**host, "platform_settings": {**host["platform_settings"],
        "segmented_reply": {**host["platform_settings"]["segmented_reply"], "enable": True}}}
    native_event = Event("native", "一起聊聊")
    native_result = MessageEventResult(chain=[Plain(part) for part in natural.split_text(TEXT)])
    native_result.result_content_type = ResultContentType.LLM_RESULT
    native_event.set_result(native_result)
    assert not await instance._bubbles.prepare(native_event, instance._options(), AsyncMock(return_value=True), native_host)
    native_stage = RespondStage()
    await native_stage.initialize(SimpleNamespace(astrbot_config=native_host))
    await native_stage.process(native_event)
    assert len(native_event.sent) == 2
    assert "".join(packet.get_plain_text() for packet in native_event.sent) == TEXT
    # Commands stay immediate and don't acquire a collection timer.
    command = Event("cmd", "/help")
    await instance.collect_messages(command)
    assert command.get_extra("dafeiyu_collection_status") == "immediate"
    # Native activation and a validated explicit hot master change are
    # separate gates, exercised with the real Context registry API.
    md.activated = False
    assert not instance._master_enabled()
    md.activated = True
    config_path = Path(TEMP.name) / "data/config/companion.json"
    config_path.parent.mkdir(parents=True)
    instance._config_path = config_path
    instance.config["enabled"] = False
    settings = {**instance.config, "enabled": False}
    config_path.write_text(json.dumps(settings), encoding="utf-8")
    assert not instance._master_enabled()
    settings["enabled"] = True
    config_path.write_text(json.dumps(settings), encoding="utf-8")
    assert instance._master_enabled() and not instance.config["enabled"]
    config_path.write_text("broken", encoding="utf-8")
    assert not instance._master_enabled()
    config_path.unlink()
    assert not instance._master_enabled()
    instance._collector.close()
    instance._turns.close()
    instance._bubbles.close()
    print(json.dumps({"sdk_version": astrbot.__version__, "real_ProcessStage": True,
        "one_normal_agent_call_for_collected_turn": True, "real_RespondStage_multiple_QQ_packets": True,
        "no_full_reply_after_split": True, "ordered_media_and_native_cleanup": True,
        "group_addressed_hard_gate": True, "real_pydantic_component_independence": True,
        "formal_response_whole": True, "native_segmentation_single_owner": True,
        "short_40_to_70_char_reply_two_actual_packets": True,
        "same_short_formal_reply_one_actual_packet": True,
        "explicit_no_reply_real_user_history": True,
        "native_inactive_and_explicit_hot_master": True,
        "real_QQ_or_model_calls": False}))


try:
    asyncio.run(run())
finally:
    os.chdir(ROOT)
    TEMP.cleanup()
