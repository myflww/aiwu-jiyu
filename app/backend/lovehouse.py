"""Love House detection, local management API, and AstrBot plugin wiring."""

from __future__ import annotations

import ctypes
import http.client
import json
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from backend import config_store, processes
from backend.paths import ASSETS, ASTRBOT

KNOWN_LATEST = "0.10.2rc1"
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
DATA_DIR = LOCALAPPDATA / "LoveHouse"
STATE_FILE = DATA_DIR / "controller-state.json"
EXE_CANDIDATES = [
    Path.home() / "Desktop" / "LoveHouse" / "LoveHouse.exe",
    Path.home() / "OneDrive" / "Desktop" / "LoveHouse" / "LoveHouse.exe",
    LOCALAPPDATA / "Programs" / "LoveHouse" / "LoveHouse.exe",
    LOCALAPPDATA / "LoveHouse" / "LoveHouse.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "LoveHouse" / "LoveHouse.exe",
]
PLUGIN_NAME = "astrbot_plugin_love_house"
PLUGIN_CONFIG = f"{PLUGIN_NAME}_config.json"

_cookie_cache: dict[str, Any] = {"port": None, "cookie": "", "at": 0.0}


class LoveHouseError(RuntimeError):
    pass


def _find_exe() -> Path | None:
    for path in EXE_CANDIDATES:
        if path.is_file():
            return path
    return None


def _read_version(exe: Path | None) -> str:
    if not exe:
        return ""
    readme = exe.parent / "WINDOWS-README.txt"
    if readme.exists():
        try:
            first = readme.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
            match = re.search(r"(\d+\.\d+\.\d+(?:rc\d+)?)", first)
            if match:
                return match.group(1)
        except Exception:
            pass
    return ""


def _port() -> int:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        port = int(data.get("controller_port"))
        if 1 <= port <= 65535:
            return port
    except Exception:
        pass
    return 8768


def _health(port: int, timeout: float = 2.0) -> dict | None:
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        connection.request(
            "GET",
            "/api/health",
            headers={"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"},
        )
        response = connection.getresponse()
        body = response.read()
        if response.status == 200:
            return json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return None


def detect() -> dict:
    exe = _find_exe()
    port = _port()
    health = _health(port) if exe or DATA_DIR.exists() else None
    version = _read_version(exe)
    if health and health.get("version"):
        version = health["version"]
    return {
        "installed": bool(exe),
        "exe": str(exe) if exe else "",
        "data_dir": str(DATA_DIR),
        "version": version,
        "latest": KNOWN_LATEST,
        "update_available": bool(version and version != KNOWN_LATEST),
        "running": bool(health),
        "port": port if health else 0,
        "health": health or {},
        "plugin_installed": (ASTRBOT / "data" / "plugins" / PLUGIN_NAME).is_dir(),
        "plugin_config": (ASTRBOT / "data" / "config" / PLUGIN_CONFIG).is_file(),
        "organizer_note": "记忆整理模型需要在爱之屋的“记忆整理”里单独配置",
    }


def start() -> dict:
    info = detect()
    if info["running"]:
        return info
    exe = _find_exe()
    if not exe:
        raise LoveHouseError("没有找到 LoveHouse.exe，请先安装爱之屋 0.10.2rc1 或更新版本")
    subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
    end = time.time() + 40
    while time.time() < end:
        health = _health(_port())
        if health:
            time.sleep(0.8)
            return detect()
        time.sleep(0.8)
    raise LoveHouseError("爱之屋启动超时；请手动打开一次，确认窗口能正常显示")


def _find_window() -> int:
    user32 = ctypes.windll.user32
    found: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if "爱之屋" in title or "love house" in title.lower():
            found.append(int(hwnd))
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found[0] if found else 0


def open_window() -> dict:
    info = detect()
    if not info["running"]:
        start()
        time.sleep(1.0)
    hwnd = _find_window()
    focused = False
    if hwnd:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        focused = True
    return {"ok": True, "focused": focused, **detect()}


