"""Bounded mood-driven local stickers, attached to the current QQ reply only."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import math
import re
import time

from astrbot.api import ToolSet
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.message_type import MessageType
from astrbot.core.cron.events import CronMessageEvent
from astrbot.core.db.po import Conversation
from astrbot.core.message.components import Image, Plain

from .catalog import EMOTIONS, choose, load_catalog, verify_asset

PLUGIN_NAME = "astrbot_plugin_dafeiyu_stickers"
SENT_KEY = "dafeiyu_sticker_sent"
UNCERTAIN_KEY = "dafeiyu_sticker_delivery_uncertain"
TOOL_ROUND_KEY = "dafeiyu_sticker_tool_round"
INTENT_KEY = "dafeiyu_sticker_request_intent"
OFFER_KEY = "dafeiyu_sticker_pending_offer"
AUTO_RESERVATION_KEY = "dafeiyu_sticker_auto_reservation"
RETRY_KEY = "dafeiyu_sticker_short_retry"
SELECTED_KEY = "dafeiyu_sticker_selected_emotion"
RECEIPT_KEY = "dafeiyu_sticker_final_send_receipt"
OFFER_TTL = 120
EXPLICIT_INTERVAL = 3
DECLINE_STICKERS = re.compile(r"(?:不要|别|不用|停止|不许)(?:再|给我|继续|给)?(?:发(?:送)?|用|使用)?(?:表情(?:包)?|贴纸|sticker|图片|图)", re.I)
CONFIRMATION = re.compile(r"(?:可以|好|好的|好呀|好啊|好哒|行|行呀|嗯|嗯嗯|要|要呀|想看|来吧|来一张|再来一张|你发|发吧|发来|发给我|继续发|安排上)(?:呀|啊|吧|哦|呢|～|~|啦|哇|嘛|！|!|。|，|,|？|\?|\s)*", re.I)
PROPOSAL = re.compile(r"要不要|想不想|再(?:来|发)(?:一|1)?张|来(?:一|1)张|发(?:一|1)张|怎么样|好不好|好吗|要吗|想看吗")
MOOD_WORDS = {
    "happy": ("开心", "高兴", "快乐", "happy"), "sad": ("委屈", "伤心", "难过", "sad"),
    "angry": ("哈气", "生气", "愤怒", "angry"), "shy": ("害羞", "脸红", "shy"),
    "sleepy": ("晚安", "困困", "睡觉", "sleepy"), "love": ("喜欢", "比心", "爱心", "love"),
    "confused": ("疑惑", "困惑", "问号", "confused"), "daily": ("日常", "摸鱼", "daily"),
}
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


@register(PLUGIN_NAME, "myflww", "大肥鱼自动心情贴纸与素材库", "1.1.1")
class DafeiyuStickers(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if isinstance(config, Mapping) else {}
        self.catalog = load_catalog()
        self._last_sent: dict[str, tuple[float, object, str]] = {}
        self._previous_asset: dict[str, str] = {}
        self._sequence: dict[str, int] = {}
        self._offers: dict[tuple[str, str], dict] = {}
        self._seen_turns: dict[tuple[str, str], float] = {}
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
    def _turn_key(event, origin):
        return origin, str(event.get_sender_id())

    def _proposal_emotion(self, text, origin, *, sticker_context=False, selected=""):
        """Recognize a bounded offer, not arbitrary positive/quoted text."""
        if not isinstance(text, str) or not 1 <= len(text) <= 6000:
            return ""
        visible = MARKER.sub("", PROTECTED.sub(" ", text)).strip()
        # A previous declarative sentence must not choose the new offer's mood.
        clauses = [part.strip() for part in re.split(r"[。！!\n]+", visible) if part.strip()]
        clause = clauses[-1] if clauses else ""
        cues = list(PROPOSAL.finditer(clause))
        if not cues:
            return ""
        start = cues[0].start()
        # "害羞的怎么样" has the mood before the question word.
        if cues[0].group() in {"怎么样", "好不好", "好吗", "要吗", "想看吗"}:
            start = 0
        offer = clause[start:][:240]
        previous = self._last_sent.get(origin)
        recent_sent = previous and previous[2] == "sent" and 0 <= self._now() - previous[0] <= OFFER_TTL
        if (not sticker_context and not recent_sent
                and not any(word in clause.casefold() for word in ("表情", "贴纸", "sticker"))):
            return ""
        moods = {mood for mood, words in MOOD_WORDS.items() if any(
            re.search(r"\b" + word + r"\b", offer, re.I) if word.isascii() else word in offer for word in words)}
        if len(moods) != 1:
            return selected if not moods and selected in EMOTIONS and sticker_context else ""
        return next(iter(moods))

    def _history_offer(self, event, req, origin):
        """Recover only the last fresh SDK private turn after plugin reload.

        No database reads, generic req.contexts, or model-supplied history. Group
        history does not identify the addressed sender, so never recover it.
        """
        conv = getattr(req, "conversation", None)
        if (event.get_message_type() != MessageType.FRIEND_MESSAGE or not isinstance(conv, Conversation)
                or conv.user_id != origin or conv.platform_id != str(event.get_platform_id())
                or origin.rsplit(":", 1)[-1] != str(event.get_sender_id())
                or not isinstance(conv.updated_at, (int, float)) or isinstance(conv.updated_at, bool)
                or not 0 <= time.time() - conv.updated_at <= OFFER_TTL
                or not isinstance(conv.history, str) or len(conv.history) > 65536):
            return None
        try:
            history = json.loads(conv.history)
        except (ValueError, TypeError):
            return None
        if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
            return None
        last = history[-1]
        if last.get("role") != "assistant" or last.get("tool_calls") or not isinstance(last.get("content"), str):
            return None
        # Link a successful own tool result to its named tool call. Never infer
        # that arbitrary tool/system output or an image path is a sticker send.
        recent = history[-12:]
        user_positions = [i for i, row in enumerate(recent) if isinstance(row, dict) and row.get("role") == "user"]
        recent = recent[user_positions[-1]:] if user_positions else []
        ids = set()
        for row in recent:
            if not isinstance(row, dict) or row.get("role") != "assistant":
                continue
            calls = row.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 32:
                return None
            for call in calls:
                if (isinstance(call, dict) and isinstance(call.get("function"), dict)
                        and call["function"].get("name") == "send_dafeiyu_sticker" and isinstance(call.get("id"), str)):
                    ids.add(call["id"])
        sent = any(isinstance(row, dict) and row.get("role") == "tool" and row.get("tool_call_id") in ids
                   and isinstance(row.get("content"), str) and row["content"].startswith("已向当前会话发送一张") for row in recent)
        mood = self._proposal_emotion(last["content"], origin, sticker_context=sent)
        return {"emotion": mood, "at": self._now(), "message_id": "history"} if mood else None

    def _intent(self, event, req=None):
        cached = event.get_extra(INTENT_KEY)
        if isinstance(cached, dict):
            return cached
        origin = self._current_qq_event(event)
        intent = {"kind": "none", "emotion": ""}
        if not origin:
            return intent
        now = self._now()
        key = self._turn_key(event, origin)
        self._offers = {k: value for k, value in self._offers.items() if 0 <= now - value["at"] <= OFFER_TTL}
        self._seen_turns = {k: value for k, value in self._seen_turns.items() if now - value < 3600}
        first_turn = key not in self._seen_turns
        if len(self._seen_turns) < 512 or not first_turn:
            self._seen_turns[key] = now
        offer = self._offers.pop(key, None)
        message = str(event.get_message_str() or "").strip()
        confirms = len(message) <= 32 and CONFIRMATION.fullmatch(message) is not None
        if self._requested_sticker(event):
            intent["kind"] = "explicit"
        elif confirms and not DECLINE_STICKERS.search(message):
            if offer is None and first_turn and req is not None:
                offer = self._history_offer(event, req, origin)
                now = self._now()
            if (offer and 0 <= now - offer["at"] <= OFFER_TTL
                    and offer["message_id"] != str(event.message_obj.message_id)):
                intent = {"kind": "confirmation", "emotion": offer["emotion"], "offer": offer}
        # The immediately following unrelated message consumes the old offer.
        event.set_extra(INTENT_KEY, intent)
        return intent

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

    def _remaining(self, origin, *, explicit=False):
        previous = self._last_sent.get(origin)
        if not previous:
            return 0
        interval = EXPLICIT_INTERVAL if explicit and previous[2] == "sent" else self._cooldown()
        return max(0, math.ceil(interval - (self._now() - previous[0])))

    async def _delivery(self, origin, reservation, state):
        async with self._lock:
            previous = self._last_sent.get(origin)
            if previous and previous[1] is reservation:
                self._last_sent[origin] = (self._now(), reservation, state)

    async def _reserve(self, event, origin: str, item: dict, *, explicit=False):
        now, reservation = self._now(), object()
        async with self._lock:
            if event.get_extra(SENT_KEY, False):
                return None, "这条回复已经尝试发送过贴纸，请勿重复发送。"
            previous = self._last_sent.get(origin)
            remaining = self._remaining(origin, explicit=explicit)
            if previous and (remaining or previous[2] == "sending"):
                if explicit:
                    # Permit a direct "你发" retry after a short throttle, but
                    # never keep the offer alive beyond its original expiry.
                    intent = event.get_extra(INTENT_KEY, {})
                    if intent.get("kind") == "confirmation":
                        self._offers[self._turn_key(event, origin)] = intent["offer"]
                        event.set_extra(RETRY_KEY, True)
                if previous[2] in {"uncertain", "sending"}:
                    return None, f"上一张表情的送达状态还没确认，请先查看 QQ；至少 {max(1, remaining)} 秒后再试，避免重复。"
                return None, f"刚刚已发过贴纸，再等 {max(1, remaining)} 秒就能发，避免刷屏。"
            self._last_sent = {key: value for key, value in self._last_sent.items() if now - value[0] < 3600}
            self._previous_asset = {key: value for key, value in self._previous_asset.items() if key in self._last_sent}
            self._sequence = {key: value for key, value in self._sequence.items() if key in self._last_sent}
            if origin not in self._last_sent and len(self._last_sent) >= 512:
                return None, "贴纸正在忙，稍后再试。"
            self._last_sent[origin] = (now, reservation, "sending" if explicit else "auto")
            self._previous_asset[origin] = item["id"]
            self._sequence[origin] = self._sequence.get(origin, 0) + 1
            event.set_extra(SENT_KEY, True)
            event.set_extra(SELECTED_KEY, item["emotion"])
        return reservation, ""

    async def _send(self, event, emotion: str, *, explicit_command=False) -> str:
        if self.config.get("enabled", True) is not True:
            return "大肥鱼贴纸还未启用。"
        origin = self._current_qq_event(event)
        if not origin:
            return "贴纸只能回应当前收到的 QQ 消息。"
        intent = self._intent(event)
        if not explicit_command and intent["kind"] == "none":
            return "这次没有收到表情请求，也没有可承接的近期表情提议；先正常回应对方，不要要求对方重复固定口令。"
        if not explicit_command and intent["kind"] == "confirmation":
            # The accepted proposal chooses the mood, never an invented tool arg.
            emotion = intent["emotion"]
        item = self._choose(emotion, origin)
        if not item:
            return "当前图库没有这张贴纸。可选开心、委屈、生气、害羞、晚安、喜欢、疑惑、日常，实际以已选图库为准。"
        try:
            path = await asyncio.to_thread(verify_asset, item)
        except (OSError, ValueError):
            return "这张随附贴纸校验失败，请在桌面端修复插件文件。"
        reservation, message = await self._reserve(event, origin, item, explicit=True)
        if reservation is None:
            return message
        try:
            await event.send(event.image_result(str(path)))
        except asyncio.CancelledError:
            event.set_extra(UNCERTAIN_KEY, True)
            await asyncio.shield(self._delivery(origin, reservation, "uncertain"))
            raise
        except Exception:
            # A timeout can occur after QQ/NapCat accepted the image. Retain
            # the attempt and cooldown; retrying this event could duplicate it.
            event.set_extra(UNCERTAIN_KEY, True)
            await self._delivery(origin, reservation, "uncertain")
            return "表情已尝试发送，但暂时没能确认是否送达；为避免重复，请先查看 QQ，不要立即重试。"
        await self._delivery(origin, reservation, "sent")
        self._offers.pop(self._turn_key(event, origin), None)
        return f"已向当前会话发送一张「{item['label']}」大肥鱼贴纸，不要重复发送。"

    @filter.on_llm_request()
    async def match_before_request(self, event: AstrMessageEvent, req):
        """Offer bounded local moods so a text-only model can choose naturally."""
        origin = self._current_qq_event(event)
        if not origin or self.config.get("enabled", True) is not True:
            return
        # Preserve explicit requests even when the persona has tools=[] or
        # auto matching is disabled. Never activate a global/inactive tool.
        intent = self._intent(event, req)
        if intent["kind"] != "none":
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
            marker = "[大肥鱼表情请求]"
            if marker not in (req.system_prompt or ""):
                req.system_prompt = (req.system_prompt or "") + "\n\n" + marker + (
                    f"\n对方刚刚确认了本会话上一条表情提议，请调用 send_dafeiyu_sticker，emotion={intent['emotion']}。"
                    "无需再问、无需复述或要求固定口令；只能发本地这一种心情的一张图。"
                    if intent["kind"] == "confirmation" else
                    "\n对方这次明确要看表情，可调用 send_dafeiyu_sticker 发送一张合适的本地贴纸。") + (
                    "发送成功后不要重复发图；若短暂节流或送达不确定，按工具实际结果说明，不要声称已经发送。"
                    "这轮不要再添加内部配图标记。")
            return
        if (self.config.get("auto_match_enabled", True) is not True
                or DECLINE_STICKERS.search(str(event.get_message_str() or ""))
                or event.get_extra(SENT_KEY, False) or event.get_extra(TOOL_ROUND_KEY, False)):
            return
        if self._remaining(origin):
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

    @staticmethod
    def _track_final_send(event):
        """Compose with other wrappers; after_message_sent alone is no receipt.

        AstrBot also runs that hook after a caught send error. Keep uncertainty
        sticky across fragments so a later successful fragment cannot erase it.
        """
        if event.get_extra(RECEIPT_KEY) is not None:
            return
        event.set_extra(RECEIPT_KEY, "pending")
        original = event.send

        async def tracked(*args, **kwargs):
            try:
                value = await original(*args, **kwargs)
            except BaseException:
                event.set_extra(RECEIPT_KEY, "uncertain")
                raise
            if event.get_extra(RECEIPT_KEY) != "uncertain":
                event.set_extra(RECEIPT_KEY, "sent")
            return value

        event.send = tracked

    @filter.on_decorating_result()
    async def attach_matching_sticker(self, event: AstrMessageEvent):
        origin = self._current_qq_event(event)
        result = event.get_result()
        if (not origin or self.config.get("enabled", True) is not True or result is None
                or not result.is_llm_result() or not result.chain
                or getattr(result, "type", None) in {"tool_direct_result", "tool_call", "tool_result", "system", "reasoning", "thinking"}):
            return
        self._track_final_send(event)
        chain, markers = strip_markers(result.chain)
        # Stage only a normal visible reply; commit after the framework sends it.
        # A normal continuation after our own sticker tool may offer another one.
        text = "".join(part.text for part in chain if isinstance(part, Plain))
        if (not event.get_extra(TOOL_ROUND_KEY, False) or event.get_extra(SENT_KEY, False)):
            mood = self._proposal_emotion(text, origin,
                sticker_context=event.get_extra(SENT_KEY, False) and not event.get_extra(UNCERTAIN_KEY, False),
                selected=event.get_extra(SELECTED_KEY, ""))
            event.set_extra(OFFER_KEY, {"text": text[:6000], "emotion": mood})
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
            event.set_extra(AUTO_RESERVATION_KEY, reservation)
        else:
            keep_visible("表情先歇一会儿，我在呢。")

    @filter.after_message_sent()
    async def remember_sent_offer(self, event: AstrMessageEvent):
        origin = self._current_qq_event(event)
        if not origin or self.config.get("enabled", True) is not True:
            return
        bubbles = event.get_extra("dafeiyu_bubbles_status")
        delivered = event.get_extra(RECEIPT_KEY) == "sent" and bubbles in {None, "sent"}
        reservation = event.get_extra(AUTO_RESERVATION_KEY)
        if reservation is not None:
            state = "sent" if delivered else "uncertain"
            await self._delivery(origin, reservation, state)
            if state == "uncertain":
                event.set_extra(UNCERTAIN_KEY, True)
        staged = event.get_extra(OFFER_KEY)
        if not isinstance(staged, dict):
            return
        key = self._turn_key(event, origin)
        if not delivered or event.get_extra(UNCERTAIN_KEY, False):
            self._offers.pop(key, None)
            return
        final_text = event.get_extra("dafeiyu_bubbles_final_text") if bubbles == "sent" else staged["text"]
        if not isinstance(final_text, str):
            return
        mood = self._proposal_emotion(final_text, origin,
            sticker_context=event.get_extra(SENT_KEY, False), selected=event.get_extra(SELECTED_KEY, ""))
        if mood and len(self._offers) < 512:
            self._offers[key] = {"emotion": mood, "at": self._now(), "message_id": str(event.message_obj.message_id)}
        elif not event.get_extra(RETRY_KEY, False):
            self._offers.pop(key, None)

    @filter.llm_tool(name="send_dafeiyu_sticker")
    async def send_dafeiyu_sticker(self, event: AstrMessageEvent, emotion: str):
        """用户索要表情，或确认本会话刚刚的表情提议时，发送一张本地图片到当前 QQ 会话。不要求固定口令。每条回复最多一张；明确请求成功后至少间隔3秒，送达不确定时不可立即重试。不接受收件人、网址或文件路径。

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
            yield event.plain_result("大肥鱼心情贴纸：开心、委屈、生气、害羞、晚安、喜欢，社区图库另有疑惑与日常。\n发送 /肥鱼表情 开心 可查看一张，也能自然索要或接着表情提议说“可以呀”。每条回复最多一张；自动配图默认间隔30秒，明确请求成功后至少间隔3秒。")
        else:
            result = await self._send(event, emotion, explicit_command=True)
            if not result.startswith("已向当前会话发送"):
                yield event.plain_result(result)
        event.stop_event()
