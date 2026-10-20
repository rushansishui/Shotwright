"""FFmpeg / FFprobe 的薄封装。

所有命令都以参数列表调用（不经过 shell），并且只读取本地文件：
通过 ``-protocol_whitelist`` 禁止网络协议，避免素材文件里的引用触发外部请求。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOCAL_PROTOCOLS = "file,pipe,crypto,data"


class MediaError(RuntimeError):
    pass


@dataclass
class Tools:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"


_tools = Tools(shutil.which("ffmpeg") or "ffmpeg", shutil.which("ffprobe") or "ffprobe")


def configure(ffmpeg: str, ffprobe: str) -> None:
    _tools.ffmpeg = ffmpeg
    _tools.ffprobe = ffprobe


def tools() -> Tools:
    return _tools


def run_ffmpeg(args: list[str], *, cwd: Path | None = None, timeout: float = 1800) -> None:
    command = [
        _tools.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        *args,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise MediaError("找不到 ffmpeg，请先安装并加入 PATH，或在 config.toml 中配置路径") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("ffmpeg 执行超时") from exc
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise MediaError(f"ffmpeg 执行失败：{tail}")


@dataclass
class ProbeResult:
    duration: float | None
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool


def probe(path: Path) -> ProbeResult:
    command = [
        _tools.ffprobe,
        "-v",
        "error",
        "-protocol_whitelist",
        LOCAL_PROTOCOLS,
        "-show_entries",
        "format=duration:stream=codec_type,width,height,duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise MediaError("找不到 ffprobe，请先安装 FFmpeg") from exc
    if result.returncode != 0:
        raise MediaError(f"无法读取媒体文件：{Path(path).name}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = data.get("format", {}).get("duration")
    try:
        duration_value = float(duration) if duration not in (None, "N/A") else None
    except ValueError:
        duration_value = None
    return ProbeResult(
        duration=duration_value,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        has_video=video is not None,
        has_audio=audio is not None,
    )


def filter_path(path: Path) -> str:
    """把路径转义成可以放进 -vf 滤镜参数里的形式（兼容 Windows 盘符）。"""
    text = Path(path).resolve().as_posix()
    for char in ("\\", ":", "'", ",", "[", "]", ";"):
        text = text.replace(char, "\\" + char)
    return text
