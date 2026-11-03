"""AstrBot dashboard helpers: login, persona bootstrap, readiness."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from backend import dpapi
from backend.paths import ASSETS, ASTRBOT, ASTRBOT_PORT, LOGS, RUN_DIR
from backend.config_store import PERSONA_ID

BASE = f"http://127.0.0.1:{ASTRBOT_PORT}"
_desktop_lock = threading.Lock()


def _request(path: str, payload: dict | None = None, token: str | None = None,
             method: str = "POST", extra_headers: dict | None = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra_headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _desktop_secret(*, create: bool = False) -> str:
    path = RUN_DIR / "astrbot-desktop-session.enc"
    with _desktop_lock:
        if path.is_file():
            try:
                secret = dpapi.unprotect(path.read_text("utf-8").strip())
                if len(secret) >= 32:
                    return secret
            except Exception:
                pass
        if not create:
            return ""
        secret = secrets.token_urlsafe(48)
        encrypted = dpapi.protect(secret)
        if os.name == "nt" and not encrypted.startswith("dpapi:"):
            raise RuntimeError("无法加密本地服务会话，请重试")
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=RUN_DIR, prefix=".desktop-session-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encrypted.encode("utf-8"))
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return secret


def desktop_environment() -> dict[str, str]:
    return {"ASTRBOT_DESKTOP_MANAGED": "1",
            "ASTRBOT_DESKTOP_SESSION_SECRET": _desktop_secret(create=True)}


def dashboard_token() -> str:
    """Use AstrBot's official loopback desktop session, with legacy fallback."""
    secret = _desktop_secret()
    if secret:
        try:
            result = _request("/api/v1/auth/desktop-session", payload={},
                              extra_headers={"X-AstrBot-Desktop-Session": secret})
            token = (result.get("data") or {}).get("token")
            if token:
                return token
        except Exception:
            pass
    password = read_initial_password()
    if password:
        return login(password)
    raise RuntimeError("当前服务尚未建立桌面会话")


def wait_ready(timeout: float = 45.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=2):
                return True
        except Exception:
            time.sleep(0.4)
    return False


def read_initial_password() -> str:
    log = LOGS / "astrbot.log"
    password = ""
    try:
        with log.open(encoding="utf-8", errors="ignore") as stream:
            while line := stream.readline(8192):
                matches = re.findall(r"Initial password:\s*(\S+)", line)
                if matches:
                    password = matches[-1]
    except Exception:
        pass
    if password:
        _cache_initial_password(password)
        return password
    cache = RUN_DIR / "astrbot-initial-login.enc"
    try:
        if cache.is_file() and not cache.is_symlink() and cache.stat().st_size < 16384:
            return dpapi.unprotect(cache.read_text("utf-8").strip())
    except Exception:
        pass
    return ""


def _cache_initial_password(password: str) -> bool:
    """Keep the legacy login fallback private when diagnostic logs are trimmed."""
    temporary = None
    try:
        encrypted = dpapi.protect(password)
        if not encrypted.startswith("dpapi:"):
            return False
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        destination = RUN_DIR / "astrbot-initial-login.enc"
        if destination.is_symlink():
            return False
        if destination.exists() and dpapi.unprotect(destination.read_text("utf-8").strip()) == password:
            return True
        with tempfile.NamedTemporaryFile(dir=RUN_DIR, prefix=".initial-login-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encrypted.encode("utf-8"))
        os.replace(temporary, destination)
        return True
    except Exception:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def preserve_log_credentials(name: str) -> bool:
    if name != "astrbot.log":
        return True
    password = read_initial_password()
    return not password or _cache_initial_password(password)


def login(password: str) -> str:
    result = _request(
        "/api/auth/login", {"username": "astrbot", "password": password}
    )
    token = (result.get("data") or {}).get("token")
    if not token:
        raise RuntimeError(result.get("message") or "AstrBot 面板登录失败")
    return token


def list_personas(token: str) -> list[dict]:
    result = _request("/api/v1/personas", token=token, method="GET")
    data = result.get("data") or []
    return data if isinstance(data, list) else []


def persona_body() -> str:
    path = ASSETS / "persona" / "大肥鱼-人格.md"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.startswith("你是「大肥鱼」"):
            start = index
            break
    return "\n".join(lines[start:]).strip()


def ensure_persona(token: str) -> bool:
    personas = list_personas(token)
    if any(item.get("persona_id") == PERSONA_ID for item in personas):
        return False
    result = _request(
        "/api/v1/personas",
        {
            "persona_id": PERSONA_ID,
            "system_prompt": persona_body(),
            "begin_dialogs": [],
            "tools": [],
            "skills": [],
            "custom_error_message": "",
        },
        token,
    )
    if result.get("status") != "ok":
        raise RuntimeError(result.get("message") or "创建大肥鱼人格失败")
    return True


def bootstrap_persona(password: str = "") -> bool:
    """Create the persona if the dashboard can be logged into."""
    if not wait_ready(20):
        return False
    secret = password or read_initial_password()
    if not secret:
        return False
    try:
        token = login(secret)
        return ensure_persona(token)
    except Exception:
        return False
