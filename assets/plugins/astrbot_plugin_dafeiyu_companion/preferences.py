"""Explicit, expiring companionship preferences; no inferred rejection score."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

PROFILES = {
    "quiet": {"label": "安静陪伴", "description": "留出更长的安静时间，每天最多两次主动生成。",
        "values": {"schedule_mode": "random", "min_interval_minutes": 180, "max_interval_minutes": 360,
                   "idle_minutes": 90, "daily_limit": 2, "trigger_probability": 100,
                   "unanswered_decay_percent": 50, "max_unanswered": 2}},
    "natural": {"label": "自然朋友", "description": "时间安排沿用 Aurabot 参考值，加上可关闭的未回应减扰。",
        "values": {"schedule_mode": "random", "min_interval_minutes": 60, "max_interval_minutes": 180,
                   "idle_minutes": 40, "daily_limit": 4, "trigger_probability": 100,
                   "unanswered_decay_percent": 50, "max_unanswered": 4}},
    "lively": {"label": "活泼陪伴", "description": "提供更多聊天机会；仍遵守免打扰和未回应暂停。",
        "values": {"schedule_mode": "random", "min_interval_minutes": 30, "max_interval_minutes": 90,
                   "idle_minutes": 20, "daily_limit": 6, "trigger_probability": 100,
                   "unanswered_decay_percent": 50, "max_unanswered": 3}},
}


def matched_profile(options):
    return next((name for name, profile in PROFILES.items()
                 if all(options.get(k) == v for k, v in profile["values"].items())), "custom")


def feedback_mode(text):
    if not isinstance(text, str) or len(text) > 80:
        return None
    text = re.sub(r"[\s，,。.!！~～]", "", text)
    if text in {"今天忙少找我", "今天有点忙少找我", "今天想安静", "今天先安静陪着我"}:
        return "quiet"
    if text in {"今天可以多陪我聊聊", "今天多陪我聊聊", "今天可以多找我", "今天多找我聊聊"}:
        return "more"
    if text in {"恢复平时的陪伴节奏", "恢复平时节奏", "取消今天的陪伴调整"}:
        return "normal"
    return None


def _safe(path):
    path = Path(path)
    for part in (path, *list(path.parents)[:3]):
        if part.exists():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("陪伴偏好目录不可为链接")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 8192:
            raise ValueError("陪伴偏好文件不可读取")
    return path


def read_preference(path, owner, bot, *, now=None):
    now = time.time() if now is None else now
    try:
        path = _safe(path)
        with path.open("rb") as stream:
            raw = stream.read(8193)
        data = json.loads(raw)
        if (len(raw) > 8192 or not isinstance(data, dict) or data.get("owner") != owner or data.get("bot") != bot
                or data.get("mode") not in {"quiet", "more"} or type(data.get("expires_at")) not in (int, float)
                or type(data.get("created_at")) not in (int, float)
                or not data["created_at"] <= now + 30 < data["expires_at"] + 30
                or not 0 < data["expires_at"] - data["created_at"] <= 86400
                or now >= data["expires_at"]):
            return {}
        return data
    except (OSError, ValueError, TypeError, UnicodeError):
        return {}


def write_preference(path, owner, bot, mode, *, duration_hours=24, now=None, expires_at=None):
    if mode not in {"quiet", "more", "normal"} or type(duration_hours) is not int or not 1 <= duration_hours <= 24:
        raise ValueError("请选择安静、更多陪伴或恢复平时，时长为1至24小时")
    if not isinstance(owner, str) or not owner.isdigit() or not isinstance(bot, str) or not bot.isdigit():
        raise ValueError("请先配置主人与大肥鱼 QQ")
    now = time.time() if now is None else now
    expires_at = now + duration_hours * 3600 if expires_at is None else expires_at
    if type(expires_at) not in (int, float) or not 0 < expires_at - now <= 86400:
        raise ValueError("临时偏好须在未来24小时内到期")
    data = {"version": 1, "owner": owner, "bot": bot, "mode": mode,
            "created_at": now, "expires_at": expires_at}
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".preference-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        _safe(path)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return data


def effective_options(options, preference):
    result = dict(options)
    if preference.get("mode") == "quiet":
        result["trigger_probability"] = 0
    elif preference.get("mode") == "more":
        # An explicit request changes opportunities, never silently raises the
        # already selected model-call/day budget or bypasses quiet hours.
        low = result["interval_minutes"] if result["schedule_mode"] == "fixed" else result["min_interval_minutes"]
        high = result["interval_minutes"] if result["schedule_mode"] == "fixed" else result["max_interval_minutes"]
        result.update(schedule_mode="random",
                      min_interval_minutes=min(low, 30),
                      max_interval_minutes=min(high, 90),
                      idle_minutes=min(result["idle_minutes"], 20))
    return result


def public_preference(data):
    return {"active": bool(data), "mode": data.get("mode", "normal"), "expires_at": data.get("expires_at"),
            "label": {"quiet": "先安静陪着你", "more": "今天多一些聊天机会"}.get(data.get("mode"), "平时的陪伴节奏"),
            "note": "临时调整到期后自动恢复；更多机会保留原每日预算和免打扰。"}
