"""Small, local companion state. No transcript, key or screen collection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as datetime_timezone
import ctypes
from ctypes import wintypes
import os
import hashlib
import json
from pathlib import Path

DEFAULTS = {
    "proactive_enabled": False, "emotion_enabled": False, "activity_enabled": False,
    "whale_style_enabled": False,
    "natural_bubbles_enabled": False, "natural_bubbles_frequency": "occasional",
    "interval_minutes": 120, "daily_limit": 4, "quiet_start": 23, "quiet_end": 8,
    "schedule_mode": "random", "min_interval_minutes": 60, "max_interval_minutes": 180,
    "idle_minutes": 40, "trigger_probability": 100, "unanswered_decay_percent": 50,
    "max_unanswered": 4, "session_expiry_days": 7, "timezone": "local",
}
BOOL_FIELDS = ("proactive_enabled", "emotion_enabled", "activity_enabled", "whale_style_enabled", "natural_bubbles_enabled")
RANGES = {"interval_minutes": (5, 1440), "daily_limit": (0, 20),
          "quiet_start": (0, 23), "quiet_end": (0, 23),
          "min_interval_minutes": (5, 1440), "max_interval_minutes": (5, 2880),
          "idle_minutes": (0, 1440), "trigger_probability": (0, 100),
          "unanswered_decay_percent": (0, 100), "max_unanswered": (0, 20), "session_expiry_days": (0, 365)}
ENUMS = {"schedule_mode": {"fixed", "random"}, "timezone": {"local", "Asia/Shanghai", "UTC"},
         "natural_bubbles_frequency": {"occasional", "often"}}
PRESETS = {"dsh_reference": {"schedule_mode": "random", "min_interval_minutes": 60,
    "max_interval_minutes": 180, "idle_minutes": 40, "daily_limit": 4,
    "quiet_start": 23, "quiet_end": 8, "timezone": "local"}}

RUNTIME_MESSAGES = {
    "host_incompatible": "宿主缺少兼容的会话锁，主动发送已暂停；请使用经过验证的 AstrBot 版本。",
    "disabled": "主动聊天未开启；插件总开关和主动聊天开关都需要开启。",
    "waiting_owner": "请先在桌面端设置主人和大肥鱼 QQ。",
    "waiting_session": "等待主人发来一条 QQ 私聊，以确认实际联系会话。",
    "platform_unavailable": "等待已登记的 QQ 连接恢复。",
    "quiet_hours": "当前为免打扰时段，不会主动联系。",
    "waiting_interval": "已安排下一次主动机会；到时仍须通过概率与当前状态判断，不承诺必发。",
    "daily_limit": "今天的主动生成名额已用完，明天继续。",
    "session_stale": "超过设定天数没有主人私聊，主动联系已暂停；聊一句即可恢复。",
    "unanswered_pause": "连续主动联系尚未获回应，已按设置暂停；主人私聊后恢复。",
    "probability_paused": "触发概率已设为0，当前不会调用模型或主动发送。",
    "probability_skipped": "本次主动机会未命中概率，未调用模型，已安排下一次机会。",
    "session_paused": "当前会话、模型服务或陪伴插件在此会话被停用。",
    "model_unavailable": "当前会话没有可用聊天模型，恢复后自动重试。",
    "waiting_context": "等待当前私聊产生聊天记录和可用人格后再自主搭话。",
    "ready": "已到自主搭话时间，正在检查会话和模型。",
    "generating": "正在结合最近聊天自行找话题，尚未确认发送。",
    "sent": "最近一次自主搭话已交给 QQ 连接发送。",
    "declined": "本次模型选择暂不打扰，已保留本次生成名额。",
    "cancelled": "生成期间会话、开关或免打扰条件变化，本次未发送。",
    "generation_failed": "本次生成失败或未返回有效短消息；保留名额以避免重复。",
    "delivery_uncertain": "本次送达结果不确定；保留名额和间隔，避免重复发送。",
    "storage_error": "本机状态无法保存，已停止本次生成与发送。",
    "stopped": "陪伴后台任务已停止。",
    "not_running": "没有收到陪伴任务的新心跳，请启动服务或重新加载插件。",
}

# Original topic rotation, informed by the referenced DSH speak-up behavior.
# No fictional real-world events or hidden memory are presented as facts.
PROACTIVE_ANGLES = (
    "从最近聊天中挑一个尚未聊完的具体话题，自然接一句；没有合适的就换轻松新话题。",
    "主动提出一个适合一起聊的小点子、轻松选择题或好奇问题，不总问‘在干嘛’。",
    "结合当前本机时间开启一个生活小话题；不要推断用户是否吃饭、睡觉或正在工作。",
    "分享一个符合大肥鱼口吻的小联想或有趣问题；想象须说成想象，不虚构现实经历。",
    "从已有聊天里明确出现的兴趣切入，聊一点新想法；缺少依据时选择一般轻松话题。",
)


def runtime_signature(config: dict) -> str:
    """Bind a public heartbeat to current settings without exposing identities."""
    data = {**normalize(config), "master_enabled": config.get("enabled") is True,
            "owner": str(config.get("owner_qq") or ""), "bot": str(config.get("bot_qq") or "")}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def schedule_now(options: dict, now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    # Windows builds need no IANA tzdata for these explicit choices.
    selected = options["timezone"]
    if selected == "Asia/Shanghai":
        return now.astimezone(datetime_timezone(timedelta(hours=8)))
    if selected == "UTC":
        return now.astimezone(datetime_timezone.utc)
    return now


def interval_bounds(options: dict) -> tuple[int, int]:
    if options["schedule_mode"] == "fixed":
        return options["interval_minutes"], options["interval_minutes"]
    return options["min_interval_minutes"], options["max_interval_minutes"]


def effective_probability(state: dict, options: dict) -> float:
    unanswered = max(0, min(1000000, int(state.get("unanswered_count", 0))))
    return round(options["trigger_probability"] * (1 - options["unanswered_decay_percent"] / 100) ** unanswered, 6)


def plan_signature(options: dict) -> str:
    keys = ("schedule_mode", "interval_minutes", "min_interval_minutes", "max_interval_minutes", "idle_minutes")
    return hashlib.sha256(json.dumps({key: options[key] for key in keys}, sort_keys=True).encode()).hexdigest()


def opportunity_window(state: dict, options: dict) -> tuple[float, float]:
    anchor = max(float(state.get(key) or 0) for key in ("last_received", "last_sent", "last_opportunity_at"))
    low, high = interval_bounds(options)
    idle_until = float(state.get("last_received") or 0) + options["idle_minutes"] * 60
    return max(anchor + low * 60, idle_until), max(anchor + high * 60, idle_until)


def after_quiet(stamp: float, options: dict, now: datetime) -> float:
    candidate = datetime.fromtimestamp(stamp, tz=now.tzinfo)
    if quiet_now(candidate, options["quiet_start"], options["quiet_end"]):
        candidate = candidate.replace(hour=options["quiet_end"], minute=0, second=0, microsecond=0)
        if candidate.timestamp() < stamp:
            candidate += timedelta(days=1)
    return candidate.timestamp()


def schedule_state(state: dict, options: dict, now: datetime, *, enabled: bool,
                   identity_ready: bool, session_known: bool) -> dict:
    """Pure scheduling policy shared by status and the actual worker."""
    now = schedule_now(options, now)
    stamp = now.timestamp()
    count = max(0, int(state.get("day_count", 0))) if state.get("day") == now.date().isoformat() else 0
    result = {"session_known": session_known, "daily_count": count,
              "daily_limit": options["daily_limit"], "next_trigger_at": None,
              "next_window_start": None, "next_window_end": None, "schedule_mode": options["schedule_mode"],
              "timezone": options["timezone"], "unanswered_count": max(0, int(state.get("unanswered_count", 0))),
              "effective_probability": effective_probability(state, options), "paused_until_reply": False}
    code = "ready"
    if not enabled or not options["proactive_enabled"]:
        code = "disabled"
    elif not identity_ready:
        code = "waiting_owner"
    elif not session_known:
        code = "waiting_session"
    else:
        received, sent = float(state.get("last_received") or 0), float(state.get("last_sent") or 0)
        low, high = opportunity_window(state, options)
        planned = float(state.get("next_opportunity_at") or 0)
        due = planned if state.get("plan_signature") == plan_signature(options) and low <= planned <= high else low
        result.update(next_window_start=after_quiet(low, options, now), next_window_end=after_quiet(high, options, now))
        if not received:
            code = "waiting_session"
        elif options["session_expiry_days"] and stamp - received > options["session_expiry_days"] * 86400:
            code = "session_stale"
        elif options["max_unanswered"] and result["unanswered_count"] >= options["max_unanswered"]:
            code = "unanswered_pause"
            result["paused_until_reply"] = True
        elif result["effective_probability"] <= 0:
            code = "probability_paused"
        elif count >= options["daily_limit"]:
            code = "daily_limit"
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            if options["daily_limit"]:
                result["next_trigger_at"] = after_quiet(max(due, tomorrow.timestamp()), options, now)
                result["next_window_start"] = after_quiet(max(low, tomorrow.timestamp()), options, now)
                result["next_window_end"] = after_quiet(max(high, tomorrow.timestamp()), options, now)
        else:
            result["next_trigger_at"] = after_quiet(max(due, stamp), options, now)
            if quiet_now(now, options["quiet_start"], options["quiet_end"]):
                code = "quiet_hours"
            elif stamp < due:
                code = "waiting_interval"
    if code in {"session_stale", "unanswered_pause", "probability_paused"} or code == "daily_limit" and not options["daily_limit"]:
        result.update(next_window_start=None, next_window_end=None)
    message = "每日生成预算设为0，主动生成已停用。" if code == "daily_limit" and not options["daily_limit"] else RUNTIME_MESSAGES[code]
    return {**result, "code": code, "message": message}

# Original AstrBot adaptation; research references are recorded in SOURCES.md.
# It changes visible expression only, never a provider's private reasoning.
WHALE_STYLE_CONTEXT = (
    "可选的大肥鱼口吻：用亲近、口语化的中文交流，带一点傲娇和嘴硬心软的甜意，"
    "可自然提到米饭或小尾巴，但不必每次自报身份、堆语气词或重复同一句口头禅。"
    "承接这次对话的具体内容，不要套用固定欢迎词、机械分点或客服式收尾；"
    "不要无故说‘还有什么可以帮助你的吗’，也不把‘避免模板回复’误解为发送告别台词。"
    "遇到认真问题仍给准确、有用的答案，不用卖萌代替解决问题。"
    "用户可见的解释优先用中文，提供简短理由、结论和实际操作结果；"
    "不展示内部推理草稿、隐藏思维链，也不承诺能改变模型内部的思考语言。"
    "是否能看屏幕或操作电脑，只依据当前实际工具、授权与执行结果；"
    "不虚构看见、操作成功或尚未具备的能力。保留已有身份事实、专业任务要求和权限边界。"
)


def normalize(data: dict, *, strict: bool = False) -> dict:
    result = dict(DEFAULTS)
    legacy = "interval_minutes" in data and "schedule_mode" not in data
    if legacy:
        result.update(schedule_mode="fixed", idle_minutes=0, unanswered_decay_percent=0, max_unanswered=0)
    for key in BOOL_FIELDS:
        if key in data:
            if strict and type(data[key]) is not bool:
                raise ValueError(f"{key} 必须是开关值")
            result[key] = data[key] is True
    for key, (low, high) in RANGES.items():
        value = data.get(key, result[key])
        if type(value) is not int or not low <= value <= high:
            if strict:
                raise ValueError(f"{key} 必须在 {low} 到 {high} 之间")
            value = result[key]
        result[key] = value
    for key, choices in ENUMS.items():
        value = data.get(key, result[key])
        if not isinstance(value, str) or value not in choices:
            if strict:
                raise ValueError(f"{key} 的选项无效")
            value = result[key]
        result[key] = value
    if result["max_interval_minutes"] < result["min_interval_minutes"]:
        if strict:
            raise ValueError("最长主动机会间隔不能小于最短间隔")
        result["max_interval_minutes"] = result["min_interval_minutes"]
    result["enabled"] = any(result[key] for key in BOOL_FIELDS)
    return result


def quiet_now(now: datetime, start: int, end: int) -> bool:
    # Equal endpoints mean no quiet window; a window crossing midnight is usual.
    if start == end:
        return False
    return start <= now.hour < end if start < end else now.hour >= start or now.hour < end


def valid_session(umo, owner: str) -> bool:
    if not isinstance(umo, str) or len(umo) > 300:
        return False
    parts = umo.split(":")
    return len(parts) == 3 and bool(parts[0]) and parts[1] == "FriendMessage" and parts[2] == owner


def mood_after(current: dict, text: str) -> dict:
    """Role-playing state, deliberately neither sentiment diagnosis nor sentience."""
    text = str(text)[:2000].casefold()
    mood = max(-2, min(2, int(current.get("warmth", 0))))
    energy = max(0, min(4, int(current.get("energy", 2))))
    if any(word in text for word in ("累了", "好累", "难过", "伤心", "烦躁", "sad", "不开心")):
        mood = max(-2, mood - 1)
        energy = max(0, energy - 1)
    elif any(word in text for word in ("开心", "谢谢", "喜欢", "好耶", "哈哈", "happy", "爱你")):
        mood = min(2, mood + 1)
        energy = min(4, energy + 1)
    else:
        mood += -1 if mood > 0 else 1 if mood < 0 else 0
    return {"warmth": mood, "energy": energy}


def mood_summary(mood: dict) -> str:
    warmth = int(mood.get("warmth", 0))
    tone = "元气、亲近" if warmth > 0 else "温柔、愿意倾听" if warmth < 0 else "平和、好奇"
    return (f"大肥鱼的互动角色状态：{tone}。这只是可调的陪伴表达风格，"
            "不是实际意识、真实情绪或对用户心理的判断。顺着对方语气自然回应，"
            "不要卖惨索取关注，不要要求用户持续聊天。")


def text_history(history, *, limit: int = 10, budget: int = 6000) -> list[dict]:
    """Only recent ordinary text goes into a tool-free proactive generation."""
    if not isinstance(history, list):
        return []
    output = []
    for item in reversed(history[-40:]):
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        if item.get("tool_calls"):
            continue
        content = item.get("content")
        if isinstance(content, list):
            content = "\n".join(str(part.get("text", "")) for part in content
                                if isinstance(part, dict) and part.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            continue
        content = content[-min(1200, budget):]
        output.append({"role": item["role"], "content": content})
        budget -= len(content)
        if len(output) >= limit or budget <= 0:
            break
    return list(reversed(output))


def foreground_activity() -> dict:
    """Read process basename and idle seconds only. No title, keys or pixels."""
    if os.name != "nt":
        return {"available": False}
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetTickCount64.restype = ctypes.c_ulonglong
    last = LastInput(ctypes.sizeof(LastInput), 0)
    if not user.GetLastInputInfo(ctypes.byref(last)):
        return {"available": False}
    idle = ((kernel.GetTickCount64() & 0xFFFFFFFF) - last.dwTime) & 0xFFFFFFFF
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
    name = ""
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if handle:
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                name = Path(buffer.value).name[:100]
        finally:
            kernel.CloseHandle(handle)
    return {"available": True, "process": name, "idle_minutes": min(1440, idle // 60000), "away": idle >= 5 * 60000}


def activity_summary(current: dict, previous: dict | None = None) -> str:
    if not current.get("available"):
        return "电脑活动概况当前不可用；不要猜测用户正在做什么。"
    change = ""
    if previous and previous.get("available") and previous.get("away") != current.get("away"):
        change = "（刚离开电脑）" if current.get("away") else "（刚回到电脑）"
    app = current.get("process") or "未知应用"
    return (f"用户授权的电脑活动概况：前台应用进程 {app}；约 {current.get('idle_minutes', 0)} 分钟未输入；"
            f"{'暂时离开' if current.get('away') else '正在使用电脑'}{change}。"
            "只知道应用名和离开/回来，不知道窗口标题、屏幕内容或键盘输入，不要假装看到了这些内容。")
