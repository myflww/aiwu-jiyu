# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve().parent

datas = [
    (str(root / "app" / "ui"), "app/ui"),
    (str(root / "assets"), "assets"),
]
binaries = []
uv = root / "build" / "vendor" / "uv.exe"
if uv.exists():
    binaries.append((str(uv), "vendor"))

webview_datas, webview_binaries, webview_hidden = collect_all("webview")
datas += webview_datas
binaries += webview_binaries
hiddenimports = webview_hidden + [
    "clr",
    "clr_loader",
    "pythonnet",
    "webview.platforms.edgechromium",
]
for package in ("pythonnet", "clr_loader", "pystray"):
    extra_datas, extra_binaries, extra_hidden = collect_all(package)
    datas += extra_datas
    binaries += extra_binaries
    hiddenimports += extra_hidden

a = Analysis(
    [str(root / "app" / "main.py")],
    pathex=[str(root / "app")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AiwuJiyu",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
