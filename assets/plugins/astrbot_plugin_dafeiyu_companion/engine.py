"""Small, local companion state. No transcript, key or screen collection."""
from __future__ import annotations

from datetime import datetime
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

DEFAULTS = {
    "proactive_enabled": False, "emotion_enabled": False, "activity_enabled": False,
    "whale_style_enabled": False,
    "interval_minutes": 120, "daily_limit": 3, "quiet_start": 23, "quiet_end": 8,
}
BOOL_FIELDS = ("proactive_enabled", "emotion_enabled", "activity_enabled", "whale_style_enabled")
RANGES = {"interval_minutes": (30, 1440), "daily_limit": (1, 10),
          "quiet_start": (0, 23), "quiet_end": (0, 23)}

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
