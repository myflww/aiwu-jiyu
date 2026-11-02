"""Shared paths for the dafeiyu desktop app."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_home() -> Path:
    override = os.environ.get("DAFEIYU_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return (Path(base) / "Dafeiyu").resolve()
    return (Path.home() / "Dafeiyu").resolve()


APP_HOME = _app_home()
RUNTIME = APP_HOME / "runtime"
ASTRBOT = RUNTIME / "astrbot"
NAPCAT = RUNTIME / "napcat"
LOGS = APP_HOME / "logs"
CONFIG_FILE = APP_HOME / "config.json"
BIN_DIR = APP_HOME / "bin"
RUN_DIR = APP_HOME / "run"

ASTRBOT_PYTHON = ASTRBOT / "venv" / "Scripts" / "python.exe"
ASTRBOT_RUNNER = ASTRBOT / "run_astrbot.py"
NAPCAT_BAT = NAPCAT / "napcat.bat"

ASTRBOT_PORT = 6185
NAPCAT_PORT = 6099
ONEBOT_PORT = 6199


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


ASSETS = resource_root() / "assets"
UI_DIR = Path(__file__).resolve().parents[1] / "ui"
if getattr(sys, "frozen", False):
    UI_DIR = resource_root() / "app" / "ui"


def ensure_dirs() -> None:
    for path in (APP_HOME, RUNTIME, LOGS, BIN_DIR, RUN_DIR):
        path.mkdir(parents=True, exist_ok=True)
