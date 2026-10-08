"""本机外部工具的检查。"""

from __future__ import annotations

import shutil


class MissingTool(Exception):
    """本机缺少运行所需的工具或环境（如 ffmpeg、GPU 运行库）：整批中止，消息说明如何补齐。"""


FFMPEG_HINT = (
    "未找到 ffmpeg：下载音频需要 ffmpeg。请执行 winget install --id Gyan.FFmpeg -e 安装，"
    "然后重新打开终端，确认 ffmpeg -version 可以运行"
)


def require_ffmpeg() -> str:
    """返回 ffmpeg 的路径；不在 PATH 中时抛 MissingTool。"""
    path = shutil.which("ffmpeg")
    if path is None:
        raise MissingTool(FFMPEG_HINT)
    return path
