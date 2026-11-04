# 功能参考与兼容适配

本插件以 AstrBot 的 Python 插件接口独立实现。下列 DSH 项目用于理解用户要求的功能和表达风格；没有复制它们的运行时代码、整段人格提示词、代理指令或素材，也没有下载执行外部插件。

## Aurabot · 住在 QQ 里的大肥鱼

- 项目：https://github.com/aureth-labs/dsh-aurabot-qq
- 已核对提交：`b3301f6b014016e141c07d266a716c0fea566aab`
- 已核对文件：`README.md`、`package.json`、`src/modules/API.md`、`src/modules/speakup/index.js`、`src/features/companion.js`、`src/modules/mood/index.js`、`src/modules/stickers/index.js`、`LICENSE`。
- 参考功能：主动搭话、心情上下文、按回复情绪挑选表情，以及功能分模块开关的方式。
- 自主搭话核对：原实现由每分钟独立 `tick` 检查空闲、免打扰和名额并轮换话题角度，不依赖用户创建 cron。本适配用 AstrBot 插件生命周期启动独立后台，保持真实主人会话验证、预算先落盘和准确状态；话题角度为独立撰写，不采用原例子中编造现实经历的建议。
- 1.2.0再次核对当前HEAD与上述提交一致。`src/core/config.js` 的 `speakUp` 默认值为：安静40分钟、随机60–180分钟、每天4次、8–23点可活动。归一化允许最短5–1440、最长5–2880分钟、日上限0–20；配置缺损时部分回退值90/240/3与完整默认值不同，不应混为一谈。[固定配置源](https://github.com/aureth-labs/dsh-aurabot-qq/blob/b3301f6b014016e141c07d266a716c0fea566aab/src/core/config.js)
- 普通主动机会不含基础随机概率或“被忽略后概率衰减”。可选追问仅在上一句带问意、15–40分钟未回复时，每句最多一次，以50%机会触发；并非所有主动机会都乘50%。本适配未照搬额外追问，避免在自己的生成预算外重复联系。[固定调度源](https://github.com/aureth-labs/dsh-aurabot-qq/blob/b3301f6b014016e141c07d266a716c0fea566aab/src/features/companion.js)
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

## AstrBot 主动消息（Pancakes-Labs）

- 项目：[astrbot_plugin_proactive_chat](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat)，固定提交 `95f459ef33208aa2c123c733ba1b5d2188dd0977`。已核对 `_conf_schema.json`、`core/services/scheduler_service.py`、`core/features/gating_feature.py`、元数据及许可证。
- 当前私聊schema最短30、最长600分钟，免打扰1–7点，未回复4次暂停、0不限制；调度代码在最短/最长秒数之间随机选点并持久化，闸门核对未回复次数。README和调度缺省回退仍有最长900分钟的值，因此本适配记录差异，不把某个数值称作统一标准。[配置源](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/blob/95f459ef33208aa2c123c733ba1b5d2188dd0977/_conf_schema.json)、[调度源](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/blob/95f459ef33208aa2c123c733ba1b5d2188dd0977/core/services/scheduler_service.py)、[未回复闸门](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/blob/95f459ef33208aa2c123c733ba1b5d2188dd0977/core/features/gating_feature.py)。
- 许可证为 [AGPL-3.0](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat/blob/95f459ef33208aa2c123c733ba1b5d2188dd0977/LICENSE)。只参考随机机会、持久化和未回复暂停的行为思路，没有复制代码、提示词或其WebUI/遥测/记忆运行时。没有把AGPL代码改标为本项目非商用许可。

## AstrBot chat_plus（Him666233）

- 项目：[astrbot_plugin_group_chat_plus](https://github.com/Him666233/astrbot_plugin_group_chat_plus)，固定提交 `aa97e0e35a18733e73db753238dd2a3021446e42`。已核对 `_conf_schema.json`、`utils/proactive_chat_manager.py`、`private_chat/private_chat_utils/private_chat_proactive_chat_manager.py`、元数据及许可证。
- 当前schema基础概率0.3、安静阈值600秒、检查60秒、连续失败3次后冷却1800秒，并有互动评分、无人回应扣分和可选时段概率。旧配置文档仍写0.2/1800秒，部分类变量回退值也不同，本次以固定提交的schema和实际代码为准。[配置源](https://github.com/Him666233/astrbot_plugin_group_chat_plus/blob/aa97e0e35a18733e73db753238dd2a3021446e42/_conf_schema.json)、[概率与失败冷却代码](https://github.com/Him666233/astrbot_plugin_group_chat_plus/blob/aa97e0e35a18733e73db753238dd2a3021446e42/utils/proactive_chat_manager.py)。
- 许可证为 [AGPL-3.0](https://github.com/Him666233/astrbot_plugin_group_chat_plus/blob/aa97e0e35a18733e73db753238dd2a3021446e42/LICENSE)。只对照“概率受互动结果影响”的行为，不复制评分实现、群聊接管流程、吐槽提示词或其他代码。爱屋及鱼采用可解释的自选百分比减扰，不假称与上游算法完全一致或有实验最佳依据。

所有参考项目保留各自作者和许可。独立实现的爱屋及鱼插件代码继续按本目录 `LICENSE` 与 `NOTICE` 使用。固定源地址、提交和已保留许可文件的 SHA-256 见 `references/sources.json`。上述数值均是社区项目的配置选择，不是本项目进行过对照实验的科学结论。


## 1.3.0 连续性依据

[Generative Agents v2](https://arxiv.org/abs/2304.03442v2) 的经验记录和动态召回、[MemGPT v2](https://arxiv.org/abs/2310.08560v2) 的分层记忆管理为架构参考；[Iqbal/Bailey CHI 2008](https://doi.org/10.1145/1357054.1357070) 为尊重打扰时机与用户控制的参考。论文不提供本软件安静／活泼档分钟数或未回复概率的实验最优依据，所有产品预设可调整。代码独立编写，不复制论文项目提示词、虚构生活事件或引入新的监控。详见项目研究说明。

## 1.4.0 消息收集与交接

[When to Talk v1](https://arxiv.org/abs/1912.09879v1)、[Talking Turns ICLR2025](https://machinelearning.apple.com/research/talking-turns) 为“何时回应”与“如何组织回复”分离的依据；不把语音时间参数直接套入QQ。

核对了 [astrbot_plugin_debounce，0d8aaaf3285d535b76e2878159a9a8679e81a3f5](https://github.com/advent259141/astrbot_plugin_debounce/blob/0d8aaaf3285d535b76e2878159a9a8679e81a3f5/main.py) 的缓冲和旧响应抑制，以及 [chat_buffer](https://github.com/ctrlkk/astrbot_plugin_chat_buffer/blob/master/main.py) 的等待取消与prompt合并（2026-11-04读取，未固定构建依赖）。两者AGPL-3.0，只学习问题边界，没有复制代码/分类模型或改标许可。本实现直接挂起真实事件、完整保留媒体组件，不伪造用户事件、不新增模型判断调用。

实际验证SDK为 AstrBot4.29.4，已核对消息阶段顺序、共享会话锁、临时媒体生命周期与原生发送器；发现并修复Pydantic.v1浅复制导致packet共用text的问题。研究说明及可重现SDK spy见项目 `docs/消息收集与群聊节制研究.md`、`build/check_turn_taking_sdk.py`。
