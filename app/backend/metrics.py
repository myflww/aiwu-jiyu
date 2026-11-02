"""Low-cost Windows physical-memory and managed-process telemetry."""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes

from backend import processes

_MB = 1024 * 1024
_pid_cache = {"at": 0.0, "ports": {}}
_pid_lock = threading.Lock()


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
            "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
            "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


def _windows():
    # ctypes defaults to a 32-bit return value. Explicit HANDLE signatures are
    # essential on 64-bit Windows; otherwise valid process handles get truncated.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
    kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        function.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                           ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return kernel, psapi


def _system_memory() -> dict:
    kernel, _ = _windows()
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("无法读取系统内存")
    return {"available": True, "total_mb": round(status.ullTotalPhys / _MB),
            "used_mb": round((status.ullTotalPhys - status.ullAvailPhys) / _MB),
            "available_mb": round(status.ullAvailPhys / _MB), "percent": int(status.dwMemoryLoad)}


def _process_mb(pid: int | None) -> int:
    if not pid:
        return 0
    kernel, psapi = _windows()
    handle = kernel.OpenProcess(0x0410, False, int(pid))
    if not handle:
        return 0
    try:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0
        return round(counters.WorkingSetSize / _MB)
    finally:
        kernel.CloseHandle(handle)


def _process_parents() -> dict[int, int]:
    kernel, _ = _windows()
    handle = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if handle in (None, ctypes.c_void_p(-1).value):
        return {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        parents = {}
        found = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while found:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            found = kernel.Process32NextW(handle, ctypes.byref(entry))
        return parents
    finally:
        kernel.CloseHandle(handle)


def _descendants(root: int | None, parents: dict[int, int]) -> set[int]:
    if not root:
        return set()
    result = {int(root)}
    previous = set()
    while result != previous:
        previous = set(result)
        result.update(pid for pid, parent in parents.items() if parent in previous)
    return result


def _managed_root(name: str, listener: int | None, parents: dict[int, int]) -> int | None:
    """Include launchers only when the PID file is an ancestor of this listener."""
    if not listener:
        return None
    managed = processes._read_pid(name)
    current = listener
    visited = set()
    while current and current not in visited:
        if current == managed:
            return managed
        visited.add(current)
        current = parents.get(current)
    return listener  # A stale/reused PID file must not count unrelated processes.


def _port_pids() -> dict[int, int]:
    # One netstat pass for both services, cached briefly. Do not spawn tasklist
    # or query the chat/memory APIs every time the memory widget refreshes.
    with _pid_lock:
        now = time.monotonic()
        if now - _pid_cache["at"] < 5:
            return dict(_pid_cache["ports"])
        output = subprocess.check_output(["netstat", "-ano", "-p", "tcp"],
                                         stderr=subprocess.DEVNULL, creationflags=processes.NO_WINDOW)
        ports = {}
        pattern = re.compile(rb"^\s*TCP\s+\S+:(6185|6099)\s+\S+\s+LISTENING\s+(\d+)\s*$")
        for line in output.splitlines():
            match = pattern.match(line)
            if match:
                ports[int(match[1])] = int(match[2])
        _pid_cache.update(at=now, ports=ports)
        return dict(ports)


def _lovehouse_pid() -> int:
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    found = []

    def callback(hwnd, _parameter):
        if not user.IsWindowVisible(hwnd):
            return True
        length = user.GetWindowTextLengthW(hwnd)
        if length > 0:
            title = ctypes.create_unicode_buffer(length + 1)
            user.GetWindowTextW(hwnd, title, length + 1)
            if "爱之屋" in title.value or "love house" in title.value.lower():
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append(int(pid.value))
        return True

    user.EnumWindows(callback_type(callback), 0)
    return found[0] if found else 0


def _component_memory(roots: dict[str, int | None], parents: dict[int, int]) -> dict[str, int]:
    # The desktop app may have launched the bot processes itself. Count each
    # process in exactly one component, with managed services taking priority.
    counted = set()
    values = {}
    for name in ("astrbot", "napcat", "lovehouse", "app"):
        pids = _descendants(roots.get(name), parents) - counted
        counted.update(pids)
        values[name + "_mb"] = sum(_process_mb(pid) for pid in pids)
    return values


def snapshot() -> dict:
    result = {"available": False, "total_mb": 0, "used_mb": 0, "available_mb": 0,
              "percent": 0, "sampled_at": int(time.time() * 1000),
              "astrbot_mb": 0, "napcat_mb": 0, "app_mb": 0, "lovehouse_mb": 0,
              "bot_total_mb": 0, "app_total_mb": 0, "processes_available": False}
    try:
        result.update(_system_memory())
    except (OSError, AttributeError):
        return result
    try:
        ports = _port_pids()
        parents = _process_parents()
        roots = {"astrbot": _managed_root("astrbot", ports.get(6185), parents),
                 "napcat": _managed_root("napcat", ports.get(6099), parents),
                 "app": os.getpid(), "lovehouse": _lovehouse_pid()}
        result.update(_component_memory(roots, parents))
        result["bot_total_mb"] = result["astrbot_mb"] + result["napcat_mb"]
        result["app_total_mb"] = sum(result[name + "_mb"] for name in roots)
        result["processes_available"] = True
    except (OSError, AttributeError, subprocess.SubprocessError):
        pass
    return result
