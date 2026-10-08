"""测试辅助：从外部观察知识库的 git 仓库。"""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(vault: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(vault), *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    return result.stdout


def commit_subjects(vault: Path) -> list[str]:
    return git(vault, "log", "--format=%s").splitlines()


def is_ignored(vault: Path, relative: str) -> bool:
    return git(vault, "check-ignore", relative).strip() == relative


def commit(vault: Path, message: str, *paths: str) -> None:
    """以测试身份提交给定路径，模拟用户在知识库中的提交。"""
    git(vault, "add", "--", *paths)
    git(vault, "-c", "user.name=测试", "-c", "user.email=test@localhost", "commit", "--quiet", "-m", message)
