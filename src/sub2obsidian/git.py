"""知识库 git 仓库的最小操作：建库与按路径提交。"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

# 用户没有配置 git 身份时，以工具身份提交，避免初始化失败。
FALLBACK_IDENTITY = ["-c", "user.name=sub2obsidian", "-c", "user.email=sub2obsidian@localhost"]


class GitError(RuntimeError):
    pass


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as error:
        raise GitError("找不到 git，请先安装 Git for Windows 并加入 PATH") from error


def _git(root: Path, *args: str) -> str:
    result = _run(root, *args)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout


def ensure_repo(root: Path) -> bool:
    """root 自身不是 git 仓库时就地新建一个；返回是否新建。

    新仓库关闭换行转换（文件按 LF 原样入库）并让 git 原样显示中文路径。
    """
    if (root / ".git").exists():
        return False
    _git(root, "init", "--quiet")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.quotepath", "false")
    return True


def _has_identity(root: Path) -> bool:
    return all(
        _run(root, "config", key).stdout.strip() for key in ("user.name", "user.email")
    )


def _ignored(root: Path, paths: Sequence[str]) -> set[str]:
    return set(_run(root, "check-ignore", "--no-index", "--", *paths).stdout.splitlines())


def commit_paths(root: Path, paths: Sequence[str], message: str) -> None:
    """只提交给定路径（相对 root），不卷入用户在知识库中的其他改动。

    被 .gitignore 排除的路径与没有变化的路径不提交。
    """
    ignored = _ignored(root, paths) if paths else set()
    paths = [path for path in paths if path not in ignored]
    if not paths:
        return
    _git(root, "add", "--", *paths)
    if _run(root, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        return
    identity = [] if _has_identity(root) else FALLBACK_IDENTITY
    _git(root, *identity, "commit", "--quiet", "-m", message, "--", *paths)
