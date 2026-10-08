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
| [F2](https://github.com/Johnserf-Seed/f2) | 抖音作品详情与收藏列表（接口签名与请求头） | 从 git 安装（其最后一个 tag 版本已过时），见下方命令 |
| [ffmpeg](https://ffmpeg.org/) | `transcribe` 与抖音视频下载音频后转成 16 kHz 单声道 WAV | `winget install --id Gyan.FFmpeg -e`，装好后**重新打开终端**，确认 `ffmpeg -version` 能运行 |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 没有平台字幕时在本机 GPU 上转写 | 随 `uv tool install .` 装好（含 Windows 所需的 cuBLAS 运行库）；需要 NVIDIA 显卡与较新的驱动，模型首次运行时自动下载 |

```powershell
# 安装 Playwright Chromium（在 uv tool install 之后执行一次）
& "$(uv tool dir)\sub2obsidian\Scripts\python.exe" -m playwright install chromium

# B站 改版导致采集失败时，先把 yt-dlp 升级到最新版（在本仓库根目录）
uv tool install --force --upgrade-package yt-dlp .

# 抖音需要 F2（从 git 安装，固定到一个提交；在本仓库根目录）
uv tool install --force --with "f2 @ git+https://github.com/Johnserf-Seed/f2@f6be8c0ffba9a127075bbeafe4838716650b6325" .
```

抖音每隔几个月更换接口签名，F2 随之更新。抖音采集持续报「接口返回空响应」且重新登录也无效时，把上面命令里的提交换成 F2 最新的提交重新安装；仍然不行时考虑改用付费的 TikHub（ADR-0003）。

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
- 在 Obsidian 中打开该知识库：先只读检查 `%APPDATA%\obsidian\obsidian.json`，知识库已在 Obsidian 登记过时，通过 `obsidian://open?path=…` 直接打开；尚未登记（或该文件不存在、无法解析）时不触发 URI——这个 URI 只能打开已登记的目录，不会登记新目录——而是在终端打印一次性的手动步骤：Obsidian 左下角仓库名 →「管理仓库…」→「打开本地仓库」→ 选择该路径 → 信任插件。手动打开一次之后，再执行 `init` 就会直接打开。本工具从不改写 `obsidian.json`。

可以放心重复执行：只补缺失的目录与文件，从不覆盖已有文件；补回的文件单独提交，不会卷入你未提交的改动。

## 登录平台

```powershell
sub2obsidian login bilibili
sub2obsidian login douyin
```

弹出工具专用的 Chromium 窗口，扫码登录后窗口自动关闭（抖音打开的是首页，在弹出的登录框里扫码）。登录状态保存在用户配置目录的专用浏览器配置里，之后的命令直接复用，不读取你日常使用的 Chrome / Edge。B站 的字幕（CC 与 AI 字幕）需要登录才能拿到；抖音的作品详情接口需要登录 cookie，从浏览器配置中读出、拼成 cookie 字符串交给 F2。每次运行中每个平台只读一次浏览器配置（只启动一次无界面浏览器），读出的 cookie 只缓存在内存里。登录失效时命令会提示「请重新登录 B站：sub2obsidian login bilibili」「请重新登录 抖音：sub2obsidian login douyin」。

## 采集一条来源（推送）

```powershell
sub2obsidian capture https://www.bilibili.com/video/BV1GJ411x7h7
sub2obsidian capture "【某视频标题-哔哩哔哩】 https://b23.tv/xxxxxxx"   # 直接粘贴 App 分享文本
sub2obsidian capture --vault "E:\笔记\知识库" <链接>                    # 指定知识库
sub2obsidian capture "7.94 复制打开抖音，看看【某某的作品】… https://v.douyin.com/xxxxxxx/ …"  # 抖音分享口令
```

- 自动从分享文本中提取链接，解析 b23.tv、v.douyin.com 短链，剥离追踪参数；同一来源无论以哪种链接提交（抖音的分享口令、短链、`/video/`、`/note/`、网页版的 `?modal_id=` 链接），只保留一份。
- B站 多P视频的每个分P是一条独立的来源：链接带 `?p=N` 时只采集第 N P；不带 p 时展开为这个视频的全部分P，全部直接采集（单P视频照旧是一条）。分P的标题为「视频标题 PN 分P标题」，时长、字幕与口播稿都按分P。
- 推送来的来源直接视为已通过筛选。有平台字幕的视频采集后为「已转写」；没有字幕的 B站 视频停在「已采集」，等待 `sub2obsidian transcribe` 转写（见下文「转写」）。
- 抖音没有平台字幕：抖音视频采集后当场下载、ASR 转写、删除临时音视频，`capture` 结束时即为「已转写」（转写失败时停在「已采集」，由 `transcribe` 重试）。抖音图文按文章处理：保存文字与全部图片（`正文.md` + `图01.jpg`…），没有口播稿，采集后为「已采集」即可编译。抖音请求低速、带随机间隔。
- 视频已删除或不可见时，来源标为「已失效」，只留元数据存根，以后不再重试。已经拿到正文或口播稿的来源，之后在平台上删除也不受影响：照常编译，不会变为「已失效」。
- 抖音接口只回空响应时，可能是登录 cookie 失效，也可能是 F2 的签名算法失效：提示先重新登录，仍不行再升级 F2（见「外部工具」），来源保持「已通过」可重试。
- 网络、风控等可重试的失败：来源保持「已通过」并记下失败原因，再次 `capture` 同一链接即重试。
- 每次 `capture` 对原始材料的改动单独提交一次 git，不卷入 Wiki 与你未提交的改动；结束时输出与 `sync` 相同格式的汇总（新增、各状态数量、失效与失败的来源及原因）。
- 上次采集被中断（Ctrl+C）留下了文件、重试时平台给出的同名文件内容不同：原始材料只增不改，不覆盖，记为这条来源的失败（失败原因写明哪个文件），同批其他来源照常采集。

原始材料的布局（只增不改，不保存视频文件）：

```
原始材料/bilibili/BV1GJ411x7h7/
  元数据.md   frontmatter：平台、平台内ID、规范链接、类型、标题、作者、发布时间、时长（秒）、
              采集途径、采集时间、来源状态、筛选建议、失败原因；正文为标题、封面与简介
  封面.jpg
  口播稿.md   每段一行，以 [时:分:秒] 开头；平台字幕与 ASR 转写格式相同
```

**平台内 ID**：B站 单P视频就是 BV 号（`bilibili/BV1GJ411x7h7`，规范链接不带参数）。多P视频的第 1 P 同样是 BV 号本身，第 N P（N≥2）为 `BV号_pN`，规范链接带 `?p=N`（如 `bilibili/BV1bK411W797_p2` ↔ `https://www.bilibili.com/video/BV1bK411W797?p=2`）。这样已有的来源目录与链接都不用改：以前采集的多P视频只取了第 1 P，正好就是现在的第 1 P；把它的链接（不带 p）再推送一次，就会补齐其余分P。以前回填（拉取）登记的多P视频同样只有第 1 P、且没有「分P」「视频标题」两项；想让其余分P也进入待筛清单，删掉用户配置目录 `state/backfill.toml` 中的各个 `[bilibili.…]` 表，下次 `sync` 重新读一遍收藏（已登记的来源跳过，只登记新的分P；收藏多时会分几次 sync 读完）。多P视频的分P在元数据中另有「分P」（序号）与「视频标题」两项，单P视频这两项为空。抖音为作品 ID，公众号文章为「公众号数字 ID_mid_idx」。

## 日常同步：飞书收件箱 + `sync`

日常增量靠**推送**：在手机上把 B站、抖音、公众号等的分享链接发给飞书机器人（与它的私聊就是**收件箱**），回到电脑执行一次 `sync`。

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
3. 拉取 B站 收藏夹与稍后再看、回填抖音收藏（见下文「回填与筛选」），新来源以「待筛」登记，并更新 `待筛清单.md`；一个平台拉取失败（登录失效、风控、签名失效、没装 F2）只在汇总里报出，不影响其他平台和后面的步骤；
4. 采集所有「已通过」的来源（包括以前采集失败的、以及 `screen` 通过的），按平台交给对应的适配器；
5. 为「已采集」的视频转写口播稿（同 `transcribe`）；
6. 对原始材料（和待筛清单）的改动单独提交一次 git（`sync: …`），不卷入 Wiki 与你未提交的改动；
7. 输出汇总：新增来源、待筛、已采集、已转写、已失效、失败的数量，以及失效与失败的来源和原因。

单条失败不中断整批：失败的来源保持原状态并记下原因，下次 `sync` 自动重试；短链暂时解析不了的链接也会记下，下次重试。飞书读取失败时照常采集与转写其他来源，收件箱的读取位置不动，下次再读；还没配置飞书时跳过收件箱。读取位置（游标）保存在用户配置目录的 `state/inbox.toml`。

### 回填与筛选（B站、抖音收藏）

存量收藏不必一条条转发：`sync` 会**拉取**你在 B站 的全部收藏夹与稍后再看（需要先 `login bilibili`），以及抖音的全部收藏和各个收藏夹（需要先 `login douyin`，并装好 F2），流程是：

1. **回填只抓元数据**：每条收藏以「待筛」登记，只记标题、作者、时长、发布时间、简介和链接，不下载封面、字幕、图片与音频，不浪费时间和 GPU。抖音的视频与图文都会进入清单。B站 收藏里的多P视频展开为每个分P一条来源：每读到一个多P视频（包括之后每次 sync 重读收藏夹开头时），都要多请求一次公开的视频信息接口来读分P列表；推送不带 p 的 B站 链接时也要多请求一次，用来查分P数。
2. **待筛清单**：`sync` 在知识库根目录生成（或更新）`待筛清单.md`，每条来源一个勾选框，附元数据、简介摘要和「建议：」栏。多P视频按视频分组：一行视频信息下面每个分P一个勾选框，可以只勾选其中几集，`screen` 按分P分别转为「已通过」或「已拒绝」。
3. **筛选建议**：在知识库目录里对 agent 说「填写筛选建议」，它按 Schema 在每条的「建议：」后写上是否知识类与建议主题域（CLI 不调用 LLM，ADR-0001）。
4. **勾选并执行 `screen`**：在 Obsidian 里勾选要保留的来源，然后执行

   ```powershell
   sub2obsidian screen
   sub2obsidian screen --reject-all   # 清单里一个都不保留时才用
   ```

   勾选的转为「已通过」，下次 `sync` 时采集并转写；未勾选的转为「已拒绝」，只留元数据存根，之后再同步也不会回到清单里。建议栏随之记入各来源元数据的「筛选建议」。清单里一个都没勾选时 `screen` 拒绝执行（「已拒绝」不可撤销），除非加 `--reject-all`。`screen` 的改动单独提交一次 git（`screen: …`）。

**分批与断点**：每次 `sync` 每个平台最多登记一批新来源（默认 50 条），请求之间随机间隔（B站 默认 1–3 秒，抖音默认 3–6 秒），以免触发风控。每个收藏夹读到哪一页记在用户配置目录的 `state/backfill.toml`：一批满了、被风控或网络打断、签名失效、或按了 Ctrl+C（已登记的来源照常提交），下次 `sync` 都从断点接着读，不会重复登记。B站 的收藏全部读完后，每次 `sync` 只看各收藏夹开头的新收藏。在 `config.toml` 中调整：

```toml
[backfill]
batch_size = 50           # 每次 sync 每个平台最多登记的新来源数
interval = [2, 5]         # B站 请求之间的随机间隔（秒），对回填与采集都生效
douyin_interval = [3, 6]  # 抖音接口请求之间的随机间隔（秒），对回填与采集都生效
```

**抖音只回填一次**：抖音的全部收藏与收藏夹读完后（`sync` 输出「抖音 回填完成」），此后的 `sync` 不再请求抖音收藏，新的抖音收藏请分享到收件箱（推送）。抖音风控严、签名每隔几个月失效，这样请求最少。想再完整拉一次时，删掉 `state/backfill.toml` 中的 `complete` 一行与 `[douyin.…]` 各表。抖音拉取报「接口返回空响应」时，先 `login douyin` 重新登录，仍然不行就升级 F2（见「外部工具」），持续失败时考虑改用 TikHub（ADR-0003）；B站 回填与收件箱照常处理。

收藏夹里已显示为「已失效视频」的条目、抖音收藏里已被删除的作品登记为「已失效」存根，不进入待筛清单；B站 的音频、番剧等非视频收藏跳过。还在待筛清单里的来源，如果你又把它的链接推送（或 `capture`）了一次，视为你已选中：直接转为「已通过」并离开清单。

## 转写（ASR）

```powershell
sub2obsidian transcribe                    # 转写所有「已采集」的视频来源
sub2obsidian transcribe --vault "E:\笔记\知识库"
```

- 平台字幕优先：有字幕的视频在 `capture` 时已经是「已转写」，不会再送去转写；没有字幕的停在「已采集」，由 `transcribe` 批量处理（可以放到夜里单独跑）。
- 每条来源：下载音频到系统临时目录 → 在本机 GPU 上用 faster-whisper `large-v3-turbo`（CUDA，int8_float16）转写 → 写入与平台字幕相同格式的 `口播稿.md`（`口播稿来源: faster-whisper large-v3-turbo`）→ 状态转为「已转写」→ 删除临时音频。原始材料里不保存任何音视频文件。
- 口播稿原样保存，不做自动纠错。只含音乐、没有人声的片段会被跳过（语音活动检测），避免转写出幻觉文字。
- 先检测音频的语言再转写；「简体中文与标点」的风格提示只给中文音频，英文等其他语言的音频不会被带成中文。重复度异常（循环重复同一个词）的段落会被丢弃。
- Whisper 在没有人声的音频上常吐出「请不吝点赞 订阅 转发 打赏支持明镜与点点栏目」「字幕志愿者 杨茜茜」「Thank you」之类的**已知幻觉句**，这些句子从口播稿中滤掉。滤掉后什么都不剩的视频（纯音乐、只有画面）照常转为「已转写」，但元数据的「无口播」写明原因，编译时 agent 只依据简介与封面，不引用口播稿。
- 单条失败（网络、风控、音频损坏）不影响同批其他来源：失败的来源保持「已采集」并在元数据中记下失败原因，下次 `transcribe` 自动重试。视频已删除时来源转为「已失效」（还没有口播稿，不能编译），已采集的原始材料保留。
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

英文等非中文音频只用术语表中不含汉字的术语。

**已知幻觉句**：内置的黑名单之外，遇到新的幻觉句可以追加（比较时忽略空白、标点与大小写；一段只由这些句子组成时整段滤掉，句子中间提到的不受影响）：

```toml
[transcribe]
hallucinations = ["本视频由某某赞助播出"]
```

## 编译

编译不写代码（ADR-0001）：在知识库目录中开启 agent 会话（如 Claude Code），说「编译」。agent 按知识库中的 Schema（`CLAUDE.md` / `AGENTS.md`）把所有可编译的来源编译进 Wiki：写来源页，新建或改写概念页（每条论断带出处，B站 出处可跳到原视频对应分P的对应秒数：`…/video/<BV号>?p=<分P>&t=<秒数>`），记录分歧，更新 `index.md` 与 `log.md`，做轻量体检，最后以一次 git 提交结束。

agent 编译时用到两条命令，你也可以直接用：

```powershell
sub2obsidian status                          # 各来源状态的数量，以及可编译的来源
sub2obsidian mark-compiled bilibili/BV1GJ411x7h7 wechat/AbCdEf123   # 把来源转为「已编译」
```

- 「可编译」= 文章或图文「已采集」，或视频「已转写」。
- `mark-compiled` 接受 `status` 列出的 `<平台>/<平台内ID>`，也接受来源在 `原始材料/` 中的目录或来源的链接（短链除外；B站 不带 p 的链接只指第 1 P）。只要有一个来源找不到或不可编译，就整批拒绝、一个都不改。
- `mark-compiled` 不单独提交 git：来源状态的改动由 agent 与 Wiki 的改动一起放进本次编译的提交。
- Schema 初始化后归知识库所有；想改进编译质量就改知识库里的 Schema，重跑 `init` 不会覆盖它。模板出新版本时见「升级 Schema」。

## 问询、存档与全库体检

同样在知识库目录的 agent 会话里进行，按 Schema 执行，不需要命令：

- **问询**：直接提问（如「做企业内部知识库问答，该用 RAG 还是微调？」）。agent 先读 `index.md` 定位页面，只用知识库里的内容回答，每条论断带出处（B站 出处可跳到原视频对应秒数）；Wiki 之外的补充会单独标明。问询不写入任何文件。
- **存档**：对某个回答说「存档」，agent 把它沉淀为 `Wiki/综述/` 下的**综述页**（跨概念的比较或总结，同样带出处，不含 Wiki 之外的补充），链接到主题域入口页与相关概念页，更新 `index.md` 与 `log.md`，以一次 git 提交（`存档: …`）结束。
- **全库体检**：说「体检」，agent 检查全部 Wiki 页面，找出并修复矛盾、孤立页、重复概念、断链、缺失的概念页，列出待你裁决的分歧，并对 Schema 提出改进建议，以一次 git 提交（`体检: …`）结束。Schema 改进建议由你决定是否采纳；采纳的由 agent 同步改进 `CLAUDE.md` 与 `AGENTS.md` 并单独提交（`Schema: …`）。裁决分歧的方式是在分歧下写一条 `> [!我]` 批注或在「我的笔记」里写下判断，下次编译或体检时生效。

## 升级 Schema

Schema 初始化后归知识库所有（`init` 不覆盖已有的 Schema），本仓库的 Schema 模板出新版本时，用旧版初始化的知识库不会自动获得新的规则与流程。升级分两步，合并由 agent 判断，CLI 不调用 LLM（ADR-0001）：

```powershell
sub2obsidian upgrade-schema                  # 或 --vault <知识库路径>
```

1. `upgrade-schema` 读 `CLAUDE.md` / `AGENTS.md` 开头说明中的版本号（「Schema 模板（版本 N）」，两份不一致时按较旧的一份；找不到版本号的按旧版处理）。比模板旧时，把当前模板渲染为待合并版本 `Schema 待合并.md` 写进知识库并单独提交，**不改动**现有的 `CLAUDE.md` 与 `AGENTS.md`；已是最新时提示无需升级，不写任何文件。可以重复执行，结果相同。
2. 在知识库目录的 agent 会话里，照 `upgrade-schema` 的提示说「按 `Schema 待合并.md` 中的『合并 Schema 流程』合并 Schema」（版本 5 之前的 Schema 里还没有这个流程；版本 5 起说「合并 Schema」即可）。agent 按「合并 Schema 流程」：从知识库的 git 历史中取出当前 Schema 所基于的模板原文，分出知识库的定制（如采纳的体检建议）与新模板的变化，保留定制、并入变化、更新版本号，删除 `Schema 待合并.md`，以一次 git 提交（`Schema: 合并模板版本 N`）结束；定制与新模板冲突、无法兼顾的地方列出来请你决定。

## 用户配置目录

所有本机配置、凭据与运行状态都放在 `%APPDATA%\sub2obsidian\`，绝不进入知识库或任何 git 仓库：

| 位置 | 内容 |
| --- | --- |
| `config.toml` | 用户设置（UTF-8 TOML）。`vault`：知识库路径，首次 `init` 时自动记下；`[transcribe]` 表的 `terms`：转写术语表，`hallucinations`：追加的已知幻觉句；`[backfill]` 表的 `batch_size`、`interval` 与 `douyin_interval`：回填的批量大小与 B站、抖音的请求间隔 |
| `credentials/` | 平台与飞书应用凭据：`bilibili.cookies.txt`、`douyin.cookies.txt`（登录时导出；B站 每次运行时从浏览器配置重新导出一次，抖音每次运行时从浏览器配置读出一次 cookie 字符串，同一次运行内复用）、`feishu.env`（飞书应用的 App ID、App Secret 与你的 open_id，由配置向导写入） |
| `browser/<平台>/` | 登录用的 Playwright 持久化浏览器配置 |
| `state/` | 运行状态：`inbox.toml`（收件箱读到的位置、待重试的链接）、`backfill.toml`（每个收藏夹回填到哪一页、哪些平台已回填完成）、`feishu.toml`（与机器人私聊的会话 ID）等 |

## 开发

```powershell
uv sync
uv run pytest
```

测试只通过「CLI 命令 + 知识库目录」观察行为，全部在临时目录中运行；`%APPDATA%` 在测试中被指向临时目录。平台适配器、收件箱、转写引擎、凭据提供者四个外部端口在行为测试中换成假实现；真实的 B站、公众号适配器用 `tests/fixtures/` 中的录制样本做契约测试，抖音适配器用按 F2 返回结构构造的样本（`tests/fixtures/douyin/`，测试不需要安装 F2），飞书收件箱用按开放平台文档构造的响应样本（`tests/fixtures/feishu/`）做契约测试（见各目录的 README），测试不需要网络、登录或浏览器。

faster-whisper 的集成测试（`tests/test_faster_whisper.py`，测试音频为 Windows 语音合成的一段中文）只在本机有 CUDA 显卡、且 `large-v3-turbo` 模型已缓存时运行，否则自动跳过；测试从不下载模型。要运行它，先执行一次 `sub2obsidian transcribe`（或 `uv run python -c "from faster_whisper.utils import download_model; download_model('large-v3-turbo')"`）把模型下载好。

## 第三方组件

知识库中预装的 [Dataview](https://github.com/blacksmithgu/obsidian-dataview) 插件（0.5.70 release 文件）随包分发于 `src/sub2obsidian/assets/obsidian/plugins/dataview/`，MIT 许可证见同目录 `LICENSE`。
