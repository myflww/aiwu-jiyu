"""Small shared configuration contract; no platform or network side effects."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

DEFAULTS = {"search_enabled": False, "search_provider": "bing_rss", "searxng_url": "",
            "profile_enabled": False, "qzone_enabled": False}
IDS = ("happy", "sad", "angry", "shy", "sleepy", "love")
LABELS = {"happy": "开心", "sad": "委屈", "angry": "气鼓鼓", "shy": "害羞", "sleepy": "晚安", "love": "比心"}
QZONE_URL = "https://qzone.qq.com/"
DOCS_URL = "https://napneko.github.io/api/4.18.42"
# Factual protocol identifiers from the installed 4.18.42 embedded
# face_config QSid/QDes rows, not legacy QQ message-face indexes.
STATUS_ICONS = {
    "smile": {"label": "微笑", "face_id": "14", "face_type": "1"},
    "focus": {"label": "奋斗", "face_id": "30", "face_type": "1"},
    "rest": {"label": "睡觉", "face_id": "8", "face_type": "1"},
    "coffee": {"label": "咖啡", "face_id": "60", "face_type": "1"},
}


def public_https(value, *, endpoint=False):
    if not isinstance(value, str) or len(value) > 1000 or re.search(r"[\x00-\x20\\]", value):
        raise ValueError("请使用公开 HTTPS 地址，不包含账号密码、查询参数或片段")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or host == "localhost" or "." not in host
                or host.endswith((".local", ".localhost", ".internal"))):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        if endpoint and (parsed.query or parsed.fragment):
            raise ValueError
        if endpoint:
            path = parsed.path.rstrip("/")
            if not path.endswith("/search"):
                path += "/search"
            return urlunsplit(("https", parsed.netloc.lower(), path, "", ""))
        return value
    except (ValueError, TypeError):
        raise ValueError("请使用公开 HTTPS 地址，不包含账号密码；本机或内网地址暂不支持") from None


def normalize(raw=None):
    raw = raw if isinstance(raw, dict) else {}
    result = dict(DEFAULTS)
    for key in ("search_enabled", "profile_enabled", "qzone_enabled"):
        result[key] = raw.get(key, False) is True
    provider = raw.get("search_provider")
    result["search_provider"] = provider if isinstance(provider, str) and provider in {"bing_rss", "searxng"} else "bing_rss"
    value = raw.get("searxng_url", "")
    if isinstance(value, str) and value.strip():
        try:
            result["searxng_url"] = public_https(value.strip(), endpoint=True)
        except ValueError:
            pass
    return result


def validate(body):
    if not isinstance(body, dict) or set(body) - set(DEFAULTS):
        raise ValueError("不支持的联网与账号设置字段")
    for key in ("search_enabled", "profile_enabled", "qzone_enabled"):
        if key in body and type(body[key]) is not bool:
            raise ValueError("能力开关须为布尔值")
    if "search_provider" in body and (not isinstance(body["search_provider"], str) or body["search_provider"] not in {"bing_rss", "searxng"}):
        raise ValueError("请选择 Bing RSS 或 SearXNG")
    result = dict(body)
    if "searxng_url" in body:
        if not isinstance(body["searxng_url"], str):
            raise ValueError("搜索服务地址须为文字")
        result["searxng_url"] = public_https(body["searxng_url"].strip(), endpoint=True) if body["searxng_url"].strip() else ""
    return result


def identity(config):
    owner, bot = str(config.get("owner_qq") or ""), str(config.get("bot_qq") or "")
    if not re.fullmatch(r"[1-9][0-9]{4,19}", owner) or not re.fullmatch(r"[1-9][0-9]{4,19}", bot) or owner == bot:
        raise ValueError("请先在爱屋及鱼配置不同的主人 QQ 和机器人 QQ；只管理机器人小号")
    return owner, bot
