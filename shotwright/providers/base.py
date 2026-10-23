"""Provider 的声明与接口。

每类外部服务（大模型、配音、素材检索、AI 视频……）都遵循同一套模式：

* :class:`ProviderSpec` 声明\"这是谁、要什么配置、能做什么\"；
* 一个工厂函数接收该 Provider 的配置字典，返回实现了对应协议的对象；
* 在 ``providers/<类别>/<id>.py`` 末尾调用 :func:`registry.register` 登记。

界面的设置页、API 的 ``/providers`` 接口都直接遍历注册表生成，
新增一家服务不需要改动其他任何文件。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

ProviderKind = Literal["llm", "tts", "stock", "video_gen", "image_gen", "music"]


class ProviderError(RuntimeError):
    """外部服务调用失败。消息会展示给用户，不能包含密钥。"""


class ProviderNotConfigured(ProviderError):
    pass


class ChargeConfirmationRequired(ProviderError):
    """收费操作没有得到用户的明确确认。"""


class UnconfirmedTaskError(ProviderError):
    """收费任务可能已在远端创建，但本地无法确认结果。不要自动重试，以免重复扣费。"""


@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    required: bool = False
    secret: bool = False
    default: str = ""
    help: str = ""


@dataclass(frozen=True)
class ProviderSpec:
    kind: ProviderKind
    id: str
    name: str
    fields: tuple[ConfigField, ...] = ()
    name_en: str = ""
    """英文界面显示的名称；为空时用 ``name``。"""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    paid: bool = False
    """调用是否会产生费用。收费操作在界面和 API 上都需要用户确认。"""
    docs_url: str = ""
    offline: bool = False
    """不需要网络即可工作（例如静音配音）。"""
    shared_key_from: tuple[tuple[str, str], ...] = ()
    """自己的 api_key 为空时，依次借用这些 (类别, id) 的 api_key（例如方舟的同一把 Key）。"""
    voices: tuple[tuple[str, str], ...] = ()
    """配音服务的可选声音 (id, 说明)。为空表示用语言目录里的 Edge 声音。"""
    max_duration: float | None = None
    """视频生成的最长时长（秒）；None 表示不限或不适用。"""
    has_audio: bool = False
    """生成的视频是否含有声轨（用于判断是否需要混音）。"""
    video_to_video: bool = False
    """是否支持视频转视频（改画面）。"""
    max_ref_images: int = 0
    """支持的参考图数量上限；0 表示不支持参考图。"""
    word_timestamps: bool = False
    """配音服务是否提供逐词时间戳（用于精确字幕对齐）。"""
    prompt_languages: tuple[str, ...] = ("en",)
    """生成模型能直接理解的提示词语言（zh / en）。项目语言不在其中时，提交前先用大模型翻译。"""

    def missing_fields(self, settings: dict[str, str]) -> list[str]:
        return [f.key for f in self.fields if f.required and not settings.get(f.key)]


Factory = Callable[[dict[str, str]], object]


# ---- 各类别的协议 ------------------------------------------------------------


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str: ...


@dataclass
class WordTiming:
    """配音里一个词/字的时间段（相对音频开头，秒）。"""

    text: str
    start: float
    end: float


@dataclass
class SpeechResult:
    path: Path
    duration: float
    words: list[WordTiming] = field(default_factory=list)
    """逐词时间戳；配音服务不提供时为空，字幕退回按字数估算分配。"""


class TTSProvider(Protocol):
    def synthesize(
        self, text: str, *, voice: str, rate: float, volume: float, output: Path
    ) -> SpeechResult: ...


@dataclass
class StockCandidate:
    provider: str
    remote_id: str
    url: str
    """下载地址。"""
    kind: Literal["video", "image"]
    width: int
    height: int
    duration: float | None = None
    preview_url: str = ""
    meta: dict[str, str] = field(default_factory=dict)


class StockProvider(Protocol):
    def search(self, query: str, *, aspect: str, min_duration: float, limit: int) -> list[StockCandidate]: ...

    def download(self, candidate: StockCandidate, output: Path) -> Path: ...


class VideoGenProvider(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        aspect: str,
        duration: float,
        output: Path,
        first_frame: Path | None = None,
        reference_video: Path | None = None,
        on_task: Callable[[str], None] | None = None,
    ) -> Path:
        """生成视频并保存到 ``output``（可改扩展名），返回实际路径。

        ``first_frame`` 为本地图片时走图生视频；``reference_video`` 为本地视频时
        走视频转视频（改画面：换手、换背景、重新打光），结果是一段新视频。
        ``on_task`` 在拿到远端任务 id 后立即回调，调用方据此把 id 写进项目，
        进程重启后还能继续轮询。提交收费任务后若无法确认结果，必须抛
        :class:`UnconfirmedTaskError`，不得自动重新提交。
        """
        ...

    def resume(self, task_id: str, *, output: Path) -> Path:
        """轮询一个已提交的任务并下载结果（重启后恢复用）。"""
        ...


class ImageGenProvider(Protocol):
    def generate(self, prompt: str, *, aspect: str, output: Path) -> Path: ...


@dataclass
class MusicCandidate:
    provider: str
    remote_id: str
    url: str
    """下载地址。"""
    title: str
    duration: float
    author: str = ""
    preview_url: str = ""
    meta: dict[str, str] = field(default_factory=dict)


class MusicProvider(Protocol):
    """背景音乐检索。返回的音频会作为普通 Asset 存进项目，渲染时循环到整片时长。"""

    def search(
        self, query: str, *, min_duration: float, max_duration: float, limit: int
    ) -> list[MusicCandidate]: ...

    def download(self, candidate: MusicCandidate, output: Path) -> Path: ...
