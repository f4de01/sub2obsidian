"""拉取与回填：分批读取用户在平台上的收藏，只抓元数据，新来源以「待筛」登记。

每次 sync 每个平台最多登记一批（批量大小可配置）新来源。每个收藏列表读到哪一页（断点）
保存在用户配置目录 state/backfill.toml，读完一页记一次：被中断（风控、网络、Ctrl+C）或
一批满了之后，下次 sync 从断点接着读；重读到的收藏已经登记过，不会重复创建来源。

一个收藏列表读到末尾即回填完成，此后每次 sync 只从列表开头往后读，直到遇到一整页都已
登记过的收藏为止（收藏按时间从新到旧排列），以发现新收藏。已拒绝与已失效的来源仍留着
存根，所以不会被重新登记、也不会再进入待筛清单。

抖音的存量只回填一次，日常增量由用户推送（抖音风控严、签名每隔几个月失效，不宜每次 sync
都去请求）：它的全部收藏列表读完后，此后的 sync 不再拉取抖音收藏。

一个平台拉取失败（登录失效、风控、签名失效、缺少 F2 等本机工具）只结束这个平台本次的拉取，
不影响其他平台与 sync 的其余步骤。
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from sub2obsidian.batch import Outcome, label
from sub2obsidian.credentials import CredentialError
from sub2obsidian.files import write_text_atomically
from sub2obsidian.links import PLATFORM_NAMES
from sub2obsidian.platforms import AdapterError, Favorite, FavoritesAdapter, PlatformAdapter
from sub2obsidian.sources import Kind, Origin, Source, SourceRepository, Status
from sub2obsidian.tools import MissingTool

BACKFILL_STATE_FILE = "backfill.toml"
# backfill.toml 顶层记录回填已完成的平台的键；其余顶层键都是平台名
_COMPLETE = "complete"

# 只回填存量、不拉取增量的平台
ONE_TIME_PLATFORMS = {"douyin"}


@dataclass
class ListProgress:
    """一个收藏列表的回填进度。"""

    cursor: str | None = None  # 下次从这一页接着读；None 为第一页
    done: bool = False  # 已读到列表末尾，此后只从开头查找新收藏


@dataclass
class BackfillState:
    lists: dict[str, dict[str, ListProgress]] = field(default_factory=dict)  # 平台 → 列表 → 进度
    complete: set[str] = field(default_factory=set)  # 回填已经完成、不再拉取的平台

    @classmethod
    def load(cls, path: Path) -> BackfillState:
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        complete = set(data.pop(_COMPLETE, []))
        lists = {
            platform: {
                list_id: ListProgress(entry.get("cursor"), bool(entry.get("done", False)))
                for list_id, entry in platform_lists.items()
            }
            for platform, platform_lists in data.items()
        }
        return cls(lists, complete)

    def save(self, path: Path) -> None:
        data: dict[str, object] = {_COMPLETE: sorted(self.complete)} if self.complete else {}
        data |= {
            platform: {
                list_id: {"done": progress.done}
                | ({"cursor": progress.cursor} if progress.cursor is not None else {})
                for list_id, progress in lists.items()
            }
            for platform, lists in self.lists.items()
        }
        write_text_atomically(path, tomli_w.dumps(data))


def pull(
    repo: SourceRepository,
    adapters: Mapping[str, PlatformAdapter],
    batch_size: int,
    state_file: Path,
) -> Iterator[Outcome]:
    """拉取每个支持拉取的平台；一个平台失败不影响其他平台。"""
    for platform, adapter in adapters.items():
        if isinstance(adapter, FavoritesAdapter):
            yield from _pull_platform(repo, adapter, platform, batch_size, state_file)


def _pull_platform(
    repo: SourceRepository,
    adapter: FavoritesAdapter,
    platform: str,
    batch_size: int,
    state_file: Path,
) -> Iterator[Outcome]:
    name = PLATFORM_NAMES.get(platform, platform)
    state = BackfillState.load(state_file)
    if platform in state.complete:
        return
    progress = state.lists.setdefault(platform, {})
    remaining = batch_size
    try:
        for favorite_list in adapter.favorite_lists():
            entry = progress.setdefault(favorite_list.id, ListProgress())
            cursor = None if entry.done else entry.cursor
            while True:
                if remaining == 0:
                    yield _batch_full(name, batch_size)
                    return
                page = adapter.favorites(favorite_list.id, cursor)
                all_known = True
                for item in page.items:
                    if repo.find(item.ref) is not None:
                        continue
                    all_known = False
                    if remaining == 0:
                        yield _batch_full(name, batch_size)
                        return
                    remaining -= 1
                    yield _register(repo, item)
                if page.next is None or (entry.done and all_known):
                    break
                cursor = page.next
                if not entry.done:
                    entry.cursor = cursor
                    state.save(state_file)
            if not entry.done:
                entry.done, entry.cursor = True, None
                state.save(state_file)
    except (AdapterError, CredentialError, MissingTool) as error:
        yield Outcome(f"拉取 {name} 收藏失败：{error}（下次 sync 从断点继续）", ok=False)
        return
    if platform in ONE_TIME_PLATFORMS:
        state.complete.add(platform)
        state.save(state_file)
        yield Outcome(f"{name} 回填完成：此后 sync 不再拉取{name}收藏，新收藏请分享到收件箱", ok=True)


def _batch_full(name: str, batch_size: int) -> Outcome:
    return Outcome(f"{name} 回填：本次已登记一批（{batch_size} 条），其余下次 sync 继续", ok=True)


def _register(repo: SourceRepository, item: Favorite) -> Outcome:
    """把一条收藏登记为拉取来的来源（待筛），写入列表里带的元数据。"""
    kind = Kind(item.kind)
    source = repo.create(item.ref, kind=kind, origin=Origin.PULL, status=Status.PENDING)
    repo.record_details(
        source,
        kind=kind,
        title=item.title,
        author=item.author,
        byline=None,
        published=item.published,
        duration=item.duration,
        description=item.description,
        cover=None,
        part=item.part,
    )
    if item.unavailable is not None:
        repo.transition(source, Status.UNAVAILABLE, reason=item.unavailable)
        return _outcome(f"来源已失效：{label(source)}，{item.unavailable}", source)
    return _outcome(f"待筛：{label(source)}", source)


def _outcome(message: str, source: Source) -> Outcome:
    return Outcome(message, ok=True, changed=source, new=True)
