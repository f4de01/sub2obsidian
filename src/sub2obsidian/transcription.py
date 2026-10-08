"""转写：「音频 → 带时间戳的分段」。

转写引擎可替换（faster-whisper 是其中一个实现，见 whisper.py），以后可对比 FunASR 等引擎。
平台字幕优先：只有采集后停在「已采集」（没有平台字幕）的视频来源才送去转写。
"""

from __future__ import annotations

import tempfile
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from sub2obsidian import transcript
from sub2obsidian.capture import TRANSCRIPT_FILE, Outcome
from sub2obsidian.credentials import CredentialError
from sub2obsidian.platforms import FetchFailed, PlatformAdapter, SourceUnavailable
from sub2obsidian.sources import Kind, Source, SourceRepository, Status
from sub2obsidian.transcript import Segment, Transcript


class TranscriptionFailed(Exception):
    """单条音频转写失败；来源保持原状态，下次再试。消息面向用户。"""


class Transcriber(Protocol):
    origin: str  # 写入口播稿的「口播稿来源」，如「faster-whisper large-v3-turbo」

    def transcribe(self, audio: Path, terms: Sequence[str]) -> list[Segment]:
        """转写一段音频；terms 是术语表，作为提示传给引擎。失败抛 TranscriptionFailed。"""
        ...


def transcribe_collected(
    vault: Path,
    adapters: Mapping[str, PlatformAdapter],
    transcriber: Transcriber,
    terms: Sequence[str],
) -> Iterator[Outcome]:
    """逐条为「已采集」的视频来源生成口播稿，每完成一条产出一个结果。

    单条失败不影响同批其他来源；缺少 ffmpeg、GPU 运行库等本机环境问题抛 MissingTool，
    此时之前已产出的结果仍然有效。
    """
    repo = SourceRepository(vault)
    for source in repo.in_status(Status.COLLECTED):
        if source.kind is Kind.VIDEO:
            yield _transcribe(source, repo, adapters[source.ref.platform], transcriber, terms)


def summary(outcomes: Sequence[Outcome]) -> str:
    counts = Counter(
        "失败" if not outcome.ok else str(outcome.changed.status)
        for outcome in outcomes
        if outcome.changed is not None
    )
    parts = [f"{label} {counts[label]}" for label in ("已转写", "已失效", "失败") if counts[label]]
    return f"转写 {len(outcomes)} 个来源：{'，'.join(parts)}"


def _transcribe(
    source: Source,
    repo: SourceRepository,
    adapter: PlatformAdapter,
    transcriber: Transcriber,
    terms: Sequence[str],
) -> Outcome:
    ref = source.ref
    try:
        # 临时音频放在知识库之外，转写完（无论成败）连同目录一起删除
        with tempfile.TemporaryDirectory(prefix="sub2obsidian-") as temporary:
            audio = adapter.download_audio(ref, Path(temporary))
            segments = transcriber.transcribe(audio, terms)
    except SourceUnavailable as error:
        repo.transition(source, Status.UNAVAILABLE, reason=str(error))
        return Outcome(f"来源已失效：{ref.display}，{error}", ok=True, changed=source)
    except (FetchFailed, CredentialError, TranscriptionFailed) as error:
        repo.record_failure(source, str(error))
        return Outcome(
            f"转写失败：{ref.display}，{error}（来源保持「{source.status}」，可重试）",
            ok=False,
            changed=source,
        )
    rendered = transcript.render(Transcript(transcriber.origin, segments), source.title)
    repo.add_file(source, TRANSCRIPT_FILE, rendered.encode("utf-8"))
    repo.transition(source, Status.TRANSCRIBED)
    return Outcome(f"已转写：{source.title}（{ref.display}）", ok=True, changed=source)
