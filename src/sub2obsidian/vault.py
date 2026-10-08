"""知识库初始化（目录骨架、索引与日志、Schema）与 Schema 升级。"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from string import Template

from sub2obsidian import git

SCHEMA_VERSION = 5

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
        pending_schema=PENDING_SCHEMA,
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


SCHEMA_FILES = ["CLAUDE.md", "AGENTS.md"]
PENDING_SCHEMA = "Schema 待合并.md"  # upgrade-schema 写出、agent 合并后删除
# Schema 开头说明中的版本标记，如「Schema 模板（版本 5）」；容忍半角括号与多余空格
_VERSION_MARK = re.compile(r"Schema\s*模板\s*[（(]\s*版本\s*(\d+)\s*[）)]")


@dataclass(frozen=True)
class SchemaUpgrade:
    current: int | None  # 知识库 Schema 的版本；没有可识别的版本标记时为 None
    pending: bool  # 是否写出了待合并版本


class NoSchema(RuntimeError):
    """知识库中 CLAUDE.md 与 AGENTS.md 都不在。"""


def _schema_version(root: Path) -> int | None:
    """知识库 Schema 的版本：取 CLAUDE.md 与 AGENTS.md 中较旧的一份，任一份没有版本标记即为 None。

    用户可能删改过开头说明，只认每份中的第一处版本标记；只剩一份时按那一份。
    """
    schemas = [root / name for name in SCHEMA_FILES if (root / name).is_file()]
    if not schemas:
        raise NoSchema(f"知识库中没有 Schema（{' 与 '.join(SCHEMA_FILES)}）")
    versions: list[int] = []
    for schema in schemas:
        mark = _VERSION_MARK.search(schema.read_text(encoding="utf-8", errors="replace"))
        if mark is None:
            return None
        versions.append(int(mark[1]))
    return min(versions)


def upgrade_schema(root: Path) -> SchemaUpgrade:
    """知识库的 Schema 比模板旧时，把模板渲染为待合并版本写进知识库并提交；不动现有 Schema。

    已是最新（或比模板还新）时不写任何文件。合并由 agent 按 Schema 中的「合并 Schema 流程」完成。
    """
    current = _schema_version(root)
    if current is not None and current >= SCHEMA_VERSION:
        return SchemaUpgrade(current=current, pending=False)
    (root / PENDING_SCHEMA).write_bytes(_text(_render("schema_template.md")))
    git.commit_paths(
        root, [PENDING_SCHEMA], f"upgrade-schema: 写出待合并的 Schema 版本 {SCHEMA_VERSION}"
    )
    return SchemaUpgrade(current=current, pending=True)
