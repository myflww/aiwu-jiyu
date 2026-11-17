"""Pinned NapCat archive preparation, offline import and transactional install.

The community archive is a transport for the original 4.18.42 bytes, not an
upstream continuation. No downloaded archive is trusted before the pinned hash
and its extraction layout have both been checked.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Callable

from backend import downloader, processes
from backend.paths import APP_HOME, NAPCAT

VERSION = "4.18.42"
ARCHIVE_NAME = "NapCat.Shell.Windows.Node.zip"
ARCHIVE_BYTES = 124_162_399
SHA256 = "77397ac38d66f46383e48f08f35d5f9d37ec575c22069f62d6c600e73b4521ed"
SOURCE_NAME = "社区归档 · 原版 4.18.42"
SOURCE_URL = f"https://github.com/XingYunStar/NapCatQQ/releases/tag/v{VERSION}"
ARCHIVE_URL = f"https://github.com/XingYunStar/NapCatQQ/releases/download/v{VERSION}/{ARCHIVE_NAME}"
MIRRORS = [f"https://ghproxy.net/{ARCHIVE_URL}", f"https://gh-proxy.com/{ARCHIVE_URL}"]

# These are the actual Node bundle entry points and required native libraries.
# Account/config directories are deliberately never inspected or copied.
REQUIRED_FILES = (
    "napcat.bat", "node.exe", "index.js", "package.json", "config.json",
    "QQNT.dll", "wrapper.node", "napcat/napcat.mjs",
    "win64/parent-ipc-core-x64.dll", "win64/SSOShareInfoHelper64.dll",
)
_PE_FILES = {"node.exe", "QQNT.dll", "wrapper.node", *REQUIRED_FILES[-2:]}
_MIN_FILE_BYTES = {"napcat.bat": 32, "index.js": 128, "package.json": 16, "config.json": 16,
                   "napcat/napcat.mjs": 64 * 1024, "node.exe": 1024 * 1024,
                   "wrapper.node": 1024 * 1024, "QQNT.dll": 4096,
                   "win64/parent-ipc-core-x64.dll": 4096, "win64/SSOShareInfoHelper64.dll": 4096}
_MAX_MEMBERS = 10_000
_MAX_UNPACKED_BYTES = 1024**3
_RESERVED_NAMES = {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)], *[f"lpt{i}" for i in range(1, 10)]}

# A primitive Lock intentionally permits start() to reserve it before spawning
# a worker, and that worker to release it. Imports cannot slip into that gap.
_operation_lock = threading.Lock()


def claim_operation() -> bool:
    return _operation_lock.acquire(blocking=False)


def release_operation() -> None:
    _operation_lock.release()


@contextmanager
def _operation(already_claimed: bool = False):
    if not already_claimed and not claim_operation():
        raise ValueError("安装或 QQ 连接包导入正在进行，请等待完成后重试。")
    try:
        yield
    finally:
        if not already_claimed:
            release_operation()


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_path(path: Path, root: Path) -> None:
    """Reject links/junctions anywhere inside an owned installation directory."""
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("QQ 连接包路径不在预期目录内。") from exc
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        if _is_link(current):
            raise ValueError("QQ 连接包路径包含链接或目录联接，已停止操作。")


def _cache_path() -> Path:
    return APP_HOME / "downloads" / ARCHIVE_NAME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _runtime_missing(directory: Path) -> list[str]:
    missing = []
    for name in REQUIRED_FILES:
        target = directory / name
        try:
            _safe_path(target, directory)
            if not target.is_file() or target.stat().st_size < _MIN_FILE_BYTES[name]:
                missing.append(name)
            elif name in _PE_FILES:
                with target.open("rb") as stream:
                    if stream.read(2) != b"MZ":
                        missing.append(name)
        except (OSError, ValueError):
            missing.append(name)
    return missing


def runtime_complete() -> bool:
    """Lightweight entry-point/native-library check, without hashing a cache."""
    return not _runtime_missing(NAPCAT)


def _member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.orig_filename
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise ValueError("QQ 连接包包含不安全的文件路径。")
    parts = name.rstrip("/").split("/")
    if any(not part or part in {".", ".."} or ":" in part or part[-1:] in {".", " "}
           or part.split(".", 1)[0].lower() in _RESERVED_NAMES for part in parts):
        raise ValueError("QQ 连接包包含不安全的文件路径。")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
        raise ValueError("QQ 连接包不能包含符号链接或特殊文件。")
    if info.flag_bits & 1:
        raise ValueError("QQ 连接包不能包含加密文件。")
    return PurePosixPath(*parts)


def _members(bundle: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    infos = bundle.infolist()
    if len(infos) > _MAX_MEMBERS or sum(info.file_size for info in infos) > _MAX_UNPACKED_BYTES:
        raise ValueError("QQ 连接包解压容量超过限制。")
    members, seen, files = [], set(), set()
    for info in infos:
        relative = _member_path(info)
        key = str(relative).casefold()
        if key in seen:
            raise ValueError("QQ 连接包包含重复文件路径。")
        seen.add(key)
        if not info.is_dir():
            if info.file_size <= 0 and str(relative) in REQUIRED_FILES:
                raise ValueError("QQ 连接包的关键文件不完整。")
            files.add(key)
        members.append((info, relative))
    if any(name.casefold() not in files for name in REQUIRED_FILES):
        raise ValueError("QQ 连接包缺少关键文件，请选择原版 Windows.Node.zip。")
    return members


def _validate_archive(path: Path) -> None:
    if _is_link(path) or not path.is_file() or path.stat().st_size != ARCHIVE_BYTES:
        raise ValueError("QQ 连接包大小不符，请重新下载或导入原版 4.18.42 Windows.Node.zip。")
    if _sha256(path) != SHA256:
        raise ValueError("QQ 连接包 SHA256 校验失败；未采用该文件，请重新下载或导入原版安装包。")
    try:
        with zipfile.ZipFile(path) as bundle:
            _members(bundle)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("QQ 连接包不是完整的 ZIP 文件，请重新下载或导入。") from exc


def inspect_napcat() -> dict:
    """Read files/metadata only; never load account config or contact services."""
    missing = _runtime_missing(NAPCAT)
    installed = not missing
    try:
        runtime_exists = NAPCAT.exists() or _is_link(NAPCAT)
        partial = runtime_exists and (not NAPCAT.is_dir() or _is_link(NAPCAT) or any(NAPCAT.iterdir()))
    except OSError:
        partial = True
    cache = _cache_path()
    cache_exists = cache.exists() or _is_link(cache)
    valid = False
    if cache_exists:
        try:
            _safe_path(cache, APP_HOME)
            _validate_archive(cache)
            valid = True
        except (OSError, ValueError):
            pass
    if installed:
        status, message = "installed", "已保留完整的本机 QQ 连接环境，无需重新下载。"
    elif partial:
        status, message = "partial", "现有 QQ 连接目录不完整，已保护其中的数据；请先将该目录改名备份，再重新安装。"
    elif valid:
        status, message = "cached", "原版 QQ 连接包已通过固定 SHA256 校验，可以继续安装。"
    elif cache_exists:
        status, message = "invalid_cache", "缓存未通过校验，可以重新下载或导入原版 4.18.42 Windows.Node.zip。"
    else:
        status, message = "missing", "将从社区归档获取原版 4.18.42，或导入已有的 Windows.Node.zip；这不是官方继续维护版。"
    return {"version": VERSION, "installed": installed, "cache_valid": valid, "cache_exists": cache_exists,
            "status": status, "message": message, "source_name": SOURCE_NAME, "source_url": SOURCE_URL,
            "archive_url": ARCHIVE_URL, "sha256": SHA256, "archive_name": ARCHIVE_NAME,
            "runtime_complete": installed, "missing_files": missing}


def _temporary_cache() -> tuple[Path, object]:
    directory = _cache_path().parent
    _safe_path(directory, APP_HOME)
    directory.mkdir(parents=True, exist_ok=True)
    stream = tempfile.NamedTemporaryFile(dir=directory, prefix=".napcat-", suffix=".part", delete=False)
    return Path(stream.name), stream


def import_napcat_archive(path: str | Path) -> dict:
    """Validate and atomically cache an offline ZIP; do not install or start it."""
    with _operation():
        if processes.port_up(6099):
            raise ValueError("QQ 连接服务正在运行，请关闭服务后再导入连接包；当前环境不会被修改。")
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError("请选择原版 4.18.42 Windows.Node.zip 的本地路径。")
        source = Path(path).expanduser()
        if not source.is_absolute() or _is_link(source) or not source.is_file():
            raise ValueError("请选择现有 ZIP 文件的绝对路径，不能使用链接。")
        temporary = None
        try:
            temporary, stream = _temporary_cache()
            with stream, source.open("rb") as incoming:
                remaining = ARCHIVE_BYTES + 1
                while remaining:
                    block = incoming.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    stream.write(block)
                    remaining -= len(block)
            _validate_archive(temporary)
            _safe_path(_cache_path(), APP_HOME)
            os.replace(temporary, _cache_path())
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {"ok": True, "message": "QQ 连接包已通过校验并保存到下载缓存，未修改或启动运行环境。",
                "napcat": inspect_napcat()}


def prepare_napcat_archive(*, log: Callable[[str], None] | None = None, _operation_claimed: bool = False) -> Path | None:
    """Fail early before Python/AstrBot work, or return a verified cached ZIP."""
    logger = log or (lambda _line: None)
    with _operation(_operation_claimed):
        current = inspect_napcat()
        if current["installed"]:
            logger(current["message"])
            return None
        if current["status"] == "partial":
            raise ValueError(current["message"] + f" 目录：{NAPCAT}")
        if current["cache_valid"]:
            logger("复用已核验的原版 QQ 连接包缓存。")
            return _cache_path()
        logger("原项目已结束；使用社区归档中的原版 4.18.42，并强制核对原固定 SHA256。")
        errors = []
        for url in (ARCHIVE_URL, *MIRRORS):
            temporary = None
            try:
                temporary, stream = _temporary_cache()
                stream.close()
                downloader.download(url, temporary, log=logger)
                _validate_archive(temporary)
                _safe_path(_cache_path(), APP_HOME)
                os.replace(temporary, _cache_path())
                temporary = None
                logger("QQ 连接包 SHA256 校验通过。")
                return _cache_path()
            except (OSError, ValueError, RuntimeError) as exc:
                errors.append(str(exc))
                logger(f"该下载源不可用或校验失败：{exc}")
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        raise ValueError("QQ 连接包暂时无法取得；请重试下载，或在安装页导入原版 4.18.42 Windows.Node.zip。"
                         "已保留现有缓存与运行环境。" + (f" 最后错误：{errors[-1]}" if errors else ""))


def install_napcat(*, log: Callable[[str], None] | None = None, _operation_claimed: bool = False) -> None:
    logger = log or (lambda _line: None)
    with _operation(_operation_claimed):
        archive = prepare_napcat_archive(log=logger, _operation_claimed=True)
        if archive is None:
            return
        if processes.port_up(6099):
            raise ValueError("QQ 连接服务正在运行，已停止安装；请先关闭服务，现有文件未被修改。")
        parent = NAPCAT.parent
        _safe_path(NAPCAT, parent)
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent, prefix=".napcat-install-") as temporary:
            staging = Path(temporary) / "payload"
            staging.mkdir()
            # Revalidate just before extraction. No extraction touches NAPCAT.
            _validate_archive(archive)
            with zipfile.ZipFile(archive) as bundle:
                for info, relative in _members(bundle):
                    target = staging.joinpath(*relative.parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.open(info) as source, target.open("xb") as output:
                            shutil.copyfileobj(source, output)
            if _runtime_missing(staging):
                raise ValueError("QQ 连接包解压后关键文件不完整，未修改现有运行环境。")
            _safe_path(NAPCAT, parent)
            if processes.port_up(6099):
                raise ValueError("QQ 连接服务已启动，已停止安装；现有文件未被修改。")
            # Never merge into a partial installation: it may contain logins,
            # user config or other files. A concurrently populated target also
            # aborts. Only an explicitly empty directory may be replaced.
            had_empty_directory = NAPCAT.exists()
            if had_empty_directory:
                if not NAPCAT.is_dir() or any(NAPCAT.iterdir()):
                    raise ValueError("QQ 连接目录已有文件，已保护原目录；请先改名备份再重试。")
                NAPCAT.rmdir()
            try:
                os.replace(staging, NAPCAT)
            except Exception:
                if had_empty_directory and not NAPCAT.exists():
                    NAPCAT.mkdir()
                raise
            logger("QQ 连接环境已在隔离目录完成校验并安装，未启动服务。")
