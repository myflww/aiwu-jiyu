"""直接调用插件类，验证不同身份 × 不同权限的放行结果。"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "runtime" / "astrbot" / "venv" / "Lib" / "site-packages"
sys.path.insert(0, str(SITE))

spec = importlib.util.spec_from_file_location(
    "pc_op_test", ROOT / "assets" / "plugins" / "astrbot_plugin_pc_operator" / "main.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

from astrbot.core.platform.message_type import MessageType  # noqa: E402


class FakeEvent:
    def __init__(self, sender, message_type=MessageType.FRIEND_MESSAGE):
        self._sender = sender
        self._type = message_type

    def get_sender_id(self):
        return self._sender

    def get_message_type(self):
        return self._type


CONFIG = {
    "enabled": True,
    "owner_qq": "123456789",
    "workspace": str(ROOT / "workspace"),
    "timeout_s": 30,
    "private_only": True,
    "members": {
        "30000001": {"read": True, "write": True, "run": False},
        "30000002": {"read": True, "write": True, "run": True},
        "30000003": {"read": False, "write": False, "run": False},
    },
}

plugin = module.PcOperator(context=None, config=CONFIG)

CASES = [
    ("主人 read", FakeEvent("123456789"), "read", True),
    ("主人 write", FakeEvent("123456789"), "write", True),
    ("主人 run", FakeEvent("123456789"), "run", True),
    ("助手 read", FakeEvent("30000001"), "read", True),
    ("助手 write", FakeEvent("30000001"), "write", True),
    ("助手 run（应拒）", FakeEvent("30000001"), "run", False),
    ("操作员 run", FakeEvent("30000002"), "run", True),
    ("仅聊天 read（应拒）", FakeEvent("30000003"), "read", False),
    ("仅聊天 write（应拒）", FakeEvent("30000003"), "write", False),
    ("陌生人 read（应拒）", FakeEvent("99999999"), "read", False),
    ("群里主人（应拒）", FakeEvent("123456789", MessageType.GROUP_MESSAGE), "read", False),
]

failed = 0
for label, event, need, expected in CASES:
    perm, error = plugin._check(event, need)
    actual = perm is not None
    mark = "PASS" if actual == expected else "FAIL"
    if actual != expected:
        failed += 1
    detail = f"role={perm['role']}" if perm else error
    print(f"[{mark}] {label:22s} allowed={actual}  {detail}")

print("\n--- 高危命令拦截（操作员，有 run 权限）---")
event = FakeEvent("30000002")
for command in ["shutdown /s /t 0", "Format-Volume -DriveLetter D", "Remove-Item C:\\ -Recurse"]:
    out = plugin._run(event, command)
    blocked = "安全策略拒绝" in out
    print(f"[{'PASS' if blocked else 'FAIL'}] {command[:40]:42s} -> {out[:40]}")
    if not blocked:
        failed += 1

print("\n--- 正常命令仍可执行（操作员）---")
out = plugin._run(event, "Write-Output ok")
print(out[:120])
if "ok" not in out:
    failed += 1

print(f"\n失败用例：{failed}")
sys.exit(1 if failed else 0)
