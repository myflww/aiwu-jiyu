"""Bounded, verified downloads used by the first-run installer."""

from __future__ import annotations

import hashlib
import re
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

CHUNK = 4 * 1024 * 1024
WORKERS = 8
TOTAL_TIMEOUT = 180
REQUEST_TIMEOUT = 15
HEARTBEAT = 10


class RangeUnsupported(RuntimeError):
    pass


def _remaining(deadline: float, cancelled: threading.Event | None = None) -> float:
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError("下载已取消")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("下载源超过总时限，切换下一个下载源")
    return remaining


def _headers(start: int | None = None, end: int | None = None) -> dict:
    headers = {"User-Agent": "DafeiyuSetup/1.0", "Accept-Encoding": "identity"}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    return headers


def _range_info(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value.strip())
    if not match:
        raise RuntimeError(f"无效的 Content-Range：{value!r}")
    return tuple(map(int, match.groups()))


def _probe(url: str, deadline: float) -> tuple[int, bool]:
    try:
        request = urllib.request.Request(url, method="HEAD", headers=_headers())
        with urllib.request.urlopen(request, timeout=min(REQUEST_TIMEOUT, _remaining(deadline))) as response:
            size = int(response.headers.get("Content-Length") or 0)
            ranges = (response.headers.get("Accept-Ranges") or "").lower() == "bytes"
            if size > 0:
                return size, ranges
    except Exception:
        _remaining(deadline)
    try:
        request = urllib.request.Request(url, headers=_headers(0, 0))
        with urllib.request.urlopen(request, timeout=min(REQUEST_TIMEOUT, _remaining(deadline))) as response:
            if response.status == 206:
                start, end, size = _range_info(response.headers.get("Content-Range") or "")
                if (start, end) == (0, 0) and size > 0:
                    return size, True
    except Exception:
        _remaining(deadline)
    return 0, False


def _fetch(
    url: str, start: int, end: int, size: int, deadline: float,
    cancelled: threading.Event, retries: int = 3,
) -> bytes:
    last_error = None
    for attempt in range(retries):
        _remaining(deadline, cancelled)
        try:
            request = urllib.request.Request(url, headers=_headers(start, end))
            timeout = min(REQUEST_TIMEOUT, _remaining(deadline, cancelled))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status == 200:
                    raise RangeUnsupported("server ignored range")
                if response.status != 206:
                    raise RuntimeError(f"HTTP {response.status}")
                actual = _range_info(response.headers.get("Content-Range") or "")
                if actual != (start, end, size):
                    raise RuntimeError(f"分片范围不匹配：{actual} != {(start, end, size)}")
                expected = end - start + 1
                data = bytearray()
                reader = getattr(response, "read1", response.read)
                while len(data) <= expected:
                    _remaining(deadline, cancelled)
                    chunk = reader(min(256 * 1024, expected + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) != expected:
                    raise RuntimeError(f"分片长度不匹配：{len(data)} != {expected}")
            _remaining(deadline, cancelled)
            return bytes(data)
        except RangeUnsupported:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 < retries:
                cancelled.wait(min(1.2 * (attempt + 1), _remaining(deadline, cancelled)))
    raise RuntimeError(f"分片下载失败：{last_error}")


def _parallel(url: str, dest: Path, size: int, log: Callable[[str], None], deadline: float) -> None:
    ranges = [(start, min(start + CHUNK - 1, size - 1)) for start in range(0, size, CHUNK)]
    log(f"分片下载 {dest.name}：{len(ranges)} 段 / {WORKERS} 线程")
    started = time.monotonic()
    cancelled = threading.Event()
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    pending = {}
    try:
        pending = {pool.submit(_fetch, url, start, end, size, deadline, cancelled): start
                   for start, end in ranges}
        written = completed = 0
        with dest.open("wb") as handle:
            while pending:
                done, _ = wait(pending, timeout=min(HEARTBEAT, _remaining(deadline)),
                               return_when=FIRST_COMPLETED)
                for future in done:
                    data = future.result()
                    handle.seek(pending.pop(future))
                    written += handle.write(data)
                    completed += 1
                log(f"  {completed}/{len(ranges)} 段，{time.monotonic() - started:.0f}s")
            if written != size:
                raise RuntimeError(f"下载总长度不匹配：{written} != {size}")
    finally:
        # Workers never write files: a timed-out attempt cannot corrupt a retry.
        cancelled.set()
        pool.shutdown(wait=False, cancel_futures=True)


def _single(url: str, dest: Path, log: Callable[[str], None], deadline: float, size: int = 0) -> None:
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=min(REQUEST_TIMEOUT, _remaining(deadline))) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        total = int(response.headers.get("Content-Length") or size)
        downloaded = 0
        last = -1
        heartbeat = time.monotonic()
        reader = getattr(response, "read1", response.read)
        with dest.open("wb") as handle:
            while True:
                _remaining(deadline)
                chunk = reader(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if total:
                    percent = int(downloaded * 100 / total)
                    if percent // 10 != last:
                        last = percent // 10
                        log(f"下载 {dest.name}: {percent}%")
                elif time.monotonic() - heartbeat >= HEARTBEAT:
                    log(f"下载 {dest.name}: {downloaded} 字节")
                    heartbeat = time.monotonic()
        if total and downloaded != total:
            raise RuntimeError(f"下载长度不匹配：{downloaded} != {total}")


def download(
    url: str,
    dest: Path,
    *,
    log: Callable[[str], None] | None = None,
    mirrors: list[str] | None = None,
    sha256: str | None = None,
) -> Path:
    logger = log or (lambda _line: None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    candidates = list(dict.fromkeys([url, *(mirrors or [])]))
    last_error: Exception | None = None
    for candidate in candidates:
        # Only a complete, verified attempt may replace a previously good file.
        with tempfile.TemporaryDirectory(prefix=f".{dest.name}-", dir=dest.parent) as tmp:
            partial = Path(tmp) / dest.name
            deadline = time.monotonic() + TOTAL_TIMEOUT
            try:
                logger(f"下载源：{urlsplit(candidate).hostname or '未知'}")
                size, ranges = _probe(candidate, deadline)
                if size and ranges:
                    try:
                        _parallel(candidate, partial, size, logger, deadline)
                    except RangeUnsupported:
                        logger("服务器不支持分片，改用单线程")
                        _single(candidate, partial, logger, deadline, size)
                else:
                    _single(candidate, partial, logger, deadline, size)
                _remaining(deadline)
                if size and partial.stat().st_size != size:
                    raise RuntimeError(f"下载大小不匹配：{partial.stat().st_size} != {size}")
                if sha256:
                    digest = hashlib.sha256()
                    with partial.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if digest.hexdigest().lower() != sha256.lower():
                        raise RuntimeError("下载 SHA256 校验失败")
                _remaining(deadline)
                partial.replace(dest)
                return dest
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger(f"下载失败：{exc}")
    raise RuntimeError(f"所有下载源均失败：{last_error}")
