"""抖音收藏的回填：sync 一次性分批拉取抖音收藏（含收藏夹）的元数据 → 待筛清单 → screen →
已通过的视频与图文经 sync 采集，视频转写。

抖音适配器是假实现（见 conftest.py）；真实适配器「列出收藏」的契约测试见
test_douyin_adapter.py。真实账号的回填放到 #12。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raw import read_metadata, source_dir, source_dirs
from test_backfill import favorite as bilibili_favorite
from test_backfill import bv, screening_list, tick
from test_capture_douyin import ASR, NOTE_ID, NOTE_TITLE, VIDEO_ID, VIDEO_TITLE, douyin_note, douyin_video
from vault_git import git
from sub2obsidian.douyin import EMPTY_RESPONSE, F2_HINT
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import Favorite, FetchFailed
from sub2obsidian.tools import MissingTool

COLLECTION = "收藏"


def aweme(n: int) -> str:
    return f"74{n:017d}"


def favorite(aweme_id: str, kind: str = "视频", **overrides) -> Favorite:
    path = "note" if kind == "图文" else "video"
    fields = dict(
        ref=SourceRef("douyin", aweme_id, f"https://www.douyin.com/{path}/{aweme_id}"),
        kind=kind,
        title=f"抖音作品 {aweme_id}",
        author="某AI博主",
        published="2024-09-10T19:30:00+08:00",
        duration=183 if kind == "视频" else None,
        description=f"作品 {aweme_id} 的文案 #AI",
    )
    fields.update(overrides)
    return Favorite(**fields)


def video_favorite() -> Favorite:
    return favorite(VIDEO_ID, title=VIDEO_TITLE, description="三分钟讲清楚 MCP 是什么 #AI #大模型")


def note_favorite() -> Favorite:
    return favorite(NOTE_ID, kind="图文", title=NOTE_TITLE, description="RAG 入门笔记")


@pytest.fixture
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    credentials.login("douyin")
    return vault


def test_sync_backfills_douyin_favorites_into_the_screening_list_with_metadata_only(
    run, douyin, initialized
):
    vault = initialized
    douyin.favorite_folders[COLLECTION] = [video_favorite(), note_favorite()]

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [
        source_dir(vault, "douyin", VIDEO_ID),
        source_dir(vault, "douyin", NOTE_ID),
    ]
    video = read_metadata(vault, "douyin", VIDEO_ID)
    assert video["来源状态"] == "待筛"
    assert video["采集途径"] == "拉取"
    assert video["类型"] == "视频"
    assert video["标题"] == VIDEO_TITLE
    assert video["时长"] == 183
    note = read_metadata(vault, "douyin", NOTE_ID)
    assert note["来源状态"] == "待筛"
    assert note["类型"] == "图文"
    assert note["规范链接"] == f"https://www.douyin.com/note/{NOTE_ID}"
    # 只抓元数据：不采集、不下载图片与音频
    assert douyin.fetched == []
    assert [p.name for p in source_dir(vault, "douyin", NOTE_ID).iterdir()] == ["元数据.md"]
    text = screening_list(vault)
    assert "## 抖音（2）" in text
    assert f"- [ ] {VIDEO_TITLE} `douyin/{VIDEO_ID}`" in text
    assert f"- [ ] {NOTE_TITLE} `douyin/{NOTE_ID}`" in text
    assert f"[原链接](https://www.douyin.com/video/{VIDEO_ID})" in text
    assert "新增来源 2" in result.output
    assert git(vault, "status", "--porcelain") == ""


def test_douyin_video_and_note_approved_by_screen_are_collected_by_sync_and_the_video_transcribed(
    run, douyin, transcriber, initialized
):
    vault = initialized
    rejected = favorite(aweme(3), title="搞笑段子")
    douyin.favorite_folders[COLLECTION] = [video_favorite(), note_favorite(), rejected]
    run.run("sync")
    tick(vault, VIDEO_ID, NOTE_ID)
    screened = run.run("screen")
    assert screened.exit_code == 0, screened.output
    douyin.posts[VIDEO_ID] = douyin_video()
    douyin.posts[NOTE_ID] = douyin_note()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    video = read_metadata(vault, "douyin", VIDEO_ID)
    assert video["来源状态"] == "已转写"
    assert video["采集途径"] == "拉取"
    assert (source_dir(vault, "douyin", VIDEO_ID) / "口播稿.md").is_file()
    assert (source_dir(vault, "douyin", VIDEO_ID) / "封面.jpg").is_file()
    note = read_metadata(vault, "douyin", NOTE_ID)
    assert note["来源状态"] == "已采集"
    note_dir = source_dir(vault, "douyin", NOTE_ID)
    assert (note_dir / "正文.md").is_file()
    assert (note_dir / "图01.jpg").read_bytes() == b"image-1"
    assert not (note_dir / "口播稿.md").exists()
    assert read_metadata(vault, "douyin", aweme(3))["来源状态"] == "已拒绝"
    assert sorted(douyin.fetched) == sorted([VIDEO_ID, NOTE_ID])  # 已拒绝的不采集
    assert [content for content, _ in transcriber.calls] == [f"audio:{VIDEO_ID}"]
    assert all(not audio.exists() for audio in douyin.audio_files)  # 临时音频已删除
    assert git(vault, "status", "--porcelain") == ""


def offer_pushed_article(wechat, inbox) -> str:
    """收件箱里有一条推送来的公众号文章链接；返回它的平台内 ID。"""
    from test_sync import WX_ID, WX_LINK, article

    wechat.articles[WX_ID] = article()
    inbox.push(WX_LINK)
    return WX_ID


def not_logged_in(douyin, credentials) -> str:
    credentials.logged_in.discard("douyin")
    return "请重新登录 抖音：sub2obsidian login douyin"


def signature_broken(douyin, credentials) -> str:
    douyin.lists_failure = FetchFailed(EMPTY_RESPONSE)
    return "抖音接口返回空响应"


def f2_missing(douyin, credentials) -> str:
    douyin.lists_failure = MissingTool(F2_HINT)
    return "未安装 F2"


@pytest.mark.parametrize("break_douyin", [not_logged_in, signature_broken, f2_missing])
def test_douyin_pull_failure_leaves_bilibili_backfill_and_the_inbox_unaffected(
    run, douyin, bilibili, wechat, inbox, credentials, initialized, break_douyin
):
    vault = initialized
    douyin.favorite_folders[COLLECTION] = [video_favorite()]
    bilibili.favorite_folders["默认收藏夹"] = [bilibili_favorite(1)]
    article_id = offer_pushed_article(wechat, inbox)
    reason = break_douyin(douyin, credentials)

    result = run.run("sync")

    assert result.exit_code == 1, result.output
    assert f"拉取 抖音 收藏失败：{reason}" in result.output
    assert not source_dir(vault, "douyin", VIDEO_ID).exists()
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "待筛"
    assert f"`bilibili/{bv(1)}`" in screening_list(vault)
    assert read_metadata(vault, "wechat", article_id)["来源状态"] == "已采集"
    assert git(vault, "status", "--porcelain") == ""


def test_douyin_backfill_interrupted_by_a_failure_resumes_from_the_checkpoint(
    run, douyin, initialized
):
    vault = initialized
    douyin.favorite_folders[COLLECTION] = [favorite(aweme(n)) for n in range(1, 8)]
    douyin.page_failures[(COLLECTION, "2")] = FetchFailed(EMPTY_RESPONSE)

    failed = run.run("sync")

    assert failed.exit_code == 1
    assert "拉取 抖音 收藏失败：抖音接口返回空响应" in failed.output
    assert "sub2obsidian login douyin" in failed.output
    assert len(source_dirs(vault)) == 6
    douyin.pages_read.clear()

    resumed = run.run("sync")

    assert resumed.exit_code == 0, resumed.output
    assert douyin.pages_read == [(COLLECTION, "2")]
    assert source_dirs(vault) == [source_dir(vault, "douyin", aweme(n)) for n in range(1, 8)]


def test_douyin_backfill_is_one_time_new_douyin_favorites_come_by_push(
    run, douyin, bilibili, initialized
):
    """抖音存量一次性拉取，日常增量由用户推送：回填完成后 sync 不再请求抖音收藏。"""
    vault = initialized
    douyin.favorite_folders[COLLECTION] = [favorite(aweme(n)) for n in range(1, 5)]
    douyin.favorite_folders["AI 学习"] = [favorite(aweme(9))]
    bilibili.favorite_folders["默认收藏夹"] = [bilibili_favorite(1)]
    backfilled = run.run("sync")
    assert "抖音 回填完成" in backfilled.output and "分享到收件箱" in backfilled.output
    assert len(source_dirs(vault)) == 6
    douyin.pages_read.clear()
    douyin.lists_read = 0
    douyin.favorite_folders[COLLECTION].insert(0, favorite(aweme(5)))
    bilibili.favorite_folders["默认收藏夹"].insert(0, bilibili_favorite(2))

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert (douyin.lists_read, douyin.pages_read) == (0, [])
    assert not source_dir(vault, "douyin", aweme(5)).exists()
    assert read_metadata(vault, "bilibili", bv(2))["来源状态"] == "待筛"  # B站 照常拉取增量
