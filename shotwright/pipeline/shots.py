"""镜头渲染阶段：把画面素材与配音合成为单个镜头 MP4，按指纹缓存。"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from shotwright.core.fingerprint import fingerprint
from shotwright.core.ids import new_id
from shotwright.core.models import MIN_SLOW_SPEED, Project, Shot, ShotVisual
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import LOCAL_PROTOCOLS, MediaCancelled, probe, run_ffmpeg

from .render_types import AUDIO_ARGS, KEN_BURNS, RenderCancelled, StageRecord

CLIP_VERSION = "clip-1"
"""视频片段处理方式（入出点、慢放定格、原声）的版本；改动处理逻辑时更新，旧缓存随之失效。"""


def fit_graph(source: str, target: str, width: int, height: int, fit: str, fx: float = 0.5, fy: float = 0.5) -> str:
    """把 ``[source]`` 适配到 width×height，输出 ``[target]``。

    - cover：等比放大铺满，按取景中心 (fx, fy) 裁掉多出的部分；
    - contain：完整显示，四周补黑边；
    - blur：完整显示，背景用同一画面放大模糊铺满（横竖屏互转时不裁掉主体）。
    """
    cover = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-ow)*{fx:.3f}:(ih-oh)*{fy:.3f}"
    )
    contain = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
    if fit == "contain":
        return f"[{source}]{contain},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[{target}]"
    if fit == "blur":
        return (
            f"[{source}]split=2[{target}_bg][{target}_fg];"
            f"[{target}_bg]{cover},boxblur=24:2,eq=brightness=-0.08[{target}_b];"
            f"[{target}_fg]{contain}[{target}_f];"
            f"[{target}_b][{target}_f]overlay=(W-w)/2:(H-h)/2,setsar=1[{target}]"
        )
    return f"[{source}]{cover},setsar=1[{target}]"


def ken_burns_graph(
    source: str, target: str, width: int, height: int, fps: int, duration: float, fit: str, fx: float, fy: float
) -> str:
    """图片慢推：先按两倍画布适配（减少缩放抖动），再从 1.0 匀速放大到 1.1 倍。"""
    frames = math.ceil(duration * fps) + 1
    return (
        f"{fit_graph(source, 'kb', width * 2, height * 2, fit, fx, fy)};"
        f"[kb]zoompan=z='1+0.1*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={width}x{height}:fps={fps},setsar=1,format=yuv420p[{target}]"
    )


@dataclass
class ClipPlan:
    """视频素材在镜头里怎么用：取哪一段、比镜头短时怎么补、原声要不要。"""

    start: float
    length: float
    """所选片段的长度（秒）。"""
    speed: float = 1.0
    """播放速度；慢放时小于 1。"""
    hold: float = 0.0
    """慢放后仍不够时，定格最后一帧补足的秒数。"""
    loop: bool = False
    whole: bool = True
    """片段就是整个素材（循环时可以直接 -stream_loop，不用先切出来）。"""
    audio: bool = False
    """是否混入原声。"""

    def key(self) -> tuple:
        return (round(self.start, 3), round(self.length, 3), round(self.speed, 4), self.loop, self.audio)


def plan_clip(visual: ShotVisual, source_duration: float, duration: float, has_audio: bool) -> ClipPlan:
    """按入出点和补足方式算出片段的用法。入出点超出素材范围时收回到素材内。"""
    total = max(source_duration, 0.1)
    start = min(max(visual.trim_start, 0.0), max(total - 0.1, 0.0))
    end = min(visual.trim_end or total, total)
    if end - start < 0.1:
        start, end = 0.0, total
    length = end - start
    whole = start < 0.01 and end > total - 0.01
    plan = ClipPlan(start=start, length=length, whole=whole, audio=has_audio and visual.source_volume > 0)
    if length >= duration - 0.01:
        return plan
    if visual.fill == "loop":
        plan.loop = True
    elif visual.fill == "slow_hold":
        plan.speed = max(MIN_SLOW_SPEED, length / duration)
        plan.hold = max(0.0, duration - length / plan.speed)
    else:
        plan.hold = duration - length
    return plan


def _video_args(fps: int, quality: str = "final") -> list[str]:
    """编码参数。``draft`` 档用更快的预设和较低质量，用于快速预览。"""
    if quality == "draft":
        return [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "30",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-g", str(fps * 2),
        ]
    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(fps * 2),
    ]


def render_shot(
    store: ProjectStore,
    project: Project,
    shot: Shot,
    voice_fp: str,
    voice_path: Path,
    duration: float,
    *,
    stages: list[StageRecord],
    cancel: threading.Event,
    quality: str = "final",
) -> tuple[str, Path]:
    """将单个镜头的画面与配音合成为 MP4，返回 (指纹, 输出路径)。"""
    canvas = project.canvas
    visual = shot.visual.effective_edit()
    asset = project.assets.get(visual.selected) if visual.selected and visual.mode != "color" else None
    if asset is not None and asset.kind not in ("video", "image"):
        asset = None
    asset_path = store.asset_path(project, asset.id) if asset is not None else None
    clip: ClipPlan | None = None
    if asset is not None and asset_path is not None and asset.kind == "video":
        source_duration = asset.duration
        has_audio = False
        if source_duration is None or visual.source_volume > 0:
            info = probe(asset_path)
            source_duration = source_duration or info.duration or duration
            has_audio = info.has_audio
        clip = plan_clip(visual, source_duration, duration, has_audio)
    visual_key = asset.sha256 if asset else f"color:{visual.color}"
    fit_key = (visual.fit, round(visual.focus_x, 3), round(visual.focus_y, 3))
    fp = fingerprint(
        "shot",
        voice_fp,
        duration,
        visual_key,
        fit_key,
        KEN_BURNS if asset is not None and asset.kind == "image" else None,
        clip.key() if clip else None,
        round(visual.source_volume, 3) if clip and clip.audio else None,
        CLIP_VERSION if clip else None,
        canvas.model_dump(),
        project.voice_for(shot).volume,
        quality,
    )
    output = store.cache_dir(project.id, "shots") / f"{fp}.mp4"
    width, height, fps = canvas.width, canvas.height, canvas.fps

    def build(temp: Path) -> None:
        source = asset_path
        scratch: Path | None = None
        try:
            if asset is None:
                video_input = [
                    "-f", "lavfi",
                    "-i", f"color=c=0x{visual.color[1:]}:s={width}x{height}:r={fps}",
                ]
            elif asset.kind == "image":
                video_input = ["-protocol_whitelist", LOCAL_PROTOCOLS, "-i", str(source)]
            else:
                assert clip is not None and source is not None
                if clip.loop and not clip.whole:
                    # 循环一个选段：先把选段切成单独的文件，再整段循环。
                    scratch = temp.with_name(temp.stem + "-clip.mp4")
                    _cut_clip(source, scratch, clip, fps, cancel)
                    source = scratch
                if clip.loop:
                    video_input = ["-stream_loop", "-1", "-protocol_whitelist", LOCAL_PROTOCOLS, "-i", str(source)]
                else:
                    video_input = [
                        "-ss", f"{clip.start:.3f}",
                        "-t", f"{clip.length:.3f}",
                        "-protocol_whitelist", LOCAL_PROTOCOLS,
                        "-i", str(source),
                    ]

            parts: list[str] = []
            if asset is not None and asset.kind == "image":
                parts.append(
                    ken_burns_graph("0:v", "v", width, height, fps, duration, visual.fit, visual.focus_x, visual.focus_y)
                )
            else:
                pre = "[0:v]"
                if clip is not None and clip.speed < 1.0:
                    pre = f"[0:v]setpts=(PTS-STARTPTS)/{clip.speed:.4f},"
                elif clip is not None:
                    pre = "[0:v]setpts=PTS-STARTPTS,"
                parts.append(f"{pre}null[raw]")
                parts.append(fit_graph("raw", "fit", width, height, visual.fit, visual.focus_x, visual.focus_y))
                tail = f"fps={fps}"
                if clip is not None and clip.hold > 0:
                    tail += f",tpad=stop_mode=clone:stop_duration={clip.hold + 0.5:.3f}"
                parts.append(f"[fit]{tail},format=yuv420p[v]")

            voice = "[1:a]aresample=48000,aformat=channel_layouts=stereo"
            if clip is not None and clip.audio:
                original = "[0:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo"
                if clip.speed < 1.0:
                    original += f",atempo={clip.speed:.4f}"
                original += f",volume={visual.source_volume:.3f}[src]"
                parts.append(original)
                parts.append(f"{voice},asplit=2[voice][key]")
                # 配音响起时原声自动压低（侧链压缩），配音停下后原声恢复。
                parts.append(
                    "[src][key]sidechaincompress=threshold=0.015:ratio=12:attack=20:release=400[duck]"
                )
                parts.append("[duck][voice]amix=inputs=2:duration=longest:normalize=0,apad[a]")
            else:
                parts.append(f"{voice},apad[a]")
            run_ffmpeg(
                [
                    *video_input,
                    "-protocol_whitelist", LOCAL_PROTOCOLS,
                    "-i", str(voice_path),
                    "-filter_complex", ";".join(parts),
                    "-map", "[v]",
                    "-map", "[a]",
                    "-t", f"{duration:.3f}",
                    *_video_args(fps, quality),
                    *AUDIO_ARGS,
                    "-movflags", "+faststart",
                    "-f", "mp4",
                    str(temp),
                ],
                cancel=cancel,
            )
        finally:
            if scratch is not None:
                scratch.unlink(missing_ok=True)

    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if output.is_file() and output.stat().st_size > 0:
        stages.append(StageRecord(f"{shot.id}:shot", "shot", fp, True, 0.0))
        return fp, output

    temp = output.with_name(f".tmp-{new_id(6)}{output.suffix}")
    try:
        build(temp)
        temp.replace(output)
    except MediaCancelled as exc:
        raise RenderCancelled(str(exc)) from exc
    finally:
        temp.unlink(missing_ok=True)
    stages.append(StageRecord(f"{shot.id}:shot", "shot", fp, False, round(time.monotonic() - started, 3)))
    return fp, output


def _cut_clip(source: Path, output: Path, clip: ClipPlan, fps: int, cancel: threading.Event) -> None:
    """把选段重新编码成独立文件（循环选段时用）。"""
    args = [
        "-ss", f"{clip.start:.3f}",
        "-t", f"{clip.length:.3f}",
        "-protocol_whitelist", LOCAL_PROTOCOLS,
        "-i", str(source),
        "-map", "0:v:0",
    ]
    if clip.audio:
        args += ["-map", "0:a:0", *AUDIO_ARGS]
    else:
        args += ["-an"]
    run_ffmpeg(
        [*args, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", "-f", "mp4", str(output)],
        cancel=cancel,
    )
