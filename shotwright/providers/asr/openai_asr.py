"""云端语音识别：OpenAI 兼容的 ``/audio/transcriptions`` 接口（需要支持逐词时间戳）。

OpenAI whisper-1、Groq whisper-large-v3 等都可以；接口要支持
``response_format=verbose_json`` 和 ``timestamp_granularities[]=word``。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from shotwright.providers import registry
from shotwright.providers.asr.faster_whisper import ZH_PROMPT, whisper_language
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, Transcript, WordTiming

MAX_BYTES = 25 * 1024 * 1024
"""OpenAI 的单个文件上限。"""


class OpenAIASR:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings.get("api_key", "")
        self.base_url = (settings.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        if self.base_url.endswith("/audio/transcriptions"):
            self.base_url = self.base_url[: -len("/audio/transcriptions")]
        self.model = (settings.get("model") or "whisper-1").strip()
        self.timeout = float(settings.get("timeout") or "600")
        self.proxy = settings.get("proxy") or None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _safe(self, text: str) -> str:
        return text.replace(self.api_key, "***") if self.api_key else text

    def check(self) -> tuple[bool, str]:
        from shotwright.providers.video_gen.common import describe_check

        try:
            with httpx.Client(timeout=30, proxy=self.proxy) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            return False, f"连不上：{self._safe(str(exc))[:200]}。请检查网络、代理或 base_url"
        if response.status_code < 300:
            return True, f"连接正常，将使用模型 {self.model}"
        return describe_check(response)

    def transcribe(
        self, audio: Path, *, language: str | None, progress: Callable[[float], None] | None = None
    ) -> Transcript:
        if audio.stat().st_size > MAX_BYTES:
            raise ProviderError("音频超过 25MB，云端识别不了；请先把原片剪短，或改用本地识别")
        lang = whisper_language(language)
        data: list[tuple[str, str]] = [
            ("model", self.model),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"),
            ("timestamp_granularities[]", "segment"),
        ]
        if lang:
            data.append(("language", lang))
        if lang == "zh":
            data.append(("prompt", ZH_PROMPT))
        try:
            with httpx.Client(timeout=self.timeout, proxy=self.proxy) as client, audio.open("rb") as handle:
                response = client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers=self._headers(),
                    data=data,
                    files={"file": (audio.name, handle, "audio/mpeg")},
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"语音识别请求失败：{self._safe(str(exc))[:200]}") from exc
        if response.status_code >= 300:
            raise ProviderError(
                f"语音识别失败（HTTP {response.status_code}）：{self._safe(response.text)[:300]}"
            )
        body = response.json()
        words = [
            WordTiming(str(w.get("word", "")).strip(), round(float(w["start"]), 3), round(float(w["end"]), 3))
            for w in body.get("words") or []
            if str(w.get("word", "")).strip()
        ]
        if not words:
            raise ProviderError(
                "识别服务没有返回逐词时间戳，请换用支持 timestamp_granularities 的模型（例如 whisper-1）"
            )
        sentences = [
            (round(float(s["start"]), 3), round(float(s["end"]), 3)) for s in body.get("segments") or []
        ]
        if progress:
            progress(1.0)
        return Transcript(words=words, sentences=sentences, language=str(body.get("language") or ""))


registry.register(
    ProviderSpec(
        kind="asr",
        id="openai",
        name="OpenAI 兼容接口（云端）",
        name_en="OpenAI-compatible (cloud)",
        paid=True,
        docs_url="https://platform.openai.com/docs/guides/speech-to-text",
        shared_key_from=(("llm", "openai"),),
        fields=(
            ConfigField(
                "api_key", "API Key", required=True, secret=True, help="留空时使用大模型 openai 小节的 Key"
            ),
            ConfigField(
                "base_url",
                "接口地址",
                default="https://api.openai.com/v1",
                help="Groq 填 https://api.groq.com/openai/v1",
            ),
            ConfigField("model", "模型", default="whisper-1", help="Groq 可用 whisper-large-v3-turbo"),
        ),
    ),
    OpenAIASR,
)
