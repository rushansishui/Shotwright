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


_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,11}$")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

Aspect = Literal["9:16", "16:9", "1:1"]
VisualMode = Literal["stock", "ai", "image", "upload", "color"]
AssetKind = Literal["video", "image", "audio"]
AssetFolder = Literal["ai-video", "ai-image", "stock", "upload", "music", "reference", "other"]
"""素材在 ``assets/`` 下的子目录：AI 视频、AI 图片、素材库下载、用户上传、背景音乐、参考素材、其他。"""
ASSET_FOLDERS: tuple[str, ...] = (
    "ai-video",
    "ai-image",
    "stock",
    "upload",
    "music",
    "reference",
    "other",
)
VideoType = Literal["product_ad", "brand", "explainer", "talking", "footage"]
"""视频类型：商品广告、品牌宣传、知识科普、口播、原片改造。决定诉求模板、写分镜时的叙事结构和镜头类型。"""
VIDEO_TYPE_NAMES: dict[str, str] = {
    "product_ad": "商品广告",
    "brand": "品牌宣传",
    "explainer": "知识科普",
    "talking": "口播",
    "footage": "原片改造",
}
ShotRole = Literal["hook", "product", "demo", "scene", "people", "explain", "talking", "cta"]
"""镜头类型：开场钩子、产品亮相、使用演示、场景氛围、人物、原理示意、口播、结尾引导。"""
ShotStatus = Literal["draft", "ready", "rendering", "done", "failed"]

ReferenceRole = Literal["product", "person", "logo", "style"]
"""参考素材的角色：产品外观、人物形象、品牌 Logo、整体风格。"""

OverlayPosition = Literal["top", "middle", "bottom"]
"""叠加文字的位置：画面顶部、居中、底部。"""

TransitionKind = Literal["none", "fade", "slide", "wipe"]
"""镜头之间的转场：无、淡入淡出、滑动、擦除。"""

FitMode = Literal["cover", "contain", "blur"]
"""画面适配画布：铺满裁切、完整显示加黑边、完整显示加模糊背景（横竖屏互转时不裁主体）。"""

FillMode = Literal["slow_hold", "hold", "loop"]
"""视频素材比镜头短时怎么补足：适度慢放再定格最后一帧、直接定格、循环播放。"""

CameraMove = Literal["auto", "static", "push_in", "pull_out", "pan", "truck", "orbit", "crane", "handheld"]
"""镜头运动：自动（交给模型）、固定、推、拉、摇、移（跟拍）、环绕、升降、手持。"""

GradePreset = Literal["", "natural", "warm", "cool", "vivid", "film", "bw"]
"""统一调色预设（见 media/grades.py）。"""

SubjectKind = Literal["product", "person", "object", "place"]
"""全片设定里的主体类别：产品、人物、物品、场所。"""

MIN_SLOW_SPEED = 0.8
"""「慢放加定格」最多慢到原速的 0.8 倍，再慢动作会明显发飘。"""

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
    duck: bool = True
    """有配音时自动压低背景音乐，配音停下再恢复。"""
    fade_in: float = Field(1.0, ge=0.0, le=10.0)
    """开头淡入时长（秒），0 表示不淡入。"""
    fade_out: float = Field(2.0, ge=0.0, le=10.0)
    """结尾淡出时长（秒），0 表示不淡出。"""
    beat_sync: bool = False
    """镜头切换点就近挪到音乐的拍点上（只延长镜头，最多 0.35 秒）。"""

    @field_validator("asset_id")
    @classmethod
    def _check_asset(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)

    @field_validator("candidates")
    @classmethod
    def _check_candidates(cls, value: list[str]) -> list[str]:
        return [validate_id(item) for item in value]


class SfxSettings(_Model):
    """音效层：转场音效和环境声。"""

    transitions: bool = False
    """在每个镜头切换点放一声轻的转场音效。"""
    transition_volume: float = Field(0.35, ge=0.0, le=1.0)
    ambience: str | None = None
    """环境声素材（音频）；循环铺满整片。"""
    ambience_volume: float = Field(0.12, ge=0.0, le=1.0)
    ambience_query: str = ""
    """上次自动检索环境声用的词。"""

    @field_validator("ambience")
    @classmethod
    def _check_ambience(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)


