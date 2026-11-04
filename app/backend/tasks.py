"""Sanitized read-only view of actual PC tool progress."""
from __future__ import annotations

import json
from pathlib import Path
import re
import stat
import time

from backend import config_store, pc_access

LABELS = {"run": "执行工作目录命令", "write": "写入作品文件", "read": "读取工作目录文件", "list": "检查工作目录",
    "desktop_status": "查看桌面状态", "desktop_elements": "读取桌面控件", "desktop_screenshot": "按需截取桌面",
    "desktop_move": "移动鼠标", "desktop_click": "点击桌面控件", "desktop_scroll": "滚动桌面页面",
    "desktop_type_text": "输入已授权文字", "desktop_press_keys": "发送桌面快捷键", "desktop_open_app": "打开应用或网页"}


def snapshot(limit=30):
    if type(limit) is not int or not 1 <= limit <= 60:
        raise ValueError("操作记录数量须为1至60")
    raw = config_store.load().get("pc_access") or {}
    raw = raw if isinstance(raw, dict) else {}
    root = Path(str(raw.get("workspace") or pc_access.DEFAULT_WORKSPACE)).expanduser()
    path = root / ".dafeiyu/tasks.json"
    rows = []
    try:
        for candidate in (root, path.parent, path):
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("操作记录路径不可为链接")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 256 * 1024:
            raise ValueError("操作记录文件不正确")
        with path.open("rb") as handle:
            encoded = handle.read(256 * 1024 + 1)
        if len(encoded) > 256 * 1024:
            raise ValueError("操作记录文件过大")
        data = json.loads(encoded)
        if data.get("schema") != 1 or not isinstance(data.get("tasks"), list):
            raise ValueError("操作记录版本不正确")
        now = time.time()
        for item in data["tasks"][-60:]:
            if not isinstance(item, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(item.get("id", ""))) or item.get("action") not in LABELS:
                continue
            start, end, status = item.get("started_at"), item.get("ended_at"), item.get("status")
            if type(start) not in (int, float) or not 0 < start <= now + 30 or status not in {"running", "completed", "failed"}:
                continue
            if status != "running" and (type(end) not in (int, float) or not start <= end <= now + 30):
                continue
            # We cannot infer process death from a file. Old unfinished work is
            # marked unconfirmed, never fabricated as complete or definitely failed.
            if status == "running" and now - start > 360:
                status = "unconfirmed"
            row = {"id": item["id"], "action": item["action"], "label": LABELS[item["action"]],
                   "status": status, "started_at": start, "ended_at": end if status in {"completed", "failed"} else None,
                   "summary": {"running": "工具正在执行，尚未确认结果。", "completed": "工具调用已完成。",
                       "failed": "工具未完成，请在当前 QQ 对话查看具体原因。", "unconfirmed": "没有收到完成记录，结果尚未确认。"}[status]}
            code = item.get("exit_code")
            if type(code) is int and -2147483648 <= code <= 2147483647:
                row.update(exit_code=code, summary=f"命令已结束，退出码 {code}。")
            relative = item.get("artifact")
            if isinstance(relative, str) and len(relative) <= 512 and "\\" not in relative and not relative.startswith("/"):
                parts = relative.split("/")
                if all(p not in {"", ".", "..", ".dafeiyu"} and ":" not in p for p in parts):
                    candidate = root.joinpath(*parts)
                    try:
                        for parent in (candidate, *candidate.parents):
                            if parent == root:
                                break
                            info = parent.lstat()
                            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                                raise ValueError("作品不可为链接")
                        if candidate.is_file() and root.resolve() in candidate.resolve().parents:
                            row.update(artifact=relative, summary="作品文件已保存，可在作品箱打开。")
                    except (OSError, ValueError):
                        pass
            rows.append(row)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ok": True, "active": [], "recent": [], "source": "tool_journal", "available": False,
                "message": "操作记录暂不可读取；不会据此推断任务已完成。"}
    rows.sort(key=lambda item: item["started_at"], reverse=True)
    return {"ok": True, "active": [item for item in rows if item["status"] == "running"],
            "recent": rows[:limit], "source": "tool_journal", "available": True,
            "message": "仅显示本机授权工具实际记录的步骤；不代表整个多步骤任务已经完成。"}
