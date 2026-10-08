"""初始化 (Init)：新建知识库骨架。"""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from vault_git import commit_subjects, git, is_ignored


def test_init_creates_vault_skeleton(run, vault: Path):
    result = run.run("init", str(vault))

    assert result.exit_code == 0, result.output
    for directory in [
        "原始材料",
        "Wiki/来源",
        "Wiki/概念",
        "Wiki/综述",
        "Wiki/主题域",
        "我的笔记",
        "附件",
    ]:
        assert (vault / directory).is_dir(), directory
    for file in ["index.md", "log.md", "CLAUDE.md", "AGENTS.md"]:
        assert (vault / file).is_file(), file


def test_init_renders_schema_template_into_claude_md_and_agents_md(run, vault: Path):
    run.run("init", str(vault))

    claude = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (vault / "AGENTS.md").read_text(encoding="utf-8")
    assert claude == agents
    assert "# Schema" in claude
    # 渲染后的 Schema 指明知识库各区的位置，且不残留模板占位符
    for area in ["原始材料/", "Wiki/来源/", "Wiki/概念/", "我的笔记/"]:
        assert area in claude
    assert "$" not in claude


def test_init_writes_index_and_log(run, vault: Path):
    run.run("init", str(vault))

    assert (vault / "index.md").read_text(encoding="utf-8").startswith("# 索引")
    log = (vault / "log.md").read_text(encoding="utf-8")
    assert log.startswith("# 日志")
    assert "] init | 初始化知识库" in log


def test_init_preconfigures_obsidian_with_dataview_enabled(run, vault: Path):
    run.run("init", str(vault))

    obsidian = vault / ".obsidian"
    app = json.loads((obsidian / "app.json").read_text(encoding="utf-8"))
    assert app["attachmentFolderPath"] == "附件"
    assert app["useMarkdownLinks"] is False  # wikilink 格式
    assert json.loads((obsidian / "community-plugins.json").read_text(encoding="utf-8")) == [
        "dataview"
    ]
    dataview = obsidian / "plugins" / "dataview"
    manifest = json.loads((dataview / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "dataview"
    assert (dataview / "main.js").stat().st_size > 100_000
    assert (dataview / "styles.css").is_file()


def test_first_init_makes_vault_a_git_repo_with_one_commit(run, vault: Path):
    run.run("init", str(vault))

    assert len(commit_subjects(vault)) == 1
    assert git(vault, "status", "--porcelain") == ""
    assert git(vault, "ls-files", "--error-unmatch", "CLAUDE.md").strip() == "CLAUDE.md"


def test_gitignore_excludes_obsidian_workspace_state(run, vault: Path):
    run.run("init", str(vault))

    for state_file in [".obsidian/workspace.json", ".obsidian/workspace-mobile.json"]:
        assert is_ignored(vault, state_file), state_file
    assert not is_ignored(vault, ".obsidian/app.json")


def test_rerun_init_never_overwrites_existing_files(run, vault: Path):
    run.run("init", str(vault))
    edited = "# Schema\n\n我自己改过的 Schema\n"
    (vault / "CLAUDE.md").write_text(edited, encoding="utf-8")
    (vault / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")

    result = run.run("init", str(vault))

    assert result.exit_code == 0, result.output
    assert (vault / "CLAUDE.md").read_text(encoding="utf-8") == edited
    assert (vault / ".obsidian" / "app.json").read_text(encoding="utf-8") == "{}"


def test_rerun_init_restores_only_missing_items(run, vault: Path):
    run.run("init", str(vault))
    (vault / "index.md").unlink()
    (vault / "Wiki" / "综述").rmdir()
    (vault / "我的笔记" / "想法.md").write_text("还没提交的笔记", encoding="utf-8")

    run.run("init", str(vault))

    assert (vault / "index.md").read_text(encoding="utf-8").startswith("# 索引")
    assert (vault / "Wiki" / "综述").is_dir()
    # 补回的文件入库，用户未提交的改动不被卷入提交
    assert git(vault, "-c", "core.quotepath=false", "status", "--porcelain").strip() == "?? 我的笔记/"


def test_rerun_init_commits_restored_files(run, vault: Path):
    run.run("init", str(vault))
    git(vault, "rm", "--quiet", "log.md")
    git(vault, "commit", "--quiet", "-m", "删掉日志")

    run.run("init", str(vault))

    assert commit_subjects(vault)[0] == "init: 补齐知识库缺失项"
    assert git(vault, "ls-files", "log.md").strip() == "log.md"


def test_rerun_init_on_complete_vault_makes_no_new_commit(run, vault: Path):
    run.run("init", str(vault))

    run.run("init", str(vault))

    assert len(commit_subjects(vault)) == 1


def test_init_opens_vault_in_obsidian_via_uri(run, launcher, vault: Path):
    run.run("init", str(vault))

    assert len(launcher.opened) == 1
    uri = launcher.opened[0]
    parts = urlsplit(uri)
    assert (parts.scheme, parts.netloc) == ("obsidian", "open")
    assert parse_qs(parts.query) == {"path": [str(vault.resolve())]}
    # 中文按 UTF-8 百分号编码，「知识库」= E7 9F A5 E8 AF 86 E5 BA 93
    assert uri.endswith("%5C%E7%9F%A5%E8%AF%86%E5%BA%93")


def test_init_reports_what_it_did(run, vault: Path):
    first = run.run("init", str(vault))
    (vault / "log.md").unlink()
    second = run.run("init", str(vault))
    third = run.run("init", str(vault))

    assert f"已新建知识库：{vault.resolve()}" in first.output
    assert "已补齐 1 项：log.md" in second.output
    assert "知识库完整，无需补齐" in third.output


def test_init_refuses_a_path_that_is_a_file(run, launcher, tmp_path: Path):
    not_a_dir = tmp_path / "笔记.md"
    not_a_dir.write_text("x", encoding="utf-8")

    result = run.run("init", str(not_a_dir))

    assert result.exit_code != 0
    assert "不是目录" in result.output
    assert launcher.opened == []
