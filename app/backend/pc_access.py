"""Owner-only computer access: permission, workspace, artifacts, audit."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from backend import config_store, processes
from backend.paths import APP_HOME, ASTRBOT, ASSETS

PLUGIN_NAME = "astrbot_plugin_pc_operator"
PLUGIN_CONFIG = f"{PLUGIN_NAME}_config.json"
DEFAULT_WORKSPACE = APP_HOME / "workspace"


def _defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "workspace": str(DEFAULT_WORKSPACE),
        "timeout_s": 60,
        "private_only": True,
        "members": {},
    }


def load() -> dict[str, Any]:
    config = config_store.load()
    data = dict(_defaults())
    incoming = config.get("pc_access") or {}
    if isinstance(incoming, dict):
        data.update({key: incoming[key] for key in incoming if key in data})
    if not data.get("workspace"):
        data["workspace"] = str(DEFAULT_WORKSPACE)
    return data


def _workspace() -> Path:
    path = Path(str(load().get("workspace") or DEFAULT_WORKSPACE)).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def members() -> dict[str, dict]:
    """按 QQ 归一的授权管理员表。主人不在里面，他始终全权。"""
    raw = load().get("members") or {}
    result: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return result
    for qq, item in raw.items():
        qq = str(qq).strip()
        if not qq.isdigit() or not isinstance(item, dict):
            continue
        result[qq] = {
            "read": bool(item.get("read")),
            "write": bool(item.get("write")),
            "run": bool(item.get("run")),
        }
    return result


def member_level(perm: dict) -> str:
    """给界面用的一句话权限描述。"""
    parts = [
        label
        for key, label in (("read", "读取"), ("write", "写文件"), ("run", "执行命令"))
        if perm.get(key)
    ]
    return " + ".join(parts) if parts else "仅聊天"


def set_member(config: dict, qq: str, read: bool, write: bool, run: bool) -> dict:
    """把授权写进 config（不落盘，交给 _apply_config 统一保存并重启）。"""
    qq = str(qq).strip()
    if not qq.isdigit() or not 5 <= len(qq) <= 12:
        raise ValueError("QQ 号必须是 5 到 12 位数字")
    pc = config.setdefault("pc_access", {})
    table = pc.setdefault("members", {})
    if not isinstance(table, dict):
        table = {}
        pc["members"] = table
    if not (read or write or run):
        table.pop(qq, None)
        return table
    table[qq] = {"read": bool(read), "write": bool(write), "run": bool(run)}
    return table


def drop_member(config: dict, qq: str) -> None:
    table = (config.get("pc_access") or {}).get("members")
    if isinstance(table, dict):
        table.pop(str(qq).strip(), None)


def _write_plugin_config(data: dict[str, Any]) -> Path:
    owner = str(config_store.load().get("owner_qq") or "").strip()
    plugin_config = {
        "enabled": bool(data.get("enabled")),
        "owner_qq": owner,
        "members": members(),
        "workspace": str(_workspace()),
        "timeout_s": max(5, min(300, int(data.get("timeout_s") or 60))),
        "max_output_chars": 8000,
        "private_only": bool(data.get("private_only", True)),
    }
    directory = ASTRBOT / "data" / "config"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PLUGIN_CONFIG
    path.write_text(
        json.dumps(plugin_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _install_plugin() -> Path:
    from backend import plugins

    plugins.install_bundled(plugin_ids=[PLUGIN_NAME])
    return plugins.plugin_path(PLUGIN_NAME)


def _restart_astrbot() -> None:
    if processes.port_up(6185):
        processes.stop_astrbot()
        time.sleep(0.8)
        processes.start_astrbot()
    else:
        processes.start_astrbot()
    processes.wait_port(6185, 45)


def configure(
    enabled: bool,
    workspace: str,
    timeout_s: int = 60,
    private_only: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    if enabled and not confirm:
        raise ValueError("开启电脑权限需要明确确认风险")
    owner = str(config_store.load().get("owner_qq") or "").strip()
    if enabled and not owner:
        raise ValueError("请先在设置里填写主人 QQ")
    target = Path(str(workspace or DEFAULT_WORKSPACE)).expanduser()
    if not target.is_absolute():
        raise ValueError("工作目录必须是绝对路径")
    target.mkdir(parents=True, exist_ok=True)
    if not os.access(target, os.W_OK):
        raise ValueError("工作目录不可写")
    data = {
        "enabled": bool(enabled),
        "workspace": str(target.resolve()),
        "timeout_s": max(5, min(300, int(timeout_s or 60))),
        "private_only": bool(private_only),
    }
    config = config_store.load()
    # 保留已授权的管理员，否则每次开/关电脑权限都会把成员权限清空。
    existing = config.get("pc_access") or {}
    data["members"] = existing.get("members") if isinstance(existing.get("members"), dict) else {}
    config["pc_access"] = data
    config_store.save(config)
    if data["enabled"]:
        _install_plugin()
    _write_plugin_config(data)
    if (ASTRBOT / "data" / "plugins" / PLUGIN_NAME).is_dir():
        _restart_astrbot()
    return status()


def _plugin_version(directory: Path) -> str:
    meta = directory / "metadata.yaml"
    if not meta.is_file():
        return ""
    try:
        for line in meta.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("version:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def ensure_plugin_current() -> bool:
    """安装包内的插件比运行目录新时自动替换，避免升级后还跑旧版。

    老版本是“仅主人”，新版本支持授权管理员；不替换的话界面给了权限、
    旧插件却仍然只认主人，用户只会看到“你不是主人”。
    """
    source = ASSETS / "plugins" / PLUGIN_NAME
    target = ASTRBOT / "data" / "plugins" / PLUGIN_NAME
    if not source.is_dir() or not target.is_dir():
        return False
    if _plugin_version(source) == _plugin_version(target):
        return False
    _install_plugin()
    return True


def sync_owner() -> bool:
    """Rewrite the plugin config so it follows the current owner.

    Called after a config change: ``owner_qq`` lives in both the app config and
    the plugin's own config, and the plugin only accepts commands from the owner
    it knows about. Without this the new owner would be rejected until the user
    re-saved the 电脑权限 page.
    """
    path = ASTRBOT / "data" / "config" / PLUGIN_CONFIG
    if not path.exists():
        return False
    _write_plugin_config(load())
    return True


def _audit_tail(limit: int = 5) -> list[dict]:
    path = _workspace() / ".dafeiyu" / "audit.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
        output = []
        for line in lines:
            try:
                item = json.loads(line)
                output.append(
                    {
                        "at": item.get("at"),
                        "sender": item.get("sender"),
                        "action": item.get("action"),
                        "command": str((item.get("detail") or {}).get("command") or (item.get("detail") or {}).get("path") or "")[:300],
                        "result": str(item.get("result") or "")[:300],
                    }
                )
            except Exception:
                continue
        return output
    except Exception:
        return []


def status() -> dict[str, Any]:
    data = load()
    plugin_dir = ASTRBOT / "data" / "plugins" / PLUGIN_NAME
    config_path = ASTRBOT / "data" / "config" / PLUGIN_CONFIG
    return {
        **data,
        "members": members(),
        "workspace": str(_workspace()),
        "plugin_installed": plugin_dir.is_dir(),
        "config_exists": config_path.is_file(),
        "audit": _audit_tail(),
    }


def _resolve(relative: str) -> Path:
    workspace = _workspace()
    target = (workspace / str(relative or ".")).resolve()
    if target != workspace and workspace not in target.parents:
        raise ValueError("路径超出工作目录")
    return target


def list_artifacts(limit: int = 200) -> list[dict]:
    workspace = _workspace()
    items: list[dict] = []
    if not workspace.exists():
        return items
    for current, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d != ".dafeiyu")
        for name in sorted(files):
            path = Path(current) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                relative = str(path.relative_to(workspace)).replace("\\", "/")
            except ValueError:
                continue
            items.append(
                {
                    "path": relative,
                    "name": name,
                    "is_dir": False,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "kind": path.suffix.lower().lstrip("."),
                }
            )
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    items.sort(key=lambda item: item["modified"], reverse=True)
    return items


def open_artifact(relative: str) -> None:
    target = _resolve(relative)
    if not target.exists():
        raise ValueError("产物不存在")
    if target.is_dir():
        subprocess.Popen(["explorer", str(target)])
    else:
        os.startfile(str(target))  # type: ignore[attr-defined]


def reveal_artifact(relative: str) -> None:
    target = _resolve(relative)
    if not target.exists():
        raise ValueError("产物不存在")
    subprocess.Popen(["explorer", "/select,", str(target)])


def open_workspace() -> None:
    subprocess.Popen(["explorer", str(_workspace())])
