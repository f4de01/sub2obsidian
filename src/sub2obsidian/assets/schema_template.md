# Schema

> 本文件由 sub2obsidian 的 Schema 模板（版本 $schema_version）在初始化时生成，此后归本知识库所有，可随 Wiki 演进自行修改；工具不会再覆盖它。
> 这是初版占位，编译、问询、体检的完整流程将在后续版本的 Schema 模板中补全。

本知识库是一个 Karpathy 式 LLM Wiki：工具把收藏的来源采集为原始材料，由你（agent）按本 Schema 把原始材料编译成按概念组织、互相链接的 Wiki。

## 知识库结构

| 位置 | 内容 | 谁来写 |
| --- | --- | --- |
| `$raw_dir/` | 原始材料：每个来源一个目录，含元数据、正文或口播稿、封面、图片 | 只由 `sub2obsidian` CLI 写入，只增不改 |
| `$sources_dir/` | 来源页：单个来源的摘要 | agent |
| `$concepts_dir/` | 概念页：围绕一个知识概念汇总多个来源 | agent |
| `$syntheses_dir/` | 综述页：用户要求存档的问询答案 | agent |
| `$domains_dir/` | 主题域入口页（如 AI、理财） | agent |
| `$notes_dir/` | 我的笔记：用户亲手写的思考 | 只有用户 |
| `$attachments_dir/` | 用户粘贴的附件 | 用户 |
| `index.md` | Wiki 页面索引 | agent |
| `log.md` | 操作日志，只追加 | agent |

## 不可违反的规则

- 不得修改 `$raw_dir/` 中的任何文件；来源状态只能通过 `sub2obsidian` CLI 变更。
- 不得改动 `$notes_dir/` 中的任何文件；编译时把它作为优先级最高的来源读入。
- 改写页面时，原样保留用户的批注（`> [!我]` callout）。
- 概念页中的每条论断都要有出处，指向对应的来源页。
- 每次编译以一次 git 提交结束。
