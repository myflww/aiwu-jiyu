"""Local HTTP API + static UI for the dafeiyu desktop app."""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from backend import autostart, config_store, installer, lovehouse, napcat_api, plugins, processes
from backend import astrbot_api
from backend import metrics, pc_access, updater, log_retention, companion, stickers, plugin_updates
from backend import dashboard_credentials, custom_stickers, sticker_contributions
from backend import health, tasks, recovery, companion_memory, sticker_library, sticker_materials
from backend import personas, life, companion_growth
from backend.paths import APP_HOME, ASSETS, LOGS, RUN_DIR, UI_DIR, ensure_dirs

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "napcat": None}


class _BodyTooLarge(ValueError):
    pass


def _bool_field(body: dict, key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} 必须是布尔值")
    return value


class LocalServer(ThreadingHTTPServer):
    log_retention_service = None
    plugin_update_requested = False

    def server_close(self) -> None:
        if self.log_retention_service:
            self.log_retention_service.stop()
        super().server_close()


def _plugin_repair_allowed(service, plugin_status: dict) -> bool:
    with service.lock:
        release = service.release or {}
        return (plugin_status.get("state") == "checked"
                and plugin_status.get("update_available") is True
                and plugin_status.get("tag") == release.get("tag")
                and release.get("version") == service.version)


def _updates_snapshot(server) -> dict:
    service = updater.get_updater()
    result = service.snapshot()
    requested = bool(getattr(server, "plugin_update_requested", False))
    if not result.get("checking") and (result.get("auto_check") or requested):
        with service.lock:
            release = dict(service.release or {})
        if release.get("tag") and release.get("version") == result.get("latest_version"):
            accepted = plugin_updates.start_check(release["tag"], force=requested)
            if accepted.get("tag") == release["tag"]:
                server.plugin_update_requested = False
    plugin_status = plugin_updates.snapshot()
    plugin_status["can_repair"] = _plugin_repair_allowed(service, plugin_status)
    return {**result, "plugin_updates": plugin_status}


def _snapshot_napcat() -> dict:
    now = time.time()
    if _cache["napcat"] is not None and now - _cache["at"] < 4:
        return _cache["napcat"]
    result = {"logged_in": False, "qq": "", "nickname": "", "error": ""}
    if processes.port_up(6099):
        try:
            status = napcat_api.login_status()
            result["logged_in"] = bool(status.get("isLogin"))
            if result["logged_in"]:
                info = napcat_api.login_info()
                result["qq"] = info.get("qq", "")
                result["nickname"] = info.get("nickname", "")
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
    _cache["napcat"] = result
    _cache["at"] = now
    return result


def state() -> dict:
    config = config_store.load()
    installed = config_store.is_installed()
    if installed and not config.get("setup_complete"):
        config = config_store.adopt_from_astrbot(config)
        config_store.save(config)
    napcat = _snapshot_napcat()
    if napcat.get("qq") and napcat["qq"] != config.get("bot_qq"):
        config["bot_qq"] = napcat["qq"]
        config["bot_nickname"] = napcat.get("nickname", "")
        config_store.save(config)
    key = config_store.api_key(config)
    return {
        "setup_complete": bool(config.get("setup_complete") and installed and key),
        "installed": installed,
        "has_key": bool(key),
        "owner_qq": config.get("owner_qq", ""),
        "bot_qq": config.get("bot_qq", ""),
        "bot_nickname": config.get("bot_nickname", ""),
        "model": {
            "provider": config.get("model", {}).get("provider", "deepseek"),
            "api_base": config.get("model", {}).get("api_base", ""),
            "model": config.get("model", {}).get("model", ""),
            "key_mask": config_store.mask_key(key),
        },
        "whitelist": config.get("whitelist", {}),
        "memory": config.get("memory", {}),
        "napcat": napcat,
        "status": processes.status(),
        "install": config.get("install_dir") or str(APP_HOME),
        "autostart": autostart.status(),
        "auto_login": bool(config.get("auto_login", True)),
        "metrics": metrics.snapshot(),
        "pc_access": pc_access.status(),
        "members": pc_access.members(),
    }


def _quick_qq() -> str:
    config = config_store.load()
    if not config.get("auto_login", True):
        return ""
    return str(config.get("bot_qq") or "").strip()


def _apply_config(config: dict) -> dict:
    """Write AstrBot config and restart only AstrBot."""
    config_store.save(config)
    config_store.sync_astrbot_config(config)
    companion.sync_config(config)
    stickers.sync_config(config)
    life.sync_config(config)
    try:
        pc_access.ensure_plugin_current()
        pc_access.sync_owner()
    except Exception:
        pass
    if processes.port_up(6185):
        processes.stop_astrbot()
        time.sleep(0.8)
        processes.start_astrbot()
        processes.wait_port(6185, 40)
    return {"ok": True, "status": processes.status()}


def _add_whitelist(config: dict, kind: str, qq: str) -> dict:
    whitelist = config.setdefault("whitelist", {"enable": True, "private": [], "groups": []})
    qq = str(qq).strip()
    if not qq.isdigit():
        raise ValueError("只支持 QQ 号或群号")
    key = "private" if kind == "private" else "groups"
    values = whitelist.setdefault(key, [])
    if qq not in values:
        values.append(qq)
    return config


