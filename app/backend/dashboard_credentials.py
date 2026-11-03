"""Read user-facing local dashboard credentials only after an explicit reveal.

No import-time reads, login requests, resets, plaintext persistence or tokens in
URLs. The HTTP caller owns same-origin POST validation and no-store headers.
Internal desktop-session/service-account/JWT/TOTP secrets are never returned.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from backend import dpapi
from backend.paths import ASTRBOT, ASTRBOT_PORT, LOGS, NAPCAT, NAPCAT_PORT, RUN_DIR

_CONFIG_LIMIT = 2 * 1024 * 1024
_LOG_LIMIT = 2 * 1024 * 1024
_CACHE_LIMIT = 16 * 1024
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_INITIAL_PASSWORD = re.compile(r"Initial password:[ \t]*([^\s\x00-\x1f\x7f]{1,1024})")


def _read(path: Path, anchor: Path, limit: int, *, tail: bool = False) -> bytes:
    """Read one fixed allowlisted file, bounded and without redirected parents."""
    if path == anchor or anchor not in path.parents:
        raise ValueError("Credential file is outside its fixed directory")
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Credential paths cannot be redirected")
        if item == anchor:
            break
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("Credential source must be a normal file")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("Credential source changed while opening")
        offset = max(0, opened.st_size - limit) if tail else 0
        if not tail and opened.st_size > limit:
            raise ValueError("Credential source is too large")
        stream.seek(offset)
        raw = stream.read(limit if tail else limit + 1)
        if len(raw) > limit:
            raise ValueError("Credential source is too large")
        if offset:
            # Do not interpret a truncated line as an initial-password notice.
            raw = raw.partition(b"\n")[2]
        return raw


def _json(path: Path, anchor: Path) -> dict:
    data = json.loads(_read(path, anchor, _CONFIG_LIMIT).decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Dashboard configuration must be an object")
    return data


def _string(value, maximum: int) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        return ""
    return value


def _initial_candidates() -> list[str]:
    """Existing encrypted cache first; bounded recent log fallback, never write."""
    candidates = []
    try:
        stored = _read(RUN_DIR / "astrbot-initial-login.enc", RUN_DIR, _CACHE_LIMIT).decode("utf-8").strip()
        if stored.startswith("dpapi:"):
            candidate = _string(dpapi.unprotect(stored), 1024)
            if candidate:
                candidates.append(candidate)
    except (OSError, ValueError, TypeError, UnicodeError):
        pass
    try:
        raw = _read(LOGS / "astrbot.log", LOGS, _LOG_LIMIT, tail=True).decode("utf-8", errors="ignore")
        matches = _INITIAL_PASSWORD.findall(_ANSI.sub("", raw))
        for candidate in reversed(matches):
            if candidate not in candidates:
                candidates.append(candidate)
                if len(candidates) >= 4:
                    break
    except (OSError, ValueError, TypeError):
        pass
    return candidates


def _saved_hash(dashboard: dict) -> tuple[str, str]:
    """Mirror the installed SDK's PBKDF2-first, legacy-MD5 selection.

    A syntactically parseable but excessive PBKDF2 hash is *not* downgraded to a
    possibly stale MD5 field. We decline verification rather than guess.
    """
    modern = dashboard.get("pbkdf2_password")
    if isinstance(modern, str) and modern.startswith("pbkdf2_sha256$"):
        parts = modern.split("$")
        if len(parts) == 4:
            try:
                iterations = int(parts[1])
                salt_bytes, digest = bytes.fromhex(parts[2]), bytes.fromhex(parts[3])
            except (ValueError, TypeError):
                pass  # SDK falls back to MD5 when this syntax is unusable.
            else:
                if not 1 <= iterations <= 2_000_000 or not 1 <= len(salt_bytes) <= 64 or len(digest) != 32:
                    return "", ""
                return "pbkdf2_sha256", modern
    legacy = dashboard.get("password")
    if isinstance(legacy, str) and re.fullmatch(r"[0-9a-fA-F]{32}", legacy):
        return "md5", legacy
    return "", ""


def _matches(kind: str, stored: str, candidate: str) -> bool:
    if kind == "md5":
        return hmac.compare_digest(hashlib.md5(candidate.encode("utf-8")).hexdigest(), stored.lower())
    if kind == "pbkdf2_sha256":
        _, count, salt, digest = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), bytes.fromhex(salt), int(count))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    return False


def _field(identifier: str, label: str, value: str, *, sensitive: bool) -> dict:
    return {"id": identifier, "label": label, "value": value, "sensitive": sensitive, "copyable": True}


def _astrbot() -> dict:
    result = {"target": "astrbot", "title": "AstrBot 登录凭据", "login_url": f"http://127.0.0.1:{ASTRBOT_PORT}/",
              "auth_type": "username_password", "status": "unavailable", "verification": "unavailable",
              "fields": [], "warnings": []}
    try:
        dashboard = _json(ASTRBOT / "data/cmd_config.json", ASTRBOT).get("dashboard")
        if not isinstance(dashboard, dict):
            raise ValueError("Dashboard configuration is missing")
        username = _string(dashboard.get("username"), 256)
        if not username:
            raise ValueError("Dashboard username is missing")
    except (OSError, ValueError, TypeError, UnicodeError):
        result["warnings"].append("暂时无法读取 AstrBot 的后台账号配置，请先完成安装并启动一次服务。")
        return result
    result["fields"].append(_field("username", "登录账号", username, sensitive=False))
    kind, saved = _saved_hash(dashboard)
    candidates = _initial_candidates() if kind else []
    if not kind:
        result["warnings"].append("当前密码存储格式无法安全核验，暂不展示未验证的密码。")
    elif not candidates:
        result["status"] = "initial_missing"
        result["warnings"].append("未找到可安全读取的初始密码；若你修改过密码，请使用自己设置的密码。")
    else:
        try:
            initial = next((candidate for candidate in candidates if _matches(kind, saved, candidate)), "")
        except (ValueError, TypeError, UnicodeError, OverflowError):
            initial = ""
        if initial:
            result.update(status="available", verification="matches_saved_configuration")
            result["fields"].append(_field("password", "初始密码（与当前配置匹配）", initial, sensitive=True))
            result["warnings"].append("已与本机保存的密码配置核对，未发起登录；若刚手动编辑配置，请以服务重载后的结果为准。")
        else:
            result.update(status="initial_expired", verification="expired")
            result["warnings"].append("保存的初始密码与当前配置不匹配，可能已被修改；请使用自己设置的密码，软件无法从哈希还原新密码。")
    if dashboard.get("enable") is False:
        result["status"] = "disabled"
        result["warnings"].append("AstrBot 后台当前配置为关闭，启用后才能登录。")
    if isinstance(dashboard.get("totp"), dict) and dashboard["totp"].get("enable") is True:
        result["warnings"].append("此后台已启用二步验证，登录时仍需你自己的动态验证码。")
    if dashboard.get("password_change_required") is True:
        result["warnings"].append("AstrBot 可能要求首次登录后修改初始密码。")
    return result


def _napcat() -> dict:
    result = {"target": "napcat", "title": "NapCat 登录凭据", "login_url": f"http://127.0.0.1:{NAPCAT_PORT}/",
              "auth_type": "token", "status": "unavailable", "verification": "unavailable", "fields": [],
              "warnings": ["NapCat WebUI 使用 Token 登录，没有单独的登录账号；这不是 QQ 密码。"]}
    try:
        config = _json(NAPCAT / "napcat/config/webui.json", NAPCAT)
        token = _string(config.get("token"), 4096)
    except (OSError, ValueError, TypeError, UnicodeError):
        result["warnings"].append("暂时无法读取 NapCat WebUI 的 Token，请先完成安装并启动一次服务。")
        return result
    if config.get("disableWebUI") is True:
        result["status"] = "disabled"
        result["warnings"].append("NapCat WebUI 当前配置为关闭，启用后才能登录。")
        return result
    if not token or token == "napcat":
        result["status"] = "initial_missing"
        result["warnings"].append("NapCat 尚未写入初始化后的 Token，请先启动一次服务。")
        return result
    result.update(status="available", verification="saved_configuration")
    result["fields"].append(_field("token", "WebUI Token", token, sensitive=True))
    result["warnings"].append("读取的是本机保存的 WebUI Token，未发起登录；手动编辑后需以服务重载后的值为准。")
    return result


def reveal(target: str) -> dict:
    """Return only the requested dashboard's allowlisted user login fields.

    Call solely from an explicitly requested same-origin POST. Never include
    these values in status/preview GETs, URL parameters, logs or release files.
    """
    if not isinstance(target, str) or target not in {"astrbot", "napcat"}:
        raise ValueError("请选择 AstrBot 或 NapCat 后台")
    return _astrbot() if target == "astrbot" else _napcat()
