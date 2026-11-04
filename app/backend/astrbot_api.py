"""AstrBot dashboard helpers: login, persona bootstrap, readiness."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import time
import urllib.request
from urllib.error import HTTPError
from pathlib import Path
from typing import Any

from backend import dpapi
from backend.paths import ASSETS, ASTRBOT, ASTRBOT_PORT, LOGS, RUN_DIR
from backend.config_store import PERSONA_ID

BASE = f"http://127.0.0.1:{ASTRBOT_PORT}"
_desktop_lock = threading.Lock()
_session_token_lock = threading.RLock()
_SESSION_TOKEN_TTL = 30.0
_session_token_cache: dict[str, Any] = {}


def _clear_session_token(token: str | None = None) -> None:
    """Forget only this request's cached credential; never retry its action."""
    with _session_token_lock:
        if token is None or _session_token_cache.get("token") == token:
            _session_token_cache.clear()


def _session_process_identity() -> tuple | None:
    """A managed PID marker changes on restart, even if a PID is recycled.

    An absent, redirected or changing marker cannot prove process continuity,
    so callers authenticate normally without reusing a cached token.
    """
    path = RUN_DIR / "astrbot.pid"
    try:
        for candidate in (RUN_DIR, path):
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return None
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= 32:
            return None
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            raw = handle.read(33)
        after = path.stat()
        def signature(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
        # Windows Python can expose different ctime semantics through fstat
        # and path.stat. Compare file identity/mtime across handles, and compare
        # ctime only between the two path reads. Keep that path value in the
        # cache binding so replacement/restart signals still invalidate it.
        if (signature(before) != signature(opened) or signature(opened) != signature(after)
                or before.st_ctime_ns != after.st_ctime_ns):
            return None
        value = raw.strip()
        if not re.fullmatch(rb"[1-9][0-9]{0,9}", value):
            return None
        return (str(RUN_DIR.resolve()), int(value), *signature(after), after.st_ctime_ns)
    except (OSError, ValueError):
        return None


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
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 401 and token:
            _clear_session_token(token)
        raise


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
    # This helper is called immediately before a managed launch. Invalidate
    # before the new PID marker exists; the encrypted desktop secret may stay
    # the same while AstrBot's dashboard JWT signing key changes on restart.
    _clear_session_token()
    return {"ASTRBOT_DESKTOP_MANAGED": "1",
            "ASTRBOT_DESKTOP_SESSION_SECRET": _desktop_secret(create=True)}


def dashboard_token() -> str:
    """Reuse a short in-memory session for the same managed service only.

    Serialized acquisition avoids hitting AstrBot's authentication burst limit
    when status, persona application and tool grants arrive together. The
    bearer token is never persisted, logged or used to retry a failed action.
    """
    with _session_token_lock:
        secret = _desktop_secret()
        process = _session_process_identity()
        binding = (hashlib.sha256(secret.encode("utf-8")).digest(), process)
        now = time.monotonic()
        if (process is not None and _session_token_cache.get("binding") == binding
                and now < _session_token_cache.get("expires_at", 0)):
            return _session_token_cache["token"]
        _session_token_cache.clear()
        token = None
        if secret:
            try:
                result = _request("/api/v1/auth/desktop-session", payload={},
                                  extra_headers={"X-AstrBot-Desktop-Session": secret})
                candidate = (result.get("data") or {}).get("token")
                if isinstance(candidate, str) and candidate:
                    token = candidate
            except Exception:
                pass
        if token is None:
            password = read_initial_password()
            if password:
                token = login(password)
        if not isinstance(token, str) or not token:
            raise RuntimeError("当前服务尚未建立桌面会话")
        # A launch or secret rotation during authentication must not hand an
        # old service's bearer to a subsequent mutation. There is no action
        # retry here; a later caller can establish a fresh session.
        current_process = _session_process_identity()
        if _desktop_secret() != secret or process is not None and current_process != process:
            raise RuntimeError("服务会话已变化，请重新检查后再操作")
        if process is not None:
            _session_token_cache.update(token=token, binding=binding,
                                        expires_at=time.monotonic() + _SESSION_TOKEN_TTL)
        return token


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
    from backend import personas
    return personas.prompt("natural")


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
