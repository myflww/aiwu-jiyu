"""Optional, owner-only companionship. No unsolicited target discovery."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime
import json
import os
from pathlib import Path
import random
import tempfile
import time

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.star.session_plugin_manager import SessionPluginManager

from .engine import (activity_summary, foreground_activity, mood_after, mood_summary,
                     normalize, quiet_now, text_history, valid_session, WHALE_STYLE_CONTEXT,
                     PROACTIVE_ANGLES, RUNTIME_MESSAGES, runtime_signature, schedule_state,
                     opportunity_window, plan_signature, schedule_now, effective_probability)
from .natural_bubbles import BubbleReplies

PLUGIN_NAME = "astrbot_plugin_dafeiyu_companion"
QQ_ADAPTERS = {"aiocqhttp", "qq"}


@register(PLUGIN_NAME, "myflww", "大肥鱼陪伴：自主搭话、情绪表达与授权活动概况", "1.2.0")
class DafeiyuCompanion(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self._worker = None
        self._closed = False
        self._state_path = None
        self._runtime_path = None
        self._state = {}
        self._runtime = {}
        self._activity = {}
        self._previous_activity = {}
        self._activity_at = 0.0
        self._last_attempt = 0.0
        self._send_lock = asyncio.Lock()
        self._last_message_key = None
        self._bubbles = BubbleReplies()

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
        if self._worker and not self._worker.done():
            return
        if self._closed:
            self._bubbles = BubbleReplies()
        self._closed = False
        # Keep a lightweight worker alive even when capabilities are off.
        # AstrBot may update the config object after plugin initialization.
        # Sampling, model calls and sending still require explicit capability flags.
        directory = StarTools.get_data_dir(PLUGIN_NAME)
        self._state_path = directory / "state.json"
        self._runtime_path = directory / "runtime.json"
        if self._state_path.is_symlink():
            self._state_path = None
        try:
            if self._state_path and self._state_path.is_file() and self._state_path.stat().st_size <= 16 * 1024:
                data = json.loads(self._state_path.read_text("utf-8"))
                owner, bot = self._identity()
                if (isinstance(data, dict) and data.get("owner") == owner and data.get("bot") == bot
                        and valid_session(data.get("umo"), owner)):
                    self._state = data
        except (OSError, ValueError):
            self._state = {}
        self._runtime = {key: self._state.get(key) for key in ("last_result", "last_result_at", "last_success_at")}
        self._worker = asyncio.create_task(self._loop(), name="dafeiyu-companion")
        self._publish()

    async def terminate(self):
        self._closed = True
        self._bubbles.close()
        if self._worker:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        self._activity = {}
        self._previous_activity = {}
        self._publish("stopped")

    @staticmethod
    def _write_json(path, data: dict) -> bool:
        if path is None or path.is_symlink():
            return False
        directory = path.parent
        temporary = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".companion-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return True
        except OSError:
            return False
        finally:
            with suppress(OSError):
                if temporary:
                    temporary.unlink(missing_ok=True)

    def _save(self) -> bool:
        # Metadata and the last short greeting only: never duplicate chat history.
        return self._write_json(self._state_path, self._state)

    @staticmethod
    def _random_unit() -> float:
        return random.random()

    def _ensure_opportunity(self, *, advance: bool = False) -> bool:
        """One persisted draw per opportunity, never a new draw every minute."""
        options = self._options()
        if not self._enabled() or not options["proactive_enabled"] or not self._state.get("last_received"):
            return True
        low, high = opportunity_window(self._state, options)
        planned = float(self._state.get("next_opportunity_at") or 0)
        signature = plan_signature(options)
        if not advance and self._state.get("plan_signature") == signature and low <= planned <= high:
            return True
        planned = low + (high - low) * self._random_unit()
        self._state.update(next_opportunity_at=planned, plan_signature=signature)
        return self._save()

    def _schedule(self, now: datetime | None = None) -> dict:
        owner, bot = self._identity()
        known = (bool(owner and bot) and self._state.get("owner") == owner
                 and self._state.get("bot") == bot and valid_session(self._state.get("umo"), owner))
        return schedule_state(self._state, self._options(), schedule_now(self._options(), now),
                              enabled=self._enabled(), identity_ready=bool(owner and bot), session_known=known)

    def _publish(self, code: str | None = None, *, result: str | None = None, now: datetime | None = None):
        now = schedule_now(self._options(), now)
        current = self._schedule(now)
        if code:
            current.update(code=code, message=RUNTIME_MESSAGES[code])
        if result:
            self._runtime.update(last_result=result, last_result_at=now.timestamp())
            if self._state.get("last_opportunity_at"):
                self._state["last_opportunity_result"] = result
            if result == "sent":
                self._runtime["last_success_at"] = now.timestamp()
            self._state.update({key: self._runtime.get(key) for key in (
                "last_result", "last_result_at", "last_success_at")})
            self._save()
        self._runtime.update(current)
        public = {key: self._runtime.get(key) for key in (
            "code", "message", "next_trigger_at", "session_known", "daily_count", "daily_limit",
            "last_result", "last_result_at", "last_success_at", "schedule_mode", "timezone",
            "next_window_start", "next_window_end", "unanswered_count", "effective_probability", "paused_until_reply")}
        for key in ("last_opportunity_at", "last_opportunity_result", "last_trigger_source", "initiated_count", "last_receipt"):
            public[key] = self._state.get(key)
        public.update(version=1, signature=runtime_signature(self.config), heartbeat_at=now.timestamp(),
                      worker_running=bool(self._worker and not self._worker.done() and not self._closed))
        self._write_json(self._runtime_path, public)

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
        self._bubbles.observe(event)
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
        self._state.update(unanswered_count=0)
        self._state.pop("next_opportunity_at", None)
        if self._options()["emotion_enabled"]:
            self._state["mood"] = mood_after(self._state.get("mood") or {}, event.get_message_str())
        self._save()
        self._ensure_opportunity()
        self._publish()

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
        # The model cannot infer an out-of-band scheduler from its tool list.
        # Describe actual flags and observed readiness instead of claiming a
        # promise to send, or confusing this worker with user-created cron jobs.
        schedule = self._schedule()
        if options["proactive_enabled"]:
            parts.append("主动聊天能力事实：本机‘大肥鱼陪伴’插件已开启自主搭话开关，"
                         "由独立后台每分钟检查已确认的主人私聊、间隔、每日上限和免打扰条件，"
                         "在设置的主动机会到来时，先按概率与未回应减扰规则决定是否联系，"
                         "命中后自行选话题并调用当前模型发起私聊，无需主人创建 cron 或逐次提醒。"
                         "这不代表你有真实自主意识，也不代表此刻已经发送。当前状态：" + schedule["message"] +
                         "不要因为工具列表里只有定时任务就说只能被动等待；也不要承诺准确发送时间、伪称已设定任务或已经发出消息。")
        else:
            parts.append("主动聊天能力事实：本机陪伴插件的自主搭话开关当前关闭，"
                         "不要声称已开启或一定会主动联系；可说明在桌面端陪伴设置中启用后由后台自主选话题，"
                         "无需主人编写 cron。")
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

    @filter.on_llm_tool_respond()
    async def companion_tool_round(self, event: AstrMessageEvent, *_args, **_kwargs):
        if self._owner_event(event):
            self._bubbles.mark_tool(event)

    @filter.on_decorating_result()
    async def natural_chat_bubbles(self, event: AstrMessageEvent):
        if not self._owner_event(event) or not self._options()["natural_bubbles_enabled"]:
            return

        async def still_authorized():
            return (self._owner_event(event) and self._options()["natural_bubbles_enabled"]
                    and await self._session_allows(event.unified_msg_origin))

        await self._bubbles.prepare(event, self._options(), still_authorized,
                                    self.context.get_config(umo=event.unified_msg_origin))

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
            now = schedule_now(options, now)
            stamp = now.timestamp()
            owner, bot = self._identity()
            target = self._state.get("umo", "")
            if not self._ensure_opportunity():
                self._publish("storage_error", result="storage_error", now=now)
                return False
            scheduled = self._schedule(now)
            self._publish(now=now)
            if scheduled["code"] != "ready":
                return False
            if not self._target_available(target):
                self._publish("platform_unavailable", now=now)
                return False
            received = float(self._state.get("last_received") or 0)
            count = scheduled["daily_count"]
            phase = "checking"
            try:
                if not await self._session_allows(target):
                    self._publish("session_paused", now=now)
                    return False
                # Never invent a target, use a random provider, run tools, or
                # send a canned fallback. Unready dependencies retry next tick,
                # rather than silently delaying another whole two-hour interval.
                provider = await self.context.get_using_provider_async(target)
                if provider is None:
                    self._publish("model_unavailable", now=now)
                    return False
                manager = self.context.conversation_manager
                cid = await manager.get_curr_conversation_id(target)
                conversation = await manager.get_conversation(target, cid) if cid else None
                if conversation is None:
                    self._publish("waiting_context", now=now)
                    return False
                try:
                    history = json.loads(conversation.history) if isinstance(conversation.history, str) else conversation.history
                except (TypeError, ValueError):
                    history = None
                contexts = text_history(history)
                _, persona, _, _ = await self.context.persona_manager.resolve_selected_persona(
                    umo=target, conversation_persona_id=getattr(conversation, "persona_id", None),
                    platform_name="aiocqhttp")
                system = str((persona or {}).get("prompt") or "")[:12000]
                if not contexts or not system:
                    self._publish("waiting_context", now=now)
                    return False
                self._state.update(last_opportunity_at=stamp, last_trigger_source="background_idle", last_receipt="not_sent")
                # Decide without a model call. A skipped opportunity has no API
                # cost and consumes no daily generation slot, but its next draw
                # must be durable to prevent repeated rolls after a restart.
                if self._random_unit() * 100 >= scheduled["effective_probability"]:
                    self._state["last_opportunity_result"] = "probability_skipped"
                    if not self._ensure_opportunity(advance=True):
                        self._publish("storage_error", result="storage_error", now=now)
                        return False
                    self._publish(result="probability_skipped", now=now)
                    return False
                self._state["last_opportunity_result"] = "eligible"
                note = await self._note()
                # Rotate an original conversation angle instead of requesting
                # the same generic greeting every time. This is a background
                # initiation, never a fake user message or a user-created cron.
                sequence = max(0, int(self._state.get("topic_sequence", 0)))
                angle = PROACTIVE_ANGLES[sequence % len(PROACTIVE_ANGLES)]
                self._state.update(last_sent=stamp, day=now.date().isoformat(), day_count=count + 1,
                                   topic_sequence=sequence + 1)
                if not self._ensure_opportunity(advance=True):
                    self._publish("storage_error", result="storage_error", now=now)
                    return False
                phase = "generation"
                self._publish("generating", now=now)
                response = await asyncio.wait_for(provider.text_chat(
                    prompt=("[本机后台自主搭话触发；这不是主人发来的消息] "
                            "现在满足主人授权的主动聊天条件。由你自己开启一个自然小话题，"
                            "不是等待主人安排任务，也不要解释 cron、插件或调度机制。"
                            f"本次话题角度：{angle} 当前本机时间：{now:%m月%d日 %H:%M}。"
                            "结合近期聊天和已有口吻，说一两句，最多100字；不要总用‘今天过得怎么样/在干嘛’，"
                            "不要重复上次主动消息，不催回复，不虚构用户活动、新闻或现实经历，"
                            "不调用工具、不输出角色分析或内部思考。如果此时确实不适合联系，只输出[暂不打扰]。"),
                    contexts=contexts, system_prompt=system + "\n\n" + note,
                    func_tool=None, image_urls=[], audio_urls=[], request_max_retries=1), timeout=45)
                text = str(getattr(response, "completion_text", "") or "").strip()
                if text == "[暂不打扰]":
                    self._publish(result="declined", now=now)
                    return False
                if (not text or len(text) > 300 or getattr(response, "tools_call_name", None)
                        or getattr(response, "is_chunk", False)):
                    self._publish(result="generation_failed", now=now)
                    return False
                live = self._options()
                fresh_now = schedule_now(live, now if supplied_now is not None else None)
                if (not self._enabled() or not live["proactive_enabled"] or self._identity() != (owner, bot)
                        or self._state.get("umo") != target or self._state.get("last_received") != received
                        or quiet_now(fresh_now, live["quiet_start"], live["quiet_end"])
                        or live["daily_limit"] < count + 1 or live["trigger_probability"] == 0
                        or (live["max_unanswered"] and self._state.get("unanswered_count", 0) >= live["max_unanswered"])
                        or await manager.get_curr_conversation_id(target) != cid
                        or not self._target_available(target) or not await self._session_allows(target)):
                    self._publish(result="cancelled", now=fresh_now)
                    return False
                # Reserve an unanswered attempt before the adapter call. A
                # crash or missing acknowledgment must not erase this guard.
                self._state.update(unanswered_count=int(self._state.get("unanswered_count", 0)) + 1,
                                   last_receipt="delivery_uncertain")
                if not self._save():
                    self._publish("storage_error", result="storage_error", now=fresh_now)
                    return False
                phase = "delivery"
                sent_ok = await self.context.send_message(target, MessageChain().message(text))
                if sent_ok:
                    self._state.update(last_greeting=text, last_success_at=fresh_now.timestamp(),
                                       last_receipt="adapter_accepted", initiated_count=int(self._state.get("initiated_count", 0)) + 1,
                                       last_opportunity_result="sent")
                    self._save()
                    self._publish(result="sent", now=fresh_now)
                    return True
                self._publish(result="delivery_uncertain", now=fresh_now)
                return False
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never put provider error repr/URLs/credentials in public status.
                result = "delivery_uncertain" if phase == "delivery" else "generation_failed"
                self._publish(result=result, now=now)
                return False

    async def _loop(self):
        while not self._closed:
            try:
                if self._enabled():
                    await self._sample_activity()
                await self._proactive_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._publish(result="generation_failed")
            await asyncio.sleep(60)
