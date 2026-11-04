# 触发与素材元数据研究

核对日期：2026-11-03。以下仅作为交互与架构参考，新增实现未复制上游代码，不引入其运行时，也不调用额外付费模型。

- [dsh-meme 功能说明](https://github.com/yyh-001/dsh-meme/blob/main/README.md)：按聊天气氛主动选图、可调出现频率、个人导入与图库管理。采用“允许配图”和“必须发图”分离的交互。
- [dsh-nekowhale-stickers 说明](https://github.com/Loping151/dsh-nekowhale-stickers/blob/main/README.md)：图片标签、画面与使用场景描述、文字标记及去重。社区 GIF 的原始许可和署名继续保存在 `vendor/nekowhale`，不更改其许可。
- [AstrBot Meme Manager 使用说明](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/main/README.md)及[事件处理源码](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/main/mixins/event_handlers.py)：自动发图概率只控制本轮资格，100% 仍可弃选；同一事件缓存一次概率判定，直接工具发送后不自动补图；支持按描述的候选 ID 并在发送前重新验证。这些原则由本插件重新实现为小规模候选列表与当前文字模型自选，未集成其向量服务或收图功能。
- [LunarMeal AstrBot Memes 说明](https://github.com/LunarMeal/astrbot_plugin_memes/blob/master/README.md)及[源码](https://github.com/LunarMeal/astrbot_plugin_memes/blob/master/main.py)：较早版本使用有限情绪占位符、人格图库及一次一个图片的提示。本插件进一步提供逐图场景数据、概率与冷却硬门控，以及严肃、悲伤倾诉和任务答复的避用规则。

场景规则是本项目的产品选择，并非上述项目共同提供或保证的能力。模型判断仍可能不完全准确，因此保留概率、自动间隔、手动模式、个人图库及逐张开关；无匹配或验证失败时保留文字回复。

自定义素材的图片数据只从用户主动选择的本地文件导入。标签、描述和适用/避用场景由用户填写，作为引用数据提供给当前会话的文字模型，不执行其中指令。不会自动搜图、监视 QQ 图片或上传个人图片到 GitHub。

## 1.3.0 图库反馈、试选与独立素材包

核对日期：2026-11-04。

- Joachims, T. (2002), *Optimizing Search Engines using Clickthrough Data*, KDD 2002，[作者公开论文](https://www.cs.cornell.edu/people/tj/publications/joachims_02c.pdf)。论文研究相对偏好学习，并指出点击受展示位置等因素影响，不能直接等同绝对相关性。这里只采用“显式相关性反馈与隐式使用记录分开”的设计启发：使用次数仅供最近使用排序，收藏只在相同匹配条件下偏好；只有用户明确点“不合适”才调整相应场景。不宣称本插件实现RankSVM，也不将论文实验结果迁移为表情匹配效果。
- [Meme Manager 公开说明](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/ec80e0a12f7651e86276dfce247f9e05d136ad93/README.md)的多包管理、批量整理、标签主模型自选和资源包差异预览为产品参考，核对提交`ec80e0a12f7651e86276dfce247f9e05d136ad93`。实际处理仍由本项目独立实现，不引入其图床、自动收图、向量服务、额外模型或运行时。
- 本地试选采用有界文字标签命中排序；显式负反馈后相关度除以`1+反馈次数`，收藏只作为同分偏好。该衰减公式、候选数上限、导入容量和ZIP压缩比阈值属于透明的工程/产品规则，不是从论文中证明最优的参数，也不代表经过真实QQ用户实验验证。
- 独立素材更新依据GitHub官方Release API返回的`asset.digest`（SHA-256）和固定仓库下载地址验证，再核对逐图哈希、真实图像、署名及原始许可。[GitHub 官方 Release Assets REST 说明](https://docs.github.com/en/rest/releases/assets)。图片数据与可执行插件代码分开；发布包只含清单、图片和原许可文本。

验证使用隔离临时图库、伪GitHub传输和真实AstrBot组件，不发送真实QQ消息、不调用付费模型、不代用户提交图片。

## 软件1.5 自动配图与真实回执

核对日期：2026-11-04。补查[STICKERCONV（ACL2024）](https://aclanthology.org/2024.acl-long.417/)、[Selecting Stickers in Open-Domain Dialogue through Multitask Learning（Findings ACL2022）](https://aclanthology.org/2022.findings-acl.241/)及上述Meme Manager固定提交的实际包回执源码。采用“对话使用意图与图片情绪分开、可明确弃选、准备不等于送达”的设计方向；没有复现论文训练、借用实验准确率或引入辅助模型。缺标记时的短日常互动、标签命中、负反馈与上一张过滤是本项目保守工程规则。

实际SDK人格工具准备、普通/主动候选、最终图片包回执和不含正文的诊断说明见[自动表情可靠性研究与实现](https://github.com/myflww/aiwu-jiyu/blob/main/docs/自动表情可靠性研究与实现.md)。
