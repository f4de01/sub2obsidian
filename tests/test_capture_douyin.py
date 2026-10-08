"""推送 (Push)：`capture <抖音链接或分享口令>` 把一条抖音视频或图文作为来源落入原始材料。

抖音没有平台字幕：视频采集后当场下载音频、ASR 转写，转写完删除临时音频。
图文按文章处理：文字与全部图片，没有口播稿。
"""

from pathlib import Path

import pytest

from raw import read_metadata, source_dir, source_dirs
from vault_git import git
from sub2obsidian.platforms import Article, Asset, FetchedSource
from sub2obsidian.transcript import Segment

VIDEO_ID = "7412345678901234567"
VIDEO_LINK = f"https://www.douyin.com/video/{VIDEO_ID}"
VIDEO_TITLE = "三分钟讲清楚 MCP 是什么"
NOTE_ID = "7423456789012345678"
NOTE_LINK = f"https://www.douyin.com/note/{NOTE_ID}"
NOTE_TITLE = "RAG 入门笔记"
COVER = b"\xff\xd8\xff\xe0 fake douyin cover"
ASR = [
    Segment(start=0.0, end=2.4, text="大家好，今天聊 MCP"),
    Segment(start=65.2, end=70.0, text="它是模型的上下文协议"),
]


def douyin_video() -> FetchedSource:
    return FetchedSource(
        kind="视频",
        title=VIDEO_TITLE,
        author="某AI博主",
        published="2024-09-10T19:30:00+08:00",
        duration=183,
        description="三分钟讲清楚 MCP 是什么 #AI #大模型",
        cover=Asset(name="封面.jpg", data=COVER),
    )


def douyin_note() -> FetchedSource:
    return FetchedSource(
        kind="图文",
        title=NOTE_TITLE,
        author="某AI博主",
        published="2024-10-01T08:00:00+08:00",
        description="RAG 入门笔记\n检索增强生成分三步 #RAG",
        article=Article(
            markdown="RAG 入门笔记\n\n检索增强生成分三步 #RAG\n\n![](图01.jpg)\n\n![](图02.webp)\n",
            images=[Asset("图01.jpg", b"image-1"), Asset("图02.webp", b"image-2")],
        ),
    )


@pytest.fixture
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("douyin")
    return vault


def test_capture_douyin_video_lands_metadata_cover_and_asr_transcript(
    run, douyin, transcriber, initialized
):
    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR

    result = run.run("capture", VIDEO_LINK)

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "douyin", VIDEO_ID)
    assert meta["平台"] == "douyin"
    assert meta["平台内ID"] == VIDEO_ID
    assert meta["规范链接"] == VIDEO_LINK
    assert meta["类型"] == "视频"
    assert meta["标题"] == VIDEO_TITLE
    assert meta["作者"] == "某AI博主"
    assert meta["发布时间"] == "2024-09-10T19:30:00+08:00"
    assert meta["时长"] == 183
    assert meta["采集途径"] == "推送"
    assert meta["来源状态"] == "已转写"
    directory = source_dir(vault, "douyin", VIDEO_ID)
    assert (directory / "封面.jpg").read_bytes() == COVER
    transcript = (directory / "口播稿.md").read_text(encoding="utf-8")
    assert "口播稿来源: 假转写引擎 v1" in transcript
    assert "[00:00:00] 大家好，今天聊 MCP\n[00:01:05] 它是模型的上下文协议\n" in transcript
    assert "已转写" in result.output


def test_douyin_audio_is_deleted_after_transcription_and_never_lands(
    run, douyin, transcriber, initialized
):
    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR

    run.run("capture", VIDEO_LINK)

    [audio] = douyin.audio_files
    assert not audio.exists()
    assert vault not in audio.parents
    names = {path.name for path in source_dir(vault, "douyin", VIDEO_ID).iterdir()}
    assert names == {"元数据.md", "封面.jpg", "口播稿.md"}


def test_capture_douyin_note_lands_text_and_every_image_like_an_article(
    run, douyin, transcriber, initialized
):
    vault = initialized
    douyin.posts[NOTE_ID] = douyin_note()

    result = run.run("capture", NOTE_LINK)

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "douyin", NOTE_ID)
    assert meta["类型"] == "图文"
    assert meta["标题"] == NOTE_TITLE
    assert meta["作者"] == "某AI博主"
    assert meta["时长"] is None
    assert meta["规范链接"] == NOTE_LINK
    assert meta["来源状态"] == "已采集"
    directory = source_dir(vault, "douyin", NOTE_ID)
    assert (directory / "图01.jpg").read_bytes() == b"image-1"
    assert (directory / "图02.webp").read_bytes() == b"image-2"
    body = (directory / "正文.md").read_text(encoding="utf-8")
    assert body.startswith(f"# {NOTE_TITLE}\n")
    assert "抖音：某AI博主" in body
    assert f"[原文]({NOTE_LINK})" in body
    assert "检索增强生成分三步 #RAG" in body
    assert "![](图01.jpg)" in body
    assert "![](图02.webp)" in body
    assert not (directory / "口播稿.md").exists()
    assert douyin.audio_files == []
    assert transcriber.calls == []


