"""AstrBot dashboard helpers: login, persona bootstrap, readiness."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from backend.paths import ASSETS, ASTRBOT, ASTRBOT_PORT, LOGS
from backend.config_store import PERSONA_ID

BASE = f"http://127.0.0.1:{ASTRBOT_PORT}"


def _request(path: str, payload: dict | None = None, token: str | None = None, method: str = "POST") -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


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
    if not log.exists():
        return ""
    try:
        text = log.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    matches = re.findall(r"Initial password:\s*(\S+)", text)
    return matches[-1] if matches else ""


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
