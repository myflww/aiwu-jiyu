"""Explicit official-release, data-only community material updates.

The GitHub release's SHA-256 digest authenticates the manifest and ZIP over
TLS; each raster and its preserved license are validated before an atomic
active-generation switch. No downloaded source code is imported or executed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

from backend import custom_stickers, stickers
from backend.paths import ASSETS

_lock = threading.RLock()
_pending = None
_busy = False
MAX_ZIP = 64 * 1024 * 1024
RELEASES = "https://api.github.com/repos/myflww/aiwu-jiyu/releases/latest"


def policy():
    return custom_stickers._material_module()


def root():
    return custom_stickers.data_root().parent / "materials"


def _download(url, maximum):
    # GitHub release asset redirects are required. Authorization is never sent,
    # and destination URLs must use GitHub's release-content host allowlist.
    parsed = urlsplit(url)
    allowed = {"api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
        raise ValueError("素材下载地址不属于官方GitHub发布")
    try:
        with urlopen(Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "AiwuJiyu-Community-Materials"}), timeout=45) as response:
            final = urlsplit(response.url)
            if final.scheme != "https" or final.hostname not in allowed:
                raise ValueError("素材下载跳转地址无效")
            content = response.read(maximum + 1)
    except HTTPError as error:
        headers = error.headers or {}
        if error.code in {403, 429} and (headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After") is not None):
            raise ValueError("GitHub公共接口暂时限流，请稍后再检查") from None
        raise ValueError("暂时无法读取官方素材，请稍后重试") from None
    except (URLError, OSError):
        raise ValueError("暂时无法连接官方素材服务，请检查网络后稍后重试") from None
    if len(content) > maximum:
        raise ValueError("素材下载容量超限")
    return content


def _asset(release, name, limit):
    rows = [row for row in release.get("assets", []) if row.get("name") == name]
    if len(rows) != 1:
        raise ValueError("正式发布尚未提供独立社区素材包")
    row = rows[0]
    digest = row.get("digest", "")
    url = row.get("browser_download_url", "")
    tag = release["tag_name"]
    if (not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or type(row.get("size")) is not int
            or not 1 <= row["size"] <= limit or url != f"https://github.com/myflww/aiwu-jiyu/releases/download/{tag}/{name}"):
        raise ValueError("正式素材包校验信息无效")
    return row


def current():
    return policy().active(root())


def rows():
    value = current()
    if value is None:
        return []
    return [{**row, "url": f"/api/stickers/materials/image/{row['id']}",
             "path": f"/api/stickers/materials/image/{row['id']}"} for row in value["stickers"]]


def asset(identifier):
    value = current()
    if value is None:
        raise ValueError("尚未安装社区素材更新")
    row = next((row for row in value["stickers"] if row["id"] == identifier), None)
    if row is None:
        raise ValueError("社区素材不存在")
    path = policy().verify(row, root())
    return path, custom_stickers._module().FORMATS[row["format"]][1]


def status():
    warning = ""
    try:
        value = current()
    except (OSError, ValueError):
        value = None
        warning = "社区更新素材暂不可读；随附图库不受影响，可重新检查素材更新。"
    with _lock:
        pending = _pending
        return {"ok": True, "version": value["version"] if value else "随附素材",
                "latest_version": pending["manifest"]["version"] if pending else None,
                "available": bool(pending and pending["available"]), "updating": _busy,
                "fingerprint": pending["fingerprint"] if pending else None,
                "changes": pending["changes"] if pending else {"added": 0, "changed": 0, "removed": 0},
                "release_url": pending["release_url"] if pending else "https://github.com/myflww/aiwu-jiyu/releases",
                "message": warning or "仅更新图片、说明与许可，不替换软件或插件代码。"}


def _comparison_row(row):
    group = row.get("source_pack") or row.get("pack_id", "original")
    community = group == "community"
    return {"sha256": row["sha256"], "title": (row.get("title") or row.get("label", ""))[:60],
            "emotion": row["emotion"],
            "tags": list(dict.fromkeys(str(term)[:24] for term in row.get("tags", row.get("aliases", []))))[:12],
            "description": str(row.get("description") or row.get("caption") or "")[:240],
            "use_when": str(row.get("use_when") or row.get("use") or "")[:160],
            "avoid_when": str(row.get("avoid_when") or "")[:160],
            "attribution": row.get("attribution") or ("可以叫我云大人 / 雾雪 · https://space.bilibili.com/103739 · https://kiriyuki.com" if community else "myflww · 爱屋及鱼"),
            "source_pack": group, "source_url": row.get("source_url") or ("https://github.com/Loping151/dsh-nekowhale-stickers" if community else "https://github.com/myflww/aiwu-jiyu"),
            "license_id": row.get("license_id") or ("LicenseRef-Nekowhale-Noncommercial" if community else "CC-BY-NC-4.0" if group == "contributed" else "PolyForm-Noncommercial-1.0.0")}


def check():
    global _pending
    release = json.loads(_download(RELEASES, 1024 * 1024))
    if (not isinstance(release, dict) or release.get("draft") or release.get("prerelease")
            or not re.fullmatch(r"v[0-9][A-Za-z0-9._-]{0,60}", str(release.get("tag_name", "")))):
        raise ValueError("暂未找到有效正式素材发布")
    manifest_asset = _asset(release, "community-stickers-manifest.json", policy().MAX_MANIFEST)
    raw = _download(manifest_asset["browser_download_url"], policy().MAX_MANIFEST)
    if len(raw) != manifest_asset["size"] or "sha256:" + hashlib.sha256(raw).hexdigest() != manifest_asset["digest"]:
        raise ValueError("社区素材清单校验失败")
    manifest = policy().validate_manifest(json.loads(raw))
    archive_asset = _asset(release, f"community-stickers-{manifest['version']}.zip", MAX_ZIP)
    repair_needed = False
    try:
        active = current()
    except (OSError, ValueError):
        active = None
        repair_needed = True
    if active:
        try:
            for row in active["stickers"]:policy().verify(row, root())
        except (OSError, ValueError):
            repair_needed = True
    def version(text):return tuple(map(int, text.split(".")))
    if active and version(manifest["version"]) < version(active["version"]):
        raise ValueError("官方素材包版本低于已安装版本，未自动降级")
    # Initial comparison uses the exact shipped inventory, so the baseline
    # independent pack does not make users download the same pictures again.
    installed = active["stickers"] if active else stickers._catalog_module().load_catalog()["stickers"]
    old = {row["id"]: _comparison_row(row) for row in installed}
    new = {row["id"]: _comparison_row(row) for row in manifest["stickers"]}
    changes = {"added": len(new.keys() - old.keys()), "removed": len(old.keys() - new.keys()),
               "changed": sum(old[key] != new[key] for key in old.keys() & new.keys())}
    fingerprint = hashlib.sha256(raw + archive_asset["digest"].encode()).hexdigest()
    with _lock:
        if _busy:
            raise ValueError("社区素材正在更新，请稍后检查")
        _pending = {"manifest": manifest, "raw": raw, "asset": archive_asset, "fingerprint": fingerprint,
                    "changes": changes, "available": any(changes.values()) or repair_needed
                    or bool(active and version(manifest["version"]) > version(active["version"])),
                    "repair_needed": repair_needed,
                    "release_url": "https://github.com/myflww/aiwu-jiyu/releases/tag/" + release["tag_name"]}
    result = status()
    result["repair_needed"] = repair_needed
    result["preview"] = [{key: row[key] for key in ("id", "title", "attribution", "license_id", "source_url")} for row in manifest["stickers"]]
    return result


def _install(content, pending):
    """Validate every archive member and raster before any active state changes."""
    module = policy()
    manifest = pending["manifest"]
    folder_root = module.safe_dir(root())
    folder_root.mkdir(parents=True, exist_ok=True)
    packs = module.safe_dir(folder_root / "packs")
    packs.mkdir(exist_ok=True)
    generation = hashlib.sha256(content).hexdigest()
    target = module.safe_dir(packs / generation[:16])
    expected = {"manifest.json"} | {row[key] for row in manifest["stickers"] for key in ("file", "license_file")}
    if target.exists():
        # Repair into a new slot. The old active generation stays available
        # until every new image has passed validation and the pointer switches.
        target = module.safe_dir(packs / (generation[:16] + "-" + uuid.uuid4().hex[:8]))
    staging = Path(tempfile.mkdtemp(prefix=".material-", dir=packs))
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) != len(expected) or {info.filename for info in infos} != expected:
                raise ValueError("素材包必须只包含清单、已核对图片及许可")
            total = 0
            for info in infos:
                mode = info.external_attr >> 16
                maximum = module.MAX_MANIFEST if info.filename == "manifest.json" else (32 * 1024 if info.filename.startswith("licenses/") else custom_stickers._module().MAX_FILE_BYTES)
                total += info.file_size
                if (info.flag_bits & 1 or stat.S_ISLNK(mode) or info.is_dir()
                        or (stat.S_IFMT(mode) and not stat.S_ISREG(mode)) or not 1 <= info.file_size <= maximum
                        or total > module.MAX_TOTAL + 4 * 1024 * 1024
                        or info.file_size > max(1024 * 1024, info.compress_size * 200)):
                    raise ValueError("素材包条目容量、压缩比或类型无效")
                with archive.open(info) as stream:data = stream.read(maximum + 1)
                if len(data) != info.file_size:
                    raise ValueError("素材包条目大小校验失败")
                path = staging / module.storage_name(info.filename)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:stream.write(data)
        if (staging / "manifest.json").read_bytes() != pending["raw"]:
            raise ValueError("素材包内外清单不一致")
        for row in manifest["stickers"]:
            path = module.plain(staging, row["file"], custom_stickers._module().MAX_FILE_BYTES)
            data = path.read_bytes()
            facts, _, _, _ = custom_stickers._module()._decode(data)
            if (hashlib.sha256(data).hexdigest() != row["sha256"] or len(data) != row["size"]
                    or any(facts[key] != row[key] for key in facts)):
                raise ValueError("素材包图片实际内容与清单不一致")
            module.plain(staging, row["license_file"], 32 * 1024)
        os.replace(staging, target)
        pointer = folder_root / "active.json"
        # Check existing pointer links before the final atomic switch.
        if pointer.exists() or pointer.is_symlink():
            info = pointer.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("社区素材指针不能使用链接")
        with tempfile.NamedTemporaryFile(prefix=".active-", dir=folder_root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps({"generation": generation, "version": manifest["version"], "slot": target.name}).encode())
        try:os.replace(temporary, pointer)
        finally:temporary.unlink(missing_ok=True)
        # Keep active plus two previous cache generations; never touch a user
        # supplied folder or a path that resolves outside the owned packs root.
        previous = sorted((path for path in packs.iterdir() if path != target and path.is_dir()
                           and re.fullmatch(r"[a-f0-9]{16}(?:-[a-f0-9]{8})?", path.name)),
                          key=lambda path: path.stat().st_mtime, reverse=True)
        for obsolete in previous[2:]:
            if not obsolete.is_symlink() and obsolete.resolve().parent == packs.resolve():
                try:shutil.rmtree(obsolete)
                except OSError:pass  # Cache cleanup never invalidates installation.
    finally:
        # Only our mkdtemp child is ever removed; resolved containment checked.
        if staging.exists() and staging.resolve().parent == packs.resolve() and staging.name.startswith(".material-"):
            shutil.rmtree(staging)


def apply(body):
    global _busy, _pending
    if not isinstance(body, dict) or set(body) != {"fingerprint"}:
        raise ValueError("请先检查并预览素材更新")
    with _lock:
        pending = _pending
        if _busy or not pending or body["fingerprint"] != pending["fingerprint"] or not pending["available"]:
            raise ValueError("素材更新预览已变化，请重新检查")
        _busy = True
    try:
        content = _download(pending["asset"]["browser_download_url"], MAX_ZIP)
        if len(content) != pending["asset"]["size"] or "sha256:" + hashlib.sha256(content).hexdigest() != pending["asset"]["digest"]:
            raise ValueError("社区素材包下载校验失败")
        _install(content, pending)
        with _lock:
            _pending = None
            _busy = False
        return {**status(), "message": "社区素材已更新，聊天会自动读取新图片，无需更新主程序。"}
    except (zipfile.BadZipFile, RuntimeError, EOFError, HTTPError):
        raise ValueError("社区素材更新未完成，原图库仍可使用，请稍后重试") from None
    finally:
        with _lock:_busy = False
