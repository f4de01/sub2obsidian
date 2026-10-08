"""收件箱：用户推送来源链接的落点。接口为「从游标之后取新消息」。

收件箱本身不记位置：游标由调用方（sync）保存在用户配置目录，下次原样交回。
飞书实现见 feishu.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class InboxError(Exception):
    """读取收件箱失败；消息面向用户。"""


class InboxNotConfigured(InboxError):
    """收件箱尚未配置（如还没有飞书应用凭据）。"""


@dataclass(frozen=True)
class InboxBatch:
    messages: list[str]  # 游标之后用户推送的消息文本，按推送先后
    cursor: str | None  # 读到的位置；下次从这里之后读


class Inbox(Protocol):
    def read(self, cursor: str | None) -> InboxBatch:
        """取游标之后的新消息；cursor 为 None 时从头读。失败抛 InboxError。"""
        ...