class PendingTask(_Model):
    """已提交但尚未取回结果的收费任务。

    进程重启后据此继续轮询，避免重复提交、重复扣费。
    """

    provider: str
    """提交任务的服务 id，例如 seedance。"""
    task_id: str
    """远端任务 id。"""
    kind: Literal["video_gen", "image_gen"] = "video_gen"
    since: float = Field(default_factory=time.time)
    """提交时间（Unix 秒），用于判断任务是否已经过久。"""

    @field_validator("provider", "task_id")
    @classmethod
    def _check_plain(cls, value: str) -> str:
        if not value or len(value) > 400:
            raise ValueError("任务标识长度不合法")
        return value


class LookSubject(_Model):
    """全片设定里的一个主体（产品、人物、物品、场所），各镜头用同一个代号指代它。"""

    code: str
    """代号，例如 P1、A；镜头提示词里用它指代这个主体，生成时自动带上外观描述。"""
    name: str = ""
    """简短名称，例如「便携咖啡机」「女主角」。"""
    kind: SubjectKind = "object"
    appearance: str = ""
    """外观：颜色、材质、形状、尺寸、服装、发型等，越具体各镜头越一致。"""
    reference: str | None = None
    """对应主体库里的参考图（素材 id）；为空表示没有。"""

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        if not _CODE_PATTERN.fullmatch(value):
            raise ValueError("代号以字母开头，只能包含字母、数字、下划线和连字符，最长 12 个字符")
        return value

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return value[:60]

    @field_validator("appearance")
    @classmethod
    def _check_appearance(cls, value: str) -> str:
        return value[:600]

    @field_validator("reference")
    @classmethod
    def _check_reference(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)


class Look(_Model):
    """全片设定：写分镜时先定下来，生成每个镜头时自动拼进提示词，保证全片一致。"""

    subjects: list[LookSubject] = Field(default_factory=list, max_length=12)
    scene: str = ""
    """场景：主要发生在哪里，时间、天气、环境特征。"""
    style: str = ""
    """视觉风格：画面质感、色调、光线、镜头语言。"""
    avoid: str = ""
    """禁止项：不要出现的内容。"""
    style_ref: str | None = None
    """风格参考图（素材 id）。预留，暂未使用。"""
    grade: GradePreset = ""
    """统一调色预设：自然、暖色、冷色、鲜艳、胶片、黑白；空字符串表示不调色。"""
    brand: str | None = None
    """品牌包。预留，v0.5 品牌包作为全片设定的预设。"""

    @field_validator("scene", "style")
    @classmethod
    def _check_long(cls, value: str) -> str:
        return value[:600]

    @field_validator("avoid")
    @classmethod
    def _check_avoid(cls, value: str) -> str:
        return value[:400]

    @field_validator("style_ref")
    @classmethod
    def _check_style_ref(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)

    @model_validator(mode="after")
    def _unique_codes(self) -> Look:
        codes = [subject.code for subject in self.subjects]
        if len(codes) != len(set(codes)):
            raise ValueError("全片设定里的代号不能重复")
        return self

    def is_empty(self) -> bool:
        return not (self.subjects or self.scene.strip() or self.style.strip() or self.avoid.strip())


TakeKind = Literal["video", "image", "keyframe", "edit", "extend"]
"""一次生成的结果类别：AI 视频、AI 图片、关键帧、改画面、续生成拼接的长镜头。"""


class Take(_Model):
    """镜头的一个生成版本：关键帧、多候选、「再来一版」、开头变体共用这一套记录。

    素材本身仍在候选列表里；这里补充它是怎么来的、属于哪一批、检查结果如何。
    """

    asset_id: str
    kind: TakeKind = "video"
    batch: str = ""
    """同一次点击生成的多个结果共用一个批次 id。"""
    prompt: str = ""
    """生成时镜头画面描述的前 300 字，便于对比不同版本。"""
    score: float | None = Field(None, ge=0.0, le=1.0)
    """检查得分（切题程度和主体一致性，0–1）；没检查时为空。"""
    issue: str = ""
    """检查发现的问题，例如「产品颜色与参考图不一致」。"""
    created_at: float = Field(default_factory=time.time)

    @field_validator("asset_id")
    @classmethod
    def _check_asset(cls, value: str) -> str:
        return validate_id(value)

    @field_validator("prompt")
    @classmethod
    def _check_prompt(cls, value: str) -> str:
        return value[:300]

    @field_validator("issue")
    @classmethod
    def _check_issue(cls, value: str) -> str:
        return value[:200]


