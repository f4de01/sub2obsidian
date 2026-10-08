"""推送采集：把分享文本中的每个链接作为推送来的来源采集进原始材料。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path

from sub2obsidian import transcript
from sub2obsidian.batch import Outcome
from sub2obsidian.credentials import CredentialError
from sub2obsidian.links import PLATFORM_NAMES, SourceRef, UnsupportedLink, extract_urls, normalize
from sub2obsidian.platforms import (
    AdapterError,
    Article,
    FetchedSource,
    FetchFailed,
    MultiPartAdapter,
    PlatformAdapter,
    SourceUnavailable,
)
from sub2obsidian.sources import (
    Kind,
    Origin,
    RawMaterialExists,
    Source,
    SourceRepository,
    Status,
)
from sub2obsidian.tools import MissingTool

TRANSCRIPT_FILE = "口播稿.md"
BODY_FILE = "正文.md"

# 采集前（含失效存根）按平台假定的来源类型；采集后以适配器给出的类型为准
_PLATFORM_KINDS = {"wechat": Kind.ARTICLE}


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


def _capture_url(
    url: str, repo: SourceRepository, adapters: Mapping[str, PlatformAdapter]
) -> Iterator[Outcome]:
    try:
        registered = register(url, repo, adapters)
    except (UnsupportedLink, AdapterError) as error:
        yield Outcome(str(error), ok=False)
        return
    for source, created in registered:
        yield from _capture_source(source, created, repo, adapters)


def _capture_source(
    source: Source, created: bool, repo: SourceRepository, adapters: Mapping[str, PlatformAdapter]
) -> Iterator[Outcome]:
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
    yield replace(outcome, new=created)


def already_registered(source: Source) -> Outcome:
    return Outcome(f"来源已存在：{source.title}（{source.ref.display}），{source.status}", ok=True)


def register(
    url: str, repo: SourceRepository, adapters: Mapping[str, PlatformAdapter]
) -> list[tuple[Source, bool]]:
    """把链接指向的来源登记为推送来的来源（直接为「已通过」），返回每个来源及它是否为本次新建。

    B站 多P视频的链接不带 p 时指向全部分P。同一来源无论以哪种链接写法提交都只登记一份。
    拉取来、还在「待筛」的来源被推送时视为用户已选中，转为「已通过」。链接无法识别时抛
    UnsupportedLink，短链或分P数查不到时抛 AdapterError（FetchFailed 可重试）。
    """
    refs = normalize(
        url,
        lambda platform, short: adapters[platform].expand_short_link(short),
        lambda platform, video: _count_parts(adapters[platform], video),
    )
    return [_register_ref(ref, repo) for ref in refs]


def _count_parts(adapter: PlatformAdapter, video: str) -> int:
    return adapter.count_parts(video) if isinstance(adapter, MultiPartAdapter) else 1


def _register_ref(ref: SourceRef, repo: SourceRepository) -> tuple[Source, bool]:
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
        _store(repo, source, fetched)
    except SourceUnavailable as error:
        repo.transition(source, Status.UNAVAILABLE, reason=str(error))
        return Outcome(f"来源已失效：{ref.display}，{error}", ok=True, changed=source)
    except (FetchFailed, CredentialError, RawMaterialExists) as error:
        # RawMaterialExists：上次采集中断，留下了内容不同的同名文件（只增不改，不覆盖）
        repo.record_failure(source, str(error))
        return Outcome(
            f"采集失败：{ref.display}，{error}（来源保持「{source.status}」，可重试）",
            ok=False,
            changed=source,
        )
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
        part=fetched.part,
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
