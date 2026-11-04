"""Desktop-side preferences for the optional bundled companion plugin."""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time
from datetime import timedelta

from backend import config_store
from backend.paths import ASTRBOT, ASSETS

PLUGIN_NAME = "astrbot_plugin_dafeiyu_companion"
_spec = importlib.util.spec_from_file_location("_aiwu_companion_policy", ASSETS / "plugins" / PLUGIN_NAME / "engine.py")
_policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_policy)
DEFAULTS = dict(_policy.DEFAULTS)
_preference_spec = importlib.util.spec_from_file_location("_aiwu_companion_preferences", ASSETS / "plugins" / PLUGIN_NAME / "preferences.py")
_preferences = importlib.util.module_from_spec(_preference_spec)
_preference_spec.loader.exec_module(_preferences)


def temporary_preference(config=None):
    config = config_store.load() if config is None else config
    return _preferences.read_preference(ASTRBOT / "data/plugin_data" / PLUGIN_NAME / "preferences.json",
        str(config.get("owner_qq") or ""), str(config.get("bot_qq") or ""))


def set_temporary(body):
    if not isinstance(body, dict) or set(body) - {"mode", "duration_hours", "until_end_of_day"}:
        raise ValueError("不支持的临时陪伴设置")
    if (type(body.get("until_end_of_day", False)) is not bool
            or "until_end_of_day" in body and "duration_hours" in body):
        raise ValueError("当天结束和指定小时数不能混用")
    config = config_store.load()
    timing = {}
    if body.get("until_end_of_day") is True:
        current = _policy.schedule_now(load(config))
        tomorrow = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        timing = {"now": current.timestamp(), "expires_at": min(tomorrow.timestamp(), current.timestamp() + 86400)}
    _preferences.write_preference(ASTRBOT / "data/plugin_data" / PLUGIN_NAME / "preferences.json",
        str(config.get("owner_qq") or ""), str(config.get("bot_qq") or ""), body.get("mode"),
        duration_hours=body.get("duration_hours", 24), **timing)
    return status(config)


def apply_profile(name):
    if not isinstance(name, str) or name not in _preferences.PROFILES:
        raise ValueError("请选择安静陪伴、自然朋友或活泼陪伴")
    return configure(_preferences.PROFILES[name]["values"])


def load(config: dict | None = None) -> dict:
    config = config_store.load() if config is None else config
    incoming = config.get("companion")
    if not isinstance(incoming, dict):
        # Existing users may have configured the optional plugin in AstrBot
        # before saving any desktop preferences. Do not erase those choices on
        # app startup; explicit desktop preferences become authoritative later.
        path = ASTRBOT / "data" / "config" / f"{PLUGIN_NAME}_config.json"
        try:
            if (not path.is_symlink() and path.is_file() and path.stat().st_size <= 128 * 1024
                    and not (getattr(path.stat(), "st_file_attributes", 0) & 0x400)):
                with path.open("rb") as stream:
                    raw = stream.read(128 * 1024 + 1)
                data = json.loads(raw) if len(raw) <= 128 * 1024 else None
                if isinstance(data, dict):
                    incoming = {key: data[key] for key in DEFAULTS if key in data}
                    if data.get("enabled") is False:
                        # A WebUI master-off preference must remain off too.
                        # This is adopted only before the first desktop save.
                        incoming.update({key: False for key in _policy.BOOL_FIELDS})
        except (OSError, ValueError, TypeError):
            pass
    return _policy.normalize(incoming if isinstance(incoming, dict) else {})


