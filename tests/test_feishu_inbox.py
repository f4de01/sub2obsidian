"""飞书收件箱的契约测试：用按开放平台文档构造的响应样本（tests/fixtures/feishu/）回放网络层。

不访问网络、不需要真实的飞书应用。样本说明见该目录的 README。
"""

import json
from pathlib import Path
from typing import Any

import pytest

from sub2obsidian.config import UserConfig
from sub2obsidian.feishu import FeishuInbox
from sub2obsidian.inbox import InboxError, InboxNotConfigured

FIXTURES = Path(__file__).parent / "fixtures" / "feishu"
APP_ID = "cli_a1b2c3d4e5f60708"
APP_SECRET = "假的应用密钥-0123456789abcdef"
OPEN_ID = "ou_7d8a6e6df7621556ce0d21922b676706"
CHAT_ID = "oc_5ad11d72b830411d72b836c20b7a4e1f"
TOKEN = "t-caecc734c2e3328a62489fe0648c4b98779515d3"
PAGE2_TOKEN = json.loads((FIXTURES / "messages_page1.json").read_text(encoding="utf-8"))["data"][
    "page_token"
]

SHARED_TEXT = "这个讲得好 [https://b23.tv/AbCd123](https://b23.tv/AbCd123)"
POST_TEXT = (
    "公众号这篇值得一看：通过增强PDF结构识别，革新检索增强生成技术(RAG) "
    "https://mp.weixin.qq.com/s/JJHlJsWEqFG77LdzhvzDNw"
)
APP_SHARE_TEXT = "【RAG 讲解-哔哩哔哩】 https://b23.tv/XyZ7890"
LAST = "1759900300321:om_4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ReplayClient:
    """回放飞书接口：按（方法, 路径, 分页标记）给出样本响应，并记录每次调用。"""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str, str | None], dict[str, Any]] = {
            ("POST", "/auth/v3/tenant_access_token/internal", None): fixture("tenant_access_token.json"),
            ("POST", "/im/v1/messages", None): fixture("send_message.json"),
            ("GET", "/im/v1/messages", None): fixture("messages_page1.json"),
            ("GET", "/im/v1/messages", PAGE2_TOKEN): fixture("messages_page2.json"),
        }
        self.calls: list[dict[str, Any]] = []

    def call(self, method, path, *, token=None, params=None, body=None) -> dict[str, Any]:
        self.calls.append(
            {"method": method, "path": path, "token": token, "params": params or {}, "body": body}
        )
        page = (params or {}).get("page_token")
        return self.responses[(method, path, page)]

    def calls_to(self, method: str, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method and c["path"] == path]


@pytest.fixture
def user_config(user_config_dir: Path) -> UserConfig:
    config = UserConfig(user_config_dir)
    config.credentials_dir.mkdir(parents=True)
    (config.credentials_dir / "feishu.env").write_text(
        f"FEISHU_APP_ID={APP_ID}\nFEISHU_APP_SECRET={APP_SECRET}\nFEISHU_OPEN_ID={OPEN_ID}\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture
def client() -> ReplayClient:
    return ReplayClient()


@pytest.fixture
def inbox(user_config: UserConfig, client: ReplayClient) -> FeishuInbox:
    return FeishuInbox(user_config, client)


def test_first_read_returns_every_user_message_across_pages_in_order(inbox, client):
    batch = inbox.read(None)

    assert batch.messages == [SHARED_TEXT, POST_TEXT, APP_SHARE_TEXT]
    assert batch.cursor == LAST
    token_call = client.calls_to("POST", "/auth/v3/tenant_access_token/internal")[0]
    assert token_call["body"] == {"app_id": APP_ID, "app_secret": APP_SECRET}
    pages = client.calls_to("GET", "/im/v1/messages")
    assert [page["params"].get("page_token") for page in pages] == [None, PAGE2_TOKEN]
    for page in pages:
        assert page["token"] == TOKEN
        assert page["params"]["container_id_type"] == "chat"
        assert page["params"]["container_id"] == CHAT_ID
        assert page["params"]["sort_type"] == "ByCreateTimeAsc"
        assert "start_time" not in page["params"]


def test_private_chat_is_bound_once_by_sending_the_user_a_greeting(inbox, client, user_config):
    inbox.read(None)
    inbox.read(LAST)

    sends = client.calls_to("POST", "/im/v1/messages")
    assert len(sends) == 1
    assert sends[0]["params"] == {"receive_id_type": "open_id"}
    assert sends[0]["body"]["receive_id"] == OPEN_ID
    assert sends[0]["body"]["msg_type"] == "text"
    assert "sub2obsidian sync" in json.loads(sends[0]["body"]["content"])["text"]
    assert (user_config.state_dir / "feishu.toml").is_file()


def test_read_after_cursor_starts_at_its_second_and_skips_messages_already_read(inbox, client):
    inbox.read(None)
    client.calls.clear()

    batch = inbox.read("1759900180789:om_2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f")

    first_page = client.calls_to("GET", "/im/v1/messages")[0]
    assert first_page["params"]["start_time"] == "1759900180"
    # 回放的两页里游标及之前的消息都不再返回
    assert batch.messages == [APP_SHARE_TEXT]
    assert batch.cursor == LAST


def test_read_with_nothing_new_keeps_the_cursor(inbox, client):
    client.responses[("GET", "/im/v1/messages", None)] = {
        "code": 0,
        "msg": "success",
        "data": {"has_more": False, "items": []},
    }

    batch = inbox.read(LAST)

    assert batch.messages == []
    assert batch.cursor == LAST


def test_missing_credentials_means_inbox_not_configured(user_config_dir: Path, client):
    inbox = FeishuInbox(UserConfig(user_config_dir), client)

    with pytest.raises(InboxNotConfigured, match="feishu-inbox-setup.sh"):
        inbox.read(None)
    assert client.calls == []


def test_incomplete_credentials_name_the_missing_value(user_config, client):
    (user_config.credentials_dir / "feishu.env").write_text(
        f"FEISHU_APP_ID={APP_ID}\nFEISHU_APP_SECRET={APP_SECRET}\n", encoding="utf-8"
    )

    with pytest.raises(InboxNotConfigured, match="FEISHU_OPEN_ID"):
        FeishuInbox(user_config, client).read(None)


def test_invalid_app_secret_asks_to_rerun_setup(inbox, client):
    client.responses[("POST", "/auth/v3/tenant_access_token/internal", None)] = fixture(
        "tenant_access_token_invalid_secret.json"
    )

    with pytest.raises(InboxError, match=r"凭据无效（10014.*feishu-inbox-setup\.sh"):
        inbox.read(None)


def test_bot_unavailable_to_the_user_explains_availability_scope(inbox, client, user_config):
    client.responses[("POST", "/im/v1/messages", None)] = fixture("send_message_no_availability.json")

    with pytest.raises(InboxError, match="230013.*可用范围"):
        inbox.read(None)
    assert not (user_config.state_dir / "feishu.toml").exists()


def test_missing_message_permission_names_the_scope(inbox, client):
    client.responses[("GET", "/im/v1/messages", None)] = fixture("messages_no_permission.json")

    with pytest.raises(InboxError, match="99991672.*im:message"):
        inbox.read(None)
