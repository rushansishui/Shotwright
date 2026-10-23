"""FFmpeg / FFprobe 的薄封装。

所有命令都以参数列表调用（不经过 shell），并且只读取本地文件：
通过 ``-protocol_whitelist`` 禁止网络协议，避免素材文件里的引用触发外部请求。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

LOCAL_PROTOCOLS = "file,pipe,crypto,data"

_POLL_INTERVAL = 0.2
"""等待 ffmpeg 时检查取消信号的间隔（秒）。"""


class MediaError(RuntimeError):
    pass


class MediaCancelled(MediaError):
    """ffmpeg 被取消信号中止。"""


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


def run_ffmpeg(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 1800,
    cancel: threading.Event | None = None,
) -> None:
    """运行 ffmpeg。``cancel`` 被设置时终止子进程并抛 :class:`MediaCancelled`。"""
    if cancel is not None and cancel.is_set():
        # 已经取消就不必启动进程。
        raise MediaCancelled("ffmpeg 已取消")
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
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise MediaError("找不到 ffmpeg，请先安装并加入 PATH，或在 config.toml 中配置路径") from exc

    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                # 先等一小段，避免命令瞬间结束时仍然轮询。
                stdout, stderr = process.communicate(timeout=_POLL_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel is not None and cancel.is_set():
                _terminate(process)
                raise MediaCancelled("ffmpeg 已取消")
            if time.monotonic() >= deadline:
                _terminate(process)
                raise MediaError("ffmpeg 执行超时")
    finally:
        if process.poll() is None:
            _terminate(process)
    if process.returncode != 0:
        tail = "\n".join((stderr or "").strip().splitlines()[-8:])
        raise MediaError(f"ffmpeg 执行失败：{tail}")


def _terminate(process: subprocess.Popen) -> None:
    """先礼后兵地结束 ffmpeg，并回收管道，避免僵尸进程。"""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - 极端情况下放弃回收
            pass


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


def detect_scenes(path: Path, *, threshold: float = 0.35, timeout: float = 600) -> list[float]:
    """按画面变化检测切镜点，返回每个镜头的起始秒数（第一个总是 0）。

    用 ffmpeg 的 scene 分数：超过阈值的帧被选中，``showinfo`` 打印它的 ``pts_time``。
    """
    command = [
        _tools.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-protocol_whitelist",
        LOCAL_PROTOCOLS,
        "-i",
        str(path),
        "-filter:v",
        f"select='gt(scene,{threshold:.2f})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except FileNotFoundError as exc:
        raise MediaError("找不到 ffmpeg，请先安装 FFmpeg") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("切镜检测超时") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-5:])
        raise MediaError(f"切镜检测失败：{tail}")
    starts = [0.0]
    for line in (result.stderr or "").splitlines():
        marker = "pts_time:"
        position = line.find(marker)
        if position < 0:
            continue
        try:
            seconds = float(line[position + len(marker) :].split()[0])
        except (ValueError, IndexError):
            continue
        if seconds > 0:
            starts.append(round(seconds, 3))
    return sorted(set(starts))


def cut_segment(source: Path, output: Path, *, start: float, duration: float) -> None:
    """把源视频的一段重新编码成独立文件（只保留画面，声音另行配音）。"""
    run_ffmpeg(
        [
            "-protocol_whitelist",
            LOCAL_PROTOCOLS,
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            str(source),
            "-t",
            f"{max(0.05, duration):.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(output),
        ]
    )
