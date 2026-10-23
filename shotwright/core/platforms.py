"""交付平台规格：推荐尺寸、编码参数和安全区。

这是唯一一份平台规格表：渲染导出用它定编码参数、把字幕和叠加文字挪进安全区；
v0.5 的发布包也会复用这里的数据。安全区按画面高 / 宽的比例给出，表示四周被平台
界面（标题、按钮、进度条、评论入口）遮住的范围，数值取各平台常见界面的保守估计。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SafeArea:
    top: float = 0.05
    bottom: float = 0.06
    left: float = 0.04
    right: float = 0.04


@dataclass(frozen=True)
class Platform:
    id: str
    name: str
    name_en: str
    aspects: tuple[str, ...]
    """推荐的画面比例（第一个最推荐）。"""
    safe: SafeArea = field(default_factory=SafeArea)
    crf: int = 20
    maxrate: str = ""
    """码率上限，例如 12M；为空表示只按 CRF 控制质量。"""
    level: str = "4.1"


PLATFORMS: dict[str, Platform] = {
    item.id: item
    for item in (
        Platform("general", "通用", "General", ("9:16", "16:9", "1:1")),
        Platform(
            "douyin",
            "抖音 / TikTok",
            "Douyin / TikTok",
            ("9:16",),
            SafeArea(top=0.10, bottom=0.20, left=0.04, right=0.14),
            crf=19,
            maxrate="12M",
        ),
        Platform(
            "kuaishou",
            "快手",
            "Kuaishou",
            ("9:16",),
            SafeArea(top=0.10, bottom=0.18, left=0.04, right=0.14),
            crf=19,
            maxrate="12M",
        ),
        Platform(
            "channels",
            "视频号",
            "WeChat Channels",
            ("9:16", "16:9"),
            SafeArea(top=0.08, bottom=0.18, left=0.04, right=0.10),
            crf=19,
            maxrate="10M",
        ),
        Platform(
            "xiaohongshu",
            "小红书",
            "Xiaohongshu",
            ("9:16", "1:1"),
            SafeArea(top=0.08, bottom=0.15, left=0.04, right=0.10),
            crf=19,
            maxrate="10M",
        ),
        Platform(
            "shorts",
            "YouTube Shorts / Reels",
            "YouTube Shorts / Reels",
            ("9:16",),
            SafeArea(top=0.08, bottom=0.20, left=0.04, right=0.12),
            crf=18,
            maxrate="15M",
        ),
        Platform(
            "bilibili",
            "哔哩哔哩 / YouTube 横屏",
            "Bilibili / YouTube",
            ("16:9",),
            SafeArea(top=0.04, bottom=0.08, left=0.03, right=0.03),
            crf=18,
            maxrate="20M",
            level="4.2",
        ),
    )
}

DEFAULT_PLATFORM = "general"


def platform(platform_id: str | None) -> Platform:
    return PLATFORMS.get(platform_id or DEFAULT_PLATFORM, PLATFORMS[DEFAULT_PLATFORM])


def encode_args(platform_id: str | None, fps: int) -> list[str]:
    """成片档的交付编码参数：H.264 High、yuv420p、两秒一个关键帧，按平台限制码率。"""
    spec = platform(platform_id)
    args = [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(spec.crf),
        "-profile:v", "high",
        "-level", spec.level,
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(fps * 2),
    ]  # fmt: skip
    if spec.maxrate:
        bufsize = f"{int(spec.maxrate.rstrip('M')) * 2}M"
        args += ["-maxrate", spec.maxrate, "-bufsize", bufsize]
    return args


def safe_margins(platform_id: str | None, width: int, height: int) -> tuple[int, int, int, int]:
    """安全区换算成像素：（上，下，左，右）。"""
    spec = platform(platform_id).safe
    return (
        round(height * spec.top),
        round(height * spec.bottom),
        round(width * spec.left),
        round(width * spec.right),
    )
