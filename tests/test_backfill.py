"""回填与筛选：sync 拉取 B站 收藏（只抓元数据）→ 待筛清单 → screen → 已通过的来源经 sync 采集转写。

B站 适配器是假实现（见 conftest.py）；真实适配器「列出收藏」的契约测试见
test_bilibili_adapter.py。真实账号的回填放到 #12。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raw import read_metadata, source_dir, source_dirs
from vault_git import git
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import Favorite, FetchedSource, FetchFailed
from sub2obsidian.transcript import Segment

LIST = "待筛清单.md"


def bv(n: int) -> str:
    return f"BV1Ab411c{n:03d}"


def favorite(n: int, **overrides) -> Favorite:
    bvid = bv(n)
    fields = dict(
        ref=SourceRef("bilibili", bvid, f"https://www.bilibili.com/video/{bvid}"),
        kind="视频",
        title=f"收藏的视频 {n}",
        author=f"UP主{n}",
        published="2024-05-01T20:00:00+08:00",
        duration=612,
        description=f"第 {n} 个视频的简介。",
    )
    fields.update(overrides)
    return Favorite(**fields)


@pytest.fixture
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    credentials.login("douyin")  # 抖音也会被拉取收藏
    return vault


def screening_list(vault: Path) -> str:
    return (vault / LIST).read_text(encoding="utf-8")


def test_sync_backfills_bilibili_favorites_as_pending_sources_with_metadata_only(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.favorite_folders["默认收藏夹"] = [favorite(1), favorite(2)]
    bilibili.favorite_folders["稍后再看"] = [favorite(3, title="稍后再看的视频")]

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", bv(n)) for n in (1, 2, 3)]
    meta = read_metadata(vault, "bilibili", bv(1))
    assert meta["来源状态"] == "待筛"
    assert meta["采集途径"] == "拉取"
    assert meta["标题"] == "收藏的视频 1"
    assert meta["作者"] == "UP主1"
    assert meta["时长"] == 612
    assert meta["发布时间"] == "2024-05-01T20:00:00+08:00"
    # 只抓元数据：不采集、不下载封面与音频
    assert bilibili.fetched == []
    assert [p.name for p in source_dir(vault, "bilibili", bv(1)).iterdir()] == ["元数据.md"]
    assert "第 1 个视频的简介。" in (source_dir(vault, "bilibili", bv(1)) / "元数据.md").read_text(
        encoding="utf-8"
    )
    summary = result.output[result.output.index("sync 汇总") :]
    assert "新增来源 3" in summary
    assert "待筛 3" in summary
    assert "勾选后执行 sub2obsidian screen" in summary


def test_sync_writes_a_screening_list_with_a_checkbox_metadata_and_suggestion_slot_per_source(
    run, bilibili, initialized
):
    vault = initialized
    long_intro = "这是一段很长的简介，" * 20
    bilibili.favorite_folders["默认收藏夹"] = [
        favorite(1, title="【大模型】RAG 到底是什么？", duration=3725, description=long_intro),
        favorite(2, author=None, duration=None, published=None, description=""),
    ]

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    text = screening_list(vault)
    lines = text.splitlines()
    first = lines.index(f"- [ ] 【大模型】RAG 到底是什么？ `bilibili/{bv(1)}`")
    assert lines[first + 1].strip() == (
        f"- UP主1 · 1:02:05 · 2024-05-01 · [原链接](https://www.bilibili.com/video/{bv(1)})"
    )
    excerpt = lines[first + 2].strip()
    assert excerpt.startswith("- 简介：这是一段很长的简介，") and excerpt.endswith("…")
    assert len(excerpt) < 120  # 只放摘要
    assert lines[first + 3].strip() == "- 建议："
    second = lines.index(f"- [ ] 收藏的视频 2 `bilibili/{bv(2)}`")
    assert lines[second + 1].strip() == f"- [原链接](https://www.bilibili.com/video/{bv(2)})"
    assert lines[second + 2].strip() == "- 建议："
    assert "sub2obsidian screen" in text  # 清单开头说明怎么用
    # 清单随原始材料一起提交
    assert git(vault, "status", "--porcelain") == ""
    assert LIST in git(vault, "show", "--name-only", "--format=", "HEAD").splitlines()


def tick(vault: Path, *bvids: str, suggestions: dict[str, str] | None = None) -> None:
    """模拟用户在 Obsidian 里勾选、agent 填写建议栏。"""
    suggestions = suggestions or {}
    lines = screening_list(vault).splitlines()
    current = None
    for i, line in enumerate(lines):
        if line.startswith("- [ ] ") and line.endswith("`"):
            current = line.rsplit("/", 1)[1].rstrip("`")
            if current in bvids:
                lines[i] = line.replace("- [ ] ", "- [x] ", 1)
        elif line.strip() == "- 建议：" and current in suggestions:
            lines[i] = f"{line}{suggestions[current]}"
    (vault / LIST).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_screen_approves_ticked_sources_and_rejects_the_rest(run, bilibili, initialized):
    vault = initialized
    bilibili.favorite_folders["默认收藏夹"] = [favorite(1), favorite(2), favorite(3)]
    run.run("sync")
    tick(
        vault,
        bv(1),
        bv(3),
        suggestions={bv(1): "知识类 · AI —— 讲 RAG 原理", bv(2): "非知识类（娱乐）"},
    )

    result = run.run("screen")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "已通过"
    assert read_metadata(vault, "bilibili", bv(3))["来源状态"] == "已通过"
    rejected = read_metadata(vault, "bilibili", bv(2))
    assert rejected["来源状态"] == "已拒绝"
    # 建议栏随筛选结果记入元数据
    assert rejected["筛选建议"] == "非知识类（娱乐）"
    assert read_metadata(vault, "bilibili", bv(1))["筛选建议"] == "知识类 · AI —— 讲 RAG 原理"
    assert read_metadata(vault, "bilibili", bv(3))["筛选建议"] is None
    assert "已通过 2，已拒绝 1" in result.output
    # 筛过的来源离开清单；原始材料与清单单独提交
    assert "bilibili/" not in screening_list(vault)
    assert "暂无待筛的来源" in screening_list(vault)
    assert git(vault, "log", "-1", "--format=%s").startswith("screen:")
    assert git(vault, "status", "--porcelain") == ""


def test_sources_approved_by_screen_are_collected_and_transcribed_by_the_next_sync(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    bilibili.favorite_folders["默认收藏夹"] = [favorite(1), favorite(2)]
    run.run("sync")
    tick(vault, bv(1))
    run.run("screen")
    bilibili.videos[bv(1)] = FetchedSource(kind="视频", title="收藏的视频 1", author="UP主1")
    transcriber.segments[f"audio:{bv(1)}"] = [Segment(0.0, 1.0, "大家好")]

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "已转写"
    assert (source_dir(vault, "bilibili", bv(1)) / "口播稿.md").is_file()
    assert read_metadata(vault, "bilibili", bv(2))["来源状态"] == "已拒绝"
    assert bilibili.fetched == [bv(1)]  # 已拒绝的来源不采集


def test_rejected_sources_do_not_return_to_the_screening_list_on_the_next_sync(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    bilibili.favorite_folders["默认收藏夹"] = [favorite(1), favorite(2)]
    run.run("sync")
    tick(vault, bv(1))
    run.run("screen")
    bilibili.videos[bv(1)] = FetchedSource(kind="视频", title="收藏的视频 1", author="UP主1")
    transcriber.segments[f"audio:{bv(1)}"] = [Segment(0.0, 1.0, "大家好")]
    # 用户之后又收藏了一个视频；被拒绝的那个仍在收藏夹里
    bilibili.favorite_folders["默认收藏夹"].insert(0, favorite(3))

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    text = screening_list(vault)
    assert f"`bilibili/{bv(3)}`" in text
    assert bv(2) not in text
    assert read_metadata(vault, "bilibili", bv(2))["来源状态"] == "已拒绝"
    assert [p.name for p in source_dir(vault, "bilibili", bv(2)).iterdir()] == ["元数据.md"]


def configure(user_config_dir: Path, text: str) -> None:
    """在用户的 config.toml 末尾追加设置（init 已在其中记下知识库路径）。"""
    with (user_config_dir / "config.toml").open("a", encoding="utf-8") as config:
        config.write("\n" + text)


FOLDER = "默认收藏夹"


def test_backfill_registers_one_batch_per_sync_and_the_next_sync_continues_from_the_checkpoint(
    run, bilibili, initialized, user_config_dir: Path
):
    vault = initialized
    configure(user_config_dir, "[backfill]\nbatch_size = 4\n")
    bilibili.favorite_folders[FOLDER] = [favorite(n) for n in range(1, 11)]  # 每页 3 条，共 4 页

    first = run.run("sync")

    assert first.exit_code == 0, first.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", bv(n)) for n in range(1, 5)]
    assert "其余下次 sync 继续" in first.output
    assert "新增来源 4" in first.output
    bilibili.pages_read.clear()

    second = run.run("sync")

    assert second.exit_code == 0, second.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", bv(n)) for n in range(1, 9)]
    # 从断点（第 2 页，上次只登记了其中 1 条）接着读，不从头再来
    assert bilibili.pages_read == [(FOLDER, "1"), (FOLDER, "2")]
    assert "新增来源 4" in second.output

    third = run.run("sync")

    assert third.exit_code == 0, third.output
    assert len(source_dirs(vault)) == 10
    assert "其余下次 sync 继续" not in third.output
    assert screening_list(vault).count("- [ ] ") == 10


def test_backfill_interrupted_by_a_platform_failure_resumes_from_the_checkpoint(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(n) for n in range(1, 8)]
    bilibili.page_failures[(FOLDER, "2")] = FetchFailed("请求过于频繁（-412），请稍后再试")

    failed = run.run("sync")

    assert failed.exit_code != 0
    assert "拉取 B站 收藏失败：请求过于频繁（-412）" in failed.output
    assert len(source_dirs(vault)) == 6  # 前两页已登记，并已提交
    assert git(vault, "status", "--porcelain") == ""
    bilibili.pages_read.clear()

    resumed = run.run("sync")

    assert resumed.exit_code == 0, resumed.output
    assert bilibili.pages_read == [(FOLDER, "2")]
    assert source_dirs(vault) == [source_dir(vault, "bilibili", bv(n)) for n in range(1, 8)]
    assert "新增来源 1" in resumed.output


def test_backfill_interrupted_with_ctrl_c_commits_what_it_registered_and_resumes_without_duplicates(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(n) for n in range(1, 8)]
    bilibili.page_failures[(FOLDER, "1")] = KeyboardInterrupt()

    interrupted = run.run("sync")

    assert interrupted.exit_code != 0
    assert "sync 已中断" in interrupted.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", bv(n)) for n in range(1, 4)]
    assert git(vault, "status", "--porcelain") == ""
    bilibili.pages_read.clear()

    resumed = run.run("sync")

    assert resumed.exit_code == 0, resumed.output
    assert bilibili.pages_read == [(FOLDER, "1"), (FOLDER, "2")]
    assert source_dirs(vault) == [source_dir(vault, "bilibili", bv(n)) for n in range(1, 8)]
    assert "新增来源 4" in resumed.output


def test_after_backfill_completes_each_sync_only_reads_the_top_of_each_list_for_new_favorites(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(n) for n in range(1, 8)]
    bilibili.favorite_folders["稍后再看"] = [favorite(20)]
    run.run("sync")  # 回填完成
    bilibili.pages_read.clear()

    unchanged = run.run("sync")

    assert unchanged.exit_code == 0, unchanged.output
    assert bilibili.pages_read == [(FOLDER, None), ("稍后再看", None)]  # 第一页全都登记过即停
    bilibili.pages_read.clear()
    bilibili.favorite_folders[FOLDER].insert(0, favorite(8))

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", bv(8))["来源状态"] == "待筛"
    assert bilibili.pages_read == [(FOLDER, None), (FOLDER, "1"), ("稍后再看", None)]
    assert "新增来源 1" in result.output


def test_favorite_already_gone_on_the_platform_becomes_an_unavailable_stub_not_a_list_entry(
    run, bilibili, initialized
):
    vault = initialized
    gone = favorite(2, title="已失效视频", author=None, unavailable="收藏夹中显示为已失效视频")
    bilibili.favorite_folders[FOLDER] = [favorite(1), gone]

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", bv(2))
    assert meta["来源状态"] == "已失效"
    assert meta["失败原因"] == "收藏夹中显示为已失效视频"
    assert bv(2) not in screening_list(vault)
    assert f"`bilibili/{bv(1)}`" in screening_list(vault)


def test_pull_needing_login_is_reported_and_the_inbox_is_still_processed(
    run, bilibili, wechat, inbox, credentials, vault: Path
):
    from test_sync import WX_ID, WX_LINK, article

    run.run("init", str(vault))  # 没有登录 B站
    bilibili.favorite_folders[FOLDER] = [favorite(1)]
    wechat.articles[WX_ID] = article()
    inbox.push(WX_LINK)

    result = run.run("sync")

    assert result.exit_code != 0
    assert "拉取 B站 收藏失败：请重新登录 B站：sub2obsidian login bilibili" in result.output
    assert read_metadata(vault, "wechat", WX_ID)["来源状态"] == "已采集"
    assert not source_dir(vault, "bilibili", bv(1)).exists()
    assert not (vault / LIST).exists()


def test_sync_without_any_favorites_writes_no_screening_list(run, bilibili, initialized):
    vault = initialized

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert not (vault / LIST).exists()


def test_ticks_and_suggestions_survive_a_sync_that_adds_more_sources_to_the_list(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(1), favorite(2)]
    run.run("sync")
    tick(vault, bv(1), suggestions={bv(1): "知识类 · AI", bv(2): "非知识类（娱乐）"})
    bilibili.favorite_folders[FOLDER].insert(0, favorite(3))

    run.run("sync")

    text = screening_list(vault)
    assert f"- [x] 收藏的视频 1 `bilibili/{bv(1)}`" in text
    assert f"- [ ] 收藏的视频 2 `bilibili/{bv(2)}`" in text
    assert f"- [ ] 收藏的视频 3 `bilibili/{bv(3)}`" in text
    assert "- 建议：知识类 · AI" in text
    assert "- 建议：非知识类（娱乐）" in text


def test_screen_with_nothing_ticked_changes_nothing_unless_rejecting_all_is_confirmed(
    run, bilibili, initialized
):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(1), favorite(2)]
    run.run("sync")
    head = git(vault, "rev-parse", "HEAD")

    refused = run.run("screen")

    assert refused.exit_code != 0
    assert "一个来源都没有勾选" in refused.output
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "待筛"
    assert git(vault, "rev-parse", "HEAD") == head

    confirmed = run.run("screen", "--reject-all")

    assert confirmed.exit_code == 0, confirmed.output
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "已拒绝"
    assert read_metadata(vault, "bilibili", bv(2))["来源状态"] == "已拒绝"


def test_screen_without_a_screening_list_asks_to_sync_first(run, initialized):
    result = run.run("screen")

    assert result.exit_code != 0
    assert "先执行 sub2obsidian sync" in result.output


def test_screen_leaves_sources_deleted_from_the_list_pending(run, bilibili, initialized):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(1), favorite(2)]
    run.run("sync")
    tick(vault, bv(1))
    lines = screening_list(vault).splitlines()
    start = next(i for i, line in enumerate(lines) if f"`bilibili/{bv(2)}`" in line)
    del lines[start : start + 4]  # 用户删掉了第二条（标题、元数据、简介、建议四行）
    (vault / LIST).write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = run.run("screen")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "已通过"
    assert read_metadata(vault, "bilibili", bv(2))["来源状态"] == "待筛"
    assert f"`bilibili/{bv(2)}`" in screening_list(vault)  # 重新生成的清单里又有它


@pytest.mark.parametrize(
    "setting",
    [
        "batch_size = 0",
        'batch_size = "十"',
        "interval = [5, 2]",
        "interval = [-1, 2]",
        "douyin_interval = [6, 3]",
    ],
)
def test_invalid_backfill_settings_are_reported_before_anything_changes(
    run, bilibili, initialized, user_config_dir: Path, setting: str
):
    vault = initialized
    configure(user_config_dir, f"[backfill]\n{setting}\n")
    bilibili.favorite_folders[FOLDER] = [favorite(1)]

    result = run.run("sync")

    assert result.exit_code != 0
    assert "config.toml 中 [backfill]" in result.output
    assert source_dirs(vault) == []


def test_ticked_entry_with_text_appended_after_the_source_key_is_still_approved(
    run, bilibili, initialized
):
    """Obsidian 的任务插件会在勾选的行末追加完成日期等文字。"""
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(1), favorite(2)]
    run.run("sync")
    text = screening_list(vault).replace(
        f"- [ ] 收藏的视频 1 `bilibili/{bv(1)}`", f"- [x] 收藏的视频 1 `bilibili/{bv(1)}` ✅ 2026-10-08"
    )
    (vault / LIST).write_text(text, encoding="utf-8")

    result = run.run("screen")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "已通过"
    assert read_metadata(vault, "bilibili", bv(2))["来源状态"] == "已拒绝"


def test_pushing_a_link_to_a_pending_source_approves_it_and_takes_it_off_the_list(
    run, bilibili, inbox, transcriber, initialized
):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(1), favorite(2)]
    run.run("sync")
    bilibili.videos[bv(1)] = FetchedSource(kind="视频", title="收藏的视频 1", author="UP主1")
    transcriber.segments[f"audio:{bv(1)}"] = [Segment(0.0, 1.0, "大家好")]
    inbox.push(f"这个值得看 https://www.bilibili.com/video/{bv(1)}")

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", bv(1))
    assert meta["来源状态"] == "已转写"
    assert meta["采集途径"] == "拉取"  # 先拉取到的，途径不变
    assert bv(1) not in screening_list(vault)
    assert f"`bilibili/{bv(2)}`" in screening_list(vault)
    assert git(vault, "status", "--porcelain") == ""


def test_capturing_a_pending_source_approves_it_and_updates_the_list(
    run, bilibili, transcriber, initialized
):
    vault = initialized
    bilibili.favorite_folders[FOLDER] = [favorite(1), favorite(2)]
    run.run("sync")
    bilibili.videos[bv(1)] = FetchedSource(kind="视频", title="收藏的视频 1", author="UP主1")
    transcriber.segments[f"audio:{bv(1)}"] = [Segment(0.0, 1.0, "大家好")]

    result = run.run("capture", f"https://www.bilibili.com/video/{bv(1)}")

    assert result.exit_code == 0, result.output
    assert read_metadata(vault, "bilibili", bv(1))["来源状态"] == "已采集"
    assert bv(1) not in screening_list(vault)
    assert git(vault, "status", "--porcelain") == ""
