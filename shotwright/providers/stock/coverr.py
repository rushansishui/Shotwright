"""Coverr 免费视频素材（免 API Key）。

接口文档：https://coverr.co/developers

检索返回的每条结果只有一个条目 id 和基础信息，真正的下载地址要再查
``/api/videos/<id>`` 的 ``urls`` 字段。列表接口不带授权信息，素材按
Coverr License 免费使用（可商用、无需署名）。

Coverr 的素材以横屏为主，竖屏画布按"铺满"裁切，因此竖屏时优先竖屏素材。
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

API_URL = "https://coverr.co/api/videos"
_MAX_DOWNLOAD = 500 * 1024 * 1024
_PAGE_SIZE = 30


class CoverrStock:
    def __init__(self, settings: dict[str, str]):
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "30"))

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            proxy=self.proxy,
            follow_redirects=True,
            headers={"User-Agent": "Shotwright/0.4"},
        )

    def search(self, query: str, *, aspect: str, min_duration: float, limit: int) -> list[StockCandidate]:
        params = {"query": query[:100], "page_size": _PAGE_SIZE}
        try:
            with self._client() as client:
                response = client.get(API_URL, params=params)
                if response.status_code >= 400:
                    raise ProviderError(f"Coverr 检索失败：HTTP {response.status_code}")
                hits = response.json().get("hits") or []
                results: list[StockCandidate] = []
                for hit in hits:
                    if len(results) >= limit:
                        break
                    candidate = self._candidate_from(client, hit, aspect, min_duration)
                    if candidate is not None:
                        results.append(candidate)
                # Coverr 没有方向参数：把方向一致的排在前面，竖屏少裁一点。
                results.sort(key=lambda item: not _matches_orientation(item.width, item.height, aspect))
                return results
        except httpx.HTTPError as exc:
            raise ProviderError(f"Coverr 检索失败：{type(exc).__name__}") from exc
        except ValueError as exc:
            raise ProviderError("Coverr 返回的格式无法解析") from exc

    def _candidate_from(
        self, client: httpx.Client, hit: dict, aspect: str, min_duration: float
    ) -> StockCandidate | None:
        if hit.get("is_premium"):
            # 付费素材不能直接下载。
            return None
        try:
            duration = float(hit.get("duration") or 0)
        except (TypeError, ValueError):
            return None
        if duration < min_duration:
            return None
        item_id = str(hit.get("id") or "").strip()
        if not item_id:
            return None
        # 列表接口没有下载地址，逐个查详情。
        try:
            detail = client.get(f"{API_URL}/{item_id}")
            if detail.status_code >= 400:
                return None
            urls = detail.json().get("urls") or {}
        except (httpx.HTTPError, ValueError):
            return None
        url = str(urls.get("mp4_download") or urls.get("mp4") or "")
        if not url.startswith("https://"):
            return None
        width = int(hit.get("max_width") or 0)
        height = int(hit.get("max_height") or 0)
        return StockCandidate(
            provider="coverr",
            remote_id=str(hit.get("video_id") or item_id),
            url=url,
            kind="video",
            width=width,
            height=height,
            duration=duration,
            preview_url=str(hit.get("thumbnail") or ""),
            meta={
                "author": str(hit.get("contributor_id") or ""),
                "source_url": f"https://coverr.co/videos/{hit.get('slug') or item_id}",
                "license": "Coverr License",
                "query": str(hit.get("title") or ""),
            },
        )

    def download(self, candidate: StockCandidate, output: Path) -> Path:
        output = output.with_suffix(".mp4")
        received = 0
        try:
            with self._client() as client, client.stream("GET", candidate.url) as response:
                if response.status_code >= 400:
                    raise ProviderError(f"素材下载失败：HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "")
                if "text/html" in content_type:
                    # CDN 有时返回一个网页而不是文件，按素材无法解析处理。
                    raise ProviderError("Coverr 没有返回视频文件")
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
        if received == 0:
            output.unlink(missing_ok=True)
            raise ProviderError("素材下载到空文件")
        return output


def _matches_orientation(width: int, height: int, aspect: str) -> bool:
    if aspect == "9:16":
        return height > width
    if aspect == "16:9":
        return width > height
    return True


registry.register(
    ProviderSpec(
        kind="stock",
        id="coverr",
        name="Coverr（免 Key）",
        name_en="Coverr (no key)",
        fields=(ConfigField("timeout", "超时（秒）", default="30"),),
        capabilities=frozenset({"video"}),
        docs_url="https://coverr.co/developers",
    ),
    CoverrStock,
)
