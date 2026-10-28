"""本地语音识别：faster-whisper（Whisper 的 CTranslate2 实现）。

不联网、不收费，原片不出本机，适合私有化部署。需要先安装：``pip install faster-whisper``。
第一次使用会下载模型（small 约 480MB），连不上 Hugging Face 时自动换国内镜像 hf-mirror.com。
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

Progress = Callable[..., None]
"""进度回调：``progress(比例, 阶段)``，阶段是 download / load / recognize。"""

MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]

MODEL_SIZES = {
    "tiny": 75_000_000,
    "base": 145_000_000,
    "small": 484_000_000,
    "medium": 1_530_000_000,
    "large-v1": 3_090_000_000,
    "large-v2": 3_090_000_000,
    "large-v3": 3_090_000_000,
    "large-v3-turbo": 1_620_000_000,
    "turbo": 1_620_000_000,
    "distil-large-v3": 1_510_000_000,
}
"""各模型的大约字节数，下载时据此估算进度。"""


def _reachable(url: str, proxy: str | None = None) -> bool:
    """几秒内能不能连上（国内网络连 Hugging Face 常常直接超时）。"""
    import httpx

    try:
        with httpx.Client(timeout=8, follow_redirects=True, proxy=proxy) as client:
            return client.head(url).status_code < 500
    except httpx.HTTPError:
        return False
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
        self.proxy = (settings.get("proxy") or "").strip() or None

    def _download(self, progress: Progress | None) -> str:
        """下载（或从缓存取）模型，返回本地目录。

        不经过 faster-whisper 自带的下载：那样没有超时、没有进度，国内网络连不上 Hugging Face 时会一直卡住。
        这里先查本地缓存；没有时依次试配置的镜像、Hugging Face 官方、hf-mirror.com，连不上的很快跳过，
        下载过程中按已下载的字节数报告进度。
        """
        if Path(self.model_name).is_dir():
            return self.model_name
        # huggingface_hub 在导入时读取这些环境变量，必须在导入之前设置。
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
        os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "15")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        if self.proxy:
            # 服务设置里配了代理时，模型下载也走代理。
            os.environ.setdefault("HTTPS_PROXY", self.proxy)
            os.environ.setdefault("HTTP_PROXY", self.proxy)
        try:
            import huggingface_hub  # type: ignore[import-not-found]
            from faster_whisper.utils import _MODELS as REPOS  # type: ignore[import-not-found]
        except ImportError:
            raise ProviderError(INSTALL_HINT) from None
        repo = self.model_name if "/" in self.model_name else REPOS.get(self.model_name)
        if repo is None:
            raise ProviderError(f"不认识的识别模型：{self.model_name}，可选 {', '.join(REPOS)}")
        kwargs = {"allow_patterns": MODEL_FILES, "cache_dir": self.download_root}
        try:
            return huggingface_hub.snapshot_download(repo, local_files_only=True, **kwargs)
        except Exception:
            pass  # 本地没有，下面下载

        endpoints = [self.mirror] if self.mirror else []
        endpoints += [e for e in ("https://huggingface.co", "https://hf-mirror.com") if e not in endpoints]
        cache = Path(self.download_root or huggingface_hub.constants.HF_HUB_CACHE)
        folder = cache / ("models--" + repo.replace("/", "--"))
        expected = MODEL_SIZES.get(self.model_name.split("/")[-1].replace("faster-whisper-", ""), 0)
        errors: list[str] = []
        for endpoint in endpoints:
            if not _reachable(f"{endpoint}/api/models/{repo}", self.proxy):
                errors.append(f"{endpoint} 连不上")
                continue
            log.info("下载语音识别模型 %s（%s）", repo, endpoint)
            done = threading.Event()

            def watch(done: threading.Event = done) -> None:
                while not done.wait(1.0):
                    if progress and expected:
                        size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) if folder.is_dir() else 0
                        progress(min(0.99, size / expected), "download")

            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            try:
                return huggingface_hub.snapshot_download(repo, endpoint=endpoint, **kwargs)
            except Exception as exc:
                errors.append(f"{endpoint}：{str(exc)[:160]}")
                log.warning("从 %s 下载识别模型失败：%s", endpoint, exc)
            finally:
                done.set()
        raise ProviderError(
            f"识别模型 {self.model_name} 下载失败（{'；'.join(errors)}）。"
            "请检查网络或代理；也可以手动下载模型文件夹后，在服务设置的「模型」里填它的路径"
        )

    def _model(self, progress: Progress | None = None):
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError:
            raise ProviderError(INSTALL_HINT) from None
        key = (self.model_name, self.device, self.compute_type)
        with _MODELS_LOCK:
            model = _MODELS.get(key)
            if model is None:
                if progress:
                    progress(0.0, "model")
                path = self._download(progress)
                if progress:
                    progress(0.0, "load")
                log.info("加载语音识别模型 %s（%s）", self.model_name, self.device)
                try:
                    model = WhisperModel(path, device=self.device, compute_type=self.compute_type)
                except Exception as exc:  # 显卡不可用、模型文件损坏等
                    hint = "；如果是显卡相关的错误，请在服务设置里把运行设备改成 cpu" if "cud" in str(exc).lower() else ""
                    raise ProviderError(f"语音识别模型 {self.model_name} 加载失败：{exc}{hint}") from exc
                _MODELS[key] = model
        return model

    def check(self) -> tuple[bool, str]:
        try:
            import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            return False, INSTALL_HINT
        return True, f"已安装 faster-whisper，将使用 {self.model_name} 模型（第一次识别时下载）"

    def transcribe(
        self, audio: Path, *, language: str | None, progress: Progress | None = None
    ) -> Transcript:
        model = self._model(progress)
        if progress:
            progress(0.0, "recognize")
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
                    progress(min(1.0, float(segment.end) / total), "recognize")
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
                help="tiny / base / small / medium / large-v3；越大越准越慢。没有显卡建议 small，有显卡用 large-v3。也可以填已下载好的模型文件夹路径",
            ),
            ConfigField("device", "运行设备", default="auto", help="auto / cpu / cuda"),
            ConfigField("compute_type", "计算精度", default="default", help="cpu 可用 int8 加速，cuda 可用 float16"),
            ConfigField(
                "download_mirror",
                "模型下载地址",
                help="留空时先试 Hugging Face，连不上自动换国内镜像 https://hf-mirror.com；也可以填其他镜像地址",
            ),
            ConfigField("download_root", "模型存放目录", help="留空用 Hugging Face 默认缓存目录"),
        ),
    ),
    FasterWhisperASR,
)
