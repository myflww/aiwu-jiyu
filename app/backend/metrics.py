"""System and process memory metrics for the overview page."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from backend import processes


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _system_memory() -> dict:
    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {"total_mb": 0, "used_mb": 0, "available_mb": 0, "percent": 0}
    total = status.ullTotalPhys / 1024 / 1024
    available = status.ullAvailPhys / 1024 / 1024
    return {
        "total_mb": round(total),
        "used_mb": round(total - available),
        "available_mb": round(available),
        "percent": int(status.dwMemoryLoad),
    }


def _process_mb(pid: int | None) -> int:
    if not pid:
        return 0
    handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, int(pid))
    if not handle:
        return 0
    try:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return 0
        return round(counters.WorkingSetSize / 1024 / 1024)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _lovehouse_pid() -> int:
    user32 = ctypes.windll.user32
    found: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if "爱之屋" in title or "love house" in title.lower():
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append(int(pid.value))
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found[0] if found else 0


def snapshot() -> dict:
    system = _system_memory()
    status = processes.status()
    astrbot_pid = status["astrbot"]["pid"]
    napcat_pid = status["napcat"]["pid"]
    astrbot_mb = _process_mb(astrbot_pid)
    napcat_mb = _process_mb(napcat_pid)
    app_mb = _process_mb(os.getpid())
    lovehouse_mb = _process_mb(_lovehouse_pid())
    return {
        **system,
        "astrbot_mb": astrbot_mb,
        "napcat_mb": napcat_mb,
        "app_mb": app_mb,
        "lovehouse_mb": lovehouse_mb,
        "bot_total_mb": astrbot_mb + napcat_mb,
    }
