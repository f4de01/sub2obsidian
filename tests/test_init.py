"""初始化 (Init)：新建知识库骨架。"""

import json
import re
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


def test_rerun_init_restores_files_the_user_chose_to_ignore(run, vault: Path):
    run.run("init", str(vault))
    with (vault / ".gitignore").open("a", encoding="utf-8") as gitignore:
        gitignore.write(".obsidian/\n")
    (vault / ".obsidian" / "app.json").unlink()

    result = run.run("init", str(vault))

    assert result.exit_code == 0, result.output
    assert (vault / ".obsidian" / "app.json").is_file()


def dataview_queries(page: str) -> list[str]:
    blocks = page.split("```dataview\n")[1:]
    return [block.partition("```")[0] for block in blocks]


def test_init_writes_dataview_status_page_counting_sources_and_listing_todos(run, vault: Path):
    run.run("init", str(vault))

    page = (vault / "来源状态.md").read_text(encoding="utf-8")
    queries = dataview_queries(page)
    # 各状态的数量：按元数据中的来源状态分组计数
    assert any("GROUP BY 来源状态" in query and "length(rows)" in query for query in queries)
    assert all('FROM "原始材料"' in query for query in queries)
    # 待办：待编译、待转写、待筛、采集失败各有一张表
    for todo in ["## 待编译", "## 待转写", "## 待筛", "## 采集失败"]:
        assert todo in page
    assert len(queries) >= 5
    assert git(vault, "ls-files", "来源状态.md").strip() == "来源状态.md"


def chapter(schema: str, heading: str) -> str:
    """Schema 中以 heading（如「## 存档流程」）开头的一节，到下一个同级或更高级标题为止。

    代码块里的示例页面也有 # 开头的行，不算标题。
    """
    level = heading.index(" ")
    lines = schema.splitlines()
    start = lines.index(heading)
    fenced = False
    for end in range(start + 1, len(lines)):
        line = lines[end]
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and (marks := re.match(r"#+ ", line)) and len(marks[0]) - 1 <= level:
            break
    else:
        end = len(lines)
    return "\n".join(lines[start:end])


def test_schema_tells_the_agent_to_compile_through_status_and_mark_compiled(run, vault: Path):
    run.run("init", str(vault))

    schema = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "版本 4" in schema
    assert "sub2obsidian status --vault ." in schema
    assert "sub2obsidian mark-compiled --vault ." in schema
    # 批注 callout 不得改动
    assert "> [!我]" in schema


def test_schema_bilibili_citations_jump_to_the_right_part_and_second(run, vault: Path):
    """B站 多P视频的每个分P是一条来源：出处链接带 p（分P序号）与 t（秒数）。"""
    run.run("init", str(vault))

    schema = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    assert "https://www.bilibili.com/video/BV1GJ411x7h7?p=1&t=205" in schema
    assert "?p=<分P>&t=<秒数>" in schema
    assert "?t=" not in schema  # 不再有不带 p 的 B站 时间戳写法
    raw = chapter(schema, "## 原始材料")
    assert "分P" in raw and "视频标题" in raw
    assert "BV号_pN" in raw


def test_schema_tells_the_agent_how_to_fill_screening_suggestions(run, vault: Path):
    run.run("init", str(vault))

    section = chapter((vault / "AGENTS.md").read_text(encoding="utf-8"), "## 筛选建议流程")
    assert "待筛清单.md" in section
    assert "「建议：」" in section
    # 每条写「是否知识类 + 建议主题域」，勾选留给用户，CLI 负责状态转换
    assert "知识类 · <主题域>" in section
    assert "非知识类" in section
    assert "不要勾选" in section
    assert "sub2obsidian screen" in section


def test_schema_defines_the_synthesis_page(run, vault: Path):
    run.run("init", str(vault))

    section = chapter((vault / "CLAUDE.md").read_text(encoding="utf-8"), "### 综述页")
    # 综述页放在 Wiki/综述/，有自己的页面类型与 frontmatter，论断同样带出处
    assert "Wiki/综述/" in section
    assert "页面类型: 综述页" in section
    assert "每条论断都要带出处" in section


def test_schema_tells_the_agent_to_answer_queries_and_archive_only_on_request(run, vault: Path):
    run.run("init", str(vault))

    schema = (vault / "CLAUDE.md").read_text(encoding="utf-8")
    query = chapter(schema, "## 问询流程")
    # 问询先读 index.md、回答带出处；用户不说「存档」就不写入任何文件
    assert "先读 `index.md`" in query
    assert "出处" in query
    assert "不写入任何文件" in query
    archive = chapter(schema, "## 存档流程")
    # 存档：写综述页，更新 index 与 log，以一次 git 提交结束
    assert "Wiki/综述/" in archive
    assert "index.md" in archive
    assert "log.md" in archive
    assert "git commit" in archive


def test_schema_tells_the_agent_how_to_run_a_full_lint(run, vault: Path):
    run.run("init", str(vault))

    lint = chapter((vault / "AGENTS.md").read_text(encoding="utf-8"), "## 全库体检流程")
    # 找出并修复五类问题，列出待裁决的分歧，Schema 改进建议留给用户决定，以一次 git 提交结束
    for problem in ["矛盾", "孤立页", "重复概念", "断链", "缺失的概念页"]:
        assert problem in lint, problem
    assert "待裁决" in lint
    assert "Schema 改进建议" in lint
    assert "由用户决定" in lint
    assert "git commit" in lint
