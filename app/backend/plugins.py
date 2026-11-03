"""Bundled AstrBot add-ons, safe source synchronization and inventory.

Only files explicitly listed in the shipped manifest are copied. Installed
configuration, memory, logs and other files are never removed or bundled.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend import astrbot_api, processes
from backend.paths import ASSETS, ASTRBOT

PLUGIN_ROOT = ASTRBOT / "data" / "plugins"
BUNDLE_ROOT = ASSETS / "plugins"
LOVE_HOUSE = "astrbot_plugin_love_house"
_toggle_lock = threading.RLock()


def _disabled_root() -> Path:
    # AstrBot scans immediate children for main.py but does not recurse. A
    # suffix such as .disabled alone is still discoverable by its real loader.
    return PLUGIN_ROOT / ".aiwu-disabled"


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _safe_plugin_directory(path: Path, plugin_id: str) -> bool:
    """Only exact active/legacy children or our anchored nested storage."""
    root, hidden = PLUGIN_ROOT, _disabled_root()
    if _linked(path) or (path.exists() and not path.is_dir()):
        return False
    if path.parent == root and path.name in (plugin_id, plugin_id + ".disabled"):
        return path.resolve().parent == root.resolve()
    if path.parent != hidden or path.name != plugin_id + ".disabled":
        return False
    if _linked(hidden) or (hidden.exists() and not hidden.is_dir()):
        return False
    return hidden.resolve().parent == root.resolve() and path.resolve().parent == hidden.resolve()


def _inactive_modules() -> set[str]:
    """Read only AstrBot's native switch preference, never chat/account data."""
    database = ASTRBOT / "data" / "data_v4.db"
    if not database.exists():
        return set()
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
        try:
            row = connection.execute(
                "SELECT value FROM preferences WHERE scope = ? AND scope_id = ? AND key = ?",
                ("global", "global", "inactivated_plugins"),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return set()  # The first bot startup is still initializing.
            raise
    values = json.loads(row[0]).get("val", []) if row else []
    if not isinstance(values, list):
        raise RuntimeError("AstrBot 插件开关数据无效")
    return set(values)


def _metadata(path: Path) -> dict:
    data: dict[str, str] = {}
    try:
        text = (path / "metadata.yaml").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return data
    for line in text.splitlines():
        match = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line.strip())
        if match:
            data[match.group(1)] = match.group(2).strip().strip("'\"")
    return data


