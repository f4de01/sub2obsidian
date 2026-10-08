"""推送采集：把分享文本中的每个链接作为推送来的来源采集进原始材料。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sub2obsidian import git, transcript
from sub2obsidian.credentials import CredentialError
from sub2obsidian.links import UnsupportedLink, extract_urls, normalize
from sub2obsidian.platforms import FetchedSource, FetchFailed, PlatformAdapter, SourceUnavailable
from sub2obsidian.sources import Kind, Origin, Source, SourceRepository, Status

TRANSCRIPT_FILE = "口播稿.md"


@dataclass(frozen=True)
class Outcome:
    message: str
    ok: bool
    changed: Source | None = None  # 本次改动了的来源


def capture_text(text: str, vault: Path, adapters: Mapping[str, PlatformAdapter]) -> list[Outcome]:
    repo = SourceRepository(vault)
    urls = extract_urls(text)
    if not urls:
        return [Outcome(f"没有找到链接：{text}", ok=False)]
    return [_capture_url(url, repo, adapters) for url in urls]


def commit_changes(vault: Path, outcomes: list[Outcome]) -> None:
    """把本次对原始材料的改动单独提交一次 git，不卷入 Wiki 与用户的其他改动。"""
    # 同一来源在一段文本里出现多次时只算一次
    by_directory = {o.changed.directory: o.changed for o in outcomes if o.changed is not None}
    changed = list(by_directory.values())
    if not changed:
        return
    lines = [" ".join(filter(None, [s.ref.display, s.meta["标题"]])) for s in changed]
    if len(lines) == 1:
        message = f"capture: {lines[0]}"
    else:
        listing = "\n".join(f"- {line}" for line in lines)
        message = f"capture: 采集 {len(lines)} 个来源\n\n{listing}"
    paths = [source.directory.relative_to(vault).as_posix() for source in changed]
    git.commit_paths(vault, paths, message)


def _capture_url(
    url: str, repo: SourceRepository, adapters: Mapping[str, PlatformAdapter]
) -> Outcome:
    try:
        ref = normalize(url, lambda platform, short: adapters[platform].expand_short_link(short))
    except (UnsupportedLink, FetchFailed) as error:
        return Outcome(str(error), ok=False)
    adapter = adapters[ref.platform]
    source = repo.find(ref)
    if source is None:
        source = repo.create(ref, kind=Kind.VIDEO, origin=Origin.PUSH, status=Status.APPROVED)
    elif source.status is not Status.APPROVED:
        # 只有停在「已通过」的来源（上次采集失败）才重试
        return Outcome(f"来源已存在：{source.title}（{ref.display}），{source.status}", ok=True)
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
    repo.record_details(
        source,
        title=fetched.title,
        author=fetched.author,
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
