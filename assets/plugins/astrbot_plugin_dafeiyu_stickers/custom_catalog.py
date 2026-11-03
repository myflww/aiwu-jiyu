"""Bounded private sticker metadata and decoded raster validation.

No network, configuration, platform or model access. Desktop imports this
module too so imported files and the runtime reader share one contract.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import stat
import warnings

EMOTIONS = ("happy", "sad", "angry", "shy", "sleepy", "love", "confused", "daily")
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 100
MAX_TOTAL_BYTES = 150 * 1024 * 1024
MAX_SIDE = 2048
MAX_FRAMES = 200
MAX_DECODED_PIXELS = 40_000_000
MAX_CATALOG_BYTES = 256 * 1024
FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
           "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}
METADATA_FIELDS = {"title", "emotion", "tags", "description", "use_when", "avoid_when", "attribution", "enabled"}
ID_PATTERN = re.compile(r"custom-[a-f0-9]{24}")


def _text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError("表情文字字段无效或过长")
    value = value.strip()
    if required and not value:
        raise ValueError("请填写表情标题")
    return value


def metadata(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("自定义表情信息格式无效")
    emotion = value.get("emotion", "daily")
    if emotion not in EMOTIONS:
        raise ValueError("请选择有效的表情心情")
    tags = value.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 12:
        raise ValueError("表情标签最多12个")
    tags = list(dict.fromkeys(_text(tag, 24, required=True) for tag in tags))
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("素材开关必须是布尔值")
    return {"title": _text(value.get("title", ""), 60, required=True), "emotion": emotion, "tags": tags,
            "description": _text(value.get("description", ""), 240),
            "use_when": _text(value.get("use_when", ""), 160), "avoid_when": _text(value.get("avoid_when", ""), 160),
            "attribution": _text(value.get("attribution", ""), 160), "enabled": enabled}


def _sniff(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if content.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "WEBP"
    raise ValueError("仅支持真实的 PNG、JPEG、WebP 或 GIF 图片")


def _decode(content: bytes, *, keep_frames=False):
    from PIL import Image, ImageOps
    if not isinstance(content, bytes) or not 24 <= len(content) <= MAX_FILE_BYTES:
        raise ValueError("单张表情不能超过8 MiB")
    expected = _sniff(content)
    images, durations, decoded_pixels = [], [], 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != expected:
                    raise ValueError("图片实际格式与文件头不一致")
                frame_count = getattr(image, "n_frames", 1)
                if not 1 <= frame_count <= MAX_FRAMES:
                    raise ValueError("动画最多支持200帧")
                loop = image.info.get("loop", 0)
                loop = max(0, min(65535, loop)) if isinstance(loop, int) and not isinstance(loop, bool) else 0
                dimensions = None
                for index in range(frame_count):
                    image.seek(index)
                    width, height = image.size
                    decoded_pixels += width * height
                    if (not 1 <= width <= MAX_SIDE or not 1 <= height <= MAX_SIDE
                            or decoded_pixels > MAX_DECODED_PIXELS):
                        raise ValueError("图片边长最多2048像素，动画总解码像素最多4000万")
                    image.load()  # Reject truncated/corrupt payloads, not just header claims.
                    if keep_frames:
                        frame = ImageOps.exif_transpose(image.copy()).convert("RGB" if expected == "JPEG" else "RGBA")
                        frame.info.clear()  # Do not retain EXIF/GPS, text chunks or private metadata.
                        images.append(frame)
                        dimensions = frame.size
                    elif dimensions is None:
                        dimensions = image.size
                    duration = image.info.get("duration", 100)
                    durations.append(max(20, min(10000, int(duration))) if isinstance(duration, (int, float)) else 100)
                facts = {"format": expected, "width": dimensions[0], "height": dimensions[1], "frames": frame_count}
                return facts, images, durations, loop
    except (OSError, SyntaxError, EOFError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("图片内容损坏或解码超出限制") from exc


def sanitize_image(content: bytes) -> tuple[bytes, dict]:
    facts, frames, durations, loop = _decode(content, keep_frames=True)
    output = io.BytesIO()
    options = {}
    if len(frames) > 1:
        options.update(save_all=True, append_images=frames[1:], duration=durations, loop=loop)
    if facts["format"] == "JPEG":
        options.update(quality=95)
    if facts["format"] == "WEBP":
        options.update(lossless=True)
    if facts["format"] == "GIF":
        options.update(disposal=2)
    frames[0].save(output, format=facts["format"], **options)
    cleaned = output.getvalue()
    if len(cleaned) > MAX_FILE_BYTES:
        raise ValueError("清理图片元数据后仍超过8 MiB，请缩小图片")
    # GIF encoders may combine identical frames. Record the stored result.
    facts, _, _, _ = _decode(cleaned)
    return cleaned, {**facts, "size": len(cleaned), "sha256": hashlib.sha256(cleaned).hexdigest()}


def safe_root(root: Path, *, create=False) -> Path:
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("自定义表情数据目录无效")
    # The three fixed private storage components may not redirect outside the
    # configured runtime through symlinks, junctions or other reparse points.
    for candidate in (root.parent.parent, root.parent, root, root / "images"):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("自定义表情目录不能使用链接")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("自定义表情目录不可用")
    if create:
        (root / "images").mkdir(parents=True, exist_ok=True)
    return root


def plain_file(root: Path, relative: str) -> Path:
    root = safe_root(root)
    if relative != "catalog.json" and not re.fullmatch(r"images/[a-f0-9]{64}\.(?:png|jpg|webp|gif)", relative):
        raise ValueError("自定义表情路径无效")
    path = root / relative
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_nlink != 1
            or root.resolve() not in path.resolve().parents):
        raise ValueError("自定义表情文件不能使用链接")
    return path


def read_catalog(root: Path) -> list[dict]:
    root = safe_root(root)
    if not (root / "catalog.json").exists() and not (root / "catalog.json").is_symlink():
        return []
    path = plain_file(root, "catalog.json")
    if path.stat().st_size > MAX_CATALOG_BYTES:
        raise ValueError("自定义表情清单过大")
    value = json.loads(path.read_text("utf-8"))
    rows = value.get("stickers") if isinstance(value, dict) else None
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(rows, list) or len(rows) > MAX_ITEMS:
        raise ValueError("自定义表情清单格式无效")
    result, seen, total = [], set(), 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("自定义表情条目无效")
        digest, image_format = row.get("sha256"), row.get("format")
        if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or not isinstance(image_format, str) or image_format not in FORMATS):
            raise ValueError("自定义表情校验信息无效")
        identifier = "custom-" + digest[:24]
        filename = "images/" + digest + "." + FORMATS[image_format][0]
        if row.get("id") != identifier or identifier in seen or row.get("file") != filename:
            raise ValueError("自定义表情标识或路径无效")
        facts = {key: row.get(key) for key in ("size", "width", "height", "frames")}
        if any(isinstance(v, bool) or not isinstance(v, int) for v in facts.values()):
            raise ValueError("自定义表情尺寸信息无效")
        if (not 24 <= facts["size"] <= MAX_FILE_BYTES or not 1 <= facts["width"] <= MAX_SIDE
                or not 1 <= facts["height"] <= MAX_SIDE or not 1 <= facts["frames"] <= MAX_FRAMES
                or facts["width"] * facts["height"] * facts["frames"] > MAX_DECODED_PIXELS):
            raise ValueError("自定义表情尺寸或容量超出限制")
        if plain_file(root, filename).stat().st_size != facts["size"]:
            raise ValueError("自定义表情文件大小不匹配")
        meta = metadata(row)
        result.append({**meta, **facts, "id": identifier, "file": filename, "format": image_format,
                       "sha256": digest, "label": meta["title"], "aliases": meta["tags"], "pack_id": "custom"})
        seen.add(identifier)
        total += facts["size"]
    if total > MAX_TOTAL_BYTES:
        raise ValueError("自定义图库总容量不能超过150 MiB")
    return result


def verify_asset(item: dict, root: Path, *, require_enabled=True) -> Path:
    # Re-read the manifest so an asset disabled/deleted during a model request
    # cannot be sent from a stale candidate cache.
    current = next((row for row in read_catalog(root) if row["id"] == item.get("id")), None)
    if not current or (require_enabled and not current["enabled"]) or current["sha256"] != item.get("sha256"):
        raise ValueError("这张自定义表情已停用或移除")
    path = plain_file(root, current["file"])
    content = path.read_bytes()
    facts, _, _, _ = _decode(content)
    if hashlib.sha256(content).hexdigest() != current["sha256"] or any(facts[key] != current[key] for key in facts):
        raise ValueError("自定义表情内容校验失败")
    return path


def _submission_file(folder: Path, name: str) -> Path:
    if name not in {"metadata.json", "LICENSE.txt", "image.png", "image.jpg", "image.webp", "image.gif"}:
        raise ValueError("投稿素材文件名无效")
    safe_root(folder)
    path = folder / name
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400 or folder.resolve() not in path.resolve().parents):
        raise ValueError("投稿素材不能通过链接读取")
    return path


def _submission(folder: Path) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32,64}", folder.name):
        raise ValueError("投稿素材目录标识无效")
    meta_path = _submission_file(folder, "metadata.json")
    licence_path = _submission_file(folder, "LICENSE.txt")
    if not 1 <= meta_path.stat().st_size <= 16 * 1024 or not 1 <= licence_path.stat().st_size <= 32 * 1024:
        raise ValueError("投稿信息或许可文件大小无效")
    value = json.loads(meta_path.read_text("utf-8-sig"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("license") != "CC-BY-NC-4.0" or not isinstance(value.get("image"), dict)):
        raise ValueError("投稿素材信息或许可无效")
    image = value["image"]
    image_format = next((kind for kind, (extension, mime) in FORMATS.items()
                         if image.get("file") == "image." + extension and image.get("mime") == mime), None)
    if image_format is None:
        raise ValueError("投稿图片格式无效")
    path = _submission_file(folder, image["file"])
    if not 24 <= path.stat().st_size <= MAX_FILE_BYTES:
        raise ValueError("投稿图片超过大小限制")
    content = path.read_bytes()
    facts, _, _, _ = _decode(content)
    digest = hashlib.sha256(content).hexdigest()
    if (digest != image.get("sha256") or facts["format"] != image_format
            or any(isinstance(image.get(key), bool) or not isinstance(image.get(key), int)
                   or image.get(key) != facts[key] for key in ("width", "height", "frames"))):
        raise ValueError("投稿图片校验失败")
    meta = metadata({**value, "enabled": True})
    return {**meta, **facts, "size": len(content), "sha256": digest, "id": "contrib-" + folder.name[:24],
            "submission": folder.name, "file": image["file"], "label": meta["title"], "aliases": meta["tags"],
            "pack_id": "contributed", "license": "CC-BY-NC-4.0", "source_url": "https://github.com/myflww/aiwu-jiyu"}


def read_contributions(root: Path) -> list[dict]:
    root = safe_root(root)
    if not root.exists():
        return []
    folders = [path for path in sorted(root.iterdir()) if path.is_dir() or path.is_symlink()]
    if len(folders) > MAX_ITEMS:
        raise ValueError("随附投稿素材数量超出限制")
    items = [_submission(folder) for folder in folders]
    if len({row["id"] for row in items}) != len(items) or sum(row["size"] for row in items) > MAX_TOTAL_BYTES:
        raise ValueError("随附投稿素材标识或容量无效")
    return items


def verify_contributed_asset(item: dict, root: Path) -> Path:
    folder = item.get("submission")
    if not isinstance(folder, str) or not re.fullmatch(r"[a-f0-9]{32,64}", folder):
        raise ValueError("投稿素材标识无效")
    root = safe_root(root)
    current = _submission(root / folder)
    if current["id"] != item.get("id") or current["sha256"] != item.get("sha256"):
        raise ValueError("投稿素材内容已变化")
    return _submission_file(root / folder, current["file"])
