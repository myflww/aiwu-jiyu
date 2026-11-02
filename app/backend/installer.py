"""First-run installer: uv + Python + AstrBot + NapCat into the runtime dir."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

from backend import config_store, downloader, plugins, processes
from backend.paths import (
    APP_HOME,
    ASSETS,
    ASTRBOT,
    ASTRBOT_RUNNER,
    BIN_DIR,
    LOGS,
    NAPCAT,
    RUNTIME,
    ensure_dirs,
    resource_root,
)

UV_VERSION = "0.13.6"
UV_SHA256 = "a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f"
UV_URL = (
    "https://ghproxy.net/https://github.com/astral-sh/uv/releases/download/"
    f"{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
)
UV_MIRRORS = [
    "https://gh-proxy.com/https://github.com/astral-sh/uv/releases/download/"
    f"{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip",
]
NAPCAT_VERSION = "4.18.42"
NAPCAT_URL = (
    "https://ghproxy.net/https://github.com/NapNeko/NapCatQQ/releases/download/"
    f"v{NAPCAT_VERSION}/NapCat.Shell.Windows.Node.zip"
)
NAPCAT_MIRRORS = [
    "https://gh-proxy.com/https://github.com/NapNeko/NapCatQQ/releases/download/"
    f"v{NAPCAT_VERSION}/NapCat.Shell.Windows.Node.zip",
]
NAPCAT_SHA256 = "77397ac38d66f46383e48f08f35d5f9d37ec575c22069f62d6c600e73b4521ed"
PYPI_INDEX = "https://mirrors.cernet.edu.cn/pypi/web/simple"
PYTHON_VERSION = "3.13.14"
PYTHON_INSTALLER_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-amd64.exe"
)
PYTHON_DIR = RUNTIME / "python"

NO_WINDOW = 0x08000000


class InstallState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.done = False
        self.error = ""
        self.step = ""
        self.progress = 0
        self.log: list[str] = []

    def update(self, step: str | None = None, progress: int | None = None, line: str | None = None) -> None:
        with self.lock:
            if step is not None:
                self.step = step
            if progress is not None:
                self.progress = max(0, min(100, progress))
            if line:
                self.log.append(line.rstrip())
                self.log = self.log[-400:]
                LOGS.mkdir(parents=True, exist_ok=True)
                with (LOGS / "install.log").open("a", encoding="utf-8") as handle:
                    handle.write(line.rstrip() + "\n")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "done": self.done,
                "error": self.error,
                "step": self.step,
                "progress": self.progress,
                "log": self.log[-160:],
            }


STATE = InstallState()


def _download(url: str, dest: Path, mirrors: list[str] | None = None) -> None:
    downloader.download(
        url,
        dest,
        log=lambda line: STATE.update(line=line),
        mirrors=mirrors,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    STATE.update(line="$ " + " ".join(cmd))
    merged = os.environ.copy()
    merged.update(env or {})
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="ignore",
        env=merged,
        creationflags=NO_WINDOW,
    )
    assert process.stdout is not None
    for line in process.stdout:
        STATE.update(line=line.rstrip())
    return process.wait()


def _ensure_uv() -> Path:
    uv = BIN_DIR / "uv.exe"
    if uv.exists():
        return uv
    bundled = resource_root() / "vendor" / "uv.exe"
    if bundled.exists():
        uv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, uv)
        STATE.update(line="使用内置 uv")
        return uv
    STATE.update(step="准备安装器", progress=8, line="下载 uv...")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "uv.zip"
        _download(UV_URL, archive, UV_MIRRORS)
        digest = _sha256(archive)
        STATE.update(line=f"uv SHA256: {digest}")
        if digest.lower() != UV_SHA256:
            raise RuntimeError("uv 校验失败，已停止安装")
        with zipfile.ZipFile(archive) as bundle:
            member = next((n for n in bundle.namelist() if n.endswith("uv.exe")), None)
            if not member:
                raise RuntimeError("uv 压缩包里没有 uv.exe")
            with bundle.open(member) as source, uv.open("wb") as target:
                shutil.copyfileobj(source, target)
    return uv


def _install_python() -> None:
    if (PYTHON_DIR / "python.exe").exists():
        return
    cache = APP_HOME / "downloads" / f"python-{PYTHON_VERSION}-amd64.exe"
    if not cache.exists():
        STATE.update(step="下载 Python 3.13", progress=14, line="下载 Python 安装包...")
        _download(PYTHON_INSTALLER_URL, cache)
    STATE.update(step="安装 Python 3.13", progress=18)
    code = _run(
        [
            str(cache),
            "/quiet",
            "InstallAllUsers=0",
            f"TargetDir={PYTHON_DIR}",
            "Include_pip=1",
            "Include_launcher=0",
            "InstallLauncherAllUsers=0",
            "AssociateFiles=0",
            "Shortcuts=0",
            "PrependPath=0",
            "Include_test=0",
        ]
    )
    if code not in (0, 3010):
        raise RuntimeError(f"Python 安装失败，退出码 {code}")
    if not (PYTHON_DIR / "python.exe").exists():
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313" / "python.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Python313" / "python.exe",
        ]
        installed = next((path for path in candidates if path.exists()), None)
        if not installed:
            raise RuntimeError("Python 安装完成，但找不到 python.exe")
        STATE.update(line=f"安装器未使用自定义目录，复制 {installed.parent} 到 {PYTHON_DIR}")
        if PYTHON_DIR.exists():
            shutil.rmtree(PYTHON_DIR)
        shutil.copytree(installed.parent, PYTHON_DIR)
    if not (PYTHON_DIR / "python.exe").exists():
        raise RuntimeError("Python 运行时不可用")


def _install_astrbot(uv: Path, config: dict) -> None:
    ASTRBOT.mkdir(parents=True, exist_ok=True)
    venv = ASTRBOT / "venv"
    python = PYTHON_DIR / "python.exe"
    if not python.exists():
        raise RuntimeError("Python 运行时不存在，无法创建 AstrBot 环境")
    STATE.update(step="创建 AstrBot 环境", progress=30)
    env = {"UV_DEFAULT_INDEX": PYPI_INDEX, "UV_HTTP_TIMEOUT": "120"}
    if not (venv / "Scripts" / "python.exe").exists():
        code = _run([str(uv), "venv", "--python", str(python), str(venv)], env=env)
        if code != 0:
            raise RuntimeError(f"创建 venv 失败，退出码 {code}")
    venv_python = venv / "Scripts" / "python.exe"
    STATE.update(step="安装 AstrBot 4.29.4", progress=38)
    code = _run(
        [
            str(uv),
            "pip",
            "install",
            "--python",
            str(venv_python),
            "AstrBot==4.29.4",
            "--index-url",
            PYPI_INDEX,
        ],
        env=env,
    )
    if code != 0:
        raise RuntimeError(f"安装 AstrBot 失败，退出码 {code}")
    runner_text = (
        '"""Relocatable AstrBot launcher for the dafeiyu runtime."""\n\n'
        "from astrbot.cli.__main__ import cli\n\n"
        'if __name__ == "__main__":\n    cli()\n'
    )
    (ASTRBOT / "run_astrbot.py").write_text(runner_text, encoding="utf-8")
    STATE.update(line=f"已写入启动器 {ASTRBOT / 'run_astrbot.py'}")


def _install_napcat() -> None:
    if (NAPCAT / "napcat.bat").exists():
        return
    STATE.update(step="下载 NapCat", progress=70)
    cache = APP_HOME / "downloads" / "NapCat.Shell.Windows.Node.zip"
    if not cache.exists():
        _download(NAPCAT_URL, cache, NAPCAT_MIRRORS)
    digest = _sha256(cache)
    STATE.update(line=f"NapCat SHA256: {digest}")
    if digest.lower() != NAPCAT_SHA256:
        raise RuntimeError("NapCat 校验失败，已停止安装")
    STATE.update(step="解压 NapCat", progress=78)
    NAPCAT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cache) as bundle:
        bundle.extractall(NAPCAT)
    if not (NAPCAT / "napcat.bat").exists():
        raise RuntimeError("NapCat 解压后缺少 napcat.bat")


def _copy_assets() -> None:
    STATE.update(step="安装人格与插件", progress=86)
    result = plugins.install_bundled()
    STATE.update(line=f"已准备 {len(result['plugins'])} 个随附插件，无需逐个下载")


def _init_astrbot() -> None:
    STATE.update(step="初始化 AstrBot", progress=92)
    python = ASTRBOT / "venv" / "Scripts" / "python.exe"
    code = _run([str(python), str(ASTRBOT_RUNNER), "init", "-y"], cwd=ASTRBOT)
    if code != 0:
        raise RuntimeError(f"AstrBot 初始化失败，退出码 {code}")


def _bootstrap(config: dict) -> None:
    STATE.running = True
    STATE.done = False
    STATE.error = ""
    STATE.step = "开始"
    STATE.progress = 0
    try:
        ensure_dirs()
        if sys.platform == "win32" and sys.getwindowsversion().build < 19041:
            raise RuntimeError("需要 Windows 10 2004（19041）或 Windows 11")
        free = shutil.disk_usage(APP_HOME).free
        if free < 3 * 1024**3:
            raise RuntimeError(f"磁盘空间不足：至少需要 3GB，当前可用 {free / 1024**3:.1f}GB")
        uv = _ensure_uv()
        _install_python()
        _install_astrbot(uv, config)
        _install_napcat()
        _copy_assets()
        _init_astrbot()
        config_store.sync_astrbot_config(config)
        STATE.update(step="安装完成", progress=100, line="运行时安装完成")
        STATE.done = True
    except Exception as exc:  # noqa: BLE001
        STATE.error = str(exc)
        STATE.update(line=f"安装失败: {exc}")
    finally:
        STATE.running = False


def start(config: dict) -> dict:
    if STATE.running:
        return {"ok": False, "message": "安装正在进行", "state": STATE.snapshot()}
    thread = threading.Thread(target=_bootstrap, args=(config,), daemon=True)
    thread.start()
    return {"ok": True, "state": STATE.snapshot()}


def snapshot() -> dict:
    return STATE.snapshot()
