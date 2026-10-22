"""API 请求体。只列出允许修改的字段，其余字段（id、素材表、修订号）由服务端维护。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shotwright.core.models import (
    BgmSettings,
    Canvas,
    Overlay,
    ReferenceAsset,
    SubtitleStyle,
    TransitionSettings,
    VisualMode,
    VoiceSettings,
    check_subject,
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(_Body):
    subject: str = ""
    """视频诉求：想要什么内容。上限 4096 字节。"""
    script: str = Field("", max_length=20000)
    title: str = Field("", max_length=120)
    mode: Literal["llm", "split", "empty"] = "split"
    """llm：让大模型写文案并拆镜头；split：把 script 按句拆成镜头；empty：空项目。"""
    shots: int | None = Field(None, ge=1, le=60)
    """期望的镜头数；为空时由大模型按目标时长和诉求内容自行决定。"""
    duration: float | None = Field(None, gt=0, le=1800)
    """目标总时长（秒）；为空时由大模型按诉求内容估算。"""
    language: str = Field("zh-CN", max_length=40)
    """视频内容的语言代码（见 /api/v2/languages），也接受"简体中文"这类旧写法。"""
    canvas: Canvas | None = None
    voice: VoiceSettings | None = None

    @field_validator("subject")
    @classmethod
    def _subject_within_limit(cls, value: str) -> str:
        return check_subject(value)


class UpdateProject(_Body):
    title: str | None = Field(None, max_length=120)
    language: str | None = Field(None, max_length=40)
    """切换视频语言：同时换成该语言的默认配音声音和字幕字体（文案不变）。"""
    subject: str | None = None
    """视频诉求；上限 4096 字节。"""
    target_seconds: float | None = Field(None, gt=0, le=1800)
    """目标总时长（秒）。"""
    canvas: Canvas | None = None
    voice: VoiceSettings | None = None
    subtitle: SubtitleStyle | None = None
    overlays: list[Overlay] | None = None
    """叠加文字与图形；整体替换。"""
    transition: TransitionSettings | None = None
    bgm: BgmSettings | None = None
    references: list[ReferenceAsset] | None = None
    """项目级参考素材库；整体替换。"""

    @field_validator("subject")
    @classmethod
    def _subject_within_limit(cls, value: str | None) -> str | None:
        return None if value is None else check_subject(value)


class VisualPatch(_Body):
    mode: VisualMode | None = None
    terms: list[str] | None = Field(None, max_length=10)
    prompt: str | None = Field(None, max_length=2000)
    provider: str | None = None
    selected: str | None = None
    first_frame: str | None = None
    clear_first_frame: bool = False
    """为真时取消首帧，改为纯文字生成视频。"""
    color: str | None = None
    fit: Literal["cover", "contain"] | None = None
    locked: bool | None = None


class UpdateShot(_Body):
    text: str | None = Field(None, max_length=2000)
    duration: float | None = Field(None, gt=0, le=120)
    clear_duration: bool = False
    visual: VisualPatch | None = None
    voice: VoiceSettings | None = None
    clear_voice: bool = False


class AddShot(_Body):
    text: str = Field("", max_length=2000)
    after: str | None = None
    """插在哪个镜头之后；为空时追加到末尾。"""


class ReorderShots(_Body):
    order: list[str]


class ChargeConsent(_Body):
    confirm_charge: bool = False
    """用户已确认这次操作可以调用收费服务（AI 视频、AI 图片）。"""


class CreateVersion(_Body):
    language: str = Field(..., max_length=40)
    """要生成的语言版本，例如 en-US。"""


class BgmSearch(_Body):
    query: str = Field("", max_length=200)
    """检索词；留空时由大模型从视频诉求和镜头提示词提炼。"""


class BgmDownload(_Body):
    provider: str
    """候选所属的 Provider id，例如 "archive"。"""
    remote_id: str = Field(..., max_length=400)
    """候选的远端唯一标识，来自搜索结果的 remote_id。"""
    url: str = Field(..., max_length=2000)
    """下载地址，来自搜索结果的 url。"""
    title: str = Field("", max_length=200)
    author: str = Field("", max_length=200)
    duration: float = Field(0.0, ge=0)
    meta: dict[str, str] = Field(default_factory=dict)
