"""FFmpeg / FFprobe 的薄封装。

所有命令都以参数列表调用（不经过 shell），并且只读取本地文件：
通过 ``-protocol_whitelist`` 禁止网络协议，避免素材文件里的引用触发外部请求。
"""

from __future__ import annotations

import json
import re
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


SPIKE_MIN = 0.03
"""画面变化分数至少到这个值，才可能算作切镜（低于它基本是压缩噪声）。"""
SPIKE_RATIO = 5.0
"""分数是前后画面平均变化的几倍时算作切镜。"""
SPIKE_WINDOW = 15
"""和前后各多少帧比较。"""


def pick_cuts(scores: list[tuple[float, float]], threshold: float) -> list[float]:
    """从每帧的 (时间, 画面变化分数) 里挑出切镜点。

    分数超过 ``threshold`` 的一定算；此外，比前后画面的变化明显突出的一帧也算——商品视频常是
    白底、固定机位、字幕位置不变，换一件商品整体分数只有 0.04 左右，固定阈值会漏掉。
    """
    cuts: list[float] = []
    values = [score for _time, score in scores]
    for index, (seconds, score) in enumerate(scores):
        if seconds <= 0:
            continue
        if score >= threshold:
            cuts.append(seconds)
            continue
        if score < SPIKE_MIN:
            continue
        around = values[max(0, index - SPIKE_WINDOW) : index] + values[index + 1 : index + 1 + SPIKE_WINDOW]
        baseline = sum(around) / len(around) if around else 0.0
        if score >= SPIKE_RATIO * max(baseline, 0.002):
            cuts.append(seconds)
    return cuts


def detect_scenes(path: Path, *, threshold: float = 0.35, timeout: float = 600) -> list[float]:
    """按画面变化检测切镜点，返回每个镜头的起始秒数（第一个总是 0）。

    ffmpeg 的 ``scene`` 滤镜给每帧打一个画面变化分数，由 :func:`pick_cuts` 挑出切镜点。
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
        "select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
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
    scores: list[tuple[float, float]] = []
    current: float | None = None
    for line in (result.stderr or "").splitlines():
        time_match = re.search(r"pts_time:(-?[0-9.]+)", line)
        if time_match:
            current = float(time_match.group(1))
            continue
        score_match = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        if score_match and current is not None:
            scores.append((current, float(score_match.group(1))))
            current = None
    starts = [0.0, *(round(seconds, 3) for seconds in pick_cuts(scores, threshold))]
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


def measure_loudness(path: Path, *, timeout: float = 600) -> float | None:
    """测量音频的整体响度（LUFS，EBU R128）。没有音轨或测不出时返回 None。"""
    command = [
        _tools.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-protocol_whitelist",
        LOCAL_PROTOCOLS,
        "-i",
        str(path),
        "-vn",
        "-af",
        "ebur128=framelog=quiet",
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
        raise MediaError("响度测量超时") from exc
    if result.returncode != 0:
        return None
    value = None
    for line in (result.stderr or "").splitlines():
        text = line.strip()
        if text.startswith("I:") and text.endswith("LUFS"):
            try:
                value = float(text[2:-4].strip())
            except ValueError:
                continue
    if value is None or value <= -70:
        return None
    return value


def loudnorm_stats(path: Path, *, target: float, true_peak: float, timeout: float = 600) -> dict[str, str] | None:
    """loudnorm 第一遍测量，返回第二遍需要的 measured_* 参数。没有声音（静音）时返回 None。"""
    command = [
        _tools.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-protocol_whitelist",
        LOCAL_PROTOCOLS,
        "-i",
        str(path),
        "-vn",
        "-af",
        f"loudnorm=I={target:g}:TP={true_peak:g}:LRA=11:print_format=json",
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
        raise MediaError("响度测量超时") from exc
    stderr = result.stderr or ""
    start, end = stderr.rfind("{"), stderr.rfind("}")
    if result.returncode != 0 or start < 0 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
        measured = float(data["input_i"])
    except (ValueError, KeyError, TypeError):
        return None
    if measured <= -70 or measured != measured:  # 静音或 NaN
        return None
    return {key: str(data[key]) for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}


def extract_frame(source: Path, output: Path, *, at: float | None = None, width: int = 512) -> Path:
    """取视频的一帧存成 JPEG（缩到 ``width`` 宽）。``at`` 为空时取最后一帧，图片直接缩放。

    看图检查只需要小图；首尾帧衔接取最后一帧时用原始宽度（``width=0``）。
    """
    scale = ["-vf", f"scale={width}:-2"] if width else []
    if at is None:
        position = ["-sseof", "-0.25"]
    else:
        position = ["-ss", f"{max(0.0, at):.3f}"]
    run_ffmpeg(
        [
            "-protocol_whitelist",
            LOCAL_PROTOCOLS,
            *position,
            "-i",
            str(source),
            "-frames:v",
            "1",
            *scale,
            "-q:v",
            "3",
            str(output),
        ]
    )
    if not output.exists():
        raise MediaError("没有取到画面")
    return output


def concat_videos(paths: list[Path], output: Path, *, fps: int = 30) -> Path:
    """把几段视频首尾相接成一段（统一成第一段的尺寸和帧率，只保留画面）。"""
    if not paths:
        raise MediaError("没有可拼接的视频")
    first = probe(paths[0])
    width, height = first.width or 1080, first.height or 1920
    args: list[str] = []
    chains = []
    for index, path in enumerate(paths):
        args += ["-protocol_whitelist", LOCAL_PROTOCOLS, "-i", str(path)]
        chains.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{index}]"
        )
    joined = "".join(f"[v{index}]" for index in range(len(paths)))
    graph = ";".join([*chains, f"{joined}concat=n={len(paths)}:v=1:a=0[out]"])
    run_ffmpeg(
        [
            *args,
            "-filter_complex",
            graph,
            "-map",
            "[out]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return output