def _remove_whitelist(config: dict, kind: str, qq: str) -> dict:
    whitelist = config.setdefault("whitelist", {})
    key = "private" if kind == "private" else "groups"
    values = whitelist.get(key) or []
    whitelist[key] = [item for item in values if str(item) != str(qq)]
    return config


def _relocate(install_dir: str) -> dict:
    target = Path(install_dir).expanduser()
    if not target.is_absolute():
        raise ValueError("安装目录必须是绝对路径")
    target.mkdir(parents=True, exist_ok=True)
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Dafeiyu"
    base.mkdir(parents=True, exist_ok=True)
    (base / "bootstrap.json").write_text(
        json.dumps({"install_dir": str(target)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if target.resolve() == APP_HOME.resolve():
        return {"ok": True, "relaunch": False, "install_dir": str(target)}
    env = os.environ.copy()
    env["DAFEIYU_HOME"] = str(target)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        command = [sys.executable]
    else:
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py")]
    subprocess.Popen(
        command,
        cwd=str(target),
        env=env,
        creationflags=0x08000000,
        close_fds=True,
    )
    threading.Timer(0.9, lambda: os._exit(0)).start()
    return {"ok": True, "relaunch": True, "install_dir": str(target)}


class Handler(BaseHTTPRequestHandler):
    server_version = "AiwuJiyuApp/" + updater.current_version()

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        limit = {"/api/stickers/custom/import": 12 * 1024 * 1024,
                 "/api/stickers/custom/batch": 36 * 1024 * 1024,
                 "/api/stickers/custom/zip": 28 * 1024 * 1024}.get(self.path, 1024 * 1024)
        if length < 0 or length > limit:
            raise _BodyTooLarge("请求内容过大；单张最多8 MiB，批量原图共24 MiB，ZIP最多20 MiB。")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("请求内容必须是对象。")
            return data
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("请求内容不是有效的 JSON。") from None

    def _static(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        candidate = (UI_DIR / relative).resolve()
        if UI_DIR.resolve() not in candidate.parents and candidate != UI_DIR.resolve():
            self._json(403, {"ok": False})
            return
        if not candidate.exists() or candidate.is_dir():
            candidate = UI_DIR / "index.html"
        if not candidate.exists():
            self._json(404, {"ok": False, "message": "UI not found"})
            return
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self._send(200, candidate.read_bytes(), content_type)

    def _asset(self, path: str) -> None:
        candidate = (ASSETS / path.lstrip("/")).resolve()
        if ASSETS.resolve() not in candidate.parents or not candidate.is_file():
            self._json(404, {"ok": False, "message": "Asset not found"})
            return
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self._send(200, candidate.read_bytes(), content_type)

    def _preview_get(self, path: str) -> None:
        """Preview only fixture data: never inspect an installed bot or its accounts."""
        status = {
            "astrbot": {"up": False, "pid": None, "port": 6185},
            "napcat": {"up": False, "pid": None, "port": 6099},
            "onebot": {"up": False, "port": 6199},
        }
        pc = {"enabled": False, "workspace": "", "members": {}, "audit": []}
        fixtures = {
            "/api/health": {"ok": True, "preview": True, "overall": "unknown", "checks": [], "summary": "界面预览不读取实际运行状态。", "readonly": True},
            "/api/tasks": {"ok": True, "preview": True, "active": [], "recent": [], "source": "preview"},
            "/api/recovery": {"ok": True, "preview": True, "backups": [], "current_version": updater.current_version()},
            "/api/personas": {"ok": True, "preview": True, "cards": personas.catalog()["cards"], "default": "natural", "active_name": "界面预览", "runtime_online": False, "can_apply": False, "revision": None, "backups": [], "notice": "预览不读取运行人格。"},
            "/api/life": {"ok": True, "preview": True, "settings": {"search_enabled": False, "profile_enabled": False, "qzone_enabled": False, "search_provider": "bing_rss", "searxng_url": ""}, "owner_configured": False, "bot_configured": False, "plugin_enabled": False, "capabilities": {}, "avatars": []},
            "/api/stickers/library": {"ok": True, "preview": True, "stickers": [], "count": 0, "total_count": 0},
            "/api/stickers/materials": {"ok": True, "preview_mode": True, "installed_version": "", "message": "预览不连接社区素材更新服务。"},
            "/api/stickers": {"ok": True, **stickers.status()},
            "/api/companion": {"ok": True, **companion.DEFAULTS, "owner_configured": False,
                                "plugin_installed": False},
            "/api/growth": {"ok": True, "enabled": False, "available": False, "library_bound": False,
                            "counts":{}, "notice":"预览不读取私人相处手记。"},
            "/api/stickers/contributions/auth": {"ok": True, "configured": False, "login": "", "remembered": False},
            "/api/stickers/contributions/status": {"ok": True, "running": False, "state": "preview", "message": "预览不提交任何图片。", "pr_url": ""},
            "/api/logs/retention": {"ok": True, **log_retention.policy(), "preview": True,
                                    "checked_at": None, "trimmed_files": 0, "cleared_files": 0,
                                    "reclaimed_bytes": 0, "deferred": [], "errors": []},
            "/api/updates": {"ok": True, "current_version": updater.current_version(), "latest_version": "",
                             "available": False, "notify": False, "checking": False, "downloading": False,
                             "installing": False, "progress": 0, "downloaded": False, "download_verified": False,
                             "auto_check": True, "checked_at": None, "error": "", "message": "",
                             "release_url": updater.REPO_URL + "/releases/latest", "ignored_version": "",
                             "snoozed_until": 0,
                             "plugin_updates": {"state": "preview", "checking": False, "plugins": [],
                                                "update_available": False, "can_repair": False,
                                                "message": "预览不连接更新服务"}},
            "/api/state": {
                "preview": True, "setup_complete": True, "installed": False,
                "has_key": False, "owner_qq": "", "bot_qq": "", "bot_nickname": "大肥鱼",
                "model": {"provider": "deepseek", "api_base": "https://api.deepseek.com/v1",
                          "model": "deepseek-chat", "key_mask": ""},
                "whitelist": {"enable": True, "private": [], "groups": []},
                "memory": {"enabled": False, "endpoint": ""},
                "napcat": {"logged_in": False, "qq": "", "nickname": ""},
                "status": status, "install": "", "autostart": {"enabled": False},
                "auto_login": False, "pc_access": pc, "members": {},
                "metrics": {"total_mb": 0, "used_mb": 0, "available_mb": 0, "percent": 0,
                            "astrbot_mb": 0, "napcat_mb": 0, "app_mb": 0, "lovehouse_mb": 0},
            },
            "/api/status": status,
            "/api/metrics": {"ok": True, "available": False, "processes_available": False,
                             "total_mb": 0, "used_mb": 0, "available_mb": 0, "percent": 0,
                             "astrbot_mb": 0, "napcat_mb": 0, "app_mb": 0, "lovehouse_mb": 0,
                             "bot_total_mb": 0, "app_total_mb": 0, "sampled_at": None},
            "/api/contacts": {"ok": True, "logged_in": False, "friends": [], "groups": []},
            "/api/plugins": {"ok": True, "plugins": []},
            "/api/pc-access": {"ok": True, **pc},
            "/api/artifacts": {"ok": True, "workspace": "", "items": []},
            "/api/lovehouse/status": {"ok": True, "installed": False, "running": False,
                                     "libraries": [], "selected_id": "", "default_umos": []},
            "/api/autostart": {"ok": True, "supported": True, "enabled": False},
            "/api/napcat/quick-login": {"ok": True, "auto_login": False, "bot_qq": "", "candidates": []},
            "/api/setup/status": {"ok": True, "running": False, "done": False, "log": []},
        }
        if path == "/api/updates/status":
            path = "/api/updates"
        if path == "/api/plugins" and hasattr(plugins, "list_bundled_plugins"):
            fixtures[path]["plugins"] = plugins.list_bundled_plugins()
        if path == "/api/logs":
            self._send(200, b"", "text/plain; charset=utf-8")
        elif path in fixtures:
            self._json(200, fixtures[path])
        else:
            self._json(404, {"ok": False, "message": "预览中没有连接运行服务"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if getattr(self.server, "preview", False) and path.startswith("/api/"):
                self._preview_get(path)
            elif path.startswith("/assets/"):
                self._asset(path[len("/assets/"):])
            elif path == "/favicon.ico":
                self._asset("ui/app-icon.ico")
            elif path in {"/api/updates", "/api/updates/status"}:
                self._json(200, _updates_snapshot(self.server))
            elif path == "/api/state":
                self._json(200, state())
            elif path == "/api/health":
                self._json(200, health.snapshot(query.get("reason", ["all"])[0]))
            elif path == "/api/tasks":
                self._json(200, tasks.snapshot())
            elif path == "/api/recovery":
                self._json(200, {**recovery.list_backups(), "current_version": updater.current_version()})
            elif path == "/api/personas":
                self._json(200, personas.status())
            elif path == "/api/life":
                self._json(200, life.status())
            elif path == "/api/stickers/library":
                self._json(200, sticker_library.browse({"query": query.get("query", [""])[0], "favorites_only": query.get("favorites_only", ["false"])[0] == "true", "sort": query.get("sort", ["default"])[0]}))
            elif path == "/api/stickers/materials":
                self._json(200, sticker_materials.status())
            elif path.startswith("/api/stickers/materials/image/"):
                asset, mime = sticker_materials.asset(path.rsplit("/", 1)[-1])
                with asset.open("rb") as stream:
                    content = stream.read(8 * 1024 * 1024 + 1)
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError("图片超过大小上限。")
                self._send(200, content, mime)
            elif path == "/api/metrics":
                self._json(200, {"ok": True, **metrics.snapshot()})
            elif path == "/api/setup/status":
                self._json(200, installer.snapshot())
            elif path == "/api/status":
                self._json(200, processes.status())
            elif path == "/api/logs":
                name = query.get("name", ["astrbot"])[0]
                self._send(200, processes.tail_log(name).encode("utf-8"), "text/plain; charset=utf-8")
            elif path == "/api/logs/retention":
                service = getattr(self.server, "log_retention_service", None)
                self._json(200, {"ok": True, **(service.snapshot() if service else log_retention.policy())})
            elif path == "/api/companion":
                self._json(200, {"ok": True, **companion.status()})
            elif path == "/api/growth":
                self._json(200, companion_growth.status())
            elif path == "/api/stickers":
                self._json(200, {"ok": True, **stickers.status(settings=stickers.load_settings(), custom_items=custom_stickers.list_items())})
            elif path.startswith("/api/stickers/custom/") and path.endswith("/image"):
                identifier = path[len("/api/stickers/custom/"):-len("/image")]
                asset, mime = custom_stickers.asset(identifier)
                with asset.open("rb") as stream:
                    content = stream.read(8 * 1024 * 1024 + 1)
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError("图片超过大小上限。")
                self._send(200, content, mime)
            elif path == "/api/stickers/contributions/auth":
                self._json(200, {"ok": True, **sticker_contributions.auth_status()})
            elif path == "/api/stickers/contributions/status":
                self._json(200, sticker_contributions.job_status())
            elif path == "/api/dashboard/credentials":
                self._json(405, {"ok": False, "message": "请在入口旁主动展开登录信息。"})
            elif path.startswith(("/api/stickers/custom/", "/api/stickers/contributions/")):
                self._json(405, {"ok": False, "message": "请在素材库中操作。"})
            elif path == "/api/qr":
                self._send(200, napcat_api.qr_png(refresh=query.get("refresh", ["0"])[0] == "1"), "image/png")
            elif path == "/api/qr/status":
                status = napcat_api.login_status()
                payload = {"ok": True, "data": status}
                if status.get("isLogin"):
                    try:
                        info = napcat_api.login_info()
                        payload["account"] = info
                        _cache["napcat"] = {
                            "logged_in": True,
                            "qq": info.get("qq", ""),
                            "nickname": info.get("nickname", ""),
                            "error": "",
                        }
                        _cache["at"] = time.time()
                    except Exception:
                        payload["account"] = {}
                self._json(200, payload)
            elif path == "/api/contacts":
                logged = bool(_snapshot_napcat().get("logged_in"))
                friends = napcat_api.friends() if logged else []
                groups = napcat_api.groups() if logged else []
                self._json(200, {"ok": True, "logged_in": logged, "friends": friends, "groups": groups})
            elif path == "/api/lovehouse/status":
                self._json(200, {"ok": True, **lovehouse.libraries()})
            elif path == "/api/lovehouse/sources":
                library_id = query.get("library_id", [""])[0]
                if not library_id:
                    raise ValueError("缺少记忆库 ID")
                self._json(200, {"ok": True, "sources": lovehouse.sources(library_id)})
            elif path == "/api/lovehouse/organizer":
                library_id = query.get("library_id", [""])[0]
                if not library_id:
                    raise ValueError("缺少记忆库 ID")
                self._json(200, {"ok": True, "organizer": lovehouse.organizer(library_id)})
            elif path == "/api/plugins":
                self._json(200, {"ok": True, "plugins": plugins.list_plugins()})
            elif path == "/api/pc-access":
                self._json(200, {"ok": True, **pc_access.status()})
            elif path == "/api/artifacts":
                self._json(200, {"ok": True, "workspace": str(pc_access._workspace()), "items": pc_access.list_artifacts()})
            elif path == "/api/autostart":
                self._json(200, {"ok": True, **autostart.status()})
            elif path == "/api/napcat/quick-login":
                config = config_store.load()
                candidates = []
                if processes.port_up(6099):
                    try:
                        candidates = napcat_api.quick_login_list()
                    except Exception:
                        candidates = []
                self._json(
                    200,
                    {
                        "ok": True,
                        "auto_login": bool(config.get("auto_login", True)),
                        "bot_qq": config.get("bot_qq", ""),
                        "candidates": candidates,
                    },
                )
            elif path == "/api/whitelist":
                config = config_store.load()
                self._json(200, {"ok": True, "whitelist": config.get("whitelist", {}), "owner_qq": config.get("owner_qq", "")})
            elif path.startswith("/static/"):
                self._static(path[len("/static/"):])
            elif path.startswith("/api/"):
                self._json(404, {"ok": False, "message": "接口不存在。"})
            elif path == "/" or path == "":
                self._static("index.html")
            else:
                self._static(path)
        except ValueError as exc:
            self._json(400, {"ok": False, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "message": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if getattr(self.server, "preview", False):
            self._json(403, {"ok": False, "message": "这是界面预览，请在正式应用中设置"})
            return
        try:
            if path == "/api/dashboard/credentials" or path.startswith(("/api/stickers/custom/", "/api/stickers/contributions/", "/api/stickers/library/", "/api/stickers/materials/", "/api/memories/", "/api/recovery/", "/api/companion/", "/api/personas/", "/api/life/", "/api/growth/")):
                expected = f"http://127.0.0.1:{self.server.server_address[1]}"
                if self.headers.get("Origin") != expected or self.headers.get("Host") != expected.removeprefix("http://"):
                    self._json(403, {"ok": False, "message": "请在爱屋及鱼窗口中操作。"})
                    return
            if path.startswith("/api/"):
                origin = self.headers.get("Origin")
                expected_origin = f"http://127.0.0.1:{self.server.server_address[1]}"
                if (origin and origin != expected_origin) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                    self._json(403, {"ok": False, "message": "请从爱屋及鱼应用窗口操作"})
                    return
            body = self._body()
            if path.startswith("/api/personas/"):
                functions = {"preview": personas.preview, "apply": personas.apply, "restore": personas.restore}
                action = path.removeprefix("/api/personas/")
                if action not in functions:
                    self._json(404, {"ok": False, "message": "人格接口不存在。"})
                    return
                self._json(200, functions[action](body))
                return
            if path.startswith("/api/life/"):
                action = path.removeprefix("/api/life/")
                functions = {"search": life.search, "prepare": life.prepare, "confirm": life.confirm, "cancel": life.cancel}
                if action == "settings":
                    life.configure(body)
                    options = life.load()
                    enabled = bool(life.authorized_tools(options))
                    current = next((r for r in plugins.list_plugins() if r["id"] == life.PLUGIN_ID), None)
                    if current and bool(current.get("enabled")) != enabled:
                        plugins.set_enabled(life.PLUGIN_ID, enabled)
                    grant = personas.grant_bundled_tools(life.authorized_tools(options))
                    payload = {**life.status(), "persona_tools": grant}
                elif action == "profile":
                    if body:
                        raise ValueError("读取机器人资料不接收其他账号参数。")
                    payload = life.profile()
                elif action in functions:
                    payload = functions[action](body)
                else:
                    self._json(404, {"ok": False, "message": "生活能力接口不存在。"})
                    return
                self._json(200, payload)
                return
            if path in {"/api/companion/profile", "/api/companion/temporary"}:
                payload = companion.apply_profile(body.get("profile")) if path.endswith("/profile") else companion.set_temporary(body)
                self._json(200, {"ok": True, **payload})
                return
            if path.startswith("/api/growth/"):
                actions = {"/api/growth/inspect":companion_growth.inspect,
                           "/api/growth/mutate":companion_growth.mutate}
                if path not in actions:
                    self._json(404, {"ok":False, "message":"成长手记接口不存在。"})
                else:
                    self._json(200, actions[path](body))
                return
            if path.startswith("/api/memories/"):
                action = path.rsplit("/", 1)[-1]
                functions = {"catalog": companion_memory.catalog, "read": companion_memory.read, "add": companion_memory.add, "mutate": companion_memory.mutate}
                if action not in functions or path != "/api/memories/" + action:
                    self._json(404, {"ok": False, "message": "记忆接口不存在。"})
                else:
                    self._json(200, {"ok": True, **functions[action](body)})
                return
            if path.startswith("/api/recovery/"):
                if path == "/api/recovery/backup":
                    payload = recovery.create_backup()
                elif path == "/api/recovery/restore":
                    payload = recovery.restore(body)
                elif path == "/api/recovery/rollback":
                    if body.get("confirmed") is not True:
                        raise ValueError("请明确确认下载匹配的旧版安装程序。")
                    payload = updater.get_updater().download_rollback(body.get("backup_id"), confirmed=True)
                else:
                    self._json(404, {"ok": False, "message": "备份接口不存在。"})
                    return
                self._json(200, payload)
                return
            if path == "/api/dashboard/credentials":
                self._json(200, {"ok": True, **dashboard_credentials.reveal(body.get("target"))})
                return
            if path == "/api/stickers/custom/import":
                self._json(200, {"ok": True, "item": custom_stickers.import_item(body)})
                return
            if path in {"/api/stickers/custom/batch", "/api/stickers/custom/zip"}:
                self._json(200, custom_stickers.import_zip(body) if path.endswith("/zip") else custom_stickers.import_batch(body))
                return
            if path.startswith("/api/stickers/library/"):
                action = path.rsplit("/", 1)[-1]
                functions = {"favorite": sticker_library.set_favorite, "preview": sticker_library.preview, "feedback": sticker_library.feedback}
                if action not in functions or path != "/api/stickers/library/" + action:
                    self._json(404, {"ok": False, "message": "图库接口不存在。"})
                else:
                    self._json(200, functions[action](body))
                return
            if path.startswith("/api/stickers/materials/"):
                if path == "/api/stickers/materials/check":
                    self._json(200, sticker_materials.check())
                elif path == "/api/stickers/materials/apply":
                    self._json(200, sticker_materials.apply(body))
                else:
                    self._json(404, {"ok": False, "message": "素材更新接口不存在。"})
                return
            if path in {"/api/stickers/custom/update", "/api/stickers/custom/delete"}:
                identifier = body.get("id")
                if path.endswith("/update"):
                    self._json(200, {"ok": True, "item": custom_stickers.update_item(identifier, body.get("changes"))})
                else:
                    self._json(200, custom_stickers.delete_item(identifier))
                return
            if path.startswith("/api/stickers/contributions/"):
                action = path.rsplit("/", 1)[-1]
                if action == "connect":
                    self._json(200, sticker_contributions.connect(body))
                elif action == "disconnect":
                    self._json(200, sticker_contributions.disconnect())
                elif action == "preview":
                    self._json(200, sticker_contributions.preview(str(body.get("item_id") or ""), body.get("attribution")))
                elif action == "submit":
                    self._json(200, sticker_contributions.start_submission(body))
                elif action == "export":
                    if not isinstance(body.get("fingerprint"), str) or len(body["fingerprint"]) != 64:
                        raise ValueError("请先预览要导出的投稿内容。")
                    content, filename = sticker_contributions.export_zip(str(body.get("item_id") or ""), body.get("attribution"), rights_confirmed=body.get("rights_confirmed"), fingerprint=body["fingerprint"])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self._json(404, {"ok": False, "message": "投稿接口不存在。"})
                return
            if path.startswith("/api/updates/"):
                origin = self.headers.get("Origin")
                expected_origin = f"http://127.0.0.1:{self.server.server_address[1]}"
                if (origin and origin != expected_origin) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                    self._json(403, {"ok": False, "message": "请从应用窗口操作更新"})
                    return
                service = updater.get_updater()
                if path == "/api/updates/check":
                    self.server.plugin_update_requested = True
                    payload = service.check()
                elif path == "/api/updates/download":
                    payload = service.download(repair=_plugin_repair_allowed(service, plugin_updates.snapshot()))
                elif path in {"/api/updates/settings", "/api/updates/preferences"}:
                    payload = service.preferences(body)
                elif path == "/api/updates/install":
                    callback = getattr(self.server, "quit_callback", None)
                    if not callback:
                        raise ValueError("请在桌面应用窗口中安装更新")
                    payload = service.install()
                    timer = threading.Timer(1.2, callback)
                    timer.daemon = True
                    timer.start()
                else:
                    self._json(404, {"ok": False, "message": "更新接口不存在"})
                    return
                plugin_status = plugin_updates.snapshot()
                plugin_status["can_repair"] = _plugin_repair_allowed(service, plugin_status)
                self._json(200, {**payload, "plugin_updates": plugin_status})
            elif path == "/api/setup/install":
                config = config_store.load()
                config["install_dir"] = str(config_store.APP_HOME)
                if body.get("api_key"):
                    config_store.set_api_key(config, str(body["api_key"]))
                model = body.get("model") or {}
                if model:
                    config.setdefault("model", {}).update(
                        {k: v for k, v in model.items() if k in {"api_base", "model", "provider"}}
                    )
                config_store.save(config)
                self._json(200, installer.start(config))
            elif path == "/api/setup/finalize":
                config = config_store.load()
                config_store.sync_astrbot_config(config)
                processes.stop_all()
                time.sleep(0.8)
                result = processes.start_all(_quick_qq())
                astrbot_api.bootstrap_persona()
                self._json(200, result)
            elif path == "/api/config":
                config = config_store.load()
                old_owner = config.get("owner_qq", "")
                if "api_key" in body and str(body["api_key"]).strip():
                    config_store.set_api_key(config, str(body["api_key"]).strip())
                if "owner_qq" in body:
                    owner = str(body["owner_qq"]).strip()
                    if owner and not owner.isdigit():
                        raise ValueError("主号 QQ 必须是数字")
                    if old_owner and old_owner != owner:
                        whitelist = config.setdefault("whitelist", {})
                        whitelist["private"] = [
                            item for item in whitelist.get("private", []) if str(item) != old_owner
                        ]
                    config["owner_qq"] = owner
                    if owner:
                        _add_whitelist(config, "private", owner)
                model = body.get("model") or {}
                for key in ("api_base", "model", "provider"):
                    if key in model and str(model[key]).strip():
                        config.setdefault("model", {})[key] = str(model[key]).strip()
                if "memory_endpoint" in body:
                    config.setdefault("memory", {})["endpoint"] = str(body["memory_endpoint"]).strip()
                self._json(200, _apply_config(config))
            elif path == "/api/owner":
                qq = str(body.get("qq") or "").strip()
                if not qq.isdigit() or not 5 <= len(qq) <= 12:
                    raise ValueError("主人 QQ 必须是 5 到 12 位数字")
                config = config_store.load()
                old_owner = str(config.get("owner_qq") or "")
                if old_owner != qq:
                    whitelist = config.setdefault("whitelist", {})
                    whitelist["private"] = [
                        item for item in whitelist.get("private", []) if str(item) != old_owner
                    ]
                    config["owner_qq"] = qq
                    _add_whitelist(config, "private", qq)
                    result = _apply_config(config)
                    self._json(
                        200,
                        {
                            **result,
                            "owner_qq": qq,
                            "whitelist": config.get("whitelist", {}),
                        },
                    )
                else:
                    self._json(
                        200,
                        {
                            "ok": True,
                            "owner_qq": qq,
                            "whitelist": config.get("whitelist", {}),
                            "status": processes.status(),
                        },
                    )
            elif path == "/api/start":
                self._json(200, processes.start_all(_quick_qq()))
            elif path == "/api/stop":
                self._json(200, processes.stop_all())
            elif path == "/api/restart":
                self._json(200, processes.restart_all(_quick_qq()))
            elif path == "/api/astrbot/restart":
                processes.stop_astrbot()
                time.sleep(0.8)
                processes.start_astrbot()
                processes.wait_port(6185, 45)
                self._json(200, {"ok": True, "status": processes.status()})
            elif path == "/api/lovehouse/start":
                self._json(200, {"ok": True, **lovehouse.start()})
            elif path == "/api/lovehouse/open":
                self._json(200, lovehouse.open_window())
            elif path == "/api/lovehouse/create-library":
                name = str(body.get("name") or "").strip()
                self._json(200, {"ok": True, **lovehouse.create_library(name)})
            elif path == "/api/lovehouse/connect":
                library_id = str(body.get("library_id") or "").strip()
                if not library_id:
                    raise ValueError("请先选择记忆库")
                umos = body.get("umos")
                if not isinstance(umos, list) or not umos:
                    umos = lovehouse.astrbot_umos()
                umos = [str(item).strip() for item in umos if str(item).strip()]
                if not umos:
                    raise ValueError("请先填 AstrBot 的 UMO（在目标会话发送 /sid 获取）")
                result = lovehouse.connect(library_id, umos, name=str(body.get("name") or "大肥鱼"))
                self._json(200, result)
            elif path == "/api/plugins/install-lovehouse":
                self._json(200, plugins.install_lovehouse())
            elif path == "/api/plugins/toggle":
                origin = self.headers.get("Origin")
                expected_origin = f"http://127.0.0.1:{self.server.server_address[1]}"
                if (origin and origin != expected_origin) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                    self._json(403, {"ok": False, "message": "请从应用窗口操作插件"})
                    return
                self._json(200, plugins.set_enabled(body.get("id"), body.get("enabled")))
            elif path == "/api/pc-access":
                desktop = _bool_field(body, "desktop_enabled") if "desktop_enabled" in body else None
                result = pc_access.configure(
                    enabled=_bool_field(body, "enabled"),
                    desktop_enabled=desktop,
                    workspace=str(body.get("workspace") or ""),
                    timeout_s=int(body.get("timeout_s") or 60),
                    private_only=_bool_field(body, "private_only", True),
                    confirm=_bool_field(body, "confirm"),
                )
                self._json(200, {"ok": True, **result})
            elif path == "/api/companion":
                result = companion.configure(body)
                self._json(200, {"ok": True, **result})
            elif path == "/api/stickers/settings":
                result = stickers.configure(body)
                current = next((item for item in plugins.list_plugins()
                                if item["id"] == stickers.PLUGIN_ID), None)
                if current and current.get("enabled") and processes.port_up(6185):
                    _apply_config(config_store.load())
                self._json(200, {"ok": True, **result})
            elif path == "/api/artifacts/open":
                pc_access.open_artifact(str(body.get("path") or ""))
                self._json(200, {"ok": True})
            elif path == "/api/artifacts/reveal":
                pc_access.reveal_artifact(str(body.get("path") or ""))
                self._json(200, {"ok": True})
            elif path == "/api/artifacts/open-folder":
                pc_access.open_workspace()
                self._json(200, {"ok": True})
            elif path == "/api/plugins/open-folder":
                subprocess.Popen(["explorer", str(plugins.PLUGIN_ROOT)])
                self._json(200, {"ok": True})
            elif path == "/api/open-url":
                url = str(body.get("url") or "")
                if url not in {
                    "http://127.0.0.1:6185",
                    "http://127.0.0.1:6185/",
                    "http://127.0.0.1:6099",
                    "http://127.0.0.1:6099/",
                    "https://platform.deepseek.com/api_keys",
                }:
                    raise ValueError("不支持的地址")
                if "6185" in url and not processes.port_up(6185):
                    raise ValueError("AstrBot 没有在运行")
                if "6099" in url and not processes.port_up(6099):
                    raise ValueError("NapCat 没有在运行")
                if not webbrowser.open(url):
                    raise ValueError("无法打开默认浏览器，请检查系统浏览器设置后重试")
                self._json(200, {"ok": True})
            elif path == "/api/lovehouse/open-folder":
                subprocess.Popen(["explorer", str(lovehouse.DATA_DIR)])
                self._json(200, {"ok": True})
            elif path == "/api/autostart":
                enabled = bool(body.get("enabled"))
                self._json(200, {"ok": True, **(autostart.enable() if enabled else autostart.disable())})
            elif path == "/api/napcat/auto-login":
                config = config_store.load()
                config["auto_login"] = bool(body.get("enabled"))
                config_store.save(config)
                self._json(200, {"ok": True, "auto_login": config["auto_login"]})
            elif path == "/api/napcat/quick-login":
                config = config_store.load()
                target = str(body.get("qq") or config.get("bot_qq") or "").strip()
                if not target:
                    raise ValueError("还没有可自动登录的 QQ，请先扫码一次")
                candidates = []
                if processes.port_up(6099):
                    try:
                        candidates = napcat_api.quick_login_list()
                    except Exception:
                        candidates = []
                quick = {item["qq"] for item in candidates if item.get("quick")}
                if candidates and target not in quick:
                    raise ValueError("这个 QQ 没有保存的快速登录授权，请重新扫码")
                processes.stop_napcat()
                time.sleep(1.0)
                processes.start_napcat(target)
                processes.wait_port(6099, 30)
                time.sleep(5)
                login = napcat_api.login_status()
                info = napcat_api.login_info() if login.get("isLogin") else {}
                if login.get("isLogin"):
                    config["bot_qq"] = info.get("qq", target)
                    config["bot_nickname"] = info.get("nickname", "")
                    config["setup_complete"] = bool(config.get("model", {}).get("api_key_enc"))
                    config_store.save(config)
                self._json(
                    200,
                    {
                        "ok": bool(login.get("isLogin")),
                        "logged_in": bool(login.get("isLogin")),
                        "account": info,
                        "status": login,
                        "candidates": candidates,
                        "message": "" if login.get("isLogin") else "自动登录未成功，请重新扫码",
                    },
                )
            elif path == "/api/setup/relocate":
                install_dir = str(body.get("install_dir") or "").strip()
                if not install_dir:
                    raise ValueError("请选择安装目录")
                self._json(200, _relocate(install_dir))
            elif path == "/api/whitelist":
                config = config_store.load()
                action = body.get("action")
                kind = "private" if body.get("type") == "private" else "group"
                qq = str(body.get("id") or "")
                if action == "add":
                    _add_whitelist(config, kind, qq)
                elif action == "remove":
                    _remove_whitelist(config, kind, qq)
                    # 移出白名单就等于收回电脑权限，否则授权会留在插件里。
                    if kind == "private":
                        pc_access.drop_member(config, qq)
                elif action == "toggle":
                    config.setdefault("whitelist", {})["enable"] = bool(body.get("enable", True))
                else:
                    raise ValueError("未知操作")
                # AstrBot 只在启动时读取 id_whitelist，写完必须重启才真正生效。
                result = _apply_config(config)
                self._json(
                    200,
                    {
                        "ok": True,
                        "whitelist": config.get("whitelist", {}),
                        "members": pc_access.members(),
                        "status": result.get("status"),
                    },
                )
            elif path == "/api/members":
                qq = str(body.get("qq") or "").strip()
                config = config_store.load()
                if str(config.get("owner_qq") or "") == qq:
                    raise ValueError("主人始终拥有全部权限，不需要单独设置")
                perm = pc_access.set_member(
                    config,
                    qq,
                    read=_bool_field(body, "read"),
                    write=_bool_field(body, "write"),
                    run=_bool_field(body, "run"),
                    desktop=_bool_field(body, "desktop"),
                )
                # 有权限就必须能和她说话。
                if qq in perm:
                    _add_whitelist(config, "private", qq)
                result = _apply_config(config)
                self._json(
                    200,
                    {
                        "ok": True,
                        "members": pc_access.members(),
                        "whitelist": config.get("whitelist", {}),
                        "status": result.get("status"),
                    },
                )
            elif path == "/api/qr/complete":
                info = napcat_api.login_info()
                config = config_store.load()
                config["bot_qq"] = info.get("qq", "")
                config["bot_nickname"] = info.get("nickname", "")
                config["setup_complete"] = bool(config.get("model", {}).get("api_key_enc"))
                config_store.save(config)
                if config["bot_qq"]:
                    napcat_api.apply_onebot_ws(config["bot_qq"])
                config_store.sync_astrbot_config(config)
                self._json(200, {"ok": True, "account": info})
            elif path == "/api/memory":
                config = config_store.load()
                memory = config.setdefault("memory", {})
                memory["endpoint"] = str(body.get("endpoint") or "").strip()
                memory["enabled"] = bool(body.get("enabled", False))
                config_store.save(config)
                self._json(200, {"ok": True, "memory": memory})
            elif path == "/api/open-folder":
                subprocess.Popen(["explorer", str(config_store.APP_HOME)])
                self._json(200, {"ok": True})
            else:
                self._json(404, {"ok": False, "message": "not found"})
        except _BodyTooLarge as exc:
            self._json(413, {"ok": False, "message": str(exc)})
        except ValueError as exc:
            self._json(400, {"ok": False, "message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "message": str(exc)})

    def log_message(self, fmt: str, *args) -> None:
        return


def start_server(*, preview: bool = False, auto_start: bool = True, auto_update: bool = True) -> ThreadingHTTPServer:
    reload_bundled = False
    if not preview:
        ensure_dirs()
    config = {} if preview else config_store.load()
    if not preview and config_store.is_installed() and not config.get("setup_complete"):
        config = config_store.adopt_from_astrbot(config)
        config_store.save(config)
    if not preview and config_store.is_installed():
        try:
            bundle = plugins.install_bundled()
            companion.sync_config(config)
            stickers.sync_config(config)
            life.sync_config(config)
            changed = set(bundle.get("updated", [])) | set(bundle.get("installed", []))
            reload_bundled = (auto_start and bool(changed)
                              and any(item["id"] in changed and item.get("runtime_enabled", item.get("enabled"))
                                      for item in bundle.get("plugins", []))
                              and processes.port_up(6185))
        except Exception as exc:
            print(f"Bundled plugin synchronization deferred: {type(exc).__name__}", flush=True)
    server = LocalServer(("127.0.0.1", 0), Handler)
    server.preview = preview
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    if not preview:
        server.log_retention_service = log_retention.LogRetention(log_dir=LOGS, run_dir=RUN_DIR,
                                                                 active_provider=processes.status,
                                                                 before_trim=astrbot_api.preserve_log_credentials)
        server.log_retention_service.start()
    if not preview and auto_update:
        updater.get_updater().start()

    def _kick_services():
        time.sleep(1.0)
        try:
            if reload_bundled and processes.port_up(6185):
                stopped, _ = processes.stop_astrbot()
                if not stopped:
                    raise RuntimeError("Existing dialogue service could not be reloaded")
                processes.start_astrbot()
                processes.wait_port(6185, 40)
            if auto_start and config.get("setup_complete") and config.get("auto_login", True):
                processes.start_all(_quick_qq())
        except Exception as exc:
            print(f"Service startup deferred: {type(exc).__name__}", flush=True)

    if not preview and (reload_bundled or auto_start and config.get("setup_complete") and config.get("auto_login", True)):
        threading.Thread(target=_kick_services, daemon=True).start()
    return server
