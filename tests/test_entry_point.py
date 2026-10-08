"""安装后的命令入口：从任意目录调用。"""

import os
import subprocess
import sys
from pathlib import Path


def test_cli_runs_from_any_directory(tmp_path: Path):
    elsewhere = tmp_path / "任意目录"
    elsewhere.mkdir()

    result = subprocess.run(
        [sys.executable, "-m", "sub2obsidian", "--help"],
        cwd=elsewhere,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "init" in result.stdout
    assert "知识库" in result.stdout
