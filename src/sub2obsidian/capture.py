"""推送采集：把分享文本中的每个链接作为推送来的来源采集进原始材料。"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sub2obsidian import git, transcript
from sub2obsidian.credentials import CredentialError
from sub2obsidian.links import PLATFORM_NAMES, UnsupportedLink, extract_urls, normalize
from sub2obsidian.platforms import (
    AdapterError,
    Article,
    FetchedSource,
    FetchFailed,
    PlatformAdapter,
    SourceUnavailable,
)
from sub2obsidian.sources import Kind, Origin, Source, SourceRepository, Status
from sub2obsidian.tools import MissingTool

TRANSCRIPT_FILE = "口播稿.md"
BODY_FILE = "正文.md"

# 采集前（含失效存根）按平台假定的来源类型；采集后以适配器给出的类型为准
_PLATFORM_KINDS = {"wechat": Kind.ARTICLE}


@dataclass(frozen=True)
class Outcome:
    message: str
    ok: bool
    changed: Source | None = None  # 本次改动了的来源
    new: bool = False  # 本次新登记了来源


def capture_text(
    text: str, vault: Path, adapters: Mapping[str, PlatformAdapter]
) -> Iterator[Outcome]:
    """逐个采集文本中的链接，每完成一个产出一个结果。

    缺少本机工具时抛 MissingTool，之前产出的结果仍然有效。
    """
    repo = SourceRepository(vault)
    urls = extract_urls(text)
    if not urls:
        yield Outcome(f"没有找到链接：{text}", ok=False)
    for url in urls:
        yield from _capture_url(url, repo, adapters)


def changed_sources(outcomes: Iterable[Outcome]) -> list[Source]:
    """结果中改动了的来源；同一来源（出现多次、或先采集后转写）只算一次，取最后的状态。"""
    by_directory = {o.changed.directory: o.changed for o in outcomes if o.changed is not None}
    return list(by_directory.values())


def commit_changes(
    vault: Path,
    outcomes: list[Outcome],
    *,
    command: str = "capture",
    verb: str = "采集",
    also: Sequence[str] = (),
) -> None:
    """把本次对原始材料的改动单独提交一次 git，不卷入 Wiki 与用户的其他改动。

    also 是随之一起提交的其他文件（如待筛清单），知识库内相对路径；没有变化的不提交。
    """
    changed = changed_sources(outcomes)
    if not changed:
        return
    lines = [label(source) for source in changed]
    if len(lines) == 1:
        message = f"{command}: {lines[0]}"
    else:
        listing = "\n".join(f"- {line}" for line in lines)
        message = f"{command}: {verb} {len(lines)} 个来源\n\n{listing}"
    paths = [source.directory.relative_to(vault).as_posix() for source in changed]
    paths += [path for path in also if (vault / path).exists()]
    git.commit_paths(vault, paths, message)


def _capture_url(
    url: str, repo: SourceRepository, adapters: Mapping[str, PlatformAdapter]
) -> Iterator[Outcome]:
    try:
        source, created = register(url, repo, adapters)
    except (UnsupportedLink, AdapterError) as error:
        yield Outcome(str(error), ok=False)
        return
    if source.status is not Status.APPROVED:
        # 只有停在「已通过」的来源（上次采集失败）才重试
        yield already_registered(source)
        return
    try:
        outcome = collect(source, repo, adapters[source.ref.platform])
    except MissingTool:
        if created:  # 整批中止前交出新登记的来源，让它的存根随本次提交落盘
            yield Outcome(f"新来源：{source.ref.display}", ok=True, changed=source, new=True)
        raise
    yield outcome


def label(source: Source) -> str:
    """「平台 平台内ID 标题」：git 提交说明与汇总中指称一个来源。"""
    return " ".join(filter(None, [source.ref.display, source.meta["标题"]]))


def already_registered(source: Source) -> Outcome:
    return Outcome(f"来源已存在：{source.title}（{source.ref.display}），{source.status}", ok=True)


def register(
    url: str, repo: SourceRepository, adapters: Mapping[str, PlatformAdapter]
) -> tuple[Source, bool]:
    """把链接登记为推送来的来源（直接为「已通过」），返回来源及它是否为本次新建。

    同一来源无论以哪种链接写法提交都只登记一份。拉取来、还在「待筛」的来源被推送时视为
    用户已选中，转为「已通过」。链接无法识别时抛 UnsupportedLink，短链解析失败时抛
    AdapterError（FetchFailed 可重试）。
    """
    ref = normalize(url, lambda platform, short: adapters[platform].expand_short_link(short))
    source = repo.find(ref)
    if source is not None:
        if source.status is Status.PENDING:
            repo.transition(source, Status.APPROVED)
        return source, False
    kind = _PLATFORM_KINDS.get(ref.platform, Kind.VIDEO)
    return repo.create(ref, kind=kind, origin=Origin.PUSH, status=Status.APPROVED), True


def collect(source: Source, repo: SourceRepository, adapter: PlatformAdapter) -> Outcome:
    """采集一条「已通过」的来源：失效转为「已失效」，可重试的失败记下原因、保持原状态。"""
    ref = source.ref
    try:
        fetched = adapter.fetch(ref)
    except SourceUnavailable as error:
        repo.transition(source, Status.UNAVAILABLE, reason=str(error))
        return Outcome(f"来源已失效：{ref.display}，{error}", ok=True, changed=source)
    except (FetchFailed, CredentialError) as error:
        repo.record_failure(source, str(error))
        return Outcome(
            f"采集失败：{ref.display}，{error}（来源保持「{source.status}」，可重试）",
            ok=False,
            changed=source,
        )
    _store(repo, source, fetched)
    return Outcome(f"已采集：{source.title}（{ref.display}），{source.status}", ok=True, changed=source)


def _store(repo: SourceRepository, source: Source, fetched: FetchedSource) -> None:
    if fetched.cover is not None:
        repo.add_file(source, fetched.cover.name, fetched.cover.data)
    if fetched.article is not None:
        for image in fetched.article.images:
            repo.add_file(source, image.name, image.data)
        body = _render_body(source, fetched, fetched.article)
        repo.add_file(source, BODY_FILE, body.encode("utf-8"))
    repo.record_details(
        source,
        kind=Kind(fetched.kind),
        title=fetched.title,
        author=fetched.author,
        byline=fetched.byline,
        published=fetched.published,
        duration=fetched.duration,
        description=fetched.description,
        cover=fetched.cover.name if fetched.cover else None,
    )
    repo.transition(source, Status.COLLECTED)
    if fetched.transcript is not None:
        rendered = transcript.render(fetched.transcript, fetched.title)
        repo.add_file(source, TRANSCRIPT_FILE, rendered.encode("utf-8"))
        repo.transition(source, Status.TRANSCRIBED)


def _render_body(source: Source, fetched: FetchedSource, article: Article) -> str:
    """正文.md：标题、出处信息行（公众号名、原文署名、发布日期、原文链接），然后是正文。"""
    name = PLATFORM_NAMES.get(source.ref.platform, source.ref.platform)
    credits = [f"{name}：{fetched.author}" if fetched.author else name]
    if fetched.byline and fetched.byline != fetched.author:
        credits.append(f"作者：{fetched.byline}")
    if fetched.published:
        credits.append(f"发布时间：{fetched.published[:10]}")
    credits.append(f"[原文]({source.ref.url})")
    return f"# {fetched.title}\n\n> {' · '.join(credits)}\n\n{article.markdown.strip()}\n"
