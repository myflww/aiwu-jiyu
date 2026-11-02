"""Application config and AstrBot cmd_config.json synchronization."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend import dpapi
from backend.paths import APP_HOME, ASTRBOT, CONFIG_FILE, NAPCAT, RUNTIME

PERSONA_ID = "大肥鱼"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_API_BASE = "https://api.deepseek.com/v1"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "setup_complete": False,
    "install_dir": str(APP_HOME),
    "auto_login": True,
    "pc_access": {
        "enabled": False,
        "workspace": str(APP_HOME / "workspace"),
        "timeout_s": 60,
        "private_only": True,
    },
    "owner_qq": "",
    "bot_qq": "",
    "bot_nickname": "",
    "model": {
        "provider": "deepseek",
        "api_base": DEFAULT_API_BASE,
        "model": DEFAULT_MODEL,
        "api_key_enc": "",
    },
    "whitelist": {"enable": True, "private": [], "groups": []},
    "memory": {"adapter": "lovehouse", "enabled": False, "endpoint": ""},
    "ui": {"onboarded": False},
}


def _merge(base: dict, incoming: dict) -> dict:
    result = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        data = {}
    return _merge(DEFAULT_CONFIG, data if isinstance(data, dict) else {})


def save(config: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def api_key(config: dict[str, Any] | None = None) -> str:
    cfg = config or load()
    return dpapi.unprotect((cfg.get("model") or {}).get("api_key_enc", ""))


def set_api_key(config: dict[str, Any], key: str) -> dict[str, Any]:
    config.setdefault("model", {})["api_key_enc"] = dpapi.protect(key.strip())
    return config


def mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * max(4, len(value) - 8) + value[-4:]


def is_installed() -> bool:
    return (
        (ASTRBOT / "venv" / "Scripts" / "python.exe").exists()
        and (ASTRBOT / "run_astrbot.py").exists()
        and (NAPCAT / "napcat.bat").exists()
    )


def detect_bot_qq() -> str:
    config_dir = NAPCAT / "napcat" / "config"
    if not config_dir.exists():
        return ""
    candidates: list[str] = []
    for path in config_dir.glob("onebot11_*.json"):
        match = re.search(r"onebot11_(\d+)\.json$", path.name)
        if match:
            candidates.append(match.group(1))
    if not candidates:
        for path in config_dir.glob("napcat_*.json"):
            match = re.search(r"napcat_(\d+)\.json$", path.name)
            if match:
                candidates.append(match.group(1))
    return candidates[0] if candidates else ""


def parse_whitelist(entries: list[Any]) -> tuple[list[str], list[str]]:
    private: list[str] = []
    groups: list[str] = []
    for raw in entries:
        text = str(raw).strip()
        if not text:
            continue
        match = re.match(r"^qq:FriendMessage:(\d+)$", text)
        if match:
            if match.group(1) not in private:
                private.append(match.group(1))
            continue
        match = re.match(r"^qq:GroupMessage:(\d+)$", text)
        if match:
            if match.group(1) not in groups:
                groups.append(match.group(1))
            continue
        if text.isdigit():
            if text not in groups:
                groups.append(text)
    return private, groups


def adopt_from_astrbot(config: dict[str, Any]) -> dict[str, Any]:
    """Prefill app config from an existing AstrBot/NapCat runtime."""
    result = json.loads(json.dumps(config))
    bot_qq = detect_bot_qq()
    if bot_qq:
        result["bot_qq"] = bot_qq

    cmd_path = ASTRBOT / "data" / "cmd_config.json"
    if not cmd_path.exists():
        return result
    try:
        data = json.loads(cmd_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return result

    providers = data.get("provider") or []
    for provider in providers:
        if provider.get("id") == "deepseek" or provider.get("provider") == "deepseek":
            keys = provider.get("key") or []
            if isinstance(keys, str):
                keys = [keys]
            if keys and not (result.get("model") or {}).get("api_key_enc"):
                set_api_key(result, str(keys[0]))
            if provider.get("api_base"):
                result["model"]["api_base"] = provider["api_base"]
            if provider.get("model"):
                result["model"]["model"] = provider["model"]
            break

    admins = data.get("admins_id") or []
    if admins and not result.get("owner_qq"):
        result["owner_qq"] = str(admins[0])

    platform_settings = data.get("platform_settings") or {}
    private, groups = parse_whitelist(platform_settings.get("id_whitelist") or [])
    result["whitelist"] = {
        "enable": bool(platform_settings.get("enable_id_white_list", True)),
        "private": private,
        "groups": groups,
    }
    if result.get("owner_qq") and result["owner_qq"] not in result["whitelist"]["private"]:
        result["whitelist"]["private"].append(result["owner_qq"])

    result["setup_complete"] = is_installed() and bool(result["model"].get("api_key_enc"))
    result["install_dir"] = str(RUNTIME.parent)
    return result


def sync_astrbot_config(config: dict[str, Any]) -> None:
    """Write the fields the app owns into AstrBot's cmd_config.json."""
    cmd_path = ASTRBOT / "data" / "cmd_config.json"
    cmd_path.parent.mkdir(parents=True, exist_ok=True)
    if cmd_path.exists():
        try:
            data = json.loads(cmd_path.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
    else:
        data = {}

    data.setdefault("dashboard", {})["host"] = "127.0.0.1"

    bot_qq = config.get("bot_qq") or ""
    platform_id = f"qq-{bot_qq}" if bot_qq else "qq-bot"
    data["platform"] = [
        {
            "id": platform_id,
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "127.0.0.1",
            "ws_reverse_port": 6199,
            "ws_reverse_token": "",
        }
    ]

    key = api_key(config)
    model = config.get("model") or {}
    data["provider"] = [
        {
            "id": "deepseek",
            "type": "openai_chat_completion",
            "provider": "deepseek",
            "provider_type": "chat_completion",
            "enable": True,
            "key": [key] if key else [],
            "api_base": model.get("api_base") or DEFAULT_API_BASE,
            "model": model.get("model") or DEFAULT_MODEL,
            "timeout": 120,
            "proxy": "",
            "custom_headers": {},
        }
    ]
    data["provider_sources"] = []

    runner = data.setdefault("agent_runner", {}).setdefault("config", {})
    runner.setdefault("model", {})["provider_id"] = "deepseek"
    runner.setdefault("persona", {})["persona_id"] = PERSONA_ID

    owner = config.get("owner_qq") or ""
    data["admins_id"] = [owner] if owner else []

    settings = data.setdefault("platform_settings", {})
    whitelist = config.get("whitelist") or {}
    private = [str(x) for x in whitelist.get("private") or [] if str(x).strip()]
    groups = [str(x) for x in whitelist.get("groups") or [] if str(x).strip()]
    entries = [f"qq:FriendMessage:{qq}" for qq in private] + groups
    settings["enable_id_white_list"] = bool(whitelist.get("enable", True))
    settings["id_whitelist"] = entries
    settings["id_whitelist_log"] = True
    settings["wl_ignore_admin_on_friend"] = True
    settings["wl_ignore_admin_on_group"] = True

    cmd_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig"
    )
