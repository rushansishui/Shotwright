"""镜头渲染阶段：把画面素材与配音合成为单个镜头 MP4，按指纹缓存。"""

from __future__ import annotations

import math
import threading
from pathlib import Path

from shotwright.core.fingerprint import fingerprint
from shotwright.core.models import Project, Shot
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import LOCAL_PROTOCOLS, MediaCancelled, run_ffmpeg

from .render_types import AUDIO_ARGS, KEN_BURNS, RenderCancelled, StageRecord


def _fit_filter(width: int, height: int, fit: str) -> str:
    if fit == "contain":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"


def ken_burns_filter(width: int, height: int, fps: int, duration: float, fit: str) -> str:
    """图片慢推：先按两倍画布适配（减少缩放抖动），再从 1.0 匀速放大到 1.1 倍。"""
    frames = math.ceil(duration * fps) + 1
    return (
        f"{_fit_filter(width * 2, height * 2, fit)},"
        f"zoompan=z='1+0.1*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={width}x{height}:fps={fps},setsar=1,format=yuv420p"
    )


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
    visual = shot.visual
    asset = project.assets.get(visual.selected) if visual.selected and visual.mode != "color" else None
    if asset is not None and asset.kind not in ("video", "image"):
        asset = None
    visual_key = asset.sha256 if asset else f"color:{visual.color}"
    fp = fingerprint(
        "shot",
        voice_fp,
        duration,
        visual_key,
        visual.fit,
        KEN_BURNS if asset is not None and asset.kind == "image" else None,
        canvas.model_dump(),
        project.voice_for(shot).volume,
        quality,
    )
    output = store.cache_dir(project.id, "shots") / f"{fp}.mp4"
    width, height, fps = canvas.width, canvas.height, canvas.fps

    def build(temp: Path) -> None:
        if asset is None:
            video_input = [
                "-f", "lavfi",
                "-i", f"color=c=0x{visual.color[1:]}:s={width}x{height}:r={fps}",
            ]
        elif asset.kind == "image":
            video_input = [
                "-protocol_whitelist", LOCAL_PROTOCOLS,
                "-i", str(store.asset_path(project, asset.id)),
            ]
        else:
            video_input = [
                "-stream_loop", "-1",
                "-protocol_whitelist", LOCAL_PROTOCOLS,
                "-i", str(store.asset_path(project, asset.id)),
            ]
        if asset is not None and asset.kind == "image":
            video_chain = ken_burns_filter(width, height, fps, duration, visual.fit)
        else:
            video_chain = f"{_fit_filter(width, height, visual.fit)},setsar=1,fps={fps},format=yuv420p"
        graph = f"[0:v]{video_chain}[v];[1:a]aresample=48000,aformat=channel_layouts=stereo,apad[a]"
        run_ffmpeg(
            [
                *video_input,
                "-protocol_whitelist", LOCAL_PROTOCOLS,
                "-i", str(voice_path),
                "-filter_complex", graph,
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

    output.parent.mkdir(parents=True, exist_ok=True)
    import time

    from shotwright.core.ids import new_id

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
