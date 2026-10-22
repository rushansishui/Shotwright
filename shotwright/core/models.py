"""领域模型：Project → Shot → Asset。

一个视频就是一个 Project；Project 由有序的 Shot（镜头）组成；镜头通过
asset id 引用 Asset（落盘的媒体文件）。所有对象都可以序列化为 JSON，
项目文件即 ``project.json``。

密钥、接口地址等 Provider 配置永远不进入这些模型：项目文件可以随意分享。
"""

from __future__ import annotations

import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shotwright.core.ids import new_id

SCHEMA_VERSION = 1

MAX_SUBJECT_BYTES = 4096
"""视频诉求内容的上限（UTF-8 字节）。中文一个字占 3 字节，约 1365 个汉字。"""


def check_subject(subject: str) -> str:
    """校验视频诉求长度。三个入口（网页、API、命令行）都走这里。"""
    if len(subject.encode("utf-8")) > MAX_SUBJECT_BYTES:
        raise ValueError(f"视频诉求最多 {MAX_SUBJECT_BYTES} 字节")
    return subject


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

Aspect = Literal["9:16", "16:9", "1:1"]
VisualMode = Literal["stock", "ai", "image", "upload", "color"]
AssetKind = Literal["video", "image", "audio"]
AssetFolder = Literal["ai-video", "ai-image", "stock", "upload", "music", "other"]
"""素材在 ``assets/`` 下的子目录：AI 视频、AI 图片、素材库下载、用户上传、背景音乐、其他。"""
ASSET_FOLDERS: tuple[str, ...] = ("ai-video", "ai-image", "stock", "upload", "music", "other")
ShotStatus = Literal["draft", "ready", "rendering", "done", "failed"]

_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


def validate_id(value: str) -> str:
    """项目、镜头、素材 id 只允许安全字符，杜绝路径穿越。"""
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ValueError("id 只能包含字母、数字、下划线和连字符，长度 1–64")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Canvas(_Model):
    aspect: Aspect = "9:16"
    fps: int = Field(30, ge=12, le=60)

    @property
    def width(self) -> int:
        return _RESOLUTIONS[self.aspect][0]

    @property
    def height(self) -> int:
        return _RESOLUTIONS[self.aspect][1]


class VoiceSettings(_Model):
    provider: str = "edge"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: float = Field(1.0, ge=0.5, le=2.0)
    volume: float = Field(1.0, ge=0.0, le=2.0)


