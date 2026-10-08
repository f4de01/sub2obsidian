"""同步编排（sync）：读取收件箱 → 拉取收藏（回填）→ 采集已通过的来源 → 转写。

每一步对每条来源产出一个结果，单条失败不中断整批；失败的来源保持原状态，下次 sync 重试。
拉取来的新来源为「待筛」，拉取之后随即更新待筛清单。原始材料的 git 提交与汇总由命令行完成。

收件箱读到的位置（游标）保存在用户配置目录 state/inbox.toml；同一文件还记着短链解析
失败、还没能登记成来源的链接，下次 sync 先重试它们。回填断点见 backfill.py。
"""

from __future__ import annotations

import tomllib
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from sub2obsidian.backfill import BACKFILL_STATE_FILE, pull
from sub2obsidian.capture import (
    Outcome,
    already_registered,
    changed_sources,
    collect,
    label,
    register,
)
from sub2obsidian.inbox import Inbox, InboxError, InboxNotConfigured
from sub2obsidian.links import UnsupportedLink, extract_urls
from sub2obsidian.platforms import AdapterError, FetchFailed, PlatformAdapter
from sub2obsidian.screening import update_list
from sub2obsidian.sources import SourceRepository, Status
from sub2obsidian.transcription import Transcriber, transcribe_collected
from sub2obsidian.vault import SCREENING_LIST

INBOX_STATE_FILE = "inbox.toml"


@dataclass
class InboxState:
    cursor: str | None = None  # 收件箱读到的位置
    retry: list[str] = field(default_factory=list)  # 待重试的链接

    @classmethod
    def load(cls, path: Path) -> InboxState:
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(cursor=data.get("cursor"), retry=list(data.get("retry", [])))

    def save(self, path: Path) -> None:
        """先写临时文件再替换，避免写坏。"""
        data: dict[str, object] = {"retry": self.retry}
        if self.cursor is not None:
            data["cursor"] = self.cursor
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".toml.tmp")
        temporary.write_text(tomli_w.dumps(data), encoding="utf-8", newline="\n")
        temporary.replace(path)


def sync(
    vault: Path,
    inbox: Inbox,
    adapters: Mapping[str, PlatformAdapter],
    transcriber: Transcriber,
    terms: Sequence[str],
    state_dir: Path,
    batch_size: int,
) -> Iterator[Outcome]:
    """逐步产出结果。缺少 ffmpeg 等本机工具时抛 MissingTool，之前产出的结果仍然有效。

    state_dir 是用户配置目录中保存收件箱游标与回填断点的目录。
    """
    repo = SourceRepository(vault)
    yield from _read_inbox(repo, inbox, adapters, state_dir / INBOX_STATE_FILE)
    try:
        yield from pull(repo, adapters, batch_size, state_dir / BACKFILL_STATE_FILE)
    finally:
        # 之后的步骤不再改动待筛的来源：此时就更新清单，拉取被中断或后面整批中止都不耽误筛选
        update_list(vault)
    for source in repo.in_status(Status.APPROVED):
        yield collect(source, repo, adapters[source.ref.platform])
    yield from transcribe_collected(vault, adapters, transcriber, terms)


def _read_inbox(
    repo: SourceRepository,
    inbox: Inbox,
    adapters: Mapping[str, PlatformAdapter],
    state_file: Path,
) -> Iterator[Outcome]:
    """把收件箱里的新链接登记为推送来的来源（已通过），然后记下读到的位置。"""
    state = InboxState.load(state_file)
    cursor, messages = state.cursor, []
    try:
        batch = inbox.read(state.cursor)
    except InboxNotConfigured as error:
        yield Outcome(f"跳过收件箱：{error}", ok=True)
    except InboxError as error:
        yield Outcome(f"读取收件箱失败：{error}", ok=False)
    else:
        cursor, messages = batch.cursor, batch.messages
        yield Outcome(f"收件箱：新消息 {len(messages)} 条", ok=True)
    links = list(state.retry)
    for text in messages:
        if found := extract_urls(text):
            links.extend(found)
        else:
            yield Outcome(f"消息里没有链接，跳过：{text}", ok=True)
    retry: list[str] = []
    for url in dict.fromkeys(links):
        try:
            source, created = register(url, repo, adapters)
        except FetchFailed as error:
            retry.append(url)
            yield Outcome(f"{error}（下次 sync 重试）", ok=False)
            continue
        except (UnsupportedLink, AdapterError) as error:
            yield Outcome(str(error), ok=False)
            continue
        if created:
            yield Outcome(f"新来源：{source.ref.display}", ok=True, changed=source, new=True)
        elif source.status is not Status.APPROVED:
            yield already_registered(source)
    # 消息已全部变成来源（或待重试的链接）之后才前移游标：中途出错时下次重读，登记去重
    InboxState(cursor, retry).save(state_file)


def summarize(outcomes: Sequence[Outcome]) -> str:
    """汇总：新增、各状态的数量，失效与失败的来源及原因。"""
    final = changed_sources(outcomes)
    counts = Counter(source.status for source in final)
    failures = [o.message for o in outcomes if not o.ok]
    parts = [f"新增来源 {sum(o.new for o in outcomes)}"]
    parts += [
        f"{status} {counts[status]}"
        for status in (Status.PENDING, Status.COLLECTED, Status.TRANSCRIBED)
    ]
    parts += [f"已失效 {counts[Status.UNAVAILABLE]}", f"失败 {len(failures)}"]
    lines = [f"sync 汇总：{'，'.join(parts)}"]
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
