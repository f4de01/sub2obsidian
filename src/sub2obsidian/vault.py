"""知识库初始化：目录骨架、索引与日志、Schema。"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from string import Template

from sub2obsidian import git

SCHEMA_VERSION = "2"

RAW_DIR = "原始材料"
SOURCES_DIR = "Wiki/来源"
CONCEPTS_DIR = "Wiki/概念"
SYNTHESES_DIR = "Wiki/综述"
DOMAINS_DIR = "Wiki/主题域"
NOTES_DIR = "我的笔记"
ATTACHMENTS_DIR = "附件"
STATUS_PAGE = "来源状态.md"
SCREENING_LIST = "待筛清单.md"  # 由 sync 生成，screen 读取

DIRECTORIES = [
    RAW_DIR,
    SOURCES_DIR,
    CONCEPTS_DIR,
    SYNTHESES_DIR,
    DOMAINS_DIR,
    NOTES_DIR,
    ATTACHMENTS_DIR,
]

ASSETS = files("sub2obsidian") / "assets"


def _render(asset: str) -> str:
    """渲染随包的模板：$-占位符换成知识库各区的位置（字面的 $ 在模板中写作 $$）。"""
    template = Template((ASSETS / asset).read_text(encoding="utf-8"))
    return template.substitute(
        schema_version=SCHEMA_VERSION,
        raw_dir=RAW_DIR,
        sources_dir=SOURCES_DIR,
        concepts_dir=CONCEPTS_DIR,
        syntheses_dir=SYNTHESES_DIR,
        domains_dir=DOMAINS_DIR,
        notes_dir=NOTES_DIR,
        attachments_dir=ATTACHMENTS_DIR,
        status_page=STATUS_PAGE,
        screening_list=SCREENING_LIST,
    )


def _index() -> str:
    return "# 索引\n\n## 主题域\n\n## 概念\n\n## 来源\n\n## 综述\n"


def _log(today: dt.date) -> str:
    return f"# 日志\n\n## [{today.isoformat()}] init | 初始化知识库\n"


# Obsidian 的工作区状态随每次打开而变，与回收站、系统杂项一起不进版本库。
GITIGNORE = """.obsidian/workspace.json
.obsidian/workspace-mobile.json
.trash/
.DS_Store
"""

DATAVIEW_FILES = ["main.js", "manifest.json", "styles.css", "LICENSE"]


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _text(value: str) -> bytes:
    return value.encode("utf-8")


def _obsidian_config() -> dict[str, bytes]:
    """.obsidian 预置配置：附件目录、wikilink 格式、Dataview 插件已安装并启用。"""
    config = {
        ".obsidian/app.json": _json(
            {
                "attachmentFolderPath": ATTACHMENTS_DIR,
                "useMarkdownLinks": False,
                "newLinkFormat": "shortest",
                "alwaysUpdateLinks": True,
            }
        ),
        ".obsidian/community-plugins.json": _json(["dataview"]),
    }
    dataview = ASSETS / "obsidian" / "plugins" / "dataview"
    for name in DATAVIEW_FILES:
        config[f".obsidian/plugins/dataview/{name}"] = (dataview / name).read_bytes()
    return config


def _skeleton_files(today: dt.date) -> dict[str, bytes]:
    """初始化负责的全部文件：知识库内相对路径 → 内容。"""
    schema = _text(_render("schema_template.md"))
    return {
        "index.md": _text(_index()),
        "log.md": _text(_log(today)),
        "CLAUDE.md": schema,
        "AGENTS.md": schema,
        STATUS_PAGE: _text(_render("status_page.md")),
        ".gitignore": _text(GITIGNORE),
        **_obsidian_config(),
    }


@dataclass(frozen=True)
class InitResult:
    new_vault: bool
    created: list[str]  # 本次补上的目录（以 / 结尾）与文件，知识库内相对路径


def init_vault(root: Path) -> InitResult:
    """新建或补全知识库：只创建缺失的目录与文件，从不覆盖已有文件。"""
    created_dirs: list[str] = []
    for directory in DIRECTORIES:
        target = root / directory
        if not target.is_dir():
            target.mkdir(parents=True)
            created_dirs.append(directory + "/")
    created_files: list[str] = []
    for relative, content in _skeleton_files(dt.date.today()).items():
        target = root / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        created_files.append(relative)
    new_vault = git.ensure_repo(root)
    message = "init: 初始化知识库" if new_vault else "init: 补齐知识库缺失项"
    git.commit_paths(root, created_files, message)
    return InitResult(new_vault=new_vault, created=created_dirs + created_files)
