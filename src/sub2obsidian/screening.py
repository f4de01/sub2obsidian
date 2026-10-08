"""筛选：待筛清单笔记的生成与读取。

待筛清单（知识库根目录的 待筛清单.md）列出全部「待筛」的来源，每条一个勾选框，附元数据
与「建议：」栏。建议栏由 agent 按 Schema 填写（CLI 不调用 LLM，ADR-0001），勾选由用户在
Obsidian 中完成；重新生成清单时保留已有的勾选与建议。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sub2obsidian.batch import Outcome, changed_sources, label
from sub2obsidian.files import write_text_atomically
from sub2obsidian.links import PLATFORM_NAMES, SourceRef, bilibili_part, bilibili_ref
from sub2obsidian.sources import Source, SourceRepository, Status
from sub2obsidian.vault import SCREENING_LIST

EXCERPT_LENGTH = 60

HEADER = """# 待筛清单

> 由 sub2obsidian 生成：列出全部「待筛」的来源，每次 sync 后更新，已有的勾选与建议会保留。
> 1. 对 agent 说「填写筛选建议」：它按 Schema 在每条的「建议：」后写上是否知识类与建议主题域。
> 2. 勾选要保留的来源，然后执行 `sub2obsidian screen`：勾选的转为「已通过」，下次 sync 时采集并转写；未勾选的转为「已拒绝」，只留存根，不会再出现在这里。
"""

# 「- [ ] 标题 `平台/平台内ID`」：清单中一条来源的开头，来源的指称是行中最后一对反引号
# （Obsidian 的任务插件可能在勾选的行末再追加完成日期等文字）
_ENTRY = re.compile(r"^\s*[-*+] \[(?P<mark>.)\] .*`(?P<key>[a-z]+/[^`\s/]+)`")
_SUGGESTION = re.compile(r"^\s+[-*+] 建议[：:][ \t]*(?P<text>.*?)\s*$")


@dataclass
class Entry:
    """清单中的一条：来源的指称、是否勾选、建议栏。"""

    key: str
    checked: bool
    suggestion: str = ""


def read_entries(vault: Path) -> list[Entry]:
    """读取清单中的条目；没有清单时为空。"""
    path = vault / SCREENING_LIST
    if not path.exists():
        return []
    entries: list[Entry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := _ENTRY.match(line):
            entries.append(Entry(match["key"], match["mark"] in "xX"))
        elif entries and (match := _SUGGESTION.match(line)):
            entries[-1].suggestion = match["text"]
    return entries


def update_list(vault: Path) -> bool:
    """按当前的待筛来源重新生成清单，保留已有的勾选与建议；返回清单是否有变化。

    从来没有过待筛来源时不生成清单。
    """
    path = vault / SCREENING_LIST
    pending = SourceRepository(vault).in_status(Status.PENDING)
    if not pending and not path.exists():
        return False
    previous = {entry.key: entry for entry in read_entries(vault)}
    text = render(pending, previous)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    write_text_atomically(path, text)
    return True


def render(pending: Iterable[Source], previous: dict[str, Entry]) -> str:
    """清单正文：按平台分组，组内按登记先后排列。"""
    by_platform: dict[str, list[Source]] = {}
    for source in sorted(pending, key=lambda s: (s.meta["采集时间"], s.ref.platform_id)):
        by_platform.setdefault(source.ref.platform, []).append(source)
    parts = [HEADER]
    if not by_platform:
        parts.append("\n暂无待筛的来源。\n")
    for platform in sorted(by_platform):
        sources = by_platform[platform]
        parts.append(f"\n## {PLATFORM_NAMES.get(platform, platform)}（{len(sources)}）\n\n")
        for group in _by_video(sources):
            if group[0].meta.get("分P"):
                parts.append(_video(group, previous))
            else:
                parts.append(_entry(group[0], previous.get(group[0].ref.key)))
    return "".join(parts)


def _by_video(sources: list[Source]) -> list[list[Source]]:
    """B站 多P视频的分P归为一组（排在其中最先登记的分P处，组内按分P序号），其余来源各自一组。"""
    groups: list[list[Source]] = []
    videos: dict[str, list[Source]] = {}
    for source in sources:
        if not source.meta.get("分P"):
            groups.append([source])
            continue
        video = bilibili_part(source.ref)[0]
        if video not in videos:
            videos[video] = []
            groups.append(videos[video])
        videos[video].append(source)
    for group in videos.values():
        group.sort(key=lambda s: s.meta["分P"])
    return groups


def _video(parts: list[Source], previous: dict[str, Entry]) -> str:
    """多P视频：一行视频信息与简介，其下每个分P一个勾选框。"""
    first = parts[0]
    video_title = first.meta.get("视频标题") or ""
    details = [
        f"多P视频：{' '.join(video_title.split())}",
        first.meta.get("作者"),
        (first.meta.get("发布时间") or "")[:10],
        f"[原视频]({bilibili_ref(bilibili_part(first.ref)[0]).url})",
    ]
    lines = ["- " + " · ".join(filter(None, details))]
    if excerpt := _excerpt(first.description):
        lines.append(f"\t- 简介：{excerpt}")
    text = "\n".join(lines) + "\n"
    for source in parts:
        title = source.title.removeprefix(video_title).strip() or f"P{source.meta['分P']}"
        text += _entry(source, previous.get(source.ref.key), title=title, indent="\t")
    return text


def _entry(
    source: Source, previous: Entry | None, *, title: str | None = None, indent: str = ""
) -> str:
    """清单中的一条来源；分P（indent 非空）只列时长与链接，作者等写在它所属的视频那一行。"""
    mark = "x" if previous and previous.checked else " "
    title = " ".join((title or source.title).split())
    details = [
        None if indent else source.meta.get("作者"),
        _duration(source.meta.get("时长")),
        None if indent else (source.meta.get("发布时间") or "")[:10],
        f"[原链接]({source.ref.url})",
    ]
    lines = [f"- [{mark}] {title} `{source.ref.key}`", "\t- " + " · ".join(filter(None, details))]
    if not indent and (excerpt := _excerpt(source.description)):
        lines.append(f"\t- 简介：{excerpt}")
    lines.append(f"\t- 建议：{previous.suggestion if previous else ''}".rstrip())
    return "".join(f"{indent}{line}\n" for line in lines)


def _duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _excerpt(description: str) -> str:
    flat = " ".join(description.split())
    return flat if len(flat) <= EXCERPT_LENGTH else flat[:EXCERPT_LENGTH] + "…"


class ScreenRefused(ValueError):
    """screen 拒绝执行：此时没有任何来源被改动。"""


def screen(vault: Path, *, reject_all: bool = False) -> list[Outcome]:
    """按清单筛选：勾选的待筛来源转为「已通过」，未勾选的转为「已拒绝」，建议栏记入元数据。

    清单里一个都没勾选时多半是还没勾选就执行了，拒绝执行（已拒绝不可撤销）；
    reject_all 为真时才全部拒绝。筛完重新生成清单。
    """
    repo = SourceRepository(vault)
    if not (vault / SCREENING_LIST).exists():
        raise ScreenRefused(f"还没有{SCREENING_LIST}：先执行 sub2obsidian sync 拉取收藏")
    entries: list[tuple[Entry, Source]] = []
    missing: list[str] = []
    for entry in read_entries(vault):
        source = repo.find(SourceRef(*entry.key.split("/", 1), ""))
        if source is None:
            missing.append(entry.key)
        elif source.status is Status.PENDING:
            entries.append((entry, source))
    if not entries:
        raise ScreenRefused(f"{SCREENING_LIST}中没有待筛的来源")
    if not reject_all and not any(entry.checked for entry, _ in entries):
        raise ScreenRefused(
            f"{SCREENING_LIST}中一个来源都没有勾选：先勾选要保留的来源；"
            "确实要全部拒绝时执行 sub2obsidian screen --reject-all"
        )
    outcomes = [Outcome(f"找不到来源：{key}（清单中的这一行被跳过）", ok=False) for key in missing]
    for entry, source in entries:
        repo.screen(source, approved=entry.checked, suggestion=entry.suggestion)
        outcomes.append(Outcome(f"{source.status}：{label(source)}", ok=True, changed=source))
    update_list(vault)
    return outcomes


def summarize(outcomes: Iterable[Outcome]) -> str:
    counts = Counter(source.status for source in changed_sources(outcomes))
    return (
        f"screen 汇总：已通过 {counts[Status.APPROVED]}，已拒绝 {counts[Status.REJECTED]}；"
        "已通过的来源在下次 sync 时采集并转写"
    )
