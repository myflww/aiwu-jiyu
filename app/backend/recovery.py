"""Local, explicit recovery points for nonsecret preferences and shipped plugins.

No chat database, model/QQ credentials, permission grants, private image library,
LoveHouse data or third-party plugin is copied. Plugin bytes must still match
this executable's reviewed bundle. A settings restore is not a binary rollback.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import uuid
import zipfile

from backend import config_store, plugins, processes
from backend.paths import APP_HOME, ASSETS

MAX_BACKUPS = 5
MAX_FILES = 512
MAX_BYTES = 128 * 1024 * 1024
MAX_MEMBER = 8 * 1024 * 1024
_ID = re.compile(r"[a-f0-9]{32}\Z")
_VERSION = re.compile(r"\d+\.\d+\.\d+\Z")
_lock = threading.RLock()
# Only preferences, never identity, API base/key, memory bindings or grants.
PREFERENCES = {
    "companion": {"proactive_enabled", "emotion_enabled", "whale_style_enabled",
        "natural_bubbles_enabled", "natural_bubbles_frequency", "interval_minutes", "daily_limit", "quiet_start", "quiet_end",
        "schedule_mode", "min_interval_minutes", "max_interval_minutes", "idle_minutes", "trigger_probability",
        "unanswered_decay_percent", "max_unanswered", "session_expiry_days", "timezone", "profile"},
    "stickers": {"auto_match_enabled", "cooldown_seconds", "pack_selection", "auto_probability", "scenario_policy", "custom_enabled"},
    "ui": {"onboarded", "mascot_style"},
}


def _version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("恢复点的软件版本不受支持")
    return value


def _current_version():
    from backend.updater import current_version
    return current_version()


def _home():
    return APP_HOME / "updates" / "recovery"


def _safe(path: Path, anchor: Path, *, file=False):
    # Do not resolve first: a junction must never silently become an allowlist.
    if path != anchor and anchor not in path.parents:
        raise ValueError("恢复路径超出允许目录")
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            s = item.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
                raise ValueError("恢复路径不可为链接")
            if item == path and file and (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1):
                raise ValueError("恢复文件须是普通文件")
        if item == anchor:
            return path
    raise ValueError("恢复目录不匹配")


def _read(path: Path, anchor: Path, limit=MAX_MEMBER):
    _safe(path, anchor, file=True)
    before = path.lstat()
    if before.st_size > limit:
        raise ValueError("恢复文件超过大小限制")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("恢复文件在读取时发生变化")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("恢复文件超过大小限制")
    return raw


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity():
    return _sha(str(APP_HOME.absolute()).casefold().encode())


def _preferences(config):
    output = {}
    for section, allowed in PREFERENCES.items():
        source = config.get(section)
        if not isinstance(source, dict):
            continue
        clean = {}
        for key in allowed:
            value = source.get(key)
            # Scalar bounded values only; descriptions, paths and arbitrary JSON
            # cannot accidentally carry credentials into an unencrypted backup.
            if isinstance(value, bool) or type(value) is int and -1 <= value <= 100000:
                clean[key] = value
            elif isinstance(value, str) and len(value) <= 48 and re.fullmatch(r"[A-Za-z0-9_./+:-]+", value):
                clean[key] = value
        output[section] = clean
    return output


def _bundle():
    result = {}
    manifest = json.loads((ASSETS / "plugins/manifest.json").read_text("utf-8"))
    for package in manifest["plugins"]:
        identifier = package["id"]
        if not re.fullmatch(r"astrbot_plugin_[a-z0-9_]+", identifier):
            raise ValueError("随附插件清单不正确")
        for row in package["files"]:
            relative = row["path"]
            if not isinstance(relative, str) or "\\" in relative or relative.startswith("/") or any(p in {"", ".", ".."} for p in relative.split("/")):
                raise ValueError("随附插件路径不正确")
            result[f"plugins/{identifier}/{relative}"] = (identifier, relative, row["sha256"], row["size"])
    if len(result) > MAX_FILES:
        raise ValueError("随附插件数量超过恢复限制")
    return result


def _path(backup_id):
    if not isinstance(backup_id, str) or not _ID.fullmatch(backup_id):
        raise ValueError("恢复点标识不正确")
    path = _home() / (backup_id + ".zip")
    return _safe(path, APP_HOME, file=True)


def _archive(backup_id, *, payload=False):
    path = _path(backup_id)
    raw = _read(path, APP_HOME, MAX_BYTES)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if (len(names) > MAX_FILES + 2 or len(names) != len(set(names))
                or sum(i.file_size for i in infos) > MAX_BYTES
                or any(i.file_size > MAX_MEMBER or i.is_dir() for i in infos)):
            raise ValueError("恢复点内容超过限制或不完整")
        if "manifest.json" not in names or "preferences.json" not in names:
            raise ValueError("恢复点缺少清单")
        if archive.getinfo("manifest.json").file_size > 256 * 1024 or archive.getinfo("preferences.json").file_size > 64 * 1024:
            raise ValueError("恢复点说明超过限制")
        manifest = json.loads(archive.read("manifest.json"))
        if (not isinstance(manifest, dict) or manifest.get("schema") != 1 or manifest.get("id") != backup_id
                or manifest.get("home") != _identity()):
            raise ValueError("恢复点不属于这份本机资料")
        _version(manifest.get("source_version"))
        created = manifest.get("created_at")
        if type(created) not in (int, float) or not 0 < created <= time.time() + 300:
            raise ValueError("恢复点时间不正确")
        rows = manifest.get("files")
        if not isinstance(rows, dict) or set(names) != {"manifest.json", *rows} or "preferences.json" not in rows:
            raise ValueError("恢复点清单与文件不匹配")
        body = {}
        for name, meta in rows.items():
            if name != "preferences.json" and (not re.fullmatch(r"plugins/astrbot_plugin_[a-z0-9_]+/[A-Za-z0-9_./-]+", name)
                    or any(part in {"", ".", ".."} for part in name.split("/"))):
                raise ValueError("恢复点含不允许的文件")
            if not isinstance(meta, dict) or meta.get("size") != archive.getinfo(name).file_size:
                raise ValueError("恢复点大小不匹配")
            value = archive.read(name)
            if _sha(value) != meta.get("sha256"):
                raise ValueError("恢复点校验未通过")
            if payload:
                body[name] = value
        return manifest, body


def _summary(manifest):
    compatible = manifest["source_version"] == _current_version()
    return {"id": manifest["id"], "created_at": manifest["created_at"], "source_version": manifest["source_version"],
            "target_version": manifest.get("target_version", ""), "reason": manifest.get("reason", "manual"),
            "plugin_files": len(manifest["files"]) - 1, "bytes": sum(x["size"] for x in manifest["files"].values()),
            "compatible": compatible, "restorable": compatible}


def list_backups():
    with _lock:
        _safe(_home(), APP_HOME)
        rows = []
        ignored = 0
        if _home().is_dir():
            paths = sorted(_home().glob("*.zip"), key=lambda p: p.name)[:100]
            for path in paths:
                try:
                    manifest, _ = _archive(path.stem)
                    rows.append(_summary(manifest))
                except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile):
                    ignored += 1
        rows.sort(key=lambda x: x["created_at"], reverse=True)
        return {"ok": True, "backups": rows, "ignored": ignored, "limit": MAX_BACKUPS,
                "scope": "非敏感陪伴/配图/界面偏好与清单内的随包插件；不包含账号密钥、授权成员、聊天、记忆或个人表情库。"}


def create_backup(reason="manual", source_version=None, target_version=""):
    if reason not in {"manual", "before_update", "before_restore", "before_rollback"}:
        raise ValueError("恢复点原因不正确")
    source_version = _version(source_version or _current_version())
    if target_version:
        _version(target_version)
    with _lock:
        _safe(_home(), APP_HOME)
        _home().mkdir(parents=True, exist_ok=True)
        payload = {"preferences.json": (json.dumps(_preferences(config_store.load()), ensure_ascii=False, sort_keys=True) + "\n").encode()}
        for name, (identifier, relative, digest, size) in _bundle().items():
            directory = plugins.plugin_path(identifier)
            source = directory / relative
            if not source.exists():
                continue
            content = _read(source, APP_HOME)
            if len(content) != size or _sha(content) != digest:
                raise ValueError("随包插件存在本地修改，无法制作已核验的恢复点；请先保留自定义修改")
            payload[name] = content
        if sum(map(len, payload.values())) > MAX_BYTES:
            raise ValueError("恢复点超过容量限制")
        identifier = uuid.uuid4().hex
        manifest = {"schema": 1, "id": identifier, "home": _identity(), "source_version": source_version,
                    "target_version": target_version, "created_at": time.time(), "reason": reason,
                    "files": {name: {"size": len(raw), "sha256": _sha(raw)} for name, raw in payload.items()}}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=_home(), prefix=".recovery-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
                    for name, content in payload.items():
                        archive.writestr(name, content)
            os.replace(temporary, _path(identifier))
            temporary = None
            _archive(identifier)  # read-back verification before pruning anything
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        # Only validated, home-bound single ZIPs are eligible for retention.
        for old in list_backups()["backups"][MAX_BACKUPS:]:
            _path(old["id"]).unlink()
        return {"ok": True, "backup": _summary(manifest), "scope": list_backups()["scope"]}


def rollback_version(backup_id):
    with _lock:
        manifest, _ = _archive(backup_id)
        return _version(manifest["source_version"])


def _atomic(path, content):
    _safe(path, APP_HOME, file=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".recover-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _plugin_preferences(config):
    """Plan the same merges as runtime sync, without writing outside the txn.

    Existing owner/bot identifiers and unrelated plugin fields are taken from
    the current profile, never a recovery ZIP. The current desktop identity is
    authoritative, exactly as normal companion sync does on application boot.
    """
    from backend import companion, stickers
    directory = APP_HOME / "runtime/astrbot/data/config"
    updates = []
    for identifier in (companion.PLUGIN_NAME, stickers.PLUGIN_ID):
        path = directory / (identifier + "_config.json")
        original = _read(path, APP_HOME, 128 * 1024) if path.exists() else None
        existing = json.loads(original) if original is not None else {}
        if not isinstance(existing, dict):
            raise ValueError("当前插件偏好不是有效对象，已停止恢复")
        merged = dict(existing)
        if identifier == companion.PLUGIN_NAME:
            incoming = config.get("companion")
            if not isinstance(incoming, dict):
                incoming = {key: existing[key] for key in companion.DEFAULTS if key in existing}
                if existing.get("enabled") is False:
                    incoming.update({key: False for key in companion._policy.BOOL_FIELDS})
            merged.update(companion._policy.normalize(incoming))
            merged.update(owner_qq=str(config.get("owner_qq") or ""), bot_qq=str(config.get("bot_qq") or ""), persona_id="大肥鱼")
        else:
            merged.update(stickers._effective_settings(config, existing))
            merged.setdefault("enabled", True)
        updates.append((path, (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode(), original))
    return updates


def restore(body):
    if not isinstance(body, dict) or set(body) != {"backup_id", "confirmed"} or body.get("confirmed") is not True:
        raise ValueError("请先确认恢复所选偏好和随包插件")
    with _lock:
        state = processes.status()
        if any(state.get(name, {}).get("up") is True for name in ("astrbot", "napcat", "onebot")):
            raise ValueError("请先在首页关闭聊天服务，再恢复；软件不会替你停止服务")
        manifest, payload = _archive(body["backup_id"], payload=True)
        if manifest["source_version"] != _current_version():
            raise ValueError("恢复点属于另一软件版本；请先下载并安装匹配旧版，再恢复同版本偏好与插件")
        expected = _bundle()
        changes = []
        for name, raw in payload.items():
            if name == "preferences.json":
                continue
            if name not in expected or _sha(raw) != expected[name][2] or len(raw) != expected[name][3]:
                raise ValueError("恢复点插件与当前版本的核验清单不匹配")
            identifier, relative, _, _ = expected[name]
            path = plugins.plugin_path(identifier) / relative
            _safe(path, APP_HOME, file=True)
            changes.append((path, raw, _read(path, APP_HOME) if path.exists() else None))
        prefs = json.loads(payload["preferences.json"])
        if _preferences(prefs) != prefs:
            raise ValueError("恢复点偏好含不支持的字段或值")
        # Merge selected nonsecret fields into CURRENT configuration. Identity,
        # credentials, workspace and grants are not taken from a stale snapshot.
        original_config = _read(config_store.CONFIG_FILE, APP_HOME, 2 * 1024 * 1024) if config_store.CONFIG_FILE.exists() else None
        cfg = json.loads(original_config) if original_config is not None else {}
        if not isinstance(cfg, dict):
            raise ValueError("当前配置不是有效对象，已停止恢复")
        for section, values in prefs.items():
            cfg.setdefault(section, {}).update(values)
        changes.extend(_plugin_preferences(cfg))
        changes.append((config_store.CONFIG_FILE, (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode(), original_config))
        safety = create_backup("before_restore")
        written = []
        try:
            for path, raw, original in changes:
                if (_read(path, APP_HOME) if path.exists() else None) != original:
                    raise ValueError("恢复期间文件发生变化，已停止恢复")
                _atomic(path, raw)
                written.append((path, original))
        except Exception:
            for path, original in reversed(written):
                if original is None:
                    _safe(path, APP_HOME, file=True).unlink(missing_ok=True)
                else:
                    _atomic(path, original)
            raise
        return {"ok": True, "restored": manifest["id"], "files": len(written), "safety_backup": safety["backup"],
                "message": "偏好和核验后的随包插件已恢复；账号密钥、权限成员、聊天、记忆及个人图库保留当前内容。下次开启服务时生效。"}
