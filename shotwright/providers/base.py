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

ProviderKind = Literal["llm", "tts", "stock", "video_gen"]


class ProviderError(RuntimeError):
    """外部服务调用失败。消息会展示给用户，不能包含密钥。"""


class ProviderNotConfigured(ProviderError):
    pass


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
    capabilities: frozenset[str] = field(default_factory=frozenset)
    paid: bool = False
    """调用是否会产生费用。收费操作在界面和 API 上都需要用户确认。"""
    docs_url: str = ""
    offline: bool = False
    """不需要网络即可工作（例如静音配音）。"""

    def missing_fields(self, settings: dict[str, str]) -> list[str]:
        return [f.key for f in self.fields if f.required and not settings.get(f.key)]


Factory = Callable[[dict[str, str]], object]


# ---- 各类别的协议 ------------------------------------------------------------


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str: ...


@dataclass
class SpeechResult:
    path: Path
    duration: float


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
    ) -> Path: ...
