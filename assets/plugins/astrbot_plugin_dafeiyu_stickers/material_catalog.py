"""Reviewed community images updated separately from executable plugin code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

try:
    from . import custom_catalog
except ImportError:  # Desktop loads this pure validator without AstrBot.
    import importlib.util
    _spec = importlib.util.spec_from_file_location("_aiwu_material_custom_catalog", Path(__file__).with_name("custom_catalog.py"))
    custom_catalog = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(custom_catalog)

MAX_MANIFEST = 512 * 1024
MAX_TOTAL = 150 * 1024 * 1024
GENERATION = re.compile(r"[a-f0-9]{64}\Z")


def safe_dir(root: Path):
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("社区素材目录无效")
    for item in (root.parent.parent, root.parent, root):
        if item.exists() and (item.is_symlink() or getattr(item.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("社区素材目录不能使用链接")
    return root


def plain(root: Path, relative: str, maximum: int):
    root = safe_dir(root)
    if relative != "manifest.json" and not re.fullmatch(r"(?:images/[a-f0-9]{64}\.(?:png|jpg|gif|webp)|licenses/[a-f0-9]{64}\.txt)", relative):
        raise ValueError("社区素材路径无效")
    path = root / storage_name(relative)
    for part in path.relative_to(root).parts:
        root = root / part
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("社区素材不能使用链接")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 1 <= info.st_size <= maximum:
        raise ValueError("社区素材文件不可用或大小超限")
    return path


def storage_name(relative):
    # Keep installed paths short enough for existing Windows runtime locations.
    # The public manifest/ZIP retain the full digest, and content verification
    # always compares all 256 bits. Short physical names never select content.
    if relative == "manifest.json":
        return relative
    match = re.fullmatch(r"images/([a-f0-9]{64})(\.(?:png|jpg|gif|webp))", relative)
    if match:
        return "i/" + match[1][:24] + match[2]
    match = re.fullmatch(r"licenses/([a-f0-9]{64})\.txt", relative)
    if match:
        return "l/" + match[1][:24] + ".txt"
    raise ValueError("社区素材路径无效")


def validate_manifest(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", "version", "source", "stickers"}
            or value.get("schema_version") != 1 or not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", str(value.get("version", "")))
            or value.get("source") != "https://github.com/myflww/aiwu-jiyu"
            or not isinstance(value.get("stickers"), list) or not 1 <= len(value["stickers"]) <= 100):
        raise ValueError("社区素材清单格式无效")
    seen, total, result = set(), 0, []
    for item in value["stickers"]:
        allowed = {"id", "title", "emotion", "tags", "description", "use_when", "avoid_when", "attribution",
                   "format", "width", "height", "frames", "size", "sha256", "file", "license_file", "license_id", "source_pack", "source_url"}
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("社区素材条目无效")
        identifier, digest, kind = item.get("id"), item.get("sha256"), item.get("format")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier) or identifier in seen
                or not isinstance(digest, str) or not GENERATION.fullmatch(digest) or kind not in custom_catalog.FORMATS
                or item.get("file") != f"images/{digest}.{custom_catalog.FORMATS[kind][0]}"
                or item.get("license_file") != f"licenses/{digest}.txt"
                or item.get("source_pack") not in {"original", "community", "contributed"}
                or item.get("license_id") not in {"CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "PolyForm-Noncommercial-1.0.0", "LicenseRef-Nekowhale-Noncommercial"}
                or not isinstance(item.get("source_url"), str) or len(item["source_url"]) > 500):
            raise ValueError("社区素材标识、路径或许可无效")
        source = urlsplit(item["source_url"])
        if source.scheme != "https" or source.hostname not in {"github.com", "kiriyuki.com"} or source.username or source.password:
            raise ValueError("社区素材来源链接无效")
        facts = {key: item.get(key) for key in ("width", "height", "frames", "size")}
        if (any(type(v) is not int for v in facts.values()) or not 24 <= facts["size"] <= custom_catalog.MAX_FILE_BYTES
                or not 1 <= facts["width"] <= custom_catalog.MAX_SIDE or not 1 <= facts["height"] <= custom_catalog.MAX_SIDE
                or not 1 <= facts["frames"] <= custom_catalog.MAX_FRAMES
                or facts["width"] * facts["height"] * facts["frames"] > custom_catalog.MAX_DECODED_PIXELS):
            raise ValueError("社区素材容量或尺寸超限")
        meta = custom_catalog.metadata({**item, "enabled": True})
        if not meta["attribution"]:
            raise ValueError("社区素材必须保留署名")
        result.append({**item, **meta, "label": meta["title"], "aliases": meta["tags"], "pack_id": item["source_pack"]})
        seen.add(identifier)
        total += facts["size"]
    if total > MAX_TOTAL:
        raise ValueError("社区素材总容量超限")
    return {**value, "stickers": result}


def active(root: Path):
    root = safe_dir(root)
    pointer = root / "active.json"
    if not pointer.exists():
        return None
    info = pointer.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 4096):
        raise ValueError("社区素材指针无效")
    value = json.loads(pointer.read_text("utf-8"))
    if not isinstance(value, dict) or not GENERATION.fullmatch(str(value.get("generation", ""))):
        raise ValueError("社区素材版本标识无效")
    slot = value.get("slot", value["generation"][:16])
    if not isinstance(slot, str) or not re.fullmatch(value["generation"][:16] + r"(?:-[a-f0-9]{8})?", slot):
        raise ValueError("社区素材存储版本无效")
    folder = safe_dir(root / "packs" / slot)
    manifest = validate_manifest(json.loads(plain(folder, "manifest.json", MAX_MANIFEST).read_text("utf-8")))
    if manifest["version"] != value.get("version"):
        raise ValueError("社区素材版本与清单不一致")
    return {"generation": value["generation"], "version": manifest["version"], "folder": folder,
            "stickers": [{**row, "material_generation": value["generation"]} for row in manifest["stickers"]]}


def verify(item, root: Path):
    current = active(root)
    if current is None or current["generation"] != item.get("material_generation"):
        raise ValueError("社区素材已更新，请重新选图")
    row = next((row for row in current["stickers"] if row["id"] == item.get("id")), None)
    if row is None or row["sha256"] != item.get("sha256"):
        raise ValueError("社区素材条目已变化")
    path = plain(current["folder"], row["file"], custom_catalog.MAX_FILE_BYTES)
    content = path.read_bytes()
    facts, _, _, _ = custom_catalog._decode(content)
    if hashlib.sha256(content).hexdigest() != row["sha256"] or any(facts[k] != row[k] for k in facts) or len(content) != row["size"]:
        raise ValueError("社区素材校验失败")
    plain(current["folder"], row["license_file"], 32 * 1024)
    return path
