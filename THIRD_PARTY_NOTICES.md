# 第三方材料与许可范围

根目录 LICENSE 与 NOTICE 适用于 myflww 有权许可的“爱屋及鱼”原创部分。
以下材料和外部组件保留原有权利及许可，根目录的非商业许可不覆盖或
替换它们的许可，也不限制其许可已经授予的权利。

## 仓库内的第三方材料

- `assets/persona/`、`assets/avatars/` 和
  `assets/plugins/astrbot_plugin_face_convert/` 随大肥鱼人格资源保留上游
  `whyever77/dafeiyu-astrbot` 的 MIT 许可。许可原文见
  `assets/LICENSE-dafeiyu-astrbot.txt`，来源为
  <https://github.com/whyever77/dafeiyu-astrbot>。
- `assets/plugins/astrbot_plugin_love_house/` 来自 myflww 的爱之屋项目，
  保留该目录的 PolyForm Noncommercial License 1.0.0。来源：
  <https://github.com/myflww/love-house>。
- 界面内嵌的 Lucide 0.468.0 SVG 图标保留 ISC 许可及原始版权声明；
  完整文本见 `app/ui/LICENSE-lucide.txt`，随应用复制到
  `THIRD-PARTY-LICENSES/lucide-0.468.0/LICENSE.txt`。
  来源：<https://github.com/lucide-icons/lucide/tree/0.468.0>。
- pywebview 的依赖 proxy_tools 0.1.0 的 wheel 仅标注 MIT 而未随附
  完整许可文件。这里额外保留其源代码匹配的上游原文许可和版权声明，
  见 `assets/licenses/proxy-tools-0.1.0/`；原文为 BSD 两条款式文本，
  不将其改写为 wheel 元数据所称的 MIT。
  来源：<https://github.com/jtushman/proxy_tools>。

## 陪伴与表情兼容适配

- Aurabot (`aureth-labs/dsh-aurabot-qq`) 与鲸鱼娘人设 (`Kaalia0912/dsh-whale-musume-persona`) 用于功能与表达方式参考。AstrBot 兼容实现没有复制其 DSH 运行时代码、完整提示词或桌宠素材；原始 MIT 许可、固定提交及参考范围见 `assets/plugins/astrbot_plugin_dafeiyu_companion/references/` 与 `SOURCES.md`。
- `assets/plugins/astrbot_plugin_dafeiyu_stickers/vendor/nekowhale/` 引入 `Loping151/dsh-nekowhale-stickers` 的精选元数据和原始 GIF。该上游代码许可为 Apache-2.0，元数据为 CC BY-NC 4.0，图像按其 `LICENSE-ASSETS` 中的非商业分享条件分发；保留作者“可以叫我云大人 / 雾雪”、原站 https://kiriyuki.com 及角色设计者来源。图像未修改，每项路径、来源、校验值和选择范围见目录内清单及署名文件。
- `yyh-001/dsh-meme` 用于了解按情绪选图与文本模型配图方式。其当前大肥鱼默认图库清单另标为个人使用，未作为公开下载素材复制进本项目；功能由本项目适配器与明确允许非商业分享的素材实现。
- 原创六张贴纸仍按本项目 `LICENSE`、`NOTICE` 使用；社区素材保留各自条款，根目录许可不覆盖或替代上述许可。

## 运行时依赖

本仓库不包含已安装运行时、第三方安装器、虚拟环境或打包的可执行文件。
桌面程序通过独立进程、HTTP 和 OneBot 接口与外部组件协作；插件则在
对应宿主中运行，使用时仍须遵守宿主及依赖的许可条件。
AstrBot 插件在宿主进程内加载；若组合发行构成 AGPL 所覆盖的衍生作品，
应履行 AGPL 的相关义务。本项目的非商业许可不构成插件与宿主许可
兼容性的保证，也不改变上游 AGPL 已经授予的权利。

| 组件 | 上游 | 许可或条款 |
| --- | --- | --- |
| AstrBot | <https://github.com/AstrBotDevs/AstrBot> | AGPL-3.0-or-later |
| NapCat | <https://github.com/NapNeko/NapCatQQ> | 以上游随附许可和条款为准 |
| 爱之屋 | <https://github.com/myflww/love-house> | PolyForm Noncommercial 1.0.0；第三方材料另计 |
| pywebview | <https://github.com/r0x0r/pywebview> | BSD-3-Clause |
| uv | <https://github.com/astral-sh/uv> | Apache-2.0 / MIT |
| Microsoft Edge WebView2 | <https://developer.microsoft.com/microsoft-edge/webview2/> | Microsoft 随附条款 |

其他依赖以其各自发行包中的许可为准。若另行分发包含第三方组件的
运行时或二进制包，应随包履行各组件的署名、许可及对应源码等义务。
原有来源记录同时保留于 `assets/NOTICE.md`。

人格预设的社区参考资料包括 Kaalia0912/dsh-whale-musume-persona、
linsport/dsh-whale-persona、xin-1712/dafeiyu-persona。对应原文的 MIT
许可与补充说明见 `assets/persona/references/`；改写来源、Git blob 和
各卡许可见 `assets/persona/cards.json`。未用本项目的非商业条款撤销
上游 MIT 材料本身授予的权利；人物视觉来源继续适用前述独立说明。
