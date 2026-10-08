"""一批来源的处理结果：逐条结果、命令结束时的汇总，以及把原始材料的改动单独提交 git。

capture、sync、screen、transcribe 每处理完一条来源产出一个结果（Outcome），命令行据此逐条
输出、提交与汇总。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sub2obsidian import git
from sub2obsidian.sources import Source, Status
from sub2obsidian.vault import SCREENING_LIST


@dataclass(frozen=True)
class Outcome:
    message: str
    ok: bool
    changed: Source | None = None  # 本次改动了的来源
    new: bool = False  # 本次新登记了来源


def changed_sources(outcomes: Iterable[Outcome]) -> list[Source]:
    """结果中改动了的来源；同一来源（出现多次、或先采集后转写）只算一次，取最后的状态。"""
    by_directory = {o.changed.directory: o.changed for o in outcomes if o.changed is not None}
    return list(by_directory.values())


def commit_changes(
    vault: Path,
    outcomes: list[Outcome],
    *,
    command: str,
    verb: str,
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


def label(source: Source) -> str:
    """「平台 平台内ID 标题」：git 提交说明与汇总中指称一个来源。"""
    return " ".join(filter(None, [source.ref.display, source.meta["标题"]]))


def summarize(outcomes: Sequence[Outcome], command: str) -> str:
    """命令结束时的汇总：新增、各状态的数量，失效与失败的来源及原因。"""
    final = changed_sources(outcomes)
    counts = Counter(source.status for source in final)
    failures = [o.message for o in outcomes if not o.ok]
    parts = [f"新增来源 {sum(o.new for o in outcomes)}"]
    parts += [
        f"{status} {counts[status]}"
        for status in (Status.PENDING, Status.COLLECTED, Status.TRANSCRIBED)
    ]
    parts += [f"已失效 {counts[Status.UNAVAILABLE]}", f"失败 {len(failures)}"]
    lines = [f"{command} 汇总：{'，'.join(parts)}"]
    unavailable = [s for s in final if s.status is Status.UNAVAILABLE]
    if unavailable:
        lines.append("已失效：")
        lines += [f"  - {label(s)}：{s.meta['失败原因']}" for s in unavailable]
    if failures:
        lines.append("失败及原因：")
        lines += [f"  - {message}" for message in failures]
    if counts[Status.PENDING]:
        lines.append(f"新的待筛来源已列入 {SCREENING_LIST}：勾选后执行 sub2obsidian screen")
    return "\n".join(lines)
