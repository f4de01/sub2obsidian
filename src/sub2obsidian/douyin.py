"""抖音平台适配器：基于 F2 采集作品详情（需登录 cookie 与接口签名），视频下载后供 ASR 转写，
图文按文章处理（文字与全部图片）。抖音没有平台字幕。

网络层（DouyinClient）与解析分开：契约测试用 F2 返回结构的样本回放网络层，验证解析与判定。
F2 失效（抖音每隔几个月更换签名）时优先升级 F2；持续失败时评估改用 TikHub（ADR-0003）。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from sub2obsidian.credentials import CredentialProvider
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import Article, Asset, FetchedSource, FetchFailed, SourceUnavailable
from sub2obsidian.tools import MissingTool, require_ffmpeg

PLATFORM = "douyin"
BEIJING = dt.timezone(dt.timedelta(hours=8))
NOTE_AWEME_TYPE = 68  # 图文

F2_COMMIT = "f6be8c0ffba9a127075bbeafe4838716650b6325"
F2_HINT = (
    "未安装 F2：抖音采集需要 F2（从 git 安装）。请在本仓库根目录执行 "
    f'uv tool install --force --with "f2 @ git+https://github.com/Johnserf-Seed/f2@{F2_COMMIT}" .'
)
EMPTY_RESPONSE = (
    "抖音接口返回空响应：登录 cookie 或接口签名已失效。请先重新登录 抖音：sub2obsidian login douyin；"
    "重新登录后仍然失败，说明 F2 的签名算法可能已失效，请升级 F2（见 README），"
    "持续失败时考虑改用 TikHub（ADR-0003）"
)
ADAPTER_BROKEN = (
    "抖音接口没有返回作品，也没有说明原因：适配器可能已失效（抖音改版或 F2 过时），"
    "请升级 F2（见 README），持续失败时考虑改用 TikHub（ADR-0003）"
)


class DouyinClient(Protocol):
    """抖音网络层。"""

    def redirect_target(self, url: str) -> str:
        """短链跳转到的地址（只看一跳，不跟随）；失败抛 FetchFailed。"""
        ...

    def aweme_detail(self, aweme_id: str, cookie: str) -> dict[str, Any] | None:
        """作品详情接口的 JSON（F2 `DouyinCrawler.fetch_post_detail` 的返回值）；
        抖音回了空响应时为 None；网络失败抛 FetchFailed，缺少 F2 抛 MissingTool。"""
        ...

    def download(self, url: str) -> bytes:
        """下载封面、图片；失败抛 FetchFailed。"""
        ...

    def download_audio(self, urls: list[str], directory: Path) -> Path:
        """依次尝试同一视频的各个播放地址，下载后转成 16 kHz 单声道 WAV 放在 directory；
        失败抛 FetchFailed，缺少 ffmpeg 抛 MissingTool。"""
        ...


class DouyinAdapter:
    platform = PLATFORM

    def __init__(self, credentials: CredentialProvider, client: DouyinClient | None = None) -> None:
        self.credentials = credentials
        self.client = client or F2DouyinClient()

    def expand_short_link(self, url: str) -> str:
        return self.client.redirect_target(url)

    def fetch(self, ref: SourceRef) -> FetchedSource:
        detail = self._detail(ref)
        common = {
            "title": _title(detail, ref),
            "author": (detail.get("author") or {}).get("nickname") or None,
            "published": _published(detail.get("create_time")),
            "description": (detail.get("desc") or "").strip(),
        }
        if images := _image_urls(detail):
            return FetchedSource(kind="图文", article=self._article(detail, images), **common)
        duration = detail.get("duration") or (detail.get("video") or {}).get("duration")
        return FetchedSource(
            kind="视频",
            duration=round(duration / 1000) if duration else None,
            cover=self._cover(detail),
            **common,
        )

    def download_audio(self, ref: SourceRef, directory: Path) -> Path:
        # 播放地址带时效，每次下载前重新取作品详情
        detail = self._detail(ref)
        if _image_urls(detail):
            raise FetchFailed(f"抖音作品 {ref.platform_id} 是图文，没有音频")
        urls = _play_urls(detail.get("video") or {})
        if not urls:
            raise FetchFailed(f"抖音作品 {ref.platform_id} 没有可下载的播放地址；{ADAPTER_BROKEN}")
        return self.client.download_audio(urls, directory)

    def _detail(self, ref: SourceRef) -> dict[str, Any]:
        """作品详情；已删除、私密等抛 SourceUnavailable，其余问题抛 FetchFailed。"""
        response = self.client.aweme_detail(ref.platform_id, self.credentials.cookie_string(PLATFORM))
        if not response:
            raise FetchFailed(EMPTY_RESPONSE)
        code = response.get("status_code")
        if code not in (0, None):
            message = response.get("status_msg") or "抖音接口出错"
            raise FetchFailed(f"{message}（{code}）")
        detail = response.get("aweme_detail")
        if not detail:
            reason = (response.get("filter_detail") or {}).get("detail_msg")
            if reason:
                raise SourceUnavailable(reason)
            raise FetchFailed(ADAPTER_BROKEN)
        if (detail.get("status") or {}).get("is_delete"):
            raise SourceUnavailable("作品已删除")
        return detail

    def _cover(self, detail: dict[str, Any]) -> Asset | None:
        video = detail.get("video") or {}
        urls = _url_list(video.get("origin_cover")) or _url_list(video.get("cover"))
        if not urls:
            return None
        return Asset(name=f"封面{_suffix(urls[0])}", data=self.client.download(urls[0]))

    def _article(self, detail: dict[str, Any], image_urls: list[str]) -> Article:
        images = [
            Asset(name=f"图{number:02d}{_suffix(url)}", data=self.client.download(url))
            for number, url in enumerate(image_urls, start=1)
        ]
        paragraphs = [line.strip() for line in (detail.get("desc") or "").splitlines() if line.strip()]
        # 话题（#AI）在 Obsidian 里会变成标签：转义成普通文字
        blocks = [re.sub(r"#(?=\S)", r"\\#", line) for line in paragraphs]
        blocks += [f"![]({image.name})" for image in images]
        return Article(markdown="\n\n".join(blocks) + "\n", images=images)


def _url_list(address: Any) -> list[str]:
    return [url for url in (address or {}).get("url_list") or [] if isinstance(url, str) and url]


def _title(detail: dict[str, Any], ref: SourceRef) -> str:
    """作品标题（图文常有）；没有时取文案第一行去掉话题；再没有就用作品 ID。"""
    if title := (detail.get("item_title") or "").strip():
        return title
    for line in (detail.get("desc") or "").splitlines():
        if text := re.sub(r"#\S+", "", line).strip():
            return text
    return ref.platform_id


def _published(create_time: Any) -> str | None:
    if not isinstance(create_time, int) or create_time <= 0:
        return None
    return dt.datetime.fromtimestamp(create_time, BEIJING).isoformat()


def _image_urls(detail: dict[str, Any]) -> list[str]:
    """图文的每张图取一个地址：有 JPEG 时优先 JPEG，否则用第一个地址。"""
    urls = []
    for image in detail.get("images") or []:
        candidates = _url_list(image)
        if candidates:
            jpeg = [url for url in candidates if _suffix(url) == ".jpg"]
            urls.append((jpeg or candidates)[0])
    return urls


def _play_urls(video: dict[str, Any]) -> list[str]:
    """转写只要音轨：取码率最低的一档（同一档的多个地址依次尝试），没有分档时用 play_addr。"""
    gears = [gear for gear in video.get("bit_rate") or [] if _url_list(gear.get("play_addr"))]
    if gears:
        smallest = min(gears, key=lambda gear: gear.get("bit_rate") or 0)
        return _url_list(smallest["play_addr"])
    return _url_list(video.get("play_addr"))


def _suffix(url: str) -> str:
    """抖音图床把格式写在路径末尾，如 `~tplv-dy-aweme-images:q75.webp`。"""
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    if suffix in (".jpeg", ".jpg"):
        return ".jpg"
    return suffix if suffix in (".png", ".webp", ".gif") else ".jpg"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class F2DouyinClient:
    """真实网络层：F2 负责作品详情接口（签名、请求头）；短链、图片与视频直接用 urllib。

    接口请求之间随机间隔数秒，图片与视频下载间隔较短。F2 只在真正采集时才导入。
    """

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
        ),
        "Referer": "https://www.douyin.com/",
    }
    TIMEOUT = 30

    def __init__(
        self,
        api_interval: tuple[float, float] = (3.0, 6.0),
        media_interval: tuple[float, float] = (0.5, 1.5),
    ) -> None:
        self.api_interval = api_interval
        self.media_interval = media_interval
        self._last_api = 0.0
        self._last_media = 0.0

    @staticmethod
    def _wait(last: float, interval: tuple[float, float]) -> float:
        wait = last + random.uniform(*interval) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        return time.monotonic()

    def _open(self, url: str, *, follow: bool = True):
        handlers: list[urllib.request.BaseHandler] = [] if follow else [_NoRedirect()]
        request = urllib.request.Request(url, headers=self.HEADERS)
        return urllib.request.build_opener(*handlers).open(request, timeout=self.TIMEOUT)

    def redirect_target(self, url: str) -> str:
        self._last_api = self._wait(self._last_api, self.api_interval)
        try:
            with self._open(url, follow=False) as response:
                return response.url
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400 and error.headers.get("Location"):
                return urljoin(url, error.headers["Location"])
            raise FetchFailed(f"抖音短链解析失败：{url}：HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise FetchFailed(f"抖音短链解析失败：{url}：{error}") from error

    def aweme_detail(self, aweme_id: str, cookie: str) -> dict[str, Any] | None:
        try:
            from f2.apps.douyin.crawler import DouyinCrawler
            from f2.apps.douyin.model import PostDetail
            from f2.apps.douyin.utils import ClientConfManager
            from f2.exceptions.api_exceptions import APIError, APIRetryExhaustedError
        except ImportError as error:
            raise MissingTool(F2_HINT) from error
        logging.getLogger("f2").setLevel(logging.ERROR)  # F2 默认往终端打 INFO 日志
        kwargs = {
            "headers": {
                "User-Agent": ClientConfManager.user_agent() or self.HEADERS["User-Agent"],
                "Referer": ClientConfManager.referer() or self.HEADERS["Referer"],
            },
            "cookie": cookie,
            "proxies": {"http://": None, "https://": None},
            "timeout": 10,
            "max_retries": 2,  # 空响应时 F2 每次重试前等 timeout 秒
        }

        async def fetch() -> dict[str, Any]:
            async with DouyinCrawler(kwargs) as crawler:
                return await crawler.fetch_post_detail(PostDetail(aweme_id=aweme_id))

        self._last_api = self._wait(self._last_api, self.api_interval)
        try:
            return asyncio.run(fetch())
        except APIRetryExhaustedError:
            return None  # 抖音一直回空响应：cookie 或签名失效
        except APIError as error:
            raise FetchFailed(f"访问抖音作品详情接口失败：{error}") from error

    def download(self, url: str) -> bytes:
        self._last_media = self._wait(self._last_media, self.media_interval)
        try:
            with self._open(url) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise FetchFailed(f"下载失败：{url}：{error}") from error

    def download_audio(self, urls: list[str], directory: Path) -> Path:
        ffmpeg = require_ffmpeg()
        video = directory / "video.mp4"
        errors = []
        for url in urls:
            self._last_media = self._wait(self._last_media, self.media_interval)
            try:
                with self._open(url) as response, video.open("wb") as target:
                    shutil.copyfileobj(response, target)
                break
            except (urllib.error.URLError, TimeoutError) as error:
                errors.append(f"{url}：{error}")
        else:
            raise FetchFailed("下载抖音视频失败：" + "；".join(errors))
        audio = directory / "audio.wav"
        command = [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(video)]
        command += ["-vn", "-ac", "1", "-ar", "16000", str(audio)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        video.unlink(missing_ok=True)
        if result.returncode != 0 or not audio.exists():
            raise FetchFailed(f"ffmpeg 提取音频失败：{result.stderr.strip()[-300:]}")
        return audio
