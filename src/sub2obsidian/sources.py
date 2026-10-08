"""来源仓储：原始材料区的唯一写入口。

每个来源一个目录 `原始材料/<平台>/<平台内ID>/`，其中：

    元数据.md    带 frontmatter 的元数据（含来源状态）；正文为标题、封面与简介
    封面.jpg     等原始材料文件：只增不改

元数据中的来源状态按状态机转换，非法转换被拒绝；已拒绝与已失效的来源只留元数据存根。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from sub2obsidian.links import SourceRef
from sub2obsidian.vault import RAW_DIR

METADATA_FILE = "元数据.md"


class Status(StrEnum):
    PENDING = "待筛"
    REJECTED = "已拒绝"
    APPROVED = "已通过"
    COLLECTED = "已采集"
    TRANSCRIBED = "已转写"
    COMPILED = "已编译"
    UNAVAILABLE = "已失效"


class Kind(StrEnum):
    VIDEO = "视频"
    ARTICLE = "文章"
    POST = "图文"


class Origin(StrEnum):
    PULL = "拉取"
    PUSH = "推送"


class IllegalTransition(ValueError):
    pass


class RawMaterialExists(FileExistsError):
    """原始材料只增不改：同名文件已存在时拒绝写入。"""


def compilable(kind: Kind, status: Status) -> bool:
    """可编译 = 文章或图文已采集，或视频已转写。"""
    if kind is Kind.VIDEO:
        return status is Status.TRANSCRIBED
    return status is Status.COLLECTED


def transition_allowed(kind: Kind, old: Status, new: Status) -> bool:
    if new is Status.UNAVAILABLE:
        return old is not Status.UNAVAILABLE
    if new is Status.COMPILED:
        return compilable(kind, old)
    if new is Status.TRANSCRIBED:
        return kind is Kind.VIDEO and old is Status.COLLECTED
    return (old, new) in {
        (Status.PENDING, Status.APPROVED),
        (Status.PENDING, Status.REJECTED),
        (Status.APPROVED, Status.COLLECTED),
    }


# frontmatter 字段及其顺序
FIELDS = [
    "平台",
    "平台内ID",
    "规范链接",
    "类型",
    "标题",
    "作者",
    "发布时间",
    "时长",
    "采集途径",
    "采集时间",
    "来源状态",
    "筛选建议",
    "失败原因",
]


@dataclass
class Source:
    directory: Path
    meta: dict[str, Any]
    body: str

    @property
    def ref(self) -> SourceRef:
        return SourceRef(self.meta["平台"], self.meta["平台内ID"], self.meta["规范链接"])

    @property
    def status(self) -> Status:
        return Status(self.meta["来源状态"])

    @property
    def kind(self) -> Kind:
        return Kind(self.meta["类型"])

    @property
    def title(self) -> str:
        return self.meta.get("标题") or self.ref.display


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _body(title: str, description: str, cover: str | None) -> str:
    parts = [f"# {title}\n"]
    if cover:
        parts.append(f"\n![封面]({cover})\n")
    if description.strip():
        parts.append(f"\n## 简介\n\n{description.strip()}\n")
    return "".join(parts)


class SourceRepository:
    def __init__(self, vault: Path) -> None:
        self.raw = vault / RAW_DIR

    def directory(self, ref: SourceRef) -> Path:
        return self.raw / ref.platform / ref.platform_id

    def find(self, ref: SourceRef) -> Source | None:
        metadata = self.directory(ref) / METADATA_FILE
        if not metadata.is_file():
            return None
        text = metadata.read_text(encoding="utf-8")
        frontmatter, _, body = text.removeprefix("---\n").partition("\n---\n")
        return Source(self.directory(ref), yaml.safe_load(frontmatter), body.lstrip("\n"))

    def create(self, ref: SourceRef, *, kind: Kind, origin: Origin, status: Status) -> Source:
        """新建来源的元数据存根；拉取来的为待筛，推送来的直接为已通过。"""
        if status not in (Status.PENDING, Status.APPROVED):
            raise IllegalTransition(f"新来源只能是「待筛」或「已通过」，不能是「{status}」")
        if self.find(ref) is not None:
            raise RawMaterialExists(f"来源已存在：{ref.display}")
        meta: dict[str, Any] = dict.fromkeys(FIELDS)
        meta.update(
            {
                "平台": ref.platform,
                "平台内ID": ref.platform_id,
                "规范链接": ref.url,
                "类型": str(kind),
                "采集途径": str(origin),
                "采集时间": _now(),
                "来源状态": str(status),
            }
        )
        source = Source(self.directory(ref), meta, _body(ref.display, "", None))
        self._save(source)
        return source

    def record_details(
        self,
        source: Source,
        *,
        title: str,
        author: str | None,
        published: str | None,
        duration: int | None,
        description: str,
        cover: str | None,
    ) -> None:
        """写入采集到的元数据；封面为来源目录中的文件名。"""
        source.meta.update({"标题": title, "作者": author, "发布时间": published, "时长": duration})
        source.body = _body(title, description, cover)
        self._save(source)

    def add_file(self, source: Source, name: str, data: bytes) -> None:
        """写入一份原始材料文件；只增不改（重试时内容相同的文件视为已写入）。"""
        target = source.directory / name
        if target.exists():
            if target.read_bytes() == data:
                return
            raise RawMaterialExists(f"原始材料只增不改，已存在：{target}")
        target.write_bytes(data)

    def transition(self, source: Source, new: Status, *, reason: str | None = None) -> None:
        if not transition_allowed(source.kind, source.status, new):
            raise IllegalTransition(
                f"{source.ref.display}：不能从「{source.status}」转为「{new}」"
            )
        source.meta["来源状态"] = str(new)
        source.meta["失败原因"] = reason
        self._save(source)

    def record_failure(self, source: Source, reason: str) -> None:
        """记录可重试的失败原因；来源保持原状态。"""
        source.meta["失败原因"] = reason
        self._save(source)

    def _save(self, source: Source) -> None:
        source.directory.mkdir(parents=True, exist_ok=True)
        frontmatter = yaml.safe_dump(source.meta, allow_unicode=True, sort_keys=False)
        target = source.directory / METADATA_FILE
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(f"---\n{frontmatter}---\n\n{source.body}", encoding="utf-8", newline="\n")
        temporary.replace(target)
