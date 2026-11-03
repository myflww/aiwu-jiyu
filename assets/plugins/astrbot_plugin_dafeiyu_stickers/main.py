"""Bounded mood-driven local stickers, attached to the current QQ reply only."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import re
import time

from astrbot.api import ToolSet
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.message_type import MessageType
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.message.components import Image, Plain

from .catalog import EMOTIONS, choose, load_catalog, verify_asset

PLUGIN_NAME = "astrbot_plugin_dafeiyu_stickers"
SENT_KEY = "dafeiyu_sticker_sent"
UNCERTAIN_KEY = "dafeiyu_sticker_delivery_uncertain"
TOOL_ROUND_KEY = "dafeiyu_sticker_tool_round"
DECLINE_STICKERS = re.compile(r"(?:不要|别|不用|停止|不许)(?:再|给我|继续|给)?(?:发(?:送)?|用|使用)?(?:表情(?:包)?|贴纸|sticker|图片|图)", re.I)
MARKER = re.compile(r"\[(?:大肥鱼表情|sticker)\s*[:：]\s*([^\[\]\r\n\x00]{1,50})\]", re.I)
PROTECTED = re.compile(
    r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`+[^`\r\n]*(?:`+|$)"
    r"|!?\[[^\]\r\n]*\]\([^\)\r\n]*\)|https?://[^\s<>]+"
    r"|<(?:thought|think|analysis|reasoning)(?:\s[^>]*)?>[\s\S]*?(?:</(?:thought|think|analysis|reasoning)>|$)", re.I)


def strip_markers(chain):
    """Only remove our own non-code/non-link markers; retain all other segments."""
    texts, spans, position = [], [], 0
    for component in chain:
        text = component.text if isinstance(component, Plain) and isinstance(component.text, str) else "\x00"
        texts.append(text)
        spans.append((position, position + len(text), component))
        position += len(text)
    joined = "".join(texts)
    protected = [(match.start(), match.end()) for match in PROTECTED.finditer(joined)]
    matches = [match for match in MARKER.finditer(joined) if not any(
        left < match.end() and match.start() < right for left, right in protected)]
    if not matches:
        return list(chain), []
    # A marker can be fragmented by another formatter into multiple Plain
    # segments. Absolute spans make that safe without flattening Face/At/etc.
    result = []
    for left, right, component in spans:
        if not isinstance(component, Plain):
            result.append(component)
            continue
        text = component.text
        edits = [(max(left, match.start()) - left, min(right, match.end()) - left)
                 for match in matches if left < match.end() and match.start() < right]
        for start, end in reversed(edits):
            text = text[:start] + text[end:]
        if text:
            result.append(Plain(text))
    return result, [match.group(1).strip() for match in matches]


@register(PLUGIN_NAME, "myflww", "大肥鱼自动心情贴纸与素材库", "1.1.0")
class DafeiyuStickers(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if isinstance(config, Mapping) else {}
        self.catalog = load_catalog()
        self._last_sent: dict[str, tuple[float, object]] = {}
        self._previous_asset: dict[str, str] = {}
        self._sequence: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _cooldown(self) -> int:
        seconds = self.config.get("cooldown_seconds", 30)
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            return 30
        return max(10, min(300, seconds))

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _selection(self) -> str:
        value = self.config.get("pack_selection", "all")
        return value if isinstance(value, str) and value in {"all", "original", "community"} else "all"

    def _choose(self, emotion: str, origin: str):
        return choose(emotion, self.catalog, pack_selection=self._selection(),
                      avoid_id=self._previous_asset.get(origin, ""), index=self._sequence.get(origin, 0))

    @staticmethod
    def _requested_sticker(event) -> bool:
        message = str(event.get_message_str() or "").casefold()
        return not DECLINE_STICKERS.search(message) and any(word in message for word in ("表情", "贴纸", "sticker"))

    @staticmethod
    def _current_qq_event(event) -> str:
        try:
            if isinstance(event, CronMessageEvent):
                return ""
            # Scheduled jobs and WebChat have different platform names, even
            # when their unified origin happens to point at a QQ conversation.
            if event.get_platform_name() not in {"aiocqhttp", "qq", "qq_official"}:
                return ""
            if event.get_message_type() not in {MessageType.FRIEND_MESSAGE, MessageType.GROUP_MESSAGE}:
                return ""
            origin = event.unified_msg_origin
            platform_id = str(event.get_platform_id() or "")
            sender, self_id = str(event.get_sender_id() or ""), str(event.get_self_id() or "")
            message_id = getattr(getattr(event, "message_obj", None), "message_id", "")
            if (not isinstance(origin, str) or not origin or len(origin) > 512 or not message_id
                    or not platform_id or not origin.startswith(platform_id + ":")
                    or not sender or (self_id and sender == self_id)):
                return ""
            return origin
        except (AttributeError, TypeError):
            return ""

    async def _reserve(self, event, origin: str, item: dict):
        now, reservation = self._now(), object()
        async with self._lock:
            if event.get_extra(SENT_KEY, False):
                return None, "这条回复已经尝试发送过贴纸，请勿重复发送。"
            previous = self._last_sent.get(origin)
            if previous and now - previous[0] < self._cooldown():
                return None, "刚刚已发过贴纸，稍等一会儿再发，避免刷屏。"
            self._last_sent = {key: value for key, value in self._last_sent.items() if now - value[0] < 3600}
            self._previous_asset = {key: value for key, value in self._previous_asset.items() if key in self._last_sent}
            self._sequence = {key: value for key, value in self._sequence.items() if key in self._last_sent}
            if origin not in self._last_sent and len(self._last_sent) >= 512:
                return None, "贴纸正在忙，稍后再试。"
            self._last_sent[origin] = (now, reservation)
            self._previous_asset[origin] = item["id"]
            self._sequence[origin] = self._sequence.get(origin, 0) + 1
            event.set_extra(SENT_KEY, True)
        return reservation, ""

    async def _send(self, event, emotion: str, *, explicit_command=False) -> str:
        if self.config.get("enabled", True) is not True:
            return "大肥鱼贴纸还未启用。"
        origin = self._current_qq_event(event)
        if not origin:
            return "贴纸只能回应当前收到的 QQ 消息。"
        if not explicit_command:
            if not self._requested_sticker(event):
                return "只有对方明确要看表情或贴纸时，才发送大肥鱼贴纸。"
        item = self._choose(emotion, origin)
        if not item:
            return "当前图库没有这张贴纸。可选开心、委屈、生气、害羞、晚安、喜欢、疑惑、日常，实际以已选图库为准。"
        try:
            path = await asyncio.to_thread(verify_asset, item)
        except (OSError, ValueError):
            return "这张随附贴纸校验失败，请在桌面端修复插件文件。"
        reservation, message = await self._reserve(event, origin, item)
        if reservation is None:
            return message
        try:
            await event.send(event.image_result(str(path)))
        except Exception:
            # A timeout can occur after QQ/NapCat accepted the image. Retain
            # the attempt and cooldown; retrying this event could duplicate it.
            event.set_extra(UNCERTAIN_KEY, True)
            return "表情已尝试发送，但暂时没能确认是否送达；为避免重复，请先查看 QQ，不要立即重试。"
        return f"已向当前会话发送一张「{item['label']}」大肥鱼贴纸，不要重复发送。"

    @filter.on_llm_request()
    async def match_before_request(self, event: AstrMessageEvent, req):
        """Offer bounded local moods so a text-only model can choose naturally."""
        origin = self._current_qq_event(event)
        if not origin or self.config.get("enabled", True) is not True:
            return
        # Preserve explicit requests even when the persona has tools=[] or
        # auto matching is disabled. Never activate a global/inactive tool.
        if self._requested_sticker(event):
            try:
                manager = self.context.get_llm_tool_manager()
                tool = next((item for item in manager.func_list if item.name == "send_dafeiyu_sticker"
                    and getattr(item, "active", True)
                    and getattr(item, "handler_module_path", "") == self.__class__.__module__), None)
                if tool is not None:
                    req.func_tool = ToolSet(tools=list(getattr(getattr(req, "func_tool", None), "tools", []) or []))
                    req.func_tool.add_tool(tool)
            except (AttributeError, TypeError):
                pass
        if (self.config.get("auto_match_enabled", True) is not True
                or DECLINE_STICKERS.search(str(event.get_message_str() or ""))
                or event.get_extra(SENT_KEY, False) or event.get_extra(TOOL_ROUND_KEY, False)):
            return
        previous = self._last_sent.get(origin)
        if previous and self._now() - previous[0] < self._cooldown():
            return
        candidates = []
        for emotion in EMOTIONS:
            item = self._choose(emotion, origin)
            if item:
                candidates.append(f"{emotion}：{item['label'][:40]}")
        if not candidates:
            return
        marker = "[大肥鱼本地心情贴纸]"
        if marker in (req.system_prompt or ""):
            return
        req.system_prompt = (req.system_prompt or "") + "\n\n" + marker + (
            "\n当前是实际收到的 QQ 聊天，可按这次对话的气氛自然搭配一张表情包，无需对方明确索要。"
            "不需要每轮都发；严肃解释、代码/链接答复、工具操作结果以及对方要求不用表情时不加。"
            "若合适，在正常回复末尾加一个内部标记，例如 [大肥鱼表情:happy]；最多一个。"
            "标记会被系统替换为本地图片，不要解释标记、不写外部网址或文件路径，也不要同时调用发送工具。"
            "可以只正常聊天。当前可用心情：" + "；".join(candidates[:12]) + "。")

    @filter.on_llm_tool_respond()
    async def after_tool(self, event: AstrMessageEvent, tool, tool_args, tool_result):
        # Keep operational/tool rounds clean even when another tool already
        # sent an image or a direct result. The explicit sticker tool still
        # responds to its own request; the decorator must not add a second one.
        if self._current_qq_event(event):
            event.set_extra(TOOL_ROUND_KEY, True)

    @filter.on_decorating_result()
    async def attach_matching_sticker(self, event: AstrMessageEvent):
        origin = self._current_qq_event(event)
        result = event.get_result()
        if (not origin or self.config.get("enabled", True) is not True or result is None
                or not result.is_llm_result() or not result.chain
                or getattr(result, "type", None) in {"tool_direct_result", "tool_call", "tool_result", "system", "reasoning", "thinking"}):
            return
        chain, markers = strip_markers(result.chain)
        if not markers:
            return
        result.chain = chain

        def keep_visible(message: str):
            if not any(not isinstance(part, Plain) or part.text.strip() for part in result.chain):
                result.chain = [Plain(message)]

        if (self.config.get("auto_match_enabled", True) is not True or event.get_extra(SENT_KEY, False)
                or DECLINE_STICKERS.search(str(event.get_message_str() or ""))
                or event.get_extra(TOOL_ROUND_KEY, False) or any(isinstance(part, Image) for part in chain)):
            keep_visible("表情发送状态还没确认，先别重复发啦。" if event.get_extra(UNCERTAIN_KEY, False)
                         else "表情已经发过去啦。" if event.get_extra(SENT_KEY, False) else "我在，先用文字陪你聊聊。")
            return
        item = next((candidate for mood in markers if (candidate := self._choose(mood, origin)) is not None), None)
        if item is None:
            keep_visible("这张表情暂时没匹配上，先聊聊吧。")
            return
        try:
            path = await asyncio.to_thread(verify_asset, item)
        except (OSError, ValueError):
            keep_visible("这张表情暂时不可用，先用文字陪你聊聊。")
            return  # Keep the text reply; never expose a private exception/path.
        reservation, _ = await self._reserve(event, origin, item)
        if reservation is not None:
            result.chain.append(Image.fromFileSystem(str(path)))
        else:
            keep_visible("表情先歇一会儿，我在呢。")

    @filter.llm_tool(name="send_dafeiyu_sticker")
    async def send_dafeiyu_sticker(self, event: AstrMessageEvent, emotion: str):
        """仅在用户明确想看大肥鱼表情或贴纸时，发送一张随附图片到当前 QQ 会话。每条回复最多一张，默认间隔30秒。不接受收件人、网址或文件路径。

        Args:
            emotion(string): 已选图库内的心情或贴纸名，支持happy/开心、sad/委屈、angry/生气、shy/害羞、sleepy/晚安、love/喜欢、confused/疑惑、daily/日常。
        """
        return await self._send(event, emotion)

    @filter.command("肥鱼表情", alias={"大肥鱼表情"})
    async def sticker_command(self, event: AstrMessageEvent, emotion: str = ""):
        """查看大肥鱼贴纸列表，或按心情发送一张，如 /肥鱼表情 开心。"""
        if not self._current_qq_event(event):
            return
        if self.config.get("enabled", True) is not True:
            yield event.plain_result("大肥鱼贴纸还未启用。")
        elif not emotion or emotion in {"列表", "帮助", "list", "help"}:
            yield event.plain_result("大肥鱼心情贴纸：开心、委屈、生气、害羞、晚安、喜欢，社区图库另有疑惑与日常。\n发送 /肥鱼表情 开心 可查看一张；开启自动匹配后也会按聊天气氛搭配，每条回复最多一张，默认间隔30秒。")
        else:
            result = await self._send(event, emotion, explicit_command=True)
            if not result.startswith("已向当前会话发送"):
                yield event.plain_result(result)
        event.stop_event()
