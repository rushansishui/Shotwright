"""静音配音：不联网、不收费，按文字长度估算时长并生成静音音轨。

用途：离线预览分镜节奏、自动化测试、只想要字幕不想要配音的视频。
"""

from __future__ import annotations

import re
from pathlib import Path

from shotwright.media.ffmpeg import run_ffmpeg
from shotwright.providers import registry
from shotwright.providers.base import ProviderSpec, SpeechResult

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)
_THAI = re.compile(r"[\u0e00-\u0e7f]")


def estimate_seconds(text: str, rate: float = 1.0) -> float:
    """中日韩约每秒 4.5 字，拼音文字约每秒 2.6 个词，泰文约每秒 12 个字符，最短 1.5 秒。"""
    cjk = len(_CJK.findall(text))
    thai = len(_THAI.findall(text))
    words = len(_WORD.findall(_THAI.sub(" ", _CJK.sub(" ", text))))
    seconds = cjk / 4.5 + words / 2.6 + thai / 12
    return round(max(1.5, seconds / max(rate, 0.1)), 2)


class SilentTTS:
    def synthesize(self, text: str, *, voice: str, rate: float, volume: float, output: Path) -> SpeechResult:
        duration = estimate_seconds(text, rate)
        output = output.with_suffix(".m4a")
        run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t",
                f"{duration:.3f}",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                str(output),
            ]
        )
        return SpeechResult(path=output, duration=duration)


registry.register(
    ProviderSpec(
        kind="tts", id="silent", name="静音（离线预览）", name_en="Silent (offline preview)", offline=True
    ),
    lambda settings: SilentTTS(),
)
