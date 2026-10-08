"""B站 适配器的契约测试：用录制或按公开接口形态构造的样本回放网络层。

样本说明见 tests/fixtures/bilibili/README.md。登录后重新录制真实字幕样本放到 #12。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sub2obsidian.bilibili import BilibiliAdapter, HttpBilibiliClient
from sub2obsidian.credentials import LoginRequired
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import FetchFailed, SourceUnavailable
from sub2obsidian.tools import MissingTool

FIXTURES = Path(__file__).parent / "fixtures" / "bilibili"
REF = SourceRef("bilibili", "BV1GJ411x7h7", "https://www.bilibili.com/video/BV1GJ411x7h7")
COVER_URL = "https://i1.hdslb.com/bfs/archive/5242750857121e05146d5d5b13a47a2a6dd36e98.jpg"


def fixture(name: str):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text.strip()


@dataclass
class Credentials:
    cookies: Path | None

    def login(self, platform: str) -> None:  # pragma: no cover - 契约测试不登录
        raise AssertionError

    def cookies_file(self, platform: str) -> Path:
        if self.cookies is None:
            raise LoginRequired(platform)
        return self.cookies


@dataclass
class ReplayClient:
    """按样本回放 B站 网络层；记录请求以便断言。"""

    info: str | None = "ytdlp_info_with_subtitles.json"
    info_error: str | None = None
    nav: str = "nav_logged_in.json"
    view: str = "view_ok.json"
    audio_error: str | None = None
    folders: str = "fav_folders.json"
    fav_pages: dict[str, str] = field(
        default_factory=lambda: {"1": "fav_resources_page1.json", "2": "fav_resources_page2.json"}
    )
    toview: str = "toview.json"
    requests: list[str] = field(default_factory=list)

    def video_info(self, url: str, cookies: Path) -> dict:
        self.requests.append(f"yt-dlp {url} cookies={cookies.name}")
        if self.info_error:
            raise FetchFailed(fixture(self.info_error))
        return fixture(self.info)

    def api(self, endpoint: str, params: dict[str, str], cookies: Path | None = None) -> dict:
        self.requests.append(f"api {endpoint} {params}")
        if endpoint == "/x/v3/fav/resource/list":
            return fixture(self.fav_pages[params["pn"]])
        return fixture(
            {
                "/x/web-interface/nav": self.nav,
                "/x/web-interface/view": self.view,
                "/x/v3/fav/folder/created/list-all": self.folders,
                "/x/v2/history/toview/web": self.toview,
            }[endpoint]
        )

    def download(self, url: str) -> bytes:
        self.requests.append(f"download {url}")
        return b"cover:" + url.encode()

    def redirect_target(self, url: str) -> str:  # pragma: no cover
        raise AssertionError

    def download_audio(self, url: str, directory: Path, cookies: Path | None) -> Path:
        self.requests.append(f"audio {url} cookies={cookies.name if cookies else None}")
        if self.audio_error:
            raise FetchFailed(fixture(self.audio_error))
        audio = directory / "BV1GJ411x7h7.wav"
        audio.write_bytes(b"RIFF fake wav")
        return audio


@pytest.fixture
def cookies(tmp_path: Path) -> Path:
    path = tmp_path / "bilibili.cookies.txt"
    path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    return path


def adapter(cookies: Path | None, client: ReplayClient) -> BilibiliAdapter:
    return BilibiliAdapter(Credentials(cookies), client=client)


def test_fetch_maps_ytdlp_info_to_source_metadata_and_cover(cookies):
    client = ReplayClient(info="ytdlp_info_no_login.json")

    fetched = adapter(cookies, client).fetch(REF)

    assert fetched.kind == "视频"
    assert fetched.title == "【官方 MV】Never Gonna Give You Up - Rick Astley"
    assert fetched.author == "索尼音乐中国"
    # 1577835803 = 2019-12-31T23:43:23Z
    assert fetched.published == "2020-01-01T07:43:23+08:00"
    assert fetched.duration == 212
    assert fetched.description == ""  # B站 用「-」表示没有简介
    assert fetched.cover is not None
    assert fetched.cover.name == "封面.jpg"
    assert fetched.cover.data == b"cover:" + COVER_URL.encode()  # http 封面地址升级为 https
    assert f"yt-dlp {REF.url} cookies=bilibili.cookies.txt" in client.requests


def test_video_without_platform_subtitles_has_no_transcript(cookies):
    """未登录录制的真实样本只有弹幕，没有字幕：留待 ASR。"""
    fetched = adapter(cookies, ReplayClient(info="ytdlp_info_no_login.json")).fetch(REF)

    assert fetched.transcript is None


def test_cc_subtitles_are_preferred_over_ai_subtitles(cookies):
    fetched = adapter(cookies, ReplayClient(info="ytdlp_info_with_subtitles.json")).fetch(REF)

    assert fetched.transcript is not None
    assert fetched.transcript.origin == "B站 CC 字幕（zh-CN）"
    segments = [(s.start, s.end, s.text) for s in fetched.transcript.segments]
    assert segments == [
        (18.01, 21.76, "我们对爱情都不陌生"),
        (22.32, 25.88, "你知道规则\n我也一样"),
        (3725.0, 3726.0, "结尾"),
    ]


def test_ai_subtitles_are_used_when_no_cc_subtitles(cookies):
    fetched = adapter(cookies, ReplayClient(info="ytdlp_info_ai_subtitles_only.json")).fetch(REF)

    assert fetched.transcript is not None
    assert fetched.transcript.origin == "B站 AI 字幕（ai-zh）"
    assert [s.text for s in fetched.transcript.segments] == ["（AI）大家好", "（AI）今天聊聊这首歌"]


def test_deleted_or_invisible_video_is_reported_unavailable(cookies):
    client = ReplayClient(info_error="ytdlp_error_unavailable.txt", view="view_unavailable.json")

    with pytest.raises(SourceUnavailable, match="稿件不可见（62002）"):
        adapter(cookies, client).fetch(REF)

    assert "api /x/web-interface/view {'bvid': 'BV1GJ411x7h7'}" in client.requests


def test_ytdlp_failure_on_a_visible_video_is_retryable(cookies):
    client = ReplayClient(info_error="ytdlp_error_unavailable.txt", view="view_ok.json")

    with pytest.raises(FetchFailed, match="extractor error"):
        adapter(cookies, client).fetch(REF)


def test_expired_login_asks_to_log_in_again(cookies):
    client = ReplayClient(nav="nav_logged_out.json")

    with pytest.raises(LoginRequired, match="请重新登录 B站：sub2obsidian login bilibili"):
        adapter(cookies, client).fetch(REF)

    assert not any(request.startswith("yt-dlp") for request in client.requests)


def test_missing_login_is_reported_before_any_request():
    client = ReplayClient()

    with pytest.raises(LoginRequired):
        adapter(None, client).fetch(REF)

    assert client.requests == []


class _Redirect(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server 的约定
        self.send_response(302)
        self.send_header(
            "Location", "https://www.bilibili.com/video/BV1GJ411x7h7?share_source=copy_link"
        )
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


def test_short_link_resolves_to_its_redirect_target_without_following_it():
    """b23.tv 以 302 + Location 跳转（真实响应如此）；本地回环服务器模拟，不访问网络。"""
    server = HTTPServer(("127.0.0.1", 0), _Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/AbCd123"
        target = BilibiliAdapter(Credentials(None), client=HttpBilibiliClient(interval=(0, 0))).expand_short_link(url)
    finally:
        server.shutdown()
        server.server_close()

    assert target == "https://www.bilibili.com/video/BV1GJ411x7h7?share_source=copy_link"


def test_logged_in_sample_yields_a_platform_subtitle_transcript(cookies):
    """登录后录制的样本（#12 用 scripts/record_bilibili_fixtures.py 替换）得到平台字幕口播稿。"""
    bvid = fixture("ytdlp_info_logged_in.json")["id"]
    ref = SourceRef("bilibili", bvid, f"https://www.bilibili.com/video/{bvid}")

    fetched = adapter(cookies, ReplayClient(info="ytdlp_info_logged_in.json")).fetch(ref)

    assert fetched.title
    assert fetched.cover is not None
    assert fetched.transcript is not None
    assert fetched.transcript.origin.startswith(("B站 CC 字幕（", "B站 AI 字幕（"))
    starts = [segment.start for segment in fetched.transcript.segments]
    assert starts and starts == sorted(starts)
    assert all(segment.text.strip() for segment in fetched.transcript.segments)


