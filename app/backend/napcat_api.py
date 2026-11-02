"""NapCat WebUI / OneBot helpers used by the desktop app."""

from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import urllib.request
from pathlib import Path
from typing import Any

from backend.paths import NAPCAT, NAPCAT_PORT, ONEBOT_PORT

API_BASE = f"http://127.0.0.1:{NAPCAT_PORT}/api"
WEBUI_CONFIG = NAPCAT / "napcat" / "config" / "webui.json"
CONFIG_DIR = NAPCAT / "napcat" / "config"

_lock = threading.Lock()
_jwt: str | None = None


def _post(path: str, payload: dict | None = None, jwt: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    request = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload or {}).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def webui_token() -> str:
    data = json.loads(WEBUI_CONFIG.read_text(encoding="utf-8-sig"))
    return str(data.get("token") or "")


def _login() -> str:
    token = webui_token()
    digest = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
    result = _post("/auth/login", {"hash": digest})
    credential = (result.get("data") or {}).get("Credential")
    if result.get("code") != 0 or not credential:
        raise RuntimeError(result.get("message") or "NapCat WebUI 登录失败")
    return credential


def _call(path: str, payload: dict | None = None) -> dict:
    global _jwt
    with _lock:
        if not _jwt:
            _jwt = _login()
        token = _jwt
    result = _post(path, payload, token)
    if result.get("code") != 0 and "Unauthorized" in str(result.get("message")):
        with _lock:
            _jwt = _login()
            token = _jwt
        result = _post(path, payload, token)
    return result


def _onebot(action: str, params: dict | None = None) -> dict:
    result = _call("/Debug/call/", {"action": action, "params": params or {}})
    if result.get("code") != 0:
        raise RuntimeError(result.get("message") or f"NapCat action failed: {action}")
    payload = result.get("data") or {}
    if payload.get("status") == "failed":
        raise RuntimeError(payload.get("message") or f"NapCat action failed: {action}")
    return payload.get("data")


def qr_png(refresh: bool = False) -> bytes:
    import qrcode

    if refresh:
        _call("/QQLogin/RefreshQRcode")
    result = _call("/QQLogin/GetQQLoginQrcode")
    if result.get("code") != 0:
        raise RuntimeError(result.get("message") or "获取二维码失败")
    url = (result.get("data") or {}).get("qrcode") or ""
    if not url:
        raise RuntimeError("NapCat 没有返回二维码地址")
    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=9,
        border=3,
    )
    code.add_data(url)
    code.make(fit=True)
    image = code.make_image(fill_color="#0b1b2b", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def login_status() -> dict:
    result = _call("/QQLogin/CheckLoginStatus")
    if result.get("code") != 0:
        raise RuntimeError(result.get("message") or "读取登录状态失败")
    return result.get("data") or {}


def login_info() -> dict:
    data = _onebot("get_login_info") or {}
    return {
        "qq": str(data.get("user_id") or ""),
        "nickname": str(data.get("nickname") or ""),
    }


def quick_login_list() -> list[dict]:
    result = _call("/QQLogin/GetQuickLoginListNew")
    if result.get("code") != 0:
        raise RuntimeError(result.get("message") or "读取快速登录列表失败")
    items = result.get("data") or []
    output = []
    for item in items:
        qq = str(item.get("uin") or "")
        if not qq:
            continue
        output.append(
            {
                "qq": qq,
                "nickname": str(item.get("nickName") or ""),
                "quick": bool(item.get("isQuickLogin")),
                "auto": bool(item.get("isAutoLogin")),
            }
        )
    return output


def friends() -> list[dict]:
    data = _onebot("get_friend_list") or []
    result = []
    for item in data:
        result.append(
            {
                "id": str(item.get("user_id") or ""),
                "name": str(item.get("nickname") or item.get("remark") or ""),
                "remark": str(item.get("remark") or ""),
            }
        )
    return result


def groups() -> list[dict]:
    data = _onebot("get_group_list") or []
    result = []
    for item in data:
        result.append(
            {
                "id": str(item.get("group_id") or ""),
                "name": str(item.get("group_name") or ""),
            }
        )
    return result


def detect_login_qq() -> str:
    if not CONFIG_DIR.exists():
        return ""
    for path in CONFIG_DIR.glob("onebot11_*.json"):
        match = re.search(r"onebot11_(\d+)\.json$", path.name)
        if match:
            return match.group(1)
    return ""


def apply_onebot_ws(qq: str) -> None:
    """Make NapCat connect to AstrBot's reverse WebSocket endpoint."""
    path = CONFIG_DIR / f"onebot11_{qq}.json"
    if not path.exists():
        path = CONFIG_DIR / f"onebot11_{qq}.json"
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    else:
        config = {
            "network": {
                "httpServers": [],
                "httpSseServers": [],
                "httpClients": [],
                "websocketServers": [],
                "websocketClients": [],
                "plugins": [],
            },
            "musicSignUrl": "",
            "enableLocalFile2Url": False,
            "parseMultMsg": False,
        }
    config.setdefault("network", {})["websocketClients"] = [
        {
            "name": "astrbot",
            "enable": True,
            "url": f"ws://127.0.0.1:{ONEBOT_PORT}/ws",
            "reportSelfMessage": False,
            "messagePostFormat": "array",
            "token": "",
            "debug": False,
            "heartInterval": 30000,
            "reconnectInterval": 3000,
        }
    ]
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _call("/OB11Config/SetConfig", {"config": json.dumps(config, ensure_ascii=False)})
    except Exception:
        # The file is already written; a restart will pick it up.
        pass


def set_profile(nickname: str, note: str = "") -> None:
    _onebot("set_qq_profile", {"nickname": nickname, "personal_note": note})


def set_avatar(path: Path) -> None:
    _onebot("set_qq_avatar", {"file": str(path.resolve())})
