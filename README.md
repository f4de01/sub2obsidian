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
| [ffmpeg](https://ffmpeg.org/) | `transcribe` 下载音频后转成 16 kHz 单声道 WAV | `winget install --id Gyan.FFmpeg -e`，装好后**重新打开终端**，确认 `ffmpeg -version` 能运行 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 没有平台字幕时在本机 GPU 上转写 | 随 `uv tool install .` 装好（含 Windows 所需的 cuBLAS 运行库）；需要 NVIDIA 显卡与较新的驱动，模型首次运行时自动下载 |

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
- 写入 Dataview 状态页 `来源状态.md`：各来源状态的数量，以及待编译、待转写、待筛、采集失败、已失效的来源清单；
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
- 推送来的来源直接视为已通过筛选。有平台字幕的视频采集后为「已转写」；没有字幕的停在「已采集」，等待 `sub2obsidian transcribe` 转写（见下文「转写」）。
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

## 日常同步：飞书收件箱 + `sync`

日常增量靠**推送**：在手机上把 B站、公众号等的分享链接发给飞书机器人（与它的私聊就是**收件箱**），回到电脑执行一次 `sync`。

### 配置飞书收件箱（一次性）

飞书自建应用需要人工创建。在本仓库根目录用 Git Bash 运行配置向导，它逐步打开飞书开放平台的页面，告诉你点哪里、复制什么：

```bash
bash scripts/feishu-inbox-setup.sh
```

向导覆盖：创建企业自建应用 → 复制 App ID 与 App Secret → 开启机器人能力 → 开通权限 `im:message`（获取与发送单聊、群组消息）→ 发布版本（可用范围包含你自己）→ 在 API 调试台取得你在该应用下的 open_id。三个值写入 `%APPDATA%\sub2obsidian\credentials\feishu.env`，绝不进入知识库或 git 仓库。可以重复运行，回车保留已保存的值。

不需要事件订阅、公网回调或常驻进程：`sync` 时通过消息列表 API 主动拉取私聊里的新消息。首次 `sync` 时机器人会给你发一条「收件箱已连接」，从而确定私聊会话；之后就把链接发到这个私聊。

### `sync`

```powershell
sub2obsidian sync
sub2obsidian sync --vault "E:\笔记\知识库"
```

依次：

1. 读取收件箱中上次读到之后的新消息（电脑关机期间推送的也不会丢；已读的消息不再处理）；
2. 从每条消息中提取链接（消息里夹杂文字也可以），作为推送来的来源登记为「已通过」；无法识别平台的链接、没有链接的消息在输出中提示；
3. 采集所有「已通过」的来源（包括以前采集失败的），按平台交给对应的适配器；
4. 为「已采集」的视频转写口播稿（同 `transcribe`）；
5. 对原始材料的改动单独提交一次 git（`sync: …`），不卷入 Wiki 与你未提交的改动；
6. 输出汇总：新增来源、已采集、已转写、已失效、失败的数量，以及失效与失败的来源和原因。

单条失败不中断整批：失败的来源保持原状态并记下原因，下次 `sync` 自动重试；短链暂时解析不了的链接也会记下，下次重试。飞书读取失败时照常采集与转写其他来源，收件箱的读取位置不动，下次再读；还没配置飞书时跳过收件箱。读取位置（游标）保存在用户配置目录的 `state/inbox.toml`。

## 转写（ASR）

```powershell
sub2obsidian transcribe                    # 转写所有「已采集」的视频来源
sub2obsidian transcribe --vault "E:\笔记\知识库"
```

- 平台字幕优先：有字幕的视频在 `capture` 时已经是「已转写」，不会再送去转写；没有字幕的停在「已采集」，由 `transcribe` 批量处理（可以放到夜里单独跑）。
- 每条来源：下载音频到系统临时目录 → 在本机 GPU 上用 faster-whisper `large-v3-turbo`（CUDA，int8_float16）转写 → 写入与平台字幕相同格式的 `口播稿.md`（`口播稿来源: faster-whisper large-v3-turbo`）→ 状态转为「已转写」→ 删除临时音频。原始材料里不保存任何音视频文件。
- 口播稿原样保存，不做自动纠错。只含音乐、没有人声的片段会被跳过（语音活动检测），避免转写出幻觉文字。
- 单条失败（网络、风控、音频损坏）不影响同批其他来源：失败的来源保持「已采集」并在元数据中记下失败原因，下次 `transcribe` 自动重试。视频已删除时来源转为「已失效」，已采集的原始材料保留。
- 缺少 ffmpeg、显卡运行库出错或模型下载失败属于本机环境问题：整批中止并给出明确提示，未处理的来源保持原状。
- 对原始材料的改动单独提交一次 git（`transcribe: …`）。

**模型下载**：首次运行时自动从 Hugging Face 下载模型（约 1.6 GB）到 `%USERPROFILE%\.cache\huggingface\hub\`。下载不了时，先设置镜像再运行：

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
sub2obsidian transcribe
```

**术语表**：在 `config.toml` 中配置，转写时作为提示传给转写引擎，让「MCP」「RAG」这类术语不被听错：

```toml
[transcribe]
terms = ["MCP", "RAG", "检索增强生成", "Claude Code"]
```

## 编译

编译不写代码（ADR-0001）：在知识库目录中开启 agent 会话（如 Claude Code），说「编译」。agent 按知识库中的 Schema（`CLAUDE.md` / `AGENTS.md`）把所有可编译的来源编译进 Wiki：写来源页，新建或改写概念页（每条论断带出处，B站 出处可跳到原视频对应秒数），记录分歧，更新 `index.md` 与 `log.md`，做轻量体检，最后以一次 git 提交结束。

agent 编译时用到两条命令，你也可以直接用：

```powershell
sub2obsidian status                          # 各来源状态的数量，以及可编译的来源
sub2obsidian mark-compiled bilibili/BV1GJ411x7h7 wechat/AbCdEf123   # 把来源转为「已编译」
```

- 「可编译」= 文章或图文「已采集」，或视频「已转写」。
- `mark-compiled` 接受 `status` 列出的 `<平台>/<平台内ID>`，也接受来源在 `原始材料/` 中的目录或来源的链接（短链除外）。只要有一个来源找不到或不可编译，就整批拒绝、一个都不改。
- `mark-compiled` 不单独提交 git：来源状态的改动由 agent 与 Wiki 的改动一起放进本次编译的提交。
- Schema 初始化后归知识库所有；想改进编译质量就改知识库里的 Schema，重跑 `init` 不会覆盖它。

## 用户配置目录

所有本机配置、凭据与运行状态都放在 `%APPDATA%\sub2obsidian\`，绝不进入知识库或任何 git 仓库：

| 位置 | 内容 |
| --- | --- |
| `config.toml` | 用户设置（UTF-8 TOML）。`vault`：知识库路径，首次 `init` 时自动记下；`[transcribe]` 表的 `terms`：转写术语表 |
| `credentials/` | 平台与飞书应用凭据：`bilibili.cookies.txt`（每次使用时从浏览器配置重新导出）、`feishu.env`（飞书应用的 App ID、App Secret 与你的 open_id，由配置向导写入） |
| `browser/<平台>/` | 登录用的 Playwright 持久化浏览器配置 |
| `state/` | 运行状态：`inbox.toml`（收件箱读到的位置、待重试的链接）、`feishu.toml`（与机器人私聊的会话 ID）等 |

## 开发

```powershell
uv sync
uv run pytest
```

测试只通过「CLI 命令 + 知识库目录」观察行为，全部在临时目录中运行；`%APPDATA%` 在测试中被指向临时目录。平台适配器、收件箱、转写引擎、凭据提供者四个外部端口在行为测试中换成假实现；真实的 B站、公众号适配器用 `tests/fixtures/` 中的录制样本做契约测试，飞书收件箱用按开放平台文档构造的响应样本（`tests/fixtures/feishu/`）做契约测试（见各目录的 README），测试不需要网络、登录或浏览器。

faster-whisper 的集成测试（`tests/test_faster_whisper.py`，测试音频为 Windows 语音合成的一段中文）只在本机有 CUDA 显卡、且 `large-v3-turbo` 模型已缓存时运行，否则自动跳过；测试从不下载模型。要运行它，先执行一次 `sub2obsidian transcribe`（或 `uv run python -c "from faster_whisper.utils import download_model; download_model('large-v3-turbo')"`）把模型下载好。

## 第三方组件

知识库中预装的 [Dataview](https://github.com/blacksmithgu/obsidian-dataview) 插件（0.5.70 release 文件）随包分发于 `src/sub2obsidian/assets/obsidian/plugins/dataview/`，MIT 许可证见同目录 `LICENSE`。
