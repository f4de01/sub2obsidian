"""已失效：只有在拿到可编译的原始材料之前就在平台上消失的来源才标为已失效。

原始材料已经拿到的来源（有正文的文章、有口播稿的视频），之后在平台上删除也照常编译，
不再变为已失效；已采集、还没转写的视频在转写时下载不到音频，才转为已失效、不能编译。
"""

from pathlib import Path

import pytest

from raw import read_metadata, source_dir
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import Article, Asset, FetchedSource, Favorite
from sub2obsidian.transcript import Segment, Transcript

BV = "BV1GJ411x7h7"
BV_LINK = f"https://www.bilibili.com/video/{BV}"
WX_ID = "3888064333_2247499360_1"
WX_LINK = (
    "https://mp.weixin.qq.com/s?__biz=Mzg4ODA2NDMzMw==&mid=2247499360&idx=1"
    "&sn=7f578d217699fabba9d56e29354ce065"
)
SUBTITLES = Transcript(origin="B站 AI 字幕（ai-zh）", segments=[Segment(0.0, 2.0, "大家好")])


def video(transcript: Transcript | None) -> FetchedSource:
    return FetchedSource(
        kind="视频",
        title="RAG 到底是什么",
        author="某知识区UP主",
        published="2024-05-01T20:00:00+08:00",
        duration=612,
        cover=Asset(name="封面.jpg", data=b"\xff\xd8 fake jpeg"),
        transcript=transcript,
    )


def article() -> FetchedSource:
    return FetchedSource(
        kind="文章",
        title="通过增强PDF结构识别，革新检索增强生成技术(RAG)",
        author="北京庖丁科技",
        published="2024-01-31T14:37:04+08:00",
        article=Article(markdown="检索增强生成（RAG）可以更好地利用领域专家知识。\n", images=[]),
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
    bilibili.videos[BV] = video(SUBTITLES)
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


def test_collected_video_whose_audio_is_gone_at_transcription_becomes_unavailable_and_not_compilable(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    bilibili.videos[BV] = video(None)  # 没有平台字幕：停在「已采集」等转写
    run.run("capture", BV_LINK)
    bilibili.audio_unavailable[BV] = "稿件不可见（62002）"

    transcribed = run.run("transcribe")

    assert transcribed.exit_code == 0, transcribed.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已失效"
    assert meta["失败原因"] == "稿件不可见（62002）"
    assert not (source_dir(vault, "bilibili", BV) / "口播稿.md").exists()
    assert transcriber.calls == []
    assert "可编译的来源：无" in run.run("status").output
    refused = run.run("mark-compiled", f"bilibili/{BV}")
    assert refused.exit_code != 0
    assert "不可编译" in refused.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已失效"