def test_captured_douyin_note_is_compilable_without_transcription(run, douyin, initialized):
    douyin.posts[NOTE_ID] = douyin_note()
    run.run("capture", NOTE_LINK)

    result = run.run("status")

    assert f"douyin/{NOTE_ID}  图文  {NOTE_TITLE}" in result.output


SHORT = "https://v.douyin.com/iRNBho6u/"
SHARE_TEXT = (
    f"7.94 复制打开抖音，看看【某AI博主的作品】{VIDEO_TITLE} # AI # 大模型 {SHORT} "
    "05/03 b@a.Nw aNj:/ "
)


@pytest.mark.parametrize(
    "submission",
    [
        pytest.param(VIDEO_LINK, id="完整链接"),
        pytest.param(SHORT, id="短链"),
        pytest.param("https://v.douyin.com/iRNBho6u", id="短链不带斜杠"),
        pytest.param(SHARE_TEXT, id="分享口令文本"),
        pytest.param(f"{SHORT} 复制此链接，打开Dou音搜索，直接观看视频！", id="短链分享文本"),
        pytest.param(
            f"https://www.douyin.com/video/{VIDEO_ID}?previous_page=app_code_link&enter_from=main",
            id="带追踪参数",
        ),
        pytest.param(f"https://www.douyin.com/discover?modal_id={VIDEO_ID}", id="网页版弹窗"),
        pytest.param(
            f"https://www.douyin.com/user/MS4wLjABAAAAxyz?from_tab_name=main&modal_id={VIDEO_ID}",
            id="主页弹窗",
        ),
        pytest.param(
            f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/?region=CN&mid=7412&u_code=0",
            id="分享页",
        ),
        pytest.param(f"https://m.douyin.com/share/video/{VIDEO_ID}", id="手机版"),
        pytest.param(f"这个讲得好{VIDEO_LINK}，推荐", id="夹杂文字"),
    ],
)
def test_same_douyin_video_submitted_in_any_link_form_is_kept_once(
    run, douyin, transcriber, initialized, submission: str
):
    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR
    douyin.short_links[SHORT] = douyin.short_links[SHORT.rstrip("/")] = (
        f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/?region=CN&mid=7412"
        "&u_code=0&did=MS4wLjABAAAA&iid=MS4wLjABAAAA&with_sec_did=1"
    )
    run.run("capture", VIDEO_LINK)

    result = run.run("capture", submission)

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "douyin", VIDEO_ID)]
    assert douyin.fetched == [VIDEO_ID]
    assert read_metadata(vault, "douyin", VIDEO_ID)["规范链接"] == VIDEO_LINK
    assert "已存在" in result.output


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(f"https://www.iesdouyin.com/share/note/{NOTE_ID}/?region=CN&mid=0", id="note"),
        pytest.param(f"https://www.iesdouyin.com/share/slides/{NOTE_ID}/?region=CN", id="slides"),
    ],
)
def test_short_link_to_a_note_stores_note_canonical_link(run, douyin, initialized, target: str):
    vault = initialized
    douyin.posts[NOTE_ID] = douyin_note()
    douyin.short_links[SHORT] = target

    result = run.run("capture", f"【分享】 {SHORT}")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "douyin", NOTE_ID)["规范链接"] == NOTE_LINK


@pytest.mark.parametrize(
    "submission",
    [
        pytest.param("https://www.douyin.com/user/MS4wLjABAAAAxyz", id="用户主页"),
        pytest.param("https://www.douyin.com/video/abc", id="不是作品ID"),
        pytest.param("https://live.douyin.com/123456789", id="直播间"),
    ],
)
def test_unrecognised_douyin_link_is_reported_and_nothing_lands(
    run, douyin, initialized, submission: str
):
    vault = initialized

    result = run.run("capture", submission)

    assert result.exit_code != 0
    assert source_dirs(vault) == []
    assert douyin.fetched == []


def test_short_link_pointing_elsewhere_is_reported(run, douyin, initialized):
    vault = initialized
    douyin.short_links[SHORT] = "https://www.douyin.com/user/MS4wLjABAAAAxyz"

    result = run.run("capture", SHORT)

    assert result.exit_code != 0
    assert "抖音短链没有指向作品" in result.output
    assert source_dirs(vault) == []


