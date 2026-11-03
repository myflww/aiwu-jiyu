# 大肥鱼电脑操作（主人 + 授权管理员）

默认关闭。桌面端「概览 → 电脑权限」里明确确认后，才会把 `enabled` 设为 true。

角色：

- 主人（`owner_qq`）：可使用总开关明确开放的能力；桌面操作还需 `desktop_enabled`。
- 授权管理员（`members.<QQ>`）：按 `read` / `write` / `run` / `desktop` 四个开关逐项放行。
- 其他白名单成员：只能聊天，调用工具会被拒绝并回一句“没有权限”。

能力：

- `pc_write_file` / `pc_read_file` / `pc_list_files`：只在工作目录内，分别需要 `write` / `read` / `read`。
- `pc_run_command`：在工作目录内执行 PowerShell，需要 `run`；带超时、输出上限、命令审计。
- `/pc <命令>`：有 `run` 权限的人在 QQ 私聊里直接执行，绕过模型工具选择。
- `pc_desktop_status` / `pc_desktop_elements` / `pc_desktop_screenshot`：真实 Windows 窗口、控件和按需截图。
- `pc_desktop_move` / `pc_desktop_click` / `pc_desktop_scroll` / `pc_desktop_type_text` / `pc_desktop_press_keys` / `pc_desktop_open_app`：鼠标、键盘与应用操作，需要完整桌面权限。修改桌面前必须读取当前窗口并传入其 `window_id`，窗口改变则重新读取。
- 桌面工具只接受真实 QQ 私聊，拒绝其他平台、机器人自身和伪造的会话来源。它们不依赖 AstrBot 内置 ComputerUse 开关；请求内只加入当前调用者有权使用的工具，不修改全局工具注册状态。

边界：

- 不是操作系统沙箱。命令仍以当前 Windows 用户权限运行；高危命令做了正则拦截，但不能保证覆盖所有绕过方式。
- 完整桌面操控会作用于当前 Windows 桌面，默认关闭，不持续截屏、不绕过锁屏或 UAC。文本模型根据控件树与坐标操作，截图不会自动赋予模型视觉能力。
- 高危正则对所有人生效，包括主人：关机、重启、格式化、diskpart、bcdedit、改注册表、删系统盘、结束进程、改执行策略等。
- 只建议在自己信任的电脑上开启；给“执行命令”权限等于把脚本执行能力交给对方。
- 群聊一律拒绝（`private_only`）；否则任何群成员都可能通过提示词诱导模型调用工具。
- 审计写入 `<workspace>/.dafeiyu/audit.jsonl`，记录发送者、角色、命令与结果摘要。
