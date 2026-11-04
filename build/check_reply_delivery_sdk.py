"""Real SDK scheduler + aiocqhttp adapter spies. No model, QQ or user data.

The local bot recorder is the only mocked transport. Real adapter.send and
base.send side effects, SDK ProcessStage and RespondStage remain in the path.
"""
from pathlib import Path
import asyncio
import copy
import functools
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="aiwu-delivery-sdk-")
os.environ["ASTRBOT_ROOT"] = TEMP.name
os.chdir(TEMP.name)
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.platform.astr_message_event import AstrMessageEvent, event_context
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult, ResultContentType
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.process_stage.stage import ProcessStage
from astrbot.core.pipeline.process_stage.method.star_request import StarRequestSubStage
from astrbot.core.pipeline.respond.stage import RespondStage
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
from astrbot.core.utils.metrics import Metric


def module(name, source):
    spec = importlib.util.spec_from_loader(name, loader=None)
    item = importlib.util.module_from_spec(spec)
    sys.modules[name] = item
    exec(compile(source, name, "exec"), item.__dict__)
    return item


FOLDER = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion"
spec = importlib.util.spec_from_file_location("_delivery_sdk_companion", FOLDER / "__init__.py", submodule_search_locations=[str(FOLDER)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from importlib import import_module
main = import_module(spec.name + ".main")
natural = import_module(spec.name + ".natural_bubbles")
try:
    previous = subprocess.check_output(["git", "show", "v1.5.0:assets/plugins/astrbot_plugin_dafeiyu_companion/natural_bubbles.py"], cwd=ROOT).decode("utf-8")
except subprocess.CalledProcessError:
    previous = subprocess.check_output(["git", "show", "HEAD:assets/plugins/astrbot_plugin_dafeiyu_companion/natural_bubbles.py"], cwd=ROOT).decode("utf-8")
old = module("_delivery_sdk_before", previous)
TEXT = "今天刚看到一朵像鲸鱼的小云，忍不住想和你分享。我们可以一边喝茶一边慢慢聊，不着急把所有话说完。"
HOST = {"provider_settings": {"enable": True, "streaming_response": False, "unsupported_streaming_strategy": "realtime_segmenting"},
        "provider_tts_settings": {"enable": False}, "t2i": False,
        "platform_settings": {"segmented_reply": {"enable": False, "only_llm_result": True, "interval_method": "random", "log_base": 2.6, "interval": "0,0"},
                              "reply_with_mention": False, "reply_with_quote": False, "forward_threshold": 1500}}
OPTIONS = {"natural_bubbles_enabled": True, "natural_bubbles_frequency": "often"}


def event(mid="turn-one", *, failing=False):
    obj = AstrBotMessage()
    obj.type, obj.self_id, obj.message_id = MessageType.FRIEND_MESSAGE, "987654321", mid
    obj.sender, obj.session_id, obj.raw_message = MessageMember("123456789", "isolated"), "123456789", {}
    obj.message_str, obj.message = "一起聊聊", [Plain("一起聊聊")]
    sent = []
    async def send_private_msg(**kwargs):
        sent.append(copy.deepcopy(kwargs["message"]))
        if failing:
            raise TimeoutError("Synthetic accept-before-timeout")
    bot = SimpleNamespace(send_private_msg=send_private_msg)
    value = AiocqhttpMessageEvent(obj.message_str, obj, PlatformMetadata("aiocqhttp", "isolated", id="qq-sdk"), obj.session_id, bot)
    value.is_at_or_wake_command = True
    value.call_llm = False
    value.set_extra("activated_handlers", [])
    return value, sent


async def run_case(policy, *, native=False, tools=False, failing=False, handler_request=False, streaming=False, mid="turn-one", service=None, root_arm=False, response_mode="auto", formal=False):
    host = copy.deepcopy(HOST)
    host["platform_settings"]["segmented_reply"]["enable"] = native
    host["provider_settings"]["streaming_response"] = streaming
    value, sent = event(mid, failing=failing)
    if formal:
        value.message_str = "这段是正式报告，请一条发完。"
    value.set_extra("dafeiyu_reply_policy", {"response_mode": response_mode, "max_bubbles": 2, "profile": "natural"})
    service = service or policy.BubbleReplies()
    service._selected = lambda *args: True
    service._delay = lambda *args: 0
    guard, accepted = AsyncMock(return_value=True), AsyncMock()
    value._dafeiyu_reply_packet_accepted = accepted
    controller = None
    metadata = star_map[main.__name__]
    metadata.activated = False
    if root_arm:
        context = SimpleNamespace(get_config=lambda **kw: host)
        controller = main.DafeiyuCompanion(context, {"enabled": True, "natural_bubbles_enabled": True, "owner_qq": "123456789", "bot_qq": "987654321"})
        controller._bubbles = service
        controller._reply_allows = guard
        metadata.activated, metadata.star_cls = True, controller
        for handler in star_handlers_registry._handlers:
            if handler.handler_module_path == main.__name__:
                raw = handler.handler.func if isinstance(handler.handler, functools.partial) else handler.handler
                handler.handler = functools.partial(raw, controller)
        await controller.adaptive_context(value, ProviderRequest())
        assert getattr(value, "_dafeiyu_natural_send_wrapper", None) is service
        # Keep the main's arm, but count accepted packets through a local hook.
        value._dafeiyu_reply_packet_accepted = accepted
    elif streaming and callable(getattr(service, "arm", None)):
        await service.arm(value, OPTIONS, guard, host)
    calls = []
    class Agent:
        async def process(self, incoming):
            calls.append(True)
            for index in range(1 if handler_request else 2):
                result = MessageEventResult(chain=[Plain(part) for part in policy.split_text(TEXT)] if native else [Plain(TEXT)])
                result.result_content_type = ResultContentType.LLM_RESULT
                if streaming and index == 0:
                    async def stream():
                        yield MessageChain([Plain(TEXT)])
                    result.chain, result.async_stream = [], stream()
                    result.result_content_type = ResultContentType.STREAMING_RESULT
                incoming.set_result(result)
                if tools:
                    incoming.set_extra(policy.TOOL_KEY, True)
                yield
    process = ProcessStage()
    process.ctx = SimpleNamespace(astrbot_config=host)
    process.star_request_sub_stage = StarRequestSubStage()
    process.agent_sub_stage = Agent()
    if handler_request:
        name = "_isolated_provider_request"
        star_map[name] = StarMetadata(name=name, activated=True)
        async def request(incoming):
            return ProviderRequest(prompt="isolated")
        value.set_extra("activated_handlers", [StarHandlerMetadata(EventType.AdapterMessageEvent, "request", "request", name, request, [])])
    class Decorate:
        async def process(self, incoming):
            await service.prepare(incoming, OPTIONS, guard, host)
    respond = RespondStage()
    await respond.initialize(SimpleNamespace(astrbot_config=host))
    scheduler = PipelineScheduler.__new__(PipelineScheduler)
    scheduler.stages = [process, Decorate(), respond]
    with event_context(value):
        await scheduler._process_stages(value)
    return value, sent, calls, accepted, service


def plain(sent):
    return "".join(part["data"]["text"] for packet in sent for part in packet if part.get("type") == "text")


async def run():
    # Previously both branches had no final-decision claim at all.
    _, old_tool, _, _, _ = await run_case(old, tools=True)
    _, old_native, _, _, _ = await run_case(old, native=True)
    assert len(old_tool) == 2 and plain(old_tool) == TEXT * 2
    assert len(old_native) == 4 and plain(old_native) == TEXT * 2
    value, sent, _, accepted, _ = await run_case(natural, tools=True, root_arm=True)
    assert plain(sent) == TEXT and len(sent) == 1 and accepted.await_count == 1
    assert value._has_send_oper
    assert value.get_result() is None
    await value.send(MessageChain([Plain(TEXT)]))
    assert len(sent) == 1, "Cleared event.result must not reopen an identical final alias"
    _, sent, _, accepted, _ = await run_case(natural, native=True, root_arm=True)
    assert plain(sent) == TEXT and len(sent) == 2 and accepted.await_count == 2
    # Real adapter dispatch can accept then time out before base.send sets the
    # flag. ProcessStage must not start a fallback Agent or repeat the packet.
    _, old_timeout, old_calls, _, _ = await run_case(old, tools=True, failing=True, handler_request=True)
    _, sent, calls, accepted, _ = await run_case(natural, tools=True, failing=True, handler_request=True, root_arm=True)
    assert len(old_timeout) == 2 and len(old_calls) == 2
    assert len(sent) == 1 and len(calls) == 1 and accepted.await_count == 0
    value, sent, _, _, service = await run_case(natural, streaming=True, root_arm=True)
    assert plain(sent) == TEXT
    assert value.get_extra(natural.COUNT_KEY) >= 1
    _, split, _, _, _ = await run_case(natural, root_arm=True, response_mode="bubbles")
    _, whole, _, _, _ = await run_case(natural, root_arm=True, response_mode="bubbles", formal=True)
    assert len(split) == 2 and plain(split) == TEXT
    assert len(whole) == 1 and plain(whole) == TEXT
    # An explicit fresh incoming message with identical output is legitimate.
    _, first, _, _, service = await run_case(natural, tools=True, mid="a", root_arm=True)
    _, second, _, _, _ = await run_case(natural, tools=True, mid="b", service=service, root_arm=True)
    assert plain(first) == plain(second) == TEXT
    print(json.dumps({"old_tool_round_real_dispatches":len(old_tool),"old_native_real_dispatches":len(old_native),
        "old_accept_before_timeout_agent_runs":len(old_calls),"new_timeout_agent_runs":len(calls),
        "real_PipelineScheduler":True,"real_ProcessStage":True,"real_RespondStage":True,
        "real_aiocqhttp_and_base_send":True,"main_arm_path":True,"native_components_preserved":True,
        "stream_owns_one_decision":True,"cleared_final_alias_suppressed":True,"dynamic_bubbles_and_formal_whole":True,
        "new_mid_same_words_allowed":True,"real_QQ_calls":0,"model_calls":0}))


if __name__ == "__main__":
    try:
        with patch.object(Metric, "upload", new=AsyncMock()):
            asyncio.run(run())
    finally:
        os.chdir(ROOT)
        TEMP.cleanup()
