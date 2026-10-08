"""sub2obsidian 的命令行入口。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from sub2obsidian.config import UserConfig
from sub2obsidian.git import GitError
from sub2obsidian.launcher import Launcher, SystemLauncher, obsidian_open_uri
from sub2obsidian.vault import init_vault


@dataclass
class Ports:
    """CLI 依赖的外部端口；行为测试中整体替换为假实现。"""

    launcher: Launcher


@click.group()
def cli() -> None:
    """把三平台收藏编译成 Obsidian LLM Wiki 知识库。"""


@cli.command()
@click.argument("path", type=click.Path(path_type=Path), required=False)
@click.pass_obj
def init(ports: Ports, path: Path | None) -> None:
    r"""新建（或补全）知识库，并在 Obsidian 中打开。

    PATH 缺省时取用户配置中的知识库路径，再缺省为 D:\Obsidian\知识库。
    """
    user_config = UserConfig.default()
    vault = (path or user_config.vault()).resolve()
    if vault.exists() and not vault.is_dir():
        raise click.ClickException(f"{vault} 不是目录，无法在此初始化知识库")
    try:
        result = init_vault(vault)
    except GitError as error:
        raise click.ClickException(str(error)) from error
    user_config.remember_vault(vault)
    if result.new_vault:
        click.echo(f"已新建知识库：{vault}")
    elif result.created:
        click.echo(f"已补齐 {len(result.created)} 项：{'、'.join(result.created)}")
    else:
        click.echo("知识库完整，无需补齐")
    ports.launcher.open(obsidian_open_uri(vault))
    click.echo("已请求 Obsidian 打开该知识库")


def main() -> None:
    cli(obj=Ports(launcher=SystemLauncher()))
