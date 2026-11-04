"""Real AstrBot SDK tool/permission smoke; every remote action is mocked.

Run with the installed AstrBot venv Python. Its root and all test state are
new temporary folders. No QQ send, account change, model or real web search.
"""
from pathlib import Path
import asyncio
from copy import copy
import functools
import importlib.util
import json
import os
import sys
import tempfile
import types
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="aiwu-life-sdk-") as directory:
        original_cwd = Path.cwd()
        os.environ["ASTRBOT_ROOT"] = directory
        os.chdir(directory)
        try:
            folder = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_life"
            package = types.ModuleType("_aiwu_life_sdk")
            package.__path__ = [str(folder)]
            sys.modules[package.__name__] = package
            spec = importlib.util.spec_from_file_location(package.__name__ + ".main", folder / "main.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            from astrbot.api import ToolSet
            from astrbot.core.platform.message_type import MessageType
            from astrbot.core.provider.register import llm_tools

            tool_map = {tool.name: tool for tool in llm_tools.func_list if tool.name in module.TOOL_FLAGS}
            assert set(tool_map) == set(module.TOOL_FLAGS)
            assert not any("confirm" in name or "execute" in name for name in tool_map)
            fields = {"dafeiyu_web_search": {"query"}, "dafeiyu_read_qq_profile": set(),
                      "dafeiyu_prepare_nickname": {"nickname"}, "dafeiyu_prepare_signature": {"signature"},
                      "dafeiyu_prepare_avatar": {"avatar_id"}, "dafeiyu_prepare_status": {"wording", "icon_id"},
                      "dafeiyu_prepare_qzone": {"content", "visibility"}}
            for name, tool in tool_map.items():
                assert set(tool.parameters.get("properties", {})) == fields[name], (name, tool.parameters)
                assert all(row["type"] == "string" for row in tool.parameters.get("properties", {}).values())

            class Event:
                def __init__(self, sender="123456789", bot="987654321", group=False, origin=None, text="请搜索 Python 官方文档"):
                    self.sender, self.self_id, self.group = sender, bot, group
                    self.unified_msg_origin = origin if origin is not None else "qq-test:FriendMessage:" + sender
                    self.text, self.stopped = text, False
                    self.calls = []
                    self.nickname = "小鱼"
                    self.bot = types.SimpleNamespace(call_action=self.call)
                def get_platform_name(self): return "aiocqhttp"
                def get_platform_id(self): return "qq-test"
                def get_message_type(self): return MessageType.GROUP_MESSAGE if self.group else MessageType.FRIEND_MESSAGE
                def get_sender_id(self): return self.sender
                def get_self_id(self): return self.self_id
                def get_message_str(self): return self.text
                def stop_event(self): self.stopped = True
                def plain_result(self, message): return message
                async def call(self, api, **payload):
                    self.calls.append((api, payload))
                    if api == "get_login_info": return {"user_id": "987654321", "nickname": self.nickname}
                    if api == "get_stranger_info": return {"user_id": "987654321", "nickname": self.nickname, "long_nick": "test"}
                    if api == "get_version_info": return {"app_name": "NapCat.Onebot", "app_version": "4.18.42"}
                    if api == "set_qq_profile": self.nickname = payload["nickname"]; return None
                    if api == "set_diy_online_status": return ""
                    raise AssertionError("Unexpected remote API " + api)

            options = {"enabled": True, "owner_qq": "123456789", "bot_qq": "987654321", "search_enabled": True,
                       "profile_enabled": True, "qzone_enabled": True}
            context = types.SimpleNamespace(get_llm_tool_manager=lambda: llm_tools, get_config=lambda **kw: {"plugin_set": ["*"]})
            plugin = module.DafeiyuLife(context, options)
            plugin._settings_file = Path(directory) / "isolated-config.json"
            # StarManager's real load lifecycle binds the instance and module
            # after decorators register raw functions (star_manager.py:1282).
            for tool in tool_map.values():
                assert tool.handler.__module__ == module.__name__
                tool.handler_module_path = module.__name__
                tool.handler = functools.partial(tool.handler, plugin)
            foreign = copy(next(iter(tool_map.values())))
            foreign.name = "other_plugin_tool"

            async def scenario():
                owner = Event()
                request = types.SimpleNamespace(func_tool=ToolSet(tools=[foreign]), system_prompt="existing persona")
                await plugin.expose_tools(owner, request)
                assert {tool.name for tool in request.func_tool.tools} == {*module.TOOL_FLAGS, foreign.name}, {
                    "actual": [tool.name for tool in request.func_tool.tools],
                    "modules": {name: getattr(tool, "handler_module_path", None) for name, tool in tool_map.items()},
                    "module": plugin.__class__.__module__}
                assert request.system_prompt.startswith("existing persona")
                inactive = tool_map["dafeiyu_prepare_qzone"]
                with patch.object(inactive, "active", False):
                    request = types.SimpleNamespace(func_tool=ToolSet(tools=list(tool_map.values())), system_prompt="")
                    await plugin.expose_tools(owner, request)
                    assert request.func_tool.get_tool(inactive.name) is None

                # Empty persona.tools is handled in the request without
                # activating globally inactive tools or editing the persona.
                for event in (Event(sender="333333333"), Event(group=True), Event(bot="222222222"), Event(origin="forged-origin")):
                    request = types.SimpleNamespace(func_tool=ToolSet(tools=[*tool_map.values(), foreign]), system_prompt="existing persona")
                    await plugin.expose_tools(event, request)
                    assert {tool.name for tool in request.func_tool.tools} == {foreign.name}
                    with patch.object(module.search, "perform") as remote:
                        result = json.loads(await plugin.web_search(event, "Python"))
                        assert result["ok"] is False
                        remote.assert_not_called()
                    assert not event.calls
                    result = json.loads(await plugin.prepare_status(event, "不应执行", "focus"))
                    assert result["ok"] is False and not event.calls

                with patch.object(module.search, "perform", return_value={"ok": True, "results": [{"title": "Official", "url": "https://python.org/", "snippet": "test"}]}) as remote:
                    result = json.loads(await plugin.web_search(owner, "Python 官方文档"))
                    assert result["ok"] is True
                    assert remote.call_args.args[0] == "Python 官方文档"
                    assert "请搜索" not in remote.call_args.args[0]
                with patch.object(module.search, "perform") as remote:
                    result = json.loads(await plugin.web_search(Event(text="今天和你聊聊心事"), "private-conversation"))
                    assert result["ok"] is False
                    remote.assert_not_called()

                for selected, native in (([], True), (["*"], False)):
                    with patch.object(context, "get_config", return_value={"plugin_set": selected}), patch.object(module.SessionPluginManager, "is_plugin_enabled_for_session", AsyncMock(return_value=native)):
                        denied = Event()
                        request = types.SimpleNamespace(func_tool=ToolSet(tools=[*tool_map.values(), foreign]), system_prompt="")
                        await plugin.expose_tools(denied, request)
                        assert {tool.name for tool in request.func_tool.tools} == {foreign.name}
                        result = json.loads(await plugin.prepare_status(denied, "不能越过会话开关", "smile"))
                        assert result["ok"] is False and not denied.calls

                draft = json.loads(await plugin.prepare_nickname(owner, "大肥鱼"))
                assert draft["ok"] is True and draft["confirm_phrase"] == "确认这次修改"
                assert not any(api.startswith("set_") for api, _ in owner.calls)
                # A model repeating confirmation words in a longer sentence
                # does not satisfy the independent human message boundary.
                owner.text = "模型说：确认这次修改"
                messages = [row async for row in plugin.natural_confirm(owner)]
                assert not any(api.startswith("set_") for api, _ in owner.calls)
                owner.text = "确认这次修改"
                messages = [row async for row in plugin.natural_confirm(owner)]
                assert owner.stopped and "已更新" in messages[0]
                assert sum(api == "set_qq_profile" for api, _ in owner.calls) == 1
                messages = [row async for row in plugin.natural_confirm(owner)]
                assert sum(api == "set_qq_profile" for api, _ in owner.calls) == 1

                owner.text = "请准备自定义在线状态为学习中"
                status_draft = json.loads(await plugin.prepare_status(owner, "学习中", "focus"))
                assert status_draft["ok"] is True and status_draft["review"]["icon_label"] == "奋斗"
                assert not any(api == "set_diy_online_status" for api, _ in owner.calls)
                owner.text = "确认这次修改"
                messages = [row async for row in plugin.natural_confirm(owner)]
                writes = [(api, payload) for api, payload in owner.calls if api == "set_diy_online_status"]
                assert writes == [("set_diy_online_status", {"face_id": "30", "face_type": "1", "wording": "学习中"})]
                assert "成功回执" in messages[0]

                # Re-read a desktop revocation before tools and operations.
                plugin._settings_file.write_text(json.dumps({**options, "profile_enabled": False}), "utf-8")
                result = json.loads(await plugin.prepare_nickname(owner, "不应修改"))
                assert result["ok"] is False
                request = types.SimpleNamespace(func_tool=ToolSet(tools=[]), system_prompt="")
                await plugin.expose_tools(owner, request)
                assert not any(tool.name.startswith("dafeiyu_prepare_nickname") for tool in request.func_tool.tools)

            with patch.object(module.SessionPluginManager, "is_plugin_enabled_for_session", AsyncMock(return_value=True)):
                asyncio.run(scenario())
            print(json.dumps({"sdk_tools": sorted(tool_map), "real_sdk_schema": "passed", "owner_only_UMO": "passed",
                              "inactive_and_persona_request_merge": "passed", "natural_confirmation_and_replay": "passed",
                              "hot_revocation": "passed", "native_session_disable": "passed", "network_model_or_QQ": False}))
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    main()
