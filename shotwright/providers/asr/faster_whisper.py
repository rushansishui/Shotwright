"""本地语音识别：faster-whisper（Whisper 的 CTranslate2 实现）。

不联网、不收费，原片不出本机，适合私有化部署。需要先安装：``pip install faster-whisper``。
第一次使用会下载模型（small 约 500MB）；国内网络可以把 ``download_mirror`` 设为 https://hf-mirror.com。
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, Transcript, WordTiming

log = logging.getLogger(__name__)

INSTALL_HINT = "没有安装 faster-whisper。请在镜匠的 Python 环境里运行：pip install faster-whisper"

_MODELS: dict[tuple[str, str, str], object] = {}
_MODELS_LOCK = threading.Lock()

ZH_PROMPT = "以下是普通话的句子，使用简体中文和标点符号。"
"""Whisper 识别中文时常输出繁体、不加标点；给一句简体带标点的提示能明显改善。"""


def whisper_language(code: str | None) -> str | None:
    """项目语言（zh-CN、pt-BR）→ Whisper 的语言代码（zh、pt）；空表示自动识别。"""
    if not code or code == "auto":
        return None
    return code.split("-")[0].lower()


class FasterWhisperASR:
    def __init__(self, settings: dict[str, str]):
        self.model_name = (settings.get("model") or "small").strip()
        self.device = (settings.get("device") or "auto").strip()
        self.compute_type = (settings.get("compute_type") or "default").strip()
        self.mirror = (settings.get("download_mirror") or "").strip().rstrip("/")
        self.download_root = (settings.get("download_root") or "").strip() or None

    def _model(self):
        if self.mirror:
            # huggingface_hub 在导入时读取 HF_ENDPOINT，必须在导入 faster_whisper 之前设置。
            os.environ.setdefault("HF_ENDPOINT", self.mirror)
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError:
            raise ProviderError(INSTALL_HINT) from None
        key = (self.model_name, self.device, self.compute_type)
        with _MODELS_LOCK:
            model = _MODELS.get(key)
            if model is None:
                log.info("加载语音识别模型 %s（%s）", self.model_name, self.device)
                try:
                    model = WhisperModel(
                        self.model_name,
                        device=self.device,
                        compute_type=self.compute_type,
                        download_root=self.download_root,
                    )
                except Exception as exc:  # 下载失败、显卡不可用等
                    raise ProviderError(
                        f"语音识别模型 {self.model_name} 加载失败：{exc}。"
                        "第一次使用需要下载模型，国内网络可以在服务设置里把下载镜像设为 https://hf-mirror.com"
                    ) from exc
                _MODELS[key] = model
        return model

    def check(self) -> tuple[bool, str]:
        try:
            import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            return False, INSTALL_HINT
        return True, f"已安装 faster-whisper，将使用 {self.model_name} 模型（第一次识别时下载）"

    def transcribe(
        self, audio: Path, *, language: str | None, progress: Callable[[float], None] | None = None
    ) -> Transcript:
        model = self._model()
        lang = whisper_language(language)
        try:
            segments, info = model.transcribe(  # type: ignore[attr-defined]
                str(audio),
                language=lang,
                word_timestamps=True,
                vad_filter=True,
                initial_prompt=ZH_PROMPT if lang == "zh" else None,
            )
            words: list[WordTiming] = []
            sentences: list[tuple[float, float]] = []
            total = float(getattr(info, "duration", 0.0) or 0.0)
            for segment in segments:
                for word in segment.words or []:
                    text = word.word.strip()
                    if text:
                        words.append(WordTiming(text, round(float(word.start), 3), round(float(word.end), 3)))
                sentences.append((round(float(segment.start), 3), round(float(segment.end), 3)))
                if progress and total > 0:
                    progress(min(1.0, float(segment.end) / total))
        except ProviderError:
            raise
        except Exception as exc:
            hint = "；如果是显卡相关的错误，请在服务设置里把运行设备改成 cpu" if "cud" in str(exc).lower() else ""
            raise ProviderError(f"语音识别失败：{exc}{hint}") from exc
        return Transcript(words=words, sentences=sentences, language=str(getattr(info, "language", "") or ""))


registry.register(
    ProviderSpec(
        kind="asr",
        id="faster_whisper",
        name="本地识别（faster-whisper，免费）",
        name_en="Local (faster-whisper, free)",
        offline=True,
        docs_url="https://github.com/SYSTRAN/faster-whisper",
        fields=(
            ConfigField(
                "model",
                "模型",
                default="small",
                help="tiny / base / small / medium / large-v3；越大越准越慢。没有显卡建议 small，有显卡用 large-v3",
            ),
            ConfigField("device", "运行设备", default="auto", help="auto / cpu / cuda"),
            ConfigField("compute_type", "计算精度", default="default", help="cpu 可用 int8 加速，cuda 可用 float16"),
            ConfigField("download_mirror", "模型下载镜像", help="国内网络填 https://hf-mirror.com；留空从 Hugging Face 下载"),
            ConfigField("download_root", "模型存放目录", help="留空用 Hugging Face 默认缓存目录"),
        ),
    ),
    FasterWhisperASR,
)
