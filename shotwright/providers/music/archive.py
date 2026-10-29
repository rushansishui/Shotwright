"""Internet Archive 的背景音乐检索（免 API Key）。

Pixabay 只对图片和视频开放 API，音乐栏目没有对应接口；Internet Archive 提供
公开的检索与元数据接口，不需要密钥，适合做背景音乐的自动检索。

检索分两步：
1. ``advancedsearch.php`` 按关键词找条目（一个条目可能是一首曲子，也可能是一张专辑）；
2. 逐个读 ``metadata/<identifier>``，挑出时长在范围内的 MP3 文件。

条目质量参差，所以按文件时长过滤，并在素材信息里保留来源页面与作者。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx

from shotwright.providers import registry
from shotwright.providers.base import (
    ConfigField,
    MusicCandidate,
    ProviderError,
    ProviderSpec,
)

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata"
DOWNLOAD_URL = "https://archive.org/download"
MAX_DOWNLOAD = 200 * 1024 * 1024
MAX_ITEMS = 10
"""最多为多少个条目查元数据；每个条目一次请求，太多会很慢。"""

_AUDIO_FORMATS = frozenset({"vbr mp3", "mp3", "128kbps mp3", "64kbps mp3", "256kbps mp3", "320kbps mp3"})

# 可商用的授权类别。条目没有 licenseurl 时算 unknown，是否采用由配置决定。
LICENSE_LABELS = {
    "cc0": "CC0",
    "public-domain": "Public Domain",
    "cc-by": "CC BY",
    "cc-by-sa": "CC BY-SA",
    "unknown": "License not stated",
    "restricted": "Restricted license",
}


def license_kind(url: str) -> str:
    """把 licenseurl 归一成几类。非商用授权归到 restricted，默认不采用。"""
    text = (url or "").lower()
    if not text:
        return "unknown"
    if "publicdomain/zero" in text or "/cc0" in text:
        return "cc0"
    if "publicdomain/mark" in text or "publicdomain" in text:
        return "public-domain"
    if "by-nc" in text or "by-nd" in text or "by-nc-nd" in text or "by-nc-sa" in text:
        return "restricted"
    if "by-sa" in text:
        return "cc-by-sa"
    if "/by/" in text or text.rstrip("/").endswith("/by"):
        return "cc-by"
    return "restricted"


def license_text(kind: str, url: str) -> str:
    label = LICENSE_LABELS.get(kind, kind)
    return f"{label} {url}".strip() if url else label


def _flag(value: object, *, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def parse_length(value: object) -> float | None:
    """Internet Archive 的时长可能是秒数（"3725.53"）或 "MM:SS" / "H:MM:SS"。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if ":" in text:
            seconds = 0.0
            for part in text.split(":"):
                seconds = seconds * 60 + float(part)
            return seconds
        return float(text)
    except ValueError:
        return None


def pick_audio_file(files: list[dict], min_duration: float, max_duration: float) -> dict | None:
    """从条目的文件列表里挑第一首时长合适的 MP3。"""
    for item in files:
        if str(item.get("format") or "").strip().lower() not in _AUDIO_FORMATS:
            continue
        name = str(item.get("name") or "")
        if not name.lower().endswith(".mp3"):
            continue
        duration = parse_length(item.get("length"))
        if duration is None or not min_duration <= duration <= max_duration:
            continue
        return item
    return None


