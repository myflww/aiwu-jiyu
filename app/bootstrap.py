"""Read the first-run install directory before backend paths are imported."""

from __future__ import annotations

import json
import os
from pathlib import Path


def bootstrap_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "Dafeiyu" / "bootstrap.json"


def apply() -> None:
    if os.environ.get("DAFEIYU_HOME", "").strip():
        return
    path = bootstrap_file()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        install_dir = str(data.get("install_dir") or "").strip()
    except Exception:
        return
    if install_dir:
        os.environ["DAFEIYU_HOME"] = install_dir