def test_unavailable_douyin_post_becomes_unavailable_stub(run, douyin, transcriber, initialized):
    vault = initialized
    douyin.unavailable[VIDEO_ID] = "因作品权限或已被删除，无法观看"

    result = run.run("capture", VIDEO_LINK)

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "douyin", VIDEO_ID)
    assert meta["来源状态"] == "已失效"
    assert meta["失败原因"] == "因作品权限或已被删除，无法观看"
    assert [p.name for p in source_dir(vault, "douyin", VIDEO_ID).iterdir()] == ["元数据.md"]
    assert transcriber.calls == []

    again = run.run("capture", VIDEO_LINK)

    assert again.exit_code == 0, again.output
    assert douyin.fetched == [VIDEO_ID]


def test_capture_douyin_without_login_asks_to_log_in_again(run, douyin, vault: Path):
    run.run("init", str(vault))
    douyin.posts[VIDEO_ID] = douyin_video()

    result = run.run("capture", VIDEO_LINK)

    assert result.exit_code != 0
    assert "请重新登录 抖音：sub2obsidian login douyin" in result.output
    meta = read_metadata(vault, "douyin", VIDEO_ID)
    assert meta["来源状态"] == "已通过"


def test_failed_douyin_capture_keeps_source_approved_and_retries_later(
    run, douyin, transcriber, initialized
):
    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR
    douyin.failures[VIDEO_ID] = "抖音接口返回空响应"

    first = run.run("capture", VIDEO_LINK)

    assert first.exit_code != 0
    meta = read_metadata(vault, "douyin", VIDEO_ID)
    assert meta["来源状态"] == "已通过"
    assert meta["失败原因"] == "抖音接口返回空响应"

    second = run.run("capture", VIDEO_LINK)

    assert second.exit_code == 0, second.output
    assert read_metadata(vault, "douyin", VIDEO_ID)["来源状态"] == "已转写"


def test_failed_transcription_leaves_douyin_video_collected_for_transcribe(
    run, douyin, transcriber, initialized
):
    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.failures[f"audio:{VIDEO_ID}"] = "显存不足"

    result = run.run("capture", VIDEO_LINK)

    assert result.exit_code != 0
    meta = read_metadata(vault, "douyin", VIDEO_ID)
    assert meta["来源状态"] == "已采集"
    assert meta["失败原因"] == "显存不足"
    assert (source_dir(vault, "douyin", VIDEO_ID) / "封面.jpg").exists()

    transcriber.failures.clear()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR
    retried = run.run("transcribe")

    assert retried.exit_code == 0, retried.output
    assert read_metadata(vault, "douyin", VIDEO_ID)["来源状态"] == "已转写"


def test_douyin_capture_and_transcript_are_committed_together_on_their_own(
    run, douyin, transcriber, initialized
):
    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR

    run.run("capture", VIDEO_LINK)

    message = git(vault, "log", "-1", "--format=%s").strip()
    assert message == f"capture: 抖音 {VIDEO_ID} {VIDEO_TITLE}"
    committed = git(vault, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted(
        f"原始材料/douyin/{VIDEO_ID}/{name}" for name in ["元数据.md", "封面.jpg", "口播稿.md"]
    )


def test_capture_only_transcribes_douyin_videos_it_just_collected(
    run, douyin, bilibili, transcriber, credentials, initialized
):
    """其他平台没有字幕的视频仍停在「已采集」，等待 transcribe。"""
    from test_capture import BV, CANONICAL, video

    vault = initialized
    credentials.login("bilibili")
    bilibili.videos[BV] = video(transcript=None)
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR

    result = run.run("capture", f"{CANONICAL} {VIDEO_LINK}")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已采集"
    assert read_metadata(vault, "douyin", VIDEO_ID)["来源状态"] == "已转写"
    assert [content for content, _ in transcriber.calls] == [f"audio:{VIDEO_ID}"]


def test_capture_passes_the_glossary_to_the_engine(
    run, douyin, transcriber, initialized, user_config_dir: Path
):
    config = user_config_dir / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + '\n[transcribe]\nterms = ["MCP", "上下文协议"]\n',
        encoding="utf-8",
    )
    douyin.posts[VIDEO_ID] = douyin_video()
    transcriber.segments[f"audio:{VIDEO_ID}"] = ASR

    run.run("capture", VIDEO_LINK)

    assert transcriber.calls == [(f"audio:{VIDEO_ID}", ["MCP", "上下文协议"])]


def test_missing_ffmpeg_stops_capture_but_collected_post_is_committed(
    run, douyin, transcriber, initialized
):
    from sub2obsidian.tools import MissingTool

    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()

    def no_ffmpeg(ref, directory):
        raise MissingTool("找不到 ffmpeg")

    douyin.download_audio = no_ffmpeg

    result = run.run("capture", VIDEO_LINK)

    assert result.exit_code != 0
    assert "capture 中止：找不到 ffmpeg" in result.output
    assert read_metadata(vault, "douyin", VIDEO_ID)["来源状态"] == "已采集"
    assert git(vault, "log", "-1", "--format=%s").startswith("capture:")
    assert git(vault, "status", "--porcelain") == ""
