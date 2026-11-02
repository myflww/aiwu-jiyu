# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

root = Path(SPECPATH).resolve().parent
version_text = os.environ.get("AIWU_APP_VERSION") or (root / "VERSION").read_text("utf-8").strip()
version_tuple = tuple(int(part) for part in version_text.split(".")) + (0,)
icon = root / "assets" / "ui" / "app-icon.ico"
datas = [(str(root / "app" / "ui"), "app/ui"), (str(root / "assets"), "assets")]
for notice in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "VERSION"):
    datas.append((str(root / notice), "."))
datas.append((str(root / "build" / "collected-licenses"), "THIRD-PARTY-LICENSES"))
import uv
binaries = [(str(uv.find_uv_bin()), "vendor")]
hiddenimports = ["clr", "clr_loader", "pythonnet", "webview.platforms.edgechromium",
                 "pystray._win32", "qrcode.image.pil"]
for package in ("webview", "pythonnet", "clr_loader", "pystray"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
a = Analysis(
    [str(root / "app" / "main.py")], pathex=[str(root / "app")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest"], noarchive=False,
)
pyz = PYZ(a.pure)
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple, mask=0x3f,
                     flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("080404B0", [
        StringStruct("CompanyName", "myflww"),
        StringStruct("FileDescription", "爱屋及鱼 · 本地陪伴控制台"),
        StringStruct("FileVersion", version_text),
        StringStruct("ProductName", "爱屋及鱼"),
        StringStruct("ProductVersion", version_text),
        StringStruct("LegalCopyright", "Copyright (c) 2026 myflww"),
        StringStruct("OriginalFilename", "AiwuJiyu.exe"),
    ])]), VarFileInfo([VarStruct("Translation", [2052, 1200])])],
)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="AiwuJiyu",
    icon=str(icon), version=version, debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None,
)
