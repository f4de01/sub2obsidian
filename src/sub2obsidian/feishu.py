r"""飞书收件箱：用户把链接私聊发给飞书自建应用的机器人，sync 时通过消息列表 API 主动拉取。

不需要公网回调，也不需要常驻进程。凭据与状态都在用户配置目录：

    credentials\feishu.env   FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_OPEN_ID（你在该应用下的
                             open_id）；由 scripts/feishu-inbox-setup.sh 引导写入
    state\feishu.toml        与机器人私聊的会话 ID（chat_id），首次读取时绑定

私聊的 chat_id 没有接口可以直接查（群列表不含单聊），所以首次读取时由机器人给你发一条
说明消息，从发送结果中取得 chat_id 并记下。

网络层（FeishuClient）与解析分开：契约测试用按开放平台文档构造的响应样本回放网络层。

你能给机器人发消息，前提是应用订阅了「接收消息」事件（im.message.receive_v1）；开发者后台保存
「使用长连接接收事件」这一订阅方式时，要求当时有客户端连着长连接：见 feishu_events.py，
只在配置时用，日常 sync 照常主动拉取。
"""

from __future__ import annotations

import json
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

import tomli_w

from sub2obsidian.config import UserConfig
from sub2obsidian.files import write_text_atomically
from sub2obsidian.inbox import InboxBatch, InboxError, InboxNotConfigured

API = "https://open.feishu.cn/open-apis"
CREDENTIALS_FILE = "feishu.env"
STATE_FILE = "feishu.toml"
SETUP_HINT = "在 sub2obsidian 仓库中运行 bash scripts/feishu-inbox-setup.sh 配置飞书应用"
GREETING = (
    "sub2obsidian 收件箱已连接。以后把 B站、公众号等的分享链接发到这里，"
    "电脑上运行 sub2obsidian sync 即可采集。"
)
PAGE_SIZE = 50

# 开放平台错误码 → 给用户的处理办法
_ADVICE = {
    230006: "应用还没有开启机器人能力：在开发者后台添加「机器人」能力并发布新版本",
    230013: "机器人对你不可用：在开发者后台把你加入应用的可用范围，并发布新版本",
    230027: "应用缺少读取消息的权限：开通 im:message 权限并发布新版本",
    99991672: "应用缺少权限：开通 im:message（获取与发送单聊、群组消息）并发布新版本",
}


