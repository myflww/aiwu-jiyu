"""Per-turn delivery claims and optional QQ bubbles in AstrBot's pipeline.

Native segmentation/streaming retain their partitioning. The same bounded
guard covers their final decision, while custom bubbles require a compatible
complete-text host. Attempts and accepted packet receipts remain distinct;
after_message_sent only closes slots, never proves delivery. No new targets.

Primary references: https://docs.astrbot.app/use/webui.html and
https://github.com/AstrBotDevs/AstrBot/issues/10047 . This is original code.
"""
from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import re


STATUS_KEY = "dafeiyu_bubbles_status"
COUNT_KEY = "dafeiyu_bubbles_sent_count"
ATTEMPT_KEY = "dafeiyu_bubbles_attempted_count"
TEXT_KEY = "dafeiyu_bubbles_final_text"
TOOL_KEY = "dafeiyu_bubbles_tool_round"
_WRAPPER_ATTR = "_dafeiyu_natural_send_wrapper"
_OBSERVED_ATTR = "_dafeiyu_natural_observation"
_SESSION_ATTR = "_dafeiyu_natural_session"
POLICY_KEY = "dafeiyu_reply_policy"
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


def split_text(text: str, *, max_bubbles=4) -> list[str]:
    """Split at complete sentence/paragraph boundaries, preserving every char.

    Tiny acknowledgments and structured/technical answers remain whole.
    The 36/12 character limits are product defaults, not research optima.
    Complete sentence boundaries are required even for a short casual reply.
    """
    if not isinstance(text, str) or not _MIN_CHAT_CHARS <= len(text.strip()) <= 1600 or _PROTECTED.search(text) or _FORMAL.search(text):
        return [text]
    boundaries = [match.end() for match in _BOUNDARY.finditer(text) if match.end() < len(text)]
    max_bubbles = max_bubbles if type(max_bubbles) is int and 1 <= max_bubbles <= 4 else 4
    if max_bubbles == 1:
        return [text]
    desired = min(max_bubbles, max(2, (len(text) + 279) // 280))
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


def _signature(message):
    """Per-turn final/packet identity after the SDK clears its result alias.

    This is never a global text filter: a new incoming message ID has a new
    delivery key. Independent nonmatching tool/status output remains separate.
    """
    fields = {"Plain": "text", "Image": "file", "Face": "id", "At": "qq", "Reply": "id"}
    values = []
    for part in getattr(message, "chain", ()):
        kind = _kind(part)
        if kind not in fields:
            return None
        values.append((kind, str(getattr(part, fields[kind], ""))))
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).digest() if values else None


def packet_invites_reply(message):
    text = "".join(str(getattr(part, "text", "")) for part in getattr(message, "chain", ()) if _kind(part) == "Plain").strip()
    if (not text or len(text) > 240 or _PROTECTED.search(text)
            or re.search(r"[“”‘’\"]|(?:[他她]说|举例|引用|这句话|意思是)", text)):
        return False
    clauses = [part.strip() for part in re.split(r"[。！!\n]+", text) if part.strip()]
    tail = clauses[-1] if clauses else ""
    return bool(tail and len(tail) <= 160 and (tail.endswith(("?", "？"))
        or re.search(r"(?:要不要|想不想|愿意吗|好不好|怎么样|可以吗).{0,36}$", tail)))


def _plan(message, *, max_bubbles=4) -> tuple[list, str]:
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
    chunks = split_text(text, max_bubbles=max_bubbles)
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
    route: str = "custom"
    owner: object = None
    remaining: Counter = field(default_factory=Counter)
    signatures: set = field(default_factory=set)


def reply_policy(value):
    value = value if isinstance(value, dict) else {}
    mode = value.get("response_mode", "auto")
    maximum = value.get("max_bubbles", 4)
    return {"response_mode": mode if mode in {"single", "bubbles", "auto"} else "auto",
            "max_bubbles": maximum if type(maximum) is int and 1 <= maximum <= 4 else 4,
            "profile": value.get("profile") if value.get("profile") in {"quiet", "natural", "lively"} else "natural"}


def selected_chunks(text, options, policy, key, *, formal=False):
    policy = reply_policy(policy)
    if formal or policy["response_mode"] == "single" or options.get("natural_bubbles_enabled") is not True:
        return [text]
    chunks = split_text(text, max_bubbles=policy["max_bubbles"])
    if policy["response_mode"] == "auto" and not BubbleReplies._selected(key, text, options.get("natural_bubbles_frequency", "occasional")):
        return [text]
    return chunks


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

    @staticmethod
    def _known_host(config):
        platform = config.get("platform_settings", {}) if isinstance(config, dict) else {}
        segmented = platform.get("segmented_reply", {}) if isinstance(platform, dict) else {}
        return isinstance(segmented, dict) and type(segmented.get("enable")) is bool

    @staticmethod
    async def _notify(event, name, *args):
        callback = getattr(event, name, None)
        if not callable(callback):
            return
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Receipt/analytics hooks cannot turn an accepted adapter call into
            # a failed send, nor grant permission to repeat it.
            event.set_extra("dafeiyu_reply_callback_error", True)

    def _claim(self, key, event, route):
        existing = self._deliveries.get(key)
        if existing is not None:
            return existing, False
        while len(self._deliveries) >= 256:
            removable = next((k for k, value in self._deliveries.items() if value.status != "sending"
                and (value.route != "host" or not value.remaining
                     or not self._current(k, getattr(value.owner, _SESSION_ATTR, None)))), None)
            if removable is None:
                return None, False
            del self._deliveries[removable]
        delivery = _Delivery(claimed=True, status="sending", route=route, owner=event)
        self._deliveries[key] = delivery
        return delivery, True

    def finish(self, event):
        """End native component slots, without inventing any send receipt."""
        key = _identity(event)
        delivery = self._deliveries.get(key)
        if delivery is not None and delivery.owner is event:
            delivery.remaining.clear()
            if delivery.status == "sending":
                delivery.status = "sent" if delivery.sent else "not_sent"
            self._report(event, delivery)

    async def arm(self, event, options, guard_async, host_config):
        """Arm once before generation; final-only decoration also calls this.

        send_streaming bypasses on_decorating_result in the real SDK. Callers
        should arm at on_llm_request so a streaming decision has the same
        at-most-once boundary. No model/result is generated or sent here.
        """
        key = _identity(event)
        if self._closed or key is None or not self._known_host(host_config):
            return False
        result = event.get_result()
        if result is not None and result.is_llm_result():
            setattr(event, "_dafeiyu_reply_source_result", result)
            setattr(event, "_dafeiyu_reply_source_components", list(result.chain or ()))
            setattr(event, "_dafeiyu_reply_source_signature", _signature(result))
        if getattr(event, _WRAPPER_ATTR, None) is self:
            return True
        self.observe(event)
        session = getattr(event, _SESSION_ATTR, None)
        if session is None or session.message_id != key[1]:
            return False
        original_send = event.send
        original_stream = getattr(event, "send_streaming", None)
        active_stream = None
        setattr(event, "_dafeiyu_sticker_bubble_packets", True)

        async def packet(message, delivery):
            async with session.send_lock:
                if delivery.status not in {"sending", "sent"}:
                    return False
                if not self._current(key, session) or not await guard_async() or not self._current(key, session):
                    delivery.status = "interrupted" if delivery.attempted else "not_sent"
                    self._report(event, delivery)
                    return False
                before = getattr(event, "_dafeiyu_sticker_before_packet", None)
                if callable(before):
                    message = await before(message)
                    if message is None:
                        return True
                    if not self._current(key, session) or not await guard_async() or not self._current(key, session):
                        delivery.status = "interrupted" if delivery.attempted else "not_sent"
                        self._report(event, delivery)
                        return False
                delivery.attempted += 1
                signature = _signature(message)
                if signature is not None and len(delivery.signatures) < 512:
                    delivery.signatures.add(signature)
                # SDK ProcessStage treats _has_send_oper=False as permission
                # for another implicit Agent run. A timeout can happen after
                # delivery but before adapter.super().send() sets that flag.
                # Mark the *attempt*, independently of accepted receipt counts.
                event._has_send_oper = True
                try:
                    value = await original_send(message)
                    if value is False:
                        raise RuntimeError("Adapter did not acknowledge the packet")
                except BaseException:
                    await self._notify(event, "_dafeiyu_sticker_after_packet", message, "failed_unknown")
                    raise
                delivery.sent += 1
                delivery.text = (delivery.text + "".join(str(getattr(part, "text", "")) for part in getattr(message, "chain", ())))[:16000]
                self._report(event, delivery)
                event.set_extra("dafeiyu_last_packet_invites_reply", packet_invites_reply(message))
                await self._notify(event, "_dafeiyu_sticker_after_packet", message, "sent")
                await self._notify(event, "_dafeiyu_reply_packet_accepted", event)
                return True

        async def send_final(message):
            nonlocal active_stream
            if message is None or getattr(message, "type", None) in _BAD_TYPES:
                return await original_send(message)
            source = event.get_result()
            prepared = getattr(event, "_dafeiyu_reply_source_result", None)
            final = source is not None and (source is prepared or source.is_llm_result())
            # The native stage can retain a local result after another hook
            # clears/replaces event.result. Its derived chain still carries
            # the prepared components; don't turn that alias into a bypass.
            if not final and prepared is not None:
                refs = {id(part) for part in getattr(event, "_dafeiyu_reply_source_components", ())}
                final = any(id(part) in refs for part in getattr(message, "chain", ()))
                if final:
                    source = prepared
            existing = self._deliveries.get(key)
            signature = _signature(message)
            if not final and signature is not None and (signature == getattr(event, "_dafeiyu_reply_source_signature", None)
                    or existing is not None and signature in existing.signatures):
                final, source = True, prepared
            stream_packet = active_stream is not None
            if stream_packet:
                try:
                    from astrbot.core.platform.astr_message_event import streaming_response_event
                    stream_packet = streaming_response_event.get() is event
                except ImportError:
                    pass  # Isolated SDK-independent test hosts.
            if not final and not stream_packet:
                return await original_send(message)  # Independent command/tool.
            if stream_packet:
                delivery = active_stream
                if delivery.status not in {"sending", "sent"}:
                    return
                await packet(message, delivery)
                return
            if existing is not None:
                if existing.attempted:
                    event._has_send_oper = True
                # Native RespondStage intentionally sends one component at a
                # time. Only the original event's still-unconsumed components
                # may continue. A cloned final/retry cannot gain new slots.
                payload = [part for part in getattr(message, "chain", ()) if _kind(part) not in {"At", "Reply"}]
                counts = Counter(id(part) for part in payload)
                if (existing.route != "host" or existing.owner is not event
                        or existing.status not in {"sending", "sent"} or not counts
                        or any(existing.remaining[identifier] < count for identifier, count in counts.items())):
                    self._report(event, existing)
                    return
                delivery = existing
                delivery.remaining.subtract(counts)
                try:
                    if await packet(message, delivery):
                        delivery.status = "sent" if delivery.sent else "not_sent"
                except asyncio.CancelledError:
                    delivery.status = "cancelled"
                    raise
                except Exception:
                    delivery.status = "failed"
                    raise RuntimeError("Final packet delivery uncertain; automatic retry suppressed") from None
                finally:
                    self._report(event, delivery)
                return
            custom = _host_allows(host_config, source)
            route = "custom" if custom else "host"
            delivery, claimed = self._claim(key, event, route)
            if not claimed:
                if delivery is not None:
                    self._report(event, delivery)
                return
            policy = reply_policy(event.get_extra(POLICY_KEY))
            plan, text = _plan(message, max_bubbles=policy["max_bubbles"])
            incoming = getattr(event, "get_message_str", lambda: "")() or ""
            formal = bool(_TASK.search(incoming) or event.get_extra("dafeiyu_formal_response", False))
            try:
                threshold = int(host_config.get("platform_settings", {}).get("forward_threshold", 1500))
            except (TypeError, ValueError):
                threshold = 1500
            tool_round = event.get_extra(TOOL_KEY, False) or event.get_extra("dafeiyu_sticker_tool_round", False)
            enabled = options.get("natural_bubbles_enabled") is True and policy["response_mode"] != "single"
            selected = policy["response_mode"] == "bubbles" or self._selected(key, text, options.get("natural_bubbles_frequency", "occasional"))
            if not custom or formal or tool_round or not enabled or not plan or len(text) > threshold or not selected:
                plan = [message]
            if route == "host":
                # Capture native components once, before a retry can substitute
                # a new whole result. Headers do not consume payload slots.
                counts = Counter(id(part) for part in getattr(source, "chain", ()) if _kind(part) not in {"At", "Reply"})
                counts |= Counter(id(part) for part in getattr(event, "_dafeiyu_reply_source_components", ()) if _kind(part) not in {"At", "Reply"})
                payload = Counter(id(part) for part in getattr(message, "chain", ()) if _kind(part) not in {"At", "Reply"})
                for identifier, count in payload.items():
                    counts[identifier] = max(counts[identifier], count)
                delivery.remaining = counts - payload
            self._report(event, delivery)
            try:
                for index, bubble in enumerate(plan):
                    if index:
                        scale = {"quiet": 1.2, "natural": 1.0, "lively": .8}[policy["profile"]]
                        await self._pause(session.signal, max(.5, min(3.0, self._delay(key, index) * scale)))
                    if not await packet(bubble, delivery):
                        return
                delivery.status = "sent" if delivery.sent else "not_sent"
            except asyncio.CancelledError:
                delivery.status = "cancelled"
                raise
            except Exception:
                delivery.status = "failed"
                raise RuntimeError("Natural chat delivery did not complete; automatic retry suppressed") from None
            finally:
                self._report(event, delivery)

        async def send_streaming(*args, **kwargs):
            nonlocal active_stream
            delivery, claimed = self._claim(key, event, "stream")
            if not claimed:
                if delivery and delivery.attempted:
                    event._has_send_oper = True
                return
            active_stream = delivery
            self._report(event, delivery)
            try:
                if not self._current(key, session) or not await guard_async() or not self._current(key, session):
                    delivery.status = "not_sent"
                    return
                # Native stream implementations can deliver without event.send.
                # Claim the decision, but don't invent a packet receipt for it.
                event._has_send_oper = True
                await original_stream(*args, **kwargs)
                if delivery.status in {"sending", "sent"}:
                    delivery.status = "sent" if delivery.sent else "receipt_unobserved"
            except asyncio.CancelledError:
                delivery.status = "cancelled"
                raise
            except Exception:
                delivery.status = "failed"
                raise RuntimeError("Streaming delivery uncertain; automatic retry suppressed") from None
            finally:
                active_stream = None
                self._report(event, delivery)

        event.send = send_final
        if callable(original_stream):
            event.send_streaming = send_streaming
        setattr(event, _WRAPPER_ATTR, self)
        return True

    async def prepare(self, event, options: dict, guard_async, host_config: dict | None = None) -> bool:
        result = event.get_result()
        if (result is None or not result.is_llm_result() or getattr(result, "type", None) in _BAD_TYPES
                or event.get_extra("agent_user_aborted", False)):
            return False
        # Tool rounds, native segmentation, TTS and image conversion still need
        # a final-decision claim. They merely opt out of our custom splitting.
        return await self.arm(event, options, guard_async, host_config)
