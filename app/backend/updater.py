"""Optional public-release updates. Downloads are verified; installation is explicit."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

from backend.paths import APP_HOME, resource_root

REPOSITORY = "myflww/aiwu-jiyu"
REPO_URL = "https://github.com/" + REPOSITORY
API_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
FEED_URL = REPO_URL + "/releases.atom"
CHECK_INTERVAL = 12 * 60 * 60
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-?rc(\d+))?$", re.I)


def version_key(value: str) -> tuple[int, ...]:
    match = _VERSION.fullmatch(str(value).strip())
    if not match:
        raise ValueError("版本号格式不受支持")
    major, minor, patch, rc = match.groups()
    return (int(major), int(minor), int(patch), 1 if rc is None else 0, int(rc or 0))


def current_version() -> str:
    try:
        value = (resource_root() / "VERSION").read_text("utf-8-sig").strip()
        version_key(value)
        return value.removeprefix("v")
    except (OSError, ValueError):
        return "1.3.1"


def _trusted_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
            and (host in {"github.com", "api.github.com"} or host.endswith(".githubusercontent.com")))


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise ValueError("更新下载跳转到了非 GitHub 地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: int = 8):
    if not _trusted_url(url):
        raise ValueError("更新地址不是可信的 GitHub HTTPS 地址")
    request = Request(url, headers={"User-Agent": "AiwuJiyu-Update/" + current_version(),
                                   "Accept": "application/vnd.github+json, application/atom+xml, */*"})
    return build_opener(_SafeRedirect()).open(request, timeout=timeout)


def _release(tag: str, assets: dict[str, str] | None = None) -> dict:
    key = version_key(tag)
    if key[3] != 1:
        raise ValueError("自动更新只使用正式版本")
    version = ".".join(str(part) for part in key[:3])
    name = f"AiwuJiyu-Setup-{version}-Windows-x64.exe"
    prefix = REPO_URL + "/releases/download/" + tag + "/"
    expected = {name: prefix + name, "SHA256SUMS.txt": prefix + "SHA256SUMS.txt"}
    if assets is not None:
        for filename, url in expected.items():
            if assets.get(filename) != url:
                raise ValueError("发布版本暂未提供匹配的 Windows 安装包和校验文件")
    return {"tag": tag, "version": version, "name": name,
            "installer_url": expected[name], "checksums_url": expected["SHA256SUMS.txt"],
            "release_url": REPO_URL + "/releases/tag/" + tag}


def _bounded_read(response, limit: int) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("更新信息超过预期大小")
    return body


def _expected_hash(contents: bytes, filename: str) -> str:
    matches = []
    for line in contents.decode("utf-8-sig").splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})[ \t]+\*?(.+?)\s*", line)
        if match and match.group(2) == filename:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError("校验清单中没有唯一匹配的安装包")
    return matches[0]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Updater:
    def __init__(self, home: Path | None = None, version: str | None = None, opener=None):
        self.home = Path(home) if home is not None else APP_HOME / "updates"
        self.version = version or current_version()
        self._open = opener or _open
        self.lock = threading.RLock()
        self.release = None
        self.installer_path = None
        self.installer_hash = ""
        self.started = False
        self.worker = None
        self.settings = {"auto_check": True, "ignored_version": "", "snoozed_until": 0}
        self.state = {"current_version": self.version, "latest_version": "", "available": False,
                      "checking": False, "downloading": False, "installing": False,
                      "progress": 0, "downloaded": False, "download_verified": False,
                      "checked_at": None, "checked_epoch": 0, "message": "", "error": "",
                      "release_url": REPO_URL + "/releases/latest"}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads((self.home / "preferences.json").read_text("utf-8"))
            self.settings["auto_check"] = data.get("auto_check", True) is True
            ignored = str(data.get("ignored_version", ""))
            if ignored:
                version_key(ignored)
            self.settings["ignored_version"] = ignored
            self.settings["snoozed_until"] = min(float(data.get("snoozed_until", 0)), time.time() + 30 * 86400)
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.home / "preferences.json.tmp"
        temporary.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.home / "preferences.json")

    def snapshot(self) -> dict:
        with self.lock:
            notify = (self.state["available"]
                      and self.settings["ignored_version"] != self.state["latest_version"]
                      and self.settings["snoozed_until"] <= time.time())
            return {"ok": True, **self.state, **self.settings, "notify": notify}

    def preferences(self, body: dict) -> dict:
        with self.lock:
            if "auto_check" in body:
                if not isinstance(body["auto_check"], bool):
                    raise ValueError("自动检查设置须为布尔值")
                self.settings["auto_check"] = body["auto_check"]
            if "ignored_version" in body:
                value = str(body["ignored_version"] or "")
                if value:
                    version_key(value)
                self.settings["ignored_version"] = value
            if "snooze_hours" in body:
                hours = max(0, min(float(body["snooze_hours"]), 24 * 30))
                self.settings["snoozed_until"] = time.time() + hours * 3600 if hours else 0
            self._save()
            return self.snapshot()

    def start(self) -> None:
        with self.lock:
            if self.started:
                return
            self.started = True

        def automatic():
            time.sleep(3)
            while True:
                with self.lock:
                    due = self.settings["auto_check"] and time.time() - self.state["checked_epoch"] >= CHECK_INTERVAL
                if due:
                    self.check()
                time.sleep(60)
        threading.Thread(target=automatic, daemon=True, name="aiwu-update-schedule").start()

    def _latest(self) -> dict:
        try:
            with self._open(API_URL, timeout=8) as response:
                data = json.loads(_bounded_read(response, 2 * 1024 * 1024))
            if data.get("draft") or data.get("prerelease"):
                raise ValueError("没有可用的正式版本")
            assets = {str(a.get("name")): str(a.get("browser_download_url")) for a in data.get("assets", [])}
            return _release(str(data["tag_name"]), assets)
        except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError):
            with self._open(FEED_URL, timeout=8) as response:
                feed = ET.fromstring(_bounded_read(response, 2 * 1024 * 1024))
            releases = []
            for entry in feed.findall("{http://www.w3.org/2005/Atom}entry"):
                for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
                    href = link.attrib.get("href", "")
                    prefix = REPO_URL + "/releases/tag/"
                    if href.startswith(prefix):
                        tag = href[len(prefix):]
                        try:
                            releases.append(_release(tag))
                        except ValueError:
                            pass
            if not releases:
                raise ValueError("暂未找到公开的正式发布版本")
            return max(releases, key=lambda r: version_key(r["version"]))

    def check(self) -> dict:
        with self.lock:
            if self.state["checking"] or self.state["downloading"] or self.state["installing"]:
                return self.snapshot()
            self.state.update(checking=True, error="", message="正在检查新版本…")
            self.worker = threading.Thread(target=self._check_worker, daemon=True, name="aiwu-update-check")
            self.worker.start()
            return self.snapshot()

    def _check_worker(self) -> None:
        try:
            release = self._latest()
            with self.lock:
                if not self.release or self.release["version"] != release["version"]:
                    self.installer_path = None
                    self.installer_hash = ""
                    self.state.update(downloaded=False, download_verified=False, progress=0)
                self.release = release
                available = version_key(release["version"]) > version_key(self.version)
                self.state.update(latest_version=release["version"], available=available,
                                  release_url=release["release_url"], error="",
                                  message="发现新版本，可以自行选择更新。" if available else "已经是最新版本。")
        except Exception:
            with self.lock:
                self.state.update(error="暂时无法连接 GitHub；请稍后重试，或打开发布页查看。", message="检查更新未完成")
        finally:
            with self.lock:
                self.state.update(checking=False, checked_epoch=time.time(),
                                  checked_at=datetime.now(timezone.utc).isoformat())

    def download(self, *, repair: bool = False) -> dict:
        with self.lock:
            if self.state["checking"] or self.state["downloading"] or self.state["installing"]:
                return self.snapshot()
            same_version_repair = (repair is True and self.release
                                   and version_key(self.release["version"]) == version_key(self.version))
            if not self.release or not (self.state["available"] or same_version_repair):
                raise ValueError("请先检查是否有新版本")
            self.state.update(downloading=True, downloaded=False, download_verified=False,
                              progress=0, error="", message="正在下载安装包…")
            release = dict(self.release)
            self.worker = threading.Thread(target=self._download_worker, args=(release,), daemon=True,
                                           name="aiwu-update-download")
            self.worker.start()
            return self.snapshot()

    def _download_worker(self, release: dict) -> None:
        temporary = self.home / (release["name"] + ".part")
        try:
            with self._open(release["checksums_url"], timeout=15) as response:
                expected = _expected_hash(_bounded_read(response, 64 * 1024), release["name"])
            self.home.mkdir(parents=True, exist_ok=True)
            count = 0
            started = time.monotonic()
            with self._open(release["installer_url"], timeout=15) as response, temporary.open("wb") as handle:
                length = int(response.headers.get("Content-Length") or 0)
                if length < 0 or length > MAX_INSTALLER_BYTES:
                    raise ValueError("安装包超过预期大小")
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MAX_INSTALLER_BYTES or time.monotonic() - started > 1800:
                        raise ValueError("下载大小或用时超过限制")
                    handle.write(chunk)
                    with self.lock:
                        self.state["progress"] = min(99, int(count * 100 / length)) if length else 0
                if length and count != length:
                    raise ValueError("安装包下载不完整")
            if _file_hash(temporary) != expected:
                raise ValueError("安装包 SHA-256 校验未通过，已停止更新")
            with temporary.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    raise ValueError("下载文件不是 Windows 安装程序")
            final = self.home / release["name"]
            os.replace(temporary, final)
            with self.lock:
                self.installer_path = final
                self.installer_hash = expected
                self.state.update(progress=100, downloaded=True, download_verified=True,
                                  error="", message="安装包已校验，可以选择安装。")
        except Exception as exc:
            with self.lock:
                message = str(exc) if isinstance(exc, ValueError) else "下载失败，请检查网络后重试。"
                self.state.update(error=message, message="下载未完成", downloaded=False, download_verified=False)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            with self.lock:
                self.state["downloading"] = False

    def install(self) -> dict:
        with self.lock:
            if self.state["installing"]:
                return self.snapshot()
            if not self.release or not self.state["download_verified"] or not self.installer_path:
                raise ValueError("请先下载并校验安装包")
            expected_path = (self.home / self.release["name"]).resolve()
            if self.installer_path.is_symlink() or self.installer_path.resolve() != expected_path:
                raise ValueError("安装包路径不匹配")
            if not expected_path.is_file() or _file_hash(expected_path) != self.installer_hash:
                self.state.update(downloaded=False, download_verified=False)
                raise ValueError("安装包已改变，请重新下载")
            # Setup may launch our new one-file executable at the same path.
            # Give that independent bootloader a fresh extraction environment.
            environment = os.environ.copy()
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            subprocess.Popen([str(expected_path)], shell=False, close_fds=True, env=environment)
            self.state.update(installing=True, message="安装程序已打开；后台机器人服务会继续运行。")
            return self.snapshot()


_instance = None
_instance_lock = threading.Lock()


def get_updater() -> Updater:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Updater()
        return _instance
