"""sub2obsidian 的命令行入口。"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import click

from sub2obsidian.capture import Outcome, capture_text, commit_changes
from sub2obsidian.compilation import Refused, mark_compiled, status_report
from sub2obsidian.config import UserConfig
from sub2obsidian.credentials import LOGIN_PLATFORMS, CredentialError, CredentialProvider
from sub2obsidian.git import GitError
from sub2obsidian.inbox import Inbox
from sub2obsidian.launcher import Launcher, SystemLauncher, obsidian_open_uri
from sub2obsidian.links import PLATFORM_NAMES
from sub2obsidian.platforms import PlatformAdapter
from sub2obsidian.sync import INBOX_STATE_FILE
from sub2obsidian.sync import summarize as summarize_sync
from sub2obsidian.sync import sync as sync_sources
from sub2obsidian.tools import MissingTool
from sub2obsidian.transcription import (
    Transcriber,
    summarize,
    transcribe_captured,
    transcribe_collected,
)
from sub2obsidian.vault import RAW_DIR, init_vault


@dataclass
class Ports:
    """CLI 依赖的外部端口；行为测试中整体替换为假实现。"""

    launcher: Launcher
    credentials: CredentialProvider
    adapters: Mapping[str, PlatformAdapter]  # 平台 → 平台适配器
    transcriber: Transcriber
    inbox: Inbox


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


def _run_batch(
    vault: Path, outcomes: Iterable[Outcome], *, command: str, verb: str
) -> tuple[list[Outcome], MissingTool | None]:
    """逐条输出结果，然后把原始材料的改动单独提交一次 git。

    缺少本机工具时整批中止，已完成的改动照常提交。
    """
    done: list[Outcome] = []
    stopped: MissingTool | None = None
    try:
        for outcome in outcomes:
            click.echo(outcome.message, err=not outcome.ok)
            done.append(outcome)
    except MissingTool as error:
        stopped = error
    try:
        commit_changes(vault, done, command=command, verb=verb)
    except GitError as error:
        raise click.ClickException(f"原始材料已写入，但 git 提交失败：{error}") from error
    return done, stopped


@cli.command()
@click.argument("text", nargs=-1, required=True)
@vault_option
@click.pass_obj
def capture(ports: Ports, text: tuple[str, ...], vault_path: Path | None) -> None:
    """采集链接或分享文本中的来源（推送来的来源视为已通过筛选）。

    抖音没有平台字幕，采集到的视频当场转写；其他平台没有字幕的视频等待 transcribe。
    """
    vault = _initialized_vault(vault_path)
    terms = UserConfig.default().glossary()

    def capture_then_transcribe() -> Iterator[Outcome]:
        captured: list[Outcome] = []
        for outcome in capture_text(" ".join(text), vault, ports.adapters):
            captured.append(outcome)
            yield outcome
        yield from transcribe_captured(vault, captured, ports.adapters, ports.transcriber, terms)

    outcomes, stopped = _run_batch(
        vault, capture_then_transcribe(), command="capture", verb="采集"
    )
    if stopped is not None:
        raise click.ClickException(f"capture 中止：{stopped}")
    if not all(outcome.ok for outcome in outcomes):
        raise SystemExit(1)


@cli.command()
@vault_option
@click.pass_obj
def transcribe(ports: Ports, vault_path: Path | None) -> None:
    """为所有「已采集」（没有平台字幕）的视频来源转写口播稿；适合单独批量执行。"""
    vault = _initialized_vault(vault_path)
    terms = UserConfig.default().glossary()
    outcomes, stopped = _run_batch(
        vault,
        transcribe_collected(vault, ports.adapters, ports.transcriber, terms),
        command="transcribe",
        verb="转写",
    )
    if stopped is not None:
        raise click.ClickException(f"转写中止：{stopped}")
    click.echo(summarize(outcomes) if outcomes else "没有待转写的来源")
    if not all(outcome.ok for outcome in outcomes):
        raise SystemExit(1)


@cli.command()
@vault_option
@click.pass_obj
def sync(ports: Ports, vault_path: Path | None) -> None:
    """读取收件箱中的新链接，采集所有「已通过」的来源并转写；原始材料的改动单独提交 git。

    缺少 ffmpeg、F2 等本机工具时整批中止，已完成的改动照常提交。
    """
    vault = _initialized_vault(vault_path)
    user_config = UserConfig.default()
    outcomes, stopped = _run_batch(
        vault,
        sync_sources(
            vault,
            ports.inbox,
            ports.adapters,
            ports.transcriber,
            user_config.glossary(),
            user_config.state_dir / INBOX_STATE_FILE,
        ),
        command="sync",
        verb="同步",
    )
    click.echo(summarize_sync(outcomes))
    if stopped is not None:
        raise click.ClickException(f"sync 中止：{stopped}")
    if not all(outcome.ok for outcome in outcomes):
        raise SystemExit(1)


@cli.command()
@vault_option
def status(vault_path: Path | None) -> None:
    """按来源状态汇总来源数量，并列出可编译的来源（编译时由 agent 读取）。"""
    vault = _initialized_vault(vault_path)
    report = status_report(vault)
    click.echo("来源状态：")
    for source_status, count in report.counts.items():
        click.echo(f"  {source_status} {count}")
    if not report.compilable:
        click.echo("可编译的来源：无")
        return
    click.echo(f"可编译的来源（{len(report.compilable)}）：")
    for source in report.compilable:
        click.echo(f"  {source.ref.key}  {source.kind}  {source.title}")
    click.echo(
        f"原始材料在 {RAW_DIR}/<平台>/<平台内ID>/；"
        "编译完成后执行 sub2obsidian mark-compiled <平台>/<平台内ID>…"
    )


@cli.command("mark-compiled")
@click.argument("sources", nargs=-1, required=True)
@vault_option
def mark_compiled_command(sources: tuple[str, ...], vault_path: Path | None) -> None:
    """把编译完成的来源转为「已编译」（agent 在编译结束、git 提交之前调用）。

    SOURCES 用 status 列出的「平台/平台内ID」指称来源。
    """
    vault = _initialized_vault(vault_path)
    try:
        compiled = mark_compiled(vault, sources)
    except Refused as error:
        raise click.ClickException(f"没有改动任何来源状态：\n{error}") from error
    for source in compiled:
        click.echo(f"已编译：{source.title}（{source.ref.key}）")


def main() -> None:
    from sub2obsidian.bilibili import BilibiliAdapter
    from sub2obsidian.browser_credentials import BrowserCredentials
    from sub2obsidian.douyin import DouyinAdapter
    from sub2obsidian.feishu import FeishuInbox
    from sub2obsidian.wechat import WechatAdapter
    from sub2obsidian.whisper import FasterWhisperTranscriber

    user_config = UserConfig.default()
    credentials = BrowserCredentials(user_config)
    cli(
        obj=Ports(
            launcher=SystemLauncher(),
            credentials=credentials,
            adapters={
                "bilibili": BilibiliAdapter(credentials),
                "douyin": DouyinAdapter(credentials),
                "wechat": WechatAdapter(),
            },
            transcriber=FasterWhisperTranscriber(),
            inbox=FeishuInbox(user_config),
        )
    )
