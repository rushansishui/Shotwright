"""API 请求体。只列出允许修改的字段，其余字段（id、素材表、修订号）由服务端维护。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shotwright.core.models import (
    BgmSettings,
    CameraMove,
    Canvas,
    DeliveryPlatform,
    FillMode,
    FitMode,
    Look,
    Overlay,
    ReferenceAsset,
    SfxSettings,
    ShotRole,
    SubtitleStyle,
    TransitionSettings,
    VideoType,
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
    video_type: VideoType | None = None
    """视频类型（商品广告、品牌宣传、知识科普、口播、原片改造），写分镜时据此安排结构。"""
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
    video_type: VideoType | None = None
    look: Look | None = None
    """全片设定；整体替换。"""
    chain_frames: bool | None = None
    budget: float | None = Field(None, ge=0.0, le=1_000_000)
    """项目费用上限（元）；显式传 null 表示不限，不传则不改。"""
    """首尾帧衔接开关。"""
    target_seconds: float | None = Field(None, gt=0, le=1800)
    """目标总时长（秒）。"""
    canvas: Canvas | None = None
    voice: VoiceSettings | None = None
    subtitle: SubtitleStyle | None = None
    overlays: list[Overlay] | None = None
    """叠加文字与图形；整体替换。"""
    transition: TransitionSettings | None = None
    ai_badge: bool | None = None
    """画面上是否显示「AI 生成」角标。"""
    bgm: BgmSettings | None = None
    delivery: DeliveryPlatform | None = None
    """交付平台：决定编码参数和字幕、叠加文字的安全区。"""
    sfx: SfxSettings | None = None
    """音效层：转场音效和环境声。"""
    loudness: float | None = Field(None, ge=-30.0, le=-5.0)
    """导出响度（LUFS）；显式传 null 表示不做响度处理，不传则不改。"""
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
    image_prompt: str | None = Field(None, max_length=2000)
    camera: CameraMove | None = None
    link: str | None = Field(None, max_length=400)
    provider: str | None = None
    selected: str | None = None
    sequence: list[str] | None = Field(None, max_length=10)
    """多选：在选中的素材之后依次播放的其他素材（按顺序）；传空列表取消多选。"""
    first_frame: str | None = None
    clear_first_frame: bool = False
    """为真时取消首帧，改为纯文字生成视频。"""
    color: str | None = None
    fit: FitMode | None = None
    focus_x: float | None = Field(None, ge=0.0, le=1.0)
    focus_y: float | None = Field(None, ge=0.0, le=1.0)
    trim_start: float | None = Field(None, ge=0.0, le=36000)
    trim_end: float | None = Field(None, gt=0.0, le=36000)
    clear_trim_end: bool = False
    """为真时出点回到素材结尾。"""
    fill: FillMode | None = None
    source_volume: float | None = Field(None, ge=0.0, le=2.0)
    locked: bool | None = None


class UpdateShot(_Body):
    text: str | None = Field(None, max_length=2000)
    duration: float | None = Field(None, gt=0, le=120)
    clear_duration: bool = False
    visual: VisualPatch | None = None
    role: ShotRole | None = None
    voice: VoiceSettings | None = None
    clear_voice: bool = False


class AddShot(_Body):
    text: str = Field("", max_length=2000)
    after: str | None = None
    """插在哪个镜头之后；为空时追加到末尾。"""


class SplitShot(_Body):
    at: float = Field(..., gt=0, le=36000)
    """拆分点：从镜头片段开头算起的秒数。"""


class ReorderShots(_Body):
    order: list[str]


class ChargeConsent(_Body):
    confirm_charge: bool = False
    """用户已确认这次操作可以调用收费服务（AI 视频、AI 图片）。"""
    quality: Literal["draft", "final"] = "final"
    """draft：快速低质量预览；final：正常成片。"""


class CandidatesRequest(ChargeConsent):
    edit_selected: bool = False
    """改画面：把当前选中的视频交给支持视频转视频的 AI 服务按提示词改写。"""
    count: int = Field(1, ge=1, le=3)
    """AI 视频一次生成几条，同一批供挑选；费用按条数成倍增加。"""
    keyframes: int = Field(0, ge=0, le=3)
    """AI 视频镜头先出几张关键帧图片（0 表示直接生成视频）。"""
    quality: Literal["draft", "final"] = "final"
    """草稿档用较低分辨率生成 AI 视频。"""


class AmbienceSearch(_Body):
    query: str = Field("", max_length=200)
    """英文检索词，例如 rain ambience；为空时按第一个镜头的检索词自动生成。"""


class RewriteShot(_Body):
    instruction: str = Field(..., min_length=1, max_length=500)
    """一句改写要求，例如「改成俯拍特写，突出按键」。"""


class ExpandRequest(_Body):
    only_short: bool = True
    """只补全画面描述太短或缺少衔接的镜头；为假时重写所有非纯色镜头。"""


class CreateVersion(_Body):
    language: str = Field(..., max_length=40)
    """要生成的语言版本，例如 en-US。"""


class BgmSearch(_Body):
    query: str = Field("", max_length=200)
    """检索词；留空时由大模型从视频诉求和镜头提示词提炼。"""


class ImportFootage(_Body):
    threshold: float = Field(0.35, ge=0.05, le=1.0)
    """画面变化阈值：越小切得越碎。"""
    max_shots: int = Field(30, ge=1, le=60)
    replace_shots: bool = False
    """为真时替换现有镜头，否则追加到末尾。"""


class BgmDownload(_Body):
    provider: str
    """候选所属的 Provider id，例如 "archive"。"""
    remote_id: str = Field(..., max_length=400)
    """候选的远端唯一标识，来自搜索结果的 remote_id。"""
    url: str = Field(..., max_length=2000)
    """下载地址，来自搜索结果的 url。"""
    preview_url: str = Field("", max_length=2000)
    """试听地址；界面会把整条候选原样发回来，这里接受但不用。"""
    title: str = Field("", max_length=200)
    author: str = Field("", max_length=200)
    duration: float = Field(0.0, ge=0)
    meta: dict[str, str] = Field(default_factory=dict)
