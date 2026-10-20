"""Microsoft Edge 在线语音（edge-tts），免费、无需密钥。

参考了 MoneyPrinterTurbo ``app/services/voice.py`` 中的 Edge TTS 用法。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from shotwright.media.ffmpeg import MediaError, probe
from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, SpeechResult

# 常用声音，界面下拉框使用。完整列表见 `edge-tts --list-voices`。
VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓 · 女声（普通话）",
    "zh-CN-YunxiNeural": "云希 · 男声（普通话）",
    "zh-CN-YunjianNeural": "云健 · 男声（普通话，有力）",
    "zh-CN-XiaoyiNeural": "晓伊 · 女声（普通话，活泼）",
    "zh-TW-HsiaoChenNeural": "曉臻 · 女声（台湾）",
    "zh-HK-HiuGaaiNeural": "曉佳 · 女声（粤语）",
    "en-US-JennyNeural": "Jenny · Female (US)",
    "en-US-GuyNeural": "Guy · Male (US)",
    "ja-JP-NanamiNeural": "七海 · 女声（日语）",
}


def _percent(value: float) -> str:
    delta = round((value - 1.0) * 100)
    return f"{delta:+d}%"


class EdgeTTS:
    def __init__(self, settings: dict[str, str]):
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "60"))

    def synthesize(self, text: str, *, voice: str, rate: float, volume: float, output: Path) -> SpeechResult:
        try:
            import edge_tts
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise ProviderError("未安装 edge-tts") from exc

        output = output.with_suffix(".mp3")
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=_percent(rate),
            volume=_percent(min(volume, 2.0)),
            proxy=self.proxy,
        )

        async def _save() -> None:
            await asyncio.wait_for(communicate.save(str(output)), timeout=self.timeout)

        try:
            asyncio.run(_save())
        except Exception as exc:
            output.unlink(missing_ok=True)
            raise ProviderError(f"Edge 配音失败：{type(exc).__name__}") from exc
        try:
            info = probe(output)
        except MediaError as exc:
            raise ProviderError("Edge 配音返回的音频无法解析") from exc
        if not info.has_audio or not info.duration:
            raise ProviderError("Edge 配音返回了空音频")
        return SpeechResult(path=output, duration=info.duration)


registry.register(
    ProviderSpec(
        kind="tts",
        id="edge",
        name="Edge 在线语音（免费）",
        fields=(ConfigField("timeout", "超时（秒）", default="60"),),
        docs_url="https://github.com/rany2/edge-tts",
    ),
    EdgeTTS,
)
