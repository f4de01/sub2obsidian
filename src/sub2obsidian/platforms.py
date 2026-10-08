"""平台适配器接口：每个平台一个实现，相互独立、可替换（ADR-0003）。

适配器的边界是「给定来源链接 → 产出来源的元数据与原始材料」，与具体抓取实现无关。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sub2obsidian.links import SourceRef
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

    kind: str  # 视频 / 文章 / 图文
    title: str
    author: str | None = None  # UP主、公众号名等发布者
    byline: str | None = None  # 原文署名作者（公众号文章在公众号名之外的作者）
    published: str | None = None  # ISO 8601，带时区
    duration: int | None = None  # 秒，仅视频
    description: str = ""
    cover: Asset | None = None
    transcript: Transcript | None = None  # 平台字幕；没有时为 None，留待 ASR
    article: Article | None = None  # 文章、图文的正文


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
