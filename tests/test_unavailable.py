"""已失效：只有在拿到可编译的原始材料之前就在平台上消失的来源才标为已失效（#15）。

原始材料已经拿到的来源（有正文的文章、有口播稿的视频），之后在平台上删除、又被推送或
出现在拉取的收藏里，也照常编译，不再变为已失效。另一面——已采集、还没转写的视频在转写时
下载不到音频，转为已失效、不能编译——见 test_transcribe.py。状态机本身禁止这类来源转为
已失效，见 test_source_repository.py。
"""

from pathlib import Path

import pytest

from raw import read_metadata, source_dir
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import Article, Asset, Favorite, FetchedSource
from sub2obsidian.transcript import Segment, Transcript

BV = "BV1GJ411x7h7"
BV_LINK = f"https://www.bilibili.com/video/{BV}"
WX_ID = "3000000001_2247480001_1"
WX_LINK = (
    "https://mp.weixin.qq.com/s?__biz=MzAwMDAwMDAwMQ==&mid=2247480001&idx=1"
    "&sn=0123456789abcdef0123456789abcdef"
)
SUBTITLES = Transcript(origin="B站 AI 字幕（ai-zh）", segments=[Segment(0.0, 2.0, "大家好")])


def subtitled_video() -> FetchedSource:
    """有平台字幕的视频：采集后即有口播稿。"""
    return FetchedSource(
        kind="视频",
        title="RAG 到底是什么",
        author="某知识区UP主",
        published="2024-05-01T20:00:00+08:00",
        duration=612,
        cover=Asset(name="封面.jpg", data=b"\xff\xd8 fake jpeg"),
        transcript=SUBTITLES,
    )


def article() -> FetchedSource:
    return FetchedSource(
        kind="文章",
        title="示例主题：给阳台菜园做一份浇水日志",
        author="示例园艺笔记",
        published="2024-06-01T08:00:00+08:00",
        article=Article(markdown="浇水日志可以帮助掌握每盆植物的需水规律。\n", images=[]),
    )


def deleted_favorite() -> Favorite:
    """收藏夹里这条视频已显示为失效。"""
    return Favorite(
        ref=SourceRef("bilibili", BV, BV_LINK),
        kind="视频",
        title="已失效视频",
        unavailable="收藏夹中显示为已失效视频",
    )


@pytest.fixture
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    credentials.login("douyin")  # sync 也会拉取抖音收藏
    return vault


def compilable_section(output: str) -> str:
    return output.partition("可编译的来源")[2]


def test_article_with_body_stays_compilable_after_it_is_deleted_on_the_platform(
    run, wechat, inbox, initialized
):
    vault = initialized
    wechat.articles[WX_ID] = article()
    run.run("capture", WX_LINK)
    wechat.unavailable[WX_ID] = "该内容已被发布者删除"

    # 文章删除之后，用户又推送了一次
    recaptured = run.run("capture", WX_LINK)
    inbox.push(WX_LINK)
    synced = run.run("sync")

    assert recaptured.exit_code == 0, recaptured.output
    assert synced.exit_code == 0, synced.output
    meta = read_metadata(vault, "wechat", WX_ID)
    assert meta["来源状态"] == "已采集"
    assert meta["失败原因"] is None
    assert (source_dir(vault, "wechat", WX_ID) / "正文.md").exists()
    assert f"wechat/{WX_ID}" in compilable_section(run.run("status").output)
    compiled = run.run("mark-compiled", f"wechat/{WX_ID}")
    assert compiled.exit_code == 0, compiled.output
    assert read_metadata(vault, "wechat", WX_ID)["来源状态"] == "已编译"


def test_video_with_transcript_stays_compilable_and_compiled_after_it_is_deleted_on_the_platform(
    run, bilibili, inbox, initialized
):
    vault = initialized
    bilibili.videos[BV] = subtitled_video()
    run.run("capture", BV_LINK)
    bilibili.unavailable[BV] = "稿件不可见（62002）"
    bilibili.audio_unavailable[BV] = "稿件不可见（62002）"
    bilibili.favorite_folders["默认收藏夹"] = [deleted_favorite()]

    # 视频删除之后：收藏夹里显示为失效，用户又推送了一次
    inbox.push(BV_LINK)
    synced = run.run("sync")
    recaptured = run.run("capture", BV_LINK)

    assert synced.exit_code == 0, synced.output
    assert recaptured.exit_code == 0, recaptured.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert meta["失败原因"] is None
    assert (source_dir(vault, "bilibili", BV) / "口播稿.md").exists()
    assert f"bilibili/{BV}" in compilable_section(run.run("status").output)

    compiled = run.run("mark-compiled", f"bilibili/{BV}")
    resynced = run.run("sync")

    assert compiled.exit_code == 0, compiled.output
    assert resynced.exit_code == 0, resynced.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已编译"
