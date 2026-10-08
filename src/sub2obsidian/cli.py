"""sub2obsidian 的命令行入口。"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import click

from sub2obsidian.batch import Outcome, commit_changes
from sub2obsidian.batch import summarize as summarize_batch
from sub2obsidian.capture import capture_text
from sub2obsidian.compilation import Refused, mark_compiled, status_report
from sub2obsidian.config import BackfillSettings, ConfigError, UserConfig
from sub2obsidian.credentials import LOGIN_PLATFORMS, CredentialError, CredentialProvider
from sub2obsidian.git import GitError
from sub2obsidian.inbox import Inbox
from sub2obsidian.launcher import Launcher, SystemLauncher, obsidian_open_uri
from sub2obsidian.links import PLATFORM_NAMES
from sub2obsidian.obsidian_registry import registered_in_obsidian
from sub2obsidian.platforms import PlatformAdapter
from sub2obsidian.screening import ScreenRefused, update_list
from sub2obsidian.screening import screen as screen_sources
from sub2obsidian.screening import summarize as summarize_screening
from sub2obsidian.sync import sync as sync_sources
from sub2obsidian.tools import MissingTool
from sub2obsidian.transcription import (
    TranscribeSettings,
    Transcriber,
    transcribe_captured,
    transcribe_collected,
)
from sub2obsidian.transcription import summarize as summarize_transcription
from sub2obsidian.vault import (
    PENDING_SCHEMA,
    RAW_DIR,
    SCHEMA_VERSION,
    SCREENING_LIST,
    NoSchema,
    SchemaState,
    init_vault,
    upgrade_schema,
)


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
    if registered_in_obsidian(vault):
        ports.launcher.open(obsidian_open_uri(vault))
        click.echo("已请求 Obsidian 打开该知识库")
    else:
        click.echo(
            "该知识库尚未在 Obsidian 登记，请手动打开一次（之后 Obsidian 会记住它）：\n"
            "  1. 在 Obsidian 左下角点仓库名，选「管理仓库…」\n"
            "  2. 点「打开本地仓库」，选择该路径：\n"
            f"     {vault}\n"
            "  3. 提示是否信任该知识库的插件时，选择信任（Dataview 才会启用）"
        )


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


def _transcribe_settings(user_config: UserConfig) -> TranscribeSettings:
    return TranscribeSettings(user_config.glossary(), user_config.hallucinations())


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
    vault: Path,
    outcomes: Iterable[Outcome],
    *,
    command: str,
    verb: str,
    also: Sequence[str] = (),
) -> tuple[list[Outcome], MissingTool | None]:
    """逐条输出结果，然后把原始材料的改动单独提交一次 git。

    缺少本机工具时整批中止、用户按 Ctrl+C 时中断，已完成的改动都照常提交；遇到意外错误时
    也先提交已完成的改动，再报出错误。
    """
    done: list[Outcome] = []
    stopped: MissingTool | None = None
    interrupted = False

    def commit() -> None:
        try:
            commit_changes(vault, done, command=command, verb=verb, also=also)
        except GitError as error:
            raise click.ClickException(f"原始材料已写入，但 git 提交失败：{error}") from error

    try:
        for outcome in outcomes:
            click.echo(outcome.message, err=not outcome.ok)
            done.append(outcome)
    except MissingTool as error:
        stopped = error
    except KeyboardInterrupt:
        interrupted = True
    except Exception:
        commit()
        raise
    commit()
    if interrupted:
        raise click.ClickException(
            f"{command} 已中断：已完成的改动已提交，再次执行 {command} 会从中断处接着处理"
        )
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
    settings = _transcribe_settings(UserConfig.default())

    def capture_then_transcribe() -> Iterator[Outcome]:
        captured: list[Outcome] = []
        try:
            for outcome in capture_text(" ".join(text), vault, ports.adapters):
                captured.append(outcome)
                yield outcome
        finally:
            update_list(vault)  # 推送了待筛的来源时，它已转为已通过，离开待筛清单
        yield from transcribe_captured(
            vault, captured, ports.adapters, ports.transcriber, settings
        )

    outcomes, stopped = _run_batch(
        vault, capture_then_transcribe(), command="capture", verb="采集", also=[SCREENING_LIST]
    )
    click.echo(summarize_batch(outcomes, "capture"))
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
    settings = _transcribe_settings(UserConfig.default())
    outcomes, stopped = _run_batch(
        vault,
        transcribe_collected(vault, ports.adapters, ports.transcriber, settings),
        command="transcribe",
        verb="转写",
    )
    if stopped is not None:
        raise click.ClickException(f"转写中止：{stopped}")
    click.echo(summarize_transcription(outcomes) if outcomes else "没有待转写的来源")
    if not all(outcome.ok for outcome in outcomes):
        raise SystemExit(1)


@cli.command()
@vault_option
@click.pass_obj
def sync(ports: Ports, vault_path: Path | None) -> None:
    """读取收件箱中的新链接，拉取 B站 与抖音收藏（新来源待筛，更新待筛清单），采集所有
    「已通过」的来源并转写；原始材料的改动单独提交 git。

    回填每次每个平台只登记一批，下次 sync 从断点继续；抖音收藏只回填一次。一个平台拉取失败
    不影响其他平台。采集或转写时缺少 ffmpeg、F2 等本机工具则整批中止，已完成的改动照常提交。
    """
    vault = _initialized_vault(vault_path)
    user_config = UserConfig.default()
    try:
        backfill = user_config.backfill()
    except ConfigError as error:
        raise click.ClickException(str(error)) from error
    outcomes, stopped = _run_batch(
        vault,
        sync_sources(
            vault,
            ports.inbox,
            ports.adapters,
            ports.transcriber,
            _transcribe_settings(user_config),
            user_config.state_dir,
            backfill.batch_size,
        ),
        command="sync",
        verb="同步",
        also=[SCREENING_LIST],
    )
    click.echo(summarize_batch(outcomes, "sync"))
    if stopped is not None:
        raise click.ClickException(f"sync 中止：{stopped}")
    if not all(outcome.ok for outcome in outcomes):
        raise SystemExit(1)


@cli.command()
@click.option("--reject-all", is_flag=True, help="清单里一个都没勾选时，确认全部拒绝。")
@vault_option
def screen(reject_all: bool, vault_path: Path | None) -> None:
    """按待筛清单筛选：勾选的来源转为「已通过」，未勾选的转为「已拒绝」。

    已通过的来源在下次 sync 时采集并转写；已拒绝的只留存根，不再进入待筛清单。
    """
    vault = _initialized_vault(vault_path)
    try:
        screened = screen_sources(vault, reject_all=reject_all)
    except ScreenRefused as error:
        raise click.ClickException(f"没有改动任何来源状态：{error}") from error
    outcomes, _ = _run_batch(
        vault, screened, command="screen", verb="筛选", also=[SCREENING_LIST]
    )
    click.echo(summarize_screening(outcomes))
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


@cli.command("upgrade-schema")
@vault_option
def upgrade_schema_command(vault_path: Path | None) -> None:
    """把新版 Schema 模板写成待合并版本，由 agent 按「合并 Schema」流程合并。"""
    vault = _initialized_vault(vault_path)
    try:
        upgrade = upgrade_schema(vault)
    except NoSchema as error:
        raise click.ClickException(f"{error}，请先执行 sub2obsidian init 补齐") from error
    except GitError as error:
        raise click.ClickException(str(error)) from error
    if upgrade.state is SchemaState.UP_TO_DATE:
        click.echo(f"知识库的 Schema 已是版本 {upgrade.current}，无需升级")
        return
    if upgrade.state is SchemaState.NEWER:
        click.echo(
            f"知识库的 Schema 是版本 {upgrade.current}，比本工具的模板（版本 {SCHEMA_VERSION}）新；"
            "没有写出任何文件。请先升级 sub2obsidian"
        )
        return
    current = (
        "没有可识别的版本号（开头说明中的「Schema 模板（版本 N）」），按旧版处理"
        if upgrade.current is None
        else f"是版本 {upgrade.current}"
    )
    click.echo(
        f"知识库的 Schema {current}，模板已是版本 {SCHEMA_VERSION}：\n"
        f"已写出待合并版本 {PENDING_SCHEMA} 并提交，CLAUDE.md 与 AGENTS.md 未改动。\n"
        f"在知识库目录中对 agent 说「按 {PENDING_SCHEMA} 中的「合并 Schema 流程」合并 Schema」"
        "（旧版 Schema 里还没有这个流程）：它会保留知识库的定制、并入新模板的变化，"
        "更新版本号后删除待合并文件并提交。"
    )


def real_ports(user_config: UserConfig) -> Ports:
    """按用户配置组装真实的外部端口。"""
    from sub2obsidian.bilibili import BilibiliAdapter, HttpBilibiliClient
    from sub2obsidian.browser_credentials import BrowserCredentials
    from sub2obsidian.douyin import DouyinAdapter, F2DouyinClient
    from sub2obsidian.feishu import FeishuInbox
    from sub2obsidian.wechat import WechatAdapter
    from sub2obsidian.whisper import FasterWhisperTranscriber

    credentials = BrowserCredentials(user_config)
    try:
        settings = user_config.backfill()
    except ConfigError:  # sync 读取设置时会报出这个错误
        settings = BackfillSettings()
    return Ports(
        launcher=SystemLauncher(),
        credentials=credentials,
        adapters={
            "bilibili": BilibiliAdapter(credentials, HttpBilibiliClient(settings.interval)),
            "douyin": DouyinAdapter(credentials, F2DouyinClient(settings.douyin_interval)),
            "wechat": WechatAdapter(),
        },
        transcriber=FasterWhisperTranscriber(),
        inbox=FeishuInbox(user_config),
    )


def main() -> None:
    cli(obj=real_ports(UserConfig.default()))
