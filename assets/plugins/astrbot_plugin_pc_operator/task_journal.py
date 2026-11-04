"""Bounded factual tool progress; never stores prompts, commands or typed text."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import uuid

LABELS = {"run": "执行工作目录命令", "write": "写入作品文件", "read": "读取工作目录文件", "list": "检查工作目录",
    "desktop_status": "查看桌面状态", "desktop_elements": "读取桌面控件", "desktop_screenshot": "按需截取桌面",
    "desktop_move": "移动鼠标", "desktop_click": "点击桌面控件", "desktop_scroll": "滚动桌面页面",
    "desktop_type_text": "输入已授权文字", "desktop_press_keys": "发送桌面快捷键", "desktop_open_app": "打开应用或网页"}
_lock = threading.RLock()
MAX_BYTES = 256 * 1024
MAX_ITEMS = 60


def _path(workspace):
    workspace = Path(workspace)
    directory = workspace / ".dafeiyu"
    for candidate in (workspace, directory, directory / "tasks.json"):
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("操作记录路径不可为链接")
            if candidate.name == "tasks.json" and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError("操作记录须是普通文件")
    return directory / "tasks.json"


def _read(path):
    if not path.exists():
        return []
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("操作记录超过容量限制")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("操作记录超过容量限制")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("tasks"), list):
        return []
    return data["tasks"][-MAX_ITEMS:]


def _write(path, rows):
    raw = (json.dumps({"schema": 1, "tasks": rows[-MAX_ITEMS:]}, ensure_ascii=False) + "\n").encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("操作记录超过容量限制")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tasks-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def begin(workspace, action, role=""):
    if action not in LABELS:
        return ""
    with _lock:
        path = _path(workspace)
        rows = _read(path)
        identifier = uuid.uuid4().hex
        rows.append({"id": identifier, "action": action, "label": LABELS[action], "status": "running",
                     "started_at": time.time(), "ended_at": None, "summary": "工具已开始执行，尚未确认结果。",
                     "role": "owner" if role == "owner" else "authorized_member"})
        _write(path, rows)
        return identifier


def finish(workspace, identifier, *, success, artifact="", exit_code=None):
    if not identifier:
        return
    with _lock:
        path = _path(workspace)
        rows = _read(path)
        row = next((item for item in rows if item.get("id") == identifier), None)
        if not row or row.get("status") != "running":
            return
        row.update(status="completed" if success else "failed", ended_at=time.time(),
                   summary="工具调用已完成。" if success else "工具未完成，请在当前 QQ 对话查看具体原因。")
        if type(exit_code) is int and -2147483648 <= exit_code <= 2147483647:
            row["exit_code"] = exit_code
            row["summary"] = f"命令已结束，退出码 {exit_code}。"
        if artifact and isinstance(artifact, str):
            candidate = Path(workspace) / artifact
            resolved = candidate.resolve()
            root = Path(workspace).resolve()
            if root in resolved.parents and ".dafeiyu" not in candidate.relative_to(Path(workspace)).parts and candidate.is_file() and not candidate.is_symlink():
                row["artifact"] = resolved.relative_to(root).as_posix()
                row["summary"] = "作品文件已保存，可在作品箱打开。"
        _write(path, rows)
