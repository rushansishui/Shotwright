"""渲染流程共用的数据类型与常量。"""

from __future__ import annotations

from dataclasses import dataclass, field

AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]

VOICE_PADDING = 0.25
"""配音后留的空隙（秒），让镜头之间有呼吸感。"""

KEN_BURNS = "ken-burns-1"
"""图片镜头的运动效果版本；改动滤镜参数时同步修改，让旧缓存失效。"""


AUDIO_MIX = "audio-mix-2"
"""整片音频处理（背景音乐闪避、淡入淡出、响度标准化）的版本；改动参数时同步修改，让旧缓存失效。"""

TRUE_PEAK = -1.5
"""响度标准化时的真峰值上限（dBTP），避免平台转码后削波。"""


class RenderError(RuntimeError):
    pass


class RenderCancelled(RenderError):
    pass


@dataclass
class StageRecord:
    key: str
    kind: str
    fingerprint: str
    reused: bool
    seconds: float


@dataclass
class ShotTiming:
    shot_id: str
    start: float
    voice_duration: float
    duration: float
    words: list[tuple[str, float, float]] = field(default_factory=list)
    """配音服务的逐词时间戳（相对本镜头音频开头）；为空时字幕按字数估算。"""


@dataclass
class RenderResult:
    render_id: str
    project_id: str
    revision: int
    video: str
    subtitles: str | None
    duration: float
    timings: list[ShotTiming]
    stages: list[StageRecord]
    warnings: list[str] = field(default_factory=list)
    inspection: list[dict] = field(default_factory=list)
    """成片抽帧检查发现的问题：[{"shot": 镜头序号, "issues": [...]}]。"""

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)
