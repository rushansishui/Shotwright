"""Microsoft Edge 在线语音（edge-tts），免费、无需密钥。

参考了 MoneyPrinterTurbo ``app/services/voice.py`` 中的 Edge TTS 用法。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from shotwright.core.languages import LANGUAGES
from shotwright.media.ffmpeg import MediaError, probe
from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, SpeechResult, WordTiming

# 各语言的常用声音（见 core/languages.py），界面下拉框使用。完整列表见 `edge-tts --list-voices`。
VOICES = {
    voice_id: f"{label}（{lang.name_zh}）" for lang in LANGUAGES.values() for voice_id, label in lang.voices
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
            # 默认只返回整句边界；取逐词边界用于字幕对齐。
            boundary="WordBoundary",
            proxy=self.proxy,
        )

        async def _save() -> list[WordTiming]:
            # 边收音频边记 WordBoundary：时间戳是 100 纳秒单位，换算成秒。
            words: list[WordTiming] = []
            with output.open("wb") as handle:

                async def _consume() -> None:
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            handle.write(chunk["data"])
                        elif chunk["type"] == "WordBoundary":
                            start = chunk["offset"] / 10_000_000
                            words.append(
                                WordTiming(
                                    text=str(chunk.get("text") or ""),
                                    start=round(start, 3),
                                    end=round(start + chunk["duration"] / 10_000_000, 3),
                                )
                            )

                await asyncio.wait_for(_consume(), timeout=self.timeout)
            return words

        try:
            words = asyncio.run(_save())
        except Exception as exc:
            output.unlink(missing_ok=True)
            raise ProviderError(f"Edge 配音失败：{type(exc).__name__}") from exc
        try:
            info = probe(output)
        except MediaError as exc:
            raise ProviderError("Edge 配音返回的音频无法解析") from exc
        if not info.has_audio or not info.duration:
            output.unlink(missing_ok=True)
            raise ProviderError("Edge 配音返回了空音频")
        return SpeechResult(path=output, duration=info.duration, words=words)


registry.register(
    ProviderSpec(
        kind="tts",
        id="edge",
        name="Edge 在线语音（免费）",
        name_en="Edge online voices (free)",
        fields=(ConfigField("timeout", "超时（秒）", default="60"),),
        docs_url="https://github.com/rany2/edge-tts",
    ),
    EdgeTTS,
)
