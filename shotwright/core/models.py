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
SpeechMode = Literal["tts", "original"]
ProjectOrigin = Literal["create", "cut"]
"""镜头的声音：tts 用文案配音；original 保留原片里的人声，文案只做字幕。"""

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

DeliveryPlatform = Literal["general", "douyin", "kuaishou", "channels", "xiaohongshu", "shorts", "bilibili"]
"""交付平台（规格见 core/platforms.py）。"""

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


SUBTITLE_EDGE = {"9:16": 0.09, "16:9": 0.07, "1:1": 0.07}
"""字幕自动边距占画面高度的比例：竖屏下方常有平台按钮，留得多一点。"""


def auto_subtitle_margin(aspect: str, height: int) -> int:
    return round(height * SUBTITLE_EDGE.get(aspect, 0.07))


TextAnimation = Literal["none", "fade", "pop", "slide_up", "typewriter", "karaoke"]
"""文字入场效果：无、淡入、弹出、上滑、逐字打出、卡拉 OK 逐字高亮（只用于字幕，跟着配音变色）。"""
TextExit = Literal["none", "fade", "shrink", "slide_down"]
"""文字出场效果：无、淡出、缩小消失、下滑淡出。"""
TextLoop = Literal["none", "pulse", "swing"]
"""文字循环效果（显示期间一直重复）：呼吸缩放、左右摇摆。"""
TextAlign = Literal["left", "center", "right"]


def _check_hex(value: str) -> str:
    if not _HEX_COLOR.fullmatch(value):
        raise ValueError("颜色必须是 #RRGGBB 格式")
    return value.upper()


def _check_font_name(value: str | None) -> str | None:
    if value is not None and ("/" in value or "\\" in value or value.startswith(".")):
        raise ValueError("字体只能是 resources/fonts 下的文件名")
    return value


