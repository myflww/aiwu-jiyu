# 参与改进爱屋及鱼

欢迎修复问题、改善界面和补充说明。请先在 Issue 中描述较大的功能调整；小修复可以直接提交 Pull Request。

## 提交方式

1. Fork 仓库，在自己的副本创建分支。
2. 完成修改和相应验证，提交到你的 Fork。
3. 向主仓库发起 Pull Request，说明解决的问题、修改后的行为和验证结果；界面改动附不含个人数据的截图。
4. 等待作者 myflww 审核。主仓库的合并和发布由作者决定。

仓库公开意味着可以浏览、下载，并在许可允许的范围内 Fork 和修改自己的副本；这些操作不会授予主仓库的写入、合并或发布权限。提交 PR 也不会自动合并。

## 本地开发

桌面程序使用 Python 后端、原生 HTML/CSS/JavaScript 界面和 pywebview。Windows 打包使用 PyInstaller；依赖版本在 `requirements-build.txt` 中锁定。以下 PowerShell 命令在仓库根目录执行，使用 Windows x64 Python 3.13：

```powershell
py -3.13 -m venv build/.venv
./build/.venv/Scripts/python.exe -m pip install -r requirements-build.txt
```

只调整界面时，优先使用隔离预览，不接入自己的聊天账号和现有数据：

```powershell
./build/.venv/Scripts/python.exe app/main.py --preview --no-auto-start
```

需要从浏览器查看时增加 `--headless`，终端会输出本机访问地址。预览中的状态是演示数据，写入接口被禁用，不能据此宣称实际插件已切换或服务已登录。

需要真实集成验证时，为测试单独指定 `DAFEIYU_HOME`，使用自己的测试账号和 API Key。不要把真实测试数据放进仓库，也不要让测试操作已有的日常服务。

首次准备运行环境时，会优先验证本机 Python 3.13 x64，再复制到程序自己的运行目录。Python 安装器返回 1603 时仍会尝试这一兜底；没有兼容运行时则保留明确失败。AstrBot 安装依次尝试 CERNET、清华和 PyPI 官方源；需要指定其他源时，在启动前设置 `DAFEIYU_PYPI_INDEX`，此时仅使用该地址。该变量接受不含凭据、查询参数或片段的 HTTP(S) 地址：

```powershell
$env:DAFEIYU_PYPI_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
```

运行时文件下载每个源约有 180 秒预算，并设置 15 秒网络超时；分片等待期间每 10 秒报告一次进度。分片范围、长度或固定 SHA-256 不符时会清理本次临时文件并换源，已有损坏的 NapCat 缓存也会重新下载。Windows 源码检出须保留 `.gitattributes`，以保证随附插件的 LF 字节与清单一致；从旧 checkout 升级时应保留自己的修改，再重新检出这些资源。

## 验证要求

修改后运行与改动有关的检查。后端完整验证可执行：

```powershell
./build/.venv/Scripts/python.exe -m unittest discover -s build -p "test_*.py" -v
./build/.venv/Scripts/python.exe build/smoke_backend.py
```

安装流程的独立回归为 `build/test_first_install.py` 和 `build/test_install_downloads.py`。前者模拟 MSI/uv 结果，后者只连接本机临时 HTTP 服务；均不登录 QQ、不操作日常运行目录。这些检查不代替干净 Windows 环境中的完整首次安装验收。

界面改动应检查首页、插件开关与待应用状态、记忆页、设置、空状态、断线状态，以及窄窗口下的布局和键盘操作。实际功能需要使用真实后端结果核验；不要用展示用假数据替代验收。

插件逻辑要覆盖热切换失败、离线保存、启动时应用、取消待应用设置和既有配置保留。内存逻辑要区分系统物理内存与进程工作集，核对启动父进程和子进程的归属，避免重复计数。

制作 Windows 包可运行：

```powershell
./build/build.ps1
```

脚本会准备依赖、收集许可、构建可执行文件和便携包。生成安装程序还需要提供 Inno Setup 编译器路径；具体参数见 `build/build.ps1`。构建产物放在 `dist`，不要作为普通源码改动提交。对外发布前应检查包内文件、许可与校验值，并使用干净环境验证首次运行。

## 内容与数据

界面文字应说明当前状态、操作结果或下一步，避免用长口号挤占功能区域。角色美术应保持蓝发、鲸鱼特征和大肥鱼的辨识度，新增素材要记录来源、提示词、修改和许可；不要提交已弃用的多人拼图或加载测试图。

请勿提交 API Key、QQ 登录令牌、Cookie、真实联系人、聊天记录、记忆库、个人目录、已安装运行环境或用户作品。`.gitignore` 已排除常见运行数据，提交前仍要检查暂存区和截图。新增测试使用临时目录、虚构号码和占位凭据。

## 署名与许可

项目作者为 **myflww**。自有部分采用 [PolyForm Noncommercial License 1.0.0](LICENSE)。贡献和二次分发应遵守该许可，未经另行授权不可商用；这是一份非商业许可，不是对任意商业用途的开放授权。

分发原版或修改版时，保留 myflww 原始署名、[NOTICE](NOTICE) 中的 Required Notice 和许可证，并可注明贡献者自己的修改。新增贡献请说明你有权提供的内容及其来源，不要移除已有署名。

第三方代码、人格和美术继续遵守各自原许可。引用或修改这些材料时，保留相应许可和来源，不得将其统一改为本项目许可。具体范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
