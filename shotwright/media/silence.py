"""把剪点挪到附近声音最小的地方（纯 Python，不依赖 numpy）。

按文字剪时，剪点来自语音识别的逐词时间，这些时间常有 0.1–0.2 秒的误差：直接在那里剪，
容易切掉保留词的字头，或者留下删掉的语气词的半个尾音。剪辑软件的常见做法是在剪点附近
找最安静的位置再剪，这里同样处理：解码成 8 kHz 单声道，只在每个剪点前后的小窗口里
按 10 毫秒一帧算能量，挪到能量最小的那一帧中间。
"""

from __future__ import annotations

import subprocess
from array import array
from pathlib import Path

from .ffmpeg import LOCAL_PROTOCOLS, MediaError, tools

SAMPLE_RATE = 8000
FRAME = 80
"""每帧 80 个采样 = 10 毫秒。"""
WINDOW = 0.15
"""在剪点前后多少秒内找最安静的位置。"""
DISTANCE_PENALTY = 0.15
"""离原剪点越远越不划算：每偏离 0.1 秒，能量按最大能量的 1.5% 加罚，安静程度差不多时留在原处。"""


def decode(path: Path, *, timeout: float = 600) -> array:
    """解码成 8 kHz 单声道 16 位采样。"""
    command = [
        tools().ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-protocol_whitelist", LOCAL_PROTOCOLS,
        "-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
    ]  # fmt: skip
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MediaError("找不到 ffmpeg，请先安装 FFmpeg") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("音频解码超时") from exc
    if result.returncode != 0:
        raise MediaError("音频解码失败")
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    return samples


def _energy(samples: array, frame: int) -> float:
    start = frame * FRAME
    chunk = samples[start : start + FRAME]
    return sum(abs(value) for value in chunk) / FRAME if chunk else 0.0


def quietest(
    samples: array,
    point: float,
    *,
    low: float | None = None,
    high: float | None = None,
    window: float = WINDOW,
) -> float:
    """``point`` 附近（前后 ``window`` 秒，且在 ``low``–``high`` 之内）最安静的位置。

    能量差不多时偏向原位置；附近都没有声音时（全是静音）原样返回。
    """
    total = len(samples) / SAMPLE_RATE
    begin = max(0.0, point - window, low if low is not None else 0.0)
    end = min(total, point + window, high if high is not None else total)
    first, last = int(begin * SAMPLE_RATE) // FRAME, int(end * SAMPLE_RATE) // FRAME
    if last - first < 2:
        return point
    energies = [(_energy(samples, frame), frame) for frame in range(first, last)]
    loudest = max(energy for energy, _ in energies)
    if loudest <= 0:
        return point
    penalty = loudest * DISTANCE_PENALTY

    def score(item: tuple[float, int]) -> float:
        energy, frame = item
        center = (frame + 0.5) * FRAME / SAMPLE_RATE
        return energy + penalty * abs(center - point) / 0.1

    _, best = min(energies, key=score)
    return round((best + 0.5) * FRAME / SAMPLE_RATE, 3)


def snap_ranges(samples: array, ranges: list[tuple[float, float]], total: float) -> list[tuple[float, float]]:
    """把保留段之间的剪点挪到附近最安静的地方；片头、片尾不动，段与段之间的先后顺序保持不变。"""
    result: list[tuple[float, float]] = []
    for index, (begin, end) in enumerate(ranges):
        floor = result[-1][1] if result else 0.0
        ceiling = ranges[index + 1][0] if index + 1 < len(ranges) else total
        new_begin = begin if begin <= 1e-3 else quietest(samples, begin, low=floor, high=end - 0.2)
        new_end = end if end >= total - 1e-3 else quietest(samples, end, low=new_begin + 0.2, high=ceiling)
        if new_end - new_begin < 0.2:
            new_begin, new_end = begin, end
        result.append((round(new_begin, 3), round(new_end, 3)))
    return result
