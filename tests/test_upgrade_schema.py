"""升级 Schema (upgrade-schema)：把新版 Schema 模板写成待合并版本，由 agent 合并。"""

from pathlib import Path

from test_init import chapter
from vault_git import commit_subjects, git

PENDING = "Schema 待合并.md"


def tracked_and_untracked(vault: Path) -> str:
    return git(vault, "-c", "core.quotepath=false", "status", "--porcelain")


def test_up_to_date_vault_needs_no_upgrade_and_writes_nothing(run, vault: Path):
    run.run("init", str(vault))

    result = run.run("upgrade-schema", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "无需升级" in result.output
    assert not (vault / PENDING).exists()
    assert tracked_and_untracked(vault) == ""
    assert len(commit_subjects(vault)) == 1


OLD_SCHEMA = """# Schema

> 本文件由 sub2obsidian 的 Schema 模板（版本 1）在初始化时生成，此后归本知识库所有。

## 我的定制

概念页一律用中文页面名。
"""


def make_old_schema(vault: Path, schema: str = OLD_SCHEMA) -> None:
    """把知识库的 Schema 换成一份带定制的旧版，并提交（像是很早以前初始化、后来改过的知识库）。"""
    for name in ["CLAUDE.md", "AGENTS.md"]:
        (vault / name).write_text(schema, encoding="utf-8")
    git(vault, "add", "--", "CLAUDE.md", "AGENTS.md")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "旧版 Schema")


def current_template(tmp_path: Path, run) -> str:
    """当前模板渲染出的 Schema：另建一个知识库，读它的 CLAUDE.md。"""
    fresh = tmp_path / "新库"
    run.run("init", str(fresh))
    return (fresh / "CLAUDE.md").read_text(encoding="utf-8")


def test_older_schema_gets_a_pending_version_and_stays_untouched(run, vault: Path, tmp_path: Path):
    run.run("init", str(vault))
    make_old_schema(vault)

    result = run.run("upgrade-schema", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "版本 1" in result.output
    assert PENDING in result.output
    assert "合并 Schema" in result.output
    assert (vault / PENDING).read_text(encoding="utf-8") == current_template(tmp_path, run)
    for name in ["CLAUDE.md", "AGENTS.md"]:
        assert (vault / name).read_text(encoding="utf-8") == OLD_SCHEMA
    # 待合并版本单独提交，工作区保持干净（编译时未提交的改动会被当作用户的批注）
    assert tracked_and_untracked(vault) == ""
    assert git(vault, "ls-files", PENDING).strip() == PENDING
    assert commit_subjects(vault)[0].startswith("upgrade-schema:")


def test_schema_without_a_version_mark_is_treated_as_older(run, vault: Path):
    run.run("init", str(vault))
    make_old_schema(vault, "# Schema\n\n我把开头说明删掉了，只留下自己的规则。\n")

    result = run.run("upgrade-schema", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "没有可识别的版本号" in result.output
    assert "按旧版处理" in result.output
    assert "None" not in result.output
    assert (vault / PENDING).is_file()


def test_rerunning_upgrade_schema_gives_the_same_result(run, vault: Path):
    run.run("init", str(vault))
    make_old_schema(vault)
    first = run.run("upgrade-schema", "--vault", str(vault))
    pending = (vault / PENDING).read_bytes()
    commits = commit_subjects(vault)

    second = run.run("upgrade-schema", "--vault", str(vault))

    assert second.exit_code == 0, second.output
    assert second.output == first.output
    assert (vault / PENDING).read_bytes() == pending
    assert commit_subjects(vault) == commits
    assert tracked_and_untracked(vault) == ""


def test_schema_newer_than_the_template_is_left_alone(run, vault: Path):
    run.run("init", str(vault))
    make_old_schema(vault, OLD_SCHEMA.replace("版本 1", "版本 999"))

    result = run.run("upgrade-schema", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "版本 999" in result.output
    assert "升级 sub2obsidian" in result.output
    assert not (vault / PENDING).exists()
    assert tracked_and_untracked(vault) == ""


def test_the_older_of_claude_md_and_agents_md_decides(run, vault: Path):
    """两份 Schema 本应相同；只改了一份时，按较旧的一份判断，免得漏掉升级。"""
    run.run("init", str(vault))
    (vault / "CLAUDE.md").write_text(OLD_SCHEMA, encoding="utf-8")
    git(vault, "add", "--", "CLAUDE.md")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "只改了 CLAUDE.md")

    result = run.run("upgrade-schema", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "版本 1" in result.output
    assert (vault / PENDING).is_file()


def test_vault_without_schema_is_told_to_run_init(run, vault: Path):
    run.run("init", str(vault))
    (vault / "CLAUDE.md").unlink()
    (vault / "AGENTS.md").unlink()

    result = run.run("upgrade-schema", "--vault", str(vault))

    assert result.exit_code != 0
    assert "sub2obsidian init" in result.output
    assert not (vault / PENDING).exists()


def test_a_single_remaining_schema_file_decides(run, vault: Path):
    run.run("init", str(vault))
    make_old_schema(vault)
    (vault / "AGENTS.md").unlink()

    result = run.run("upgrade-schema", "--vault", str(vault))

    assert result.exit_code == 0, result.output
    assert "版本 1" in result.output
    assert (vault / PENDING).is_file()


def test_schema_tells_the_agent_how_to_merge_a_pending_version(run, vault: Path):
    run.run("init", str(vault))

    schema = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    opening = schema.partition("## 不可违反的规则")[0]
    assert "「合并 Schema」" in opening  # 开头列出用户的口令
    merge = chapter(schema, "## 合并 Schema 流程")
    assert PENDING in merge
    assert "sub2obsidian upgrade-schema --vault ." in merge
    # 保留知识库的定制、并入新模板的变化、更新版本号、删除待合并文件、提交 git
    assert "定制" in merge
    assert "版本号" in merge
    assert f'git rm --quiet -- "{PENDING}"' in merge
    assert "git commit" in merge
    assert "Schema: 合并模板版本" in merge
    # 合并只动 Schema，不改 Wiki、原始材料与我的笔记
    assert "原始材料/" in merge and "我的笔记/" in merge
