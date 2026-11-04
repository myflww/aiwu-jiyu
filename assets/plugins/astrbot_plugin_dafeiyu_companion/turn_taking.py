"""Real-event trailing collection and conservative group admission.

No fabricated events, extra model calls, disk transcript, or adapter sends.
The final incoming event continues through AstrBot's ordinary pipeline.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time

COLLECTED_KEY = "dafeiyu_collected_turn"
TURN_KEY = "dafeiyu_turn_token"
GROUP_KEY = "dafeiyu_group_admission"
STATUS_KEY = "dafeiyu_collection_status"
NO_REPLY_KEY = "dafeiyu_explicit_no_reply"
_NO_REPLY = {"不用回", "不用回复", "不用回我", "不用回复我", "这条不用回", "这条不用回复",
             "我先忙了不用回", "我先忙了不用回复", "我去忙了不用回", "先不聊了", "先聊到这里",
             "先这样不用回", "先别回了", "先别回复了", "停止回复", "不要再发消息了"}
_COMMAND = re.compile(r"^\s*[/!！＃#]")
_URGENT = re.compile(r"(?:急救|救命|着火|失火|自杀|想死|不想活|立即停止|马上停止|别发了|不要再发|停止回复|先别回|等我说完)")
_STOP = re.compile(r"^\s*(?:先?别(?:再)?回(?:复)?了?|停止回复|不要再发(?:消息)?了?|先?等我说完|停一下)[。！!\s]*$")


def identity(event):
    try:
        sender, bot = str(event.get_sender_id()), str(event.get_self_id())
        origin, mid = event.unified_msg_origin, str(event.message_obj.message_id or "")
        kind = str(getattr(event.get_message_type(), "value", event.get_message_type()))
        if (event.get_platform_name() not in {"aiocqhttp", "qq"} or not sender.isdigit()
                or sender == bot or not mid or len(mid) > 180 or not isinstance(origin, str)
                or len(origin) > 300 or origin.split(":", 1)[0] != event.get_platform_id()
                or kind not in {"FriendMessage", "GroupMessage", "friend", "group"}):
            return None
        if (kind in {"FriendMessage", "friend"} and origin != f"{event.get_platform_id()}:FriendMessage:{sender}"
                or kind in {"GroupMessage", "group"} and ":GroupMessage:" not in origin):
            return None
        return origin, sender, mid
    except (AttributeError, TypeError, ValueError):
        return None


def is_group(event):
    kind = event.get_message_type()
    return str(getattr(kind, "value", kind)) in {"GroupMessage", "group"}


def command_event(event):
    if _COMMAND.match(event.get_message_str() or ""):
        return True
    for handler in event.get_extra("activated_handlers", []) or []:
        if any(type(item).__name__ in {"CommandFilter", "CommandGroupFilter", "RegexFilter", "ButtonActionFilter"}
               for item in getattr(handler, "event_filters", ())):
            return True
    return bool(getattr(event, "is_button_interaction", lambda: False)())


def no_reply_requested(event):
    """Only literal complete plain-text endings, never assent/media/commands."""
    if command_event(event) or not event.get_messages() or any(type(item).__name__ != "Plain" for item in event.get_messages()):
        return False
    collected = event.get_extra(COLLECTED_KEY)
    texts = collected.get("original_texts") if isinstance(collected, dict) else None
    text = texts[-1] if isinstance(texts, list) and texts else event.get_message_str()
    compact = re.sub(r"[\s，,。.!！～~]+", "", text or "")
    return compact in _NO_REPLY or bool(_STOP.fullmatch(text or ""))


async def retain_user_input(context, event, guard):
    """Persist a genuine user-only turn in the host store, with no generation."""
    from .reply_pipeline import session_guard
    manager = getattr(context, "conversation_manager", None)
    if manager is None:
        return "unavailable"
    try:
        async with session_guard(event.unified_msg_origin, required=callable(getattr(context, "get_registered_star", None))):
            if not await guard():
                return "not_authorized"
            origin = event.unified_msg_origin
            record = {"role": "user", "content": event.get_message_str()}
            cid = await manager.get_curr_conversation_id(origin)
            if cid:
                current = await manager.get_conversation(origin, cid)
                source = current.history
                if isinstance(source, str) and len(source) > 2 * 1024 * 1024:
                    return "unavailable"
                history = json.loads(source) if isinstance(source, str) else list(source)
                if not isinstance(history, list) or len(history) > 10000:
                    return "unavailable"
                history.append(record)
                if not await guard() or await manager.get_curr_conversation_id(origin) != cid:
                    return "not_authorized"
                await manager.update_conversation(origin, conversation_id=cid, history=history)
            else:
                if not callable(getattr(manager, "new_conversation", None)) or not await guard():
                    return "unavailable"
                await manager.new_conversation(origin, content=[record])
        return "saved"
    except Exception:
        return "unavailable"


def _words(options):
    return [word.strip() for word in re.split(r"[,，\n]", options.get("group_wake_words", "大肥鱼,小鱼")) if word.strip()]


def explicitly_addressed(event, options, host_config):
    """At-all is intentionally not an explicit invitation to this bot."""
    bot = str(event.get_self_id())
    for component in event.get_messages():
        kind = type(component).__name__
        if kind == "At" and str(getattr(component, "qq", "")) == bot:
            return True
        if kind == "Reply" and str(getattr(component, "sender_id", "")) == bot:
            return True
    if addressed_elsewhere(event):
        return False
    text = event.get_message_str() or ""
    prefixes = host_config.get("wake_prefix", []) if isinstance(host_config, dict) else []
    # The SDK already removes a matched wake prefix before listener execution.
    # Verify it against the immutable input message string, never infer from
    # is_at_or_wake_command alone (which can also mean @all).
    original = str(getattr(event.message_obj, "message_str", "") or text).lstrip()
    return any(isinstance(word, str) and word and original.startswith(word) for word in [*_words(options), *prefixes])


def addressed_elsewhere(event):
    bot = str(event.get_self_id())
    return any((type(item).__name__ == "At" and str(getattr(item, "qq", "")) not in {bot, "all"})
               or (type(item).__name__ == "Reply" and str(getattr(item, "sender_id", "")) not in {"", bot})
               for item in event.get_messages())


class Turns:
    """Arrival tokens scoped to sender, shared by collection and final sends."""
    def __init__(self):
        self.current = OrderedDict()
        self.seen = OrderedDict()
        self.closed = False

    def observe(self, event):
        ident = identity(event)
        if self.closed or ident is None:
            return None
        existing = event.get_extra(TURN_KEY)
        if existing is not None:
            return existing
        if ident in self.seen:
            event.set_extra(STATUS_KEY, "duplicate")
            return None
        key = ident[:2]
        token = object()
        self.current[key] = token
        self.current.move_to_end(key)
        self.seen[ident] = token
        while len(self.current) > 256:
            self.current.popitem(last=False)
        while len(self.seen) > 2048:
            self.seen.popitem(last=False)
        event.set_extra(TURN_KEY, token)
        return token

    def current_event(self, event):
        ident = identity(event)
        return bool(not self.closed and ident and self.current.get(ident[:2]) is event.get_extra(TURN_KEY))

    def close(self):
        self.closed = True
        self.current.clear()
        self.seen.clear()


@dataclass
class _Pending:
    event: object
    future: asyncio.Future


@dataclass
class _Batch:
    started: float
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    entries: list = field(default_factory=list)
    chars: int = 0
    components: int = 0
    timer: asyncio.Task | None = None
    last: float = 0
    delay: float = 10
    max_wait: float = 30


class MessageCollector:
    """Wait in real AdapterMessage callbacks; release one latest event only."""
    def __init__(self, plain_factory, *, clock=time.monotonic):
        self._plain = plain_factory
        self._clock = clock
        self._pending = OrderedDict()
        self._closed = False

    def close(self):
        self._closed = True
        for key in list(self._pending):
            self.cancel(key)

    def cancel(self, key):
        batch = self._pending.pop(key, None)
        if batch is None:
            return
        if batch.timer and batch.timer is not asyncio.current_task():
            batch.timer.cancel()
        for item in batch.entries:
            item.event.set_extra(STATUS_KEY, "cancelled")
            if not item.future.done():
                item.future.set_result(False)
        batch.changed.set()

    def cancel_event(self, event):
        ident = identity(event)
        if ident:
            self.cancel(ident[:2])

    def has_pending(self, event):
        ident = identity(event)
        return bool(ident and ident[:2] in self._pending)

    def pending_admission(self, event):
        ident = identity(event)
        batch = self._pending.get(ident[:2]) if ident else None
        return batch.entries[0].event.get_extra(GROUP_KEY) if batch and batch.entries else None

    def pending_group_mode(self, event):
        ident = identity(event)
        batch = self._pending.get(ident[:2]) if ident else None
        return batch.entries[0].event.get_extra("dafeiyu_collection_group_mode") if batch and batch.entries else None

    def _finish(self, key, batch):
        if self._pending.get(key) is not batch:
            return
        self._pending.pop(key, None)
        if batch.timer and batch.timer is not asyncio.current_task():
            batch.timer.cancel()
        live = [item for item in batch.entries if not item.future.cancelled()]
        if not live:
            return
        event = live[-1].event
        components, texts, ids = [], [], []
        for index, item in enumerate(live):
            source = item.event
            if index:
                components.append(self._plain("\n"))
            components.extend(list(source.get_messages()))
            texts.append(source.get_message_str() or "")
            ids.append(str(source.message_obj.message_id))
            if source is not event:
                # Native track/untrack APIs preserve the exact existing media
                # objects until the continuing event finishes its SDK scope.
                for path in tuple(getattr(source, "_temporary_local_files", ())):
                    event.track_temporary_local_file(path)
                    source.untrack_temporary_local_file(path)
        event.message_obj.message = components
        event.message_str = "\n".join(texts)
        event.message_obj.message_str = event.message_str
        event.set_extra(COLLECTED_KEY, {"count": len(live), "message_ids": ids, "original_texts": texts})
        policies = [item.event.get_extra("dafeiyu_reply_policy") for item in live]
        policies = [item for item in policies if isinstance(item, dict)]
        if policies:
            strict = [item for item in policies if item.get("response_mode") == "single"]
            policy = dict(strict[-1] if strict else policies[-1])
            # A late fragment must not turn an earlier formal request into
            # chat bubbles. Keep public metadata only; input/media stay native.
            if strict:
                policy["response_mode"] = "single"
            counts = [item["max_bubbles"] for item in policies
                      if type(item.get("max_bubbles")) is int and 1 <= item["max_bubbles"] <= 4]
            if counts:
                policy["max_bubbles"] = min(counts)
            policy["collection_seconds"], policy["collection_max_wait_seconds"] = batch.delay, batch.max_wait
            event.set_extra("dafeiyu_reply_policy", policy)
        for item in live:
            winner = item.event is event
            item.event.set_extra(STATUS_KEY, "released" if winner else "collected")
            if not item.future.done():
                item.future.set_result(winner)

    async def _wait(self, key, batch):
        try:
            while self._pending.get(key) is batch:
                remaining = min(batch.last + batch.delay,
                                batch.started + batch.max_wait) - self._clock()
                if remaining <= 0:
                    self._finish(key, batch)
                    return
                batch.changed.clear()
                try:
                    await asyncio.wait_for(batch.changed.wait(), remaining)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            return

    async def collect(self, event, options, guard):
        ident = identity(event)
        if self._closed or ident is None:
            return False
        if event.get_extra("dafeiyu_collection_started", False):
            return False
        event.set_extra("dafeiyu_collection_started", True)
        key = ident[:2]
        quiet = no_reply_requested(event)
        if not quiet and (command_event(event) or _URGENT.search(event.get_message_str() or "")):
            self.cancel(key)
            event.set_extra(STATUS_KEY, "immediate")
            return True
        if not options.get("message_collection_enabled"):
            self.cancel(key)
            event.set_extra(STATUS_KEY, "disabled")
            return True
        now = self._clock()
        # The newest real arrival may ask for a shorter or longer trailing
        # pause. The first-arrival deadline can only get stricter, never slide
        # forever as a person continues typing or changes persona/settings.
        def finite(value, fallback, maximum):
            return min(maximum, max(0.0, float(value))) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else fallback
        delay = finite(options.get("collection_seconds"), 10.0, 30.0)
        max_wait = finite(options.get("collection_max_wait_seconds"), 30.0, 120.0)
        batch = self._pending.get(key)
        expired = bool(batch and now >= min(batch.last + batch.delay, batch.started + batch.max_wait))
        if batch is None:
            # Bounded number of real incoming waiters. Eviction cancels old
            # work rather than starting unguarded, duplicate model calls.
            if len(self._pending) >= 128:
                self.cancel(next(iter(self._pending)))
            batch = _Batch(started=now, last=now, delay=delay, max_wait=max_wait)
            self._pending[key] = batch
        else:
            batch.delay, batch.max_wait = delay, min(batch.max_wait, max_wait)
        future = asyncio.get_running_loop().create_future()
        batch.entries.append(_Pending(event, future))
        batch.last = now
        batch.chars += len(event.get_message_str() or "")
        batch.components += len(event.get_messages())
        if (quiet or expired or len(batch.entries) >= options["collection_max_messages"]
                or batch.chars >= options["collection_max_chars"] or batch.components >= 128):
            # Threshold flushes whole messages; never truncate the last input
            # or strip its media in order to force an artificial character cap.
            self._finish(key, batch)
        else:
            batch.changed.set()
            if batch.timer is None:
                batch.timer = asyncio.create_task(self._wait(key, batch), name="aiwu-message-collection")
        try:
            winner = await future
            if winner and not self._closed and await guard():
                return True
            return False
        except asyncio.CancelledError:
            # Restart/SDK cancellation invalidates the entire pending batch.
            # A released old winner may be cancelled during awaited guard
            # after a new batch with the same key has already begun.
            if self._pending.get(key) is batch:
                self.cancel(key)
            raise


class GroupAdmission:
    """Only light unsolicited participation is budgeted; explicit asks survive."""
    def __init__(self, *, clock=time.time):
        self._clock = clock
        self._groups = OrderedDict()

    def admit(self, event, options, host_config):
        if not is_group(event):
            return "private"
        if command_event(event):
            return "command"
        if explicitly_addressed(event, options, host_config):
            return "addressed"
        if options["group_reply_mode"] != "light" or options["group_hourly_limit"] <= 0:
            return "silent"
        if addressed_elsewhere(event):
            return "silent"
        text = event.get_message_str() or ""
        # Light mode is opt-in and must concern the configured character name;
        # arbitrary group traffic is never silently collected into a prompt.
        if not any(word in text for word in _words(options)) or len(text) > 1000 or _URGENT.search(text):
            return "silent"
        group_id = str(getattr(event, "get_group_id", lambda: "")() or "")
        if not group_id:
            # SDK aiocqhttp unique_session may use sender_group in the UMO.
            # Prefer its real group object; fallback only for small test hosts.
            group_id = str(getattr(getattr(event.message_obj, "group", None), "group_id", "") or "")
        if not group_id:
            group_id = event.unified_msg_origin.split(":", 2)[-1].split("_")[-1]
        origin = event.get_platform_id() + ":GroupMessage:" + group_id
        now = self._clock()
        times = self._groups.setdefault(origin, deque())
        self._groups.move_to_end(origin)
        while len(self._groups) > 256:
            self._groups.popitem(last=False)
        while times and now - times[0] >= 3600:
            times.popleft()
        if len(times) >= options["group_hourly_limit"] or times and now - times[-1] < options["group_cooldown_seconds"]:
            return "silent"
        ident = identity(event)
        digest = hashlib.sha256(("\0".join(ident) + "\0group-light").encode()).digest()
        if int.from_bytes(digest[:4], "big") / 2**32 >= options["group_participation_probability"] / 100:
            return "silent"
        # Reserve before model execution. Failures keep the slot to avoid an
        # outage causing an aggressive retry burst. No promise of delivery.
        times.append(now)
        return "light"
