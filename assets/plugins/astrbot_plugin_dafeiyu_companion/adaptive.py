"""Bounded, local turn timing and group follow-up policy.

The controller observes only events already admitted by host permissions. It
never calls a model, sends, stores a transcript, or grants a capability. Timing
constants are conservative product defaults, not paper-derived optima.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
import hashlib
import math
import re
import time

from .turn_taking import (GROUP_KEY, GroupAdmission, addressed_elsewhere,
    command_event, explicitly_addressed, identity, is_group, no_reply_requested)

POLICY_KEY = "dafeiyu_reply_policy"
_OBSERVED = "dafeiyu_adaptive_observed"
_PROFILES = {
    "natural": (2.4, 20, 90, 3, "自然亲近地回应，先接住这次话题；不机械复述，不连环追问。"),
    "classic": (2.8, 22, 70, 3, "保留大肥鱼俏皮的口吻，内容贴合眼前话题，不把每句都变成角色表演。"),
    "gentle": (3.2, 24, 110, 2, "语气温和，给对方留余地；不催促、不说教，也不替对方判断感受。"),
    "playful": (1.8, 18, 80, 4, "轻快有趣，可以自然接梗；严肃、困难或紧急话题优先认真处理。"),
    "study": (2.2, 20, 75, 2, "清楚直接，先回应当前问题；学习与任务内容保持完整，不用闲聊遮住结论。"),
}
_CONTINUING = re.compile(r"(?:我还没说完|还有(?:一点|个|件|就是)|先别急|等(?:一下|一会|我补充)|我接着说|先说(?:一下|一点)|听我说|[，,、：:]\s*$)")
_CORRECTION = re.compile(r"^(?:不是[，,。\s]|不对|说错了|打错了|改一下|更正|我的意思是|我是说|准确地说|应该是)")
_FORMAL = re.compile(r"(?:正式|重要|严肃|认真(?:一点|点|回答|说)|别闹|不要闹|急事|不要分条|别分条|一条发完|合同|法律|财务|完整文稿|写(?:一篇|一份)|论文|代码|脚本|编程|报错|调试|执行|运行|表格|文档|计算|步骤|详细(?:说明|解释)|操作电脑|打开文件|删除文件)", re.I)
_QUESTION = re.compile(r"(?:[?？]\s*$|(?:什么|怎么|怎样|如何|为何|为什么|哪个|哪里|多少|能不能|可不可以|是否|有没有|要不要).{0,100}|[吗呢]\s*[。！!]?\s*$)")
_URGENT = re.compile(r"(?:急救|救命|着火|失火|自杀|想死|不想活|立即停止|马上停止|危险|紧急)")
_DISTRESS = re.compile(r"(?:很难过|好难过|心情不好|很伤心|好伤心|有点难受|好累|很焦虑|很害怕|好害怕|很生气|气死了)")
_EXCITED = re.compile(r"(?:好开心|太开心|太棒了|好耶|成功了|哈哈哈|太可爱|好可爱)")
_CASUAL_SWITCH = re.compile(r"(?:^|[，,。；;])\s*(?:(?:小鱼|大肥鱼)[，,]?\s*)?(?:那|好|现在|先|我们|咱们|来|你陪我|陪我)?\s*(?:一起聊聊|随便聊聊|聊聊天|闲聊(?:一下|一会|吧|[。！!\s]*$)|换(?:个|一个|点)?(?:轻松|开心|有趣|日常)?的?话题|(?:聊|说|谈)(?:点|一点)(?:轻松|开心|有趣|日常)的?)")
_FOLLOWUP = re.compile(r"^(?:那(?:么|个|这|就|要|你|它|他|她|为|怎|还|能|是|也|会|我)|然后呢|后来呢|接下来呢|为什么(?:呢|呀)?[?？]?$|为啥|怎么(?:会|办|说|做)|所以|也就是说|可是|不过|但是|还有呢|再(?:说|讲|举|来)|具体(?:呢|一点)|什么意思|这样(?:吗|呢)|你(?:刚才|说的)|这个|那个|它(?:呢|是|会|要))")
_FOLLOWUP_REQUEST = re.compile(r"^(?:再(?:说|讲)(?:详细|具体)(?:一)?点|再举个例子|接着说|继续说|再来一张|再来一个)[。！!～~\s]*$")
_TOPIC_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,30}|[\u4e00-\u9fff]{2,40}")
_STOP_BIGRAMS = {"这个", "那个", "什么", "怎么", "一下", "可以", "就是", "知道", "我们", "你们", "他们", "大肥", "肥鱼", "小鱼", "今天", "现在", "然后", "哈哈", "一个", "不是", "因为", "所以", "时候", "一下", "有没有"}
_CONFIRM = {"好", "好的", "好呀", "可以", "可以呀", "可以的", "行", "行呀", "来吧", "收到", "嗯", "嗯嗯"}


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _number(value, default, low, high):
    return (min(high, max(low, float(value))) if isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value) else default)


def _topic(text):
    """Retain at most 48 irreversible topic hashes, never incoming text."""
    parts = []
    for word in _TOPIC_WORDS.findall((text or "")[:2000].lower()):
        if word.isascii():
            parts.append(_hash(word))
        else:
            parts.extend(_hash(word[index:index + 2]) for index in range(len(word) - 1)
                         if word[index:index + 2] not in _STOP_BIGRAMS)
    return frozenset(parts[:48])


def _group_key(event):
    group = str(getattr(event, "get_group_id", lambda: "")() or "")
    if not group:
        group = str(getattr(getattr(event.message_obj, "group", None), "group_id", "") or "")
    if not group:
        group = event.unified_msg_origin.split(":", 2)[-1].split("_")[-1]
    return _hash(event.get_platform_id() + "\0" + group)


def _direct_mention(event):
    bot = str(event.get_self_id())
    return any(type(item).__name__ == "At" and str(getattr(item, "qq", "")) == bot
               or type(item).__name__ == "Reply" and str(getattr(item, "sender_id", "")) == bot
               for item in event.get_messages())


@dataclass
class _Cadence:
    last: float
    ema: float = 2.5
    samples: int = 0
    signals: dict = field(default_factory=dict)


@dataclass
class _Group:
    traffic: deque = field(default_factory=lambda: deque(maxlen=64))
    # Last acknowledged partner only: unrelated members cannot acquire a turn
    # merely by borrowing someone else's topic or a group-wide response time.
    partner: str | None = None
    replied: float | None = None
    response_token: tuple | None = None
    confirmation_until: float | None = None
    confirmation_consumed: bool = False
    topic: frozenset = field(default_factory=frozenset)
    followups: deque = field(default_factory=lambda: deque(maxlen=12))


class AdaptiveController:
    """Synchronous policy; state updates are atomic within one event loop."""
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._cadence = OrderedDict()
        self._groups = OrderedDict()
        self._seen = OrderedDict()
        self._sent = OrderedDict()
        self._legacy = GroupAdmission(clock=clock)
        self._closed = False

    def close(self):
        self._closed = True
        for store in (self._cadence, self._groups, self._seen, self._sent):
            store.clear()

    @staticmethod
    def _bounded(store, key, value, maximum):
        store[key] = value
        store.move_to_end(key)
        while len(store) > maximum:
            store.popitem(last=False)
        return value

    def _observe(self, event):
        ident = identity(event)
        if self._closed or ident is None:
            return None
        token = tuple(_hash(part) for part in ident)
        if token in self._seen:
            return self._seen[token]
        now = self._clock()
        key = token[:2]
        state = self._cadence.get(key)
        if state is None or now < state.last or now - state.last > 120:
            state = _Cadence(last=now)
        else:
            decay = 2 ** (-(now - state.last) / 45.0)
            state.signals = {key: value * decay for key, value in state.signals.items() if value * decay >= .1}
            gap = min(20.0, max(.25, now - state.last))
            state.ema = gap if state.samples == 0 else .35 * gap + .65 * state.ema
            state.samples = min(state.samples + 1, 50)
            state.last = now
        text = event.get_message_str() or ""
        cue = ("focused" if _FORMAL.search(text) or _URGENT.search(text) else
               "support" if _DISTRESS.search(text) else "playful" if _EXCITED.search(text) else None)
        casual_switch = bool(cue is None and _CASUAL_SWITCH.search(text))
        if casual_switch:
            # A real request to change to casual talk is a stronger turn cue
            # than our fading task heuristic. Assent/ambiguous supplements do
            # not match this, and explicit formal/urgent content still wins.
            state.signals.pop("focused", None)
        # Uninvited group traffic contributes only arrival timing and member
        # hashes. Its semantic cues are not retained across events.
        if cue and not is_group(event):
            state.signals = {cue: 1.0}
        scene = max(state.signals, key=state.signals.get) if state.signals else "calm"
        if state.signals and state.signals[scene] < .35:
            scene = "calm"
        if cue:
            scene = cue
        self._bounded(self._cadence, key, state, 256)
        busy = False
        if is_group(event):
            group_key = _group_key(event)
            group = self._groups.get(group_key) or _Group()
            group.traffic.append((now, token[1]))
            while group.traffic and now - group.traffic[0][0] > 30:
                group.traffic.popleft()
            busy = len(group.traffic) >= 8 or len({item[1] for item in group.traffic}) >= 4
            self._bounded(self._groups, group_key, group, 128)
        result = {"ema": state.ema, "samples": state.samples, "busy": busy, "token": token, "scene": scene,
                  "casual_switch": casual_switch}
        self._bounded(self._seen, token, result, 2048)
        return result

    def predict(self, event, options, profile=None):
        """Observe once, return safe timing overrides and public reply policy."""
        seen = self._observe(event)
        profile = profile if isinstance(profile, str) and profile in _PROFILES else options.get("persona_profile", "natural")
        profile = profile if isinstance(profile, str) and profile in _PROFILES else "natural"
        base, cap, _, bubbles, style = _PROFILES[profile]
        mode = options.get("reply_timing_mode", "adaptive")
        mode = mode if mode in {"adaptive", "fixed", "instant"} else "adaptive"
        max_wait = _number(options.get("collection_max_wait_seconds"), 30, 1, 120)
        text = (event.get_message_str() or "").strip()
        scene = seen["scene"] if seen else "calm"
        reasons = []
        response_mode = "auto"
        # Media objects remain untouched. A photo may be followed by a caption;
        # do not mistake its empty visible string for a finished short question.
        media = any(type(item).__name__ not in {"Plain", "At", "Reply"} for item in event.get_messages())
        if no_reply_requested(event):
            delay, response_mode, bubbles = 0.0, "single", 1
            reasons.append("explicit_end")
        elif command_event(event) or _URGENT.search(text):
            delay, response_mode, bubbles = 0.0, "single", 1
            reasons.append("command_or_urgent")
        elif _FORMAL.search(text) or "```" in text or re.search(r"^\s*\|.+\|\s*$", text, re.M):
            delay, response_mode, bubbles = 1.2, "single", 1
            reasons.append("formal_or_task")
        elif _CONTINUING.search(text):
            delay = min(12.0, max(6.0, base + (seen["ema"] if seen else 2.5) * 1.4))
            reasons.append("explicit_continuation")
        elif _CORRECTION.search(text):
            delay = min(8.0, max(4.0, base + (seen["ema"] if seen else 2.5)))
            reasons.append("correction")
        elif media:
            delay = max(4.5, base + 1.5)
            reasons.append("media_caption_window")
        elif _QUESTION.search(text) and len(text) >= 5:
            delay = min(2.0, max(.8, base * .55 + (seen["ema"] if seen else 2.5) * .12))
            reasons.append("complete_question")
        elif len(text) <= 9 and not re.search(r"[。！？!?]$", text):
            delay = min(7.0, max(4.0, base + (seen["ema"] if seen else 2.5) * .7))
            reasons.append("short_fragment")
        else:
            delay = min(6.0, max(1.2, base + (seen["ema"] if seen else 2.5) * .4))
            reasons.append("conversational_turn")
        if seen and seen["samples"]:
            reasons.append("bounded_cadence_ema")
        if seen and seen.get("casual_switch"):
            reasons.append("explicit_casual_switch")
        if response_mode != "single":
            response_mode = "auto" if profile == "study" else "bubbles"
            if scene == "support":
                style += " 最近话题提及困难，先认真接住、不说教；不推断对方此刻的心理或健康状态，不作单句诊断。"
                response_mode = "auto"
                bubbles = min(bubbles, 2)
                if reasons[0] in {"conversational_turn", "complete_question"}:
                    delay = min(delay, 2.4)
                reasons.append("recent_support_context")
            elif scene == "playful":
                style += " 可以跟随对方明确表达的开心，回应适度，不夸大或凭空制造亲密。"
                if reasons[0] == "conversational_turn":
                    delay = max(1.2, delay * .85)
                reasons.append("recent_positive_context")
            elif scene == "focused":
                response_mode, bubbles = "single", 1
                style += " 最近在认真处理事情，保持清楚完整；对方切回闲聊后不强行延续任务语气。"
                reasons.append("recent_focused_context")
        if mode == "fixed":
            if reasons[0] not in {"explicit_end", "command_or_urgent"}:
                delay = _number(options.get("collection_seconds"), 10, 0, 30)
            cap = max_wait
            reasons.append("fixed_wait")
        elif mode == "instant":
            delay, cap = 0.0, max_wait
            reasons.append("instant_wait")
        delay = min(max_wait, delay)
        if seen is None:
            delay, response_mode, bubbles = 0.0, "single", 1
            reasons.append("unavailable")
        plan = {"version": 1, "mode": mode, "profile": profile,
                "collection_seconds": round(delay, 2),
                "collection_max_wait_seconds": min(max_wait, cap),
                "response_mode": response_mode, "max_bubbles": bubbles,
                "style_hint": style, "reasons": reasons,
                "scene": scene,
                "cadence_samples": seen["samples"] if seen else 0,
                "group_busy": bool(seen and seen["busy"])}
        event.set_extra(POLICY_KEY, plan)
        event.set_extra(_OBSERVED, bool(seen))
        return dict(plan)

    def admit(self, event, options, host_config):
        """Return eligibility only; host/session/white-list gates stay outside."""
        if self._closed or identity(event) is None:
            return "silent"
        if not is_group(event):
            return "private"
        seen = self._observe(event)
        mode = options.get("group_reply_mode", "auto")
        if mode not in {"auto", "quiet"}:
            return self._legacy.admit(event, options, host_config)
        if command_event(event):
            return "command"
        if _direct_mention(event) or mode == "auto" and explicitly_addressed(event, options, host_config):
            return "addressed"
        if mode == "quiet" or addressed_elsewhere(event) or seen is None:
            return "silent"
        group = self._groups.get(_group_key(event))
        now = self._clock()
        policy = event.get_extra(POLICY_KEY)
        profile = policy.get("profile") if isinstance(policy, dict) else options.get("persona_profile", "natural")
        profile = profile if isinstance(profile, str) and profile in _PROFILES else "natural"
        window = _PROFILES[profile][2]
        busy = seen["busy"]
        if busy:
            window = min(window, 25)
        if (not group or group.partner != seen["token"][1] or group.replied is None
                or not 0 <= now - group.replied <= window):
            return "silent"
        text = (event.get_message_str() or "").strip()
        if not text or len(text) > 500 or no_reply_requested(event):
            return "silent"
        overlap = len(_topic(text) & group.topic)
        question = bool(_QUESTION.search(text))
        confirmation = (group.confirmation_until is not None and now <= group.confirmation_until
                        and all(type(item).__name__ == "Plain" for item in event.get_messages())
                        and re.sub(r"[\s，,。.!！～~]+", "", text) in _CONFIRM)
        confirmation = confirmation or bool(seen.get("confirmation_reserved") and now <= group.replied + 45)
        related = (bool(_FOLLOWUP.search(text)) and question
                   or bool(_FOLLOWUP_REQUEST.fullmatch(text)) or overlap >= 2 and question)
        if not (confirmation or related) or busy and overlap < 2 and not confirmation:
            return "silent"
        # Follow-ups are invited dialogue, not random group participation. A
        # small sliding budget still prevents a two-person bot flood.
        while group.followups and now - group.followups[0] >= 60:
            group.followups.popleft()
        if seen.get("followup_reserved"):
            return "auto_followup"
        if len(group.followups) >= (2 if busy else 4):
            return "silent"
        group.followups.append(now)
        seen["followup_reserved"] = True
        if confirmation:
            group.confirmation_until = None
            group.confirmation_consumed = True
            seen["confirmation_reserved"] = True
        return "auto_followup"

    def mark_sent(self, event, *, accepted=True):
        """A real final packet accepted; never call for planning/empty sends."""
        ident = identity(event)
        if self._closed or not accepted or ident is None or not is_group(event):
            return
        token = tuple(_hash(part) for part in ident)
        if event.get_extra(GROUP_KEY) not in {"addressed", "continuation", "auto_followup"}:
            return
        count = event.get_extra("dafeiyu_bubbles_sent_count", 1)
        count = count if type(count) is int and count > 0 else 1
        previous = self._sent.get(token, 0)
        if count <= previous:
            return
        group_key = _group_key(event)
        group = self._groups.get(group_key) or _Group()
        if not previous:
            group.partner, group.replied = token[1], self._clock()
            group.response_token = token
            group.topic = _topic(event.get_message_str())
            group.confirmation_until = None
            group.confirmation_consumed = False
        if group.response_token == token:
            # A following image-only packet has no visible question and must
            # not revoke an invitation already accepted in the preceding text.
            if not group.confirmation_consumed and event.get_extra("dafeiyu_last_packet_invites_reply") is True:
                group.confirmation_until = group.replied + 45
        self._bounded(self._groups, group_key, group, 128)
        self._bounded(self._sent, token, count, 2048)
