"""B站 平台适配器：基于 yt-dlp 采集视频元数据、封面与平台字幕（CC 或 AI 字幕，需登录），
并为没有字幕的视频下载供转写的音频（需要 ffmpeg）。

多P视频的每个分P是一条来源：分P数、分P标题与时长取自公开的 view 接口（不需要登录），
采集与下载音频时 yt-dlp 打开的是该分P的链接，字幕也就按分P。

拉取时列出用户的全部收藏夹与稍后再看，只取元数据，其中的多P视频展开为每个分P一条。所用的是
yt-dlp 的 B站 收藏夹、稍后再看提取器调用的同一组接口（fav/resource、toview），但直接按页读取：
yt-dlp 的提取器只给出 BV 号，要拿标题、简介、时长得逐个视频再解析一遍，回填几千条收藏时
请求量会翻几倍。

网络层（BilibiliClient）与解析分开：契约测试用录制样本回放网络层，验证解析与判定。
"""

from __future__ import annotations

import datetime as dt
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import replace
from http.cookiejar import MozillaCookieJar
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlsplit

from sub2obsidian.credentials import CredentialProvider, LoginRequired
from sub2obsidian.links import SourceRef, UnsupportedLink, bilibili_part, bilibili_ref
from sub2obsidian.platforms import (
    Asset,
    Favorite,
    FavoriteList,
    FavoritesPage,
    FetchedSource,
    FetchFailed,
    SourceUnavailable,
)
from sub2obsidian.sources import Kind, VideoPart
from sub2obsidian.tools import require_ffmpeg
from sub2obsidian.transcript import Transcript, parse_srt

PLATFORM = "bilibili"
API = "https://api.bilibili.com"
BEIJING = dt.timezone(dt.timedelta(hours=8))

# view 接口中表示稿件已删除或不可见的错误码 → 来源已失效。
# 其他错误码（如 62004 审核中、-412 风控）视为可重试的失败。
UNAVAILABLE_CODES = {-404, 62002, 62012}
NOT_LOGGED_IN = -101  # nav 接口：账号未登录

WATCH_LATER = FavoriteList(id="toview", title="稍后再看")
FAVORITES_PAGE_SIZE = 20  # 收藏夹接口每页最多 20 条
VIDEO_MEDIA = 2  # 收藏夹里的视频；其余（音频、合集等）不是视频来源
INVALID_ATTRS = {1, 9}  # 收藏夹里已失效的视频：9 为 UP主删除，1 为其他原因
INVALID_TITLE = "已失效视频"


class BilibiliClient(Protocol):
    """B站 网络层。"""

    def video_info(self, url: str, cookies: Path) -> dict[str, Any]:
        """yt-dlp 的 info dict（含内嵌 SRT 字幕）；失败抛 FetchFailed。"""
        ...

    def api(self, endpoint: str, params: dict[str, str], cookies: Path | None = None) -> dict[str, Any]:
        """api.bilibili.com 的 JSON 响应；网络失败抛 FetchFailed。"""
        ...

    def download(self, url: str) -> bytes: ...

    def redirect_target(self, url: str) -> str:
        """链接跳转到的地址（只看一跳，不跟随）。"""
        ...

    def download_audio(self, url: str, directory: Path, cookies: Path | None) -> Path:
        """把视频的音轨下载到 directory 并转成 16 kHz 单声道 WAV；失败抛 FetchFailed，
        缺少 ffmpeg 时抛 MissingTool。"""
        ...