def test_rate_limited_login_check_is_retryable_not_a_login_problem(cookies):
    client = ReplayClient(nav="nav_rate_limited.json")

    with pytest.raises(FetchFailed, match="请求被拦截（-412）"):
        adapter(cookies, client).fetch(REF)


def test_audio_is_downloaded_into_the_given_directory_with_login_cookies(cookies, tmp_path: Path):
    client = ReplayClient()
    directory = tmp_path / "临时音频"
    directory.mkdir()

    audio = adapter(cookies, client).download_audio(REF, directory)

    assert audio.parent == directory
    assert audio.read_bytes() == b"RIFF fake wav"
    assert client.requests == [f"audio {REF.url} cookies=bilibili.cookies.txt"]


def test_audio_of_a_public_video_downloads_without_login(tmp_path: Path):
    client = ReplayClient()

    audio = adapter(None, client).download_audio(REF, tmp_path)

    assert audio.exists()
    assert client.requests == [f"audio {REF.url} cookies=None"]


def test_audio_of_a_deleted_video_is_reported_unavailable(cookies, tmp_path: Path):
    client = ReplayClient(audio_error="ytdlp_error_unavailable.txt", view="view_unavailable.json")

    with pytest.raises(SourceUnavailable, match="稿件不可见（62002）"):
        adapter(cookies, client).download_audio(REF, tmp_path)


