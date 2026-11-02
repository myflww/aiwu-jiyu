"""Read-only AstrBot plugin inventory for the desktop app."""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from backend.paths import ASSETS, ASTRBOT

PLUGIN_ROOT = ASTRBOT / "data" / "plugins"
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


def list_plugins() -> list[dict]:
    if not PLUGIN_ROOT.exists():
        return []
    result = []
    for path in sorted(PLUGIN_ROOT.iterdir()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        meta = _metadata(path)
        result.append(
            {
                "id": path.name,
                "name": meta.get("display_name") or meta.get("name") or path.name,
                "version": meta.get("version") or "",
                "desc": meta.get("desc") or "",
                "author": meta.get("author") or "",
                "enabled": not path.name.endswith(".disabled"),
                "path": str(path),
                "lovehouse": path.name == LOVE_HOUSE,
            }
        )
    return result


def install_lovehouse() -> dict:
    source = ASSETS / "plugins" / LOVE_HOUSE
    if not source.is_dir():
        raise RuntimeError("安装包里没有爱之屋插件")
    PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
    target = PLUGIN_ROOT / LOVE_HOUSE
    if target.exists():
        backup_root = ASTRBOT / "data" / "plugin_backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / f"{LOVE_HOUSE}.bak-{int(time.time())}"
        shutil.copytree(target, backup)
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return {"ok": True, "path": str(target), "plugins": list_plugins()}
