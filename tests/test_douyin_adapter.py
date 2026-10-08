"""抖音适配器的契约测试：用 F2 返回结构的样本回放网络层（作品详情、列出收藏）。

样本说明见 tests/fixtures/douyin/README.md。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from sub2obsidian.credentials import LoginRequired
from sub2obsidian.douyin import PAGE_SIZE, DouyinAdapter
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import (
    Favorite,
    FavoriteList,
    FavoritesAdapter,
    FetchFailed,
    SourceUnavailable,
)

FIXTURES = Path(__file__).parent / "fixtures" / "douyin"
VIDEO = SourceRef("douyin", "7412345678901234567", "https://www.douyin.com/video/7412345678901234567")
NOTE = SourceRef("douyin", "7423456789012345678", "https://www.douyin.com/note/7423456789012345678")
DELETED = SourceRef("douyin", "7434567890123456789", "https://www.douyin.com/video/7434567890123456789")
COOKIE = "sessionid=0123abcd; ttwid=1%7Cvisitor"


def sample(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"post_detail_{name}.json").read_text(encoding="utf-8"))


def listing(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@dataclass
class ReplayClient:
    """按样本回放抖音网络层；response 为 None 表示抖音返回了空响应。记录请求以便断言。"""

    response: dict[str, Any] | None = field(default_factory=lambda: sample("video"))
    redirects: dict[str, str] = field(default_factory=dict)
    details: list[tuple[str, str]] = field(default_factory=list)  # (作品 ID, cookie)
    downloads: list[str] = field(default_factory=list)
    audio_requests: list[list[str]] = field(default_factory=list)
    # 列出收藏：(接口, 收藏夹 ID 或 None, 游标) → 响应；None 表示空响应
    pages: dict[tuple[str, str | None, int], dict[str, Any] | None] = field(default_factory=dict)
    list_requests: list[tuple[str, str | None, int, int, str]] = field(default_factory=list)

    def _page(self, api: str, collects_id: str | None, cursor: int, count: int, cookie: str):
        self.list_requests.append((api, collects_id, cursor, count, cookie))
        return copy.deepcopy(self.pages[(api, collects_id, cursor)])

    def collection(self, cursor: int, count: int, cookie: str) -> dict[str, Any] | None:
        return self._page("collection", None, cursor, count, cookie)

    def collects(self, cursor: int, count: int, cookie: str) -> dict[str, Any] | None:
        return self._page("collects", None, cursor, count, cookie)

    def collects_video(
        self, collects_id: str, cursor: int, count: int, cookie: str
    ) -> dict[str, Any] | None:
        return self._page("collects_video", collects_id, cursor, count, cookie)

    def redirect_target(self, url: str) -> str:
        return self.redirects[url]

    def aweme_detail(self, aweme_id: str, cookie: str) -> dict[str, Any] | None:
        self.details.append((aweme_id, cookie))
        return copy.deepcopy(self.response)

    def download(self, url: str) -> bytes:
        self.downloads.append(url)
        return b"image:" + url.encode()

    def download_audio(self, urls: list[str], directory: Path) -> Path:
        self.audio_requests.append(urls)
        audio = directory / "audio.wav"
        audio.write_bytes(b"RIFF fake wav")
        return audio


@dataclass
class Credentials:
    logged_in: bool = True
    asked: list[str] = field(default_factory=list)

    def login(self, platform: str) -> None:  # pragma: no cover
        raise AssertionError

    def cookies_file(self, platform: str) -> Path:  # pragma: no cover
        raise AssertionError("抖音用 cookie 字符串，不用 cookies.txt")

    def cookie_string(self, platform: str) -> str:
        self.asked.append(platform)
        if not self.logged_in:
            raise LoginRequired(platform)
        return COOKIE


def adapter(client: ReplayClient, credentials: Credentials | None = None) -> DouyinAdapter:
    return DouyinAdapter(credentials or Credentials(), client)


def test_fetch_maps_video_detail_to_source_metadata():
    client = ReplayClient(sample("video"))

    fetched = adapter(client).fetch(VIDEO)

    assert fetched.kind == "视频"
    assert fetched.title == "三分钟讲清楚 MCP 是什么"
    assert fetched.author == "某AI博主"
    assert fetched.byline is None
    assert fetched.published == "2024-09-10T19:30:00+08:00"
    assert fetched.duration == 183
    assert fetched.description == "三分钟讲清楚 MCP 是什么 #AI #大模型 #MCP"
    assert fetched.transcript is None  # 抖音没有平台字幕，留待 ASR
    assert fetched.article is None
    assert fetched.cover is not None
    assert fetched.cover.name == "封面.jpg"
    origin_cover = sample("video")["aweme_detail"]["video"]["origin_cover"]["url_list"][0]
    assert fetched.cover.data == b"image:" + origin_cover.encode()
    assert client.details == [(VIDEO.platform_id, COOKIE)]


def test_fetch_maps_note_detail_to_article_with_every_image():
    client = ReplayClient(sample("note"))

    fetched = adapter(client).fetch(NOTE)

    assert fetched.kind == "图文"
    assert fetched.title == "RAG 入门笔记"
    assert fetched.author == "某AI博主"
    assert fetched.published == "2024-10-01T08:00:00+08:00"
    assert fetched.duration is None
    assert fetched.cover is None  # 图文的封面就是第一张图
    assert fetched.article is not None
    images = sample("note")["aweme_detail"]["images"]
    jpeg_urls = [image["url_list"][2] for image in images]  # 有 JPEG 时优先 JPEG
    assert client.downloads == jpeg_urls
    assert [(image.name, image.data) for image in fetched.article.images] == [
        (f"图0{i}.jpg", b"image:" + url.encode()) for i, url in enumerate(jpeg_urls, start=1)
    ]
    markdown = fetched.article.markdown
    assert markdown.index("RAG 入门笔记") < markdown.index("检索增强生成分三步：切分、检索、生成")
    assert "收藏起来慢慢看 \\#RAG \\#AI学习" in markdown  # 话题不变成 Obsidian 标签
    assert markdown.index("![](图01.jpg)") < markdown.index("![](图02.jpg)") < markdown.index(
        "![](图03.jpg)"
    )


def test_note_image_without_jpeg_keeps_its_own_format():
    response = sample("note")
    for image in response["aweme_detail"]["images"]:
        image["url_list"] = image["url_list"][:2]  # 只有 WebP

    fetched = adapter(ReplayClient(response)).fetch(NOTE)

    assert fetched.article is not None
    assert [image.name for image in fetched.article.images] == ["图01.webp", "图02.webp", "图03.webp"]
    assert "![](图01.webp)" in fetched.article.markdown


def test_video_title_falls_back_to_first_line_of_description_without_topics():
    response = sample("video")
    response["aweme_detail"]["desc"] = "\n  RAG 是什么？#AI #大模型\n第二行"

    fetched = adapter(ReplayClient(response)).fetch(VIDEO)

    assert fetched.title == "RAG 是什么？"


def test_post_without_any_text_is_titled_by_its_id():
    response = sample("video")
    response["aweme_detail"]["desc"] = "#AI #大模型"

    fetched = adapter(ReplayClient(response)).fetch(VIDEO)

    assert fetched.title == VIDEO.platform_id


def test_deleted_post_is_reported_unavailable_with_douyins_reason():
    client = ReplayClient(sample("deleted"))

    with pytest.raises(SourceUnavailable, match="因作品权限或已被删除，无法观看"):
        adapter(client).fetch(DELETED)


def test_post_marked_deleted_in_its_status_is_unavailable():
    response = sample("video")
    response["aweme_detail"]["status"]["is_delete"] = True

    with pytest.raises(SourceUnavailable, match="已删除"):
        adapter(ReplayClient(response)).fetch(VIDEO)


def test_empty_response_says_to_log_in_again_or_that_the_adapter_may_be_broken():
    """cookie 或签名失效时抖音只回空响应：分不清是哪一种，两种出路都告诉用户。"""
    client = ReplayClient(response=None)

    with pytest.raises(FetchFailed) as raised:
        adapter(client).fetch(VIDEO)

    message = str(raised.value)
    assert "sub2obsidian login douyin" in message
    assert "F2" in message
    assert "ADR-0003" in message


def test_detail_without_post_or_reason_points_at_the_adapter():
    response = {"status_code": 0, "aweme_detail": None, "log_pb": {"impr_id": "x"}}

    with pytest.raises(FetchFailed, match="适配器可能已失效"):
        adapter(ReplayClient(response)).fetch(VIDEO)


def test_error_status_code_is_a_retryable_failure_with_douyins_message():
    response = {"status_code": 2053, "status_msg": "请求太频繁，请稍后再试"}

    with pytest.raises(FetchFailed, match="请求太频繁，请稍后再试"):
        adapter(ReplayClient(response)).fetch(VIDEO)


def test_not_logged_in_asks_to_log_in_before_any_request():
    client = ReplayClient()

    with pytest.raises(LoginRequired, match="请重新登录 抖音"):
        adapter(client, Credentials(logged_in=False)).fetch(VIDEO)

    assert client.details == []


def test_download_audio_uses_the_smallest_rendition_with_its_fallback_urls(tmp_path: Path):
    """转写只要音轨：下载码率最低的那一档，省流量。"""
    client = ReplayClient(sample("video"))

    audio = adapter(client).download_audio(VIDEO, tmp_path)

    assert audio.parent == tmp_path
    gears = sample("video")["aweme_detail"]["video"]["bit_rate"]
    smallest = min(gears, key=lambda gear: gear["bit_rate"])
    assert client.audio_requests == [smallest["play_addr"]["url_list"]]
    assert client.details == [(VIDEO.platform_id, COOKIE)]


def test_download_audio_skips_video_only_dash_renditions(tmp_path: Path):
    """真实作品详情里码率最低的往往是 dash 档：只有画面没有音轨，ffmpeg 提不出音频。"""
    response = sample("video")
    gears = response["aweme_detail"]["video"]["bit_rate"]
    for gear in gears:
        gear["format"] = "mp4"
    smallest_mp4 = min(gears, key=lambda gear: gear["bit_rate"])
    gears.append(
        {
            "gear_name": "540_2_1",
            "bit_rate": 1,
            "format": "dash",
            "play_addr": {"url_list": ["https://v11-weba.douyinvod.com/fake/media-video-hvc1/"]},
        }
    )
    client = ReplayClient(response)

    adapter(client).download_audio(VIDEO, tmp_path)

    assert client.audio_requests == [smallest_mp4["play_addr"]["url_list"]]


def test_download_audio_without_renditions_uses_play_addr(tmp_path: Path):
    response = sample("video")
    response["aweme_detail"]["video"]["bit_rate"] = []
    client = ReplayClient(response)

    adapter(client).download_audio(VIDEO, tmp_path)

    assert client.audio_requests == [response["aweme_detail"]["video"]["play_addr"]["url_list"]]


def test_download_audio_of_deleted_post_is_unavailable(tmp_path: Path):
    with pytest.raises(SourceUnavailable):
        adapter(ReplayClient(sample("deleted"))).download_audio(DELETED, tmp_path)


def test_download_audio_of_note_is_refused(tmp_path: Path):
    client = ReplayClient(sample("note"))

    with pytest.raises(FetchFailed, match="图文"):
        adapter(client).download_audio(NOTE, tmp_path)

    assert client.audio_requests == []


def test_short_link_is_expanded_through_its_redirect():
    short = "https://v.douyin.com/iRNBho6u/"
    target = "https://www.iesdouyin.com/share/video/7412345678901234567/?region=CN"
    client = ReplayClient(redirects={short: target})

    assert adapter(client).expand_short_link(short) == target


# ---- 列出收藏（F2 的 collection / collects 模式）----

AI_FOLDER = "7300000000000000001"


def test_douyin_adapter_supports_pulling_favorites():
    assert isinstance(adapter(ReplayClient()), FavoritesAdapter)


def test_favorite_lists_are_all_favorites_then_every_folder():
    client = ReplayClient(pages={("collects", None, 0): listing("collects_list")})

    lists = adapter(client).favorite_lists()

    assert lists == [
        FavoriteList("collection", "全部收藏"),
        FavoriteList(f"collects-{AI_FOLDER}", "收藏夹：AI 学习"),
        FavoriteList("collects-7300000000000000002", "收藏夹：理财"),
    ]
    assert client.list_requests == [("collects", None, 0, PAGE_SIZE, COOKIE)]


def test_favorite_folders_spanning_several_pages_are_all_listed():
    first = listing("collects_list")
    second = copy.deepcopy(first)
    first["collects_list"] = first["collects_list"][:1]
    first["has_more"], first["cursor"] = True, 1
    second["collects_list"] = second["collects_list"][1:]
    client = ReplayClient(pages={("collects", None, 0): first, ("collects", None, 1): second})

    lists = adapter(client).favorite_lists()

    assert [favorite_list.title for favorite_list in lists] == [
        "全部收藏",
        "收藏夹：AI 学习",
        "收藏夹：理财",
    ]


def test_all_favorites_page_maps_each_post_to_favorite_metadata():
    client = ReplayClient(pages={("collection", None, 0): listing("collection_page")})

    page = adapter(client).favorites("collection", None)

    video, note, deleted = page.items
    assert video == Favorite(
        ref=VIDEO,
        kind="视频",
        title="三分钟讲清楚 MCP 是什么",
        author="某AI博主",
        published="2024-09-10T19:30:00+08:00",
        duration=183,
        description="三分钟讲清楚 MCP 是什么 #AI #大模型 #MCP",
    )
    assert note.ref == NOTE  # 图文的规范链接是 /note/
    assert note.kind == "图文"
    assert note.title == "RAG 入门笔记"
    assert note.duration is None
    assert note.published == "2024-10-01T08:00:00+08:00"
    assert note.unavailable is None
    assert deleted.ref.platform_id == "7445678901234567890"
    assert deleted.unavailable == "作品已删除"
    assert page.next == "1727712000123456"
    # 只取元数据：不下载封面、图片，也不请求作品详情
    assert client.downloads == [] and client.details == []
    assert client.list_requests == [("collection", None, 0, PAGE_SIZE, COOKIE)]


def test_folder_page_is_read_from_the_cursor_and_its_last_page_has_no_next():
    cursor = 1727712000123456
    client = ReplayClient(pages={("collects_video", AI_FOLDER, cursor): listing("collects_video_page")})

    page = adapter(client).favorites(f"collects-{AI_FOLDER}", str(cursor))

    [item] = page.items
    assert item.ref == SourceRef(
        "douyin", "7456789012345678901", "https://www.douyin.com/video/7456789012345678901"
    )
    assert item.title == "Agent 工作流拆解"
    assert item.duration == 95
    assert page.next is None
    assert client.list_requests == [("collects_video", AI_FOLDER, cursor, PAGE_SIZE, COOKIE)]


def test_empty_favorites_page_is_a_last_page():
    response = {"status_code": 0, "aweme_list": None, "cursor": 0, "has_more": 0}
    client = ReplayClient(pages={("collection", None, 0): response})

    page = adapter(client).favorites("collection", None)

    assert (page.items, page.next) == ([], None)


def test_favorites_empty_response_says_to_log_in_again_or_upgrade_f2():
    """cookie 或签名失效时抖音只回空响应：停止本平台的回填，两种出路都告诉用户。"""
    client = ReplayClient(pages={("collection", None, 0): None})

    with pytest.raises(FetchFailed) as raised:
        adapter(client).favorites("collection", None)

    message = str(raised.value)
    assert "sub2obsidian login douyin" in message
    assert "F2" in message
    assert "ADR-0003" in message


def test_favorite_lists_empty_response_is_a_retryable_failure():
    client = ReplayClient(pages={("collects", None, 0): None})

    with pytest.raises(FetchFailed, match="sub2obsidian login douyin"):
        adapter(client).favorite_lists()


def test_favorites_error_status_code_is_a_retryable_failure_with_douyins_message():
    response = {"status_code": 2053, "status_msg": "请求太频繁，请稍后再试"}
    client = ReplayClient(pages={("collection", None, 0): response})

    with pytest.raises(FetchFailed, match="请求太频繁，请稍后再试"):
        adapter(client).favorites("collection", None)


def test_favorites_page_whose_cursor_does_not_move_points_at_the_adapter():
    """游标不前进时再读只会得到同一页：报适配器可能失效，而不是把列表当作读完。"""
    cursor = 1727712000123456
    response = listing("collection_page")
    response["cursor"] = cursor
    client = ReplayClient(pages={("collection", None, cursor): response})

    with pytest.raises(FetchFailed, match="适配器可能已失效"):
        adapter(client).favorites("collection", str(cursor))


def test_listing_favorites_without_login_asks_to_log_in_before_any_request():
    client = ReplayClient()

    with pytest.raises(LoginRequired, match="请重新登录 抖音"):
        adapter(client, Credentials(logged_in=False)).favorite_lists()
    with pytest.raises(LoginRequired):
        adapter(client, Credentials(logged_in=False)).favorites("collection", None)

    assert client.list_requests == []
