"""收费配音服务（Azure、硅基流动、MiniMax、ElevenLabs）。

这几家都是「POST 一段 JSON（或 SSML）拿到音频二进制」，区别在于鉴权方式、请求体字段
和返回格式。公共部分放在这里：发请求、校验返回的音频能读、抹掉密钥。

它们大多不返回逐词时间戳，字幕会退回按字数估算分配（见 ``media/subtitles.py``）。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx

from shotwright.media.ffmpeg import MediaError, probe
from shotwright.providers.base import ProviderError, SpeechResult

MAX_AUDIO = 50 * 1024 * 1024


def fetch_audio(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict | str | bytes,
    label: str,
    secret: str,
    proxy: str | None,
    timeout: float,
    suffix: str = ".mp3",
    content_type: str = "application/json",
) -> Path:
    """POST 到配音服务，把返回的音频写进临时文件并校验可读。"""
    body: bytes
    if isinstance(payload, str):
        body = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        body = payload
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request_headers = {"Content-Type": content_type, **headers}
    try:
        with httpx.Client(timeout=timeout, proxy=proxy, follow_redirects=True) as client:
            with client.stream("POST", url, headers=request_headers, content=body) as response:
                if response.status_code >= 400:
                    detail = _read_error(response, secret)
                    raise ProviderError(f"{label} 拒绝了请求：HTTP {response.status_code}，{detail}")
                handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                path = Path(handle.name)
                received = 0
                with handle:
                    for chunk in response.iter_bytes(64 * 1024):
                        received += len(chunk)
                        if received > MAX_AUDIO:
                            handle.close()
                            path.unlink(missing_ok=True)
                            raise ProviderError(f"{label} 返回的音频过大")
                        handle.write(chunk)
    except httpx.HTTPError as exc:
        raise ProviderError(f"{label} 请求失败：{type(exc).__name__}") from exc
    if received == 0:
        path.unlink(missing_ok=True)
        raise ProviderError(f"{label} 返回了空音频")
    return path


def _read_error(response: httpx.Response, secret: str) -> str:
    try:
        text = response.read().decode("utf-8", errors="replace")[:300]
    except httpx.HTTPError:
        return ""
    return _redact(text, secret)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def finish(path: Path, *, label: str) -> SpeechResult:
    """读取音频时长；读不出来就报错，避免把坏文件送进渲染。"""
    try:
        info = probe(path)
    except MediaError as exc:
        path.unlink(missing_ok=True)
        raise ProviderError(f"{label} 返回的音频无法解析") from exc
    if not info.has_audio or not info.duration:
        path.unlink(missing_ok=True)
        raise ProviderError(f"{label} 返回了空音频")
    return SpeechResult(path=path, duration=info.duration)


def percent(value: float) -> str:
    """把 1.0 这样的倍率转成配音服务常见的 "+0%" 写法。"""
    return f"{round((value - 1.0) * 100):+d}%"
