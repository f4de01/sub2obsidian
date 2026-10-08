"""知识库初始化（目录骨架、索引与日志、Schema、Obsidian 预置）、图谱预置与 Schema 升级。"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from pathlib import Path
from string import Template

from sub2obsidian import git

SCHEMA_VERSION = 7

RAW_DIR = "原始材料"
WIKI_DIR = "Wiki"
SOURCES_DIR = "Wiki/来源"
CONCEPTS_DIR = "Wiki/概念"
SYNTHESES_DIR = "Wiki/综述"
DOMAINS_DIR = "Wiki/主题域"
SUBTOPICS_DIR = "Wiki/子主题"
NOTES_DIR = "我的笔记"
ATTACHMENTS_DIR = "附件"
STATUS_PAGE = "来源状态.md"
SCREENING_LIST = "待筛清单.md"  # 由 sync 生成，screen 读取
SCHEMA_FILES = ["CLAUDE.md", "AGENTS.md"]  # 内容相同的两份 Schema
PENDING_SCHEMA = "Schema 待合并.md"  # upgrade-schema 写出，agent 合并后删除
GRAPH_SETTINGS = ".obsidian/graph.json"  # Obsidian 关系图谱的设置

DIRECTORIES = [
    RAW_DIR,
    SOURCES_DIR,
    CONCEPTS_DIR,
    SYNTHESES_DIR,
    DOMAINS_DIR,
    SUBTOPICS_DIR,
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
        subtopics_dir=SUBTOPICS_DIR,
        notes_dir=NOTES_DIR,
        attachments_dir=ATTACHMENTS_DIR,
        status_page=STATUS_PAGE,
        screening_list=SCREENING_LIST,
        pending_schema=PENDING_SCHEMA,
        graph_settings=GRAPH_SETTINGS,
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


def _colour(folder: str, rgb: int) -> dict[str, object]:
    return {"query": f"path:{folder}/", "color": {"a": 1, "rgb": rgb}}


# 关系图谱预置：只显示 Wiki 与我的笔记——原始材料、Schema、index、log、来源状态、待筛清单、
# 待合并的 Schema 都不在这两处，自然被滤掉；按页面类型着色（Okabe-Ito 色盲友好配色）。
GRAPH_PRESET: dict[str, object] = {
    "search": f"path:{WIKI_DIR}/ OR path:{NOTES_DIR}/",
    "showTags": False,
    "showAttachments": False,
    "hideUnresolved": True,  # 示例链接、断链形成的幽灵节点不显示
    "showOrphans": True,  # 孤立页留在图上，便于发现
    "colorGroups": [
        _colour(DOMAINS_DIR, 0xD55E00),  # 主题域：朱红，醒目
        _colour(SUBTOPICS_DIR, 0xE69F00),  # 子主题：橙，醒目
        _colour(CONCEPTS_DIR, 0x0072B2),  # 概念页：蓝，主色
        _colour(SYNTHESES_DIR, 0x009E73),  # 综述页：蓝绿
        _colour(SOURCES_DIR, 0x9E9E9E),  # 来源页：灰，退后
        _colour(NOTES_DIR, 0xCC79A7),  # 我的笔记：紫红
    ],
    "showArrow": False,
    "textFadeMultiplier": -1,  # 缩小时页面名也早些显示
    "nodeSizeMultiplier": 1.3,
    "lineSizeMultiplier": 0.5,  # 细线，减少杂乱
    "centerStrength": 0.4,
    "repelStrength": 15,  # 节点推得更开，簇之间留出空隙
    "linkStrength": 0.8,
    "linkDistance": 200,
}


def _skeleton_files(today: dt.date) -> dict[str, bytes]:
    """初始化负责的全部文件：知识库内相对路径 → 内容。"""
    schema = _text(_render("schema_template.md"))
    return {
        "index.md": _text(_index()),
        "log.md": _text(_log(today)),
        **{name: schema for name in SCHEMA_FILES},
        STATUS_PAGE: _text(_render("status_page.md")),
        ".gitignore": _text(GITIGNORE),
        **_obsidian_config(),
        GRAPH_SETTINGS: _json(GRAPH_PRESET),
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


class GraphState(Enum):
    WRITTEN = "已写入预置"
    ALREADY = "已是预置"
    CUSTOMISED = "用户已自定义"


# Obsidian 第一次打开关系图谱时写下的设置；只有这些键、且都是这些值的 graph.json 没有用户的选择。
_OBSIDIAN_GRAPH_DEFAULTS: dict[str, object] = {
    "search": "",
    "showTags": False,
    "showAttachments": False,
    "hideUnresolved": False,
    "showOrphans": True,
    "colorGroups": [],
    "showArrow": False,
    "textFadeMultiplier": 0,
    "nodeSizeMultiplier": 1,
    "lineSizeMultiplier": 1,
    "centerStrength": 0.518713248970312,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 250,
}


def _graph_choices(settings: dict[str, object]) -> dict[str, object]:
    """去掉缩放、面板开合这些随浏览而变的界面状态，只留用户能调的设置。"""
    return {
        key: value
        for key, value in settings.items()
        if key not in ("scale", "close") and not key.startswith("collapse-")
    }


def _same(a: object, b: object) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return not isinstance(b, bool) and math.isclose(a, b)
    return a == b


def _is_obsidian_default(settings: dict[str, object]) -> bool:
    return all(
        key in _OBSIDIAN_GRAPH_DEFAULTS and _same(value, _OBSIDIAN_GRAPH_DEFAULTS[key])
        for key, value in _graph_choices(settings).items()
    )


def _read_graph(target: Path) -> dict[str, object] | None:
    """graph.json 的内容；文件不存在时为空设置，读不懂（不是 JSON 对象）时为 None。"""
    if not target.exists():
        return {}
    try:
        settings = json.loads(target.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return settings if isinstance(settings, dict) else None


def preset_graph(root: Path) -> GraphState:
    """graph.json 缺失或仍是 Obsidian 的默认设置时写入图谱预置并提交；用户调过的从不覆盖。"""
    target = root / GRAPH_SETTINGS
    settings = _read_graph(target)
    if settings is None:
        return GraphState.CUSTOMISED
    if _graph_choices(settings) == GRAPH_PRESET:
        return GraphState.ALREADY
    if not _is_obsidian_default(settings):
        return GraphState.CUSTOMISED
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_json(GRAPH_PRESET))
    git.commit_paths(root, [GRAPH_SETTINGS], "graph-preset: 写入关系图谱预置")
    return GraphState.WRITTEN


# Schema 开头说明中的版本标记，如「Schema 模板（版本 5）」；容忍半角括号与多余空格
_VERSION_MARK = re.compile(r"Schema\s*模板\s*[（(]\s*版本\s*(\d+)\s*[）)]")


class SchemaState(Enum):
    UP_TO_DATE = "已是最新"
    NEWER = "比模板新"  # 工具比知识库旧
    PENDING = "已写出待合并版本"


@dataclass(frozen=True)
class SchemaUpgrade:
    state: SchemaState
    current: int | None  # 知识库 Schema 的版本；没有可识别的版本标记时为 None


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
    if current == SCHEMA_VERSION:
        return SchemaUpgrade(SchemaState.UP_TO_DATE, current)
    if current is not None and current > SCHEMA_VERSION:
        return SchemaUpgrade(SchemaState.NEWER, current)
    (root / PENDING_SCHEMA).write_bytes(_text(_render("schema_template.md")))
    git.commit_paths(
        root, [PENDING_SCHEMA], f"upgrade-schema: 写出待合并的 Schema 版本 {SCHEMA_VERSION}"
    )
    return SchemaUpgrade(SchemaState.PENDING, current)