class BilibiliAdapter:
    platform = PLATFORM

    def __init__(self, credentials: CredentialProvider, client: BilibiliClient | None = None) -> None:
        self.credentials = credentials
        self.client = client or HttpBilibiliClient()
        self._mid: int | None = None  # 已确认登录的账号；None 表示还没确认

    def expand_short_link(self, url: str) -> str:
        return self.client.redirect_target(url)

    def count_parts(self, video_id: str) -> int:
        try:
            return len(_pages(self._view(video_id)))
        except SourceUnavailable:
            return 1  # 采集时再判定为已失效

    def fetch(self, ref: SourceRef) -> FetchedSource:
        cookies = self.credentials.cookies_file(PLATFORM)
        self._verify_login(cookies)
        bvid, number = bilibili_part(ref)
        view = self._view(bvid)
        pages = _pages(view)
        if number > len(pages):
            raise SourceUnavailable(f"该视频没有第 {number} P（共 {len(pages)} P）")
        source = self._to_source(self.client.video_info(ref.url, cookies))
        if len(pages) == 1:
            return source
        # yt-dlp 给多P视频的标题加了它自己的分P后缀：视频标题以 view 接口为准
        title, duration, part = _part(view.get("title") or bvid, pages[number - 1])
        return replace(source, title=title, duration=duration, part=part)

    def download_audio(self, ref: SourceRef, directory: Path) -> Path:
        # 公开视频的音频不需要登录；已登录时带上 cookie，降低被风控拦截的概率
        try:
            cookies: Path | None = self.credentials.cookies_file(PLATFORM)
        except LoginRequired:
            cookies = None
        try:
            return self.client.download_audio(ref.url, directory, cookies)
        except FetchFailed:
            self._view(bilibili_part(ref)[0])  # 视频已删除或不可见时抛 SourceUnavailable
            raise

    def favorite_lists(self) -> list[FavoriteList]:
        cookies = self.credentials.cookies_file(PLATFORM)
        mid = self._verify_login(cookies)
        data = self._data("/x/v3/fav/folder/created/list-all", {"up_mid": str(mid)}, cookies)
        folders = [
            FavoriteList(str(folder["id"]), folder.get("title") or str(folder["id"]))
            for folder in data.get("list") or []
        ]
        return [*folders, WATCH_LATER]

    def favorites(self, list_id: str, cursor: str | None) -> FavoritesPage:
        cookies = self.credentials.cookies_file(PLATFORM)
        self._verify_login(cookies)
        if list_id == WATCH_LATER.id:
            data = self._data("/x/v2/history/toview/web", {}, cookies)
            listed = [(_watch_later_item(item), item.get("videos")) for item in data.get("list") or []]
            return FavoritesPage(self._expand(listed), None)
        page = int(cursor or 1)
        params = {
            "media_id": list_id,
            "pn": str(page),
            "ps": str(FAVORITES_PAGE_SIZE),
            "order": "mtime",  # 按收藏时间从新到旧
            "type": "0",
            "platform": "web",
        }
        data = self._data("/x/v3/fav/resource/list", params, cookies)
        medias = [media for media in data.get("medias") or [] if media.get("type") == VIDEO_MEDIA]
        listed = [(_favorite_media(media), media.get("page")) for media in medias]
        return FavoritesPage(self._expand(listed), str(page + 1) if data.get("has_more") else None)

    def _expand(self, listed: list[tuple[Favorite | None, int | None]]) -> list[Favorite]:
        """（收藏, 分P数）→ 收藏，多P视频展开为每个分P一条；不是视频来源的（None）跳过。"""
        return [
            part
            for favorite, count in listed
            if favorite is not None
            for part in self._listed_parts(favorite, count or 1)
        ]

    def _data(self, endpoint: str, params: dict[str, str], cookies: Path) -> dict[str, Any]:
        """需要登录的接口的 data；登录失效抛 LoginRequired，其他错误码可重试。"""
        response = self.client.api(endpoint, params, cookies)
        code = response.get("code")
        if code == NOT_LOGGED_IN:
            raise LoginRequired(PLATFORM)
        if code != 0:
            raise FetchFailed(f"{response.get('message') or 'B站 接口出错'}（{code}）")
        return response.get("data") or {}

    def _verify_login(self, cookies: Path) -> int:
        """cookie 失效时 yt-dlp 只会悄悄拿不到字幕，所以先向 nav 接口确认登录态；返回账号 mid。"""
        if self._mid is not None:
            return self._mid
        nav = self.client.api("/x/web-interface/nav", {}, cookies)
        code = nav.get("code")
        data = nav.get("data") or {}
        if code == NOT_LOGGED_IN or (code == 0 and not data.get("isLogin")):
            raise LoginRequired(PLATFORM)
        if code != 0:  # 如 -412 风控拦截：可重试，不是登录问题
            raise FetchFailed(f"{nav.get('message') or 'B站 接口出错'}（{code}）")
        self._mid = int(data.get("mid") or 0)
        return self._mid

    def _view(self, bvid: str) -> dict[str, Any]:
        """公开的 view 接口给出的视频信息（含分P列表），不需要登录。

        视频已删除或不可见时抛 SourceUnavailable，其他错误码（审核中、风控）抛 FetchFailed。
        """
        view = self.client.api("/x/web-interface/view", {"bvid": bvid})
        code = view.get("code")
        message = view.get("message") or "稿件不可用"
        if code in UNAVAILABLE_CODES:
            raise SourceUnavailable(f"{message}（{code}）")
        if code != 0:
            raise FetchFailed(f"{message}（{code}）")
        return view.get("data") or {}

    def _listed_parts(self, favorite: Favorite, count: int) -> list[Favorite]:
        """收藏列表中的多P视频展开为每个分P一条；单P视频与已失效的视频原样保留。"""
        if count <= 1 or favorite.unavailable is not None:
            return [favorite]
        bvid = favorite.ref.platform_id
        listed = []
        for page in _pages(self._view(bvid)):
            title, duration, part = _part(favorite.title, page)
            ref = bilibili_ref(bvid, part.number)
            listed.append(replace(favorite, ref=ref, title=title, duration=duration, part=part))
        return listed

    def _to_source(self, info: dict[str, Any]) -> FetchedSource:
        duration = info.get("duration")
        return FetchedSource(
            kind=Kind.VIDEO,
            title=info.get("title") or info["id"],
            author=info.get("uploader"),
            published=_beijing_time(info.get("timestamp")),
            duration=round(duration) if duration is not None else None,
            description=_description(info.get("description")),
            cover=self._cover(info.get("thumbnail")),
            transcript=_subtitles(info.get("subtitles") or {}),
        )

    def _cover(self, url: str | None) -> Asset | None:
        if not url:
            return None
        if url.startswith("http://"):
            url = "https://" + url.removeprefix("http://")
        suffix = PurePosixPath(urlsplit(url).path).suffix.lower() or ".jpg"
        return Asset(name=f"封面{suffix}", data=self.client.download(url))


