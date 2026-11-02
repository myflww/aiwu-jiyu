"""Computer access tools for the dafeiyu bot.

Two kinds of callers are allowed:

* the owner (``owner_qq``) — every tool
* granted admins (``members``) — only the tools their permission flags cover

The app writes this plugin's config and enables it only after an explicit
permission confirmation. All paths are confined to the configured workspace;
commands are audited, time-limited and screened for the most destructive
system operations. This is not an OS sandbox.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.filter.command import GreedyStr

PLUGIN_NAME = "astrbot_plugin_pc_operator"
MAX_WRITE_CHARS = 200_000
MAX_READ_CHARS = 20_000

PERM_LABELS = {
    "read": "读取文件",
    "write": "生成/修改文件",
    "run": "执行命令",
}

BLOCKED = re.compile(
    r"(?i)("
    r"format-volume|clear-disk|initialize-disk|diskpart|bcdedit|"
    r"shutdown|restart-computer|stop-computer|set-executionpolicy|"
    r"reg\s+delete|net\s+user|takeown|icacls\s+[a-z]:\\|cipher\s+/w|"
    r"vssadmin\s+delete|wbadmin\s+delete|schtasks\s+/delete|sc\s+delete|"
    r"taskkill\s+/f\s+/im|stop-process\s+-name|"
    r"remove-item\s+-recurse\s+['\"]?[a-z]:[\\/]|"
    r"remove-item\s+['\"]?[a-z]:[\\/]?['\"]?\s|"
    r"del\s+/[sfq].*[a-z]:\\|rd\s+/s.*[a-z]:\\|rmdir\s+/s.*[a-z]:\\"
    r")"
)


@register(PLUGIN_NAME, "dafeiyu", "大肥鱼电脑操作（主人 + 授权管理员）", "0.2.0")
class PcOperator(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

    # ---------- 权限 ----------

    def _members(self) -> dict[str, dict]:
        raw = self.config.get("members") or {}
        members: dict[str, dict] = {}
        if not isinstance(raw, dict):
            return members
        for qq, item in raw.items():
            qq = str(qq).strip()
            if not qq.isdigit() or not isinstance(item, dict):
                continue
            members[qq] = {
                "read": bool(item.get("read")),
                "write": bool(item.get("write")),
                "run": bool(item.get("run")),
            }
        return members

    def _perm(self, event: AstrMessageEvent) -> tuple[dict | None, str]:
        """Resolve the caller's permissions. Returns (perm, error message)."""
        if not self.config.get("enabled"):
            return None, "电脑权限还没开启。请让主人在桌面端「概览 → 电脑权限」里打开。"
        if self.config.get("private_only", True):
            if event.get_message_type() != MessageType.FRIEND_MESSAGE:
                return None, "电脑操作只允许私聊，群里不做。"
        sender = str(event.get_sender_id() or "")
        owner = str(self.config.get("owner_qq") or "").strip()
        if owner and sender == owner:
            return {"role": "owner", "read": True, "write": True, "run": True}, ""
        item = self._members().get(sender)
        if item is None:
            return None, "你还没有电脑操作权限。可以让主人在桌面端「白名单 → 权限」里给你开。"
        return {"role": "admin", **item}, ""

    def _check(self, event: AstrMessageEvent, need: str) -> tuple[dict | None, str]:
        perm, error = self._perm(event)
        if perm is None:
            return None, error
        if perm.get(need):
            return perm, ""
        label = PERM_LABELS.get(need, need)
        return None, f"你的权限里没有「{label}」。可以让主人在桌面端帮你打开这一项。"

    # ---------- 基础设施 ----------

    def _workspace(self) -> Path:
        raw = str(self.config.get("workspace") or "").strip()
        if raw:
            workspace = Path(raw).expanduser()
        else:
            local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            workspace = Path(local) / "Dafeiyu" / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace.resolve()

    def _resolve(self, relative: str) -> Path:
        workspace = self._workspace()
        target = (workspace / str(relative or ".")).resolve()
        if target != workspace and workspace not in target.parents:
            raise ValueError("路径超出大肥鱼工作目录")
        return target

    def _audit(
        self,
        event: AstrMessageEvent,
        action: str,
        detail: dict,
        result: str,
        role: str = "",
    ) -> None:
        try:
            directory = self._workspace() / ".dafeiyu"
            directory.mkdir(parents=True, exist_ok=True)
            record = {
                "at": time.time(),
                "sender": str(event.get_sender_id() or ""),
                "role": role,
                "action": action,
                "detail": detail,
                "result": result[:1600],
            }
            with (directory / "audit.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _run(self, event: AstrMessageEvent, command: str, cwd: str = ".") -> str:
        perm, error = self._check(event, "run")
        if perm is None:
            return error
        role = perm.get("role", "")
        command = str(command or "").strip()
        if not command:
            return "命令为空。"
        if BLOCKED.search(command):
            result = (
                "命令被安全策略拒绝：包含磁盘、系统账户、关机、计划任务或系统目录删除等高危操作。"
                "这类操作对所有人（包括主人）都拦截。"
            )
            self._audit(event, "run", {"command": command, "cwd": cwd}, result, role)
            return result
        try:
            cwd_path = self._resolve(cwd)
            if not cwd_path.is_dir():
                return "工作目录不存在。"
        except ValueError as exc:
            return str(exc)
        timeout = max(5, min(300, int(self.config.get("timeout_s") or 60)))
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=0x08000000,
            )
            output = (completed.stdout or "") + (("\n[stderr]\n" + completed.stderr) if completed.stderr else "")
            result = f"exit={completed.returncode}\n{output.strip()[: int(self.config.get('max_output_chars') or 8000)]}"
        except subprocess.TimeoutExpired:
            result = f"命令超时（{timeout}s），已终止。"
        except Exception as exc:  # noqa: BLE001
            result = f"执行失败：{type(exc).__name__}: {exc}"
        self._audit(event, "run", {"command": command, "cwd": cwd}, result, role)
        return result

    # ---------- 工具 ----------

    @filter.llm_tool(name="pc_run_command")
    async def pc_run_command(self, event: AstrMessageEvent, command: str, cwd: str = "."):
        """在执行一条 PowerShell 命令，返回退出码和输出。用于构建软件、运行脚本、查看目录等任务。只能操作工作目录内的文件，不要尝试修改系统设置。

        Args:
            command(string): 要执行的 PowerShell 命令
            cwd(string): 相对于工作目录的执行目录，默认 .
        """
        return self._run(event, command, cwd)

    @filter.llm_tool(name="pc_write_file")
    async def pc_write_file(self, event: AstrMessageEvent, path: str, content: str):
        """在工作目录中写入一个文本文件，自动创建父目录。用于生成网页、脚本、配置、说明文档等产物。

        Args:
            path(string): 相对于工作目录的文件路径
            content(string): 文件内容
        """
        perm, error = self._check(event, "write")
        if perm is None:
            return error
        if len(content) > MAX_WRITE_CHARS:
            return f"内容过长（上限 {MAX_WRITE_CHARS} 字符）。"
        try:
            target = self._resolve(path)
        except ValueError as exc:
            return str(exc)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            result = f"已写入 {target.relative_to(self._workspace())}（{len(content)} 字符）。"
            self._audit(event, "write", {"path": str(target)}, result, perm.get("role", ""))
            return result
        except Exception as exc:  # noqa: BLE001
            return f"写入失败：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="pc_read_file")
    async def pc_read_file(self, event: AstrMessageEvent, path: str, max_chars: int = 8000):
        """读取工作目录中的文本文件，用于检查生成结果或修改现有代码。

        Args:
            path(string): 相对于工作目录的文件路径
            max_chars(int): 最多读取的字符数，默认 8000
        """
        perm, error = self._check(event, "read")
        if perm is None:
            return error
        try:
            target = self._resolve(path)
        except ValueError as exc:
            return str(exc)
        if not target.is_file():
            return "文件不存在。"
        limit = max(100, min(MAX_READ_CHARS, int(max_chars or 8000)))
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            self._audit(event, "read", {"path": str(target)}, f"{min(len(text), limit)} 字符", perm.get("role", ""))
            return text[:limit]
        except Exception as exc:  # noqa: BLE001
            return f"读取失败：{type(exc).__name__}: {exc}"

    @filter.llm_tool(name="pc_list_files")
    async def pc_list_files(self, event: AstrMessageEvent, path: str = "."):
        """列出工作目录中的文件和子目录，用于确认产物结构。

        Args:
            path(string): 相对于工作目录的目录，默认 .
        """
        perm, error = self._check(event, "read")
        if perm is None:
            return error
        try:
            root = self._resolve(path)
        except ValueError as exc:
            return str(exc)
        if not root.is_dir():
            return "目录不存在。"
        lines = []
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != ".dafeiyu"][:40]
            relative = Path(current).relative_to(self._workspace())
            for name in sorted(files)[:60]:
                item = Path(current) / name
                try:
                    size = item.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{relative / name}  {size} bytes")
            if len(lines) >= 120:
                break
        return "\n".join(lines) if lines else "工作目录为空。"

    @filter.command("pc")
    async def pc_command(self, event: AstrMessageEvent, command: GreedyStr):
        """直接执行一条 PowerShell 命令（主人或有「执行命令」权限的管理员）。"""
        yield event.plain_result(self._run(event, str(command)))
