"""faster-whisper 转写引擎的集成测试。

用真实模型与 GPU 的测试只在本机有 CUDA GPU、且 large-v3-turbo 模型已在 Hugging Face 缓存中时运行，
否则跳过（测试从不联网下载模型）。测试音频 fixtures/audio/检索增强生成.wav 由 Windows 自带的
中文语音合成（Microsoft Huihui）生成，16 kHz 单声道，约 12 秒，朗读：

    大家好，今天我们来聊一聊检索增强生成。它先从知识库里检索相关的资料，再交给大模型生成回答。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sub2obsidian.tools import MissingTool
from sub2obsidian.transcription import TranscriptionFailed
from sub2obsidian.whisper import MODEL, FasterWhisperTranscriber, model_cached

AUDIO = Path(__file__).parent / "fixtures" / "audio" / "检索增强生成.wav"


def gpu_available() -> bool:
    import ctranslate2

    return ctranslate2.get_cuda_device_count() > 0


needs_gpu_and_model = pytest.mark.skipif(
    not (gpu_available() and model_cached(MODEL)),
    reason=f"需要本机 CUDA GPU 与已缓存的 faster-whisper {MODEL} 模型",
)


@pytest.fixture(scope="module")
def engine() -> FasterWhisperTranscriber:
    return FasterWhisperTranscriber(local_files_only=True)


@needs_gpu_and_model
def test_chinese_speech_becomes_timestamped_segments(engine: FasterWhisperTranscriber):
    segments = engine.transcribe(AUDIO, ["检索增强生成", "RAG"])

    text = "".join(segment.text for segment in segments)
    assert "检索增强生成" in text
    assert "知识库" in text
    assert segments[0].start < 2
    starts = [segment.start for segment in segments]
    assert starts == sorted(starts)
    assert all(segment.start < segment.end <= 13 for segment in segments)
    assert all(segment.text == segment.text.strip() and segment.text for segment in segments)
    assert engine.origin == "faster-whisper large-v3-turbo"


@needs_gpu_and_model
def test_undecodable_audio_fails_only_that_source(engine: FasterWhisperTranscriber, tmp_path: Path):
    broken = tmp_path / "坏的音频.wav"
    broken.write_bytes(b"this is not audio")

    with pytest.raises(TranscriptionFailed):
        engine.transcribe(broken, [])


def test_model_that_cannot_be_obtained_stops_with_a_clear_error(tmp_path: Path):
    """模型下载失败（这里用离线 + 空缓存模拟）是本机环境问题：整批中止并说明如何处理。"""
    engine = FasterWhisperTranscriber(local_files_only=True, cache_dir=tmp_path / "空的模型缓存")

    with pytest.raises(MissingTool, match="转写模型"):
        engine.transcribe(AUDIO, [])
