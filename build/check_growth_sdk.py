"""Real SDK reflection schema/request hooks, isolated local evidence, no sends."""
from pathlib import Path
import asyncio
from copy import deepcopy
import functools
import importlib
import importlib.util
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="aiwu-growth-sdk-")
os.environ["ASTRBOT_ROOT"] = TEMP.name
os.chdir(TEMP.name)
from astrbot.api import ToolSet
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.message.components import Plain
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.register.star_handler import llm_tools

plugin = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_companion"
spec = importlib.util.spec_from_file_location("_growth_sdk_companion", plugin / "__init__.py", submodule_search_locations=[str(plugin)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
main = importlib.import_module(spec.name + ".main")
OWNER, BOT, UMO = "123456789", "987654321", "qq-sdk:FriendMessage:123456789"


class Event(AstrMessageEvent):
    def __init__(self, mid, text):
        obj = AstrBotMessage()
        obj.type, obj.self_id, obj.message_id = MessageType.FRIEND_MESSAGE, BOT, mid
        obj.sender = MessageMember(OWNER, "offline owner")
        obj.message_str, obj.message, obj.raw_message, obj.session_id = text, [Plain(text)], {}, OWNER
        super().__init__(text, obj, PlatformMetadata("aiocqhttp", "offline adapter", id="qq-sdk"), OWNER)


def material():
    packet = {"schema": "love-house-memory-context/v1", "material_role": "evidence_not_instructions",
        "identity": {"library_id": "lib_sdk", "is_demo": False}, "records": [{"id": "memory_1",
        "value": "主人希望自然简洁地交流。", "layer": "preference", "subject": "user",
        "interpretation": "current_memory", "validity": {"active": True},
        "source": {"kind": "conversation_turn", "evidence": [{"id": "source_1", "role": "user"}]}}]}
    return "<love_house_memory_material>\n历史资料，仅供参考。\n" + json.dumps(packet, ensure_ascii=False) + "\n</love_house_memory_material>"


async def run():
    tool = next(item for item in llm_tools.func_list if item.name == "dafeiyu_reflect_experience"
                and getattr(item.handler, "__module__", None) == main.__name__)
    # Mirror the actual StarManager loader's post-construction binding. The
    # decorator registers the raw handler; module/instance are filled at load.
    tool.handler_module_path = main.__name__
    properties = tool.parameters["properties"]
    assert properties["evidence_ids"]["type"] == "array"
    assert properties["evidence_ids"]["items"]["type"] == "string"
    assert properties["behavior"]["type"] == "string"
    foreign = deepcopy(tool)
    foreign.name = "unrelated_existing_tool"
    foreign.handler_module_path = "other.module"
    registry = {}
    memory = SimpleNamespace(_route=lambda event: SimpleNamespace(source_id="source_sdk", library_id="lib_sdk"))
    registry["love_house"] = SimpleNamespace(activated=True, star_cls=memory)
    manager = SimpleNamespace(func_list=[tool, foreign])
    context = SimpleNamespace(get_config=lambda **kw: {"provider_settings": {"enable": True}, "plugin_set": ["*"]},
        get_registered_star=lambda name: registry.get(name), get_llm_tool_manager=lambda: manager)
    instance = main.DafeiyuCompanion(context, {"enabled": True, "owner_qq": OWNER, "bot_qq": BOT, "growth_enabled": True})
    tool.handler = functools.partial(tool.handler, instance)
    registry[main.PLUGIN_NAME] = SimpleNamespace(activated=True, star_cls=instance)
    instance._growth_root = Path(TEMP.name) / "growth-data"
    instance._session_allows = AsyncMock(return_value=True)
    instance._plugin_allowed = AsyncMock(return_value=True)
    instance._reply_pipeline.plugin_allowed = instance._plugin_allowed
    event = Event("feedback", "简短点，少发表情。")
    instance._turns.observe(event)
    event.set_extra(main.GROUP_KEY, "private")
    instance._adaptive.predict(event, instance._options())
    event.set_extra("love_house_material_source_sdk", material())
    await instance._observe_growth_feedback(event)
    assert event.get_extra("dafeiyu_reply_policy")["response_mode"] == "single"
    assert event.get_extra("dafeiyu_growth_no_auto_sticker") is True
    request = ProviderRequest(func_tool=ToolSet(tools=[]))
    await instance.growth_context(event, request)
    assert request.func_tool.get_tool(tool.name) is tool
    assert "<dafeiyu_growth_material>" in request.system_prompt
    outcome = json.loads(await instance.dafeiyu_reflect_experience(event, ["memory_1"], "也许少一些追问更合适", "根据真实反馈继续观察", "fewer_questions"))
    assert outcome["ok"] and outcome["status"] == "pending"
    # A second loop request gets exactly this local tool, without growing or
    # mutating the global list or retaining it when the feature is switched off.
    original = ToolSet(tools=[foreign, tool])
    request2 = ProviderRequest(func_tool=original)
    await instance.growth_context(event, request2)
    assert request2.func_tool is not original
    assert request2.func_tool.get_tool(foreign.name) is foreign
    instance.config["growth_enabled"] = False
    await instance.growth_context(event, request2)
    assert request2.func_tool.get_tool(tool.name) is None
    assert request2.func_tool.get_tool(foreign.name) is foreign
    assert original.get_tool(tool.name) is tool and manager.func_list == [tool, foreign]
    assert not json.loads(await instance.dafeiyu_reflect_experience(event, ["memory_1"], "猜测", "建议"))["ok"]
    instance._collector.close()
    instance._adaptive.close()
    instance._turns.close()
    instance._bubbles.close()
    from importlib.metadata import version
    print(json.dumps({"sdk_version": version("astrbot"), "real_tool_array_string_schema": True,
        "real_ProviderRequest_ToolSet_persona_empty": True, "foreign_and_global_tools_unchanged": True,
        "same_event_real_memory_evidence_required": True, "explicit_feedback_actual_policy": True,
        "only_pending_reflection_no_self_approval": True, "hot_off_removes_owned_tool": True,
        "real_QQ_or_model_calls": False}))


try:
    asyncio.run(run())
finally:
    os.chdir(ROOT)
    TEMP.cleanup()
