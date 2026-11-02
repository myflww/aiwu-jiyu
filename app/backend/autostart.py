"""Per-user Windows autostart entry (HKCU Run) for the tray app."""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

from backend.paths import APP_HOME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Dafeiyu"


def _exe_command() -> tuple[Path, list[str]]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ["--tray"]
    script = Path(__file__).resolve().parents[1] / "main.py"
    return Path(sys.executable), [str(script), "--tray"]


def _launcher() -> Path:
    exe, args = _exe_command()
    vbs = APP_HOME / "start-tray.vbs"
    parts: list[str] = []
    for index, arg in enumerate([str(exe), *args]):
        if index == 0 or not arg.startswith("-"):
            parts.append(f'Chr(34) & "{arg}" & Chr(34)')
        else:
            parts.append(f'"{arg}"')
    run_expr = ' & " " & '.join(parts)
    content = (
        'Set sh = CreateObject("WScript.Shell")\n'
        f'sh.Environment("PROCESS")("DAFEIYU_HOME") = "{APP_HOME}"\n'
        f"sh.Run {run_expr}, 0, False\n"
    )
    vbs.write_text(content, encoding="utf-16")
    return vbs


def _command_line() -> str:
    vbs = _launcher()
    return f'wscript.exe //B //Nologo "{vbs}"'


def status() -> dict:
    if winreg is None:
        return {"supported": False, "enabled": False, "command": ""}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return {"supported": True, "enabled": bool(value), "command": str(value)}
    except FileNotFoundError:
        return {"supported": True, "enabled": False, "command": ""}
    except OSError:
        return {"supported": False, "enabled": False, "command": ""}


def enable() -> dict:
    if winreg is None:
        raise RuntimeError("当前系统不支持注册表自启动")
    command = _command_line()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    return status()


def disable() -> dict:
    if winreg is None:
        return status()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
    return status()