class ShotVisual(_Model):
    mode: VisualMode = "stock"
    terms: list[str] = Field(default_factory=list)
    """stock 模式的检索词（建议英文）。"""
    prompt: str = ""
    """ai / image 模式的画面描述（只写这个镜头本身；全片设定、运镜和衔接在生成时自动拼上）。"""
    image_prompt: str = ""
    """AI 图片和关键帧用的静态画面描述（一个瞬间，不写动作过程）；为空时用 prompt。"""
    camera: CameraMove = "auto"
    """镜头运动；auto 表示由画面描述决定。"""
    link: str = ""
    """衔接说明：开头怎样承接上一个镜头、结尾留给下一个镜头什么。"""
    provider: str | None = None
    """为空时使用配置里该类别的默认 Provider。"""
    candidates: list[str] = Field(default_factory=list)
    """所有来源的候选素材；界面按当前画面来源筛选显示。"""
    selected: str | None = None
    first_frame: str | None = None
    """ai 模式的首帧图片（图生视频）；为空时纯文字生成。"""
    color: str = "#1F2937"
    """color 模式或没有可用素材时的纯色画面。"""
    fit: FitMode = "cover"
    focus_x: float = Field(0.5, ge=0.0, le=1.0)
    focus_y: float = Field(0.5, ge=0.0, le=1.0)
    """铺满裁切时的取景中心（0 = 最左 / 最上，1 = 最右 / 最下）。"""
    trim_start: float = Field(0.0, ge=0.0, le=36000)
    trim_end: float | None = Field(None, gt=0.0, le=36000)
    """视频素材的入点和出点（秒）；出点为空表示到素材结尾。"""
    edit_asset: str | None = None
    """入出点和取景是针对哪个素材设的。换了素材后这些设置不再生效，按默认处理。"""
    fill: FillMode = "slow_hold"
    """所选片段比镜头短时的补足方式。"""
    source_volume: float = Field(0.0, ge=0.0, le=2.0)
    """视频素材原声的音量，0 表示不用原声。有配音时原声自动压低。"""
    locked: bool = False
    """锁定后，自动补素材和\"全部重新生成\"都不会改动这个镜头的画面。"""
    pending: PendingTask | None = None
    """已提交、尚未取回结果的收费任务；重启后据此继续轮询。"""
    takes: list[Take] = Field(default_factory=list)
    """生成版本记录（按时间先后），与候选素材对应；候选被挤掉时一并清理。"""

    @field_validator("candidates")
    @classmethod
    def _check_candidates(cls, value: list[str]) -> list[str]:
        return [validate_id(item) for item in value]

    @field_validator("link")
    @classmethod
    def _check_link(cls, value: str) -> str:
        return value[:400]

    @field_validator("selected", "first_frame", "edit_asset")
    @classmethod
    def _check_selected(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)

    @field_validator("color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not _HEX_COLOR.fullmatch(value):
            raise ValueError("颜色必须是 #RRGGBB 格式")
        return value.upper()

    @model_validator(mode="after")
    def _check_trim(self) -> ShotVisual:
        if self.trim_end is not None and self.trim_end <= self.trim_start + 0.05:
            raise ValueError("出点必须晚于入点")
        return self

    def take(self, asset_id: str | None) -> Take | None:
        return next((item for item in self.takes if item.asset_id == asset_id), None)

    def effective_edit(self) -> ShotVisual:
        """当前选中素材实际生效的剪辑设置：入出点和取景属于别的素材时恢复默认。"""
        if self.edit_asset is not None and self.edit_asset == self.selected:
            return self
        return self.model_copy(update={"trim_start": 0.0, "trim_end": None, "focus_x": 0.5, "focus_y": 0.5})


class Shot(_Model):
    id: str = Field(default_factory=new_id)
    text: str = ""
    duration: float | None = Field(None, gt=0, le=120)
    """None 表示跟随配音时长。"""
    visual: ShotVisual = Field(default_factory=ShotVisual)
    role: ShotRole | None = None
    """镜头类型；写分镜时按视频类型的叙事结构给出，用来选默认的画面来源。"""
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


class ReferenceAsset(_Model):
    """项目级参考素材：生成画面时作为产品外观、人物形象、Logo 或风格参考。"""

    asset_id: str
    role: ReferenceRole = "product"
    label: str = ""
    """界面上的简短说明，例如「正面包装」「模特 A」。"""
    enabled: bool = True
    """关闭后不参与生成，但保留在库里。"""

    @field_validator("asset_id")
    @classmethod
    def _check_asset(cls, value: str) -> str:
        return validate_id(value)

    @field_validator("label")
    @classmethod
    def _check_label(cls, value: str) -> str:
        return value[:100]


class TransitionSettings(_Model):
    """镜头之间的转场。"""

    kind: TransitionKind = "none"
    duration: float = Field(0.4, ge=0.1, le=2.0)
    """转场时长（秒）；相邻两个镜头在交界处重叠这么长，整片相应变短。"""


class Overlay(_Model):
    """画面上叠加的文字或图形（卖点、价格、Logo、购买引导），与字幕分开。"""

    id: str = Field(default_factory=new_id)
    text: str = ""
    """文字内容；为空时用 image 素材做图形叠加。"""
    image: str | None = None
    """叠加的图片素材 id（通常是 Logo）。"""
    start: float = Field(0.0, ge=0.0)
    """出现时间（整片时间轴，秒）。"""
    duration: float | None = Field(None, gt=0, le=1800)
    """持续时长；为空表示一直显示到片尾。"""
    position: OverlayPosition = "top"
    size: int = Field(52, ge=12, le=200)
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    margin: int = Field(120, ge=0, le=1000)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return validate_id(value)

    @field_validator("image")
    @classmethod
    def _check_image(cls, value: str | None) -> str | None:
        return None if value is None else validate_id(value)

    @field_validator("color", "outline_color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not _HEX_COLOR.fullmatch(value):
            raise ValueError("颜色必须是 #RRGGBB 格式")
        return value.upper()


class Project(_Model):
    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=new_id)
    title: str = ""
    subject: str = ""
    """视频诉求：用户想要的内容。最多 4096 字节（UTF-8）。"""
    video_type: VideoType | None = None
    """视频类型；为空表示没选（旧项目或只按文案拆镜头）。"""
    look: Look = Field(default_factory=Look)
    """全片设定：主体代号与外观、场景、视觉风格、禁止项。"""
    chain_frames: bool = False
    """首尾帧衔接：生成 AI 视频时，用上一个 AI 镜头的最后一帧作为首帧（没有手动指定首帧时）。"""
    language: str = "zh-CN"
    """视频内容的语言（BCP-47，例如 zh-CN、en-US），决定文案语言、默认配音和字幕字体。"""
    target_seconds: float | None = Field(None, gt=0, le=1800)
    """目标总时长（秒）。只影响生成时的镜头数和文案长度；成片以实际配音为准。"""
    group: str | None = None
    """同一个视频的不同语言版本共用一个 group（原始项目的 id）。"""
    canvas: Canvas = Field(default_factory=Canvas)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    subtitle: SubtitleStyle = Field(default_factory=SubtitleStyle)
    overlays: list[Overlay] = Field(default_factory=list)
    """叠加在画面上的文字与图形（卖点、价格、Logo），与字幕分开。"""
    transition: TransitionSettings = Field(default_factory=TransitionSettings)
    ai_badge: bool = False
    """在画面上显示「AI 生成」角标（元数据标识始终写入，不受此开关影响）。"""
    bgm: BgmSettings = Field(default_factory=BgmSettings)
    sfx: SfxSettings = Field(default_factory=SfxSettings)
    """音效层：转场音效和环境声。"""
    loudness: float | None = Field(-14.0, ge=-30.0, le=-5.0)
    """导出时把整片响度统一到这个值（LUFS），短视频平台常用 -14；为空时不处理。"""
    references: list[ReferenceAsset] = Field(default_factory=list)
    """项目级参考素材库：产品图、人物设定、Logo、风格图。镜头生成时作为参考。"""
    shots: list[Shot] = Field(default_factory=list)
    assets: dict[str, Asset] = Field(default_factory=dict)
    budget: float | None = Field(None, ge=0.0, le=1_000_000)
    """项目费用上限（元）；为空表示不限。收费生成前估算本次费用，累计会超过上限时拒绝提交。"""
    cost_credits: float = Field(0.0, ge=0.0)
    """项目累计花费的估算金额（元）：每次收费生成成功后按参考单价累加，供界面显示。"""
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
