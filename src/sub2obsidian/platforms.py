"""平台适配器接口：每个平台一个实现，相互独立、可替换（ADR-0003）。

适配器的边界是「给定来源链接 → 产出来源的元数据与原始材料」，与具体抓取实现无关。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from sub2obsidian.links import SourceRef
from sub2obsidian.sources import Kind, VideoPart
from sub2obsidian.transcript import Transcript


class AdapterError(Exception):
    """适配器报告的、与单条来源相关的问题；消息面向用户。"""


class SourceUnavailable(AdapterError):
    """来源在平台上已删除、私密或下架：转为「已失效」，不再重试。"""


class FetchFailed(AdapterError):
    """可重试的失败（网络、风控、验证页等）：来源保持原状态，下次再试。"""


@dataclass(frozen=True)
class Asset:
    """随来源落入原始材料的一个文件（封面、图片）。"""

    name: str
    data: bytes


@dataclass(frozen=True)
class Article:
    """文章、图文的正文：Markdown 中的图片已改写为对 images 中本地文件名的引用。"""

    markdown: str
    images: list[Asset]


@dataclass(frozen=True)
class FetchedSource:
    """适配器按 ID 采集到的原始内容。"""

    kind: Kind
    title: str
    author: str | None = None  # UP主、公众号名等发布者
    byline: str | None = None  # 原文署名作者（公众号文章在公众号名之外的作者）
    published: str | None = None  # ISO 8601，带时区
    duration: int | None = None  # 秒，仅视频
    description: str = ""
    cover: Asset | None = None
    transcript: Transcript | None = None  # 平台字幕；没有时为 None，留待 ASR
    article: Article | None = None  # 文章、图文的正文
    part: VideoPart | None = None  # B站 多P视频的分P


@dataclass(frozen=True)
class Favorite:
    """拉取到的一条收藏：来源身份与筛选所需的元数据；不含任何原始材料文件。"""

    ref: SourceRef
    kind: Kind
    title: str
    author: str | None = None
    published: str | None = None  # ISO 8601，带时区
    duration: int | None = None  # 秒，仅视频
    description: str = ""
    unavailable: str | None = None  # 收藏里已显示为失效时的原因
    part: VideoPart | None = None  # B站 多P视频的分P


@dataclass(frozen=True)
class FavoriteList:
    """一个收藏列表：一个收藏夹，或稍后再看。"""

    id: str
    title: str


@dataclass(frozen=True)
class FavoritesPage:
    """收藏列表的一页，按收藏时间从新到旧；next 是下一页的游标，没有下一页时为 None。"""

    items: list[Favorite]
    next: str | None


class PlatformAdapter(Protocol):
    platform: str

    def expand_short_link(self, url: str) -> str:
        """把本平台的短链（如 b23.tv）解析为它跳转到的完整链接。"""
        ...

    def fetch(self, ref: SourceRef) -> FetchedSource:
        """采集一条来源；不可用时抛 SourceUnavailable，可重试的失败抛 FetchFailed。"""
        ...

    def download_audio(self, ref: SourceRef, directory: Path) -> Path:
        """把视频来源的音频下载到 directory（临时目录，由调用方转写后删除），返回音频文件。

        不可用时抛 SourceUnavailable，可重试的失败抛 FetchFailed，缺少 ffmpeg 等本机工具时抛
        MissingTool。
        """
        ...


@runtime_checkable
class MultiPartAdapter(PlatformAdapter, Protocol):
    """视频可以有多个分P的平台（B站）的适配器：每个分P是一条来源。"""

    def count_parts(self, video_id: str) -> int:
        """视频有几个分P；视频已删除或不可见时为 1（由采集判定已失效），可重试的失败抛 FetchFailed。"""
        ...


@runtime_checkable
class FavoritesAdapter(PlatformAdapter, Protocol):
    """支持拉取的平台适配器：列出用户的收藏，只取元数据。

    请求之间的随机间隔由适配器自己负责。未登录或登录失效时抛 LoginRequired，
    可重试的失败（网络、风控）抛 FetchFailed。
    """

    def favorite_lists(self) -> list[FavoriteList]:
        """用户的全部收藏列表。"""
        ...

    def favorites(self, list_id: str, cursor: str | None) -> FavoritesPage:
        """收藏列表的一页；cursor 为 None 时取第一页。"""
        ...
