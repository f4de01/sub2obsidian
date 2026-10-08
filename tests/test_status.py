"""`status`：按来源状态汇总数量，并列出可编译的来源（供 agent 编译时读取）。"""

from pathlib import Path

import pytest

from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import Asset, FetchedSource
from sub2obsidian.sources import Kind, Origin, SourceRepository, Status
from sub2obsidian.transcript import Segment, Transcript

SUBTITLES = Transcript(origin="B站 AI 字幕（ai-zh）", segments=[Segment(0.0, 2.0, "大家好")])


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
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    return vault


def counts(output: str) -> dict[str, int]:
    """从 status 输出中读出「状态 数量」行。"""
    found = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            found[parts[0]] = int(parts[1])
    return found


def test_status_of_empty_vault_counts_zero_in_every_source_status(run, initialized):
    result = run.run("status")

    assert result.exit_code == 0, result.output
    assert counts(result.output) == {
        "待筛": 0,
        "已拒绝": 0,
        "已通过": 0,
        "已采集": 0,
        "已转写": 0,
        "已编译": 0,
        "已失效": 0,
    }
    assert "可编译的来源：无" in result.output


SUBTITLED = "BV1GJ411x7h7"
UNSUBTITLED = "BV1xx411c7mD"
DELETED = "BV1Ab411c7Zz"
FLAKY = "BV1Qs411c7Kp"
PULLED = "BV1Pd411c7Wq"


def capture(run, bv: str) -> None:
    run.run("capture", f"https://www.bilibili.com/video/{bv}")


@pytest.fixture
def sources_in_every_reachable_status(run, bilibili, initialized) -> Path:
    bilibili.videos[SUBTITLED] = video("RAG 到底是什么", SUBTITLES)
    bilibili.videos[UNSUBTITLED] = video("没有字幕的视频", None)
    bilibili.unavailable[DELETED] = "视频已被删除"
    bilibili.failures[FLAKY] = "网络超时"
    for bv in (SUBTITLED, UNSUBTITLED, DELETED, FLAKY):
        capture(run, bv)
    # 拉取来的来源以「待筛」进入原始材料（拉取随后续工单实现，这里直接经来源仓储写入）
    repo = SourceRepository(initialized)
    repo.create(
        SourceRef("bilibili", PULLED, f"https://www.bilibili.com/video/{PULLED}"),
        kind=Kind.VIDEO,
        origin=Origin.PULL,
        status=Status.PENDING,
    )
    return initialized


def compilable_section(output: str) -> str:
    return output.partition("可编译的来源")[2]


def test_status_counts_sources_by_source_status(run, sources_in_every_reachable_status):
    result = run.run("status")

    assert result.exit_code == 0, result.output
    assert counts(result.output) == {
        "待筛": 1,
        "已拒绝": 0,
        "已通过": 1,
        "已采集": 1,
        "已转写": 1,
        "已编译": 0,
        "已失效": 1,
    }


def test_status_lists_only_compilable_sources_with_their_identity_and_title(
    run, sources_in_every_reachable_status
):
    result = run.run("status")

    listed = compilable_section(result.output)
    assert "（1）" in listed
    assert f"bilibili/{SUBTITLED}" in listed
    assert "RAG 到底是什么" in listed
    for not_compilable in (UNSUBTITLED, DELETED, FLAKY, PULLED):
        assert not_compilable not in listed


def test_status_lists_a_collected_article_as_compilable(run, initialized):
    # 文章采集随后续工单实现，这里直接经来源仓储写入一篇「已采集」的文章
    repo = SourceRepository(initialized)
    article = repo.create(
        SourceRef("wechat", "AbCdEf123", "https://mp.weixin.qq.com/s/AbCdEf123"),
        kind=Kind.ARTICLE,
        origin=Origin.PUSH,
        status=Status.APPROVED,
    )
    repo.transition(article, Status.COLLECTED)

    result = run.run("status")

    assert "wechat/AbCdEf123  文章" in compilable_section(result.output)
