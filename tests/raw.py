"""测试辅助：从外部读取知识库原始材料区中的来源。"""

from __future__ import annotations

from pathlib import Path

import yaml

RAW = "原始材料"


def source_dir(vault: Path, platform: str, platform_id: str) -> Path:
    return vault / RAW / platform / platform_id


def source_dirs(vault: Path) -> list[Path]:
    return sorted(path for path in (vault / RAW).glob("*/*") if path.is_dir())


def read_metadata(vault: Path, platform: str, platform_id: str) -> dict:
    """来源元数据：元数据.md 的 frontmatter。"""
    text = (source_dir(vault, platform, platform_id) / "元数据.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    frontmatter, _, _ = text[4:].partition("\n---\n")
    return yaml.safe_load(frontmatter)