def _pages(view: dict[str, Any]) -> list[dict[str, Any]]:
    """view 接口中的分P列表；单P视频只有一项。"""
    return view.get("pages") or [{"page": 1}]


def _part(video_title: str, page: dict[str, Any]) -> tuple[str, int | None, VideoPart]:
    """多P视频中一个分P的（标题, 时长, 分P）：标题为「视频标题 P序号 分P标题」。"""
    number = int(page["page"])
    name = " ".join((page.get("part") or "").split())
    title = f"{video_title} P{number}" + (f" {name}" if name and name != video_title else "")
    return title, page.get("duration") or None, VideoPart(number, video_title)


def _beijing_time(timestamp: int | None) -> str | None:
    return dt.datetime.fromtimestamp(timestamp, BEIJING).isoformat() if timestamp else None


def _description(text: str | None) -> str:
    """B站 用「-」表示没有简介。"""
    text = (text or "").strip()
    return "" if text == "-" else text


def _listed_video(
    bvid: str | None,
    *,
    title: str | None,
    author: str | None,
    published: int | None,
    duration: int | None,
    description: str | None,
    unavailable: bool,
) -> Favorite | None:
    """收藏列表中的一个视频；没有 BV 号（如番剧、课程）时不算视频来源。"""
    try:
        ref = bilibili_ref(bvid or "")
    except UnsupportedLink:
        return None
    return Favorite(
        ref=ref,
        kind=Kind.VIDEO,
        title=title or ref.platform_id,
        author=author,
        published=_beijing_time(published),
        duration=duration or None,
        description=_description(description),
        unavailable="收藏夹中显示为已失效视频" if unavailable or title == INVALID_TITLE else None,
    )


def _favorite_media(media: dict[str, Any]) -> Favorite | None:
    return _listed_video(
        media.get("bvid") or media.get("bv_id"),
        title=media.get("title"),
        author=(media.get("upper") or {}).get("name"),
        published=media.get("pubtime"),
        duration=media.get("duration"),
        description=media.get("intro"),
        unavailable=media.get("attr") in INVALID_ATTRS,
    )


def _watch_later_item(item: dict[str, Any]) -> Favorite | None:
    return _listed_video(
        item.get("bvid"),
        title=item.get("title"),
        author=(item.get("owner") or {}).get("name"),
        published=item.get("pubdate"),
        duration=item.get("duration"),
        description=item.get("desc"),
        unavailable=False,
    )


