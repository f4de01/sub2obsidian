"""凭据提供者：给出某平台可用的登录 cookie。凭据只存放在用户配置目录。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sub2obsidian.links import PLATFORM_NAMES


# 需要登录的平台
LOGIN_PLATFORMS = {"bilibili", "douyin"}


class CredentialError(Exception):
    """无法给出可用凭据；消息面向用户。"""


class LoginRequired(CredentialError):
    """没有登录或登录已失效；消息告诉用户重新登录哪个平台。"""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        name = PLATFORM_NAMES.get(platform, platform)
        super().__init__(f"请重新登录 {name}：sub2obsidian login {platform}")


class CredentialProvider(Protocol):
    def login(self, platform: str) -> None:
        """打开登录页供用户扫码，登录成功后保存凭据。"""
        ...

    def cookies_file(self, platform: str) -> Path:
        """导出供 yt-dlp 使用的 Netscape cookies.txt；未登录或已失效时抛 LoginRequired。"""
        ...

    def cookie_string(self, platform: str) -> str:
        """供 F2 使用的 cookie 字符串（`name=value; …`）；未登录或已失效时抛 LoginRequired。"""
        ...
