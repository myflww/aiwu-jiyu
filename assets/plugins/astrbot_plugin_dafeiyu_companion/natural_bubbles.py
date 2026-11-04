"""Bounded, optional QQ chat bubbles, without replacing AstrBot's pipeline.

The installed AstrBot RespondStage sends each component independently when its
native segmented_reply is enabled. We deliberately yield to that setting, and
wrap only this incoming event's final send otherwise. The native respond stage
still owns after_message_sent and result cleanup. No model calls or new targets.

Primary references: https://docs.astrbot.app/use/webui.html and
https://github.com/AstrBotDevs/AstrBot/issues/10047 . This is original code.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import re


STATUS_KEY = "dafeiyu_bubbles_status"
COUNT_KEY = "dafeiyu_bubbles_sent_count"
ATTEMPT_KEY = "dafeiyu_bubbles_attempted_count"
TEXT_KEY = "dafeiyu_bubbles_final_text"
TOOL_KEY = "dafeiyu_bubbles_tool_round"
_WRAPPER_ATTR = "_dafeiyu_natural_send_wrapper"
_OBSERVED_ATTR = "_dafeiyu_natural_observation"
_SESSION_ATTR = "_dafeiyu_natural_session"
_BAD_TYPES = {"tool_direct_result", "tool_call", "tool_result", "system", "reasoning", "thinking", "break", "agent_stats"}
_PROTECTED = re.compile(
    r"`|~~~|\b(?:https?|ftp|file)://|\bwww\.|\[[^\]\n]*\]\(|!\[|\[CQ:|"
    r"<\/?(?:think|analysis|reasoning)\b|\[大肥鱼表情:|\[表情:|"
    r"(?m:^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)、]\s*|>|\|))",
    re.IGNORECASE,
)
_BOUNDARY = re.compile(r"(?:[。！？!?]+[”’」』\"']*|(?<=[A-Za-z])\.(?=\s|$)|\n\s*\n)\s*")
_FORMAL = re.compile(r"(?:参考文献|技术方案|执行结果|法律意见|诊断结论|步骤\s*\d|以下是.{0,8}(?:报告|方案|代码))")
_TASK = re.compile(r"(?:写(?:一份|一篇)|代码|脚本|表格|论文|方案|报告|详细(?:解释|说明)|完整(?:内容|步骤|文稿)|操作电脑|修复|部署|安装|正式|重要|严肃|不要分条|别分条|一条发完|合同|法律|财务)")
_MIN_CHAT_CHARS = 36
_MIN_BUBBLE_CHARS = 12


def split_text(text: str) -> list[str]:
    """Split at complete sentence/paragraph boundaries, preserving every char.

    Tiny acknowledgments and structured/technical answers remain whole.
    The 36/12 character limits are product defaults, not research optima.
    Complete sentence boundaries are required even for a short casual reply.
    """
    if not isinstance(text, str) or not _MIN_CHAT_CHARS <= len(text.strip()) <= 1600 or _PROTECTED.search(text) or _FORMAL.search(text):
        return [text]
    boundaries = [match.end() for match in _BOUNDARY.finditer(text) if match.end() < len(text)]
    desired = min(4, max(2, (len(text) + 279) // 280))
    for count in range(desired, 1, -1):
        cuts, previous = [], 0
        for part in range(1, count):
            candidates = [point for point in boundaries
                          if len(text[previous:point].strip()) >= _MIN_BUBBLE_CHARS
                          and len(text[point:].strip()) >= _MIN_BUBBLE_CHARS * (count - part)]
            if not candidates:
                break
            point = min(candidates, key=lambda item: abs(item - len(text) * part / count))
            cuts.append(point)
            previous = point
        if len(cuts) == count - 1:
            points = [0, *cuts, len(text)]
            return [text[points[index]:points[index + 1]] for index in range(count)]
    return [text]


def _kind(component) -> str:
    return type(component).__name__


def _plan(message) -> tuple[list, str]:
    """Keep headers on the first bubble and a single trailing sticker on last.

    Inline Face and mixed media are kept as one original chain. In particular,
    an image, Face, record, file or tool result never becomes its own bubble.
    """
    if (getattr(message, "type", None) in _BAD_TYPES or getattr(message, "use_t2i_", None) is True
            or getattr(message, "use_markdown_", None) is True):
        return [], ""
    chain = list(getattr(message, "chain", ()) or ())
    headers = []
    while chain and _kind(chain[0]) in {"At", "Reply"}:
        headers.append(chain.pop(0))
    trailing = []
    if chain and _kind(chain[-1]) == "Image":
        trailing = [chain.pop()]
    if not chain or any(_kind(part) != "Plain" for part in chain):
        return [], ""
    # Adjacent Plain components carry no intervening media; join without adding
    # or removing characters. derive() preserves host message metadata.
    text = "".join(str(part.text) for part in chain)
    chunks = split_text(text)
    if len(chunks) < 2 or not callable(getattr(message, "derive", None)):
        return [], text
    plan = []
    for index, chunk in enumerate(chunks):
        # AstrBot 4.29.4 components use pydantic.v1: copy.copy shares __dict__.
        # Mutating that shallow copy rewrites *all* planned packets and the
        # original reply to the last chunk. Deep-copy before changing text.
        plain = deepcopy(chain[0])
        plain.text = chunk
        components = [*headers, plain] if index == 0 else [plain]
        if index == len(chunks) - 1:
            components.extend(trailing)
        plan.append(message.derive(components))
    return plan, text


def _identity(event):
    try:
        origin = event.unified_msg_origin
        sender, bot = str(event.get_sender_id()), str(event.get_self_id())
        message_id = str(event.message_obj.message_id or "")
        kind = str(getattr(event.get_message_type(), "value", event.get_message_type()))
        if (event.get_platform_name() not in {"aiocqhttp", "qq"} or not sender.isdigit()
                or sender == bot or not message_id or len(message_id) > 180
                or not isinstance(origin, str) or len(origin) > 300
                or origin.split(":", 1)[0] != event.get_platform_id()
                or kind not in {"FriendMessage", "friend", "GroupMessage", "group"}):
            return None
        if (kind in {"FriendMessage", "friend"} and origin != f"{event.get_platform_id()}:FriendMessage:{sender}"
                or kind in {"GroupMessage", "group"} and ":GroupMessage:" not in origin):
            return None
        # A member's new group input interrupts only that member's own reply,
        # never another member's independently addressed turn.
        scope = origin if kind in {"FriendMessage", "friend"} else origin + "\0" + sender
        return scope, message_id
    except (AttributeError, TypeError, ValueError):
        return None


def _host_allows(config: dict, result) -> bool:
    if not isinstance(config, dict):
        return False
    platform = config.get("platform_settings", {})
    provider = config.get("provider_settings", {})
    if not isinstance(platform, dict) or not isinstance(provider, dict):
        return False
    segmented = platform.get("segmented_reply")
    if not isinstance(segmented, dict) or segmented.get("enable") is not False:
        return False  # Unknown host versions and native segmentation own delivery.
    tts = config.get("provider_tts_settings", {})
    return (not config.get("t2i") and not provider.get("streaming_response")
            and not provider.get("display_reasoning_text")
            and isinstance(tts, dict) and not tts.get("enable")
            and getattr(result, "use_t2i_", None) is not True
            and getattr(result, "async_stream", None) is None)


@dataclass
class _Session:
    message_id: str
    signal: asyncio.Event = field(default_factory=asyncio.Event)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class _Delivery:
    claimed: bool = False
    status: str = ""
    sent: int = 0
    attempted: int = 0
    text: str = ""


class BubbleReplies:
    """Per-plugin memory-only coordinator; caller enforces real QQ session grants."""

    def __init__(self):
        self._sessions = OrderedDict()
        self._seen = OrderedDict()
        self._deliveries = OrderedDict()
        self._closed = False

    def observe(self, event):
        key = _identity(event)
        if self._closed or key is None or getattr(event, _OBSERVED_ATTR, None) == key:
            return
        origin, message_id = key
        if key in self._seen:
            setattr(event, _OBSERVED_ATTR, key)
            setattr(event, _SESSION_ATTR, self._seen[key])
            return  # A duplicate older event must not become the newest turn.
        current = self._sessions.get(origin)
        if current is None or current.message_id != message_id:
            if current is not None:
                current.signal.set()
            self._sessions[origin] = _Session(message_id, send_lock=current.send_lock) if current else _Session(message_id)
        self._sessions.move_to_end(origin)
        while len(self._sessions) > 128:
            _, previous = self._sessions.popitem(last=False)
            previous.signal.set()
        setattr(event, _OBSERVED_ATTR, key)
        setattr(event, _SESSION_ATTR, self._sessions[origin])
        self._seen[key] = self._sessions[origin]
        while len(self._seen) > 256:
            self._seen.popitem(last=False)

    @staticmethod
    def mark_tool(event):
        event.set_extra(TOOL_KEY, True)

    def close(self):
        self._closed = True
        for session in self._sessions.values():
            session.signal.set()
        self._sessions.clear()
        self._seen.clear()
        self._deliveries.clear()

    def _current(self, key, session) -> bool:
        return not self._closed and self._sessions.get(key[0]) is session and not session.signal.is_set()

    @staticmethod
    def _selected(key, text: str, frequency: str) -> bool:
        probability = {"occasional": 0.30, "often": 0.70}.get(frequency, 0.30)
        digest = hashlib.sha256(("\0".join(key) + "\0" + text).encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") / 2**32 < probability

    @staticmethod
    def _delay(key, index: int) -> float:
        digest = hashlib.sha256(("\0".join(key) + str(index)).encode("utf-8")).digest()
        return 0.8 + int.from_bytes(digest[:2], "big") / 65535

    async def _pause(self, signal: asyncio.Event, seconds: float):
        try:
            await asyncio.wait_for(signal.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _report(event, delivery):
        event.set_extra(STATUS_KEY, delivery.status)
        event.set_extra(COUNT_KEY, delivery.sent)
        event.set_extra(ATTEMPT_KEY, delivery.attempted)
        event.set_extra(TEXT_KEY, delivery.text)

    async def prepare(self, event, options: dict, guard_async, host_config: dict | None = None) -> bool:
        """Install one wrapper, never send from the decorating hook itself.

        guard_async() must recheck current owner, option, plugin and LLM/session
        authorization. It is called before *every* actual bubble transmission.
        An exception/ambiguous adapter failure stops delivery; we never retry.
        """
        key = _identity(event)
        result = event.get_result()
        if (self._closed or key is None
                or result is None or not result.is_llm_result() or getattr(result, "type", None) in _BAD_TYPES
                or not _host_allows(host_config, result)
                or event.get_extra(TOOL_KEY, False) or event.get_extra("dafeiyu_sticker_tool_round", False)
                or event.get_extra("agent_user_aborted", False)):
            return False
        if getattr(event, _WRAPPER_ATTR, None) is self:
            return True
        self.observe(event)
        session = getattr(event, _SESSION_ATTR, None)
        if session is None or session.message_id != key[1]:
            return False
        original_send = event.send
        # A cooperating image plugin delegates exact actual packets to us.
        # This flag avoids counting a whole unsent split chain as image sent.
        setattr(event, "_dafeiyu_sticker_bubble_packets", True)

        async def send_final(message):
            # Intermediate tool/status/plugin sends use the original host path.
            if (getattr(message, "type", None) in _BAD_TYPES or event.get_extra(TOOL_KEY, False)
                    or event.get_extra("dafeiyu_sticker_tool_round", False)):
                return await original_send(message)
            previous = self._deliveries.get(key)
            if previous is not None and previous.claimed:
                self._report(event, previous)
                return
            plan, text = _plan(message)
            try:
                threshold = int(host_config.get("platform_settings", {}).get("forward_threshold", 1500))
            except (TypeError, ValueError):
                threshold = 1500
            incoming = getattr(event, "get_message_str", lambda: "")() or ""
            if (options.get("natural_bubbles_enabled") is not True or _TASK.search(incoming)
                    or event.get_extra("dafeiyu_formal_response", False)
                    or not plan or len(text) > threshold
                    or not self._selected(key, text, options.get("natural_bubbles_frequency", "occasional"))):
                plan = [message]
                text = "".join(str(getattr(part, "text", "")) for part in getattr(message, "chain", ()))
            delivery = self._deliveries.setdefault(key, _Delivery())
            self._deliveries.move_to_end(key)
            while len(self._deliveries) > 256:
                self._deliveries.popitem(last=False)
            # Claim before any await. Repeated results/retries for an incoming
            # message cannot restart an already-sent or uncertain split reply.
            if delivery.claimed:
                self._report(event, delivery)
                return
            delivery.claimed = True
            delivery.status = "sending"
            delivery.text = text[:1600]
            self._report(event, delivery)
            try:
                for index, bubble in enumerate(plan):
                    if index:
                        await self._pause(session.signal, self._delay(key, index))
                    async with session.send_lock:
                        if not self._current(key, session) or not await guard_async() or not self._current(key, session):
                            delivery.status = "interrupted" if delivery.attempted else "not_sent"
                            self._report(event, delivery)
                            return
                        before = getattr(event, "_dafeiyu_sticker_before_packet", None)
                        after = getattr(event, "_dafeiyu_sticker_after_packet", None)
                        if callable(before):
                            bubble = await before(bubble)
                        if bubble is None:
                            continue
                        # Do not let an awaited revalidation race new input or
                        # revoked session/plugin permission.
                        if callable(before) and (not self._current(key, session) or not await guard_async() or not self._current(key, session)):
                            delivery.status = "interrupted" if delivery.attempted else "not_sent"
                            self._report(event, delivery)
                            return
                        delivery.attempted += 1
                        try:
                            await original_send(bubble)
                        except BaseException:
                            if callable(after):
                                await after(bubble, "failed_unknown")
                            raise
                        if callable(after):
                            await after(bubble, "sent")
                    delivery.sent += 1
                    self._report(event, delivery)
                delivery.status = "sent"
                self._report(event, delivery)
            except asyncio.CancelledError:
                delivery.status = "cancelled"
                self._report(event, delivery)
                raise
            except Exception:
                delivery.status = "failed"
                self._report(event, delivery)
                raise RuntimeError("Natural chat delivery did not complete; automatic retry suppressed") from None

        event.send = send_final
        setattr(event, _WRAPPER_ATTR, self)
        return True