def _subtitles(subtitles: dict[str, list[dict[str, Any]]]) -> Transcript | None:
    """平台字幕：人工 CC 字幕优先（中文优先），其次 AI 字幕；弹幕不算字幕。"""
    available = {
        lang: entry["data"]
        for lang, entries in subtitles.items()
        if lang != "danmaku"
        for entry in entries
        if entry.get("ext") == "srt" and entry.get("data")
    }

    def rank(lang: str) -> tuple[bool, bool]:
        is_ai = lang.startswith("ai-")
        return (is_ai, not lang.removeprefix("ai-").startswith("zh"))

    for lang in sorted(available, key=rank):
        segments = parse_srt(available[lang])
        if segments:
            kind = "AI 字幕" if lang.startswith("ai-") else "CC 字幕"
            return Transcript(origin=f"B站 {kind}（{lang}）", segments=segments)
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _QuietLogger:
    """让 yt-dlp 不往终端打印；错误仍以 DownloadError 抛出。"""

    def debug(self, message: str) -> None:
        pass

    info = warning = error = debug


class HttpBilibiliClient:
    """真实网络层：urllib 访问 B站 接口，yt-dlp 解析视频页；请求之间随机间隔。"""

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }
    TIMEOUT = 20

    def __init__(self, interval: tuple[float, float] = (1.0, 3.0)) -> None:
        self.interval = interval
        self._last_request = 0.0

    def _throttle(self) -> None:
        wait = self._last_request + random.uniform(*self.interval) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _open(self, url: str, cookies: Path | None = None, *, follow: bool = True):
        handlers: list[urllib.request.BaseHandler] = []
        if cookies is not None:
            jar = MozillaCookieJar(str(cookies))
            jar.load(ignore_discard=True, ignore_expires=True)
            handlers.append(urllib.request.HTTPCookieProcessor(jar))
        if not follow:
            handlers.append(_NoRedirect())
        self._throttle()
        request = urllib.request.Request(url, headers=self.HEADERS)
        return urllib.request.build_opener(*handlers).open(request, timeout=self.TIMEOUT)

    def api(self, endpoint: str, params: dict[str, str], cookies: Path | None = None) -> dict[str, Any]:
        url = API + endpoint + (f"?{urlencode(params)}" if params else "")
        try:
            with self._open(url, cookies) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise FetchFailed(f"访问 B站 接口失败：{endpoint}：{error}") from error

    def download(self, url: str) -> bytes:
        try:
            with self._open(url) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            raise FetchFailed(f"下载失败：{url}：{error}") from error

    def redirect_target(self, url: str) -> str:
        try:
            with self._open(url, follow=False) as response:
                return response.url
        except urllib.error.HTTPError as error:
            if 300 <= error.code < 400 and error.headers.get("Location"):
                return urljoin(url, error.headers["Location"])
            raise FetchFailed(f"短链解析失败：{url}：HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise FetchFailed(f"短链解析失败：{url}：{error}") from error

    def video_info(self, url: str, cookies: Path) -> dict[str, Any]:
        import yt_dlp  # 导入较慢，只在真正采集时加载

        options = {
            "logger": _QuietLogger(),
            "skip_download": True,
            "noplaylist": True,  # 多 P 视频只取 URL 指向的那一 P（缺省第 1 P）
            "writesubtitles": True,  # 让 yt-dlp 抓取字幕并以 SRT 内嵌在 info 中
            "subtitleslangs": ["all", "-danmaku"],
            "cookiefile": str(cookies),
            "cachedir": False,
            "sleep_interval_requests": self.interval[0],
        }
        self._throttle()
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.sanitize_info(ydl.extract_info(url, download=False))
        except yt_dlp.utils.DownloadError as error:
            raise FetchFailed(str(error).removeprefix("ERROR: ")) from error

    def download_audio(self, url: str, directory: Path, cookies: Path | None) -> Path:
        ffmpeg = require_ffmpeg()
        import yt_dlp

        options = {
            "logger": _QuietLogger(),
            "noplaylist": True,
            "format": "bestaudio/best",
            "outtmpl": str(directory / "%(id)s.%(ext)s"),
            "ffmpeg_location": ffmpeg,
            # 转写只需要 16 kHz 单声道；统一成 WAV，转写引擎不必再适配各种封装
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
            "postprocessor_args": {"extractaudio": ["-ar", "16000", "-ac", "1"]},
            "cachedir": False,
            "noprogress": True,
            "sleep_interval_requests": self.interval[0],
        }
        if cookies is not None:
            options["cookiefile"] = str(cookies)
        self._throttle()
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as error:
            raise FetchFailed(str(error).removeprefix("ERROR: ")) from error
        audio = sorted(directory.glob("*.wav"))
        if not audio:
            raise FetchFailed(f"音频下载后没有找到转换好的 WAV 文件：{url}")
        return audio[0]
