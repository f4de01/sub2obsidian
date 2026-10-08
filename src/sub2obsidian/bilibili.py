"""B站 平台适配器：基于 yt-dlp 采集视频元数据、封面与平台字幕（CC 或 AI 字幕，需登录）。

网络层（BilibiliClient）与解析分开：契约测试用录制样本回放网络层，验证解析与判定。
"""

from __future__ import annotations

import datetime as dt
import json
import random
import time
import urllib.error
import urllib.request
from http.cookiejar import MozillaCookieJar
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlsplit

from sub2obsidian.credentials import CredentialProvider, LoginRequired
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import Asset, FetchedSource, FetchFailed, SourceUnavailable
from sub2obsidian.transcript import Transcript, parse_srt

PLATFORM = "bilibili"
API = "https://api.bilibili.com"
BEIJING = dt.timezone(dt.timedelta(hours=8))

# view 接口中表示稿件已删除或不可见的错误码 → 来源已失效。
# 其他错误码（如 62004 审核中、-412 风控）视为可重试的失败。
UNAVAILABLE_CODES = {-404, 62002, 62012}
NOT_LOGGED_IN = -101  # nav 接口：账号未登录


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


class BilibiliAdapter:
    platform = PLATFORM

    def __init__(self, credentials: CredentialProvider, client: BilibiliClient | None = None) -> None:
        self.credentials = credentials
        self.client = client or HttpBilibiliClient()
        self._login_verified = False

    def expand_short_link(self, url: str) -> str:
        return self.client.redirect_target(url)

    def fetch(self, ref: SourceRef) -> FetchedSource:
        cookies = self.credentials.cookies_file(PLATFORM)
        self._verify_login(cookies)
        try:
            info = self.client.video_info(ref.url, cookies)
        except FetchFailed:
            self._raise_if_unavailable(ref)
            raise
        return self._to_source(info)

    def _verify_login(self, cookies: Path) -> None:
        """cookie 失效时 yt-dlp 只会悄悄拿不到字幕，所以先向 nav 接口确认登录态。"""
        if self._login_verified:
            return
        nav = self.client.api("/x/web-interface/nav", {}, cookies)
        code = nav.get("code")
        if code == NOT_LOGGED_IN or (code == 0 and not (nav.get("data") or {}).get("isLogin")):
            raise LoginRequired(PLATFORM)
        if code != 0:  # 如 -412 风控拦截：可重试，不是登录问题
            raise FetchFailed(f"{nav.get('message') or 'B站 接口出错'}（{code}）")
        self._login_verified = True

    def _raise_if_unavailable(self, ref: SourceRef) -> None:
        view = self.client.api("/x/web-interface/view", {"bvid": ref.platform_id})
        code = view.get("code")
        if code in UNAVAILABLE_CODES:
            raise SourceUnavailable(f"{view.get('message') or '稿件不可用'}（{code}）")

    def _to_source(self, info: dict[str, Any]) -> FetchedSource:
        timestamp = info.get("timestamp")
        description = (info.get("description") or "").strip()
        duration = info.get("duration")
        return FetchedSource(
            kind="视频",
            title=info.get("title") or info["id"],
            author=info.get("uploader"),
            published=(
                dt.datetime.fromtimestamp(timestamp, BEIJING).isoformat() if timestamp else None
            ),
            duration=round(duration) if duration is not None else None,
            description="" if description == "-" else description,
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
