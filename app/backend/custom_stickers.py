"""Private local sticker CRUD and explicit, path-free sharing exports."""
from __future__ import annotations

import base64
import binascii
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading

from PIL import Image  # Keep Pillow's frozen-application decoder hook visible.
from backend.paths import ASSETS, ASTRBOT

PLUGIN_ID = "astrbot_plugin_dafeiyu_stickers"
_lock = threading.RLock()


@lru_cache(maxsize=1)
def _module():
    path = ASSETS / "plugins" / PLUGIN_ID / "custom_catalog.py"
    spec = importlib.util.spec_from_file_location("_aiwu_private_sticker_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def data_root() -> Path:
    return ASTRBOT / "data/plugin_data" / PLUGIN_ID / "custom"


def _row(item: dict) -> dict:
    url = "/api/stickers/custom/" + item["id"] + "/image"
    fields = ("id", "title", "emotion", "tags", "description", "use_when", "avoid_when", "attribution", "enabled",
              "format", "width", "height", "frames", "size")
    return {**{key: item[key] for key in fields}, "label": item["title"], "aliases": list(item["tags"]),
            "pack_id": "custom", "url": url, "path": url}


def list_items() -> list[dict]:
    with _lock:
        return [_row(item) for item in _module().read_catalog(data_root())]


def _save(items: list[dict]) -> None:
    module, root = _module(), data_root()
    module.safe_root(root, create=True)
    path = root / "catalog.json"
    if path.exists() or path.is_symlink():
        module.plain_file(root, "catalog.json")
    with tempfile.NamedTemporaryFile(prefix=".catalog-", dir=root, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write((json.dumps({"schema_version": 1, "stickers": items}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    try:
        if temporary.stat().st_size > module.MAX_CATALOG_BYTES:
            raise ValueError("自定义表情清单容量已满")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _find(identifier, items):
    if not isinstance(identifier, str) or not _module().ID_PATTERN.fullmatch(identifier):
        raise ValueError("自定义表情标识无效")
    item = next((item for item in items if item["id"] == identifier), None)
    if item is None:
        raise ValueError("这张自定义表情不存在")
    return item


def import_item(body: dict) -> dict:
    module = _module()
    if not isinstance(body, dict) or set(body) - module.METADATA_FIELDS - {"image_base64"}:
        raise ValueError("自定义表情上传字段无效")
    encoded = body.get("image_base64")
    if not isinstance(encoded, str) or not 32 <= len(encoded) <= ((module.MAX_FILE_BYTES + 2) // 3) * 4:
        raise ValueError("请选择不超过8 MiB的本地图片")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("图片编码无效，仅接收本地文件内容") from None
    meta = module.metadata(body)
    cleaned, facts = module.sanitize_image(content)
    digest, extension = facts["sha256"], module.FORMATS[facts["format"]][0]
    entry = {**meta, **facts, "id": "custom-" + digest[:24], "file": "images/" + digest + "." + extension,
             "label": meta["title"], "aliases": meta["tags"], "pack_id": "custom"}
    with _lock:
        root = module.safe_root(data_root(), create=True)
        items = module.read_catalog(root)
        existing = next((item for item in items if item["id"] == entry["id"]), None)
        remaining = [item for item in items if item["id"] != entry["id"]]
        if len(remaining) >= module.MAX_ITEMS or sum(item["size"] for item in remaining) + facts["size"] > module.MAX_TOTAL_BYTES:
            raise ValueError("自定义图库最多100张、总容量150 MiB，请先移除部分素材")
        if existing and existing["sha256"] != digest:
            raise ValueError("图片标识冲突，请更换素材")
        target = root / entry["file"]
        created_identity = None
        try:
            if target.exists() or target.is_symlink():
                path = module.plain_file(root, entry["file"])
                if path.read_bytes() != cleaned:
                    raise ValueError("已有素材文件校验失败，请先修复或移除")
            else:
                # Exclusive creation never follows a link or overwrites user data.
                with target.open("xb") as stream:
                    info = os.fstat(stream.fileno())
                    created_identity = (info.st_dev, info.st_ino)
                    stream.write(cleaned)
            _save([*remaining, entry])
        except BaseException:
            # A full catalog or failed atomic write must not leave uncounted
            # image files. Never remove a pre-existing file or a replacement
            # created by another process after our exclusive creation.
            if created_identity is not None:
                try:
                    committed = any(item["id"] == entry["id"] for item in module.read_catalog(root))
                    path = module.plain_file(root, entry["file"])
                    info = path.stat()
                    if not committed and (info.st_dev, info.st_ino) == created_identity:
                        path.unlink()
                except (OSError, ValueError):
                    pass
            raise
    return _row(entry)


def update_item(identifier: str, body: dict) -> dict:
    module = _module()
    if not isinstance(body, dict) or not body or set(body) - module.METADATA_FIELDS:
        raise ValueError("自定义表情设置字段无效")
    with _lock:
        items = module.read_catalog(data_root())
        current = _find(identifier, items)
        target = {**current, **module.metadata({**current, **body})}
        target.update(label=target["title"], aliases=target["tags"])
        _save([target if item["id"] == identifier else item for item in items])
        return _row(target)


def delete_item(identifier: str) -> dict:
    with _lock:
        module, root = _module(), data_root()
        items = module.read_catalog(root)
        current = _find(identifier, items)
        path = module.plain_file(root, current["file"])
        _save([item for item in items if item["id"] != identifier])
        path.unlink()
    return {"ok": True, "deleted": identifier}


def asset(identifier: str) -> tuple[Path, str]:
    with _lock:
        module, root = _module(), data_root()
        current = _find(identifier, module.read_catalog(root))
        path = module.verify_asset(current, root, require_enabled=False)
        return path, module.FORMATS[current["format"]][1]


def export_item(identifier: str) -> dict:
    """Only an explicitly selected image and editable public metadata.

    This creates no files and performs no upload. The submission UI separately
    asks the user for redistribution rights and a public sharing licence.
    """
    with _lock:
        module, root = _module(), data_root()
        current = _find(identifier, module.read_catalog(root))
        path = module.verify_asset(current, root, require_enabled=False)
        content, facts = module.sanitize_image(path.read_bytes())
        extension, mime = module.FORMATS[facts["format"]]
        public = {key: current[key] for key in sorted(module.METADATA_FIELDS - {"enabled"})}
        return {"schema_version": 1, "metadata": public,
                "image": {"filename": "sticker-" + facts["sha256"][:24] + "." + extension,
                          "mime": mime, "sha256": facts["sha256"], "width": facts["width"], "height": facts["height"],
                          "frames": facts["frames"], "size": len(content), "base64": base64.b64encode(content).decode("ascii")}}
