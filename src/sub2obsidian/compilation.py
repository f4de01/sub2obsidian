"""编译的 CLI 配套：来源状态汇总，以及编译结束时把来源转为「已编译」。

编译本身由 agent 按知识库中的 Schema 执行（ADR-0001），这里只提供它读写来源状态的入口。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sub2obsidian.links import SourceRef, UnsupportedLink, extract_urls, normalize
from sub2obsidian.sources import METADATA_FILE, Source, SourceRepository, Status


@dataclass(frozen=True)
class StatusReport:
    counts: dict[Status, int]  # 每个来源状态都有，含 0
    compilable: list[Source]


def status_report(vault: Path) -> StatusReport:
    sources = SourceRepository(vault).all()
    tally = Counter(source.status for source in sources)
    return StatusReport(
        counts={status: tally[status] for status in Status},
        compilable=[source for source in sources if source.is_compilable],
    )


def _no_expansion(platform: str, url: str) -> str:
    raise UnsupportedLink(f"短链需要联网解析，请改用「平台/平台内ID」：{url}")


def _ref_of(name: str) -> SourceRef | None:
    """来源的指称 → 来源身份。

    接受「平台/平台内ID」、原始材料中的来源目录（或其中的元数据.md）以及来源的链接。
    """
    if extract_urls(name) == [name]:
        try:
            return normalize(name, _no_expansion)
        except UnsupportedLink:
            return None
    parts = [part for part in name.replace("\\", "/").split("/") if part]
    if parts and parts[-1] == METADATA_FILE:
        parts.pop()
    if len(parts) < 2 or {".", ".."} & set(parts[-2:]):
        return None
    # 来源目录只由「平台 + 平台内 ID」决定，查找时用不到规范链接
    return SourceRef(parts[-2], parts[-1], "")


class Refused(ValueError):
    """mark-compiled 拒绝执行：列出每个被拒绝的来源及原因，此时没有任何来源被改动。"""


def mark_compiled(vault: Path, names: Sequence[str]) -> list[Source]:
    """把 names 指称的来源全部转为「已编译」。

    只要有一个来源找不到或不可编译，就全部拒绝、一个都不改，免得编译提交只记下半批。
    """
    repo = SourceRepository(vault)
    sources: dict[Path, Source] = {}  # 同一来源被指称多次时只算一次
    problems: list[str] = []
    for name in names:
        ref = _ref_of(name)
        source = repo.find(ref) if ref is not None else None
        if source is None:
            problems.append(f"找不到来源：{name}")
        elif not source.is_compilable:
            problems.append(
                f"{source.ref.key}（{source.title}）不可编译：{source.kind}处于「{source.status}」"
            )
        else:
            sources.setdefault(source.directory, source)
    if problems:
        raise Refused("\n".join(problems))
    for source in sources.values():
        repo.transition(source, Status.COMPILED)
    return list(sources.values())
