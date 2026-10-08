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

## 用户配置目录

所有本机配置、凭据与运行状态都放在 `%APPDATA%\sub2obsidian\`，绝不进入知识库或任何 git 仓库：

| 位置 | 内容 |
| --- | --- |
| `config.toml` | 用户设置（UTF-8 TOML）。`vault`：知识库路径，首次 `init` 时自动记下 |
| `credentials/` | 平台与飞书应用凭据 |
| `browser/<平台>/` | 登录用的 Playwright 持久化浏览器配置 |
| `state/` | 收件箱游标、回填断点等运行状态 |

## 开发

```powershell
uv sync
uv run pytest
```

测试只通过「CLI 命令 + 知识库目录」观察行为，全部在临时目录中运行；`%APPDATA%` 在测试中被指向临时目录。

## 第三方组件

知识库中预装的 [Dataview](https://github.com/blacksmithgu/obsidian-dataview) 插件（0.5.70 release 文件）随包分发于 `src/sub2obsidian/assets/obsidian/plugins/dataview/`，MIT 许可证见同目录 `LICENSE`。
