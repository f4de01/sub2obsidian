"""行为测试的公共夹具：测试只通过「CLI 命令 + 知识库目录」观察行为。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from sub2obsidian.cli import Ports, cli


@dataclass
class FakeLauncher:
    """替换真实的 Obsidian 启动器，记录被打开的 URI。"""

    opened: list[str] = field(default_factory=list)

    def open(self, uri: str) -> None:
        self.opened.append(uri)


@dataclass
class Cli:
    launcher: FakeLauncher

    def run(self, *args: str) -> Result:
        result = CliRunner().invoke(
            cli, list(args), obj=Ports(launcher=self.launcher), catch_exceptions=False
        )
        return result


@pytest.fixture(autouse=True)
def user_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 %APPDATA% 指向临时目录，测试绝不碰真实的用户配置目录。"""
    appdata = tmp_path / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata / "sub2obsidian"


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def run(launcher: FakeLauncher) -> Cli:
    return Cli(launcher=launcher)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """一个位于中文路径下、尚不存在的知识库目录。"""
    return tmp_path / "我的库" / "知识库"
