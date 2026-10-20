"""Pixabay 免费视频素材。

接口文档：https://pixabay.com/api/docs/#api_search_videos
Pixabay 的视频大多是横屏，竖屏画布会按"铺满"方式裁切；因此竖屏时优先选更高清的版本，
让裁切后的画面仍然清楚。按 Pixabay 的要求，展示素材时注明来源（作者与页面链接已记录在
素材信息里）。
"""

from __future__ import annotations

from pathlib import Path

import httpx

from shotwright.providers import registry
from shotwright.providers.base import ConfigField, ProviderError, ProviderSpec, StockCandidate

API_URL = "https://pixabay.com/api/videos/"
_MAX_DOWNLOAD = 500 * 1024 * 1024
_RENDITIONS = ("tiny", "small", "medium", "large")
# 画布短边需要的最小像素：竖屏 1080 宽要从横屏素材里裁，素材高度至少要 1080。
_MIN_SHORT_SIDE = {"9:16": 1080, "16:9": 720, "1:1": 720}


def pick_rendition(videos: dict, aspect: str) -> tuple[str, int, int, int] | None:
    """返回 (url, 宽, 高, 字节数)：满足清晰度要求的最小版本；都不满足时取最大的可用版本。"""
    need = _MIN_SHORT_SIDE.get(aspect, 720)
    usable = []
    for name in _RENDITIONS:
        item = videos.get(name) or {}
        url = item.get("url")
        try:
            width, height, size = (
                int(item.get("width") or 0),
                int(item.get("height") or 0),
                int(item.get("size") or 0),
            )
        except (TypeError, ValueError):
            continue
        if not isinstance(url, str) or not url.startswith("https://") or not width or not height:
            continue
        if size > _MAX_DOWNLOAD:
            continue
        usable.append((url, width, height, size))
    if not usable:
        return None
    for candidate in usable:  # 已按从小到大排列
        if min(candidate[1], candidate[2]) >= need:
            return candidate
    return usable[-1]


class PixabayStock:
    def __init__(self, settings: dict[str, str]):
        self.api_key = settings["api_key"]
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "60"))

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            proxy=self.proxy,
            follow_redirects=True,
            headers={"User-Agent": "Shotwright/0.1"},
        )

    def search(self, query: str, *, aspect: str, min_duration: float, limit: int) -> list[StockCandidate]:
        params = {"key": self.api_key, "q": query[:100], "per_page": 30, "safesearch": "true"}
        try:
            with self._client() as client:
                response = client.get(API_URL, params=params)
        except httpx.HTTPError as exc:
            # 异常信息可能带有含密钥的 URL，只报告类型。
            raise ProviderError(f"Pixabay 检索失败：{type(exc).__name__}") from exc
        if response.status_code in (400, 401, 403):
            raise ProviderError("Pixabay 拒绝了请求，请检查 API Key")
        if response.status_code == 429:
            raise ProviderError("Pixabay 请求过于频繁，请稍后再试")
        if response.status_code >= 400:
            raise ProviderError(f"Pixabay 检索失败：HTTP {response.status_code}")
        try:
            hits = response.json().get("hits", [])
        except ValueError as exc:
            raise ProviderError("Pixabay 返回的格式无法解析") from exc

        results: list[StockCandidate] = []
        for hit in hits if isinstance(hits, list) else []:
            duration = hit.get("duration")
            if not isinstance(duration, (int, float)) or duration < min_duration:
                continue
            chosen = pick_rendition(hit.get("videos") or {}, aspect)
            if chosen is None:
                continue
            url, width, height, _ = chosen
            thumbnail = ((hit.get("videos") or {}).get("tiny") or {}).get("thumbnail") or ""
            results.append(
                StockCandidate(
                    provider="pixabay",
                    remote_id=str(hit.get("id", "")),
                    url=url,
                    kind="video",
                    width=width,
                    height=height,
                    duration=float(duration),
                    preview_url=str(thumbnail),
                    meta={
                        "author": str(hit.get("user") or ""),
                        "source_url": str(hit.get("pageURL") or ""),
                        "license": "Pixabay Content License",
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
            raise ProviderError(f"素材下载失败：{type(exc).__name__}") from exc
        except ProviderError:
            output.unlink(missing_ok=True)
            raise
        return output


registry.register(
    ProviderSpec(
        kind="stock",
        id="pixabay",
        name="Pixabay",
        fields=(ConfigField("api_key", "API Key", required=True, secret=True),),
        capabilities=frozenset({"video"}),
        docs_url="https://pixabay.com/api/docs/",
    ),
    PixabayStock,
)
