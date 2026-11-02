"""WebView2 runtime preflight for the desktop window."""

from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

from backend.paths import ASSETS, BIN_DIR

CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
KEYS = [
    rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{CLIENT_GUID}",
    rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{CLIENT_GUID}",
]
BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def installed_version() -> str:
    if winreg is None:
        return ""
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in KEYS:
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ) as handle:
                    value, _ = winreg.QueryValueEx(handle, "pv")
                    if value:
                        return str(value)
            except OSError:
                continue
    return ""


def _setup_exe() -> Path:
    bundled = ASSETS / "vendor" / "MicrosoftEdgeWebview2Setup.exe"
    if bundled.exists():
        return bundled
    target = BIN_DIR / "MicrosoftEdgeWebview2Setup.exe"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(BOOTSTRAPPER_URL, headers={"User-Agent": "DafeiyuSetup/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            handle.write(chunk)
    return target


def ensure() -> bool:
    if installed_version():
        return True
    try:
        setup = _setup_exe()
        subprocess.run(
            [str(setup), "/silent", "/install"],
            capture_output=True,
            check=False,
            timeout=900,
            creationflags=0x08000000,
        )
    except Exception:
        return False
    return bool(installed_version())
