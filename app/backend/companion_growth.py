"""Desktop review of scoped expression learning; never call a model or QQ."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import stat
import sqlite3

from backend import companion, companion_memory, config_store
from backend.paths import ASTRBOT, ASSETS

PLUGIN = "astrbot_plugin_dafeiyu_companion"
_module = None
_source_module = None


def _shared():
    global _module
    if _module is None:
        spec = importlib.util.spec_from_file_location("_aiwu_growth_shared", ASSETS / "plugins" / PLUGIN / "growth.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module = module
    return _module


def _journal():
    return _shared().Growth(ASTRBOT / "data" / "plugin_data" / PLUGIN)


def _safe_json(path, maximum):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("记忆来源配置不可读取")
    for parent in (path, *path.parents):
        if parent.exists() or parent.is_symlink():
            info = parent.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("记忆来源配置不能使用链接")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
        raise ValueError("记忆来源配置不可读取")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    after = path.stat()
    if len(raw) > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("记忆来源配置在读取时变化，请刷新")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("记忆来源配置格式不正确")
    return data


def _identity(config):
    owner, bot = str(config.get("owner_qq") or ""), str(config.get("bot_qq") or "")
    _shared()._identity(owner, bot)
    return owner, bot


def _binding(config):
    """Only the already registered owner's exact private source, no discovery."""
    global _source_module
    owner, bot = _identity(config)
    plugin = _safe_json(ASTRBOT / "data/config/astrbot_plugin_love_house_config.json", 128 * 1024)
    bindings = plugin.get("bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 100:
        raise ValueError("请先核对记忆小屋的主人私聊连接")
    selected = []
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("platform", "qq") != "qq":
            continue
        umos = binding.get("allowed_umos")
        if not isinstance(umos, list):
            continue
        private = [u for u in umos if isinstance(u, str) and u.split(":")[1:] == ["FriendMessage", owner]]
        if not private:
            continue
        path = Path(str(binding.get("source_config_path", "")))
        data = _safe_json(path, 65536)
        if _source_module is None:
            spec = importlib.util.spec_from_file_location("_aiwu_growth_source", ASSETS / "plugins/astrbot_plugin_love_house/source_client.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _source_module = module
        source = _source_module.validate_config(data)
        if (source["runtime_agent_id"] != binding.get("runtime_agent_id") or source["adapter"] != "astrbot"
                or "qq" not in source["platforms"] and source["platforms"] != ["*"]):
            raise ValueError("主人私聊与记忆来源身份不一致")
        library = _shared()._library(source["library_id"])
        for umo in private:
            _shared()._identity(owner, bot, umo)
            selected.append((library, umo, plugin.get("enabled") is True and source["enabled"] is True and source["revoked"] is False))
    if len(selected) != 1:
        raise ValueError("请先为主人私聊连接唯一的真实记忆库；不合并不同来源")
    return selected[0]


def _counts(entries):
    import time
    now = time.time()
    return {"feedback": sum(e["kind"] == "feedback" and e["status"] == "active" for e in entries),
            "reflection": sum(e["kind"] == "reflection" for e in entries),
            "pending": sum(e["kind"] == "reflection" and e["status"] == "pending" for e in entries),
            "approved": sum(e["kind"] == "reflection" and e["status"] == "approved" for e in entries),
            "paused": sum(e["status"] == "paused" for e in entries),
            "trial": sum(e["kind"] == "reflection" and e["status"] in {"pending", "approved"}
                         and type(e.get("trial_until")) in (int, float) and e["trial_until"] > now for e in entries)}


def status():
    config = config_store.load()
    enabled = companion.load(config).get("growth_enabled", True) is True
    entries, bound, available = [], False, False
    try:
        owner, bot = _identity(config)
        library, _, active = _binding(config)
        bound = True
        entries = _journal().entries(owner, bot, library)
        available = active and enabled and companion._plugin_enabled()
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, sqlite3.Error):
        pass
    return {"ok": True, "enabled": enabled, "available": available, "library_bound": bound, "counts": _counts(entries),
            "notice": "已连接主人私聊记忆库。实际学习还须通过本轮会话权限与证据核验。" if available else "请先连接主人私聊记忆库并启用成长学习；暂停时原手记保留。",
            "disclaimer": "成长指可核验的表达调整与待验证推测，不代表真实意识；试行计数不证明本轮实际采用，不增加隐蔽模型请求。"}


def inspect(body=None):
    if body is not None and (not isinstance(body, dict) or body):
        raise ValueError("查看成长手记不接受任意账号、库或路径")
    config = config_store.load()
    owner, bot = _identity(config)
    library, _, _ = _binding(config)
    return {"ok": True, "enabled": companion.load(config).get("growth_enabled", True) is True,
            "library_id": library, "entries": _journal().entries(owner, bot, library),
            "notice": "明确表达偏好可以直接调整语气；反思保持推测。自由文本计划仅供查看，暂停不会假装删除原始记忆。"}


def mutate(body):
    if (not isinstance(body, dict) or set(body) != {"id", "action", "expected_revision", "confirmed"}
            or body.get("confirmed") is not True):
        raise ValueError("请先查看当前记录，再明确确认本次使用状态更改")
    config = config_store.load()
    owner, bot = _identity(config)
    library, origin, active = _binding(config)
    def verify(expected_library, evidence):
        if not active or expected_library != library:
            return False
        nodes, _ = companion_memory._graph(library)
        indexed = {node["id"]: node for node in nodes}
        latest = config_store.load()
        if _identity(latest) != (owner, bot) or _binding(latest) != (library, origin, active):
            raise ValueError("账号或记忆连接在核验时已变化，请重新查看")
        return all(_shared().witness_is_current(w, indexed, library) for w in evidence)
    return _journal().change(owner, bot, library, body["id"], body["action"], body["expected_revision"],
                             confirmed=True, verify=verify)


def forget_library(library_id):
    """Called only after the existing native forget preview/consent is checked.

    The native library operation is the caller's next step. Failure here must
    stop it; this helper never reports a partial text/checkpoint cleanup as ok.
    """
    return _journal().forget_library(library_id)
