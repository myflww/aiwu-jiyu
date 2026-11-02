"""Start, stop and inspect the AstrBot / NapCat child processes."""

from __future__ import annotations

import re
import os
import subprocess
import threading
import time
from pathlib import Path

from backend.paths import (
    ASTRBOT,
    ASTRBOT_PORT,
    ASTRBOT_PYTHON,
    ASTRBOT_RUNNER,
    LOGS,
    NAPCAT,
    NAPCAT_BAT,
    NAPCAT_PORT,
    ONEBOT_PORT,
    RUN_DIR,
)

NO_WINDOW = 0x08000000
DETACHED = 0x00000008
NEW_GROUP = 0x00000200
ALLOWED_IMAGES = {"python.exe", "pythonw.exe", "node.exe", "cmd.exe"}


def _netstat_pid(port: int) -> int | None:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except Exception:
        return None
    pattern = re.compile(rb":" + str(port).encode() + rb"\s+.*LISTENING\s+(\d+)")
    for line in out.splitlines():
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None


def _image_name(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
        text = out.decode("mbcs", errors="ignore")
        return text.split(",", 1)[0].strip().strip('"')
    except Exception:
        return ""


def port_up(port: int) -> bool:
    return _netstat_pid(port) is not None


def status() -> dict:
    astrbot_pid = _netstat_pid(ASTRBOT_PORT)
    napcat_pid = _netstat_pid(NAPCAT_PORT)
    return {
        "astrbot": {"up": bool(astrbot_pid), "pid": astrbot_pid, "port": ASTRBOT_PORT},
        "napcat": {"up": bool(napcat_pid), "pid": napcat_pid, "port": NAPCAT_PORT},
        "onebot": {"up": port_up(ONEBOT_PORT), "port": ONEBOT_PORT},
    }


def _pid_file(name: str) -> Path:
    return RUN_DIR / f"{name}.pid"


def _write_pid(name: str, pid: int) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    _pid_file(name).write_text(str(pid), encoding="ascii")


def _read_pid(name: str) -> int | None:
    try:
        return int(_pid_file(name).read_text(encoding="ascii").strip())
    except Exception:
        return None


def _clear_pid(name: str) -> None:
    try:
        _pid_file(name).unlink()
    except Exception:
        pass


def _kill_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        check=False,
        creationflags=NO_WINDOW,
    )


def _spawn(cmd: list[str], cwd: Path, log_name: str, *, environment: dict | None = None) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    handle = open(LOGS / log_name, "ab", buffering=0)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=NEW_GROUP | NO_WINDOW,
            startupinfo=startupinfo,
            close_fds=True,
            env={**os.environ, **environment} if environment else None,
        )
        return process
    finally:
        handle.close()


def start_astrbot() -> None:
    if not ASTRBOT_PYTHON.exists() or not ASTRBOT_RUNNER.exists():
        raise RuntimeError("AstrBot 运行时尚未安装")
    if port_up(ASTRBOT_PORT):
        return
    from backend import astrbot_api, plugins

    plugins.apply_pending()
    process = _spawn([str(ASTRBOT_PYTHON), str(ASTRBOT_RUNNER), "run"], ASTRBOT, "astrbot.log",
                     environment=astrbot_api.desktop_environment())
    _write_pid("astrbot", process.pid)

    def _sync_pending():
        if wait_port(ASTRBOT_PORT, 45):
            try:
                plugins.apply_pending_live()
            except Exception:
                pass  # Keep the queued intent visible for the next attempt.

    if plugins._pending_switches():
        threading.Thread(target=_sync_pending, daemon=True).start()


def start_napcat(quick_qq: str | None = None) -> None:
    if not NAPCAT_BAT.exists():
        raise RuntimeError("NapCat 运行时尚未安装")
    if port_up(NAPCAT_PORT):
        return
    command = ["cmd", "/c", "napcat.bat"]
    if quick_qq:
        command.extend(["-q", str(quick_qq)])
    process = _spawn(command, NAPCAT, "napcat.log")
    _write_pid("napcat", process.pid)


def wait_port(port: int, timeout: float = 45.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if port_up(port):
            return True
        time.sleep(0.4)
    return False


def start_all(quick_qq: str | None = None) -> dict:
    messages: list[str] = []
    if not port_up(ASTRBOT_PORT):
        start_astrbot()
        messages.append("AstrBot 启动中")
    if not port_up(NAPCAT_PORT):
        start_napcat(quick_qq)
        messages.append("NapCat 快速登录中" if quick_qq else "NapCat 启动中")
    if not port_up(ASTRBOT_PORT) and wait_port(ASTRBOT_PORT, 45):
        messages.append("AstrBot 已就绪")
    if not port_up(NAPCAT_PORT) and wait_port(NAPCAT_PORT, 25):
        messages.append("NapCat 已就绪")
    return {"ok": port_up(ASTRBOT_PORT) and port_up(NAPCAT_PORT), "messages": messages, "status": status()}


def _kill_port(port: int) -> tuple[bool, str]:
    pid = _netstat_pid(port)
    if not pid:
        return True, "already stopped"
    image = _image_name(pid).lower()
    if image not in ALLOWED_IMAGES:
        return False, f"refused: port {port} owned by {image or 'unknown'}"
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        check=False,
        creationflags=NO_WINDOW,
    )
    return True, f"stopped pid {pid}"


def stop_all() -> dict:
    messages = []
    ok, msg = stop_astrbot()
    messages.append(f"AstrBot: {msg}")
    ok, msg = stop_napcat()
    messages.append(f"NapCat: {msg}")
    end = time.time() + 12
    while time.time() < end and (port_up(ASTRBOT_PORT) or port_up(NAPCAT_PORT)):
        time.sleep(0.3)
    return {"ok": True, "messages": messages, "status": status()}


def _stop_managed(name: str, port: int) -> tuple[bool, str]:
    pid = _read_pid(name)
    if pid:
        image = _image_name(pid).lower()
        if image in ALLOWED_IMAGES:
            _kill_tree(pid)
        _clear_pid(name)
    ok, msg = _kill_port(port)
    return ok, msg


def stop_astrbot() -> tuple[bool, str]:
    return _stop_managed("astrbot", ASTRBOT_PORT)


def stop_napcat() -> tuple[bool, str]:
    return _stop_managed("napcat", NAPCAT_PORT)


def restart_all(quick_qq: str | None = None) -> dict:
    stop_all()
    time.sleep(0.8)
    return start_all(quick_qq)


def tail_log(name: str, max_chars: int = 12000) -> str:
    files = {
        "astrbot": LOGS / "astrbot.log",
        "napcat": LOGS / "napcat.log",
    }
    path = files.get(name)
    if not path or not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text[-max_chars:]
