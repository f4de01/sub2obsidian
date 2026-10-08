"""转写：「音频 → 带时间戳的分段」。

转写引擎可替换（faster-whisper 是其中一个实现，见 whisper.py），以后可对比 FunASR 等引擎。
平台字幕优先：只有采集后停在「已采集」（没有平台字幕）的视频来源才送去转写。

ASR 在没有人声的音频（纯音乐、只有画面）上会吐出训练数据里的字幕署名、片尾语等已知幻觉句。
这些句子从转写结果中滤掉；滤掉后什么都不剩的视频照常转为「已转写」，但在元数据中标记
「无口播」并写明原因，编译时只依据简介与封面（见 Schema）。
"""

from __future__ import annotations

import re
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sub2obsidian import transcript
from sub2obsidian.batch import Outcome, changed_sources
from sub2obsidian.capture import TRANSCRIPT_FILE
from sub2obsidian.credentials import CredentialError
from sub2obsidian.platforms import FetchFailed, PlatformAdapter, SourceUnavailable
from sub2obsidian.sources import Kind, RawMaterialExists, Source, SourceRepository, Status
from sub2obsidian.transcript import Segment, Transcript


# 没有平台字幕的平台：视频只能靠 ASR，capture 采集后当场转写。
# 其他平台没有字幕的视频停在「已采集」，等 transcribe 批量转写。
ASR_ONLY_PLATFORMS = {"douyin"}


# Whisper 在没有人声的音频上常吐出的句子（多来自训练数据中的字幕署名、片尾语与频道口号）。
# 比较时忽略空白、标点与大小写；一段只由这些句子（可重复）组成时整段滤掉。
KNOWN_HALLUCINATIONS = (
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    "明镜需要您的支持 欢迎订阅明镜",
    "优优独播剧场——YoYo Television Series Exclusive",
    "字幕志愿者 杨茜茜",
    "字幕志愿者 李宗盛",
    "中文字幕志愿者 李宗盛",
    "字幕由Amara.org社区提供",
    "小编字幕由Amara.org社区提供",
    "由 Amara.org 社区提供的字幕",
    "谢谢观看",
    "感谢观看",
    "Subtitles by the Amara.org community",
    "Thank you",
    "Thank you for watching",
    "Thanks for watching",
    "Thank you very much",
    "you",
)


@dataclass(frozen=True)
class TranscribeSettings:
    """用户在 config.toml [transcribe] 中的转写设置。"""

    terms: Sequence[str] = ()  # 术语表，作为提示传给引擎
    hallucinations: Sequence[str] = ()  # 追加的已知幻觉句，与内置的一起滤掉


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
    settings: TranscribeSettings,
) -> Iterator[Outcome]:
    """逐条为「已采集」的视频来源生成口播稿，每完成一条产出一个结果。

    单条失败不影响同批其他来源；缺少 ffmpeg、GPU 运行库等本机环境问题抛 MissingTool，
    此时之前已产出的结果仍然有效。
    """
    repo = SourceRepository(vault)
    for source in repo.in_status(Status.COLLECTED):
        if source.kind is Kind.VIDEO:
            yield _transcribe(source, repo, adapters[source.ref.platform], transcriber, settings)


def transcribe_captured(
    vault: Path,
    outcomes: Iterable[Outcome],
    adapters: Mapping[str, PlatformAdapter],
    transcriber: Transcriber,
    settings: TranscribeSettings,
) -> Iterator[Outcome]:
    """capture 刚采集到的视频中，只能靠 ASR 的（抖音）当场转写，每完成一条产出一个结果。"""
    repo = SourceRepository(vault)
    for source in changed_sources(outcomes):
        if (
            source.ref.platform in ASR_ONLY_PLATFORMS
            and source.kind is Kind.VIDEO
            and source.status is Status.COLLECTED
        ):
            yield _transcribe(source, repo, adapters[source.ref.platform], transcriber, settings)


def summarize(outcomes: Sequence[Outcome]) -> str:
    counts = Counter(
        "失败" if not outcome.ok else str(outcome.changed.status)
        for outcome in outcomes
        if outcome.changed is not None
    )
    no_speech = sum(
        1 for outcome in outcomes if outcome.ok and outcome.changed and outcome.changed.no_speech
    )

    def part(label: str) -> str:
        text = f"{label} {counts[label]}"
        return f"{text}（无口播 {no_speech}）" if label == "已转写" and no_speech else text

    parts = [part(label) for label in ("已转写", "已失效", "失败") if counts[label]]
    return f"转写 {len(outcomes)} 个来源：{'，'.join(parts)}"


def _normalized(text: str) -> str:
    """比较幻觉句用：去掉空白、标点与符号，忽略大小写。"""
    return "".join(c for c in text.casefold() if unicodedata.category(c)[0] not in "PSZC")


def _hallucination_pattern(extra: Sequence[str]) -> re.Pattern[str]:
    phrases = {_normalized(phrase) for phrase in (*KNOWN_HALLUCINATIONS, *extra)} - {""}
    alternatives = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(f"(?:{alternatives})+")


def _drop_hallucinations(
    segments: Sequence[Segment], extra: Sequence[str]
) -> tuple[list[Segment], list[str]]:
    """（保留的分段, 滤掉的幻觉句原文）；只由已知幻觉句组成的分段整段滤掉。"""
    pattern = _hallucination_pattern(extra)
    kept: list[Segment] = []
    dropped: list[str] = []
    for segment in segments:
        if pattern.fullmatch(_normalized(segment.text)):
            dropped.append(" ".join(segment.text.split()))
        else:
            kept.append(segment)
    return kept, dropped


def _no_speech_reason(dropped: Sequence[str]) -> str:
    """口播稿为空时，元数据「无口播」中写的原因。"""
    if not dropped:
        return "ASR 没有识别出任何口播（多为纯音乐或只有画面；有人声的话请检查音频后重新转写）"
    quoted = "、".join(f"「{text}」" for text in dict.fromkeys(dropped))
    return f"ASR 只输出了已知幻觉句{quoted}，已滤掉；视频没有口播（纯音乐或只有画面）"


def _transcribe(
    source: Source,
    repo: SourceRepository,
    adapter: PlatformAdapter,
    transcriber: Transcriber,
    settings: TranscribeSettings,
) -> Outcome:
    ref = source.ref
    try:
        # 临时音频放在知识库之外，转写完（无论成败）连同目录一起删除
        with tempfile.TemporaryDirectory(prefix="sub2obsidian-") as temporary:
            audio = adapter.download_audio(ref, Path(temporary))
            segments = transcriber.transcribe(audio, settings.terms)
        segments, dropped = _drop_hallucinations(segments, settings.hallucinations)
        rendered = transcript.render(Transcript(transcriber.origin, segments), source.title)
        repo.add_file(source, TRANSCRIPT_FILE, rendered.encode("utf-8"))
    except SourceUnavailable as error:
        repo.transition(source, Status.UNAVAILABLE, reason=str(error))
        return Outcome(f"来源已失效：{ref.display}，{error}", ok=True, changed=source)
    except (FetchFailed, CredentialError, TranscriptionFailed, RawMaterialExists) as error:
        repo.record_failure(source, str(error))
        return Outcome(
            f"转写失败：{ref.display}，{error}（来源保持「{source.status}」，可重试）",
            ok=False,
            changed=source,
        )
    no_speech = None if segments else _no_speech_reason(dropped)
    repo.transition(source, Status.TRANSCRIBED, no_speech=no_speech)
    label = "已转写（无口播）" if no_speech else "已转写"
    return Outcome(f"{label}：{source.title}（{ref.display}）", ok=True, changed=source)
