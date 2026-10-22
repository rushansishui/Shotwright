"""硅基流动（SiliconFlow）配音。

接口兼容 OpenAI 的 ``/audio/speech``：POST JSON，返回音频二进制。
按字符计费，``paid=True``。

文档：https://docs.siliconflow.cn/api-reference/audio/create-speech
"""

from __future__ import annotations

from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderSpec, SpeechResult
from shotwright.providers.tts.http_tts import fetch_audio, finish

BASE_URL = "https://api.siliconflow.cn/v1"


class SiliconFlowTTS:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]
        self.base_url = (settings.get("base_url") or BASE_URL).rstrip("/")
        self.model = settings.get("model") or "FunAudioLLM/CosyVoice2-0.5B"
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "120"))

    def synthesize(self, text: str, *, voice: str, rate: float, volume: float, output: Path) -> SpeechResult:
        payload = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            # 硅基流动用 0.25–4.0 的语速倍率，与项目的 rate 语义一致。
            "speed": max(0.25, min(4.0, rate)),
            "gain": max(-10.0, min(10.0, round((volume - 1.0) * 8, 1))),
        }
        path = fetch_audio(
            url=f"{self.base_url}/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload=payload,
            label="硅基流动配音",
            secret=self.api_key,
            proxy=self.proxy,
            timeout=self.timeout,
            suffix=".mp3",
        )
        return finish(path, label="硅基流动配音")


registry.register(
    ProviderSpec(
        kind="tts",
        id="siliconflow",
        name="硅基流动",
        name_en="SiliconFlow",
        fields=(
            ConfigField("api_key", "API Key", required=True, secret=True),
            ConfigField("model", "模型", default="FunAudioLLM/CosyVoice2-0.5B"),
            ConfigField("base_url", "Base URL", default=BASE_URL),
            ConfigField("timeout", "超时（秒）", default="120"),
        ),
        paid=True,
        docs_url="https://docs.siliconflow.cn/api-reference/audio/create-speech",
        voices=(
            ("FunAudioLLM/CosyVoice2-0.5B:alex", "Alex · 男声"),
            ("FunAudioLLM/CosyVoice2-0.5B:anna", "Anna · 女声"),
            ("FunAudioLLM/CosyVoice2-0.5B:bella", "Bella · 女声"),
            ("FunAudioLLM/CosyVoice2-0.5B:benjamin", "Benjamin · 男声"),
            ("FunAudioLLM/CosyVoice2-0.5B:claire", "Claire · 女声"),
            ("FunAudioLLM/CosyVoice2-0.5B:david", "David · 男声"),
        ),
    ),
    SiliconFlowTTS,
)
