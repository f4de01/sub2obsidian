# sub2obsidian

把抖音、B站、微信公众号里收藏的知识类视频与文章采集为**原始材料**，再由 agent 会话按知识库中的 **Schema** 编译成按概念组织、互相链接的 Obsidian 个人知识库（Karpathy 式 LLM Wiki）。

领域术语见 [CONTEXT.md](CONTEXT.md)，架构决策见 [docs/adr/](docs/adr/)。

## 安装

需要 [uv](https://docs.astral.sh/uv/) 与 [Git for Windows](https://git-scm.com/download/win)（`git` 在 PATH 中）。

```powershell
# 在本仓库根目录
uv tool install .
# 升级时
uv tool install --force .
```

安装后可在任意目录调用 `sub2obsidian`。项目固定使用 Python 3.12（见 `.python-version`），uv 会自动准备。

### 外部工具

| 工具 | 用途 | 安装 |
| --- | --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | B站 视频元数据与平台字幕 | 作为 Python 依赖随 `uv tool install .` 一起装好，无需单独安装 |
| Playwright Chromium | `login` 扫码登录用的专用浏览器 | 见下方命令（约 150 MB，装在 `%LOCALAPPDATA%\ms-playwright\`） |

```powershell
# 安装 Playwright Chromium（在 uv tool install 之后执行一次）
& "$(uv tool dir)\sub2obsidian\Scripts\python.exe" -m playwright install chromium

# B站 改版导致采集失败时，先把 yt-dlp 升级到最新版（在本仓库根目录）
uv tool install --force --upgrade-package yt-dlp .
```

开发环境（`uv sync` 之后）用 `uv run python -m playwright install chromium` 安装浏览器。

## 初始化知识库

```powershell
sub2obsidian init                 # 使用配置中的路径，缺省为 D:\Obsidian\知识库
sub2obsidian init "E:\笔记\知识库"  # 指定路径
```

`init` 会：

- 建立目录骨架：`原始材料/`、`Wiki/来源/`、`Wiki/概念/`、`Wiki/综述/`、`Wiki/主题域/`、`我的笔记/`、`附件/`；
- 写入 `index.md`、`log.md`，以及由 Schema 模板渲染的 `CLAUDE.md` 与 `AGENTS.md`；
- 预置 `.obsidian`：附件目录为 `附件/`、使用 wikilink，Dataview 插件已安装并启用；
- 把知识库设为 git 仓库（`.gitignore` 排除 Obsidian 工作区状态），首次初始化提交一次；
- 通过 `obsidian://open?path=…` 让 Obsidian 登记并打开该知识库。

可以放心重复执行：只补缺失的目录与文件，从不覆盖已有文件；补回的文件单独提交，不会卷入你未提交的改动。

## 登录平台

```powershell
sub2obsidian login bilibili
```

弹出工具专用的 Chromium 窗口，扫码登录 B站 后窗口自动关闭。登录状态保存在用户配置目录的专用浏览器配置里，之后的命令直接复用，不读取你日常使用的 Chrome / Edge。B站 的字幕（CC 与 AI 字幕）需要登录才能拿到；登录失效时命令会提示「请重新登录 B站：sub2obsidian login bilibili」。

## 采集一条来源（推送）

```powershell
sub2obsidian capture https://www.bilibili.com/video/BV1GJ411x7h7
sub2obsidian capture "【某视频标题-哔哩哔哩】 https://b23.tv/xxxxxxx"   # 直接粘贴 App 分享文本
sub2obsidian capture --vault "E:\笔记\知识库" <链接>                    # 指定知识库
```

- 自动从分享文本中提取链接，解析 b23.tv 短链、剥离追踪参数；同一视频无论以哪种链接提交，只保留一份。
- 推送来的来源直接视为已通过筛选。有平台字幕的视频采集后为「已转写」；没有字幕的停在「已采集」，等待转写。
- 视频已删除或不可见时，来源标为「已失效」，只留元数据存根，以后不再重试。
- 网络、风控等可重试的失败：来源保持「已通过」并记下失败原因，再次 `capture` 同一链接即重试。
- 每次 `capture` 对原始材料的改动单独提交一次 git，不卷入 Wiki 与你未提交的改动。

原始材料的布局（只增不改，不保存视频文件）：

```
原始材料/bilibili/BV1GJ411x7h7/
  元数据.md   frontmatter：平台、平台内ID、规范链接、类型、标题、作者、发布时间、时长（秒）、
              采集途径、采集时间、来源状态、筛选建议、失败原因；正文为标题、封面与简介
  封面.jpg
  口播稿.md   每段一行，以 [时:分:秒] 开头；平台字幕与 ASR 转写格式相同
```

## 用户配置目录

所有本机配置、凭据与运行状态都放在 `%APPDATA%\sub2obsidian\`，绝不进入知识库或任何 git 仓库：

| 位置 | 内容 |
| --- | --- |
| `config.toml` | 用户设置（UTF-8 TOML）。`vault`：知识库路径，首次 `init` 时自动记下 |
| `credentials/` | 平台与飞书应用凭据；如 `bilibili.cookies.txt`（每次使用时从浏览器配置重新导出） |
| `browser/<平台>/` | 登录用的 Playwright 持久化浏览器配置 |
| `state/` | 收件箱游标、回填断点等运行状态 |

## 开发

```powershell
uv sync
uv run pytest
```

测试只通过「CLI 命令 + 知识库目录」观察行为，全部在临时目录中运行；`%APPDATA%` 在测试中被指向临时目录。平台适配器、凭据提供者等外部端口在行为测试中换成假实现；真实的 B站 适配器用 `tests/fixtures/bilibili/` 中的录制样本做契约测试（见该目录的 README），测试不需要网络、登录或浏览器。

## 第三方组件

知识库中预装的 [Dataview](https://github.com/blacksmithgu/obsidian-dataview) 插件（0.5.70 release 文件）随包分发于 `src/sub2obsidian/assets/obsidian/plugins/dataview/`，MIT 许可证见同目录 `LICENSE`。
