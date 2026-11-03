"""Verified public sticker inventory and separately managed send preferences."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import tempfile

from backend import config_store
from backend.paths import ASSETS, ASTRBOT

PLUGIN_ID = "astrbot_plugin_dafeiyu_stickers"
PACK_DIR = ASSETS / "plugins" / PLUGIN_ID / "pack"
IDS = ("happy", "sad", "angry", "shy", "sleepy", "love")
DEFAULT_SETTINGS = {"auto_match_enabled": True, "cooldown_seconds": 30, "pack_selection": "all"}


def _normalize(value: dict | None) -> dict:
    incoming = value if isinstance(value, dict) else {}
    seconds = incoming.get("cooldown_seconds", 30)
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        seconds = 30
    selection = incoming.get("pack_selection", "all")
    return {"auto_match_enabled": incoming.get("auto_match_enabled", True) is True,
            "cooldown_seconds": max(10, min(300, seconds)),
            "pack_selection": selection if isinstance(selection, str) and selection in {"all", "original", "community"} else "all"}


def load_settings() -> dict:
    return _normalize(config_store.load().get("stickers"))


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
def _pack() -> dict:
    data = _catalog_module().load_catalog(PACK_DIR)
    packs = {pack["id"]: pack for pack in data["packs"]}
    rows = []
    for item in data["stickers"]:
        group = item.get("pack_id", "original")
        relative = item["file"] if group == "community" else "pack/" + item["file"]
        url = f"/assets/plugins/{PLUGIN_ID}/{relative}"
        rows.append({key: item[key] for key in ("id", "label", "emotion", "width", "height", "size")}
                    | {"url": url, "path": url, "format": item.get("format", "PNG"), "pack_id": group,
                       "aliases": item.get("aliases", []), "attribution": packs[group]["attribution"],
                       "source_url": item.get("source_url") or packs[group].get("source_url", "")})
    return {"pack_name": "大肥鱼表情素材库", "count": len(rows), "plugin_id": PLUGIN_ID,
            "stickers": rows, "packs": data["packs"], "license": "参见各素材包许可与署名",
            "attribution": "原创素材与社区素材分别保留署名", "version": "1.1.0"}


def status(*, settings: dict | None = None) -> dict:
    # Return detached JSON values so callers cannot change the cached inventory.
    # The preview path deliberately does not load an installed profile.
    return json.loads(json.dumps({**_pack(), "settings": _normalize(settings)}, ensure_ascii=False))


def sync_config(config: dict) -> bool:
    """Sync only send preferences; never activate the host plugin."""
    settings = _normalize(config.get("stickers"))
    directory = ASTRBOT / "data/config"
    path = directory / (PLUGIN_ID + "_config.json")
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        raise ValueError("贴纸配置不能写入链接文件")
    current = {}
    if path.is_file():
        value = json.loads(path.read_text("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("贴纸配置格式无效")
        current = value
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
    if "auto_match_enabled" in body and not isinstance(body["auto_match_enabled"], bool):
        raise ValueError("自动匹配开关必须是布尔值")
    if "cooldown_seconds" in body and (isinstance(body["cooldown_seconds"], bool)
            or not isinstance(body["cooldown_seconds"], int) or not 10 <= body["cooldown_seconds"] <= 300):
        raise ValueError("贴纸间隔必须是10到300秒的整数")
    if "pack_selection" in body and (not isinstance(body["pack_selection"], str)
            or body["pack_selection"] not in {"all", "original", "community"}):
        raise ValueError("请选择全部、原创或社区图库")
    config = config_store.load()
    settings = {**_normalize(config.get("stickers")), **body}
    config["stickers"] = settings
    # Preserve other plugin preferences and its own enabled=false setting.
    sync_config(config)
    config_store.save(config)
    return status(settings=settings)