def test_audio_download_failure_on_a_visible_video_is_retryable(cookies, tmp_path: Path):
    client = ReplayClient(audio_error="ytdlp_error_unavailable.txt", view="view_ok.json")

    with pytest.raises(FetchFailed, match="extractor error"):
        adapter(cookies, client).download_audio(REF, tmp_path)


def test_audio_download_without_ffmpeg_fails_clearly_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    empty = tmp_path / "空的PATH"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(MissingTool, match="未找到 ffmpeg"):
        HttpBilibiliClient(interval=(0, 0)).download_audio(REF.url, tmp_path, None)


# ---- 列出收藏（拉取）：样本按 B站 公开接口形态构造，登录后录制真实样本放到 #12 ----

FAVORITES_PARAMS = {"ps": "20", "order": "mtime", "type": "0", "platform": "web"}


def test_favorite_lists_are_the_users_folders_plus_watch_later(cookies):
    client = ReplayClient()

    lists = adapter(cookies, client).favorite_lists()

    assert [(item.id, item.title) for item in lists] == [
        ("1052622027", "默认收藏夹"),
        ("2087654301", "AI 学习"),
        ("toview", "稍后再看"),
    ]
    # 收藏夹属于当前登录的账号：mid 取自 nav 接口
    assert client.requests == [
        "api /x/web-interface/nav {}",
        "api /x/v3/fav/folder/created/list-all {'up_mid': '10000001'}",
    ]


