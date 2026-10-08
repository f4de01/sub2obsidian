"""文件写入的公共约定。"""

from __future__ import annotations

from pathlib import Path


def write_text_atomically(path: Path, text: str) -> None:
    """以 UTF-8、LF 换行写入文本：先写同目录的临时文件再替换，中途中断也不会留下写坏的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
