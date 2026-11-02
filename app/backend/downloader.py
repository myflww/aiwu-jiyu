"""Parallel range downloader used by the first-run installer."""

from __future__ import annotations

import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

CHUNK = 4 * 1024 * 1024
WORKERS = 8
_lock = threading.Lock()
_done = 0


class RangeUnsupported(RuntimeError):
    pass


def _headers(start: int | None = None, end: int | None = None) -> dict:
    headers = {"User-Agent": "DafeiyuSetup/1.0"}
    if start is not None:
        headers["Range"] = f"bytes={start}-{end}"
    return headers


def _probe(url: str) -> tuple[int, bool]:
    try:
        request = urllib.request.Request(url, method="HEAD", headers=_headers())
        with urllib.request.urlopen(request, timeout=30) as response:
            size = int(response.headers.get("Content-Length") or 0)
            ranges = (response.headers.get("Accept-Ranges") or "").lower() == "bytes"
            if size:
                return size, ranges
    except Exception:
        pass
    try:
        request = urllib.request.Request(url, headers=_headers(0, 0))
        with urllib.request.urlopen(request, timeout=30) as response:
            content_range = response.headers.get("Content-Range") or ""
            if "/" in content_range:
                size = int(content_range.rsplit("/", 1)[1])
                return size, True
    except Exception:
        pass
    return 0, False


def _fetch(url: str, start: int, end: int, path: Path, retries: int = 3) -> None:
    last_error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=_headers(start, end))
            with urllib.request.urlopen(request, timeout=180) as response:
                if response.status == 200:
                    raise RangeUnsupported("server ignored range")
                if response.status != 206:
                    raise RuntimeError(f"HTTP {response.status}")
                data = response.read()
            expected = end - start + 1
            if len(data) != expected:
                raise RuntimeError(f"short read {len(data)} != {expected}")
            with _lock:
                with path.open("r+b") as handle:
                    handle.seek(start)
                    handle.write(data)
            return
        except RangeUnsupported:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"range failed: {last_error}")


def _parallel(url: str, dest: Path, size: int, log: Callable[[str], None]) -> None:
    global _done
    _done = 0
    ranges = [(start, min(start + CHUNK - 1, size - 1)) for start in range(0, size, CHUNK)]
    with dest.open("wb") as handle:
        handle.truncate(size)
    log(f"分片下载 {dest.name}：{len(ranges)} 段 / {WORKERS} 线程")
    started = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(_fetch, url, start, end, dest) for start, end in ranges]
        for future in as_completed(futures):
            future.result()
            with _lock:
                _done += 1
                if _done % 8 == 0 or _done == len(ranges):
                    elapsed = max(0.1, time.time() - started)
                    log(f"  {_done}/{len(ranges)} 段，{elapsed:.0f}s")


def _single(url: str, dest: Path, log: Callable[[str], None]) -> None:
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=180) as response, dest.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        last = -1
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            handle.write(chunk)
            downloaded += len(chunk)
            if total:
                percent = int(downloaded * 100 / total)
                if percent // 10 != last:
                    last = percent // 10
                    log(f"下载 {dest.name}: {percent}%")


def download(
    url: str,
    dest: Path,
    *,
    log: Callable[[str], None] | None = None,
    mirrors: list[str] | None = None,
) -> Path:
    logger = log or (lambda _line: None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    candidates = [url, *(mirrors or [])]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            size, ranges = _probe(candidate)
            logger(f"下载源：{candidate.split('//', 1)[-1].split('/', 1)[0]}")
            if size and ranges:
                try:
                    _parallel(candidate, dest, size, logger)
                except RangeUnsupported:
                    logger("服务器不支持分片，改用单线程")
                    _single(candidate, dest, logger)
            else:
                _single(candidate, dest, logger)
            if size and dest.stat().st_size != size:
                raise RuntimeError(f"下载大小不匹配：{dest.stat().st_size} != {size}")
            return dest
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger(f"下载失败：{exc}")
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
    raise RuntimeError(f"下载失败：{last_error}")