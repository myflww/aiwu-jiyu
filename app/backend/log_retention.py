"""Bound this application's three diagnostic logs without replacing live files.

Chat history, runtime-internal logs and memory databases are outside this module.
Inherited service stdout must use FILE_APPEND_DATA on Windows: a normal Python
``ab`` descriptor alone does not make a child's native WriteFile append safely
after a file is compacted. Old active writers are deferred until a normal restart.
"""
from __future__ import annotations

import ctypes
import copy
import io
import json
import os
from pathlib import Path
import stat
import secrets
import threading
import time
from typing import Callable

from backend.paths import LOGS, RUN_DIR

LOG_NAMES = ("app.log", "astrbot.log", "napcat.log")
MAX_BYTES = 5 * 1024 * 1024
KEEP_BYTES = 1024 * 1024
RETENTION_DAYS = 7
CHECK_INTERVAL_SECONDS = 10 * 60
QUIET_SECONDS = 5
_REPARSE_POINT = 0x400


def policy() -> dict:
    return {"enabled": True, "retention_days": RETENTION_DAYS,
            "max_file_bytes": MAX_BYTES, "keep_bytes": KEEP_BYTES,
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
            "quiet_seconds": QUIET_SECONDS, "files": list(LOG_NAMES)}


def _plain_path(path: Path) -> None:
    """Reject symlinks/junctions rather than resolving through them."""
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            raise OSError("Diagnostic log path is a link or junction")


def _checked_file(path: Path, mode: str):
    if path.name not in LOG_NAMES:
        raise ValueError("Unsupported diagnostic log")
    _plain_path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError("Diagnostic log is not a private regular file")
    handle = path.open(mode, buffering=0)
    after = os.fstat(handle.fileno())
    if (before.st_ino, before.st_dev) != (after.st_ino, after.st_dev) or after.st_nlink != 1:
        handle.close()
        raise OSError("Diagnostic log changed while opening")
    return handle


def open_append_log(path: Path):
    """Return unbuffered append-only stdout suitable for native child processes."""
    path = Path(path)
    if path.name not in LOG_NAMES:
        raise ValueError("Unsupported diagnostic log")
    _plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
    else:
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        # FILE_APPEND_DATA, with no FILE_WRITE_DATA right. DuplicateHandle in
        # subprocess.Popen retains these rights for stdout/stderr in the child.
        handle = create(str(path), 0x0004 | 0x0080 | 0x00100000, 0x1 | 0x2 | 0x4, None,
                        4, 0x80 | 0x00200000, None)  # OPEN_ALWAYS, OPEN_REPARSE_POINT
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_APPEND | os.O_BINARY)
        except BaseException:
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(handle)
            raise
    stream = os.fdopen(fd, "ab", buffering=0)
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        stream.close()
        raise OSError("Diagnostic log is not a private regular file")
    return stream


def open_text_log(path: Path):
    return io.TextIOWrapper(open_append_log(path), encoding="utf-8", errors="backslashreplace",
                            line_buffering=True, write_through=True)


