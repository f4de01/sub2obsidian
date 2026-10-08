r"""用户配置目录 %APPDATA%\sub2obsidian\ 的读写约定。

目录布局（后续工单按此存放，全部只在本机、绝不进入知识库或任何 git 仓库）：

    config.toml        用户设置，UTF-8 编码的 TOML：vault（知识库路径）、[transcribe]（术语表）、
                       [backfill]（回填的批量大小与请求间隔）
    credentials/       平台与飞书应用凭据（cookies.txt、cookie 字符串、App Secret 等）
    browser/<平台>/    凭据提供者使用的 Playwright 持久化浏览器配置
    state/             收件箱游标、回填断点等运行状态
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomli_w

from sub2obsidian.files import write_text_atomically

APP_NAME = "sub2obsidian"
DEFAULT_VAULT = Path("D:/Obsidian/知识库")


def appdata_dir() -> Path:
    r"""%APPDATA%（缺省为 ~\AppData\Roaming）：本工具与 Obsidian 的用户配置目录都在其下。"""
    appdata = os.environ.get("APPDATA")
    return Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"


class ConfigError(ValueError):
    """config.toml 中的设置无效；消息指出哪一项、应该怎么写。"""


@dataclass(frozen=True)
class BackfillSettings:
    """回填设置。

    batch_size：每次 sync 每个平台最多登记多少条新来源。
    interval：B站 请求之间的随机间隔（最短, 最长），单位秒；对回填与采集的请求都生效。
    douyin_interval：抖音接口请求之间的随机间隔（最短, 最长），单位秒；对回填与采集都生效。
    抖音风控更严，默认间隔更长。
    """

    batch_size: int = 50
    interval: tuple[float, float] = (1.0, 3.0)
    douyin_interval: tuple[float, float] = (3.0, 6.0)


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def _interval(section: dict[str, Any], key: str, default: tuple[float, float]) -> tuple[float, float]:
    """[backfill] 表中的一项随机间隔：[最短, 最长] 秒，或一个数；写错时抛 ConfigError。"""
    interval = section.get(key, list(default))
    if _number(interval):
        interval = [interval, interval]
    if not (
        isinstance(interval, list)
        and len(interval) == 2
        and all(_number(value) for value in interval)
        and interval[0] <= interval[1]
    ):
        raise ConfigError(
            f"config.toml 中 [backfill] {key} 应为 [最短, 最长] 秒（如 [2, 5]），现为 {interval!r}"
        )
    return (float(interval[0]), float(interval[1]))


class UserConfig:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def default(cls) -> UserConfig:
        return cls(appdata_dir() / APP_NAME)

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
        """整体写回 config.toml。"""
        write_text_atomically(self.config_file, tomli_w.dumps(settings))

    def vault(self) -> Path:
        """配置中的知识库路径；未配置时为默认路径。"""
        configured = self.read().get("vault")
        return Path(configured) if configured else DEFAULT_VAULT

    def glossary(self) -> list[str]:
        """转写术语表：[transcribe] 表的 terms，作为提示传给转写引擎；未配置时为空。"""
        terms = self.read().get("transcribe", {}).get("terms", [])
        if isinstance(terms, str):  # 只写了一个术语时也能用
            terms = [terms]
        return [str(term).strip() for term in terms if str(term).strip()]

    def backfill(self) -> BackfillSettings:
        """[backfill] 表：batch_size（正整数），interval 与 douyin_interval（[最短, 最长] 秒，
        或一个数）；缺省取默认值，写错时抛 ConfigError。"""
        section = self.read().get("backfill", {})
        default = BackfillSettings()
        batch_size = section.get("batch_size", default.batch_size)
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ConfigError(f"config.toml 中 [backfill] batch_size 应为正整数，现为 {batch_size!r}")
        return BackfillSettings(
            batch_size,
            _interval(section, "interval", default.interval),
            _interval(section, "douyin_interval", default.douyin_interval),
        )

    def remember_vault(self, vault: Path) -> None:
        """首次初始化时记下知识库路径，供后续命令使用；已配置的不覆盖。"""
        settings = self.read()
        if "vault" not in settings:
            settings["vault"] = str(vault)
            self.write(settings)
