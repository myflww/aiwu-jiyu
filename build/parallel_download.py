"""Parallel HTTP range downloader for large release assets."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_lock = threading.Lock()
_done = 0


def _fetch(url: str, start: int, end: int, path: Path, retries: int = 4) -> None:
    headers = {"User-Agent": "DafeiyuDownloader/1.0", "Range": f"bytes={start}-{end}"}
    last_error = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response:
                if response.status not in (200, 206):
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
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"range {start}-{end} failed: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output")
    parser.add_argument("size", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunk", type=int, default=4 * 1024 * 1024)
    args = parser.parse_args()

    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == args.size:
        print("already downloaded, verifying...", flush=True)
    else:
        with path.open("wb") as handle:
            handle.truncate(args.size)
        ranges = [(start, min(start + args.chunk - 1, args.size - 1)) for start in range(0, args.size, args.chunk)]
        print(f"downloading {args.size} bytes in {len(ranges)} ranges with {args.workers} workers", flush=True)
        started = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(_fetch, args.url, start, end, path) for start, end in ranges]
            for future in as_completed(futures):
                future.result()
                global _done
                with _lock:
                    _done += 1
                    if _done % 4 == 0 or _done == len(ranges):
                        elapsed = max(0.1, time.time() - started)
                        print(f"  {_done}/{len(ranges)} ranges, {elapsed:.1f}s", flush=True)

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    print(f"sha256={digest.hexdigest()}", flush=True)
    print(f"size={path.stat().st_size}", flush=True)


if __name__ == "__main__":
    main()
