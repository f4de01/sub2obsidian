"""转写：`transcribe` 为所有「已采集」的视频来源下载音频、用转写引擎生成口播稿。

转写引擎与平台适配器在这里都是假实现（见 conftest.py）；真实的 faster-whisper 见
test_faster_whisper.py。
"""

from pathlib import Path

import pytest

from raw import read_metadata, source_dir
from vault_git import commit_subjects, git
from sub2obsidian.platforms import Asset, FetchedSource
from sub2obsidian.tools import FFMPEG_HINT, MissingTool
from sub2obsidian.transcript import Segment, Transcript

BV = "BV1GJ411x7h7"
CANONICAL = f"https://www.bilibili.com/video/{BV}"
TITLE = "【大模型】RAG 到底是什么？10 分钟讲清楚"

ASR_SEGMENTS = [
    Segment(start=0.0, end=2.6, text=" 大家好，今天聊 RAG"),
    Segment(start=2.6, end=6.1, text="也就是检索增强生成 "),
    Segment(start=3725.4, end=3730.0, text="一个多小时后的结尾"),
]


def video(transcript: Transcript | None = None, title: str = TITLE) -> FetchedSource:
    return FetchedSource(
        kind="视频",
        title=title,
        author="某知识区UP主",
        published="2024-05-01T20:00:00+08:00",
        duration=612,
        cover=Asset(name="封面.jpg", data=b"\xff\xd8 fake jpeg"),
        transcript=transcript,
    )


@pytest.fixture
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    return vault


def capture_without_subtitles(run, bilibili, transcriber, bv: str = BV, title: str = TITLE) -> None:
    bilibili.videos[bv] = video(title=title)
    transcriber.segments[f"audio:{bv}"] = ASR_SEGMENTS
    result = run.run("capture", f"https://www.bilibili.com/video/{bv}")
    assert result.exit_code == 0, result.output


