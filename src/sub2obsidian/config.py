r"""用户配置目录 %APPDATA%\sub2obsidian\ 的读写约定。

目录布局（后续工单按此存放，全部只在本机、绝不进入知识库或任何 git 仓库）：

    config.toml        用户设置，UTF-8 编码的 TOML；目前有 vault（知识库路径）
    credentials/       平台与飞书应用凭据（cookies.txt、cookie 字符串、App Secret 等）
    browser/<平台>/    凭据提供者使用的 Playwright 持久化浏览器配置
    state/             收件箱游标、回填断点等运行状态
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

APP_NAME = "sub2obsidian"
DEFAULT_VAULT = Path("D:/Obsidian/知识库")


class UserConfig:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def default(cls) -> UserConfig:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return cls(base / APP_NAME)

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    @property
    def credentials_dir(self) -> Path:
        return self.root / "credentials"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    def browser_profile_dir(self, platform: str) -> Path:
        return self.root / "browser" / platform

    def read(self) -> dict[str, Any]:
        if not self.config_file.exists():
            return {}
        return tomllib.loads(self.config_file.read_text(encoding="utf-8"))

    def write(self, settings: dict[str, Any]) -> None:
        """整体写回 config.toml；先写临时文件再替换，避免写坏。"""
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.config_file.with_suffix(".toml.tmp")
        temporary.write_text(tomli_w.dumps(settings), encoding="utf-8", newline="\n")
        temporary.replace(self.config_file)

    def vault(self) -> Path:
        """配置中的知识库路径；未配置时为默认路径。"""
        configured = self.read().get("vault")
        return Path(configured) if configured else DEFAULT_VAULT

    def remember_vault(self, vault: Path) -> None:
        """首次初始化时记下知识库路径，供后续命令使用；已配置的不覆盖。"""
        settings = self.read()
        if "vault" not in settings:
            settings["vault"] = str(vault)
            self.write(settings)
