# 飞书收件箱契约测试样本

全部是**构造**的样本：调用飞书接口需要真实的自建应用与凭据，不能在测试里录制。字段与结构照搬飞书开放平台文档
（2026-10 查阅）中的响应示例与字段说明，ID、时间戳与消息内容替换为合乎格式的假值。

| 文件 | 接口 | 依据 |
| --- | --- | --- |
| `tenant_access_token.json` | `POST /open-apis/auth/v3/tenant_access_token/internal` | [自建应用获取 tenant_access_token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal) 的响应体示例 |
| `tenant_access_token_invalid_secret.json` | 同上 | 通用错误码 10014（app secret invalid） |
| `send_message.json` | `POST /open-apis/im/v1/messages?receive_id_type=open_id` | [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create) 的响应体：`data` 是发出的消息，其中 `chat_id` 即与用户私聊的会话 ID |
| `send_message_no_availability.json` | 同上 | 错误码 230013：机器人对该用户不可用（不在应用可用范围内） |
| `messages_page1.json`、`messages_page2.json` | `GET /open-apis/im/v1/messages?container_id_type=chat&sort_type=ByCreateTimeAsc` | [获取会话历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list) 的响应体示例与分页规则（`has_more` 为 true 时给出 `page_token`）；`create_time` 为毫秒；`body.content` 是 JSON 字符串，格式见 [接收消息内容](https://open.feishu.cn/document/server-docs/im-v1/message-content-description/message_content)：文本消息里的超链接写作 `[文字](地址)`，富文本 `post` 由 `text`、`a`、`img` 等标签组成 |
| `messages_no_permission.json` | 同上 | 通用错误码 99991672：应用没有开通所需权限 |

两页消息合起来是一段私聊：机器人发的连接说明（应忽略）、一条带飞书超链接写法的文本、一张图片（无链接）、
一条富文本（链接在 `a` 标签里）、一条已撤回的消息（应忽略）、一条 App 分享文本。

接入真实应用后（#12 人工验收），如果解析与样本不符，用真实响应（脱敏后）替换这些文件，契约测试应仍然通过。