class FeishuClient(Protocol):
    """飞书开放平台网络层。"""

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用接口（path 相对 /open-apis），返回响应 JSON，包括 code 非 0 的出错响应。

        连不上飞书时抛 InboxError。
        """
        ...


class FeishuInbox:
    def __init__(self, user_config: UserConfig, client: FeishuClient | None = None) -> None:
        self.user_config = user_config
        self.client = client or HttpFeishuClient()

    def read(self, cursor: str | None) -> InboxBatch:
        app_id, app_secret, open_id = read_credentials(
            self.user_config, "FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_OPEN_ID"
        )
        token = self._token(app_id, app_secret)
        chat_id = self._chat(token, open_id)
        after = _parse_cursor(cursor)
        messages: list[str] = []
        last = after
        for item in self._items(token, chat_id, after):
            position = (int(item["create_time"]), item["message_id"])
            if after is not None and (position[0] < after[0] or position == after):
                continue
            last = position
            if (item.get("sender") or {}).get("sender_type") != "user" or item.get("deleted"):
                continue
            text = _text(item)
            if text:
                messages.append(text)
        return InboxBatch(messages, None if last is None else f"{last[0]}:{last[1]}")

    def _token(self, app_id: str, app_secret: str) -> str:
        response = self.client.call(
            "POST",
            "/auth/v3/tenant_access_token/internal",
            body={"app_id": app_id, "app_secret": app_secret},
        )
        if response.get("code") != 0:
            raise InboxError(
                f"飞书应用凭据无效（{response.get('code')}：{response.get('msg')}），"
                f"请重新{SETUP_HINT}"
            )
        return response["tenant_access_token"]

    def _chat(self, token: str, open_id: str) -> str:
        """与机器人私聊的 chat_id；还没绑定（或换了用户）时发一条说明消息来取得。"""
        path = self.user_config.state_dir / STATE_FILE
        state = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if state.get("open_id") == open_id and state.get("chat_id"):
            return state["chat_id"]
        response = self.client.call(
            "POST",
            "/im/v1/messages",
            token=token,
            params={"receive_id_type": "open_id"},
            body={
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": GREETING}, ensure_ascii=False),
            },
        )
        chat_id = _data(response, "连接飞书私聊失败")["chat_id"]
        write_text_atomically(path, tomli_w.dumps({"open_id": open_id, "chat_id": chat_id}))
        return chat_id

    def _items(
        self, token: str, chat_id: str, after: tuple[int, str] | None
    ) -> Iterator[dict[str, Any]]:
        """按创建时间升序逐页列出私聊消息；有游标时从游标所在的那一秒开始。"""
        params = {
            "container_id_type": "chat",
            "container_id": chat_id,
            "sort_type": "ByCreateTimeAsc",
            "page_size": str(PAGE_SIZE),
        }
        if after is not None:
            params["start_time"] = str(after[0] // 1000)
        while True:
            response = self.client.call("GET", "/im/v1/messages", token=token, params=params)
            data = _data(response, "读取飞书消息失败")
            yield from data.get("items") or []
            if not data.get("has_more"):
                return
            params = {**params, "page_token": data["page_token"]}


def read_credentials(user_config: UserConfig, *keys: str) -> list[str]:
    """feishu.env 中的这几项（按给出的顺序）；文件不在或缺项时抛 InboxNotConfigured。"""
    path = user_config.credentials_dir / CREDENTIALS_FILE
    if not path.exists():
        raise InboxNotConfigured(f"飞书收件箱尚未配置：{SETUP_HINT}")
    values = _read_env(path)
    missing = [key for key in keys if not values.get(key)]
    if missing:
        raise InboxNotConfigured(f"飞书凭据缺少 {'、'.join(missing)}：{SETUP_HINT}")
    return [values[key] for key in keys]


def _data(response: dict[str, Any], action: str) -> dict[str, Any]:
    code = response.get("code")
    if code == 0:
        return response.get("data") or {}
    advice = _ADVICE.get(code) if isinstance(code, int) else None
    raise InboxError(f"{action}（{code}：{response.get('msg')}）" + (f"：{advice}" if advice else ""))


def _parse_cursor(cursor: str | None) -> tuple[int, str] | None:
    """游标为「最后一条已读消息的创建时间（毫秒）:消息 ID」。"""
    if not cursor:
        return None
    millis, _, message_id = cursor.partition(":")
    return int(millis), message_id


def _text(item: dict[str, Any]) -> str:
    """文本与富文本消息的纯文字；富文本里的超链接写成链接地址。其他类型的消息不含链接。"""
    try:
        content = json.loads((item.get("body") or {}).get("content") or "{}")
    except json.JSONDecodeError:
        return ""
    if item.get("msg_type") == "text":
        return str(content.get("text", "")).strip()
    if item.get("msg_type") == "post":
        lines = [content.get("title") or ""]
        for paragraph in content.get("content") or []:
            lines.append("".join(_post_element(element) for element in paragraph))
        return "\n".join(line for line in lines if line.strip()).strip()
    return ""


def _post_element(element: dict[str, Any]) -> str:
    tag = element.get("tag")
    if tag == "text":
        return element.get("text", "")
    if tag == "a":
        href, text = element.get("href", ""), element.get("text", "")
        return f"{text} {href} " if text and text != href else f" {href} "
    return ""


def _read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE 每行一项（向导写入的格式）；# 开头为注释。"""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("\"'")
    return values


class HttpFeishuClient:
    """真实网络层：urllib 访问飞书开放平台。"""

    TIMEOUT = 30

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = API + path + (f"?{urlencode(params)}" if params else "")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # 接口出错时飞书仍在响应体里给出 code 与 msg
            try:
                return json.loads(error.read().decode("utf-8"))
            except ValueError:
                raise InboxError(f"飞书接口出错：HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise InboxError(f"连接飞书失败：{error}") from error