def test_video_without_subtitles_is_collected_then_transcribed_in_platform_subtitle_format(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已采集"

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert meta["失败原因"] is None
    transcript = (source_dir(vault, "bilibili", BV) / "口播稿.md").read_text(encoding="utf-8")
    assert transcript == (
        "---\n"
        "口播稿来源: 假转写引擎 v1\n"
        "---\n"
        f"# 口播稿：{TITLE}\n"
        "\n"
        "[00:00:00] 大家好，今天聊 RAG\n"
        "[00:00:02] 也就是检索增强生成\n"
        "[01:02:05] 一个多小时后的结尾\n"
    )
    assert "已转写" in result.output


def test_video_with_platform_subtitles_is_never_sent_to_asr(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    subtitles = Transcript("B站 CC 字幕（zh-CN）", [Segment(0.0, 2.0, "平台字幕")])
    bilibili.videos[BV] = video(transcript=subtitles)
    run.run("capture", CANONICAL)
    before = (source_dir(vault, "bilibili", BV) / "口播稿.md").read_bytes()

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    assert bilibili.audio_files == []
    assert transcriber.calls == []
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"
    assert (source_dir(vault, "bilibili", BV) / "口播稿.md").read_bytes() == before


def test_temporary_audio_is_deleted_and_no_audio_lands_in_raw_material(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)

    run.run("transcribe")

    [audio] = bilibili.audio_files
    assert not audio.exists()
    assert not audio.parent.exists()
    assert not audio.is_relative_to(vault)
    assert sorted(p.name for p in source_dir(vault, "bilibili", BV).iterdir()) == [
        "元数据.md",
        "口播稿.md",
        "封面.jpg",
    ]


@pytest.mark.parametrize(
    ("setting", "terms"),
    [
        pytest.param('terms = ["MCP", "RAG", "检索增强生成"]', ["MCP", "RAG", "检索增强生成"], id="术语列表"),
        pytest.param('terms = "MCP"', ["MCP"], id="单个术语"),
    ],
)
def test_glossary_from_user_config_is_passed_to_the_engine_as_hints(
    run, bilibili, transcriber, initialized, user_config_dir: Path, setting: str, terms: list[str]
):
    config = user_config_dir / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + f"\n[transcribe]\n{setting}\n", encoding="utf-8"
    )
    capture_without_subtitles(run, bilibili, transcriber)

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    assert transcriber.calls == [(f"audio:{BV}", terms)]


def test_without_glossary_the_engine_gets_no_hints(run, bilibili, transcriber, initialized):
    capture_without_subtitles(run, bilibili, transcriber)

    run.run("transcribe")

    assert transcriber.calls == [(f"audio:{BV}", [])]


OTHER = "BV1Ab411c7De"


@pytest.mark.parametrize(
    "fail",
    [
        pytest.param(
            lambda bilibili, transcriber: bilibili.audio_failures.update(
                {BV: "音频下载失败：请求过于频繁（412）"}
            ),
            id="音频下载失败",
        ),
        pytest.param(
            lambda bilibili, transcriber: transcriber.failures.update(
                {f"audio:{BV}": "音频解码失败：文件已损坏"}
            ),
            id="转写失败",
        ),
    ],
)
def test_one_failed_source_keeps_its_status_with_reason_and_does_not_block_the_batch(
    run, bilibili, transcriber, initialized, fail
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber, BV)
    capture_without_subtitles(run, bilibili, transcriber, OTHER, title="另一个视频")
    fail(bilibili, transcriber)

    result = run.run("transcribe")

    assert result.exit_code != 0
    failed = read_metadata(vault, "bilibili", BV)
    assert failed["来源状态"] == "已采集"
    assert failed["失败原因"] in result.output
    assert failed["失败原因"] in ("音频下载失败：请求过于频繁（412）", "音频解码失败：文件已损坏")
    assert not (source_dir(vault, "bilibili", BV) / "口播稿.md").exists()
    assert read_metadata(vault, "bilibili", OTHER)["来源状态"] == "已转写"
    assert all(not audio.exists() for audio in bilibili.audio_files)


def test_failed_source_is_retried_on_the_next_run_and_reason_cleared(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    bilibili.audio_failures[BV] = "网络超时"
    run.run("transcribe")

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert meta["失败原因"] is None


def test_video_gone_from_platform_becomes_unavailable_keeping_collected_material_not_compilable(
    run, bilibili, transcriber, initialized
):
    """还没转写的视频没有可编译的原始材料：下载不到音频就转为已失效，不能编译（#15）。"""
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    bilibili.audio_unavailable[BV] = "稿件不可见（62002）"

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    assert "已失效" in result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已失效"
    assert meta["失败原因"] == "稿件不可见（62002）"
    assert (source_dir(vault, "bilibili", BV) / "封面.jpg").exists()
    assert not (source_dir(vault, "bilibili", BV) / "口播稿.md").exists()
    assert transcriber.calls == []
    assert "可编译的来源：无" in run.run("status").output
    refused = run.run("mark-compiled", f"bilibili/{BV}")
    assert refused.exit_code != 0
    assert "不可编译" in refused.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已失效"


def test_missing_ffmpeg_stops_the_batch_with_a_clear_error_and_changes_nothing(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber, BV)
    capture_without_subtitles(run, bilibili, transcriber, OTHER, title="另一个视频")
    head = commit_subjects(vault)[0]

    def no_ffmpeg(ref, directory):
        raise MissingTool(FFMPEG_HINT)

    bilibili.download_audio = no_ffmpeg

    result = run.run("transcribe")

    assert result.exit_code != 0
    assert "未找到 ffmpeg" in result.output
    assert "winget install --id Gyan.FFmpeg" in result.output
    for bv in (BV, OTHER):
        meta = read_metadata(vault, "bilibili", bv)
        assert meta["来源状态"] == "已采集"
        assert meta["失败原因"] is None
    assert commit_subjects(vault)[0] == head
    assert transcriber.calls == []


def test_transcribe_commits_raw_material_changes_on_their_own(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    (vault / "Wiki" / "概念" / "草稿.md").write_text("未提交的 Wiki 改动", encoding="utf-8")

    run.run("transcribe")

    assert commit_subjects(vault)[0] == f"transcribe: B站 {BV} {TITLE}"
    committed = git(vault, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == [
        f"原始材料/bilibili/{BV}/元数据.md",
        f"原始材料/bilibili/{BV}/口播稿.md",
    ]
    assert git(vault, "status", "--porcelain").strip() == "?? Wiki/"


def test_transcribe_reports_a_summary(run, bilibili, transcriber, initialized):
    capture_without_subtitles(run, bilibili, transcriber, BV)
    capture_without_subtitles(run, bilibili, transcriber, OTHER, title="另一个视频")
    transcriber.failures[f"audio:{OTHER}"] = "音频解码失败"

    result = run.run("transcribe")

    assert "转写 2 个来源：已转写 1，失败 1" in result.output


def test_transcribe_with_nothing_to_do_says_so(run, initialized):
    vault = initialized
    head = commit_subjects(vault)[0]

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    assert "没有待转写的来源" in result.output
    assert commit_subjects(vault)[0] == head


def test_transcribe_requires_an_initialized_vault(run, vault: Path):
    result = run.run("transcribe", "--vault", str(vault))

    assert result.exit_code != 0
    assert "请先执行 sub2obsidian init" in result.output


def test_sources_transcribed_before_the_batch_stopped_are_kept_and_committed(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber, OTHER, title="另一个视频")
    capture_without_subtitles(run, bilibili, transcriber, BV)  # 按平台内 ID 排在 OTHER 之后
    download = bilibili.download_audio

    def ffmpeg_vanishes_after_first(ref, directory):
        if ref.platform_id == BV:
            raise MissingTool(FFMPEG_HINT)
        return download(ref, directory)

    bilibili.download_audio = ffmpeg_vanishes_after_first

    result = run.run("transcribe")

    assert result.exit_code != 0
    assert read_metadata(vault, "bilibili", OTHER)["来源状态"] == "已转写"
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已采集"
    assert commit_subjects(vault)[0] == f"transcribe: B站 {OTHER} 另一个视频"


def test_existing_different_transcript_fails_only_that_source(
    run, bilibili, transcriber, initialized
):
    """原始材料只增不改：已有一份不同的口播稿时不覆盖，记为该来源的失败。"""
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber, BV)
    capture_without_subtitles(run, bilibili, transcriber, OTHER, title="另一个视频")
    existing = source_dir(vault, "bilibili", OTHER) / "口播稿.md"
    existing.write_text("上次留下的口播稿\n", encoding="utf-8")

    result = run.run("transcribe")

    assert result.exit_code != 0
    assert existing.read_text(encoding="utf-8") == "上次留下的口播稿\n"
    meta = read_metadata(vault, "bilibili", OTHER)
    assert meta["来源状态"] == "已采集"
    assert "只增不改" in meta["失败原因"]
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"


# ---- 无口播与已知幻觉句（#19） ----

HALLUCINATION = "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"


def test_video_whose_asr_output_is_only_known_hallucinations_is_marked_without_speech(
    run, bilibili, transcriber, initialized
):
    """纯音乐视频：Whisper 只吐出已知幻觉句。滤掉后口播稿为空，标记无口播，仍可编译。"""
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    transcriber.segments[f"audio:{BV}"] = [Segment(0.7, 4.0, HALLUCINATION)]

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert "已知幻觉句" in meta["无口播"]
    assert HALLUCINATION in meta["无口播"]
    transcript = (source_dir(vault, "bilibili", BV) / "口播稿.md").read_text(encoding="utf-8")
    assert transcript == f"---\n口播稿来源: 假转写引擎 v1\n---\n# 口播稿：{TITLE}\n\n"
    assert f"已转写（无口播）：{TITLE}" in result.output
    assert "转写 1 个来源：已转写 1（无口播 1）" in result.output
    assert f"bilibili/{BV}" in run.run("status").output


def test_known_hallucinations_are_filtered_out_of_a_transcript_with_speech(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    transcriber.segments[f"audio:{BV}"] = [
        Segment(0.0, 2.6, "大家好，今天聊 RAG"),
        Segment(2.6, 5.0, "字幕志愿者 杨茜茜"),
        Segment(5.0, 8.0, "也就是检索增强生成"),
        Segment(600.0, 602.0, " Thank you. Thank you! "),
    ]

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert meta["无口播"] is None
    transcript = (source_dir(vault, "bilibili", BV) / "口播稿.md").read_text(encoding="utf-8")
    assert transcript.endswith(
        "\n[00:00:00] 大家好，今天聊 RAG\n[00:00:05] 也就是检索增强生成\n"
    )


def test_a_sentence_merely_containing_a_known_hallucination_is_kept(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    transcriber.segments[f"audio:{BV}"] = [Segment(0.0, 3.0, "Thank you for joining, today we talk about RAG.")]

    run.run("transcribe")

    transcript = (source_dir(vault, "bilibili", BV) / "口播稿.md").read_text(encoding="utf-8")
    assert "[00:00:00] Thank you for joining, today we talk about RAG." in transcript
    assert read_metadata(vault, "bilibili", BV)["无口播"] is None


def test_video_with_no_asr_output_at_all_is_marked_without_speech(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    capture_without_subtitles(run, bilibili, transcriber)
    transcriber.segments[f"audio:{BV}"] = []

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert "没有识别出任何口播" in meta["无口播"]


def test_hallucinations_from_user_config_are_filtered_too(
    run, bilibili, transcriber, initialized, user_config_dir: Path
):
    vault = initialized
    config = user_config_dir / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8")
        + '\n[transcribe]\nhallucinations = ["本视频由某某赞助播出"]\n',
        encoding="utf-8",
    )
    capture_without_subtitles(run, bilibili, transcriber)
    transcriber.segments[f"audio:{BV}"] = [
        Segment(0.0, 3.0, "本视频由某某赞助播出。"),
        Segment(3.0, 5.0, "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"),
    ]

    result = run.run("transcribe")

    assert result.exit_code == 0, result.output
    without_speech = read_metadata(vault, "bilibili", BV)["无口播"]
    assert "本视频由某某赞助播出" in without_speech
    assert HALLUCINATION in without_speech


def test_douyin_video_captured_without_speech_is_marked_too(
    run, douyin, transcriber, initialized
):
    """抖音视频采集后当场转写，走同一套幻觉过滤与无口播标记。"""
    vault = initialized
    run.credentials.login("douyin")
    douyin.posts["7300000000000000001"] = video()
    transcriber.segments["audio:7300000000000000001"] = [Segment(0.0, 2.0, "谢谢观看")]

    result = run.run("capture", "https://www.douyin.com/video/7300000000000000001")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "douyin", "7300000000000000001")
    assert meta["来源状态"] == "已转写"
    assert "谢谢观看" in meta["无口播"]
