"""Real SDK hook/tool/image spy; no model, QQ, credentials or user profile.

Run with the installed AstrBot venv Python. All SDK state stays in a temporary
directory; event.send below is a local recorder, never a network adapter.
"""
from pathlib import Path
import asyncio
import base64
import importlib.util
import json
import os
import re
import sys
import tempfile
import types
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory(prefix="aiwu-sticker-sdk-150-")
os.environ["ASTRBOT_ROOT"] = TEMP.name
os.chdir(TEMP.name)


def load(name, folder):
    package = types.ModuleType(name)
    package.__path__ = [str(folder)]
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name + ".main", folder / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plugin_module = load("sticker_sdk_150", ROOT / "assets/plugins/astrbot_plugin_dafeiyu_stickers")
face_module = load("face_sdk_150", ROOT / "assets/plugins/astrbot_plugin_face_convert")
from astrbot.api import ToolSet
from astrbot.core.message.components import Plain, Image, Face
from astrbot.core.message.message_event_result import MessageEventResult, ResultContentType
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest, LLMResponse
from astrbot.core.provider.register import llm_tools
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot.core import astr_main_agent


class Event:
    unified_msg_origin = "qq-spy:FriendMessage:123456789"
    def __init__(self, text="你好，小鱼", reply="你好呀，见到你真开心[doge]！"):
        self.message_obj = types.SimpleNamespace(message_id="sdk-spy-message")
        self.message_str, self.result = text, MessageEventResult(chain=[Plain(reply)]).set_result_content_type(ResultContentType.LLM_RESULT)
        self.extras, self.sent = {}, []
    def get_platform_name(self): return "aiocqhttp"
    def get_platform_id(self): return "qq-spy"
    def get_sender_id(self): return "123456789"
    def get_self_id(self): return "987654321"
    def get_message_type(self): return MessageType.FRIEND_MESSAGE
    def get_group_id(self): return ""
    def get_message_str(self): return self.message_str
    def get_result(self): return self.result
    def get_extra(self, key, default=None): return self.extras.get(key, default)
    def set_extra(self, key, value): self.extras[key] = value
    async def send(self, message): self.sent.append(message)


async def main():
    tool = next(tool for tool in llm_tools.func_list if tool.name == "send_dafeiyu_sticker")
    assert tool.parameters["properties"]["emotion"]["type"] == "string"
    persona = {"prompt": "existing persona", "tools": [], "skills": []}
    context = types.SimpleNamespace(
        persona_manager=types.SimpleNamespace(resolve_selected_persona=AsyncMock(return_value=("spy", persona, None, False))),
        get_llm_tool_manager=lambda: llm_tools,
        get_config=lambda **kw: {"plugin_set": ["*"], "subagent_orchestrator": {}},
        subagent_orchestrator=None,
        platform_manager=types.SimpleNamespace(platform_insts=[types.SimpleNamespace(meta=lambda:types.SimpleNamespace(id="qq-spy",name="aiocqhttp"))]))
    event = Event("发一张开心表情", "好呀")
    request = ProviderRequest()
    request.conversation = types.SimpleNamespace(persona_id="spy")
    with patch.object(astr_main_agent, "SkillManager", return_value=types.SimpleNamespace(list_skills=lambda **kw: [])):
        await astr_main_agent._ensure_persona_and_skills(request, {"computer_use_runtime": "none"}, context, event)
    assert isinstance(request.func_tool, ToolSet) and not request.func_tool.tools
    plugin = plugin_module.DafeiyuStickers(context, {"auto_probability": 100})
    # StarLoader binds registered tools to the active instance after import.
    tool.active = True
    tool.handler_module_path = plugin.__class__.__module__
    await plugin.match_before_request(event, request)
    assert [item.name for item in request.func_tool.tools] == ["send_dafeiyu_sticker"]
    assert persona["tools"] == []  # Request-local repair, never opens all tools.
    for first in (True, False):
        plugin = plugin_module.DafeiyuStickers(context, {"auto_probability": 100, "pack_selection": "community"})
        event = Event()
        request = ProviderRequest(system_prompt="existing persona", func_tool=ToolSet())
        await plugin.match_before_request(event, request)
        assert not request.func_tool.tools  # Automatic images need no tool call.
        converter = object.__new__(face_module.FaceConvertPlugin)
        converter.face_map, converter.pattern = {"doge": 179}, re.compile(r"\[([^\[\]]{1,8})\]")
        converter.cfg, converter._face_history = face_module.DEFAULT_CONFIG.copy(), {}
        if first:
            await plugin.attach_matching_sticker(event)
            await converter.convert_faces(event)
        else:
            await converter.convert_faces(event)
            await plugin.attach_matching_sticker(event)
        assert sum(isinstance(part, Image) for part in event.result.chain) == 1
        assert sum(isinstance(part, Face) for part in event.result.chain) == 1
        assert event.sent == []
        with patch.object(SessionPluginManager, "is_plugin_enabled_for_session", new=AsyncMock(return_value=True)):
            await event.send(event.result)
        await plugin.remember_sent_offer(event)
        image = next(part for part in event.sent[0].chain if isinstance(part, Image))
        assert base64.b64decode(await image.convert_to_base64()).startswith((b"GIF87a", b"GIF89a"))
        assert event.get_extra(plugin_module.IMAGE_RECEIPT_KEY) == "sent"
    plugin = plugin_module.DafeiyuStickers(context, {"auto_probability": 100})
    event = Event("今天好开心", "好耶！[sticker:happy]")
    await plugin.match_before_request(event, ProviderRequest(system_prompt="persona", func_tool=ToolSet()))
    response = LLMResponse(role="assistant", completion_text="好耶！[sticker:happy]")
    await plugin.capture_model_decision(event, response)
    assert response.completion_text == "好耶！"
    event.result.chain = [Plain(response.completion_text)]
    await plugin.attach_matching_sticker(event)
    assert sum(isinstance(part, Image) for part in event.result.chain) == 1
    plugin = plugin_module.DafeiyuStickers(context, {"auto_probability": 100})
    with patch.object(SessionPluginManager, "is_plugin_enabled_for_session", new=AsyncMock(return_value=True)):
        origin = Event.unified_msg_origin
        assert await plugin.proactive_context(origin, "轻松问候")
        prepared = await plugin.prepare_proactive(origin, "你好呀，见到你真开心！")
        assert isinstance(prepared["component"], Image)
        assert isinstance(await plugin.revalidate_proactive(origin, prepared["reservation"]), Image)
        await plugin.acknowledge_proactive(origin, prepared["reservation"], "not_sent")
        assert not plugin._last_sent
    print(json.dumps({"real_sdk": True, "persona_empty_tools_verified": True,
        "explicit_tool_request_local_only": True, "automatic_greeting_without_tools": True,
        "both_face_hook_orders": True, "gif_serialization": True, "captured_clean_history": True, "proactive_fallback": True,
        "real_qq_sends": 0, "model_calls": 0}))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        os.chdir(ROOT)
        TEMP.cleanup()
