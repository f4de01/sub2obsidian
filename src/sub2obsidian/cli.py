"""sub2obsidian 的命令行入口。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import click

from sub2obsidian.capture import Outcome, capture_text, commit_changes
from sub2obsidian.config import UserConfig
from sub2obsidian.credentials import LOGIN_PLATFORMS, CredentialError, CredentialProvider
from sub2obsidian.git import GitError
from sub2obsidian.launcher import Launcher, SystemLauncher, obsidian_open_uri
from sub2obsidian.links import PLATFORM_NAMES
from sub2obsidian.platforms import PlatformAdapter
from sub2obsidian.tools import MissingTool
from sub2obsidian.transcription import Transcriber, transcribe_collected
from sub2obsidian.transcription import summary as transcription_summary
from sub2obsidian.vault import RAW_DIR, init_vault


@dataclass
class Ports:
    """CLI 依赖的外部端口；行为测试中整体替换为假实现。"""

    launcher: Launcher
    credentials: CredentialProvider
    adapters: Mapping[str, PlatformAdapter]  # 平台 → 平台适配器
    transcriber: Transcriber


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


@cli.command()
@click.argument("platform", type=click.Choice(sorted(LOGIN_PLATFORMS)))
@click.pass_obj
def login(ports: Ports, platform: str) -> None:
    """打开 PLATFORM 的登录页扫码登录；登录状态保存在用户配置目录，可复用。"""
    try:
        ports.credentials.login(platform)
    except CredentialError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"已登录 {PLATFORM_NAMES[platform]}，凭据保存在用户配置目录（不会进入知识库）")


def _initialized_vault(path: Path | None) -> Path:
    vault = (path or UserConfig.default().vault()).resolve()
    if not (vault / RAW_DIR).is_dir():
        raise click.ClickException(f"知识库尚未初始化：{vault}，请先执行 sub2obsidian init")
    return vault


vault_option = click.option(
    "--vault",
    "vault_path",
    type=click.Path(path_type=Path),
    help="知识库路径；缺省取用户配置中的知识库。",
)


@cli.command()
@click.argument("text", nargs=-1, required=True)
@vault_option
@click.pass_obj
def capture(ports: Ports, text: tuple[str, ...], vault_path: Path | None) -> None:
    """采集链接或分享文本中的来源（推送来的来源视为已通过筛选）。"""
    vault = _initialized_vault(vault_path)
    outcomes = capture_text(" ".join(text), vault, ports.adapters)
    for outcome in outcomes:
        click.echo(outcome.message, err=not outcome.ok)
    try:
        commit_changes(vault, outcomes)
    except GitError as error:
        raise click.ClickException(f"原始材料已写入，但 git 提交失败：{error}") from error
    if not all(outcome.ok for outcome in outcomes):
        raise SystemExit(1)


@cli.command()
@vault_option
@click.pass_obj
def transcribe(ports: Ports, vault_path: Path | None) -> None:
    """为所有「已采集」（没有平台字幕）的视频来源转写口播稿；适合单独批量执行。"""
    vault = _initialized_vault(vault_path)
    terms = UserConfig.default().glossary()
    outcomes: list[Outcome] = []
    stopped: MissingTool | None = None
    try:
        for outcome in transcribe_collected(vault, ports.adapters, ports.transcriber, terms):
            click.echo(outcome.message, err=not outcome.ok)
            outcomes.append(outcome)
    except MissingTool as error:
        stopped = error
    try:
        commit_changes(vault, outcomes, command="transcribe", verb="转写")
    except GitError as error:
        raise click.ClickException(f"口播稿已写入，但 git 提交失败：{error}") from error
    if stopped is not None:
        raise click.ClickException(f"转写中止：{stopped}")
    click.echo(transcription_summary(outcomes) if outcomes else "没有待转写的来源")
    if not all(outcome.ok for outcome in outcomes):
        raise SystemExit(1)


def main() -> None:
    from sub2obsidian.bilibili import BilibiliAdapter
    from sub2obsidian.browser_credentials import BrowserCredentials
    from sub2obsidian.whisper import FasterWhisperTranscriber

    credentials = BrowserCredentials(UserConfig.default())
    cli(
        obj=Ports(
            launcher=SystemLauncher(),
            credentials=credentials,
            adapters={"bilibili": BilibiliAdapter(credentials)},
            transcriber=FasterWhisperTranscriber(),
        )
    )
