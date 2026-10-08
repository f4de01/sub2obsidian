"""推送 (Push)：`capture <链接或分享文本>` 把一个 B站 视频作为来源落入原始材料。"""

from pathlib import Path

import pytest

from raw import read_metadata, source_dir, source_dirs
from vault_git import commit_subjects, git
from sub2obsidian.platforms import Asset, FetchedSource
from sub2obsidian.transcript import Segment, Transcript

BV = "BV1GJ411x7h7"
CANONICAL = f"https://www.bilibili.com/video/{BV}"
COVER = b"\xff\xd8\xff\xe0 fake jpeg bytes"


def video(transcript: Transcript | None) -> FetchedSource:
    return FetchedSource(
        kind="视频",
        title="【大模型】RAG 到底是什么？10 分钟讲清楚",
        author="某知识区UP主",
        published="2024-05-01T20:00:00+08:00",
        duration=612,
        description="本期讲检索增强生成。\n第二行简介。",
        cover=Asset(name="封面.jpg", data=COVER),
        transcript=transcript,
    )


SUBTITLES = Transcript(
    origin="B站 AI 字幕（ai-zh）",
    segments=[
        Segment(start=0.5, end=3.2, text="大家好，今天聊 RAG"),
        Segment(start=3.2, end=7.9, text="也就是检索增强生成"),
        Segment(start=3725.0, end=3730.0, text="一个多小时后的结尾"),
    ],
)


@pytest.fixture
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    return vault


def test_capture_subtitled_video_lands_metadata_cover_and_transcript(run, bilibili, initialized):
    vault = initialized
    bilibili.videos[BV] = video(SUBTITLES)

    result = run.run("capture", CANONICAL)

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["平台"] == "bilibili"
    assert meta["平台内ID"] == BV
    assert meta["链接"] == CANONICAL
    assert meta["类型"] == "视频"
    assert meta["标题"] == "【大模型】RAG 到底是什么？10 分钟讲清楚"
    assert meta["作者"] == "某知识区UP主"
    assert meta["发布时间"] == "2024-05-01T20:00:00+08:00"
    assert meta["时长"] == 612
    assert meta["来源状态"] == "已转写"
    assert meta["采集途径"] == "推送"
    assert meta["采集时间"]
    assert meta["失败原因"] is None
    directory = source_dir(vault, "bilibili", BV)
    assert (directory / "封面.jpg").read_bytes() == COVER
    transcript = (directory / "口播稿.md").read_text(encoding="utf-8")
    assert "口播稿来源: B站 AI 字幕（ai-zh）" in transcript
    assert "[00:00:00] 大家好，今天聊 RAG\n" in transcript
    assert "[00:00:03] 也就是检索增强生成\n" in transcript
    assert "[01:02:05] 一个多小时后的结尾\n" in transcript
    assert "已转写" in result.output


SHORT = "https://b23.tv/AbCd123"


@pytest.mark.parametrize(
    "submission",
    [
        pytest.param(CANONICAL, id="BV链接"),
        pytest.param(f"https://www.bilibili.com/video/{BV}/", id="末尾斜杠"),
        pytest.param(
            f"https://www.bilibili.com/video/{BV}/?spm_id_from=333.1007.tianma.1-1-1.click"
            "&vd_source=0123456789abcdef0123456789abcdef",
            id="带追踪参数",
        ),
        pytest.param(
            f"https://m.bilibili.com/video/{BV}?share_source=copy_web&share_medium=android"
            "&bbid=XY0123&ts=1714567890123#reply123",
            id="移动端带参数",
        ),
        pytest.param(f"https://www.bilibili.com/video/bv{BV[2:]}", id="小写bv前缀"),
        pytest.param(SHORT, id="b23短链"),
        pytest.param(f"https://b23.tv/{BV}", id="b23直接带BV"),
        pytest.param(f"【【大模型】RAG 到底是什么？10 分钟讲清楚-哔哩哔哩】 {SHORT}", id="App分享文本"),
        pytest.param(f"这个讲得好{CANONICAL}?p=1&share_source=copy_web，推荐", id="夹杂文字"),
    ],
)
def test_same_video_submitted_in_any_link_form_is_kept_once(
    run, bilibili, initialized, submission: str
):
    vault = initialized
    bilibili.videos[BV] = video(SUBTITLES)
    bilibili.short_links[SHORT] = (
        f"https://www.bilibili.com/video/{BV}?share_medium=android&share_source=copy_link"
        "&bbid=XY0123&ts=1714567890123"
    )
    run.run("capture", CANONICAL)

    result = run.run("capture", submission)

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", BV)]
    assert bilibili.fetched == [BV]
    assert read_metadata(vault, "bilibili", BV)["链接"] == CANONICAL
    assert "已存在" in result.output


def test_first_capture_through_short_link_stores_canonical_link(run, bilibili, initialized):
    vault = initialized
    bilibili.videos[BV] = video(SUBTITLES)
    bilibili.short_links[SHORT] = f"https://www.bilibili.com/video/{BV}?share_source=copy_link"

    result = run.run("capture", f"【分享】 {SHORT}")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", BV)["链接"] == CANONICAL


def test_unavailable_video_becomes_unavailable_stub_and_is_not_retried(run, bilibili, initialized):
    vault = initialized
    bilibili.unavailable[BV] = "稿件不可见（62002）"

    first = run.run("capture", CANONICAL)
    second = run.run("capture", CANONICAL)

    assert first.exit_code == 0, first.output
    assert "已失效" in first.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已失效"
    assert meta["失败原因"] == "稿件不可见（62002）"
    assert meta["链接"] == CANONICAL
    assert sorted(p.name for p in source_dir(vault, "bilibili", BV).iterdir()) == ["元数据.md"]
    assert bilibili.fetched == [BV]  # 第二次提交不再访问平台
    assert "已存在" in second.output


