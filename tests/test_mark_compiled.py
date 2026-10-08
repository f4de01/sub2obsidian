"""`mark-compiled <来源…>`：agent 编译结束时把来源转为「已编译」；不可编译的来源被拒绝。"""

from pathlib import Path

import pytest

from raw import read_metadata
from vault_git import commit_subjects, git
from sub2obsidian.platforms import Asset, FetchedSource
from sub2obsidian.transcript import Segment, Transcript

SUBTITLES = Transcript(origin="B站 AI 字幕（ai-zh）", segments=[Segment(0.0, 2.0, "大家好")])
TRANSCRIBED = "BV1GJ411x7h7"
ALSO_TRANSCRIBED = "BV1Kb411W7t2"
COLLECTED = "BV1xx411c7mD"


def video(title: str, transcript: Transcript | None) -> FetchedSource:
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
def vault_with_sources(run, vault: Path, credentials, bilibili) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    bilibili.videos[TRANSCRIBED] = video("RAG 到底是什么", SUBTITLES)
    bilibili.videos[ALSO_TRANSCRIBED] = video("向量数据库入门", SUBTITLES)
    bilibili.videos[COLLECTED] = video("还没转写的视频", None)
    for bv in (TRANSCRIBED, ALSO_TRANSCRIBED, COLLECTED):
        run.run("capture", f"https://www.bilibili.com/video/{bv}")
    return vault


def status_of(vault: Path, bv: str) -> str:
    return read_metadata(vault, "bilibili", bv)["来源状态"]


def test_mark_compiled_turns_compilable_sources_into_compiled(run, vault_with_sources):
    vault = vault_with_sources

    result = run.run("mark-compiled", f"bilibili/{TRANSCRIBED}", f"bilibili/{ALSO_TRANSCRIBED}")

    assert result.exit_code == 0, result.output
    assert status_of(vault, TRANSCRIBED) == "已编译"
    assert status_of(vault, ALSO_TRANSCRIBED) == "已编译"
    assert "RAG 到底是什么" in result.output
    assert "向量数据库入门" in result.output


def test_mark_compiled_refuses_a_video_that_is_not_yet_transcribed(run, vault_with_sources):
    vault = vault_with_sources

    result = run.run("mark-compiled", f"bilibili/{COLLECTED}")

    assert result.exit_code != 0
    assert status_of(vault, COLLECTED) == "已采集"
    assert f"bilibili/{COLLECTED}" in result.output
    assert "不可编译" in result.output


def test_mark_compiled_refuses_a_source_already_compiled(run, vault_with_sources):
    vault = vault_with_sources
    run.run("mark-compiled", f"bilibili/{TRANSCRIBED}")

    result = run.run("mark-compiled", f"bilibili/{TRANSCRIBED}")

    assert result.exit_code != 0
    assert status_of(vault, TRANSCRIBED) == "已编译"
    assert "不可编译" in result.output


def test_mark_compiled_refuses_an_unknown_source(run, vault_with_sources):
    result = run.run("mark-compiled", "bilibili/BV1zz411c7zz")

    assert result.exit_code != 0
    assert "找不到来源：bilibili/BV1zz411c7zz" in result.output


def test_mark_compiled_changes_nothing_when_any_source_is_refused(run, vault_with_sources):
    vault = vault_with_sources

    result = run.run("mark-compiled", f"bilibili/{TRANSCRIBED}", f"bilibili/{COLLECTED}")

    assert result.exit_code != 0
    assert status_of(vault, TRANSCRIBED) == "已转写"
    assert status_of(vault, COLLECTED) == "已采集"


@pytest.mark.parametrize(
    "name",
    [
        f"原始材料/bilibili/{TRANSCRIBED}",
        f"原始材料\\bilibili\\{TRANSCRIBED}\\",
        f"原始材料/bilibili/{TRANSCRIBED}/元数据.md",
        f"https://www.bilibili.com/video/{TRANSCRIBED}?spm_id_from=333.1007",
    ],
    ids=["raw-dir", "windows-path", "metadata-file", "link"],
)
def test_mark_compiled_accepts_the_raw_directory_or_a_link_of_the_source(
    run, vault_with_sources, name
):
    result = run.run("mark-compiled", name)

    assert result.exit_code == 0, result.output
    assert status_of(vault_with_sources, TRANSCRIBED) == "已编译"


def test_mark_compiled_leaves_the_status_change_to_the_compile_commit(run, vault_with_sources):
    vault = vault_with_sources
    commits_before = commit_subjects(vault)

    run.run("mark-compiled", f"bilibili/{TRANSCRIBED}")

    # 编译以一次 git 提交结束：来源状态的改动由 agent 与 Wiki 的改动一起提交
    assert commit_subjects(vault) == commits_before
    assert git(vault, "status", "--porcelain").strip() == f"M 原始材料/bilibili/{TRANSCRIBED}/元数据.md"


def test_mark_compiled_names_the_same_source_twice_without_refusing(run, vault_with_sources):
    result = run.run(
        "mark-compiled", f"bilibili/{TRANSCRIBED}", f"原始材料/bilibili/{TRANSCRIBED}"
    )

    assert result.exit_code == 0, result.output
    assert status_of(vault_with_sources, TRANSCRIBED) == "已编译"
    assert result.output.count("已编译：") == 1
