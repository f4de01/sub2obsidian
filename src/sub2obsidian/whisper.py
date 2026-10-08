"""转写引擎的 faster-whisper 实现：本机 GPU 上的 large-v3-turbo（CUDA，int8_float16）。

首次运行时自动从 Hugging Face 下载模型（约 1.6 GB）到其缓存目录；下载不了时可设置
HF_ENDPOINT=https://hf-mirror.com 后重试。Windows 上 CUDA 所需的 cuBLAS 由 nvidia-cublas-cu12
包提供（cuDNN 随 ctranslate2 自带），这里把它的 DLL 目录登记给进程。
"""

from __future__ import annotations

import os
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

# 每个 30 秒窗口都带上的提示：引导简体中文与标点，并列出术语表
STYLE_HINT = "以下是普通话的句子，使用简体中文和标点。"

# 这些 GPU 运行库错误说明本机环境有问题，而不是某段音频有问题
_GPU_ERRORS = ("cuda", "cublas", "cudnn")

_dll_directories: list[Any] = []


def _register_cuda_libraries() -> None:
    if sys.platform != "win32" or _dll_directories:
        return
    try:
        import nvidia.cublas  # type: ignore[import-not-found]
    except ImportError:
        return
    for root in nvidia.cublas.__path__:
        library = Path(root) / "bin"
        if library.is_dir():
            _dll_directories.append(os.add_dll_directory(str(library)))
            # ctranslate2 用时才按默认顺序加载 cuBLAS，只认 PATH，不认 add_dll_directory
            os.environ["PATH"] = f"{library}{os.pathsep}{os.environ.get('PATH', '')}"


def hint(terms: Sequence[str]) -> str:
    if not terms:
        return STYLE_HINT
    return f"{STYLE_HINT}术语：{'、'.join(terms)}。"


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
        device: str = DEVICE,
        compute_type: str = COMPUTE_TYPE,
        local_files_only: bool = False,
        cache_dir: Path | None = None,
    ) -> None:
        self.origin = f"faster-whisper {model}"
        self.model = model
        self.device = device
        self.compute_type = compute_type
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
            self._loaded = WhisperModel(path, device=self.device, compute_type=self.compute_type)
        except (RuntimeError, ValueError) as error:
            raise MissingTool(
                f"无法在 GPU 上加载转写模型 {self.model}（{self.device}，{self.compute_type}）：{error}"
            ) from error
        return self._loaded

    def transcribe(self, audio: Path, terms: Sequence[str]) -> list[Segment]:
        model = self._load()
        try:
            segments, _ = model.transcribe(
                str(audio),
                hotwords=hint(terms),
                # 不以上一窗口的文字为提示：长音频上可避免整段重复的幻觉
                condition_on_previous_text=False,
                vad_filter=True,
            )
            # segments 是惰性生成器：解码与推理在遍历时才真正发生
            return [
                Segment(start=s.start, end=s.end, text=s.text.strip())
                for s in segments
                if s.text.strip()
            ]
        except Exception as error:  # noqa: BLE001 - 单条音频的任何失败都不应拖垮整批
            message = str(error) or type(error).__name__
            if isinstance(error, RuntimeError) and any(k in message.lower() for k in _GPU_ERRORS):
                raise MissingTool(f"GPU 转写出错（请检查显卡驱动与 CUDA 运行库）：{message}") from error
            raise TranscriptionFailed(f"转写失败：{message}") from error
