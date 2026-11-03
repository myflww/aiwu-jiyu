# 触发与素材元数据研究

核对日期：2026-11-03。以下仅作为交互与架构参考，新增实现未复制上游代码，不引入其运行时，也不调用额外付费模型。

- [dsh-meme 功能说明](https://github.com/yyh-001/dsh-meme/blob/main/README.md)：按聊天气氛主动选图、可调出现频率、个人导入与图库管理。采用“允许配图”和“必须发图”分离的交互。
- [dsh-nekowhale-stickers 说明](https://github.com/Loping151/dsh-nekowhale-stickers/blob/main/README.md)：图片标签、画面与使用场景描述、文字标记及去重。社区 GIF 的原始许可和署名继续保存在 `vendor/nekowhale`，不更改其许可。
- [AstrBot Meme Manager 使用说明](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/main/README.md)及[事件处理源码](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/main/mixins/event_handlers.py)：自动发图概率只控制本轮资格，100% 仍可弃选；同一事件缓存一次概率判定，直接工具发送后不自动补图；支持按描述的候选 ID 并在发送前重新验证。这些原则由本插件重新实现为小规模候选列表与当前文字模型自选，未集成其向量服务或收图功能。
- [LunarMeal AstrBot Memes 说明](https://github.com/LunarMeal/astrbot_plugin_memes/blob/master/README.md)及[源码](https://github.com/LunarMeal/astrbot_plugin_memes/blob/master/main.py)：较早版本使用有限情绪占位符、人格图库及一次一个图片的提示。本插件进一步提供逐图场景数据、概率与冷却硬门控，以及严肃、悲伤倾诉和任务答复的避用规则。

场景规则是本项目的产品选择，并非上述项目共同提供或保证的能力。模型判断仍可能不完全准确，因此保留概率、自动间隔、手动模式、个人图库及逐张开关；无匹配或验证失败时保留文字回复。

自定义素材的图片数据只从用户主动选择的本地文件导入。标签、描述和适用/避用场景由用户填写，作为引用数据提供给当前会话的文字模型，不执行其中指令。不会自动搜图、监视 QQ 图片或上传个人图片到 GitHub。