def _manifest() -> list[dict]:
    value = json.loads((BUNDLE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or not isinstance(value.get("plugins"), list):
        raise RuntimeError("随附插件清单不可用")
    entries = value["plugins"]
    names = [entry.get("id", "") for entry in entries]
    if len(names) != len(set(names)) or not all(
        isinstance(name, str) and re.fullmatch(r"astrbot_plugin_[A-Za-z0-9_]+", name)
        for name in names
    ):
        raise RuntimeError("随附插件清单的名称无效")
    return entries


def _member(directory: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise RuntimeError("随附插件清单包含无效路径")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise RuntimeError("随附插件清单包含无效路径")
    current = directory
    if current.is_symlink():
        raise RuntimeError("插件清单不能通过符号链接访问文件")
    for part in parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError("插件清单不能通过符号链接访问文件")
    target = directory.joinpath(*parts).resolve()
    if directory.resolve() not in target.parents:
        raise RuntimeError("随附插件路径超出插件目录")
    return target


def _row(path: Path, *, plugin_id: str, bundled: bool, installed: bool,
         inactive: set[str] | None = None) -> dict:
    meta = _metadata(path)
    return {
        "id": plugin_id,
        "name": meta.get("display_name") or meta.get("name") or plugin_id,
        "version": meta.get("version") or "",
        "desc": meta.get("desc") or "",
        "author": meta.get("author") or "",
        # Host module availability is separate from its permission/configuration.
        "enabled": installed and not path.name.endswith(".disabled") and not any(
            module.startswith(f"data.plugins.{plugin_id}.") for module in (inactive or set())),
        "path": str(path) if installed else "",
        "lovehouse": plugin_id.removesuffix(".disabled") == LOVE_HOUSE,
        "bundled": bundled,
        "installed": installed,
        "pending": bundled and not installed,
        "runtime_enabled": installed and not path.name.endswith(".disabled") and not any(
            module.startswith(f"data.plugins.{plugin_id}.") for module in (inactive or set())),
        "pending_change": False,
    }


def list_bundled_plugins() -> list[dict]:
    """Read package metadata only; safe for first-run and isolated previews."""
    return [
        _row(BUNDLE_ROOT / entry["id"], plugin_id=entry["id"], bundled=True, installed=False)
        for entry in _manifest()
    ]


def list_plugins() -> list[dict]:
    bundled = {entry["id"]: entry for entry in list_bundled_plugins()}
    result = []
    seen: set[str] = set()
    inactive = _inactive_modules()
    pending = _pending_entries()

    def append(path: Path, plugin_id: str) -> None:
        seen.add(plugin_id)
        row = _row(path, plugin_id=plugin_id, bundled=plugin_id in bundled,
                   installed=True, inactive=inactive)
        if plugin_id in pending:
            entry = pending[plugin_id]
            row["enabled"] = entry["enabled"]
            if isinstance(entry.get("runtime_enabled"), bool):
                row["runtime_enabled"] = entry["runtime_enabled"]
            row["pending_change"] = True
            row["pending"] = True
        result.append(row)

    for path in sorted(PLUGIN_ROOT.iterdir()) if PLUGIN_ROOT.exists() else ():
        if not path.is_dir() or path.name.startswith((".", "__")):
            continue
        original_id = path.name.removesuffix(".disabled")
        if original_id in seen:
            continue
        if not _safe_plugin_directory(path, original_id):
            continue
        if not ((path / "main.py").is_file() or (path / (original_id + ".py")).is_file()):
            continue
        append(path, original_id)
    # Hidden optional packages are installed and manageable, but intentionally
    # absent from AstrBot's immediate-child discovery. Do not scan arbitrary
    # hidden content or follow a link/junction placed at the storage location.
    for plugin_id in bundled:
        if plugin_id in seen:
            continue
        path = plugin_path(plugin_id)
        if not _safe_plugin_directory(path, plugin_id) or not path.is_dir():
            continue
        if (path / "main.py").is_file() or (path / (plugin_id + ".py")).is_file():
            append(path, plugin_id)
    result.extend(row for plugin_id, row in bundled.items() if plugin_id not in seen)
    return result


def _pending_entries() -> dict[str, dict]:
    path = ASTRBOT / "data" / "dafeiyu-plugin-switches.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("待应用插件开关数据无效")
    entries = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"[\w-]+", key):
            raise RuntimeError("待应用插件开关数据无效")
        if isinstance(item, bool):
            entries[key] = {"enabled": item, "attempted": True}  # legacy queue, unknown outcome
        elif isinstance(item, dict) and isinstance(item.get("enabled"), bool):
            entries[key] = item
        else:
            raise RuntimeError("待应用插件开关数据无效")
    return entries


def _pending_switches() -> dict[str, bool]:
    return {key: entry["enabled"] for key, entry in _pending_entries().items()}


def _save_pending(plugin_id: str, enabled: bool | None, *, runtime_enabled: bool | None = None,
                  attempted: bool = True, baseline_enabled: bool | None = None) -> None:
    values = _pending_entries()
    if enabled is None:
        values.pop(plugin_id, None)
    else:
        values[plugin_id] = {"enabled": enabled, "runtime_enabled": runtime_enabled,
                             "attempted": attempted, "baseline_enabled": baseline_enabled}
    path = ASTRBOT / "data" / "dafeiyu-plugin-switches.json"
    _write_atomic(path, (json.dumps(values, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def apply_pending() -> None:
    """Apply queued switches before the managed AstrBot process is spawned."""
    with _toggle_lock:
        pending = _pending_switches()
        if not pending:
            return
        if processes.port_up(6185):
            raise RuntimeError("聊天服务仍在退出，请稍后重试")
        if not (ASTRBOT / "data" / "data_v4.db").is_file():
            return  # First launch initializes the database; apply via the live API next.
        for plugin_id, enabled in pending.items():
            directory = plugin_path(plugin_id)
            if not _safe_plugin_directory(directory, plugin_id):
                raise RuntimeError("插件目录无效")
            if not directory.is_dir():
                _save_pending(plugin_id, None)
                continue
            _set_offline(plugin_id, enabled)
            if directory.name.endswith(".disabled") and enabled:
                directory.rename(PLUGIN_ROOT / plugin_id)
            _save_pending(plugin_id, None)


def apply_pending_live() -> None:
    # Read and apply under the same reentrant lock. Otherwise a startup worker
    # could replay a stale snapshot after the user has changed/cancelled it.
    with _toggle_lock:
        for plugin_id, enabled in _pending_switches().items():
            set_enabled(plugin_id, enabled)


def _runtime_plugin(token: str, plugin_id: str) -> dict | None:
    inventory = astrbot_api._request("/api/plugin/get", token=token, method="GET")
    if inventory.get("status") != "ok":
        raise RuntimeError("无法读取 AstrBot 插件状态")
    data = inventory.get("data") or []
    if not isinstance(data, list):
        raise RuntimeError("AstrBot 插件列表格式不兼容")
    return next((item for item in data if item.get("root_dir_name") == plugin_id
                 or item.get("name") == plugin_id), None)


def _set_offline(plugin_id: str, enabled: bool) -> None:
    """Change the native preference only while AstrBot is stopped.

    AstrBot caches preferences in memory, so writing its database while it is
    running would display a switch that has not actually taken effect.
    """
    database = ASTRBOT / "data" / "data_v4.db"
    if not database.is_file():
        raise ValueError("请先启动一次聊天服务，完成插件初始化")
    with closing(sqlite3.connect(database, timeout=3)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id, value FROM preferences WHERE scope = ? AND scope_id = ? AND key = ?",
            ("global", "global", "inactivated_plugins"),
        ).fetchone()
        current = json.loads(row[1]).get("val", []) if row else []
        if not isinstance(current, list):
            raise RuntimeError("AstrBot 插件开关数据无效")
        directory = plugin_path(plugin_id)
        module_name = "main" if (directory / "main.py").is_file() else plugin_id
        module = f"data.plugins.{plugin_id}.{module_name}"
        values = [value for value in current if not value.startswith(f"data.plugins.{plugin_id}.")]
        if not enabled:
            values.append(module)
        value = json.dumps({"val": values}, ensure_ascii=False)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
        if row:
            connection.execute("UPDATE preferences SET value = ?, updated_at = ? WHERE id = ?",
                               (value, now, row[0]))
        else:
            connection.execute(
                "INSERT INTO preferences (scope, scope_id, key, value, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("global", "global", "inactivated_plugins", value, now, now),
            )


def set_enabled(plugin_id: str, enabled: bool) -> dict:
    """Use AstrBot's real hot switch; preserve configuration and permissions."""
    if not isinstance(plugin_id, str) or not re.fullmatch(r"[\w-]+", plugin_id):
        raise ValueError("插件 ID 无效")
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    with _toggle_lock:
        row = next((row for row in list_plugins() if row["id"] == plugin_id), None)
        if not row or not row["installed"]:
            raise ValueError("插件尚未安装")
        directory = plugin_path(plugin_id)
        if not _safe_plugin_directory(directory, plugin_id):
            raise ValueError("插件目录无效")
        online = processes.port_up(6185)
        pending = False
        applied = not online
        cancelled = False
        previous = _pending_entries().get(plugin_id)
        native_enabled = not directory.name.endswith(".disabled") and not any(
            module.startswith(f"data.plugins.{plugin_id}.") for module in _inactive_modules())
        if online:
            token = None
            attempted = False
            observed = None
            try:
                if directory.name.endswith(".disabled"):
                    raise RuntimeError("旧版插件目录需要重新加载")
                token = astrbot_api.dashboard_token()
                runtime = _runtime_plugin(token, plugin_id)
                observed = bool(runtime and runtime.get("activated"))
                if not runtime:
                    raise ValueError("插件尚未加载，请先在诊断页重启聊天服务")
                if observed != enabled or native_enabled != enabled:
                    attempted = True
                    result = astrbot_api._request(
                        "/api/plugin/on" if enabled else "/api/plugin/off",
                        {"name": runtime["name"]}, token=token,
                    )
                    if result.get("status") != "ok":
                        raise RuntimeError(result.get("message") or "AstrBot 未能切换插件")
                    current = _runtime_plugin(token, plugin_id)
                    observed = bool(current and current.get("activated"))
                    if not current or observed != enabled:
                        raise RuntimeError("插件未能完成加载，请重启对话服务后检查")
                _save_pending(plugin_id, None)
                applied = True
            except Exception:
                # The current host may predate desktop-session support. Save
                # intent separately; do not alter its cached DB or interrupt it.
                if token:
                    try:
                        current = _runtime_plugin(token, plugin_id)
                        observed = bool(current and current.get("activated"))
                    except Exception:
                        pass
                if (previous and previous.get("attempted") is False and not attempted
                        and enabled == previous.get("baseline_enabled") and native_enabled == enabled
                        and (observed is None or observed == enabled)):
                    # Cancel a purely queued edit; no native mutation was ever
                    # attempted, so this does not claim a failed reload worked.
                    _save_pending(plugin_id, None)
                    cancelled = True
                else:
                    _save_pending(plugin_id, enabled,
                                  runtime_enabled=observed if observed is not None else row["runtime_enabled"],
                                  attempted=attempted or bool(previous and previous.get("attempted")),
                                  baseline_enabled=previous.get("baseline_enabled") if previous else native_enabled)
                    pending = True
        else:
            if (ASTRBOT / "data" / "data_v4.db").is_file():
                _set_offline(plugin_id, enabled)
                if directory.name.endswith(".disabled") and enabled:
                    directory.rename(PLUGIN_ROOT / plugin_id)
                _save_pending(plugin_id, None)
            else:
                _save_pending(plugin_id, enabled)
                applied = False
                pending = True
        rows = list_plugins()
        message = "已启用" if enabled else "已停用"
        if cancelled:
            message = "已取消待应用更改"
        if pending:
            message = "已保存，重启对话后生效"
        return {"ok": True, "id": plugin_id, "enabled": enabled,
                "applied": applied, "restart_required": pending, "pending": pending,
                "runtime_enabled": next(item["runtime_enabled"] for item in rows if item["id"] == plugin_id),
                "message": message, "plugins": rows}


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".bundle-", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def plugin_path(plugin_id: str) -> Path:
    target = PLUGIN_ROOT / plugin_id
    disabled = PLUGIN_ROOT / (plugin_id + ".disabled")
    if target.exists() or _linked(target):
        return target
    if disabled.exists() or _linked(disabled):
        return disabled
    hidden = _disabled_root() / (plugin_id + ".disabled")
    # Return the invalid candidate as well if its parent is a link: mutation
    # callers must reject it, not silently install through/around that link.
    if _linked(_disabled_root()) or hidden.exists() or _linked(hidden):
        return hidden
    return target


def install_bundled(*, plugin_ids: list[str] | None = None) -> dict:
    """Install all shipped add-ons, backing up changed source files only.

    This does not grant computer permissions or register memory sources. A
    disabled plugin directory stays disabled, and files not in the manifest
    (including user-created configuration and plugin data) stay untouched.
    """
    entries = _manifest()
    selected = set(plugin_ids) if plugin_ids is not None else {entry["id"] for entry in entries}
    if selected - {entry["id"] for entry in entries}:
        raise RuntimeError("这个插件不在随附清单内")
    entries = [entry for entry in entries if entry["id"] in selected]
    prepared = []
    for entry in entries:
        source = BUNDLE_ROOT / entry["id"]
        files = entry.get("files")
        if not isinstance(files, list) or not files:
            raise RuntimeError("随附插件缺少文件清单")
        contents = []
        seen = set()
        for row in files:
            relative = row.get("path")
            if relative in seen:
                raise RuntimeError("随附插件清单包含重复文件")
            seen.add(relative)
            content = _member(source, relative).read_bytes()
            if len(content) != row.get("size") or hashlib.sha256(content).hexdigest() != row.get("sha256"):
                raise RuntimeError("随附插件文件校验失败：" + entry["id"])
            contents.append((relative, content))
        prepared.append((entry, contents))

    backup_root = ASTRBOT / "data" / "plugin_backups" / (
        f"bundled-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    result = {"ok": True, "installed": [], "updated": [], "unchanged": [], "backup": ""}
    for entry, contents in prepared:
        plugin_id = entry["id"]
        target = plugin_path(plugin_id)
        if not target.exists() and entry.get("default_enabled", True) is False:
            target = _disabled_root() / (plugin_id + ".disabled")
        if not _safe_plugin_directory(target, plugin_id):
            raise RuntimeError("不能更新非普通目录或运行目录外的插件：" + plugin_id)
        existed = target.exists()
        changed = False
        for relative, content in contents:
            destination = _member(target, relative)
            if destination.exists():
                if not destination.is_file():
                    raise RuntimeError("插件文件位置被其他目录占用")
                current = destination.read_bytes()
                if current == content:
                    continue
                _write_atomic(_member(backup_root / plugin_id, relative), current)
                result["backup"] = str(backup_root)
            _write_atomic(destination, content)
            changed = True
        if not existed:
            result["installed"].append(plugin_id)
        elif changed:
            result["updated"].append(plugin_id)
        else:
            result["unchanged"].append(plugin_id)
    result["plugins"] = list_plugins()
    return result


def install_lovehouse() -> dict:
    result = install_bundled(plugin_ids=[LOVE_HOUSE])
    result["path"] = str(plugin_path(LOVE_HOUSE))
    return result
