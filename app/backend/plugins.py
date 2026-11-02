"""Bundled AstrBot add-ons, safe source synchronization and inventory.

Only files explicitly listed in the shipped manifest are copied. Installed
configuration, memory, logs and other files are never removed or bundled.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

from backend.paths import ASSETS, ASTRBOT

PLUGIN_ROOT = ASTRBOT / "data" / "plugins"
BUNDLE_ROOT = ASSETS / "plugins"
LOVE_HOUSE = "astrbot_plugin_love_house"


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


def _row(path: Path, *, plugin_id: str, bundled: bool, installed: bool) -> dict:
    meta = _metadata(path)
    return {
        "id": plugin_id,
        "name": meta.get("display_name") or meta.get("name") or plugin_id,
        "version": meta.get("version") or "",
        "desc": meta.get("desc") or "",
        "author": meta.get("author") or "",
        # Host module availability is separate from its permission/configuration.
        "enabled": installed and not path.name.endswith(".disabled"),
        "path": str(path) if installed else "",
        "lovehouse": plugin_id.removesuffix(".disabled") == LOVE_HOUSE,
        "bundled": bundled,
        "installed": installed,
        "pending": bundled and not installed,
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
    for path in sorted(PLUGIN_ROOT.iterdir()) if PLUGIN_ROOT.exists() else ():
        if not path.is_dir() or path.name.startswith("."):
            continue
        original_id = path.name.removesuffix(".disabled")
        seen.add(original_id)
        result.append(_row(path, plugin_id=path.name, bundled=original_id in bundled, installed=True))
    result.extend(row for plugin_id, row in bundled.items() if plugin_id not in seen)
    return result


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
    return disabled if not target.exists() and disabled.exists() else target


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
        prepared.append((entry["id"], contents))

    backup_root = ASTRBOT / "data" / "plugin_backups" / (
        f"bundled-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    )
    result = {"ok": True, "installed": [], "updated": [], "unchanged": [], "backup": ""}
    for plugin_id, contents in prepared:
        target = plugin_path(plugin_id)
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise RuntimeError("不能更新非普通目录中的插件：" + plugin_id)
        if target.resolve().parent != PLUGIN_ROOT.resolve():
            raise RuntimeError("插件目录超出运行目录")
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
