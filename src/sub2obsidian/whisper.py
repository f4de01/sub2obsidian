"""转写引擎的 faster-whisper 实现：本机 GPU 上的 large-v3-turbo（CUDA，int8_float16）。

首次运行时自动从 Hugging Face 下载模型（约 1.6 GB）到其缓存目录；下载不了时可设置
HF_ENDPOINT=https://hf-mirror.com 后重试。Windows 上 CUDA 所需的 cuBLAS 由 nvidia-cublas-cu12
包提供（cuDNN 随 ctranslate2 自带），这里把它的 DLL 目录加进 PATH。

抑制幻觉与漂移：
- 先检测一次语言再显式传给引擎；中文风格提示只给中文音频——把它给英文音频时，难解码的段落
  会在温度回退中被带成乱码中文并陷入「练习练习……」式的重复。非中文音频也不带含汉字的术语。
- 语音活动检测跳过没有人声的片段；hallucination_silence_threshold 跳过长静默中的幻觉。
- 丢弃压缩比超过阈值（重复度异常）的段落。不用 no_repeat_ngram_size：它会把正常重复的说法
  （如「implement ticket two / three」）也改掉。
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sub2obsidian.tools import MissingTool
from sub2obsidian.transcript import Segment
from sub2obsidian.transcription import TranscriptionFailed

MODEL = "large-v3-turbo"
DEVICE = "cuda"
COMPUTE_TYPE = "int8_float16"

# 中文音频每个 30 秒窗口都带上的提示：引导简体中文与标点，并列出术语表
STYLE_HINT = "以下是普通话的句子，使用简体中文和标点。"
CHINESE = "zh"
_HAN = re.compile(r"[一-鿿]")  # 汉字

# 语言检测取有人声的前几个 30 秒窗口（视频开头常是音乐）
LANGUAGE_DETECTION_SEGMENTS = 3
# 压缩比（文本长度 / zlib 压缩后长度）超过它的段落多是重复循环：引擎先以更高温度重试，仍超过就丢弃
COMPRESSION_RATIO_THRESHOLD = 2.4
# 长于它（秒）的静默中出现的可疑段落视为幻觉，跳过
HALLUCINATION_SILENCE_THRESHOLD = 2.0

# 这些 GPU 运行库错误说明本机环境有问题，而不是某段音频有问题
_GPU_ERRORS = ("cuda", "cublas", "cudnn")


def _register_cuda_libraries() -> None:
    """把 nvidia-cublas-cu12 的 DLL 目录放到 PATH 最前面。

    ctranslate2 在第一次推理时才加载 cuBLAS，走默认搜索顺序，只认 PATH，不认 os.add_dll_directory。
    """
    if sys.platform != "win32":
        return
    try:
        import nvidia.cublas  # type: ignore[import-not-found]
    except ImportError:
        return
    path = os.environ.get("PATH", "")
    for root in nvidia.cublas.__path__:
        library = str(Path(root) / "bin")
        if Path(library).is_dir() and library not in path.split(os.pathsep):
            os.environ["PATH"] = path = f"{library}{os.pathsep}{path}"


def hint(language: str, terms: Sequence[str]) -> str | None:
    """给引擎的提示：中文音频是风格提示加术语表；其他语言只列不含汉字的术语，没有就不提示。"""
    if language == CHINESE:
        return f"{STYLE_HINT}术语：{'、'.join(terms)}。" if terms else STYLE_HINT
    foreign = [term for term in terms if not _HAN.search(term)]
    return ", ".join(foreign) or None


def model_cached(model: str = MODEL, cache_dir: Path | None = None) -> bool:
    """模型是否已完整下载到本机缓存（不联网）。"""
    from faster_whisper.utils import download_model

    try:
        download_model(model, local_files_only=True, cache_dir=str(cache_dir) if cache_dir else None)
    except Exception:  # noqa: BLE001 - 任何原因拿不到本地模型都算未缓存
        return False
    return True


class FasterWhisperTranscriber:
    def __init__(
        self,
        model: str = MODEL,
        *,
        local_files_only: bool = False,
        cache_dir: Path | None = None,
    ) -> None:
        self.origin = f"faster-whisper {model}"
        self.model = model
        self.local_files_only = local_files_only
        self.cache_dir = cache_dir
        self._loaded: Any = None

    def _load(self) -> Any:
        if self._loaded is not None:
            return self._loaded
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        _register_cuda_libraries()
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model

        try:
            path = download_model(
                self.model,
                local_files_only=self.local_files_only,
                cache_dir=str(self.cache_dir) if self.cache_dir else None,
            )
        except Exception as error:  # noqa: BLE001 - 网络、镜像、缓存损坏等都归为拿不到模型
            raise MissingTool(
                f"无法获取转写模型 {self.model}：{error}。首次运行需要联网从 Hugging Face 下载"
                "（约 1.6 GB）；下载不了时可设置环境变量 HF_ENDPOINT=https://hf-mirror.com 后重试"
            ) from error
        try:
            self._loaded = WhisperModel(path, device=DEVICE, compute_type=COMPUTE_TYPE)
        except (RuntimeError, ValueError) as error:
            raise MissingTool(
                f"无法在 GPU 上加载转写模型 {self.model}（{DEVICE}，{COMPUTE_TYPE}）：{error}"
            ) from error
        return self._loaded

    def transcribe(self, audio: Path, terms: Sequence[str]) -> list[Segment]:
        model = self._load()
        try:
            from faster_whisper.audio import decode_audio

            samples = decode_audio(str(audio))
            language, _, _ = model.detect_language(
                samples, vad_filter=True, language_detection_segments=LANGUAGE_DETECTION_SEGMENTS
            )
            segments, _ = model.transcribe(
                samples,
                language=language,
                hotwords=hint(language, terms),
                # 不以上一窗口的文字为提示：长音频上可避免整段重复的幻觉
                condition_on_previous_text=False,
                vad_filter=True,
                compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
                word_timestamps=True,  # hallucination_silence_threshold 需要逐词时间戳
                hallucination_silence_threshold=HALLUCINATION_SILENCE_THRESHOLD,
            )
            # segments 是惰性生成器：解码与推理在遍历时才真正发生
            return [
                Segment(start=s.start, end=s.end, text=s.text.strip())
                for s in segments
                if s.text.strip() and s.compression_ratio <= COMPRESSION_RATIO_THRESHOLD
            ]
        except Exception as error:  # noqa: BLE001 - 单条音频的任何失败都不应拖垮整批
            message = str(error) or type(error).__name__
            if isinstance(error, RuntimeError) and any(k in message.lower() for k in _GPU_ERRORS):
                raise MissingTool(f"GPU 转写出错（请检查显卡驱动与 CUDA 运行库）：{message}") from error
            raise TranscriptionFailed(f"转写失败：{message}") from error