def _process_token(pid: int) -> int | None:
    """Creation time makes a recycled PID insufficient proof of a safe writer."""
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}").stat().st_ctime_ns
        except OSError:
            return None
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def _parents() -> dict[int, int]:
    if os.name != "nt":
        return {}
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        return {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        present = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while present:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            present = kernel32.Process32NextW(handle, ctypes.byref(entry))
        return parents
    finally:
        kernel32.CloseHandle(handle)


def register_writer(name: str, pid: int, *, run_dir: Path = RUN_DIR) -> None:
    """Best-effort proof that a managed service inherited the new append handle."""
    if name not in LOG_NAMES:
        raise ValueError("Unsupported diagnostic log")
    try:
        token = _process_token(pid)
        if token is None:
            return
        _plain_path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        marker = run_dir / (name + ".append-writer.json")
        _plain_path(marker)
        temporary = marker.with_name(marker.name + "." + secrets.token_hex(8) + ".tmp")
        with temporary.open("x", encoding="utf-8") as output:
            output.write(json.dumps({"schema": 1, "pid": pid, "created": token}))
        temporary.replace(marker)
    except OSError:
        pass  # A missing proof defers maintenance; it must never stop a service.


def _safe_active_writer(name: str, pid: int, run_dir: Path) -> bool:
    try:
        marker = run_dir / (name + ".append-writer.json")
        _plain_path(marker)
        if marker.stat().st_size > 1024:
            return False
        data = json.loads(marker.read_text("utf-8"))
        root = int(data["pid"])
        token = _process_token(root)
        if data.get("schema") != 1 or token is None or token != data.get("created"):
            return False
        parents = None
        visited = set()
        while pid > 0 and pid not in visited:
            if pid == root:
                return True
            visited.add(pid)
            if parents is None:
                parents = _parents()
            pid = parents.get(pid, 0)
        return False
    except (OSError, ValueError, TypeError, KeyError):
        return False


def read_tail(name: str, max_chars: int = 12000, *, log_dir: Path = LOGS) -> str:
    """Do not load an arbitrarily large pre-upgrade console log every 3 seconds."""
    if name not in LOG_NAMES or max_chars <= 0:
        return ""
    limit = min(max_chars, 12000)
    try:
        with _checked_file(log_dir / name, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            handle.seek(max(0, size - limit * 4 - 128))
            return handle.read(limit * 4 + 128).decode("utf-8", errors="ignore")[-limit:]
    except OSError:
        return ""


class LogRetention:
    def __init__(self, *, log_dir: Path = LOGS, run_dir: Path = RUN_DIR,
                 active_provider: Callable[[], dict] | None = None,
                 before_trim: Callable[[str], bool] | None = None):
        self.log_dir, self.run_dir = Path(log_dir), Path(run_dir)
        self.active_provider = active_provider
        self.before_trim = before_trim
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = {**policy(), "checked_at": None, "trimmed_files": 0,
                       "cleared_files": 0, "reclaimed_bytes": 0, "deferred": [], "errors": []}

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="aiwu-log-retention")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.cleanup()
            self._stop.wait(CHECK_INTERVAL_SECONDS)

    def cleanup(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        active = None
        try:
            if self.active_provider:
                active = self.active_provider()
        except Exception:
            pass
        result = {"checked_at": now, "trimmed_files": 0, "cleared_files": 0,
                  "reclaimed_bytes": 0, "deferred": [], "errors": []}
        for name in LOG_NAMES:
            path = self.log_dir / name
            try:
                _plain_path(path)
                before = path.stat()
                if not before.st_size:
                    continue
                expired = now - before.st_mtime >= RETENTION_DAYS * 86400
                if not expired and before.st_size <= MAX_BYTES:
                    continue
                service = name.removesuffix(".log")
                if service == "app":
                    pid = os.getpid()
                elif active is None or service not in active:
                    result["deferred"].append({"name": name, "reason": "service_status_unknown"})
                    continue
                else:
                    value = active[service]
                    pid = value.get("pid") if isinstance(value, dict) else value
                    if isinstance(value, dict) and value.get("up") and not pid:
                        result["deferred"].append({"name": name, "reason": "service_status_unknown"})
                        continue
                if pid and not _safe_active_writer(name, int(pid), self.run_dir):
                    result["deferred"].append({"name": name, "reason": "legacy_writer"})
                    continue
                if now - before.st_mtime < QUIET_SECONDS:
                    result["deferred"].append({"name": name, "reason": "busy"})
                    continue
                if self.before_trim and not self.before_trim(name):
                    result["errors"].append(name)
                    continue
                with _checked_file(path, "r+b") as handle:
                    observed = os.fstat(handle.fileno())
                    if (observed.st_size, observed.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                        result["deferred"].append({"name": name, "reason": "busy"})
                        continue
                    payload = b""
                    if not expired:
                        offset = max(0, observed.st_size - KEEP_BYTES)
                        handle.seek(offset)
                        payload = handle.read(KEEP_BYTES)
                        if offset and b"\n" in payload:
                            payload = payload.split(b"\n", 1)[1]
                        payload = payload.decode("utf-8", errors="ignore").encode("utf-8")
                    final = os.fstat(handle.fileno())
                    if (final.st_size, final.st_mtime_ns) != (observed.st_size, observed.st_mtime_ns):
                        result["deferred"].append({"name": name, "reason": "busy"})
                        continue
                    # Keep the same file object for inherited native stdout. Do
                    # not rename/remove it and strand a live service on an archive.
                    handle.seek(0)
                    handle.write(payload)
                    handle.truncate(len(payload))
                    result["cleared_files" if expired else "trimmed_files"] += 1
                    result["reclaimed_bytes"] += observed.st_size - len(payload)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError):
                result["errors"].append(name)
        with self._lock:
            self._state.update(result)
        return self.snapshot()