class SubtitleStyle(_Model):
    enabled: bool = True
    font: str = "NotoSansSC-Bold.ttf"
    size: int = Field(64, ge=16, le=200)
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline: float = Field(3.0, ge=0.0, le=12.0)
    position: Literal["top", "middle", "bottom"] = "bottom"
    margin: int = Field(240, ge=0, le=1000)
    """距画面上 / 下边缘的距离（像素，按画布分辨率计）。"""

    @field_validator("color", "outline_color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not _HEX_COLOR.fullmatch(value):
            raise ValueError("颜色必须是 #RRGGBB 格式")
        return value.upper()

    @field_validator("font")
    @classmethod
    def _check_font(cls, value: str) -> str:
        if "/" in value or "\\" in value or value.startswith("."):
            raise ValueError("字体只能是 resources/fonts 下的文件名")
        return value


class BgmSettings(_Model):
    asset_id: str | None = None
    """选中的背景音乐；为空时不用背景音乐。"""
    candidates: list[str] = Field(default_factory=list)
    """自动检索到的候选音频，界面用来切换。与 asset_id 独立，换来源时不清空。"""
    query: str = ""
    """上次检索用的词，界面上回填。"""
    volume: float = Field(0.2, ge=0.0, le=1.0)

    @field_validator("asset_id")
    @classmethod
    def _check_asset(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)

    @field_validator("candidates")
    @classmethod
    def _check_candidates(cls, value: list[str]) -> list[str]:
        return [validate_id(item) for item in value]


class ShotVisual(_Model):
    mode: VisualMode = "stock"
    terms: list[str] = Field(default_factory=list)
    """stock 模式的检索词（建议英文）。"""
    prompt: str = ""
    """ai / image 模式的提示词。"""
    provider: str | None = None
    """为空时使用配置里该类别的默认 Provider。"""
    candidates: list[str] = Field(default_factory=list)
    """所有来源的候选素材；界面按当前画面来源筛选显示。"""
    selected: str | None = None
    first_frame: str | None = None
    """ai 模式的首帧图片（图生视频）；为空时纯文字生成。"""
    color: str = "#1F2937"
    """color 模式或没有可用素材时的纯色画面。"""
    fit: Literal["cover", "contain"] = "cover"
    locked: bool = False
    """锁定后，自动补素材和\"全部重新生成\"都不会改动这个镜头的画面。"""

    @field_validator("candidates")
    @classmethod
    def _check_candidates(cls, value: list[str]) -> list[str]:
        return [validate_id(item) for item in value]

    @field_validator("selected", "first_frame")
    @classmethod
    def _check_selected(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)

    @field_validator("color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not _HEX_COLOR.fullmatch(value):
            raise ValueError("颜色必须是 #RRGGBB 格式")
        return value.upper()


class Shot(_Model):
    id: str = Field(default_factory=new_id)
    text: str = ""
    duration: float | None = Field(None, gt=0, le=120)
    """None 表示跟随配音时长。"""
    visual: ShotVisual = Field(default_factory=ShotVisual)
    voice: VoiceSettings | None = None
    """单独覆盖这个镜头的配音设置；为空时用项目设置。"""
    status: ShotStatus = "draft"
    error: str | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return validate_id(value)


class Asset(_Model):
    id: str
    kind: AssetKind
    source: str
    """来源：pexels、upload、tts、ffmpeg 等。"""
    filename: str
    folder: AssetFolder | None = None
    """所在子目录（见 :data:`AssetFolder`）。为空表示旧项目，文件直接放在 ``assets/`` 下。"""
    sha256: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    meta: dict[str, str] = Field(default_factory=dict)
    """作者、来源页面、授权等展示信息，只放可公开的字符串。"""

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return validate_id(value)

    @field_validator("filename")
    @classmethod
    def _check_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value.startswith("."):
            raise ValueError("素材文件名不能包含路径")
        return value


class Project(_Model):
    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=new_id)
    title: str = ""
    subject: str = ""
    """视频诉求：用户想要的内容。最多 4096 字节（UTF-8）。"""
    language: str = "zh-CN"
    """视频内容的语言（BCP-47，例如 zh-CN、en-US），决定文案语言、默认配音和字幕字体。"""
    target_seconds: float | None = Field(None, gt=0, le=1800)
    """目标总时长（秒）。只影响生成时的镜头数和文案长度；成片以实际配音为准。"""
    group: str | None = None
    """同一个视频的不同语言版本共用一个 group（原始项目的 id）。"""
    canvas: Canvas = Field(default_factory=Canvas)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    subtitle: SubtitleStyle = Field(default_factory=SubtitleStyle)
    bgm: BgmSettings = Field(default_factory=BgmSettings)
    shots: list[Shot] = Field(default_factory=list)
    assets: dict[str, Asset] = Field(default_factory=dict)
    revision: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, value: str) -> str:
        return check_subject(value)

    @field_validator("group")
    @classmethod
    def _check_group(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return validate_id(value)

    @model_validator(mode="after")
    def _check_references(self) -> Project:
        shot_ids = [shot.id for shot in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("镜头 id 不能重复")
        for key, asset in self.assets.items():
            if key != asset.id:
                raise ValueError(f"素材键 {key} 与素材 id {asset.id} 不一致")
        return self

    def shot(self, shot_id: str) -> Shot:
        for shot in self.shots:
            if shot.id == shot_id:
                return shot
        raise KeyError(shot_id)

    def voice_for(self, shot: Shot) -> VoiceSettings:
        return shot.voice or self.voice
