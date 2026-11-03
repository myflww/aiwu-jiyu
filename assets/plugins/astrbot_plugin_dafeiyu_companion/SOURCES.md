# 功能参考与兼容适配

本插件以 AstrBot 的 Python 插件接口独立实现。下列 DSH 项目用于理解用户要求的功能和表达风格；没有复制它们的运行时代码、整段人格提示词、代理指令或素材，也没有下载执行外部插件。

## Aurabot · 住在 QQ 里的大肥鱼

- 项目：https://github.com/aureth-labs/dsh-aurabot-qq
- 已核对提交：`b3301f6b014016e141c07d266a716c0fea566aab`
- 已核对文件：`README.md`、`package.json`、`src/modules/API.md`、`src/modules/speakup/index.js`、`src/modules/mood/index.js`、`src/modules/stickers/index.js`、`LICENSE`。
- 参考功能：主动搭话、心情上下文、按回复情绪挑选表情，以及功能分模块开关的方式。
- 原项目使用 DeepSeek Harness、Node.js ESM 与独立 OneBot 通道，不能作为 AstrBot 的 Python 插件直接加载。爱屋及鱼继续使用现有 NapCat 和 AstrBot，不增加第二套 QQ 桥接，也不替换爱之屋记忆。
- 代码许可证：MIT；版权所有者为 `Copyright (c) 2026 Aurabot contributors`。原许可逐字保留在 `references/LICENSE-dsh-aurabot-qq.txt`。
- 素材范围：原项目 README 另行说明其桌宠形象与动画采用 CC BY-NC-SA 4.0，并由独立素材仓库提供。这些素材未被引入，本次不能把代码的 MIT 许可推广到它们。

## 大肥鱼鲸鱼娘人设

- 项目：https://github.com/Kaalia0912/dsh-whale-musume-persona
- 已核对提交：`0fbdcf217e3bf94e35fef36f7ac56134b704529b`
- 已核对文件：`README.md`、`cordis.patch.yml`、`agent-presets/whale/agent.cordis.yml`、`LICENSE`。
- 参考风格：亲近的鲸鱼娘形象、轻微傲娇和甜意、自然口语，以及认真完成实际工具任务。
- 原项目通过 DSH persona/preset 生效，并提出中文推理偏好。本插件不照搬 DSH 的全局覆盖和代理指令，只在用户明确启用 `whale_style_enabled` 时，给主人私聊追加独立撰写的短表达偏好；保留现有人格、记忆、工具与权限。
- 中文行为边界：用户可见的解释尽量使用中文并简要说明结论和操作结果，不要求或展示模型隐藏思维链，也不承诺控制内部推理语言。
- “避免模板回复”指减少固定欢迎词、无关分点和客服式收尾，不是发送告别模板。
- 代码许可证：MIT；版权所有者为 `Copyright (c) 2026 dsh-whale-musume-persona contributors`。原许可逐字保留在 `references/LICENSE-dsh-whale-musume-persona.txt`。

两个参考项目的作者及许可保留各自权利。独立实现的爱屋及鱼插件代码继续按本目录 `LICENSE` 与 `NOTICE` 使用。固定源地址、提交和许可文件 SHA-256 见 `references/sources.json`。
