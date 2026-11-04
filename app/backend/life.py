"""Desktop controls for the preinstalled owner-only life capabilities.

Status/configuration never sends a search, logs in to QQ or changes an account.
All remote writes require a stored exact preview and explicit one-use consent.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
import importlib
import importlib.util
from html.parser import HTMLParser  # Keep dynamic resource-module dependencies visible to PyInstaller.
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile

from backend import config_store, napcat_api, plugins
from backend.paths import ASSETS, ASTRBOT

PLUGIN_ID = "astrbot_plugin_dafeiyu_life"
TOOLS = {"search_enabled": ["dafeiyu_web_search"],
         "profile_enabled": ["dafeiyu_read_qq_profile", "dafeiyu_prepare_nickname", "dafeiyu_prepare_signature", "dafeiyu_prepare_avatar", "dafeiyu_prepare_status"],
         "qzone_enabled": ["dafeiyu_prepare_qzone"]}


@lru_cache(maxsize=1)
def _shared():
    root = ASSETS / "plugins" / PLUGIN_ID
    name = "_aiwu_life_shared"
    spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return {key: importlib.import_module("." + key, name) for key in ("settings", "search", "account", "state")}


def _plugin_config():
    path = ASTRBOT / "data/config" / (PLUGIN_ID + "_config.json")
    for candidate in (path, *list(path.parents)[:5]):
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("生活能力配置不可使用链接")
    if not path.exists() and not path.is_symlink():
        return {}
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
        raise ValueError("生活能力配置文件不可读取")
    result = json.loads(path.read_text("utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("生活能力配置格式无效")
    return result


def load(config=None):
    config = config_store.load() if config is None else config
    desktop = config.get("life")
    return _shared()["settings"].normalize({**_plugin_config(), **(desktop if isinstance(desktop, dict) else {})})


def _master_allowed():
    directory = plugins.plugin_path(PLUGIN_ID)
    try:
        return (directory.is_dir() and not directory.name.endswith(".disabled")
                and plugins._safe_plugin_directory(directory, PLUGIN_ID)
                and not any(name.startswith("data.plugins." + PLUGIN_ID + ".") for name in plugins._inactive_modules())
                and plugins._pending_switches().get(PLUGIN_ID) is not False)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, TypeError):
        return False


def _runtime_config():
    config = config_store.load()
    options = load(config)
    return {**options, "owner_qq": str(config.get("owner_qq") or ""), "bot_qq": str(config.get("bot_qq") or ""),
            "enabled": any(options[key] for key in TOOLS) and _master_allowed()}


def _accounts():
    return _shared()["account"].Accounts(ASTRBOT / "data/plugin_data" / PLUGIN_ID,
                                        ASSETS / "plugins/astrbot_plugin_dafeiyu_stickers/pack", _runtime_config)


def authorized_tools(options=None):
    options = load() if options is None else _shared()["settings"].normalize(options)
    return [name for flag, names in TOOLS.items() if options[flag] for name in names]


def status():
    config = config_store.load()
    options, shared = load(config), _shared()
    owner, bot = str(config.get("owner_qq") or ""), str(config.get("bot_qq") or "")
    available = _master_allowed()
    try:
        shared["settings"].identity(config)
        identity_ready = True
    except ValueError:
        identity_ready = False
    recent = []
    if identity_ready:
        try:
            recent = _accounts().drafts.recent(owner, bot)
        except (OSError, ValueError, sqlite3.Error):
            pass
    return {"ok": True, "settings": options, "owner_configured": bool(owner), "bot_configured": bool(bot),
            "plugin_installed": plugins.plugin_path(PLUGIN_ID).is_dir(), "plugin_enabled": available,
            "capabilities": {
                "search": {"supported": True, "available": available and identity_ready and options["search_enabled"] and (options["search_provider"] == "bing_rss" or bool(options["searxng_url"])),
                           "message": "Bing RSS 无需 API Key，需联网；公开订阅格式可能改变，失败会如实报告。搜索只发指定关键词。"},
                "profile": {"supported": True, "available": available and identity_ready and options["profile_enabled"], "message": "只修改机器人小号；先核对前后内容，再由主人确认。"},
                "avatar": {"supported": True, "available": available and identity_ready and options["profile_enabled"], "message": "只能选择六张已校验的随附大肥鱼图片，不读取任意本机文件或下载任意图片。"},
                "signature": {"supported": True, "available": available and identity_ready and options["profile_enabled"]},
                "status": {"supported": True, "available": available and identity_ready and options["profile_enabled"], "runtime_checked": False,
                           "message": "机器人小号的自定义在线状态：固定基础图标与一句文案，会切为在线；不是个人兴趣标签。先预览并由主人确认。"},
                "tags": {"supported": False, "available": False, "message": "当前未找到已核验的个人资料标签修改接口，请在 QQ 官方客户端手动管理。"},
                "qzone": {"supported": True, "available": available and identity_ready and options["qzone_enabled"], "runtime_checked": False,
                          "message": "QQ 空间纯文字说说；默认仅自己可见，准备和执行时核对 NapCat 实际版本与登录账号，不是微信朋友圈。", "official_url": shared["settings"].QZONE_URL}},
            "avatars": [{"id": name, "label": shared["settings"].LABELS[name], "url": "/assets/plugins/astrbot_plugin_dafeiyu_stickers/pack/" + name + ".png"} for name in shared["settings"].IDS],
            "icons": [{"id": name, **row} for name, row in shared["settings"].STATUS_ICONS.items()],
            "recent": recent, "docs_url": shared["settings"].DOCS_URL}


def sync_config(config):
    options = load(config)
    path = ASTRBOT / "data/config" / (PLUGIN_ID + "_config.json")
    current = _plugin_config()
    target = {**current, **options, "enabled": any(options[key] for key in TOOLS),
              "owner_qq": str(config.get("owner_qq") or ""), "bot_qq": str(config.get("bot_qq") or "")}
    if current == target:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".life-", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write((json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def configure(body):
    values = _shared()["settings"].validate(body)
    config = config_store.load()
    target = {**load(config), **values}
    if target["search_enabled"] and target["search_provider"] == "searxng" and not target["searxng_url"]:
        raise ValueError("启用 SearXNG 前请填写启用 JSON 格式的公开 HTTPS 搜索地址")
    config["life"] = target
    config_store.save(config)
    sync_config(config)
    return status()


async def _call(api, payload):
    # Existing loopback WebUI authentication is reused without exposing it.
    def execute():
        if api == "send_qzone_msg" or api.startswith("set_"):
            config = _runtime_config()
            key = "qzone_enabled" if api == "send_qzone_msg" else "profile_enabled"
            if not config["enabled"] or not config[key]:
                raise _shared()["account"].BeforeDispatchBlocked("发送请求前授权已关闭，操作停止")
        value = napcat_api._call("/Debug/call/", {"action": api, "params": payload})
        if not isinstance(value, dict) or type(value.get("code")) is not int or value["code"] != 0:
            raise _shared()["account"].Rejected("NapCat WebUI 未接受操作，没有报告成功。")
        result = value.get("data")
        if not isinstance(result, dict) or type(result.get("retcode")) is not int or result["retcode"] != 0 or result.get("status") != "ok":
            raise _shared()["account"].failure(result.get("retcode") if isinstance(result, dict) else None,
                                               result.get("message", result.get("wording", "")) if isinstance(result, dict) else "")
        return result.get("data")
    return await asyncio.to_thread(execute)


def search(body):
    if not isinstance(body, dict) or set(body) != {"query"}:
        raise ValueError("只接收明确的搜索关键词")
    config = _runtime_config()
    owner, bot = _shared()["settings"].identity(config)
    if not config["enabled"]:
        raise ValueError("生活能力插件尚未启用，请先开启插件与网页搜索开关")
    _accounts().drafts.reserve_search(owner, bot)
    return _shared()["search"].perform(body["query"], config)


def profile():
    return asyncio.run(_accounts().profile(_call))


def prepare(body):
    try:
        return asyncio.run(_accounts().prepare(body, _call))
    except _shared()["account"].Unavailable as error:
        return {"ok": False, "status": "unavailable", "message": str(error),
                "official_url": _shared()["settings"].QZONE_URL if body.get("action") == "qzone" else "https://im.qq.com/"}


def confirm(body):
    return asyncio.run(_accounts().confirm(body, _call))


def cancel(body):
    if not isinstance(body, dict) or set(body) != {"draft_id"}:
        raise ValueError("请选择要取消的待确认准备")
    owner, bot = _shared()["settings"].identity(config_store.load())
    return _accounts().drafts.cancel(owner, bot, body["draft_id"])
