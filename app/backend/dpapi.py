"""Windows DPAPI helpers for storing the model API key at rest."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

if hasattr(ctypes, "windll"):
    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
else:  # pragma: no cover - non-Windows development fallback
    _crypt32 = None
    _kernel32 = None


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _blob_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect(text: str) -> str:
    if not text:
        return ""
    raw = text.encode("utf-8")
    if _crypt32 is None:
        return "plain:" + base64.b64encode(raw).decode("ascii")
    in_blob = _blob(raw)
    out_blob = _DataBlob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        return "plain:" + base64.b64encode(raw).decode("ascii")
    try:
        return "dpapi:" + base64.b64encode(_blob_bytes(out_blob)).decode("ascii")
    finally:
        _kernel32.LocalFree(out_blob.pbData)


def unprotect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("plain:"):
        return base64.b64decode(value[6:]).decode("utf-8")
    if not value.startswith("dpapi:"):
        return value
    raw = base64.b64decode(value[6:])
    if _crypt32 is None:
        return raw.decode("utf-8", errors="ignore")
    in_blob = _blob(raw)
    out_blob = _DataBlob()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        return ""
    try:
        return _blob_bytes(out_blob).decode("utf-8", errors="ignore")
    finally:
        _kernel32.LocalFree(out_blob.pbData)
