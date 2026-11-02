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
from backend import metrics, pc_access
from backend.paths import APP_HOME, ASSETS, LOGS, UI_DIR, ensure_dirs

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "napcat": None}


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
    server_version = "DafeiyuApp/1.0"

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/state":
                self._json(200, state())
            elif path == "/api/setup/status":
                self._json(200, installer.snapshot())
            elif path == "/api/status":
                self._json(200, processes.status())
            elif path == "/api/logs":
                name = query.get("name", ["astrbot"])[0]
                self._send(200, processes.tail_log(name).encode("utf-8"), "text/plain; charset=utf-8")
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
            elif path == "/" or path == "":
                self._static("index.html")
            else:
                self._static(path)
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "message": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._body()
        try:
            if path == "/api/setup/install":
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
            elif path == "/api/pc-access":
                result = pc_access.configure(
                    enabled=bool(body.get("enabled")),
                    workspace=str(body.get("workspace") or ""),
                    timeout_s=int(body.get("timeout_s") or 60),
                    private_only=bool(body.get("private_only", True)),
                    confirm=bool(body.get("confirm")),
                )
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
                }:
                    raise ValueError("不支持的地址")
                if "6185" in url and not processes.port_up(6185):
                    raise ValueError("AstrBot 没有在运行")
                if "6099" in url and not processes.port_up(6099):
                    raise ValueError("NapCat 没有在运行")
                webbrowser.open(url)
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
                    read=bool(body.get("read")),
                    write=bool(body.get("write")),
                    run=bool(body.get("run")),
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
                import subprocess

                subprocess.Popen(["explorer", str(config_store.APP_HOME)])
                self._json(200, {"ok": True})
            else:
                self._json(404, {"ok": False, "message": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "message": str(exc)})

    def log_message(self, fmt: str, *args) -> None:
        return


def start_server() -> ThreadingHTTPServer:
    ensure_dirs()
    config = config_store.load()
    if config_store.is_installed() and not config.get("setup_complete"):
        config = config_store.adopt_from_astrbot(config)
        config_store.save(config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def _kick_services():
        time.sleep(1.0)
        try:
            processes.start_all(_quick_qq())
        except Exception:
            pass

    if config.get("setup_complete") and config.get("auto_login", True):
        threading.Thread(target=_kick_services, daemon=True).start()
    return server
