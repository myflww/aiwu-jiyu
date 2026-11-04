"""Shared, explicit outward reply path for owner-authorized proactive messages.

Uses real activated plugin instances, the host session lock and existing bubble
splitter. It never forges a received event or grants autonomous tool access.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import re

from .natural_bubbles import split_text, BubbleReplies

_MARKER = re.compile(r"\[(?:大肥鱼表情|sticker)\s*[:：]\s*[^\[\]\r\n\x00]{1,50}\]", re.I)
_PROTECTED = re.compile(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`+[^`\r\n]*(?:`+|$)"
    r"|!?\[[^\]\r\n]*\]\([^\)\r\n]*\)|https?://[^\s<>]+"
    r"|<(?:thought|think|analysis|reasoning)(?:\s[^>]*)?>[\s\S]*?(?:</(?:thought|think|analysis|reasoning)>|$)", re.I)


def visible_text(text):
    """Remove our protocol markers even when the owning plugin was disabled."""
    if not isinstance(text, str):
        return ""
    protected = [(m.start(), m.end()) for m in _PROTECTED.finditer(text)]
    matches = [m for m in _MARKER.finditer(text) if not any(a < m.end() and m.start() < b for a, b in protected)]
    for match in reversed(matches):
        text = text[:match.start()] + text[match.end():]
    return text


@asynccontextmanager
async def session_guard(origin, *, required=False):
    try:
        from astrbot.core.utils.session_lock import session_lock_manager
    except ImportError:
        # Minimal SDK test doubles have no registry or host lock. A real
        # Context must provide the pinned shared lock; fail closed otherwise.
        if required:
            raise RuntimeError("AstrBot shared session lock is unavailable") from None
        yield
        return
    async with session_lock_manager.acquire_lock(origin):
        yield


class ReplyPipeline:
    def __init__(self, context, chain_factory, plugin_allowed):
        self.context, self.chain_factory, self.plugin_allowed = context, chain_factory, plugin_allowed

    async def _plugin(self, names, origin):
        getter = getattr(self.context, "get_registered_star", None)
        if not callable(getter):
            return None
        for name in names:
            metadata = getter(name)
            instance = getattr(metadata, "star_cls", None)
            if metadata is not None and getattr(metadata, "activated", False) and instance is not None:
                if await self.plugin_allowed(origin, name):
                    return instance
        return None

    async def context_notes(self, origin, message_id, query):
        notes, status = [], {"memory": "not_enabled", "stickers": "not_enabled"}
        memory = await self._plugin(("love_house", "astrbot_plugin_love_house"), origin)
        if memory and callable(getattr(memory, "companion_recall", None)):
            try:
                material = await memory.companion_recall(origin, message_id, query)
                if material:
                    notes.append(material)
                status["memory"] = "ready" if material else "not_bound"
            except Exception:
                status["memory"] = "unavailable"
        sticker = await self._plugin(("astrbot_plugin_dafeiyu_stickers",), origin)
        if sticker and callable(getattr(sticker, "proactive_context", None)):
            try:
                note = await sticker.proactive_context(origin, query)
                if isinstance(note, str) and len(note) <= 16000:
                    notes.append(note)
                    status["stickers"] = "ready"
            except Exception:
                status["stickers"] = "unavailable"
        return "\n".join(notes), status

    @staticmethod
    async def _settle(coro):
        """Finish bookkeeping even if the caller is being cancelled."""
        task = asyncio.create_task(coro)
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                interrupted = True
        return task.result(), interrupted

    async def deliver(self, origin, cid, message_id, text, options, guard, *, on_result=None):
        sticker, image, reservation, cancelled = None, None, None, False
        status = {"accepted": False, "attempted": 0, "sent_bubbles": 0, "history": "not_sent", "memory": "not_sent", "sticker": "none"}
        try:
            sticker, interrupted = await self._settle(self._plugin(("astrbot_plugin_dafeiyu_stickers",), origin))
            cancelled |= interrupted
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            status["sticker"] = "unavailable"
        if not cancelled and sticker and callable(getattr(sticker, "prepare_proactive", None)):
            try:
                prepared, interrupted = await self._settle(sticker.prepare_proactive(origin, text))
                cancelled |= interrupted
                text = prepared.get("text", text)
                image, reservation = prepared.get("component"), prepared.get("reservation")
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                status["sticker"] = "unavailable"
        text = visible_text(text)
        valid_text = bool(text.strip())
        chunks = split_text(text) if valid_text and options.get("natural_bubbles_enabled") else [text] if valid_text else []
        if len(chunks) > 1 and not BubbleReplies._selected((origin, message_id), text, options.get("natural_bubbles_frequency", "occasional")):
            chunks = [text]
        accepted, image_receipt = [], "not_sent"
        if not valid_text:
            status["invalid"] = True
        try:
            for index, chunk in enumerate(chunks):
                if cancelled or not await guard():
                    status["cancelled"] = True
                    break
                if index:
                    await asyncio.sleep(BubbleReplies._delay((origin, message_id), index))
                    if not await guard():
                        status["cancelled"] = True
                        break
                final_image = None
                if image is not None and index == len(chunks) - 1:
                    current = await self._plugin(("astrbot_plugin_dafeiyu_stickers",), origin)
                    verifier = getattr(sticker, "revalidate_proactive", None)
                    if current is sticker and callable(verifier):
                        final_image = await verifier(origin, reservation)
                    if final_image is None:
                        status["sticker"] = "not_sent"
                    if not await guard():
                        status["cancelled"] = True
                        break
                chain = self.chain_factory().message(chunk)
                if final_image is not None:
                    chain.chain.append(final_image)
                    image_receipt = "failed_unknown"
                status["attempted"] += 1
                delivered = await self.context.send_message(origin, chain)
                if delivered is not True:
                    status["delivery_uncertain"] = True
                    break
                accepted.append(chunk)
                status["sent_bubbles"] = len(accepted)
                if final_image is not None:
                    status["sticker"] = "adapter_accepted"
                    image_receipt = "sent"
        except asyncio.CancelledError:
            cancelled = True
            status["cancelled"] = True
            if status["attempted"] > len(accepted):
                status["delivery_uncertain"] = True
        except Exception:
            status["delivery_uncertain"] = status["attempted"] > len(accepted)
        finally:
            if reservation is not None and sticker and callable(getattr(sticker, "acknowledge_proactive", None)):
                try:
                    _, interrupted = await self._settle(sticker.acknowledge_proactive(origin, reservation, image_receipt))
                    cancelled |= interrupted
                except asyncio.CancelledError:
                    cancelled = True
                    status["sticker"] = "receipt_unavailable"
                except Exception:
                    status["sticker"] = "receipt_unavailable"
        status["text"] = "".join(accepted)
        status["accepted"] = bool(accepted)
        if status["text"]:
            _, interrupted = await self._settle(self._record_accepted(origin, cid, message_id, status))
            cancelled |= interrupted
        if cancelled:
            status["cancelled"] = True
        if on_result:
            on_result(status)
        if cancelled:
            raise asyncio.CancelledError
        return status

    async def _record_accepted(self, origin, cid, message_id, status):
        visible = status["text"]
        manager = self.context.conversation_manager
        updater = getattr(manager, "update_conversation", None)
        try:
            if not callable(updater):
                status["history"] = "unsupported"
            elif await manager.get_curr_conversation_id(origin) != cid:
                status["history"] = "conversation_changed"
            else:
                current = await manager.get_conversation(origin, cid)
                history = json.loads(current.history) if isinstance(current.history, str) else list(current.history)
                if not isinstance(history, list):
                    raise ValueError("History is not a list")
                history.append({"role": "assistant", "content": visible})
                await updater(origin, conversation_id=cid, history=history)
                status["history"] = "saved"
        except Exception:
            status["history"] = "unavailable"
        try:
            memory = await self._plugin(("love_house", "astrbot_plugin_love_house"), origin)
        except Exception:
            status["memory"] = "unavailable"
            return
        if memory and callable(getattr(memory, "companion_record", None)):
            try:
                result = await memory.companion_record(origin, message_id, visible, receipt="adapter_accepted")
                status["memory"] = "durably_staged" if result else "not_bound"
            except Exception:
                status["memory"] = "unavailable"
        else:
            status["memory"] = "not_enabled"