class TextStyle(_Model):
    """字幕和标题共用的文字样式（对标剪映 / CapCut 的文字面板）。

    描边、阴影、底框、发光都可以叠加；开了底框时描边不显示（ASS 的底框占用了描边颜色）。
    """

    font: str | None = None
    """字体文件名；标题为空时跟随字幕字体。"""
    bold: bool = True
    italic: bool = False
    underline: bool = False
    color: str = "#FFFFFF"
    opacity: float = Field(1.0, ge=0.1, le=1.0)
    """文字整体不透明度。"""
    outline: float = Field(3.0, ge=0.0, le=16.0)
    """描边粗细（像素），0 表示不描边。"""
    outline_color: str = "#000000"
    shadow: float = Field(0.0, ge=0.0, le=20.0)
    """阴影距离（像素），0 表示无阴影。"""
    shadow_color: str = "#000000"
    shadow_opacity: float = Field(0.6, ge=0.0, le=1.0)
    shadow_angle: float = Field(45.0, ge=0.0, le=360.0)
    """阴影方向（度）：0 向右，90 向下，45 是右下。"""
    shadow_blur: float = Field(0.0, ge=0.0, le=20.0)
    """阴影模糊（像素），0 表示硬阴影。"""
    outline2: float = Field(0.0, ge=0.0, le=16.0)
    """第二层描边粗细（像素，套在第一层描边外面），0 表示不加；花字常用。"""
    outline2_color: str = "#FFFFFF"
    glow: float = Field(0.0, ge=0.0, le=12.0)
    """边缘柔化 / 发光（像素）；配合彩色描边就是霓虹字。"""
    background: str = "#000000"
    background_opacity: float = Field(0.0, ge=0.0, le=1.0)
    """底框不透明度，0 表示不加底框。"""
    spacing: float = Field(0.0, ge=-10.0, le=40.0)
    """字间距（像素）。"""
    line_spacing: float = Field(0.0, ge=0.0, le=120.0)
    """行间距（像素）：多行文字时各行之间额外留的距离。"""
    align: TextAlign = "center"
    animation: TextAnimation = "none"
    exit: TextExit = "none"
    loop: TextLoop = "none"
    highlight_color: str = "#FFE45C"
    """卡拉 OK 高亮色：已读到的字变成这个颜色。"""

    @field_validator("color", "outline_color", "outline2_color", "shadow_color", "background", "highlight_color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        return _check_hex(value)

    @field_validator("font")
    @classmethod
    def _check_font(cls, value: str | None) -> str | None:
        return _check_font_name(value)


class SubtitleStyle(TextStyle):
    enabled: bool = True
    font: str = "NotoSansSC-Bold.ttf"
    size: int = Field(64, ge=16, le=200)
    position: Literal["top", "middle", "bottom"] = "bottom"
    margin: int | None = Field(None, ge=0, le=1000)
    """距画面上 / 下边缘的距离（像素，按画布分辨率计）；为空时按画面比例自动（见 :func:`auto_subtitle_margin`）。"""
    keywords: list[str] = Field(default_factory=list, max_length=40)
    """关键词高亮：字幕里出现这些词时换成高亮色并放大。"""
    keyword_color: str = "#FFD60A"
    keyword_scale: float = Field(1.15, ge=1.0, le=1.6)

    @field_validator("keywords")
    @classmethod
    def _check_keywords(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for word in value:
            word = word.strip()[:20]
            if word and word not in seen:
                seen.append(word)
        return seen

    @field_validator("keyword_color")
    @classmethod
    def _check_keyword_color(cls, value: str) -> str:
        return _check_hex(value)

    @field_validator("margin", mode="before")
    @classmethod
    def _migrate_margin(cls, value):
        # 240 是旧版本的固定默认值：在横屏里会让字幕离底边太远，旧项目改为自动。
        return None if value == 240 else value

    @field_validator("font")
    @classmethod
    def _check_font(cls, value: str) -> str:
        return _check_font_name(value) or "NotoSansSC-Bold.ttf"


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


MAX_SUBJECT_IMAGES = 10
"""每个主体最多几张参考图（产品实拍常有好几种颜色、角度）。一次生成只用其中的几张，见 prompting.MAX_REFERENCES。"""


class LookSubject(_Model):
    """全片设定里的一个主体（产品、人物、物品、场所），各镜头用同一个代号指代它。"""

    code: str
    """代号，例如 P1、A；镜头提示词里用它指代这个主体，生成时自动带上外观描述。"""
    name: str = ""
    """简短名称，例如「便携咖啡机」「女主角」。"""
    kind: SubjectKind = "object"
    appearance: str = ""
    """外观：颜色、材质、形状、尺寸、服装、发型等，越具体各镜头越一致。"""
    images: list[str] = Field(default_factory=list, max_length=MAX_SUBJECT_IMAGES)
    """这个主体的参考图（素材 id，例如产品实拍图、模特照片）。生成用到这个主体的 AI 画面时
    默认只参照第一张（主图）；镜头可以改选别的图（例如另一种颜色），见 :attr:`ShotVisual.refs`。"""
    image_labels: dict[str, str] = Field(default_factory=dict)
    """参考图的说明（素材 id → 颜色 / 款式，例如「浅粉色」），写进提示词，让模型按这一款生成。"""
    no_reference: bool = False
    """用户确认这个主体不需要参考图（例如路人、背景人物），准备清单里算作就绪。"""

    @model_validator(mode="before")
    @classmethod
    def _migrate_reference(cls, data):
        # 旧版本只有一张参考图（reference 字段），并入 images。
        if isinstance(data, dict) and "reference" in data:
            data = dict(data)
            reference = data.pop("reference")
            if reference and reference not in (data.get("images") or []):
                data["images"] = [reference, *(data.get("images") or [])]
        return data

    @field_validator("images")
    @classmethod
    def _check_images(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            item = validate_id(item)
            if item not in seen:
                seen.append(item)
        return seen

    @field_validator("image_labels")
    @classmethod
    def _check_labels(cls, value: dict[str, str], info) -> dict[str, str]:
        # 只保留现有图片的说明，去掉空说明（images 在前面声明，这里已经校验过）。
        images = info.data.get("images") or []
        return {key: text.strip()[:30] for key, text in value.items() if key in images and text.strip()}

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
    confirmed: bool | None = None
    """素材是否已确认：确认前不能生成 AI 画面。``None`` 是有这个功能之前的旧项目，按已确认处理。
    由服务端维护（``POST /projects/{id}/look/confirm``），客户端改全片设定时不能改它。"""
    revised_at: float | None = None
    """确认之后又改了设定的时间：在这之前生成的 AI 画面可能和新设定不一致。"""

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
    sequence: list[str] = Field(default_factory=list)
    """多选：在选中的素材之后依次播放的其他素材（按选择顺序）；为空时只播放选中的素材。"""
    first_frame: str | None = None
    """ai 模式的首帧图片（图生视频）；为空时纯文字生成。"""
    refs: list[str] = Field(default_factory=list, max_length=MAX_SUBJECT_IMAGES)
    """这个镜头用哪些主体参考图（素材 id，按顺序）；为空时用画面里各主体的主图。
    例如商品有几种颜色，这个镜头想展示粉色款，就选粉色那张。
    这里是可选的上限；一次生成实际发几张由生成服务的上限决定（见 prompting.shot_references）。"""
    color: str = "#1F2937"
    """color 模式或没有可用素材时的纯色画面。"""
    fit: FitMode = "cover"
    focus_x: float = Field(0.5, ge=0.0, le=1.0)
    focus_y: float = Field(0.5, ge=0.0, le=1.0)
    """铺满裁切时的取景中心（0 = 最左 / 最上，1 = 最右 / 最下）。"""
    trim_start: float = Field(0.0, ge=0.0, le=36000)
    trim_end: float | None = Field(None, gt=0.0, le=36000)
    """视频素材的入点和出点（秒）；出点为空表示到素材结尾。"""
    skips: list[tuple[float, float]] = Field(default_factory=list, max_length=300)
    """入出点之间要剪掉的片段（素材里的秒数），例如按文字剪辑时删掉的口误、语气词和长停顿；播放时直接跳过。"""
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

    @field_validator("candidates", "sequence", "refs")
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
        return self.model_copy(
            update={"trim_start": 0.0, "trim_end": None, "focus_x": 0.5, "focus_y": 0.5, "skips": []}
        )

    def skips_within(self, start: float, end: float) -> list[tuple[float, float]]:
        """落在 [start, end] 里的剪掉片段，按时间排好、重叠的合并。"""
        result: list[list[float]] = []
        for a, b in sorted(self.skips):
            a, b = max(a, start), min(b, end)
            if b - a <= 0.001:
                continue
            if result and a <= result[-1][1]:
                result[-1][1] = max(result[-1][1], b)
            else:
                result.append([a, b])
        return [(round(a, 3), round(b, 3)) for a, b in result]

    def kept_seconds(self, start: float, end: float) -> float:
        """入出点之间去掉剪掉片段后的长度。"""
        return max(0.0, end - start - sum(b - a for a, b in self.skips_within(start, end)))

    def to_source_time(self, start: float, end: float, offset: float) -> float:
        """镜头里的第 ``offset`` 秒对应素材里的哪一秒（跳过剪掉的片段）。"""
        position = start + offset
        for a, b in self.skips_within(start, end):
            if a < position:
                position += b - a
        return min(position, end)

    def to_shot_time(self, start: float, end: float, source: float) -> float | None:
        """素材里的某一秒在镜头里是第几秒；落在剪掉的片段里时返回 None。"""
        if source < start or source > end:
            return None
        shift = 0.0
        for a, b in self.skips_within(start, end):
            if a <= source < b:
                return None
            if b <= source:
                shift += b - a
        return source - start - shift


class Shot(_Model):
    id: str = Field(default_factory=new_id)
    text: str = ""
    duration: float | None = Field(None, gt=0, le=120)
    """None 表示跟随配音时长。"""
    visual: ShotVisual = Field(default_factory=ShotVisual)
    title: str = ""
    """镜头小标题：这个镜头出现期间显示；为空时不显示。"""
    role: ShotRole | None = None
    """镜头类型；写分镜时按视频类型的叙事结构给出，用来选默认的画面来源。"""
    voice: VoiceSettings | None = None
    """单独覆盖这个镜头的配音设置；为空时用项目设置。"""
    speech: SpeechMode = "tts"
    """original：原片自带人声（剪辑页导入），不再配音；字幕按语音识别的逐词时间对齐。"""
    status: ShotStatus = "draft"
    error: str | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        return value[:60]

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


class HeadlineStyle(TextStyle):
    """画面顶部常驻的大标题。"""

    enabled: bool = False
    text: str = ""
    """标题文字；可以用换行分成两行。"""
    size: int = Field(76, ge=16, le=200)
    outline: float = Field(5.0, ge=0.0, le=16.0)
    margin: int = Field(160, ge=0, le=1000)
    """距画面顶部的距离（像素）；交付平台的安全区更大时以安全区为准。"""

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return value[:80]


class IntroCard(TextStyle):
    """片头标题卡：开头几秒压暗画面、居中显示大标题和副标题，淡入淡出后进入正片。"""

    enabled: bool = False
    text: str = ""
    """主标题；为空时用常驻大标题或项目标题。"""
    subtitle: str = ""
    duration: float = Field(2.5, ge=1.0, le=8.0)
    size: int = Field(110, ge=24, le=240)
    outline: float = Field(6.0, ge=0.0, le=16.0)
    animation: TextAnimation = "fade"
    dim: float = Field(0.55, ge=0.0, le=1.0)
    """画面压暗程度，0 表示不压暗。"""

    @field_validator("text", "subtitle")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return value[:80]


class ShotTitleStyle(TextStyle):
    """每个镜头小标题的样式（文字写在各镜头的 ``title`` 上）。"""

    size: int = Field(56, ge=16, le=200)
    color: str = "#FFE45C"
    outline: float = Field(4.0, ge=0.0, le=16.0)
    animation: TextAnimation = "fade"
    position: OverlayPosition = "top"
    margin: int = Field(330, ge=0, le=1500)
    """距所在边缘的距离（像素）；默认放在常驻大标题下面。"""


class TitleSettings(_Model):
    """标题：常驻大标题、片头标题卡、镜头小标题的样式。"""

    headline: HeadlineStyle = Field(default_factory=HeadlineStyle)
    intro: IntroCard = Field(default_factory=IntroCard)
    shot: ShotTitleStyle = Field(default_factory=ShotTitleStyle)
    candidates: list[str] = Field(default_factory=list, max_length=10)
    """AI 给出的候选标题，界面上点一下就能用。"""


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


class Pronunciation(_Model):
    """读音纠正：配音时把 ``word`` 换成读音相同的 ``say`` 再合成，字幕仍显示 ``word``。

    多数配音服务不支持指定读音，用同音字替换对所有服务都有效，例如「飞的」读作「飞滴」。
    """

    word: str = Field(min_length=1, max_length=20)
    say: str = Field(min_length=1, max_length=40)
    note: str = Field("", max_length=60)
    """说明（例如正确的拼音），只在界面上显示。"""

    @field_validator("word", "say")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("读音纠正的词不能为空")
        return value


class Project(_Model):
    schema_version: int = SCHEMA_VERSION
    id: str = Field(default_factory=new_id)
    title: str = ""
    subject: str = ""
    """视频诉求：用户想要的内容。最多 4096 字节（UTF-8）。"""
    idea: str = Field("", max_length=4000)
    """用户最初随手写的想法（新建时 AI 据此整理出 ``subject``），留作记录，方便以后改了重新创作。"""
    script: list[str] = Field(default_factory=list, max_length=60)
    """生成分镜时用的脚本（每段一个镜头）：用户定稿的脚本，或 AI 写的文案、粘贴的文案拆出的段落。
    之后改镜头文案不影响这里，留作创作记录。"""
    video_type: VideoType | None = None
    """视频类型；为空表示没选（旧项目或只按文案拆镜头）。"""
    origin: ProjectOrigin = "create"
    """create：「创作」里从想法生成的视频；cut：「剪辑」里导入已有视频剪的。两边的任务列表分开显示。"""
    titles: TitleSettings = Field(default_factory=TitleSettings)
    """标题：画面顶部常驻大标题、片头标题卡、镜头小标题样式。"""
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
    pronunciations: list[Pronunciation] = Field(default_factory=list, max_length=200)
    """读音纠正：配音时按这些词替换成同音字，字幕不变。"""
    subtitle: SubtitleStyle = Field(default_factory=SubtitleStyle)
    overlays: list[Overlay] = Field(default_factory=list)
    """叠加在画面上的文字与图形（卖点、价格、Logo），与字幕分开。"""
    transition: TransitionSettings = Field(default_factory=TransitionSettings)
    ai_badge: bool = False
    """在画面上显示「AI 生成」角标（元数据标识始终写入，不受此开关影响）。"""
    bgm: BgmSettings = Field(default_factory=BgmSettings)
    delivery: DeliveryPlatform = "general"
    """交付平台：决定成片的编码参数，字幕和叠加文字会挪进这个平台的安全区。"""
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

    @field_validator("script")
    @classmethod
    def _check_script(cls, value: list[str]) -> list[str]:
        return [line.strip()[:500] for line in value if line.strip()]

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