def sync_config(config: dict | None = None) -> None:
    config = config_store.load() if config is None else config
    # No plugin files/config are created on a machine before its runtime exists.
    directory = ASTRBOT / "data" / "config"
    if not (ASTRBOT / "data").is_dir():
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{PLUGIN_NAME}_config.json"
    if path.is_symlink():
        raise ValueError("陪伴插件配置路径不可为符号链接")
    existing = {}
    if path.is_file() and path.stat().st_size <= 128 * 1024:
        try:
            candidate = json.loads(path.read_text("utf-8-sig"))
            if isinstance(candidate, dict):
                existing = candidate
        except (OSError, ValueError):
            pass
    existing.update(load(config))
    existing.update(owner_qq=str(config.get("owner_qq") or ""), bot_qq=str(config.get("bot_qq") or ""), persona_id="大肥鱼")
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".companion-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write((json.dumps(existing, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(body: dict) -> dict:
    if not isinstance(body, dict) or set(body) - set(DEFAULTS):
        raise ValueError("不支持的陪伴设置")
    config = config_store.load()
    data = load(config)
    data.update(body)
    data = _policy.normalize(data, strict=True)
    if data["enabled"] and not str(config.get("owner_qq") or "").strip():
        raise ValueError("请先设置主人 QQ，再开启陪伴能力")
    config["companion"] = data
    config_store.save(config)
    sync_config(config)
    return status(config)


def status(config: dict | None = None) -> dict:
    from backend import plugins
    config = config_store.load() if config is None else config
    return {**load(config), "owner_configured": bool(config.get("owner_qq")),
            "plugin_installed": plugins.plugin_path(PLUGIN_NAME).is_dir(),
            "runtime": _runtime_status(config),
            "profiles": json.loads(json.dumps(_preferences.PROFILES, ensure_ascii=False)),
            "profile": _preferences.matched_profile(load(config)),
            "temporary_preference": _preferences.public_preference(temporary_preference(config)),
            "effective_settings": _preferences.effective_options(load(config), temporary_preference(config)),
            "presets": {key: dict(value) for key, value in _policy.PRESETS.items()},
            "preset_sources": {"dsh_reference": {"label": "Aurabot 参考值",
                "source": "https://github.com/aureth-labs/dsh-aurabot-qq/blob/b3301f6b014016e141c07d266a716c0fea566aab/src/core/config.js",
                "note": "来自上游默认配置，不是科学最佳值；仅填入时间范围、日预算和免打扰，保存后才应用。概率、未回复减扰及其他能力保持你的选择。"}},
            "privacy_summary": "只对主人私聊生效。活动感知仅含前台应用名与闲置时长；不记录按键、窗口标题或屏幕。启用后这些概况可用于模型回复。",
            "proactive_summary": "开启后由后台在主动机会到来时自主选话题，无需设置 cron。机会须通过概率、未回应减扰、预算与免打扰判断，不承诺必发；使用当前模型可能产生 API 用量。",
            "style_summary": "可选傲娇甜口与自然中文表达，减少固定欢迎词；保留现有人格和工具，不要求或展示隐藏思维链。"}


