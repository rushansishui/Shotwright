"""MiniMax 配音。

接口返回一个包含音频下载地址的 JSON（不是直接的二进制），所以要二次下载。
按字符计费，``paid=True``。

文档：https://platform.minimaxi.com/document/T2A%20V2
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, SpeechResult
from shotwright.providers.paid_task import error_detail, redact
from shotwright.providers.tts.http_tts import finish, percent

DEFAULT_URL = "https://api.minimaxi.com/v1/t2a_v2"
MAX_AUDIO = 50 * 1024 * 1024


class MiniMaxTTS:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]
        self.url = settings.get("url") or DEFAULT_URL
        self.model = settings.get("model") or "speech-2.5-hd-preview"
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "120"))

    def synthesize(self, text: str, *, voice: str, rate: float, volume: float, output: Path) -> SpeechResult:
        payload = {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice,
                "speed": max(0.5, min(2.0, rate)),
                "vol": max(0.1, min(10.0, round(volume, 2))),
                "pitch": 0,
            },
            "audio_setting": {"format": "mp3", "sample_rate": 32000, "bitrate": 128000},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout, proxy=self.proxy) as client:
                response = client.post(self.url, headers=headers, content=json.dumps(payload).encode("utf-8"))
                if response.status_code >= 400:
                    raise ProviderError(
                        f"MiniMax 拒绝了请求：HTTP {response.status_code}，{error_detail(response, self.api_key)}"
                    )
                body = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"MiniMax 请求失败：{type(exc).__name__}") from exc
        except ValueError as exc:
            raise ProviderError("MiniMax 返回的格式无法解析") from exc

        base = body.get("base_resp") or {}
        if base.get("status_code") not in (0, None):
            message = redact(str(base.get("status_msg") or ""), self.api_key)[:200]
            raise ProviderError(f"MiniMax 返回错误：{message}")

        data = body.get("data") or {}
        # 常见返回：data.audio 是十六进制字符串；部分版本给 data.url。
        audio_hex = data.get("audio")
        path = output.with_suffix(".mp3")
        path.parent.mkdir(parents=True, exist_ok=True)
        if audio_hex:
            try:
                raw = bytes.fromhex(str(audio_hex))
            except ValueError as exc:
                raise ProviderError("MiniMax 返回的音频数据损坏") from exc
            if not raw or len(raw) > MAX_AUDIO:
                raise ProviderError("MiniMax 返回的音频大小异常")
            path.write_bytes(raw)
        else:
            url = str(data.get("url") or "")
            if not url.startswith("https://"):
                raise ProviderError("MiniMax 没有返回可下载的音频地址")
            self._download(url, path)
        return finish(path, label="MiniMax 配音")

    def _download(self, url: str, path: Path) -> None:
        try:
            with httpx.Client(timeout=self.timeout, proxy=self.proxy, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise ProviderError(f"MiniMax 音频下载失败：HTTP {response.status_code}")
                    received = 0
                    with path.open("wb") as handle:
                        for chunk in response.iter_bytes(64 * 1024):
                            received += len(chunk)
                            if received > MAX_AUDIO:
                                raise ProviderError("MiniMax 返回的音频过大")
                            handle.write(chunk)
        except httpx.HTTPError as exc:
            path.unlink(missing_ok=True)
            raise ProviderError(f"MiniMax 音频下载失败：{type(exc).__name__}") from exc
        except ProviderError:
            path.unlink(missing_ok=True)
            raise


registry.register(
    ProviderSpec(
        kind="tts",
        id="minimax",
        name="MiniMax",
        name_en="MiniMax",
        fields=(
            ConfigField("api_key", "API Key", required=True, secret=True),
            ConfigField("model", "模型", default="speech-2.5-hd-preview"),
            ConfigField("url", "接口地址", default=DEFAULT_URL),
            ConfigField("timeout", "超时（秒）", default="120"),
        ),
        paid=True,
        docs_url="https://platform.minimaxi.com/document/T2A%20V2",
        voices=(
            ("male-qn-qingse", "青涩青年 · 男声"),
            ("male-qn-jingying", "精英青年 · 男声"),
            ("female-shaonv", "少女 · 女声"),
            ("female-yujie", "御姐 · 女声"),
            ("female-chengshu", "成熟女性 · 女声"),
            ("presenter_male", "男性主持人"),
            ("presenter_female", "女性主持人"),
        ),
    ),
    MiniMaxTTS,
)


__all__ = ["MiniMaxTTS", "percent"]
