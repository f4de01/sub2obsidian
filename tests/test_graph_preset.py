"""图谱预置 (graph-preset)：给已有知识库写入关系图谱预置，从不覆盖用户调过的图谱设置。"""

import json
from pathlib import Path

from test_init import graph_settings
from vault_git import commit, commit_subjects, git

GRAPH = Path(".obsidian") / "graph.json"

# 用户在 Obsidian 中第一次打开关系图谱、什么都没调时，Obsidian 写下的 graph.json（取自真实知识库）
OBSIDIAN_DEFAULT = {
    "collapse-filter": True,
    "search": "",
    "showTags": False,
    "showAttachments": False,
    "hideUnresolved": False,
    "showOrphans": True,
    "collapse-color-groups": True,
    "colorGroups": [],
    "collapse-display": True,
    "showArrow": False,
    "textFadeMultiplier": 0,
    "nodeSizeMultiplier": 1,
    "lineSizeMultiplier": 1,
    "collapse-forces": True,
    "centerStrength": 0.518713248970312,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 250,
    "scale": 0.9246720829508701,
    "close": False,
}


def old_vault(run, vault: Path) -> None:
    """版本 7 之前初始化的知识库：init 没有写 graph.json。"""
    run.run("init", str(vault))
    git(vault, "rm", "--quiet", str(GRAPH))
    commit(vault, "旧版 init 没有图谱预置")


def write_graph(vault: Path, settings: object) -> None:
    (vault / GRAPH).write_text(json.dumps(settings, indent=2), encoding="utf-8")


def preset(tmp_path: Path, run) -> dict:
    """init 新建的知识库里的图谱预置。"""
    fresh = tmp_path / "新库"
    run.run("init", str(fresh))
    return graph_settings(fresh)


def test_graph_left_at_obsidian_default_gets_the_preset(run, vault: Path, tmp_path: Path):
    old_vault(run, vault)
    write_graph(vault, OBSIDIAN_DEFAULT)

    result = run.run("graph-preset", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert graph_settings(vault) == preset(tmp_path, run)
    assert "已写入图谱预置" in result.output
    for legend in ["主题域朱红", "子主题紫红", "概念页蓝", "综述页蓝绿", "来源页灰", "我的笔记橙"]:
        assert legend in result.output, legend
    assert "关闭" in result.output and "重新打开" in result.output
    assert commit_subjects(vault)[0] == "graph-preset: 写入关系图谱预置"
    assert git(vault, "status", "--porcelain") == ""


def test_missing_graph_settings_get_the_preset(run, vault: Path, tmp_path: Path):
    old_vault(run, vault)

    result = run.run("graph-preset", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert graph_settings(vault) == preset(tmp_path, run)
    assert git(vault, "ls-files", str(GRAPH)).strip() == ".obsidian/graph.json"


def test_obsidian_default_counts_whatever_the_zoom_and_open_panels(run, vault: Path, tmp_path: Path):
    """缩放比例、面板开合随浏览而变，不算用户的选择；缺少的键按 Obsidian 的默认值。"""
    old_vault(run, vault)
    browsed = {**OBSIDIAN_DEFAULT, "scale": 2.5, "close": True, "collapse-filter": False}
    del browsed["showArrow"]
    write_graph(vault, browsed)

    run.run("graph-preset", "--vault", str(vault))

    assert graph_settings(vault) == preset(tmp_path, run)


def test_customised_graph_settings_are_never_overwritten(run, vault: Path):
    old_vault(run, vault)
    for customised in [
        {**OBSIDIAN_DEFAULT, "search": "path:Wiki/概念/"},
        {**OBSIDIAN_DEFAULT, "colorGroups": [{"query": "tag:#AI", "color": {"a": 1, "rgb": 255}}]},
        {**OBSIDIAN_DEFAULT, "repelStrength": 12},
        {**OBSIDIAN_DEFAULT, "linkStrength": True},
        {**OBSIDIAN_DEFAULT, "某个新版 Obsidian 才有的设置": True},
    ]:
        write_graph(vault, customised)
        before = (vault / GRAPH).read_bytes()

        result = run.run("graph-preset", "--vault", str(vault))

        assert result.exit_code == 0, result.output
        assert (vault / GRAPH).read_bytes() == before, customised
        assert "未改动" in result.output


def test_unreadable_graph_settings_are_left_alone(run, vault: Path):
    old_vault(run, vault)
    (vault / GRAPH).write_text('{"search": ', encoding="utf-8")

    result = run.run("graph-preset", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert (vault / GRAPH).read_text(encoding="utf-8") == '{"search": '
    assert "读不懂" in result.output and "未改动" in result.output
    assert "调过" not in result.output


def test_graph_already_preset_needs_nothing(run, vault: Path):
    run.run("init", str(vault))

    result = run.run("graph-preset", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "已是预置" in result.output
    assert len(commit_subjects(vault)) == 1


def test_preset_with_settings_from_a_newer_obsidian_counts_as_preset(run, vault: Path):
    """新版 Obsidian 往预置里补了自己的新设置，仍算预置，不当作用户调过的。"""
    run.run("init", str(vault))
    write_graph(vault, {**graph_settings(vault), "某个新版 Obsidian 才有的设置": 0, "scale": 1.7})

    result = run.run("graph-preset", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "已是预置" in result.output


def test_graph_preset_on_an_uninitialised_vault_asks_for_init(run, vault: Path):
    result = run.run("graph-preset", "--vault", str(vault))

    assert result.exit_code != 0
    assert "sub2obsidian init" in result.output
    assert not vault.exists()
