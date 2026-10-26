"""背景音乐的节拍检测（纯 Python，不依赖 numpy）。

做法：把音乐解码成 8 kHz 单声道，每 20 ms 算一次能量，能量的上升量作为起音强度；
在 0.3–1.0 秒的范围里找起音强度自相关最强的间隔作为拍长（偏向 120 BPM 附近），
再找让拍点落在起音上最多的相位，得到整条音乐的拍点时刻。

结果只用来把镜头切换点就近挪到拍点上，精度到几十毫秒就够了。
"""

from __future__ import annotations

import subprocess
from array import array
from pathlib import Path

from .ffmpeg import LOCAL_PROTOCOLS, MediaError, tools

SAMPLE_RATE = 8000
HOP = 160
"""每帧 160 个采样 = 20 毫秒。"""
MAX_SECONDS = 300
MIN_LAG, MAX_LAG = 15, 50
"""拍长范围：15–50 帧，即 0.3–1.0 秒（60–200 BPM）。"""


def _decode(path: Path) -> array:
    command = [
        tools().ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-protocol_whitelist", LOCAL_PROTOCOLS,
        "-i", str(path), "-t", str(MAX_SECONDS),
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
    ]  # fmt: skip
    try:
        result = subprocess.run(command, capture_output=True, timeout=300)
    except FileNotFoundError as exc:
        raise MediaError("找不到 ffmpeg，请先安装 FFmpeg") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("音乐解码超时") from exc
    if result.returncode != 0:
        raise MediaError("音乐解码失败")
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    return samples


def onset_envelope(samples: array) -> list[float]:
    energy = []
    for start in range(0, len(samples) - HOP + 1, HOP):
        frame = samples[start : start + HOP]
        energy.append(sum(abs(value) for value in frame) / HOP)
    return [max(0.0, energy[i] - energy[i - 1]) for i in range(1, len(energy))]


def detect_beats(path: Path) -> tuple[list[float], float]:
    """返回（拍点时刻列表，BPM）。音乐太短或没有明显节奏时返回空列表。"""
    onset = onset_envelope(_decode(path))
    return beats_from_onset(onset)


def beats_from_onset(onset: list[float]) -> tuple[list[float], float]:
    n = len(onset)
    if n < MAX_LAG * 4 or max(onset, default=0.0) <= 0:
        return [], 0.0
    mean = sum(onset) / n
    centred = [value - mean for value in onset]
    best_lag, best_score = 0, 0.0
    for lag in range(MIN_LAG, MAX_LAG + 1):
        score = sum(centred[i] * centred[i + lag] for i in range(n - lag)) / (n - lag)
        # 轻微偏向 0.5 秒（120 BPM）附近，避免选成半拍或两拍。
        weight = 1.0 / (1.0 + abs(lag - 25) / 40)
        if score * weight > best_score:
            best_lag, best_score = lag, score * weight
    if best_lag == 0:
        return [], 0.0
    best_phase, best_sum = 0, -1.0
    for phase in range(best_lag):
        total = sum(onset[i] for i in range(phase, n, best_lag))
        if total > best_sum:
            best_phase, best_sum = phase, total
    seconds = HOP / SAMPLE_RATE
    # 起音包络比能量帧晚一帧（差分），拍点时刻加回去。
    beats = [round((i + 1) * seconds, 3) for i in range(best_phase, n, best_lag)]
    return beats, round(60.0 / (best_lag * seconds), 1)


def snap_cuts(
    durations: list[float],
    overlap: float,
    beats: list[float],
    *,
    fixed: list[bool] | None = None,
    max_shift: float = 0.35,
) -> list[float]:
    """把镜头切换点向后挪到最近的拍点上（最多 ``max_shift`` 秒）。

    只延长镜头、不缩短，配音不会被截断；最后一个镜头的结尾不动；``fixed`` 为真的
    镜头（用户手动设了时长）保持原样。
    """
    if not beats or len(durations) < 2:
        return durations
    result = list(durations)
    cursor = 0.0
    for index in range(len(result) - 1):
        cut = cursor + result[index] - overlap
        target = next((beat for beat in beats if cut - 1e-6 <= beat <= cut + max_shift), None)
        if target is not None and not (fixed and fixed[index]):
            result[index] = round(result[index] + (target - cut), 3)
        cursor += result[index] - overlap
    return result
