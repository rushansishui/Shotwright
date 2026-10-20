"""镜头画面：检索或生成候选素材，并登记到项目。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from shotwright.config import Settings
from shotwright.core.models import Asset, Project, Shot
from shotwright.core.store import ProjectStore
from shotwright.media.ffmpeg import MediaError, probe
from shotwright.providers import registry
from shotwright.providers.base import ProviderError, StockProvider, VideoGenProvider
from shotwright.services.durations import estimated_shot_seconds


def _register_media(
    store: ProjectStore,
    project: Project,
    path: Path,
    *,
    source: str,
    meta: dict[str, str] | None = None,
) -> Asset:
    try:
        info = probe(path)
    except MediaError as exc:
        raise ProviderError(f"素材无法解析：{exc}") from exc
    if not info.has_video:
        raise ProviderError("素材不包含画面")
    kind = "image" if not info.duration or info.duration < 0.05 else "video"
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        kind = "image"
    return store.add_asset(
        project,
        path,
        kind=kind,
        source=source,
        width=info.width,
        height=info.height,
        duration=info.duration if kind == "video" else None,
        meta=meta,
        move=True,
    )


def fetch_stock_candidates(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
    *,
    limit: int = 3,
) -> list[str]:
    """按检索词找素材并下载前 ``limit`` 个，返回新增的素材 id。"""
    provider: StockProvider = registry.create("stock", shot.visual.provider, settings)  # type: ignore[assignment]
    terms = shot.visual.terms or ([project.subject] if project.subject else [])
    if not terms:
        raise ProviderError("这个镜头没有检索词，请先填写英文检索词")
    min_duration = min(estimated_shot_seconds(project, shot), 10.0)
    found = []
    for term in terms:
        found.extend(
            provider.search(term, aspect=project.canvas.aspect, min_duration=min_duration, limit=limit)
        )
        if len(found) >= limit:
            break
    seen = {project.assets[a].meta.get("remote_id") for a in shot.visual.candidates if a in project.assets}
    added: list[str] = []
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        for index, candidate in enumerate(found):
            if candidate.remote_id in seen:
                continue
            path = provider.download(candidate, Path(temp) / f"c{index}")
            meta = {**candidate.meta, "remote_id": candidate.remote_id, "preview_url": candidate.preview_url}
            asset = _register_media(store, project, path, source=candidate.provider, meta=meta)
            added.append(asset.id)
            if len(added) >= limit:
                break
    if not added and not shot.visual.candidates:
        raise ProviderError("没有找到合适的素材，试试换几个检索词")
    _attach(shot, added)
    return added


def generate_ai_candidate(
    store: ProjectStore,
    settings: Settings,
    project: Project,
    shot: Shot,
) -> list[str]:
    provider: VideoGenProvider = registry.create("video_gen", shot.visual.provider, settings)  # type: ignore[assignment]
    if not shot.visual.prompt:
        raise ProviderError("这个镜头没有画面提示词")
    with tempfile.TemporaryDirectory(prefix="shotwright-") as temp:
        path = provider.generate(
            shot.visual.prompt,
            aspect=project.canvas.aspect,
            duration=estimated_shot_seconds(project, shot),
            output=Path(temp) / "gen",
        )
        asset = _register_media(
            store,
            project,
            path,
            source=shot.visual.provider or "video_gen",
            meta={"prompt": shot.visual.prompt},
        )
    _attach(shot, [asset.id])
    return [asset.id]


def refresh_candidates(store: ProjectStore, settings: Settings, project: Project, shot: Shot) -> list[str]:
    if shot.visual.mode == "stock":
        return fetch_stock_candidates(store, settings, project, shot)
    if shot.visual.mode == "ai":
        return generate_ai_candidate(store, settings, project, shot)
    raise ProviderError("这个镜头的画面来源不需要检索或生成")


def _attach(shot: Shot, asset_ids: list[str]) -> None:
    candidates = list(shot.visual.candidates)
    for asset_id in asset_ids:
        if asset_id not in candidates:
            candidates.append(asset_id)
    shot.visual.candidates = candidates[-12:]
    if asset_ids and (shot.visual.selected is None or shot.visual.selected not in shot.visual.candidates):
        shot.visual.selected = asset_ids[0]


def add_upload(store: ProjectStore, project: Project, path: Path, *, filename: str) -> Asset:
    """登记用户上传的文件（画面或音频）。"""
    try:
        info = probe(path)
    except MediaError as exc:
        raise ProviderError("无法识别上传的文件，请上传常见的视频、图片或音频格式") from exc
    suffix = Path(filename).suffix.lower()
    if info.has_video and suffix not in {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}:
        return _register_media(store, project, path, source="upload", meta={"filename": filename[:200]})
    if info.has_audio:
        return store.add_asset(
            project,
            path,
            kind="audio",
            source="upload",
            suffix=suffix or ".mp3",
            duration=info.duration,
            meta={"filename": filename[:200]},
            move=True,
        )
    raise ProviderError("上传的文件既没有画面也没有声音")
