"""Real PC tool contracts with mocked I/O: no clicks, typing or QQ sends."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "assets/plugins/astrbot_plugin_pc_operator"
sys.path.insert(0, str(ROOT / "app"))
from backend import pc_access, config_store


class ToolSet:
    def __init__(self, tools=None):
        self.tools = list(tools or [])

    def remove_tool(self, name):
        self.tools = [tool for tool in self.tools if tool.name != name]

    def get_tool(self, name):
        return next((tool for tool in self.tools if tool.name == name), None)

    def add_tool(self, tool):
        self.remove_tool(tool.name)
        self.tools.append(tool)


def load_plugin():
    stubs = {}
    for name in ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star", "astrbot.core",
                 "astrbot.core.platform", "astrbot.core.platform.message_type", "astrbot.core.star",
                 "astrbot.core.star.filter", "astrbot.core.star.filter.command", "pcdesktoptest"):
        stubs[name] = types.ModuleType(name)
    stubs["pcdesktoptest"].__path__ = [str(PLUGIN)]

    class Star:
        def __init__(self, context): self.context = context

    def tool(name):
        def decorator(method):
            method.tool_name = name
            return method
        return decorator

    stubs["astrbot.api"].logger = Mock()
    stubs["astrbot.api"].ToolSet = ToolSet
    stubs["astrbot.api.event"].AstrMessageEvent = object
    stubs["astrbot.api.event"].filter = types.SimpleNamespace(
        llm_tool=tool, command=lambda *a, **k: lambda method: method,
        on_llm_request=lambda *a, **k: lambda method: method)
    stubs["astrbot.api.star"].Context = object
    stubs["astrbot.api.star"].Star = Star
    stubs["astrbot.api.star"].register = lambda *a, **k: lambda cls: cls
    stubs["astrbot.core.platform.message_type"].MessageType = types.SimpleNamespace(
        FRIEND_MESSAGE="friend", GROUP_MESSAGE="group")
    stubs["astrbot.core.star.filter.command"].GreedyStr = str
    with patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("pcdesktoptest.main", PLUGIN / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


pc = load_plugin()
native = pc.desktop_native


class Event:
    def __init__(self, sender="123456789", kind="friend", platform="aiocqhttp", bot="987654321"):
        self.sender, self.kind, self.sent = sender, kind, []
        self.platform, self.bot = platform, bot
        self.unified_msg_origin = "qq-987654321:FriendMessage:" + sender

    def get_sender_id(self): return self.sender
    def get_message_type(self): return self.kind
    def get_platform_name(self): return self.platform
    def get_platform_id(self): return "qq-987654321"
    def get_self_id(self): return self.bot
    def image_result(self, path): return {"image": path}
    async def send(self, message): self.sent.append(message)


class DesktopPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aiwu-pc-desktop-")
        self.addCleanup(self.temp.cleanup)
        self.config = dict(enabled=True, owner_qq="123456789", desktop_enabled=False,
            workspace=self.temp.name, private_only=True, members={
                "30000001": dict(read=True, write=True, run=True, desktop=False),
                "30000002": dict(read=True, write=True, run=True, desktop=True)})
        self.plugin = pc.PcOperator(None, self.config)

    def test_desktop_is_independent_default_off_and_private_only(self):
        self.assertFalse(config_store.DEFAULT_CONFIG["pc_access"]["desktop_enabled"])
        self.assertIsNone(self.plugin._check(Event(), "desktop")[0])
        self.assertIsNotNone(self.plugin._check(Event(), "run")[0])
        self.config["desktop_enabled"] = True
        self.assertIsNotNone(self.plugin._check(Event(), "desktop")[0])
        self.assertIsNone(self.plugin._check(Event("30000001"), "desktop")[0])
        self.assertIsNotNone(self.plugin._check(Event("30000002"), "desktop")[0])
        self.assertIsNone(self.plugin._check(Event("99999999"), "desktop")[0])
        self.config["private_only"] = False
        self.assertIsNone(self.plugin._check(Event(kind="group"), "desktop")[0])
        self.config["enabled"] = False
        self.assertIsNone(self.plugin._check(Event(), "desktop")[0])

    def test_string_flags_do_not_accidentally_enable_desktop_or_send_screenshot(self):
        self.config["desktop_enabled"] = "false"
        self.assertIsNone(self.plugin._check(Event(), "desktop")[0])
        self.config["desktop_enabled"] = True
        self.config["members"]["30000002"]["desktop"] = "true"
        self.assertIsNone(self.plugin._check(Event("30000002"), "desktop")[0])
        with patch.object(native, "get_desktop") as get:
            self.assertFalse(json.loads(asyncio.run(self.plugin.pc_desktop_screenshot(Event(), "false")))["ok"])
            get.assert_not_called()

    def test_denied_desktop_tool_never_touches_native_or_screenshot_files(self):
        with patch.object(native, "get_desktop") as get:
            result = json.loads(asyncio.run(self.plugin.pc_desktop_click(Event(), 10, 20)))
            self.assertFalse(result["ok"])
            result = json.loads(asyncio.run(self.plugin.pc_desktop_screenshot(Event())))
            self.assertFalse(result["ok"])
            get.assert_not_called()
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_non_qq_forged_origin_self_and_other_bot_cannot_use_desktop(self):
        self.config["desktop_enabled"] = True
        self.config["bot_qq"] = "987654321"
        forged = Event()
        forged.unified_msg_origin = "different-platform:FriendMessage:123456789"
        events = [Event(platform="slack"), Event(platform="webchat"), Event(bot="123456789"),
                  Event(bot="222222222"), forged]
        with patch.object(native, "get_desktop") as get:
            for event in events:
                self.assertFalse(json.loads(asyncio.run(self.plugin.pc_desktop_status(event)))["ok"])
            get.assert_not_called()

    def test_authorized_click_maps_exact_arguments_and_audit_excludes_typed_text(self):
        self.config["desktop_enabled"] = True
        desktop = types.SimpleNamespace(click=Mock(return_value={"clicked": True}),
                                        type_text=Mock(return_value={"typed": True}))
        with patch.object(native, "get_desktop", return_value=desktop):
            self.assertTrue(json.loads(asyncio.run(self.plugin.pc_desktop_click(Event(), 22, 44, "right", 2, 77)))["ok"])
            desktop.click.assert_called_once_with(22, 44, "right", 2, 77)
            asyncio.run(self.plugin.pc_desktop_type_text(Event(), "example-private-text", 77))
        audit = Path(self.temp.name, ".dafeiyu/audit.jsonl").read_text("utf-8")
        self.assertNotIn("example-private-text", audit)
        self.assertIn("desktop_type_text", audit)

    def test_screenshot_uses_standard_image_result_and_is_not_faked_as_model_vision(self):
        self.config["desktop_enabled"] = True
        def screenshot(path):
            path.write_bytes(native.encode_png(1, 1, bytes([0, 0, 255, 0])))
            return dict(path=str(path), x=0, y=0, width=1, height=1)
        event = Event()
        with patch.object(native, "get_desktop", return_value=types.SimpleNamespace(screenshot=screenshot)):
            result = json.loads(asyncio.run(self.plugin.pc_desktop_screenshot(event)))
        self.assertTrue(result["ok"] and result["sent_to_user"])
        self.assertFalse(result["image_attached_to_model"])
        self.assertEqual(event.sent, [{"image": result["path"]}])
        self.assertTrue(Path(result["path"]).is_relative_to(Path(self.temp.name)))

    def test_request_local_tool_exposure_fixes_empty_persona_without_global_mutation(self):
        self.config["desktop_enabled"] = True
        tools = [types.SimpleNamespace(name=name, active=True, parameters={"properties": {"x": {"type": "number"}}},
                  handler_module_path=self.plugin.__class__.__module__) for name in pc.TOOL_PERMISSIONS]
        other = types.SimpleNamespace(name="other_plugin_tool", active=True)
        inactive = types.SimpleNamespace(name="pc_desktop_open_app", active=False, parameters={},
                  handler_module_path=self.plugin.__class__.__module__)
        tools = [tool for tool in tools if tool.name != inactive.name] + [inactive]
        self.plugin.context = types.SimpleNamespace(get_llm_tool_manager=lambda: types.SimpleNamespace(func_list=tools))
        original = ToolSet([other])
        req = types.SimpleNamespace(func_tool=original, system_prompt="原有人格与记忆")
        asyncio.run(self.plugin.expose_authorized_pc_tools(Event(), req))
        self.assertIsNot(req.func_tool, original)
        self.assertEqual(original.tools, [other])
        self.assertIs(req.func_tool.get_tool("other_plugin_tool"), other)
        self.assertIsNotNone(req.func_tool.get_tool("pc_desktop_click"))
        self.assertEqual(req.func_tool.get_tool("pc_desktop_click").parameters["properties"]["x"]["type"], "integer")
        self.assertEqual(next(tool for tool in tools if tool.name == "pc_desktop_click").parameters["properties"]["x"]["type"], "number")
        self.assertIsNone(req.func_tool.get_tool("pc_desktop_open_app"))
        self.assertIn("原有人格与记忆", req.system_prompt)
        self.assertIn("不依赖 AstrBot 内置 ComputerUse", req.system_prompt)
        unauthorized = types.SimpleNamespace(func_tool=req.func_tool, system_prompt="")
        asyncio.run(self.plugin.expose_authorized_pc_tools(Event("99999999"), unauthorized))
        self.assertFalse(any(tool.name in pc.TOOL_PERMISSIONS for tool in unauthorized.func_tool.tools))
        self.assertIsNotNone(req.func_tool.get_tool("pc_desktop_click"))

    def test_file_paths_still_reject_escape_when_desktop_enabled(self):
        self.config["desktop_enabled"] = True
        with self.assertRaises(ValueError): self.plugin._resolve("../outside.txt")


class BackendPermissionTests(unittest.TestCase):
    def test_member_desktop_requires_separate_flag(self):
        config = {}
        pc_access.set_member(config, "30000001", True, True, True)
        self.assertFalse(config["pc_access"]["members"]["30000001"]["desktop"])
        pc_access.set_member(config, "30000002", False, False, False, desktop=True)
        self.assertTrue(config["pc_access"]["members"]["30000002"]["desktop"])
        with self.assertRaises(ValueError):
            pc_access.set_member(config, "30000002", True, True, True, desktop="false")

    def test_global_desktop_requires_confirm_and_config_sync_contains_both_gates(self):
        with patch.object(config_store, "load", return_value={"owner_qq": "123456789"}):
            with self.assertRaisesRegex(ValueError, "明确确认"):
                pc_access.configure(True, "C:/example", confirm=False, desktop_enabled=True)
        with tempfile.TemporaryDirectory(prefix="aiwu-pc-config-") as temporary:
            workspace = Path(temporary, "workspace")
            workspace.mkdir()
            config = dict(owner_qq="123456789", pc_access=dict(enabled=True, desktop_enabled=True, members={
                "30000002": dict(read=True, write=True, run=True, desktop=True)}))
            with patch.object(config_store, "load", return_value=config), \
                 patch.object(pc_access, "ASTRBOT", Path(temporary)), \
                 patch.object(pc_access, "_workspace", return_value=workspace):
                path = pc_access._write_plugin_config(config["pc_access"])
                saved = json.loads(path.read_text("utf-8"))
                self.assertTrue(saved["enabled"] and saved["desktop_enabled"])
                self.assertTrue(saved["members"]["30000002"]["desktop"])
                disabled = pc_access._write_plugin_config({**config["pc_access"], "enabled": False})
                self.assertFalse(json.loads(disabled.read_text("utf-8"))["desktop_enabled"])


class NativeContractTests(unittest.TestCase):
    def test_bounds_validate_integer_physical_pixels_including_negative_monitor_origin(self):
        bounds = dict(x=-100, y=0, width=200, height=100)
        self.assertEqual(native.point(-10, 30, bounds), (-10, 30))
        for x, y in ((100, 0), (-101, 2), (1.5, 2), (True, 2), (0, -1)):
            with self.assertRaises(ValueError): native.point(x, y, bounds)

    def test_key_mapping_rejects_source_injection_and_secure_desktop_sequence(self):
        self.assertEqual(native.key_codes("Ctrl+L"), [0x11, ord("L")])
        for keys in ("Ctrl+L;Remove-Item", "Ctrl+Alt+Delete", "Ctrl+Ctrl+A", "{ENTER}"):
            with self.assertRaises(ValueError): native.key_codes(keys)

    def test_png_encoder_roundtrips_real_bgra_pixels(self):
        png = native.encode_png(2, 1, bytes([0, 0, 255, 0, 255, 0, 0, 0]))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        offset, image_data = 8, b""
        while offset < len(png):
            length = struct.unpack(">I", png[offset:offset + 4])[0]
            kind = png[offset + 4:offset + 8]
            data = png[offset + 8:offset + 8 + length]
            if kind == b"IDAT": image_data += data
            offset += length + 12
        self.assertEqual(zlib.decompress(image_data), b"\0\xff\0\0\0\0\xff")

    def test_unicode_and_keyboard_events_only_reach_mock_sendinput(self):
        desktop = object.__new__(native.WindowsDesktop)
        desktop._lock = threading.RLock()
        desktop._foreground, desktop._send = Mock(), Mock()
        desktop.type_text("鱼😀\n", 77)
        inputs = desktop._send.call_args[0][0]
        self.assertEqual(len(inputs), 8)
        self.assertEqual([inputs[i].ki.wScan for i in (0, 2, 4)], [ord("鱼"), 0xD83D, 0xDE00])
        self.assertEqual(inputs[6].ki.wVk, 0x0D)
        desktop.press_keys("Ctrl+L", 77)
        self.assertEqual([entry.ki.wVk for entry in desktop._send.call_args[0][0]], [0x11, ord("L"), ord("L"), 0x11])

    def test_stale_foreground_cancels_before_mouse_motion(self):
        desktop = object.__new__(native.WindowsDesktop)
        desktop._lock = threading.RLock()
        desktop.user32 = types.SimpleNamespace(GetForegroundWindow=Mock(return_value=77), SetCursorPos=Mock())
        desktop._send = Mock()
        with self.assertRaisesRegex(RuntimeError, "前台窗口已改变"):
            desktop.click(10, 20, window_id=66)
        desktop.user32.SetCursorPos.assert_not_called()
        desktop._send.assert_not_called()
        with self.assertRaisesRegex(ValueError, "有效的前台"):
            desktop._foreground(0)

    def test_partial_keyboard_batch_releases_modifiers_before_reporting_error(self):
        desktop = object.__new__(native.WindowsDesktop)
        desktop._lock = threading.RLock()
        desktop._foreground = Mock()
        desktop._send = Mock(side_effect=[RuntimeError("partial batch"), None])
        with self.assertRaisesRegex(RuntimeError, "partial batch"):
            desktop.press_keys("Ctrl+L", 77)
        self.assertEqual(desktop._send.call_count, 2)
        releases = desktop._send.call_args_list[1].args[0]
        self.assertEqual([event.ki.wVk for event in releases], [ord("L"), 0x11])
        self.assertTrue(all(event.ki.dwFlags & 0x0002 for event in releases))

    def test_partial_mouse_batch_releases_button_before_reporting_error(self):
        desktop = object.__new__(native.WindowsDesktop)
        desktop.user32 = types.SimpleNamespace()
        desktop._lock = threading.RLock()
        desktop._foreground, desktop.move = Mock(), Mock()
        desktop._send = Mock(side_effect=[RuntimeError("partial mouse"), None])
        with self.assertRaisesRegex(RuntimeError, "partial mouse"):
            desktop.click(12, 34, "left", 1, 77)
        self.assertEqual(desktop._send.call_count, 2)
        self.assertEqual(desktop._send.call_args_list[1].args[0][0].mi.dwFlags, 0x0004)

    def test_open_app_rejects_commands_scripts_and_url_credentials_without_launching(self):
        desktop = object.__new__(native.WindowsDesktop)
        with patch.object(native.webbrowser, "open") as browser, patch.object(native.os, "startfile", create=True) as start:
            for target in ("cmd.exe /c echo bad", "C:/test.ps1", "https://user:password@example.test", "javascript:alert(1)"):
                with self.assertRaises(ValueError): desktop.open_app(target)
            browser.assert_not_called()
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
