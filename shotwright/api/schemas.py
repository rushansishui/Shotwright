"""API 请求体。只列出允许修改的字段，其余字段（id、素材表、修订号）由服务端维护。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shotwright.core.models import BgmSettings, Canvas, SubtitleStyle, VisualMode, VoiceSettings


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProject(_Body):
    subject: str = Field("", max_length=500)
    script: str = Field("", max_length=20000)
    title: str = Field("", max_length=120)
    mode: Literal["llm", "split", "empty"] = "split"
    """llm：让大模型写文案并拆镜头；split：把 script 按句拆成镜头；empty：空项目。"""
    shots: int = Field(6, ge=1, le=30)
    language: str = Field("简体中文", max_length=40)
    canvas: Canvas | None = None
    voice: VoiceSettings | None = None


class UpdateProject(_Body):
    title: str | None = Field(None, max_length=120)
    subject: str | None = Field(None, max_length=500)
    canvas: Canvas | None = None
    voice: VoiceSettings | None = None
    subtitle: SubtitleStyle | None = None
    bgm: BgmSettings | None = None


class VisualPatch(_Body):
    mode: VisualMode | None = None
    terms: list[str] | None = Field(None, max_length=10)
    prompt: str | None = Field(None, max_length=2000)
    provider: str | None = None
    selected: str | None = None
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
