"""启动器：把 URI 交给操作系统打开（如让 Obsidian 打开已登记的知识库）。"""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


class Launcher(Protocol):
    def open(self, uri: str) -> None: ...


class SystemLauncher:
    """交给系统的 URI 处理程序；Windows 上 obsidian:// 由 Obsidian 注册处理。"""

    def open(self, uri: str) -> None:
        if hasattr(os, "startfile"):
            os.startfile(uri)  # noqa: S606 - 打开的是本工具自己拼出的 obsidian:// URI
        else:
            webbrowser.open(uri)


def obsidian_open_uri(vault: Path) -> str:
    """`obsidian://open?path=…`：只能打开已在 Obsidian 登记的目录，不会登记新目录。"""
    return "obsidian://open?path=" + quote(str(vault), safe="")