def test_favorites_page_maps_video_metadata_without_fetching_the_videos(cookies):
    client = ReplayClient()

    page = adapter(cookies, client).favorites("1052622027", None)

    assert page.next == "2"
    first = page.items[0]
    assert first.ref == REF
    assert first.kind == "视频"
    assert first.title == "【官方 MV】Never Gonna Give You Up - Rick Astley"
    assert first.author == "索尼音乐中国"
    assert first.published == "2020-01-01T07:43:23+08:00"
    assert first.duration == 212
    assert first.description == ""  # B站 用「-」表示没有简介
    assert first.unavailable is None
    assert not any(request.startswith(("yt-dlp", "download")) for request in client.requests)
    assert client.requests[-1] == (
        f"api /x/v3/fav/resource/list {({'media_id': '1052622027', 'pn': '1'} | FAVORITES_PARAMS)}"
    )


def test_favorite_already_gone_is_marked_unavailable_and_non_videos_are_skipped(cookies):
    page = adapter(cookies, ReplayClient()).favorites("1052622027", None)

    assert [item.ref.platform_id for item in page.items] == ["BV1GJ411x7h7", "BV1x4411V7Ab"]
    gone = page.items[1]
    assert gone.unavailable == "收藏夹中显示为已失效视频"


def test_last_favorites_page_has_no_next_cursor(cookies):
    client = ReplayClient()

    page = adapter(cookies, client).favorites("1052622027", "2")

    assert page.next is None
    [item] = page.items
    assert item.ref.url == "https://www.bilibili.com/video/BV1Ab411c7De"
    assert item.description == "检索增强生成（RAG）入门：\n为什么需要它、怎么搭一个最小可用的 RAG。"
    assert item.published == "2024-05-01T20:00:00+08:00"
    assert "'pn': '2'" in client.requests[-1]


def test_watch_later_is_one_page_of_videos(cookies):
    client = ReplayClient()

    page = adapter(cookies, client).favorites("toview", None)

    assert page.next is None
    [item] = page.items
    assert item.ref.platform_id == "BV1Mc411P7Qr"
    assert item.title == "10 分钟看懂 MCP 协议"
    assert item.author == "讲协议的UP"
    assert item.duration == 600
    assert item.description == "MCP 是什么、为什么火。"
    assert item.published == "2025-01-01T08:00:00+08:00"
    assert client.requests[-1] == "api /x/v2/history/toview/web {}"


def test_favorites_api_error_is_retryable(cookies):
    client = ReplayClient(fav_pages={"1": "fav_resources_private.json"})

    with pytest.raises(FetchFailed, match="访问权限不足（-403）"):
        adapter(cookies, client).favorites("1052622027", None)


def test_listing_favorites_with_expired_login_asks_to_log_in_again(cookies):
    client = ReplayClient(nav="nav_logged_out.json")

    with pytest.raises(LoginRequired, match="请重新登录 B站"):
        adapter(cookies, client).favorite_lists()

    assert client.requests == ["api /x/web-interface/nav {}"]


def test_listing_favorites_without_login_makes_no_request():
    client = ReplayClient()

    with pytest.raises(LoginRequired):
        adapter(None, client).favorites("1052622027", None)

    assert client.requests == []


class _Json(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server 的约定
        body = b'{"code": 0, "data": {}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def test_requests_are_spaced_by_the_configured_random_interval(monkeypatch: pytest.MonkeyPatch):
    """本地回环服务器代替 api.bilibili.com，不访问网络。"""
    import time

    server = HTTPServer(("127.0.0.1", 0), _Json)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("sub2obsidian.bilibili.API", f"http://127.0.0.1:{server.server_port}")
    client = HttpBilibiliClient(interval=(0.3, 0.4))
    started: list[float] = []
    try:
        for _ in range(3):
            started.append(time.monotonic())
            client.api("/x/v3/fav/resource/list", {"pn": "1"})
            started[-1] = time.monotonic() - started[-1]
    finally:
        server.shutdown()
        server.server_close()

    # 第一个请求不等；之后每个请求都至少等足最短间隔
    assert started[0] < 0.3
    assert all(0.3 <= elapsed < 1.0 for elapsed in started[1:])
