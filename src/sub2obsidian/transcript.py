"""统一的带时间戳口播稿格式：平台字幕与 ASR 转写都输出这一种。

口播稿.md 的样子：

    ---
    口播稿来源: B站 AI 字幕（ai-zh）
    ---
    # 口播稿：<标题>

    [00:00:00] 第一段
    [00:00:03] 第二段

每段一行，以该段起始时刻 `[时:分:秒]`（向下取整到秒）开头；文本原样保存，不做纠错。
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Segment:
    start: float  # 秒
    end: float  # 秒
    text: str


@dataclass(frozen=True)
class Transcript:
    origin: str  # 口播稿来源，如「B站 CC 字幕（zh-CN）」「faster-whisper large-v3-turbo」
    segments: list[Segment]


def timestamp(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def render(transcript: Transcript, title: str) -> str:
    frontmatter = yaml.safe_dump(
        {"口播稿来源": transcript.origin}, allow_unicode=True, sort_keys=False
    )
    lines = []
    for segment in transcript.segments:
        text = " ".join(segment.text.split())
        if text:
            lines.append(f"[{timestamp(segment.start)}] {text}\n")
    return f"---\n{frontmatter}---\n# 口播稿：{title}\n\n" + "".join(lines)


def _srt_seconds(timecode: str) -> float:
    hours, minutes, seconds = timecode.strip().replace(",", ".").split(":")
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def parse_srt(text: str) -> list[Segment]:
    """SRT 字幕 → 分段；多行字幕保留换行。"""
    segments = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = block.strip().split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        start, _, end = lines[timing].partition("-->")
        content = "\n".join(lines[timing + 1 :]).strip()
        if content:
            segments.append(Segment(_srt_seconds(start), _srt_seconds(end.split()[0]), content))
    return segments
