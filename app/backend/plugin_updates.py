"""Read-only bundled plugin checks against an already verified stable release.

The desktop updater owns release discovery and user preferences. This module
never discovers main/HEAD, downloads plugin code, or toggles an installed add-on.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from backend import plugins, updater

PLUGIN_IDS = (
    "astrbot_plugin_face_convert", "astrbot_plugin_love_house", "astrbot_plugin_pc_operator",
    "astrbot_plugin_dafeiyu_companion", "astrbot_plugin_dafeiyu_stickers",
    "astrbot_plugin_dafeiyu_life",
)
CACHE_SECONDS = 6 * 60 * 60
FAILURE_BACKOFF = 60
MAX_MANIFEST_BYTES = 512 * 1024
_STABLE_TAG = re.compile(r"v?\d+\.\d+\.\d+", re.I)


def _version(value) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        major, minor, patch, stable, rc = updater.version_key(value)
        return f"{major}.{minor}.{patch}" + (f"rc{rc}" if not stable else "")
    except ValueError:
        return None


def _tag(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64 or not _STABLE_TAG.fullmatch(value):
        raise ValueError("插件更新只接受已经验证的 GitHub 正式发布标签")
    if updater.version_key(value)[3] != 1:
        raise ValueError("插件更新只接受正式发布版本")
    return value


def _manifest_url(tag: str) -> str:
    return f"https://raw.githubusercontent.com/{updater.REPOSITORY}/{_tag(tag)}/assets/plugins/manifest.json"


def _trusted_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        prefix = f"/{updater.REPOSITORY}/"
        suffix = "/assets/plugins/manifest.json"
        return (parsed.scheme == "https" and parsed.hostname == "raw.githubusercontent.com"
                and parsed.port in (None, 443) and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment and parsed.path.startswith(prefix)
                and parsed.path.endswith(suffix)
                and bool(_STABLE_TAG.fullmatch(parsed.path[len(prefix):-len(suffix)])))
    except ValueError:
        return False


class _SafeRedirect(HTTPRedirectHandler):
    def __init__(self, expected_url: str):
        super().__init__()
        self.expected_url = expected_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl != self.expected_url or not _trusted_url(newurl):
            raise ValueError("插件版本清单跳转到了其他地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: int = 8):
    if not _trusted_url(url):
        raise ValueError("插件版本清单不是可信的 GitHub 正式发布地址")
    request = Request(url, headers={"User-Agent": "AiwuJiyu-Plugin-Update/1", "Accept": "application/json"})
    return build_opener(_SafeRedirect(url)).open(request, timeout=timeout)


def _metadata_version(folder: Path) -> str | None:
    path = folder / "metadata.yaml"
    if plugins._linked(folder) or plugins._linked(path) or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            content = handle.read(65537)
        if len(content) > 65536:
            return None
        values = re.findall(r"^version:\s*([^\r\n]+)", content.decode("utf-8-sig"), re.M)
        return _version(values[0].strip().strip("'\"")) if len(values) == 1 else None
    except (OSError, UnicodeError):
        return None


def inventory() -> dict:
    """Read fixed local metadata only; no network, database or user switches."""
    try:
        bundled = {entry["id"]: entry for entry in plugins._manifest() if entry["id"] in PLUGIN_IDS}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        bundled = {}
    rows = []
    for plugin_id in PLUGIN_IDS:
        entry = bundled.get(plugin_id, {})
        directory = plugins.plugin_path(plugin_id)
        safe = plugins._safe_plugin_directory(directory, plugin_id)
        is_installed = safe and directory.is_dir() and (directory / "main.py").is_file()
        bundled_version = _metadata_version(plugins.BUNDLE_ROOT / plugin_id) or _version(entry.get("version"))
        rows.append({"id": plugin_id, "name": entry.get("name") or plugin_id,
                     "installed": _metadata_version(directory) if is_installed else None,
                     "bundled": bundled_version, "latest": None, "is_installed": bool(is_installed),
                     "status": "unchecked", "available": False, "update_via": "app"})
    return {"ok": True, "state": "unchecked", "checking": False, "tag": "", "checked_at": None,
            "available": False, "update_available": False, "message": "尚未检查正式版插件清单。", "error": "",
            "plugins": rows, "external_plugins_message": "其他插件请在 AstrBot 后台检查更新。"}


def _remote_versions(content: bytes) -> dict[str, str]:
    value = json.loads(content)
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("plugins"), list) or len(value["plugins"]) > 64):
        raise ValueError("正式版插件清单格式无效")
    versions = {}
    for entry in value["plugins"]:
        if not isinstance(entry, dict):
            raise ValueError("正式版插件清单条目无效")
        plugin_id = entry.get("id")
        if plugin_id not in PLUGIN_IDS:
            continue
        version = _version(entry.get("version"))
        if plugin_id in versions or version is None:
            raise ValueError("正式版插件版本号无效")
        versions[plugin_id] = version
    return versions


class PluginUpdates:
    def __init__(self, opener=None, clock=None):
        self._open = opener or _open
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, dict[str, str]]] = {}
        self.worker = None
        self._state = {"state": "unchecked", "checking": False, "tag": "", "checked_at": None,
                       "checked_epoch": 0, "message": "尚未检查正式版插件清单。", "error": ""}
        self._versions: dict[str, str] = {}

    def snapshot(self) -> dict:
        with self._lock:
            state, versions = dict(self._state), dict(self._versions)
        result = inventory()
        result.update(state)
        for row in result["plugins"]:
            row["latest"] = versions.get(row["id"])
            if state["state"] != "checked":
                row["status"] = "unknown" if state["state"] == "unavailable" else "unchecked"
                continue
            latest = row["latest"]
            if latest is None or row["bundled"] is None:
                row["status"] = "unknown"
                continue
            current = [row["bundled"]] + ([row["installed"]] if row["is_installed"] else [])
            row["available"] = any(value is not None and updater.version_key(value) < updater.version_key(latest) for value in current)
            row["status"] = ("update_available" if row["available"] else "unknown" if None in current
                             else "current" if row["is_installed"] else "not_installed")
        result["available"] = result["update_available"] = any(row["available"] for row in result["plugins"])
        if state["state"] == "checked":
            result["message"] = ("随附插件有新版本，可通过更新或修复爱屋及鱼获得。" if result["available"] else
                                 "已核对正式版清单；部分插件版本暂时无法确认。" if any(row["status"] == "unknown" for row in result["plugins"]) else
                                 "随附插件版本不低于当前正式版。")
        return result

    def start_check(self, tag: str, force: bool = False) -> dict:
        tag = _tag(tag)  # The caller supplies the updater's verified stable tag.
        now = self._clock()
        with self._lock:
            if self._state["checking"]:
                return self.snapshot()
            cached = self._cache.get(tag)
            if not force and cached and 0 <= now - cached[0] < CACHE_SECONDS:
                self._versions = dict(cached[1])
                self._state.update(state="checked", checking=False, tag=tag, checked_epoch=cached[0],
                                   checked_at=datetime.fromtimestamp(cached[0], timezone.utc).isoformat(), error="")
                return self.snapshot()
            if (not force and self._state["tag"] == tag and self._state["state"] == "unavailable"
                    and 0 <= now - self._state["checked_epoch"] < FAILURE_BACKOFF):
                return self.snapshot()
            self._versions = {}
            self._state.update(state="checking", checking=True, tag=tag, error="", message="正在核对正式版插件清单…")
            self.worker = threading.Thread(target=self._check_worker, args=(tag,), daemon=True, name="aiwu-plugin-update-check")
            self.worker.start()
            return self.snapshot()

    def _check_worker(self, tag: str) -> None:
        try:
            with self._open(_manifest_url(tag), timeout=8) as response:
                content = response.read(MAX_MANIFEST_BYTES + 1)
            if len(content) > MAX_MANIFEST_BYTES:
                raise ValueError("正式版插件清单超过大小上限")
            versions = _remote_versions(content)
            with self._lock:
                now = self._clock()
                # Cache bounded public metadata only, never plugin source or
                # private profile data. Limit memory across changing tags.
                self._cache = {tag: (now, versions)}
                self._versions = versions
                self._state.update(state="checked", error="")
        except Exception:
            with self._lock:
                self._versions = {}
                self._state.update(state="unavailable", error="暂时无法读取 GitHub 正式版插件清单，请稍后重试。",
                                   message="插件版本检查未完成，不能确认是否为最新。")
        finally:
            with self._lock:
                now = self._clock()
                self._state.update(checking=False, checked_epoch=now,
                                   checked_at=datetime.fromtimestamp(now, timezone.utc).isoformat())


_checker = PluginUpdates()


def start_check(tag: str, force: bool = False) -> dict:
    return _checker.start_check(tag, force=force)


def snapshot() -> dict:
    return _checker.snapshot()
