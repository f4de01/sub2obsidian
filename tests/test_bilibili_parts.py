"""B站 分P：多P视频的每个分P是一条独立的来源。

推送（capture、收件箱）：链接带 `?p=N` 只采集第 N P；不带 p 时展开为全部分P，全部视为已通过。
回填：多P视频的每个分P各占待筛清单一行，按视频分组；screen 按分P转换状态。
B站 适配器是假实现（见 conftest.py）；真实适配器的契约测试见 test_bilibili_adapter.py。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raw import read_metadata, source_dir, source_dirs
from vault_git import git
from sub2obsidian.links import bilibili_ref
from sub2obsidian.platforms import Favorite, FetchedSource
from sub2obsidian.sources import VideoPart
from sub2obsidian.transcript import Segment, Transcript

BV = "BV1Ab411c7De"
VIDEO = f"https://www.bilibili.com/video/{BV}"
VIDEO_TITLE = "RAG 从入门到实战（全 3 集）"
PART_TITLES = {1: "什么是 RAG", 2: "向量检索", 3: "评估与调优"}
PART_IDS = {1: BV, 2: f"{BV}_p2", 3: f"{BV}_p3"}


def part(n: int) -> FetchedSource:
    """平台上第 n P 的采集结果：标题、时长、口播稿都按分P。"""
    return FetchedSource(
        kind="视频",
        title=f"{VIDEO_TITLE} P{n} {PART_TITLES[n]}",
        author="某知识区UP主",
        published="2024-05-01T20:00:00+08:00",
        duration=300 + n,
        description="三集讲清楚 RAG。",
        transcript=Transcript("B站 AI 字幕（ai-zh）", [Segment(1.0, 2.0, f"第 {n} 集开场")]),
        part=VideoPart(number=n, video_title=VIDEO_TITLE),
    )


@pytest.fixture
def initialized(run, vault: Path, credentials, bilibili) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    credentials.login("douyin")  # sync 也会拉取抖音收藏
    bilibili.parts[BV] = 3
    for n, platform_id in PART_IDS.items():
        bilibili.videos[platform_id] = part(n)
    return vault


def test_capturing_a_multi_part_link_without_p_collects_every_part_as_its_own_source(
    run, bilibili, initialized
):
    vault = initialized

    result = run.run("capture", VIDEO)

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", PART_IDS[n]) for n in (1, 2, 3)]
    for n, platform_id in PART_IDS.items():
        meta = read_metadata(vault, "bilibili", platform_id)
        assert meta["平台内ID"] == platform_id
        assert meta["规范链接"] == (VIDEO if n == 1 else f"{VIDEO}?p={n}")
        assert meta["采集途径"] == "推送"
        assert meta["来源状态"] == "已转写"
        assert meta["标题"] == f"{VIDEO_TITLE} P{n} {PART_TITLES[n]}"
        assert meta["时长"] == 300 + n
        assert meta["分P"] == n
        assert meta["视频标题"] == VIDEO_TITLE
        transcript = (source_dir(vault, "bilibili", platform_id) / "口播稿.md").read_text(
            encoding="utf-8"
        )
        assert f"[00:00:01] 第 {n} 集开场" in transcript
    assert bilibili.fetched == [PART_IDS[n] for n in (1, 2, 3)]
    assert "新增来源 3" in result.output
    assert git(vault, "log", "-1", "--format=%s").strip() == "capture: 采集 3 个来源"


def test_capturing_a_link_with_p_collects_only_that_part(run, bilibili, initialized):
    vault = initialized

    result = run.run("capture", f"{VIDEO}?p=2&share_source=copy_web")

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", f"{BV}_p2")]
    assert read_metadata(vault, "bilibili", f"{BV}_p2")["规范链接"] == f"{VIDEO}?p=2"
    assert bilibili.fetched == [f"{BV}_p2"]


def test_capturing_the_whole_video_after_one_part_adds_only_the_other_parts(
    run, bilibili, initialized
):
    vault = initialized
    run.run("capture", f"{VIDEO}?p=2")

    result = run.run("capture", VIDEO)

    assert result.exit_code == 0, result.output
    assert len(source_dirs(vault)) == 3
    assert bilibili.fetched == [f"{BV}_p2", BV, f"{BV}_p3"]
    assert "来源已存在" in result.output
    assert "新增来源 2" in result.output


def test_single_part_video_keeps_its_bv_number_as_the_platform_id(run, bilibili, initialized):
    vault = initialized
    single = "BV1GJ411x7h7"
    bilibili.videos[single] = FetchedSource(kind="视频", title="单P视频")

    result = run.run("capture", f"https://www.bilibili.com/video/{single}?p=1")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", single)
    assert meta["规范链接"] == f"https://www.bilibili.com/video/{single}"
    assert meta["分P"] is None
    assert meta["视频标题"] is None


def test_part_count_that_cannot_be_looked_up_is_reported_and_nothing_lands(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.part_failures[BV] = "请求过于频繁（-412），请稍后再试"

    result = run.run("capture", VIDEO)

    assert result.exit_code != 0
    assert "请求过于频繁（-412）" in result.output
    assert source_dirs(vault) == []


def test_multi_part_link_pushed_to_the_inbox_collects_every_part(
    run, bilibili, inbox, initialized
):
    vault = initialized
    inbox.push(f"这套课不错 {VIDEO}")

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    for platform_id in PART_IDS.values():
        meta = read_metadata(vault, "bilibili", platform_id)
        assert meta["采集途径"] == "推送"
        assert meta["来源状态"] == "已转写"


def test_inbox_link_whose_part_count_fails_is_retried_by_the_next_sync(
    run, bilibili, inbox, initialized
):
    vault = initialized
    bilibili.part_failures[BV] = "请求过于频繁（-412），请稍后再试"
    inbox.push(VIDEO)

    failed = run.run("sync")

    assert failed.exit_code != 0
    assert source_dirs(vault) == []

    retried = run.run("sync")

    assert retried.exit_code == 0, retried.output
    assert len(source_dirs(vault)) == 3


# ---- 回填：多P视频的每个分P各占待筛清单一行，按视频分组 ----

LIST = "待筛清单.md"
SINGLE = "BV1GJ411x7h7"


def listed_part(n: int) -> Favorite:
    """收藏夹里的多P视频，由适配器展开为每个分P一条。"""
    return Favorite(
        ref=bilibili_ref(BV, n),
        kind="视频",
        title=f"{VIDEO_TITLE} P{n} {PART_TITLES[n]}",
        author="某知识区UP主",
        published="2024-05-01T20:00:00+08:00",
        duration=300 + n,
        description="三集讲清楚 RAG。",
        part=VideoPart(number=n, video_title=VIDEO_TITLE),
    )


def listed_single() -> Favorite:
    return Favorite(
        ref=bilibili_ref(SINGLE),
        kind="视频",
        title="单P视频",
        author="另一个UP主",
        duration=212,
    )


@pytest.fixture
def backfilled(run, bilibili, initialized) -> Path:
    bilibili.favorite_folders["默认收藏夹"] = [listed_part(1), listed_part(2), listed_part(3)]
    bilibili.favorite_folders["稍后再看"] = [listed_single()]
    result = run.run("sync")
    assert result.exit_code == 0, result.output
    return initialized


def screening_lines(vault: Path) -> list[str]:
    return (vault / LIST).read_text(encoding="utf-8").splitlines()


def test_backfilled_parts_are_pending_sources_of_their_own(backfilled):
    vault = backfilled
    for n, platform_id in PART_IDS.items():
        meta = read_metadata(vault, "bilibili", platform_id)
        assert meta["来源状态"] == "待筛"
        assert meta["采集途径"] == "拉取"
        assert meta["分P"] == n
        assert meta["视频标题"] == VIDEO_TITLE
        assert meta["时长"] == 300 + n


def test_screening_list_groups_the_parts_of_a_video_under_it_one_line_per_part(backfilled):
    lines = screening_lines(backfilled)

    header = next(i for i, line in enumerate(lines) if VIDEO_TITLE in line)
    assert lines[header] == (
        f"- 多P视频：{VIDEO_TITLE} · 某知识区UP主 · 2024-05-01 · [原视频]({VIDEO})"
    )
    assert lines[header + 1] == "\t- 简介：三集讲清楚 RAG。"
    assert lines[header + 2 : header + 11] == [
        f"\t- [ ] P1 什么是 RAG `bilibili/{BV}`",
        f"\t\t- 5:01 · [原链接]({VIDEO})",
        "\t\t- 建议：",
        f"\t- [ ] P2 向量检索 `bilibili/{BV}_p2`",
        f"\t\t- 5:02 · [原链接]({VIDEO}?p=2)",
        "\t\t- 建议：",
        f"\t- [ ] P3 评估与调优 `bilibili/{BV}_p3`",
        f"\t\t- 5:03 · [原链接]({VIDEO}?p=3)",
        "\t\t- 建议：",
    ]
    # 单P视频照旧一行，不在分组里
    assert f"- [ ] 单P视频 `bilibili/{SINGLE}`" in lines
    assert "## B站（4）" in lines


def tick_parts(vault: Path, *platform_ids: str, suggestion: str = "") -> None:
    """模拟用户勾选分P、agent 在每个分P的建议栏写上建议。"""
    lines = screening_lines(vault)
    for i, line in enumerate(lines):
        if any(line.endswith(f"`bilibili/{platform_id}`") for platform_id in platform_ids):
            lines[i] = line.replace("- [ ] ", "- [x] ", 1)
        elif line.strip() == "- 建议：" and suggestion:
            lines[i] = line + suggestion
    (vault / LIST).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_screen_approves_only_the_ticked_parts(run, backfilled):
    vault = backfilled
    tick_parts(vault, BV, f"{BV}_p3", SINGLE, suggestion="知识类 · AI")

    result = run.run("screen")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已通过"
    assert read_metadata(vault, "bilibili", f"{BV}_p2")["来源状态"] == "已拒绝"
    assert read_metadata(vault, "bilibili", f"{BV}_p3")["来源状态"] == "已通过"
    assert read_metadata(vault, "bilibili", f"{BV}_p3")["筛选建议"] == "知识类 · AI"
    assert "已通过 3，已拒绝 1" in result.output
    assert "暂无待筛的来源" in (vault / LIST).read_text(encoding="utf-8")


def test_parts_approved_by_screen_are_collected_by_the_next_sync(run, bilibili, backfilled):
    vault = backfilled
    tick_parts(vault, f"{BV}_p2")
    run.run("screen")

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert bilibili.fetched == [f"{BV}_p2"]
    assert read_metadata(vault, "bilibili", f"{BV}_p2")["来源状态"] == "已转写"


def test_ticks_on_parts_survive_a_sync_that_lists_more_parts(run, bilibili, backfilled):
    vault = backfilled
    tick_parts(vault, f"{BV}_p2")
    other = "BV1Mc411P7Qr"
    bilibili.favorite_folders["默认收藏夹"].insert(
        0,
        Favorite(
            ref=bilibili_ref(other, 2),
            kind="视频",
            title="另一个多P视频 P2 下集",
            part=VideoPart(number=2, video_title="另一个多P视频"),
        ),
    )

    run.run("sync")

    lines = screening_lines(vault)
    assert f"\t- [x] P2 向量检索 `bilibili/{BV}_p2`" in lines
    assert f"\t- [ ] P1 什么是 RAG `bilibili/{BV}`" in lines
    assert any(line.startswith("- 多P视频：另一个多P视频") for line in lines)
    assert f"\t- [ ] P2 下集 `bilibili/{other}_p2`" in lines


def test_mark_compiled_names_a_part_by_its_key_or_its_link(run, initialized):
    vault = initialized
    run.run("capture", VIDEO)

    def statuses() -> list[str]:
        return [read_metadata(vault, "bilibili", PART_IDS[n])["来源状态"] for n in (1, 2, 3)]

    # 不带 p 的链接是第 1 P 的规范链接：只指第 1 P，不展开为全部分P
    first = run.run("mark-compiled", VIDEO)

    assert first.exit_code == 0, first.output
    assert statuses() == ["已编译", "已转写", "已转写"]

    rest = run.run("mark-compiled", f"bilibili/{BV}_p2", f"{VIDEO}?p=3&t=12")

    assert rest.exit_code == 0, rest.output
    assert statuses() == ["已编译", "已编译", "已编译"]


def test_status_lists_each_compilable_part(run, initialized):
    run.run("capture", VIDEO)

    result = run.run("status")

    for n, platform_id in PART_IDS.items():
        assert f"bilibili/{platform_id}  视频  {VIDEO_TITLE} P{n} {PART_TITLES[n]}" in result.output
