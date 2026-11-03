"""Optional, owner-only companionship. No unsolicited target discovery."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.star.session_plugin_manager import SessionPluginManager

from .engine import (activity_summary, foreground_activity, mood_after, mood_summary,
                     normalize, quiet_now, text_history, valid_session, WHALE_STYLE_CONTEXT)

PLUGIN_NAME = "astrbot_plugin_dafeiyu_companion"
QQ_ADAPTERS = {"aiocqhttp", "qq"}


@register(PLUGIN_NAME, "myflww", "大肥鱼陪伴：主动问候、情绪表达与授权活动概况", "1.0.0")
class DafeiyuCompanion(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self._worker = None
        self._closed = False
        self._state_path = None
        self._state = {}
        self._activity = {}
        self._previous_activity = {}
        self._activity_at = 0.0
        self._last_attempt = 0.0
        self._send_lock = asyncio.Lock()
        self._last_message_key = None

    def _options(self) -> dict:
        return normalize(self.config)

    def _enabled(self) -> bool:
        return not self._closed and self.config.get("enabled") is True and self._options()["enabled"]

    def _owner(self) -> str:
        owner = str(self.config.get("owner_qq") or "").strip()
        return owner if owner.isdigit() and 5 <= len(owner) <= 12 else ""

    def _identity(self) -> tuple[str, str]:
        return self._owner(), str(self.config.get("bot_qq") or "").strip()

    async def initialize(self):
        if not self._enabled() or not self._owner():
            return
        directory = StarTools.get_data_dir(PLUGIN_NAME)
        self._state_path = directory / "state.json"
        if self._state_path.is_symlink():
            self._state_path = None
            return
        try:
            if self._state_path.is_file() and self._state_path.stat().st_size <= 16 * 1024:
                data = json.loads(self._state_path.read_text("utf-8"))
                owner, bot = self._identity()
                if (isinstance(data, dict) and data.get("owner") == owner and data.get("bot") == bot
                        and valid_session(data.get("umo"), owner)):
                    self._state = data
        except (OSError, ValueError):
            self._state = {}
        self._worker = asyncio.create_task(self._loop(), name="dafeiyu-companion")

    async def terminate(self):
        self._closed = True
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        self._activity = {}
        self._previous_activity = {}

    def _save(self) -> bool:
        if self._state_path is None or self._state_path.is_symlink():
            return False
        # Metadata and the last short greeting only: never duplicate chat history.
        directory = self._state_path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".companion-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(self._state, ensure_ascii=False).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            return False
        try:
            os.replace(temporary, self._state_path)
            return True
        except OSError:
            return False
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def _owner_event(self, event: AstrMessageEvent) -> bool:
        if not self._enabled() or not self._owner():
            return False
        try:
            owner, bot = self._identity()
            sender = str(event.get_sender_id())
            actual_bot = str(event.get_self_id())
            umo = event.unified_msg_origin
            return (sender == owner and sender != actual_bot and (not bot or actual_bot == bot)
                    and event.get_message_type() == MessageType.FRIEND_MESSAGE
                    and event.get_platform_name() in QQ_ADAPTERS
                    and valid_session(umo, owner) and umo.split(":", 1)[0] == event.get_platform_id())
        except Exception:
            return False

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_owner(self, event: AstrMessageEvent):
        if not self._owner_event(event):
            return
        # on_llm_request may be called repeatedly for the same tool-loop event.
        if getattr(event, "_dafeiyu_companion_seen", False):
            return
        event._dafeiyu_companion_seen = True
        owner, bot = self._identity()
        key = (event.unified_msg_origin, str(getattr(getattr(event, "message_obj", None), "message_id", "")))
        if key[1] and self._last_message_key == key:
            return
        self._last_message_key = key
        if self._state.get("owner") != owner or self._state.get("bot") != bot:
            self._state = {}
        self._state.update(owner=owner, bot=bot, umo=event.unified_msg_origin, last_received=time.time())
        if self._options()["emotion_enabled"]:
            self._state["mood"] = mood_after(self._state.get("mood") or {}, event.get_message_str())
        self._save()

    async def _sample_activity(self):
        if not self._options()["activity_enabled"] or not self._enabled():
            self._activity = {}
            self._previous_activity = {}
            return
        if time.monotonic() - self._activity_at < 60:
            return
        previous = self._activity
        try:
            current = await asyncio.to_thread(foreground_activity)
        except Exception:
            current = {"available": False}
        if self._enabled() and self._options()["activity_enabled"]:
            self._previous_activity = previous
            self._activity = current
            self._activity_at = time.monotonic()

    async def _note(self) -> str:
        options = self._options()
        parts = []
        if options["whale_style_enabled"]:
            parts.append(WHALE_STYLE_CONTEXT)
        if options["emotion_enabled"]:
            parts.append(mood_summary(self._state.get("mood") or {}))
        if options["activity_enabled"]:
            await self._sample_activity()
            parts.append(activity_summary(self._activity, self._previous_activity))
        if options["proactive_enabled"] and self._state.get("last_greeting"):
            parts.append("你最近主动发过这一句问候，可自然承接，不要重复发送：" + str(self._state["last_greeting"])[:300])
        return "\n".join(parts)

    @filter.on_llm_request()
    async def companion_context(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._owner_event(event):
            return
        await self.observe_owner(event)
        note = await self._note()
        marker = "[爱屋及鱼·可选陪伴状态]"
        if note and marker not in (req.system_prompt or ""):
            req.system_prompt = (req.system_prompt or "") + "\n\n" + marker + "\n" + note

    def _target_available(self, umo: str) -> bool:
        if not valid_session(umo, self._owner()):
            return False
        try:
            for platform in self.context.platform_manager.platform_insts:
                meta = platform.meta()
                if meta.id == umo.split(":", 1)[0] and meta.name in QQ_ADAPTERS:
                    return True
        except Exception:
            pass
        return False

    async def _session_allows(self, umo: str) -> bool:
        if not await SessionServiceManager.is_session_enabled(umo):
            return False
        if not await SessionServiceManager.is_llm_enabled_for_session(umo):
            return False
        if not await SessionPluginManager.is_plugin_enabled_for_session(umo, PLUGIN_NAME):
            return False
        configured = self.context.get_config(umo=umo)
        provider_settings = configured.get("provider_settings", {})
        if not isinstance(provider_settings, dict) or not provider_settings.get("enable", True):
            return False
        selected = configured.get("plugin_set", ["*"])
        return selected == ["*"] or isinstance(selected, list) and PLUGIN_NAME in selected

    async def _proactive_once(self, now: datetime | None = None):
        async with self._send_lock:
            options = self._options()
            supplied_now = now
            now = now or datetime.now().astimezone()
            stamp = now.timestamp()
            owner, bot = self._identity()
            target = self._state.get("umo", "")
            if (not self._enabled() or not options["proactive_enabled"] or not owner or not bot
                    or self._state.get("owner") != owner or self._state.get("bot") != bot
                    or not self._target_available(target)
                    or quiet_now(now, options["quiet_start"], options["quiet_end"])):
                return False
            if not await self._session_allows(target):
                return False
            interval = options["interval_minutes"] * 60
            received = float(self._state.get("last_received") or 0)
            sent = float(self._state.get("last_sent") or 0)
            count = max(0, int(self._state.get("day_count", 0))) if self._state.get("day") == now.date().isoformat() else 0
            if (not received or stamp - max(received, sent) < interval
                    or stamp - received > 7 * 86400 or count >= options["daily_limit"]
                    or stamp - self._last_attempt < max(600, interval)):
                return False
            self._last_attempt = stamp
            # Never invent a session, use a random provider, run tools, or send a canned fallback.
            provider = await self.context.get_using_provider_async(target)
            if provider is None:
                return False
            manager = self.context.conversation_manager
            cid = await manager.get_curr_conversation_id(target)
            if not cid:
                return False
            conversation = await manager.get_conversation(target, cid)
            if conversation is None:
                return False
            try:
                history = json.loads(conversation.history) if isinstance(conversation.history, str) else conversation.history
            except (TypeError, ValueError):
                return False
            contexts = text_history(history)
            if not contexts:
                return False
            personas = self.context.persona_manager
            _, persona, _, _ = await personas.resolve_selected_persona(
                umo=target, conversation_persona_id=getattr(conversation, "persona_id", None),
                platform_name="aiocqhttp")
            if not persona:
                return False
            system = str(persona.get("prompt") or "")[:12000]
            if not system:
                return False
            note = await self._note()
            # Reserve the interval and daily slot durably before a billable
            # generation or delivery. Failed/uncertain attempts keep their
            # reservation, so reloads cannot retry an already-delivered send.
            self._state.update(last_sent=stamp, day=now.date().isoformat(), day_count=count + 1)
            if not self._save():
                return False
            response = await asyncio.wait_for(provider.text_chat(
                prompt="现在是用户允许的主动问候时间。结合已有上下文，作为大肥鱼自然地发一句简短关心或轻松话题，不催回复，不虚构用户活动，不输出工具调用或角色分析。最多100字。",
                contexts=contexts, system_prompt=system + "\n\n" + note,
                func_tool=None, image_urls=[], audio_urls=[], request_max_retries=1), timeout=45)
            text = str(getattr(response, "completion_text", "") or "").strip()
            if (not text or len(text) > 300 or getattr(response, "tools_call_name", None)
                    or getattr(response, "is_chunk", False)):
                return False
            # Recheck after network wait: a new user message, changed identity,
            # disabled capability, quiet window or switched conversation cancels the send.
            live = self._options()
            fresh_now = datetime.now().astimezone() if supplied_now is None else now
            if (not self._enabled() or not live["proactive_enabled"] or self._identity() != (owner, bot)
                    or self._state.get("umo") != target or self._state.get("last_received") != received
                    or quiet_now(fresh_now, live["quiet_start"], live["quiet_end"])
                    or await manager.get_curr_conversation_id(target) != cid
                    or not self._target_available(target) or not await self._session_allows(target)):
                return False
            sent_ok = await self.context.send_message(target, MessageChain().message(text))
            if sent_ok:
                self._state.update(last_greeting=text)
                self._save()
                return True
            return False

    async def _loop(self):
        while not self._closed:
            await asyncio.sleep(60)
            try:
                if self._enabled() and valid_session(self._state.get("umo"), self._owner()):
                    await self._sample_activity()
                    await self._proactive_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # No exception repr: provider errors can contain URLs or credentials.
                continue
