r"""只读检查知识库是否已在 Obsidian 登记：Obsidian 把登记过的目录记在 %APPDATA%\obsidian\obsidian.json。

本工具从不改写这个文件；`obsidian://open` 只能打开登记过的目录，未登记时由用户手动打开一次。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sub2obsidian.config import appdata_dir


def _registry_file() -> Path:
    return appdata_dir() / "obsidian" / "obsidian.json"


def _normalized_path(path: str) -> str:
    """比较路径时忽略大小写、分隔符写法与末尾分隔符。"""
    return os.path.normcase(os.path.normpath(path)).rstrip("\\/")


def registered_in_obsidian(vault: Path) -> bool:
    """知识库是否已在 Obsidian 登记；登记文件不存在或无法解析时视为未登记。"""
    try:
        registry = json.loads(_registry_file().read_text(encoding="utf-8"))
        entries = registry["vaults"].values()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
    target = _normalized_path(str(vault))
    return any(
        isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and _normalized_path(entry["path"]) == target
        for entry in entries
    )
