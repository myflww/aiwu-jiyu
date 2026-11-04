"""Build the reviewed data-only sticker assets uploaded with an official release.

No network, private profile or model access. The complete license text is
carried beside each original/authorized community image without relicensing.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "assets/plugins/astrbot_plugin_dafeiyu_stickers"


def load(name):
    spec = importlib.util.spec_from_file_location("_build_stickers_" + name, PLUGIN / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(output: Path, version="1.0.0"):
    catalog, custom, materials = load("catalog"), load("custom_catalog"), load("material_catalog")
    inventory = catalog.load_catalog()
    rows, contents = [], {}
    for item in inventory["stickers"]:
        path = catalog.verify_asset(item)
        content = path.read_bytes()
        facts, _, _, _ = custom._decode(content)
        kind, digest = facts["format"], hashlib.sha256(content).hexdigest()
        group = item.get("pack_id", "original")
        filename = f"images/{digest}.{custom.FORMATS[kind][0]}"
        license_file = f"licenses/{digest}.txt"
        if group == "community":
            license_id = "LicenseRef-Nekowhale-Noncommercial"
            attribution = "可以叫我云大人 / 雾雪 · https://space.bilibili.com/103739 · https://kiriyuki.com"
            license_text = (PLUGIN / "vendor/nekowhale/LICENSE-ASSETS").read_bytes() + b"\n\n" + (PLUGIN / "vendor/nekowhale/ATTRIBUTION.md").read_bytes()
            source = "https://github.com/Loping151/dsh-nekowhale-stickers"
        elif group == "contributed":
            license_id = "CC-BY-NC-4.0"
            attribution = item["attribution"]
            license_text = (PLUGIN / "community/submissions" / item["submission"] / "LICENSE.txt").read_bytes()
            source = "https://github.com/myflww/aiwu-jiyu"
        else:
            license_id = "PolyForm-Noncommercial-1.0.0"
            attribution = "myflww · 爱屋及鱼"
            license_text = (PLUGIN / "LICENSE").read_bytes() + b"\n\n" + (PLUGIN / "NOTICE").read_bytes()
            source = "https://github.com/myflww/aiwu-jiyu"
        row = {"id": item["id"], "title": (item.get("title") or item["label"])[:60], "emotion": item["emotion"],
               "tags": list(dict.fromkeys(str(term)[:24] for term in item.get("tags", item.get("aliases", []))))[:12],
               "description": str(item.get("description") or item.get("caption") or "")[:240],
               "use_when": str(item.get("use_when") or item.get("use") or "")[:160],
               "avoid_when": str(item.get("avoid_when") or "")[:160], "attribution": attribution,
               **facts, "size": len(content), "sha256": digest, "file": filename,
               "license_id": license_id, "license_file": license_file, "source_pack": group, "source_url": item.get("source_url") or source}
        rows.append(row)
        contents[filename] = content
        if license_file in contents and contents[license_file] != license_text:
            raise ValueError("同图包含不同许可，需要人工审核合并后发布")
        contents[license_file] = license_text
    value = {"schema_version": 1, "version": version, "source": "https://github.com/myflww/aiwu-jiyu", "stickers": rows}
    materials.validate_manifest(value)
    manifest = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output.mkdir(parents=True, exist_ok=True)
    (output / "community-stickers-manifest.json").write_bytes(manifest)
    package = output / f"community-stickers-{version}.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest)
        for name, content in sorted(contents.items()):
            archive.writestr(name, content)
    if package.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("独立素材包超过64 MiB，请人工精选后再发布")
    print(json.dumps({"version": version, "images": len(rows), "archive_bytes": package.stat().st_size,
                      "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                      "archive_sha256": hashlib.sha256(package.read_bytes()).hexdigest()}))
    return value, package


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/materials")
    args = parser.parse_args()
    build(args.output, args.version)
