"""ElevenLabs 配音（多语种，音色自然）。

返回 mp3 二进制，不提供逐词时间戳。按字符计费，``paid=True``。

文档：https://elevenlabs.io/docs/api-reference/text-to-speech
"""

from __future__ import annotations

from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderSpec, SpeechResult
from shotwright.providers.tts.http_tts import fetch_audio, finish

API_URL = "https://api.elevenlabs.io/v1/text-to-speech"


class ElevenLabsTTS:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]
        self.base_url = (settings.get("base_url") or API_URL).rstrip("/")
        self.model = settings.get("model") or "eleven_multilingual_v2"
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "120"))

    def synthesize(self, text: str, *, voice: str, rate: float, volume: float, output: Path) -> SpeechResult:
        payload = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                # 语速直接对应 ElevenLabs 的 speed 字段。
                "speed": max(0.7, min(1.2, rate)),
            },
        }
        path = fetch_audio(
            url=f"{self.base_url}/{voice}",
            headers={"xi-api-key": self.api_key, "Accept": "audio/mpeg"},
            payload=payload,
            label="ElevenLabs 配音",
            secret=self.api_key,
            proxy=self.proxy,
            timeout=self.timeout,
            suffix=".mp3",
        )
        return finish(path, label="ElevenLabs 配音")


registry.register(
    ProviderSpec(
        kind="tts",
        id="elevenlabs",
        name="ElevenLabs",
        name_en="ElevenLabs",
        fields=(
            ConfigField("api_key", "API Key", required=True, secret=True),
            ConfigField("model", "模型", default="eleven_multilingual_v2"),
            ConfigField("base_url", "Base URL", default=API_URL),
            ConfigField("timeout", "超时（秒）", default="120"),
        ),
        paid=True,
        docs_url="https://elevenlabs.io/docs/api-reference/text-to-speech",
        voices=(
            ("21m00Tcm4TlvDq8ikWAM", "Rachel · 女声"),
            ("EXAVITQu4vr4xnSDFsdx", "Charlotte · 女声"),
            ("pNInz6obpgDQGcFmaJgB", "Adam · 男声"),
            ("onwK4e9ZLuTAKqWW03F9", "Daniel · 男声"),
            ("XB0fDUnXU5powFXDhCwa", "Charlotte · 多语种"),
        ),
    ),
    ElevenLabsTTS,
)
