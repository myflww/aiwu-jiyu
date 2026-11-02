"""Collect installed dependency notices without copying a runtime or user state."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def collect(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    manifest = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get("Name", "")):
        name = dist.metadata.get("Name", "unknown")
        included = []
        for file in dist.files or ():
            text = str(file).replace("\\", "/")
            if ".dist-info/" not in text or not any(
                word in file.name.lower() for word in ("license", "licence", "copying", "notice")
            ):
                continue
            source = Path(dist.locate_file(file))
            if not source.is_file():
                continue
            # Nested vendored .dist-info notices must retain their full path;
            # several different dependencies otherwise overwrite LICENSE.
            first, _, remaining = text.partition("/")
            relative = remaining if first.endswith(".dist-info") else text
            destination = target / name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            included.append(destination.relative_to(target).as_posix())
        manifest.append({"name": name, "version": dist.version, "files": included})
    lucide_source = ROOT / "app" / "ui" / "LICENSE-lucide.txt"
    lucide_target = target / "lucide-0.468.0" / "LICENSE.txt"
    lucide_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(lucide_source, lucide_target)
    manifest.append({"name": "lucide", "version": "0.468.0", "files": [lucide_target.relative_to(target).as_posix()]})
    proxy_source = ROOT / "assets" / "licenses" / "proxy-tools-0.1.0"
    proxy_provenance = json.loads((proxy_source / "provenance.json").read_text("utf-8"))
    proxy_dist = metadata.distribution("proxy_tools")
    proxy_code = Path(proxy_dist.locate_file("proxy_tools/__init__.py"))
    if hashlib.sha256(proxy_code.read_bytes()).hexdigest() != proxy_provenance["installed_source_sha256"]:
        raise RuntimeError("proxy_tools changed: check its matching upstream license")
    if hashlib.sha256((proxy_source / "LICENSE.txt").read_bytes()).hexdigest() != proxy_provenance["license_sha256"]:
        raise RuntimeError("proxy_tools upstream license text changed")
    shutil.copytree(proxy_source, target / proxy_source.name, dirs_exist_ok=True)
    for row in manifest:
        if row["name"].replace("_", "-") == "proxy-tools":
            row["files"].append(proxy_source.name + "/LICENSE.txt")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, target / "CPython-LICENSE.txt")
    sdk = ROOT / "assets" / "licenses" / "webview2-sdk-1.0.3856.49"
    provenance = json.loads((sdk / "provenance.json").read_text("utf-8"))
    import webview

    webview_root = Path(webview.__file__).parent
    for row in provenance["matched_redistributables"]:
        relative = row["pywebview_wheel_member"].removeprefix("webview/")
        dll = webview_root / relative
        if not dll.is_file() or hashlib.sha256(dll.read_bytes()).hexdigest() != row["sha256"]:
            raise RuntimeError("WebView2 SDK changed: refresh matching notices before building")
    shutil.copytree(sdk, target / sdk.name, dirs_exist_ok=True)
    (target / "dependency-licenses.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    collect(ROOT / "build" / "collected-licenses")