def _runtime_status(config: dict) -> dict:
    """Read a bounded public heartbeat; never return private state or error repr."""
    options = load(config)
    code = "not_running" if options["proactive_enabled"] else "disabled"
    result = {"code": code, "message": _policy.RUNTIME_MESSAGES[code], "worker_running": False,
              "heartbeat_at": None, "next_trigger_at": None, "session_known": False,
              "last_result": None, "last_result_at": None, "last_success_at": None,
              "daily_count": 0, "daily_limit": options["daily_limit"],
              "schedule_mode": options["schedule_mode"], "timezone": options["timezone"],
              "next_window_start": None, "next_window_end": None,
              "unanswered_count": 0, "effective_probability": options["trigger_probability"],
              "paused_until_reply": False, "last_opportunity_at": None, "last_opportunity_result": None,
              "last_trigger_source": None, "initiated_count": 0, "last_receipt": None,
              "usage": {"scope": "proactive", "date": _policy.schedule_now(options).date().isoformat(),
                        "input_tokens": 0, "output_tokens": 0, "reported_calls": 0, "unreported_calls": 0, "available": False}}
    path = ASTRBOT / "data" / "plugin_data" / PLUGIN_NAME / "runtime.json"
    try:
        # Reject redirected/reparse files and directories, not just a final symlink.
        for candidate in (path, path.parent, path.parent.parent):
            if candidate.is_symlink() or (getattr(candidate.stat(), "st_file_attributes", 0) & 0x400):
                return result
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 16 * 1024 or info.st_nlink != 1:
            return result
        with path.open("rb") as stream:
            raw = stream.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            return result
        candidate = json.loads(raw)
        expected = {**options, "owner_qq": str(config.get("owner_qq") or ""),
                    "bot_qq": str(config.get("bot_qq") or "")}
        if (not isinstance(candidate, dict) or type(candidate.get("version")) is not int or candidate.get("version") != 1
                or candidate.get("signature") != _policy.runtime_signature(expected)):
            return result
        heartbeat = candidate.get("heartbeat_at")
        if type(heartbeat) not in (int, float) or not math.isfinite(heartbeat) or not -30 <= time.time() - heartbeat <= 180:
            return result
        for key in ("heartbeat_at", "next_trigger_at", "last_result_at", "last_success_at",
                    "next_window_start", "next_window_end", "last_opportunity_at"):
            value = candidate.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 4102444800):
                return result
            result[key] = value
        if type(candidate.get("worker_running")) is not bool or type(candidate.get("session_known")) is not bool:
            return result
        status_code = candidate.get("code")
        if status_code not in _policy.RUNTIME_MESSAGES:
            return result
        result.update(code=status_code, message=_policy.RUNTIME_MESSAGES[status_code],
                      worker_running=candidate["worker_running"], session_known=candidate["session_known"])
        count = candidate.get("daily_count")
        if type(count) is int and 0 <= count <= 20:
            result["daily_count"] = count
        if status_code == "daily_limit" and not options["daily_limit"]:
            result["message"] = "每日生成预算设为0，主动生成已停用。"
        for key in ("unanswered_count", "initiated_count"):
            value = candidate.get(key)
            if type(value) is int and 0 <= value <= 1000000000:
                result[key] = value
        probability = candidate.get("effective_probability")
        if type(probability) in (int, float) and math.isfinite(probability) and 0 <= probability <= 100:
            result["effective_probability"] = probability
        if type(candidate.get("paused_until_reply")) is bool:
            result["paused_until_reply"] = candidate["paused_until_reply"]
        if candidate.get("last_trigger_source") == "background_idle":
            result["last_trigger_source"] = "background_idle"
        if candidate.get("last_opportunity_result") in {*_policy.RUNTIME_MESSAGES, "eligible"}:
            result["last_opportunity_result"] = candidate["last_opportunity_result"]
        if candidate.get("last_receipt") in {"not_sent", "adapter_accepted", "delivery_uncertain"}:
            result["last_receipt"] = candidate["last_receipt"]
        usage = candidate.get("usage")
        if (isinstance(usage, dict) and usage.get("scope") == "proactive"
                and usage.get("date") == result["usage"]["date"]
                and all(type(usage.get(key)) is int and 0 <= usage[key] <= 1_000_000_000_000
                        for key in ("input_tokens", "output_tokens", "reported_calls", "unreported_calls"))):
            result["usage"] = {key:usage[key] for key in ("scope", "date", "input_tokens", "output_tokens", "reported_calls", "unreported_calls")}
            result["usage"]["available"] = True
        pipeline = candidate.get("reply_pipeline")
        if isinstance(pipeline, dict):
            result["reply_pipeline"] = {key: value for key, value in pipeline.items()
                if key in {"history", "memory", "sticker", "sent_bubbles", "attempted", "accepted", "cancelled"}
                and (isinstance(value, bool) or type(value) is int and 0 <= value <= 4
                     or isinstance(value, str) and value in {"saved", "unsupported", "conversation_changed", "unavailable",
                        "durably_staged", "not_bound", "not_enabled", "not_sent", "none", "adapter_accepted", "receipt_unavailable"})}
        last_result = candidate.get("last_result")
        if last_result in _policy.RUNTIME_MESSAGES:
            result["last_result"] = last_result
        if not result["worker_running"] and options["proactive_enabled"] and status_code != "stopped":
            result.update(code="not_running", message=_policy.RUNTIME_MESSAGES["not_running"])
        return result
    except (OSError, ValueError, TypeError):
        return result
