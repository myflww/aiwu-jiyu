"""Verified public sticker inventory and separately managed send preferences."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile

from backend import config_store
from backend.paths import ASSETS, ASTRBOT

PLUGIN_ID = "astrbot_plugin_dafeiyu_stickers"
PACK_DIR = ASSETS / "plugins" / PLUGIN_ID / "pack"
IDS = ("happy", "sad", "angry", "shy", "sleepy", "love")
DEFAULT_SETTINGS = {"auto_match_enabled": True, "cooldown_seconds": 30, "pack_selection": "all",
                    "auto_probability": 35, "scenario_policy": "balanced", "custom_enabled": True}


def _normalize(value: dict | None) -> dict:
    incoming = value if isinstance(value, dict) else {}
    seconds = incoming.get("cooldown_seconds", 30)
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        seconds = 30
    selection = incoming.get("pack_selection", "all")
    probability = incoming.get("auto_probability", 35)
    if isinstance(probability, bool) or not isinstance(probability, int):
        probability = 35
    policy = incoming.get("scenario_policy", "balanced")
    return {"auto_match_enabled": incoming.get("auto_match_enabled", True) is True,
            "cooldown_seconds": max(10, min(300, seconds)),
            "pack_selection": selection if isinstance(selection, str) and selection in {"all", "original", "community", "custom"} else "all",
            "auto_probability": max(0, min(100, probability)),
            "scenario_policy": policy if isinstance(policy, str) and policy in {"balanced", "playful", "manual"} else "balanced",
            "custom_enabled": incoming.get("custom_enabled", True) is True}


def load_settings() -> dict:
    return _effective_settings(config_store.load(), _plugin_config())


def _plugin_config() -> dict:
    path = ASTRBOT / "data/config" / (PLUGIN_ID + "_config.json")
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        raise ValueError("贴纸配置不能写入链接文件")
    if not path.is_file():
        return {}
    current = json.loads(path.read_text("utf-8-sig"))
    if not isinstance(current, dict):
        raise ValueError("贴纸配置格式无效")
    return current


def _effective_settings(config: dict, current: dict) -> dict:
    # Desktop settings win only where explicitly present. Older desktop
    # profiles have stickers=null; restarting must preserve AstrBot WebUI edits.
    desktop = config.get("stickers")
    existing = {key: value for key, value in current.items() if key in DEFAULT_SETTINGS}
    explicit = {key: value for key, value in desktop.items() if key in DEFAULT_SETTINGS} if isinstance(desktop, dict) else {}
    return _normalize({**existing, **explicit})


@lru_cache(maxsize=1)
def _catalog_module():
    # Shared validation code belongs to this shipped plugin; it imports no
    # AstrBot/runtime modules and accepts no user-supplied source path.
    path = ASSETS / "plugins" / PLUGIN_ID / "catalog.py"
    spec = importlib.util.spec_from_file_location("_aiwu_public_sticker_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _library_module():
    path = ASSETS / "plugins" / PLUGIN_ID / "library_state.py"
    spec = importlib.util.spec_from_file_location("_aiwu_sticker_diagnostics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _pack() -> dict:
    data = _catalog_module().load_catalog(PACK_DIR)
    packs = {pack["id"]: pack for pack in data["packs"]}
    rows = []
    for item in data["stickers"]:
        group = item.get("pack_id", "original")
        relative = ("community/submissions/" + item["submission"] + "/" + item["file"] if group == "contributed"
                    else item["file"] if group == "community" else "pack/" + item["file"])
        url = f"/assets/plugins/{PLUGIN_ID}/{relative}"
        rows.append({key: item[key] for key in ("id", "label", "emotion", "width", "height", "size")}
                    | {"url": url, "path": url, "format": item.get("format", "PNG"), "pack_id": group,
                       "aliases": item.get("aliases", []), "attribution": item.get("attribution") or packs[group]["attribution"],
                       "description": item.get("description") or item.get("caption", ""),
                       "use_when": item.get("use_when") or item.get("use", ""), "avoid_when": item.get("avoid_when", ""),
                       "source_url": item.get("source_url") or packs[group].get("source_url", "")})
    return {"pack_name": "大肥鱼表情素材库", "count": len(rows), "plugin_id": PLUGIN_ID,
            "stickers": rows, "packs": data["packs"], "license": "参见各素材包许可与署名",
            "attribution": "原创素材与社区素材分别保留署名", "version": "1.3.0"}


def status(*, settings: dict | None = None, custom_items: list | None = None) -> dict:
    # Return detached JSON values so callers cannot change the cached inventory.
    # The preview path deliberately does not load an installed profile.
    result = json.loads(json.dumps({**_pack(), "settings": _normalize(settings)}, ensure_ascii=False))
    rows = []
    for item in custom_items or []:
        if not isinstance(item, dict) or not re.fullmatch(r"custom-[a-f0-9]{24}", str(item.get("id", ""))):
            continue
        url = "/api/stickers/custom/" + item["id"] + "/image"
        row = {key: item.get(key) for key in ("id", "title", "label", "emotion", "tags", "aliases", "description",
               "use_when", "avoid_when", "attribution", "enabled", "format", "width", "height", "frames", "size")}
        rows.append({**row, "pack_id": "custom", "url": url, "path": url})
    result["stickers"].extend(rows)
    result["count"] += len(rows)
    result["packs"].append({"id": "custom", "name": "我的表情", "count": len(rows),
                           "attribution": "本机个人素材", "license": "由用户管理；未默认授予公开再分发许可"})
    # Preview calls omit custom_items and must remain completely profile-free.
    if custom_items is not None:
        try:
            result["runtime_diagnostics"] = _library_module().LibraryState(ASTRBOT / "data/plugin_data" / PLUGIN_ID).diagnostics()
        except Exception:
            result["runtime_diagnostics"] = {"available": False, "last_at": None, "last_reason": None,
                "last_message": "配图诊断记录暂不可读", "counts": {}, "note": "不影响现有图库浏览。"}
        from backend import sticker_materials
        try:
            extra = sticker_materials.rows()
        except (OSError, ValueError):
            extra = []
            result["material_warning"] = "社区更新素材暂时无法读取，随附和个人图库仍可使用；可重新检查素材更新。"
        if extra:
            merged = {row["id"]: row for row in result["stickers"]}
            merged.update({row["id"]: row for row in extra})
            result["stickers"] = list(merged.values())
            result["count"] = len(merged)
            result["material_version"] = sticker_materials.current()["version"]
    return result


def sync_config(config: dict) -> bool:
    """Sync only send preferences; never activate the host plugin."""
    directory = ASTRBOT / "data/config"
    path = directory / (PLUGIN_ID + "_config.json")
    current = _plugin_config()
    settings = _effective_settings(config, current)
    target = {**current, **settings}
    target.setdefault("enabled", True)
    if current == target:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".stickers-", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write((json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def configure(body: dict) -> dict:
    if not isinstance(body, dict) or set(body) - set(DEFAULT_SETTINGS):
        raise ValueError("贴纸设置字段无效")
    if any(key in body and not isinstance(body[key], bool) for key in ("auto_match_enabled", "custom_enabled")):
        raise ValueError("自动匹配开关必须是布尔值")
    if "cooldown_seconds" in body and (isinstance(body["cooldown_seconds"], bool)
            or not isinstance(body["cooldown_seconds"], int) or not 10 <= body["cooldown_seconds"] <= 300):
        raise ValueError("贴纸间隔必须是10到300秒的整数")
    if "pack_selection" in body and (not isinstance(body["pack_selection"], str)
            or body["pack_selection"] not in {"all", "original", "community", "custom"}):
        raise ValueError("请选择全部、原创、社区或自定义图库")
    if "auto_probability" in body and (isinstance(body["auto_probability"], bool)
            or not isinstance(body["auto_probability"], int) or not 0 <= body["auto_probability"] <= 100):
        raise ValueError("自动配图概率必须是0至100的整数")
    if "scenario_policy" in body and (not isinstance(body["scenario_policy"], str) or body["scenario_policy"] not in {"balanced", "playful", "manual"}):
        raise ValueError("请选择自然陪伴、活泼互动或仅应请求的场景规则")
    config = config_store.load()
    settings = {**_effective_settings(config, _plugin_config()), **body}
    config["stickers"] = settings
    # Preserve other plugin preferences and its own enabled=false setting.
    sync_config(config)
    config_store.save(config)
    return status(settings=settings)
