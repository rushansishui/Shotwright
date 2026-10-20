"""Pexels 免费视频素材。

检索与挑选清晰度的逻辑参考了 MoneyPrinterTurbo ``app/services/material.py``
中的 ``search_videos_pexels``（MIT）。
"""

from __future__ import annotations

from pathlib import Path

import httpx

from shotwright.providers import registry
from shotwright.providers.base import (
    ConfigField,
    ProviderError,
    ProviderSpec,
    StockCandidate,
)

_ORIENTATION = {"9:16": "portrait", "16:9": "landscape", "1:1": "square"}
_MIN_SIZE = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}
_MAX_DOWNLOAD = 500 * 1024 * 1024


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


class PexelsStock:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "60"))

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            proxy=self.proxy,
            follow_redirects=True,
            headers={"User-Agent": "Shotwright/0.2"},
        )

    def search(self, query: str, *, aspect: str, min_duration: float, limit: int) -> list[StockCandidate]:
        params = {"query": query, "per_page": 30, "orientation": _ORIENTATION.get(aspect, "portrait")}
        try:
            with self._client() as client:
                response = client.get(
                    "https://api.pexels.com/videos/search",
                    params=params,
                    headers={"Authorization": self.api_key},
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Pexels 检索失败：{type(exc).__name__}") from exc
        if response.status_code == 401:
            raise ProviderError("Pexels API Key 无效")
        if response.status_code >= 400:
            raise ProviderError(f"Pexels 检索失败：HTTP {response.status_code}")
        try:
            videos = response.json().get("videos", [])
        except ValueError as exc:
            raise ProviderError("Pexels 返回的格式无法解析") from exc

        min_w, min_h = _MIN_SIZE.get(aspect, (720, 1280))
        results: list[StockCandidate] = []
        for video in videos if isinstance(videos, list) else []:
            duration = video.get("duration")
            if not isinstance(duration, (int, float)) or duration < min_duration:
                continue
            renditions = []
            for item in video.get("video_files") or []:
                try:
                    width, height = int(item["width"]), int(item["height"])
                except (KeyError, TypeError, ValueError):
                    continue
                link = item.get("link")
                if (
                    isinstance(link, str)
                    and link.startswith("https://")
                    and width >= min_w
                    and height >= min_h
                ):
                    renditions.append((width * height, width, height, link))
            if not renditions:
                continue
            # 选能铺满画布的最小清晰度，下载更快。
            _, width, height, link = min(renditions)
            user = video.get("user") or {}
            results.append(
                StockCandidate(
                    provider="pexels",
                    remote_id=str(video.get("id", "")),
                    url=link,
                    kind="video",
                    width=width,
                    height=height,
                    duration=float(duration),
                    preview_url=str(video.get("image") or ""),
                    meta={
                        "author": str(user.get("name") or ""),
                        "author_url": str(user.get("url") or ""),
                        "source_url": str(video.get("url") or ""),
                        "license": "Pexels License",
                        "query": query,
                    },
                )
            )
            if len(results) >= limit:
                break
        return results

    def download(self, candidate: StockCandidate, output: Path) -> Path:
        output = output.with_suffix(".mp4")
        received = 0
        try:
            with self._client() as client, client.stream("GET", candidate.url) as response:
                if response.status_code >= 400:
                    raise ProviderError(f"素材下载失败：HTTP {response.status_code}")
                with output.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        received += len(chunk)
                        if received > _MAX_DOWNLOAD:
                            raise ProviderError("素材文件过大")
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            output.unlink(missing_ok=True)
            raise ProviderError(_redact(f"素材下载失败：{type(exc).__name__}", self.api_key)) from exc
        except ProviderError:
            output.unlink(missing_ok=True)
            raise
        return output


registry.register(
    ProviderSpec(
        kind="stock",
        id="pexels",
        name="Pexels",
        fields=(ConfigField("api_key", "API Key", required=True, secret=True),),
        capabilities=frozenset({"video"}),
        docs_url="https://www.pexels.com/api/",
    ),
    PexelsStock,
)
