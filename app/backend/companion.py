"""Desktop-side preferences for the optional bundled companion plugin."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile

from backend import config_store
from backend.paths import ASTRBOT, ASSETS

PLUGIN_NAME = "astrbot_plugin_dafeiyu_companion"
_spec = importlib.util.spec_from_file_location("_aiwu_companion_policy", ASSETS / "plugins" / PLUGIN_NAME / "engine.py")
_policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_policy)
DEFAULTS = dict(_policy.DEFAULTS)


def load(config: dict | None = None) -> dict:
    config = config_store.load() if config is None else config
    incoming = config.get("companion")
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
            "privacy_summary": "只对主人私聊生效。活动感知仅含前台应用名与闲置时长；不记录按键、窗口标题或屏幕。启用后这些概况可用于模型回复。",
            "proactive_summary": "先与她私聊建立会话；主动消息使用你的当前模型，可能产生 API 用量；免打扰时段不发送。",
            "style_summary": "可选傲娇甜口与自然中文表达，减少固定欢迎词；保留现有人格和工具，不要求或展示隐藏思维链。"}