class ArchiveMusic:
    def __init__(self, settings: dict[str, str]):
        self.proxy = settings.get("proxy") or None
        self.timeout = float(settings.get("timeout", "30"))
        self.collection = (settings.get("collection") or "").strip()
        self.user_agent = settings.get("user_agent") or "Shotwright/0.4"
        # Archive 上大量条目没有 licenseurl。默认保留但标注"未标注"，便于发布前人工确认；
        # 设为 false 就只返回明确可商用的授权。
        self.allow_unknown = _flag(settings.get("allow_unknown_license"), default=True)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            proxy=self.proxy,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        )

    def build_query(self, query: str) -> str:
        """把用户/大模型给的检索词包成 Archive 的查询语法。"""
        terms = " AND ".join(part for part in query.replace(",", " ").split() if part)
        clauses = ["mediatype:audio", "format:(MP3)"]
        if self.collection:
            clauses.append(f"collection:({self.collection})")
        if terms:
            clauses.append(f"({terms})")
        return " AND ".join(clauses)

    def search(
        self, query: str, *, min_duration: float, max_duration: float, limit: int
    ) -> list[MusicCandidate]:
        params = {
            "q": self.build_query(query),
            "fl[]": ["identifier", "title", "creator"],
            "rows": MAX_ITEMS,
            "output": "json",
        }
        try:
            with self._client() as client:
                response = client.get(SEARCH_URL, params=params)
                if response.status_code >= 400:
                    raise ProviderError(f"Internet Archive 检索失败：HTTP {response.status_code}")
                docs = (response.json().get("response") or {}).get("docs") or []
                results: list[MusicCandidate] = []
                for doc in docs:
                    if len(results) >= limit:
                        break
                    candidate = self._candidate_from(client, doc, min_duration, max_duration)
                    if candidate is not None:
                        results.append(candidate)
                return results
        except httpx.HTTPError as exc:
            raise ProviderError(f"Internet Archive 检索失败：{type(exc).__name__}") from exc
        except ValueError as exc:
            raise ProviderError("Internet Archive 返回的格式无法解析") from exc

    def _candidate_from(
        self, client: httpx.Client, doc: dict, min_duration: float, max_duration: float
    ) -> MusicCandidate | None:
        identifier = str(doc.get("identifier") or "").strip()
        if not identifier:
            return None
        try:
            response = client.get(f"{METADATA_URL}/{identifier}")
            if response.status_code >= 400:
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            # 单个条目读不到不影响其它结果。
            return None
        chosen = pick_audio_file(payload.get("files") or [], min_duration, max_duration)
        if chosen is None:
            return None
        meta = payload.get("metadata") or {}
        license_url = str(meta.get("licenseurl") or "")
        kind = license_kind(license_url)
        if kind == "restricted" or (kind == "unknown" and not self.allow_unknown):
            # 非商用或未标注授权：不放进来，免得用户踩坑。
            return None
        name = str(chosen.get("name") or "")
        creator = meta.get("creator")
        if isinstance(creator, list):
            creator = creator[0] if creator else ""
        url = f"{DOWNLOAD_URL}/{identifier}/{name}"
        return MusicCandidate(
            provider="archive",
            remote_id=f"{identifier}/{name}",
            url=url,
            # 用 download 地址直接试听：/stream/ 返回的是 M3U 播放列表
            # （audio/x-mpegurl），浏览器的 <audio> 解码不了，会显示 0:00 且无法播放。
            # download 地址支持 Range 请求，浏览器可以边下边播。
            preview_url=quote(url, safe=":/?&=%"),
            title=str(meta.get("title") or doc.get("title") or name)[:200],
            duration=parse_length(chosen.get("length")) or 0.0,
            author=str(creator or "")[:200],
            meta={
                "title": str(meta.get("title") or name)[:200],
                "author": str(creator or "")[:200],
                "source_url": f"https://archive.org/details/{identifier}",
                "license": license_text(kind, license_url)[:300],
                "license_kind": kind,
                "query": str(doc.get("title") or ""),
            },
        )

    def download(self, candidate: MusicCandidate, output: Path) -> Path:
        output = output.with_suffix(".mp3")
        received = 0
        try:
            with self._client() as client, client.stream("GET", candidate.url) as response:
                if response.status_code >= 400:
                    raise ProviderError(f"背景音乐下载失败：HTTP {response.status_code}")
                with output.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        received += len(chunk)
                        if received > MAX_DOWNLOAD:
                            raise ProviderError("背景音乐文件过大")
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            output.unlink(missing_ok=True)
            raise ProviderError(f"背景音乐下载失败：{type(exc).__name__}") from exc
        except ProviderError:
            output.unlink(missing_ok=True)
            raise
        if received == 0:
            output.unlink(missing_ok=True)
            raise ProviderError("背景音乐下载到空文件")
        return output


registry.register(
    ProviderSpec(
        kind="music",
        id="archive",
        name="Internet Archive（免 Key）",
        name_en="Internet Archive (no key)",
        fields=(
            ConfigField("collection", "限定合集", help="留空搜索全部公开音频，例如 opensource_audio"),
            ConfigField("timeout", "超时（秒）", default="30"),
            ConfigField(
                "allow_unknown_license",
                "保留未标注授权的曲目",
                default="true",
                help='设为 "false" 时只返回明确可商用的授权（CC0 / 公共领域 / CC BY / CC BY-SA）',
            ),
        ),
        capabilities=frozenset({"audio"}),
        docs_url="https://archive.org/advancedsearch.php",
        offline=False,
    ),
    ArchiveMusic,
)
