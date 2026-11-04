"""Regenerate the exact shipped plugin inventory after a reviewed source change."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "assets" / "plugins"
IDS = ("astrbot_plugin_face_convert", "astrbot_plugin_love_house", "astrbot_plugin_pc_operator",
       "astrbot_plugin_dafeiyu_companion", "astrbot_plugin_dafeiyu_stickers", "astrbot_plugin_dafeiyu_life")
OPTIONAL = {"astrbot_plugin_dafeiyu_companion", "astrbot_plugin_dafeiyu_stickers", "astrbot_plugin_dafeiyu_life"}
TEXT = {".py", ".json", ".yaml", ".yml", ".md", ".txt", ".toml"}


def main() -> None:
    path = BUNDLE / "manifest.json"
    previous = {entry["id"]: entry for entry in json.loads(path.read_text("utf-8"))["plugins"]}
    entries = []
    for plugin_id in IDS:
        directory = BUNDLE / plugin_id
        files = []
        for source in sorted(directory.rglob("*")):
            relative = source.relative_to(directory)
            if "__pycache__" in relative.parts or source.suffix in {".pyc", ".pyo"} or not source.is_file():
                continue
            if source.is_symlink():
                raise ValueError("Plugin source cannot be a link")
            content = source.read_bytes()
            if source.suffix in TEXT or source.name in {"LICENSE", "NOTICE"}:
                normalized = content.replace(b"\r\n", b"\n")
                if normalized != content:
                    source.write_bytes(normalized)
                    content = normalized
            files.append({"path": relative.as_posix(), "sha256": hashlib.sha256(content).hexdigest(),
                          "size": len(content)})
        metadata = dict(re.findall(r"^([A-Za-z_]+):\s*([^\r\n]+)$",
                                   (directory / "metadata.yaml").read_text("utf-8"), re.M))
        entry = {**previous.get(plugin_id, {}), "id": plugin_id,
                 "name": metadata.get("display_name", plugin_id), "version": metadata.get("version", ""),
                 "author": metadata.get("author", "myflww"), "files": files}
        if plugin_id in OPTIONAL:
            entry.update(default_enabled=plugin_id == "astrbot_plugin_dafeiyu_companion", license="PolyForm-Noncommercial-1.0.0; see its LICENSE and NOTICE")
        entries.append(entry)
    path.write_text(json.dumps({"schema_version": 1, "plugins": entries}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"Updated {len(entries)} plugins, {sum(len(row['files']) for row in entries)} verified files")


if __name__ == "__main__":
    main()