def _cookie(port: int, force: bool = False) -> str:
    now = time.time()
    if (
        not force
        and _cookie_cache["port"] == port
        and _cookie_cache["cookie"]
        and now - _cookie_cache["at"] < 600
    ):
        return str(_cookie_cache["cookie"])
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request(
        "GET",
        "/",
        headers={"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"},
    )
    response = connection.getresponse()
    response.read()
    header = response.getheader("Set-Cookie") or ""
    cookie = header.split(";", 1)[0] if header else ""
    if not cookie:
        raise LoveHouseError("无法取得爱之屋管理会话；请先在本机打开一次爱之屋界面")
    _cookie_cache.update({"port": port, "cookie": cookie, "at": now})
    return cookie


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    library_id: str | None = None,
    retry: bool = True,
) -> Any:
    port = _port()
    if not _health(port, timeout=1.5):
        raise LoveHouseError("爱之屋没有在运行，请先启动爱之屋")
    cookie = _cookie(port)
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Origin": f"http://127.0.0.1:{port}",
        "Cookie": cookie,
        "Content-Type": "application/json",
    }
    if library_id:
        headers["X-NKB-Library"] = library_id
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request(method, path, body=payload, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    if response.status == 401 and retry:
        _cookie(port, force=True)
        return _request(method, path, body, library_id, retry=False)
    data: Any = {}
    if raw:
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = {"raw": raw.decode("utf-8", errors="ignore")}
    if response.status >= 400:
        message = data.get("error") if isinstance(data, dict) else ""
        raise LoveHouseError(message or f"爱之屋接口失败（HTTP {response.status}）")
    return data


def libraries() -> dict:
    info = detect()
    if not info["running"]:
        return {**info, "libraries": [], "selected_id": ""}
    data = _request("GET", "/api/libraries")
    items = data.get("libraries") or []
    selected = data.get("selected_id") or ""
    preferred = next((item for item in items if item.get("name") == "大肥鱼" and not item.get("is_demo")), None)
    return {
        **info,
        "libraries": items,
        "selected_id": selected,
        "suggested_id": (preferred or {}).get("id") or selected,
        "default_umos": astrbot_umos(),
    }


def create_library(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise LoveHouseError("记忆库名称不能为空")
    data = _request("POST", "/api/libraries", {"name": name})
    return {"ok": True, "result": data, **libraries()}


def sources(library_id: str) -> dict:
    return _request("GET", "/api/agent/sources", library_id=library_id)


def organizer(library_id: str) -> dict:
    data = _request("GET", "/api/organizer", library_id=library_id)
    settings = data.get("settings") or {}
    provider = data.get("provider") or {}
    configuration = data.get("configuration") or {}
    return {
        "library_id": library_id,
        "enabled": bool(settings.get("enabled")),
        "configured": bool(configuration.get("configured")),
        "model_ready": bool(provider.get("model_ready")),
        "base_url": provider.get("base_url") or settings.get("base_url") or "",
        "model": provider.get("model") or settings.get("model") or "",
        "storage": provider.get("storage") or "none",
        "message": provider.get("configuration_error") or ("已配置" if provider.get("model_ready") else "尚未配置记忆整理模型"),
        "raw": data,
    }


def astrbot_umos(owner_qq: str = "") -> list[str]:
    config = config_store.load()
    owner = (owner_qq or config.get("owner_qq") or "").strip()
    if not owner:
        return []
    try:
        data = json.loads((ASTRBOT / "data" / "cmd_config.json").read_text(encoding="utf-8-sig"))
        platform = (data.get("platform") or [{}])[0]
        platform_id = str(platform.get("id") or "qq")
    except Exception:
        platform_id = "qq"
    return [f"{platform_id}:FriendMessage:{owner}"]


def create_astrbot_source(library_id: str, umos: list[str], name: str = "大肥鱼") -> dict:
    config = config_store.load()
    bot_qq = str(config.get("bot_qq") or "")
    runtime_id = f"astrbot-{bot_qq}" if bot_qq else "astrbot-dafeiyu"
    body = {
        "name": name,
        "adapter": "astrbot",
        "runtime_agent_id": runtime_id,
        "platforms": ["qq"],
        "astrbot_setup": {
            "same_machine": True,
            "platform": "qq",
            "allowed_umos": umos,
        },
    }
    data = _request("POST", "/api/agent/sources", body, library_id=library_id)
    if data.get("astrbot_setup_error"):
        raise LoveHouseError(data["astrbot_setup_error"])
    if not data.get("astrbot_setup"):
        raise LoveHouseError("爱之屋没有返回 AstrBot 插件配置")
    return data


def install_plugin(config: dict) -> dict:
    source = ASSETS / "plugins" / PLUGIN_NAME
    if not source.is_dir():
        raise LoveHouseError("安装包内缺少爱之屋 AstrBot 插件")
    plugins_dir = ASTRBOT / "data" / "plugins"
    config_dir = ASTRBOT / "data" / "config"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    target = plugins_dir / PLUGIN_NAME
    if target.exists():
        backup_root = ASTRBOT / "data" / "plugin_backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / f"{PLUGIN_NAME}.bak-{int(time.time())}"
        shutil.copytree(target, backup)
        shutil.rmtree(target)
    shutil.copytree(source, target)

    config_path = config_dir / PLUGIN_CONFIG
    existing: dict = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except Exception:
            existing = {}
    existing.setdefault("enabled", True)
    existing.setdefault("record_observations", True)
    existing.setdefault("record_scheduled_outputs", False)
    bindings = existing.get("bindings") or []
    new_bindings = config.get("bindings") or []
    new = new_bindings[0] if new_bindings else None
    if new:
        bindings = [
            item
            for item in bindings
            if item.get("runtime_agent_id") != new.get("runtime_agent_id")
            and item.get("source_config_path") != new.get("source_config_path")
        ]
        bindings.append(new)
    existing["bindings"] = bindings
    config_path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    restarted = False
    if processes.port_up(6185):
        processes.stop_astrbot()
        time.sleep(0.8)
        processes.start_astrbot()
    else:
        processes.start_astrbot()
    processes.wait_port(6185, 45)
    restarted = True
    return {
        "plugin_dir": str(target),
        "config_path": str(config_path),
        "bindings": len(bindings),
        "restarted": restarted,
    }


def plugin_status() -> dict:
    plugin_dir = ASTRBOT / "data" / "plugins" / PLUGIN_NAME
    config_path = ASTRBOT / "data" / "config" / PLUGIN_CONFIG
    bindings = 0
    enabled = False
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            bindings = len(data.get("bindings") or [])
            enabled = bool(data.get("enabled"))
        except Exception:
            pass
    return {
        "installed": plugin_dir.is_dir(),
        "config_exists": config_path.exists(),
        "enabled": enabled,
        "bindings": bindings,
        "plugin_dir": str(plugin_dir),
        "config_path": str(config_path),
    }


def connect(library_id: str, umos: list[str], name: str = "大肥鱼") -> dict:
    info = detect()
    if not info["running"]:
        start()
    created = create_astrbot_source(library_id, umos, name=name)
    setup = created.get("astrbot_setup") or {}
    installed = install_plugin(setup.get("config") or {})
    return {
        "ok": True,
        "source": created.get("source") or {},
        "setup": {
            "config_path": setup.get("config_path"),
            "config_filename": setup.get("config_filename"),
            "plugin_directory": setup.get("plugin_directory"),
            "instructions": setup.get("instructions") or [],
        },
        "installed": installed,
        "plugin": plugin_status(),
        "organizer": organizer(library_id),
        "sources": sources(library_id),
    }
