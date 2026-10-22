"""Azure 语音服务（Cognitive Services Speech）。

与 Edge 在线语音用的是同一套神经网络声音，但这里是官方的、有 SLA 的接口：
按订阅计费，需要填写 region 和 API Key。返回格式是裸的音频字节，不含时间戳。

文档：https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech
"""

from __future__ import annotations

from pathlib import Path

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderSpec, SpeechResult
from shotwright.providers.tts.http_tts import fetch_audio, finish, percent


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_ssml(text: str, voice: str, rate: float, volume: float) -> str:
    """Azure 用 SSML 传文本；语速和音量按百分比写进去。"""
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
        f'<voice name="{_escape(voice)}">'
        f'<prosody rate="{percent(rate)}" volume="{percent(volume)}">{_escape(text)}</prosody>'
        f"</voice></speak>"
    )


class AzureTTS:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]
        self.region = settings["region"].strip()
        if not self.region:
            raise ValueError("region 不能为空")
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "60"))
        self.output_format = settings.get("format") or "audio-24khz-48kbitrate-mono-mp3"

    @property
    def _url(self) -> str:
        return f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"

    def synthesize(self, text: str, *, voice: str, rate: float, volume: float, output: Path) -> SpeechResult:
        path = fetch_audio(
            url=self._url,
            headers={
                "Ocp-Apim-Subscription-Key": self.api_key,
                "X-Microsoft-OutputFormat": self.output_format,
                "User-Agent": "Shotwright/0.4",
            },
            payload=build_ssml(text, voice, rate, volume),
            content_type="application/ssml+xml",
            label="Azure 配音",
            secret=self.api_key,
            proxy=self.proxy,
            timeout=self.timeout,
            suffix=".mp3",
        )
        return finish(path, label="Azure 配音")


registry.register(
    ProviderSpec(
        kind="tts",
        id="azure",
        name="Azure 语音（官方）",
        name_en="Azure Speech (official)",
        fields=(
            ConfigField("api_key", "API Key", required=True, secret=True),
            ConfigField("region", "区域", required=True, help="例如 eastasia、westus2"),
            ConfigField("format", "输出格式", default="audio-24khz-48kbitrate-mono-mp3"),
            ConfigField("timeout", "超时（秒）", default="60"),
        ),
        paid=True,
        docs_url="https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech",
        voices=(
            ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声"),
            ("zh-CN-YunxiNeural", "云希 · 男声"),
            ("zh-CN-YunjianNeural", "云健 · 男声（有力）"),
            ("en-US-JennyNeural", "Jenny · Female"),
            ("en-US-GuyNeural", "Guy · Male"),
            ("ja-JP-NanamiNeural", "七海 · 女性"),
            ("ko-KR-SunHiNeural", "선히 · 여성"),
        ),
    ),
    AzureTTS,
)


__all__ = ["AzureTTS", "build_ssml"]
