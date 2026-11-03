"""Fixed original PNGs and an attributed, bounded local community GIF pack."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import struct

IDS = ("happy", "sad", "angry", "shy", "sleepy", "love")
EMOTIONS = IDS + ("confused", "daily")
PACK_DIR = Path(__file__).resolve().parent / "pack"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_COMMUNITY_FILES = 24
MAX_COMMUNITY_BYTES = 30 * 1024 * 1024
COMMUNITY_PREFIX = "vendor/nekowhale/pack/"
PACK_SELECTIONS = ("all", "original", "community")


def _plain_file(path: Path, root: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("贴纸资源超出固定目录") from None
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("贴纸资源路径无效")
    candidates = [root]
    for part in relative.parts:
        candidates.append(candidates[-1] / part)
    for candidate in candidates:
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("贴纸资源不能通过链接读取")
    if not path.is_file() or path.stat().st_nlink != 1 or root.resolve() not in path.resolve().parents:
        raise ValueError("贴纸资源不可用")
    return path


def load_catalog(root: Path = PACK_DIR, *, include_community: bool = True) -> dict:
    root = Path(root)
    path = _plain_file(root / "catalog.json", root)
    if path.stat().st_size > 64 * 1024:
        raise ValueError("贴纸清单过大")
    data = json.loads(path.read_text("utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("stickers"), list):
        raise ValueError("贴纸清单格式无效")
    entries = data["stickers"]
    if len(entries) != len(IDS) or {item.get("id") for item in entries} != set(IDS):
        raise ValueError("贴纸清单必须包含固定的六种心情")
    for item in entries:
        if item.get("file") != item["id"] + ".png" or item.get("emotion") != item["id"]:
            raise ValueError("贴纸路径或心情无效")
        if not isinstance(item.get("label"), str) or not 1 <= len(item["label"]) <= 20:
            raise ValueError("贴纸名称无效")
        if not isinstance(item.get("aliases"), list) or not all(
                isinstance(alias, str) and 1 <= len(alias) <= 30 for alias in item["aliases"]):
            raise ValueError("贴纸别名无效")
        verify_asset(item, root)
        item["pack_id"] = "original"
    data["packs"] = [{"id": "original", "name": data.get("pack_name", "大肥鱼心情贴纸"),
                      "count": len(entries), "license": data.get("license", ""),
                      "attribution": data.get("attribution", "myflww · 爱屋及鱼"),
                      "source_url": "https://github.com/myflww/aiwu-jiyu"}]
    community = root.parent / "vendor/nekowhale/community-catalog.json"
    if include_community and community.exists():
        community = _plain_file(community, root.parent)
        if community.stat().st_size > 128 * 1024:
            raise ValueError("社区贴纸清单过大")
        extra = json.loads(community.read_text("utf-8"))
        rows = extra.get("stickers")
        if extra.get("schema_version") != 1 or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_COMMUNITY_FILES:
            raise ValueError("社区贴纸清单无效")
        seen, total = set(IDS), 0
        for item in rows:
            identifier = item.get("id", "")
            if not isinstance(identifier, str) or identifier in seen or not re.fullmatch(r"nekowhale-[A-Za-z0-9_-]{1,40}", identifier):
                raise ValueError("社区贴纸标识无效")
            seen.add(identifier)
            if item.get("emotion") not in EMOTIONS or not isinstance(item.get("label"), str) or not 1 <= len(item["label"]) <= 60:
                raise ValueError("社区贴纸心情或名称无效")
            if not isinstance(item.get("aliases"), list) or len(item["aliases"]) > 30 or not all(
                    isinstance(alias, str) and 1 <= len(alias) <= 50 for alias in item["aliases"]):
                raise ValueError("社区贴纸别名无效")
            if item.get("format") != "GIF":
                raise ValueError("社区贴纸仅支持随附 GIF")
            item["pack_id"] = "community"
            verify_asset(item, root)
            total += item["size"]
        if total > MAX_COMMUNITY_BYTES:
            raise ValueError("社区贴纸包过大")
        attribution = extra.get("attribution")
        license_info = extra.get("license")
        if not isinstance(attribution, dict) or not attribution.get("author") or not attribution.get("website") or not license_info:
            raise ValueError("社区贴纸缺少来源署名或许可")
        data["packs"].append({"id": "community", "name": extra.get("pack_name", "大肥鱼社区精选"),
            "count": len(rows), "license": license_info, "attribution": attribution,
            "source_url": extra.get("source_url", ""), "source_commit": extra.get("source_commit", "")})
        entries.extend(rows)
    return data


def verify_asset(item: dict, root: Path = PACK_DIR) -> Path:
    identifier = item.get("id")
    root = Path(root)
    community = item.get("pack_id") == "community"
    if community:
        filename = item.get("file")
        if (not isinstance(identifier, str) or not re.fullmatch(r"nekowhale-[A-Za-z0-9_-]{1,40}", identifier)
                or not isinstance(filename, str)
                or not re.fullmatch(r"vendor/nekowhale/pack/[A-Za-z0-9_-]{1,48}\.gif", filename)
                or item.get("format") != "GIF"):
            raise ValueError("仅支持随附的社区 GIF")
        path = _plain_file(root.parent / filename, root.parent)
    else:
        if identifier not in IDS or item.get("file") != identifier + ".png":
            raise ValueError("仅支持随附的大肥鱼贴纸")
        path = _plain_file(root / (identifier + ".png"), root)
    size = path.stat().st_size
    if not 24 <= size <= MAX_FILE_BYTES or size != item.get("size"):
        raise ValueError("贴纸大小校验失败")
    content = path.read_bytes()
    width, height = item.get("width"), item.get("height")
    if (isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int)
            or not isinstance(height, int) or not 1 <= width <= 2048 or not 1 <= height <= 2048):
        raise ValueError("贴纸尺寸超出上限")
    if community:
        image_ok = content[:6] in (b"GIF87a", b"GIF89a") and struct.unpack("<HH", content[6:10]) == (width, height)
        if isinstance(item.get("frames"), bool) or not isinstance(item.get("frames"), int) or not 1 <= item["frames"] <= 2000:
            raise ValueError("社区 GIF 帧数无效")
    else:
        image_ok = (content[:8] == b"\x89PNG\r\n\x1a\n" and content[12:16] == b"IHDR"
                    and struct.unpack(">II", content[16:24]) == (width, height))
    if not image_ok or hashlib.sha256(content).hexdigest() != item.get("sha256"):
        raise ValueError("贴纸内容校验失败")
    return path


def choose(emotion: str, catalog: dict, *, pack_selection: str = "all", avoid_id: str = "", index: int = 0) -> dict | None:
    if not isinstance(emotion, str) or len(emotion) > 50 or pack_selection not in PACK_SELECTIONS:
        return None
    value = emotion.strip().casefold()
    items = [item for item in catalog["stickers"] if pack_selection == "all" or item.get("pack_id", "original") == pack_selection]
    exact = next((item for item in items if item["id"] == value and value not in EMOTIONS), None)
    if exact:
        return exact
    canonical = value if value in EMOTIONS else next((item["emotion"] for item in items if value in {
        item["label"].casefold(), *[alias.casefold() for alias in item["aliases"]]}), "")
    matches = [item for item in items if item["emotion"] == canonical]
    if not matches:
        return None
    candidate = matches[index % len(matches)]
    if len(matches) > 1 and candidate["id"] == avoid_id:
        candidate = matches[(index + 1) % len(matches)]
    return candidate