def test_failed_capture_keeps_source_approved_with_reason_and_retries_later(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.videos[BV] = video(SUBTITLES)
    bilibili.failures[BV] = "请求过于频繁（412），请稍后再试"

    failed = run.run("capture", CANONICAL)

    assert failed.exit_code != 0
    assert "请求过于频繁（412）" in failed.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已通过"
    assert meta["失败原因"] == "请求过于频繁（412），请稍后再试"

    retried = run.run("capture", CANONICAL)

    assert retried.exit_code == 0, retried.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert meta["失败原因"] is None
    assert bilibili.fetched == [BV, BV]


def test_capture_without_login_asks_to_log_in_again(run, bilibili, credentials, vault: Path):
    run.run("init", str(vault))
    bilibili.videos[BV] = video(SUBTITLES)

    result = run.run("capture", CANONICAL)

    assert result.exit_code != 0
    assert "请重新登录 B站：sub2obsidian login bilibili" in result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已通过"


def test_video_without_platform_subtitles_stays_collected_awaiting_asr(run, bilibili, initialized):
    vault = initialized
    bilibili.videos[BV] = video(transcript=None)

    result = run.run("capture", CANONICAL)

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已采集"
    assert not (source_dir(vault, "bilibili", BV) / "口播稿.md").exists()


@pytest.mark.parametrize(
    "submission",
    [
        pytest.param("随便说点什么，没有链接", id="没有链接"),
        pytest.param("https://example.com/video/BV1GJ411x7h7", id="未知平台"),
        pytest.param("https://space.bilibili.com/12345", id="B站个人空间"),
        pytest.param("https://www.bilibili.com/video/av170001", id="av号"),
    ],
)
def test_unrecognised_submission_is_reported_and_nothing_lands(
    run, bilibili, initialized, submission: str
):
    vault = initialized

    result = run.run("capture", submission)

    assert result.exit_code != 0
    assert source_dirs(vault) == []
    assert bilibili.fetched == []


def test_short_link_that_fails_to_resolve_is_reported(run, bilibili, initialized):
    vault = initialized

    result = run.run("capture", "https://b23.tv/NoSuch1")

    assert result.exit_code != 0
    assert "https://b23.tv/NoSuch1" in result.output
    assert source_dirs(vault) == []


def test_one_bad_link_does_not_block_others_in_the_same_text(run, bilibili, initialized):
    vault = initialized
    bilibili.videos[BV] = video(SUBTITLES)

    result = run.run("capture", f"https://example.com/x 和 {CANONICAL}")

    assert result.exit_code != 0
    assert "无法识别的链接：https://example.com/x" in result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"


def test_capture_shell_split_share_text_is_joined(run, bilibili, initialized):
    """PowerShell 中不加引号粘贴分享文本时，各段作为多个参数传入。"""
    vault = initialized
    bilibili.videos[BV] = video(SUBTITLES)

    result = run.run("capture", "【RAG", "讲解-哔哩哔哩】", CANONICAL)

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"


def test_capture_requires_an_initialized_vault(run, bilibili, credentials, vault: Path):
    credentials.login("bilibili")
    bilibili.videos[BV] = video(SUBTITLES)

    result = run.run("capture", "--vault", str(vault), CANONICAL)

    assert result.exit_code != 0
    assert "请先执行 sub2obsidian init" in result.output
    assert not vault.exists()


def test_capture_uses_vault_option_over_user_config(run, bilibili, credentials, tmp_path: Path):
    configured, other = tmp_path / "配置的库", tmp_path / "另一个库"
    run.run("init", str(configured))
    run.run("init", str(other))
    credentials.login("bilibili")
    bilibili.videos[BV] = video(SUBTITLES)

    result = run.run("capture", "--vault", str(other), CANONICAL)

    assert result.exit_code == 0, result.output
    assert source_dirs(other) == [source_dir(other, "bilibili", BV)]
    assert source_dirs(configured) == []


def test_capture_commits_raw_material_on_its_own(run, bilibili, initialized):
    vault = initialized
    bilibili.videos[BV] = video(SUBTITLES)
    (vault / "Wiki" / "概念" / "草稿.md").write_text("未提交的 Wiki 改动", encoding="utf-8")

    run.run("capture", CANONICAL)

    subject = commit_subjects(vault)[0]
    assert subject.startswith("capture:")
    assert BV in subject
    committed = git(vault, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == [
        f"原始材料/bilibili/{BV}/元数据.md",
        f"原始材料/bilibili/{BV}/口播稿.md",
        f"原始材料/bilibili/{BV}/封面.jpg",
    ]
    assert git(vault, "status", "--porcelain").strip() == "?? Wiki/"


def test_capture_commit_names_each_changed_source(run, bilibili, initialized):
    vault = initialized
    other = "BV1Ab411c7De"
    bilibili.videos[BV] = video(SUBTITLES)
    bilibili.unavailable[other] = "稿件不可见（62002）"

    run.run("capture", f"{CANONICAL} https://www.bilibili.com/video/{other}")

    message = git(vault, "log", "-1", "--format=%B")
    assert message.startswith("capture: 采集 2 个来源")
    assert f"- B站 {BV} 【大模型】RAG 到底是什么？10 分钟讲清楚" in message
    assert f"- B站 {other}\n" in message
